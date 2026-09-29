from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from symgov_backend.ed_knowledge import (
    KnowledgeTopic,
    Visibility,
    source_path_issues,
)

# Offline fixture limits. They are deliberately not runtime capacity promises.
MAX_QUESTION_CHARS = 1_000
MAX_QUERY_TERMS = 128
MAX_TOKEN_CHARS = 64
MAX_TOKENS_PER_CHUNK = 512
MAX_CHUNKS = 100
MAX_CHUNKS_BYTES = 512_000
MAX_INDEX_TERMS = 20_000
MAX_INDEX_BYTES = 1_000_000
MAX_POSTINGS_SCAN = 5_000
# Decision 7.4 (2026-09-29). This bounds how much of a source file is read and
# hashed; a cited range is separately capped (ed_corpus.MAX_UNIT_BYTES). The
# builder and verify import this one value so all three agree.
MAX_SOURCE_BYTES = 262_144
MAX_RESULTS = 4
MAX_CONTEXT_CHARS = 4_000

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
# Function words carry no topic. Left in, every question shares "the" or "is"
# with some chunk and a real corpus could never report that it has no answer.
# The same list applies to postings and to questions.
_STOP_WORDS = frozenset(
    """
    a about all also am an and any are as at be been being but by can could
    did do does doing for from had has have here how i if in into is it its
    just me my no not of on or our should so some than that the their them
    then there these they this those to too very was we were what when where
    which who whom whose why will with would you your
    """.split()
)
_SHA256_VERSION = re.compile(r"^sha256:[0-9a-f]{64}$")
_KNOWLEDGE_VERSION = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
_CHUNK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_PROMPT_INJECTION = re.compile(
    r"(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions|"
    r"(?:reveal|print|show)\s+(?:the\s+)?system\s+prompt|"
    r"<\|(?:im_start|system)\|>|\[/?INST\])",
    re.IGNORECASE,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class _Chunk(_StrictModel):
    # No approval field: approval belongs to the bundle and is bound to its
    # index digest (see `query_index`). `extra="forbid"` means a chunk that
    # tries to carry its own approval state is malformed.
    chunk_id: str = Field(alias="id", min_length=3, max_length=128)
    extracted_at: str = Field(alias="extractedAt", min_length=1, max_length=40)
    fact_key: str = Field(alias="factKey", min_length=3, max_length=128)
    scope: str = Field(min_length=1, max_length=160)
    # The builder resolves every reviewed heading or symbol to these lines,
    # so they are always present; the symbol is only a display label.
    source_line_end: int = Field(alias="sourceLineEnd", ge=1)
    source_line_start: int = Field(alias="sourceLineStart", ge=1)
    source_path: str = Field(alias="sourcePath", min_length=1, max_length=500)
    source_symbol: str | None = Field(alias="sourceSymbol", default=None, max_length=240)
    source_version: str = Field(alias="sourceVersion", min_length=1, max_length=200)
    superseded_by: tuple[str, ...] = Field(alias="supersededBy", default=())
    text: str = Field(min_length=1, max_length=4_000)
    title: str = Field(min_length=1, max_length=240)
    topic: KnowledgeTopic
    visibility: Visibility


class KnowledgeCitation(_StrictModel):
    reference: str
    label: str
    source_version: str
    source_locator: str


class RetrievalItem(_StrictModel):
    chunk_id: str
    topic: KnowledgeTopic
    text: str
    citation: KnowledgeCitation
    audit_source_path: str = Field(exclude=True)


class KnowledgeRetrievalResult(_StrictModel):
    status: Literal["answered", "cannot_answer", "unavailable"]
    items: tuple[RetrievalItem, ...] = ()
    model_context: str = ""


class OfflineFixtureRetrievalResult(KnowledgeRetrievalResult):
    """A result from the offline fixture runner, named so it cannot pass for runtime."""


class RetrievalIndexError(ValueError):
    pass


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def index_digest(chunks_bytes: bytes, postings_bytes: bytes) -> str:
    """Hash the two index files with names and lengths to prevent ambiguous joins.

    This is the value a steward approves: it names the exact bytes that may
    be served.
    """
    payload = b"".join(
        (
            b"chunks.jsonl\0",
            len(chunks_bytes).to_bytes(8, "big"),
            chunks_bytes,
            b"postings.json\0",
            len(postings_bytes).to_bytes(8, "big"),
            postings_bytes,
        )
    )
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _tokens(value: str, *, maximum: int) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    result: list[str] = []
    for match in _TOKEN.finditer(normalized):
        token = match.group(0)
        if len(token) > MAX_TOKEN_CHARS or token in _STOP_WORDS:
            continue
        result.append(token)
        if len(result) > maximum:
            raise RetrievalIndexError(f"token limit exceeds {maximum}")
    return tuple(result)


def _load_chunks(chunks_bytes: bytes) -> tuple[_Chunk, ...]:
    if len(chunks_bytes) > MAX_CHUNKS_BYTES:
        raise RetrievalIndexError("chunk bytes limit exceeded")
    chunks: list[_Chunk] = []
    identifiers: set[str] = set()
    try:
        lines = chunks_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise RetrievalIndexError("chunks must be UTF-8") from exc
    if len(lines) > MAX_CHUNKS:
        raise RetrievalIndexError("chunk count limit exceeded")
    for line in lines:
        if not line:
            continue
        try:
            chunk = _Chunk.model_validate_json(line)
        except (ValidationError, ValueError) as exc:
            raise RetrievalIndexError("malformed chunk record") from exc
        if chunk.chunk_id in identifiers:
            raise RetrievalIndexError("duplicate chunk identifier")
        identifiers.add(chunk.chunk_id)
        chunks.append(chunk)
    return tuple(chunks)


def build_postings(chunks_bytes: bytes) -> bytes:
    """Build deterministic lexical postings from offline corpus chunk bytes."""
    chunks = _load_chunks(chunks_bytes)
    postings: dict[str, list[list[str | int]]] = {}
    for chunk in sorted(chunks, key=lambda item: item.chunk_id):
        frequencies = Counter(
            _tokens(f"{chunk.title} {chunk.text}", maximum=MAX_TOKENS_PER_CHUNK)
        )
        for term in sorted(frequencies):
            postings.setdefault(term, []).append([chunk.chunk_id, frequencies[term]])
            if len(postings) > MAX_INDEX_TERMS:
                raise RetrievalIndexError("index term limit exceeded")
    result = _canonical_json(postings)
    if len(result) > MAX_INDEX_BYTES:
        raise RetrievalIndexError("index bytes limit exceeded")
    return result


def _load_postings(postings_bytes: bytes) -> dict[str, tuple[tuple[str, int], ...]]:
    if len(postings_bytes) > MAX_INDEX_BYTES:
        raise RetrievalIndexError("index bytes limit exceeded")
    try:
        raw = json.loads(postings_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalIndexError("malformed postings index") from exc
    if not isinstance(raw, dict) or len(raw) > MAX_INDEX_TERMS:
        raise RetrievalIndexError("malformed postings index")
    parsed: dict[str, tuple[tuple[str, int], ...]] = {}
    for term, entries in raw.items():
        if (
            not isinstance(term, str)
            or not term
            or len(term) > MAX_TOKEN_CHARS
            or not isinstance(entries, list)
        ):
            raise RetrievalIndexError("malformed postings index")
        values: list[tuple[str, int]] = []
        previous = ""
        for entry in entries:
            if (
                not isinstance(entry, list)
                or len(entry) != 2
                or not isinstance(entry[0], str)
                or not isinstance(entry[1], int)
                or isinstance(entry[1], bool)
                or entry[1] < 1
                or entry[0] <= previous
            ):
                raise RetrievalIndexError("malformed postings index")
            previous = entry[0]
            values.append((entry[0], entry[1]))
        parsed[term] = tuple(values)
    if _canonical_json(raw) != postings_bytes:
        raise RetrievalIndexError("postings index is not canonical")
    return parsed


def _eligible(chunk: _Chunk) -> bool:
    return (
        chunk.visibility is Visibility.AUTHENTICATED
        and chunk.scope == "product"
        and not chunk.superseded_by
    )


def _has_control_characters(value: str, *, allow_layout: bool = False) -> bool:
    allowed = {"\t", "\n"} if allow_layout else set()
    return any(
        unicodedata.category(character) == "Cc" and character not in allowed
        for character in value
    )


def _model_facing_safe(chunk: _Chunk) -> bool:
    if not _CHUNK_ID.fullmatch(chunk.chunk_id) or _has_control_characters(chunk.title):
        return False
    if chunk.source_symbol is not None and _has_control_characters(chunk.source_symbol):
        return False
    return (
        not _has_control_characters(chunk.text, allow_layout=True)
        and not _PROMPT_INJECTION.search(unicodedata.normalize("NFKC", chunk.text))
    )


def _source_matches(chunk: _Chunk, root: Path) -> bool:
    if not _SHA256_VERSION.fullmatch(chunk.source_version):
        return False
    if source_path_issues(chunk.source_path, chunk.chunk_id, root):
        return False
    path = root / chunk.source_path
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_SOURCE_BYTES + 1)
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if len(data) > MAX_SOURCE_BYTES:
        return False
    if chunk.source_version != f"sha256:{hashlib.sha256(data).hexdigest()}":
        return False

    # Matching bytes mean the reviewed lines are exactly the lines the builder
    # resolved, so the range is all that needs checking. Re-deriving the
    # symbol here disagreed with the builder about Markdown heading slugs.
    return chunk.source_line_start <= chunk.source_line_end <= len(text.splitlines())


def _citation(chunk: _Chunk, knowledge_version: str) -> KnowledgeCitation:
    if chunk.source_symbol:
        locator = f"symbol {chunk.source_symbol}"
    else:
        locator = f"lines {chunk.source_line_start}-{chunk.source_line_end}"
    return KnowledgeCitation(
        reference=f"knowledge:{knowledge_version}:{chunk.chunk_id}",
        label=chunk.title,
        source_version=chunk.source_version,
        source_locator=locator,
    )


def _query_terms(question: str) -> tuple[str, ...]:
    """Distinct content terms in question order, bounded without failing.

    The question is already bounded to MAX_QUESTION_CHARS, so it cannot hold
    more tokens than characters; only the distinct terms are capped.
    """
    terms = dict.fromkeys(_tokens(question, maximum=MAX_QUESTION_CHARS))
    return tuple(terms)[:MAX_QUERY_TERMS]


def _passage(item: RetrievalItem) -> str:
    return f"[{item.citation.reference}] {item.citation.label}\n{item.text}"


def query_index(
    postings_bytes: bytes,
    chunks_bytes: bytes,
    question: str,
    *,
    knowledge_version: str,
    approved_index_digest: str | None,
    repository_root: str | Path,
    offline_fixture: bool = False,
) -> OfflineFixtureRetrievalResult:
    """Run synthetic offline fixture retrieval, never runtime-approved retrieval.

    `approved_index_digest` is the digest a steward approved. Nothing is
    served unless it names exactly these chunk and postings bytes. This
    function checks that binding only. Runtime serving goes through
    `ed_knowledge_runtime`, which first proves the approval with the
    steward's signature.
    """
    if offline_fixture is not True:
        raise ValueError("offline fixture acknowledgement is required")
    return retrieve(
        postings_bytes,
        chunks_bytes,
        question,
        knowledge_version=knowledge_version,
        approved_index_digest=approved_index_digest,
        repository_root=repository_root,
        result_type=OfflineFixtureRetrievalResult,
    )


def retrieve(
    postings_bytes: bytes,
    chunks_bytes: bytes,
    question: str,
    *,
    knowledge_version: str,
    approved_index_digest: str | None,
    repository_root: str | Path,
    result_type: type[KnowledgeRetrievalResult] = KnowledgeRetrievalResult,
) -> KnowledgeRetrievalResult:
    """Bounded lexical retrieval over an index whose digest was approved."""
    if len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f"question exceeds {MAX_QUESTION_CHARS} characters")
    if not _KNOWLEDGE_VERSION.fullmatch(knowledge_version):
        raise ValueError("knowledge_version is not safe for a citation reference")
    if approved_index_digest is None or approved_index_digest != index_digest(
        chunks_bytes, postings_bytes
    ):
        return result_type(status="unavailable")
    try:
        terms = _query_terms(question)
        postings = _load_postings(postings_bytes)
        chunks = _load_chunks(chunks_bytes)
        if build_postings(chunks_bytes) != postings_bytes:
            raise RetrievalIndexError("postings index does not match chunks")
    except RetrievalIndexError:
        return result_type(status="unavailable")
    if not terms:
        return result_type(status="cannot_answer")

    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    scores: Counter[str] = Counter()
    scanned = 0
    for term in terms:
        for chunk_id, frequency in postings.get(term, ()):
            scanned += 1
            if scanned > MAX_POSTINGS_SCAN:
                return result_type(status="unavailable")
            chunk = by_id.get(chunk_id)
            if chunk is None:
                return result_type(status="unavailable")
            if _eligible(chunk):
                title_frequency = Counter(
                    _tokens(chunk.title, maximum=MAX_TOKENS_PER_CHUNK)
                )[term]
                scores[chunk_id] += frequency + (title_frequency * 3)

    candidates = [by_id[chunk_id] for chunk_id in scores]
    root = Path(repository_root).resolve()
    if any(
        not _model_facing_safe(chunk) or not _source_matches(chunk, root)
        for chunk in candidates
    ):
        return result_type(status="unavailable")
    ranked = sorted(
        candidates,
        key=lambda chunk: (-scores[chunk.chunk_id], chunk.chunk_id),
    )

    items: list[RetrievalItem] = []
    passages: list[str] = []
    context_size = 0
    for chunk in ranked:
        if len(items) == MAX_RESULTS:
            break
        item = RetrievalItem(
            chunk_id=chunk.chunk_id,
            topic=chunk.topic,
            text=chunk.text,
            citation=_citation(chunk, knowledge_version),
            audit_source_path=chunk.source_path,
        )
        passage = _passage(item)
        separator = 2 if passages else 0
        if context_size + separator + len(passage) > MAX_CONTEXT_CHARS:
            continue
        items.append(item)
        passages.append(passage)
        context_size += separator + len(passage)
    if not items:
        return result_type(status="cannot_answer")
    # Each passage is labelled with its reference so the model can say which
    # one supports a claim, and the server can check that it did.
    return result_type(
        status="answered",
        items=tuple(items),
        model_context="\n\n".join(passages),
    )

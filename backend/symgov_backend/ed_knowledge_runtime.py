"""Serve Ed product knowledge only from a steward-signed bundle (Slice D).

A bundle lives in the release tree under
`backend/symgov_backend/data/ed_knowledge/bundles/<name>/` (decision 7.4):
`bundle/` holds the four built files, `approval.json` the receipt and
`approval.json.sig` the Ed Knowledge Steward's signature over it. A bundle is
loaded only when `verify-approval` passes against the signer list named by
`SYMGOV_ED_ALLOWED_SIGNERS`, which lives outside the repository. Any failure
means Ed serves no product knowledge; it never falls back to a draft, an
example manifest or an older bundle.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .ed_corpus_cli import BundleError, verify_approval
from .ed_retrieval import KnowledgeRetrievalResult, retrieve
from .settings import SymgovAPISettings


logger = logging.getLogger(__name__)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BUNDLES_PATH = Path("backend/symgov_backend/data/ed_knowledge/bundles")
_BUNDLE_NAME = re.compile(r"^[0-9a-f]{12}$")
# A failed load is retried after this long rather than on every request, so a
# missing signature does not run ssh-keygen per question.
_FAILURE_RETRY_SECONDS = 60.0
# A good load is verified again after this long, so a steward key removed
# from the signer list stops serving without a restart (contract review 10).
_SUCCESS_REVERIFY_SECONDS = 600.0

_cache: dict[tuple[str, str, str], tuple[ApprovedKnowledge | None, float]] = {}
_loading: set[tuple[str, str, str]] = set()
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class ApprovedKnowledge:
    bundle_name: str
    index_digest: str
    manifest_digest: str
    principal: str
    chunks_bytes: bytes
    postings_bytes: bytes
    repository_root: Path

    @property
    def version(self) -> str:
        """Short form used in citation references: `knowledge:<version>:<chunk>`."""
        return self.index_digest.removeprefix("sha256:")[:12]


def clear_approved_knowledge_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _load(name: str, signers: str, root: Path) -> ApprovedKnowledge | None:
    home = root / BUNDLES_PATH / name
    try:
        approval = verify_approval(
            bundle=home / "bundle",
            repository=root,
            receipt=home / "approval.json",
            signature=home / "approval.json.sig",
            allowed_signers=signers,
            tolerate_source_drift=True,
        )
        chunks = (home / "bundle" / "chunks.jsonl").read_bytes()
        postings = (home / "bundle" / "postings.json").read_bytes()
    except (BundleError, OSError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        logger.warning("Ed knowledge bundle %s not loaded: %s", name, code)
        return None
    if approval.principal is None or name != approval.index_digest.removeprefix("sha256:")[:12]:
        logger.warning("Ed knowledge bundle %s not loaded: name does not match its digest", name)
        return None
    if approval.drifted_sources:
        # Not fatal: retrieval drops each chunk whose source changed, so the
        # claims citing these files are withheld until the steward re-signs.
        logger.warning(
            "Ed knowledge bundle %s loaded with stale sources: %s",
            name,
            ",".join(approval.drifted_sources),
        )
    return ApprovedKnowledge(
        bundle_name=name,
        index_digest=approval.index_digest,
        manifest_digest=approval.manifest_digest,
        principal=approval.principal,
        chunks_bytes=chunks,
        postings_bytes=postings,
        repository_root=root,
    )


def load_approved_knowledge(
    settings: SymgovAPISettings, *, repository_root: str | Path | None = None
) -> ApprovedKnowledge | None:
    """The configured bundle if the steward's signature verifies, else None."""
    name = settings.ed_knowledge_bundle
    signers = settings.ed_allowed_signers
    if not name or not signers or not _BUNDLE_NAME.fullmatch(name):
        return None
    root = Path(repository_root).resolve() if repository_root is not None else REPOSITORY_ROOT
    key = (name, signers, str(root))
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            knowledge, loaded_at = cached
            fresh_for = _SUCCESS_REVERIFY_SECONDS if knowledge is not None else _FAILURE_RETRY_SECONDS
            if now - loaded_at < fresh_for:
                return knowledge
        if key in _loading:
            # Security review L4: a load runs ssh-keygen and must not block
            # other requests. While one runs, serve the last good load if
            # there is one, and otherwise no product knowledge.
            return cached[0] if cached is not None else None
        _loading.add(key)
    try:
        knowledge = _load(name, signers, root)
    finally:
        with _cache_lock:
            _loading.discard(key)
    with _cache_lock:
        _cache[key] = (knowledge, time.monotonic())
    return knowledge


def retrieve_approved(knowledge: ApprovedKnowledge, question: str) -> KnowledgeRetrievalResult:
    """Retrieve from a verified bundle. Source bytes are re-checked every query."""
    return retrieve(
        knowledge.postings_bytes,
        knowledge.chunks_bytes,
        question,
        knowledge_version=knowledge.version,
        approved_index_digest=knowledge.index_digest,
        repository_root=knowledge.repository_root,
    )

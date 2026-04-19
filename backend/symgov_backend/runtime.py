from __future__ import annotations

import hashlib
import hmac
import os
import socket
import struct
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DEPS = BACKEND_ROOT / ".deps"

if BACKEND_DEPS.exists() and str(BACKEND_DEPS) not in sys.path:
    sys.path.insert(0, str(BACKEND_DEPS))

from sqlalchemy import text

from .db import create_session_factory, read_env_file
from .models import (
    AgentDefinition,
    AgentOutputArtifact,
    AgentQueueItem,
    AgentRun,
    Attachment,
    AuditEvent,
    ClassificationRecord,
    ExternalIdentity,
    IntakeRecord,
    ProvenanceAssessment,
    ReviewCase,
    ControlException,
    ValidationReport,
)


DEFAULT_STORAGE_ENV_FILE = Path("/data/.openclaw/workspace/symgov/.env.backend.storage")
LEGACY_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "symgov/runtime-legacy-id")
AGENT_DEFINITION_SEEDS = (
    {
        "slug": "scott",
        "display_name": "Scott",
        "role": "intake agent",
        "model": "ollama/gemma4:e4b",
        "status": "active",
        "queue_family": "intake",
    },
    {
        "slug": "vlad",
        "display_name": "Vlad",
        "role": "technical validation agent",
        "model": "ollama/gemma4:e4b",
        "status": "active",
        "queue_family": "validation",
    },
    {
        "slug": "tracy",
        "display_name": "Tracy",
        "role": "provenance and rights agent",
        "model": "ollama/gemma4:e4b",
        "status": "active",
        "queue_family": "provenance",
    },
    {
        "slug": "daisy",
        "display_name": "Daisy",
        "role": "review coordination agent",
        "model": "ollama/gemma4:e4b",
        "status": "active",
        "queue_family": "review_coordination",
    },
    {
        "slug": "libby",
        "display_name": "Libby",
        "role": "classification and research librarian",
        "model": "ollama/gemma4:e4b",
        "status": "active",
        "queue_family": "classification",
    },
)


def parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc).replace(microsecond=0)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def coerce_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return uuid.uuid5(LEGACY_ID_NAMESPACE, str(value))


def coerce_numeric(value: float | int | str | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def read_storage_env_file(env_file: str | os.PathLike[str] | None = None) -> tuple[Path, dict[str, str]]:
    path = Path(env_file) if env_file else DEFAULT_STORAGE_ENV_FILE
    return path, read_env_file(path)


def _storage_connection_settings(env: dict[str, str]) -> dict[str, str]:
    endpoint = env.get("SYMGOV_S3_ENDPOINT")
    bucket = env.get("SYMGOV_S3_BUCKET")
    region = env.get("SYMGOV_S3_REGION") or "us-east-1"
    access_key_id = env.get("SYMGOV_S3_ACCESS_KEY_ID")
    secret_access_key = env.get("SYMGOV_S3_SECRET_ACCESS_KEY")
    if not endpoint:
        raise RuntimeError("Missing required storage setting: SYMGOV_S3_ENDPOINT")
    if not bucket:
        raise RuntimeError("Missing required storage setting: SYMGOV_S3_BUCKET")
    if not access_key_id:
        raise RuntimeError("Missing required storage setting: SYMGOV_S3_ACCESS_KEY_ID")
    if not secret_access_key:
        raise RuntimeError("Missing required storage setting: SYMGOV_S3_SECRET_ACCESS_KEY")
    return {
        "endpoint": endpoint,
        "bucket": bucket,
        "region": region,
        "access_key_id": access_key_id,
        "secret_access_key": secret_access_key,
    }


def _canonical_object_path(endpoint: str, bucket: str, object_key: str) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    if not parsed.scheme or not parsed.hostname:
        raise RuntimeError("SYMGOV_S3_ENDPOINT must be a full URL with scheme and hostname.")

    prefix = parsed.path.rstrip("/")
    key_path = urllib.parse.quote(object_key.lstrip("/"), safe="/-_.~")
    return f"{prefix}/{bucket}/{key_path}" if prefix else f"/{bucket}/{key_path}"


def _aws_v4_sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _aws_v4_signing_key(secret_key: str, date_stamp: str, region: str, service: str = "s3") -> bytes:
    date_key = _aws_v4_sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    region_key = hmac.new(date_key, region.encode("utf-8"), hashlib.sha256).digest()
    service_key = hmac.new(region_key, service.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()


def download_object_bytes(
    *,
    object_key: str,
    env_file: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    resolved_env_path, env = read_storage_env_file(env_file)
    settings = _storage_connection_settings(env)
    endpoint = settings["endpoint"]
    bucket = settings["bucket"]
    region = settings["region"]
    access_key_id = settings["access_key_id"]
    secret_access_key = settings["secret_access_key"]

    parsed = urllib.parse.urlparse(endpoint)
    host = parsed.netloc
    canonical_uri = _canonical_object_path(endpoint, bucket, object_key)
    payload_hash = hashlib.sha256(b"").hexdigest()
    request_time = datetime.now(timezone.utc)
    amz_date = request_time.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = request_time.strftime("%Y%m%d")

    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [
            "GET",
            canonical_uri,
            "",
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = _aws_v4_signing_key(secret_access_key, date_stamp, region)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    request_url = urllib.parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            canonical_uri,
            "",
            "",
            "",
        )
    )
    request = urllib.request.Request(
        request_url,
        method="GET",
        headers={
            "Authorization": authorization,
            "Host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if not (200 <= response.status < 300):
            raise RuntimeError(f"Storage download failed with HTTP {response.status} for {object_key}")
        payload = response.read()
        content_type = response.headers.get("Content-Type") or "application/octet-stream"
        etag = response.headers.get("ETag")

    return {
        "bucket": bucket,
        "endpoint": endpoint,
        "env_path": str(resolved_env_path),
        "object_key": object_key,
        "payload": payload,
        "content_type": content_type,
        "size_bytes": len(payload),
        "etag": etag,
        "status_code": response.status,
    }


def check_storage_health(env_file: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    resolved_env_path, env = read_storage_env_file(env_file)
    endpoint = env.get("SYMGOV_S3_ENDPOINT")
    if not endpoint:
        raise RuntimeError("Missing required storage setting: SYMGOV_S3_ENDPOINT")

    parsed = urllib.parse.urlparse(endpoint)
    if not parsed.scheme or not parsed.hostname:
        raise RuntimeError("SYMGOV_S3_ENDPOINT must be a full URL with scheme and hostname.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    health_url = urllib.parse.urljoin(endpoint.rstrip("/") + "/", "minio/health/live")
    result: dict[str, Any] = {
        "env_path": str(resolved_env_path),
        "endpoint": endpoint,
        "bucket": env.get("SYMGOV_S3_BUCKET"),
        "region": env.get("SYMGOV_S3_REGION"),
        "access_key_id": env.get("SYMGOV_S3_ACCESS_KEY_ID"),
        "use_ssl": env.get("SYMGOV_S3_USE_SSL"),
        "network_ok": False,
        "healthcheck_ok": False,
    }

    with socket.create_connection((parsed.hostname, port), timeout=5):
        result["network_ok"] = True

    request = urllib.request.Request(health_url, method="GET")
    with urllib.request.urlopen(request, timeout=5) as response:
        result["healthcheck_ok"] = 200 <= response.status < 300
        result["healthcheck_status"] = response.status
        result["healthcheck_url"] = health_url

    return result


def check_database_health(
    env_file: str | os.PathLike[str] | None = None,
    migration: bool = False,
) -> dict[str, Any]:
    session_factory = create_session_factory(env_file=env_file, migration=migration, pool_pre_ping=True)
    engine = session_factory.kw["bind"]
    assert engine is not None
    parsed = urllib.parse.urlparse(str(engine.url))

    startup_params = (
        b"user\x00" + urllib.parse.unquote(parsed.username or "").encode("utf-8") +
        b"\x00database\x00" + (parsed.path or "/").lstrip("/").encode("utf-8") +
        b"\x00application_name\x00symgov-backend-healthcheck\x00"
        + b"client_encoding\x00UTF8\x00\x00"
    )
    startup = struct.pack("!I", len(startup_params) + 8) + struct.pack("!I", 196608) + startup_params
    with socket.create_connection((parsed.hostname or "localhost", parsed.port or 5432), timeout=5) as conn:
        conn.sendall(startup)
        payload = conn.recv(4096)

    result: dict[str, Any] = {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": (parsed.path or "/").lstrip("/"),
        "username": urllib.parse.unquote(parsed.username or ""),
        "network_ok": True,
        "postgres_protocol_ok": bool(payload) and chr(payload[0]) == "R",
        "auth_message_type": chr(payload[0]) if payload else None,
        "auth_code": struct.unpack("!I", payload[5:9])[0] if len(payload) >= 9 and chr(payload[0]) == "R" else None,
    }

    with session_factory() as session:
        current_user, current_database = session.execute(text("select current_user, current_database()")).one()
        table_names = (
            "agent_definitions",
            "agent_queue_items",
            "agent_runs",
            "agent_output_artifacts",
            "intake_records",
            "validation_reports",
            "provenance_assessments",
        )
        table_counts = {
            table_name: session.execute(text(f"select count(*) from {table_name}")).scalar_one()
            for table_name in table_names
        }

    result.update(
        {
            "query_ok": True,
            "current_user": current_user,
            "current_database": current_database,
            "table_counts": table_counts,
        }
    )
    return result


class RuntimePersistenceBridge:
    def __init__(self, env_file: str | os.PathLike[str] | None = None):
        self.session_factory = create_session_factory(env_file=env_file, pool_pre_ping=True)

    @contextmanager
    def session_scope(self):
        with self.session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def seed_agent_definitions(self) -> list[dict[str, str]]:
        operations: list[dict[str, str]] = []
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with self.session_scope() as session:
            for spec in AGENT_DEFINITION_SEEDS:
                row = session.query(AgentDefinition).filter_by(slug=spec["slug"]).one_or_none()
                if row is None:
                    row = AgentDefinition(
                        id=coerce_uuid(f"agent-definition:{spec['slug']}"),
                        slug=spec["slug"],
                        display_name=spec["display_name"],
                        role=spec["role"],
                        model=spec["model"],
                        status=spec["status"],
                        queue_family=spec["queue_family"],
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                    operations.append({"slug": spec["slug"], "action": "inserted"})
                    continue

                changed = False
                for field in ("display_name", "role", "model", "status", "queue_family"):
                    if getattr(row, field) != spec[field]:
                        setattr(row, field, spec[field])
                        changed = True
                row.updated_at = now
                operations.append({"slug": spec["slug"], "action": "updated" if changed else "unchanged"})

        return operations

    def upsert_external_identity(
        self,
        *,
        display_name: str,
        email: str | None = None,
        organization: str | None = None,
        identity_type: str = "submitter",
        status: str = "active",
    ) -> dict[str, str]:
        normalized_email = email.strip().lower() if email else None
        now = datetime.now(timezone.utc).replace(microsecond=0)

        with self.session_scope() as session:
            row = None
            if normalized_email:
                row = (
                    session.query(ExternalIdentity)
                    .filter(text("lower(email) = :email"))
                    .params(email=normalized_email)
                    .one_or_none()
                )

            if row is None:
                row = ExternalIdentity(
                    id=uuid.uuid4(),
                    display_name=display_name,
                    email=normalized_email,
                    organization=organization,
                    identity_type=identity_type,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                action = "inserted"
            else:
                row.display_name = display_name
                row.email = normalized_email
                row.organization = organization
                row.identity_type = identity_type
                row.status = status
                row.updated_at = now
                action = "updated"

            session.flush()
            return {
                "id": str(row.id),
                "display_name": row.display_name,
                "email": row.email or "",
                "action": action,
            }

    def create_attachment(
        self,
        *,
        parent_type: str,
        parent_id: str | uuid.UUID,
        filename: str,
        object_key: str,
        content_type: str,
        size_bytes: int,
        sha256: str | None = None,
    ) -> dict[str, str]:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        attachment_id = uuid.uuid4()
        with self.session_scope() as session:
            row = Attachment(
                id=attachment_id,
                parent_type=parent_type,
                parent_id=coerce_uuid(parent_id),
                filename=filename,
                object_key=object_key,
                content_type=content_type,
                size_bytes=size_bytes,
                sha256=sha256,
                created_at=now,
            )
            session.add(row)
        return {
            "id": str(attachment_id),
            "object_key": object_key,
            "filename": filename,
        }

    def upload_object_bytes(
        self,
        *,
        object_key: str,
        payload: bytes,
        content_type: str,
        env_file: str | os.PathLike[str] | None = None,
    ) -> dict[str, Any]:
        resolved_env_path, env = read_storage_env_file(env_file)
        settings = _storage_connection_settings(env)
        endpoint = settings["endpoint"]
        bucket = settings["bucket"]
        region = settings["region"]
        access_key_id = settings["access_key_id"]
        secret_access_key = settings["secret_access_key"]

        parsed = urllib.parse.urlparse(endpoint)
        host = parsed.netloc
        canonical_uri = _canonical_object_path(endpoint, bucket, object_key)
        payload_hash = hashlib.sha256(payload).hexdigest()
        request_time = datetime.now(timezone.utc)
        amz_date = request_time.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = request_time.strftime("%Y%m%d")

        canonical_headers = (
            f"host:{host}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            [
                "PUT",
                canonical_uri,
                "",
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = _aws_v4_signing_key(secret_access_key, date_stamp, region)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            "AWS4-HMAC-SHA256 "
            f"Credential={access_key_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )

        request_url = urllib.parse.urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                canonical_uri,
                "",
                "",
                "",
            )
        )
        request = urllib.request.Request(
            request_url,
            data=payload,
            method="PUT",
            headers={
                "Authorization": authorization,
                "Content-Length": str(len(payload)),
                "Content-Type": content_type,
                "Host": host,
                "x-amz-content-sha256": payload_hash,
                "x-amz-date": amz_date,
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            if not (200 <= response.status < 300):
                raise RuntimeError(f"Storage upload failed with HTTP {response.status} for {object_key}")
            etag = response.headers.get("ETag")

        return {
            "bucket": bucket,
            "endpoint": endpoint,
            "env_path": str(resolved_env_path),
            "object_key": object_key,
            "content_type": content_type,
            "size_bytes": len(payload),
            "etag": etag,
            "status_code": response.status,
        }

    def upload_file(
        self,
        *,
        object_key: str,
        path: str | os.PathLike[str],
        content_type: str,
        env_file: str | os.PathLike[str] | None = None,
    ) -> dict[str, Any]:
        file_path = Path(path)
        return self.upload_object_bytes(
            object_key=object_key,
            payload=file_path.read_bytes(),
            content_type=content_type,
            env_file=env_file,
        )

    def create_audit_event(
        self,
        *,
        entity_type: str,
        entity_id: str | uuid.UUID,
        action: str,
        payload_json: dict[str, Any],
        actor_id: str | uuid.UUID | None = None,
    ) -> dict[str, str]:
        event_id = uuid.uuid4()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with self.session_scope() as session:
            row = AuditEvent(
                id=event_id,
                entity_type=entity_type,
                entity_id=coerce_uuid(entity_id),
                action=action,
                actor_id=coerce_uuid(actor_id),
                payload_json=payload_json,
                created_at=now,
            )
            session.add(row)
        return {"id": str(event_id), "action": action}

    def create_agent_output_artifact(
        self,
        *,
        queue_item_id: str | uuid.UUID,
        artifact_type: str,
        schema_version: str,
        payload_json: dict[str, Any],
        created_at: str | datetime | None = None,
    ) -> dict[str, str]:
        artifact_id = uuid.uuid4()
        created_value = created_at if isinstance(created_at, datetime) else parse_timestamp(created_at) if created_at else datetime.now(timezone.utc).replace(microsecond=0)
        with self.session_scope() as session:
            row = AgentOutputArtifact(
                id=artifact_id,
                queue_item_id=coerce_uuid(queue_item_id),
                artifact_type=artifact_type,
                schema_version=schema_version,
                payload_json=payload_json,
                created_at=created_value,
            )
            session.add(row)
        return {"id": str(artifact_id), "artifact_type": artifact_type}

    def create_review_case(
        self,
        *,
        source_entity_type: str,
        source_entity_id: str | uuid.UUID,
        current_stage: str,
        escalation_level: str,
        owner_id: str | uuid.UUID | None = None,
        opened_at: str | datetime | None = None,
    ) -> dict[str, str]:
        review_case_id = uuid.uuid4()
        opened_value = opened_at if isinstance(opened_at, datetime) else parse_timestamp(opened_at) if opened_at else datetime.now(timezone.utc).replace(microsecond=0)
        with self.session_scope() as session:
            row = ReviewCase(
                id=review_case_id,
                source_entity_type=source_entity_type,
                source_entity_id=coerce_uuid(source_entity_id),
                current_stage=current_stage,
                owner_id=coerce_uuid(owner_id),
                escalation_level=escalation_level,
                opened_at=opened_value,
                closed_at=None,
            )
            session.add(row)
        return {
            "id": str(review_case_id),
            "source_entity_type": source_entity_type,
            "source_entity_id": str(coerce_uuid(source_entity_id)),
            "current_stage": current_stage,
            "escalation_level": escalation_level,
        }

    def update_review_case(
        self,
        *,
        review_case_id: str | uuid.UUID,
        current_stage: str | None = None,
        escalation_level: str | None = None,
        source_entity_type: str | None = None,
        source_entity_id: str | uuid.UUID | None = None,
    ) -> dict[str, str]:
        with self.session_scope() as session:
            row = session.get(ReviewCase, coerce_uuid(review_case_id))
            if row is None:
                raise RuntimeError(f"Missing review_cases row for id {review_case_id}.")
            if current_stage is not None:
                row.current_stage = current_stage
            if escalation_level is not None:
                row.escalation_level = escalation_level
            if source_entity_type is not None:
                row.source_entity_type = source_entity_type
            if source_entity_id is not None:
                row.source_entity_id = coerce_uuid(source_entity_id)
            session.flush()
            return {
                "id": str(row.id),
                "source_entity_type": row.source_entity_type,
                "source_entity_id": str(row.source_entity_id),
                "current_stage": row.current_stage,
                "escalation_level": row.escalation_level,
            }

    def create_control_exception(
        self,
        *,
        source_type: str,
        source_id: str | uuid.UUID,
        severity: str,
        rule_code: str,
        detail: str,
        status: str = "open",
        created_at: str | datetime | None = None,
    ) -> dict[str, str]:
        exception_id = uuid.uuid4()
        created_value = created_at if isinstance(created_at, datetime) else parse_timestamp(created_at) if created_at else datetime.now(timezone.utc).replace(microsecond=0)
        with self.session_scope() as session:
            row = ControlException(
                id=exception_id,
                source_type=source_type,
                source_id=coerce_uuid(source_id),
                severity=severity,
                rule_code=rule_code,
                detail=detail,
                status=status,
                created_at=created_value,
                updated_at=created_value,
            )
            session.add(row)
        return {"id": str(exception_id), "rule_code": rule_code}

    def persist_agent_execution(
        self,
        queue_item: dict[str, Any],
        run_record: dict[str, Any],
        output_artifact_record: dict[str, Any],
        durable_record: dict[str, Any],
        durable_kind: str,
    ) -> dict[str, str]:
        with self.session_scope() as session:
            agent_definition = session.query(AgentDefinition).filter_by(slug=queue_item["agent_id"]).one_or_none()
            if agent_definition is None:
                raise RuntimeError(f"Missing agent_definitions row for slug {queue_item['agent_id']}.")

            queue_item_id = coerce_uuid(queue_item["id"])
            agent_queue_item = session.get(AgentQueueItem, queue_item_id)
            if agent_queue_item is None:
                agent_queue_item = AgentQueueItem(id=queue_item_id)
                session.add(agent_queue_item)

            agent_queue_item.agent_id = agent_definition.id
            agent_queue_item.source_type = queue_item["source_type"]
            agent_queue_item.source_id = coerce_uuid(queue_item["source_id"])
            agent_queue_item.status = queue_item["status"]
            agent_queue_item.priority = queue_item["priority"]
            agent_queue_item.payload_json = queue_item["payload_json"]
            agent_queue_item.confidence = coerce_numeric(queue_item.get("confidence"))
            agent_queue_item.escalation_reason = queue_item.get("escalation_reason")
            agent_queue_item.created_at = parse_timestamp(queue_item.get("created_at"))
            agent_queue_item.started_at = parse_timestamp(queue_item["started_at"]) if queue_item.get("started_at") else None
            agent_queue_item.completed_at = parse_timestamp(queue_item["completed_at"]) if queue_item.get("completed_at") else None
            session.flush()

            agent_run = session.get(AgentRun, coerce_uuid(run_record["id"]))
            if agent_run is None:
                agent_run = AgentRun(id=coerce_uuid(run_record["id"]))
                session.add(agent_run)
            agent_run.queue_item_id = queue_item_id
            agent_run.model = run_record["model"]
            agent_run.prompt_version = run_record["prompt_version"]
            agent_run.tool_trace_json = run_record["tool_trace_json"]
            agent_run.result_status = run_record["result_status"]
            agent_run.started_at = parse_timestamp(run_record["started_at"])
            agent_run.completed_at = parse_timestamp(run_record["completed_at"])

            output_artifact = session.get(AgentOutputArtifact, coerce_uuid(output_artifact_record["id"]))
            if output_artifact is None:
                output_artifact = AgentOutputArtifact(id=coerce_uuid(output_artifact_record["id"]))
                session.add(output_artifact)
            output_artifact.queue_item_id = queue_item_id
            output_artifact.artifact_type = output_artifact_record["artifact_type"]
            output_artifact.schema_version = output_artifact_record["schema_version"]
            output_artifact.payload_json = output_artifact_record["payload_json"]
            output_artifact.created_at = parse_timestamp(output_artifact_record["created_at"])

            if durable_kind == "intake_record":
                record = session.get(IntakeRecord, coerce_uuid(durable_record["id"]))
                if record is None:
                    record = IntakeRecord(id=coerce_uuid(durable_record["id"]))
                    session.add(record)
                record.queue_item_id = queue_item_id
                record.source_type = durable_record["source_type"]
                record.source_ref = durable_record["source_ref"]
                record.submitter = durable_record["submitter"]
                record.submission_kind = durable_record["submission_kind"]
                record.intake_status = durable_record["intake_status"]
                record.eligibility_status = durable_record["eligibility_status"]
                record.source_package_id = coerce_uuid(durable_record.get("source_package_id"))
                record.raw_object_key = durable_record.get("raw_object_key")
                record.normalized_submission_json = durable_record["normalized_submission_json"]
                record.routing_recommendation_json = durable_record["routing_recommendation_json"]
                record.report_json = durable_record["report_json"]
                record.created_at = parse_timestamp(durable_record["created_at"])
            elif durable_kind == "validation_report":
                record = session.get(ValidationReport, coerce_uuid(durable_record["id"]))
                if record is None:
                    record = ValidationReport(id=coerce_uuid(durable_record["id"]))
                    session.add(record)
                record.queue_item_id = queue_item_id
                record.source_type = durable_record["source_type"]
                record.source_id = coerce_uuid(durable_record["source_id"])
                record.validation_status = durable_record["validation_status"]
                record.defect_count = durable_record["defect_count"]
                record.normalized_payload_json = durable_record["normalized_payload_json"]
                record.report_json = durable_record["report_json"]
                record.created_at = parse_timestamp(durable_record["created_at"])
            elif durable_kind == "provenance_assessment":
                record = session.get(ProvenanceAssessment, coerce_uuid(durable_record["id"]))
                if record is None:
                    record = ProvenanceAssessment(id=coerce_uuid(durable_record["id"]))
                    session.add(record)
                record.queue_item_id = queue_item_id
                record.intake_record_id = coerce_uuid(durable_record["intake_record_id"])
                record.rights_status = durable_record["rights_status"]
                record.risk_level = durable_record["risk_level"]
                record.confidence = coerce_numeric(durable_record["confidence"]) or Decimal("0")
                record.summary = durable_record["summary"]
                record.evidence_json = durable_record["evidence_json"]
                record.report_json = durable_record["report_json"]
                record.assessed_at = parse_timestamp(durable_record["assessed_at"])
            elif durable_kind == "classification_record":
                record_id = coerce_uuid(durable_record["id"])
                record = session.get(ClassificationRecord, record_id)
                symbol_key = durable_record.get("symbol_key")
                if record is None:
                    record = ClassificationRecord(id=record_id)
                    session.add(record)

                if durable_record.get("status") == "current":
                    prior_query = session.query(ClassificationRecord).filter(
                        ClassificationRecord.id != record_id,
                        ClassificationRecord.status == "current",
                    )
                    if symbol_key:
                        prior_query = prior_query.filter(ClassificationRecord.symbol_key == symbol_key)
                    else:
                        prior_query = prior_query.filter(
                            ClassificationRecord.source_type == durable_record["source_type"],
                            ClassificationRecord.source_id == coerce_uuid(durable_record["source_id"]),
                        )

                    prior_records = prior_query.all()
                    supersedes_id = durable_record.get("supersedes_classification_id")
                    if supersedes_id is None and prior_records:
                        supersedes_id = str(prior_records[0].id)

                    for prior in prior_records:
                        prior.status = "obsolete"
                        prior.updated_at = parse_timestamp(durable_record.get("updated_at") or durable_record["created_at"])

                    durable_record["supersedes_classification_id"] = supersedes_id

                record.queue_item_id = queue_item_id
                record.intake_record_id = coerce_uuid(durable_record.get("intake_record_id"))
                record.validation_report_id = coerce_uuid(durable_record.get("validation_report_id"))
                record.provenance_assessment_id = coerce_uuid(durable_record.get("provenance_assessment_id"))
                record.review_case_id = coerce_uuid(durable_record.get("review_case_id"))
                record.origin_attachment_id = coerce_uuid(durable_record.get("origin_attachment_id"))
                record.origin_object_key = durable_record.get("origin_object_key")
                record.origin_file_name = durable_record.get("origin_file_name")
                record.origin_batch_id = durable_record.get("origin_batch_id")
                record.parent_review_case_id = coerce_uuid(durable_record.get("parent_review_case_id"))
                record.symbol_key = symbol_key
                record.symbol_region_index = durable_record.get("symbol_region_index")
                record.status = durable_record.get("status") or "current"
                record.classification_status = durable_record.get("classification_status") or "provisional"
                record.supersedes_classification_id = coerce_uuid(durable_record.get("supersedes_classification_id"))
                record.source_id = coerce_uuid(durable_record["source_id"])
                record.source_type = durable_record["source_type"]
                record.category = durable_record["category"]
                record.discipline = durable_record["discipline"]
                record.format = durable_record.get("format")
                record.industry = durable_record.get("industry")
                record.symbol_family = durable_record.get("symbol_family")
                record.process_category = durable_record.get("process_category")
                record.parent_equipment_class = durable_record.get("parent_equipment_class")
                record.standards_source = durable_record.get("standards_source")
                record.library_provenance_class = durable_record.get("library_provenance_class")
                record.source_classification = durable_record.get("source_classification")
                record.aliases_json = durable_record.get("aliases_json") or []
                record.search_terms_json = durable_record.get("search_terms_json") or []
                record.source_refs_json = durable_record.get("source_refs_json") or []
                record.evidence_json = durable_record.get("evidence_json") or {}
                record.taxonomy_terms_created_json = durable_record.get("taxonomy_terms_created_json") or []
                record.review_summary = durable_record.get("review_summary")
                record.confidence = coerce_numeric(durable_record["confidence"]) or Decimal("0")
                record.libby_approved = bool(durable_record.get("libby_approved"))
                record.created_at = parse_timestamp(durable_record["created_at"])
                record.updated_at = parse_timestamp(durable_record.get("updated_at") or durable_record["created_at"])
            else:
                raise ValueError(f"Unsupported durable_kind: {durable_kind}")

        return {
            "agent_slug": queue_item["agent_id"],
            "queue_item_id": str(queue_item_id),
            "durable_kind": durable_kind,
            "durable_record_id": str(coerce_uuid(durable_record["id"])),
        }

from __future__ import annotations

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
        return {"id": str(review_case_id), "current_stage": current_stage}

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
            else:
                raise ValueError(f"Unsupported durable_kind: {durable_kind}")

        return {
            "agent_slug": queue_item["agent_id"],
            "queue_item_id": str(queue_item_id),
            "durable_kind": durable_kind,
            "durable_record_id": str(coerce_uuid(durable_record["id"])),
        }

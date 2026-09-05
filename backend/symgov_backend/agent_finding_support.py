"""Stage 10 WP10.2/WP10.3 -- shared fingerprint/upsert plumbing for
`AgentFinding` rows, used by both the Organization Steward
(`organization_steward.py`) and Platform Governance (`platform_governance.py`)
deterministic finding-generation services. Neither service invokes an LLM
(Stage 10 plan §4 Q4) -- both compute findings from existing tables only and
call `upsert_active_finding` for each condition they detect.

The deterministic fingerprint (I-22) is a hash over the exact tuple that
identifies "this same underlying condition, for this same target, under
this same detection policy" -- capability scope, finding type, target
entity, and policy version. Retries/re-runs must not create duplicate
active findings: `upsert_active_finding` first looks for an existing row
sharing that fingerprint with `status in ('open', 'acknowledged')` (the
partial unique index `uq_agent_findings_active_fingerprint` enforces this
at the database level too) and only touches `last_seen_at`/`evidence_json`
on it if found: a fresh row is inserted only when no active row shares the
fingerprint (either this is genuinely new, or the prior finding sharing
this fingerprint was already dismissed/resolved/superseded by a human).

Findings are never auto-resolved by either service -- FR-AGT-007 requires a
human to acknowledge/dismiss/resolve/supersede. A finding whose condition
is no longer detected on a later run simply stops being touched; its
`last_seen_at` falls behind the run's own timestamp, which is exactly the
programme plan's own "stale-snapshot detection" signal for the dashboard
(WP10.6) to surface -- not a reason for this module to change its status.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AgentFinding


def compute_fingerprint(
    *,
    logical_agent_name: str,
    scope_type: str,
    scope_id: uuid.UUID | None,
    finding_type: str,
    entity_type: str,
    entity_id: uuid.UUID,
    policy_version: str,
) -> str:
    parts = [
        logical_agent_name,
        scope_type,
        str(scope_id) if scope_id is not None else "",
        finding_type,
        entity_type,
        str(entity_id),
        policy_version,
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def upsert_active_finding(
    session: Session,
    *,
    agent_config_id: uuid.UUID,
    severity: str,
    finding_type: str,
    entity_type: str,
    entity_id: uuid.UUID,
    summary: str,
    evidence: dict,
    policy_version: str,
    fingerprint: str,
    now: datetime,
) -> AgentFinding:
    """Insert a new active finding for `fingerprint`, or touch the existing
    one if a row with this fingerprint is already `open`/`acknowledged`.
    The caller is responsible for committing the session."""
    existing = session.execute(
        select(AgentFinding).where(
            AgentFinding.fingerprint == fingerprint,
            AgentFinding.status.in_(("open", "acknowledged")),
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.last_seen_at = now
        existing.evidence_json = evidence
        existing.summary = summary
        existing.severity = severity
        return existing

    finding = AgentFinding(
        agent_config_id=agent_config_id,
        severity=severity,
        finding_type=finding_type,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        evidence_json=evidence,
        policy_version=policy_version,
        fingerprint=fingerprint,
        status="open",
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
    )
    session.add(finding)
    return finding

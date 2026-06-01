from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
import re

from sqlalchemy import desc

from .models import ClassificationRecord, ProvenanceAssessment, ReviewCase, ReviewSplitItem, ReviewSymbolProperty, ValidationReport
from .runtime import RuntimePersistenceBridge, coerce_uuid

AUTOMATION_POLICY_VERSION = "symgov-automation-policy-v1"
PLACEHOLDER_VALUES = {"", "unknown", "tbd", "todo", "pending", "uncategorized", "general", "n/a", "na", "none"}
PLACEHOLDER_CATEGORIES = PLACEHOLDER_VALUES | {"symbol", "symbols", "unclassified_symbol", "symbol_sheet", "mixed_symbol_set"}
PLACEHOLDER_DISCIPLINES = PLACEHOLDER_VALUES | {"general", "general_industry", "unknown_discipline"}
GENERIC_SPLIT_NAME_PATTERNS = (
    re.compile(r"^\d{1,3}[-_ ]+[a-z0-9]+[-_ ]+region[-_ ]+\d{1,4}$", re.IGNORECASE),
    re.compile(r"^region[-_ ]+\d{1,4}$", re.IGNORECASE),
    re.compile(r"^symbol[-_ ]*\d{1,4}$", re.IGNORECASE),
    re.compile(r"^child[-_ ]*\d{1,4}$", re.IGNORECASE),
)
LOW_RISK_RIGHTS_STATUSES = {
    "approved",
    "cleared",
    "low_risk",
    "permissive",
    "public_domain",
    "public domain",
    "cc0",
    "cc-by",
    "cc by",
}
LOW_RISK_PROVENANCE_RISK_LEVELS = {"low", "none", "minimal"}
PASS_VALIDATION_STATUSES = {"pass", "passed", "valid"}
BLOCKING_REVIEW_STAGES = {
    "classification_review",
    "raster_split_review",
    "changes_requested",
    "human_review",
    "review_required",
}


@dataclass(frozen=True)
class AutomationGateDecision:
    allowed: bool
    next_agent: str | None
    reasons: list[str]
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "next_agent": self.next_agent,
            "reasons": self.reasons,
            "evidence": self.evidence,
        }


def _reasonable_value(value: Any) -> bool:
    normalized = " ".join(str(value or "").strip().lower().split())
    return normalized not in PLACEHOLDER_VALUES and len(normalized) >= 3


def _normalized_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def _is_generic_split_name(
    name: Any,
    *,
    proposed_symbol_id: Any = None,
    file_name: Any = None,
    name_source: Any = None,
) -> bool:
    normalized_name = _normalized_token(name)
    if not normalized_name:
        return True
    compact_name = normalized_name.replace(" ", "-")
    for pattern in GENERIC_SPLIT_NAME_PATTERNS:
        if pattern.match(compact_name):
            return True
    proposed = _normalized_token(proposed_symbol_id)
    file_stem = Path(str(file_name)).stem if file_name else ""
    file_token = _normalized_token(file_stem)
    if proposed and normalized_name == proposed and str(name_source or "").strip().lower() == "fallback":
        return True
    if file_token and normalized_name == file_token and str(name_source or "").strip().lower() == "fallback":
        return True
    if str(name_source or "").strip().lower() == "fallback" and " region " in f" {normalized_name} ":
        return True
    return False


def evaluate_symbol_metadata_gate(
    *,
    name: Any,
    category: Any,
    discipline: Any,
    proposed_symbol_id: Any = None,
    file_name: Any = None,
    name_source: Any = None,
) -> AutomationGateDecision:
    reasons: list[str] = []
    category_key = "_".join(_normalized_token(category).split())
    discipline_key = "_".join(_normalized_token(discipline).split())

    if not _reasonable_value(name):
        reasons.append("symbol_name_missing_or_placeholder")
    elif _is_generic_split_name(
        name,
        proposed_symbol_id=proposed_symbol_id,
        file_name=file_name,
        name_source=name_source,
    ):
        reasons.append("symbol_name_is_generic_split_fallback")

    if category_key in PLACEHOLDER_CATEGORIES or len(category_key) < 3:
        reasons.append("category_missing_or_placeholder")
    if discipline_key in PLACEHOLDER_DISCIPLINES or len(discipline_key) < 3:
        reasons.append("discipline_missing_or_placeholder")

    return AutomationGateDecision(
        allowed=not reasons,
        next_agent="rupert" if not reasons else "daisy",
        reasons=reasons,
        evidence={
            "policy_version": AUTOMATION_POLICY_VERSION,
            "name": str(name or ""),
            "category": str(category or ""),
            "discipline": str(discipline or ""),
            "proposed_symbol_id": str(proposed_symbol_id or ""),
            "file_name": str(file_name or ""),
            "name_source": str(name_source or ""),
        },
    )


def _decimal_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_publication_automation_gate(
    classification: ClassificationRecord,
    *,
    validation: ValidationReport | None,
    provenance: ProvenanceAssessment | None,
    review_case: ReviewCase | None,
) -> AutomationGateDecision:
    reasons: list[str] = []

    evidence = {
        "policy_version": AUTOMATION_POLICY_VERSION,
        "classification_id": str(classification.id),
        "classification_status": classification.classification_status,
        "classification_record_status": classification.status,
        "classification_confidence": _decimal_float(classification.confidence),
        "libby_approved": bool(classification.libby_approved),
        "category": classification.category,
        "discipline": classification.discipline,
        "source_classification": classification.source_classification,
        "validation_report_id": str(validation.id) if validation else None,
        "validation_status": validation.validation_status if validation else None,
        "validation_defect_count": validation.defect_count if validation else None,
        "provenance_assessment_id": str(provenance.id) if provenance else None,
        "rights_status": provenance.rights_status if provenance else None,
        "provenance_risk_level": provenance.risk_level if provenance else None,
        "review_case_id": str(review_case.id) if review_case else None,
        "review_case_stage": review_case.current_stage if review_case else None,
    }

    if classification.status != "current":
        reasons.append("classification_record_not_current")
    if classification.classification_status != "classified":
        reasons.append("classification_not_final")
    if not classification.libby_approved:
        reasons.append("libby_not_approved_for_no_human_review")
    if not _reasonable_value(classification.category):
        reasons.append("category_missing_or_placeholder")
    if not _reasonable_value(classification.discipline):
        reasons.append("discipline_missing_or_placeholder")
    if str(classification.category or "").strip().lower() in {"symbol_sheet", "unclassified_symbol"}:
        reasons.append("not_a_single_low_risk_symbol")

    if validation is None:
        reasons.append("validation_report_missing")
    else:
        if str(validation.validation_status or "").strip().lower() not in PASS_VALIDATION_STATUSES:
            reasons.append("validation_not_passed")
        if int(validation.defect_count or 0) != 0:
            reasons.append("validation_defects_present")

    if provenance is None:
        reasons.append("provenance_assessment_missing")
    else:
        rights_status = str(provenance.rights_status or "").strip().lower()
        risk_level = str(provenance.risk_level or "").strip().lower()
        if rights_status not in LOW_RISK_RIGHTS_STATUSES:
            reasons.append("rights_not_low_risk")
        if risk_level not in LOW_RISK_PROVENANCE_RISK_LEVELS:
            reasons.append("provenance_risk_not_low")

    if review_case is not None and str(review_case.current_stage or "").strip().lower() in BLOCKING_REVIEW_STAGES:
        reasons.append("human_review_case_still_open")

    allowed = not reasons
    return AutomationGateDecision(
        allowed=allowed,
        next_agent="rupert" if allowed else "daisy",
        reasons=[] if allowed else reasons,
        evidence=evidence,
    )


def evaluate_publication_automation_candidates(
    *,
    db_env_file: str | Path | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    bridge = RuntimePersistenceBridge(env_file=str(db_env_file) if db_env_file else None)
    decisions: list[dict[str, Any]] = []
    with bridge.session_scope() as session:
        classifications = (
            session.query(ClassificationRecord)
            .filter(ClassificationRecord.status == "current")
            .order_by(desc(ClassificationRecord.created_at))
            .limit(limit)
            .all()
        )
        for classification in classifications:
            validation = session.get(ValidationReport, classification.validation_report_id) if classification.validation_report_id else None
            provenance = session.get(ProvenanceAssessment, classification.provenance_assessment_id) if classification.provenance_assessment_id else None
            review_case = session.get(ReviewCase, classification.review_case_id) if classification.review_case_id else None
            decision = evaluate_publication_automation_gate(
                classification,
                validation=validation,
                provenance=provenance,
                review_case=review_case,
            )
            decisions.append(decision.as_dict())

    allowed = [item for item in decisions if item["allowed"]]
    blocked = [item for item in decisions if not item["allowed"]]
    reason_counts: dict[str, int] = {}
    for item in blocked:
        for reason in item["reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "policy_version": AUTOMATION_POLICY_VERSION,
        "candidate_count": len(decisions),
        "allowed_count": len(allowed),
        "blocked_count": len(blocked),
        "blocked_reason_counts": dict(sorted(reason_counts.items())),
        "decisions": decisions,
    }


def evaluate_publication_automation_candidate(
    classification_id: str,
    *,
    db_env_file: str | Path | None = None,
) -> dict[str, Any]:
    bridge = RuntimePersistenceBridge(env_file=str(db_env_file) if db_env_file else None)
    with bridge.session_scope() as session:
        classification = session.get(ClassificationRecord, coerce_uuid(classification_id))
        if classification is None:
            raise RuntimeError(f"Classification record not found: {classification_id}")
        validation = session.get(ValidationReport, classification.validation_report_id) if classification.validation_report_id else None
        provenance = session.get(ProvenanceAssessment, classification.provenance_assessment_id) if classification.provenance_assessment_id else None
        review_case = session.get(ReviewCase, classification.review_case_id) if classification.review_case_id else None
        return evaluate_publication_automation_gate(
            classification,
            validation=validation,
            provenance=provenance,
            review_case=review_case,
        ).as_dict()


def evaluate_review_split_metadata_candidates(
    *,
    db_env_file: str | Path | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    bridge = RuntimePersistenceBridge(env_file=str(db_env_file) if db_env_file else None)
    decisions: list[dict[str, Any]] = []
    with bridge.session_scope() as session:
        rows = (
            session.query(ReviewSymbolProperty, ReviewSplitItem)
            .join(ReviewSplitItem, ReviewSplitItem.id == ReviewSymbolProperty.review_split_item_id)
            .order_by(desc(ReviewSymbolProperty.updated_at))
            .limit(limit)
            .all()
        )
        for properties, split_item in rows:
            decision = evaluate_symbol_metadata_gate(
                name=properties.name,
                category=properties.category,
                discipline=properties.discipline,
                proposed_symbol_id=split_item.proposed_symbol_id,
                file_name=split_item.file_name,
                name_source=split_item.name_source,
            ).as_dict()
            decision["evidence"] = {
                **decision["evidence"],
                "review_symbol_properties_id": str(properties.id),
                "review_split_item_id": str(split_item.id),
                "review_case_id": str(properties.review_case_id),
                "property_source": properties.source,
                "property_updated_by": properties.updated_by,
            }
            decisions.append(decision)

    allowed = [item for item in decisions if item["allowed"]]
    blocked = [item for item in decisions if not item["allowed"]]
    reason_counts: dict[str, int] = {}
    for item in blocked:
        for reason in item["reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "policy_version": AUTOMATION_POLICY_VERSION,
        "candidate_count": len(decisions),
        "allowed_count": len(allowed),
        "blocked_count": len(blocked),
        "blocked_reason_counts": dict(sorted(reason_counts.items())),
        "decisions": decisions,
    }

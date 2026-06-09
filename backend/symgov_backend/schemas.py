from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


REVIEW_SYMBOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9 \-/$]*$")


class APIHealthResponse(BaseModel):
    ok: bool
    service: str
    time: str


class APIErrorResponse(BaseModel):
    error: str
    detail: str


class ExternalSubmissionFileInput(BaseModel):
    name: str = Field(min_length=1)
    note: str = ""
    content_type: str = "application/octet-stream"
    content_base64: str = Field(min_length=1)


class ExternalSubmissionRequest(BaseModel):
    pin: str = Field(min_length=4, max_length=4)
    submitter_name: str = Field(min_length=1)
    submitter_email: str = Field(min_length=3)
    overall_description: str = Field(min_length=1)
    files: list[ExternalSubmissionFileInput] = Field(min_length=1)


class ExternalSubmissionQueueItemResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    fileName: str
    fileNote: str
    batchSummary: str
    status: str = "queued"
    routes: list[str]
    payload: dict[str, Any]
    attachmentId: str
    attachmentObjectKey: str
    scottQueueItemPath: str | None = None
    intakeRecordId: str | None = None
    intakeStatus: str = "pending"
    eligibilityStatus: str = "pending"
    dbPersistence: dict[str, Any] | None = None
    downstreamCreated: dict[str, Any]


class ExternalSubmissionResponse(BaseModel):
    batchId: str
    createdAt: str
    submitterName: str
    submitterEmail: str
    sharedSummary: str
    queueItems: list[ExternalSubmissionQueueItemResponse]


class WorkspaceReviewChildResponse(BaseModel):
    id: str
    proposedSymbolId: str
    proposedSymbolName: str
    displayName: str | None = None
    packageDisplayId: str | None = None
    packageSymbolSequence: int | None = None
    fileName: str
    parentFileName: str
    nameSource: str | None = None
    attachmentObjectKey: str | None = None
    previewUrl: str | None = None
    reviewStatus: str = "awaiting_decision"
    latestAction: str | None = None
    latestNote: str | None = None
    latestDetails: str | None = None
    processedAt: str | None = None
    downstreamAgentSlug: str | None = None
    downstreamQueueItemId: str | None = None
    duplicateReview: dict[str, Any] | None = None


class WorkspaceReviewSymbolPropertiesResponse(BaseModel):
    id: str
    reviewCaseId: str
    splitItemId: str | None = None
    symbolRecordKey: str
    name: str
    description: str = ""
    category: str | None = None
    discipline: str | None = None
    format: str | None = None
    source: str
    updatedBy: str | None = None
    updatedAt: str


class WorkspaceReviewSymbolPropertyOptionResponse(BaseModel):
    fieldName: str
    value: str
    useCount: int
    lastUsedAt: str


class WorkspaceReviewSymbolPropertyOptionListResponse(BaseModel):
    items: list[WorkspaceReviewSymbolPropertyOptionResponse]


class WorkspaceHumanReviewDecisionSummary(BaseModel):
    id: str
    decisionCode: str
    decisionSummary: str | None = None
    decisionNote: str | None = None
    deciderName: str
    deciderRole: str
    fromStage: str
    toStage: str | None = None
    createdAt: str


class WorkspaceReviewCaseResponse(BaseModel):
    id: str
    reviewItemType: str = "review_case"
    parentReviewCaseId: str | None = None
    splitItemId: str | None = None
    splitChildKey: str | None = None
    splitChildStatus: str | None = None
    symbolId: str
    displayName: str | None = None
    packageDisplayId: str | None = None
    packageSymbolSequence: int | None = None
    title: str
    owner: str
    due: str
    priority: str
    risk: str
    pages: int
    packs: int
    status: str
    summary: str
    clarifications: list[str]
    currentStage: str
    escalationLevel: str
    openedAt: str
    validationStatus: str
    defectCount: int
    sourceFileName: str
    sourceObjectKey: str | None = None
    sourcePreviewUrl: str | None = None
    intakeRecordId: str
    childCount: int
    classificationStatus: str | None = None
    classificationConfidence: float | None = None
    libbyApproved: bool | None = None
    engineeringDiscipline: str | None = None
    format: str | None = None
    industry: str | None = None
    symbolFamily: str | None = None
    processCategory: str | None = None
    parentEquipmentClass: str | None = None
    standardsSource: str | None = None
    libraryProvenanceClass: str | None = None
    sourceClassification: str | None = None
    aliases: list[str] = []
    keywords: list[str] = []
    sourceRefs: list[str] = []
    classificationSummary: str | None = None
    latestDecision: WorkspaceHumanReviewDecisionSummary | None = None
    symbolProperties: WorkspaceReviewSymbolPropertiesResponse | None = None
    children: list[WorkspaceReviewChildResponse]


class WorkspaceReviewCaseListResponse(BaseModel):
    items: list[WorkspaceReviewCaseResponse]


class WorkspaceAgentQueueItemResponse(BaseModel):
    id: str
    agentId: str
    agentName: str
    queueFamily: str
    sourceType: str
    sourceId: str
    displayName: str | None = None
    packageDisplayId: str | None = None
    packageSymbolSequence: int | None = None
    status: str
    priority: str
    payload: dict[str, Any]
    toolSummary: list[str] = Field(default_factory=list)
    publishedSymbolId: str | None = None
    publishedPageCode: str | None = None
    publishedPackCode: str | None = None
    publishedStandardsPath: str | None = None
    confidence: float | None = None
    escalationReason: str | None = None
    createdAt: str
    startedAt: str | None = None
    completedAt: str | None = None


class WorkspaceAgentQueueItemListResponse(BaseModel):
    items: list[WorkspaceAgentQueueItemResponse]


class WorkspaceReggieQueueControlSuggestionResponse(BaseModel):
    id: str
    sourceType: str
    sourceId: str | None = None
    severity: str
    ruleCode: str
    detail: str
    status: str
    suggestedRemediation: str
    observationalOnly: bool = True
    evidence: dict[str, Any] = Field(default_factory=dict)


class WorkspaceReggieQueueControlListResponse(BaseModel):
    generatedAt: str
    dryRun: bool = True
    activeOnly: bool = True
    agents: list[str]
    runtimeRecordsSeen: int = 0
    dbActiveRowsInspected: int = 0
    changeCount: int = 0
    missingRuntimeCount: int = 0
    runtimeOrphanCount: int = 0
    skippedCount: int = 0
    controlSuggestionCount: int = 0
    items: list[WorkspaceReggieQueueControlSuggestionResponse]


class WorkspaceScottSourceSearchStartRequest(BaseModel):
    durationSeconds: int = Field(default=120, ge=30, le=300)
    seedQuery: str | None = Field(default=None, max_length=200)


class WorkspaceScottSourceSearchStartResponse(BaseModel):
    queueItemId: str
    status: str
    durationSeconds: int
    startedAt: str
    expectedCompletedAt: str


class WorkspaceScottSourceSearchStopResponse(BaseModel):
    queueItemId: str
    status: str
    stoppedAt: str
    termination: str


class WorkspaceScottSourceSiteResponse(BaseModel):
    id: str
    url: str
    status: str
    title: str | None = None
    domain: str
    description: str | None = None
    industry: str | None = None
    process: str | None = None
    organizationType: str | None = None
    sourcePrompt: str | None = None
    includeNextRun: bool = False
    symbolFormats: list[Any] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    relevanceScore: float | None = None
    firstSeenAt: str
    lastSeenAt: str
    lastSessionQueueItemId: str | None = None


class WorkspaceScottSourceSitePromptUpdateRequest(BaseModel):
    sourcePrompt: str | None = Field(default=None, max_length=4000)


class WorkspaceScottSourceSiteIncludeNextRunUpdateRequest(BaseModel):
    includeNextRun: bool


class WorkspaceScottSourceSiteStatusUpdateRequest(BaseModel):
    status: str = Field(min_length=1, max_length=64)

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized == "ignore":
            return "ignored"
        allowed = {"candidate", "low_signal", "ignored"}
        if normalized not in allowed:
            raise ValueError(f"Unsupported Scott source status: {value}")
        return normalized


class WorkspaceScottSourceSiteListResponse(BaseModel):
    items: list[WorkspaceScottSourceSiteResponse]
    total: int
    offset: int
    limit: int
    hasMore: bool


class WorkspaceHannahCurationSearchStartRequest(BaseModel):
    durationSeconds: int = Field(default=120, ge=30, le=300)


class WorkspaceHannahCurationSearchStartResponse(BaseModel):
    queueItemId: str
    status: str
    durationSeconds: int
    startedAt: str
    expectedCompletedAt: str
    createdCount: int = 0
    skippedCount: int = 0
    message: str | None = None


class WorkspaceHannahCurationSearchStopResponse(BaseModel):
    queueItemId: str
    status: str
    stoppedAt: str
    termination: str


class WorkspaceHannahPhotoCandidateResponse(BaseModel):
    id: str
    symbolId: str
    symbolSlug: str | None = None
    symbolName: str | None = None
    pageTitle: str | None = None
    category: str | None = None
    discipline: str | None = None
    sourceUrl: str
    imageUrl: str
    sourceDomain: str
    title: str | None = None
    description: str | None = None
    rightsStatus: str
    licenseLabel: str | None = None
    status: str
    relevanceScore: float | None = None
    previewUrl: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    firstSeenAt: str
    lastSeenAt: str
    lastSessionQueueItemId: str | None = None


class WorkspaceHannahPhotoCandidateListResponse(BaseModel):
    items: list[WorkspaceHannahPhotoCandidateResponse]
    total: int
    offset: int
    limit: int
    hasMore: bool


class WorkspaceHannahCleanupActionRequest(BaseModel):
    message: str = Field(min_length=3, max_length=600)


class WorkspaceHannahCleanupActionResponse(BaseModel):
    action: str
    recordRef: str
    status: str
    detail: str
    changes: dict[str, Any] = Field(default_factory=dict)


class WorkspaceWhitneyDemandScanStartRequest(BaseModel):
    durationSeconds: int = Field(default=120, ge=30, le=300)
    focus: str | None = Field(default=None, max_length=120)


class WorkspaceWhitneyDemandScanStartResponse(BaseModel):
    queueItemId: str
    status: str
    durationSeconds: int
    startedAt: str
    expectedCompletedAt: str


class WorkspaceWhitneyDemandScanStopResponse(BaseModel):
    queueItemId: str
    status: str
    stoppedAt: str
    termination: str


class WorkspaceWhitneyDemandSignalResponse(BaseModel):
    id: str
    signalType: str
    marketSegment: str | None = None
    discipline: str | None = None
    category: str | None = None
    sourceType: str
    sourceRef: str | None = None
    symbolId: str | None = None
    symbolSlug: str | None = None
    symbolName: str | None = None
    pageTitle: str | None = None
    title: str
    summary: str
    demandScore: float | None = None
    confidence: float | None = None
    recommendedAction: str | None = None
    status: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    firstSeenAt: str
    lastSeenAt: str
    lastSessionQueueItemId: str | None = None


class WorkspaceWhitneyDemandSignalListResponse(BaseModel):
    items: list[WorkspaceWhitneyDemandSignalResponse]
    total: int
    offset: int
    limit: int
    hasMore: bool


class WorkspaceDaisyAssignmentProposalResponse(BaseModel):
    proposalRank: int
    reviewer: str
    role: str
    reason: str


class WorkspaceDaisyStageTransitionResponse(BaseModel):
    fromStage: str
    toStage: str
    action: str
    reason: str


class WorkspaceDaisyEvidenceRequestResponse(BaseModel):
    requestType: str
    detail: str


class WorkspaceDaisyReportResponse(BaseModel):
    id: str
    queueItemId: str
    reviewCaseId: str | None = None
    sourceType: str | None = None
    sourceId: str | None = None
    coordinationStatus: str
    coordinationSummary: str
    createdAt: str
    currentStage: str | None = None
    escalationLevel: str | None = None
    decision: str | None = None
    confidence: float | None = None
    escalationTarget: str | None = None
    defectCount: int
    assignmentProposals: list[WorkspaceDaisyAssignmentProposalResponse]
    stageTransitionProposals: list[WorkspaceDaisyStageTransitionResponse]
    contributorEvidenceRequests: list[WorkspaceDaisyEvidenceRequestResponse]


class WorkspaceDaisyReportListResponse(BaseModel):
    items: list[WorkspaceDaisyReportResponse]


class WorkspaceReviewChildDecisionInput(BaseModel):
    childId: str = Field(min_length=1)
    action: str = Field(min_length=1)
    note: str = ""
    details: str = ""
    proposedSymbolName: str | None = None
    proposedSymbolId: str | None = None


class WorkspaceReviewDecisionRequest(BaseModel):
    decisionCode: str = Field(min_length=1)
    decisionNote: str = ""
    deciderName: str = "SME reviewer"
    deciderRole: str = "sme_reviewer"
    childDecisions: list[WorkspaceReviewChildDecisionInput] = Field(default_factory=list)
    caseComment: str = ""


class WorkspaceReviewSymbolPropertiesUpdateRequest(BaseModel):
    splitItemId: str | None = None
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=256)
    category: str | None = Field(default=None, max_length=80)
    discipline: str | None = Field(default=None, max_length=80)
    format: str | None = Field(default=None, max_length=40)
    updatedBy: str = Field(default="Human", max_length=80)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not REVIEW_SYMBOL_NAME_PATTERN.match(trimmed):
            raise ValueError("Name may only contain letters, numbers, spaces, hyphens, slashes, and dollar signs.")
        return trimmed

    @field_validator("description", "category", "discipline", "format", "updatedBy")
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class WorkspaceSplitReviewProcessRequest(BaseModel):
    deciderName: str = "SME reviewer"
    deciderRole: str = "sme_reviewer"
    caseComment: str = ""
    childDecisions: list[WorkspaceReviewChildDecisionInput] = Field(default_factory=list)


class WorkspaceSplitReviewProcessItemResponse(BaseModel):
    childId: str
    action: str
    status: str
    targetAgentSlug: str | None = None
    downstreamQueueItemId: str | None = None
    decisionId: str | None = None


class WorkspaceSplitReviewProcessResponse(BaseModel):
    reviewCaseId: str
    processedCount: int
    skippedPendingCount: int
    remainingOpenCount: int
    items: list[WorkspaceSplitReviewProcessItemResponse]
    currentStage: str
    closedAt: str | None = None


class WorkspaceReviewActionResponse(BaseModel):
    id: str
    actionCode: str
    actionStatus: str
    targetAgentSlug: str | None = None
    targetStage: str | None = None
    createdAt: str


class WorkspaceReviewDecisionResponse(BaseModel):
    reviewCaseId: str
    decision: WorkspaceHumanReviewDecisionSummary
    actions: list[WorkspaceReviewActionResponse]
    currentStage: str
    closedAt: str | None = None

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError


REVIEW_SYMBOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9 \-/$]*$")


class APIHealthResponse(BaseModel):
    ok: bool
    service: str
    time: str


class APIErrorResponse(BaseModel):
    error: str
    detail: str


class APIValidationErrorResponse(APIErrorResponse):
    issues: list[dict[str, Any]]


class CatalogSelfServiceApiKeyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customerName: str = Field(min_length=1, max_length=200)
    integrationName: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(min_length=1, max_length=10)
    expiresAt: datetime | None = None


class CatalogSelfServiceApiKeyRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyId: str = Field(min_length=36, max_length=36)
    keyPrefix: str = Field(min_length=1, max_length=100)


class CatalogSelfServiceApiKeyCreateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: CatalogSelfServiceApiKeyCreateRequest


class CatalogSelfServiceApiKeyRevokeEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: CatalogSelfServiceApiKeyRevokeRequest


class AuthLoginRequest(BaseModel):
    email: str = Field(min_length=3)
    pin: str = Field(min_length=4, max_length=4)


class SubscriptionResponse(BaseModel):
    tier: str
    startedOn: str
    expiresOn: str | None
    isActive: bool
    isProtected: bool


class AuthUserResponse(BaseModel):
    id: str
    email: str
    displayName: str
    roles: list[str]
    mustChangePin: bool
    subscription: SubscriptionResponse
    session: dict[str, Any]
    organization: dict[str, Any] | None
    isPlatformAdmin: bool
    capabilities: dict[str, bool]
    recentStepUpAt: str | None = None


class AuthSelectionChallengeResponse(BaseModel):
    token: str
    expiresAt: str
    choices: list[dict[str, str]]
    page: int
    pageSize: int
    total: int
    hasMore: bool


class AuthSelectOrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)
    organizationId: uuid.UUID | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=5, ge=1, le=5)


class AuthLoginResponse(BaseModel):
    user: AuthUserResponse | None
    selectionChallenge: AuthSelectionChallengeResponse | None = None


class AuthMeResponse(BaseModel):
    user: AuthUserResponse | None


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    name: str
    shortDescription: str | None = None
    externalReference: str | None = None
    metadata: dict[str, Any] | None = None


class ProjectPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    shortDescription: str | None = None
    externalReference: str | None = None
    metadata: dict[str, Any] = cast(dict[str, Any], None)
    status: str = cast(str, None)

    @model_validator(mode="after")
    def require_field(self):
        if not self.model_fields_set:
            raise ValueError("At least one field is required.")
        return self

    def only_status(self) -> bool:
        return self.model_fields_set == {"status"}


class SymbolSetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    name: str
    description: str | None = None
    disciplines: list[str] | None = None
    useCases: list[str] | None = None


class SymbolSetPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    description: str | None = None
    disciplines: list[str] = cast(list[str], None)
    useCases: list[str] = cast(list[str], None)
    status: str = cast(str, None)

    @model_validator(mode="after")
    def require_field(self):
        if not self.model_fields_set:
            raise ValueError("At least one field is required.")
        return self


class ProjectSummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    shortDescription: str | None
    status: str


class ProjectResponse(ProjectSummary):
    externalReference: str | None
    metadata: dict[str, Any]
    createdAt: datetime
    updatedAt: datetime
    closedAt: datetime | None


class SymbolSetSummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    disciplines: list[str]
    useCases: list[str]
    status: str


class SymbolSetResponse(SymbolSetSummary):
    copiedFromSymbolSetId: uuid.UUID | None
    createdAt: datetime
    updatedAt: datetime
    supersededAt: datetime | None
    archivedAt: datetime | None


class PagedProjectResponse(BaseModel):
    items: list[ProjectResponse]
    page: int
    pageSize: int
    total: int


class PagedSymbolSetResponse(BaseModel):
    items: list[SymbolSetResponse]
    page: int
    pageSize: int
    total: int


class SymbolSetCopyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    name: str


class SymbolSetItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    governedSymbolId: uuid.UUID
    sortOrder: int = Field(ge=0)
    groupName: str | None = None
    displayLabel: str | None = None
    notes: str | None = None
    preferredFormat: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class SymbolSetItemsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[SymbolSetItemInput] = Field(max_length=1000)
    etag: str | None = Field(default=None, min_length=3, max_length=200)


class SymbolSetItemResponse(BaseModel):
    id: uuid.UUID
    governedSymbolId: uuid.UUID
    catalogSymbolId: str | None = None
    displayId: str | None = None
    sortOrder: int
    groupName: str | None
    displayLabel: str | None
    notes: str | None
    preferredFormat: str | None
    provenance: dict[str, Any]
    currentRevisionId: uuid.UUID | None
    availabilityStatus: str
    availabilityReason: str | None
    canonicalName: str | None = None
    category: str | None = None
    discipline: str | None = None
    slug: str | None = None
    createdAt: datetime
    updatedAt: datetime


class SymbolSetItemsResponse(BaseModel):
    items: list[SymbolSetItemResponse]
    page: int
    pageSize: int
    total: int
    etag: str


class SymbolSetProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: uuid.UUID
    isDefault: bool = False


class SymbolSetProjectsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projects: list[SymbolSetProjectInput] = Field(max_length=500)


class SymbolSetProjectEntry(BaseModel):
    project: ProjectSummary
    isDefault: bool


class SymbolSetProjectsResponse(BaseModel):
    items: list[SymbolSetProjectEntry]
    page: int
    pageSize: int
    total: int


class OrganizationDefaultSymbolSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    setId: uuid.UUID


class OrganizationDefaultSymbolSetResponse(BaseModel):
    defaultSymbolSetId: uuid.UUID


class ProjectSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: uuid.UUID


class ActiveSetSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    setCode: str


class SymbolContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selectedProject: ProjectSummary | None
    activeSet: SymbolSetSummary | None
    reason: Literal["explicit", "user_preference", "project_default", "organization_default", "none"]


class EffectivePaletteEntryResponse(BaseModel):
    governedSymbolId: uuid.UUID
    catalogSymbolId: str | None = None
    displayId: str | None = None
    source: Literal["set", "organization_wide"]
    canonicalName: str
    category: str
    discipline: str
    sortOrder: int
    groupName: str | None
    displayLabel: str | None
    preferredFormat: str | None
    notes: str | None
    provenance: dict[str, Any]
    currentRevisionId: uuid.UUID | None


class EffectivePaletteResponse(BaseModel):
    activeSet: SymbolSetSummary | None
    reason: Literal["explicit", "user_preference", "project_default", "organization_default", "none"]
    items: list[EffectivePaletteEntryResponse]
    page: int
    pageSize: int
    total: int


class SymbolSetBuilderSearchEntryResponse(BaseModel):
    governedSymbolId: uuid.UUID
    catalogSymbolId: str | None = None
    displayId: str | None = None
    source: Literal["public", "organization"]
    canonicalName: str
    category: str
    discipline: str
    slug: str
    organizationWide: bool | None
    currentRevisionId: uuid.UUID | None


class SymbolSetBuilderSearchResponse(BaseModel):
    items: list[SymbolSetBuilderSearchEntryResponse]
    page: int
    pageSize: int
    total: int


class ProfileUpgradeOptionResponse(BaseModel):
    years: int
    totalPricePence: int
    expiresOn: str


class ProfilePlanResponse(BaseModel):
    currency: str = "GBP"
    annualPricePence: int = 5000
    minimumYears: int = 1
    maximumYears: int = 5
    paymentRequired: bool = False
    upgradeOptions: list[ProfileUpgradeOptionResponse]


class ProfileResponse(BaseModel):
    user: AuthUserResponse
    plan: ProfilePlanResponse


class SelfServiceUpgradeRequest(BaseModel):
    years: int = Field(strict=True, ge=1, le=5)
    confirmed: bool


class SelfServiceDowngradeRequest(BaseModel):
    confirmed: bool


class ProfileSubscriptionMutationResponse(BaseModel):
    user: AuthUserResponse
    plan: ProfilePlanResponse
    notificationStatus: str = "queued"


class AuthChangePinRequest(BaseModel):
    currentPin: str = Field(min_length=4, max_length=4)
    newPin: str = Field(min_length=4, max_length=4)


class AuthChangePinResponse(BaseModel):
    user: AuthUserResponse | None
    selectionChallenge: AuthSelectionChallengeResponse | None = None


class AuthReauthenticateRequest(BaseModel):
    pin: str = Field(min_length=4, max_length=4)


class AdminUserResponse(BaseModel):
    id: str
    email: str
    displayName: str
    roles: list[str]
    isActive: bool
    isDeleted: bool
    mustChangePin: bool
    createdAt: str
    updatedAt: str
    subscription: SubscriptionResponse


class AdminUserListResponse(BaseModel):
    items: list[AdminUserResponse]
    page: int
    pageSize: int
    total: int


class AdminUserCreateRequest(BaseModel):
    email: str = Field(min_length=3)
    displayName: str = Field(min_length=1)
    roles: list[str]
    pin: str = Field(default="4590", min_length=4, max_length=4)
    isActive: bool = True


class AdminUserUpdateRequest(BaseModel):
    displayName: str | None = Field(default=None, min_length=1)
    roles: list[str] | None = None
    isActive: bool | None = None


class AdminSubscriptionMonthsRequest(BaseModel):
    months: int = Field(ge=-120, le=120)

    @field_validator("months")
    @classmethod
    def months_must_not_be_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("Months must not be zero.")
        return value


class AdminUserResetPinRequest(BaseModel):
    pin: str = Field(default="4590", min_length=4, max_length=4)


class AdminAuthThrottleRecoveryRequest(BaseModel):
    scope: str = Field(pattern="^(account|ip)$")
    key: str = Field(min_length=1, max_length=320)
    reason: str = Field(min_length=10, max_length=500)


class AdminUserMutationResponse(BaseModel):
    user: AdminUserResponse


class LLMSettingsResponse(BaseModel):
    provider: str
    defaultModel: str
    featureModels: dict[str, str] = Field(default_factory=dict)
    configuredModels: list[str] = Field(default_factory=list)
    openrouterApiKeyConfigured: bool = False
    updatedAt: str | None = None


class LLMSettingsUpdateRequest(BaseModel):
    provider: str = "openrouter"
    defaultModel: str = Field(min_length=3)
    featureModels: dict[str, str] = Field(default_factory=dict)


class OpenRouterModelResponse(BaseModel):
    id: str
    name: str
    contextLength: int = 0
    promptPricePerToken: str = ""
    completionPricePerToken: str = ""
    description: str = ""


class OpenRouterModelListResponse(BaseModel):
    items: list[OpenRouterModelResponse]


class LLMChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    model: str | None = None
    feature: str | None = None
    temperature: float = Field(default=0.2, ge=0, le=2)
    maxTokens: int = Field(default=700, ge=50, le=4000)


class LLMChatResponse(BaseModel):
    provider: str
    model: str
    outputText: str
    latencyMs: int
    usage: dict[str, Any] = Field(default_factory=dict)


class ExternalSubmissionFileInput(BaseModel):
    name: str = Field(min_length=1)
    note: str = ""
    content_type: str = "application/octet-stream"
    content_base64: str = Field(min_length=1)


class ExternalSubmissionRequest(BaseModel):
    submitter_name: str = Field(min_length=1)
    submitter_email: str = Field(min_length=3)
    overall_description: str = Field(min_length=1)
    source_notes: str | None = None
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


class WorkspaceReviewAssetResponse(BaseModel):
    objectKey: str
    filename: str
    contentType: str | None = None
    format: str | None = None
    role: str | None = None
    previewable: bool = False
    selectedPreview: bool = False


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
    # None for the standard discipline list, which is fixed rather than remembered.
    lastUsedAt: str | None = None


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


class WorkspaceSubmissionContextResponse(BaseModel):
    submissionSummary: str | None = None
    sourceNotes: str | None = None
    fileNote: str | None = None
    contributorDeclaration: str | None = None
    submittedBy: str | None = None
    submissionBatchId: str | None = None


class WorkspaceReviewCaseResponse(BaseModel):
    id: str
    reviewItemType: str = "review_case"
    reviewKind: str = "symbol_review"
    sourcePreviewUnavailable: bool = False
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
    rightsStatus: str | None = None
    rightsDisposition: str | None = None
    processingOutcome: str | None = None
    sourceFileName: str
    sourceObjectKey: str | None = None
    sourcePreviewUrl: str | None = None
    sourceAssets: list[WorkspaceReviewAssetResponse] = []
    availableFormats: list[str] = []
    submissionContext: WorkspaceSubmissionContextResponse | None = None
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




class WorkspaceTracyStatusResponse(BaseModel):
    generatedAt: str
    queueStatusCounts: dict[str, int] = Field(default_factory=dict)
    oldestActiveQueueItemAt: str | None = None
    rightsDispositionCounts: dict[str, int] = Field(default_factory=dict)
    processingOutcomeCounts: dict[str, int] = Field(default_factory=dict)
    assessmentsMissingReviewCases: int = 0
    assessmentsWithoutOpenReviewCases: int = 0
    rightsLaneOpenCount: int = 0
    runtimeQueueFiles: int = 0
    runtimeStatusCounts: dict[str, int] = Field(default_factory=dict)

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
    createdAt: str | None = None
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
    durationSeconds: int
    seedQuery: str | None = None
    mode: str = "discovery"  # "discovery" or "download"


class WorkspaceScottSourceSearchStartResponse(BaseModel):
    queueItemId: str
    status: str
    durationSeconds: int
    startedAt: str
    expectedCompletedAt: str
    availableSeedQueries: list[str] = []


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
    requiresAuth: bool = False
    authStatus: str = "no_auth"
    authSecretKey: str | None = None
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


class WorkspaceScottSourceSiteAuthUpdateRequest(BaseModel):
    requiresAuth: bool | None = None
    authStatus: str | None = None
    authSecretKey: str | None = Field(default=None, max_length=100)


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
    rightsDisposition: str | None = None
    processingOutcome: str | None = None
    riskLevel: str | None = None
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


class SessionAuthoritativeHumanMutationRequest(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def reject_client_actor_identity(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        candidates = [value]
        wrapped = value.get("request")
        if isinstance(wrapped, dict):
            candidates.append(wrapped)
        forbidden = sorted(
            key
            for candidate in candidates
            for key in ("deciderName", "deciderRole", "updatedBy")
            if key in candidate
        )
        if forbidden:
            raise PydanticCustomError(
                "session_authoritative_identity",
                "Human actor identity fields are session-authoritative and must not be supplied: {fields}",
                {"fields": ", ".join(sorted(set(forbidden)))},
            )
        return value


class WorkspaceReviewDecisionRequest(SessionAuthoritativeHumanMutationRequest):
    decisionCode: str = Field(min_length=1)
    decisionNote: str = ""
    childDecisions: list[WorkspaceReviewChildDecisionInput] = Field(default_factory=list)
    caseComment: str = ""


class WorkspaceRightsEvidenceResponse(BaseModel):
    provenanceAssessmentId: str
    tracyQueueItemId: str | None = None
    rightsStatus: str
    rightsDisposition: str
    processingOutcome: str
    riskLevel: str
    confidence: float | None = None
    summary: str
    defects: list[dict[str, Any]] = Field(default_factory=list)
    recommendedActions: list[str] = Field(default_factory=list)
    sourceContext: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] = Field(default_factory=dict)


class WorkspaceRightsReviewCaseResponse(WorkspaceReviewCaseResponse):
    rightsEvidence: WorkspaceRightsEvidenceResponse | None = None


class WorkspaceRightsReviewCaseListResponse(BaseModel):
    items: list[WorkspaceRightsReviewCaseResponse]


class WorkspaceRightsReviewDecisionRequest(SessionAuthoritativeHumanMutationRequest):
    decisionCode: str = Field(min_length=1)
    correctedRightsStatus: str | None = Field(default=None, max_length=80)
    correctedRightsDisposition: str | None = Field(default=None, max_length=80)
    correctedProcessingOutcome: str | None = Field(default=None, max_length=80)
    licenseLabel: str | None = Field(default=None, max_length=160)
    sourceUrl: str | None = Field(default=None, max_length=1000)
    evidenceNote: str = Field(default="", max_length=4000)
    @field_validator(
        "decisionCode",
        "correctedRightsStatus",
        "correctedRightsDisposition",
        "correctedProcessingOutcome",
        "licenseLabel",
        "sourceUrl",
        "evidenceNote",
    )
    @classmethod
    def trim_rights_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class WorkspaceReviewSymbolPropertiesUpdateRequest(SessionAuthoritativeHumanMutationRequest):
    splitItemId: str | None = None
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=256)
    category: str | None = Field(default=None, max_length=80)
    discipline: str | None = Field(default=None, max_length=80)
    format: str | None = Field(default=None, max_length=40)


    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not REVIEW_SYMBOL_NAME_PATTERN.match(trimmed):
            raise ValueError("Name may only contain letters, numbers, spaces, hyphens, slashes, and dollar signs.")
        return trimmed

    @field_validator("description", "category", "discipline", "format")
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class WorkspaceSplitReviewProcessRequest(SessionAuthoritativeHumanMutationRequest):
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


class WorkspaceRightsReviewDecisionResponse(WorkspaceReviewDecisionResponse):
    updatedRights: dict[str, Any] = Field(default_factory=dict)


# --- Organization Admin (Slice 3A) ---

class OrgDetailResponse(BaseModel):
    id: str
    code: str
    displayName: str
    legalName: str | None = None
    locale: str
    entitlementStatus: str
    isActive: bool
    isProtected: bool
    iconUrl: str
    hasCustomIcon: bool
    customIconEnabled: bool = False


class ProductUsageDayItem(BaseModel):
    date: date
    eventCount: int
    distinctUserCount: int


class ProductUsageEventTypeSummary(BaseModel):
    eventType: str
    days: list[ProductUsageDayItem]
    suppressedDayCount: int
    totalEventCount: int


class OrganizationUsageSummaryResponse(BaseModel):
    organizationId: str
    since: date
    until: date
    eventTypes: list[ProductUsageEventTypeSummary]


class OrganizationBadgeItem(BaseModel):
    badgeType: str
    awardedAt: str


class OrganizationContributionsResponse(BaseModel):
    organizationId: str
    acceptedContributionCount: int
    reversedContributionCount: int
    badges: list[OrganizationBadgeItem]


class UserContributionsResponse(BaseModel):
    acceptedContributionCount: int
    reversedContributionCount: int


class OrgIconUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contentType: str = Field(min_length=1, max_length=100)
    contentBase64: str = Field(min_length=1)


class OrgUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    displayName: str | None = Field(default=None, min_length=1, max_length=200)
    legalName: str | None = Field(default=None, min_length=1, max_length=200)


class OrgMemberCapabilityItem(BaseModel):
    capability: str
    grantedAt: str


class OrgMemberResponse(BaseModel):
    membershipId: str
    userId: str
    email: str
    displayName: str
    userIsActive: bool
    status: str
    baseRole: str
    capabilities: list[OrgMemberCapabilityItem]
    activatedAt: str | None = None
    deactivatedAt: str | None = None


class OrgMemberListResponse(BaseModel):
    items: list[OrgMemberResponse]
    page: int
    pageSize: int
    total: int


def _require_one_user_reference(email: str | None, user_id: str | None, *, names: str) -> None:
    """People are named by email in the UI; the ID form stays for API callers."""
    if bool(email and email.strip()) == bool(user_id):
        raise PydanticCustomError("user_reference", "Provide exactly one of {names}.", {"names": names})


class OrgAddMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, max_length=320)
    userId: str | None = None
    baseRole: str = Field(pattern="^(admin|user)$")

    @model_validator(mode="after")
    def require_one_identifier(self):
        _require_one_user_reference(self.email, self.userId, names="email or userId")
        return self


class OrgPatchMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseRole: str | None = Field(default=None, pattern="^(admin|user)$")
    grantCapability: str | None = None
    revokeCapability: str | None = None


# --- Platform Admin (Slice 3B) ---

class PlatformAdminItem(BaseModel):
    userId: str
    email: str
    displayName: str
    userIsActive: bool
    grantedAt: str


class PlatformAdminListResponse(BaseModel):
    items: list[PlatformAdminItem]
    page: int
    pageSize: int
    total: int


class GrantPlatformAdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, max_length=320)
    userId: str | None = None

    @model_validator(mode="after")
    def require_one_identifier(self):
        _require_one_user_reference(self.email, self.userId, names="email or userId")
        return self


# --- Platform Admin organization directory (Slice 3C) ---

class PlatformOrganizationItem(BaseModel):
    id: str
    code: str
    displayName: str
    legalName: str | None
    entitlementStatus: str
    isActive: bool
    isProtected: bool


class PlatformOrganizationListResponse(BaseModel):
    items: list[PlatformOrganizationItem]
    page: int
    pageSize: int
    total: int


class CreateOrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    displayName: str = Field(min_length=1, max_length=200)
    legalName: str | None = Field(default=None, min_length=1, max_length=200)
    locale: str = "en-US"
    initialAdminEmail: str | None = Field(default=None, max_length=320)
    initialAdminUserId: str | None = None

    @model_validator(mode="after")
    def require_one_initial_admin(self):
        _require_one_user_reference(
            self.initialAdminEmail, self.initialAdminUserId,
            names="initialAdminEmail or initialAdminUserId",
        )
        return self


# --- Platform Admin protected Symgov member management (Slice 3F) ---

class PlatformAddSymgovMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, max_length=320)
    userId: str | None = None
    baseRole: str = Field(pattern="^(admin|user)$")
    reason: str = Field(min_length=10, max_length=1000)

    @model_validator(mode="after")
    def require_one_identifier(self):
        _require_one_user_reference(self.email, self.userId, names="email or userId")
        return self


class PlatformPatchSymgovMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseRole: str = Field(pattern="^(admin|user)$")
    reason: str = Field(min_length=10, max_length=1000)

class PlatformDeactivateSymgovMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, max_length=1000)

class PlatformReactivateMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, max_length=1000)


# --- WP5.3: organization-private symbol drafts ---

class OrganizationSymbolDraftCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=256)
    category: str = Field(min_length=1, max_length=128)
    discipline: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=2000)
    description: str | None = Field(default=None, max_length=4000)
    aliases: list[str] | None = None
    keywords: list[str] | None = None


class OrganizationSymbolAssetResponse(BaseModel):
    id: str
    objectKey: str
    filename: str
    contentType: str
    role: str
    sha256: str
    sizeBytes: int


class OrganizationSymbolRevisionResponse(BaseModel):
    id: str
    revisionLabel: str
    lifecycleState: str
    name: str
    summary: str
    description: str | None
    aliases: list[str]
    keywords: list[str]
    assets: list[OrganizationSymbolAssetResponse]
    createdAt: datetime
    pendingSubmissionId: str | None = None
    pendingSubmissionRationale: str | None = None
    pendingSubmissionSubmittedAt: datetime | None = None


class OrganizationSymbolDraftResponse(BaseModel):
    id: str
    slug: str
    canonicalName: str
    category: str
    discipline: str
    visibility: str
    organizationWide: bool
    organizationId: str
    ownerId: str
    currentRevisionId: str | None
    currentRevision: OrganizationSymbolRevisionResponse | None
    createdAt: datetime
    updatedAt: datetime


class OrganizationSymbolDraftListResponse(BaseModel):
    items: list[OrganizationSymbolDraftResponse]


class OrganizationSymbolAssetUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=256)
    contentType: str = Field(min_length=1, max_length=128)
    contentBase64: str = Field(min_length=1)
    role: str = Field(default="source", pattern="^(source|preview)$")


class OrganizationSymbolSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rationale: str | None = Field(default=None, max_length=2000)


class OrganizationSymbolSubmissionResponse(BaseModel):
    id: str
    organizationId: str
    governedSymbolId: str
    symbolRevisionId: str
    submittedByUserId: str
    submittedAt: datetime
    status: str


# --- WP5.4: organization review lifecycle ---

class OrganizationSymbolReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern="^(approved|rejected|changes_requested)$")
    rationale: str | None = Field(default=None, max_length=2000)


class OrganizationSymbolReviewDecisionResponse(BaseModel):
    id: str
    submissionId: str
    organizationId: str
    governedSymbolId: str
    symbolRevisionId: str
    decidedByUserId: str
    decision: str
    rationale: str | None
    decidedAt: datetime


class OrganizationSymbolOrganizationWideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


# --- Stage 7 WP7.2: promotion requests ---

class PromotionRequestSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)
    sharingAcknowledgment: bool
    proposedMetadata: dict = Field(default_factory=dict)
    traceId: str | None = Field(default=None, max_length=200)


class PromotionRequestResponse(BaseModel):
    id: str
    governedSymbolId: str
    organizationId: str
    symbolRevisionId: str
    status: str
    proposedMetadata: dict
    reason: str
    sharingAcknowledgment: bool
    submittedByUserId: str
    submittedAt: datetime
    closedAt: datetime | None
    traceId: str | None
    reviewCaseId: str | None = None
    possibleDuplicateOfGovernedSymbolId: str | None = None
    possibleDuplicateOfSlug: str | None = None


class PromotionRequestListResponse(BaseModel):
    items: list[PromotionRequestResponse]


class PromotionRequestWithdrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=2000)


# --- Stage 7 WP7.4: demotion ---

class DemotionImpactPreviewResponse(BaseModel):
    governedSymbolId: str
    eligible: bool
    reasons: list[str]
    blockingOrganizationIds: list[str]
    favouritesCount: int


class DemotionExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)


class DemotionResponse(BaseModel):
    governedSymbolId: str
    visibility: str
    symbolRevisionIds: list[str]
    publishedPageIds: list[str]
    packEntryIds: list[str]
    retiredPackIds: list[str]


# --- Stage 10 WP10.4: agent oversight (Organization Steward / Platform Governance) ---

class AgentConfigurationItem(BaseModel):
    id: str
    logicalAgentName: str
    scopeType: str
    scopeId: str | None
    enabled: bool
    modelAlias: str | None
    allowedCapabilities: list[str]
    updatedAt: datetime


class AgentConfigurationListResponse(BaseModel):
    items: list[AgentConfigurationItem]


class CreateAgentConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    logicalAgentName: Literal["organization_steward", "platform_governance"]
    scopeType: Literal["platform", "organization"]
    scopeId: str | None = None
    enabled: bool = False


class UpdateAgentConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    modelAlias: str | None = None
    allowedCapabilities: list[str] | None = None


class AgentFindingItem(BaseModel):
    id: str
    agentConfigId: str
    logicalAgentName: str
    severity: str
    findingType: str
    entityType: str
    entityId: str
    summary: str
    evidence: dict[str, Any]
    policyVersion: str
    status: str
    firstSeenAt: datetime
    lastSeenAt: datetime
    acknowledgedAt: datetime | None
    dismissedAt: datetime | None
    dismissReason: str | None
    resolvedAt: datetime | None
    supersededByFindingId: str | None
    assigneeUserId: str | None
    issueReference: str | None


class AgentFindingListResponse(BaseModel):
    items: list[AgentFindingItem]


class DismissAgentFindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)


class EscalateAgentFindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issueReference: str | None = Field(default=None, max_length=200)
    assigneeUserId: str | None = None


class RunAgentResponse(BaseModel):
    touchedFindingIds: list[str]


# ---------------------------------------------------------------------------
# SM-P1-01 WP1.2 -- semantic review API
#
# Queue pages carry `limit`/`offset` rather than the `page`/`pageSize`/`total`
# of `PagedProjectResponse`. That is deliberate and not an oversight: the
# WP1.1 queue functions take a bound and an offset and return rows, with no
# COUNT behind them, so a `total` here would be a number this API cannot
# actually compute. Reporting an invented one is worse than reporting none.
# ---------------------------------------------------------------------------


class SemanticReviewSymbolIdentityResponse(BaseModel):
    """The operator-readable identity of a symbol, per `CLAUDE.md`.

    `catalogSymbolId` is the human identifier (`S-000001`); the UUID stays
    available as a transport key but is never the compact label. The owning
    organisation is deliberately absent: a reviewer needs to act on the row,
    not to learn which tenant a public symbol was promoted from.
    """

    governedSymbolId: str
    catalogSymbolId: str | None
    canonicalName: str
    slug: str
    visibility: str


class SemanticReviewConceptIdentityResponse(BaseModel):
    semanticConceptId: str
    conceptCode: str
    preferredName: str | None
    conceptKind: str
    status: str


class SemanticReviewCapabilitiesResponse(BaseModel):
    """Which decisions the services will actually accept for this row.

    `mustRepropose` is not the negation of `canVerify`. It is true only where
    verification is barred by a rule a reviewer cannot lift -- a
    `legacy_backfill` classification (section 12.3) or an `ai_assisted` rights
    determination (section 8.4) -- and the row is otherwise live, so the
    available action is to reject and propose afresh.
    """

    canVerify: bool
    canReject: bool
    canRetire: bool
    mustRepropose: bool
    blockedReason: str | None


class SymbolClassificationReviewRowResponse(BaseModel):
    """One classification assignment as a reviewer sees it.

    `classificationNodeId` is the transport key a re-proposal needs, added by
    the 2026-09-14 WP1.2 amendment. Section 12.3 tells a reviewer to reject a
    `legacy_backfill` row and propose afresh, and
    `propose_symbol_revision_classification` takes a node id -- so without this
    field the instruction could be read but never carried out. It sits
    alongside `schemeCode`/`nodeCode`/`nodeLabel` and never replaces them:
    `CLAUDE.md` keeps the human-readable label prominent.
    """

    assignmentId: str
    symbolRevisionId: str
    symbol: SemanticReviewSymbolIdentityResponse
    classificationSchemeId: str
    classificationNodeId: str
    schemeCode: str
    nodeCode: str
    nodeLabel: str
    assignmentRole: str
    status: str
    method: str
    confidence: float | None
    evidence: dict[str, Any]
    proposedAt: str
    capabilities: SemanticReviewCapabilitiesResponse


class SymbolSemanticAssignmentReviewRowResponse(BaseModel):
    assignmentId: str
    symbolRevisionId: str
    symbol: SemanticReviewSymbolIdentityResponse
    concept: SemanticReviewConceptIdentityResponse
    assignmentRole: str
    status: str
    method: str
    confidence: float | None
    evidence: dict[str, Any]
    proposedAt: str
    capabilities: SemanticReviewCapabilitiesResponse


class ConceptClassificationReviewRowResponse(BaseModel):
    assignmentId: str
    concept: SemanticReviewConceptIdentityResponse
    classificationSchemeId: str
    schemeCode: str
    nodeCode: str
    nodeLabel: str
    assignmentRole: str
    status: str
    method: str
    confidence: float | None
    evidence: dict[str, Any]
    proposedAt: str
    capabilities: SemanticReviewCapabilitiesResponse


class ConceptExternalMappingReviewRowResponse(BaseModel):
    referenceId: str
    concept: SemanticReviewConceptIdentityResponse
    schemeVersionId: str
    schemeCode: str
    schemeVersionLabel: str
    externalIdentifier: str
    externalLabel: str | None
    mappingType: str
    status: str
    method: str
    confidence: float | None
    evidence: dict[str, Any]
    proposedAt: str
    capabilities: SemanticReviewCapabilitiesResponse


class RightsRecordReviewRowResponse(BaseModel):
    recordId: str
    subjectKind: str
    symbolRevisionId: str | None
    symbol: SemanticReviewSymbolIdentityResponse | None
    sourcePackageId: str | None
    standardVersionId: str | None
    rightsStatus: str
    disposition: str
    determinationMethod: str
    licenceReference: str | None
    status: str
    evidence: dict[str, Any]
    proposedAt: str
    capabilities: SemanticReviewCapabilitiesResponse


class ClassificationNodeOptionResponse(BaseModel):
    """One node a reviewer may propose against."""

    nodeId: str
    nodeCode: str
    nodeLabel: str
    parentNodeId: str | None


class ClassificationSchemeOptionResponse(BaseModel):
    schemeId: str
    schemeCode: str
    name: str
    nodes: list[ClassificationNodeOptionResponse]


class ClassificationSchemeOptionsResponse(BaseModel):
    """The schemes and nodes a reviewer may actually assign into.

    Decision Q6 keeps `USE-CASE`, `DOCUMENT-TYPE` and `REPRESENTATION-TYPE`
    read-only in v1, and the propose route already refuses them. Listing them
    here as choices would invite a 422 the caller could have been spared, so
    this read offers only the schemes a reviewer may actually assign into.

    No pagination, re-decided rather than inherited. SM-P1-02 WP2.1 added
    `ISO-ICS-7`, whose 1381 nodes decision D4 bounds to the 441 at field and
    group level; with the two original schemes' 31 that is 472 nodes in about
    75 KB, read once per session. Chris ruled on 2026-09-18 that this stays a
    single response and that the flat-picker problem is answered by a
    searchable control in WP2.3, not by a paged contract here.
    """

    items: list[ClassificationSchemeOptionResponse]


class SymbolClassificationQueueResponse(BaseModel):
    items: list[SymbolClassificationReviewRowResponse]
    limit: int
    offset: int


class SymbolSemanticAssignmentQueueResponse(BaseModel):
    items: list[SymbolSemanticAssignmentReviewRowResponse]
    limit: int
    offset: int


class ConceptClassificationQueueResponse(BaseModel):
    items: list[ConceptClassificationReviewRowResponse]
    limit: int
    offset: int


class ConceptExternalMappingQueueResponse(BaseModel):
    items: list[ConceptExternalMappingReviewRowResponse]
    limit: int
    offset: int


class ConceptClassificationListResponse(BaseModel):
    """A concept's classifications after a decision -- not a queue page.

    `ConceptExternalMappingListResponse`' reasoning, for the same reason: the
    queue lists only `proposed` rows, so a reviewer who had just rejected an
    assignment would get back a response that did not contain it.
    """

    items: list[ConceptClassificationReviewRowResponse]


class ConceptExternalMappingListResponse(BaseModel):
    """A concept's mappings after a write -- not a queue page.

    No `limit`/`offset`: a write applied no pagination bound, and reporting
    one would describe a page that was never taken.
    """

    items: list[ConceptExternalMappingReviewRowResponse]


class RightsRecordQueueResponse(BaseModel):
    items: list[RightsRecordReviewRowResponse]
    limit: int
    offset: int


class SymbolRevisionSemanticStateResponse(BaseModel):
    """One revision's whole governed semantic state, in one read.

    External mappings are not here: they hang off a concept, not a revision,
    and their queue is the surface that reviews them. Including them would
    mean choosing which of an assigned concept's mappings are "this
    revision's", which is a relationship the model does not assert.
    """

    symbolRevisionId: str
    revisionLabel: str
    lifecycleState: str
    symbol: SemanticReviewSymbolIdentityResponse
    semanticAssignments: list[SymbolSemanticAssignmentReviewRowResponse]
    classificationAssignments: list[SymbolClassificationReviewRowResponse]
    rightsRecords: list[RightsRecordReviewRowResponse]


class SemanticConceptCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conceptKind: str = Field(min_length=1, max_length=64)
    preferredName: str = Field(min_length=1, max_length=256)
    definition: str = Field(min_length=1, max_length=8000)
    revisionLabel: str = Field(default="r1", min_length=1, max_length=64)
    aliases: list[str] | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=4000)
    rationale: str | None = Field(default=None, max_length=2000)


class SemanticConceptRevisionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revisionLabel: str = Field(min_length=1, max_length=64)
    preferredName: str = Field(min_length=1, max_length=256)
    definition: str = Field(min_length=1, max_length=8000)
    aliases: list[str] | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=4000)
    rationale: str | None = Field(default=None, max_length=2000)


class SemanticConceptRevisionTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targetState: str = Field(min_length=1, max_length=32)


class SemanticConceptRevisionResponse(BaseModel):
    semanticConceptId: str
    conceptCode: str
    revisionId: str
    revisionLabel: str
    preferredName: str
    definition: str
    lifecycleState: str
    conceptStatus: str
    createdAt: str


class SymbolSemanticAssignmentProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semanticConceptId: uuid.UUID
    assignmentRole: str = Field(min_length=1, max_length=32)
    method: str = Field(min_length=1, max_length=32)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: dict[str, Any] | None = None


class SymbolClassificationProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classificationNodeId: uuid.UUID
    assignmentRole: str = Field(min_length=1, max_length=32)
    method: str = Field(min_length=1, max_length=32)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: dict[str, Any] | None = None


class ConceptExternalMappingProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemeVersionId: uuid.UUID
    externalIdentifier: str = Field(min_length=1, max_length=512)
    mappingType: str = Field(min_length=1, max_length=32)
    mappingMethod: str = Field(min_length=1, max_length=32)
    externalLabel: str | None = Field(default=None, max_length=512)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: dict[str, Any] | None = None


class SemanticReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targetStatus: str = Field(min_length=1, max_length=32)


class ExternalMappingDecisionRequest(BaseModel):
    """Verifying a mapping needs its basis, and section 16.2 rejects one.

    `verificationBasis` is required to verify and recorded in the evidence, so
    the reason a mapping is trusted outlives the reviewer. An `exact` mapping
    cannot be verified on `string_similarity`, however high its confidence --
    the service enforces that, and this schema does not second-guess it.
    """

    model_config = ConfigDict(extra="forbid")

    targetStatus: str = Field(min_length=1, max_length=32)
    verificationBasis: str | None = Field(default=None, min_length=1, max_length=64)


# ---------------------------------------------------------------------------
# SM-P1-01 WP1.5 -- the approval's classification forecast
#
# Decision Q9 (2026-09-14). Every governed semantic assertion is written by
# the approval handoff that runs *after* the review this sits in, so there is
# no recorded state to report; what there is, and what a reviewer can still
# act on, is what approving the case *will* assert and which section 9.3
# fields will fall into a gap.
#
# Nothing in this response is recorded state, and the field names say so:
# `willAssert` and `willGap`, not `assignments` and `gaps`. `CLAUDE.md`
# forbids presenting an illustrative value as a production one, and a forecast
# labelled like a record is exactly that.
# ---------------------------------------------------------------------------


class ClassificationForecastAssignmentResponse(BaseModel):
    """One classification the approval will propose.

    `classificationNodeId` is the transport key; `nodeCode` and `nodeLabel`
    are what a reviewer reads, and `matchBasis` says which rule found the node
    -- `exact`, `plural_variant` or `legacy_taxonomy` -- so a value matched
    through the legacy taxonomy table is visibly not an exact hit.
    """

    field: str
    schemeCode: str
    assignmentRole: str
    rawValue: str
    classificationNodeId: str
    nodeCode: str
    nodeLabel: str
    matchBasis: str


class ClassificationForecastGapResponse(BaseModel):
    """One section 9.3 field the approval will record but not map.

    The raw value is always carried: a gap without the value is still a loss,
    which is section 16.1's "no field silently dropped" read forwards.
    """

    field: str
    rawValue: str | None
    reason: str
    detail: str | None


class ClassificationForecastLinkResponse(BaseModel):
    """A section 9.3 row whose target is a link, not a classification.

    `standardsSource` reaches `symbol_standard_links` and
    `libraryProvenanceClass` reaches `source_package_entries`.
    """

    field: str
    rawValue: str | None
    target: str
    relationshipType: str | None = None


class ReviewCaseClassificationPreviewResponse(BaseModel):
    """What approving this review case will assert. Not what it has asserted.

    `disciplineUsed`/`categoryUsed` are the values the forecast actually
    planned from, after the reviewed-property precedence. They are reported
    rather than described so a reviewer who has just corrected a discipline in
    place can see that the correction is the one being forecast -- and so this
    response carries no second copy of the precedence rule to drift from the
    writer's.
    """

    reviewCaseId: str
    splitItemId: str | None
    classificationRecordId: str | None
    disciplineUsed: str | None
    categoryUsed: str | None
    method: str
    mapperVersion: str
    willAssert: list[ClassificationForecastAssignmentResponse]
    willGap: list[ClassificationForecastGapResponse]
    willLink: list[ClassificationForecastLinkResponse]


# ---------------------------------------------------------------------------
# SM-P1-01 WP1.3 -- rights record review API
#
# The reviewer's own proposal is the point of these two. Section 7.12 asks for
# who, when and why on an approval; `transition_rights_record` demands all
# three, and refuses an `ai_assisted` determination outright (section 8.4), so
# these models stay deliberately thin and let the service decide.
# ---------------------------------------------------------------------------


class RightsRecordProposeRequest(BaseModel):
    """A reviewer's own rights determination.

    Exactly one subject must be named. A record about "a package and also an
    edition" asserts one decision about two different things, and a record
    about nothing is not a record -- `rights_provenance._subject_kwargs`
    enforces that, and this model does not duplicate the rule.

    `rightsStatus` defaults to `unknown`, which is the only honest value
    before anyone has looked. A permissive disposition may be *proposed* on
    any status; only approving one is constrained, so a reviewer can put
    "I believe we may distribute this" forward and have the licence evidence
    demanded at the decision.
    """

    model_config = ConfigDict(extra="forbid")

    disposition: str = Field(min_length=1, max_length=32)
    determinationMethod: str = Field(min_length=1, max_length=32)
    rightsStatus: str = Field(default="unknown", min_length=1, max_length=32)
    symbolRevisionId: uuid.UUID | None = None
    sourcePackageId: uuid.UUID | None = None
    standardVersionId: uuid.UUID | None = None
    licenceReference: str | None = Field(default=None, max_length=512)
    decisionReason: str | None = Field(default=None, max_length=2000)
    evidence: dict[str, Any] | None = None


class RightsRecordDecisionRequest(BaseModel):
    """Approve, reject or retire one rights record.

    `rightsStatus` and `licenceReference` may be corrected as part of the
    decision, because the decision is often what establishes them: a record
    proposed as `unknown` becomes `licensed` when the contract is found.
    """

    model_config = ConfigDict(extra="forbid")

    targetStatus: str = Field(min_length=1, max_length=32)
    decisionReason: str | None = Field(default=None, max_length=2000)
    rightsStatus: str | None = Field(default=None, min_length=1, max_length=32)
    licenceReference: str | None = Field(default=None, max_length=512)

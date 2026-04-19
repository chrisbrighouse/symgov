from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    routes: list[str]
    payload: dict[str, Any]
    attachmentId: str
    attachmentObjectKey: str
    intakeRecordId: str
    intakeStatus: str
    eligibilityStatus: str
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
    fileName: str
    parentFileName: str
    nameSource: str | None = None
    attachmentObjectKey: str | None = None
    previewUrl: str | None = None


class WorkspaceReviewCaseResponse(BaseModel):
    id: str
    symbolId: str
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
    children: list[WorkspaceReviewChildResponse]


class WorkspaceReviewCaseListResponse(BaseModel):
    items: list[WorkspaceReviewCaseResponse]


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

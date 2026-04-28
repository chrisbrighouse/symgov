export const symbols = [
  {
    id: 'FV-201A',
    name: 'Flow Control Valve',
    category: 'Valve',
    discipline: 'P&ID',
    revision: 'Rev 4',
    effectiveDate: '2026-03-18',
    pageCode: 'P-142',
    pack: 'Owner-operator core P&ID pack',
    summary: 'Approved company variant for pneumatically actuated flow control in process service.',
    rationale: 'Adds required fail-position annotation and positioner note for contractor clarity.',
    downloads: ['SVG', 'DWG mapping sheet', 'PDF legend'],
    keywords: ['control valve', 'flow control', 'fv', 'isa 5.1'],
    status: 'Published',
    metric: '18 downstream consumers',
    clarificationCount: 2
  },
  {
    id: 'HV-110',
    name: 'Manual Gate Valve',
    category: 'Valve',
    discipline: 'P&ID',
    revision: 'Rev 2',
    effectiveDate: '2025-12-02',
    pageCode: 'P-021',
    pack: 'Owner-operator core P&ID pack',
    summary: 'Baseline manual isolation symbol aligned to the approved internal standard.',
    rationale: 'No visual delta from baseline standard; internal service metadata tightened.',
    downloads: ['SVG', 'PDF legend'],
    keywords: ['manual valve', 'gate valve', 'hv', 'isolation'],
    status: 'Published',
    metric: '7 active packs',
    clarificationCount: 0
  },
  {
    id: 'PI-301',
    name: 'Pressure Indicator Bubble',
    category: 'Instrument',
    discipline: 'P&ID',
    revision: 'Rev 3',
    effectiveDate: '2026-02-10',
    pageCode: 'I-104',
    pack: 'Instrumentation overlay pack',
    summary: 'Local pressure indication symbol with approved lettering and export legibility guidance.',
    rationale: 'Supports reduced-size print sets without changing the core ISA reading.',
    downloads: ['SVG', 'DWG mapping sheet', 'PDF legend'],
    keywords: ['pressure indicator', 'instrument bubble', 'pi'],
    status: 'Published',
    metric: '3 unresolved field questions',
    clarificationCount: 3
  }
];

export const changeQueue = [
  {
    id: 'CR-182',
    symbolId: 'TT-410',
    title: 'Approve temperature transmitter symbol for Q2 overlay',
    owner: 'S. Wong',
    due: '2026-04-18',
    priority: 'High',
    risk: 'Medium',
    pages: 1,
    packs: 1,
    status: 'In review',
    summary: 'Confirm field wiring note language and ISA clause mapping before first publication.',
    clarifications: ['No external clarification yet', 'Awaiting EPC wording confirmation']
  },
  {
    id: 'CR-177',
    symbolId: 'FV-201A',
    title: 'Confirm fail-close notation for LNG retrofit pack',
    owner: 'A. Shah',
    due: '2026-04-15',
    priority: 'Medium',
    risk: 'High',
    pages: 3,
    packs: 2,
    status: 'Awaiting approver',
    summary: 'Published guidance triggered a contractor question on inherited fail-state notation.',
    clarifications: ['Clarification from EPC routed from Standards View', 'Downstream pack note may need revision']
  },
  {
    id: 'CR-171',
    symbolId: 'PI-301',
    title: 'Extend reduced-print example guidance',
    owner: 'R. Flynn',
    due: '2026-04-22',
    priority: 'Low',
    risk: 'Low',
    pages: 2,
    packs: 1,
    status: 'Ready for review',
    summary: 'Adds a smaller print example without changing the approved symbol geometry.',
    clarifications: ['Two repeated field questions reference low-scale readability']
  }
];

export const daisyCoordinationReports = [
  {
    id: 'DCR-182A',
    queueItemId: 'AQI-DAISY-182A',
    reviewCaseId: 'CR-182',
    sourceType: 'review_case',
    sourceId: 'CR-182',
    coordinationStatus: 'escalated',
    coordinationSummary: 'Daisy prepared reviewer assignments and requested a coordinated follow-up before case movement.',
    createdAt: '2026-04-18T16:20:00Z',
    currentStage: 'raster_split_review',
    escalationLevel: 'medium',
    decision: 'escalate',
    confidence: 0.82,
    escalationTarget: 'human_reviewer',
    defectCount: 1,
    assignmentProposals: [
      {
        proposalRank: 1,
        reviewer: 'methods_lead',
        role: 'primary_reviewer',
        reason: 'Primary reviewer for raster extraction quality and symbol naming decisions.'
      },
      {
        proposalRank: 2,
        reviewer: 'qa_admin',
        role: 'secondary_reviewer',
        reason: 'Secondary reviewer for queue follow-through and final case packaging.'
      }
    ],
    stageTransitionProposals: [
      {
        fromStage: 'raster_split_review',
        toStage: 'review_pending_assignment',
        action: 'request_assignment',
        reason: 'Split review needs explicit reviewer ownership before final child-symbol decisions.'
      }
    ],
    contributorEvidenceRequests: [
      {
        requestType: 'technical_clarification',
        detail: 'Confirm the intended symbol names for the extracted records that still have OCR uncertainty.'
      }
    ]
  },
  {
    id: 'DCR-177A',
    queueItemId: 'AQI-DAISY-177A',
    reviewCaseId: 'CR-177',
    sourceType: 'review_case',
    sourceId: 'CR-177',
    coordinationStatus: 'completed',
    coordinationSummary: 'Daisy prepared an approver-ready handoff for the fail-close notation review.',
    createdAt: '2026-04-17T14:05:00Z',
    currentStage: 'approval_review',
    escalationLevel: 'low',
    decision: 'pass',
    confidence: 0.9,
    escalationTarget: 'none',
    defectCount: 0,
    assignmentProposals: [
      {
        proposalRank: 1,
        reviewer: 'approvals_lead',
        role: 'primary_reviewer',
        reason: 'Approver review is the next gating step for this case.'
      }
    ],
    stageTransitionProposals: [
      {
        fromStage: 'approval_review',
        toStage: 'ready_for_human_decision',
        action: 'prepare_human_decision',
        reason: 'The case is stable enough for final human sign-off.'
      }
    ],
    contributorEvidenceRequests: []
  }
];

export const processingActivity = [
  {
    id: 'PIPE-001',
    batchId: 'subext-20260419T135250Z',
    title: 'Single-symbol JPG intake completed downstream processing',
    submittedAt: '2026-04-19T13:52:50Z',
    sourceFileName: 'pressure-control-valve.jpg',
    operatorStatus: 'Ready for review',
    priority: 'Medium',
    owner: 'SymGov Ops',
    reviewCaseId: 'CR-182',
    agents: [
      {
        id: 'scott',
        name: 'Scott',
        stage: 'Intake',
        status: 'completed',
        summary: 'Accepted contributor submission and routed raster input downstream.',
        artifactCount: 1
      },
      {
        id: 'vlad',
        name: 'Vlad',
        stage: 'Technical validation',
        status: 'completed',
        summary: 'Detected a single-symbol raster candidate with no split required.',
        artifactCount: 2
      },
      {
        id: 'tracy',
        name: 'Tracy',
        stage: 'Provenance',
        status: 'completed',
        summary: 'Preserved source metadata and passed filename context to Libby.',
        artifactCount: 1
      },
      {
        id: 'libby',
        name: 'Libby',
        stage: 'Classification',
        status: 'completed',
        summary: 'Prepared provisional valve classification evidence for review.',
        artifactCount: 1
      },
      {
        id: 'daisy',
        name: 'Daisy',
        stage: 'Review coordination',
        status: 'completed',
        summary: 'Created reviewer assignment and stage movement proposals.',
        artifactCount: 1
      }
    ]
  },
  {
    id: 'PIPE-002',
    batchId: 'subext-20260416T182301Z',
    title: 'Multi-symbol raster sheet split review',
    submittedAt: '2026-04-16T18:23:01Z',
    sourceFileName: '01-symbols2.png',
    operatorStatus: 'Review coordination active',
    priority: 'High',
    owner: 'SymGov Ops',
    reviewCaseId: '89746140-bb29-4197-bd9c-a4c89743a219',
    agents: [
      {
        id: 'scott',
        name: 'Scott',
        stage: 'Intake',
        status: 'completed',
        summary: 'Accepted PNG intake and routed to Vlad and Tracy.',
        artifactCount: 1
      },
      {
        id: 'vlad',
        name: 'Vlad',
        stage: 'Technical validation',
        status: 'escalated',
        summary: 'Detected 29 candidate regions and created a raster split review case.',
        artifactCount: 4
      },
      {
        id: 'tracy',
        name: 'Tracy',
        stage: 'Provenance',
        status: 'queued',
        summary: 'Awaiting provenance replay or current queue processing.',
        artifactCount: 0
      },
      {
        id: 'libby',
        name: 'Libby',
        stage: 'Classification',
        status: 'waiting',
        summary: 'Waiting for upstream provenance/classification handoff context.',
        artifactCount: 0
      },
      {
        id: 'daisy',
        name: 'Daisy',
        stage: 'Review coordination',
        status: 'completed',
        summary: 'Prepared reviewer routing for the split-review case.',
        artifactCount: 1
      }
    ]
  },
  {
    id: 'PIPE-003',
    batchId: 'internal-seed-demo',
    title: 'Seeded operator activity example',
    submittedAt: '2026-04-26T09:00:00Z',
    sourceFileName: 'instrument-overlay-import.json',
    operatorStatus: 'Processing',
    priority: 'Low',
    owner: 'Methods Lead',
    reviewCaseId: '',
    agents: [
      {
        id: 'scott',
        name: 'Scott',
        stage: 'Intake',
        status: 'completed',
        summary: 'Normalized imported metadata and accepted package for downstream checks.',
        artifactCount: 1
      },
      {
        id: 'vlad',
        name: 'Vlad',
        stage: 'Technical validation',
        status: 'completed',
        summary: 'No raster split work required for structured input.',
        artifactCount: 1
      },
      {
        id: 'tracy',
        name: 'Tracy',
        stage: 'Provenance',
        status: 'running',
        summary: 'Checking rights and source-package declarations.',
        artifactCount: 0
      },
      {
        id: 'libby',
        name: 'Libby',
        stage: 'Classification',
        status: 'waiting',
        summary: 'Waiting for Tracy provenance output.',
        artifactCount: 0
      },
      {
        id: 'daisy',
        name: 'Daisy',
        stage: 'Review coordination',
        status: 'waiting',
        summary: 'No review case created yet.',
        artifactCount: 0
      }
    ]
  }
];

export const submissionPresets = [
  'Contractor-originated company variant',
  'Imported standards package cleanup',
  'Raster sheet intake requiring split review'
];

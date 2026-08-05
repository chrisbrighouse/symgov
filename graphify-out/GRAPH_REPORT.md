# Graph Report - backend  (2026-08-04)

## Corpus Check
- 92 files · ~90,011 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1399 nodes · 4831 edges · 63 communities (60 shown, 3 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 108 edges (avg confidence: 0.6)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- ORM Models and Runtime
- User Authentication
- Workspace Review Routes
- Catalog Favourites
- Catalog API Key Management
- Workspace Classification Models
- Daisy Reporting Schemas
- Publication Handoff
- Management CLI
- LLM Usage Ledger
- Telemetry and Queue Helpers
- Workspace Intake Models
- Agent Queue Worker
- Catalog Credential Safety
- Asset Manifest Processing
- Catalog Taxonomy Modes
- BTX Conversion
- Backend Architecture Overview
- API Entrypoints
- Catalog Search Assets
- Filename Metadata Inference
- Queue Reconciliation
- Catalog API Authentication
- Database Migration Runtime
- Email Delivery Worker
- Workspace Policy Dependencies
- Request Schema Validation
- Catalog Symbol Identifiers
- Runtime Notifications
- Agent Feedback Events
- Catalog Usage Privacy
- Rights Evidence Review
- Split Item Lifecycle
- Subscription Email Delivery
- Authentication Roles Migration
- Bounded Catalog Routes
- API Route Modules

## God Nodes (most connected - your core abstractions)
1. `_get()` - 155 edges
2. `Base` - 57 edges
3. `RuntimePersistenceBridge` - 55 edges
4. `AuthenticatedUser` - 51 edges
5. `ReviewCase` - 49 edges
6. `coerce_uuid()` - 46 edges
7. `User` - 41 edges
8. `AuditEvent` - 37 edges
9. `AgentQueueItem` - 36 edges
10. `ValidationReport` - 32 edges

## Surprising Connections (you probably didn't know these)
- `FastAPI ASGI Application` --semantically_similar_to--> `FastAPI`  [INFERRED] [semantically similar]
  README.md → requirements.txt
- `SQLAlchemy Persistence` --semantically_similar_to--> `SQLAlchemy`  [INFERRED] [semantically similar]
  README.md → requirements.txt
- `Alembic Migrations` --semantically_similar_to--> `Alembic`  [INFERRED] [semantically similar]
  README.md → requirements.txt
- `_safe_key_payload()` --references--> `CatalogApiKeyDTO`  [EXTRACTED]
  manage_symgov.py → symgov_backend/catalog_api_keys.py
- `_created_key_payload()` --references--> `CatalogApiKeyCreateDTO`  [EXTRACTED]
  manage_symgov.py → symgov_backend/catalog_api_keys.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Backend Runtime Web Stack** — backend_requirements_fastapi, backend_requirements_uvicorn, backend_requirements_sqlalchemy, backend_requirements_psycopg_binary [INFERRED 0.85]
- **Subscription Notification Flow** — backend_readme_user_subscriptions, backend_readme_transactional_email_outbox, backend_readme_agentmail_transport, backend_readme_smtp_transport [EXTRACTED 1.00]

## Communities (63 total, 3 thin omitted)

### Community 0 - "ORM Models and Runtime"
Cohesion: 0.06
Nodes (80): DeclarativeBase, Protocol, Base, AgentDefinition, AgentFeedbackEvent, AgentOutputArtifact, AgentRun, Attachment (+72 more)

### Community 1 - "User Authentication"
Cohesion: 0.08
Nodes (98): date, _as_aware_utc(), authenticate_user(), AuthenticatedUser, create_session_token(), create_user_session(), current_user_from_token(), derive_review_operation_actor() (+90 more)

### Community 2 - "Workspace Review Routes"
Cohesion: 0.06
Nodes (89): ReviewOperationActor, actor_snapshot(), attach_latest_decision(), build_decision_summary(), build_published_symbol_workspace_item(), _build_reggie_queue_control_response(), close_parent_sheet_reviews_for_split(), create_review_action() (+81 more)

### Community 3 - "Catalog Favourites"
Cohesion: 0.07
Nodes (77): put, add_catalog_favourite(), load_favourite_symbol_ids(), Session, UUID, remove_catalog_favourite(), _user_uuid(), AgentQueueItem (+69 more)

### Community 4 - "Catalog API Key Management"
Cohesion: 0.06
Nodes (71): _audit_payload(), _aware_utc(), CatalogApiKeyAlreadyActiveError, CatalogApiKeyCreateDTO, CatalogApiKeyDTO, CatalogApiKeyError, CatalogApiKeyNotFoundError, CatalogApiKeyPrefixMismatchError (+63 more)

### Community 5 - "Workspace Classification Models"
Cohesion: 0.09
Nodes (73): ClassificationRecord, ReviewCase, ReviewSymbolProperty, apply_classification_fields(), attach_latest_decision(), build_children(), build_decision_summary(), build_preview_url() (+65 more)

### Community 6 - "Daisy Reporting Schemas"
Cohesion: 0.06
Nodes (69): BaseModel, build_daisy_report_item(), list_workspace_daisy_reports(), load_daisy_report_payloads(), AdminSubscriptionMonthsRequest, AdminUserCreateRequest, AdminUserListResponse, AdminUserResetPinRequest (+61 more)

### Community 7 - "Publication Handoff"
Cohesion: 0.11
Nodes (60): HumanReviewDecision, SymbolRevision, approval_actor_snapshot(), approved_child_decisions(), approved_revisions_for_decision(), build_pack_metadata(), candidate_id_from_intake(), _canonical_grayscale_pixels() (+52 more)

### Community 8 - "Management CLI"
Cohesion: 0.09
Nodes (52): ArgumentParser, Decimal, build_parser(), _created_key_payload(), main(), parse_args(), _parse_aware_datetime(), _parse_catalog_key_status() (+44 more)

### Community 9 - "LLM Usage Ledger"
Cohesion: 0.08
Nodes (52): LLMUsageEvent, Authoritative, append-only record of one sanitized LLM attempt., _build_settings_response(), get_llm_settings(), get_llm_usage(), list_openrouter_models(), llm_chat(), patch (+44 more)

### Community 10 - "Telemetry and Queue Helpers"
Cohesion: 0.07
Nodes (35): HTTPRedirectHandler, Pattern, build_llm_event(), _canonical_uuid(), initiator_pseudonym(), LangfuseTransport, LLMTelemetry, _NoRedirectHandler (+27 more)

### Community 11 - "Workspace Intake Models"
Cohesion: 0.13
Nodes (46): _get(), IntakeRecord, ReviewSplitItem, ValidationReport, apply_classification_fields(), build_children(), build_preview_url(), build_provenance_notes() (+38 more)

### Community 12 - "Agent Queue Worker"
Cohesion: 0.15
Nodes (40): agent_worker_health_payload(), AgentQueueWorkerConfig, AgentQueueWorkerState, _build_hermes_worker_prompt(), claim_published_feedback_queue_item(), drain_agent_queues(), is_published_feedback_queue_item(), _load_module() (+32 more)

### Community 13 - "Catalog Credential Safety"
Cohesion: 0.15
Nodes (41): IntegrationAuthContext, contains_catalog_credentials(), Detect real-looking credentials without rejecting explicit documentation…, catalog_download_content_disposition(), catalog_download_header_token(), catalog_download_now(), _catalog_ed_citations(), catalog_ed_query() (+33 more)

### Community 14 - "Asset Manifest Processing"
Cohesion: 0.19
Nodes (29): _add_asset(), _add_download(), _asset_from_mapping(), canonical_asset_format(), choose_preview_asset(), _clean(), _clean_content_type(), content_type_for_format() (+21 more)

### Community 15 - "Catalog Taxonomy Modes"
Cohesion: 0.22
Nodes (24): CatalogEdMode, SelectedCatalogEdMode, CatalogEdResult, _contains(), interpret_catalog_ed_prompt(), _interpret_terms(), _Interpretation, _select_mode() (+16 more)

### Community 16 - "BTX Conversion"
Cohesion: 0.19
Nodes (23): BtxConversionError, convert_btx(), _emit_dxf(), _emit_png(), _emit_svg(), _inflate(), _inflate_hex(), _line_points() (+15 more)

### Community 17 - "Backend Architecture Overview"
Cohesion: 0.12
Nodes (24): Agent Workflow Runtime, Alembic Migrations, Catalog API Keys, External Submission API, FastAPI ASGI Application, Isolated Backend Testing, manage_symgov CLI, MinIO Storage (+16 more)

### Community 18 - "API Entrypoints"
Cohesion: 0.17
Nodes (19): FastAPI, main(), parse_args(), Namespace, create_app(), load_app_version(), require_any_role(), require_user() (+11 more)

### Community 19 - "Catalog Search Assets"
Cohesion: 0.19
Nodes (21): list_download_assets(), List downloadable assets, excluding generated preview derivatives by default., catalog_symbol_filters(), catalog_symbol_ref(), catalog_symbol_summary(), CatalogSearchResult, _contextual_search_context(), _contextual_search_score() (+13 more)

### Community 20 - "Filename Metadata Inference"
Cohesion: 0.21
Nodes (18): _display_token(), infer_filename_metadata(), inferred_candidate_title(), Any, _tokenize_stem(), candidate_symbol_id(), candidate_title(), guess_declared_format() (+10 more)

### Community 21 - "Queue Reconciliation"
Cohesion: 0.20
Nodes (16): build_reggie_queue_control_suggestions(), _coerce_optional_uuid(), is_active_queue_status(), is_terminal_queue_status(), iter_runtime_queue_records(), _load_json(), Any, Path (+8 more)

### Community 22 - "Catalog API Authentication"
Cohesion: 0.22
Nodes (18): _as_aware_utc(), authenticate_catalog_api_key(), _bearer_token(), CatalogApiAuthenticationError, get_catalog_api_key_context(), get_catalog_feedback_api_key_context(), hash_api_key(), _normalize_scopes() (+10 more)

### Community 23 - "Database Migration Runtime"
Cohesion: 0.19
Nodes (10): Engine, sessionmaker, create_database_engine(), create_session_factory(), get_database_url(), normalize_database_url(), Path, PathLike (+2 more)

### Community 24 - "Email Delivery Worker"
Cohesion: 0.19
Nodes (10): AgentMailEmailSender, configured_email_sender(), deliver_configured_email_batch(), deliver_pending_email_batch(), _NoRedirectHandler, Any, datetime, Session (+2 more)

### Community 25 - "Workspace Policy Dependencies"
Cohesion: 0.24
Nodes (14): classify_workspace_policy(), ConcreteWorkspaceOperation, expand_workspace_operations(), get_current_user(), get_db_session(), get_runtime_bridge(), matched_route_template(), normalize_workspace_route_path() (+6 more)

### Community 26 - "Request Schema Validation"
Cohesion: 0.16
Nodes (8): field_validator, model_validator, Any, SessionAuthoritativeHumanMutationRequest, WorkspaceReviewDecisionRequest, WorkspaceReviewSymbolPropertiesUpdateRequest, WorkspaceRightsReviewDecisionRequest, WorkspaceSplitReviewProcessRequest

### Community 27 - "Catalog Symbol Identifiers"
Cohesion: 0.25
Nodes (13): correct_catalog_symbol_id(), ensure_catalog_symbol_id(), format_allocated_catalog_symbol_id(), _is_identifier_pk_violation(), normalize_catalog_symbol_id(), datetime, IntegrityError, Session (+5 more)

### Community 28 - "Runtime Notifications"
Cohesion: 0.50
Nodes (12): _build_finish_message(), _build_start_message(), _load_config(), _message_lines(), _normalize_text(), _phase_enabled(), Any, Path (+4 more)

### Community 29 - "Agent Feedback Events"
Cohesion: 0.26
Nodes (11): add_agent_feedback_events(), build_duplicate_decision_feedback_events(), build_symbol_property_feedback_events(), _changed(), Any, datetime, Session, UUID (+3 more)

### Community 30 - "Catalog Usage Privacy"
Cohesion: 0.32
Nodes (11): Defensively hide credential material from legacy label surfaces., redact_catalog_credential_label(), build_catalog_usage_event(), hash_client_ip(), log_catalog_usage_event_best_effort(), datetime, Request, Session (+3 more)

### Community 31 - "Rights Evidence Review"
Cohesion: 0.38
Nodes (7): ProvenanceAssessment, build_rights_evidence_payload(), build_rights_review_case_response(), _list_of_dicts(), _list_of_strings(), WorkspaceReviewCaseResponse, WorkspaceRightsReviewCaseResponse

### Community 32 - "Split Item Lifecycle"
Cohesion: 0.33
Nodes (6): is_open_split_item_status(), Return the lifecycle group for a ReviewSplitItem status. Keep status grouping…, Whether a split item still needs a human/Daisy review decision., Choose the displayed split-item state after downstream handoff has run.…, split_item_status_after_handoff(), split_item_status_group()

### Community 33 - "Subscription Email Delivery"
Cohesion: 0.40
Nodes (5): AgentMail Transport, SMTP Transport, Synchronous Entitlement Resolution, Transactional Email Outbox, User Subscriptions

## Knowledge Gaps
- **7 isolated node(s):** `AgentMail Transport`, `SMTP Transport`, `Catalog API Keys`, `OpenClaw Compatibility`, `MinIO Storage` (+2 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `_get()` connect `Workspace Intake Models` to `User Authentication`, `Workspace Review Routes`, `Catalog Favourites`, `Catalog API Key Management`, `Workspace Classification Models`, `Daisy Reporting Schemas`, `LLM Usage Ledger`, `Agent Queue Worker`, `Catalog Credential Safety`, `Catalog Taxonomy Modes`, `API Entrypoints`, `Catalog Search Assets`, `Rights Evidence Review`?**
  _High betweenness centrality (0.087) - this node is a cross-community bridge._
- **Why does `User` connect `User Authentication` to `ORM Models and Runtime`, `Catalog Favourites`, `Catalog API Key Management`, `Publication Handoff`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Why does `AuthenticatedUser` connect `User Authentication` to `Workspace Review Routes`, `Catalog Favourites`, `Catalog API Key Management`, `Catalog Credential Safety`, `API Entrypoints`, `Workspace Policy Dependencies`?**
  _High betweenness centrality (0.033) - this node is a cross-community bridge._
- **Are the 52 inferred relationships involving `Base` (e.g. with `AgentDefinition` and `AgentFeedbackEvent`) actually correct?**
  _`Base` has 52 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `RuntimePersistenceBridge` (e.g. with `QueueRuntimeRecord` and `AgentQueueWorkerConfig`) actually correct?**
  _`RuntimePersistenceBridge` has 6 INFERRED edges - model-reasoned connections that need verification._
- **What connects `AgentMail Transport`, `SMTP Transport`, `Catalog API Keys` to the rest of the system?**
  _7 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `ORM Models and Runtime` be split into smaller, more focused modules?**
  _Cohesion score 0.060810810810810814 - nodes in this community are weakly interconnected._
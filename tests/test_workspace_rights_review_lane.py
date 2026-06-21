from pathlib import Path


APP_JSX = Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx"
WORKSPACE_PY = Path(__file__).resolve().parents[1] / "backend" / "symgov_backend" / "routes" / "workspace.py"


def test_workspace_pipeline_has_separate_rights_review_lane():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "id: 'rights_review'" in source
    assert "subtitle: 'Provenance/Rights Review'" in source
    assert "'review_coordination', 'rights_review', 'human_review'" in source


def test_rupert_publication_queue_is_on_second_screen_left_of_hannah():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "pipeline: ['intake', 'validation', 'provenance', 'classification', 'review_coordination', 'rights_review', 'human_review']" in source
    assert "intelligence: ['publication', 'curation', 'control_audit', 'market_intelligence', 'ux_feedback']" in source


def test_daisy_rights_review_queue_family_is_routed_to_rights_lane_before_agent_lane():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "queueItem.queueFamily === 'rights_review'" in source
    assert "candidate.id === 'rights_review'" in source


def test_rights_lane_cards_open_dedicated_rights_review_screen():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "['human_review', 'rights_review', 'ux_feedback'].includes(column.id)" in source
    assert "item.queueFamily === 'rights_review' || item.columnId === 'rights_review'" in source
    assert "navigate(`/rights?review=${encodeURIComponent(item.reviewCaseId)}`)" in source
    assert "queueFilter === 'rights'" in source


def test_top_nav_has_first_class_rights_button_left_of_reviews_and_route():
    source = APP_JSX.read_text(encoding="utf-8")

    assert 'to="/rights"' in source
    assert source.index('to="/rights"') < source.index('to="/reviews"')
    assert 'Route path="/rights" element={<RightsReviewPage />}' in source
    assert "function RightsReviewPage()" in source


def test_rights_screen_has_corrective_problem_fields_and_rights_actions():
    source = APP_JSX.read_text(encoding="utf-8")

    for label in ["Correct problem fields", "Corrected rights status", "Corrected rights disposition", "License or permission label", "Source URL / evidence link"]:
        assert label in source
    for label in ["Clear rights", "Restrict publication", "Request rights evidence", "Mark conflict", "Defer rights"]:
        assert label in source


def test_rights_screen_uses_picklists_for_corrected_status_and_disposition():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "const RIGHTS_STATUS_OPTIONS = [" in source
    assert "const RIGHTS_DISPOSITION_OPTIONS = [" in source
    assert "const RIGHTS_PROCESSING_OUTCOME_OPTIONS = [" in source
    assert 'aria-label="Corrected rights status"' in source
    assert 'aria-label="Corrected rights disposition"' in source
    assert 'aria-label="Corrected processing outcome"' in source
    assert "RIGHTS_STATUS_OPTIONS.map(([value, label])" in source
    assert "RIGHTS_DISPOSITION_OPTIONS.map(([value, label])" in source
    assert "RIGHTS_PROCESSING_OUTCOME_OPTIONS.map(([value, label])" in source

    status_label_index = source.index("Corrected rights status")
    disposition_label_index = source.index("Corrected rights disposition")
    processing_label_index = source.index("Corrected processing outcome")
    assert source.index("<select", status_label_index, disposition_label_index) > status_label_index
    assert source.index("<select", disposition_label_index, processing_label_index) > disposition_label_index
    assert "<input value={activeDecision.correctedRightsStatus}" not in source
    assert "<input value={activeDecision.correctedRightsDisposition}" not in source
    assert "<input value={activeDecision.correctedProcessingOutcome}" not in source
    assert "['rights_cleared'" not in source
    assert "['review_required', 'Review required']" not in source.split("const RIGHTS_DISPOSITION_OPTIONS = [", 1)[1].split("];", 1)[0]


def test_provenance_rights_stage_is_human_visible_but_separate_lane():
    source = WORKSPACE_PY.read_text(encoding="utf-8")
    app_source = APP_JSX.read_text(encoding="utf-8")

    assert '"provenance_rights_review"' in source
    assert "review.currentStage === 'provenance_rights_review' ? 'rights_review' : 'human_review'" in app_source


def test_daisy_coordination_card_stays_in_coordination_until_daisy_emits_rights_card():
    source = (Path(__file__).resolve().parents[1] / "backend" / "symgov_backend" / "routes" / "workspace.py").read_text(encoding="utf-8")

    assert "def queue_family_for_agent_queue_item" in source
    assert 'requested_review_family == "rights_review"' in source
    assert 'return "rights_review"' in source
    assert 'return default_queue_family' in source
    assert "queue_family_for_agent_queue_item(definition.slug, payload_json, definition.queue_family)" in source

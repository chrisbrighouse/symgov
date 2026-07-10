\set ON_ERROR_STOP on
\pset pager off

BEGIN;

CREATE TEMP TABLE target_packages AS
  SELECT id FROM source_packages WHERE package_code IN ('0086','0087','0088');

CREATE TEMP TABLE target_intakes AS
  SELECT id, queue_item_id FROM intake_records WHERE source_package_id IN (SELECT id FROM target_packages);

CREATE TEMP TABLE target_queue AS
  SELECT id FROM agent_queue_items
  WHERE payload_json->>'submission_batch_id' IN ('subext-20260710T134247Z','subext-20260710T135948Z','subext-20260710T144404Z')
     OR source_id IN (SELECT id FROM target_intakes)
     OR id IN (SELECT queue_item_id FROM target_intakes);

CREATE TEMP TABLE target_validation AS
  SELECT id FROM validation_reports
  WHERE source_id IN (SELECT id FROM target_intakes)
     OR queue_item_id IN (SELECT id FROM target_queue);

CREATE TEMP TABLE target_provenance AS
  SELECT id FROM provenance_assessments
  WHERE intake_record_id IN (SELECT id FROM target_intakes)
     OR queue_item_id IN (SELECT id FROM target_queue);

CREATE TEMP TABLE target_review AS
  SELECT id FROM review_cases
  WHERE source_entity_id IN (SELECT id FROM target_provenance)
     OR source_entity_id IN (SELECT id FROM target_validation)
     OR source_entity_id IN (SELECT id FROM target_intakes)
     OR source_entity_id IN (SELECT id FROM target_queue);

CREATE TEMP TABLE target_review_split AS
  SELECT id FROM review_split_items WHERE review_case_id IN (SELECT id FROM target_review);

CREATE TEMP TABLE target_review_symbol_properties AS
  SELECT id FROM review_symbol_properties
  WHERE review_case_id IN (SELECT id FROM target_review)
     OR review_split_item_id IN (SELECT id FROM target_review_split);

CREATE TEMP TABLE target_review_actions AS
  SELECT id FROM review_case_actions WHERE review_case_id IN (SELECT id FROM target_review);

CREATE TEMP TABLE target_review_decisions AS
  SELECT id FROM human_review_decisions WHERE review_case_id IN (SELECT id FROM target_review);

CREATE TEMP TABLE target_classification AS
  SELECT id FROM classification_records
  WHERE queue_item_id IN (SELECT id FROM target_queue)
     OR intake_record_id IN (SELECT id FROM target_intakes)
     OR validation_report_id IN (SELECT id FROM target_validation)
     OR provenance_assessment_id IN (SELECT id FROM target_provenance)
     OR review_case_id IN (SELECT id FROM target_review)
     OR parent_review_case_id IN (SELECT id FROM target_review)
     OR origin_batch_id IN ('subext-20260710T134247Z','subext-20260710T135948Z','subext-20260710T144404Z');

CREATE TEMP TABLE target_source_package_entries AS
  SELECT id FROM source_package_entries WHERE source_package_id IN (SELECT id FROM target_packages);

CREATE TEMP TABLE target_runs AS
  SELECT id FROM agent_runs WHERE queue_item_id IN (SELECT id FROM target_queue);

CREATE TEMP TABLE target_artifacts AS
  SELECT id FROM agent_output_artifacts WHERE queue_item_id IN (SELECT id FROM target_queue);

CREATE TEMP TABLE target_attachments AS
  SELECT id FROM attachments
  WHERE parent_id IN (SELECT id FROM target_queue)
     OR parent_id IN (SELECT id FROM target_intakes)
     OR parent_id IN (SELECT id FROM target_packages)
     OR parent_id IN (SELECT id FROM target_validation)
     OR parent_id IN (SELECT id FROM target_provenance)
     OR object_key ~ '(20260710T134247|20260710T135948|20260710T144404)';

CREATE TEMP TABLE target_audit AS
  SELECT id FROM audit_events
  WHERE entity_id IN (SELECT id FROM target_queue)
     OR entity_id IN (SELECT id FROM target_intakes)
     OR entity_id IN (SELECT id FROM target_packages)
     OR entity_id IN (SELECT id FROM target_validation)
     OR entity_id IN (SELECT id FROM target_provenance)
     OR entity_id IN (SELECT id FROM target_review)
     OR payload_json::text ~ '(20260710T134247|20260710T135948|20260710T144404|external-submission-20260710T134247Z|external-submission-20260710T135948Z|external-submission-20260710T144404Z)';

\copy (SELECT 'source_packages' AS table_name, count(*) AS count FROM target_packages UNION ALL SELECT 'intake_records', count(*) FROM target_intakes UNION ALL SELECT 'agent_queue_items', count(*) FROM target_queue UNION ALL SELECT 'validation_reports', count(*) FROM target_validation UNION ALL SELECT 'provenance_assessments', count(*) FROM target_provenance UNION ALL SELECT 'review_cases', count(*) FROM target_review UNION ALL SELECT 'review_split_items', count(*) FROM target_review_split UNION ALL SELECT 'review_symbol_properties', count(*) FROM target_review_symbol_properties UNION ALL SELECT 'review_case_actions', count(*) FROM target_review_actions UNION ALL SELECT 'human_review_decisions', count(*) FROM target_review_decisions UNION ALL SELECT 'classification_records', count(*) FROM target_classification UNION ALL SELECT 'source_package_entries', count(*) FROM target_source_package_entries UNION ALL SELECT 'agent_runs', count(*) FROM target_runs UNION ALL SELECT 'agent_output_artifacts', count(*) FROM target_artifacts UNION ALL SELECT 'attachments', count(*) FROM target_attachments UNION ALL SELECT 'audit_events', count(*) FROM target_audit) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/manifest.counts' WITH CSV HEADER;

\copy (SELECT row_to_json(t) FROM (SELECT sp.* FROM source_packages sp JOIN target_packages x USING(id) ORDER BY sp.created_at, sp.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/source_packages.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT ir.* FROM intake_records ir JOIN target_intakes x USING(id) ORDER BY ir.created_at, ir.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/intake_records.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT aq.* FROM agent_queue_items aq JOIN target_queue x USING(id) ORDER BY aq.created_at, aq.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/agent_queue_items.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT vr.* FROM validation_reports vr JOIN target_validation x USING(id) ORDER BY vr.created_at, vr.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/validation_reports.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT pa.* FROM provenance_assessments pa JOIN target_provenance x USING(id) ORDER BY pa.assessed_at, pa.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/provenance_assessments.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT rc.* FROM review_cases rc JOIN target_review x USING(id) ORDER BY rc.opened_at, rc.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/review_cases.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT rsi.* FROM review_split_items rsi JOIN target_review_split x USING(id) ORDER BY rsi.created_at, rsi.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/review_split_items.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT rsp.* FROM review_symbol_properties rsp JOIN target_review_symbol_properties x USING(id) ORDER BY rsp.created_at, rsp.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/review_symbol_properties.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT rca.* FROM review_case_actions rca JOIN target_review_actions x USING(id) ORDER BY rca.created_at, rca.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/review_case_actions.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT hrd.* FROM human_review_decisions hrd JOIN target_review_decisions x USING(id) ORDER BY hrd.created_at, hrd.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/human_review_decisions.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT cr.* FROM classification_records cr JOIN target_classification x USING(id) ORDER BY cr.created_at, cr.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/classification_records.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT spe.* FROM source_package_entries spe JOIN target_source_package_entries x USING(id) ORDER BY spe.created_at, spe.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/source_package_entries.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT ar.* FROM agent_runs ar JOIN target_runs x USING(id) ORDER BY ar.started_at, ar.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/agent_runs.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT aoa.* FROM agent_output_artifacts aoa JOIN target_artifacts x USING(id) ORDER BY aoa.created_at, aoa.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/agent_output_artifacts.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT a.* FROM attachments a JOIN target_attachments x USING(id) ORDER BY a.created_at, a.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/attachments.jsonl';
\copy (SELECT row_to_json(t) FROM (SELECT ae.* FROM audit_events ae JOIN target_audit x USING(id) ORDER BY ae.created_at, ae.id) t) TO '/data/symgov/docs/ops/backups/records-0086-0088-reset-20260710T152022Z/audit_events.jsonl';

DELETE FROM review_symbol_properties WHERE id IN (SELECT id FROM target_review_symbol_properties);
DELETE FROM review_split_items WHERE id IN (SELECT id FROM target_review_split);
DELETE FROM review_case_actions WHERE id IN (SELECT id FROM target_review_actions);
DELETE FROM human_review_decisions WHERE id IN (SELECT id FROM target_review_decisions);
DELETE FROM classification_records WHERE id IN (SELECT id FROM target_classification);
DELETE FROM review_cases WHERE id IN (SELECT id FROM target_review);
DELETE FROM attachments WHERE id IN (SELECT id FROM target_attachments);
DELETE FROM provenance_assessments WHERE id IN (SELECT id FROM target_provenance);
DELETE FROM validation_reports WHERE id IN (SELECT id FROM target_validation);
DELETE FROM intake_records WHERE id IN (SELECT id FROM target_intakes);
DELETE FROM source_package_entries WHERE id IN (SELECT id FROM target_source_package_entries);
DELETE FROM source_packages WHERE id IN (SELECT id FROM target_packages);
DELETE FROM audit_events WHERE id IN (SELECT id FROM target_audit);
DELETE FROM agent_output_artifacts WHERE id IN (SELECT id FROM target_artifacts);
DELETE FROM agent_runs WHERE id IN (SELECT id FROM target_runs);
DELETE FROM agent_queue_items WHERE id IN (SELECT id FROM target_queue);

COMMIT;

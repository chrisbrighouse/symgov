import { createElement, useEffect, useState } from 'react';

import { fetchReviewCaseClassificationPreview } from './api.js';
import { canAccessSemanticReview } from './semanticReviewJourney.js';

// SM-P1-01 WP1.5, decision Q9 (2026-09-14).
//
// WP1.5 was specified as a read-only "Engineering meaning" summary of the
// proposed semantic state, embedded beside an intake review. Measured against
// the tables, that population is empty by construction: every governed
// semantic assertion is written by the approval handoff that runs *after*
// this review, and the symbol revision itself is created from the review
// decision, so an open review case has no revision to ask about.
//
// So this forecasts instead. It shows what approving the item in focus will
// assert and which section 9.3 fields will fall into a gap, computed by the
// API from the same mapping path the approval runs. `CLAUDE.md` forbids
// presenting an illustrative value as a production one, so every label here
// says "will", and the panel states outright that nothing in it is recorded.
//
// Read-only throughout: the decision controls stay on the semantic review
// surface (WP1.4) so a governance act has exactly one audit path.

// `classification_mapping.MAPPING_GAP_REASONS`, as sentences. The vocabulary
// is closed and enforced by the backend, so an unrecognised value means the
// vocabulary moved -- it is humanised rather than shown raw, and never
// dropped, because a gap a reviewer cannot read is a gap they cannot act on.
export const GAP_REASON_LABELS = {
  no_value: 'No value was recorded',
  placeholder_value: 'The recorded value is a placeholder',
  no_scheme: 'No classification scheme exists for this field yet',
  no_node_match: 'No active node in the scheme matches this value',
  no_concept_target: 'There is no semantic concept to attach this to',
  no_relationship_table: 'The concept relationship this needs does not exist yet',
  no_standard_match: 'No registered standard matches this value',
  ambiguous_standard_version: 'That standard has more than one active edition',
  no_source_package: 'The submission names no source package',
  carried_in_payload: 'Kept as free text on the revision, not as structured data',
  mapping_failed: 'The mapping could not be completed',
  already_assigned: 'The revision already carries this classification',
};

// `classification_mapping.MATCH_BASES`. Shown because a value matched through
// the legacy browse taxonomy is a weaker match than an exact one, and a
// reviewer confirming a forecast should be able to see which they are looking
// at before the approval makes it a proposal.
export const MATCH_BASIS_LABELS = {
  exact: 'Exact match',
  plural_variant: 'Matched on a singular/plural variant',
  legacy_taxonomy: 'Matched through the legacy browse taxonomy',
};

// The names the Reviews pane already uses for these values, so the forecast
// reads as a statement about the fields beside it rather than about a
// different vocabulary.
export const FORECAST_FIELD_LABELS = {
  engineeringDiscipline: 'Discipline',
  category: 'Category',
  symbolFamily: 'Symbol family',
  industry: 'Industry',
  processCategory: 'Process category',
  parentEquipmentClass: 'Equipment class',
  standardsSource: 'Standards source',
  libraryProvenanceClass: 'Provenance class',
  sourceClassification: 'Source classification',
  aliases: 'Aliases',
  keywords: 'Keywords',
  sourceRefs: 'Source references',
  format: 'Format',
};

export const FORECAST_TARGET_LABELS = {
  symbol_standard_links: 'Linked to the standard edition it names',
  source_package_entries: 'Recorded against its source package',
};

function humanise(value) {
  const words = String(value || '')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim()
    .toLowerCase();
  if (!words) return '';
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function formatGapReason(reason) {
  return GAP_REASON_LABELS[reason] || humanise(reason) || 'Not mapped';
}

export function formatMatchBasis(basis) {
  return MATCH_BASIS_LABELS[basis] || humanise(basis) || 'Matched';
}

export function formatForecastField(field) {
  return FORECAST_FIELD_LABELS[field] || humanise(field) || 'Field';
}

export function formatForecastTarget(target) {
  return FORECAST_TARGET_LABELS[target] || humanise(target) || 'Recorded';
}

const DEFAULT_API = { fetchPreview: fetchReviewCaseClassificationPreview };

export function ReviewClassificationForecast({ auth, reviewCaseId, splitItemId = '', api = DEFAULT_API }) {
  const [state, setState] = useState({ loading: false, error: '', preview: null });

  // The API's own boundary, reproduced rather than approximated. `/reviews` is
  // already gated `RequireAnyRole roles={['admin','reviewer']}`, which is an
  // exact match for the router's `require_any_role({"admin", "reviewer"})`,
  // so in practice this predicate adds the default-off flag -- but sharing
  // `canAccessSemanticReview` with the WP1.4 surface is what keeps the UI from
  // drifting into a stricter or looser rule than the one actually enforced.
  const permitted = canAccessSemanticReview(auth?.user);

  useEffect(() => {
    if (!permitted || !reviewCaseId) {
      setState({ loading: false, error: '', preview: null });
      return undefined;
    }
    let cancelled = false;
    setState({ loading: true, error: '', preview: null });
    api.fetchPreview(reviewCaseId, splitItemId ? { splitItemId } : {})
      .then((preview) => {
        if (!cancelled) setState({ loading: false, error: '', preview });
      })
      .catch((error) => {
        if (!cancelled) {
          setState({ loading: false, error: error.message || 'Approval forecast unavailable.', preview: null });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [api, permitted, reviewCaseId, splitItemId]);

  if (!permitted || !reviewCaseId) return null;

  const { loading, error, preview } = state;

  return createElement(
    'div',
    { className: 'copy-block review-classification-forecast', 'aria-labelledby': 'review-classification-forecast-heading' },
    createElement('h4', { id: 'review-classification-forecast-heading' }, 'What approving this will assert'),
    createElement(
      'p',
      { className: 'daisy-empty-text' },
      'A forecast of the approval, not recorded state. Structured classification is written when the '
      + 'decision is processed; nothing below exists yet, and rejecting this item writes none of it.',
    ),
    loading
      ? createElement('p', { role: 'status' }, 'Loading the approval forecast…')
      : null,
    error
      ? createElement('p', { role: 'alert', className: 'error-text' }, error)
      : null,
    preview ? forecastBody(preview) : null,
  );
}

function forecastBody(preview) {
  const willAssert = Array.isArray(preview.willAssert) ? preview.willAssert : [];
  const willGap = Array.isArray(preview.willGap) ? preview.willGap : [];
  const willLink = Array.isArray(preview.willLink) ? preview.willLink : [];

  return createElement(
    'div',
    { className: 'review-forecast-body' },
    preview.disciplineUsed || preview.categoryUsed
      ? createElement(
        'p',
        { className: 'set-admin-muted' },
        `Forecast from discipline ${preview.disciplineUsed || 'none'} and category ${preview.categoryUsed || 'none'}`
        + ' — the reviewed values on this item, where they are set.',
      )
      : null,
    createElement(
      'section',
      { 'aria-label': 'Classifications the approval will propose' },
      createElement('h5', null, `Will propose (${willAssert.length})`),
      willAssert.length
        ? createElement(
          'ul',
          { className: 'review-forecast-list' },
          willAssert.map((item) => createElement(
            'li',
            { key: `${item.schemeCode}:${item.classificationNodeId}:${item.assignmentRole}` },
            createElement('strong', null, `${formatForecastField(item.field)}: ${item.nodeLabel}`),
            createElement(
              'span',
              { className: 'set-admin-muted' },
              ` ${item.nodeCode} in ${item.schemeCode} · ${item.assignmentRole} · `
              + `${formatMatchBasis(item.matchBasis)} from “${item.rawValue}”`,
            ),
          )),
        )
        : createElement(
          'p',
          { className: 'daisy-empty-text' },
          'Approving this item will record no structured classification. Every value below is a gap.',
        ),
    ),
    willLink.length
      ? createElement(
        'section',
        { 'aria-label': 'Source links the approval will assert' },
        createElement('h5', null, `Will link (${willLink.length})`),
        createElement(
          'ul',
          { className: 'review-forecast-list' },
          willLink.map((item) => createElement(
            'li',
            { key: `${item.field}:${item.target}` },
            createElement('strong', null, `${formatForecastField(item.field)}: ${item.rawValue || 'Not recorded'}`),
            createElement('span', { className: 'set-admin-muted' }, ` ${formatForecastTarget(item.target)}`),
          )),
        ),
      )
      : null,
    createElement(
      'section',
      { 'aria-label': 'Fields the approval will leave unmapped' },
      createElement('h5', null, `Will not map (${willGap.length})`),
      willGap.length
        ? createElement(
          'ul',
          { className: 'review-forecast-list' },
          willGap.map((gap) => createElement(
            'li',
            { key: `${gap.field}:${gap.reason}` },
            createElement('strong', null, `${formatForecastField(gap.field)}: ${gap.rawValue || 'No value'}`),
            createElement(
              'span',
              { className: 'set-admin-muted' },
              ` ${formatGapReason(gap.reason)}${gap.detail ? ` — ${gap.detail}` : ''}`,
            ),
          )),
        )
        : createElement('p', { className: 'daisy-empty-text' }, 'Every recorded field maps.'),
    ),
  );
}

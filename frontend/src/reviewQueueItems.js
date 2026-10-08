const TERMINAL_REVIEW_STAGES = ['approved', 'closed', 'published', 'rejected', 'superseded_by_raster_split'];

export function isTerminalReviewStage(stage) {
  return TERMINAL_REVIEW_STAGES.includes(String(stage || '').trim().toLowerCase());
}

// A Daisy report only becomes a Reviews queue card while its case is still
// open. `closedAt` is authoritative: a retired case carries a stage the
// terminal list does not know, so the stage alone would resurface it.
export function isDaisyReportOpenForReview(report) {
  if (report?.closedAt) return false;
  return !isTerminalReviewStage(report?.currentStage || report?.coordinationStatus);
}

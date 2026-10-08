const TERMINAL_REVIEW_STAGES = ['approved', 'closed', 'published', 'rejected', 'superseded_by_raster_split'];

export function isTerminalReviewStage(stage) {
  return TERMINAL_REVIEW_STAGES.includes(String(stage || '').trim().toLowerCase());
}

// A Daisy report only becomes a Reviews queue card while its case is open.
// `closedAt` is authoritative: a retired case carries a stage the terminal
// list does not know, so the stage alone would resurface it. A report whose
// case no longer exists has no `currentStage` (the API reads it from the case)
// and cannot be opened or decided, so it is not a card either.
export function isDaisyReportOpenForReview(report) {
  if (!report?.currentStage) return false;
  if (report.closedAt) return false;
  return !isTerminalReviewStage(report.currentStage);
}

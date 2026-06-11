export const DEFAULT_AGENT_RUN_DURATION_SECONDS = 60;

export function formatCountdown(seconds) {
  const safeSeconds = Math.max(0, Math.ceil(Number(seconds) || 0));
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}

export function durationSecondsToParts(seconds) {
  const safeSeconds = Math.max(0, Math.floor(Number(seconds) || 0));
  return {
    minutes: Math.floor(safeSeconds / 60),
    seconds: safeSeconds % 60
  };
}

export function setDurationPart(durationSeconds, part, rawValue) {
  const nextParts = durationSecondsToParts(durationSeconds);
  const max = part === 'seconds' ? 59 : Number.POSITIVE_INFINITY;
  const parsed = Number.parseInt(String(rawValue), 10);
  const sanitized = Number.isFinite(parsed) ? Math.min(Math.max(parsed, 0), max) : 0;
  nextParts[part] = sanitized;
  return nextParts.minutes * 60 + nextParts.seconds;
}

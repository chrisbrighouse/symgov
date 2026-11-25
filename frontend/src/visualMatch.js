// Helpers for the Catalog's Visual match tab: choosing an image, the request it sends, and the plain-language
// copy for what comes back. Free of React so they can be tested directly.

export const VISUAL_VIEW = 'visual';
export const VISUAL_MAX_BYTES = 8 * 1024 * 1024;
export const VISUAL_ACCEPTED_TYPES = ['image/png', 'image/jpeg'];
export const VISUAL_FILTER_KEYS = ['catalogDisciplines', 'catalogCategories', 'useCases', 'availableFormats'];

// Said once, above the drop area, so nobody sends an image without knowing where it goes.
export const VISUAL_MATCH_DISCLOSURE =
  'The image you add is sent to Google\'s embedding service through OpenRouter so Symgov can find similar symbols. ' +
  'It is not used for training, and Google may keep it briefly only for abuse monitoring. Symgov does not store it. ' +
  'Do not add images you are not allowed to share.';

export const VISUAL_MATCH_BANDS_NOTE =
  'The match labels are provisional: they come from whether two matching methods agree, not from a measured ' +
  'probability, and they have not yet been validated on new drawings. Always check a match against your drawing.';

export const BAND_COPY = {
  likely: {
    label: 'Likely match',
    text: 'Two separate methods agree on this one. It is still a suggestion, so check it against your drawing.'
  },
  possible: {
    label: 'Possible match',
    text: 'It looks similar, but the methods do not both point here.'
  },
  weak: {
    label: 'Weak match',
    text: 'A distant resemblance, shown because you asked for weaker matches.'
  }
};

export function bandCopy(band) {
  return BAND_COPY[band] || { label: 'Match', text: '' };
}

export function validateImageFile(file) {
  if (!file || typeof file !== 'object') return { ok: false, message: 'Choose a PNG or JPEG image.' };
  if (!VISUAL_ACCEPTED_TYPES.includes(file.type)) {
    return { ok: false, message: 'Only PNG and JPEG images can be matched.' };
  }
  if (!(file.size > 0)) return { ok: false, message: 'That image is empty.' };
  if (file.size > VISUAL_MAX_BYTES) {
    return { ok: false, message: 'That image is larger than 8 MB. Crop it to the symbol and try again.' };
  }
  return { ok: true, message: '' };
}

// The first image on a paste or drop, if there is one.
export function firstImageFile(dataTransfer) {
  const items = Array.from(dataTransfer?.items || []);
  for (const item of items) {
    if (item.kind === 'file' && String(item.type).startsWith('image/')) {
      const file = item.getAsFile?.();
      if (file) return file;
    }
  }
  const files = Array.from(dataTransfer?.files || []);
  return files.find((file) => String(file.type).startsWith('image/')) || null;
}

export function bytesToBase64(bytes) {
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export async function fileToBase64(file) {
  return bytesToBase64(new Uint8Array(await file.arrayBuffer()));
}

// Only the filters the server knows, and only the ticked ones.
export function buildVisualMatchBody({ imageBase64, filters = {}, includeWeak = false, limit = 10 }) {
  const clean = {};
  for (const key of VISUAL_FILTER_KEYS) {
    const values = (filters[key] || []).filter((value) => typeof value === 'string' && value.trim());
    if (values.length) clean[key] = values;
  }
  return { imageBase64, filters: clean, includeWeak: Boolean(includeWeak), limit };
}

export function visualMatchErrorMessage(status, fallback = '') {
  if (status === 404) return 'Visual matching is not switched on for this environment.';
  if (status === 413) return 'That image is too large. Crop it to the symbol and try again.';
  if (status === 415) return 'Only PNG and JPEG images can be matched.';
  if (status === 422) return fallback || 'One of the filters is not valid.';
  if (status === 503) return 'Visual matching is busy or not ready. Try again in a minute.';
  if (status === 0) return 'Symgov could not be reached. Check your connection and try again.';
  return fallback || 'The image could not be matched.';
}

// What to say about the result as a whole. Never claims a symbol does not exist.
export function resultNotices(response) {
  const notices = [];
  if (!response) return notices;
  if (response.degraded) {
    notices.push('The image service was unavailable, so a simpler method ranked these. They are less reliable, and none is marked as a likely match.');
  }
  if (Array.isArray(response.warnings) && response.warnings.includes('some_symbols_not_embedded')) {
    notices.push('A few symbols have not been indexed yet and were not searched.');
  }
  const pending = Number(response.privateSymbolsPending || 0);
  if (pending > 0) {
    notices.push(pending === 1
      ? '1 of your organization\'s private symbols is still being indexed and was not searched. Try again in a few minutes.'
      : `${pending} of your organization's private symbols are still being indexed and were not searched. Try again in a few minutes.`);
  }
  if (response.strongerMatchOutsideFilters) {
    notices.push('There may be a closer match outside your filters. Clear a filter to see it.');
  }
  return notices;
}

export function groupHeading(group, index) {
  const copy = bandCopy(group?.band);
  const count = Array.isArray(group?.symbols) ? group.symbols.length : 0;
  const same = count > 1 ? ` · ${count} interchangeable symbols` : '';
  return `${index + 1}. ${copy.label}${same}`;
}

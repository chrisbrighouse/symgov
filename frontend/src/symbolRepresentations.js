import { buildCatalogDownloadOptions } from './catalogDownload.js';
import { formatsSharingFrame } from './symbolOverlay.js';
import { stateVariantList } from './symbolGeometry.js';

// What the viewer can show: one representation per asset (format x state). A
// representation either has an image URL the browser can draw, or is
// download-only (DXF, DWG and the like).

const VIEWABLE_FORMATS = new Set(['SVG', 'PNG', 'JPG', 'GIF', 'WEBP']);

export function normalizeFormat(value) {
  const normalized = String(value || '').trim().replace(/^\./, '').toUpperCase();
  return normalized === 'JPEG' ? 'JPG' : normalized;
}

export function isViewableFormat(format) {
  return VIEWABLE_FORMATS.has(normalizeFormat(format));
}

function withFormat(url, format, resolveUrl) {
  const resolved = resolveUrl(url);
  if (!resolved) return null;
  try {
    const parsed = new URL(resolved, 'http://symgov.invalid');
    parsed.searchParams.set('format', format);
    // Keep a relative URL relative.
    return resolved.startsWith('http') ? parsed.toString() : `${parsed.pathname}${parsed.search}`;
  } catch {
    return resolved;
  }
}

function conditionOf(value) {
  return typeof value === 'string' ? value.trim() : '';
}

// The primary drawings first (one per format), then the states. The first
// entry that can be viewed is the default selection.
export function buildRepresentations(symbol, { resolveUrl = (url) => url } = {}) {
  const items = [];
  const seenFormats = new Set();
  const previewUrl = symbol?.previewUrl || null;
  const defaultFormat = normalizeFormat(symbol?.previewAsset?.format);

  const assets = Array.isArray(symbol?.downloadAssets) ? symbol.downloadAssets : [];
  const primaryAssets = assets.filter((asset) => asset && asset.role !== 'option');
  const previewAssets = [...(symbol?.previewAssets || []), symbol?.previewAsset].filter(Boolean);
  // A symbol whose download list is empty can still have a preview.
  const candidates = primaryAssets.length ? primaryAssets : previewAssets;

  candidates.forEach((asset) => {
    const format = normalizeFormat(asset.format);
    if (!format || seenFormats.has(format)) return;
    seenFormats.add(format);
    const viewable = isViewableFormat(format) && Boolean(previewUrl);
    items.push({
      key: `asset-${format}`,
      kind: 'primary',
      format,
      label: 'Primary drawing',
      condition: '',
      filename: asset.filename || '',
      viewable,
      url: viewable ? withFormat(previewUrl, format, resolveUrl) : null,
      sharesPrimaryFrame: formatsSharingFrame(format)
    });
  });

  stateVariantList(symbol).forEach((variant) => {
    items.push({
      key: `state-${variant.index}`,
      kind: 'state',
      stateIndex: variant.index,
      format: 'SVG',
      label: `State ${variant.index}`,
      condition: conditionOf(variant.condition),
      filename: variant.filename,
      viewable: true,
      url: resolveUrl(variant.url),
      sharesPrimaryFrame: true
    });
  });

  // Primary drawing: the preview's own format when it can be viewed.
  const primaries = items.filter((item) => item.kind === 'primary');
  const defaultItem = primaries.find((item) => item.format === defaultFormat && item.viewable)
    || primaries.find((item) => item.viewable)
    || items.find((item) => item.viewable)
    || null;

  return { items, defaultKey: defaultItem ? defaultItem.key : '' };
}

// The download picker: each format the symbol offers, and, when it has
// option (state) variants and an SVG, the SVG + variants zip.
export function downloadChoices(symbol) {
  const formats = buildCatalogDownloadOptions([symbol]);
  const choices = formats.map((format) => ({ value: format, label: format, format, includeStateVariants: false }));
  if (formats.includes('SVG') && stateVariantList(symbol).length > 0) {
    const at = choices.findIndex((choice) => choice.value === 'SVG');
    choices.splice(at + 1, 0, {
      value: 'SVG+STATES',
      label: 'SVG + state variants (zip)',
      format: 'SVG',
      includeStateVariants: true
    });
  }
  return choices;
}

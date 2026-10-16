import { createElement, useState } from 'react';

import { appConfig } from './config.js';

// The legend sheet is built by the API (`legend_sheet.py`): one SVG holding
// every page of an A3 landscape grid, stacked, each page a
// `<g class="legend-page">`. PDF is the browser's print dialog, so this
// module only fetches the SVG and lays it out one page per printed sheet.

const PAGE_WIDTH_MM = 420;
const PAGE_HEIGHT_MM = 297;

export function legendSheetPath(kind, id) {
  const root = kind === 'project' ? 'projects' : 'symbol-sets';
  return `/org/me/${root}/${encodeURIComponent(id)}/legend.svg`;
}

export function legendPageCount(svgText) {
  const matches = String(svgText || '').match(/class="legend-page"/g);
  return matches ? matches.length : 0;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]
  ));
}

// The sheet is defined once (hidden) and each printed page is a `<use>` of it
// through its own viewBox, so a 500-symbol sheet is not repeated per page.
export function buildLegendPrintHtml(svgText, title = 'Legend') {
  const pages = legendPageCount(svgText);
  if (!pages) return '';
  const definition = String(svgText).replace(/^<\?xml[^>]*\?>\s*/, '').replace(/<svg\b/, '<svg id="legend-root"');
  const sheets = Array.from({ length: pages }, (_, index) => (
    `<section class="legend-print-page"><svg xmlns="http://www.w3.org/2000/svg" width="${PAGE_WIDTH_MM}mm" height="${PAGE_HEIGHT_MM}mm" viewBox="0 ${PAGE_HEIGHT_MM * index} ${PAGE_WIDTH_MM} ${PAGE_HEIGHT_MM}" role="img" aria-label="${escapeHtml(title)}, page ${index + 1} of ${pages}"><use href="#legend-root" x="0" y="0" width="${PAGE_WIDTH_MM}" height="${PAGE_HEIGHT_MM * pages}"/></svg></section>`
  )).join('');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>`
    + '<style>@page{size:A3 landscape;margin:0}html,body{margin:0;background:#fff}'
    + '.legend-source{position:absolute;width:0;height:0;overflow:hidden}'
    + `.legend-print-page{width:${PAGE_WIDTH_MM}mm;height:${PAGE_HEIGHT_MM}mm;overflow:hidden;break-after:page}`
    + '.legend-print-page:last-child{break-after:auto}'
    + '@media screen{body{background:#e2e8f0}.legend-print-page{margin:12px auto;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.3)}}'
    + `</style></head><body><div class="legend-source" aria-hidden="true">${definition}</div>${sheets}</body></html>`;
}

export async function requestLegendSheet(path, { fetchImpl = globalThis.fetch, apiRoot = appConfig.apiRoot } = {}) {
  if (!apiRoot) return { ok: false, message: 'No API root configured for this environment.' };
  try {
    const response = await fetchImpl(`${apiRoot}${path}`, { credentials: 'include', cache: 'no-store' });
    if (!response.ok) {
      return { ok: false, message: response.status === 404 ? 'This legend is not available.' : 'The legend could not be built.' };
    }
    const svg = await response.text();
    if (!legendPageCount(svg)) return { ok: false, message: 'The legend came back empty.' };
    return { ok: true, svg };
  } catch (error) {
    return { ok: false, message: error instanceof Error ? error.message : 'The legend could not be built.' };
  }
}

// The window is opened before the request so the browser treats it as a
// response to the click; it is closed again if the legend cannot be built.
export async function openLegendForPrint(path, title, { openWindow = (...args) => globalThis.window.open(...args), ...options } = {}) {
  const target = openWindow('', '_blank');
  if (!target) return { ok: false, message: 'Allow pop-ups to open the legend.' };
  const result = await requestLegendSheet(path, options);
  if (!result.ok) {
    target.close();
    return result;
  }
  target.document.open();
  target.document.write(buildLegendPrintHtml(result.svg, title));
  target.document.close();
  target.focus();
  if (typeof target.print === 'function') setTimeout(() => target.print(), 300);
  return { ok: true, message: '' };
}

export function LegendButton({ kind, id, code, title, open = openLegendForPrint }) {
  const [state, setState] = useState({ busy: false, message: '' });
  const run = async () => {
    setState({ busy: true, message: '' });
    const result = await open(legendSheetPath(kind, id), title);
    setState({ busy: false, message: result.ok ? '' : result.message });
  };
  return createElement('span', { className: 'legend-button' },
    createElement('button', {
      type: 'button',
      disabled: state.busy,
      onClick: run,
      'aria-label': `Open legend sheet for ${kind === 'project' ? 'Project' : 'Symbol Set'} ${code}`,
    }, state.busy ? 'Building legend…' : 'Legend sheet'),
    state.message ? createElement('span', { role: 'alert', className: 'set-admin-status error' }, state.message) : null,
  );
}

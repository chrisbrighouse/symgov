const FORMAT_ORDER = ['DXF', 'DWG', 'SVG', 'PNG', 'JPG', 'JPEG', 'PDF', 'ZIP'];

function normalizeFormat(value) {
  const normalized = String(value || '').trim().replace(/^\./, '').toUpperCase();
  return normalized === 'JPEG' ? 'JPG' : normalized;
}

export function buildCatalogDownloadOptions(symbols = []) {
  const formats = new Set();
  symbols.forEach((symbol) => {
    (symbol?.downloadAssets || []).forEach((asset) => {
      const format = normalizeFormat(asset?.format);
      if (format) formats.add(format);
    });
  });
  return [...formats].sort((left, right) => {
    const leftIndex = FORMAT_ORDER.indexOf(left);
    const rightIndex = FORMAT_ORDER.indexOf(right);
    if (leftIndex === -1 && rightIndex === -1) return left.localeCompare(right);
    if (leftIndex === -1) return 1;
    if (rightIndex === -1) return -1;
    return leftIndex - rightIndex;
  });
}

export function catalogDownloadAvailability(selectedCount, format, busy = false) {
  const count = Number(selectedCount || 0);
  let reason = '';
  if (count < 1) reason = 'Select at least one symbol.';
  else if (count > 10) reason = 'Select no more than 10 symbols.';
  else if (!normalizeFormat(format)) reason = 'Choose a download format.';
  else if (busy) reason = 'Download is being prepared.';
  return {
    enabled: !reason,
    label: `Download (${count})`,
    reason
  };
}

export function parseCatalogDownloadFilename(contentDisposition = '') {
  const encoded = String(contentDisposition).match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1]);
    } catch {
      return encoded[1];
    }
  }
  const quoted = String(contentDisposition).match(/filename="([^"]+)"/i);
  if (quoted) return quoted[1];
  const plain = String(contentDisposition).match(/filename=([^;]+)/i);
  return plain ? plain[1].trim() : '';
}

export function catalogDownloadResultMessage({ downloadedCount, selectedCount, skippedSymbols = [], format = '' }) {
  const downloaded = Number(downloadedCount || 0);
  const selected = Number(selectedCount || 0);
  const normalizedFormat = normalizeFormat(format);
  if (!skippedSymbols.length) {
    return `Downloaded ${downloaded} selected symbol${downloaded === 1 ? '' : 's'}.`;
  }
  return `Downloaded ${downloaded} of ${selected} selected symbols. ${normalizedFormat} is not available for: ${skippedSymbols.join(', ')}.`;
}

export async function requestCatalogDownload({ apiRoot, symbolIds, format, fetchImpl = fetch }) {
  if (!apiRoot) throw new Error('API root is not configured.');
  const response = await fetchImpl(`${apiRoot}/catalog/symbols/download`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbolIds, format: normalizeFormat(format) })
  });
  if (!response.ok) {
    let detail = 'Symbol download failed.';
    try {
      const payload = await response.json();
      detail = payload?.detail || detail;
    } catch {
      // Keep the stable fallback for non-JSON server errors.
    }
    throw new Error(detail);
  }
  const skippedSymbols = String(response.headers.get('X-Symgov-Skipped-Symbols') || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean);
  return {
    blob: await response.blob(),
    filename: parseCatalogDownloadFilename(response.headers.get('Content-Disposition')) || 'symgov-symbols.zip',
    selectedCount: Number(response.headers.get('X-Symgov-Selected-Count') || symbolIds.length),
    downloadedCount: Number(response.headers.get('X-Symgov-Downloaded-Count') || 0),
    skippedSymbols
  };
}

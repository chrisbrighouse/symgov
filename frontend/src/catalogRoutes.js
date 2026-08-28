const AUTHENTICATION_PATHS = new Set(['/login', '/select-organization', '/change-pin']);
const PERCENT_ESCAPE = /%(?![0-9a-fA-F]{2})/;
const CONTROL_CHARACTER = /[\u0000-\u001f\u007f]/;
const SCHEME = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;
const CATALOG_SYMBOL_ID = /^[A-Z0-9](?:[A-Z0-9-]{0,30}[A-Z0-9])$/;

function fallbackDestination(fallback) {
  if (typeof fallback !== 'string' || fallback === '' || fallback === '/login') return '/standards';
  return fallback === safeInternalDestination(fallback, '/standards') ? fallback : '/standards';
}

function decodedParts(value) {
  if (PERCENT_ESCAPE.test(value)) return null;
  try {
    const decoded = decodeURIComponent(value);
    if (CONTROL_CHARACTER.test(decoded) || decoded.includes('\\') || decoded.startsWith('//') || SCHEME.test(decoded)) return null;
    const pathname = decodeURIComponent(value.split(/[?#]/, 1)[0]);
    if (pathname.split('/').some((segment) => segment === '.' || segment === '..')) return null;
    if (AUTHENTICATION_PATHS.has(pathname)) return null;
    return decoded;
  } catch {
    return null;
  }
}

export function safeInternalDestination(value, fallback = '/standards') {
  const safeFallback = fallback === '/standards' ? '/standards' : fallbackDestination(fallback);
  if (typeof value !== 'string' || value.length === 0) return safeFallback;
  if (!value.startsWith('/') || value.startsWith('//') || value.startsWith('///')) return safeFallback;
  if (CONTROL_CHARACTER.test(value) || value.includes('\\')) return safeFallback;
  if (decodedParts(value) === null) return safeFallback;
  return value;
}

export function internalDestinationFromLocation(location, fallback = '/standards') {
  if (!location || typeof location !== 'object' || Array.isArray(location)) return fallbackDestination(fallback);
  const { pathname, search = '', hash = '' } = location;
  if (
    typeof pathname !== 'string' ||
    (search !== '' && (typeof search !== 'string' || !search.startsWith('?'))) ||
    (hash !== '' && (typeof hash !== 'string' || !hash.startsWith('#')))
  ) return fallbackDestination(fallback);
  return safeInternalDestination(`${pathname}${search}${hash}`, fallback);
}

export function destinationFromRouterState(state, fallback = '/standards') {
  if (!state || typeof state !== 'object' || Array.isArray(state)) return fallbackDestination(fallback);
  const from = state.from;
  if (typeof from === 'string') return safeInternalDestination(from, fallback);
  return fallbackDestination(fallback);
}

export function routeForCatalogSymbol(catalogSymbolId) {
  if (typeof catalogSymbolId !== 'string' || !CATALOG_SYMBOL_ID.test(catalogSymbolId)) return null;
  return `/s/${encodeURIComponent(catalogSymbolId)}`;
}

export function absoluteHashRoute(origin, semanticPath) {
  if (typeof origin !== 'string' || typeof semanticPath !== 'string') return null;
  let parsed;
  try {
    parsed = new URL(origin);
  } catch {
    return null;
  }
  if (
    !['http:', 'https:'].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== '/' ||
    parsed.search ||
    parsed.hash
  ) return null;
  const safePath = safeInternalDestination(semanticPath, '');
  if (safePath !== semanticPath || !safePath) return null;
  return `${parsed.origin}/#${safePath}`;
}

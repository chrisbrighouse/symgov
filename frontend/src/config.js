function inferApiRoot() {
  const metaRoot = document.querySelector('meta[name="symgov-api-root"]')?.content?.trim();
  const metaBase = document.querySelector('meta[name="symgov-api-base-url"]')?.content?.trim();
  const windowConfig = window.SYMGOV_CONFIG || {};
  const runtimeRoot =
    window.SYMGOV_API_ROOT ||
    window.SYMGOV_API_BASE_URL ||
    windowConfig.apiRoot ||
    windowConfig.apiBaseUrl ||
    metaRoot ||
    metaBase;

  if (runtimeRoot) {
    return runtimeRoot.replace(/\/$/, '');
  }

  const { hostname, origin, protocol } = window.location;

  if (protocol === 'file:') {
    return 'http://127.0.0.1:8010/api/v1';
  }

  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return `${origin}/api/v1`;
  }

  return '';
}

export const appConfig = {
  build: window.SYMGOV_CONFIG?.build || '',
  apiRoot: inferApiRoot()
};

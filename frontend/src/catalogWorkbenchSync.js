// Catalog preferences, saved views and clipboard belong to the signed-in
// account and are saved server-side (X-01). These are the localStorage keys
// the Catalog used before; they held whichever account last used the browser,
// so they are deleted on load rather than imported into the next account.
export const LEGACY_CATALOG_STORAGE_KEYS = [
  'symgov.catalog.preferences.v1',
  'symgov.catalog.savedViews.v1',
  'symgov.catalog.clipboard.v1'
];

export function clearLegacyCatalogStorage(storage) {
  if (!storage) {
    return;
  }
  LEGACY_CATALOG_STORAGE_KEYS.forEach((key) => {
    try {
      storage.removeItem(key);
    } catch (error) {
      console.warn(`Unable to remove ${key} from local storage`, error);
    }
  });
}

// Debounces saves per section, and sends at most one request per section at a
// time, so a burst of checkbox toggles becomes one request and an older
// request can never land after a newer one.
export function createCatalogWorkbenchSaver({ save, delayMs = 400, onError = () => {}, onSaved = () => {} }) {
  const sections = new Map();

  function sectionState(section) {
    if (!sections.has(section)) {
      sections.set(section, { timer: null, pending: undefined, hasPending: false, inFlight: null });
    }
    return sections.get(section);
  }

  async function run(section) {
    const state = sectionState(section);
    if (state.inFlight || !state.hasPending) {
      return state.inFlight;
    }
    const value = state.pending;
    state.pending = undefined;
    state.hasPending = false;
    state.inFlight = (async () => {
      try {
        await save(section, value);
        onSaved(section);
      } catch (error) {
        onError(section, error);
      } finally {
        state.inFlight = null;
      }
      if (state.hasPending && !state.timer) {
        await run(section);
      }
    })();
    return state.inFlight;
  }

  function schedule(section, value) {
    const state = sectionState(section);
    state.pending = value;
    state.hasPending = true;
    if (state.timer) {
      clearTimeout(state.timer);
    }
    state.timer = setTimeout(() => {
      state.timer = null;
      run(section);
    }, delayMs);
  }

  async function flush() {
    const runs = Array.from(sections.entries()).map(([section, state]) => {
      if (state.timer) {
        clearTimeout(state.timer);
        state.timer = null;
      }
      return (state.inFlight || Promise.resolve()).then(() => run(section));
    });
    await Promise.all(runs);
  }

  return { schedule, flush };
}

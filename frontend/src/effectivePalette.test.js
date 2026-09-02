import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

import { canMountEffectivePalette } from './projectContext.js';
import { EffectivePalettePanel } from './EffectivePalettePanel.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function baseAuth(overrides = {}) {
  return {
    user: {
      session: { purpose: 'application', mode: 'organization', activeOrganizationId: 'org-1' },
      organization: { id: 'org-1', baseRole: 'member' },
      capabilities: { symbolSetsEnabled: true },
      ...overrides,
    },
  };
}

function baseItem(overrides = {}) {
  return {
    governedSymbolId: 'sym-1',
    source: 'set',
    canonicalName: 'Fire Extinguisher',
    category: 'fire',
    discipline: 'fire-safety',
    sortOrder: 0,
    groupName: null,
    displayLabel: null,
    preferredFormat: null,
    notes: null,
    provenance: {},
    currentRevisionId: 'rev-1',
    ...overrides,
  };
}

function buildApi({ selectedProject = { id: 'proj-1', code: 'PRJ', name: 'Project One' }, palette = { activeSet: null, reason: 'none', items: [], page: 1, pageSize: 50, total: 0 } } = {}) {
  return {
    getContext: async () => ({ selectedProject, activeSet: palette.activeSet, reason: palette.reason }),
    getPalette: async () => palette,
  };
}

describe('canMountEffectivePalette', () => {
  it('mirrors canMountProjectContext gating', () => {
    assert.equal(canMountEffectivePalette(baseAuth()), true);
    assert.equal(canMountEffectivePalette(baseAuth({ capabilities: { symbolSetsEnabled: false } })), false);
    assert.equal(canMountEffectivePalette(null), false);
  });
});

describe('EffectivePalettePanel', () => {
  it('renders nothing when the gate fails', async () => {
    const api = buildApi();
    let renderer;
    await act(async () => { renderer = create(createElement(EffectivePalettePanel, { auth: baseAuth({ capabilities: { symbolSetsEnabled: false } }), api })); });
    assert.equal(renderer.toJSON(), null);
    await act(async () => renderer.unmount());
  });

  it('prompts for a Project when none is selected', async () => {
    const api = buildApi({ selectedProject: null });
    let renderer;
    await act(async () => { renderer = create(createElement(EffectivePalettePanel, { auth: baseAuth(), api })); });
    assert.match(JSON.stringify(renderer.toJSON()), /Select a Project above/);
    await act(async () => renderer.unmount());
  });

  it('shows an empty state when the palette has no items', async () => {
    const api = buildApi();
    let renderer;
    await act(async () => { renderer = create(createElement(EffectivePalettePanel, { auth: baseAuth(), api })); });
    assert.match(JSON.stringify(renderer.toJSON()), /No symbols are currently in the effective palette/);
    await act(async () => renderer.unmount());
  });

  it('lists items with human-readable identity, source badges, and grouping', async () => {
    const api = buildApi({
      palette: {
        activeSet: { id: 'set-1', code: 'SET-01', name: 'Electrical' },
        reason: 'project_default',
        items: [
          baseItem({ groupName: 'Electrical' }),
          baseItem({ governedSymbolId: 'sym-2', source: 'organization_wide', canonicalName: 'Org Beacon', groupName: 'Organization-wide' }),
        ],
        page: 1,
        pageSize: 50,
        total: 2,
      },
    });
    let renderer;
    await act(async () => { renderer = create(createElement(EffectivePalettePanel, { auth: baseAuth(), api })); });
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Fire Extinguisher/);
    assert.match(text, /Org Beacon/);
    assert.match(text, /Using the Project default Symbol Set \(SET-01\)/);
    assert.match(text, /Organization-wide/);
    const groupHeadings = renderer.root.findAllByType('h3').map((node) => node.children.join(''));
    assert.deepEqual(groupHeadings.sort(), ['Electrical', 'Organization-wide'].sort());
    await act(async () => renderer.unmount());
  });

  it('paginates using the refreshed page from the palette response', async () => {
    let requestedPage = null;
    const api = {
      getContext: async () => ({ selectedProject: { id: 'proj-1', code: 'PRJ', name: 'Project One' } }),
      getPalette: async (projectId, options) => {
        requestedPage = options.page;
        return {
          activeSet: null,
          reason: 'none',
          items: [baseItem()],
          page: options.page,
          pageSize: 1,
          total: 2,
        };
      },
    };
    let renderer;
    await act(async () => { renderer = create(createElement(EffectivePalettePanel, { auth: baseAuth(), api })); });
    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Next effective palette page' }).props.onClick(); });
    assert.equal(requestedPage, 2);
    await act(async () => renderer.unmount());
  });
});

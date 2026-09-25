import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

async function loadCandidates() {
  const vite = await createServer({ configFile: false, root: process.cwd(), server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  const { buildPublishedPreviewCandidates } = await vite.ssrLoadModule('/frontend/src/App.jsx');
  return { vite, buildPublishedPreviewCandidates };
}

const dxfOnly = {
  slug: 'pipeacc-equipment-pump-016',
  previewUrl: null,
  payload: {
    review_case_id: 'd9b3a6ef-cadc-4eb8-a99d-6cc3824069e7',
    source_object_key: 'external-submissions/x/members/0016-pump/PipeAcc_Equipment_Pump.dxf',
  },
};

test('a symbol the API says has no preview gets no guessed preview requests', async () => {
  const { vite, buildPublishedPreviewCandidates } = await loadCandidates();
  try {
    assert.deepEqual(buildPublishedPreviewCandidates(dxfOnly), []);
    assert.deepEqual(buildPublishedPreviewCandidates(dxfOnly, 'PNG'), []);
  } finally {
    await vite.close();
  }
});

test('an API preview URL is tried first', async () => {
  const { vite, buildPublishedPreviewCandidates } = await loadCandidates();
  try {
    const candidates = buildPublishedPreviewCandidates({
      slug: 'dexpi-ttc-03751721',
      previewUrl: '/api/v1/published/symbols/S-000096/preview',
    });
    assert.ok(candidates.length >= 1);
    assert.ok(candidates[0].endsWith('/api/v1/published/symbols/S-000096/preview'));
  } finally {
    await vite.close();
  }
});

test('without an API answer, a raw DXF or ZIP source is never requested as a preview', async () => {
  const { vite, buildPublishedPreviewCandidates } = await loadCandidates();
  try {
    const { previewUrl: _omitted, ...unknown } = dxfOnly;
    const dxf = buildPublishedPreviewCandidates(unknown);
    assert.ok(!dxf.some((url) => url.includes('/children/preview')), dxf.join(' '));
    // The published route is still guessed when nothing says there is no preview.
    assert.ok(dxf.some((url) => url.includes('/api/v1/published/symbols/pipeacc-equipment-pump-016/preview')));

    const png = buildPublishedPreviewCandidates({
      ...unknown,
      payload: { ...unknown.payload, source_object_key: 'external-submissions/x/sheet-01.png' },
    });
    assert.ok(png.some((url) => url.includes('/children/preview')), png.join(' '));
  } finally {
    await vite.close();
  }
});

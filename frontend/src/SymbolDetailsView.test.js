import { after, before, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';
import { normalizeSymbolDetails } from './symbolDetailsModel.js';
import { discDetails, discValve, legacyPng, pilotSymbol } from './symbolDetailsFixtures.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// The view focuses tabs and scrolls to the viewer through `document`; there is
// none under Node, so a stand-in that finds nothing.
globalThis.document = { getElementById: () => null };

let vite;
let View;
let Viewer;

before(async () => {
  vite = await createServer({ configFile: false, root: process.cwd(), server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  View = (await vite.ssrLoadModule('/frontend/src/SymbolDetailsView.jsx')).default;
  Viewer = (await vite.ssrLoadModule('/frontend/src/SymbolViewer.jsx')).default;
});

after(async () => {
  await vite.close();
});

const noop = () => {};

function props(symbol, rawDetails = null, overrides = {}) {
  return {
    symbol,
    symbolState: 'ready',
    details: normalizeSymbolDetails(rawDetails),
    detailsStatus: rawDetails ? 'ready' : 'unavailable',
    tab: 'classification',
    onTabChange: noop,
    navigation: { enabled: true, hasPrevious: true, hasNext: true, position: 2, total: 9 },
    onBack: noop,
    onPrevious: noop,
    onNext: noop,
    actions: {
      preparingDownload: false,
      onDownload: noop,
      onAddToClipboard: noop,
      favourite: { pressed: false, pending: false, disabled: false, onToggle: noop },
      onComment: noop,
      onSendForReview: noop
    },
    statusMessages: [],
    commentsPanel: createElement('p', null, 'Comments thread'),
    resolveUrl: (url) => url,
    origin: 'https://symgov.example',
    onShowAllInClass: noop,
    ...overrides
  };
}

function markup(viewProps) {
  return renderToStaticMarkup(createElement(MemoryRouter, null, createElement(View, viewProps)));
}

function mount(viewProps) {
  let renderer;
  act(() => {
    renderer = TestRenderer.create(createElement(MemoryRouter, null, createElement(View, viewProps)));
  });
  return renderer;
}

const textOf = (node) => (typeof node === 'string' ? node : (node.children || []).map(textOf).join(''));

describe('the header card', () => {
  it('names the symbol, with its badges, actions and identifiers', () => {
    const html = markup(props(discValve, discDetails));
    assert.match(html, /Approved symbol/);
    assert.match(html, /S-273 · Modular Valve Double Isolation and Bleed \(DISC ND0004\)/);
    ['Public', 'Published', 'Revision r1', 'Valves', 'Piping / P&amp;ID', 'DISC DEXPI symbol library'].forEach((badge) => assert.match(html, new RegExp(badge)));
    ['Download', 'Add to clipboard', 'Comment', 'Send for review'].forEach((label) => assert.match(html, new RegExp(`>${label}<`)));
    assert.match(html, /aria-label="Download format"/);
    assert.match(html, /SVG \+ state variants \(zip\)/);
    assert.match(html, /aria-pressed="false"/);
    assert.match(html, /aria-label="Copy Symgov details URL"/);
    assert.match(html, /https:\/\/symgov\.example\/#\/s\/S-273/);
    assert.match(html, /aria-label="Copy Concept"/);
    assert.match(html, /http:\/\/data\.posccaesar\.org\/rdl\/RDS552689/);
  });

  it('offers no zip option and hides identifiers a symbol lacks', () => {
    const html = markup(props(legacyPng));
    assert.doesNotMatch(html, /state variants \(zip\)/);
    assert.doesNotMatch(html, /aria-label="Copy Concept"/);
    assert.match(html, /aria-label="Copy Symgov details URL"/);
  });

  it('has the breadcrumb Catalog › pack › catalog ID', () => {
    const html = markup(props(discValve, discDetails));
    assert.match(html, /<nav class="symbol-breadcrumb" aria-label="Breadcrumb">/);
    assert.match(html, /Catalog<\/button><\/li><li><span>DISC DEXPI symbol library \(DISC Profile 0\.6\.3\)<\/span><\/li><li><span aria-current="page">S-273<\/span>/);
  });
});

describe('previous and next', () => {
  it('are enabled for a view opened from the catalog and disabled for a link', () => {
    const open = markup(props(discValve, discDetails));
    assert.doesNotMatch(open, /<button[^>]*disabled=""[^>]*>‹ Previous result/);
    assert.match(open, /2 of 9/);
    const link = markup(props(discValve, discDetails, { navigation: { enabled: false, hasPrevious: false, hasNext: false, position: 0, total: 0 } }));
    assert.match(link, /disabled=""[^>]*>‹ Previous result/);
    assert.match(link, /disabled=""[^>]*>Next result ›/);
    assert.doesNotMatch(link, /of 9/);
  });

  it('call their handlers, and Back calls onBack', () => {
    const calls = [];
    const renderer = mount(props(discValve, discDetails, {
      onPrevious: () => calls.push('previous'),
      onNext: () => calls.push('next'),
      onBack: () => calls.push('back')
    }));
    const buttons = renderer.root.findAllByType('button');
    const named = (label) => buttons.find((button) => textOf(button).includes(label));
    act(() => named('‹ Previous result').props.onClick());
    act(() => named('Next result ›').props.onClick());
    act(() => named('Back to catalog').props.onClick());
    assert.deepEqual(calls, ['previous', 'next', 'back']);
  });
});

describe('the viewer', () => {
  it('shows S-273 with overlays on, the toggles, and a tile per state', () => {
    const html = markup(props(discValve, discDetails));
    assert.match(html, /FORMATS AND STATES|Formats and states · click to view/);
    assert.match(html, /aria-label="Overlays"/);
    ['Connection points', 'Insertion point', 'Label slots', 'Dimensions \\(mm\\)'].forEach((label) => assert.match(html, new RegExp(`>${label}<`)));
    assert.equal((html.match(/data-point-index="/g) || []).length, 3);
    assert.match(html, /ValvePosition = &#x27;NC&#x27;/);
    assert.match(html, /ValvePosition = &#x27;1NCAngle&#x27;/);
    // The frame (18.35 x 11.35 mm at -9.175, -9.1125) plus a 28% margin all round.
    assert.match(html, /viewBox="-14\.313 -14\.2505 28\.626 21\.626"/);
    // Defaults: points and insertion on, labels and dimensions off.
    assert.match(html, /overlay-insertion/);
    assert.doesNotMatch(html, /overlay-labels/);
    assert.doesNotMatch(html, /overlay-dimensions/);
  });

  it('hides the overlay toggles for a symbol with no geometry', () => {
    [pilotSymbol, legacyPng].forEach((symbol) => {
      const html = markup(props(symbol));
      assert.doesNotMatch(html, /aria-label="Overlays"/);
      assert.doesNotMatch(html, /overlay-point/);
      assert.match(html, /symbol-viewer-image/);
    });
  });

  it('shows a DXF as a greyed, download-only tile', () => {
    const html = markup(props(pilotSymbol));
    assert.match(html, /symbol-tile unavailable/);
    assert.match(html, /Not viewable · download only/);
  });

  it('marks the selected tile with aria-pressed and switches the main view on click', () => {
    const renderer = mount(props(discValve, discDetails));
    const tiles = () => renderer.root.findAll((node) => node.type === 'button' && String(node.props.className || '').startsWith('symbol-tile'));
    assert.deepEqual(tiles().map((tile) => tile.props['aria-pressed']), [true, false, false]);
    const mainSource = () => renderer.root.findByType('image').props.href;
    assert.equal(mainSource(), '/api/v1/published/symbols/S-273/preview?format=SVG');
    act(() => tiles()[2].props.onClick());
    assert.deepEqual(tiles().map((tile) => tile.props['aria-pressed']), [false, false, true]);
    assert.equal(mainSource(), '/api/v1/published/symbols/S-273/state-variants/2');
    assert.match(textOf(renderer.toJSON()), /ValvePosition = '1NCAngle'/);
  });

  it('toggles overlays on and off', () => {
    const renderer = mount(props(discValve, discDetails));
    const toggle = (label) => renderer.root.findAll((node) => node.type === 'button' && textOf(node) === label)[0];
    assert.equal(toggle('Label slots').props['aria-pressed'], false);
    assert.equal(renderer.root.findAll((node) => node.props.className === 'overlay-labels').length, 0);
    act(() => toggle('Label slots').props.onClick());
    assert.equal(toggle('Label slots').props['aria-pressed'], true);
    assert.equal(renderer.root.findAll((node) => node.props.className === 'overlay-labels').length, 1);
    act(() => toggle('Connection points').props.onClick());
    assert.equal(renderer.root.findAll((node) => String(node.props.className || '').startsWith('overlay-point ')).length, 0);
  });

  it('zooms in and out and refits', () => {
    const renderer = mount(props(discValve, discDetails));
    const button = (label) => renderer.root.findAll((node) => node.type === 'button' && node.props['aria-label'] === label)[0];
    const svgStyle = () => renderer.root.findByType('svg').props.style.width;
    assert.equal(svgStyle(), '100%');
    act(() => button('Zoom in').props.onClick());
    assert.equal(svgStyle(), '125%');
    act(() => button('Zoom out').props.onClick());
    act(() => button('Zoom out').props.onClick());
    assert.equal(svgStyle(), '80%');
    act(() => button('Fit to view').props.onClick());
    assert.equal(svgStyle(), '100%');
  });

  it('draws the S-273 connection points where the register puts them', () => {
    const renderer = mount(props(discValve, discDetails));
    const points = renderer.root.findAll((node) => node.props['data-point-index'] !== undefined);
    const centres = points.map((point) => {
      const circle = point.findAllByType('circle').find((node) => node.props.r !== undefined && !String(point.props.className).includes('halo'));
      return [point.props['data-point-index'], circle.props.cx, circle.props.cy];
    });
    assert.deepEqual(centres, [[1, 9, 0.0625], [2, -9, 0.0625], [3, 0, -8.9375]]);
  });

  it('highlights a point when told to, even with the points overlay off', () => {
    let renderer;
    const render = (highlightIndex) => {
      const representations = { items: [{ key: 'asset-SVG', kind: 'primary', format: 'SVG', label: 'Primary drawing', condition: '', viewable: true, url: '/x.svg', sharesPrimaryFrame: true }], defaultKey: 'asset-SVG' };
      return createElement(Viewer, { symbol: discValve, representations, selectedKey: 'asset-SVG', onSelectKey: noop, highlightIndex });
    };
    act(() => { renderer = TestRenderer.create(render(0)); });
    assert.equal(renderer.root.findAll((node) => String(node.props.className || '').includes('is-highlighted')).length, 0);
    act(() => renderer.update(render(2)));
    const highlighted = renderer.root.findAll((node) => String(node.props.className || '').includes('is-highlighted'));
    assert.equal(highlighted.length, 1);
    assert.equal(highlighted[0].props['data-point-index'], 2);
  });
});

describe('at a glance and attribution', () => {
  it('lists the S-273 rows and the placeholder badge from the rights record', () => {
    const html = markup(props(discValve, discDetails));
    ['DEXPI element', 'DEXPI class', 'Custom type', 'Concept', 'Size \\(mm\\)', 'Connections', 'Transforms', 'States', 'Main external reference', 'Last update'].forEach((label) => assert.match(html, new RegExp(`<dt>${label}</dt>`)));
    assert.match(html, /<dd>3 piping<\/dd>/);
    assert.match(html, /<dd>TR1970 STPV035<\/dd>/);
    assert.match(html, /Source and attribution/);
    assert.match(html, /Placeholder wording · final text pending/);
  });

  it('has no placeholder badge when the record is final', () => {
    const final = { ...discDetails, rights: { ...discDetails.rights, attributionIsPlaceholder: false } };
    assert.doesNotMatch(markup(props(discValve, final)), /Placeholder wording/);
  });

  it('shows no blank labels and no stray zeros for a legacy symbol', () => {
    const html = markup(props(legacyPng));
    assert.doesNotMatch(html, /<dt><\/dt>|<dd><\/dd>/);
    assert.doesNotMatch(html, /<dd>0<\/dd>/);
    assert.doesNotMatch(html, /Source and attribution/);
    assert.match(html, /No register details are recorded/);
  });
});

describe('the tabs', () => {
  it('are an ARIA tablist in the agreed order with Classification & mappings selected by default', () => {
    const html = markup(props(discValve, discDetails));
    assert.match(html, /role="tablist"/);
    const labels = [...html.matchAll(/role="tab"[^>]*>([^<]+)</g)].map((match) => match[1].replace(/&amp;/g, '&'));
    assert.deepEqual(labels, ['Classification & mappings', 'Details', 'Connection points (3)', 'Labels & states', 'Source & rights', 'History', 'Comments (2)']);
    assert.match(html, /id="symbol-details-tab-classification"[^>]*aria-selected="true"[^>]*tabindex="0"|aria-selected="true"[^>]*id="symbol-details-tab-classification"/);
    assert.match(html, /role="tabpanel"/);
  });

  it('moves with the arrow keys, Home and End', () => {
    const picked = [];
    const renderer = mount(props(discValve, discDetails, { onTabChange: (tab) => picked.push(tab) }));
    const list = renderer.root.findByProps({ role: 'tablist' });
    const press = (key) => act(() => list.props.onKeyDown({ key, preventDefault: noop }));
    press('ArrowRight');
    press('End');
    press('Home');
    press('ArrowLeft');
    assert.deepEqual(picked, ['details', 'comments', 'classification', 'comments']);
  });

  it('shows the S-273 classification path and the POSC Caesar mapping', () => {
    const html = markup(props(discValve, discDetails));
    assert.match(html, /Piping › CustomOperatedValve › Double Block And Bleed Valve/);
    assert.match(html, /POSC Caesar RDL/);
    assert.match(html, />RDS552689</);
    assert.match(html, /href="http:\/\/data\.posccaesar\.org\/rdl\/RDS552689"/);
    assert.doesNotMatch(html, /ISO ICS/);
  });

  it('shows other drawings of the concept and eight symbols of the class with a link to the catalog', () => {
    const html = markup(props(discValve, discDetails));
    assert.match(html, /Other drawings of this concept/);
    assert.match(html, /DEXPI Trainin|DEXPI TrainingTestCases/);
    assert.match(html, /Other symbols in DEXPI class CustomOperatedValve/);
    // One card for the concept, eight for the class (not the ten the server sent).
    assert.equal((html.match(/class="symbol-related-card"/g) || []).length, 9);
    assert.match(html, /See all 12 in the catalog/);
  });

  it('asks the catalog for the class when "See all" is pressed', () => {
    const classes = [];
    const renderer = mount(props(discValve, discDetails, { onShowAllInClass: (name) => classes.push(name) }));
    const seeAll = renderer.root.findAll((node) => node.type === 'button' && textOf(node).startsWith('See all'))[0];
    act(() => seeAll.props.onClick());
    assert.deepEqual(classes, ['CustomOperatedValve']);
  });

  it('says so, briefly, when a symbol has no classification data', () => {
    assert.match(markup(props(legacyPng)), /not shown for this symbol|No governed classifications/);
    assert.match(markup(props(legacyPng, null, { detailsStatus: 'error' })), /could not be loaded/);
    assert.match(markup(props(legacyPng, null, { detailsStatus: 'loading' })), /Loading classifications/);
  });

  it('has only the tabs a legacy symbol has data for', () => {
    const html = markup(props(legacyPng));
    const labels = [...html.matchAll(/role="tab"[^>]*>([^<]+)</g)].map((match) => match[1].replace(/&amp;/g, '&'));
    assert.deepEqual(labels, ['Classification & mappings', 'Details', 'Comments']);
  });

  it('falls back to the default tab when the one in the URL does not exist', () => {
    const html = markup(props(legacyPng, null, { tab: 'connections' }));
    assert.match(html, /aria-labelledby="symbol-details-tab-classification"/);
  });

  it('renders the Details tab sections', () => {
    const html = markup(props(discValve, discDetails, { tab: 'details' }));
    ['Identity', 'Geometry', 'Source register'].forEach((title) => assert.match(html, new RegExp(`>${title}<`)));
    assert.match(html, /<dt>Drawing extent<\/dt><dd>18\.35 × 11\.35 mm<\/dd>/);
    assert.match(html, /<dt>Source ID<\/dt><dd>ND0004<\/dd>/);
  });

  it('renders the connection table and highlights rows through the handler', () => {
    const html = markup(props(discValve, discDetails, { tab: 'connections' }));
    assert.match(html, /<th scope="col">X mm<\/th>/);
    assert.match(html, /<th scope="row">3<\/th><td>piping<\/td><td>0<\/td><td>-8\.94<\/td><td>270°<\/td>/);
    const seen = [];
    const renderer = mount(props(discValve, discDetails, { tab: 'connections' }));
    const row = renderer.root.findAllByType('tr')[2];
    // The viewer is a sibling in the same view, so the handler is the view's own state;
    // here it is enough that the row reacts to hover and focus without throwing.
    act(() => row.props.onMouseEnter());
    const highlighted = renderer.root.findAll((node) => String(node.props.className || '').includes('is-highlighted'));
    seen.push(highlighted.length);
    act(() => row.props.onMouseLeave());
    seen.push(renderer.root.findAll((node) => String(node.props.className || '').includes('is-highlighted')).length);
    assert.deepEqual(seen, [1, 0]);
  });

  it('renders label slots verbatim and the states with a View button that switches the viewer', () => {
    const renderer = mount(props(discValve, discDetails, { tab: 'labels' }));
    const text = textOf(renderer.toJSON());
    assert.match(text, /<ObjectDisplayName>\n<NominalDiameter><VDS>/);
    assert.match(text, /States \(2\)/);
    const view = renderer.root.findAll((node) => node.type === 'button' && textOf(node).startsWith('View'))[1];
    act(() => view.props.onClick());
    assert.equal(renderer.root.findByType('image').props.href, '/api/v1/published/symbols/S-273/state-variants/2');
  });

  it('renders rights and provenance on Source & rights', () => {
    const html = markup(props(discValve, discDetails, { tab: 'source' }));
    ['Rights', 'Provenance'].forEach((title) => assert.match(html, new RegExp(`>${title}<`)));
    assert.match(html, /<dt>Licensor<\/dt><dd>Tonia Pedersen<\/dd>/);
    assert.match(html, /ToniaPedersen\/DISCDEXPI @ 0123456789ab/);
    assert.match(html, /Symbols\/ND0004\.svg/);
    assert.match(html, /4dcaf5439e3c7cbad485c8f993beec74dc9f3424c896c39c2a5bb9eee9ead8eb/);
    assert.match(html, /57f3a42e135a83d4bf797e3cdaf60e8de5ef1187467e9c6fc977de752087aec9/);
    assert.match(html, /Placeholder wording/);
  });

  it('lists history newest first and only revision and approval events', () => {
    const html = markup(props(discValve, discDetails, { tab: 'history' }));
    const list = html.slice(html.indexOf('symbol-history-list'));
    const order = [...list.matchAll(/<span>([^<]+)<\/span><\/li>/g)].map((match) => match[1]);
    assert.deepEqual(order, ['Published', 'Rights approved', 'Revision r1 imported']);
  });

  it('shows the comments thread on the Comments tab', () => {
    assert.match(markup(props(discValve, discDetails, { tab: 'comments' })), /Comments thread/);
  });
});

describe('loading and failure', () => {
  it('shows a loading note, and a not-found notice with a way back', () => {
    assert.match(markup(props(null, null, { symbolState: 'loading' })), /Loading symbol…/);
    const html = markup(props(null, null, { symbolState: 'error' }));
    assert.match(html, /Symbol not found/);
    assert.match(html, /Back to catalog/);
  });

  it('surfaces status messages from the actions', () => {
    const html = markup(props(discValve, discDetails, { statusMessages: [{ mode: 'error', message: 'Download failed.' }] }));
    assert.match(html, /role="alert">Download failed\./);
  });
});

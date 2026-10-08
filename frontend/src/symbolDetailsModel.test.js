import test from 'node:test';
import assert from 'node:assert/strict';
import {
  attributionBlock,
  availableTabs,
  connectionRows,
  detailsSections,
  glanceRows,
  identifierList,
  labelSlotRows,
  mappingDisplay,
  normalizeSymbolDetails,
  provenanceRows,
  rightsRows,
  sourceRepositoryText,
  transformSummary,
  yesNo
} from './symbolDetailsModel.js';
import { discDetails, discValve, legacyPng, pilotSymbol } from './symbolDetailsFixtures.js';

const labels = (list) => list.map((item) => item.label);
const byLabel = (list, label) => list.find((item) => item.label === label)?.value;

test('S-273 at a glance lists what the register states, in order', () => {
  const rows = glanceRows(discValve);
  assert.deepEqual(labels(rows), [
    'DEXPI element', 'DEXPI class', 'Custom type', 'Concept', 'Size (mm)',
    'Connections', 'Transforms', 'States', 'Main external reference', 'Last update'
  ]);
  assert.equal(byLabel(rows, 'DEXPI class'), 'CustomOperatedValve');
  assert.equal(byLabel(rows, 'Size (mm)'), '18 × 11 mm');
  assert.equal(byLabel(rows, 'Connections'), '3 piping');
  assert.equal(byLabel(rows, 'Transforms'), 'rotate / mirror');
  assert.equal(byLabel(rows, 'States'), '2');
  assert.equal(byLabel(rows, 'Main external reference'), 'TR1970 STPV035');
  assert.equal(byLabel(rows, 'Last update'), '05 Oct 2026');
});

test('a symbol without register data shows only the rows it has, never a blank or a zero', () => {
  const pilot = glanceRows(pilotSymbol);
  assert.deepEqual(labels(pilot), ['DEXPI element', 'DEXPI class', 'Concept']);
  const legacy = glanceRows(legacyPng);
  assert.deepEqual(legacy, []);
  [...pilot, ...glanceRows(discValve)].forEach((row) => {
    assert.notEqual(row.value, '');
    assert.notEqual(row.value, '0');
  });
});

test('transforms say what is allowed, and nothing when the register is silent', () => {
  const withTransform = (transform) => ({ payload: { disc: { transform } } });
  assert.equal(transformSummary(withTransform({ rotation: 'Yes', mirroring: 'No', resize_x: 'YES', resize_y: 'No' })), 'rotate / resize');
  assert.equal(transformSummary(withTransform({ rotation: 'No', mirroring: 'No', resize_x: 'No', resize_y: 'No' })), 'fixed');
  assert.equal(transformSummary(withTransform({ rotation: null, mirroring: null, resize_x: null, resize_y: null })), '');
  assert.equal(transformSummary(pilotSymbol), '');
  assert.equal(yesNo('YES'), true);
  assert.equal(yesNo('no'), false);
  assert.equal(yesNo(null), null);
  assert.equal(yesNo('maybe'), null);
});

test('identifiers are the details URL and the concept, each only when it exists', () => {
  const list = identifierList(discValve, 'https://symgov.example');
  assert.deepEqual(list.map((item) => item.key), ['details', 'concept']);
  assert.equal(list[0].value, 'https://symgov.example/#/s/S-273');
  assert.equal(list[1].value, 'DoubleBlockAndBleedValve');
  assert.equal(list[1].secondary, 'http://data.posccaesar.org/rdl/RDS552689');
  assert.equal(list[1].copy, 'DoubleBlockAndBleedValve http://data.posccaesar.org/rdl/RDS552689');
  // The custom type's URI is the concept's own here, so it is not listed twice.
  assert.ok(!list.some((item) => item.key === 'customType'));
});

test('a custom type with its own URI is listed, a concept without a URI is key-only, and a symbol with none has none', () => {
  const symbol = {
    catalogSymbolId: 'S-9',
    payload: { dexpi: { custom_type: { label: 'X', rdl_uri: 'http://example.org/a' }, semantic_concept: { concept_key: 'Y', rdl_uri: 'http://example.org/b' } } }
  };
  assert.deepEqual(identifierList(symbol, 'https://symgov.example').map((item) => item.key), ['details', 'concept', 'customType']);
  const pilot = identifierList(pilotSymbol, 'https://symgov.example');
  assert.deepEqual(pilot.map((item) => item.key), ['details', 'concept']);
  assert.equal(pilot[1].secondary, '');
  assert.deepEqual(identifierList({ id: 'x', payload: {} }, 'https://symgov.example'), []);
});

test('the Details tab has identity, geometry and register sections for S-273', () => {
  const sections = detailsSections(discValve);
  assert.deepEqual(sections.map((section) => section.title), ['Identity', 'Geometry', 'Source register']);
  const identity = sections[0].rows;
  assert.equal(byLabel(identity, 'Catalog ID'), 'S-273');
  assert.equal(byLabel(identity, 'Source ID'), 'ND0004');
  assert.equal(byLabel(identity, 'TR1970'), 'STPV035');
  assert.equal(byLabel(identity, 'Aliases'), 'ND0004, STPV035, CustomOperatedValve, DoubleBlockAndBleedValve');
  assert.ok(!labels(identity).includes('Legacy ID'));
  assert.ok(!labels(identity).includes('NORSOK Z-004'));
  const geometry = sections[1].rows;
  assert.equal(byLabel(geometry, 'Register size'), '18 × 11 mm');
  assert.equal(byLabel(geometry, 'Drawing extent'), '18.35 × 11.35 mm');
  assert.equal(byLabel(geometry, 'Insertion point'), '0, 0 mm');
  assert.equal(byLabel(geometry, 'Rotation'), 'Allowed');
  assert.equal(byLabel(geometry, 'Resize X'), 'Not allowed');
  const register = sections[2].rows;
  assert.equal(byLabel(register, 'Register update'), '15 May 2026');
  assert.equal(byLabel(register, 'Mapping status'), 'OK');
  assert.equal(byLabel(register, 'Profile'), 'DISC Profile 0.6.3 (DEXPI 1.3)');
});

test('a legacy symbol has only the identity it has', () => {
  const sections = detailsSections(legacyPng);
  assert.deepEqual(sections.map((section) => section.title), ['Identity']);
  assert.deepEqual(labels(sections[0].rows), ['Catalog ID']);
  assert.deepEqual(detailsSections({ payload: {} }), []);
});

test('the connection table lists number, kind, x, y and direction', () => {
  assert.deepEqual(connectionRows(discValve), [
    { index: 1, kind: 'piping', x: '9', y: '0.06', direction: '0°' },
    { index: 2, kind: 'piping', x: '-9', y: '0.06', direction: '180°' },
    { index: 3, kind: 'piping', x: '0', y: '-8.94', direction: '270°' }
  ]);
  assert.deepEqual(connectionRows(pilotSymbol), []);
});

test('label slots keep their template verbatim', () => {
  const [slot] = labelSlotRows(discValve);
  assert.equal(slot.index, 'A');
  assert.equal(slot.lines, 3);
  assert.equal(slot.box, '3, -3.94, 8, -2.94 mm');
  assert.equal(slot.template, '<ObjectDisplayName>\n<NominalDiameter><VDS>\n<TrimType> <LockMechanism> <ShutoffCapability> ');
  assert.deepEqual(labelSlotRows(legacyPng), []);
});

test('the details response is defaulted, and rejected and retired classifications drop out', () => {
  const details = normalizeSymbolDetails(discDetails);
  assert.deepEqual(details.classifications.map((item) => [item.scheme, item.path, item.method]), [
    ['DEXPI class', 'Piping › CustomOperatedValve › Double Block And Bleed Valve', 'Source mapping'],
    ['Engineering discipline', 'Piping / P&ID', 'Rule']
  ]);
  assert.deepEqual(details.externalMappings, [{
    system: 'POSC Caesar RDL',
    identifier: 'http://data.posccaesar.org/rdl/RDS552689',
    label: 'DOUBLE BLOCK AND BLEED VALVE',
    relation: 'Exact match'
  }]);
  assert.equal(details.sameClass.total, 12);
  assert.equal(details.sameClass.items.length, 8);
  assert.equal(details.sameConcept[0].reference, 'S-103');

  const empty = normalizeSymbolDetails(null);
  assert.deepEqual([empty.classifications, empty.externalMappings, empty.sameConcept, empty.history], [[], [], [], []]);
  assert.equal(empty.sameClass, null);
  assert.equal(empty.rights, null);
  assert.equal(normalizeSymbolDetails({ sameClass: { className: 'X', total: 0, items: [] } }).sameClass, null);
  const retired = normalizeSymbolDetails({ classifications: [{ schemeName: 'A', nodePath: ['B'], status: 'Retired' }] });
  assert.deepEqual(retired.classifications, []);
});

test('mappings link a URI identifier and show its last segment', () => {
  assert.deepEqual(mappingDisplay('http://data.posccaesar.org/rdl/RDS552689'), { text: 'RDS552689', href: 'http://data.posccaesar.org/rdl/RDS552689' });
  assert.deepEqual(mappingDisplay('ISO-1234'), { text: 'ISO-1234', href: '' });
  assert.deepEqual(mappingDisplay('javascript:alert(1)'), { text: 'javascript:alert(1)', href: '' });
});

test('the source repository reads repo @ short commit', () => {
  assert.equal(sourceRepositoryText(discDetails.provenance), 'ToniaPedersen/DISCDEXPI @ 0123456789ab');
  assert.equal(sourceRepositoryText(null), '');
  assert.equal(sourceRepositoryText({ releaseVersion: 'v1' }), 'v1');
});

test('Source & rights holds rights, provenance, hash and signature', () => {
  const details = normalizeSymbolDetails(discDetails);
  assert.deepEqual(labels(rightsRows(details)), ['Status', 'Disposition', 'Licensor', 'Creator', 'Attribution']);
  const provenance = provenanceRows(discValve, details);
  assert.deepEqual(labels(provenance), ['Pack', 'Source repository @ commit', 'Source file', 'File SHA-256', 'Geometry signature']);
  assert.equal(byLabel(provenance, 'File SHA-256'), '4dcaf5439e3c7cbad485c8f993beec74dc9f3424c896c39c2a5bb9eee9ead8eb');
  assert.deepEqual(rightsRows(normalizeSymbolDetails(null)), []);
});

test('the attribution block comes from the rights record, with its placeholder flag', () => {
  const details = normalizeSymbolDetails(discDetails);
  const block = attributionBlock(discValve, details);
  assert.equal(block.placeholder, true);
  assert.equal(block.licensor, 'Tonia Pedersen');
  assert.equal(attributionBlock(discValve, { rights: { ...discDetails.rights, attributionIsPlaceholder: false } }).placeholder, false);
});

test('without a rights record the stored wording stands in and claims nothing about being final', () => {
  const block = attributionBlock(discValve, normalizeSymbolDetails(null));
  assert.equal(block.attribution, 'Licensed from the DISCDEXPI GitHub repo by Tonia Pedersen.');
  assert.equal(block.placeholder, false);
  assert.equal(attributionBlock(legacyPng, normalizeSymbolDetails(null)), null);
});

test('tabs appear only for symbols that have something to show', () => {
  const details = normalizeSymbolDetails(discDetails);
  assert.deepEqual(availableTabs(discValve, details), ['classification', 'details', 'connections', 'labels', 'source', 'history', 'comments']);
  assert.deepEqual(availableTabs(legacyPng, normalizeSymbolDetails(null)), ['classification', 'details', 'comments']);
  assert.deepEqual(availableTabs({ id: 'x', payload: {} }, null), ['classification', 'comments']);
});

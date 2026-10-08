// Published symbol rows for the Details view tests. `discValve` is the real
// DISC ND0004 record (published as S-273) as the published API serves it;
// `pilotSymbol` is a DEXPI pilot symbol (no geometry, no states); `legacyPng`
// is an intake-published symbol with only a PNG.

export const discValve = {
  id: 'disc-dexpi-nd0004',
  symbolId: '11111111-1111-4111-8111-111111111111',
  catalogSymbolId: 'S-273',
  slug: 'disc-dexpi-nd0004',
  name: 'Modular Valve Double Isolation and Bleed (DISC ND0004)',
  summary: 'Modular Valve Double Isolation and Bleed, from the DISC DEXPI project symbol legend (DISC Profile 0.6.3).',
  category: 'Valves',
  discipline: 'Piping / P&ID',
  pack: 'DISC DEXPI symbol library (DISC Profile 0.6.3)',
  packCode: 'disc-dexpi',
  revision: 'r1',
  revisionId: '22222222-2222-4222-8222-222222222222',
  revisionCreatedAt: '2026-10-05T09:30:00+00:00',
  lastUpdatedAt: '2026-10-05T09:30:00+00:00',
  status: 'Published',
  source: 'public',
  commentCount: 2,
  isFavourite: false,
  previewUrl: '/api/v1/published/symbols/S-273/preview',
  previewAsset: { format: 'svg' },
  previewAssets: [{ format: 'svg' }],
  downloadAssets: [{ role: 'primary', format: 'svg', filename: 'ND0004.svg', sha256: '4dcaf5439e3c7cbad485c8f993beec74dc9f3424c896c39c2a5bb9eee9ead8eb' }],
  stateVariants: [
    { index: 1, condition: "ValvePosition = 'NC'", filename: 'ND0004_option1.svg', url: '/api/v1/published/symbols/S-273/state-variants/1' },
    { index: 2, condition: "ValvePosition = '1NCAngle'", filename: 'ND0004_option2.svg', url: '/api/v1/published/symbols/S-273/state-variants/2' }
  ],
  payload: {
    name: 'Modular Valve Double Isolation and Bleed (DISC ND0004)',
    aliases: ['ND0004', 'STPV035', 'CustomOperatedValve', 'DoubleBlockAndBleedValve'],
    assets: [{ role: 'primary', format: 'svg', sha256: '4dcaf5439e3c7cbad485c8f993beec74dc9f3424c896c39c2a5bb9eee9ead8eb' }],
    dexpi: {
      dexpi_elements: ['PipingComponent'],
      component_classes: ['CustomOperatedValve'],
      custom_type: { label: 'DOUBLE BLOCK AND BLEED VALVE', rdl_uri: 'http://data.posccaesar.org/rdl/RDS552689' },
      semantic_concept: { concept_key: 'DoubleBlockAndBleedValve', rdl_uri: 'http://data.posccaesar.org/rdl/RDS552689' },
      geometry_signature: '57f3a42e135a83d4bf797e3cdaf60e8de5ef1187467e9c6fc977de752087aec9',
      standard_assertion: {
        standard_code: 'DEXPI',
        version_label: 'DISC Profile 0.6.3 (DEXPI 1.3)',
        source_symbol_identifier: 'ND0004'
      },
      attribution: 'Licensed from the DISCDEXPI GitHub repo by Tonia Pedersen.'
    },
    disc: {
      disc_id: 'ND0004',
      legacy_id: null,
      grouping: 'Valve',
      register_last_update: '2026-05-15',
      register_status: { origo: 'OK', sizing: 'OK', label: 'OK', connections: 'OK', mapping: 'OK' },
      size_mm: { h: 11, w: 18 },
      transform: { rotation: 'Yes', mirroring: 'Yes', resize_x: 'No', resize_y: 'No' },
      references: { TR1970: 'STPV035', NORSOK_Z004: null, ISA_5_1: null, label_symbol_ref: null },
      type_codes: null,
      label_templates: { A: '<ObjectDisplayName>\n<NominalDiameter><VDS>\n<TrimType> <LockMechanism> <ShutoffCapability> ' }
    },
    geometry: {
      units: 'mm',
      origin: [0, 0],
      y_axis: 'down',
      bbox_mm: [-9.175, -9.1125, 9.175, 2.2375],
      connection_points: [
        { index: 1, x_mm: 9.0, y_mm: 0.0625, directions_deg: [0.0], kinds: ['piping'] },
        { index: 2, x_mm: -9.0, y_mm: 0.0625, directions_deg: [180.0], kinds: ['piping'] },
        { index: 3, x_mm: 0.0, y_mm: -8.9375, directions_deg: [270.0], kinds: ['piping'] }
      ],
      label_slots: [{
        label_index: 'A',
        lines: 3,
        box_mm: [3.0, -3.9375, 8.0, -2.9375],
        template: '<ObjectDisplayName>\n<NominalDiameter><VDS>\n<TrimType> <LockMechanism> <ShutoffCapability> '
      }]
    }
  }
};

export const discDetails = {
  catalogSymbolId: 'S-273',
  classifications: [
    { schemeCode: 'DEXPI-CLASS', schemeName: 'DEXPI class', nodeCode: 'CustomOperatedValve/DoubleBlockAndBleedValve', nodePath: ['Piping', 'CustomOperatedValve', 'Double Block And Bleed Valve'], nodeLabel: 'Double Block And Bleed Valve', method: 'source_mapping', role: 'primary', status: 'verified' },
    { schemeCode: 'ENGINEERING-DISCIPLINE', schemeName: 'Engineering discipline', nodeCode: 'PIPING', nodePath: ['Piping / P&ID'], nodeLabel: 'Piping / P&ID', method: 'rule', role: 'primary', status: 'verified' },
    { schemeCode: 'ISO-ICS-7', schemeName: 'ISO ICS', nodeCode: '23.060', nodePath: ['Valves'], nodeLabel: 'Valves', method: 'manual', role: 'secondary', status: 'rejected' }
  ],
  externalMappings: [
    { system: 'POSC Caesar RDL', identifier: 'http://data.posccaesar.org/rdl/RDS552689', label: 'DOUBLE BLOCK AND BLEED VALVE', relation: 'exact', status: 'verified' }
  ],
  sameConcept: [
    { catalogSymbolId: 'S-103', name: 'Double block and bleed valve', slug: 'dexpi-ttc-aaaa', packCode: 'dexpi-ttc', pack: 'DEXPI TrainingTestCases', previewUrl: '/api/v1/published/symbols/S-103/preview' }
  ],
  sameClass: {
    className: 'CustomOperatedValve',
    total: 12,
    items: Array.from({ length: 10 }, (_, index) => ({
      catalogSymbolId: `S-${300 + index}`,
      name: `Operated valve ${index}`,
      slug: `disc-${index}`,
      packCode: 'disc-dexpi',
      pack: 'DISC DEXPI',
      previewUrl: `/api/v1/published/symbols/S-${300 + index}/preview`
    }))
  },
  rights: {
    status: 'licensed',
    disposition: 'distribute',
    licensor: 'Tonia Pedersen',
    creator: 'DISC DEXPI project',
    attributionText: 'Licensed from the DISCDEXPI GitHub repo by Tonia Pedersen.',
    attributionIsPlaceholder: true,
    sourceUrl: 'https://github.com/ToniaPedersen/DISCDEXPI'
  },
  provenance: {
    packCode: 'disc-dexpi',
    pack: 'DISC DEXPI symbol library (DISC Profile 0.6.3)',
    sourceUri: 'https://github.com/ToniaPedersen/DISCDEXPI/tree/main',
    sourceCommit: '0123456789abcdef0123456789abcdef01234567',
    releaseVersion: 'DISC Profile 0.6.3',
    sourcePath: 'Symbols/ND0004.svg',
    providerEntryIdentifier: 'ND0004'
  },
  history: [
    { kind: 'published', at: '2026-10-05T10:00:00+00:00', label: 'Published' },
    { kind: 'rights_approved', at: '2026-10-05T09:45:00+00:00', label: 'Rights approved' },
    { kind: 'revision_imported', at: '2026-10-05T09:30:00+00:00', label: 'Revision r1 imported' }
  ]
};

export const pilotSymbol = {
  id: 'dexpi-ttc-0123',
  symbolId: '33333333-3333-4333-8333-333333333333',
  catalogSymbolId: 'S-103',
  name: 'Gate valve',
  summary: 'A gate valve from the DEXPI TrainingTestCases.',
  pack: 'DEXPI TrainingTestCases',
  revision: 'r1',
  status: 'Published',
  source: 'public',
  commentCount: 0,
  previewUrl: '/api/v1/published/symbols/S-103/preview',
  previewAsset: { format: 'svg' },
  previewAssets: [{ format: 'svg' }],
  downloadAssets: [{ role: 'primary', format: 'svg', filename: 'gate.svg' }, { role: 'primary', format: 'dxf', filename: 'gate.dxf' }],
  stateVariants: [],
  payload: {
    name: 'Gate valve',
    dexpi: {
      component_classes: ['GateValve'],
      dexpi_elements: ['PipingComponent'],
      semantic_concept: { concept_key: 'GateValve', concept_code: 'GateValve' }
    }
  }
};

export const legacyPng = {
  id: 'legacy-pump',
  symbolId: '44444444-4444-4444-8444-444444444444',
  catalogSymbolId: 'S-1',
  name: 'Centrifugal pump',
  summary: 'A centrifugal pump.',
  pack: 'Library',
  revision: 'r2',
  status: 'Published',
  source: 'public',
  commentCount: 0,
  previewUrl: '/api/v1/published/symbols/S-1/preview',
  previewAsset: { format: 'png' },
  previewAssets: [{ format: 'png' }],
  downloadAssets: [{ role: 'primary', format: 'png', filename: 'pump.png' }],
  stateVariants: [],
  payload: { name: 'Centrifugal pump' }
};

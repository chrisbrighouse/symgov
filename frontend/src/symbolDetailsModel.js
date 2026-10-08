import { connectionPointSummary, stateVariantList } from './symbolGeometry.js';
import { drawingFrame, formatMm, insertionPoint } from './symbolOverlay.js';
import { symgovDetailsUrl } from './symbolDetailsRoute.js';

// What the Details view says about a symbol, shaped from two sources: the
// published symbol row (payload, assets, states) and the read-only details
// response (classifications, mappings, related symbols, rights, provenance,
// history). Every function returns nothing for a value the symbol does not
// have, so the view never renders a blank label or a "0".

function text(value) {
  if (value === null || value === undefined) return '';
  if (Array.isArray(value)) return value.map(text).filter(Boolean).join(', ');
  if (typeof value === 'object') {
    return Object.entries(value)
      .map(([key, item]) => (text(item) ? `${key}: ${text(item)}` : ''))
      .filter(Boolean)
      .join(', ');
  }
  return String(value).trim();
}

function row(label, value) {
  const shown = typeof value === 'string' ? value.trim() : value;
  return shown === '' || shown === null || shown === undefined || shown === false ? null : { label, value: shown };
}

function rows(list) {
  return list.filter(Boolean);
}

const dexpiOf = (symbol) => symbol?.payload?.dexpi || {};
const discOf = (symbol) => symbol?.payload?.disc || {};
const geometryOf = (symbol) => symbol?.payload?.geometry || {};

// The register states transforms as Yes / No (and, in places, YES / NO) or
// leaves them blank. Anything else is unknown.
export function yesNo(value) {
  if (typeof value === 'boolean') return value;
  const normalized = String(value ?? '').trim().toLowerCase();
  if (normalized === 'yes') return true;
  if (normalized === 'no') return false;
  return null;
}

const DATE_FORMATTER = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/London',
  day: '2-digit',
  month: 'short',
  year: 'numeric'
});

const DATE_TIME_FORMATTER = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/London',
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit'
});

function formatWith(formatter, value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return formatter.format(date).replace(',', '');
}

export const formatDate = (value) => formatWith(DATE_FORMATTER, value);
export const formatDateTime = (value) => formatWith(DATE_TIME_FORMATTER, value);

function sizeText(size) {
  if (!size || typeof size !== 'object') return '';
  const width = Number(size.w);
  const height = Number(size.h);
  if (!Number.isFinite(width) || !Number.isFinite(height)) return '';
  return `${formatMm(width)} × ${formatMm(height)} mm`;
}

// Which of rotate / mirror / resize the register allows. Nothing is said when
// the register states none of them.
export function transformSummary(symbol) {
  const transform = discOf(symbol).transform;
  if (!transform || typeof transform !== 'object') return '';
  const flags = {
    rotate: yesNo(transform.rotation),
    mirror: yesNo(transform.mirroring),
    resize: [yesNo(transform.resize_x), yesNo(transform.resize_y)]
  };
  const resizeKnown = flags.resize.some((flag) => flag !== null);
  if (flags.rotate === null && flags.mirror === null && !resizeKnown) return '';
  const allowed = [];
  if (flags.rotate) allowed.push('rotate');
  if (flags.mirror) allowed.push('mirror');
  if (flags.resize.some((flag) => flag === true)) allowed.push('resize');
  return allowed.length ? allowed.join(' / ') : 'fixed';
}

// "3 piping" or "3 piping, 1 signal": the connections by kind, without the
// total in front that the Details tab shows.
export function connectionsSummary(symbol) {
  const summary = connectionPointSummary(symbol);
  if (!summary.count) return '';
  return summary.kinds.map(({ kind, count }) => `${count} ${kind}`).join(', ');
}

// The main external reference: the TR1970 code where the register has one,
// else NORSOK Z-004, else ISA-5.1.
export function mainExternalReference(symbol) {
  const references = discOf(symbol).references || {};
  const options = [
    ['TR1970', references.TR1970],
    ['NORSOK Z-004', references.NORSOK_Z004],
    ['ISA-5.1', references.ISA_5_1]
  ];
  const found = options.find(([, value]) => text(value));
  return found ? `${found[0]} ${text(found[1])}` : '';
}

export function conceptOf(symbol) {
  const concept = dexpiOf(symbol).semantic_concept;
  if (!concept || typeof concept !== 'object') return null;
  const key = text(concept.concept_key) || text(concept.concept_code);
  if (!key) return null;
  return { key, rdlUri: isHttpUrl(concept.rdl_uri) ? String(concept.rdl_uri) : '' };
}

export function customTypeOf(symbol) {
  const custom = dexpiOf(symbol).custom_type;
  if (!custom || typeof custom !== 'object') return null;
  const label = text(custom.label);
  if (!label) return null;
  return { label, rdlUri: isHttpUrl(custom.rdl_uri) ? String(custom.rdl_uri) : '' };
}

export function isHttpUrl(value) {
  return typeof value === 'string' && /^https?:\/\/[^\s]+$/i.test(value.trim());
}

// The "At a glance" list. Rows the symbol does not have are left out.
export function glanceRows(symbol) {
  const dexpi = dexpiOf(symbol);
  const concept = conceptOf(symbol);
  const customType = customTypeOf(symbol);
  const states = stateVariantList(symbol).length;
  return rows([
    row('DEXPI element', text(dexpi.dexpi_elements)),
    row('DEXPI class', text(dexpi.component_classes)),
    row('Custom type', customType?.label),
    row('Concept', concept?.key),
    row('Size (mm)', sizeText(discOf(symbol).size_mm)),
    row('Connections', connectionsSummary(symbol)),
    row('Transforms', transformSummary(symbol)),
    row('States', states > 0 ? String(states) : ''),
    row('Main external reference', mainExternalReference(symbol)),
    row('Last update', formatDate(symbol?.lastUpdatedAt || symbol?.revisionCreatedAt))
  ]);
}

// Source and attribution for the side card. The rights record is the source
// of truth; the wording stored on the symbol stands in only when the record
// could not be loaded, and then makes no claim about it being a placeholder.
export function attributionBlock(symbol, details) {
  const rights = details?.rights || null;
  const stored = text(dexpiOf(symbol).attribution);
  const attribution = text(rights?.attributionText) || (rights ? '' : stored);
  const licensor = text(rights?.licensor);
  const creator = text(rights?.creator);
  if (!attribution && !licensor && !creator) return null;
  return {
    attribution,
    licensor,
    creator,
    placeholder: Boolean(rights?.attributionIsPlaceholder)
  };
}

export const IDENTIFIER_LABELS = {
  details: 'Symgov details URL',
  concept: 'Concept',
  customType: 'Custom type URI'
};

// The identifiers a user may want to copy. Each is hidden when it does not
// exist. A custom type whose URI is the concept's own is not listed twice.
export function identifierList(symbol, origin) {
  const list = [];
  const detailsUrl = symgovDetailsUrl(origin, symbol?.catalogSymbolId);
  if (detailsUrl) list.push({ key: 'details', label: IDENTIFIER_LABELS.details, value: detailsUrl, copy: detailsUrl });
  const concept = conceptOf(symbol);
  if (concept) {
    list.push({
      key: 'concept',
      label: IDENTIFIER_LABELS.concept,
      value: concept.key,
      secondary: concept.rdlUri,
      copy: concept.rdlUri ? `${concept.key} ${concept.rdlUri}` : concept.key
    });
  }
  const customType = customTypeOf(symbol);
  if (customType?.rdlUri && customType.rdlUri !== concept?.rdlUri) {
    list.push({
      key: 'customType',
      label: IDENTIFIER_LABELS.customType,
      value: customType.rdlUri,
      copy: customType.rdlUri
    });
  }
  return list;
}

// ---- Tabs ---------------------------------------------------------------

export function primaryAssetSha(symbol) {
  const assets = symbol?.payload?.assets;
  if (Array.isArray(assets) && assets[0]?.sha256) return String(assets[0].sha256);
  const primary = (symbol?.downloadAssets || []).find((asset) => asset?.role === 'primary' && asset.sha256);
  return primary ? String(primary.sha256) : '';
}

const REGISTER_STATUSES = [
  ['origo', 'Origo'],
  ['sizing', 'Sizing'],
  ['label', 'Label'],
  ['connections', 'Connections'],
  ['mapping', 'Mapping']
];

function allowance(value) {
  const flag = yesNo(value);
  if (flag === null) return '';
  return flag ? 'Allowed' : 'Not allowed';
}

// The Details tab: three sections of label/value rows, empty ones dropped.
export function detailsSections(symbol) {
  const disc = discOf(symbol);
  const dexpi = dexpiOf(symbol);
  const references = disc.references || {};
  const frame = drawingFrame(symbol);
  const hasGeometry = Boolean(frame);
  const transform = disc.transform || {};
  const registerStatus = disc.register_status || {};
  const aliases = Array.isArray(symbol?.payload?.aliases) ? symbol.payload.aliases : [];
  const sections = [
    {
      id: 'identity',
      title: 'Identity',
      rows: rows([
        row('Catalog ID', text(symbol?.catalogSymbolId)),
        row('Source ID', text(disc.disc_id) || text(dexpi.standard_assertion?.source_symbol_identifier)),
        row('Legacy ID', text(disc.legacy_id)),
        row('TR1970', text(references.TR1970)),
        row('NORSOK Z-004', text(references.NORSOK_Z004)),
        row('ISA-5.1', text(references.ISA_5_1)),
        row('Aliases', text(aliases))
      ])
    },
    {
      id: 'geometry',
      title: 'Geometry',
      rows: rows([
        row('Register size', sizeText(disc.size_mm)),
        row('Drawing extent', hasGeometry ? `${formatMm(frame.width)} × ${formatMm(frame.height)} mm` : ''),
        row('Insertion point', hasGeometry ? (({ x, y }) => `${formatMm(x)}, ${formatMm(y)} mm`)(insertionPoint(symbol)) : ''),
        row('Rotation', allowance(transform.rotation)),
        row('Mirroring', allowance(transform.mirroring)),
        row('Resize X', allowance(transform.resize_x)),
        row('Resize Y', allowance(transform.resize_y))
      ])
    },
    {
      id: 'register',
      title: 'Source register',
      rows: rows([
        row('Grouping', text(disc.grouping)),
        row('Register update', formatDate(disc.register_last_update)),
        ...REGISTER_STATUSES.map(([key, label]) => row(`${label} status`, text(registerStatus[key]))),
        row('Profile', text(dexpi.standard_assertion?.version_label))
      ])
    }
  ];
  return sections.filter((section) => section.rows.length);
}

// The Connection points table: number, kind, X and Y in mm, direction.
export function connectionRows(symbol) {
  const points = geometryOf(symbol).connection_points;
  if (!Array.isArray(points)) return [];
  return points
    .filter((point) => point && Number.isFinite(point.x_mm) && Number.isFinite(point.y_mm))
    .map((point, position) => ({
      index: point.index ?? position + 1,
      kind: text(point.kinds),
      x: formatMm(point.x_mm),
      y: formatMm(point.y_mm),
      direction: Array.isArray(point.directions_deg) && point.directions_deg.length
        ? point.directions_deg.map((degrees) => `${parseFloat(Number(degrees).toFixed(2))}°`).join(', ')
        : ''
    }));
}

// Label slots with their boxes and templates, verbatim, and the type codes.
export function labelSlotRows(symbol) {
  const slots = geometryOf(symbol).label_slots;
  const registerTemplates = discOf(symbol).label_templates || {};
  if (!Array.isArray(slots)) return [];
  return slots
    .filter((slot) => slot && slot.label_index !== undefined && slot.label_index !== null)
    .map((slot) => {
      const box = Array.isArray(slot.box_mm) && slot.box_mm.length === 4 && slot.box_mm.every(Number.isFinite)
        ? `${slot.box_mm.map(formatMm).join(', ')} mm`
        : '';
      return {
        index: String(slot.label_index),
        lines: Number(slot.lines) > 0 ? Number(slot.lines) : null,
        box,
        template: slot.template ?? registerTemplates[slot.label_index] ?? ''
      };
    });
}

export function typeCodes(symbol) {
  return text(discOf(symbol).type_codes);
}

// ---- The details response -------------------------------------------------

const METHOD_LABELS = {
  source_mapping: 'Source mapping',
  rule: 'Rule',
  manual: 'Manual',
  ai_assisted: 'AI-assisted',
  legacy_backfill: 'Legacy backfill'
};

const RELATION_LABELS = {
  exact: 'Exact match',
  close: 'Close match',
  broader: 'Broader',
  narrower: 'Narrower',
  related: 'Related'
};

const HIDDEN_CLASSIFICATION_STATUSES = new Set(['rejected', 'retired']);

function list(value) {
  return Array.isArray(value) ? value : [];
}

function relatedSymbol(item) {
  if (!item || typeof item !== 'object') return null;
  const reference = text(item.catalogSymbolId) || text(item.slug);
  if (!reference) return null;
  return {
    reference,
    catalogSymbolId: text(item.catalogSymbolId),
    name: text(item.name),
    pack: text(item.pack) || text(item.packCode),
    previewUrl: text(item.previewUrl)
  };
}

// The details response with every field defaulted, so a missing or older
// answer shows as "no data" rather than throwing. Rejected and retired
// classifications are dropped here as well as on the server.
export function normalizeSymbolDetails(payload) {
  const source = payload && typeof payload === 'object' ? payload : {};
  const sameClass = source.sameClass && typeof source.sameClass === 'object' ? source.sameClass : null;
  const classTotal = Number(sameClass?.total);
  return {
    classifications: list(source.classifications)
      .filter((item) => item && !HIDDEN_CLASSIFICATION_STATUSES.has(String(item.status || '').toLowerCase()))
      .map((item) => ({
        scheme: text(item.schemeName) || text(item.schemeCode),
        path: list(item.nodePath).map(text).filter(Boolean).join(' › ') || text(item.nodeLabel) || text(item.nodeCode),
        method: METHOD_LABELS[item.method] || text(item.method),
        role: text(item.role)
      }))
      .filter((item) => item.scheme && item.path),
    externalMappings: list(source.externalMappings)
      .map((item) => ({
        system: text(item?.system),
        identifier: text(item?.identifier),
        label: text(item?.label),
        relation: RELATION_LABELS[item?.relation] || text(item?.relation)
      }))
      .filter((item) => item.system && item.identifier),
    sameConcept: list(source.sameConcept).map(relatedSymbol).filter(Boolean),
    sameClass: sameClass && text(sameClass.className) && Number.isFinite(classTotal) && classTotal > 0
      ? {
          className: text(sameClass.className),
          total: classTotal,
          items: list(sameClass.items).map(relatedSymbol).filter(Boolean).slice(0, 8)
        }
      : null,
    rights: source.rights && typeof source.rights === 'object' ? source.rights : null,
    provenance: source.provenance && typeof source.provenance === 'object' ? source.provenance : null,
    // Newest first, whatever order the server sent them in.
    history: list(source.history)
      .filter((item) => item && item.at && text(item.label))
      .map((item) => ({ kind: text(item.kind), at: item.at, label: text(item.label) }))
      .sort((left, right) => (Date.parse(right.at) || 0) - (Date.parse(left.at) || 0))
  };
}

// A mapping identifier is a URI; show its last segment and link the rest.
export function mappingDisplay(identifier) {
  if (!isHttpUrl(identifier)) return { text: identifier, href: '' };
  const segment = identifier.replace(/[#/]+$/, '').split(/[/#]/).pop();
  return { text: segment || identifier, href: identifier };
}

// "repo @ commit": the source's last path segment with the commit shortened.
export function sourceRepositoryText(provenance) {
  if (!provenance) return '';
  const uri = text(provenance.sourceUri);
  const commit = text(provenance.sourceCommit);
  const repository = isHttpUrl(uri)
    ? uri.replace(/\/(tree|blob)\/.*$/, '').replace(/\/+$/, '').split('/').slice(-2).join('/')
    : uri;
  const shortCommit = commit.length > 12 ? commit.slice(0, 12) : commit;
  if (repository && shortCommit) return `${repository} @ ${shortCommit}`;
  return repository || shortCommit || text(provenance.releaseVersion);
}

export function rightsRows(details) {
  const rights = details?.rights;
  if (!rights) return [];
  return rows([
    row('Status', text(rights.status)),
    row('Disposition', text(rights.disposition)),
    row('Licensor', text(rights.licensor)),
    row('Creator', text(rights.creator)),
    row('Attribution', text(rights.attributionText))
  ]);
}

export function provenanceRows(symbol, details) {
  const provenance = details?.provenance;
  return rows([
    row('Pack', text(provenance?.pack) || text(symbol?.pack)),
    row('Source repository @ commit', sourceRepositoryText(provenance)),
    row('Source file', text(provenance?.sourcePath)),
    row('File SHA-256', primaryAssetSha(symbol)),
    row('Geometry signature', text(dexpiOf(symbol).geometry_signature))
  ]);
}

// Which tabs this symbol has. Classification is always offered (it is the
// default, and says so when empty); Comments always; the others only when
// there is something to show.
export function availableTabs(symbol, details) {
  const normalized = details || normalizeSymbolDetails(null);
  const ids = ['classification'];
  if (detailsSections(symbol).length) ids.push('details');
  if (connectionRows(symbol).length) ids.push('connections');
  if (labelSlotRows(symbol).length || stateVariantList(symbol).length || typeCodes(symbol)) ids.push('labels');
  if (rightsRows(normalized).length || provenanceRows(symbol, normalized).some((item) => item.label !== 'Pack')) ids.push('source');
  if (normalized.history.length) ids.push('history');
  ids.push('comments');
  return ids;
}

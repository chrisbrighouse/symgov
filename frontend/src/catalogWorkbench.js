const CATALOG_DISCIPLINE_ORDER = [
  'Electrical',
  'Fire & Life Safety',
  'Piping / P&ID',
  'Process',
  'Instrumentation & Controls',
  'Mechanical',
  'HVAC',
  'Civil / Structural',
  'Architectural',
  'Safety / Signage',
  'General / Annotation'
];

const CATALOG_CATEGORY_ORDER = [
  'Valves',
  'Pumps',
  'Vessels / Tanks',
  'Pipework / Fittings',
  'Instruments',
  'Fire Alarm Devices',
  'Sensors / Detectors',
  'Motors / Drives',
  'Electrical Devices',
  'Switchgear / Distribution',
  'Lighting',
  'Controls',
  'Actuators',
  'Heating / HVAC',
  'Safety Devices',
  'Annotations / Tags',
  'Drawing Symbols',
  'Equipment',
  'Miscellaneous / Unclassified'
];

const CATALOG_USE_CASE_ORDER = [
  'Insert into CAD drawing',
  'Mark up / annotate drawing',
  'Use in PDF/report',
  'Use as web/app icon',
  'Use as reference only',
  'Compare against standard'
];

const FORMAT_ORDER = ['DXF', 'DWG', 'SVG', 'PNG', 'JPG', 'JPEG', 'PDF', 'RVT', 'RFA', 'IFC', 'ZIP', 'JSON'];

function compactUnique(values) {
  return Array.from(new Set((values || []).map((value) => String(value || '').trim()).filter(Boolean)));
}

function sortByPreferredOrder(values, preferredOrder) {
  const order = new Map(preferredOrder.map((value, index) => [value.toLowerCase(), index]));
  return compactUnique(values).sort((left, right) => {
    const leftRank = order.has(left.toLowerCase()) ? order.get(left.toLowerCase()) : Number.MAX_SAFE_INTEGER;
    const rightRank = order.has(right.toLowerCase()) ? order.get(right.toLowerCase()) : Number.MAX_SAFE_INTEGER;
    if (leftRank !== rightRank) {
      return leftRank - rightRank;
    }
    return left.localeCompare(right);
  });
}

function textTokens(...values) {
  return values
    .flatMap((value) => {
      if (Array.isArray(value)) {
        return value;
      }
      if (value && typeof value === 'object') {
        return Object.values(value);
      }
      return [value];
    })
    .map((value) => String(value || '').toLowerCase());
}

function symbolContextText(symbol = {}) {
  return textTokens(
    symbol.name,
    symbol.displayName,
    symbol.category,
    symbol.discipline,
    symbol.summary,
    symbol.description,
    symbol.keywords,
    symbol.downloads,
    symbol.downloadAssets,
    symbol.payload?.name,
    symbol.payload?.description,
    symbol.payload?.summary,
    symbol.payload?.keywords,
    symbol.payload?.source_file,
    symbol.payload?.source_file_name
  ).join(' ');
}

export function normalizeCatalogDiscipline(value) {
  const raw = String(value || '').trim();
  const normalized = raw.toLowerCase().replace(/[\s-]+/g, '_');
  const map = {
    electrical: ['Electrical'],
    elec: ['Electrical'],
    fire: ['Fire & Life Safety'],
    fire_alarm: ['Fire & Life Safety', 'Electrical'],
    fire_alarms: ['Fire & Life Safety', 'Electrical'],
    fire_life_safety: ['Fire & Life Safety'],
    piping: ['Piping / P&ID'],
    p_id: ['Piping / P&ID'],
    pid: ['Piping / P&ID'],
    process: ['Process'],
    process_instrumentation: ['Instrumentation & Controls', 'Piping / P&ID'],
    instrumentation: ['Instrumentation & Controls'],
    controls: ['Instrumentation & Controls'],
    instrumentation_controls: ['Instrumentation & Controls'],
    mechanical: ['Mechanical'],
    mech: ['Mechanical'],
    hvac: ['HVAC'],
    civil: ['Civil / Structural'],
    structural: ['Civil / Structural'],
    architectural: ['Architectural'],
    safety: ['Safety / Signage'],
    signage: ['Safety / Signage'],
    general: ['General / Annotation'],
    unknown_discipline: ['General / Annotation'],
    '': []
  };
  return map[normalized] || (raw ? [raw] : []);
}

export function normalizeCatalogCategory(value, symbol = {}) {
  const raw = String(value || '').trim();
  const normalized = raw.toLowerCase().replace(/[\s-]+/g, '_');
  const context = symbolContextText(symbol);
  const categories = [];

  if (/fire|smoke|heat|detector|call\s?point|break\s?glass|sounder|beacon|alarm/.test(context)) {
    categories.push('Fire Alarm Devices');
  }
  if (/detector|sensor|smoke|heat|co\b|carbon/.test(context)) {
    categories.push('Sensors / Detectors');
  }

  const map = {
    valve: ['Valves'],
    valves: ['Valves'],
    valve_symbol: ['Valves'],
    gate_valve: ['Valves'],
    gate_valves: ['Valves'],
    pump: ['Pumps'],
    pumps: ['Pumps'],
    vessel: ['Vessels / Tanks'],
    vessels: ['Vessels / Tanks'],
    tank: ['Vessels / Tanks'],
    tanks: ['Vessels / Tanks'],
    pipework: ['Pipework / Fittings'],
    pipe: ['Pipework / Fittings'],
    fitting: ['Pipework / Fittings'],
    fittings: ['Pipework / Fittings'],
    instrument: ['Instruments'],
    instruments: ['Instruments'],
    motor: ['Motors / Drives'],
    motors: ['Motors / Drives'],
    drive: ['Motors / Drives'],
    drives: ['Motors / Drives'],
    smallpower: ['Electrical Devices'],
    lighting: ['Lighting'],
    heating: ['Heating / HVAC'],
    hvac: ['Heating / HVAC'],
    actuator: ['Actuators'],
    actuators: ['Actuators'],
    control: ['Controls'],
    controls: ['Controls'],
    counter: ['Instruments'],
    cylinder: ['Equipment'],
    envelope: ['Equipment'],
    stirrer: ['Equipment'],
    symbol: ['Drawing Symbols'],
    symbol_sheet: ['Drawing Symbols'],
    annotation: ['Annotations / Tags'],
    tag: ['Annotations / Tags'],
    tags: ['Annotations / Tags'],
    '': []
  };

  categories.push(...(map[normalized] || (raw ? [raw] : [])));
  return sortByPreferredOrder(categories.length ? categories : ['Miscellaneous / Unclassified'], CATALOG_CATEGORY_ORDER);
}

export function availableFormatsForSymbol(symbol = {}) {
  const formats = [];
  const pushFormat = (value) => {
    const text = String(value || '').trim();
    if (!text) {
      return;
    }
    const extensionMatch = text.match(/\.([a-z0-9]+)(?:$|[?#])/i);
    const clean = (extensionMatch ? extensionMatch[1] : text).replace(/^image\//i, '').replace(/^application\//i, '');
    const mapped = clean.toLowerCase() === 'jpeg' ? 'JPG' : clean.toUpperCase();
    if (mapped && mapped.length <= 8) {
      formats.push(mapped);
    }
  };

  pushFormat(symbol.format);
  pushFormat(symbol.contentType);
  (symbol.availableFormats || []).forEach(pushFormat);
  (symbol.downloads || []).forEach(pushFormat);
  (symbol.downloadAssets || []).forEach((asset) => {
    pushFormat(asset?.format || asset?.filename || asset?.content_type || asset?.contentType || asset?.object_key);
  });
  [...(symbol.previewAssets || []), symbol.previewAsset].filter(Boolean).forEach((asset) => {
    pushFormat(asset?.format || asset?.filename || asset?.content_type || asset?.contentType || asset?.object_key);
  });
  const payload = symbol.payload || {};
  pushFormat(payload.format);
  pushFormat(payload.source_format);
  (payload.downloads || []).forEach((asset) => {
    if (typeof asset === 'string') {
      pushFormat(asset);
    } else {
      pushFormat(asset?.format || asset?.filename || asset?.content_type || asset?.contentType || asset?.object_key);
    }
  });

  return sortByPreferredOrder(formats, FORMAT_ORDER);
}

export function useCasesForFormats(formats = []) {
  const normalized = new Set((formats || []).map((format) => String(format || '').toUpperCase()));
  const useCases = [];
  if (['DXF', 'DWG', 'RVT', 'RFA', 'IFC'].some((format) => normalized.has(format))) {
    useCases.push('Insert into CAD drawing');
  }
  if (['PNG', 'JPG', 'JPEG', 'SVG', 'PDF'].some((format) => normalized.has(format))) {
    useCases.push('Mark up / annotate drawing');
  }
  if (['PNG', 'JPG', 'JPEG', 'PDF', 'SVG'].some((format) => normalized.has(format))) {
    useCases.push('Use in PDF/report');
  }
  return sortByPreferredOrder(useCases, CATALOG_USE_CASE_ORDER);
}

export function catalogTaxonomyForSymbol(symbol = {}) {
  const context = symbolContextText(symbol);
  const disciplines = [
    ...normalizeCatalogDiscipline(symbol.discipline),
    ...normalizeCatalogDiscipline(symbol.engineeringDiscipline),
    ...(Array.isArray(symbol.disciplines) ? symbol.disciplines.flatMap(normalizeCatalogDiscipline) : [])
  ];
  if (/fire|smoke|heat|detector|call\s?point|break\s?glass|sounder|beacon|alarm/.test(context)) {
    disciplines.push('Fire & Life Safety');
  }
  const categories = [
    ...normalizeCatalogCategory(symbol.category, symbol),
    ...(Array.isArray(symbol.categories) ? symbol.categories.flatMap((category) => normalizeCatalogCategory(category, symbol)) : [])
  ];
  const availableFormats = availableFormatsForSymbol(symbol);
  const useCases = useCasesForFormats(availableFormats);

  return {
    disciplines: sortByPreferredOrder(disciplines, CATALOG_DISCIPLINE_ORDER),
    categories: sortByPreferredOrder(categories, CATALOG_CATEGORY_ORDER),
    availableFormats,
    useCases
  };
}

export function buildCatalogFacetValues(symbols = []) {
  const disciplines = [];
  const categories = [];
  const formats = [];
  const useCases = [];
  symbols.forEach((symbol) => {
    const taxonomy = catalogTaxonomyForSymbol(symbol);
    disciplines.push(...taxonomy.disciplines);
    categories.push(...taxonomy.categories);
    formats.push(...taxonomy.availableFormats);
    useCases.push(...taxonomy.useCases);
  });
  return {
    disciplines: sortByPreferredOrder(disciplines, CATALOG_DISCIPLINE_ORDER),
    categories: sortByPreferredOrder(categories, CATALOG_CATEGORY_ORDER),
    formats: sortByPreferredOrder(formats, FORMAT_ORDER),
    useCases: sortByPreferredOrder(useCases, CATALOG_USE_CASE_ORDER)
  };
}

export function serializeCatalogPreferences(input = {}) {
  return {
    disciplines: sortByPreferredOrder(input.disciplines || [], CATALOG_DISCIPLINE_ORDER),
    categories: sortByPreferredOrder(input.categories || [], CATALOG_CATEGORY_ORDER),
    formats: sortByPreferredOrder((input.formats || []).map((format) => String(format || '').toUpperCase()), FORMAT_ORDER),
    useCases: sortByPreferredOrder(input.useCases || [], CATALOG_USE_CASE_ORDER)
  };
}

export function buildCatalogViewSnapshot({ name, query = '', facetFilters = {}, preferredFormats = [] } = {}) {
  return {
    id: `view-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    name: String(name || '').trim() || 'Untitled Catalog view',
    query: String(query || ''),
    facetFilters: Object.fromEntries(
      Object.entries(facetFilters || {}).map(([key, value]) => [key, compactUnique(value)])
    ),
    preferredFormats: sortByPreferredOrder((preferredFormats || []).map((format) => String(format || '').toUpperCase()), FORMAT_ORDER),
    createdAt: new Date().toISOString()
  };
}

export function applySavedCatalogView(view = {}) {
  return {
    query: String(view.query || ''),
    facetFilters: Object.fromEntries(
      Object.entries(view.facetFilters || {}).map(([key, value]) => [key, compactUnique(value)])
    ),
    preferredFormats: sortByPreferredOrder((view.preferredFormats || []).map((format) => String(format || '').toUpperCase()), FORMAT_ORDER)
  };
}

function displaySymbolId(symbol = {}) {
  const packageId = symbol.packageDisplayId || symbol.package_display_id;
  const sequence = symbol.packageSymbolSequence ?? symbol.package_symbol_sequence;
  const packageDisplay = packageId && sequence != null ? `${packageId}-${sequence}` : '';
  return symbol.displayName || symbol.display_name || symbol.symbolDisplayId || symbol.symbol_display_id || packageDisplay || symbol.id || symbol.symbolId || '';
}

function displaySymbolName(symbol = {}) {
  return symbol.name || symbol.payload?.name || symbol.payload?.canonical_name || symbol.canonicalName || symbol.slug || displaySymbolId(symbol);
}

export function addSymbolsToClipboard(current = [], symbols = []) {
  const byId = new Map((current || []).map((item) => [item.id, item]));
  (symbols || []).forEach((symbol) => {
    const id = String(symbol.id || symbol.symbolId || symbol.slug || '').trim();
    if (!id || byId.has(id)) {
      return;
    }
    byId.set(id, {
      id,
      displayName: displaySymbolId(symbol),
      name: displaySymbolName(symbol),
      availableFormats: availableFormatsForSymbol(symbol)
    });
  });
  return Array.from(byId.values());
}

export function removeSymbolFromClipboard(current = [], symbolId) {
  return (current || []).filter((item) => item.id !== symbolId);
}

export function buildCatalogSearchText(symbol = {}) {
  const taxonomy = catalogTaxonomyForSymbol(symbol);
  return compactUnique([
    displaySymbolId(symbol),
    displaySymbolName(symbol),
    symbol.id,
    symbol.symbolId,
    symbol.slug,
    symbol.category,
    symbol.discipline,
    symbol.pack,
    symbol.packCode,
    symbol.pageCode,
    symbol.summary,
    symbol.description,
    ...(symbol.keywords || []),
    ...taxonomy.disciplines,
    ...taxonomy.categories,
    ...taxonomy.availableFormats,
    ...taxonomy.useCases
  ]).join(' ');
}

export function buildCatalogCardSummary(symbol = {}) {
  const taxonomy = catalogTaxonomyForSymbol(symbol);
  return {
    id: String(symbol.id || symbol.symbolId || symbol.slug || '').trim(),
    displayId: displaySymbolId(symbol),
    name: displaySymbolName(symbol),
    categories: taxonomy.categories,
    disciplines: taxonomy.disciplines,
    formats: taxonomy.availableFormats,
    useCases: taxonomy.useCases,
    hasPhotos: Array.isArray(symbol.supplementalPhotos) && symbol.supplementalPhotos.length > 0,
    commentCount: Number(symbol.commentCount || 0) || (symbol.hasComments ? 1 : 0)
  };
}

export function buildCatalogPreviewOptions(symbol = {}, selectedFormat = '') {
  const taxonomy = catalogTaxonomyForSymbol(symbol);
  const normalizeFormat = (value) => String(value || '').trim().replace(/^\./, '').toUpperCase();
  const activeFormat = normalizeFormat(selectedFormat || symbol.previewAsset?.format);
  const previewableFormats = new Set(
    [...(symbol.previewAssets || []), symbol.previewAsset]
      .filter(Boolean)
      .map((asset) => normalizeFormat(asset.format))
      .filter(Boolean)
  );

  return taxonomy.availableFormats.map((format) => {
    const normalized = normalizeFormat(format);
    return {
      format: normalized,
      active: normalized === activeFormat,
      previewable: previewableFormats.has(normalized)
    };
  });
}

export function buildReviewPreviewOptions(review = {}, selectedFormat = '') {
  const normalizeFormat = (value) => String(value || '').trim().replace(/^\./, '').toUpperCase();
  const assetsByFormat = new Map();
  (review.sourceAssets || []).forEach((asset) => {
    const format = normalizeFormat(asset?.format);
    const currentAsset = assetsByFormat.get(format);
    const assetPriority = asset?.selectedPreview ? 2 : asset?.previewable ? 1 : 0;
    const currentPriority = currentAsset?.selectedPreview ? 2 : currentAsset?.previewable ? 1 : 0;
    if (format && (!currentAsset || assetPriority > currentPriority)) {
      assetsByFormat.set(format, asset);
    }
  });
  const defaultAsset = (review.sourceAssets || []).find((asset) => asset?.selectedPreview);
  const activeFormat = normalizeFormat(selectedFormat || defaultAsset?.format || review.format);
  const formats = sortByPreferredOrder(
    [...(review.availableFormats || []), ...assetsByFormat.keys()].map(normalizeFormat),
    FORMAT_ORDER
  );

  return formats.map((format) => {
    const asset = assetsByFormat.get(format);
    return {
      format,
      active: format === activeFormat,
      previewable: Boolean(asset?.previewable),
      objectKey: asset?.objectKey || ''
    };
  });
}

export function buildReviewPreviewUrl(review = {}, objectKey = '', format = '') {
  const sourcePreviewUrl = String(review.sourcePreviewUrl || '').trim();
  if (!sourcePreviewUrl || !objectKey) {
    return sourcePreviewUrl || null;
  }
  const separator = sourcePreviewUrl.includes('?') ? '&' : '?';
  const normalizedFormat = String(format || '').trim().replace(/^\./, '').toUpperCase();
  const formatQuery = normalizedFormat ? `&format=${encodeURIComponent(normalizedFormat)}` : '';
  return `${sourcePreviewUrl}${separator}object_key=${encodeURIComponent(objectKey)}${formatQuery}`;
}

export function interpretEdCatalogPrompt(prompt = '') {
  const rawPrompt = String(prompt || '').trim();
  const normalizedPrompt = rawPrompt.toLowerCase();
  const disciplines = [];
  const categories = [];
  const useCases = [];
  const formats = [];
  const matchedTerms = [];
  const hasExplicitFormatMention = FORMAT_ORDER.some((format) => {
    const pattern = new RegExp(`\\b${format.toLowerCase()}\\b`, 'i');
    return pattern.test(rawPrompt);
  });

  const match = (pattern, onMatch, label) => {
    if (pattern.test(normalizedPrompt)) {
      onMatch();
      matchedTerms.push(label);
    }
  };

  match(/\b(fire|alarm|smoke|heat detector|break\s?glass|call\s?point|sounder|beacon)\b/, () => {
    disciplines.push('Fire & Life Safety');
    categories.push('Fire Alarm Devices');
  }, 'fire alarm');
  match(/\b(detector|sensor|smoke|heat|co\b|carbon monoxide)\b/, () => {
    categories.push('Sensors / Detectors');
  }, 'detector/sensor');
  match(/\b(electrical|elec|switchgear|distribution|lighting)\b/, () => {
    disciplines.push('Electrical');
  }, 'electrical');
  match(/\b(switchgear|distribution|panelboard|panelboards|switchboard|switchboards)\b/, () => {
    categories.push('Switchgear / Distribution');
  }, 'switchgear/distribution');
  match(/\b(lighting|light|lights|luminaire|luminaires)\b/, () => {
    categories.push('Lighting');
  }, 'lighting');
  match(/\b(mechanical|mech)\b/, () => {
    disciplines.push('Mechanical');
  }, 'mechanical');
  match(/\b(motor|motors|drive|drives|vfd|starter|starters)\b/, () => {
    disciplines.push('Electrical');
    categories.push('Motors / Drives');
  }, 'motors/drives');
  match(/\b(p\s?&\s?id|pid|piping|pipework|process)\b/, () => {
    disciplines.push('Piping / P&ID');
  }, 'piping/p&id');
  match(/\b(valve|valves)\b/, () => {
    categories.push('Valves');
  }, 'valves');
  match(/\b(pump|pumps)\b/, () => {
    categories.push('Pumps');
  }, 'pumps');
  match(/\b(cad|dxf|dwg|insert|editable)\b/, () => {
    useCases.push('Insert into CAD drawing');
  }, 'cad');
  match(/\b(markup|marking up|drawing review|review|annotate|annotation|redline|png|jpg|jpeg)\b/, () => {
    useCases.push('Mark up / annotate drawing');
  }, 'markup');
  match(/\b(pdf|reports?|documents?|documentation)\b/, () => {
    useCases.push('Use in PDF/report');
    if (!hasExplicitFormatMention) {
      formats.push('SVG', 'PNG', 'PDF');
    }
  }, 'documentation');

  FORMAT_ORDER.forEach((format) => {
    const pattern = new RegExp(`\\b${format.toLowerCase()}\\b`, 'i');
    if (pattern.test(rawPrompt)) {
      formats.push(format === 'JPEG' ? 'JPG' : format);
      matchedTerms.push(format);
    }
  });

  const facetFilters = {
    catalogDisciplines: sortByPreferredOrder(disciplines, CATALOG_DISCIPLINE_ORDER),
    catalogCategories: sortByPreferredOrder(categories, CATALOG_CATEGORY_ORDER),
    useCases: sortByPreferredOrder(useCases, CATALOG_USE_CASE_ORDER),
    availableFormats: sortByPreferredOrder(formats, FORMAT_ORDER)
  };

  Object.keys(facetFilters).forEach((key) => {
    if (!facetFilters[key].length) {
      delete facetFilters[key];
    }
  });

  const searchTerms = compactUnique([
    ...(facetFilters.catalogDisciplines || []),
    ...(facetFilters.catalogCategories || []),
    ...(facetFilters.useCases || []),
    ...(facetFilters.availableFormats || [])
  ]);

  return {
    query: rawPrompt,
    searchQuery: searchTerms.join(' ') || rawPrompt,
    facetFilters,
    preferredFormats: facetFilters.availableFormats || [],
    matchedTerms: compactUnique(matchedTerms),
    explanation: matchedTerms.length
      ? `Ed mapped ${compactUnique(matchedTerms).join(', ')} to Catalog filters. No records were changed.`
      : 'Ed did not find exact filter matches, so the prompt was applied as a Catalog search only. No records were changed.',
    mutatesRecords: false
  };
}

export function sortSymbolsByPreferredFormats(symbols = [], preferredFormats = []) {
  const preferred = (preferredFormats || []).map((format) => String(format || '').toUpperCase()).filter(Boolean);
  if (!preferred.length) {
    return symbols;
  }
  return [...symbols].sort((left, right) => {
    const leftFormats = availableFormatsForSymbol(left);
    const rightFormats = availableFormatsForSymbol(right);
    const leftRank = preferred.findIndex((format) => leftFormats.includes(format));
    const rightRank = preferred.findIndex((format) => rightFormats.includes(format));
    const normalizedLeftRank = leftRank === -1 ? Number.MAX_SAFE_INTEGER : leftRank;
    const normalizedRightRank = rightRank === -1 ? Number.MAX_SAFE_INTEGER : rightRank;
    if (normalizedLeftRank !== normalizedRightRank) {
      return normalizedLeftRank - normalizedRightRank;
    }
    return 0;
  });
}

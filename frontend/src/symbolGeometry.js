// What an imported symbol library records about a drawing beyond its picture:
// where its connections are, where labels go and what they say, which option
// (state) variants it has, and the attribution its source asks to travel with
// it. Every value is read from the served symbol; nothing here is invented, and
// a symbol without these fields simply yields nothing.

const KIND_ORDER = ['piping', 'signal', 'label', 'auxiliary'];

function geometryOf(symbol) {
  const geometry = symbol?.payload?.geometry;
  return geometry && typeof geometry === 'object' ? geometry : null;
}

export function connectionPointSummary(symbol) {
  const points = geometryOf(symbol)?.connection_points;
  if (!Array.isArray(points) || !points.length) return { count: 0, kinds: [] };
  const counts = new Map();
  points.forEach((point) => {
    const kinds = Array.isArray(point?.kinds) && point.kinds.length ? point.kinds : ['unspecified'];
    kinds.forEach((kind) => counts.set(kind, (counts.get(kind) || 0) + 1));
  });
  const kinds = [...counts.entries()]
    .sort(([left], [right]) => {
      const leftIndex = KIND_ORDER.indexOf(left);
      const rightIndex = KIND_ORDER.indexOf(right);
      if (leftIndex === -1 && rightIndex === -1) return left.localeCompare(right);
      if (leftIndex === -1) return 1;
      if (rightIndex === -1) return -1;
      return leftIndex - rightIndex;
    })
    .map(([kind, count]) => ({ kind, count }));
  return { count: points.length, kinds };
}

export function connectionPointText(summary) {
  if (!summary.count) return '';
  const kinds = summary.kinds.map(({ kind, count }) => `${count} ${kind}`).join(', ');
  return `${summary.count} (${kinds})`;
}

// Label slots and the template text the register gives each one. A slot with
// no template still lists, so the count matches what is stored.
export function labelTemplateList(symbol) {
  const slots = geometryOf(symbol)?.label_slots;
  const registerTemplates = symbol?.payload?.disc?.label_templates;
  const fromSlots = Array.isArray(slots)
    ? slots.map((slot) => ({
        label: String(slot?.label_index ?? ''),
        lines: Number(slot?.lines) || null,
        template: slot?.template ?? registerTemplates?.[slot?.label_index] ?? ''
      }))
    : [];
  return fromSlots.filter((slot) => slot.label);
}

export function stateVariantList(symbol) {
  const variants = Array.isArray(symbol?.stateVariants) ? symbol.stateVariants : [];
  return variants
    .filter((variant) => variant && variant.url && variant.index != null)
    .map((variant) => ({
      index: variant.index,
      condition: variant.condition || '',
      filename: variant.filename || '',
      url: variant.url
    }))
    .sort((left, right) => left.index - right.index);
}

export function stateVariantCount(symbols = []) {
  return symbols.reduce((total, symbol) => total + stateVariantList(symbol).length, 0);
}

export function symbolAttribution(symbol) {
  const text = symbol?.payload?.dexpi?.attribution;
  return typeof text === 'string' ? text.trim() : '';
}

export function hasGeometryDetails(symbol) {
  return Boolean(
    connectionPointSummary(symbol).count ||
      labelTemplateList(symbol).length ||
      stateVariantList(symbol).length ||
      symbolAttribution(symbol)
  );
}

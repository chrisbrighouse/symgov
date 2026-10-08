// Overlay geometry for the symbol viewer. An imported drawing's geometry is in
// millimetres, origin-centred, y axis down, which is exactly an SVG user space,
// so the viewer draws the picture and its overlays in one <svg> whose viewBox
// is the drawing's own `bbox_mm` frame (plus a margin). A position in the
// overlay is then the stored mm value, with no pixel maths to drift.
//
// Directions are degrees clockwise from +x on screen: with y down, that is the
// vector (cos, sin).

const FORMATS_SHARING_FRAME = new Set(['SVG', 'PNG']);

function finite(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

function geometryOf(symbol) {
  const geometry = symbol?.payload?.geometry;
  return geometry && typeof geometry === 'object' ? geometry : null;
}

// The drawing's frame, or null when the symbol has none worth drawing in.
export function drawingFrame(symbol) {
  const box = geometryOf(symbol)?.bbox_mm;
  if (!Array.isArray(box) || box.length !== 4 || !box.every(finite)) return null;
  const [x0, y0, x1, y1] = box;
  if (!(x1 > x0) || !(y1 > y0)) return null;
  return { x0, y0, x1, y1, width: x1 - x0, height: y1 - y0 };
}

// Overlays belong to a rendering that shares the primary drawing's frame: its
// SVG or PNG renditions and the option (state) SVGs. Other formats may be
// cropped or scaled differently, so overlays would lie on them.
export function overlaysAvailable(symbol, representation) {
  if (!drawingFrame(symbol) || !representation) return false;
  return representation.sharesPrimaryFrame === true
    && FORMATS_SHARING_FRAME.has(String(representation.format || '').toUpperCase());
}

export function formatsSharingFrame(format) {
  return FORMATS_SHARING_FRAME.has(String(format || '').toUpperCase());
}

// The viewBox for a frame: the frame itself with a margin all round, so the
// dimension lines and the outermost connection points have room.
export function viewBoxFor(frame, marginFraction = 0.28) {
  const margin = Math.max(frame.width, frame.height) * marginFraction;
  return {
    x: frame.x0 - margin,
    y: frame.y0 - margin,
    width: frame.width + margin * 2,
    height: frame.height + margin * 2,
    margin
  };
}

export function viewBoxAttribute(box) {
  return [box.x, box.y, box.width, box.height].map(round4).join(' ');
}

// Marker sizes scale with the drawing, so a 18 mm valve and a 400 mm vessel
// both get readable markers.
export function markerSizing(frame) {
  const unit = Math.max(frame.width, frame.height);
  return {
    ringRadius: unit * 0.032,
    arrowLength: unit * 0.1,
    arrowHead: unit * 0.032,
    fontSize: unit * 0.05,
    strokeWidth: unit * 0.008,
    crosshair: unit * 0.045
  };
}

export function directionVector(degrees) {
  const radians = (degrees * Math.PI) / 180;
  return { dx: round4(Math.cos(radians)), dy: round4(Math.sin(radians)) };
}

function kindClass(kinds) {
  const first = Array.isArray(kinds) && kinds.length ? String(kinds[0]) : '';
  if (first === 'piping' || first === 'signal') return first;
  return 'other';
}

// Each connection point as the viewer draws it: where, which way, what kind
// (orange for piping, blue for signal), and the number it is listed under.
export function connectionPointMarkers(symbol, frame = drawingFrame(symbol)) {
  const points = geometryOf(symbol)?.connection_points;
  if (!frame || !Array.isArray(points)) return [];
  const sizing = markerSizing(frame);
  return points
    .filter((point) => point && finite(point.x_mm) && finite(point.y_mm))
    .map((point, position) => {
      const direction = Array.isArray(point.directions_deg) && finite(point.directions_deg[0])
        ? point.directions_deg[0]
        : null;
      const marker = {
        key: `cp-${point.index ?? position + 1}`,
        index: point.index ?? position + 1,
        x: point.x_mm,
        y: point.y_mm,
        direction,
        kind: kindClass(point.kinds),
        kinds: Array.isArray(point.kinds) ? point.kinds : [],
        arrow: null,
        number: null
      };
      // The number sits to the side of the arrow, clear of both it and the
      // ring; a point with no direction gets its number above it.
      const side = direction === null
        ? { dx: 0, dy: -1 }
        : (({ dx, dy }) => ({ dx: -dy, dy: dx }))(directionVector(direction));
      const gap = sizing.ringRadius * 2.4;
      marker.number = {
        x: round4(point.x_mm + side.dx * gap),
        y: round4(point.y_mm + side.dy * gap + sizing.fontSize * 0.35)
      };
      if (direction !== null) {
        const { dx, dy } = directionVector(direction);
        const startX = point.x_mm + dx * sizing.ringRadius;
        const startY = point.y_mm + dy * sizing.ringRadius;
        marker.arrow = {
          x1: round4(startX),
          y1: round4(startY),
          x2: round4(startX + dx * sizing.arrowLength),
          y2: round4(startY + dy * sizing.arrowLength),
          dx,
          dy
        };
      }
      return marker;
    });
}

// The arrow head as a triangle: tip at the arrow's end, base set back along
// the direction, half-width to either side.
export function arrowHeadPoints(arrow, size) {
  const baseX = arrow.x2 - arrow.dx * size;
  const baseY = arrow.y2 - arrow.dy * size;
  const px = -arrow.dy * size * 0.55;
  const py = arrow.dx * size * 0.55;
  return [
    [arrow.x2, arrow.y2],
    [baseX + px, baseY + py],
    [baseX - px, baseY - py]
  ].map(([x, y]) => `${round4(x)},${round4(y)}`).join(' ');
}

// The insertion point is the drawing's origin; a symbol that states none is
// placed at (0, 0).
export function insertionPoint(symbol) {
  const origin = geometryOf(symbol)?.origin;
  if (Array.isArray(origin) && origin.length === 2 && origin.every(finite)) {
    return { x: origin[0], y: origin[1] };
  }
  return { x: 0, y: 0 };
}

export function labelSlotBoxes(symbol) {
  const slots = geometryOf(symbol)?.label_slots;
  if (!Array.isArray(slots)) return [];
  return slots
    .filter((slot) => Array.isArray(slot?.box_mm) && slot.box_mm.length === 4 && slot.box_mm.every(finite))
    .map((slot, position) => {
      const [x0, y0, x1, y1] = slot.box_mm;
      return {
        key: `label-${slot.label_index ?? position}`,
        label: String(slot.label_index ?? position + 1),
        x: Math.min(x0, x1),
        y: Math.min(y0, y1),
        width: Math.abs(x1 - x0),
        height: Math.abs(y1 - y0)
      };
    });
}

export function formatMm(value) {
  if (!finite(value)) return '';
  return `${parseFloat(value.toFixed(2))}`;
}

// The drawing's width and height as dimension lines set just outside the
// frame: width below it, height to its right.
export function dimensionLines(frame) {
  const offset = Math.max(frame.width, frame.height) * 0.1;
  return {
    width: {
      x1: frame.x0,
      x2: frame.x1,
      y: frame.y1 + offset,
      label: `${formatMm(frame.width)} mm`
    },
    height: {
      y1: frame.y0,
      y2: frame.y1,
      x: frame.x1 + offset,
      label: `${formatMm(frame.height)} mm`
    }
  };
}

export const ZOOM_MIN = 0.5;
export const ZOOM_MAX = 8;
const ZOOM_STEP = 1.25;

export function stepZoom(current, direction) {
  const next = direction > 0 ? current * ZOOM_STEP : current / ZOOM_STEP;
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, round4(next)));
}

// `+ 0` turns -0 into 0, so a vector pointing straight up reads (0, -1).
function round4(value) {
  return Math.round(value * 10000) / 10000 + 0;
}

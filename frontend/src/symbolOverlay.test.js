import test from 'node:test';
import assert from 'node:assert/strict';
import {
  arrowHeadPoints,
  connectionPointMarkers,
  dimensionLines,
  directionVector,
  drawingFrame,
  formatMm,
  insertionPoint,
  labelSlotBoxes,
  markerSizing,
  overlaysAvailable,
  stepZoom,
  viewBoxAttribute,
  viewBoxFor,
  ZOOM_MAX,
  ZOOM_MIN
} from './symbolOverlay.js';
import { discValve, legacyPng, pilotSymbol } from './symbolDetailsFixtures.js';

const near = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1e-6, message || `${actual} != ${expected}`);

test('the frame is the drawing bbox, and absent without geometry', () => {
  const frame = drawingFrame(discValve);
  near(frame.width, 18.35);
  near(frame.height, 11.35);
  assert.equal(drawingFrame(pilotSymbol), null);
  assert.equal(drawingFrame(legacyPng), null);
  assert.equal(drawingFrame({ payload: { geometry: { bbox_mm: [1, 1, 1, 5] } } }), null);
  assert.equal(drawingFrame({ payload: { geometry: { bbox_mm: ['a', 0, 1, 1] } } }), null);
});

test('directions are degrees clockwise from +x on a y-down screen', () => {
  assert.deepEqual(directionVector(0), { dx: 1, dy: 0 });
  assert.deepEqual(directionVector(90), { dx: 0, dy: 1 });
  assert.deepEqual(directionVector(180), { dx: -1, dy: 0 });
  // 270 points up the screen: negative y.
  assert.deepEqual(directionVector(270), { dx: 0, dy: -1 });
});

test('S-273 connection points sit where the register puts them and point outwards', () => {
  const markers = connectionPointMarkers(discValve);
  assert.equal(markers.length, 3);
  assert.deepEqual(markers.map((marker) => [marker.index, marker.x, marker.y, marker.direction]), [
    [1, 9, 0.0625, 0],
    [2, -9, 0.0625, 180],
    [3, 0, -8.9375, 270]
  ]);
  assert.ok(markers.every((marker) => marker.kind === 'piping'));
  const [east, west, north] = markers;
  assert.ok(east.arrow.x2 > east.arrow.x1 && east.arrow.y2 === east.arrow.y1);
  assert.ok(west.arrow.x2 < west.arrow.x1);
  assert.ok(north.arrow.y2 < north.arrow.y1 && north.arrow.x2 === north.arrow.x1);
  // The arrow starts on the ring, not at its centre.
  const sizing = markerSizing(drawingFrame(discValve));
  near(east.arrow.x1 - east.x, sizing.ringRadius);
});

test('every S-273 connection point lies inside the frame', () => {
  const frame = drawingFrame(discValve);
  connectionPointMarkers(discValve).forEach((marker) => {
    assert.ok(marker.x >= frame.x0 && marker.x <= frame.x1, `point ${marker.index} x`);
    assert.ok(marker.y >= frame.y0 && marker.y <= frame.y1, `point ${marker.index} y`);
  });
});

test('piping is orange, signal is blue, anything else is neutral, and a point without a direction has no arrow', () => {
  const symbol = {
    payload: {
      geometry: {
        bbox_mm: [-10, -10, 10, 10],
        connection_points: [
          { index: 1, x_mm: 1, y_mm: 1, directions_deg: [0], kinds: ['piping'] },
          { index: 2, x_mm: 2, y_mm: 2, directions_deg: [90], kinds: ['signal'] },
          { index: 3, x_mm: 3, y_mm: 3, directions_deg: [], kinds: ['auxiliary'] },
          { index: 4, x_mm: 4, y_mm: 4 }
        ]
      }
    }
  };
  const markers = connectionPointMarkers(symbol);
  assert.deepEqual(markers.map((marker) => marker.kind), ['piping', 'signal', 'other', 'other']);
  assert.equal(markers[2].arrow, null);
  assert.equal(markers[3].arrow, null);
});

test('connection points with a non-numeric position are skipped', () => {
  const symbol = { payload: { geometry: { bbox_mm: [0, 0, 10, 10], connection_points: [{ x_mm: 'a', y_mm: 1 }, { x_mm: 1, y_mm: 1 }] } } };
  assert.equal(connectionPointMarkers(symbol).length, 1);
});

test('the arrow head points along the arrow', () => {
  const [east] = connectionPointMarkers(discValve);
  const points = arrowHeadPoints(east.arrow, 1).split(' ').map((pair) => pair.split(',').map(Number));
  assert.equal(points.length, 3);
  assert.deepEqual(points[0], [east.arrow.x2, east.arrow.y2]);
  // The base sits behind the tip, on both sides of the axis.
  assert.ok(points[1][0] < points[0][0] && points[2][0] < points[0][0]);
  assert.ok(points[1][1] > east.arrow.y2 && points[2][1] < east.arrow.y2);
});

test('the insertion point is the stated origin, else (0, 0)', () => {
  assert.deepEqual(insertionPoint(discValve), { x: 0, y: 0 });
  assert.deepEqual(insertionPoint({ payload: { geometry: { origin: [1.5, -2] } } }), { x: 1.5, y: -2 });
  assert.deepEqual(insertionPoint(pilotSymbol), { x: 0, y: 0 });
});

test('label slots become boxes in mm', () => {
  const [box] = labelSlotBoxes(discValve);
  assert.equal(box.label, 'A');
  near(box.x, 3);
  near(box.y, -3.9375);
  near(box.width, 5);
  near(box.height, 1);
  assert.deepEqual(labelSlotBoxes(pilotSymbol), []);
  // A reversed box is normalised.
  const [flipped] = labelSlotBoxes({ payload: { geometry: { label_slots: [{ label_index: 'B', box_mm: [8, -2, 3, -3] }] } } });
  assert.deepEqual([flipped.x, flipped.y, flipped.width, flipped.height], [3, -3, 5, 1]);
});

test('dimension lines sit outside the frame and are labelled in mm', () => {
  const frame = drawingFrame(discValve);
  const lines = dimensionLines(frame);
  assert.equal(lines.width.label, '18.35 mm');
  assert.equal(lines.height.label, '11.35 mm');
  assert.ok(lines.width.y > frame.y1);
  assert.ok(lines.height.x > frame.x1);
  near(lines.width.x1, frame.x0);
  near(lines.width.x2, frame.x1);
});

test('the viewBox is the frame with an even margin, so the frame is centred', () => {
  const frame = drawingFrame(discValve);
  const box = viewBoxFor(frame);
  near(frame.x0 - box.x, box.margin);
  near(box.x + box.width - frame.x1, box.margin);
  near(frame.y0 - box.y, box.margin);
  near(box.y + box.height - frame.y1, box.margin);
  assert.equal(viewBoxAttribute({ x: -1, y: -2, width: 3.123456, height: 4 }), '-1 -2 3.1235 4');
});

test('marker sizes scale with the drawing', () => {
  const small = markerSizing({ width: 18, height: 11 });
  const large = markerSizing({ width: 180, height: 110 });
  near(large.ringRadius, small.ringRadius * 10);
});

test('overlays are offered only with geometry and a representation in the primary frame', () => {
  const primary = { format: 'SVG', sharesPrimaryFrame: true };
  assert.equal(overlaysAvailable(discValve, primary), true);
  assert.equal(overlaysAvailable(discValve, { format: 'PNG', sharesPrimaryFrame: true }), true);
  assert.equal(overlaysAvailable(discValve, { format: 'JPG', sharesPrimaryFrame: true }), false);
  assert.equal(overlaysAvailable(discValve, { format: 'SVG', sharesPrimaryFrame: false }), false);
  assert.equal(overlaysAvailable(discValve, null), false);
  assert.equal(overlaysAvailable(pilotSymbol, primary), false);
  assert.equal(overlaysAvailable(legacyPng, { format: 'PNG', sharesPrimaryFrame: true }), false);
});

test('zoom steps are bounded', () => {
  assert.ok(stepZoom(1, 1) > 1);
  assert.ok(stepZoom(1, -1) < 1);
  assert.equal(stepZoom(ZOOM_MAX, 1), ZOOM_MAX);
  assert.equal(stepZoom(ZOOM_MIN, -1), ZOOM_MIN);
});

test('millimetres drop needless decimals', () => {
  assert.equal(formatMm(18), '18');
  assert.equal(formatMm(0.0625), '0.06');
  assert.equal(formatMm(-8.9375), '-8.94');
  assert.equal(formatMm(NaN), '');
});

test('each point has a number position beside it, clear of the ring', () => {
  const frame = drawingFrame(discValve);
  const sizing = markerSizing(frame);
  const [east, , north] = connectionPointMarkers(discValve);
  // Direction 0: the number sits to the side (below on a y-down screen).
  near(east.number.x, east.x);
  assert.ok(east.number.y > east.y + sizing.ringRadius);
  // Direction 270 (up): the number sits to the side, left or right of the arrow's line.
  assert.ok(Math.abs(north.number.x - north.x) > sizing.ringRadius);
});

import React, { useEffect, useMemo, useState } from 'react';
import {
  arrowHeadPoints,
  connectionPointMarkers,
  dimensionLines,
  drawingFrame,
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

const OVERLAY_TOGGLES = [
  { key: 'connections', label: 'Connection points' },
  { key: 'insertion', label: 'Insertion point' },
  { key: 'labels', label: 'Label slots' },
  { key: 'dimensions', label: 'Dimensions (mm)' }
];

const DEFAULT_OVERLAYS = { connections: true, insertion: true, labels: false, dimensions: false };

function conditionText(representation) {
  return representation.condition || '';
}

// One drawing, drawn in an <svg> whose viewBox is the drawing's own frame in
// mm, so overlays use the stored positions as they are (see symbolOverlay.js).
function OverlayDrawing({ symbol, representation, frame, overlays, highlightIndex, zoom }) {
  const box = viewBoxFor(frame);
  const sizing = markerSizing(frame);
  const markers = useMemo(() => connectionPointMarkers(symbol, frame), [symbol, frame]);
  const slots = useMemo(() => labelSlotBoxes(symbol), [symbol]);
  const origin = insertionPoint(symbol);
  const dimensions = dimensionLines(frame);
  const stroke = { vectorEffect: 'non-scaling-stroke' };
  return (
    <svg
      className="symbol-viewer-svg"
      viewBox={viewBoxAttribute(box)}
      style={{ width: `${zoom * 100}%`, height: `${zoom * 100}%` }}
      role="img"
      aria-label={`${representation.label}${conditionText(representation) ? `, ${conditionText(representation)}` : ''}, with overlays`}
    >
      <image
        href={representation.url}
        x={frame.x0}
        y={frame.y0}
        width={frame.width}
        height={frame.height}
        preserveAspectRatio="xMidYMid meet"
      />
      {overlays.dimensions ? (
        <g className="overlay-dimensions" fontSize={sizing.fontSize * 0.85}>
          <line x1={dimensions.width.x1} y1={dimensions.width.y} x2={dimensions.width.x2} y2={dimensions.width.y} {...stroke} />
          <line x1={dimensions.width.x1} y1={dimensions.width.y - sizing.crosshair / 2} x2={dimensions.width.x1} y2={dimensions.width.y + sizing.crosshair / 2} {...stroke} />
          <line x1={dimensions.width.x2} y1={dimensions.width.y - sizing.crosshair / 2} x2={dimensions.width.x2} y2={dimensions.width.y + sizing.crosshair / 2} {...stroke} />
          <text x={(dimensions.width.x1 + dimensions.width.x2) / 2} y={dimensions.width.y + sizing.fontSize * 1.3} textAnchor="middle">{dimensions.width.label}</text>
          <line x1={dimensions.height.x} y1={dimensions.height.y1} x2={dimensions.height.x} y2={dimensions.height.y2} {...stroke} />
          <line x1={dimensions.height.x - sizing.crosshair / 2} y1={dimensions.height.y1} x2={dimensions.height.x + sizing.crosshair / 2} y2={dimensions.height.y1} {...stroke} />
          <line x1={dimensions.height.x - sizing.crosshair / 2} y1={dimensions.height.y2} x2={dimensions.height.x + sizing.crosshair / 2} y2={dimensions.height.y2} {...stroke} />
          <text x={dimensions.height.x + sizing.fontSize * 0.5} y={(dimensions.height.y1 + dimensions.height.y2) / 2} textAnchor="start">{dimensions.height.label}</text>
        </g>
      ) : null}
      {overlays.labels ? (
        <g className="overlay-labels" fontSize={sizing.fontSize}>
          {slots.map((slot) => (
            <g key={slot.key}>
              <rect x={slot.x} y={slot.y} width={slot.width} height={slot.height} {...stroke} />
              <text x={slot.x + sizing.fontSize * 0.3} y={slot.y + sizing.fontSize * 0.95} strokeWidth={sizing.fontSize * 0.3}>{slot.label}</text>
            </g>
          ))}
        </g>
      ) : null}
      {overlays.insertion ? (
        <g className="overlay-insertion" {...stroke}>
          <line x1={origin.x - sizing.crosshair} y1={origin.y} x2={origin.x + sizing.crosshair} y2={origin.y} {...stroke} />
          <line x1={origin.x} y1={origin.y - sizing.crosshair} x2={origin.x} y2={origin.y + sizing.crosshair} {...stroke} />
          <circle cx={origin.x} cy={origin.y} r={sizing.ringRadius * 0.45} {...stroke} />
        </g>
      ) : null}
      {markers.map((marker) => {
        const highlighted = marker.index === highlightIndex;
        if (!overlays.connections && !highlighted) return null;
        return (
          <g
            key={marker.key}
            className={`overlay-point overlay-point-${marker.kind}${highlighted ? ' is-highlighted' : ''}`}
            data-point-index={marker.index}
          >
            {highlighted ? <circle className="overlay-point-halo" cx={marker.x} cy={marker.y} r={sizing.ringRadius * 2.4} {...stroke} /> : null}
            <circle cx={marker.x} cy={marker.y} r={sizing.ringRadius} {...stroke} />
            {marker.arrow ? (
              <>
                <line x1={marker.arrow.x1} y1={marker.arrow.y1} x2={marker.arrow.x2} y2={marker.arrow.y2} {...stroke} />
                <polygon points={arrowHeadPoints(marker.arrow, sizing.arrowHead)} />
              </>
            ) : null}
            <text x={marker.number.x} y={marker.number.y} fontSize={sizing.fontSize} strokeWidth={sizing.fontSize * 0.3} textAnchor="middle">{marker.index}</text>
          </g>
        );
      })}
    </svg>
  );
}

function RepresentationTile({ item, selected, onSelect }) {
  const caption = (
    <>
      <span className="symbol-tile-format">{item.format}</span>
      <span className="symbol-tile-label">{item.label}</span>
      {item.condition ? <span className="symbol-tile-condition">{item.condition}</span> : null}
    </>
  );
  if (!item.viewable) {
    return (
      <li>
        <div className="symbol-tile unavailable" aria-disabled="true">
          <span className="symbol-tile-thumb" aria-hidden="true">{item.format}</span>
          {caption}
          <span className="symbol-tile-note">Not viewable · download only</span>
        </div>
      </li>
    );
  }
  return (
    <li>
      <button
        type="button"
        className={`symbol-tile${selected ? ' selected' : ''}`}
        aria-pressed={selected}
        onClick={() => onSelect(item.key)}
      >
        <span className="symbol-tile-thumb">
          <img src={item.url} alt={`Thumbnail of ${item.label}${item.condition ? `, ${item.condition}` : ''}`} loading="lazy" />
        </span>
        {caption}
      </button>
    </li>
  );
}

// The viewer card: the selected representation large on a grid, a toolbar
// (name, format, condition, zoom, overlay toggles) and the strip of every
// format and state, click to switch.
export default function SymbolViewer({ symbol, representations, selectedKey, onSelectKey, highlightIndex = 0 }) {
  const { items } = representations;
  const selected = items.find((item) => item.key === selectedKey) || null;
  const frame = drawingFrame(symbol);
  const overlayAvailable = overlaysAvailable(symbol, selected && selected.viewable ? selected : null);
  const [overlays, setOverlays] = useState(DEFAULT_OVERLAYS);
  const [zoom, setZoom] = useState(1);

  // A new symbol starts from the defaults again.
  const symbolKey = symbol?.id || symbol?.symbolId || '';
  useEffect(() => {
    setOverlays(DEFAULT_OVERLAYS);
    setZoom(1);
  }, [symbolKey]);

  const toggleOverlay = (key) => setOverlays((current) => ({ ...current, [key]: !current[key] }));

  return (
    <section className="glass-panel pane symbol-viewer-card" aria-label="Symbol viewer">
      <div className="symbol-viewer-toolbar">
        <div className="symbol-viewer-title">
          {selected ? (
            <>
              <strong>{selected.label}</strong>
              <span className="symbol-format-badge">{selected.format}</span>
              {conditionText(selected) ? <span className="symbol-viewer-condition">{conditionText(selected)}</span> : null}
            </>
          ) : (
            <strong>No viewable drawing</strong>
          )}
        </div>
        <div className="symbol-viewer-zoom" role="group" aria-label="Zoom">
          <button type="button" className="action-button secondary compact" aria-label="Zoom out" disabled={zoom <= ZOOM_MIN} onClick={() => setZoom((current) => stepZoom(current, -1))}>−</button>
          <button type="button" className="action-button secondary compact" onClick={() => setZoom(1)} aria-label="Fit to view">Fit</button>
          <button type="button" className="action-button secondary compact" aria-label="Zoom in" disabled={zoom >= ZOOM_MAX} onClick={() => setZoom((current) => stepZoom(current, 1))}>+</button>
          <span className="symbol-viewer-zoom-level" role="status" aria-label="Zoom level">{Math.round(zoom * 100)}%</span>
        </div>
      </div>

      {overlayAvailable ? (
        <div className="symbol-viewer-overlays" role="group" aria-label="Overlays">
          {OVERLAY_TOGGLES.map((toggle) => (
            <button
              key={toggle.key}
              type="button"
              className={`symbol-overlay-toggle${overlays[toggle.key] ? ' active' : ''}`}
              aria-pressed={overlays[toggle.key]}
              onClick={() => toggleOverlay(toggle.key)}
            >
              {toggle.label}
            </button>
          ))}
          {overlays.connections ? (
            <span className="symbol-overlay-legend">
              <span className="legend-swatch legend-piping" aria-hidden="true" /> piping
              <span className="legend-swatch legend-signal" aria-hidden="true" /> signal
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="symbol-viewer-stage" tabIndex={0} role="region" aria-label="Symbol drawing (scrolls when zoomed)">
        {selected && selected.viewable ? (
          overlayAvailable ? (
            <OverlayDrawing
              symbol={symbol}
              representation={selected}
              frame={frame}
              overlays={overlays}
              highlightIndex={highlightIndex}
              zoom={zoom}
            />
          ) : (
            <img
              className="symbol-viewer-image"
              src={selected.url}
              style={{ width: `${zoom * 100}%`, height: `${zoom * 100}%` }}
              alt={`${selected.label} of ${symbol?.name || symbol?.catalogSymbolId || 'symbol'}${selected.condition ? `, ${selected.condition}` : ''}`}
            />
          )
        ) : (
          <p className="symbol-viewer-empty">
            {items.length
              ? 'None of this symbol’s formats can be viewed here. Use Download to get them.'
              : 'This symbol has no drawing to show.'}
          </p>
        )}
      </div>

      {items.length ? (
        <div className="symbol-format-strip">
          <h4 id="symbol-format-strip-title">Formats and states · click to view</h4>
          <ul className="symbol-tile-list" aria-labelledby="symbol-format-strip-title">
            {items.map((item) => (
              <RepresentationTile key={item.key} item={item} selected={item.key === selectedKey} onSelect={onSelectKey} />
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

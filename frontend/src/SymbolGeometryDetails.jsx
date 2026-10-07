import React from 'react';
import {
  connectionPointSummary,
  connectionPointText,
  labelTemplateList,
  stateVariantList,
  symbolAttribution
} from './symbolGeometry.js';

// The detail panel's account of an imported drawing: connection points, label
// templates, an option (state) gallery and the source's attribution. It renders
// nothing for a symbol that carries none of them.
export default function SymbolGeometryDetails({ symbol }) {
  const connections = connectionPointSummary(symbol);
  const labels = labelTemplateList(symbol);
  const variants = stateVariantList(symbol);
  const attribution = symbolAttribution(symbol);
  if (!connections.count && !labels.length && !variants.length && !attribution) return null;

  return (
    <section className="symbol-geometry-details" aria-label="Drawing geometry and states">
      {connections.count || labels.length ? (
        <div className="fact-grid detail-list">
          {connections.count ? (
            <div className="fact-card">
              <span>Connection points</span>
              <strong>{connectionPointText(connections)}</strong>
            </div>
          ) : null}
          {labels.length ? (
            <div className="fact-card">
              <span>Label slots</span>
              <strong>{labels.map((slot) => slot.label).join(', ')}</strong>
            </div>
          ) : null}
        </div>
      ) : null}
      {labels.some((slot) => slot.template) ? (
        <div className="copy-block">
          <h4>Label templates</h4>
          <dl className="label-template-list">
            {labels
              .filter((slot) => slot.template)
              .map((slot) => (
                <React.Fragment key={slot.label}>
                  <dt>Label {slot.label}{slot.lines ? ` · ${slot.lines} line${slot.lines === 1 ? '' : 's'}` : ''}</dt>
                  <dd><pre>{slot.template}</pre></dd>
                </React.Fragment>
              ))}
          </dl>
        </div>
      ) : null}
      {variants.length ? (
        <div className="copy-block">
          <h4>States ({variants.length})</h4>
          <ul className="state-variant-gallery">
            {variants.map((variant) => (
              <li key={variant.index}>
                <figure>
                  <img
                    src={variant.url}
                    alt={`State ${variant.index}${variant.condition ? `: ${variant.condition}` : ''}`}
                    loading="lazy"
                  />
                  <figcaption>
                    <strong>State {variant.index}</strong>
                    <span>{variant.condition || 'No condition stated by the source'}</span>
                  </figcaption>
                </figure>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {attribution ? (
        <div className="copy-block">
          <h4>Source and attribution</h4>
          <p>{attribution}</p>
        </div>
      ) : null}
    </section>
  );
}

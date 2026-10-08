import React from 'react';
import { Link } from 'react-router-dom';
import {
  connectionRows,
  detailsSections,
  formatDateTime,
  labelSlotRows,
  mappingDisplay,
  provenanceRows,
  rightsRows,
  typeCodes
} from './symbolDetailsModel.js';
import { detailsPath } from './symbolDetailsRoute.js';
import { stateVariantList } from './symbolGeometry.js';

export function KeyValueList({ rows, className = '' }) {
  if (!rows.length) return null;
  return (
    <dl className={`symbol-kv ${className}`.trim()}>
      {rows.map((item) => (
        <React.Fragment key={item.label}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

export function PlaceholderBadge() {
  return <span className="symbol-placeholder-badge">Placeholder wording · final text pending</span>;
}

function EmptyNote({ children }) {
  return <p className="symbol-details-empty">{children}</p>;
}

function RelatedThumbnails({ items, label, resolveUrl }) {
  return (
    <ul className="symbol-related-grid" aria-label={label}>
      {items.map((item) => (
        <li key={item.reference}>
          <Link className="symbol-related-card" to={detailsPath(item.reference)} replace>
            <span className="symbol-related-thumb">
              {item.previewUrl ? <img src={resolveUrl(item.previewUrl)} alt={`Preview of ${item.name || item.reference}`} loading="lazy" /> : null}
            </span>
            <strong>{item.catalogSymbolId || item.reference}</strong>
            {item.name ? <span>{item.name}</span> : null}
            {item.pack ? <span className="symbol-related-pack">{item.pack}</span> : null}
          </Link>
        </li>
      ))}
    </ul>
  );
}

// Classification & mappings: governed classifications, external mappings,
// the same concept in other packs, and the DEXPI class's other symbols.
export function ClassificationPanel({ details, status, resolveUrl, onShowAllInClass }) {
  const hasAny = details.classifications.length || details.externalMappings.length
    || details.sameConcept.length || details.sameClass;
  if (status === 'loading') return <EmptyNote>Loading classifications and mappings…</EmptyNote>;
  if (status === 'error') return <EmptyNote>Classifications and mappings could not be loaded.</EmptyNote>;
  if (!hasAny) {
    return (
      <EmptyNote>
        {status === 'unavailable'
          ? 'Classifications and mappings are not shown for this symbol.'
          : 'No governed classifications or external mappings are recorded for this symbol.'}
      </EmptyNote>
    );
  }
  return (
    <div className="symbol-tab-sections">
      {details.classifications.length ? (
        <section aria-labelledby="symbol-classifications-title">
          <h4 id="symbol-classifications-title">Governed classifications</h4>
          <table className="symbol-table">
            <thead><tr><th scope="col">Scheme</th><th scope="col">Node path</th><th scope="col">Method</th></tr></thead>
            <tbody>
              {details.classifications.map((item, index) => (
                <tr key={`${item.scheme}-${item.path}-${index}`}>
                  <td>{item.scheme}</td>
                  <td>{item.path}</td>
                  <td>{item.method}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      {details.externalMappings.length ? (
        <section aria-labelledby="symbol-mappings-title">
          <h4 id="symbol-mappings-title">External mappings</h4>
          <table className="symbol-table">
            <thead><tr><th scope="col">System</th><th scope="col">Linked identifier</th><th scope="col">Relation</th></tr></thead>
            <tbody>
              {details.externalMappings.map((item, index) => {
                const display = mappingDisplay(item.identifier);
                return (
                  <tr key={`${item.system}-${item.identifier}-${index}`}>
                    <td>{item.system}</td>
                    <td>
                      {display.href
                        ? <a href={display.href} target="_blank" rel="noreferrer noopener">{display.text}</a>
                        : display.text}
                      {item.label ? <span className="symbol-table-note"> {item.label}</span> : null}
                    </td>
                    <td>{item.relation}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      ) : null}
      {details.sameConcept.length ? (
        <section aria-labelledby="symbol-concept-title">
          <h4 id="symbol-concept-title">Other drawings of this concept</h4>
          <RelatedThumbnails items={details.sameConcept} label="Other drawings of this concept" resolveUrl={resolveUrl} />
        </section>
      ) : null}
      {details.sameClass ? (
        <section aria-labelledby="symbol-class-title">
          <h4 id="symbol-class-title">Other symbols in DEXPI class {details.sameClass.className}</h4>
          {details.sameClass.items.length
            ? <RelatedThumbnails items={details.sameClass.items} label={`Other symbols in DEXPI class ${details.sameClass.className}`} resolveUrl={resolveUrl} />
            : null}
          <button type="button" className="action-button secondary compact" onClick={() => onShowAllInClass(details.sameClass.className)}>
            See all {details.sameClass.total} in the catalog
          </button>
        </section>
      ) : null}
    </div>
  );
}

export function DetailsPanel({ symbol }) {
  const sections = detailsSections(symbol);
  if (!sections.length) return <EmptyNote>No further details are recorded for this symbol.</EmptyNote>;
  return (
    <div className="symbol-tab-columns">
      {sections.map((section) => (
        <section key={section.id} aria-labelledby={`symbol-details-${section.id}`}>
          <h4 id={`symbol-details-${section.id}`}>{section.title}</h4>
          <KeyValueList rows={section.rows} />
        </section>
      ))}
    </div>
  );
}

// The table's rows highlight their point in the viewer on hover or focus.
export function ConnectionsPanel({ symbol, onHighlight }) {
  const rows = connectionRows(symbol);
  if (!rows.length) return <EmptyNote>This symbol has no recorded connection points.</EmptyNote>;
  return (
    <table className="symbol-table symbol-connections-table">
      <caption className="visually-hidden">Connection points; hover or focus a row to highlight it in the viewer</caption>
      <thead>
        <tr>
          <th scope="col">#</th>
          <th scope="col">Kind</th>
          <th scope="col">X mm</th>
          <th scope="col">Y mm</th>
          <th scope="col">Direction</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr
            key={row.index}
            tabIndex={0}
            onMouseEnter={() => onHighlight(row.index)}
            onMouseLeave={() => onHighlight(0)}
            onFocus={() => onHighlight(row.index)}
            onBlur={() => onHighlight(0)}
          >
            <th scope="row">{row.index}</th>
            <td>{row.kind}</td>
            <td>{row.x}</td>
            <td>{row.y}</td>
            <td>{row.direction}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function LabelsStatesPanel({ symbol, resolveUrl, onViewState }) {
  const slots = labelSlotRows(symbol);
  const codes = typeCodes(symbol);
  const states = stateVariantList(symbol);
  if (!slots.length && !codes && !states.length) return <EmptyNote>No label slots or states are recorded for this symbol.</EmptyNote>;
  return (
    <div className="symbol-tab-sections">
      {slots.length ? (
        <section aria-labelledby="symbol-label-slots-title">
          <h4 id="symbol-label-slots-title">Label slots</h4>
          <table className="symbol-table">
            <thead>
              <tr>
                <th scope="col">Label</th>
                {slots.some((slot) => slot.lines) ? <th scope="col">Lines</th> : null}
                {slots.some((slot) => slot.box) ? <th scope="col">Box</th> : null}
                {slots.some((slot) => slot.template) ? <th scope="col">Template</th> : null}
              </tr>
            </thead>
            <tbody>
              {slots.map((slot) => (
                <tr key={slot.index}>
                  <th scope="row">{slot.index}</th>
                  {slots.some((other) => other.lines) ? <td>{slot.lines || ''}</td> : null}
                  {slots.some((other) => other.box) ? <td>{slot.box}</td> : null}
                  {slots.some((other) => other.template) ? <td>{slot.template ? <pre className="symbol-template">{slot.template}</pre> : null}</td> : null}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      {codes ? (
        <section aria-labelledby="symbol-type-codes-title">
          <h4 id="symbol-type-codes-title">Type codes</h4>
          <p>{codes}</p>
        </section>
      ) : null}
      {states.length ? (
        <section aria-labelledby="symbol-states-title">
          <h4 id="symbol-states-title">States ({states.length})</h4>
          <ul className="symbol-state-list">
            {states.map((state) => (
              <li key={state.index}>
                <img src={resolveUrl(state.url)} alt={`State ${state.index}${state.condition ? `: ${state.condition}` : ''}`} loading="lazy" />
                <div>
                  <strong>State {state.index}</strong>
                  <span>{state.condition || 'No condition stated by the source'}</span>
                </div>
                <button type="button" className="action-button secondary compact" onClick={() => onViewState(`state-${state.index}`)}>
                  View<span className="visually-hidden"> state {state.index} in the viewer</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

export function SourcePanel({ symbol, details, status }) {
  const rights = rightsRows(details);
  const provenance = provenanceRows(symbol, details);
  if (status === 'loading' && !rights.length) return <EmptyNote>Loading rights and provenance…</EmptyNote>;
  if (!rights.length && !provenance.length) return <EmptyNote>No source or rights information is recorded for this symbol.</EmptyNote>;
  return (
    <div className="symbol-tab-columns">
      {rights.length ? (
        <section aria-labelledby="symbol-rights-title">
          <h4 id="symbol-rights-title">Rights</h4>
          <KeyValueList rows={rights} />
          {details.rights?.attributionIsPlaceholder ? <PlaceholderBadge /> : null}
        </section>
      ) : null}
      {provenance.length ? (
        <section aria-labelledby="symbol-provenance-title">
          <h4 id="symbol-provenance-title">Provenance</h4>
          <KeyValueList rows={provenance} className="symbol-kv-mono" />
        </section>
      ) : null}
    </div>
  );
}

export function HistoryPanel({ details, status }) {
  if (status === 'loading') return <EmptyNote>Loading history…</EmptyNote>;
  if (!details.history.length) return <EmptyNote>No revision or approval history is recorded for this symbol.</EmptyNote>;
  return (
    <ol className="symbol-history-list">
      {details.history.map((event, index) => (
        <li key={`${event.kind}-${event.at}-${index}`}>
          <time dateTime={event.at}>{formatDateTime(event.at)}</time>
          <span>{event.label}</span>
        </li>
      ))}
    </ol>
  );
}

import React from 'react';
import source from '../../backend/symgov_backend/data/ics-source.json';

export default function SupportDataSources() {
  return (
    <section className="glass-panel pane support-panel" aria-labelledby="classification-data-sources">
      <div className="section-heading">
        <h3 id="classification-data-sources">Classification standard / Data sources</h3>
        <p>International Classification for Standards (ICS)</p>
      </div>
      <p>{source.attribution}</p>
      <p>{source.clarification}</p>
      <p>ISO Open Data source update: {source.source_update_year}; edition {source.edition}, first published {source.publication_year}. This is source metadata, not a claim that a newer edition has been checked or activated.</p>
      <ul>
        <li><a href={source.browse_url}>Browse the ISO ICS catalogue</a></li>
        <li><a href={source.page_url}>ISO Open Data</a></li>
        <li><a href={source.license_url}>Open Data Commons Attribution License (ODC-By) v1.0</a></li>
      </ul>
    </section>
  );
}

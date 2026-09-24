import React from 'react';
import source from '../../backend/symgov_backend/data/ics-source.json';
import dexpiSource from '../../backend/symgov_backend/data/dexpi-source.json';

export default function SupportDataSources() {
  return (
    <>
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
      <section className="glass-panel pane support-panel" aria-labelledby="symbol-library-data-sources">
        <div className="section-heading">
          <h3 id="symbol-library-data-sources">Symbol library / Data sources</h3>
          <p>DEXPI TrainingTestCases (package {dexpiSource.package_code})</p>
        </div>
        <p>{dexpiSource.attribution}</p>
        <p>{dexpiSource.modifications}</p>
        <p>{dexpiSource.clarification}</p>
        <p>{dexpiSource.limitation}</p>
        <ul>
          <li><a href={dexpiSource.source_url}>DEXPI TrainingTestCases repository</a></li>
          <li><a href={dexpiSource.page_url}>DEXPI</a></li>
          <li><a href={dexpiSource.license_url}>Creative Commons Attribution 4.0 International (CC BY 4.0)</a></li>
        </ul>
      </section>
    </>
  );
}

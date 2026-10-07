import React, { useEffect, useState } from 'react';
import source from '../../backend/symgov_backend/data/ics-source.json';
import dexpiSource from '../../backend/symgov_backend/data/dexpi-source.json';
import { listPublishedDataSources } from './api.js';

// One section per imported library whose rights are approved. Every word of
// it, the attribution included, comes from the library's own source package and
// rights record: nothing about a library is written into this file.
export function ImportedLibrarySources({ sources }) {
  return (
    <>
      {sources.map((source) => {
        const headingId = `library-source-${String(source.packageCode).toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
        return (
          <section key={source.packageCode} className="glass-panel pane support-panel" aria-labelledby={headingId}>
            <div className="section-heading">
              <h3 id={headingId}>{source.title}</h3>
              <p>
                Symbol library / Data sources (package {source.packageCode}
                {source.releaseVersion ? `, ${source.releaseVersion}` : ''})
              </p>
            </div>
            <p className="library-source-attribution">{source.attributionText}</p>
            {source.attributionIsPlaceholder ? (
              <p role="note">This attribution wording is provisional until the licensor supplies the final text.</p>
            ) : null}
            <ul>
              {source.licensor ? <li>Licensed by {source.licensor}</li> : null}
              {source.creator ? <li>Created by {source.creator}</li> : null}
              <li>{source.publishedSymbols} published symbols</li>
              {source.sourceUri ? <li><a href={source.sourceUri}>Source repository</a></li> : null}
              {source.organisationUrl ? <li><a href={source.organisationUrl}>{source.organisationUrl.replace(/^https?:\/\//, '')}</a></li> : null}
            </ul>
          </section>
        );
      })}
    </>
  );
}

export default function SupportDataSources() {
  const [librarySources, setLibrarySources] = useState([]);

  useEffect(() => {
    let cancelled = false;
    listPublishedDataSources()
      .then((result) => {
        if (!cancelled) setLibrarySources(result.sources);
      })
      .catch(() => {
        if (!cancelled) setLibrarySources([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
      <ImportedLibrarySources sources={librarySources} />
    </>
  );
}

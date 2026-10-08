import React, { useEffect, useMemo, useRef, useState } from 'react';
import FavouriteButton from './FavouriteButton.js';
import SymbolViewer from './SymbolViewer.jsx';
import {
  ClassificationPanel,
  ConnectionsPanel,
  DetailsPanel,
  HistoryPanel,
  KeyValueList,
  LabelsStatesPanel,
  PlaceholderBadge,
  SourcePanel
} from './SymbolDetailsTabs.jsx';
import { catalogScopeBadge } from './catalogWorkbench.js';
import {
  attributionBlock,
  availableTabs,
  connectionRows,
  glanceRows,
  identifierList
} from './symbolDetailsModel.js';
import { DETAILS_TABS, resolveDetailsTab } from './symbolDetailsRoute.js';
import { buildRepresentations, downloadChoices } from './symbolRepresentations.js';

export function ExpandIcon() {
  return (
    <svg className="button-icon" width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M9.5 2H14v4.5M6.5 14H2V9.5M14 2 9 7M2 14l5-5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

async function copyText(value) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    // Fall through to the selection fallback.
  }
  try {
    const field = document.createElement('textarea');
    field.value = value;
    field.setAttribute('readonly', '');
    field.style.position = 'fixed';
    field.style.opacity = '0';
    document.body.appendChild(field);
    field.select();
    const copied = document.execCommand('copy');
    field.remove();
    return copied;
  } catch {
    return false;
  }
}

function Identifier({ item }) {
  const [state, setState] = useState('');
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  async function copy() {
    const copied = await copyText(item.copy);
    setState(copied ? 'copied' : 'failed');
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState(''), 2000);
  }
  return (
    <div className="symbol-identifier">
      <dt>{item.label}</dt>
      <dd>
        <span className="symbol-identifier-value">
          <code>{item.value}</code>
          {item.secondary ? <code className="symbol-identifier-secondary">{item.secondary}</code> : null}
        </span>
        <button type="button" className="action-button secondary compact" onClick={copy} aria-label={`Copy ${item.label}`}>
          {state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : 'Copy'}
        </button>
      </dd>
    </div>
  );
}

function Badge({ children, modifier = '' }) {
  if (!children) return null;
  return <span className={`symbol-badge ${modifier}`.trim()}>{children}</span>;
}

// The tablist follows the ARIA pattern: roving tabindex, arrow keys move and
// activate, Home and End jump, Tab leaves the group.
function DetailsTabList({ tabs, active, onSelect }) {
  function onKeyDown(event) {
    const index = tabs.findIndex((tab) => tab.id === active);
    let next = null;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = tabs.length - 1;
    if (next === null) return;
    event.preventDefault();
    onSelect(tabs[next].id);
    document.getElementById(`symbol-details-tab-${tabs[next].id}`)?.focus();
  }
  return (
    <div className="symbol-details-tablist" role="tablist" aria-label="Symbol detail sections" onKeyDown={onKeyDown}>
      {tabs.map((tab) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            id={`symbol-details-tab-${tab.id}`}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls="symbol-details-tabpanel"
            tabIndex={selected ? 0 : -1}
            className={`symbol-details-tab${selected ? ' active' : ''}`}
            onClick={() => onSelect(tab.id)}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

function NotFound({ message, onBack }) {
  return (
    <section className="symbol-details-view" aria-label="Symbol details">
      <div className="glass-panel pane symbol-details-notice" role="status">
        <h3>{message.title}</h3>
        <p>{message.body}</p>
        <button type="button" className="action-button secondary compact" onClick={onBack}>Back to catalog</button>
      </div>
    </section>
  );
}

// The full-page Details view for one published symbol. It sits on the Catalog
// route, in place of the filters, results and side panel; the Catalog stays
// mounted underneath, so going back finds it as it was.
export default function SymbolDetailsView({
  symbol,
  symbolState,
  details,
  detailsStatus,
  tab,
  onTabChange,
  navigation,
  onBack,
  onPrevious,
  onNext,
  actions,
  statusMessages = [],
  commentsPanel,
  resolveUrl,
  origin,
  onShowAllInClass
}) {
  const representations = useMemo(() => buildRepresentations(symbol, { resolveUrl }), [symbol, resolveUrl]);
  const [selectedKey, setSelectedKey] = useState(representations.defaultKey);
  const [highlightIndex, setHighlightIndex] = useState(0);
  const choices = useMemo(() => downloadChoices(symbol), [symbol]);
  const [choiceValue, setChoiceValue] = useState(() => choices[0]?.value || '');
  const symbolKey = symbol?.id || '';

  // A new symbol starts from its own primary drawing and first download.
  useEffect(() => {
    setSelectedKey(representations.defaultKey);
    setHighlightIndex(0);
  }, [symbolKey, representations.defaultKey]);
  // Keyed by the choices' values, not the array: a favourite toggled on the
  // symbol replaces the row and must not reset the format picked.
  const choiceKey = choices.map((choice) => choice.value).join('|');
  useEffect(() => {
    setChoiceValue(choices[0]?.value || '');
  }, [symbolKey, choiceKey]);

  if (!symbol) {
    if (symbolState === 'error') {
      return (
        <NotFound
          onBack={onBack}
          message={{ title: 'Symbol not found', body: 'This symbol does not exist or is not available to you.' }}
        />
      );
    }
    return (
      <section className="symbol-details-view" aria-label="Symbol details" aria-busy="true">
        <p className="inline-status info" role="status">Loading symbol…</p>
      </section>
    );
  }

  const tabIds = availableTabs(symbol, details);
  const activeTab = resolveDetailsTab(tab, tabIds);
  const tabs = DETAILS_TABS.filter((item) => tabIds.includes(item.id)).map((item) => {
    if (item.id === 'connections') return { ...item, label: `Connection points (${connectionRows(symbol).length})` };
    if (item.id === 'comments') {
      const count = Number(symbol.commentCount || 0);
      return count > 0 ? { ...item, label: `Comments (${count})` } : item;
    }
    return item;
  });
  const catalogId = symbol.catalogSymbolId || '';
  const name = symbol.name || '';
  const title = [catalogId, name].filter(Boolean).join(' · ') || symbol.id;
  const identifiers = identifierList(symbol, origin);
  const glance = glanceRows(symbol);
  const attribution = attributionBlock(symbol, details);
  const scope = catalogScopeBadge(symbol);
  const selectedChoice = choices.find((choice) => choice.value === choiceValue) || null;

  function viewState(key) {
    setSelectedKey(key);
    document.getElementById('symbol-viewer-anchor')?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }

  return (
    <section className="symbol-details-view" aria-label={`Details for ${title}`}>
      <div className="symbol-details-topbar">
        <nav className="symbol-breadcrumb" aria-label="Breadcrumb">
          <ol>
            <li><button type="button" className="text-button" onClick={onBack}>Catalog</button></li>
            {symbol.pack ? <li><span>{symbol.pack}</span></li> : null}
            <li><span aria-current="page">{catalogId || symbol.id}</span></li>
          </ol>
        </nav>
        <div className="symbol-details-stepper">
          <button type="button" className="action-button secondary compact" onClick={onBack}>Back to catalog</button>
          <button
            type="button"
            className="action-button secondary compact"
            disabled={!navigation.hasPrevious}
            title={navigation.enabled ? undefined : 'Opened from a link, so there are no results to step through.'}
            onClick={onPrevious}
          >
            ‹ Previous result
          </button>
          {navigation.enabled && navigation.position ? (
            <span className="symbol-details-position" role="status">{navigation.position} of {navigation.total}</span>
          ) : null}
          <button
            type="button"
            className="action-button secondary compact"
            disabled={!navigation.hasNext}
            title={navigation.enabled ? undefined : 'Opened from a link, so there are no results to step through.'}
            onClick={onNext}
          >
            Next result ›
          </button>
        </div>
      </div>

      <section className="glass-panel pane symbol-header-card" aria-labelledby="symbol-details-title">
        <p className="eyebrow">Approved symbol</p>
        <h2 id="symbol-details-title">{title}</h2>
        {symbol.summary ? <p className="symbol-header-summary">{symbol.summary}</p> : null}
        <div className="symbol-badge-row">
          <Badge modifier={`scope-${scope.modifier}`}>{scope.label}</Badge>
          <Badge>{symbol.status || 'Published'}</Badge>
          <Badge>{symbol.revision ? `Revision ${symbol.revision}` : ''}</Badge>
          <Badge>{symbol.category}</Badge>
          <Badge>{symbol.discipline}</Badge>
          <Badge>{symbol.pack}</Badge>
        </div>
        <div className="symbol-header-actions">
          <label className="catalog-download-format">
            <span>Format</span>
            <select
              aria-label="Download format"
              value={choiceValue}
              disabled={!choices.length || actions.preparingDownload}
              onChange={(event) => setChoiceValue(event.target.value)}
            >
              {choices.length ? null : <option value="">No downloads</option>}
              {choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
            </select>
          </label>
          <button
            type="button"
            className="action-button compact"
            disabled={!selectedChoice || actions.preparingDownload}
            onClick={() => actions.onDownload(selectedChoice)}
          >
            {actions.preparingDownload ? 'Preparing…' : 'Download'}
          </button>
          <button type="button" className="action-button secondary compact" onClick={actions.onAddToClipboard}>Add to clipboard</button>
          <span className="symbol-header-favourite">
            <FavouriteButton
              symbol={symbol}
              pressed={actions.favourite.pressed}
              pending={actions.favourite.pending}
              disabled={actions.favourite.disabled}
              onToggle={actions.favourite.onToggle}
            />
            <span aria-hidden="true">Favourite</span>
          </span>
          <button type="button" className="action-button secondary compact" onClick={actions.onComment}>Comment</button>
          <button type="button" className="action-button compact" onClick={actions.onSendForReview}>Send for review</button>
        </div>
        {statusMessages.map((message) => (
          <p
            key={`${message.mode}-${message.message}`}
            className={`inline-status ${message.mode || 'info'}`}
            role={message.mode === 'error' ? 'alert' : 'status'}
          >
            {message.message}
          </p>
        ))}
        {identifiers.length ? (
          <dl className="symbol-identifiers" aria-label="Identifiers">
            {identifiers.map((item) => <Identifier key={item.key} item={item} />)}
          </dl>
        ) : null}
      </section>

      <div className="symbol-details-main" id="symbol-viewer-anchor">
        <SymbolViewer
          symbol={symbol}
          representations={representations}
          selectedKey={selectedKey}
          onSelectKey={setSelectedKey}
          highlightIndex={highlightIndex}
        />
        <section className="glass-panel pane symbol-glance-card" aria-labelledby="symbol-glance-title">
          <h3 id="symbol-glance-title">At a glance</h3>
          {glance.length ? <KeyValueList rows={glance} /> : <p className="symbol-details-empty">No register details are recorded for this symbol.</p>}
          {attribution ? (
            <div className="symbol-attribution">
              <h4>Source and attribution</h4>
              {attribution.placeholder ? <PlaceholderBadge /> : null}
              {attribution.attribution ? <p>{attribution.attribution}</p> : null}
              <KeyValueList rows={[
                attribution.licensor ? { label: 'Licensor', value: attribution.licensor } : null,
                attribution.creator ? { label: 'Creator', value: attribution.creator } : null
              ].filter(Boolean)} />
            </div>
          ) : null}
        </section>
      </div>

      <section className="glass-panel pane symbol-details-tabs-card">
        <DetailsTabList tabs={tabs} active={activeTab} onSelect={onTabChange} />
        <div
          id="symbol-details-tabpanel"
          role="tabpanel"
          aria-labelledby={`symbol-details-tab-${activeTab}`}
          tabIndex={0}
          className="symbol-details-tabpanel"
        >
          {activeTab === 'classification' ? (
            <ClassificationPanel details={details} status={detailsStatus} resolveUrl={resolveUrl} onShowAllInClass={onShowAllInClass} />
          ) : null}
          {activeTab === 'details' ? <DetailsPanel symbol={symbol} /> : null}
          {activeTab === 'connections' ? <ConnectionsPanel symbol={symbol} onHighlight={setHighlightIndex} /> : null}
          {activeTab === 'labels' ? <LabelsStatesPanel symbol={symbol} resolveUrl={resolveUrl} onViewState={viewState} /> : null}
          {activeTab === 'source' ? <SourcePanel symbol={symbol} details={details} status={detailsStatus} /> : null}
          {activeTab === 'history' ? <HistoryPanel details={details} status={detailsStatus} /> : null}
          {activeTab === 'comments' ? commentsPanel : null}
        </div>
      </section>
    </section>
  );
}

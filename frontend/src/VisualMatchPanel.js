import { createElement as h, useCallback, useEffect, useRef, useState } from 'react';

import { matchSymbolImage } from './api.js';
import {
  VISUAL_ACCEPTED_TYPES,
  VISUAL_FILTER_KEYS,
  VISUAL_MATCH_DISCLOSURE,
  bandCopy,
  buildVisualMatchBody,
  fileToBase64,
  firstImageFile,
  groupHeading,
  resultNotices,
  validateImageFile,
  visualMatchErrorMessage
} from './visualMatch.js';

const FILTER_LABELS = {
  catalogDisciplines: 'Discipline',
  catalogCategories: 'Category',
  useCases: 'Use case',
  availableFormats: 'Format'
};

// The Catalog's Visual match tab: add an image of a symbol (paste, drop or choose), optionally narrow the
// search, and get ranked groups of interchangeable Catalog symbols with a plain-language confidence band.
// Nothing is sent until "Find matches" is pressed, because the image leaves Symgov (see the disclosure).
export default function VisualMatchPanel({ filterOptions = [], onOpenSymbol, renderPreview, match = matchSymbolImage }) {
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState('');
  const [fileError, setFileError] = useState('');
  const [filters, setFilters] = useState({});
  const [includeWeak, setIncludeWeak] = useState(false);
  const [state, setState] = useState({ status: 'idle', response: null, error: '' });
  const sequence = useRef(0);
  const fileInput = useRef(null);

  const choose = useCallback((candidate) => {
    const verdict = validateImageFile(candidate);
    if (!verdict.ok) {
      setFileError(verdict.message);
      return;
    }
    setFileError('');
    setFile(candidate);
    setState({ status: 'idle', response: null, error: '' });
  }, []);

  useEffect(() => {
    if (!file || typeof URL === 'undefined' || !URL.createObjectURL) {
      setPreviewUrl('');
      return undefined;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL?.(url);
  }, [file]);

  // Pasting anywhere on the tab works, but not while typing in a field.
  useEffect(() => {
    function onPaste(event) {
      const tag = event.target?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || event.target?.isContentEditable) return;
      const pasted = firstImageFile(event.clipboardData);
      if (pasted) {
        event.preventDefault();
        choose(pasted);
      }
    }
    document.addEventListener('paste', onPaste);
    return () => document.removeEventListener('paste', onPaste);
  }, [choose]);

  async function find() {
    if (!file) return;
    const ticket = ++sequence.current;
    setState({ status: 'working', response: null, error: '' });
    try {
      const imageBase64 = await fileToBase64(file);
      const response = await match(buildVisualMatchBody({ imageBase64, filters, includeWeak }));
      if (ticket === sequence.current) setState({ status: 'done', response, error: '' });
    } catch (error) {
      if (ticket === sequence.current) {
        setState({ status: 'error', response: null, error: visualMatchErrorMessage(error?.status ?? 0, error?.message) });
      }
    }
  }

  function toggleFilter(key, value) {
    setFilters((current) => {
      const ticked = new Set(current[key] || []);
      if (ticked.has(value)) ticked.delete(value); else ticked.add(value);
      return { ...current, [key]: [...ticked] };
    });
  }

  const working = state.status === 'working';
  const response = state.response;
  const options = filterOptions.filter((option) => VISUAL_FILTER_KEYS.includes(option.key) && option.values?.length);

  return h('section', { className: 'visual-match-panel', 'aria-labelledby': 'visual-match-heading' },
    h('div', { className: 'section-heading' },
      h('h3', { id: 'visual-match-heading' }, 'Visual match'),
      h('p', null, 'Find Catalog symbols that look like one in your drawing.')),
    h('p', { className: 'visual-match-disclosure copy-block' }, VISUAL_MATCH_DISCLOSURE),
    h('div', {
      className: `visual-match-drop${file ? ' has-image' : ''}`,
      onDragOver: (event) => event.preventDefault(),
      onDrop: (event) => {
        event.preventDefault();
        const dropped = firstImageFile(event.dataTransfer);
        if (dropped) choose(dropped);
      }
    },
      previewUrl
        ? h('img', { className: 'visual-match-query-image', src: previewUrl, alt: 'The image you are matching' })
        : h('p', { className: 'muted-text' }, 'Paste an image here, drop one, or choose a file. PNG or JPEG, up to 8 MB. Crop to a single symbol for the best results.'),
      h('div', { className: 'visual-match-actions' },
        h('input', {
          ref: fileInput, type: 'file', accept: VISUAL_ACCEPTED_TYPES.join(','), className: 'visual-match-file',
          id: 'visual-match-file', 'aria-label': 'Choose an image to match',
          onChange: (event) => { if (event.target.files?.[0]) choose(event.target.files[0]); event.target.value = ''; }
        }),
        h('button', { type: 'button', className: 'action-button secondary', onClick: () => fileInput.current?.click() },
          file ? 'Choose a different image' : 'Choose an image'),
        h('button', { type: 'button', className: 'action-button', disabled: !file || working, onClick: find },
          working ? 'Matching…' : 'Find matches'))),
    fileError ? h('p', { className: 'inline-status error', role: 'alert' }, fileError) : null,
    options.length
      ? h('details', { className: 'visual-match-filters' },
          h('summary', null, 'Narrow the search'),
          options.map((option) => h('fieldset', { key: option.key, className: 'visual-match-filter' },
            h('legend', null, FILTER_LABELS[option.key] || option.label),
            option.values.map((entry) => h('label', { key: entry.value, className: 'visual-match-filter-option' },
              h('input', {
                type: 'checkbox', checked: (filters[option.key] || []).includes(entry.value),
                onChange: () => toggleFilter(option.key, entry.value)
              }),
              ' ', entry.value)))))
      : null,
    h('label', { className: 'visual-match-weak' },
      h('input', { type: 'checkbox', checked: includeWeak, onChange: (event) => setIncludeWeak(event.target.checked) }),
      ' Include weak matches'),
    h('div', { className: 'visual-match-results', 'aria-live': 'polite', 'aria-busy': working || undefined },
      working ? h('p', { className: 'muted-text', role: 'status' }, 'Matching your image against the Catalog…') : null,
      state.status === 'error' ? h('p', { className: 'inline-status error', role: 'alert' }, state.error) : null,
      response ? [
        ...resultNotices(response).map((notice) => h('p', { key: notice, className: 'inline-status' }, notice)),
        response.groups.length === 0
          ? h('p', { key: 'none', className: 'copy-block' }, response.notice || 'No close match found among the symbols you can search.')
          : null,
        ...response.groups.map((group, index) => h('section', {
          key: group.rank, className: `visual-match-group band-${group.band}`, 'aria-labelledby': `visual-match-group-${group.rank}`
        },
          h('h4', { id: `visual-match-group-${group.rank}` }, groupHeading(group, index)),
          h('p', { className: 'muted-text' }, bandCopy(group.band).text),
          h('ul', { className: 'visual-match-symbols' },
            group.symbols.map((symbol) => h('li', { key: symbol.symbolId || symbol.displayId, className: 'visual-match-symbol' },
              renderPreview ? renderPreview(symbol) : null,
              h('div', null,
                h('strong', { className: 'visual-match-symbol-id' }, symbol.displayId),
                h('span', { className: 'visual-match-symbol-name' }, ` ${symbol.name}`)),
              h('button', {
                type: 'button', className: 'action-button secondary compact',
                'aria-label': `Open details for ${symbol.displayId} ${symbol.name}`,
                onClick: () => onOpenSymbol?.(symbol)
              }, 'Open details')))))),
        h('p', { key: 'count', className: 'muted-text' }, `Searched ${response.symbolsSearched} symbols.`)
      ] : null));
}

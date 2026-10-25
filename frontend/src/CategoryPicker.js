import { createElement, useState } from 'react';

// The Catalog's category picklist (2026-10-25), shared by the private drafts
// form and the public review editor. `categories` is the list in display
// order (built-in names, reviewer additions, then Unclassified).
//
// - A stored value that is not on the list stays selectable, marked as such,
//   so saving other fields never silently changes it.
// - With `onAdd`, the last option opens an inline "new category" row. `onAdd`
//   resolves to `{ added: true, name }` or `{ added: false, similarTo }`;
//   a near match is offered back before anything is added.
export const ADD_CATEGORY_OPTION = '__add_category__';

export function CategoryPicker({ id, value, onChange, categories, onAdd = null, required = false, disabled = false }) {
  const [adding, setAdding] = useState(false);
  const [draftName, setDraftName] = useState('');
  const [similarTo, setSimilarTo] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const listed = categories || [];
  const offList = value && !listed.includes(value);

  function closeAdd() {
    setAdding(false);
    setDraftName('');
    setSimilarTo('');
    setError('');
  }

  async function add(allowSimilar) {
    const name = draftName.trim();
    if (!name) {
      setError('Type a category name.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const result = await onAdd(name, { allowSimilar });
      if (!result.added && result.similarTo) {
        setSimilarTo(result.similarTo);
        return;
      }
      onChange(result.name);
      closeAdd();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Category could not be added.');
    } finally {
      setBusy(false);
    }
  }

  function useSimilar() {
    onChange(similarTo);
    closeAdd();
  }

  const select = createElement('select', {
    id,
    value: value || '',
    required,
    disabled: disabled || adding,
    onChange: (event) => {
      if (event.target.value === ADD_CATEGORY_OPTION) {
        setAdding(true);
        return;
      }
      onChange(event.target.value);
    },
  },
    createElement('option', { value: '' }, 'Choose a category'),
    offList ? createElement('option', { key: `current-${value}`, value }, `${value} (not on the list)`) : null,
    ...listed.map((name) => createElement('option', { key: name, value: name }, name)),
    onAdd ? createElement('option', { key: ADD_CATEGORY_OPTION, value: ADD_CATEGORY_OPTION }, '+ Add a new category…') : null,
  );

  if (!adding) {
    return select;
  }

  const inputId = `${id}-new`;
  return createElement('div', { className: 'category-picker-adding' },
    select,
    createElement('div', { className: 'category-picker-add-row' },
      createElement('label', { htmlFor: inputId, className: 'visually-hidden' }, 'New category name'),
      createElement('input', {
        id: inputId,
        type: 'text',
        maxLength: 80,
        autoFocus: true,
        placeholder: 'New category name',
        value: draftName,
        disabled: busy,
        onChange: (event) => {
          setDraftName(event.target.value);
          setSimilarTo('');
        },
        // Enter adds; it must not submit the surrounding form.
        onKeyDown: (event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            add(false);
          } else if (event.key === 'Escape') {
            event.preventDefault();
            closeAdd();
          }
        },
      }),
      similarTo
        ? null
        : createElement('button', { type: 'button', className: 'action-button compact primary', disabled: busy, onClick: () => add(false) },
          busy ? 'Adding…' : 'Add'),
      createElement('button', { type: 'button', className: 'action-button compact ghost', disabled: busy, onClick: closeAdd }, 'Cancel'),
    ),
    similarTo
      ? createElement('div', { className: 'category-picker-similar', role: 'status' },
        createElement('p', null, `“${draftName.trim()}” is close to “${similarTo}”, which is already on the list.`),
        createElement('button', { type: 'button', className: 'action-button compact primary', disabled: busy, onClick: useSimilar },
          `Use ${similarTo}`),
        createElement('button', { type: 'button', className: 'action-button compact', disabled: busy, onClick: () => add(true) },
          `Add “${draftName.trim()}” anyway`),
      )
      : null,
    error ? createElement('p', { className: 'error-text', role: 'alert' }, error) : null,
    createElement('p', { className: 'set-admin-muted' }, 'A new category is offered to every reviewer and submitter at once.'),
  );
}

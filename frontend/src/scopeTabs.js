import { createElement } from 'react';

// The Public / Private tab bar shared by the Reviews and Submissions surfaces
// (unified submission & review WP4/WP5). Arrow keys move between tabs; only the
// selected tab is in the tab order.
export function ScopeTabs({ entries, selected, onSelect, label, idPrefix, panelId }) {
  function onKeyDown(event) {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault?.();
    const index = entries.findIndex((entry) => entry.key === selected);
    const step = event.key === 'ArrowRight' ? 1 : -1;
    onSelect(entries[(index + step + entries.length) % entries.length].key);
  }

  return createElement(
    'nav',
    { className: 'reviews-scope-tabs', role: 'tablist', 'aria-label': label, onKeyDown },
    entries.map((entry) => createElement('button', {
      key: entry.key,
      type: 'button',
      role: 'tab',
      id: `${idPrefix}-${entry.key}`,
      'aria-selected': entry.key === selected,
      'aria-controls': panelId,
      tabIndex: entry.key === selected ? 0 : -1,
      className: `reviews-scope-tab${entry.key === selected ? ' active' : ''}`,
      onClick: () => onSelect(entry.key),
    }, entry.label)),
  );
}

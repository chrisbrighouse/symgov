import { createElement } from 'react';

export default function FavouriteFilter({ checked = false, onChange }) {
  return createElement(
    'label',
    { className: 'checkbox-row catalog-favourites-filter' },
    createElement('input', {
      type: 'checkbox',
      checked,
      onChange(event) {
        onChange?.(event.target.checked);
      }
    }),
    createElement('span', null, 'Show Favourites')
  );
}

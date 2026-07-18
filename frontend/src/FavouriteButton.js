import { createElement } from 'react';

import { favouriteButtonLabel } from './catalogFavourites.js';

export default function FavouriteButton({ symbol, pressed = false, pending = false, disabled = false, onToggle }) {
  return createElement(
    'button',
    {
      type: 'button',
      className: `catalog-favourite-button${pressed ? ' selected' : ''}`,
      'aria-label': favouriteButtonLabel(symbol, pressed),
      'aria-pressed': pressed,
      'aria-busy': pending || undefined,
      disabled: pending || disabled,
      onClick(event) {
        event.stopPropagation();
        onToggle?.();
      }
    },
    createElement('span', { 'aria-hidden': 'true' }, pressed ? '★' : '☆')
  );
}

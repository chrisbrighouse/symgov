import { createElement } from 'react';

// WP-H5 -- Hannah's approved photos for a symbol's semantic concept, shown
// under the symbol preview in the Catalog. They are illustrative, not part of
// the licensed record, so they carry a credit line instead of a download:
// author, licence and source, each only when known.
//
// `resolveUrl` turns the API-relative `previewUrl` into an absolute URL.

const MAX_PHOTOS = 2;

function sourceLabel(photo) {
  const domain = String(photo.sourceDomain || '').toLowerCase();
  if (domain.endsWith('wikimedia.org')) {
    return 'Wikimedia Commons';
  }
  return photo.sourceDomain || 'Source';
}

export function photoCreditParts(photo) {
  const parts = [];
  if (photo.author) {
    parts.push({ key: 'author', text: photo.author });
  }
  if (photo.licenseLabel) {
    parts.push({ key: 'license', text: photo.licenseLabel, href: photo.licenseUrl || null });
  }
  if (photo.sourceUrl) {
    parts.push({ key: 'source', text: sourceLabel(photo), href: photo.sourceUrl });
  }
  return parts;
}

function CreditPart({ part }) {
  if (!part.href) {
    return createElement('span', null, part.text);
  }
  return createElement('a', { href: part.href, target: '_blank', rel: 'noreferrer' }, part.text);
}

function PhotoCredit({ photo }) {
  const parts = photoCreditParts(photo);
  if (!parts.length) {
    return null;
  }
  const children = [];
  parts.forEach((part, index) => {
    if (index > 0) {
      children.push(createElement('span', { key: `${part.key}-separator`, 'aria-hidden': 'true' }, ' · '));
    }
    children.push(createElement(CreditPart, { key: part.key, part }));
  });
  return createElement('figcaption', { className: 'supplemental-photo-credit' }, children);
}

function PhotoCard({ photo, resolveUrl }) {
  const title = photo.title || photo.sourceDomain || 'Equipment photo';
  const image = createElement('img', {
    src: resolveUrl(photo.previewUrl),
    alt: photo.title || 'Illustrative equipment photo',
    loading: 'lazy',
  });
  return createElement(
    'figure',
    { className: 'supplemental-photo-card' },
    createElement(
      'a',
      { className: 'supplemental-photo-link', href: photo.sourceUrl || resolveUrl(photo.previewUrl), target: '_blank', rel: 'noreferrer' },
      image,
      createElement('span', { className: 'supplemental-photo-title' }, title),
    ),
    createElement(PhotoCredit, { photo }),
  );
}

export default function SupplementalPhotoStrip({ photos, resolveUrl = (url) => url }) {
  if (!Array.isArray(photos) || photos.length === 0) {
    return null;
  }
  return createElement(
    'section',
    { className: 'supplemental-photo-strip', 'aria-label': 'Illustrative equipment photos' },
    createElement(
      'div',
      { className: 'supplemental-photo-grid' },
      photos.slice(0, MAX_PHOTOS).map((photo) => createElement(PhotoCard, { key: photo.id, photo, resolveUrl })),
    ),
  );
}

import { createElement, useEffect, useState } from 'react';

import { organizationSymbolAssetPreviewUrl } from './api.js';

// The stored image of a private symbol's file, in the same checkerboard frame
// as the Public review screen. With several files, one button per file picks
// which is shown. Used by the Private review queue and by the submitter's own
// Submissions list (`compact`).
export function OrganizationSymbolAssetPreview({ symbolId, revisionId, assets, name, compact = false }) {
  const files = assets || [];
  const [selectedId, setSelectedId] = useState(null);
  const [failedIds, setFailedIds] = useState([]);

  useEffect(() => {
    setSelectedId(null);
    setFailedIds([]);
  }, [symbolId, revisionId]);

  const active = files.find((asset) => asset.id === selectedId) || files[0] || null;
  const src = active ? organizationSymbolAssetPreviewUrl(symbolId, revisionId, active.id) : null;
  const showImage = Boolean(src) && !failedIds.includes(active.id);

  return createElement(
    'div',
    { className: `organization-symbol-preview${compact ? ' compact' : ''}` },
    createElement(
      'div',
      { className: 'review-visual-frame' },
      showImage
        ? createElement('img', {
          key: src,
          className: 'review-source-image',
          src,
          alt: `Preview of ${active.filename} for ${name}`,
          onError: () => setFailedIds((ids) => [...ids, active.id]),
        })
        : createElement('div', { className: 'review-source-fallback' },
          createElement('small', null, active ? 'Preview unavailable' : 'No file attached')),
    ),
    files.length > 1
      ? createElement(
        'div',
        { className: 'catalog-preview-format-badges', role: 'group', 'aria-label': `Files of ${name}` },
        files.map((asset) => createElement('button', {
          key: asset.id,
          type: 'button',
          className: `catalog-preview-format-badge${asset.id === active?.id ? ' active' : ''}`,
          'aria-pressed': asset.id === active?.id,
          onClick: () => setSelectedId(asset.id),
        }, asset.filename)),
      )
      : null,
  );
}

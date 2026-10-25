import { createElement, useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { fetchNotifications, markAllNotificationsRead, markNotificationRead } from './api.js';
import { canMountOrganizationSymbolDrafts } from './projectContext.js';

// Unified submission & review WP6 (spec draft 2 FR17): a header bell with the
// unread count and the newest notices for the active organization session.
// In-app only (D9); email is a later package.

const POLL_MS = 60000;

const DEFAULT_API = {
  list: fetchNotifications,
  markRead: markNotificationRead,
  markAllRead: markAllNotificationsRead,
};

export function canUseNotifications(auth) {
  return auth?.user?.capabilities?.organizationSymbolWorkflowV2Enabled === true && canMountOrganizationSymbolDrafts(auth);
}

// Notices about a submission waiting on reviewers open Reviews; notices about
// a decision on your own submission open Submissions.
export function notificationView(item) {
  const actor = item.actorName || 'Someone';
  const symbol = item.symbolName || 'a private symbol';
  switch (item.kind) {
    case 'org_symbol.submitted':
      return { text: `${actor} submitted ${symbol} for review`, to: '/reviews?scope=private' };
    case 'org_symbol.withdrawn':
      return { text: `${actor} withdrew ${symbol} from review`, to: '/reviews?scope=private' };
    case 'org_symbol.approved':
      return { text: `${actor} approved ${symbol}`, to: '/standards/submit?target=private' };
    case 'org_symbol.rejected':
      return { text: `${actor} rejected ${symbol}`, to: '/standards/submit?target=private' };
    case 'org_symbol.changes_requested':
      return { text: `${actor} asked for changes to ${symbol}`, to: '/standards/submit?target=private' };
    default:
      return { text: symbol, to: '/' };
  }
}

function BellIcon() {
  return createElement(
    'svg',
    { viewBox: '0 0 24 24', 'aria-hidden': 'true', fill: 'none', stroke: 'currentColor', strokeWidth: '2', strokeLinecap: 'round', strokeLinejoin: 'round' },
    createElement('path', { d: 'M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9' }),
    createElement('path', { d: 'M10.3 21a1.94 1.94 0 0 0 3.4 0' }),
  );
}

export function NotificationBell({ api = DEFAULT_API, pollMs = POLL_MS }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [unread, setUnread] = useState(0);
  const [error, setError] = useState('');
  const rootRef = useRef(null);
  const buttonRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const page = await api.list();
      setItems(page?.items || []);
      setUnread(page?.unreadCount || 0);
      setError('');
    } catch (err) {
      setError(err.message || 'Notifications unavailable.');
    }
  }, [api]);

  useEffect(() => {
    refresh();
    if (!pollMs) return undefined;
    const timer = setInterval(refresh, pollMs);
    return () => clearInterval(timer);
  }, [refresh, pollMs]);

  useEffect(() => {
    if (!open || typeof document === 'undefined') return undefined;
    function onPointerDown(event) {
      if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false);
    }
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open]);

  function toggle() {
    if (!open) refresh();
    setOpen(!open);
  }

  function close() {
    setOpen(false);
    buttonRef.current?.focus?.();
  }

  async function openItem(item) {
    setOpen(false);
    if (!item.readAt) {
      try {
        await api.markRead(item.id);
      } catch {
        // Opening the item matters more than the read mark; the next refresh retries.
      }
    }
    navigate(notificationView(item).to);
    refresh();
  }

  async function readAll() {
    try {
      await api.markAllRead();
      await refresh();
    } catch (err) {
      setError(err.message || 'Could not mark notifications as read.');
    }
  }

  const label = unread > 0 ? `Notifications, ${unread} unread` : 'Notifications';

  return createElement(
    'div',
    {
      className: 'notification-bell',
      ref: rootRef,
      onKeyDown: (event) => { if (event.key === 'Escape' && open) close(); },
    },
    createElement('button', {
      type: 'button',
      ref: buttonRef,
      className: 'ghost-button notification-bell-button',
      'aria-label': label,
      'aria-expanded': open,
      'aria-controls': 'notification-panel',
      onClick: toggle,
    },
      createElement(BellIcon),
      unread > 0 ? createElement('span', { className: 'notification-count', 'aria-hidden': 'true' }, unread > 99 ? '99+' : String(unread)) : null,
    ),
    open
      ? createElement(
        'div',
        { id: 'notification-panel', className: 'notification-panel', role: 'region', 'aria-label': 'Notifications' },
        createElement('div', { className: 'notification-panel-header' },
          createElement('h2', null, 'Notifications'),
          unread > 0
            ? createElement('button', { type: 'button', className: 'action-button compact ghost', onClick: readAll }, 'Mark all read')
            : null,
        ),
        error ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, error) : null,
        !error && items.length === 0 ? createElement('p', { className: 'set-admin-muted' }, 'No notifications yet.') : null,
        items.length > 0
          ? createElement('ul', { className: 'notification-list' },
            items.map((item) => {
              const view = notificationView(item);
              return createElement('li', { key: item.id },
                createElement('button', {
                  type: 'button',
                  className: `notification-item${item.readAt ? '' : ' unread'}`,
                  onClick: () => openItem(item),
                },
                  createElement('span', { className: 'notification-text' },
                    item.readAt ? null : createElement('span', { className: 'visually-hidden' }, 'Unread: '),
                    view.text),
                  item.rationale ? createElement('span', { className: 'notification-rationale' }, `“${item.rationale}”`) : null,
                  createElement('time', { className: 'notification-time', dateTime: item.createdAt }, new Date(item.createdAt).toLocaleString()),
                ));
            }))
          : null,
      )
      : null,
  );
}

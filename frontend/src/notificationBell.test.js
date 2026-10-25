import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter, useLocation } from 'react-router-dom';

import { NotificationBell, canUseNotifications, notificationView } from './NotificationBell.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function auth({ v2 = true, mode = 'organization' } = {}) {
  return {
    user: {
      id: 'user-1',
      session: { mode, purpose: 'application', activeOrganizationId: 'org-1' },
      organization: { id: 'org-1', code: 'acme', displayName: 'Acme', baseRole: 'user', capabilities: [] },
      capabilities: { organizationSymbolsEnabled: true, organizationSymbolWorkflowV2Enabled: v2 },
    },
  };
}

const ITEMS = [
  { id: 'n1', kind: 'org_symbol.changes_requested', symbolName: 'Acme pump', actorName: 'Rex', rationale: 'Add the flange.', createdAt: '2026-10-24T10:00:00Z', readAt: null },
  { id: 'n2', kind: 'org_symbol.submitted', symbolName: 'Acme valve', actorName: 'Ann', createdAt: '2026-10-24T09:00:00Z', readAt: '2026-10-24T09:30:00Z' },
];

function fakeApi(page = { items: ITEMS, unreadCount: 1 }) {
  const calls = [];
  return {
    calls,
    list: async () => { calls.push(['list']); return page; },
    markRead: async (id) => { calls.push(['read', id]); },
    markAllRead: async () => { calls.push(['readAll']); },
  };
}

let location = null;
function LocationProbe() {
  location = useLocation();
  return null;
}

async function mount(api) {
  let renderer;
  await act(async () => {
    renderer = create(createElement(MemoryRouter, { initialEntries: ['/standards'] },
      createElement(NotificationBell, { api, pollMs: 0 }),
      createElement(LocationProbe)));
  });
  return renderer;
}

function bell(renderer) {
  return renderer.root.find((node) => node.type === 'button' && node.props.className?.includes('notification-bell-button'));
}

describe('canUseNotifications', () => {
  it('needs the workflow flag and an organization session', () => {
    assert.equal(canUseNotifications(auth()), true);
    assert.equal(canUseNotifications(auth({ v2: false })), false);
    assert.equal(canUseNotifications(auth({ mode: 'personal' })), false);
  });
});

describe('notificationView', () => {
  it('sends reviewer notices to Reviews and decision notices to Submissions', () => {
    assert.deepEqual(notificationView(ITEMS[1]), { text: 'Ann submitted Acme valve for review', to: '/reviews?scope=private' });
    assert.deepEqual(notificationView(ITEMS[0]), { text: 'Rex asked for changes to Acme pump', to: '/standards/submit?target=private' });
  });
});

describe('NotificationBell', () => {
  it('shows the unread count in its accessible name and lists notices when opened', async () => {
    const api = fakeApi();
    const renderer = await mount(api);
    assert.equal(bell(renderer).props['aria-label'], 'Notifications, 1 unread');
    assert.equal(bell(renderer).props['aria-expanded'], false);

    await act(async () => bell(renderer).props.onClick());
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Rex asked for changes to Acme pump/);
    assert.match(text, /Add the flange/);
    assert.match(text, /Unread: /);
    assert.equal(bell(renderer).props['aria-expanded'], true);
    await act(async () => renderer.unmount());
  });

  it('marks an unread notice read and opens its item', async () => {
    const api = fakeApi();
    const renderer = await mount(api);
    await act(async () => bell(renderer).props.onClick());
    const [first] = renderer.root.findAll((node) => node.type === 'button' && node.props.className?.startsWith('notification-item'));
    await act(async () => first.props.onClick());
    assert.deepEqual(api.calls.filter(([name]) => name === 'read'), [['read', 'n1']]);
    assert.equal(`${location.pathname}${location.search}`, '/standards/submit?target=private');
    assert.equal(renderer.root.findAll((node) => node.props.id === 'notification-panel').length, 0);
    await act(async () => renderer.unmount());
  });

  it('marks everything read, and closes on Escape', async () => {
    const api = fakeApi();
    const renderer = await mount(api);
    await act(async () => bell(renderer).props.onClick());
    const readAll = renderer.root.find((node) => node.type === 'button' && node.props.children === 'Mark all read');
    await act(async () => readAll.props.onClick());
    assert.ok(api.calls.some(([name]) => name === 'readAll'));

    const root = renderer.root.find((node) => node.props.className === 'notification-bell');
    await act(async () => root.props.onKeyDown({ key: 'Escape' }));
    assert.equal(renderer.root.findAll((node) => node.props.id === 'notification-panel').length, 0);
    await act(async () => renderer.unmount());
  });

  it('says so when there is nothing to show', async () => {
    const renderer = await mount(fakeApi({ items: [], unreadCount: 0 }));
    assert.equal(bell(renderer).props['aria-label'], 'Notifications');
    await act(async () => bell(renderer).props.onClick());
    assert.match(JSON.stringify(renderer.toJSON()), /No notifications yet/);
    await act(async () => renderer.unmount());
  });
});

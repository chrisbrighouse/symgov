# Hermes dashboard Cloudflare exposure restart note

Timestamp: 2026-06-26T18:26:47Z
Host: srv1442722.hstgr.cloud

## Status

The full Hermes Agent web dashboard is now exposed through the existing Cloudflare Tunnel for `apps.chrisbrighouse.com` at:

- https://apps.chrisbrighouse.com/hermes-dashboard/

This reuses the existing `cloudflared-symgov-apps.service` tunnel and `applications-web` nginx container. No new Cloudflare Tunnel was created.

Existing lightweight Symgov/Hermes control surface remains available at:

- https://apps.chrisbrighouse.com/hermes/

## Changes made

1. Added dashboard auth env vars to `/root/.hermes/.env`:
   - `HERMES_DASHBOARD_BASIC_AUTH_USERNAME`
   - `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD`
   - `HERMES_DASHBOARD_BASIC_AUTH_SECRET`
   - `HERMES_DASHBOARD_PUBLIC_URL=https://apps.chrisbrighouse.com/hermes-dashboard`

2. Created and enabled host systemd service:
   - `/etc/systemd/system/hermes-web-dashboard.service`
   - Service runs: `hermes dashboard --host 172.31.30.1 --port 9120 --no-open --skip-build`
   - Bound to the host side of the Symgov Docker bridge, not a public wildcard address.

3. Built the Hermes web UI frontend once from:
   - `/usr/local/lib/hermes-agent/web`
   - Command used: `npm install && npm run build`

4. Updated `/docker/symgov-hermes/nginx.conf`:
   - Added `/hermes-dashboard/` reverse proxy to `http://172.31.30.1:9120/`
   - Added `/auth/password-login` proxy because the login HTML posts to absolute `/auth/password-login`
   - Fixed `/hermes` redirect to use `https://$host/hermes/`
   - Added websocket headers for dashboard chat/API socket paths

5. Recreated `applications-web` so the container picked up the changed single-file nginx config bind mount:
   - `docker compose -f /docker/symgov-hermes/docker-compose.yml up -d --force-recreate --no-deps applications-web`

## Verification

- `hermes-web-dashboard.service`: active
- `cloudflared-symgov-apps.service`: active
- `applications-web`: healthy after recreate
- Unauthenticated dashboard route returns login redirect:
  - `https://apps.chrisbrighouse.com/hermes-dashboard/` -> 302 to `https://apps.chrisbrighouse.com/hermes-dashboard/login?next=%2Fhermes-dashboard%2F`
- Authenticated login POST to `https://apps.chrisbrighouse.com/auth/password-login` returned:
  - `{"ok":true,"next":"/hermes-dashboard/"}`
- Authenticated dashboard HTML returned HTTP 200 and included:
  - `<title>Hermes Agent - Dashboard</title>`
  - SPA root element
  - injected `/hermes-dashboard` base path
- Static dashboard bundle returned HTTP 200 through Cloudflare:
  - `/hermes-dashboard/assets/index-DFQeXcPV.js`
- Existing Symgov app root remained HTTP 200.
- Existing `/hermes` control surface redirect now points to HTTPS.

## Current uncommitted state

Repository `/data/symgov` had no uncommitted changes at the time of checking. The main changes are host/docker operational files outside the repo:

- `/root/.hermes/.env`
- `/etc/systemd/system/hermes-web-dashboard.service`
- `/docker/symgov-hermes/nginx.conf`

This restart note itself is a new repo file unless committed later.

## Credentials

The dashboard uses the same username/password values as the existing lightweight `/hermes` control surface, copied from `/opt/hermes-dashboard/hermes-dashboard.env` into `/root/.hermes/.env` under the Hermes dashboard basic-auth env var names.

Because a credential appeared in command output during verification, rotate the dashboard password soon if this terminal transcript is stored somewhere less trusted than the VPS admin account.

## Useful commands

Check services:

```bash
systemctl status hermes-web-dashboard.service --no-pager -l
systemctl status cloudflared-symgov-apps.service --no-pager -l
docker ps --filter name=applications-web --format 'table {{.Names}}\t{{.Status}}'
```

Restart dashboard only:

```bash
systemctl restart hermes-web-dashboard.service
```

After editing `/docker/symgov-hermes/nginx.conf`, recreate nginx container:

```bash
cd /docker/symgov-hermes
docker compose up -d --force-recreate --no-deps applications-web
```

Verify public route:

```bash
curl -I https://apps.chrisbrighouse.com/hermes-dashboard/
```

## Restart prompt

Continue from `/data/symgov/docs/plans/2026-06-26-hermes-dashboard-cloudflare-restart.md`. Verify `https://apps.chrisbrighouse.com/hermes-dashboard/` in a browser, sign in with the existing Hermes dashboard credentials, and test the Chat tab websocket. If login works but the Chat tab fails, inspect browser devtools websocket URL under `/hermes-dashboard/api/pty` and journal logs for `hermes-web-dashboard.service`.

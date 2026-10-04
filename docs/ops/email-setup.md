# Symgov outbound email

How Symgov sends email, how to switch it on, and how to move to a
`symgov.org` sender later. This repository is public: record variable names
here, never values.

Status when written (2026-10-04): the code is complete and tested. Whether
production has the transport enabled was **not verified**; a read of the
production container environment was blocked for the agent. Check with the
command in "Verify" below before assuming either way.

## How it works

- `backend/symgov_backend/email_outbox.py` queues rows in the `email_outbox`
  table in the same transaction as the change that caused them.
- `backend/symgov_backend/email_worker.py` runs inside the API process
  (started from `app.py`). Every `SYMGOV_EMAIL_WORKER_INTERVAL_SECONDS`
  (default 30) it sends pending rows and marks them `sent`.
- Failures are kept as a sanitized error category with exponential retry
  (30s doubling, capped at 1h). A failed send never undoes the change that
  queued it.
- AgentMail sends use the outbox row UUID as the `Idempotency-Key`, so a
  retry inside AgentMail's 24-hour window does not duplicate the message.
- The AgentMail transport is pinned to `https://api.agentmail.to/v0` and
  refuses redirects, so configuration cannot redirect the bearer token.
- The sender address is simply the inbox address; there is no separate
  "from" setting for AgentMail.

Today the only producer is a Plus subscription change (upgrade or
downgrade) from `routes/profile.py`: one email to the customer, one to
`SYMGOV_SUBSCRIPTION_ADMIN_EMAIL` (default `chris.brighouse@hotmail.co.uk`).
`email_outbox` only allows `recipient_kind` of `customer` or `admin` and is
keyed to subscription events. Any other notification type needs a migration
and a generalised queue function first.

## Settings

| Variable | Purpose |
|---|---|
| `SYMGOV_EMAIL_TRANSPORT` | `smtp` (default) or `agentmail` |
| `SYMGOV_AGENTMAIL_INBOX` | Sending inbox, e.g. `alfi-bot@agentmail.to` |
| `AGENTMAIL_API_KEY` | Secret. Process env first, then the Hermes profile `.env` (`SYMGOV_HERMES_ENV_FILE`, default `/root/.hermes/profiles/<profile>/.env`) |
| `SYMGOV_AGENTMAIL_TIMEOUT_SECONDS` | Optional, default 20 |
| `SYMGOV_SUBSCRIPTION_ADMIN_EMAIL` | Admin copy recipient |
| `SYMGOV_EMAIL_WORKER_INTERVAL_SECONDS` | Worker cycle, default 30 |
| `SYMGOV_SMTP_*` | Alternative SMTP transport; see `backend/README.md` |

If the selected transport is not fully configured the worker only logs
`AgentMail transport is selected but not fully configured.` and rows stay
pending. A missing key fails quietly, so check the API log after any change.

## Current choice: shared agentmail.to sender (free tier)

Chosen 2026-10-04 to avoid the cost of a custom domain. Mail is sent from
`alfi-bot@agentmail.to`, the inbox Alfi already uses.

Enable it in the `symgov-api` service in `/docker/symgov-hermes/docker-compose.yml`:

1. Set `SYMGOV_EMAIL_TRANSPORT=agentmail` and
   `SYMGOV_AGENTMAIL_INBOX=alfi-bot@agentmail.to`.
2. Give the container `AGENTMAIL_API_KEY`. Prefer a small 0600 `env_file`
   holding only that key. Do not mount the whole Hermes `.env`: it would
   expose every Hermes secret to the API. (The host path in the fallback is
   outside the container, so the fallback only works if it is mounted.)
3. Recreate with `docker compose up -d symgov-api`. Never `restart`; see the
   deploy-release skill.
4. Confirm the API log has no "not fully configured" warning.

Production compose edits are made by Chris with a `!` command; the agent's
access to production reads is blocked by design.

Caveats on the free tier:

- Mail from the shared `agentmail.to` domain may reach Hotmail's junk folder
  or be rejected. Check junk when testing.
- Sending limits on the free tier were not checked. Confirm in the AgentMail
  console before real users depend on it.
- The same inbox is shared with Alfi, so replies and Alfi's own mail mix.
  Acceptable for testing only.

## Testing

Only `chris.brighouse@hotmail.co.uk` is a valid test recipient. Chris's
account is the protected perpetual Plus owner, so a self-service change on it
may not queue anything. The reliable test is to insert one `email_outbox` row
addressed to that address and let the worker send it, then check that its
`status` becomes `sent` and that the message arrives (including the junk
folder). Inserting the row is a production database write and needs Chris to
run it.

## Verify what production has

Read-only, run by Chris. It prints names only and hides secret values:

    ! cd /docker/symgov-hermes && docker ps --format '{{.Names}}' | grep -i symgov; grep -n -i "AGENTMAIL\|EMAIL_TRANSPORT\|SMTP\|env_file\|SYMGOV_HERMES\|SUBSCRIPTION_ADMIN" docker-compose.yml | sed -E 's/((KEY|PASSWORD|TOKEN|SECRET)[A-Z_]*[:=] *).*/\1<redacted>/'; docker exec $(docker ps --format '{{.Names}}' | grep -i 'symgov-api' | head -1) env | grep -i "^SYMGOV_EMAIL\|^SYMGOV_AGENTMAIL\|^AGENTMAIL\|^SYMGOV_SMTP_HOST\|^SYMGOV_SUBSCRIPTION" | sed -E 's/^(AGENTMAIL_API_KEY)=.*/\1=<set>/'

## Later: send from mail.symgov.org

Decided 2026-10-04 to use the subdomain `mail.symgov.org`, not the apex, so
the apex stays free for human mailboxes (Google Workspace or Microsoft 365)
later. The sender would be e.g. `notifications@mail.symgov.org`. No Symgov
code change is needed: only AgentMail, DNS and one variable.

State of DNS on 2026-10-03: `symgov.org` is hosted on Cloudflare
(nameservers `art.ns.cloudflare.com`, `diva.ns.cloudflare.com`) and has no
MX, SPF or DMARC records, so nothing exists to clash with.

Steps:

1. **AgentMail plan.** Custom domains need the Developer plan or above (the
   free tier has none). Pricing seen 2026-10-03: Developer includes 10
   domains, extra domains $2/month on pay-as-you-go. Re-check current
   pricing. The account that owns the API key owns the domain; today that is
   Alfi's account.
2. **Register the domain.** In the AgentMail console (or
   `POST /domains/...`) add `mail.symgov.org`. Confirm it accepts the
   subdomain directly. The wildcard-MX setup in AgentMail's docs is for
   creating inboxes on arbitrary subdomains and is not needed here. It
   returns the exact records to publish; use those, not generic ones.
3. **Publish DNS in Cloudflare.** MX, SPF (TXT), DKIM (CNAMEs) and DMARC
   (TXT) as returned. Set every DKIM CNAME to **DNS only** (grey cloud); a
   proxied record will not verify. AgentMail moves the domain from Pending to
   Verified automatically (minutes to 48 hours).
4. **Create the inbox**, e.g. username `notifications` on `mail.symgov.org`.
5. **Switch Symgov.** Set `SYMGOV_AGENTMAIL_INBOX=notifications@mail.symgov.org`
   in compose and recreate `symgov-api` (`up -d`, not `restart`). Check the
   log, then send a test as above.
6. **Rollback.** Set `SYMGOV_AGENTMAIL_INBOX` back to `alfi-bot@agentmail.to`
   and recreate. Pending rows keep their retry schedule.

Sources (checked 2026-10-03):
<https://docs.agentmail.to/custom-domains>,
<https://docs.agentmail.to/knowledge-base/custom-domain-setup>

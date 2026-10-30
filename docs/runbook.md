# Runbook

## First deployment (Raspberry Pi 5, Ubuntu Server 24.04, ARM64)

1. Install Docker Engine + Compose plugin. Create `/opt/muda` owned by the deploy user.
2. Copy the repository there, then `cp .env.example .env && chmod 600 .env` and fill it in.
   Generate `PREVIEW_SECRET` with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
3. Cloudflare: Zero Trust -> Networks -> Tunnels -> create a tunnel, copy its token to `TUNNEL_TOKEN`,
   and add a public hostname pointing to the service `http://web:8080`. Use one domain only;
   `/admin` is reserved and returns 404 until Phase 4.
4. Telegram: create a bot with @BotFather, put the token in `.env`. Put your numeric Telegram user id
   in `TELEGRAM_ALLOWED_USER_ID`. Authorization never uses the username.
5. `docker compose up -d --build`. The `api` container runs `alembic upgrade head`; `bot` waits for it.
6. Check: `docker compose ps` (api healthy), `curl https://<domain>/api/site`, send `/start` to the bot.

Local smoke test without Cloudflare:
`docker compose -f docker-compose.yml -f docker-compose.local.yml up --build api bot web`
then open `http://127.0.0.1:8080`.

## Deploy updates

CI must be green on `main`. The `Deploy` workflow (self-hosted runner label `muda-prod`) syncs the
sources into `/opt/muda` (keeping `.env`) and runs `docker compose up -d --build`. The runner is only
triggered by pushes to `main` after CI, never by pull requests. Do not enable it for forks.

## Backup

SQLite online backup (safe while the app runs, integrity-checked, atomic file write, keeps the
newest `BACKUP_KEEP` files):

```bash
docker compose --profile tools run --rm backup
```

Schedule it daily, then copy the files off the Pi (Backblaze B2 arrives in Phase 3):

```cron
17 3 * * * cd /opt/muda && docker compose --profile tools run --rm backup >> /var/log/muda-backup.log 2>&1
```

Backups live in the `muda-data` volume under `/data/backups`. Copy one out:

```bash
docker run --rm -v muda_muda-data:/data -v "$PWD":/out alpine cp -r /data/backups /out/
```

## Restore

1. `docker compose stop bot api`
2. Restore the chosen file (this replaces the live database and drops its WAL leftovers):

```bash
docker run --rm -v muda_muda-data:/data -v "$PWD/backups":/b alpine sh -c \
  'rm -f /data/muda.db-wal /data/muda.db-shm && cp /b/muda-YYYYMMDDTHHMMSSZ.db /data/muda.db && chown 10001:10001 /data/muda.db'
```

3. `docker compose up -d`. Any job that was `PROCESSING` at the time is marked `FAILED/INTERRUPTED`
   by the startup recovery; nothing is re-sent to the AI provider.
4. Test the procedure once before you need it.

## Operations

| Situation | What happens / what to do |
|---|---|
| Bot or worker crashes | `restart: unless-stopped` restarts it; recovery fails interrupted `PROCESSING` jobs (never retried, to avoid double AI charges) and finishes pending cancellations. The public site keeps serving. |
| API down | Static pages still load; the data call fails and the page shows a friendly message. |
| AI budget exhausted | New requests fail with a clear message; raise `AI_MONTHLY_BUDGET_USD` and restart `bot`. This is an application cap, not a provider billing limit - also set a spending limit in the DeepSeek console. |
| Stuck job | `/status` then `/cancel` in Telegram. Cancellation is idempotent and never publishes. |
| Rotate secrets | Edit `.env`, `docker compose up -d`. Rotating `PREVIEW_SECRET` invalidates outstanding preview links. |

## Logs

JSON lines on stdout (`docker compose logs -f api bot`). Every line has a `correlation_id` (request id
for the API, job id for the worker). Secrets are redacted; Nginx does not log preview URLs.

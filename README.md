# Muda - AI-powered website management

Manage the **Luna Cafe** demo website from Telegram in natural language. Phase 1 supports two kinds
of change, both through a validated, approval-gated pipeline:

- Content: `Change the Latte price to 150 TL.`
- Design: `Make the website dark, center the hero title, and make the menu cards more rounded.`

```text
Telegram -> worker -> DeepSeek -> validated change -> immutable draft -> preview link
         -> Approve/Reject buttons -> publish (atomic pointer swap) -> public website
```

## Architecture (modular monolith)

```text
Internet -> Cloudflare Tunnel -> Nginx (web) --+-- static React build
                                               +-- /api -> FastAPI (api)     [private network, no internet]
                                   SQLite (WAL) volume shared by api + bot
bot container: Telegram long polling + persistent job worker -> DeepSeek (outbound only)
```

| Path | Purpose |
|---|---|
| `backend/src/muda/domain` | Pure rules: job state machine, `DesignConfig`, allowed operations, site content |
| `backend/src/muda/persistence` | SQLAlchemy models, job repository, draft/approve/publish, recovery |
| `backend/src/muda/ai` | `AIProvider` protocol, DeepSeek adapter, fake provider, pricing, budgets |
| `backend/src/muda/worker.py` | DB-driven job worker with cancellation checkpoints |
| `backend/src/muda/bot_service.py` | Telegram-independent bot logic (auth, rate limits, approvals) |
| `backend/src/muda/api.py` | Read-only public API and signed preview endpoint |
| `frontend/` | React + TypeScript + Vite + Ant Design + Tailwind |
| `docs/adr/` | Architecture decision records |
| `docs/runbook.md` | Deploy, backup and recovery |

## Develop

```bash
cd backend  && uv sync && uv run pytest && uv run ruff check . && uv run mypy src tests
cd frontend && npm ci && npm test && npm run lint && npm run typecheck && npm run build
```

Tests never call DeepSeek or Telegram; they use `FakeProvider` and fakes for the Telegram objects.

## Configuration

See `.env.example`. Required in production: `PUBLIC_BASE_URL`, `PREVIEW_SECRET` (>= 32 chars),
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, `DEEPSEEK_API_KEY`, `TUNNEL_TOKEN`.

## Security model in one paragraph

Telegram text and model output are untrusted. The model can only emit one of two allowlisted
operations; every proposal is validated by strict Pydantic schemas, applied to an immutable draft
version and shown to the owner. Nothing becomes live without an authenticated approval that names
the exact job and draft. Design values are closed vocabularies mapped to fixed CSS classes, so no
model output is ever executed or rendered as markup.

## Known limitations (Phase 1)

- Single user, single worker process, SQLite. No multi-tenancy or billing by design.
- Rate limiting for messages is in memory and resets on restart.
- Expired approvals are marked `EXPIRED` but the owner is not notified.
- The Docker images and GitHub Actions workflows were validated statically here; their first real
  run happens in CI and on the Raspberry Pi.

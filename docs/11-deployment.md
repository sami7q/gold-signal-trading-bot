# Deployment

## Local development

```bash
# Database (optional locally — SQLite works fine for dev/test; use Postgres to match prod)
docker compose up -d

# Backend
cd backend
python -m venv .venv
. .venv/Scripts/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
copy ..\.env.example ..\.env    # then edit as needed
alembic upgrade head
uvicorn app.main:app --reload

# Dashboard
cd dashboard
npm install
cp .env.local.example .env.local
npm run dev
```

Backend: http://localhost:8000 (`/docs` for the OpenAPI UI). Dashboard: http://localhost:3000.

## Docker Compose (Postgres only, for now)

`docker-compose.yml` at the repo root runs Postgres 16 with a persistent named volume and a
healthcheck. The backend and dashboard are **not** containerized yet — both run natively on the
target machine, which matters for the VPS target below since `MetaTrader5` requires an actual
Windows MT5 terminal process, not something that fits in a portable Linux container.

## VPS deployment (Phase 13 — after Phases 0-12 are stable)

The target is a **Windows VPS**, driven by the MT5 terminal requirement
(PROJECT_SPEC.md section 3). Outline:

1. Install the MT5 terminal, log into the target account (DEMO first — see
   [10-mt5-integration.md](10-mt5-integration.md)), and confirm it stays connected unattended.
2. Install Python 3.12+, PostgreSQL (or point `DATABASE_URL` at a managed Postgres instance),
   Node.js 20+.
3. Deploy the backend as a Windows service (e.g. via NSSM or Task Scheduler with "restart on
   failure") running `uvicorn app.main:app --host 0.0.0.0 --port 8000`. `app/lifecycle.py` already
   handles reconnecting MT5/Telegram gracefully on process restart — a service manager only needs
   to restart the *process*, not coordinate anything else.
4. Run `alembic upgrade head` against the real Postgres database before first start, and on every
   deploy thereafter.
5. Build the dashboard (`npm run build`) and serve it (`npm run start`, or export behind IIS/
   nginx) — see [13-security.md](13-security.md) before exposing it publicly; it must not be
   reachable without authentication.
6. Put a reverse proxy (nginx, Caddy, or IIS URL Rewrite) in front of both processes for HTTPS.
   Terminate TLS at the proxy; keep the backend/dashboard listening on localhost only.
7. Confirm the environment variables actually deployed have `TELEGRAM_ENABLED` /`MT5_ENABLED` set
   deliberately (not accidentally left `true` from a dev `.env` copy) and `TRADING_MODE` matches
   what's intended (`DEMO` until the Phase 12 checklist is fully signed off).

## Health monitoring

`GET /api/health`, `/api/health/broker`, `/api/health/signal-source` are the three endpoints to
point an external uptime monitor at. `broker.healthy == false` after MT5 was expected to connect
is the signal that trading has silently fallen back to nothing (which is safe — no trade — but
means the bot isn't doing its job).

## Log retention

Structured JSON logs go to stdout (`app/core/logging.py`); redirect them to a rotating file or an
aggregator of choice at the process-manager level (VPS deployment step 3) — the application itself
does not manage log files.

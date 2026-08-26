# Gold Signal Trading Bot

Automated gold (XAUUSD) BUY-signal trading bot: Telegram → parser → rules engine → MT5, fully
testable with mock/paper integrations before any real account is connected.

## Development status

Internal build phase (backend + dashboard + tests). Telegram and MT5 are **not** connected.
Everything runs against a Mock Signal Source and a Paper Broker.

## Current mode

```
TRADING_MODE=SIMULATION
AUTO_TRADING=false
TELEGRAM_ENABLED=false
MT5_ENABLED=false
```

No real-money trade can occur in this configuration.

## Quick start

```bash
# 1. Database (optional — tests default to SQLite; use Postgres for real runs)
docker compose up -d

# 2. Backend
cd backend
python -m venv .venv
. .venv/Scripts/activate   # Windows
pip install -e ".[dev]"
copy ..\.env.example ..\.env
alembic upgrade head
uvicorn app.main:app --reload

# 3. Dashboard
cd dashboard
npm install
npm run dev
```

## Test command

```bash
cd backend
pytest
```

## URLs

- Dashboard: http://localhost:3000
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs

## Documentation

- Full specification: [PROJECT_SPEC.md](PROJECT_SPEC.md)
- Architecture, testing, deployment, security, and integration guides: [docs/](docs/)

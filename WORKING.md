# Working Guide

This guide is the practical developer setup for the payment ledger project. It is meant to be clearer and easier to follow than the long specification documents when you are actually working on the codebase.

## 1. Project goal

This project is a fintech-style ledger service that models:

- wallet creation
- deposits and withdrawals
- peer-to-peer transfers
- idempotent API writes
- ledger-based balance derivation
- async webhook delivery
- reconciliation jobs and monitoring

The core idea is that balances are not stored as mutable wallet totals. Instead, they are derived from the ledger, which keeps an append-only, auditable history of every movement.

## 2. Requirements

Before starting, make sure you have:

- Python 3.11+
- Node.js 18+
- npm
- Docker Desktop or Docker Engine (optional but useful for full stack testing)
- Git

On Windows, use PowerShell commands as shown below.

## 3. Recommended local setup

### Step 1: create and activate the environment

```powershell
cd "C:\Users\Phaneendra Simhadri\OneDrive - Amrita vishwa vidyapeetham\ledger"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Step 2: configure environment variables

Copy the sample file and edit it for your local values:

```powershell
Copy-Item .env.example .env
```

Example values:

```env
DATABASE_URL=sqlite+aiosqlite:///./ledger.db
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=change-this-in-production
WEBHOOK_SECRET=change-this-in-production
WEBHOOK_URL=http://localhost:8000/webhooks
FRONTEND_URL=http://localhost:5173
ENVIRONMENT=development
LOG_LEVEL=INFO
RECONCILIATION_INTERVAL_MINUTES=60
IDEMPOTENCY_KEY_TTL_HOURS=24
```

> If you are using PostgreSQL, replace the database URL with your local Postgres connection string.

### Step 3: run the backend

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Check the app:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Expected response:

```json
{"status":"ok","db":"ok","redis":"ok"}
```

If Redis is not running, the health endpoint may return a degraded response or 503.

### Step 4: run the frontend

Open a second terminal and run:

```powershell
cd frontend
npm install
npm run dev
```

Then visit:

- http://localhost:5173

## 4. Full stack with Docker

This project includes Docker Compose for the full backend stack:

```powershell
docker compose up --build
```

The following services are exposed:

- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- PostgreSQL: localhost:5432
- Redis: localhost:6379

To stop everything:

```powershell
docker compose down
```

To reset the database volume:

```powershell
docker compose down -v
```

## 5. Main project structure

```text
ledger/
├── app/
│   ├── config.py
│   ├── database.py
│   ├── main.py
│   ├── middleware/
│   ├── models/
│   ├── routers/
│   ├── services/
│   ├── workers/
│   └── jobs/
├── alembic/
├── frontend/
├── tests/
├── .env.example
├── docker-compose.yml
├── Dockerfile
├── README.md
├── WORKING.md
├── requirements.txt
├── pytest.ini
├── locustfile.py
└── alembic.ini
```

## 6. Useful commands

### Run tests

```powershell
pytest -q
```

### Run with database migrations

```powershell
alembic upgrade head
```

### Start Celery worker

```powershell
celery -A app.workers.celery_app worker --loglevel=info
```

### Start Celery Beat

```powershell
celery -A app.workers.celery_app beat --loglevel=info
```

### Load testing

```powershell
locust
```

## 7. Typical development workflow

1. Update environment values in `.env`.
2. Start Redis and Postgres if you are not using Docker.
3. Run the API locally.
4. Start the frontend.
5. Register a user and create wallet(s).
6. Make a deposit, withdrawal, or transfer.
7. Inspect transaction history and balances.
8. Use the idempotency key on write requests to test duplicate protection.
9. Run the test suite before committing changes.

## 8. Common issues

### Redis is not connected

- Make sure Redis is running.
- Check the `REDIS_URL` value in `.env`.
- If using Docker, run `docker compose up -d redis` or start the whole stack.

### Database errors

- Confirm the `DATABASE_URL` is valid.
- Run `alembic upgrade head`.
- If using SQLite locally, ensure the database file is writable.

### JWT errors

- Check that `JWT_SECRET` is set and not empty.
- Make sure the same secret is used across the app startup and any clients that sign tokens.

### Frontend cannot connect to backend

- Confirm the backend is running on `http://localhost:8000`.
- Ensure `FRONTEND_URL` and CORS configuration match the frontend origin.

## 9. Working rules

- Do not commit `.env` files with real secrets.
- Use Alembic for database schema changes.
- Prefer `.env.example` for configuration examples.
- Keep the ledger logic consistent with double-entry accounting.
- Add or update tests when changing money movement behavior.

## 10. Good starting points

If you want to understand the code quickly, begin here:

- `app/main.py` — FastAPI app setup and health endpoints
- `app/config.py` — environment settings
- `app/database.py` — database engine and session configuration
- `app/services/ledger.py` — money movement rules
- `app/services/balance.py` — ledger-derived balances
- `app/services/idempotency.py` — request deduplication
- `app/routers/` — API endpoints
- `tests/` — verification and regression coverage

This is the fastest path to being productive in the repository.

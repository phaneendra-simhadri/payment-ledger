# Payment Ledger

A production-minded wallet and payment ledger built with FastAPI, asynchronous SQLAlchemy, PostgreSQL, Redis, and Celery. The service models deposits, withdrawals, and peer-to-peer transfers using auditable double-entry accounting.

The project is designed to demonstrate the difficult parts of payment infrastructure:

- Balances derived from an append-only ledger, never stored as mutable wallet totals.
- Idempotent write APIs using a client-supplied `Idempotency-Key`.
- Account row locks for balance-check-then-write operations.
- Durable webhook delivery records with Celery retries.
- Scheduled reconciliation of every ledger transaction.
- A small React demo that makes the idempotency behavior visible.

## Contents

- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Using the application](#using-the-application)
- [API reference](#api-reference)
- [Accounting model](#accounting-model)
- [Idempotency and concurrency](#idempotency-and-concurrency)
- [Webhooks and background jobs](#webhooks-and-background-jobs)
- [Configuration](#configuration)
- [Database migrations](#database-migrations)
- [Frontend demo](#frontend-demo)
- [Testing and load testing](#testing-and-load-testing)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)

## Architecture

```text
React/Vite frontend
        |
        | JSON + JWT + Idempotency-Key
        v
FastAPI API  ----->  PostgreSQL
     |
     +-------------> Redis <------------- Celery worker
                                         |
                                         +-- Webhook delivery
                                         +-- Scheduled reconciliation
```

### Technology choices

| Concern | Technology |
| --- | --- |
| API | FastAPI |
| Validation | Pydantic v2 |
| ORM | SQLAlchemy 2.0 async |
| Production database | PostgreSQL 15+ |
| Local fallback database | SQLite with `aiosqlite` |
| Queue and rate limiting | Redis |
| Background work | Celery and Celery Beat |
| Schema management | Alembic |
| Authentication | JWT with HS256 |
| Password hashing | bcrypt through Passlib |
| Frontend | React 18, Vite, Axios, React Router |

## Quick start

### Option A: Docker Compose

This is the recommended way to run the complete backend stack. From the repository root:

```powershell
docker compose up --build
```

The services are then available at:

| Service | URL |
| --- | --- |
| API | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| OpenAPI JSON | http://localhost:8000/openapi.json |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

The API container applies `alembic upgrade head` before starting. The worker and beat containers process webhook and reconciliation tasks. Start the separate Vite frontend with the [Frontend demo](#frontend-demo) instructions.

Stop the stack with:

```powershell
docker compose down
```

Remove the PostgreSQL volume as well when you need a completely clean database:

```powershell
docker compose down -v
```

### Option B: Run the API locally

The repository includes a local SQLite default, so the API can be started without PostgreSQL or Redis for basic development. Redis enables distributed rate limiting and Celery dispatch.

1. Create the virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

3. Start the API from the repository root:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

For the full local infrastructure, start PostgreSQL and Redis first:

```powershell
docker compose up -d db redis
```

Then set the connection strings before starting the API:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/payment_ledger"
$env:REDIS_URL = "redis://localhost:6379/0"
$env:JWT_SECRET = "replace-this-local-secret"
$env:WEBHOOK_SECRET = "replace-this-webhook-secret"
$env:FRONTEND_URL = "http://localhost:5173"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

> Use the same Python environment for installation and startup. On Windows, prefer `.venv\Scripts\python.exe -m uvicorn ...` when the global interpreter has different packages installed.

Check that the API is ready:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Expected response when both dependencies are available:

```json
{"status":"ok","db":"ok","redis":"ok"}
```

## Using the application

### Login through the frontend

1. Open http://localhost:5173.
2. Select **Create an account**.
3. Register with a valid email, name, and password of at least six characters.
4. Return to the login page and use the same credentials.
5. Create an INR wallet from the overview screen if the account does not have one.

The frontend stores the JWT in session storage for the current browser session. It discovers wallets through `GET /api/v1/accounts`.

### Basic API flow

The normal payment flow is:

1. Register a user.
2. Log in and capture the JWT.
3. Create a wallet.
4. Deposit funds.
5. Create a second user and wallet.
6. Transfer money to the second wallet.
7. Read balances and transaction history.

The examples below use PowerShell. Replace the placeholder IDs with values returned by the API.

Register:

```powershell
$base = "http://localhost:8000/api/v1"
$email = "user-$([guid]::NewGuid())@example.com"
$password = "secret123"
$body = @{ email = $email; name = "Demo User"; password = $password } | ConvertTo-Json
Invoke-RestMethod "$base/auth/register" -Method Post -ContentType "application/json" -Body $body
```

Login:

```powershell
$login = Invoke-RestMethod "$base/auth/login" -Method Post -ContentType "application/json" -Body (@{ email = $email; password = $password } | ConvertTo-Json)
$headers = @{ Authorization = "Bearer $($login.access_token)" }
```

Create and list wallets:

```powershell
$wallet = Invoke-RestMethod "$base/accounts" -Method Post -Headers $headers -ContentType "application/json" -Body (@{ currency = "INR" } | ConvertTo-Json)
Invoke-RestMethod "$base/accounts" -Headers $headers
```

Deposit:

```powershell
$depositBody = @{ account_id = $wallet.account_id; amount = "1000.00"; currency = "INR" } | ConvertTo-Json
$depositHeaders = $headers.Clone()
$depositHeaders["Idempotency-Key"] = [guid]::NewGuid().ToString()
Invoke-RestMethod "$base/deposits" -Method Post -Headers $depositHeaders -ContentType "application/json" -Body $depositBody
```

## API reference

All API routes use the `/api/v1` prefix except `/health`. Protected routes require:

```http
Authorization: Bearer <access-token>
```

All mutating payment routes also require:

```http
Idempotency-Key: <unique-key>
```

### Authentication

| Method | Route | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/auth/register` | No | Create a user |
| `POST` | `/auth/login` | No | Return a 24-hour JWT |

Registration body:

```json
{"email":"user@example.com","name":"Demo User","password":"secret123"}
```

Login response:

```json
{"access_token":"<jwt>","token_type":"bearer"}
```

### Accounts

| Method | Route | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/accounts` | Yes | List the authenticated user's active wallets |
| `POST` | `/accounts` | Yes | Create one wallet for a currency |
| `GET` | `/accounts/{account_id}/balance` | Yes | Derive the wallet balance from ledger entries |
| `GET` | `/accounts/{account_id}/transactions` | Yes | Return paginated wallet history |

Create wallet body:

```json
{"currency":"INR"}
```

Balance response:

```json
{"account_id":"<uuid>","balance":"1000.00","currency":"INR"}
```

History supports `page` and `page_size` query parameters. The default page size is 20 and the maximum is 100. `direction` is relative to the requested wallet: `debit` means money left and `credit` means money arrived.

### Deposits

`POST /deposits` requires a wallet owned by the authenticated user.

```json
{"account_id":"<wallet-id>","amount":"500.00","currency":"INR"}
```

A successful response has status `completed`. Common errors:

| Status | Error | Meaning |
| --- | --- | --- |
| `400` | `MISSING_IDEMPOTENCY_KEY` | Header was omitted |
| `403` | `UNAUTHORIZED` | Wallet is not owned by the user |
| `409` | `IDEMPOTENCY_KEY_CONFLICT` | Key was reused with a different payload |
| `422` | `INVALID_AMOUNT` | Amount is zero, negative, or malformed |
| `422` | `CURRENCY_MISMATCH` | Request currency differs from wallet currency |

### Withdrawals

`POST /withdrawals` uses a row lock before checking balance.

```json
{"account_id":"<wallet-id>","amount":"200.00","currency":"INR"}
```

Insufficient funds return `422` with error `INSUFFICIENT_FUNDS`. No ledger entries are written when the balance check fails.

### Transfers

`POST /transfers` moves funds from a wallet owned by the caller to another wallet.

```json
{
  "from_account_id":"<sender-wallet-id>",
  "to_account_id":"<recipient-wallet-id>",
  "amount":"300.00",
  "currency":"INR",
  "description":"Lunch split"
}
```

Common errors:

| Status | Error | Meaning |
| --- | --- | --- |
| `400` | `SAME_ACCOUNT_TRANSFER` | Source and destination are identical |
| `403` | `UNAUTHORIZED` | Caller does not own the source wallet |
| `422` | `INSUFFICIENT_FUNDS` | Source balance is too low |
| `422` | `CURRENCY_MISMATCH` | Wallet currencies or request currency differ |

### Health and operations

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check database and Redis connectivity; returns `503` if either is down |
| `GET` | `/api/v1/metrics` | Read in-memory counters |
| `GET` | `/api/v1/admin/reconcile` | Trigger reconciliation in the current implementation |

The reconciliation route is currently exposed as a development operation. It should be protected and changed to `POST` before production deployment.

## Accounting model

The system uses double-entry accounting. Each completed money movement creates exactly two positive ledger rows with equal amounts:

| Operation | Debit | Credit |
| --- | --- | --- |
| Deposit 500 | `system_bank` 500 | user wallet 500 |
| Withdrawal 200 | user wallet 200 | `system_bank` 200 |
| Transfer 300 | sender wallet 300 | recipient wallet 300 |

A wallet balance is always derived as:

```sql
SUM(credits) - SUM(debits)
```

There is no mutable balance column. The ledger is the source of truth and remains auditable by transaction ID.

## Idempotency and concurrency

Every deposit, withdrawal, and transfer requires an idempotency key. The request body is canonicalized with sorted JSON keys and hashed with SHA-256.

The intended behavior is:

- New key: create and process a transaction.
- Same key and same payload: replay the original transaction response.
- Same key and different payload: return `409 IDEMPOTENCY_KEY_CONFLICT`.
- Keys older than 24 hours are eligible for reuse.
- A database uniqueness constraint protects the key when concurrent requests race.

Withdrawals and transfers lock the relevant account rows with `SELECT ... FOR UPDATE` before checking balance and writing ledger entries. This keeps the balance check and ledger write in the same database transaction. Transfers lock both accounts so unrelated wallets can continue independently.

The frontend's **Prove idempotency** action sends two identical transfer requests in parallel with one generated key. Both responses should contain the same `transaction_id`, while only one transaction is committed.

## Webhooks and background jobs

Successful transfers create a `webhook_deliveries` row in the same database transaction as the ledger entries. After commit, the API attempts to enqueue `deliver_webhook` in Celery. If the broker is unavailable, the durable delivery row remains available for recovery.

Webhook requests include:

- `Content-Type: application/json`
- `X-Signature: sha256=<HMAC-SHA256>`
- `X-Delivery-Id: <delivery-id>`

Delivery failures use retry delays of 10 seconds, 30 seconds, 2 minutes, 10 minutes, and 1 hour. After the retry budget is exhausted, the delivery is marked `dead`.

Celery Beat schedules reconciliation hourly through `run_reconciliation_task`. Reconciliation checks every complete transaction has exactly two equal-and-opposite entries and writes a `reconciliation_logs` record.

## Configuration

Settings are loaded from `.env` when present. Copy `.env.example` to `.env` for local customization.

| Variable | Default | Description |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite+aiosqlite:///./ledger.db` | Async SQLAlchemy database URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis broker, backend, and rate-limit store |
| `JWT_SECRET` | `change-this-in-production` | JWT signing secret; replace outside local development |
| `WEBHOOK_SECRET` | `change-this-in-production` | Outbound webhook HMAC secret |
| `WEBHOOK_URL` | `http://localhost:8000/webhooks` | Transfer webhook target |
| `FRONTEND_URL` | `http://localhost:5173` | Allowed browser origin |
| `ENVIRONMENT` | `development` | `development` or `production` |
| `LOG_LEVEL` | `INFO` | Application log level |
| `RECONCILIATION_INTERVAL_MINUTES` | `60` | Intended reconciliation interval |
| `IDEMPOTENCY_KEY_TTL_HOURS` | `24` | Idempotency replay window |

Do not commit `.env` or real secrets. The repository ignores `.env` files and generated local databases.

## Database migrations

Alembic owns the production schema. The initial revision is in `alembic/versions/0001_initial_schema.py`.

Run migrations manually:

```powershell
alembic upgrade head
```

Create a new revision after changing models:

```powershell
alembic revision --autogenerate -m "describe the schema change"
alembic upgrade head
```

Inspect the current revision:

```powershell
alembic current
alembic history
```

In development, `ENVIRONMENT=development` allows startup schema initialization for the local SQLite fallback. In Compose production mode, the API runs Alembic before Uvicorn and does not call `create_all()`.

## Frontend demo

The frontend is intentionally thin and focused on demonstrating the backend:

- `Login` and `Register` screens.
- Wallet discovery and creation.
- Ledger-derived balance dashboard.
- Transfer form with generated idempotency keys.
- Parallel idempotency proof.
- Paginated transaction history with credit/debit coloring.

Start it from the repository root:

```powershell
Set-Location frontend
npm install
npm run dev
```

Open http://localhost:5173.

To point the frontend at another API:

```powershell
$env:VITE_API_BASE_URL = "https://your-api.example.com/api/v1"
npm run dev
```

Build it for deployment:

```powershell
npm run build
npm run preview
```

The generated `frontend/dist/` directory is ignored by Git.

## Testing and load testing

Run the Python test suite:

```powershell
python -m pytest -q
```

Run static compilation checks:

```powershell
python -m compileall -q app locustfile.py
```

Run the frontend build:

```powershell
Set-Location frontend
npm run build
```

Start a Locust load test against a running API:

```powershell
locust -f locustfile.py --host=http://localhost:8000
```

The Locust user registers, logs in, creates and funds a wallet, creates a recipient wallet, and then exercises transfers and balance reads.

## Troubleshooting

### Login or registration returns 500

Confirm the API uses the same virtual environment where dependencies were installed:

```powershell
.\.venv\Scripts\python.exe -c "import bcrypt, email_validator; print(bcrypt.__version__)"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The project pins `bcrypt==4.0.1` because it is compatible with the declared Passlib version. `email-validator` is required by Pydantic's `EmailStr`.

### Frontend says network error

Check both processes:

- Frontend: http://localhost:5173
- API: http://localhost:8000/health

If the API runs elsewhere, set `VITE_API_BASE_URL` and restart Vite. The value must include `/api/v1`.

### Health reports Redis down

Start Redis with Docker Compose:

```powershell
docker compose up -d redis
```

Then restart the API. Basic local API work can use the in-memory rate-limit fallback, but Celery webhooks require Redis.

### Port 8000 is already in use

Find the listener:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess
```

Stop only the stale development process, then start the API again. The frontend Vite server normally uses port 5173.

### Database schema is stale

For a PostgreSQL deployment:

```powershell
alembic upgrade head
```

For a disposable local SQLite database, stop the API and remove `ledger.db`, then start it again. Do not delete a database containing data you need.

## Project layout

```text
.
├── app/
│   ├── main.py                 # FastAPI application and middleware
│   ├── config.py               # Environment-backed settings
│   ├── database.py             # Async engine, sessions, system accounts
│   ├── middleware/auth.py      # JWT authentication
│   ├── models/                 # SQLAlchemy persistence models
│   ├── routers/                # Auth, accounts, deposits, withdrawals, transfers
│   ├── schemas/                # Pydantic request and response schemas
│   ├── services/               # Balance, ledger, idempotency, metrics, rate limits
│   ├── jobs/                   # Reconciliation
│   └── workers/                # Celery configuration and webhook task
├── alembic/
│   ├── env.py
│   └── versions/               # Versioned database migrations
├── frontend/
│   ├── src/api/                # Axios client
│   ├── src/components/         # Balance, transaction, idempotency UI
│   ├── src/context/            # Auth context
│   └── src/pages/              # Login, register, dashboard, send, history
├── tests/                      # Pytest suite
├── docker-compose.yml          # API, worker, beat, PostgreSQL, Redis
├── Dockerfile
├── locustfile.py
├── requirements.txt
└── .env.example
```

## Security notes

- Replace all default secrets before any shared or production deployment.
- Use HTTPS for the API and frontend in deployed environments.
- Passwords are stored as bcrypt hashes, never plaintext.
- JWTs expire after 24 hours.
- Do not log passwords, email addresses, or complete account identifiers.
- Restrict `FRONTEND_URL` to the actual deployed frontend origin.
- Protect the reconciliation operation with an admin authorization policy before production use.

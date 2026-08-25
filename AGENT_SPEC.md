# Payment & Wallet Ledger System — Agent Implementation Spec

> This document is the single source of truth. Implement exactly what is described here — no extra features, no shortcuts, no substitutions unless explicitly marked as optional. Read the entire document before writing a single line of code.

---

## 1. Project Overview

A production-grade double-entry payment ledger system — a stripped-down Razorpay/PhonePe engine. The system handles wallet creation, deposits, withdrawals, and P2P transfers with full idempotency, concurrency safety, async webhook delivery, and a reconciliation job.

**Not a toy CRUD wallet app.** Every design decision must reflect how real fintech systems work.

---

## 2. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| Web framework | FastAPI | Async, Pydantic v2 for schemas |
| ORM | SQLAlchemy 2.0 | Use async session |
| Database | PostgreSQL 15+ | Primary store for all data |
| Task queue | Celery + Redis | Async webhook delivery |
| Migrations | Alembic | All schema changes via migrations, never raw DDL on live DB |
| Testing | pytest + pytest-asyncio | All critical paths must have tests |
| Load testing | Locust | Script included, see section 10 |
| Containerisation | Docker + Docker Compose | Full local dev environment |
| Deployment | Render or Railway | Free tier, single Dockerfile |

---

## 3. Project Structure

```
payment-ledger/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app entry point
│   ├── database.py              # Async engine, session factory
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── account.py
│   │   ├── transaction.py
│   │   └── ledger_entry.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── account.py
│   │   ├── transaction.py
│   │   └── common.py
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── accounts.py
│   │   ├── deposits.py
│   │   ├── withdrawals.py
│   │   └── transfers.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── idempotency.py       # Idempotency key check logic
│   │   ├── ledger.py            # Core double-entry write logic
│   │   ├── balance.py           # Balance derivation from ledger
│   │   └── rate_limiter.py      # Token bucket per user
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── celery_app.py        # Celery config
│   │   └── webhook_tasks.py     # Webhook delivery + retry logic
│   ├── jobs/
│   │   ├── __init__.py
│   │   └── reconciliation.py   # Scheduled balance reconciliation
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── auth.py              # JWT validation middleware
│   └── config.py                # Settings via pydantic-settings
├── alembic/
│   ├── env.py
│   └── versions/
├── tests/
│   ├── conftest.py
│   ├── test_transfers.py        # Concurrency tests go here
│   ├── test_idempotency.py
│   └── test_reconciliation.py
├── locustfile.py
├── docker-compose.yml
├── Dockerfile
├── alembic.ini
├── requirements.txt
└── .env.example
```

---

## 4. Database Schema

Implement exactly these tables. Use Alembic for all migrations.

### 4.1 users

```sql
CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       VARCHAR(255) UNIQUE NOT NULL,
    name        VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.2 accounts

```sql
CREATE TABLE accounts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id     UUID REFERENCES users(id) ON DELETE RESTRICT,
    account_type VARCHAR(20) NOT NULL,
    -- allowed values: 'user_wallet', 'system_bank', 'system_revenue'
    -- system_bank: counterparty for all deposits/withdrawals (money comes from/goes to here)
    -- system_revenue: counterparty for fees (future use)
    currency     VARCHAR(3) NOT NULL DEFAULT 'INR',
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT account_type_check CHECK (
        account_type IN ('user_wallet', 'system_bank', 'system_revenue')
    )
);
```

### 4.3 transactions

```sql
CREATE TABLE transactions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key  VARCHAR(64) UNIQUE NOT NULL,
    request_hash     VARCHAR(64) NOT NULL,
    -- SHA-256 of the request payload (sorted keys), for 409 detection
    type             VARCHAR(20) NOT NULL,
    -- allowed values: 'deposit', 'withdrawal', 'transfer'
    status           VARCHAR(20) NOT NULL DEFAULT 'pending',
    -- allowed values: 'pending', 'completed', 'failed'
    amount           NUMERIC(18, 2) NOT NULL,
    currency         VARCHAR(3) NOT NULL DEFAULT 'INR',
    from_account_id  UUID REFERENCES accounts(id),
    to_account_id    UUID REFERENCES accounts(id),
    metadata         JSONB,
    -- store any extra info: description, merchant_ref, etc.
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT amount_positive CHECK (amount > 0),
    CONSTRAINT type_check CHECK (type IN ('deposit', 'withdrawal', 'transfer')),
    CONSTRAINT status_check CHECK (status IN ('pending', 'completed', 'failed'))
);

CREATE INDEX idx_transactions_idempotency ON transactions(idempotency_key);
CREATE INDEX idx_transactions_from_account ON transactions(from_account_id);
CREATE INDEX idx_transactions_to_account ON transactions(to_account_id);
CREATE INDEX idx_transactions_created_at ON transactions(created_at);
```

### 4.4 ledger_entries

```sql
CREATE TABLE ledger_entries (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
    account_id     UUID NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    entry_type     VARCHAR(6) NOT NULL,
    -- ONLY 'debit' or 'credit' — nothing else
    amount         NUMERIC(18, 2) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT entry_type_check CHECK (entry_type IN ('debit', 'credit')),
    CONSTRAINT amount_positive CHECK (amount > 0)
);

CREATE INDEX idx_ledger_account ON ledger_entries(account_id);
CREATE INDEX idx_ledger_transaction ON ledger_entries(transaction_id);
```

### 4.5 webhook_deliveries

```sql
CREATE TABLE webhook_deliveries (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID NOT NULL REFERENCES transactions(id),
    target_url     TEXT NOT NULL,
    payload        JSONB NOT NULL,
    status         VARCHAR(20) NOT NULL DEFAULT 'pending',
    -- allowed: 'pending', 'delivered', 'failed', 'dead'
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_attempted_at TIMESTAMPTZ,
    next_retry_at  TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.6 reconciliation_logs

```sql
CREATE TABLE reconciliation_logs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    accounts_checked INTEGER NOT NULL,
    mismatches_found INTEGER NOT NULL DEFAULT 0,
    mismatch_details JSONB,
    -- array of {account_id, expected_balance, derived_balance, delta}
    status         VARCHAR(20) NOT NULL
    -- 'ok' or 'mismatch_found'
);
```

---

## 5. Core Design Rules — Do Not Violate These

### 5.1 Double-entry invariant

Every money movement creates exactly two ledger entries. The sum of all entries for a transaction must be zero (one debit, one credit, equal amounts).

```
Deposit ₹500 into user wallet:
  DEBIT  system_bank        ₹500
  CREDIT user_wallet        ₹500

Withdrawal ₹200 from user wallet:
  DEBIT  user_wallet        ₹200
  CREDIT system_bank        ₹200

Transfer ₹300 from user A to user B:
  DEBIT  user_A_wallet      ₹300
  CREDIT user_B_wallet      ₹300
```

Never update a balance column directly. Never store a mutable balance. Balance is always derived:

```sql
SELECT
    COALESCE(SUM(CASE WHEN entry_type = 'credit' THEN amount ELSE 0 END), 0) -
    COALESCE(SUM(CASE WHEN entry_type = 'debit'  THEN amount ELSE 0 END), 0)
    AS balance
FROM ledger_entries
WHERE account_id = :account_id;
```

### 5.2 Idempotency

Every mutating endpoint (`POST /deposits`, `POST /withdrawals`, `POST /transfers`) requires an `Idempotency-Key` header. Requests missing this header receive `400 Bad Request`.

The idempotency service must:
1. Compute `SHA-256` of the request body (JSON with sorted keys) as `request_hash`.
2. Try to find an existing transaction with the same `idempotency_key`.
3. If not found → insert a `pending` transaction, process, return result.
4. If found and `request_hash` matches → return the original response (replay).
5. If found and `request_hash` differs → return `409 Conflict`.
6. Keys expire after 24 hours — after expiry, the key can be reused for a new request.

The `UNIQUE` constraint on `transactions.idempotency_key` is the enforcement mechanism. No separate Redis cache for this. The DB constraint handles concurrent duplicates atomically.

### 5.3 Concurrency safety

All balance-check-then-write operations must use `SELECT ... FOR UPDATE` to acquire a row-level lock. This serialises concurrent requests on the same account without blocking unrelated accounts.

```python
# In the transfer service — pseudocode for the pattern
async with db.begin():
    # Lock the sender's account row
    account = await db.execute(
        select(Account)
        .where(Account.id == from_account_id)
        .with_for_update()
    )
    
    balance = await get_balance(from_account_id, db)
    if balance < amount:
        raise InsufficientFundsError()
    
    # Write ledger entries inside the same transaction
    await write_ledger_entries(transaction_id, from_account_id, to_account_id, amount, db)
```

Never check balance and then write outside a single DB transaction.

---

## 6. API Contract

Base URL: `/api/v1`

All requests/responses use `application/json`. All amounts in `NUMERIC` — never floats. Currency always uppercase 3-letter ISO code.

### 6.1 Auth

`POST /auth/register`
```json
Request:  { "email": "phani@example.com", "name": "Phani", "password": "secret" }
Response: { "user_id": "uuid", "email": "...", "name": "..." }
```

`POST /auth/login`
```json
Request:  { "email": "phani@example.com", "password": "secret" }
Response: { "access_token": "jwt...", "token_type": "bearer" }
```

JWT payload must include `user_id` and `exp` (24-hour expiry). All subsequent endpoints require `Authorization: Bearer <token>`.

---

### 6.2 Accounts

`POST /accounts`
```json
Request:  { "currency": "INR" }
Response: { "account_id": "uuid", "account_type": "user_wallet", "currency": "INR", "created_at": "..." }
```
Creates a `user_wallet` account for the authenticated user. One account per user per currency.

`GET /accounts/{account_id}/balance`
```json
Response: { "account_id": "uuid", "balance": "1250.00", "currency": "INR" }
```
Balance derived from ledger — never from a stored column.

`GET /accounts/{account_id}/transactions`
```json
Response: {
  "account_id": "uuid",
  "transactions": [
    {
      "transaction_id": "uuid",
      "type": "transfer",
      "amount": "500.00",
      "direction": "debit",
      "status": "completed",
      "created_at": "..."
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 45
}
```
Paginated. Default page size 20. `direction` is from the perspective of `account_id` — debit means money left, credit means money arrived.

---

### 6.3 Deposits

`POST /deposits`

Headers required: `Idempotency-Key: <uuid>`

```json
Request: {
  "account_id": "uuid",
  "amount": "500.00",
  "currency": "INR"
}

Response 200: {
  "transaction_id": "uuid",
  "status": "completed",
  "amount": "500.00",
  "currency": "INR",
  "created_at": "..."
}

Response 400: { "error": "MISSING_IDEMPOTENCY_KEY", "message": "Idempotency-Key header is required" }
Response 409: { "error": "IDEMPOTENCY_KEY_CONFLICT", "message": "Key reused with different payload" }
Response 422: { "error": "INVALID_AMOUNT", "message": "Amount must be greater than 0" }
```

Ledger entries created:
- DEBIT `system_bank` ₹500
- CREDIT `account_id` ₹500

---

### 6.4 Withdrawals

`POST /withdrawals`

Headers required: `Idempotency-Key: <uuid>`

```json
Request: {
  "account_id": "uuid",
  "amount": "200.00",
  "currency": "INR"
}

Response 200: { "transaction_id": "uuid", "status": "completed", "amount": "200.00", ... }
Response 422: { "error": "INSUFFICIENT_FUNDS", "message": "Account balance is insufficient" }
```

Must use `SELECT FOR UPDATE` before balance check. Ledger entries:
- DEBIT `account_id` ₹200
- CREDIT `system_bank` ₹200

---

### 6.5 Transfers

`POST /transfers`

Headers required: `Idempotency-Key: <uuid>`

```json
Request: {
  "from_account_id": "uuid",
  "to_account_id": "uuid",
  "amount": "300.00",
  "currency": "INR",
  "description": "Lunch split"  // optional, stored in metadata
}

Response 200: {
  "transaction_id": "uuid",
  "status": "completed",
  "from_account_id": "uuid",
  "to_account_id": "uuid",
  "amount": "300.00",
  "currency": "INR",
  "created_at": "..."
}

Response 400: { "error": "SAME_ACCOUNT_TRANSFER", "message": "Cannot transfer to the same account" }
Response 403: { "error": "UNAUTHORIZED", "message": "You do not own the source account" }
Response 422: { "error": "INSUFFICIENT_FUNDS", "message": "..." }
Response 422: { "error": "CURRENCY_MISMATCH", "message": "Account currencies must match" }
```

Ledger entries:
- DEBIT `from_account_id` ₹300
- CREDIT `to_account_id` ₹300

After successful DB commit, enqueue a webhook delivery task to Celery.

---

### 6.6 Health

`GET /health`
```json
Response: { "status": "ok", "db": "ok", "redis": "ok" }
```
Check both DB and Redis connectivity. Return 503 if either is down.

---

## 7. Idempotency Service — Implementation Detail

```python
# app/services/idempotency.py

import hashlib
import json
from datetime import datetime, timedelta, timezone

IDEMPOTENCY_KEY_TTL_HOURS = 24

def compute_request_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()

async def check_idempotency(
    idempotency_key: str,
    request_payload: dict,
    db: AsyncSession
) -> tuple[bool, Transaction | None]:
    """
    Returns (is_fresh, existing_transaction)
    is_fresh=True  → process this request normally
    is_fresh=False → replay existing_transaction
    Raises HTTP 409 on hash mismatch.
    """
    request_hash = compute_request_hash(request_payload)
    expiry_cutoff = datetime.now(timezone.utc) - timedelta(hours=IDEMPOTENCY_KEY_TTL_HOURS)

    result = await db.execute(
        select(Transaction)
        .where(Transaction.idempotency_key == idempotency_key)
        .where(Transaction.created_at > expiry_cutoff)
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        return True, None

    if existing.request_hash != request_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "IDEMPOTENCY_KEY_CONFLICT",
                "message": "Idempotency key reused with different payload"
            }
        )

    return False, existing
```

---

## 8. Async Worker — Webhook Delivery

### 8.1 Celery config

```python
# app/workers/celery_app.py

from celery import Celery

celery_app = Celery(
    "payment_ledger",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/1"
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,          # Only ack after task completes, not when picked up
    task_reject_on_worker_lost=True,
)
```

### 8.2 Webhook task with exponential backoff

```python
# app/workers/webhook_tasks.py

import hmac
import hashlib
import httpx
from celery import Task
from app.workers.celery_app import celery_app

WEBHOOK_SECRET = "your-secret-key-from-env"

RETRY_DELAYS = [10, 30, 120, 600, 3600]
# 10s, 30s, 2min, 10min, 1hr — after 5 failures, move to dead

def sign_payload(payload: str) -> str:
    return hmac.new(
        WEBHOOK_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

@celery_app.task(
    bind=True,
    max_retries=5,
    name="deliver_webhook"
)
def deliver_webhook(self: Task, webhook_delivery_id: str):
    # Fetch webhook_delivery row from DB
    # Build payload JSON
    # Sign payload: X-Signature: sha256=<hmac>
    # POST to target_url with 5s timeout
    # On success: update status to 'delivered'
    # On failure: increment attempts, update last_attempted_at
    #   if attempts < max_retries: retry with countdown=RETRY_DELAYS[attempts]
    #   if attempts >= max_retries: update status to 'dead'

    try:
        delivery = get_webhook_delivery(webhook_delivery_id)
        payload_str = json.dumps(delivery.payload)
        signature = sign_payload(payload_str)

        response = httpx.post(
            delivery.target_url,
            content=payload_str,
            headers={
                "Content-Type": "application/json",
                "X-Signature": f"sha256={signature}",
                "X-Delivery-Id": str(delivery.id),
            },
            timeout=5.0
        )
        response.raise_for_status()
        mark_delivered(webhook_delivery_id)

    except Exception as exc:
        attempt = self.request.retries
        if attempt < len(RETRY_DELAYS):
            mark_retry(webhook_delivery_id, RETRY_DELAYS[attempt])
            raise self.retry(exc=exc, countdown=RETRY_DELAYS[attempt])
        else:
            mark_dead(webhook_delivery_id)
```

---

## 9. Reconciliation Job

Runs on a schedule — every 60 minutes via Celery Beat or a cron job. Can also be triggered manually via `POST /admin/reconcile` (admin-only endpoint).

### Logic

```python
# app/jobs/reconciliation.py

async def run_reconciliation(db: AsyncSession):
    """
    For every active user_wallet account:
    1. Derive balance from ledger_entries
    2. Compare against last known reconciled balance (or re-derive expected)
    3. Flag any mismatch
    4. Write a reconciliation_log row with results
    """
    accounts = await get_all_active_user_wallets(db)
    mismatches = []

    for account in accounts:
        derived = await derive_balance(account.id, db)
        # In a real system you'd compare against a shadow table or checkpoint
        # For this project: verify that credit sum - debit sum = derived balance
        # and that every transaction has exactly 2 ledger entries summing to zero
        entries = await get_ledger_entries(account.id, db)
        for txn_id, txn_entries in group_by_transaction(entries):
            total = sum(e.amount if e.entry_type == 'credit' else -e.amount for e in txn_entries)
            if abs(total) > Decimal('0.001'):
                mismatches.append({
                    "account_id": str(account.id),
                    "transaction_id": str(txn_id),
                    "expected_delta": "0.00",
                    "actual_delta": str(total)
                })

    await write_reconciliation_log(
        accounts_checked=len(accounts),
        mismatches_found=len(mismatches),
        mismatch_details=mismatches,
        status="ok" if not mismatches else "mismatch_found",
        db=db
    )

    if mismatches:
        # Log at ERROR level — in production this pages an on-call engineer
        logger.error(f"Reconciliation found {len(mismatches)} mismatches", extra={"mismatches": mismatches})
```

---

## 10. Rate Limiting

Token bucket per authenticated user. Implemented in Redis.

```
Limit: 10 requests per second per user_id for mutating endpoints
Limit: 60 requests per minute per user_id for read endpoints
```

On limit exceeded: return `429 Too Many Requests` with header `Retry-After: <seconds>`.

Use `fastapi-limiter` or implement manually with Redis INCR + EXPIRE.

---

## 11. Security

### JWT auth
- HS256 algorithm, secret from environment variable `JWT_SECRET`
- 24-hour expiry
- Payload: `{ "user_id": "uuid", "exp": timestamp }`
- All non-auth endpoints require valid token

### Password storage
- `bcrypt` with work factor 12
- Never store plaintext

### Webhook signature
- Every outbound webhook has `X-Signature: sha256=<hmac>` header
- Secret from environment variable `WEBHOOK_SECRET`
- Receiving systems can verify using the same HMAC computation

### Sensitive fields
- Store passwords hashed only
- No PII in logs — never log email, password, or full account IDs in plain text

---

## 12. Observability

### Structured logging
Use `structlog` or Python's `logging` with JSON formatter. Every log line must include:
- `timestamp`
- `level`
- `event`
- `request_id` (UUID generated per request, propagated via middleware)
- `user_id` (when available)
- `transaction_id` (when relevant)

```python
# Example log — transfer completed
{
  "timestamp": "2025-09-01T10:23:45Z",
  "level": "info",
  "event": "transfer_completed",
  "request_id": "req_abc123",
  "user_id": "usr_xyz",
  "transaction_id": "txn_def",
  "amount": "500.00",
  "currency": "INR",
  "duration_ms": 42
}
```

### Metrics endpoint
`GET /metrics` — returns:
```json
{
  "transfers_total": 1240,
  "deposits_total": 890,
  "withdrawals_total": 430,
  "failed_transactions_total": 12,
  "webhook_dead_letter_total": 3,
  "p99_transfer_latency_ms": 85
}
```
Track these with simple in-memory counters (or use `prometheus_client` if you want a proper `/metrics` Prometheus scrape endpoint — optional).

---

## 13. Tests — Must Have

### test_idempotency.py
```
- Same key + same payload → second call returns original response, no new DB row
- Same key + different payload → 409
- Expired key (>24h) + same payload → treated as fresh, new transaction created
- Concurrent duplicate requests → only one transaction created, other gets replay
```

### test_transfers.py
```
- Happy path transfer → correct ledger entries created
- Insufficient funds → 422, no ledger entries created, transaction marked failed
- Concurrent withdrawals on same account → only one succeeds if balance allows only one
  (test this with threading.Thread or asyncio.gather firing simultaneous requests)
- Transfer to same account → 400
- Currency mismatch → 422
- Transfer from account you don't own → 403
```

### test_reconciliation.py
```
- Clean ledger → reconciliation logs status 'ok', mismatches=0
- Manually insert a bad ledger entry → reconciliation detects and logs the mismatch
```

---

## 14. Load Test

```python
# locustfile.py

from locust import HttpUser, task, between

class PaymentUser(HttpUser):
    wait_time = between(0.5, 2)

    def on_start(self):
        # Register + login, store token
        pass

    @task(3)
    def transfer(self):
        import uuid
        self.client.post(
            "/api/v1/transfers",
            json={
                "from_account_id": self.from_account,
                "to_account_id": self.to_account,
                "amount": "10.00",
                "currency": "INR"
            },
            headers={
                "Authorization": f"Bearer {self.token}",
                "Idempotency-Key": str(uuid.uuid4())
            }
        )

    @task(1)
    def check_balance(self):
        self.client.get(
            f"/api/v1/accounts/{self.from_account}/balance",
            headers={"Authorization": f"Bearer {self.token}"}
        )
```

Run: `locust -f locustfile.py --host=http://localhost:8000`
Target: sustain 100 concurrent users, p99 < 200ms for transfers.

---

## 15. Docker Compose

```yaml
version: '3.9'
services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/payment_ledger
      - REDIS_URL=redis://redis:6379/0
      - JWT_SECRET=change-this-in-production
      - WEBHOOK_SECRET=change-this-in-production
    depends_on:
      - db
      - redis
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  worker:
    build: .
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/payment_ledger
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - db
      - redis
    command: celery -A app.workers.celery_app worker --loglevel=info

  beat:
    build: .
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/payment_ledger
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - db
      - redis
    command: celery -A app.workers.celery_app beat --loglevel=info

  db:
    image: postgres:15
    environment:
      POSTGRES_DB: payment_ledger
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

volumes:
  pgdata:
```

---

## 16. Environment Variables

```bash
# .env.example
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/payment_ledger
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=change-this-in-production
WEBHOOK_SECRET=change-this-in-production
ENVIRONMENT=development         # development | production
LOG_LEVEL=INFO
RECONCILIATION_INTERVAL_MINUTES=60
IDEMPOTENCY_KEY_TTL_HOURS=24
```

---

## 17. Implementation Order

Build in this exact order. Do not skip ahead.

1. Docker Compose up — Postgres + Redis running
2. SQLAlchemy models + Alembic migration for all tables
3. Seed the `system_bank` and `system_revenue` accounts (run once on startup)
4. Auth endpoints (register, login, JWT middleware)
5. Account creation + balance endpoint
6. Deposits (with idempotency, double-entry ledger write)
7. Withdrawals (with idempotency, `SELECT FOR UPDATE`)
8. Transfers (with idempotency, `SELECT FOR UPDATE`, enqueue webhook)
9. Celery worker + webhook delivery task
10. Reconciliation job
11. Rate limiting middleware
12. Structured logging + request_id middleware
13. Metrics endpoint
14. Tests (write after each feature, not all at the end)
15. Locust load test script
16. Dockerfile + deploy to Render/Railway

---

## 18. What Makes This Stand Out in an Interview

When asked "walk me through your best project" — lead with these four points in order:

1. **Double-entry design** — "Balance is never stored, it's always derived from an append-only ledger. Every money movement creates exactly two entries summing to zero. This means the ledger is auditable and replayable — it cannot silently drift."

2. **Idempotency** — "Every mutating API requires a client-generated idempotency key. I use a unique DB constraint as the enforcement mechanism, not a cache. Even concurrent duplicate requests resolve correctly because the DB constraint is atomic."

3. **Concurrency correctness** — "I reproduced a race condition by firing concurrent withdrawal requests against the same account, watched it overdraw, then fixed it with row-level locking inside a DB transaction. I have a test that proves it's fixed."

4. **Reconciliation** — "A scheduled job independently re-derives every account balance from the ledger and flags mismatches. This is what real fintech systems run in production — a safety net that exists because bugs happen and silent balance drift is the worst possible failure mode."

---

*End of spec. Implement exactly this. Do not add features not listed here.*

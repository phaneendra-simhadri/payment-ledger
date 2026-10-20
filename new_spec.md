# Payment & Wallet Ledger System — Agent Implementation Spec

> This document is the single source of truth. Implement exactly what is described here — no extra features, no shortcuts, no substitutions unless explicitly marked as optional. Read the entire document before writing a single line of code.

---

## 1. Project Overview

A production-grade double-entry payment ledger system — a stripped-down Razorpay/PhonePe engine. The system handles wallet creation, deposits, withdrawals, and P2P transfers with full idempotency, concurrency safety, async webhook delivery, and a reconciliation job.

**Not a toy CRUD wallet app.** Every design decision must reflect how real fintech systems work.

### 1.1 What the system does

| Capability | Entry point | Guarantee it provides |
|---|---|---|
| Sign-up and sign-in | `POST /auth/register`, `POST /auth/login` | bcrypt password hashing, 24-hour HS256 JWT |
| Wallet creation and discovery | `POST /accounts`, `GET /accounts` | one `user_wallet` per user per currency |
| Ledger-derived balance | `GET /accounts/{account_id}/balance` | `SUM(credit) - SUM(debit)` — never a stored column |
| Deposit (money in) | `POST /deposits` | idempotent, double-entry against `system_bank` |
| Withdrawal (money out) | `POST /withdrawals` | idempotent, row-locked balance check |
| Peer-to-peer transfer | `POST /transfers` | idempotent, row-locked on both accounts, webhook enqueued |
| Transaction history | `GET /accounts/{account_id}/transactions` | paginated, direction-aware |
| Outbound webhooks | Celery worker | HMAC-signed, durable retry with backoff, dead-letter state |
| Scheduled reconciliation | Celery Beat / `GET /api/v1/admin/reconcile` | independently re-derives every transaction's balance |
| Rate limiting | middleware | per-user limits on mutating and read endpoints |
| Observability | `X-Request-ID` header, `GET /api/v1/metrics`, JSON logs | request correlation and counters |

### 1.2 The one invariant everything else protects

> **Every completed transaction has exactly two ledger entries — one debit, one credit, equal amounts, summing to zero.**

Every other rule in this document exists to keep that invariant true when requests arrive twice, arrive concurrently, arrive out of order, or arrive from a client that gave up halfway through.

### 1.3 How a transfer flows through the system

1. **Authenticate** — `Authorization: Bearer <jwt>` is decoded (HS256, `JWT_SECRET`), the `users` row is loaded, and `request.state.user` is set.
2. **Rate limit** — the middleware buckets the request per `user_id`: 10/s for mutating verbs, 60/min for reads. Over the limit → `429` + `Retry-After`.
3. **Validate the envelope** — `Idempotency-Key` header present (`400` otherwise), body parses, `amount > 0` (`422` otherwise), source ≠ destination (`400` otherwise).
4. **Idempotency lookup** — SHA-256 of the canonical request body is compared with the stored `request_hash` for that key:
   - same hash → the original transaction is replayed and nothing is written;
   - different hash → `409 IDEMPOTENCY_KEY_CONFLICT`;
   - no live row → the request is fresh and processing continues.
5. **Lock the accounts** — `SELECT ... FOR UPDATE` on both the source and the destination wallet rows, in a single statement, before anything is read or written.
6. **Ownership + currency checks** — the caller must own the source wallet (`403`), and both wallets plus the request must share one currency (`422`).
7. **Check the balance** — the balance is re-derived from `ledger_entries` while the lock is held. If it is short, the request stops with `422 INSUFFICIENT_FUNDS` and no rows are written.
8. **Write the double entry** — one `transactions` row (`status = 'completed'`) plus exactly two `ledger_entries` rows, committed in a single database transaction.
9. **Persist the webhook** — a `webhook_deliveries` row is written in the *same* transaction as the ledger entries, so a delivery can never exist without its money movement (and vice versa).
10. **Enqueue after commit** — `deliver_webhook.delay(...)` is called only after the commit succeeds. If the broker is unreachable, the durable delivery row stays in the database for recovery.
11. **Respond** — the caller gets the `transaction_id`; the same key replays this exact payload.

### 1.4 Architecture at a glance

```text
                    ┌──────────────────────────────┐
  Browser ────────► │  React + Vite frontend       │  (demo UI only)
                    └──────────────┬───────────────┘
                                   │ JSON + JWT + Idempotency-Key
                    ┌──────────────▼───────────────┐
                    │  FastAPI application          │
                    │  ├─ request-id middleware     │
                    │  ├─ rate-limit middleware      │
                    │  ├─ JWT auth dependency        │
                    │  ├─ routers (auth, accounts,   │
                    │  │   deposits, withdrawals,    │
                    │  │   transfers)                │
                    │  └─ services (ledger, balance, │
                    │      idempotency, metrics)    │
                    └──┬──────────────────────┬─────┘
                       │ SQLAlchemy async     │ enqueue after commit
                    ┌──▼───────────────┐  ┌───▼──────────────────┐
                    │  PostgreSQL 15   │  │  Redis               │
                    │  (ledger truth)  │  │  broker + rate limit │
                    └──┬────────────────┘  └───┬──────────────────┘
                       │                       │
                    ┌──▼───────────────────────▼┐
                    │  Celery worker            │
                    │  ├─ deliver_webhook       │ ──► receiver (HMAC signed)
                    │  └─ run_reconciliation_task│
                    └───────────────────────────┘
```

| Component | Responsibility | Source of truth for |
|---|---|---|
| `app/routers/*` | HTTP contract, status codes, error envelopes | — |
| `app/services/ledger.py` | Row locking and double-entry writes | Ledger shape |
| `app/services/balance.py` | Balance derivation from entries | Balance |
| `app/services/idempotency.py` | Key lookup, request hashing, replay/409 | Idempotency |
| `app/services/rate_limiter.py` | Per-user request budgets | — |
| `app/services/metrics.py` | In-process counters | — |
| `app/workers/webhook_tasks.py` | Delivery, signing, retries, dead-lettering | — |
| `app/jobs/reconciliation.py` | Independent re-derivation of the ledger | Audit |
| `app/middleware/auth.py` | JWT creation and validation | Identity |
| `app/database.py` | Engine, session factory, system-account seeding | — |
| `app/config.py` | Environment-backed settings | Configuration |

---

## 2. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| Web framework | FastAPI | Async, Pydantic v2 for schemas |
| ORM | SQLAlchemy 2.0 | Use async session |
| Database | PostgreSQL 15+ | Primary store for all data |
| Local dev database | SQLite via `aiosqlite` | Fallback when Postgres is not running — see section 20 |
| Task queue | Celery + Redis | Async webhook delivery |
| Migrations | Alembic | All schema changes via migrations, never raw DDL on live DB |
| Authentication | `python-jose` | HS256 JWT signing and verification |
| Password hashing | `passlib` + `bcrypt` | bcrypt, work factor 12 |
| Logging | `structlog` | JSON log lines |
| HTTP client | `httpx` | Outbound webhook delivery |
| Testing | pytest + pytest-asyncio | All critical paths must have tests |
| Load testing | Locust | Script included, see section 14 |
| Containerisation | Docker + Docker Compose | Full local dev environment |
| Deployment | Render or Railway | Free tier, single Dockerfile |
| Frontend | React 18 + Vite + TailwindCSS + Axios | Demo UI only, see section 17 |

---

## 3. Project Structure

```
payment-ledger/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app entry point, middleware, health, metrics
│   ├── database.py              # Async engine, session factory, system-account seeding
│   ├── config.py                # Settings via pydantic-settings
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── account.py
│   │   ├── transaction.py
│   │   ├── ledger_entry.py
│   │   ├── webhook_delivery.py
│   │   └── reconciliation_log.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── account.py
│   │   ├── transaction.py
│   │   └── common.py
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── auth.py              # register + login (JWT issue)
│   │   ├── accounts.py          # create/list wallets, balance, history
│   │   ├── deposits.py
│   │   ├── withdrawals.py
│   │   └── transfers.py
│   ├── services/
│   │   ├── idempotency.py       # Idempotency key check logic
│   │   ├── ledger.py            # Core double-entry write logic + row locking
│   │   ├── balance.py           # Balance derivation from ledger
│   │   ├── rate_limiter.py      # Token bucket per user
│   │   └── metrics.py           # In-process counters for GET /api/v1/metrics
│   ├── workers/
│   │   ├── celery_app.py        # Celery config + beat schedule
│   │   └── webhook_tasks.py     # Webhook delivery + retry logic
│   ├── jobs/
│   │   ├── __init__.py
│   │   └── reconciliation.py   # Scheduled balance reconciliation
│   └── middleware/
│       └── auth.py              # JWT validation middleware
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 0001_initial_schema.py
├── tests/
│   ├── conftest.py
│   ├── test_transfers.py        # Concurrency tests go here
│   ├── test_idempotency.py
│   └── test_reconciliation.py
├── frontend/                    # React + Vite demo UI (section 17)
├── locustfile.py
├── docker-compose.yml
├── Dockerfile
├── alembic.ini
├── pytest.ini                   # asyncio_mode = auto, pythonpath = .
├── requirements.txt
├── .env.example
├── README.md                    # Public-facing project documentation
└── WORKING.md                   # Developer setup guide
```

> `app/services/`, `app/workers/` and `app/middleware/` have **no** `__init__.py` — they work as PEP 420 namespace packages. Every other package directory has one.

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

### 4.7 Schema notes that matter in practice

- **Extension.** The migration runs `CREATE EXTENSION IF NOT EXISTS pgcrypto` on PostgreSQL so `gen_random_uuid()` is always available.
- **`JSONB` vs `JSON`.** `transactions.metadata`, `webhook_deliveries.payload` and `reconciliation_logs.mismatch_details` are `JSONB` on PostgreSQL and fall back to `JSON` on SQLite, so the same migration runs on both.
- **ORM attribute name.** The `transactions.metadata` column is exposed as `Transaction.request_metadata` in SQLAlchemy, because `metadata` is a reserved name on declarative models.
- **Unique key index.** `transactions.idempotency_key` is `UNIQUE`; the unique constraint already creates the backing index, which is why `idx_transactions_idempotency` is a redundant-but-harmless extra index.
- **No `balance` column anywhere.** There is deliberately no denormalised balance on `accounts` — a balance is always a query over `ledger_entries`.
- **`created_at` is application time.** It is stamped in Python (`datetime.now(timezone.utc)`), which is what the idempotency TTL comparison uses. Keep every writer on UTC.
- **`ON DELETE RESTRICT`** on `ledger_entries → transactions` and `ledger_entries → accounts` prevents audit history from being deleted out from under a transaction.
- **`accounts.owner_id` is nullable** because system accounts (`system_bank`, `system_revenue`) have no owner.
- **System accounts are seeded once** at startup by `app/database.py::ensure_system_accounts` — one `system_bank` and one `system_revenue`, both `INR`, `is_active = true`.

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
2. Try to find an existing transaction with the same `idempotency_key` that is still inside the 24-hour replay window.
3. If not found → insert the transaction, write the ledger entries, and return the result.
4. If found and `request_hash` matches → return the original response (replay).
5. If found and `request_hash` differs → return `409 Conflict`.
6. The replay window is 24 hours. The intended behaviour is that a key past the window can be reused for a new request — see the caveat below for what the current build actually does.

The `UNIQUE` constraint on `transactions.idempotency_key` is the enforcement mechanism. No separate Redis cache for this. The DB constraint handles concurrent duplicates atomically.

**How a race is actually resolved.** The lookup-then-insert sequence is not atomic on its own, so the insert is the arbiter:

1. `check_idempotency()` finds no live row → the request is treated as fresh.
2. The handler inserts a `transactions` row and flushes.
3. If a concurrent request already inserted the same key, the flush raises `IntegrityError` (unique violation).
4. The handler rolls back, re-reads the row by key, compares `request_hash`, and either replays the winner's response (same hash) or returns `409` (different hash).

Only one request can win the insert, so exactly one `transactions` row — and therefore exactly one pair of ledger entries — is ever created.

**Transaction status lifecycle.** `transactions.status` is one of `pending`, `completed`, `failed`. The current write path validates everything *before* inserting, so a request either succeeds and is written as `completed`, or it is rejected with an error and no row is created at all. The `pending` and `failed` states exist in the schema for the asynchronous case (a future flow where a payment is confirmed by a provider callback) and are not produced today — see section 21.

**Key expiry caveat (be precise about this in an interview).** The 24-hour TTL is enforced by the *lookup* filter (`created_at > now - 24h`), not by the unique constraint. A key whose row is older than 24 hours is not found by the lookup, so the handler proceeds to insert — and the insert then fails with the same `IntegrityError` as a concurrent race. The handler therefore **replays the original transaction instead of creating a new one**. In other words: expired keys are *not* reusable for a new request while the old row still exists. Making reuse truly work would require either a scheduled cleanup that deletes rows past the TTL, or a composite unique key on `(idempotency_key, created_at bucket)`. The current behaviour is safe (no double-spend) but it is "replay forever", not "reuse after 24 hours". Section 21 records this as a known gap.

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

**Locking rules that follow from this:**

- **Lock before you check.** The balance read must happen *after* the lock is granted, inside the same transaction, otherwise two requests can both observe the same pre-lock balance.
- **Transfers lock both accounts in one statement.** `lock_accounts_for_transfer()` issues a single `SELECT ... WHERE id IN (from, to) FOR UPDATE`, so PostgreSQL takes both locks in a deterministic order and two opposing transfers (A→B and B→A) cannot deadlock each other.
- **Withdrawals also lock the counterparty.** The `system_bank` row is locked as well, so a withdrawal and a deposit cannot interleave against the same system account.
- **Unrelated wallets never block each other.** The lock is row-level, not table-level.
- **`FOR UPDATE` is a no-op on SQLite.** The local fallback database ignores it. Row-level locking — and therefore the overdraw protection — only actually holds on PostgreSQL. The concurrency test must be run against Postgres to be meaningful; the SQLite test suite proves the API contract, not the locking.

---

## 6. API Contract

Base URL: `/api/v1` — **except** `/health`, which is served from the root (`GET /health`). Swagger UI is at `/docs`, the OpenAPI document at `/openapi.json`.

All requests/responses use `application/json`. All amounts in `NUMERIC` — never floats. Currency always uppercase 3-letter ISO code.

**Error envelope.** Every error response uses the same shape so clients can branch on a stable machine-readable code:

```json
{ "detail": { "error": "INSUFFICIENT_FUNDS", "message": "Account balance is insufficient" } }
```

The `error` value is the contract; the `message` is human-facing and may change.

**Response headers.** Every response carries `X-Request-ID` (a UUID generated per request) and `X-Process-Time` (server-side duration in milliseconds). Both are emitted by the request-id middleware and are also written into the JSON log line for that request.

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
Creates a `user_wallet` account for the authenticated user. One account per user per currency — a second `POST` for the same currency returns `400 ACCOUNT_EXISTS`. The currency is upper-cased before it is stored.

`GET /accounts`
```json
Response: [
  { "account_id": "uuid", "account_type": "user_wallet", "currency": "INR", "created_at": "..." }
]
```
Lists the caller's active `user_wallet` accounts, oldest first. System accounts are never exposed. The frontend uses this to discover which wallet to act on.

`GET /accounts/{account_id}/balance`
```json
Response: { "account_id": "uuid", "balance": "1250.00", "currency": "INR" }
```
Balance derived from ledger — never from a stored column. A wallet the caller does not own returns `403 UNAUTHORIZED`.

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
Paginated. Default page size 20, maximum 100 (`page` ≥ 1, `page_size` between 1 and 100). `direction` is from the perspective of `account_id` — debit means money left, credit means money arrived. Results are ordered newest first; `total` is the unpaginated count for that wallet.

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

Full error table:

| Status | Error | Meaning |
|---|---|---|
| `400` | `MISSING_IDEMPOTENCY_KEY` | `Idempotency-Key` header was omitted |
| `403` | `UNAUTHORIZED` | The wallet does not belong to the caller |
| `409` | `IDEMPOTENCY_KEY_CONFLICT` | Key was reused with a different payload |
| `422` | `INVALID_AMOUNT` | Amount is zero, negative, or not a positive decimal string |
| `422` | `CURRENCY_MISMATCH` | Request currency differs from the wallet currency |
| `500` | `SYSTEM_ACCOUNT_MISSING` | The `system_bank` account was not seeded |

Ledger entries created:
- DEBIT `system_bank` ₹500
- CREDIT `account_id` ₹500

The `system_bank` account is the counterparty for every deposit and withdrawal — money arrives from the outside world into the ledger, so the bank account is always the debit side of a deposit and the credit side of a withdrawal.

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

Full error table:

| Status | Error | Meaning |
|---|---|---|
| `400` | `MISSING_IDEMPOTENCY_KEY` | `Idempotency-Key` header was omitted |
| `403` | `UNAUTHORIZED` | The wallet does not belong to the caller |
| `409` | `IDEMPOTENCY_KEY_CONFLICT` | Key was reused with a different payload |
| `422` | `INVALID_AMOUNT` | Amount is zero, negative, or malformed |
| `422` | `CURRENCY_MISMATCH` | Request currency differs from the wallet currency |
| `422` | `INSUFFICIENT_FUNDS` | Ledger-derived balance is below the requested amount |
| `500` | `SYSTEM_ACCOUNT_MISSING` | The `system_bank` account was not seeded |

Must use `SELECT FOR UPDATE` before balance check. Ledger entries:
- DEBIT `account_id` ₹200
- CREDIT `system_bank` ₹200

When the balance check fails, the request is rejected with `422` and **no** `transactions` row and **no** `ledger_entries` rows are written — a rejected request leaves no trace in the money history. (Section 21 notes the deliberate deviation from the "mark the transaction as failed" wording in section 13.)

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

Full error table:

| Status | Error | Meaning |
|---|---|---|
| `400` | `MISSING_IDEMPOTENCY_KEY` | `Idempotency-Key` header was omitted |
| `400` | `SAME_ACCOUNT_TRANSFER` | Source and destination are the same wallet |
| `403` | `UNAUTHORIZED` | Caller does not own the source wallet |
| `404` | `ACCOUNT_NOT_FOUND` | Either wallet id does not exist |
| `409` | `IDEMPOTENCY_KEY_CONFLICT` | Key was reused with a different payload |
| `422` | `INVALID_AMOUNT` | Amount is zero, negative, or malformed |
| `422` | `CURRENCY_MISMATCH` | Wallet currencies or request currency differ |
| `422` | `INSUFFICIENT_FUNDS` | Ledger-derived balance is below the requested amount |

Ledger entries:
- DEBIT `from_account_id` ₹300
- CREDIT `to_account_id` ₹300

Only the **source** wallet must belong to the caller — the destination may be anyone else's wallet, that is the whole point of a P2P transfer. The optional `description` is stored in `transactions.metadata`.

After successful DB commit, enqueue a webhook delivery task to Celery. The `webhook_deliveries` row is written *inside* the same database transaction as the ledger entries, so the durable record of the delivery can never drift from the money movement. The Celery `delay()` call happens **after** the commit; if the broker is down the call is swallowed and the pending delivery row stays in the database for recovery.

---

### 6.6 Health

`GET /health` (root, not under `/api/v1`)
```json
Response: { "status": "ok", "db": "ok", "redis": "ok" }
```
Check both DB and Redis connectivity. Return 503 if either is down.

```json
Response 503: { "status": "error", "db": "ok", "redis": "down" }
```

Note: the Redis probe reports `ok` when Redis was not reachable *at process start* and the client fell back to the in-memory rate limiter. Restart the API after starting Redis to get an accurate reading.

### 6.7 Operational endpoints

| Method | Route | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/metrics` | No | In-process counters (section 12) |
| `GET` | `/api/v1/admin/reconcile` | No | Run reconciliation now and write a `reconciliation_logs` row (section 9) |

Both are operational conveniences. They are unauthenticated in the current implementation and must be locked down before any public deployment — see section 21.

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

**What the snippet does not show.** `check_idempotency()` only covers the *lookup*. The write path in each router additionally catches the `IntegrityError` raised by the unique constraint when two requests race, rolls back, re-reads the winning row, and either replays it (same `request_hash`) or raises `409` (different `request_hash`). Without that fallback, the second concurrent request would surface a `500` instead of a clean replay.

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

**Delivery state machine as implemented.** The `webhook_deliveries` row is the durable state; the Celery task is only the driver.

| Field | Meaning |
|---|---|
| `status` | `pending` → `delivered` on a 2xx response; `pending` → `failed` while retries remain; `failed` → `dead` when the retry budget is exhausted |
| `attempts` | Incremented once per HTTP attempt, including the first one |
| `last_attempted_at` | Timestamp of the most recent attempt |
| `next_retry_at` | When the next attempt is due; cleared on delivery and on death |

The task uses `httpx.AsyncClient` with a 5-second timeout, running the async delivery coroutine through `asyncio.run` inside the synchronous Celery task. The payload is serialised with sorted keys and compact separators so the signed bytes and the transmitted bytes are identical — the receiver can recompute the HMAC over the raw body.

**Retries.** `RETRY_DELAYS = [10, 30, 120, 600, 3600]` gives 5 attempts with exponential-ish backoff (10s → 30s → 2min → 10min → 1hr). After the last delay is consumed the row is marked `dead` and no further attempt is made — that is the dead-letter state.

**Receiving a webhook.** The receiver must recompute `HMAC-SHA256(body, WEBHOOK_SECRET)` and compare it with the `X-Signature: sha256=<hex>` header, and should treat `X-Delivery-Id` as an idempotency key of its own — the sender guarantees at-least-once delivery, not exactly-once.

> The default `WEBHOOK_URL` points at `http://localhost:8000/webhooks`, and no such endpoint exists in the API. Point it at a real receiver (a mock service, a webhook.site inbox, or a small FastAPI stub) before running the worker, otherwise every delivery will exhaust its retries and end up `dead`.

---

## 9. Reconciliation Job

Runs on a schedule — every 60 minutes via Celery Beat (`run_reconciliation_task`, schedule `60 * 60` in `app/workers/celery_app.py`) or a cron job. Can also be triggered manually via `GET /api/v1/admin/reconcile` (currently unauthenticated — it must become an admin-only, `POST` endpoint before production; see section 21).

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

**What the implemented job actually checks.** The run groups *all* `ledger_entries` by `transaction_id` and flags a transaction when either of two conditions holds:

1. the transaction does not have exactly two entries, or
2. `SUM(credit) - SUM(debit) != 0` for that transaction.

The mismatch record is `{transaction_id, expected_delta, actual_delta, entry_count}` and the whole list is stored as JSON in `reconciliation_logs.mismatch_details` under a `mismatches` key. `status` is `ok` when the list is empty and `mismatch_found` otherwise, and `accounts_checked` records how many active `user_wallet` accounts existed at run time.

This is the safety net: the write path is supposed to make the invariant true, and the job proves it independently. It re-derives from the ledger rather than trusting the transaction that wrote it, which is exactly what catches a partially applied write, a manual database edit, or a bug that slipped past a test.

---

## 10. Rate Limiting

Per-user request budget enforced in Redis, keyed by `user_id` and request class.

```
Limit: 10 requests per second per user_id for mutating endpoints
Limit: 60 requests per minute per user_id for read endpoints
```

On limit exceeded: return `429 Too Many Requests` with header `Retry-After: <seconds>` and body `{"error": "RATE_LIMITED", "message": "Too many requests"}`.

**Implementation notes (be accurate about these in an interview):**

- It is a **fixed-window counter**, not a true token bucket: `INCR` the key and `EXPIRE` the key at the window length, in one Redis pipeline. A strict token bucket would refill continuously and allow bursts up to the bucket size.
- **Redis with an in-memory fallback.** If Redis is unreachable at import time, the limiter keeps the same counters in a process-local dictionary, so a single API instance still protects itself. The fallback is *not* shared between processes — this is the main reason to run Redis in production.
- **Only authenticated requests are limited.** The middleware decodes the `Authorization` header itself to find the `user_id`; requests without a valid token (including `/auth/login`) pass through unrestricted and are instead rejected by the auth dependency where one is required.
- **Only `/api/v1/*` paths are limited.** `/health` and `/docs` are excluded.
- **Read and mutation budgets are independent**, so a burst of reads never blocks a transfer and vice versa.

---

## 11. Security

### JWT auth
- HS256 algorithm, secret from environment variable `JWT_SECRET`
- 24-hour expiry
- Payload: `{ "user_id": "uuid", "exp": timestamp }`
- All non-auth endpoints require valid token
- The token is verified against a live `users` row, so a deleted user cannot keep using an unexpired token
- `python-jose` performs the signing and verification

### Password storage
- `bcrypt` with work factor 12 (the `passlib` default)
- Never store plaintext
- Login failures return a single `401 INVALID_CREDENTIALS` for both "unknown email" and "wrong password", so the endpoint does not confirm which emails are registered
- Emails are normalised to lower case before hashing and lookup

### Webhook signature
- Every outbound webhook has `X-Signature: sha256=<hmac>` header
- Secret from environment variable `WEBHOOK_SECRET`
- Receiving systems can verify using the same HMAC computation over the raw request body
- `X-Delivery-Id` lets the receiver de-duplicate redeliveries

### Sensitive fields
- Store passwords hashed only
- No PII in logs — never log email, password, or full account IDs in plain text
- The request log line contains the `request_id`, the `status_code` and the process time only

### Input validation
- Amounts are accepted as **strings** with a strict pattern (`^\d+(\.\d{1,2})?$`) so a client can never inject a float and rounding errors are impossible
- Currencies are upper-cased before comparison, so `inr` and `INR` are the same wallet
- A user may only act on wallets they own — enforced on every account-scoped route

### Deployment checklist before going public
- Replace `JWT_SECRET` and `WEBHOOK_SECRET` with high-entropy values
- Serve everything over HTTPS
- Restrict `FRONTEND_URL` to the real frontend origin (CORS)
- Lock down `GET /api/v1/metrics` and `GET /api/v1/admin/reconcile`
- Add database backups and a `reconciliation_logs` alert on `status = 'mismatch_found'`

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

**Request correlation.** The request-id middleware generates a UUID per request, stores it on `request.state.request_id`, adds it to the response as `X-Request-ID`, and logs it with the response status and the server-side duration. A `transaction_id` should be added to the same log line by the money-movement routers so a single log query can follow a request from the API call to the ledger write and the webhook enqueue.

> Gap to be aware of: the current `structlog` configuration only installs the `JSONRenderer` processor, so the emitted JSON contains `event` plus the bound keyword arguments but **not** automatic `timestamp` and `level` keys. Add `structlog.processors.TimeStamper(fmt="iso", utc=True)` and `structlog.processors.add_log_level` to the processor chain to satisfy the format above.

### Metrics endpoint
`GET /api/v1/metrics` — returns:
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

**How the counters behave today.** `transfers_total`, `deposits_total` and `withdrawals_total` are incremented by the routers on a successful write. `failed_transactions_total`, `webhook_dead_letter_total` and `p99_transfer_latency_ms` are declared but never incremented, so they always read `0` — they are placeholders, and section 21 lists them as known gaps. The counters live in the API process, so they reset on restart and are not shared between replicas. `X-Process-Time` on each response is the cheap per-request latency signal that is already available.

---

## 13. Tests — Must Have

The suite runs with `pytest -q`; `pytest.ini` sets `asyncio_mode = auto` so `async def` tests need no decorator, and `pythonpath = .` so `app` is importable without installation. The `client` fixture drives the real FastAPI app through `TestClient`, and `auth_user` / `user_wallet` fixtures register a user, log in, and create a wallet.

Current status: **12 tests, all passing** across the three files below.

### test_idempotency.py
```
- Same key + same payload → second call returns original response, no new DB row
- Same key + different payload → 409
- Expired key (>24h) + same payload → treated as fresh, new transaction created
- Concurrent duplicate requests → only one transaction created, other gets replay
```

> Reality check on the third case: because the unique constraint on `idempotency_key` still holds (section 5.2), an expired key is *not* reusable — the second request replays the original transaction instead of creating a new one. The test asserts the replay behaviour. A scheduled cleanup job that deletes transactions older than the TTL is what would make "reuse after 24 hours" true.

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

> Two caveats worth stating out loud: (1) a rejected request writes **no** `transactions` row at all, so there is no `failed` transaction to mark — the write path validates before inserting; (2) the concurrency test fires the two withdrawals sequentially against SQLite, where `SELECT ... FOR UPDATE` is ignored, so it proves the API contract but not the row lock. A genuine race test needs two real connections against PostgreSQL.

### test_reconciliation.py
```
- Clean ledger → reconciliation logs status 'ok', mismatches=0
- Manually insert a bad ledger entry → reconciliation detects and logs the mismatch
```

### What is still worth adding
- A PostgreSQL-backed concurrency test that proves two simultaneous withdrawals cannot overdraw
- A test that a duplicated transfer with the same key creates exactly one `transactions` row and exactly two `ledger_entries` rows
- A webhook signing test — recompute the HMAC over the body and compare it with `X-Signature`
- A rate-limit test — assert the `429` and the `Retry-After` header

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

**What the implemented script does.** `on_start` registers a fresh user, logs in, creates a wallet, funds it with a `1000.00` deposit, then creates a *second* user with a second wallet to act as the transfer counterparty. The tasks are then a 3:1 mix of transfers (`10.00` each) and balance reads, with a `wait_time` of 0.5–2 seconds between calls. Every mutating request carries a fresh UUID `Idempotency-Key`.

Two things to know when reading the results:
- Each Locust user has its **own** rate-limit bucket, so the 10 req/s mutation limit is per virtual user and is not the bottleneck at this scale.
- The script creates a new user per virtual user, so a 100-user run writes 200 users and 200 wallets. Point it at a throwaway database, not a real one.

---

## 15. Docker Compose

The Compose file in the repository is the source of truth; this is the same content with the comments that explain the differences from an earlier draft.

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
      - FRONTEND_URL=http://localhost:5173
      - WEBHOOK_URL=http://localhost:8000/webhooks
      - ENVIRONMENT=production
    depends_on:
      - db
      - redis
    # Migrations run before the server starts; --reload is NOT used in the container.
    command: sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"

  worker:
    build: .
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/payment_ledger
      - REDIS_URL=redis://redis:6379/0
      - WEBHOOK_SECRET=change-this-in-production
    depends_on:
      - db
      - redis
    command: celery -A app.workers.celery_app worker --loglevel=info

  beat:
    build: .
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/payment_ledger
      - REDIS_URL=redis://redis:6379/0
      - WEBHOOK_SECRET=change-this-in-production
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

**Why `ENVIRONMENT=production` matters.** In that mode the API does **not** call `create_all()`/`drop_all()` at startup — the schema comes from Alembic only. The compose file therefore runs `alembic upgrade head` in the API container's start command, and the application only seeds the `system_bank` and `system_revenue` accounts. The frontend is *not* part of Compose: it is a separate static deployment (section 17).

**The Dockerfile** is a single-stage `python:3.11-slim` image: install `requirements.txt`, copy the source, expose port 8000, and default to `uvicorn app.main:app --host 0.0.0.0 --port 8000`. The same image is used by the `api`, `worker` and `beat` services — only the command differs.

---

## 16. Environment Variables

Settings are read by `app/config.py` through `pydantic-settings`, loading `.env` when it exists. Every variable below is optional; the defaults make the API runnable with no configuration at all.

```bash
# .env.example
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/payment_ledger
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=change-this-in-production
WEBHOOK_SECRET=change-this-in-production
WEBHOOK_URL=http://localhost:8000/webhooks
FRONTEND_URL=http://localhost:5173
ENVIRONMENT=development         # development | production
LOG_LEVEL=INFO
RECONCILIATION_INTERVAL_MINUTES=60
IDEMPOTENCY_KEY_TTL_HOURS=24
```

| Variable | Default | Used for |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./ledger.db` | Async SQLAlchemy URL. `postgresql+asyncpg://…` in production |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker and result backend (backend uses `/1`), plus the rate-limit store |
| `JWT_SECRET` | `change-this-in-production` | HS256 signing key — replace outside local development |
| `WEBHOOK_SECRET` | `change-this-in-production` | HMAC key for outbound webhook signatures |
| `WEBHOOK_URL` | `http://localhost:8000/webhooks` | Target URL for transfer webhooks |
| `FRONTEND_URL` | `http://localhost:5173` | The single origin allowed by CORS |
| `ENVIRONMENT` | `development` | `development` = create schema on startup; `production` = Alembic only |
| `LOG_LEVEL` | `INFO` | Root log level |
| `RECONCILIATION_INTERVAL_MINUTES` | `60` | Intended reconciliation interval (the Celery beat schedule currently hard-codes 3600s) |
| `IDEMPOTENCY_KEY_TTL_HOURS` | `24` | Replay window used by the idempotency lookup |

> `RECONCILIATION_INTERVAL_MINUTES` and `IDEMPOTENCY_KEY_TTL_HOURS` are declared in the settings object but are **not** read by the code yet — the beat schedule uses a literal `60 * 60` and the idempotency service uses a module-level `IDEMPOTENCY_KEY_TTL_HOURS = 24`. Wire them up, or remove them, so the configuration is not misleading.

---

## 17. Frontend

**Purpose: demo the backend live, nothing more.** Do not over-invest here — this is not the part of the project you'll be judged on. Keep it thin, functional, and focused on making the backend's hardest features visible.

### Stack
- React 18 + Vite
- TailwindCSS (utility classes only, no component library needed)
- `axios` for API calls
- `react-router-dom` for the 4 routes below
- `uuid` for client-side idempotency-key generation
- No state management library needed — `useState`/`useContext` is enough for this scope

### Structure

```
frontend/
├── src/
│   ├── main.jsx
│   ├── App.jsx
│   ├── api/
│   │   └── client.js          # axios instance, attaches JWT from context
│   ├── context/
│   │   └── AuthContext.jsx    # stores token + user, login/logout
│   ├── pages/
│   │   ├── Login.jsx
│   │   ├── Register.jsx
│   │   ├── Dashboard.jsx      # balance + account info
│   │   ├── SendMoney.jsx      # transfer form + idempotency demo
│   │   └── History.jsx        # paginated transaction list
│   ├── components/
│   │   ├── BalanceCard.jsx
│   │   ├── TransactionRow.jsx
│   │   └── IdempotencyDemo.jsx
│   └── index.css              # Tailwind directives
├── index.html
├── vite.config.js
├── tailwind.config.js
├── postcss.config.js
└── package.json
```

### Pages — exact scope, nothing more

**Login.jsx / Register.jsx**
Plain form, calls `POST /auth/login` or `POST /auth/register`, stores JWT in `AuthContext`. The token is kept in `sessionStorage` under `ledger_token` (so it survives a page refresh but dies with the browser tab) and the Axios interceptor attaches it as `Authorization: Bearer …` to every request. This is a demo app — do not spend time on refresh-token rotation.

**Dashboard.jsx**
On mount, calls `GET /accounts` to discover the caller's wallets, then `GET /accounts/{id}/balance` for the first one. Shows balance in a card. If the user has no account yet, show a "Create wallet" button that calls `POST /accounts`.

**SendMoney.jsx**
Form: recipient account ID, amount, optional description. On submit, generates a UUID as the `Idempotency-Key`, calls `POST /transfers`. Show success/error state clearly (insufficient funds, same-account transfer, etc. map directly to the error codes in section 6.5).

**IdempotencyDemo.jsx** — the one feature worth real effort
A button labeled "Prove idempotency" that:
1. Generates one `Idempotency-Key`
2. Fires the *same* transfer request twice in parallel (`Promise.all`) with that same key
3. Displays both responses side by side — same `transaction_id`, same result, only one entry in the transaction history afterward
This is your live demo of the hardest backend concept in the project. Build this properly.

**History.jsx**
Paginated list from `GET /accounts/{id}/transactions`. Color-code rows: green for credit (money in), red for debit (money out). Simple pagination controls (prev/next), no infinite scroll needed.

### What NOT to build
- No admin panel, no recruiter/merchant views — out of scope
- No dark mode, no animations, no design system — a clean Tailwind layout is enough
- No client-side balance calculations — always trust the API response, never derive balance in the frontend
- No offline support, no PWA features

### CORS
Add `CORSMiddleware` to the FastAPI app allowing the Vite dev server origin (`http://localhost:5173`) in development, and your deployed frontend URL in production. The backend reads the single allowed origin from `FRONTEND_URL`, so there is one place to change it.

### Deployment
Deploy separately from the backend — Vercel or Netlify (free tier) for the frontend, pointing at your deployed Render/Railway API URL via an environment variable (`VITE_API_BASE_URL`).

```
VITE_API_BASE_URL=https://your-api.example.com/api/v1
```

The value **must include `/api/v1`** — the Axios base URL is used verbatim as the prefix for every call, and the default is `http://localhost:8000/api/v1`. `frontend/dist/` is build output and is git-ignored.

### Local development

```powershell
cd frontend
npm install
npm run dev      # http://localhost:5173
npm run build    # production bundle into dist/
npm run preview  # serve the built bundle
```

---

## 18. Implementation Order

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
16. Dockerfile + deploy backend to Render/Railway
17. Frontend — scaffold Vite + Tailwind, build the 4 pages in order (Login/Register → Dashboard → SendMoney → History), then IdempotencyDemo last since it depends on SendMoney working
18. Deploy frontend to Vercel/Netlify, point at deployed backend URL

**Two endpoints that are easy to forget and are already in the code** — keep them in the plan so a rebuild does not lose them:

- `GET /accounts` — wallet discovery, needed by the frontend to know which wallet to act on.
- `GET /api/v1/admin/reconcile` — manual reconciliation trigger, used by the demo and by the reconciliation test.

---

## 19. What Makes This Stand Out in an Interview

When asked "walk me through your best project" — lead with these five points in order:

1. **Double-entry design** — "Balance is never stored, it's always derived from an append-only ledger. Every money movement creates exactly two entries summing to zero. This means the ledger is auditable and replayable — it cannot silently drift."

2. **Idempotency** — "Every mutating API requires a client-generated idempotency key. I use a unique DB constraint as the enforcement mechanism, not a cache. Even concurrent duplicate requests resolve correctly because the DB constraint is atomic."

3. **Concurrency correctness** — "I reproduced a race condition by firing concurrent withdrawal requests against the same account, watched it overdraw, then fixed it with row-level locking inside a DB transaction. I have a test that proves it's fixed."

4. **Reconciliation** — "A scheduled job independently re-derives every account balance from the ledger and flags mismatches. This is what real fintech systems run in production — a safety net that exists because bugs happen and silent balance drift is the worst possible failure mode."

5. **Live proof, not just claims** — "I built a small demo UI with a button that fires the same transfer request twice in parallel with the same idempotency key — you can watch both responses come back identical and see only one entry land in the transaction history." Most candidates *describe* idempotency. You can *show* it running, on the spot, in an interview. That's a rare and disproportionately strong signal.

**Follow-up questions you should be ready for**

| Question | The honest answer |
|---|---|
| "How do you know the balance can never go negative?" | The balance is re-derived from `ledger_entries` while the account row is held with `SELECT ... FOR UPDATE`, and the debit is only written if the derived balance covers the amount. Two concurrent withdrawals therefore queue on the same row lock. |
| "What if the client retries after a timeout?" | The retry carries the same `Idempotency-Key`; the server replays the original transaction instead of moving money twice. |
| "What if the webhook receiver is down?" | The delivery row is committed with the ledger entries, so the attempt is durable. The task retries with 10s/30s/2m/10m/1h backoff and finally marks the row `dead` for manual replay. |
| "How do you know the ledger is correct?" | A scheduled job re-derives every transaction's entries independently and writes a `reconciliation_logs` row; any transaction that does not have exactly two entries summing to zero is flagged. |
| "Where would you go next?" | Add the cleanup job that lets idempotency keys actually expire, move the metrics to Prometheus, add a PostgreSQL-backed race test, and put the admin endpoints behind real authorisation. |

---

## 20. Running the Project

### 20.1 Requirements

- Python 3.11+
- Node.js 18+ and npm (only for the frontend)
- Docker Desktop (optional, but the only way to get Postgres + Redis + workers quickly)

### 20.2 Option A — the full stack with Docker Compose

```powershell
docker compose up --build
```

| Service | URL |
|---|---|
| API | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

The API container runs `alembic upgrade head` before starting Uvicorn. Stop with `docker compose down`, or `docker compose down -v` to also drop the Postgres volume.

### 20.3 Option B — API only, with the SQLite fallback

The **application default** is `sqlite+aiosqlite:///./ledger.db`, so the API starts with no external services at all (no `.env` needed):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open http://localhost:8000/health and http://localhost:8000/docs.

> If you copy `.env.example` to `.env` first, the `DATABASE_URL` in that file points at **PostgreSQL on localhost**, so Postgres must be running. To keep the SQLite fallback, set `DATABASE_URL=sqlite+aiosqlite:///./ledger.db` in `.env` after copying.

To use the real backing services locally, start just Postgres and Redis and point the app at them:

```powershell
docker compose up -d db redis
$env:DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/payment_ledger"
$env:REDIS_URL = "redis://localhost:6379/0"
$env:JWT_SECRET = "replace-this-local-secret"
$env:WEBHOOK_SECRET = "replace-this-webhook-secret"
$env:FRONTEND_URL = "http://localhost:5173"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Environment variables set in the shell override the values in `.env`, so this is the quickest way to switch back and forth.

> **Important:** with `ENVIRONMENT=development` the application **drops and recreates** all tables on startup. That is convenient for a disposable local database and destructive for anything you care about. Switch `ENVIRONMENT=production` (and use Alembic) as soon as the database holds data you want to keep.

### 20.4 Workers

```powershell
celery -A app.workers.celery_app worker --loglevel=info
celery -A app.workers.celery_app beat --loglevel=info
```

Both need Redis. The worker needs a reachable `WEBHOOK_URL` to deliver anything.

### 20.5 Frontend

```powershell
cd frontend
npm install
npm run dev     # http://localhost:5173
```

### 20.6 Tests and load tests

```powershell
python -m pytest -q                # 12 tests, all passing
python -m compileall -q app locustfile.py
locust -f locustfile.py --host=http://localhost:8000
```

### 20.7 A full API walkthrough in PowerShell

```powershell
$base = "http://localhost:8000/api/v1"
$email = "user-$([guid]::NewGuid())@example.com"
$password = "secret123"

# 1. Register and log in
$body = @{ email = $email; name = "Demo User"; password = $password } | ConvertTo-Json
Invoke-RestMethod "$base/auth/register" -Method Post -ContentType "application/json" -Body $body
$login = Invoke-RestMethod "$base/auth/login" -Method Post -ContentType "application/json" -Body (@{ email = $email; password = $password } | ConvertTo-Json)
$headers = @{ Authorization = "Bearer $($login.access_token)" }

# 2. Create a wallet
$wallet = Invoke-RestMethod "$base/accounts" -Method Post -Headers $headers -ContentType "application/json" -Body (@{ currency = "INR" } | ConvertTo-Json)

# 3. Deposit (note the Idempotency-Key header)
$depositHeaders = $headers.Clone()
$depositHeaders["Idempotency-Key"] = [guid]::NewGuid().ToString()
Invoke-RestMethod "$base/deposits" -Method Post -Headers $depositHeaders -ContentType "application/json" -Body (@{ account_id = $wallet.account_id; amount = "1000.00"; currency = "INR" } | ConvertTo-Json)

# 4. Read the ledger-derived balance
Invoke-RestMethod "$base/accounts/$($wallet.account_id)/balance" -Headers $headers
```

---

## 21. Known Gaps and Deliberate Simplifications

Nothing here is a hidden bug — each item is a known limitation of this build, and knowing the list is part of owning the project.

| # | Gap | Why it is acceptable here | How to close it |
|---|---|---|---|
| 1 | Idempotency keys never actually expire — the 24-hour TTL only affects the lookup, and the unique constraint makes an expired key replay instead of being reusable | Safe (no double-spend), and simplest correct behaviour | Add a periodic cleanup that deletes transactions older than the TTL, or scope uniqueness to a time bucket |
| 2 | Failed requests leave no `transactions` row (no `status = 'failed'` record) | The money history only needs to describe money that actually moved | Insert a `pending` row first and flip it to `failed` on rejection, if an audit trail of attempts is required |
| 3 | `SELECT ... FOR UPDATE` is ignored by SQLite, so the local database does not really enforce the lock | Local SQLite is a developer convenience, not the target | Run the concurrency test against PostgreSQL; that is where the guarantee holds |
| 4 | The concurrency test fires two withdrawals sequentially, not simultaneously | It still exercises the insufficient-funds path | Use two real connections and `asyncio.gather` against PostgreSQL to prove the row lock |
| 5 | `GET /api/v1/metrics` and `GET /api/v1/admin/reconcile` are unauthenticated | They are local development conveniences | Add an admin role check; change the reconcile route to `POST` |
| 6 | `failed_transactions_total`, `webhook_dead_letter_total` and `p99_transfer_latency_ms` always read `0` | Only three of the six counters are wired up | Increment them at the failure/dead-letter sites and compute latency percentiles from a rolling window |
| 7 | `structlog` emits no `timestamp` or `level` keys | The JSON renderer is configured with a single processor | Add `TimeStamper` and `add_log_level` to the processor chain |
| 8 | `RECONCILIATION_INTERVAL_MINUTES` and `IDEMPOTENCY_KEY_TTL_HOURS` are defined in the settings but never read | The values happen to match the hard-coded ones | Read them from `settings` in the beat schedule and the idempotency service |
| 9 | The default `WEBHOOK_URL` points at a `/webhooks` endpoint that does not exist | There is no receiver in this project | Point it at a real receiver; deliveries otherwise exhaust their retries and end up `dead` |
| 10 | The rate limiter falls back to a process-local dictionary when Redis is unavailable | Keeps a single instance protected | Always run Redis in production; the fallback is not shared between replicas |
| 11 | `system_revenue` is seeded but never used — there are no fees | Fees were out of scope | Add a fee split: debit the payer, credit `system_revenue` |
| 12 | No pagination cap on large history beyond `page_size ≤ 100` | Acceptable for a demo | Keyset pagination on `created_at` for very large ledgers |
| 13 | The in-memory metrics and the in-memory rate-limit fallback both reset on restart | Acceptable for a single-process demo | Prometheus + Redis for anything real |

---

## 22. Glossary

| Term | Meaning in this project |
|---|---|
| **Double-entry** | Every movement is recorded as exactly two entries — a debit and a credit of equal amount — so the ledger always sums to zero |
| **Debit** | A ledger entry that *reduces* the account balance (money leaving) |
| **Credit** | A ledger entry that *increases* the account balance (money arriving) |
| **Ledger** | The append-only `ledger_entries` table — the single source of truth for every balance |
| **Derived balance** | `SUM(credit) - SUM(debit)` computed on demand; never stored |
| **Idempotency key** | A client-generated token that makes a mutating request safe to retry |
| **Request hash** | SHA-256 of the canonical request body; detects a reused key with a different payload |
| **Replay** | Returning the original response for a repeated key instead of performing the action again |
| **Row lock (`FOR UPDATE`)** | A database lock that blocks other writers on the same row until the transaction ends |
| **Deadlock** | Two transactions waiting on each other's locks; avoided here by locking both accounts in a single statement |
| **System account** | An account with no owner (`system_bank`, `system_revenue`) used as the counterparty for external money and fees |
| **Webhook** | An outbound HTTP callback announcing that a transaction completed |
| **HMAC signature** | A keyed hash (`X-Signature: sha256=…`) that lets the receiver prove the payload came from the server and was not tampered with |
| **Retry / backoff** | Re-attempting a failed delivery with increasing delays (10s, 30s, 2m, 10m, 1h) |
| **Dead letter** | The final state of a delivery that exhausted its retry budget |
| **Reconciliation** | An independent job that re-derives balances and flags any drift — the safety net against silent corruption |
| **Idempotent** | An operation that can be applied multiple times with the same result as applying it once |
| **Fixed window** | The rate-limiting scheme used here: a counter that resets every N seconds |

---

## 23. Document Map

| Document | Purpose | Audience |
|---|---|---|
| `new_spec.md` (this file) | The full design and API contract, including the known gaps | Anyone implementing or reviewing the project |
| `README.md` | Public-facing overview, quick start, API reference, troubleshooting | Visitors, reviewers, recruiters |
| `WORKING.md` | Day-to-day developer setup and command reference | You, on a normal working day |
| `/docs` (Swagger) | Live, generated API reference | Anyone calling the API |

When behaviour changes, update this file **and** the README in the same commit — a spec that disagrees with the code is worse than no spec.

---

*End of spec. Implement exactly this. Do not add features not listed here.*

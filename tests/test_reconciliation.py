import asyncio
import uuid

from app.database import async_session_maker
from app.jobs.reconciliation import run_reconciliation
from app.models import Account, LedgerEntry, Transaction, User


def _create_user_and_wallet(client, prefix):
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    password = "secret123"

    register = client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Reconcile User", "password": password},
    )
    assert register.status_code == 200, register.text

    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text

    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    account = client.post("/api/v1/accounts", headers=headers, json={"currency": "INR"})
    assert account.status_code == 200, account.text
    return headers, account.json()["account_id"]


def test_clean_ledger_reconciliation_status_ok(client):
    headers, account_id = _create_user_and_wallet(client, "reconcile-ok")
    deposit = client.post(
        "/api/v1/deposits",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"account_id": account_id, "amount": "100.00", "currency": "INR"},
    )
    assert deposit.status_code == 200, deposit.text

    async def check():
        async with async_session_maker() as session:
            log = await run_reconciliation(session)
            assert log.status == "ok"
            assert log.mismatches_found == 0

    asyncio.run(check())


def test_bad_ledger_entry_is_detected(client):
    async def add_bad_entry():
        async with async_session_maker() as session:
            user = User(email=f"mismatch-{uuid.uuid4().hex[:8]}@example.com", name="Mismatch User", password_hash="dummy")
            session.add(user)
            await session.flush()

            account = Account(owner_id=user.id, account_type="user_wallet", currency="INR", is_active=True)
            session.add(account)
            await session.flush()

            tx = Transaction(
                idempotency_key=str(uuid.uuid4()),
                request_hash="x" * 64,
                type="transfer",
                status="completed",
                amount="25.00",
                currency="INR",
                from_account_id=account.id,
                to_account_id=account.id,
            )
            session.add(tx)
            await session.flush()

            session.add(LedgerEntry(transaction_id=tx.id, account_id=account.id, entry_type="debit", amount="25.00"))
            await session.commit()

    asyncio.run(add_bad_entry())

    async def check():
        async with async_session_maker() as session:
            log = await run_reconciliation(session)
            assert log.status == "mismatch_found"
            assert log.mismatches_found >= 1

    asyncio.run(check())

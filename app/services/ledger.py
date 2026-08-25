from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, LedgerEntry, Transaction


async def get_account_for_update(db: AsyncSession, account_id: str) -> Account | None:
    result = await db.execute(
        select(Account)
        .where(Account.id == account_id)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def lock_accounts_for_transfer(db: AsyncSession, from_account_id: str, to_account_id: str) -> tuple[Account | None, Account | None]:
    accounts = await db.execute(
        select(Account)
        .where((Account.id == from_account_id) | (Account.id == to_account_id))
        .with_for_update()
    )
    rows = accounts.scalars().all()
    by_id = {str(account.id): account for account in rows}
    return by_id.get(from_account_id), by_id.get(to_account_id)


async def write_double_entry_ledger(
    db: AsyncSession,
    tx: Transaction,
    from_account_id: str | None,
    to_account_id: str | None,
    amount: Decimal,
) -> None:
    entries = []
    if from_account_id is not None:
        entries.append(
            LedgerEntry(
                transaction_id=tx.id,
                account_id=from_account_id,
                entry_type='debit',
                amount=amount,
            )
        )
    if to_account_id is not None:
        entries.append(
            LedgerEntry(
                transaction_id=tx.id,
                account_id=to_account_id,
                entry_type='credit',
                amount=amount,
            )
        )
    db.add_all(entries)


async def ensure_transaction_ledger_balance(
    db: AsyncSession,
    tx: Transaction,
    from_account_id: str | None,
    to_account_id: str | None,
    amount: Decimal,
) -> None:
    if from_account_id is None and to_account_id is None:
        return
    await write_double_entry_ledger(db, tx, from_account_id, to_account_id, amount)

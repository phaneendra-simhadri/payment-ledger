from __future__ import annotations

from decimal import Decimal

from collections import defaultdict

from sqlalchemy import select

from app.database import async_session_maker
from app.models import Account, LedgerEntry, ReconciliationLog
from app.workers.celery_app import celery_app


async def run_reconciliation(db):
    accounts = await db.execute(select(Account).where(Account.account_type == 'user_wallet', Account.is_active.is_(True)))
    accounts = accounts.scalars().all()
    mismatches = []

    entries_result = await db.execute(select(LedgerEntry))
    entries_by_transaction = defaultdict(list)
    for entry in entries_result.scalars():
        entries_by_transaction[entry.transaction_id].append(entry)

    for transaction_id, transaction_entries in entries_by_transaction.items():
        delta = sum(
            (entry.amount if entry.entry_type == 'credit' else -entry.amount)
            for entry in transaction_entries
        )
        if len(transaction_entries) != 2 or delta != Decimal('0.00'):
            mismatches.append({
                'transaction_id': str(transaction_id),
                'expected_delta': '0.00',
                'actual_delta': str(delta),
                'entry_count': len(transaction_entries),
            })

    log = ReconciliationLog(
        accounts_checked=len(accounts),
        mismatches_found=len(mismatches),
        mismatch_details={'mismatches': mismatches},
        status='ok' if not mismatches else 'mismatch_found',
    )
    db.add(log)
    await db.commit()
    return log


@celery_app.task(name='run_reconciliation_task')
def run_reconciliation_task():
    from app.database import async_session_maker

    async def execute():
        async with async_session_maker() as session:
            await run_reconciliation(session)

    import asyncio
    asyncio.run(execute())

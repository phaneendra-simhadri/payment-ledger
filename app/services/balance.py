from __future__ import annotations

from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import parse_uuid
from app.models.ledger_entry import LedgerEntry


async def derive_balance(account_id: str, db: AsyncSession) -> Decimal:
    account_uuid = parse_uuid(account_id)
    total = await db.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (LedgerEntry.entry_type == 'credit', LedgerEntry.amount),
                        else_=Decimal('0.00'),
                    )
                ),
                Decimal('0.00'),
            )
            - func.coalesce(
                func.sum(
                    case(
                        (LedgerEntry.entry_type == 'debit', LedgerEntry.amount),
                        else_=Decimal('0.00'),
                    )
                ),
                Decimal('0.00'),
            )
        ).where(LedgerEntry.account_id == account_uuid)
    )
    value = total.scalar() or Decimal('0.00')
    return Decimal(str(value))


async def get_balance(account_id: str, db: AsyncSession) -> Decimal:
    return await derive_balance(account_id, db)

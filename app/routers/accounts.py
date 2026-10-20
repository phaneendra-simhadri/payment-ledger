from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.database import async_session_maker, parse_uuid
from app.middleware.auth import auth_required
from app.models import Account, Transaction
from app.schemas.account import AccountCreate
from app.services.balance import get_balance

router = APIRouter(prefix='/accounts', tags=['accounts'])


class AccountQueryParams(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


@router.get('')
async def list_accounts(user=Depends(auth_required)):
    async with async_session_maker() as session:
        result = await session.execute(
            select(Account)
            .where(Account.owner_id == user.id, Account.account_type == 'user_wallet', Account.is_active.is_(True))
            .order_by(Account.created_at)
        )
        return [
            {
                'account_id': str(account.id),
                'account_type': account.account_type,
                'currency': account.currency,
                'created_at': account.created_at.isoformat(),
            }
            for account in result.scalars().all()
        ]


@router.post('')
async def create_account(payload: AccountCreate, user=Depends(auth_required)):
    async with async_session_maker() as session:
        safe_currency = payload.currency.upper()
        existing = await session.execute(
            select(Account).where(Account.owner_id == user.id).where(Account.currency == safe_currency)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=400, detail={'error': 'ACCOUNT_EXISTS', 'message': 'User account already exists for this currency'})

        account = Account(
            owner_id=user.id,
            account_type='user_wallet',
            currency=safe_currency,
            is_active=True,
        )
        session.add(account)
        await session.commit()
        await session.refresh(account)
        return {
            'account_id': str(account.id),
            'account_type': account.account_type,
            'currency': account.currency,
            'created_at': account.created_at.isoformat(),
        }


@router.get('/{account_id}/balance')
async def get_account_balance(account_id: str, user=Depends(auth_required)):
    async with async_session_maker() as session:
        account_uuid = parse_uuid(account_id)
        account = await session.get(Account, account_uuid)
        if account is None or account.owner_id != user.id:
            raise HTTPException(status_code=403, detail={'error': 'UNAUTHORIZED', 'message': 'You do not own this account'})

        balance = await get_balance(str(account_uuid), session)
        return {'account_id': account_id, 'balance': str(balance.quantize(Decimal('0.01'))), 'currency': account.currency}


@router.get('/{account_id}/transactions')
async def list_transactions(
    account_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user=Depends(auth_required),
):
    async with async_session_maker() as session:
        account_uuid = parse_uuid(account_id)
        account = await session.get(Account, account_uuid)
        if account is None or account.owner_id != user.id:
            raise HTTPException(status_code=403, detail={'error': 'UNAUTHORIZED', 'message': 'You do not own this account'})

        stmt = (
            select(Transaction)
            .where((Transaction.from_account_id == account_uuid) | (Transaction.to_account_id == account_uuid))
            .order_by(Transaction.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await session.execute(stmt)
        transactions = result.scalars().all()
        rows = []
        for txn in transactions:
            direction = 'debit' if str(txn.from_account_id) == account_id else 'credit'
            rows.append({
                'transaction_id': str(txn.id),
                'type': txn.type,
                'amount': str(txn.amount),
                'direction': direction,
                'status': txn.status,
                'created_at': txn.created_at.isoformat(),
            })

        total_stmt = select(func.count()).select_from(Transaction).where(
            (Transaction.from_account_id == account_uuid) | (Transaction.to_account_id == account_uuid)
        )
        total = await session.scalar(total_stmt)
        return {'account_id': account_id, 'transactions': rows, 'page': page, 'page_size': page_size, 'total': total or 0}

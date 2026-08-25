from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import async_session_maker
from app.middleware.auth import auth_required
from app.models import Account, LedgerEntry, Transaction
from app.schemas.transaction import DepositRequest
from app.services.idempotency import check_idempotency, compute_request_hash
from app.services.metrics import increment

router = APIRouter(prefix='/deposits', tags=['deposits'])


def transaction_response(transaction: Transaction) -> dict:
    return {'transaction_id': str(transaction.id), 'status': transaction.status, 'amount': str(transaction.amount), 'currency': transaction.currency, 'created_at': transaction.created_at.isoformat()}


@router.post('')
async def create_deposit(payload: DepositRequest, user=Depends(auth_required), idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    if idempotency_key is None:
        raise HTTPException(status_code=400, detail={'error': 'MISSING_IDEMPOTENCY_KEY', 'message': 'Idempotency-Key header is required'})
    payload_data = payload.model_dump()
    amount = Decimal(payload.amount)
    if amount <= 0:
        raise HTTPException(status_code=422, detail={'error': 'INVALID_AMOUNT', 'message': 'Amount must be greater than 0'})

    async with async_session_maker() as session:
        is_fresh, existing = await check_idempotency(idempotency_key, payload_data, session)
        if not is_fresh and existing is not None:
            return transaction_response(existing)
        account = await session.get(Account, payload.account_id)
        if account is None or account.owner_id != user.id:
            raise HTTPException(status_code=403, detail={'error': 'UNAUTHORIZED', 'message': 'You do not own this account'})
        if account.currency != payload.currency.upper():
            raise HTTPException(status_code=422, detail={'error': 'CURRENCY_MISMATCH', 'message': 'Account currency does not match request currency'})
        bank = await session.scalar(select(Account).where(Account.account_type == 'system_bank'))
        if bank is None:
            raise HTTPException(status_code=500, detail={'error': 'SYSTEM_ACCOUNT_MISSING', 'message': 'System bank account is not configured'})
        transaction = Transaction(idempotency_key=idempotency_key, request_hash=compute_request_hash(payload_data), type='deposit', status='completed', amount=amount, currency=payload.currency.upper(), from_account_id=bank.id, to_account_id=account.id, request_metadata={'description': 'deposit'})
        session.add(transaction)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            existing = await session.scalar(select(Transaction).where(Transaction.idempotency_key == idempotency_key))
            if existing is None or existing.request_hash != compute_request_hash(payload_data):
                raise HTTPException(status_code=409, detail={'error': 'IDEMPOTENCY_KEY_CONFLICT', 'message': 'Idempotency key reused with different payload'})
            return transaction_response(existing)
        session.add_all([LedgerEntry(transaction_id=transaction.id, account_id=bank.id, entry_type='debit', amount=amount), LedgerEntry(transaction_id=transaction.id, account_id=account.id, entry_type='credit', amount=amount)])
        await session.commit()
        increment('deposits_total')
        return transaction_response(transaction)

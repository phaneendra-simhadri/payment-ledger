from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import async_session_maker
from app.middleware.auth import auth_required
from app.models import Account, LedgerEntry, Transaction, WebhookDelivery
from app.schemas.transaction import TransferRequest
from app.services.balance import get_balance
from app.services.idempotency import check_idempotency, compute_request_hash
from app.services.ledger import lock_accounts_for_transfer
from app.services.metrics import increment

router = APIRouter(prefix='/transfers', tags=['transfers'])


def transfer_response(transaction: Transaction) -> dict:
    return {'transaction_id': str(transaction.id), 'status': transaction.status, 'from_account_id': str(transaction.from_account_id), 'to_account_id': str(transaction.to_account_id), 'amount': str(transaction.amount), 'currency': transaction.currency, 'created_at': transaction.created_at.isoformat()}


@router.post('')
async def create_transfer(payload: TransferRequest, user=Depends(auth_required), idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    if idempotency_key is None:
        raise HTTPException(status_code=400, detail={'error': 'MISSING_IDEMPOTENCY_KEY', 'message': 'Idempotency-Key header is required'})
    payload_data = payload.model_dump()
    if payload.from_account_id == payload.to_account_id:
        raise HTTPException(status_code=400, detail={'error': 'SAME_ACCOUNT_TRANSFER', 'message': 'Cannot transfer to the same account'})
    amount = Decimal(payload.amount)
    if amount <= 0:
        raise HTTPException(status_code=422, detail={'error': 'INVALID_AMOUNT', 'message': 'Amount must be greater than 0'})

    async with async_session_maker() as session:
        is_fresh, existing = await check_idempotency(idempotency_key, payload_data, session)
        if not is_fresh and existing is not None:
            return transfer_response(existing)
        from_account, to_account = await lock_accounts_for_transfer(session, payload.from_account_id, payload.to_account_id)
        if from_account is None or to_account is None:
            raise HTTPException(status_code=404, detail={'error': 'ACCOUNT_NOT_FOUND', 'message': 'Account not found'})
        if from_account.owner_id != user.id:
            raise HTTPException(status_code=403, detail={'error': 'UNAUTHORIZED', 'message': 'You do not own the source account'})
        if from_account.currency != to_account.currency or from_account.currency != payload.currency.upper():
            raise HTTPException(status_code=422, detail={'error': 'CURRENCY_MISMATCH', 'message': 'Account currencies must match'})
        if await get_balance(str(from_account.id), session) < amount:
            raise HTTPException(status_code=422, detail={'error': 'INSUFFICIENT_FUNDS', 'message': 'Account balance is insufficient'})
        transaction = Transaction(idempotency_key=idempotency_key, request_hash=compute_request_hash(payload_data), type='transfer', status='completed', amount=amount, currency=payload.currency.upper(), from_account_id=from_account.id, to_account_id=to_account.id, request_metadata={'description': payload.description})
        session.add(transaction)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            existing = await session.scalar(select(Transaction).where(Transaction.idempotency_key == idempotency_key))
            if existing is None or existing.request_hash != compute_request_hash(payload_data):
                raise HTTPException(status_code=409, detail={'error': 'IDEMPOTENCY_KEY_CONFLICT', 'message': 'Idempotency key reused with different payload'})
            return transfer_response(existing)
        session.add_all([LedgerEntry(transaction_id=transaction.id, account_id=from_account.id, entry_type='debit', amount=amount), LedgerEntry(transaction_id=transaction.id, account_id=to_account.id, entry_type='credit', amount=amount)])
        delivery = WebhookDelivery(transaction_id=transaction.id, target_url=settings.webhook_url, payload={'transaction_id': str(transaction.id), 'type': transaction.type, 'status': transaction.status, 'from_account_id': str(transaction.from_account_id), 'to_account_id': str(transaction.to_account_id), 'amount': str(transaction.amount), 'currency': transaction.currency})
        session.add(delivery)
        await session.commit()
        try:
            from app.workers.webhook_tasks import deliver_webhook
            deliver_webhook.delay(str(delivery.id))
        except Exception:
            pass
        increment('transfers_total')
        return transfer_response(transaction)

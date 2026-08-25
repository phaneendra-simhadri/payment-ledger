from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction

IDEMPOTENCY_KEY_TTL_HOURS = 24


def compute_request_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def check_idempotency(idempotency_key: str, request_payload: dict, db: AsyncSession) -> tuple[bool, Transaction | None]:
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
            detail={'error': 'IDEMPOTENCY_KEY_CONFLICT', 'message': 'Idempotency key reused with different payload'},
        )
    return False, existing

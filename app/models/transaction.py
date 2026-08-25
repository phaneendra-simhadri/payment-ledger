from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Transaction(Base):
    __tablename__ = 'transactions'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default='pending')
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default='INR')
    from_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('accounts.id'), nullable=True)
    to_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('accounts.id'), nullable=True)
    request_metadata: Mapped[dict | None] = mapped_column('metadata', JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)

    ledger_entries: Mapped[list['LedgerEntry']] = relationship(back_populates='transaction')

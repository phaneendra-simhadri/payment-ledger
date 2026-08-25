from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LedgerEntry(Base):
    __tablename__ = 'ledger_entries'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('transactions.id', ondelete='RESTRICT'), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('accounts.id', ondelete='RESTRICT'), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(6), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)

    transaction: Mapped['Transaction'] = relationship(back_populates='ledger_entries')
    account: Mapped['Account'] = relationship(back_populates='ledger_entries')

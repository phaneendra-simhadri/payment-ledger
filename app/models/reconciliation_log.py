from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Integer, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ReconciliationLog(Base):
    __tablename__ = 'reconciliation_logs'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
    accounts_checked: Mapped[int] = mapped_column(Integer, nullable=False)
    mismatches_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mismatch_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

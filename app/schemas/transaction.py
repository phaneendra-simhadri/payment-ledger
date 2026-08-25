from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DepositRequest(BaseModel):
    account_id: str
    amount: str = Field(..., pattern=r'^\d+(\.\d{1,2})?$')
    currency: str = 'INR'


class WithdrawalRequest(BaseModel):
    account_id: str
    amount: str = Field(..., pattern=r'^\d+(\.\d{1,2})?$')
    currency: str = 'INR'


class TransferRequest(BaseModel):
    from_account_id: str
    to_account_id: str
    amount: str = Field(..., pattern=r'^\d+(\.\d{1,2})?$')
    currency: str = 'INR'
    description: str | None = None


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: str
    status: str
    amount: str
    currency: str
    created_at: datetime


class TransferResponse(TransactionResponse):
    from_account_id: str | None = None
    to_account_id: str | None = None


class TransactionHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: str
    type: str
    amount: str
    direction: str
    status: str
    created_at: datetime


class AccountTransactionsResponse(BaseModel):
    account_id: str
    transactions: list[TransactionHistoryItem]
    page: int = 1
    page_size: int = 20
    total: int = 0

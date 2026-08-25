from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AccountCreate(BaseModel):
    currency: str = 'INR'


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: str
    account_type: str
    currency: str
    created_at: datetime


class BalanceResponse(BaseModel):
    account_id: str
    balance: str
    currency: str

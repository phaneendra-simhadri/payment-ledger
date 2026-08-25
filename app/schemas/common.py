from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    error: str
    message: str


class Pagination(BaseModel):
    page: int = 1
    page_size: int = 20
    total: int


class StandardResponse(BaseModel):
    status: str = 'ok'
    data: dict[str, Any] | None = None

import os
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    database_url: str = Field(default='sqlite+aiosqlite:///./ledger.db')
    redis_url: str = Field(default='redis://localhost:6379/0')
    jwt_secret: str = Field(default='change-this-in-production')
    webhook_secret: str = Field(default='change-this-in-production')
    webhook_url: str = Field(default='http://localhost:8000/webhooks')
    frontend_url: str = Field(default='http://localhost:5173')
    environment: Literal['development', 'production'] = 'development'
    log_level: str = 'INFO'
    reconciliation_interval_minutes: int = 60
    idempotency_key_ttl_hours: int = 24


settings = Settings()

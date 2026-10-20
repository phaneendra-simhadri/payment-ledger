from __future__ import annotations

import uuid
from typing import AsyncGenerator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


DATABASE_URL = settings.database_url

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def parse_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


async def ensure_system_accounts() -> None:
    from app.models import Account

    async with async_session_maker() as session:
        for account_type in ('system_bank', 'system_revenue'):
            result = await session.execute(
                select(Account).where(Account.account_type == account_type)
            )
            if result.scalar_one_or_none() is None:
                account = Account(
                    id=uuid.uuid4(),
                    owner_id=None,
                    account_type=account_type,
                    currency='INR',
                    is_active=True,
                )
                session.add(account)
        await session.commit()


async def init_db() -> None:
    if settings.environment == 'production':
        await ensure_system_accounts()
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await ensure_system_accounts()


async def create_schema_if_needed() -> None:
    await init_db()

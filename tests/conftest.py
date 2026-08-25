import asyncio
from typing import AsyncGenerator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import async_session_maker, init_db


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture()
async def db_session() -> AsyncGenerator:
    await init_db()
    async with async_session_maker() as session:
        yield session


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client

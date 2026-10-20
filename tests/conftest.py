import uuid
from typing import AsyncGenerator

import pytest
from fastapi.testclient import TestClient

from app.database import async_session_maker, init_db
from app.main import app


@pytest.fixture()
async def db_session() -> AsyncGenerator:
    await init_db()
    async with async_session_maker() as session:
        yield session


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def auth_user(client):
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    password = "secret123"

    register = client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Test User", "password": password},
    )
    assert register.status_code == 200, register.text

    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text

    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return {"email": email, "password": password, "headers": headers}


@pytest.fixture()
def user_wallet(client, auth_user):
    response = client.post(
        "/api/v1/accounts",
        headers=auth_user["headers"],
        json={"currency": "INR"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return {**auth_user, "account_id": payload["account_id"]}

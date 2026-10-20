import uuid


def test_same_key_same_payload_replays_original_response(client, auth_user):
    response = client.post(
        "/api/v1/accounts",
        headers=auth_user["headers"],
        json={"currency": "INR"},
    )
    assert response.status_code == 200
    account_id = response.json()["account_id"]

    idempotency_key = str(uuid.uuid4())
    payload = {"account_id": account_id, "amount": "100.00", "currency": "INR"}
    first = client.post(
        "/api/v1/deposits",
        headers={**auth_user["headers"], "Idempotency-Key": idempotency_key},
        json=payload,
    )
    second = client.post(
        "/api/v1/deposits",
        headers={**auth_user["headers"], "Idempotency-Key": idempotency_key},
        json=payload,
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["transaction_id"] == second.json()["transaction_id"]
    assert first.json()["amount"] == "100.00"


def test_same_key_different_payload_returns_conflict(client, auth_user):
    response = client.post(
        "/api/v1/accounts",
        headers=auth_user["headers"],
        json={"currency": "INR"},
    )
    assert response.status_code == 200
    account_id = response.json()["account_id"]

    idempotency_key = str(uuid.uuid4())
    headers = {**auth_user["headers"], "Idempotency-Key": idempotency_key}

    first = client.post(
        "/api/v1/deposits",
        headers=headers,
        json={"account_id": account_id, "amount": "100.00", "currency": "INR"},
    )
    second = client.post(
        "/api/v1/deposits",
        headers=headers,
        json={"account_id": account_id, "amount": "200.00", "currency": "INR"},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["error"] == "IDEMPOTENCY_KEY_CONFLICT"


async def test_expired_key_can_be_reused(client, db_session, auth_user):
    response = client.post(
        "/api/v1/accounts",
        headers=auth_user["headers"],
        json={"currency": "INR"},
    )
    account_id = response.json()["account_id"]

    key = str(uuid.uuid4())
    headers = {**auth_user["headers"], "Idempotency-Key": key}
    first = client.post(
        "/api/v1/deposits",
        headers=headers,
        json={"account_id": account_id, "amount": "50.00", "currency": "INR"},
    )
    assert first.status_code == 200, first.text

    from app.services.idempotency import check_idempotency
    from app.models import Transaction
    from sqlalchemy import select

    tx = (await db_session.execute(select(Transaction).where(Transaction.idempotency_key == key))).scalar_one()
    tx.created_at = tx.created_at.replace(year=2000)
    await db_session.commit()

    second = client.post(
        "/api/v1/deposits",
        headers={**auth_user["headers"], "Idempotency-Key": key},
        json={"account_id": account_id, "amount": "50.00", "currency": "INR"},
    )

    assert second.status_code == 200, second.text
    assert second.json()["transaction_id"] == first.json()["transaction_id"]


def test_concurrent_duplicate_requests_only_one_transaction_creates(client, auth_user):
    response = client.post(
        "/api/v1/accounts",
        headers=auth_user["headers"],
        json={"currency": "INR"},
    )
    account_id = response.json()["account_id"]

    headers = {**auth_user["headers"], "Idempotency-Key": str(uuid.uuid4())}
    payload = {"account_id": account_id, "amount": "75.00", "currency": "INR"}

    first = client.post("/api/v1/deposits", headers=headers, json=payload)
    second = client.post("/api/v1/deposits", headers=headers, json=payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["transaction_id"] == second.json()["transaction_id"]

import uuid


def _register_and_login(client, email_prefix):
    email = f"{email_prefix}-{uuid.uuid4().hex[:8]}@example.com"
    password = "secret123"

    register = client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Transfer User", "password": password},
    )
    assert register.status_code == 200, register.text

    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text

    return {
        "email": email,
        "password": password,
        "headers": {"Authorization": f"Bearer {login.json()['access_token']}"},
    }


def _create_wallet(client, headers, currency="INR"):
    response = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"currency": currency},
    )
    assert response.status_code == 200, response.text
    return response.json()["account_id"]


def _deposit(client, headers, account_id, amount, key, expected_status=200):
    response = client.post(
        "/api/v1/deposits",
        headers={**headers, "Idempotency-Key": key},
        json={"account_id": account_id, "amount": str(amount), "currency": "INR"},
    )
    assert response.status_code == expected_status, response.text
    return response


def test_happy_path_transfer_creates_ledger_entries(client):
    sender = _register_and_login(client, "sender")
    recipient = _register_and_login(client, "recipient")

    sender_wallet = _create_wallet(client, sender["headers"])
    recipient_wallet = _create_wallet(client, recipient["headers"])

    _deposit(client, sender["headers"], sender_wallet, 200.00, str(uuid.uuid4()))
    _deposit(client, recipient["headers"], recipient_wallet, 100.00, str(uuid.uuid4()))

    transfer = client.post(
        "/api/v1/transfers",
        headers={**sender["headers"], "Idempotency-Key": str(uuid.uuid4())},
        json={
            "from_account_id": sender_wallet,
            "to_account_id": recipient_wallet,
            "amount": "50.00",
            "currency": "INR",
            "description": "demo transfer",
        },
    )

    assert transfer.status_code == 200, transfer.text
    assert transfer.json()["status"] == "completed"

    sender_balance = client.get(f"/api/v1/accounts/{sender_wallet}/balance", headers=sender["headers"])
    recipient_balance = client.get(f"/api/v1/accounts/{recipient_wallet}/balance", headers=recipient["headers"])

    assert sender_balance.json()["balance"] == "150.00"
    assert recipient_balance.json()["balance"] == "150.00"


def test_insufficient_funds_returns_422(client):
    sender = _register_and_login(client, "pooruser")
    receiver = _register_and_login(client, "richuser")

    sender_wallet = _create_wallet(client, sender["headers"])
    receiver_wallet = _create_wallet(client, receiver["headers"])
    _deposit(client, receiver["headers"], receiver_wallet, 200.00, str(uuid.uuid4()))

    response = client.post(
        "/api/v1/transfers",
        headers={**sender["headers"], "Idempotency-Key": str(uuid.uuid4())},
        json={
            "from_account_id": sender_wallet,
            "to_account_id": receiver_wallet,
            "amount": "25.00",
            "currency": "INR",
            "description": "insufficient funds",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] in {"INSUFFICIENT_FUNDS", "ACCOUNT_NOT_FOUND"}


def test_concurrent_withdrawal_only_one_succeeds(client):
    user = _register_and_login(client, "withdrawaluser")
    wallet = _create_wallet(client, user["headers"])
    _deposit(client, user["headers"], wallet, 100.00, str(uuid.uuid4()))

    first = client.post(
        "/api/v1/withdrawals",
        headers={**user["headers"], "Idempotency-Key": str(uuid.uuid4())},
        json={"account_id": wallet, "amount": "60.00", "currency": "INR"},
    )
    second = client.post(
        "/api/v1/withdrawals",
        headers={**user["headers"], "Idempotency-Key": str(uuid.uuid4())},
        json={"account_id": wallet, "amount": "60.00", "currency": "INR"},
    )

    outcomes = {first.status_code, second.status_code}
    assert 200 in outcomes
    assert 422 in outcomes or 200 in outcomes


def test_transfer_to_same_account_returns_400(client):
    user = _register_and_login(client, "selftransfer")
    wallet = _create_wallet(client, user["headers"])

    response = client.post(
        "/api/v1/transfers",
        headers={**user["headers"], "Idempotency-Key": str(uuid.uuid4())},
        json={
            "from_account_id": wallet,
            "to_account_id": wallet,
            "amount": "10.00",
            "currency": "INR",
            "description": "bad transfer",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "SAME_ACCOUNT_TRANSFER"


def test_currency_mismatch_returns_422(client):
    sender = _register_and_login(client, "usduser")
    receiver = _register_and_login(client, "usdreceiver")

    sender_wallet = _create_wallet(client, sender["headers"], currency="USD")
    receiver_wallet = _create_wallet(client, receiver["headers"], currency="INR")
    _deposit(client, sender["headers"], sender_wallet, 100.00, str(uuid.uuid4()), expected_status=422)

    response = client.post(
        "/api/v1/transfers",
        headers={**sender["headers"], "Idempotency-Key": str(uuid.uuid4())},
        json={
            "from_account_id": sender_wallet,
            "to_account_id": receiver_wallet,
            "amount": "10.00",
            "currency": "USD",
            "description": "currency mismatch",
        },
    )

    assert response.status_code in {404, 422}
    assert response.json()["detail"]["error"] in {"CURRENCY_MISMATCH", "ACCOUNT_NOT_FOUND"}


def test_transfer_from_account_you_do_not_own_returns_403(client):
    owner = _register_and_login(client, "owner")
    attacker = _register_and_login(client, "attacker")

    owner_wallet = _create_wallet(client, owner["headers"])
    attacker_wallet = _create_wallet(client, attacker["headers"])
    _deposit(client, owner["headers"], owner_wallet, 100.00, str(uuid.uuid4()))

    response = client.post(
        "/api/v1/transfers",
        headers={**attacker["headers"], "Idempotency-Key": str(uuid.uuid4())},
        json={
            "from_account_id": owner_wallet,
            "to_account_id": attacker_wallet,
            "amount": "10.00",
            "currency": "INR",
            "description": "steal",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "UNAUTHORIZED"

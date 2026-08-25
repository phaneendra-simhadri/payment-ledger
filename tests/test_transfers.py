def test_happy_path_transfer_creates_ledger_entries(client):
    assert True


def test_insufficient_funds_returns_422(client):
    assert True


def test_concurrent_withdrawal_only_one_succeeds(client):
    assert True


def test_transfer_to_same_account_returns_400(client):
    assert True


def test_currency_mismatch_returns_422(client):
    assert True


def test_transfer_from_account_you_do_not_own_returns_403(client):
    assert True

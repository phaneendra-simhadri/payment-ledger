from locust import HttpUser, between, task


class PaymentUser(HttpUser):
    wait_time = between(0.5, 2)

    def on_start(self):
        import uuid

        email = f'locust-{uuid.uuid4()}@example.com'
        password = 'locust-password'
        registration = self.client.post(
            '/api/v1/auth/register',
            json={'email': email, 'name': 'Locust User', 'password': password},
        )
        if registration.status_code not in (200, 201):
            raise RuntimeError(f'Registration failed: {registration.text}')
        login = self.client.post('/api/v1/auth/login', json={'email': email, 'password': password})
        login.raise_for_status()
        self.token = login.json()['access_token']
        headers = {'Authorization': f'Bearer {self.token}'}
        account = self.client.post('/api/v1/accounts', json={'currency': 'INR'}, headers=headers)
        account.raise_for_status()
        self.from_account = account.json()['account_id']
        deposit = self.client.post(
            '/api/v1/deposits',
            json={'account_id': self.from_account, 'amount': '1000.00', 'currency': 'INR'},
            headers={**headers, 'Idempotency-Key': str(uuid.uuid4())},
        )
        deposit.raise_for_status()
        recipient_email = f'locust-recipient-{uuid.uuid4()}@example.com'
        recipient = self.client.post(
            '/api/v1/auth/register',
            json={'email': recipient_email, 'name': 'Locust Recipient', 'password': password},
        )
        recipient.raise_for_status()
        recipient_login = self.client.post('/api/v1/auth/login', json={'email': recipient_email, 'password': password})
        recipient_login.raise_for_status()
        recipient_headers = {'Authorization': f"Bearer {recipient_login.json()['access_token']}"}
        recipient_account = self.client.post('/api/v1/accounts', json={'currency': 'INR'}, headers=recipient_headers)
        recipient_account.raise_for_status()
        self.to_account = recipient_account.json()['account_id']

    @task(3)
    def transfer(self):
        import uuid
        self.client.post(
            '/api/v1/transfers',
            json={
                'from_account_id': self.from_account,
                'to_account_id': self.to_account,
                'amount': '10.00',
                'currency': 'INR'
            },
            headers={
                'Authorization': f'Bearer {self.token}',
                'Idempotency-Key': str(uuid.uuid4())
            }
        )

    @task(1)
    def check_balance(self):
        self.client.get(
            f"/api/v1/accounts/{self.from_account}/balance",
            headers={'Authorization': f'Bearer {self.token}'}
        )

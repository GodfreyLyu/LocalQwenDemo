import time

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from app.config import Settings
from app.main import create_app


class FakeModel:
    def __init__(self, result="## Summary\nCheck empty input before division.", delay=0.01):
        self.result, self.delay = result, delay
        self.calls = self.active = self.peak = 0

    def load(self):
        pass

    def count_tokens(self, source, language):
        return len(source.split()) + 50

    def review(self, source, language, stop):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            stop.wait(self.delay)
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
        finally:
            self.active -= 1


def wait_ready(client):
    for _ in range(200):
        if client.get("/health/ready").status_code == 200:
            return
        time.sleep(0.01)
    raise AssertionError("Application did not become ready")


def wait_review(client, review_id):
    for _ in range(200):
        response = client.get(f"/api/v1/reviews/{review_id}")
        assert response.status_code == 200, response.text
        if response.json()["status"] in ("completed", "failed"):
            return response.json()
        time.sleep(0.01)
    raise AssertionError("Review did not finish")


def register(client, name="alice", password="correct-horse-battery"):
    response = client.post("/api/v1/auth/register", json={"login_id": name, "password": password})
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


@pytest.fixture
def factory(tmp_path, monkeypatch):
    """Create real API/coordinator lifecycles with Moto accounts and temporary SQLite.

    FakeModel replaces only inference. Closing clients in reverse order drains
    their lifespan workers before the Moto context and temporary storage disappear."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        # Only the test transport uses Moto's intercepted SDK endpoint. Runtime factory safety
        # is tested separately; no application mode allows a regional endpoint.
        monkeypatch.setattr(
            "app.users.local_client",
            lambda endpoint, region: boto3.client(
                "dynamodb",
                region_name=region,
                aws_access_key_id="testing",
                aws_secret_access_key="testing",
            ),
        )
        boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="test-users",
            KeySchema=[{"AttributeName": "login_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "login_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        clients = []

        def make(model=None, ready=True, **overrides):
            values = dict(
                environment="test",
                signing_secret="test-only-" * 5,
                allowed_origin="https://testserver",
                cookie_secure=True,
                data_dir=tmp_path,
                aws_region="us-east-1",
                dynamodb_table="test-users",
                dynamodb_endpoint_url="http://127.0.0.1:8001",
            )
            values.update(overrides)
            app = create_app(Settings(**values), model=model or FakeModel())
            client = TestClient(
                app, base_url="https://testserver", headers={"Origin": "https://testserver"}
            )
            client.__enter__()
            clients.append(client)
            if ready:
                wait_ready(client)
            return client

        yield make
        for client in reversed(clients):
            client.__exit__(None, None, None)

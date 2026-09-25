"""Local table initialization exercises the real SDK serializer with stubbed responses."""

import sys
from pathlib import Path

import pytest
from botocore.stub import Stubber

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import init_local_users as init  # noqa: E402

from app.local_dynamodb import local_client  # noqa: E402

TABLE = {
    "TableName": "llm-review-users",
    "TableStatus": "ACTIVE",
    "KeySchema": [{"AttributeName": "login_id", "KeyType": "HASH"}],
    "AttributeDefinitions": [{"AttributeName": "login_id", "AttributeType": "S"}],
}
PARAMS = {"TableName": "llm-review-users"}


@pytest.mark.parametrize("exists", [True, False])
def test_initialization_reuses_existing_table_or_creates_once(monkeypatch, exists):
    monkeypatch.setenv("DYNAMODB_ENDPOINT_URL", "http://127.0.0.1:8001")
    client = local_client("http://127.0.0.1:8001")
    monkeypatch.setattr(init, "local_client", lambda _: client)
    with Stubber(client) as stub:
        if not exists:
            stub.add_client_error(
                "describe_table",
                service_error_code="ResourceNotFoundException",
                expected_params=PARAMS,
            )
            stub.add_response(
                "create_table",
                {"TableDescription": TABLE},
                PARAMS
                | {
                    "BillingMode": "PAY_PER_REQUEST",
                    "KeySchema": TABLE["KeySchema"],
                    "AttributeDefinitions": TABLE["AttributeDefinitions"],
                },
            )
            stub.add_response("describe_table", {"Table": TABLE}, PARAMS)  # Bounded waiter.
        stub.add_response("describe_table", {"Table": TABLE}, PARAMS)
        init.initialize()
        # A repeated initializer must only inspect, never create, delete, or alter existing data.
        stub.add_response("describe_table", {"Table": TABLE}, PARAMS)
        init.initialize()
        stub.assert_no_pending_responses()


def test_schema_conflict_is_not_overwritten(monkeypatch):
    monkeypatch.setenv("DYNAMODB_ENDPOINT_URL", "http://localhost:8001")
    client = local_client("http://localhost:8001")
    monkeypatch.setattr(init, "local_client", lambda _: client)
    with Stubber(client) as stub:
        stub.add_response(
            "describe_table",
            {"Table": TABLE | {"KeySchema": [{"AttributeName": "wrong", "KeyType": "HASH"}]}},
            PARAMS,
        )
        with pytest.raises(ValueError, match="existing data retained"):
            init.initialize()
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "endpoint", ["", "http://review-dynamodb:8000", "https://dynamodb.us-east-1.amazonaws.com"]
)
def test_initializer_requires_explicit_loopback_transport(monkeypatch, endpoint):
    monkeypatch.setenv("DYNAMODB_ENDPOINT_URL", endpoint)
    monkeypatch.setattr(init, "local_client", lambda _: pytest.fail("Must not construct client"))
    with pytest.raises(ValueError):
        init.initialize()

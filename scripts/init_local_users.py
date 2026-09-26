"""Idempotently initialize the local users table; reject missing/non-loopback endpoints."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.persistence.local_dynamodb import local_client, validate_local_endpoint  # noqa: E402


def initialize():
    endpoint = os.environ.get("DYNAMODB_ENDPOINT_URL", "")
    validate_local_endpoint(endpoint, loopback_only=True)
    db = local_client(endpoint)
    name = "llm-review-users"
    try:
        table = db.describe_table(TableName=name)["Table"]
    except db.exceptions.ResourceNotFoundException:
        try:
            db.create_table(
                TableName=name,
                BillingMode="PAY_PER_REQUEST",
                KeySchema=[{"AttributeName": "login_id", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "login_id", "AttributeType": "S"}],
            )
        except db.exceptions.ResourceInUseException:
            pass  # Another initializer won; verify its schema instead of replacing anything.
        db.get_waiter("table_exists").wait(
            TableName=name, WaiterConfig={"Delay": 1, "MaxAttempts": 30}
        )
        table = db.describe_table(TableName=name)["Table"]
    if (
        table["TableStatus"] != "ACTIVE"
        or table["KeySchema"] != [{"AttributeName": "login_id", "KeyType": "HASH"}]
        or table["AttributeDefinitions"] != [{"AttributeName": "login_id", "AttributeType": "S"}]
    ):
        raise ValueError(
            "DynamoDB Local users table schema/status mismatch; existing data retained."
        )
    print("Local user table is ready; existing accounts retained.")


if __name__ == "__main__":
    try:
        initialize()
    except Exception as error:
        print(
            f"Local table initialization failed ({type(error).__name__}); details withheld.",
            file=sys.stderr,
        )
        sys.exit(1)

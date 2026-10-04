"""Idempotently initialize the local users table; reject missing/non-loopback endpoints."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.persistence.initialize_users import ensure_users_table
from app.persistence.local_dynamodb import (
    local_client,
    validate_local_endpoint,
)


def initialize():
    endpoint = os.environ.get("DYNAMODB_ENDPOINT_URL", "")
    validate_local_endpoint(endpoint, loopback_only=True)
    db = local_client(endpoint)
    ensure_users_table(db, "llm-review-users")
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

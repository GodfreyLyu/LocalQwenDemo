"""Local-only test harness: Moto emulates DynamoDB; optionally use deterministic inference."""

import argparse
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fake-model", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".local" / "demo")
    args = parser.parse_args()
    # This process only uses the mocked service. Never use an AWS credential profile.
    os.environ.update(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_EC2_METADATA_DISABLED="true",
        AWS_CONFIG_FILE=os.devnull,
        AWS_SHARED_CREDENTIALS_FILE=os.devnull,
    )
    for key in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        os.environ.pop(key, None)
    import boto3
    import uvicorn
    from moto import mock_aws

    from app.config import Settings
    from app.main import create_app
    from app.users import DynamoUsers

    class DemoModel:
        def load(self):
            pass

        def count_tokens(self, source, language):
            return len(source.split()) + 50

        def review(self, source, language, stop):
            stop.wait(0.8)
            return (
                "## Summary\nDeterministic test review.\n\n## Findings\n"
                "Check empty inputs before division.\n\n## Suggestions\n"
                "Add an explicit guard and verify the expected behavior.\n\n"
                "This local test fixture does not perform real model inference."
            )

    settings = Settings(
        environment="local",
        signing_secret=secrets.token_urlsafe(48),
        cookie_secure=False,
        allowed_origin="http://localhost:5173",
        data_dir=args.data_dir,
        aws_region="us-east-1",
        dynamodb_table="demo-users",
        dynamodb_endpoint_url="http://127.0.0.1:8001",
        hf_home=ROOT / ".local" / "models" / "huggingface",
    )
    with mock_aws():
        boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="demo-users",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "login_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "login_id", "AttributeType": "S"}],
        )
        print(
            "Local harness: accounts reset on restart; reviews persist on disk. No AWS resources."
        )

        class DemoUsers(DynamoUsers):
            # This fake harness injects Moto's intercepted client, never the runtime transport.
            def __init__(self):
                self.client = boto3.client(
                    "dynamodb",
                    region_name="us-east-1",
                    aws_access_key_id="testing",
                    aws_secret_access_key="testing",
                )
                self.table_name = settings.dynamodb_table

        app = create_app(
            settings, users=DemoUsers(), model=DemoModel() if args.fake_model else None
        )
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()

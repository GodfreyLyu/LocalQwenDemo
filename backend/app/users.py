import time
from uuid import uuid4

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from app.errors import AppError
from app.local_dynamodb import local_client


class DynamoUsers:
    def __init__(self, settings):
        # Low-level clients can be shared by FastAPI worker threads; resource objects cannot.
        self.client = local_client(settings.dynamodb_endpoint_url, settings.aws_region)
        self.table_name = settings.dynamodb_table

    def ping(self):
        self.client.describe_table(TableName=self.table_name)

    def get(self, login_id):
        item = self.client.get_item(
            TableName=self.table_name,
            Key={"login_id": {"S": login_id}},
            ConsistentRead=True,
        ).get("Item")
        if item is None:
            return None
        deserialize = TypeDeserializer().deserialize
        return {key: deserialize(value) for key, value in item.items()}

    def create(self, login_id, password_hash):
        user = {
            "login_id": login_id,
            "user_id": str(uuid4()),
            "password_hash": password_hash,
            "created_at": str(time.time()),
            "disabled": False,
        }
        serialize = TypeSerializer().serialize
        try:
            self.client.put_item(
                TableName=self.table_name,
                Item={key: serialize(value) for key, value in user.items()},
                ConditionExpression="attribute_not_exists(login_id)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise AppError(
                    "login_unavailable", "This login identifier is unavailable.", 409
                ) from None
            raise
        return user

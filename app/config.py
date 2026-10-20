"""Settings and DB credentials.

Credentials come from AWS Secrets Manager (DB_SECRET_ARN). For local dev you
can instead set DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD directly.
"""
import json
import os
from functools import lru_cache

CERT_PATH = os.environ.get(
    "DB_SSLROOTCERT",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "certs", "global-bundle.pem"),
)


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes")


# Turn on once the frontend sends the X-Session-Id header (see README).
AUTH_ENFORCED = _bool("AUTH_ENFORCED", False)
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "24"))

CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS",
        "https://app.lumico.com.au,https://lumico-work-frontend.vercel.app,http://localhost:5173",
    ).split(",")
    if o.strip()
]


@lru_cache(maxsize=1)
def db_credentials() -> dict:
    arn = os.environ.get("DB_SECRET_ARN")
    if arn:
        import boto3  # available in the Lambda runtime

        client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "ap-southeast-2"))
        secret = json.loads(client.get_secret_value(SecretId=arn)["SecretString"])
        return {
            "host": secret["host"],
            "port": int(secret.get("port", 5432)),
            "dbname": secret["dbname"],
            "user": secret["username"],
            "password": secret["password"],
        }
    return {
        "host": os.environ["DB_HOST"],
        "port": int(os.environ.get("DB_PORT", "5432")),
        "dbname": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
    }


def conninfo() -> dict:
    """psycopg connection kwargs. Verifies the RDS certificate when the CA bundle is present."""
    creds = db_credentials()
    if os.path.exists(CERT_PATH):
        creds = {**creds, "sslmode": "verify-full", "sslrootcert": CERT_PATH}
    else:
        # Local dev only. Production packages must include certs/global-bundle.pem.
        if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
            raise RuntimeError("certs/global-bundle.pem missing from the Lambda package")
        creds = {**creds, "sslmode": "require"}
    return creds

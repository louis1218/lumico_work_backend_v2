"""Settings and DB credentials.

Credentials come from AWS Secrets Manager (DB_SECRET_ARN). For local dev you
can instead set DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD directly.
"""

import json
import os
from functools import lru_cache

CERT_PATH = os.environ.get(
    "DB_SSLROOTCERT",
    os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "certs", "global-bundle.pem"
    ),
)


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes")


# Turn on once the frontend sends the X-Session-Id header (see README).
AUTH_ENFORCED = _bool("AUTH_ENFORCED", False)
SESSION_HOURS = int(
    os.environ.get("SESSION_HOURS", "168")
)  # matches the 12h the frontend uses
S3_BUCKET = os.environ.get("S3_BUCKET", "solar-farm-uploads")
S3_REGION = os.environ.get("S3_REGION", "ap-southeast-2")
S3_PUBLIC_BASE = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com"
TIMEZONE = os.environ.get(
    "TIMEZONE", "Australia/Adelaide"
)  # used only when the app sends no work_date

# Solar farm layout
BLOCKS = 25
TRACKERS_PER_BLOCK = 180
DEFECT_TYPES = [
    "Cracked glass",
    "Shattered glass",
    "Hotspot or burn mark",
    "Delamination or discolouration",
    "Damaged frame",
    "Loose or misaligned",
    "Cable or connector damage",
    "Heavy soiling",
    "Shading",
    "Other",
]

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

        client = boto3.client(
            "secretsmanager", region_name=os.environ.get("AWS_REGION", "ap-southeast-2")
        )
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
    local_mode = os.environ.get("DB_SSLMODE")  # local testing only, ignored on Lambda
    if local_mode and not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        creds = {**creds, "sslmode": local_mode}
    elif os.path.exists(CERT_PATH):
        creds = {**creds, "sslmode": "verify-full", "sslrootcert": CERT_PATH}
    else:
        # Local dev only. Production packages must include certs/global-bundle.pem.
        if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
            raise RuntimeError(
                "certs/global-bundle.pem missing from the Lambda package"
            )
        creds = {**creds, "sslmode": "require"}
    return creds

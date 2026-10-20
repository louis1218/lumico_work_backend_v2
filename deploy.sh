#!/usr/bin/env bash
# Deploy the Lumico API to the Lumico AWS account.
#   ./deploy.sh              run tests, build, upload, smoke-test
#   ./deploy.sh --skip-tests same, without pytest
#
# Needs an AWS CLI profile named "lumico" (see README: "Deploying"). Override with AWS_PROFILE_NAME.
set -euo pipefail
cd "$(dirname "$0")"

PROFILE="${AWS_PROFILE_NAME:-lumico}"
REGION="ap-southeast-2"
FUNCTION="lumico-api"
EXPECTED_ACCOUNT="640527821355"   # Lumico. Never deploy anywhere else.
PY_TARGET="3.13"                  # Lambda runtime
PYTHON="${PYTHON:-}"
[ -z "$PYTHON" ] && { [ -x .venv/bin/python ] && PYTHON=.venv/bin/python || PYTHON=python3; }

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
aws() { command aws --profile "$PROFILE" --region "$REGION" "$@"; }

# 1. Make sure we are talking to the Lumico account
say "Checking AWS account (profile: $PROFILE)"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)" \
  || die "Cannot use AWS profile '$PROFILE'. Run: aws configure --profile $PROFILE"
[ "$ACCOUNT" = "$EXPECTED_ACCOUNT" ] \
  || die "Profile '$PROFILE' is account $ACCOUNT, expected Lumico $EXPECTED_ACCOUNT. Nothing was deployed."
echo "OK: account $ACCOUNT"

# 2. Tests
if [ "${1:-}" != "--skip-tests" ]; then
  say "Running tests"
  "$PYTHON" -m pytest -q tests
fi

# 3. Build
say "Building lumico-api.zip (Linux x86_64, Python $PY_TARGET)"
[ -f certs/global-bundle.pem ] || {
  echo "Downloading RDS CA bundle"
  curl -fsS -o certs/global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
}
rm -rf build lumico-api.zip && mkdir build
grep -vE '^(boto3|uvicorn)' requirements.txt > build-req.txt   # boto3 is already in the Lambda runtime
"$PYTHON" -m pip install -q -r build-req.txt --target build \
  --platform manylinux2014_x86_64 --only-binary=:all: --python-version "$PY_TARGET"
rm -f build-req.txt
cp -r app certs lambda_handler.py build/
(cd build && zip -qr ../lumico-api.zip . -x '*.pyc' '*/__pycache__/*')
rm -rf build
SIZE="$(wc -c < lumico-api.zip | tr -d ' ')"
echo "zip size: $((SIZE / 1024 / 1024)) MB"
[ "$SIZE" -lt 50000000 ] || die "Zip is over 50 MB, too big for a direct upload."

# 4. Upload and wait
say "Uploading to Lambda function $FUNCTION"
aws lambda update-function-code --function-name "$FUNCTION" --zip-file fileb://lumico-api.zip \
  --query '[FunctionName,LastModified,CodeSize]' --output text
aws lambda wait function-updated --function-name "$FUNCTION"

# 5. Smoke test /health through the real Lambda
say "Smoke test: GET /health"
EVENT="$(mktemp)"; OUT="$(mktemp)"
cat > "$EVENT" <<'JSON'
{"version":"2.0","routeKey":"$default","rawPath":"/health","rawQueryString":"",
 "headers":{"host":"example.com"},
 "requestContext":{"http":{"method":"GET","path":"/health","sourceIp":"1.2.3.4","protocol":"HTTP/1.1","userAgent":"deploy"},"stage":"$default"},
 "isBase64Encoded":false}
JSON
aws lambda invoke --function-name "$FUNCTION" --cli-binary-format raw-in-base64-out \
  --payload "file://$EVENT" "$OUT" >/dev/null
if grep -q '"statusCode": *200' "$OUT"; then
  say "Deployed OK"
else
  cat "$OUT"; rm -f "$EVENT" "$OUT"
  die "Smoke test failed. The new code is uploaded but not healthy; check CloudWatch logs for /aws/lambda/$FUNCTION."
fi
rm -f "$EVENT" "$OUT"
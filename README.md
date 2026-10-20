# Lumico API (FastAPI)

One FastAPI app replacing the 14 Node Lambdas in `lumico_lambda`. Same request/response
shapes, so the frontend only needs new URLs.

## Old Lambda → new route

| Old API Gateway call | New route | Notes |
|---|---|---|
| `SiteLogin` | `POST /auth/login` | one error message for bad user or bad password |
| `siteLogout` | `POST /auth/logout` | |
| `...Password` (reset-password) | `POST /auth/reset-password` | |
| `getUserList` | `GET /users` | public (login screen needs it) |
| `getTimeSheet` | `POST /timesheet/query` | `work_date` now required (old code crashed without it) |
| `logTimesheet` | `POST /timesheet` | |
| `getWorkLogSharedEditSession` | `POST /sessions/upload/get` | |
| `WorkLogSharedEditSession` | `POST /sessions/upload` | |
| `getSharedSessionRealignmentLog` | `POST /sessions/realignment/get` | |
| `SharedSessionRealignmentTask` | `POST /sessions/realignment` | |
| `getUserWorkLog` | `GET /work-logs?taskType=&date=&site=&block=&tracker_id=` | |
| `...FarmWorkLog` (solar_farm_work_log) | `POST /work-logs/solar-farm` | |
| `issueReport` | `POST /issues` | |
| `...FarmTrackerScan` | `POST /barcode/scan` | public until we know who calls it |
| `/health` | `GET /health` | |

NOT ported yet (code is not in git, export it from the Lambda console first):
`getPresignedURL-1`, `energy-quote`, `BarcodeScanCheckSerialNumber`, `getBarcodeLogsForGoogleSheet`.

## Configuration (environment variables)

| Variable | Meaning |
|---|---|
| `DB_SECRET_ARN` | Secrets Manager secret `lumico/prod/db` (host, port, dbname, username, password) |
| `AUTH_ENFORCED` | `false` at first. `true` = protected routes need the `X-Session-Id` header |
| `CORS_ORIGINS` | comma-separated allowed origins (defaults include app.lumico.com.au) |
| `SESSION_HOURS` | session lifetime when enforced (default 24) |
| `DB_SSLROOTCERT` | optional path to the RDS CA bundle (default `certs/global-bundle.pem`) |

For local dev you can skip the secret and set `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest httpx
curl -o certs/global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
pytest -q tests                      # no database needed
export DB_HOST=... DB_NAME=postgres DB_USER=lumico_app DB_PASSWORD=...
uvicorn app.main:app --reload --port 8000
```

## Package for Lambda (x86_64, Python 3.13)

```bash
rm -rf build && mkdir build
pip install -r requirements.txt --target build \
  --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.13
cp -r app certs lambda_handler.py build/
(cd build && zip -qr ../lumico-api.zip .)
```

Lambda handler: `lambda_handler.handler`. Memory 512 MB, timeout 15 s.

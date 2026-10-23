import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config
from .routers import auth, barcode, inspections, reports, sessions, timesheet, uploads, users, work_logs

logger = logging.getLogger("lumico")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Lumico API", docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# The frontend reads {"error": "..."} from every failure, so keep that shape.
@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    return JSONResponse({"error": "Invalid request"}, status_code=400)


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    logger.exception("Unhandled error")  # details go to CloudWatch, not to the caller
    return JSONResponse({"error": "Internal server error"}, status_code=500)


@app.get("/health")
def health():
    return {"status": "ok"}


for r in (auth.router, users.router, timesheet.router, sessions.router, work_logs.router, barcode.router, uploads.router,
          inspections.router, reports.router):
    app.include_router(r)

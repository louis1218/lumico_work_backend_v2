"""Replaces: getPresignedURL-1.

The app asks for a short-lived upload address, then PUTs the photo straight to S3
(the photo never passes through the API). Response shape is unchanged: {uploadURL, key}.
"""
import base64
import binascii
import re
import secrets
from functools import lru_cache

import boto3
from botocore.config import Config
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import config
from ..auth import require_session

router = APIRouter(prefix="/uploads", tags=["uploads"], dependencies=[Depends(require_session)])

_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


@lru_cache(maxsize=1)
def _s3():
    return boto3.client("s3", region_name=config.S3_REGION, config=Config(signature_version="s3v4"))


def _clean(value: str, limit: int) -> str:
    return _UNSAFE.sub("_", value).strip("._")[:limit]


def _new_key(project: str, mode: str, filename: str, contentType: str) -> tuple[str, str]:
    ext = _EXT.get(str(contentType or "").lower())
    if not ext:
        raise HTTPException(status_code=400, detail="Unsupported file type (images only)")
    project, mode, name = _clean(str(project or ""), 60), _clean(str(mode or ""), 40), _clean(str(filename or ""), 120)
    if not project or not mode or not name:
        raise HTTPException(status_code=400, detail="Missing or invalid project, mode or filename")
    # Random suffix so two uploads with the same name can never overwrite each other.
    return f"{project}/{mode}/{name}_{secrets.token_hex(3)}{ext}", contentType.lower()


MAX_PHOTO_BYTES = 4 * 1024 * 1024  # Lambda takes at most 6 MB per request, and base64 adds a third


@router.post("/photo")
def upload_photo(payload: dict = Body(...)):
    """Fallback for phones: the photo comes through the API and the API writes it to S3.

    Used when a direct PUT to S3 will not start. After a phone loses and regains signal, iOS can keep
    a dead connection to the S3 address for about a minute, while the API address works at once.
    """
    key, content_type = _new_key(payload.get("project"), payload.get("mode"), payload.get("filename"), payload.get("contentType"))
    try:
        data = base64.b64decode(str(payload.get("data") or ""), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid photo")
    if not data:
        raise HTTPException(status_code=400, detail="Invalid photo")
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="Photo is too large")
    _s3().put_object(Bucket=config.S3_BUCKET, Key=key, Body=data, ContentType=content_type)
    return {"key": key}


@router.get("/presign")
def presign(project: str, mode: str, filename: str, contentType: str):
    key, _ = _new_key(project, mode, filename, contentType)
    url = _s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": config.S3_BUCKET, "Key": key, "ContentType": contentType},
        ExpiresIn=300,
    )
    return {"uploadURL": url, "key": key}

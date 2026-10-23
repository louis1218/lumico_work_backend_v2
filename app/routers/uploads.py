"""Replaces: getPresignedURL-1.

The app asks for a short-lived upload address, then PUTs the photo straight to S3
(the photo never passes through the API). Response shape is unchanged: {uploadURL, key}.
"""
import re
import secrets
from functools import lru_cache

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, HTTPException

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


@router.get("/presign")
def presign(project: str, mode: str, filename: str, contentType: str):
    ext = _EXT.get(contentType.lower())
    if not ext:
        raise HTTPException(status_code=400, detail="Unsupported file type (images only)")
    project, mode, name = _clean(project, 60), _clean(mode, 40), _clean(filename, 120)
    if not project or not mode or not name:
        raise HTTPException(status_code=400, detail="Missing or invalid project, mode or filename")

    # Random suffix so two uploads with the same name can never overwrite each other.
    key = f"{project}/{mode}/{name}_{secrets.token_hex(3)}{ext}"
    url = _s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": config.S3_BUCKET, "Key": key, "ContentType": contentType},
        ExpiresIn=300,
    )
    return {"uploadURL": url, "key": key}

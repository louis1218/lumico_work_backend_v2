from typing import Iterable

from fastapi import HTTPException


def require_fields(payload: dict, fields: Iterable[str], message: str = "Missing required fields") -> None:
    """Same 400 + {"error": ...} behaviour as the old Lambdas (None or "" counts as missing)."""
    for f in fields:
        v = payload.get(f)
        if v is None or v == "":
            raise HTTPException(status_code=400, detail=message)

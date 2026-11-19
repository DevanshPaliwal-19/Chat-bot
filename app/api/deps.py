from __future__ import annotations

import hmac
import re

from fastapi import Request

from ..container import Container
from ..errors import Forbidden, RateLimited, ValidationFailed
from ..schemas import ID_PATTERN

_ID = re.compile(ID_PATTERN)


def get_container(request: Request) -> Container:
    return request.app.state.container


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request, bucket: str, session_id: str | None = None) -> None:
    c = get_container(request)
    allowed, retry = c.limiter.check(f"{bucket}:{client_ip(request)}:{session_id or '-'}")
    if not allowed:
        raise RateLimited(headers={"Retry-After": str(int(retry) + 1)})


def require_admin(request: Request) -> None:
    key = get_container(request).settings.admin_api_key
    if key is None:
        raise Forbidden("Admin endpoints are disabled because no admin key is configured.", code="admin_disabled")
    supplied = request.headers.get("x-admin-key", "")
    if not hmac.compare_digest(supplied.encode(), key.get_secret_value().encode()):
        raise Forbidden("Invalid admin key.", code="admin_forbidden")


def check_id(value: str | None, name: str, *, required: bool = True) -> str | None:
    if value is None or value == "":
        if required:
            raise ValidationFailed(f"{name} is required.", code="invalid_id")
        return None
    if not _ID.match(value):
        raise ValidationFailed(f"{name} is not valid.", code="invalid_id")
    return value

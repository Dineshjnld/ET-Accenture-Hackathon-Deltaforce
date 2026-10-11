"""Common API dependencies: pagination, request context, audit metadata."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.schemas import PageParams


@dataclass
class RequestContext:
    actor: str
    actor_role: str | None
    correlation_id: str
    ip: str | None
    path: str
    method: str


def get_context(request: Request) -> RequestContext:
    import uuid

    actor = "anonymous"
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            from app.security import decode_token

            payload = decode_token(auth.split(" ", 1)[1])
            actor = str(payload.get("sub", actor))
        except Exception:
            actor = "anonymous"
    return RequestContext(
        actor=actor,
        actor_role=request.headers.get("x-actor-role"),
        correlation_id=request.headers.get("x-correlation-id") or f"corr-{uuid.uuid4().hex[:12]}",
        ip=request.client.host if request.client else None,
        path=request.url.path,
        method=request.method,
    )


def pagination(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    sort: str | None = Query(None),
    order: str = Query("desc", pattern="^(asc|desc)$"),
) -> PageParams:
    return PageParams(page=page, page_size=page_size, sort=sort, order=order)  # type: ignore[arg-type]


def page_response(items: list[Any], total: int, params: PageParams) -> dict[str, Any]:
    pages = max(1, (total + params.page_size - 1) // params.page_size) if total else 0
    return {
        "items": items,
        "total": total,
        "page": params.page,
        "page_size": params.page_size,
        "pages": pages,
    }


DbSession = Depends(get_db)
Ctx = Depends(get_context)
Page = Depends(pagination)

__all__ = [
    "RequestContext",
    "get_context",
    "pagination",
    "page_response",
    "DbSession",
    "Ctx",
    "Page",
    "settings",
]

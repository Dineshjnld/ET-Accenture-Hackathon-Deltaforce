"""Top-level API router — wires all feature modules."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.compliance import router as compliance_router
from app.api.v1.documents import router as documents_router
from app.api.v1.system import router as system_router

router = APIRouter()

# Feature modules — each defines a ``router`` FastAPI.APIRouter
router.include_router(compliance_router, tags=["compliance"])
router.include_router(documents_router, tags=["documents"])
router.include_router(system_router, tags=["system"])


__all__ = ["router"]
"""FastAPI application entry point for ReguLens AI."""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.errors import register_exception_handlers, AppError, NotFoundError, ValidationError, UnauthorizedError, PermissionDeniedError
from app.logging_config import configure_logging, get_logger

from app.api.v1.router import router

logger = get_logger("regulens.main")


def create_app() -> FastAPI:
    """Application factory."""
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: ensure DB tables exist (import models to register tables).
        import app.models  # noqa: F401
        from app.db import Base, get_engine

        Base.metadata.create_all(bind=get_engine())
        logger.info("application.startup", app_name=settings.APP_NAME)
        yield
        # Shutdown
        logger.info("application.shutdown", app_name=settings.APP_NAME)

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=settings.APP_TAGLINE,
        lifespan=lifespan,
    )

    # CORS — explicitly configured, no wildcards
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.CORS_ORIGINS),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "X-Requested-With",
            "X-Correlation-ID",
            "X-Actor-Role",
        ],
        expose_headers=["X-Correlation-ID"],
        max_age=600,
    )

    # API
    app.include_router(router, prefix=settings.API_PREFIX)

    # Global exception handlers
    register_exception_handlers(app)

    return app


app = create_app()
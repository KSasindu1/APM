from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.planning import (
    router as planning_router,
)
from app.core.config import (
    get_settings,
    validate_configuration,
)


logging.basicConfig(
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(
    app: FastAPI,
):

    try:

        validate_configuration()

        logger.info(
            "Application configuration validated."
        )

    except Exception:

        logger.exception(
            "Application configuration validation failed."
        )

        raise

    logger.info(
        "Application startup complete."
    )

    yield

    logger.info(
        "Application shutdown."
    )


settings = get_settings()


app = FastAPI(
    title="Agentic Project Management System",
    description=(
        "AI-powered multi-agent project management system"
    ),
    version="1.0.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=list(
        settings.cors_origins
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(
    planning_router
)


@app.get("/")
async def root():

    return {
        "message": (
            "Agentic Project Management System API"
        ),
        "status": "running",
        "version": "1.0.0",
    }


@app.get("/health")
async def health():

    return {
        "status": "healthy",
    }
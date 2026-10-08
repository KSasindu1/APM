from __future__ import annotations

import logging
import threading
import time
import webbrowser
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.planning import router as planning_router
from app.api.risks import router as risk_router
from app.core.config import (
    get_settings,
    validate_configuration,
)


logging.basicConfig(
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


def open_swagger():
    """
    Open Swagger UI after FastAPI starts.
    """

    time.sleep(1.5)

    webbrowser.open(
        "http://127.0.0.1:8000/docs"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        validate_configuration()
    except Exception:
        logger.error(
            "Application configuration validation failed."
        )
        raise

    logger.info(
        "Application configuration validated."
    )

    threading.Thread(
        target=open_swagger,
        daemon=True,
    ).start()

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
        "AI-powered multi-agent project management system "
        "with Risk & Knowledge Retrieval and Planning & Scheduling."
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
    risk_router
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

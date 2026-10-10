
from __future__ import annotations

import logging
import threading
import time
import webbrowser

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------
# API Routers
# ---------------------------------------------------------

from app.api.auth import router as auth_router
from app.api.accounts import router as accounts_router
from app.api.risks import router as risk_router
from app.api.planning import router as planning_router
from app.agents.coordinator.router import router as coordinator_router

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

from app.core.config import (
    get_settings,
    validate_configuration,
)
from app.agents.monitoring.api import create_shared_router

# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# Settings
# ---------------------------------------------------------

settings = get_settings()

# ---------------------------------------------------------
# Automatically Open Swagger UI
# ---------------------------------------------------------

def open_swagger():
    """
    Open Swagger UI after the FastAPI server starts.
    Intended for local development.
    """

    time.sleep(1.5)

    webbrowser.open(
        "http://127.0.0.1:8000/docs"
    )


# ---------------------------------------------------------
# Application Lifecycle
# ---------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application startup and shutdown.
    """

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

    # Automatically open Swagger UI locally
    threading.Thread(
        target=open_swagger,
        daemon=True,
    ).start()

    yield

    logger.info(
        "Application shutdown."
    )


# ---------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------

app = FastAPI(
    title="Agentic Project Management System",
    description=(
        "AI-powered multi-agent project management "
        "system with Planning & Scheduling, "
        "Risk & Knowledge Retrieval, "
        "Monitoring & Reporting, and Admin Authentication."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------
# CORS Middleware
# ---------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# Admin Authentication Router
# ---------------------------------------------------------

app.include_router(auth_router)
app.include_router(accounts_router)

app.include_router(
    create_shared_router(
        app,
        planning_api_key=settings.planning_api_key,
    )
)


# ---------------------------------------------------------
# Risk & Knowledge Retrieval Router
# ---------------------------------------------------------

app.include_router(risk_router)


# ---------------------------------------------------------
# Planning & Scheduling Router
# ---------------------------------------------------------

app.include_router(planning_router)
app.include_router(coordinator_router)


# ---------------------------------------------------------
# Root Endpoint
# ---------------------------------------------------------

@app.get("/")
async def root():
    return {
        "message": (
            "Agentic Project Management System API"
        ),
        "status": "running",
        "version": "1.0.0",
        "agents": [
            "Planning & Scheduling",
            "Risk & Knowledge Retrieval",
            "Monitoring & Reporting",
        ],
    }


# ---------------------------------------------------------
# Health Check Endpoint
# ---------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "healthy",
    }

from contextlib import asynccontextmanager
import threading
import time
import webbrowser

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.risks import router as risk_router


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
    """
    Application lifecycle.
    """

    threading.Thread(
        target=open_swagger,
        daemon=True,
    ).start()

    yield


app = FastAPI(
    title="Agentic Project Management System",
    description=(
        "AI-powered multi-agent project management "
        "system with Risk & Knowledge Retrieval."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# Risk & Knowledge Retrieval Agent
# ---------------------------------------------------------

app.include_router(
    risk_router
)


@app.get("/")
async def root():
    return {
        "message": (
            "Agentic Project Management System API"
        ),
        "status": "running",
        "version": "0.1.0",
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
    }
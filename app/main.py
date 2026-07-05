"""FastAPI application entry point.

Phase 2: diary CRUD router + chat router (multi-turn + extraction) + static PWA.

Route order matters here:
  1. include_router(...)  → /api/diary/*, /api/chat/*
  2. @app.get("/health")  → health probe
  3. StaticFiles mount    → catches everything else (serves index.html for /)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import APP_ENV, LOG_LEVEL
from app.database import init_db
from app.routers import chat, diary

# ---- Logging ----------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format='{"ts":"%(asctime)s","level":"%(levelname)s","name":"%(name)s","msg":"%(message)s"}',
)
logger = logging.getLogger("diary.app")

# ---- App --------------------------------------------------------------------
app = FastAPI(
    title="Personal AI Voice Diary",
    description="Self-hosted voice diary: speak → transcribe → store → recall.",
    version=__version__,
    docs_url="/docs" if APP_ENV != "production" else None,
    redoc_url=None,
)

# Permissive CORS for local dev; lock down in Phase 5 (Synology + Tunnel).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Routers ----------------------------------------------------------------
app.include_router(diary.router)
app.include_router(chat.router)


# ---- Routes -----------------------------------------------------------------
@app.get("/health", response_model=None)
async def health() -> JSONResponse:
    """Liveness probe.

    Returns 200 with a small JSON body. Used by the dev box and (Phase 5) by
    Cloudflare / container health checks.
    """
    payload: Dict[str, Any] = {
        "data": {
            "status": "ok",
            "service": "diary",
            "version": __version__,
            "env": APP_ENV,
        },
        "error": None,
    }
    return JSONResponse(content=payload, status_code=200)


# ---- Static frontend --------------------------------------------------------
# Mount AFTER routes so FastAPI's router matches /api/* and /health first;
# the static mount acts as a fallback that serves index.html for /.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")


# ---- Startup ----------------------------------------------------------------
@app.on_event("startup")
async def on_startup() -> None:
    """Initialize the database and log a single structured startup line."""
    init_db()
    logger.info("diary.startup version=%s env=%s", __version__, APP_ENV)

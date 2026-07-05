"""Application configuration loaded from environment variables.

Centralizes env access so the rest of the app doesn't read os.environ directly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env from the project root (one level above this file's parent).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _get_str(key: str, default: Optional[str] = None) -> Optional[str]:
    """Read a string env var, returning default if unset or empty."""
    value = os.getenv(key)
    return value if value else default


def _get_int(key: str, default: int) -> int:
    """Read an int env var, falling back to default on parse error."""
    raw = os.getenv(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ---- LLM (DeepSeek, OpenAI-compatible) -------------------------------------
DEEPSEEK_API_KEY: Optional[str] = _get_str("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL: str = _get_str("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1") or ""
DEEPSEEK_MODEL: str = _get_str("DEEPSEEK_MODEL", "deepseek-chat") or ""


# ---- App --------------------------------------------------------------------
APP_ENV: str = _get_str("APP_ENV", "development") or "development"
APP_HOST: str = _get_str("APP_HOST", "127.0.0.1") or "127.0.0.1"
APP_PORT: int = _get_int("APP_PORT", 8000)
LOG_LEVEL: str = _get_str("LOG_LEVEL", "INFO") or "INFO"

# ---- Data -------------------------------------------------------------------
DATA_DIR: Path = _PROJECT_ROOT / (_get_str("DATA_DIR", "./data") or "./data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

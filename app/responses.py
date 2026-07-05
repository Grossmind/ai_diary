"""Consistent JSON response envelope helpers.

Per project conventions, every API response uses:
    success: {"data": <T>,           "error": null}
    failure: {"data": null,          "error": {"message": "...", "code": "..."}}
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def ok(data: Any, status_code: int = 200) -> JSONResponse:
    """Wrap a successful payload in the standard envelope.

    Args:
        data: Any JSON-serializable value.
        status_code: HTTP status code (default 200; use 201 for resource creation).

    Returns:
        JSONResponse with `{"data": ..., "error": null}` and the given status.
    """
    return JSONResponse(content={"data": data, "error": None}, status_code=status_code)


def err(message: str, code: str, status_code: int = 400) -> JSONResponse:
    """Wrap an error in the standard envelope.

    Args:
        message: Human-readable error message (safe to surface to the user).
        code: Machine-readable error code (e.g. "NOT_FOUND", "DB_ERROR").
        status_code: HTTP status code (default 400).

    Returns:
        JSONResponse with `{"data": null, "error": {...}}` and the given status.
    """
    return JSONResponse(
        content={"data": None, "error": {"message": message, "code": code}},
        status_code=status_code,
    )

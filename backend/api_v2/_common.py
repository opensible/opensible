"""Shared schemas + security scheme for the /api/v2 pilot.

Keep this module small: only cross-cutting concerns that every v2 blueprint
needs. Blueprint-specific schemas live in the blueprint file.
"""
from __future__ import annotations

from marshmallow import Schema, fields


# ---------- Common response building blocks ----------

class ErrorResponse(Schema):
    """Matches the legacy ``{"success": False, "error": "..."}`` shape."""

    success = fields.Boolean(required=True, dump_default=False)
    error = fields.String(required=True)


class OkResponse(Schema):
    """Bare success acknowledgement used by many mutating endpoints."""

    success = fields.Boolean(required=True, dump_default=True)


# ---------- Security scheme ----------

BEARER_AUTH_NAME = "BearerAuth"

BEARER_AUTH_SCHEME = {
    "type": "http",
    "scheme": "bearer",
    "bearerFormat": "JWT",
    "description": (
        "JWT access token issued by /api/auth/login, or an API token from "
        "/api/api-tokens. Prefix with 'Bearer '."
    ),
}


def apply_security(api) -> None:
    """Register the shared BearerAuth scheme on the flask-smorest Api."""
    try:
        api.spec.components.security_scheme(BEARER_AUTH_NAME, BEARER_AUTH_SCHEME)
    except Exception:  # pragma: no cover - already registered
        pass

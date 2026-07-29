"""v2: personal API tokens — Pydantic v2 pilot.

Mirrors /api/api-tokens/* with **Pydantic v2** models driving both runtime
validation and OpenAPI docs. Handlers delegate to storage.api_tokens_store
so business logic stays in one place.

This module is the reference pattern for future hand-written v2 blueprints.
Auto-proxied legacy endpoints continue to use Marshmallow via flask-smorest.
"""
from __future__ import annotations

from typing import Literal, Optional

from flask import request
from flask.views import MethodView
from flask_smorest import Blueprint, abort
from pydantic import BaseModel, ConfigDict, Field

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from storage.api_tokens_store import (
    create_token,
    delete_token,
    list_tokens,
    revoke_token,
    rotate_token,
)

from ._pydantic import pyd_body, pyd_response


blp = Blueprint(
    "api_tokens_v2",
    __name__,
    url_prefix="/api/v2/api-tokens",
    description="Personal API tokens (list / create / rotate / revoke / delete).",
)


# ---------- Pydantic models ----------

class TokenInfo(BaseModel):
    """A single API token record (never contains the plaintext value)."""
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    scope: str
    projectId: Optional[str] = None
    createdAt: Optional[str] = None
    expiresAt: Optional[str] = None
    lastUsedAt: Optional[str] = None
    revoked: bool = False


class ListTokensOut(BaseModel):
    success: bool = True
    tokens: list[TokenInfo]


class CreateTokenIn(BaseModel):
    """Request body for creating a new API token."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    scope: Literal["global", "project"] = "global"
    projectId: Optional[str] = None
    expiresDays: Optional[int] = Field(default=None, ge=0, le=3650)


class CreateTokenOut(BaseModel):
    success: bool = True
    tokenId: str
    token: str = Field(description="Plaintext token. Shown only once.")
    message: str


class OkOut(BaseModel):
    success: bool = True


# ---------- Helpers ----------

def _current_user_id() -> str:
    user = getattr(request, "current_user", {}) or {}
    uid = user.get("user_id")
    if not uid:
        abort(401, message="Unauthorized")
    return uid


def _current_username() -> str:
    return (getattr(request, "current_user", {}) or {}).get("username", "")


# ---------- Routes ----------

@blp.route("")
class TokensCollection(MethodView):
    @require_auth
    @pyd_response(200, ListTokensOut)
    @blp.doc(security=[{"BearerAuth": []}])
    def get(self):
        """List API tokens for the authenticated user."""
        return ListTokensOut(tokens=list_tokens(_current_user_id()))

    @require_auth
    @pyd_body(CreateTokenIn)
    @pyd_response(200, CreateTokenOut)
    @blp.doc(security=[{"BearerAuth": []}])
    def post(self, payload: CreateTokenIn):
        """Create a new API token. The plaintext token is returned only once."""
        uid = _current_user_id()
        project_id = payload.projectId if payload.scope == "project" else None
        token_id, plaintext = create_token(
            user_id=uid,
            username=_current_username(),
            name=payload.name,
            scope=payload.scope,
            project_id=project_id,
            expires_days=payload.expiresDays,
        )
        return CreateTokenOut(
            tokenId=token_id,
            token=plaintext,
            message="IMPORTANT: Save this token! It will not be shown again.",
        )


@blp.route("/<string:token_id>/rotate")
class RotateToken(MethodView):
    @require_auth
    @pyd_response(200, CreateTokenOut)
    @blp.doc(security=[{"BearerAuth": []}])
    def post(self, token_id: str):
        """Rotate a token: revoke the old one and issue a new plaintext token."""
        result = rotate_token(token_id, _current_user_id())
        if not result:
            abort(404, message="Token not found or already revoked")
        new_id, plaintext = result
        return CreateTokenOut(
            tokenId=new_id,
            token=plaintext,
            message="IMPORTANT: Save this token! It will not be shown again.",
        )


@blp.route("/<string:token_id>/revoke")
class RevokeToken(MethodView):
    @require_auth
    @pyd_response(200, OkOut)
    @blp.doc(security=[{"BearerAuth": []}])
    def post(self, token_id: str):
        """Revoke a token so it can no longer authenticate requests."""
        if not revoke_token(token_id, _current_user_id()):
            abort(404, message="Token not found")
        return OkOut()


@blp.route("/<string:token_id>")
class DeleteToken(MethodView):
    @require_auth
    @pyd_response(200, OkOut)
    @blp.doc(security=[{"BearerAuth": []}])
    def delete(self, token_id: str):
        """Permanently delete a token record."""
        if not delete_token(token_id, _current_user_id()):
            abort(404, message="Token not found")
        return OkOut()

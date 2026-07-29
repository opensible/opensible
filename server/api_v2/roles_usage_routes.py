"""v2: roles usage — Pydantic v2 typed response."""
from __future__ import annotations

from typing import Dict, List

from flask.views import MethodView
from flask_smorest import Blueprint
from pydantic import BaseModel, ConfigDict, Field, RootModel

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from api.roles_usage_routes import usage as _v1_usage

from ._pydantic import pyd_response


blp = Blueprint(
    "roles_usage_v2",
    __name__,
    url_prefix="/api/v2/roles",
    description="Which playbooks and templates reference each role.",
)


class RoleUsageEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    playbooks: List[str] = Field(default_factory=list)
    templates: List[str] = Field(default_factory=list)


class RoleUsageMap(RootModel[Dict[str, RoleUsageEntry]]):
    """Map of role name -> usage entry."""


class RolesUsageOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    usage: RoleUsageMap


@blp.route("/usage")
class RolesUsageView(MethodView):
    @require_auth
    @pyd_response(200, RolesUsageOut)
    @blp.doc(security=[{"BearerAuth": []}])
    def get(self):
        """Return a map of role name -> playbooks and templates referencing it."""
        return _v1_usage().get_json()

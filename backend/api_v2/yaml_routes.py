"""v2: YAML parse / validate — Pydantic v2 with strict, nested models."""
from __future__ import annotations

from typing import Any, Literal, Optional

import yaml
from flask import current_app
from flask.views import MethodView
from flask_smorest import Blueprint
from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from ._pydantic import pyd_body, pyd_response


blp = Blueprint(
    "yaml_v2",
    __name__,
    url_prefix="/api/v2/yaml",
    description="YAML parse and validate (Pydantic v2).",
)


# ---------- Models ----------

class YamlParseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    yaml: str = Field(min_length=1, description="YAML text to parse.")


class YamlParseOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    data: Optional[Any] = Field(default=None, description="Parsed YAML tree (any type).")
    error: Optional[str] = None


YamlContext = Literal[
    "generic", "playbook", "inventory", "vars", "group_vars", "host_vars", "role"
]


class YamlValidateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    content: str = Field(description="YAML text to validate.")
    context: YamlContext = "generic"
    filename: Optional[str] = Field(default=None, max_length=500)


class YamlErrorLoc(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str
    line: int = Field(ge=1)
    column: int = Field(ge=1)


class YamlValidateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool = True
    valid: Optional[bool] = None
    error: Optional[YamlErrorLoc] = None


# ---------- Routes ----------

@blp.route("/parse")
class YamlParseView(MethodView):
    @require_auth
    @pyd_body(YamlParseIn)
    @pyd_response(200, YamlParseOut)
    @blp.doc(security=[{"BearerAuth": []}])
    def post(self, payload: YamlParseIn):
        """Parse YAML text and return the resulting object."""
        try:
            parsed = yaml.safe_load(payload.yaml)
        except yaml.YAMLError as e:
            return {"success": False, "error": f"Invalid YAML syntax: {e}"}
        except Exception as e:
            current_app.logger.error(f"Error parsing YAML: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
        if parsed is None:
            return {"success": False, "error": "YAML is empty or invalid"}
        return {"success": True, "data": parsed}


@blp.route("/validate")
class YamlValidateView(MethodView):
    @require_auth
    @pyd_body(YamlValidateIn)
    @pyd_response(200, YamlValidateOut)
    @blp.doc(security=[{"BearerAuth": []}])
    def post(self, payload: YamlValidateIn):
        """Validate YAML text; on failure return line/column of the problem."""
        content = payload.content
        if not content or not content.strip():
            return {
                "success": True,
                "valid": False,
                "error": {"message": "YAML cannot be empty", "line": 1, "column": 1},
            }

        try:
            YAML(typ="safe").load(content)
        except Exception as e:
            mark = getattr(e, "problem_mark", None) or getattr(e, "context_mark", None)
            line = column = None
            if mark is not None:
                try:
                    line = int(mark.line) + 1
                    column = int(mark.column) + 1
                except Exception:
                    line = column = None
            problem = getattr(e, "problem", None)
            msg = str(problem) if problem else str(e)
            current_app.logger.info(
                f"YAML validate failed (context={payload.context}, "
                f"filename={payload.filename}): {type(e).__name__}: {msg}"
            )
            return {
                "success": True,
                "valid": False,
                "error": {"message": msg, "line": line or 1, "column": column or 1},
            }

        return {"success": True, "valid": True}

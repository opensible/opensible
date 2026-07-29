"""Pydantic v2 <-> flask-smorest bridge.

Provides two thin decorators so hand-written v2 blueprints can use Pydantic
models for BOTH runtime validation AND OpenAPI documentation, without
duplicating a Marshmallow schema on the side.

Usage
-----
    from pydantic import BaseModel, Field
    from ._pydantic import pyd_body, pyd_response

    class CreateThingIn(BaseModel):
        name: str = Field(min_length=1, max_length=200)

    class CreateThingOut(BaseModel):
        success: bool = True
        id: str

    @blp.route("")
    class Things(MethodView):
        @pyd_body(CreateThingIn)
        @pyd_response(200, CreateThingOut)
        def post(self, payload: CreateThingIn):
            return CreateThingOut(id="abc")

Design notes
------------
* We do NOT use ``@blp.arguments`` / ``@blp.response`` for Pydantic-backed
  routes — those are Marshmallow-native. Instead we register the request
  body / response schema via ``@blp.doc(...)``, feeding it the JSON schema
  produced by ``model.model_json_schema()``.
* Validation errors return 422 with the structured Pydantic error list —
  matches FastAPI's convention and is machine-parseable by clients.
* Response handlers may return either a Pydantic model instance, a dict, or
  a ``(body, status)`` tuple; ``pyd_response`` normalizes all three.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Type

from flask import jsonify, request
from pydantic import BaseModel, ValidationError

__all__ = ["pyd_body", "pyd_query", "pyd_response"]


def _json_schema(model: Type[BaseModel]) -> dict[str, Any]:
    """Return a JSON schema dict safe for inlining into an OpenAPI spec."""
    # ref_template avoids Pydantic's default "#/$defs/..." refs, which
    # OpenAPI 3 tools don't resolve.
    return model.model_json_schema(ref_template="#/components/schemas/{model}")


def pyd_body(model: Type[BaseModel]) -> Callable:
    """Validate the JSON request body with a Pydantic v2 model.

    On success, the validated model instance is injected as ``payload``.
    On failure, returns HTTP 422 with a structured error list.
    """
    schema = _json_schema(model)

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any):
            raw = request.get_json(silent=True) or {}
            try:
                payload = model.model_validate(raw)
            except ValidationError as exc:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Validation error",
                            "details": exc.errors(include_url=False),
                        }
                    ),
                    422,
                )
            kwargs["payload"] = payload
            return fn(*args, **kwargs)

        # Register request body in the OpenAPI spec via flask-smorest's
        # manual-doc mechanism (same channel used by @blp.doc()).
        doc = getattr(wrapper, "_apidoc", {}) or {}
        manual = doc.setdefault("manual_doc", {})
        manual["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": schema}},
        }
        wrapper._apidoc = doc
        return wrapper

    return decorator


def pyd_query(model: Type[BaseModel]) -> Callable:
    """Validate URL query string with a Pydantic v2 model.

    Injected as ``query`` kwarg. Emits OpenAPI ``parameters`` (``in: query``)
    from the model's JSON schema properties.
    """
    schema = _json_schema(model)
    props = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    parameters = [
        {
            "name": name,
            "in": "query",
            "required": name in required,
            "schema": spec,
            **({"description": spec["description"]} if "description" in spec else {}),
        }
        for name, spec in props.items()
    ]

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any):
            raw = {k: v for k, v in request.args.items()}
            # multi-valued params → list
            for k in list(request.args.keys()):
                values = request.args.getlist(k)
                if len(values) > 1:
                    raw[k] = values
            try:
                query = model.model_validate(raw)
            except ValidationError as exc:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Validation error",
                            "details": exc.errors(include_url=False),
                        }
                    ),
                    422,
                )
            kwargs["query"] = query
            return fn(*args, **kwargs)

        doc = getattr(wrapper, "_apidoc", {}) or {}
        manual = doc.setdefault("manual_doc", {})
        existing = manual.setdefault("parameters", [])
        existing.extend(parameters)
        wrapper._apidoc = doc
        return wrapper

    return decorator




def pyd_response(status: int, model: Type[BaseModel], description: str = "OK") -> Callable:
    """Serialize the view's return value using a Pydantic v2 model.

    The handler may return: a model instance, a dict/list, or ``(body, status)``.
    """
    schema = _json_schema(model)

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any):
            result = fn(*args, **kwargs)
            # Pass through explicit Response tuples untouched.
            if isinstance(result, tuple):
                return result
            if isinstance(result, BaseModel):
                body = result.model_dump(mode="json")
            elif isinstance(result, (dict, list)):
                # Validate outbound dicts too — cheap safety net that catches
                # handler drift from the declared response contract.
                body = model.model_validate(result).model_dump(mode="json")
            else:
                return result
            return jsonify(body), status

        doc = getattr(wrapper, "_apidoc", {}) or {}
        manual = doc.setdefault("manual_doc", {})
        responses = manual.setdefault("responses", {})
        responses[str(status)] = {
            "description": description,
            "content": {"application/json": {"schema": schema}},
        }
        wrapper._apidoc = doc
        return wrapper

    return decorator

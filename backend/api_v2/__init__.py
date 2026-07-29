"""flask-smorest auto-generated OpenAPI docs at /api/v2/*.

Two-tier strategy:

1. **Manual conversions** — hand-written flask-smorest MethodView blueprints
   with rich marshmallow schemas (yaml_v2, roles_usage_v2, api_tokens_v2,
   queue_search_v2). Registered eagerly during ``init_api_v2``.

2. **Auto-proxy** — for every remaining ``/api/*`` route, we scan the app's
   URL map (via ``finalize_api_v2`` called near the end of ``app.py``) and
   mount a mirrored ``/api/v2/*`` proxy on a flask-smorest ``Blueprint``.
   Docs appear automatically; runtime behavior is delegated to the v1 view
   function.

The whole module is **additive**: legacy ``/api/*`` and ``/api/docs`` stay
byte-identical. If ``flask-smorest`` is missing, everything silently no-ops.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from flask import Flask


# Held between init_api_v2() and finalize_api_v2() so app.py doesn't need
# to know about the flask-smorest Api instance.
_state: dict = {"api": None, "finalized": False}


def _register_manual_blueprints(api) -> None:
    """Register hand-converted v2 blueprints with rich schemas."""
    registrations = (
        ("api_v2.yaml_routes", "blp"),
        ("api_v2.roles_usage_routes", "blp"),
        ("api_v2.api_tokens_routes", "blp"),
        ("api_v2.queue_search_routes", "blp"),
    )
    for module_path, attr in registrations:
        try:
            module = __import__(module_path, fromlist=[attr])
            api.register_blueprint(getattr(module, attr))
        except Exception as e:
            logger.error("Failed to register %s: %s", module_path, e, exc_info=True)


def init_api_v2(app: "Flask") -> None:
    """Attach the flask-smorest Api and register manual blueprints.

    Call ``finalize_api_v2(app)`` near the end of ``app.py`` (after every
    ``@app.route`` and blueprint route is defined) to mount auto-proxies for
    the remaining v1 endpoints.
    """
    try:
        from flask_smorest import Api
    except Exception as e:  # pragma: no cover - optional dep
        logger.info("flask-smorest not available, skipping /api/v2: %s", e)
        return

    app.config.setdefault("API_TITLE", "OpenSible API (v2)")
    app.config.setdefault("API_VERSION", "v2")
    app.config.setdefault("OPENAPI_VERSION", "3.0.3")
    app.config.setdefault("OPENAPI_URL_PREFIX", "/api/v2")
    app.config.setdefault("OPENAPI_JSON_PATH", "openapi.json")
    app.config.setdefault("OPENAPI_SWAGGER_UI_PATH", "docs")
    app.config.setdefault(
        "OPENAPI_SWAGGER_UI_URL",
        "https://unpkg.com/swagger-ui-dist@5.9.0/",
    )

    try:
        api = Api(app)
    except Exception as e:
        logger.error("Failed to init flask-smorest Api: %s", e, exc_info=True)
        return

    # Shared BearerAuth security scheme so @blp.doc(security=...) resolves.
    try:
        from ._common import apply_security
        apply_security(api)
    except Exception as e:
        logger.warning("Could not apply v2 security scheme: %s", e)

    _register_manual_blueprints(api)
    _state["api"] = api


def finalize_api_v2(app: "Flask") -> None:
    """Scan the completed URL map and mount /api/v2/* auto-proxies.

    Must be called after every v1 route (blueprints + @app.route handlers)
    has been registered — typically near the bottom of ``app.py``, just
    before ``if __name__ == '__main__':``.
    """
    if _state["finalized"]:
        return
    api = _state["api"]
    if api is None:
        logger.info("finalize_api_v2: no Api instance; init skipped or failed.")
        return
    try:
        from .auto_register import register_auto_proxies
        register_auto_proxies(api, app)
        _state["finalized"] = True
    except Exception as e:
        logger.error("api_v2 finalize failed: %s", e, exc_info=True)

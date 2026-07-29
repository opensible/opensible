"""Auto-register v2 proxy endpoints for every v1 /api/* route.

After the app finishes registering all v1 blueprints and @app.route handlers,
we scan ``app.url_map``, find every ``/api/*`` rule that is not already under
``/api/v2/``, and register a mirrored proxy on a flask-smorest ``Blueprint``
under ``/api/v2/*``. The proxy simply calls the original view function, so
behavior is byte-identical to v1.

The result: every v1 endpoint appears in the auto-generated OpenAPI spec at
``/api/v2/openapi.json`` and in Swagger UI at ``/api/v2/docs``.

Manually converted blueprints (yaml_v2, roles_usage_v2, api_tokens_v2,
queue_search_v2) already own their v2 paths — we skip those here to avoid
"URL rule already exists" collisions.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Callable, Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exclusion rules — endpoints hidden from /api/v2/docs by default.
#
# Goal: keep the public surface small and safe. We hide anything that is:
#   - internal / debug / health-check / metrics
#   - static file serving, proxy passthroughs, SSE streams, websocket upgrades
#   - secret / vault / credential storage (sensitive attack surface)
#   - admin-only maintenance & migration helpers
#
# Match by exact blueprint name, blueprint prefix, or path substring.
# ---------------------------------------------------------------------------
_EXCLUDED_BLUEPRINTS: set[str] = {
    # Sensitive secret / credential surfaces — never expose in public docs.
    "secrets_api",
    "global_secrets_api",
    "vaults_api",
    # Internal-only helpers.
    "admin_api",
    "worker_api",
    "logs_api",
    "misc_api",
    "cost_api",
    "backups_api",
    "cicd_api",
}

# Path substrings (case-insensitive) — hide any /api/* URL containing these.
_EXCLUDED_PATH_PATTERNS: tuple[str, ...] = (
    "/health",
    "/healthz",
    "/readiness",
    "/liveness",
    "/ping",
    "/metrics",
    "/debug",
    "/internal",
    "/_internal",
    "/__",
    "/proxy",
    "/stream",
    "/sse",
    "/ws",
    "/socket",
    "/static",
    "/assets",
    "/download",
    "/upload-chunk",
    "/raw",
    "/secret",
    "/secrets",
    "/vault",
    "/vaults",
    "/credential",
    "/credentials",
    "/token-store",
    "/migrate",
    "/migration",
    "/dev/",
    "/test/",
    "/reload",
    "/shutdown",
    "/gc",
    "/dump",
    "/env",
)

# Endpoint-name suffix patterns to drop (endpoint = "<bp>.<view>").
_EXCLUDED_ENDPOINT_RE = re.compile(
    r"(?i)(health|ping|metrics|debug|internal|reload|shutdown|dump|"
    r"secret|vault|credential|migrate)"
)


def _is_excluded(path: str, endpoint: str, bp_name: str) -> bool:
    if bp_name in _EXCLUDED_BLUEPRINTS:
        return True
    lower = path.lower()
    for pat in _EXCLUDED_PATH_PATTERNS:
        if pat in lower:
            return True
    view_name = endpoint.split(".", 1)[1] if "." in endpoint else endpoint
    if _EXCLUDED_ENDPOINT_RE.search(view_name):
        return True
    return False

if TYPE_CHECKING:
    from flask import Flask


# ---------------------------------------------------------------------------
# Paths already covered by manually converted v2 blueprints.
# ---------------------------------------------------------------------------
_MANUAL_V2_PATHS: set[str] = {
    # yaml_v2
    "/api/yaml/parse",
    "/api/yaml/validate",
    # roles_usage_v2
    "/api/roles/usage",
    # api_tokens_v2
    "/api/api-tokens",
    "/api/api-tokens/<token_id>",
    "/api/api-tokens/<token_id>/rotate",
    "/api/api-tokens/<token_id>/revoke",
    # queue_search_v2
    "/api/queue",
    "/api/search",
    "/api/capabilities",
    "/api/projects/<project_id>/queue-stats",
}


# ---------------------------------------------------------------------------
# Nicer tag names, grouped by v1 blueprint / module.
# Anything not listed falls back to the blueprint name.
# ---------------------------------------------------------------------------
_TAG_LABELS: dict[str, str] = {
    "admin_api": "Admin",
    "ansible_cfg_api": "Ansible Config",
    "ansible_run_api": "Ansible Run",
    "api_tokens_docs_api": "Docs & Docs Access",
    "auth_api": "Auth",
    "backups_api": "Backups",
    "cicd_api": "CI/CD",
    "cloud_api": "Cloud",
    "cost_api": "Cost",
    "data_api": "Data",
    "executions_api": "Executions",
    "global_secrets_api": "Global Secrets",
    "group_host_vars_api": "Group / Host Vars",
    "host_checks_api": "Host Checks",
    "inventory_files_api": "Inventory Files",
    "inventory_groups_hosts_api": "Inventory Groups / Hosts",
    "inventory_api": "Inventory",
    "logs_api": "Logs",
    "misc_api": "Misc",
    "playbook_api": "Playbook",
    "playbooks_api": "Playbooks",
    "projects_api": "Projects",
    "queue_search_api": "Queue & Search",
    "roles_api": "Roles",
    "roles_usage_api": "Roles Usage",
    "secrets_api": "Secrets",
    "settings_api": "Settings",
    "sources_api": "Sources",
    "templates_api": "Templates",
    "users_api": "Users",
    "vaults_api": "Vaults",
    "worker_api": "Worker",
    "yaml_api": "YAML",
}


def _iter_v1_rules(app) -> Iterable[tuple[str, list[str], Callable, str]]:
    """Yield (path, methods, view_fn, endpoint) for every /api/* v1 rule
    that survives the exclusion list."""
    hidden = 0
    for rule in app.url_map.iter_rules():
        path = str(rule.rule)
        if not path.startswith("/api/"):
            continue
        if path.startswith("/api/v2/"):
            continue
        if path in _MANUAL_V2_PATHS:
            continue
        endpoint = rule.endpoint
        bp_name = endpoint.split(".", 1)[0] if "." in endpoint else "app"
        if _is_excluded(path, endpoint, bp_name):
            hidden += 1
            continue
        view_fn = app.view_functions.get(endpoint)
        if view_fn is None:
            continue
        methods = sorted(
            m for m in (rule.methods or set()) if m not in {"HEAD", "OPTIONS"}
        )
        if not methods:
            continue
        yield path, methods, view_fn, endpoint
    if hidden:
        logger.info("api_v2 auto-register: hid %d internal/sensitive routes.", hidden)


def _make_proxy(view_fn: Callable, endpoint_name: str) -> Callable:
    """Wrap a v1 view function with a uniquely named passthrough."""

    def proxy(**kwargs):  # noqa: ANN001, ANN002
        return view_fn(**kwargs)

    proxy.__name__ = endpoint_name
    proxy.__doc__ = (view_fn.__doc__ or "").strip() or f"Proxy of v1 {view_fn.__name__}."
    return proxy


def register_auto_proxies(api, app) -> None:
    """Register one flask-smorest Blueprint per v1 blueprint tag."""
    try:
        from flask_smorest import Blueprint
    except Exception as e:  # pragma: no cover
        logger.warning("flask-smorest unavailable; auto-proxies skipped: %s", e)
        return

    # Group v1 rules by originating blueprint name (endpoint prefix).
    by_bp: dict[str, list[tuple[str, list[str], Callable, str]]] = defaultdict(list)
    for path, methods, view_fn, endpoint in _iter_v1_rules(app):
        bp_name = endpoint.split(".", 1)[0] if "." in endpoint else "app"
        by_bp[bp_name].append((path, methods, view_fn, endpoint))

    if not by_bp:
        logger.info("api_v2 auto-register: no v1 rules found to proxy.")
        return

    total = 0
    for bp_name, rules in sorted(by_bp.items()):
        tag = _TAG_LABELS.get(bp_name, bp_name.replace("_", " ").title())
        # Blueprint name becomes the OpenAPI tag; keep it stable & short.
        blp = Blueprint(
            tag,
            __name__,
            url_prefix="/api/v2",
            description=f"{tag} (auto-documented proxy of legacy /api/*).",
        )
        for path, methods, view_fn, endpoint in rules:
            v2_subpath = path[len("/api"):]  # e.g. "/inventory/list"
            proxy_name = f"v2__{endpoint.replace('.', '__')}"
            try:
                blp.add_url_rule(
                    v2_subpath,
                    endpoint=proxy_name,
                    view_func=_make_proxy(view_fn, proxy_name),
                    methods=methods,
                )
                total += 1
            except Exception as e:
                logger.warning(
                    "auto-proxy failed for %s %s -> %s: %s",
                    methods, path, v2_subpath, e,
                )
        try:
            api.register_blueprint(blp)
        except Exception as e:
            logger.error("Failed to register auto v2 blueprint %r: %s", tag, e, exc_info=True)

    logger.info("api_v2 auto-register: mounted %d proxied endpoints across %d tags.", total, len(by_bp))

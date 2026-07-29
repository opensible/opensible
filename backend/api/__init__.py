"""API blueprints package.

Phase 1 (scaffold): empty blueprints, no routes moved yet.
Subsequent phases will move route handlers out of app.py into these
blueprint modules. Each blueprint module exposes a `bp` attribute.

Usage from app.py:
    from api import register_blueprints
    register_blueprints(app)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask


def register_blueprints(app: "Flask") -> None:
    """Register all API blueprints onto the given Flask app.

    Imports are lazy/local so a broken blueprint module can't take down
    the whole app at import time — the offending module logs and skips.
    """
    from importlib import import_module

    modules = [
        "api.auth_routes",
        "api.users_routes",
        "api.inventory_routes",
        "api.playbook_routes",
        "api.ansible_cfg_routes",
        "api.cloud_routes",
        "api.worker_routes",
        "api.secrets_routes",
        "api.settings_routes",
        "api.logs_routes",
        "api.cost_routes",
        "api.templates_routes",
        "api.roles_usage_routes",
        "api.cicd_routes",
        "api.misc_routes",
        "api.backups_routes",
        "api.admin_routes",
        "api.executions_routes",
        "api.global_secrets_routes",
        "api.roles_routes",
        "api.playbooks_routes",
        "api.vaults_routes",
        "api.sources_routes",
        "api.group_host_vars_routes",
        "api.api_tokens_docs_routes",
        "api.inventory_groups_hosts_routes",
        "api.inventory_files_routes",
        "api.yaml_routes",
        "api.data_routes",
        "api.host_checks_routes",
        "api.ansible_run_routes",
        "api.queue_search_routes",
        "api.projects_routes",
        "api.audit_log_routes",
    ]
    for mod_name in modules:
        try:
            mod = import_module(mod_name)
            bp = getattr(mod, "bp", None)
            if bp is not None:
                app.register_blueprint(bp)
        except Exception as e:  # pragma: no cover - defensive
            app.logger.error(f"Failed to register blueprint {mod_name}: {e}", exc_info=True)

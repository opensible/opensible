#!/usr/bin/env python3
"""
- Ansible 
"""
import os
import yaml
import re
import subprocess
import threading
import uuid
import time
import logging
from pathlib import Path
from io import StringIO, BytesIO
import fcntl
from flask import Flask, request, jsonify, Response, stream_with_context, send_file
import zipfile
import tarfile
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional, Tuple

# Import GitSourceManager
try:
    from services.git_source_manager import GitSourceManager, GitSourceError
except ImportError:
    from services.git_source_manager import GitSourceManager, GitSourceError

# Import Playbook modules
try:
    from storage.playbook_storage import PlaybookStorage
    from playbooks.parser import PlaybookParser, PlaybookParseError
    from playbooks.generator import PlaybookGenerator
    from playbooks.validator import PlaybookValidator
except ImportError:
    from storage.playbook_storage import PlaybookStorage
    from playbooks.parser import PlaybookParser, PlaybookParseError
    from playbooks.generator import PlaybookGenerator
    from playbooks.validator import PlaybookValidator

try:
    from utils.vault_utils import (
        is_ansible_vault_encrypted,
        parse_vault_id_from_header,
        ansible_vault_decrypt,
        ansible_vault_encrypt
    )
except ImportError:
    from utils.vault_utils import (
        is_ansible_vault_encrypted,
        parse_vault_id_from_header,
        ansible_vault_decrypt,
        ansible_vault_encrypt
    )

app = Flask(__name__)

# Phase 1 of app.py blueprint refactor: register modular API blueprints.
# Empty stubs today; subsequent phases will move route handlers from this
# file into backend/api/*_routes.py. See .lovable/plan.md.
try:
    import app_context as _app_context
    _app_context.set_app(app)
    from api import register_blueprints as _register_api_blueprints
    _register_api_blueprints(app)
except Exception as _bp_err:
    import logging as _logging
    _logging.getLogger(__name__).error(
        f"Blueprint registration failed (continuing with legacy routes): {_bp_err}",
        exc_info=True,
    )

# Pilot: flask-smorest auto-generated OpenAPI docs at /api/v2/*.
# Fully additive — never touches legacy /api/* routes or /api/docs.
# Silently no-ops if flask-smorest isn't installed.
try:
    from api_v2 import init_api_v2 as _init_api_v2
    _init_api_v2(app)
except Exception as _v2_err:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        f"/api/v2 pilot not mounted (continuing without it): {_v2_err}"
    )

# CORS 
# NOTE: when supports_credentials=True, browsers reject Access-Control-Allow-Origin: *.
# By default, allow the local static frontend on port 8080 to call the API on port 5000.
_default_cors_origins = 'http://localhost:8080,http://127.0.0.1:8080,http://0.0.0.0:8080'
_cors_origins_env = os.environ.get('CORS_ALLOWED_ORIGINS', _default_cors_origins).strip()
_cors_allowed_origins = [o.strip() for o in _cors_origins_env.split(',') if o.strip()]
try:
    from flask_cors import CORS
    CORS(
        app,
        resources={r"/api/*": {"origins": _cors_allowed_origins}},
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        vary_header=True,
    )
except ImportError:
    # flask-cors , 
    import warnings
    warnings.warn("flask-cors not installed. CORS support disabled. Install with: pip install flask-cors")


@app.before_request
def handle_api_options_preflight():
    """Return a clean response for browser CORS preflight requests."""
    if request.method == 'OPTIONS' and request.path.startswith('/api/'):
        response = app.make_response(('', 204))
        origin = request.headers.get('Origin')
        if origin and origin in _cors_allowed_origins:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Headers'] = request.headers.get(
            'Access-Control-Request-Headers',
            'Content-Type, Authorization, X-Requested-With, Accept',
        )
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        return response


@app.after_request
def add_api_cors_headers(response):
    """Ensure CORS headers exist even if flask-cors is missing or preflight exits early."""
    if not request.path.startswith('/api/'):
        return response

    origin = request.headers.get('Origin')
    if origin and origin in _cors_allowed_origins:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Vary'] = 'Origin'

    requested_headers = request.headers.get('Access-Control-Request-Headers')
    response.headers['Access-Control-Allow-Headers'] = requested_headers or 'Content-Type, Authorization, X-Requested-With, Accept'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
    return response

# ( Docker: app /app → parent=/app; backend/ → parent.parent=)
_app_dir = Path(__file__).resolve().parent
BASE_DIR = _app_dir.parent if _app_dir.name == 'backend' else _app_dir

# ( Docker DATA_DIR=/app/data)
DATA_DIR = Path(os.environ.get('DATA_DIR', str(BASE_DIR / 'data')))
DATA_DIR.mkdir(parents=True, exist_ok=True)

LOG_DIR = DATA_DIR / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / 'backend.log'

# : [TIMESTAMP] LEVEL: message
UNIFIED_LOG_FORMAT = '[%(asctime)s] %(levelname)s: %(message)s'

# execution_settings.json
# ( EXECUTION_SETTINGS_FILE, )
def get_logging_settings():
    """Load logging settings from the config DB (falls back to env)."""
    try:
        from storage import config_db as _cfg
        _cfg.migrate_from_json_if_needed(DATA_DIR)
        settings = _cfg.get_setting(DATA_DIR, 'execution_settings', None)
        if isinstance(settings, dict):
            log_level = str(settings.get('log_level', 'INFO')).upper()
            max_log_size_mb = int(settings.get('max_log_size_mb', 10))
            return log_level, max_log_size_mb
    except Exception:
        pass

    # Fallback to environment variables
    log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
    max_log_size_mb = int(os.environ.get('MAX_LOG_SIZE_MB', '10'))
    return log_level, max_log_size_mb


LOG_LEVEL_ENV, MAX_LOG_SIZE_MB = get_logging_settings()
LOG_LEVEL_MAP = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL
}
LOG_LEVEL = LOG_LEVEL_MAP.get(LOG_LEVEL_ENV, logging.DEBUG)

# handler 
from logging.handlers import RotatingFileHandler


def _reset_current_log_file(path: Path):
    """Start the live Server Logs view at the current container start.

    Docker already keeps historical container stdout/stderr, while the frontend
    Server Logs page reads /app/data/logs/*.log. Truncating only the active file
    prevents old worker/backend entries from previous containers appearing as if
    they came from the current run.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    except Exception:
        pass


_reset_current_log_file(LOG_FILE)
file_handler = RotatingFileHandler(
    LOG_FILE,
    encoding='utf-8',
    maxBytes=MAX_LOG_SIZE_MB * 1024 * 1024,  # MB bytes
    backupCount=5  # 5 
)
file_handler.setLevel(LOG_LEVEL)
file_formatter = logging.Formatter(
    UNIFIED_LOG_FORMAT,
    datefmt='%Y-%m-%d %H:%M:%S'
)
file_handler.setFormatter(file_formatter)

# handler
console_handler = logging.StreamHandler()
# INFO , LOG_LEVEL
console_level = LOG_LEVEL if LOG_LEVEL >= logging.INFO else logging.INFO
console_handler.setLevel(console_level)
console_formatter = logging.Formatter(
    UNIFIED_LOG_FORMAT,
    datefmt='%Y-%m-%d %H:%M:%S'
)
console_handler.setFormatter(console_formatter)

# handlers logger
# Flask's app.logger has its own default stderr handler. If we also attach our
# file+console handlers and keep propagation enabled, every app.logger message is
# printed multiple times in `docker logs`. Use the root logger as the single
# console/file pipeline and let app.logger propagate to it.
app.logger.handlers.clear()
app.logger.setLevel(LOG_LEVEL)
app.logger.propagate = True

# root logger (source_sync_service, git_source_manager, routes, app.logger)
root_logger = logging.getLogger()
root_logger.setLevel(LOG_LEVEL)
root_logger.handlers.clear()
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

# Werkzeug ( handler)
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# logger frontend 
FRONTEND_LOG_FILE = LOG_DIR / 'frontend.log'
_reset_current_log_file(FRONTEND_LOG_FILE)
frontend_file_handler = RotatingFileHandler(
    FRONTEND_LOG_FILE,
    encoding='utf-8',
    maxBytes=MAX_LOG_SIZE_MB * 1024 * 1024,  # MB bytes
    backupCount=5  # 5 
)
frontend_file_handler.setLevel(LOG_LEVEL)
frontend_file_handler.setFormatter(file_formatter)

frontend_logger = logging.getLogger('frontend')
frontend_logger.setLevel(LOG_LEVEL)
frontend_logger.addHandler(frontend_file_handler)
frontend_logger.propagate = False  # root logger


# Phase 1 refactor: these helpers moved to backend/utils/.
# Re-exported here so every existing `app.safe_log_error(...)` / etc.
# call site keeps working unchanged.
from utils.json_io import (
    safe_log_error,
    safe_write_json_with_lock,
    safe_read_json_with_lock,
)
from utils.ssh_keys import (
    validate_ssh_private_key,
    normalize_pem_key_for_file,
)


# ( Docker: app /app → parent=/app; backend/ → parent.parent=)
_app_dir = Path(__file__).resolve().parent
BASE_DIR = _app_dir.parent if _app_dir.name == 'backend' else _app_dir

# ( Docker DATA_DIR=/app/data)
DATA_DIR = Path(os.environ.get('DATA_DIR', str(BASE_DIR / 'data')))
DATA_DIR.mkdir(parents=True, exist_ok=True)

EXECUTION_SETTINGS_FILE = DATA_DIR / 'execution_settings.json'
ROLES_CONFIG_FILE = DATA_DIR / 'roles-config.json'
PROJECTS_CONFIG_FILE = DATA_DIR / 'projects.json'
BACKUP_SETTINGS_FILE = DATA_DIR / 'backup_settings.json'

# ==================== Host status cache ====================
# Phase 2 refactor: extracted to backend/utils/host_status.py. Re-exported so
# every existing caller (routes, worker callbacks) keeps working unchanged.
from utils.host_status import (  # noqa: F401
    HOST_STATUS_TTL_DEFAULT,
    HOST_STATUS_CACHE,
    get_host_status_ttl_seconds,
    set_host_check_status,
    get_host_check_status,
)

PROJECTS_DIR = DATA_DIR / 'projects'  # data/projects/
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# , , , data/
GIT_CACHE_DIR = DATA_DIR / 'cache' / 'git'
GIT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

TEMP_DIR = DATA_DIR / 'temp'
TEMP_DIR.mkdir(parents=True, exist_ok=True)

BACKUPS_DIR = DATA_DIR / 'backups'
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
# (tar.gz) Backup Now / Restore
BACKUP_ARCHIVES_DIR = BACKUPS_DIR / 'archives'
BACKUP_ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)

# Phase 2 refactor: publish filesystem roots to app_context so extracted
# helpers under backend/utils/ can read them without importing app.
import app_context as _app_context
_app_context.set_data_dir(DATA_DIR)
_app_context.set_projects_dir(PROJECTS_DIR)
_app_context.set_backups_dir(BACKUPS_DIR)

GLOBAL_SECRETS_DIR = DATA_DIR / 'global' / 'secrets'
GLOBAL_SECRETS_DIR.mkdir(parents=True, exist_ok=True)

EXECUTIONS_LEGACY_DIR = DATA_DIR / 'executions' / 'legacy'
EXECUTIONS_LEGACY_DIR.mkdir(parents=True, exist_ok=True)

# Initialize GitSourceManager (pass DATA_DIR for global secrets)
git_source_manager = GitSourceManager(GIT_CACHE_DIR, PROJECTS_DIR, DATA_DIR)

# Initialize SourceSyncService
try:
    from services.source_sync_service import SourceSyncService
except ImportError:
    from services.source_sync_service import SourceSyncService
source_sync_service = SourceSyncService(PROJECTS_DIR, git_source_manager)

# Initialize Playbook modules
playbook_storage = PlaybookStorage(PROJECTS_DIR)
playbook_parser = PlaybookParser()
playbook_generator = PlaybookGenerator()

# Initialize AutosyncScheduler (will be initialized after load_project_config is defined)
autosync_scheduler = None

# Feature flag for Project Sources
PROJECT_SOURCES_ENABLED = os.environ.get('PROJECT_SOURCES_ENABLED', 'true').lower() == 'true'

# ruamel.yaml 
yaml_loader = YAML()
yaml_loader.preserve_quotes = True
yaml_loader.width = 4096

# Initialize Global Secrets Manager
try:
    from services.global_secrets_manager import GlobalSecretsManager, GlobalSecretError
    global_secrets_manager = GlobalSecretsManager(DATA_DIR)
except ImportError:
    # If module not available, set to None (endpoints will handle gracefully)
    global_secrets_manager = None
    GlobalSecretError = Exception

# ============================================================================
# AUTHENTICATION & RBAC INITIALIZATION
# ============================================================================

# Import authentication modules
try:
    from .auth import generate_token, add_token_to_blacklist, verify_token
    from auth.middleware import require_auth, require_optional_auth, set_data_dir, set_access_control_service
    from services.user_service import UserService
    from services.role_service import RoleService, PermissionService
    from services.permission_service import AccessControlService
    from auth.validators import validate_username, validate_password, validate_email, validate_role_name
    from auth.seed import seed_default_user, seed_default_roles
except ImportError:
    from auth import generate_token, add_token_to_blacklist, verify_token
    from auth.middleware import require_auth, require_optional_auth, set_data_dir, set_access_control_service
    from services.user_service import UserService
    from services.role_service import RoleService, PermissionService
    from services.permission_service import AccessControlService
    from auth.validators import validate_username, validate_password, validate_email, validate_role_name
    from auth.seed import seed_default_user, seed_default_roles

# Set DATA_DIR in auth_middleware
set_data_dir(DATA_DIR)

# Docs IP allowlist (/api/docs, /api/openapi.json)
import utils.docs_access as _docs_access
_docs_access.set_data_dir(DATA_DIR)

# Initialize authentication services
user_service = UserService(DATA_DIR)
role_service = RoleService(DATA_DIR)
permission_service = PermissionService(DATA_DIR)
access_control_service = AccessControlService(DATA_DIR)

# Set access_control_service in auth_middleware for @require_permission decorator
set_access_control_service(access_control_service)

# Register Cloud Provisioning blueprint (OpenTofu/Terraform stacks)
try:
    try:
        from services.cloud_provisioning import register as _register_cloud
    except ImportError:
        from services.cloud_provisioning import register as _register_cloud
    _register_cloud(app)
except Exception as _e:  # pragma: no cover
    app.logger.error(f"[cloud] Failed to register Cloud Provisioning blueprint: {_e}")


# ============================================================================
# RBAC helpers for Global Secrets
# ============================================================================

def _current_user_id():
    """Return the user_id of the request principal, or None."""
    cu = getattr(request, 'current_user', None) or {}
    return cu.get('user_id')


def _is_internal_request():
    cu = getattr(request, 'current_user', None) or {}
    return cu.get('user_id') == '__internal__'


def can_read_global_secrets():
    """True iff the current user holds the `secrets.read` permission."""
    if _is_internal_request():
        return True
    user_id = _current_user_id()
    if not user_id:
        return False
    try:
        from auth.middleware import get_access_control_service
        acs = get_access_control_service()
        return bool(acs.has_permission(user_id, 'secrets.read'))
    except Exception as e:
        app.logger.error(f"can_read_global_secrets: permission check failed: {e}")
        return False


def can_write_global_secrets():
    """True iff the current user holds any of the secrets write permissions."""
    if _is_internal_request():
        return True
    user_id = _current_user_id()
    if not user_id:
        return False
    try:
        from auth.middleware import get_access_control_service
        acs = get_access_control_service()
        return any(
            acs.has_permission(user_id, perm)
            for perm in ('secrets.create', 'secrets.update', 'secrets.delete')
        )
    except Exception as e:
        app.logger.error(f"can_write_global_secrets: permission check failed: {e}")
        return False


# Phase 1 refactor: YAML helpers moved to backend/utils/yaml_io.py.
# Re-exported here so every existing `app.validate_yaml_content(...)` /
# `app.parse_yaml_with_comments(...)` / `app.get_variable_descriptions()`
# call site keeps working unchanged.
from utils.yaml_io import (
    validate_yaml_content,
    parse_yaml_with_comments,
    get_variable_descriptions,
)


# ============================================================================
# PROJECT MANAGEMENT
# ============================================================================

def load_projects():
    """Load projects list from the config DB (SQLite + WAL)."""
    try:
        from storage import config_db as _cfg
        _cfg.migrate_from_json_if_needed(DATA_DIR)
        return _cfg.list_projects(DATA_DIR)
    except Exception as e:
        app.logger.error(f"Error loading projects: {e}", exc_info=True)
        return []


def save_projects(projects):
    """Persist projects list to the config DB (SQLite + WAL)."""
    try:
        from storage import config_db as _cfg
        return _cfg.replace_all_projects(DATA_DIR, projects)
    except Exception as e:
        app.logger.error(f"Error saving projects: {e}", exc_info=True)
        return False



def get_default_project_id():
    """ ID """
    projects = load_projects()
    default_project = next((p for p in projects if p.get('name') == 'Default Project'), None)
    if default_project:
        return default_project['id']
    return None


def _initialize_projects():
    """Initialize project directories on startup."""
    try:
        projects = load_projects()
        
        for project in projects:
            project_id = project.get('id')
            if not project_id:
                continue
            
            project_dir = get_project_dir(project_id)
            project_dir.mkdir(exist_ok=True)
            
            # ( )
            # repo/ - Git-synced workspace
            repo_dir = project_dir / 'repo'
            repo_dir.mkdir(exist_ok=True)
            (repo_dir / 'roles').mkdir(exist_ok=True)
            (repo_dir / 'playbooks').mkdir(exist_ok=True)
            (repo_dir / 'inventories').mkdir(exist_ok=True)
            (repo_dir / 'group_vars').mkdir(exist_ok=True)  # optional shared
            (repo_dir / 'host_vars').mkdir(exist_ok=True)    # optional shared
            (repo_dir / 'scripts').mkdir(exist_ok=True)
            
            # prod - Git
            # prod_env_dir = repo_dir / 'inventories' / 'prod'
            # prod_env_dir.mkdir(parents=True, exist_ok=True)
            # (prod_env_dir / 'group_vars').mkdir(exist_ok=True)
            # (prod_env_dir / 'host_vars').mkdir(exist_ok=True)
            
            # ui/ - UI definitions
            ui_dir = project_dir / 'ui'
            ui_dir.mkdir(exist_ok=True)
            (ui_dir / 'playbooks').mkdir(exist_ok=True)
            
            # runtime/ - generated / ephemeral
            runtime_dir = project_dir / 'runtime'
            runtime_dir.mkdir(exist_ok=True)
            (runtime_dir / 'generated_playbooks').mkdir(exist_ok=True)
            (runtime_dir / 'inventory_snapshots').mkdir(exist_ok=True)
            (runtime_dir / 'artifacts').mkdir(exist_ok=True)
            
            # history/ - immutable execution history
            history_dir = project_dir / 'history'
            history_dir.mkdir(exist_ok=True)
            (history_dir / 'executions').mkdir(exist_ok=True)
            (history_dir / 'logs').mkdir(exist_ok=True)
            
            # secrets/ - credentials store
            secrets_dir = project_dir / 'secrets'
            secrets_dir.mkdir(exist_ok=True)
            (secrets_dir / 'ssh_keys').mkdir(exist_ok=True)
            (secrets_dir / 'vault').mkdir(exist_ok=True)
            (secrets_dir / 'vault_keys').mkdir(exist_ok=True)
            (secrets_dir / 'git_auth').mkdir(exist_ok=True)
        
        # ansible-config 
        _ensure_ansible_config_directory_for_all_projects()
        
        projects_count = len([p for p in projects if not p.get('isArchived', False)])
        app.logger.info(f"Projects initialized. Total active projects: {projects_count}")
    except Exception as e:
        app.logger.error(f"Error initializing projects: {e}", exc_info=True)


def _ensure_ansible_config_directory_for_all_projects():
    """ ansible-config """
    try:
        projects = load_projects()
        created_count = 0
        
        for project in projects:
            project_id = project.get('id')
            if not project_id:
                continue
            
            project_dir = get_project_dir(project_id)
            ansible_config_dir = project_dir / 'ansible-config'
            
            if not ansible_config_dir.exists():
                ansible_config_dir.mkdir(parents=True, exist_ok=True)
                created_count += 1
                app.logger.debug(f"Created ansible-config directory for project {project_id} ({project.get('name', 'Unknown')})")
        
        if created_count > 0:
            app.logger.debug(f"Created ansible-config directory for {created_count} project(s)")
    except Exception as e:
        app.logger.error(f"Error ensuring ansible-config directory for all projects: {e}", exc_info=True)


# Phase 2 refactor: project-path helpers moved to backend/utils/project_paths.py.
# Re-exported here so every existing caller keeps working unchanged.
from utils.paths import safe_child_path as _safe_child_path  # noqa: F401
from utils.project_paths import (  # noqa: F401
    get_project_dir,
    resolve_ansible_config_path,
)


def create_minimal_ansible_config(config_path):
    """ ansible config , """
    try:
        # , 
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        minimal_config = """[defaults]
executable = /bin/bash
allow_world_readable_tmpfiles = true
stdout_callback = default
callbacks_enabled = timer
roles_path = roles
inventory = inventory.yml
host_key_checking = false
forks = 50
deprecation_warnings = False
gather_facts = False
gathering = explicit
interpreter_python = /usr/bin/python3

[ssh_connection]
pipelining = True
ssh_args = -C -o ControlMaster=auto -o ControlPersist=30m
        """
        
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(minimal_config)
        
        app.logger.debug(f"Created minimal ansible config at {config_path}")
        return True
    except Exception as e:
        app.logger.error(f"Error creating minimal ansible config at {config_path}: {e}", exc_info=True)
        return False


def get_repo_layout(project_id: str) -> dict:
    """
    Get repo layout configuration with defaults.
    
    Returns layout paths for Ansible entities. If repoLayout is not configured,
    returns default values.
    
    Args:
        project_id: Project ID
    
    Returns:
        dict with keys: 'playbooks', 'roles', 'inventories', 'vars'
        Default values: 'playbooks', 'roles', 'inventories', 'vars'
    """
    config = load_project_config(project_id)
    layout = config.get('repoLayout', {})
    
    return {
        'playbooks': layout.get('playbooks', 'playbooks'),
        'roles': layout.get('roles', 'roles'),
        'inventories': layout.get('inventories', 'inventories')
    }


# Phase 1 refactor: moved to backend/utils/paths.py.
from utils.paths import validate_repo_layout_path  # noqa: F401


from utils.project_paths import (  # noqa: F401
    resolve_repo_path,
    get_project_inventories_dir,
)


def ensure_inventory_dirs(inventory_file_path: Path):
    """
 group_vars host_vars inventory , .
    
    Args:
 inventory_file_path: Path inventory 
    """
    try:
        inventory_dir = inventory_file_path.parent
        group_vars_dir = inventory_dir / 'group_vars'
        host_vars_dir = inventory_dir / 'host_vars'
        
        if not group_vars_dir.exists():
            group_vars_dir.mkdir(parents=True, exist_ok=True)
            app.logger.info(f"Created group_vars directory: {group_vars_dir}")
        
        if not host_vars_dir.exists():
            host_vars_dir.mkdir(parents=True, exist_ok=True)
            app.logger.info(f"Created host_vars directory: {host_vars_dir}")
    except Exception as e:
        app.logger.warning(f"Failed to create group_vars/host_vars directories for {inventory_file_path}: {e}")


def ensure_all_inventory_dirs(project_id: str):
    """
 group_vars host_vars inventory inventories.
 inventories: inventory.yaml, inventory.yml, hosts.yaml, hosts.yml, hosts ( ).
    """
    try:
        inventories_dir = get_project_inventories_dir(project_id)
        inventory_names = ['inventory.yaml', 'inventory.yml', 'hosts.yaml', 'hosts.yml', 'hosts', 'hosts.ini']
        inventory_files = []
        if inventories_dir.exists():
            for name in inventory_names:
                p = inventories_dir / name
                if p.exists() and p.is_file():
                    inventory_files.append(p)
            for inv_file in inventories_dir.rglob('*'):
                if inv_file.is_file() and inv_file.name in inventory_names:
                    rel = inv_file.relative_to(inventories_dir)
                    if 'group_vars' not in rel.parts and 'host_vars' not in rel.parts:
                        if inv_file not in inventory_files:
                            inventory_files.append(inv_file)
        for inventory_file in inventory_files:
            ensure_inventory_dirs(inventory_file)
        if inventory_files:
            app.logger.info(f"Checked {len(inventory_files)} inventory files and ensured group_vars/host_vars directories")
    except Exception as e:
        app.logger.error(f"Error ensuring inventory directories: {e}", exc_info=True)


def ensure_group_vars_host_vars_from_inventory(project_id: str, inventory_files_full: list):
    """ group_vars host_vars inventory inventory-.
    (get_all_data), .
       """
    if not inventory_files_full:
        return
    try:
        ensure_all_inventory_dirs(project_id)
        project_dir = get_project_dir(project_id)
        repo_dir = project_dir / 'repo'
        inventories_dir = get_project_inventories_dir(project_id)
        for full_path_str in inventory_files_full:
            full_path = Path(full_path_str)
            if not full_path.exists():
                continue
            try:
                if _is_ini_inventory_file(full_path):
                    inventory_data = _parse_ini_inventory(full_path) or {}
                else:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        inventory_data = yaml_loader.load(f) or {}
                inventory_dir = full_path.parent
                group_vars_dir = inventory_dir / 'group_vars'
                group_vars_dir.mkdir(parents=True, exist_ok=True)
                all_children = inventory_data.get('all', {}).get('children', {})
                groups_in_this_inventory = set()
                for group_name in all_children.keys():
                    if isinstance(group_name, str) and not group_name.startswith('#'):
                        groups_in_this_inventory.add(group_name)
                if inventory_data.get('all', {}).get('vars'):
                    groups_in_this_inventory.add('all')
                groups_in_this_inventory.add('all')
                for group_name in groups_in_this_inventory:
                    group_file = group_vars_dir / f"{group_name}.yml"
                    if not group_file.exists():
                        with open(group_file, 'w', encoding='utf-8') as f:
                            yaml_loader.dump({}, f)
                        app.logger.info(f"Auto-created group_vars for {group_name} at {group_file}")
            except Exception as e:
                app.logger.debug(f"ensure_group_vars_host_vars skip {full_path}: {e}")
        hosts_from_inv = get_inventory_hosts(inventory_files_full)
        for h in hosts_from_inv:
            host_name = h.get('name')
            if not host_name:
                continue
            inventory_file = h.get('inventory_file', '')
            host_vars_dir = get_host_vars_dir_for_inventory(project_id, inventory_file)
            host_vars_dir.mkdir(parents=True, exist_ok=True)
            host_file = host_vars_dir / f"{host_name}.yml"
            if not host_file.exists():
                with open(host_file, 'w', encoding='utf-8') as f:
                    yaml_loader.dump({}, f)
                app.logger.info(f"Auto-created host_vars for {host_name} at {host_file}")
    except Exception as e:
        app.logger.warning(f"ensure_group_vars_host_vars_from_inventory: {e}", exc_info=True)


from utils.project_paths import (  # noqa: F401
    get_project_roles_dir,
    get_project_playbooks_dir,
    get_project_inventory_file,
)


def get_project_group_vars_dir(project_id):
    """ group_vars (fallback)
    
    : inventories/group_vars/ ( inventories)
       """
    inventories_dir = get_project_inventories_dir(project_id)
    return inventories_dir / 'group_vars'


def get_project_host_vars_dir(project_id):
    """ host_vars (fallback)
    
    : inventories/host_vars/ ( inventories)
       """
    inventories_dir = get_project_inventories_dir(project_id)
    return inventories_dir / 'host_vars'


def get_host_vars_dir_for_inventory(project_id: str, inventory_file_path: str = None) -> Path:
    """
 host_vars inventory .
    
 inventory_file_path, inventory .
 inventories/host_vars (fallback).
    
    Args:
 project_id: ID 
 inventory_file_path: inventory ( repo/ )
    
    Returns:
 Path host_vars
    """
    if inventory_file_path:
        project_dir = get_project_dir(project_id)
        repo_dir = project_dir / 'repo'
        
        app.logger.debug(f"[get_host_vars_dir_for_inventory] Looking for inventory file: {inventory_file_path} in project {project_id}")
        
        inventory_path = None
        
        # 1. repo/
        repo_path = repo_dir / inventory_file_path
        if repo_path.exists():
            inventory_path = repo_path
            app.logger.debug(f"[get_host_vars_dir_for_inventory] Found via repo_path: {repo_path}")
        else:
            # 2. , 
            full_path = Path(inventory_file_path)
            if full_path.exists():
                try:
                    if full_path.is_relative_to(project_dir):
                        inventory_path = full_path
                        app.logger.debug(f"[get_host_vars_dir_for_inventory] Found via full_path: {full_path}")
                except ValueError:
                    # full_path project_dir
                    pass
            
            if not inventory_path:
                # 3. repo/ 
                file_name = Path(inventory_file_path).name
                found_files = list(repo_dir.rglob(file_name))
                if found_files:
                    # , , 
                    best_match = None
                    path_parts = Path(inventory_file_path).parts
                    for found_file in found_files:
                        # , 
                        try:
                            rel_path = found_file.relative_to(repo_dir)
                            if str(rel_path) == inventory_file_path or rel_path.parts[-len(path_parts):] == path_parts:
                                best_match = found_file
                                break
                        except ValueError:
                            pass
                    
                    inventory_path = best_match or found_files[0]
                    app.logger.debug(f"[get_host_vars_dir_for_inventory] Found via rglob: {inventory_path}")
        
        if inventory_path and inventory_path.exists():
            # inventory 
            host_vars_dir = inventory_path.parent / 'host_vars'
            app.logger.debug(f"[get_host_vars_dir_for_inventory] Using host_vars dir: {host_vars_dir} for inventory: {inventory_path}")
            return host_vars_dir
        else:
            app.logger.warning(f"[get_host_vars_dir_for_inventory] Inventory file not found: {inventory_file_path}, using fallback")
    
    # Fallback: vars/host_vars
    fallback_dir = get_project_host_vars_dir(project_id)
    app.logger.debug(f"[get_host_vars_dir_for_inventory] Using fallback dir: {fallback_dir}")
    return fallback_dir


def get_group_vars_dir_for_inventory(project_id: str, inventory_file_path: str = None) -> Path:
    """
 group_vars inventory .
    
 inventory_file_path, inventory .
 inventories/group_vars (fallback).
    
    Args:
 project_id: ID 
 inventory_file_path: inventory ( repo/ )
    
    Returns:
 Path group_vars
    """
    if inventory_file_path:
        project_dir = get_project_dir(project_id)
        repo_dir = project_dir / 'repo'
        
        inventory_path = None
        
        # 1. repo/
        repo_path = repo_dir / inventory_file_path
        if repo_path.exists():
            inventory_path = repo_path
        else:
            # 2. , 
            full_path = Path(inventory_file_path)
            if full_path.exists() and full_path.is_relative_to(project_dir):
                inventory_path = full_path
            else:
                # 3. repo/
                found_files = list(repo_dir.rglob(Path(inventory_file_path).name))
                if found_files:
                    inventory_path = found_files[0]
        
        if inventory_path and inventory_path.exists():
            # inventory 
            return inventory_path.parent / 'group_vars'
    
    # Fallback: vars/group_vars
    return get_project_group_vars_dir(project_id)


def find_inventory_file_for_group(project_id: str, group_name: str) -> str:
    """
 inventory , .
    
    Args:
 project_id: ID 
 group_name: 
        
    Returns:
 str: inventory ( repo/) 
    """
    project_dir = get_project_dir(project_id)
    repo_dir = project_dir / 'repo'
    inventories_dir = get_project_inventories_dir(project_id)
    
    # inventory (.yaml, .yml, .ini, hosts)
    inventory_files = []
    if inventories_dir.exists():
        for inv_file in inventories_dir.rglob('*.yaml'):
            if inv_file.is_file():
                rel_path = inv_file.relative_to(inventories_dir)
                if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                    inventory_files.append(str(inv_file.relative_to(repo_dir)))
        for inv_file in inventories_dir.rglob('*.yml'):
            if inv_file.is_file():
                rel_path = inv_file.relative_to(inventories_dir)
                if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                    inventory_files.append(str(inv_file.relative_to(repo_dir)))
        for inv_file in inventories_dir.rglob('*.ini'):
            if inv_file.is_file():
                rel_path = inv_file.relative_to(inventories_dir)
                if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                    inventory_files.append(str(inv_file.relative_to(repo_dir)))
        for inv_file in inventories_dir.rglob('*'):
            if inv_file.is_file() and inv_file.name == 'hosts' and inv_file.suffix == '':
                rel_path = inv_file.relative_to(inventories_dir)
                if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                    inventory_files.append(str(inv_file.relative_to(repo_dir)))
    
    root_inventory = repo_dir / 'inventory.yml'
    if root_inventory.exists():
        inventory_files.append('inventory.yml')
    
    for inv_file_path in inventory_files:
        full_path = repo_dir / inv_file_path
        if full_path.exists():
            try:
                if _is_ini_inventory_file(full_path):
                    inventory_data = _parse_ini_inventory(full_path) or {}
                else:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        inventory_data = yaml_loader.load(f) or {}
                all_children = inventory_data.get('all', {}).get('children', {})
                if group_name in all_children:
                    return inv_file_path
            except Exception as e:
                app.logger.debug(f"Error reading inventory {inv_file_path} for group search: {e}")
                continue
    return ''


def find_inventory_files_for_playbook(project_id: str, playbook: dict) -> list:
    """
 inventory , .
    
    Args:
 project_id: ID 
 playbook: 
        
    Returns:
 list: inventory ( repo/)
    """
    app.logger.info(f"[find_inventory_files_for_playbook] ========== Starting inventory file search ==========")
    app.logger.info(f"[find_inventory_files_for_playbook] Project ID: {project_id}")
    app.logger.info(f"[find_inventory_files_for_playbook] Playbook: {playbook.get('name', 'Unknown')} (ID: {playbook.get('id', 'Unknown')})")
    
    project_dir = get_project_dir(project_id)
    repo_dir = project_dir / 'repo'
    inventories_dir = get_project_inventories_dir(project_id)
    
    app.logger.debug(f"[find_inventory_files_for_playbook] Project dir: {project_dir}")
    app.logger.debug(f"[find_inventory_files_for_playbook] Repo dir: {repo_dir}")
    app.logger.debug(f"[find_inventory_files_for_playbook] Inventories dir: {inventories_dir}")
    
    hosts_to_find = set()
    groups_to_find = set()
    
    app.logger.info(f"[find_inventory_files_for_playbook] Analyzing playbook with {len(playbook.get('plays', []))} plays")
    
    for play in playbook.get('plays', []):
        hosts_value = play.get('hosts', '')
        app.logger.debug(f"[find_inventory_files_for_playbook] Play hosts value: {hosts_value} (type: {type(hosts_value).__name__})")
        
        if isinstance(hosts_value, str):
            # IP 
            if hosts_value and hosts_value != 'all':
                # , IP 
                import re
                if re.match(r'^\d+\.\d+\.\d+\.\d+$', hosts_value):
                    # IP ()
                    hosts_to_find.add(hosts_value)
                    app.logger.debug(f"[find_inventory_files_for_playbook] Added host: {hosts_value}")
                else:
                    groups_to_find.add(hosts_value)
                    app.logger.debug(f"[find_inventory_files_for_playbook] Added group: {hosts_value}")
        elif isinstance(hosts_value, list):
            for host in hosts_value:
                if host and host != 'all':
                    import re
                    if re.match(r'^\d+\.\d+\.\d+\.\d+$', str(host)):
                        hosts_to_find.add(str(host))
                        app.logger.debug(f"[find_inventory_files_for_playbook] Added host from list: {host}")
                    else:
                        groups_to_find.add(str(host))
                        app.logger.debug(f"[find_inventory_files_for_playbook] Added group from list: {host}")
    
    app.logger.info(f"[find_inventory_files_for_playbook] Hosts to find: {hosts_to_find}, Groups to find: {groups_to_find}")
    
    if not hosts_to_find and not groups_to_find:
        # /, ( default)
        app.logger.warning(f"[find_inventory_files_for_playbook] No hosts or groups found in playbook, returning empty list")
        return []
    
    # inventory (.yaml, .yml, .ini, hosts)
    inventory_files = []
    if inventories_dir.exists():
        for inv_file in inventories_dir.rglob('*.yaml'):
            if inv_file.is_file():
                rel_path = inv_file.relative_to(inventories_dir)
                if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                    repo_rel_path = str(inv_file.relative_to(repo_dir))
                    inventory_files.append(repo_rel_path)
                    app.logger.debug(f"[find_inventory_files_for_playbook] Found inventory file: {repo_rel_path}")
        for inv_file in inventories_dir.rglob('*.yml'):
            if inv_file.is_file():
                rel_path = inv_file.relative_to(inventories_dir)
                if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                    repo_rel_path = str(inv_file.relative_to(repo_dir))
                    inventory_files.append(repo_rel_path)
                    app.logger.debug(f"[find_inventory_files_for_playbook] Found inventory file: {repo_rel_path}")
        for inv_file in inventories_dir.rglob('*.ini'):
            if inv_file.is_file():
                rel_path = inv_file.relative_to(inventories_dir)
                if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                    repo_rel_path = str(inv_file.relative_to(repo_dir))
                    inventory_files.append(repo_rel_path)
                    app.logger.debug(f"[find_inventory_files_for_playbook] Found inventory file: {repo_rel_path}")
        for inv_file in inventories_dir.rglob('*'):
            if inv_file.is_file() and inv_file.name == 'hosts' and inv_file.suffix == '':
                rel_path = inv_file.relative_to(inventories_dir)
                if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                    repo_rel_path = str(inv_file.relative_to(repo_dir))
                    inventory_files.append(repo_rel_path)
                    app.logger.debug(f"[find_inventory_files_for_playbook] Found inventory file: {repo_rel_path}")
    else:
        app.logger.warning(f"[find_inventory_files_for_playbook] Inventories directory does not exist: {inventories_dir}")
    
    root_inventory = repo_dir / 'inventory.yml'
    if root_inventory.exists():
        inventory_files.append('inventory.yml')
        app.logger.debug(f"[find_inventory_files_for_playbook] Found root inventory file: inventory.yml")
    
    app.logger.info(f"[find_inventory_files_for_playbook] Total inventory files found: {len(inventory_files)}")
    app.logger.info(f"[find_inventory_files_for_playbook] Inventory files: {inventory_files}")
    
    found_inventory_files = set()
    
    for inv_file_path in inventory_files:
        full_path = repo_dir / inv_file_path
        app.logger.debug(f"[find_inventory_files_for_playbook] Checking inventory file: {inv_file_path} (full path: {full_path})")
        if not full_path.exists():
            app.logger.warning(f"[find_inventory_files_for_playbook] Inventory file not found: {full_path}")
            continue
            
        try:
            if _is_ini_inventory_file(full_path):
                inventory_data = _parse_ini_inventory(full_path) or {}
            else:
                with open(full_path, 'r', encoding='utf-8') as f:
                    inventory_data = yaml_loader.load(f) or {}
            
            app.logger.debug(f"[find_inventory_files_for_playbook] Loaded inventory data from {inv_file_path}")
            if inventory_data.get('all'):
                all_children = inventory_data.get('all', {}).get('children', {})
                app.logger.debug(f"[find_inventory_files_for_playbook] Inventory has 'all' section with children: {list(all_children.keys())}")
                for group_name, group_data in all_children.items():
                    if isinstance(group_data, dict) and 'hosts' in group_data:
                        hosts_in_group = group_data.get('hosts', {})
                        if isinstance(hosts_in_group, dict):
                            app.logger.debug(f"[find_inventory_files_for_playbook] Group {group_name} has hosts (dict): {list(hosts_in_group.keys())}")
                        elif isinstance(hosts_in_group, list):
                            app.logger.debug(f"[find_inventory_files_for_playbook] Group {group_name} has hosts (list): {hosts_in_group}")
            else:
                app.logger.warning(f"[find_inventory_files_for_playbook] Inventory {inv_file_path} does not have 'all' section")
            
            found_hosts = False
            found_groups = False
            
            if hosts_to_find:
                for host_name in hosts_to_find:
                    app.logger.debug(f"[find_inventory_files_for_playbook] Searching for host {host_name} in inventory {inv_file_path}")
                    host_result = find_host_in_inventory(inventory_data, host_name)
                    if host_result is not None:
                        found_hosts = True
                        app.logger.info(f"[find_inventory_files_for_playbook] ✓ Found host {host_name} in inventory {inv_file_path}")
                        break
                    else:
                        app.logger.warning(f"[find_inventory_files_for_playbook] ✗ Host {host_name} NOT found in inventory {inv_file_path}")
                        # inventory 
                        all_children = inventory_data.get('all', {}).get('children', {})
                        app.logger.debug(f"[find_inventory_files_for_playbook] Inventory structure - groups: {list(all_children.keys())}")
                        for group_name, group_data in all_children.items():
                            if isinstance(group_data, dict) and 'hosts' in group_data:
                                group_hosts = group_data.get('hosts', {})
                                if isinstance(group_hosts, dict):
                                    app.logger.debug(f"[find_inventory_files_for_playbook] Group {group_name} hosts (dict): {list(group_hosts.keys())}")
                                elif isinstance(group_hosts, list):
                                    app.logger.debug(f"[find_inventory_files_for_playbook] Group {group_name} hosts (list): {group_hosts}")
            
            if groups_to_find:
                all_children = inventory_data.get('all', {}).get('children', {})
                for group_name in groups_to_find:
                    if group_name in all_children:
                        found_groups = True
                        app.logger.info(f"[find_inventory_files_for_playbook] ✓ Found group {group_name} in inventory {inv_file_path}")
                        break
                    for child_group_name, child_data in all_children.items():
                        if isinstance(child_data, dict):
                            nested_children = child_data.get('children', {})
                            if group_name in nested_children:
                                found_groups = True
                                app.logger.info(f"[find_inventory_files_for_playbook] ✓ Found nested group {group_name} in inventory {inv_file_path}")
                                break
            
            # , 
            if found_hosts or found_groups:
                found_inventory_files.add(inv_file_path)
                app.logger.info(f"[find_inventory_files_for_playbook] ✓ Added matching inventory file: {inv_file_path} (hosts: {found_hosts}, groups: {found_groups})")
            else:
                app.logger.debug(f"[find_inventory_files_for_playbook] No match in inventory {inv_file_path}")
                
        except Exception as e:
            app.logger.warning(f"[find_inventory_files_for_playbook] Error reading inventory {inv_file_path} for playbook search: {e}", exc_info=True)
            continue
    
    app.logger.info(f"[find_inventory_files_for_playbook] Final inventory files: {list(found_inventory_files)}")
    return list(found_inventory_files)



from utils.project_paths import (  # noqa: F401
    get_project_executions_dir,
    get_project_logs_dir,
    get_project_roles_config_file,
    get_project_secrets_dir,
    get_project_vault_dir,
    get_project_vault_keys_dir,
    get_project_vaults_file,
    get_project_config_file,
    get_project_host_status_file,
    get_project_generated_playbook_path,
)


from utils.ssh_keys import copy_keys_to_generated_playbooks_dedup as _copy_keys_to_generated_playbooks_dedup  # noqa: F401  (extracted in Phase 1)


from utils.paths import slugify_secret_name  # noqa: F401  (re-export; extracted in Phase 1)


def get_secret_file_path(project_id, secret_name, secret_type=None):
    """ 
    
    :
    - secrets/ssh_keys/<key_id>.json - SSH 
    - secrets/vault/vault_pass - vault 
    - secrets/git_auth/<auth_id>.json - git 
       """
    safe_name = slugify_secret_name(secret_name)
    secrets_dir = get_project_secrets_dir(project_id)
    
    # : 
    if secret_type == 'ssh_key' or (not secret_type):
        # SSH 
        ssh_keys_dir = secrets_dir / 'ssh_keys'
        ssh_keys_dir.mkdir(parents=True, exist_ok=True)
        return ssh_keys_dir / f"{safe_name}.json"
    elif secret_type == 'vault_password':
        vault_dir = secrets_dir / 'vault'
        vault_dir.mkdir(parents=True, exist_ok=True)
        return vault_dir / 'vault_pass'
    elif secret_type == 'git_auth':
        git_auth_dir = secrets_dir / 'git_auth'
        git_auth_dir.mkdir(parents=True, exist_ok=True)
        return git_auth_dir / f"{safe_name}.json"
    else:
        # SSH 
        ssh_keys_dir = secrets_dir / 'ssh_keys'
        ssh_keys_dir.mkdir(parents=True, exist_ok=True)
        return ssh_keys_dir / f"{safe_name}.json"


# Request-context helpers extracted to utils.request_ctx in Phase 1.
# get_project_id_from_request needs the default-project resolver, which still
# lives in app.py; we bind it here so callers keep the original signature.
from utils.request_ctx import (
    project_id_from_request_sources as _project_id_from_request_sources,  # noqa: F401
    require_project_id_from_request,  # noqa: F401
)
from utils.request_ctx import get_project_id_from_request as _get_project_id_from_request_impl


def get_project_id_from_request():
    """Return request project_id or the default project. Thin wrapper that binds
    the extracted helper to app.py's default-project resolver."""
    return _get_project_id_from_request_impl(get_default_project_id)



# Phase 2 refactor: extracted to backend/utils/yaml_io.py.
from utils.yaml_io import (  # noqa: F401
    create_backup,
    save_yaml_with_comments,
)


# Inventory traversal helpers extracted to utils.inventory_io in Phase 1.
from utils.inventory_io import (
    extract_hosts_from_group,  # noqa: F401
    extract_groups_from_inventory,  # noqa: F401
)


# INI inventory parsing helpers extracted to utils.inventory_io in Phase 1.
# Re-exported with the original leading-underscore names so all in-file callers
# keep working unchanged.
from utils.inventory_io import (
    is_ini_inventory_file as _is_ini_inventory_file,  # noqa: F401
    parse_ini_key_value as _parse_ini_key_value,  # noqa: F401
    parse_ini_inventory as _parse_ini_inventory,  # noqa: F401
)


def get_inventory_groups(inventory_files=None):
    """ inventory 
    
    Args:
 inventory_files: inventory ( )
 None, dict ( BASE_DIR)
    
    Returns:
        dict: {group_name: {'hosts': [host1, host2, ...], 'children': [child_group1, ...]}}
    """
    groups = {}
    
    # , dict ( BASE_DIR)
    if inventory_files is None or len(inventory_files) == 0:
        return groups
    
    for file_name in inventory_files:
        # , 
        if Path(file_name).is_absolute() or '/' in file_name or '\\' in file_name:
            file_path = Path(file_name)
        else:
            # BASE_DIR fallback 
            app.logger.warning(f"Inventory file {file_name} specified as relative path without full path. Skipping.")
            continue
        
        if not file_path.exists():
            app.logger.warning(f"Inventory file {file_name} not found")
            continue

        rel_file_path = None
        try:
            parts = file_path.parts
            if 'projects' in parts:
                project_idx = parts.index('projects')
                if project_idx + 1 < len(parts):
                    project_dir = Path(*parts[:project_idx + 2])
                    repo_dir = project_dir / 'repo'
                    try:
                        rel_file_path = file_path.relative_to(repo_dir)
                    except ValueError:
                        rel_file_path = Path(file_path.name)
            if rel_file_path is None:
                rel_file_path = Path(file_path.name)
        except Exception:
            rel_file_path = Path(file_path.name)
        inventory_file_str = str(rel_file_path) if rel_file_path else file_path.name

        try:
            if _is_ini_inventory_file(file_path):
                inventory = _parse_ini_inventory(file_path)
            else:
                with open(file_path, 'r', encoding='utf-8') as f:
                    inventory = yaml.safe_load(f)
            
            if not inventory:
                app.logger.warning(f"Inventory file {file_name} is empty or invalid")
                continue
            
            if 'all' in inventory:
                all_section = inventory['all']
                if isinstance(all_section, dict):
                    extract_groups_from_inventory(all_section, groups, 'all', inventory_file_str)
            else:
                extract_groups_from_inventory(inventory, groups, 'root', inventory_file_str)
                
        except yaml.YAMLError as e:
            app.logger.error(f"Error parsing YAML in file {file_name}: {e}")
            continue
        except Exception as e:
            app.logger.warning(f"Error reading inventory file {file_name}: {e}")
            continue
    
    if 'all' in groups and not groups['all']['hosts'] and not groups['all'].get('vars'):
        del groups['all']
    
    app.logger.info(f"Found {len(groups)} groups from {len(inventory_files)} inventory files")
    return groups


def get_inventory_hosts(inventory_files=None):
    """ inventory 
    , :
    - : all.hosts
    - : all.children
    - 
    
       Args:
    inventory_files: inventory ( )
    None, ( BASE_DIR)
       """
    hosts = []
    hosts_set = set()
    
    # , ( BASE_DIR)
    if inventory_files is None or len(inventory_files) == 0:
        return hosts
    
    for file_name in inventory_files:
        # , 
        if Path(file_name).is_absolute() or '/' in file_name or '\\' in file_name:
            file_path = Path(file_name)
        else:
            # BASE_DIR fallback 
            app.logger.warning(f"Inventory file {file_name} specified as relative path without full path. Skipping.")
            continue
        
        if not file_path.exists():
            app.logger.warning(f"Inventory file {file_name} not found")
            continue

        file_path_str = str(file_path)
        try:
            if _is_ini_inventory_file(file_path):
                inventory = _parse_ini_inventory(file_path)
            else:
                with open(file_path, 'r', encoding='utf-8') as f:
                    inventory = yaml.safe_load(f)
            
            if not inventory:
                app.logger.warning(f"Inventory file {file_name} is empty or invalid")
                continue

            hosts_before = len(hosts)
            if 'all' in inventory:
                all_section = inventory['all']
                if isinstance(all_section, dict):
                    extract_hosts_from_group(all_section, hosts_set, hosts, 'all', file_path_str)
            else:
                extract_hosts_from_group(inventory, hosts_set, hosts, 'root', file_path_str)
            
            hosts_added = len(hosts) - hosts_before
            app.logger.info(f"Loaded {hosts_added} hosts from inventory file: {file_path_str} (total: {len(hosts)})")
                
        except yaml.YAMLError as e:
            app.logger.error(f"YAML parsing error in file {file_name}: {e}")
            continue
        except Exception as e:
            app.logger.warning(f"Error reading inventory file {file_name}: {e}")
            continue
    
    app.logger.info(f"Found {len(hosts)} total hosts from {len(inventory_files)} inventory files")
    return hosts


from utils.inventory_io import find_host_in_inventory  # noqa: F401  (extracted in Phase 1)


def get_host_vars_file(host, project_id, inventory_file_path=None):
    """ host_vars Project Storage
    
    Args:
 host: 
 project_id: ID (REQUIRED)
 inventory_file_path: inventory (, )
    
    Returns:
 Path: host_vars Project Storage
    """
    if not project_id:
        raise ValueError("project_id is required")
    
    # host_vars inventory 
    if inventory_file_path:
        host_vars_dir = get_host_vars_dir_for_inventory(project_id, inventory_file_path)
    else:
        # inventory 
        hosts_list = get_inventory_hosts()
        for h in hosts_list:
            if h['name'] == host:
                host_inventory_file = h.get('inventory_file', '')
                if host_inventory_file:
                    host_vars_dir = get_host_vars_dir_for_inventory(project_id, host_inventory_file)
                    break
        else:
            # Fallback: 
            host_vars_dir = get_project_host_vars_dir(project_id)
    
    hosts_list = get_inventory_hosts()
    for h in hosts_list:
        if h['name'] == host:
            vars_file = h.get('vars_file', '')
            if vars_file.startswith('host_vars/'):
                vars_file = vars_file.replace('host_vars/', '')
                return host_vars_dir / vars_file
    return host_vars_dir / f'{host}.yml'


# ============================================================================
# AUTHENTICATION API ENDPOINTS
# ============================================================================

# --- Login brute-force protection (in-memory sliding-window) ---
# Auth routes moved to api/auth_routes.py (Phase 2 refactor).



# Users / Roles / Permissions routes moved to api/users_routes.py (Phase 3).




# Ansible
ansible_output = {'output': '', 'status': 'idle', 'process': None}

def generate_dynamic_playbook(selected_roles):
    """ playbook """
    role_mapping = {
        '00_init': '00_init',
        '01_backup_etc': '01_backup_etc',
        '02_init_sshd': '02_init_sshd',
        '03_configure_users': '03_configure_users',
        '04_configure_hostname': '04_configure_hostname',
        '05_configure_sysctl_limits': '05_configure_sysctl_limits',
        '06_configure_kernel': '06_configure_kernel',
        '07_remove_unwanted_services': '07_remove_unwanted_services',
        '08_configure_security': '08_configure_security',
        '09_configure_locales': '09_configure_locales',
        '10_manage_services': '10_manage_services',
        '11_certificates': '11_certificates',
        '12_date_timezone': '12_date_timezone',
        '13_configure_repo': '13_configure_repo',
        '14_install_software': '14_install_software',
        '15_configure_bash': '15_configure_bash',
        '16_configure_network': '16_configure_network',
        '17_update_reboot': '17_update_reboot'
    }
    
    role_descriptions = {
        '00_init': 'System initialization',
        '01_backup_etc': 'Backup must be FIRST - create a backup before making changes',
        '02_init_sshd': 'SSH and user configuration',
        '03_configure_users': 'SSH and user configuration',
        '04_configure_hostname': 'Basic system configuration',
        '05_configure_sysctl_limits': 'Basic system configuration',
        '06_configure_kernel': 'Basic system configuration',
        '07_remove_unwanted_services': 'Basic system configuration',
        '08_configure_security': 'Basic system configuration',
        '09_configure_locales': 'Basic system configuration',
        '10_manage_services': 'Basic system configuration',
        '11_certificates': 'Network settings and certificates',
        '12_date_timezone': 'Network settings and certificates',
        '13_configure_repo': 'Network settings and certificates',
        '14_install_software': 'Network settings and certificates',
        '15_configure_bash': 'Additional configuration',
        '16_configure_network': 'Additional configuration',
        '17_update_reboot': 'Final actions - update and reboot must be LAST'
    }
    
    playbook_content = """- hosts: all
  become: True
  remote_user: root
  vars:
    ansible_ssh_user: root
  roles:
    """
    
    # roles-storage ( pack/), 
    filtered_roles = [r for r in selected_roles if '/' in r]
    sorted_roles = sorted(filtered_roles, key=lambda x: (
        int(x.split('/')[-1].split('_')[0]) if x.split('/')[-1].split('_')[0].isdigit() else 999
    ))
    
    for role_tag in sorted_roles:
        # "core-roles/00_init"
        if '/' in role_tag:
            pack_id, role_name = role_tag.split('/', 1)
            role_path = f"roles-storage/{pack_id}/{role_name}"
            # role_descriptions ( )
            description = role_descriptions.get(role_name, '')
            if description:
                playbook_content += f"\n    # {description}\n"
            playbook_content += f"    - role: {role_path}\n"
            playbook_content += f"      tags: [{role_tag}]\n"
    
    return playbook_content



# Phase 3 refactor: run_ansible / ansible_status / stop_ansible / host_facts
# routes moved to backend/api/ansible_run_routes.py.


# Phase 3 refactor: backup and backup-settings routes moved to
# backend/api/backups_routes.py (registered via api.register_blueprints).



# Phase 3 refactor: server-log and frontend-log routes extracted to
# backend/api/logs_routes.py (registered via api.register_blueprints).
# The helper below is re-exported so any legacy in-process caller still works.
from api.logs_routes import _parse_log_timestamp  # noqa: F401


# ==================== Execution History Functions ====================

# Phase 3 refactor: execution/backup settings extracted to
# backend/utils/backup_settings.py. Re-exported so every existing caller
# (routes, worker callbacks, retention logic) keeps working unchanged.
from utils.backup_settings import (  # noqa: F401
    load_execution_settings,
    save_execution_settings,
    load_backup_settings,
    save_backup_settings,
    get_project_backup_enabled,
    should_create_backup,
    cleanup_old_backups,
    cleanup_old_archives,
    validate_log_level,
)


def set_log_level(level_name):
    """
 .
    
    Args:
 level_name: ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
    """
    if not validate_log_level(level_name):
        app.logger.warning(f"Invalid log level: {level_name}, using INFO. Valid levels: DEBUG, INFO, WARNING, ERROR, CRITICAL")
        level = logging.INFO
    else:
        level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL
        }
        level = level_map.get(level_name.upper(), logging.INFO)
    
    # handlers
    app.logger.setLevel(level)
    file_handler.setLevel(level)
    console_handler.setLevel(level)
    
    # frontend logger
    frontend_logger.setLevel(level)
    frontend_file_handler.setLevel(level)
    
    app.logger.info(f"Log level changed to: {level_name.upper()}")
    return True


def save_execution_settings(settings):
    """Persist execution-history settings to the config DB."""
    try:
        from utils.backup_settings import save_execution_settings as _save
        return _save(settings)
    except Exception as e:
        app.logger.error(f"Error saving execution history settings: {e}")
        return False




# Phase 3 refactor: execution-history helpers extracted to
# backend/services/execution_history.py. Re-exported here so every existing
# caller (routes, worker callbacks, retention logic) keeps working unchanged.
from services.execution_history import (  # noqa: F401
    create_execution_record,
    update_execution_record,
    append_execution_log,
    get_execution_log,
    list_executions,
    get_execution,
    _cleanup_generated_playbooks_for_execution,
    _cleanup_orphaned_generated_playbooks,
    delete_execution,
    apply_retention_policy,
    clear_all_executions,
    get_execution_stats,
)

# ==================== Execution History API Endpoints ====================

# Phase 3 refactor: execution history and execution_settings routes moved
# to backend/api/executions_routes.py.



# ==================== Roles Storage API Endpoints ====================


# Phase 3 refactor: roles-storage / role-details helpers extracted to
# backend/services/roles_storage.py. Re-exported for backward compatibility.
from services.roles_storage import (  # noqa: F401
    scan_roles_storage,
    load_roles_config,
    save_roles_config,
    extract_variables_from_yaml_text,
    get_role_details,
)

# ============================================================================


# Phase 3 refactor: project CRUD (list/create/get/update) routes moved to
# backend/api/projects_routes.py.

# ============================================================================
# Vault Keys API (secrets/vault_keys)
# ============================================================================

# ============================================================================
# Phase 3 refactor: 5 vault-key routes moved to backend/api/vaults_routes.py
# (registered via api.register_blueprints).
# ============================================================================

# Vault helpers extracted to services/vault_service.py — re-exported here so
# `from app import ...` continues to resolve for legacy callers and blueprints.
from services.vault_service import (  # noqa: E402,F401
    _load_vault_file_keys,
    _save_vault_file_key,
    _scan_repo_vault_files,
    _load_vaults,
    _enrich_vault_vault_id_from_content,
    _save_vaults,
    get_key_password_for_vault,
    get_key_password_by_key_id,
    _get_default_vault_password,
    _decrypt_content_if_encrypted,
    _encrypt_content_if_requested,
    _resolve_vault_file_path,
)


# ============================================================================
# Phase 3 refactor: 4 vault-file routes (get/encrypt/decrypt/save) moved to backend/api/vaults_routes.py
# (registered via api.register_blueprints).
# ============================================================================

# ============================================================================
# Project Sources API
# ============================================================================

def load_project_config(project_id):
    """ project.json"""
    config_file = get_project_config_file(project_id)
    if not config_file.exists():
        return {}
    
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        app.logger.error(f"Invalid JSON in project config {project_id}: {e}")
        return {}
    except Exception as e:
        app.logger.error(f"Error loading project config {project_id}: {e}")
        return {}


def save_project_config(project_id, config):
    """ project.json
    
    Raises:
        IOError: If file write fails
        TypeError: If config contains non-serializable data
    """
    config_file = get_project_config_file(project_id)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        app.logger.info(f"Project config saved: {project_id}")
    except IOError as e:
        app.logger.error(f"Failed to write project config file {config_file}: {e}", exc_info=True)
        raise IOError(f"Failed to save project config: {e}") from e
    except TypeError as e:
        app.logger.error(f"Failed to serialize project config for {project_id}: {e}. Config contains non-serializable data.", exc_info=True)
        raise TypeError(f"Failed to serialize project config: {e}") from e




# Phase 3 refactor: project hosts_status / host_settings routes moved to
# backend/api/projects_routes.py.

def _find_groups_for_host(project_id: str, host_name: str):
    """Return the set of inventory group names that contain host_name (recursively).

    Always includes 'all'. Walks every inventory file under the project so hosts
    picked up by a group referenced in a playbook (e.g. ``hosts: app_vms``) can
    inherit their connection credentials from ``group_vars/app_vms.yml``.
    """
    groups = {'all'}
    try:
        inventories_dir = get_project_inventories_dir(project_id)
        if not inventories_dir.exists():
            return groups

        def _walk(group_data, group_name):
            if not isinstance(group_data, dict):
                return
            hosts = group_data.get('hosts')
            if isinstance(hosts, dict) and host_name in hosts:
                groups.add(group_name)
            elif isinstance(hosts, list) and host_name in hosts:
                groups.add(group_name)
            children = group_data.get('children')
            if isinstance(children, dict):
                for child_name, child_data in children.items():
                    _walk(child_data, child_name)

        for inv_file in list(inventories_dir.rglob('*')):
            if not inv_file.is_file():
                continue
            if inv_file.name not in ('inventory.yaml', 'inventory.yml', 'hosts.yaml', 'hosts.yml', 'hosts', 'hosts.ini'):
                continue
            try:
                rel_parts = inv_file.relative_to(inventories_dir).parts
                if 'group_vars' in rel_parts or 'host_vars' in rel_parts:
                    continue
            except ValueError:
                pass
            try:
                if _is_ini_inventory_file(inv_file):
                    data = _parse_ini_inventory(inv_file)
                else:
                    with open(inv_file, 'r', encoding='utf-8') as f:
                        data = yaml.safe_load(f) or {}
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            if 'all' in data and isinstance(data['all'], dict):
                _walk(data['all'], 'all')
            else:
                for gname, gdata in data.items():
                    _walk(gdata, gname)
    except Exception as e:
        app.logger.debug(f"[_find_groups_for_host] {host_name}: {e}")
    return groups


def resolve_connection_secret_for_host(project_id: str, host_name: str):
    """Resolve connection info for host from host_vars, then group_vars, then all vars.

    Returns (secret_name_or_None, has_connection). ``has_connection`` is True when
    either a connectionSecret is defined or explicit ansible connection variables
    (ansible_password / ansible_ssh_private_key_file) are set at any scope the
    host inherits from — so hosts that only carry credentials via their group's
    group_vars are still checkable.
    """
    if not project_id or not host_name:
        return None, False
    try:
        host_vars_dir = get_project_host_vars_dir(project_id)
        host_file = host_vars_dir / f"{host_name}.yml"
        if host_file.exists():
            with open(host_file, 'r', encoding='utf-8') as f:
                host_vars = yaml_loader.load(f) or {}
            if isinstance(host_vars, dict):
                if host_vars.get('connectionSecret'):
                    return host_vars['connectionSecret'], True
                if host_vars.get('ansible_ssh_private_key_file') or host_vars.get('ansible_password'):
                    return host_vars.get('connectionSecret'), True

        # Fall back to group_vars for every group this host belongs to (plus 'all').
        group_vars_dir = get_project_group_vars_dir(project_id)
        if group_vars_dir.exists():
            groups = _find_groups_for_host(project_id, host_name)
            # Check specific groups before 'all' so a group-level secret wins.
            ordered = [g for g in groups if g != 'all'] + (['all'] if 'all' in groups else [])
            for group_name in ordered:
                for ext in ('.yml', '.yaml'):
                    gfile = group_vars_dir / f"{group_name}{ext}"
                    if not gfile.exists():
                        continue
                    try:
                        with open(gfile, 'r', encoding='utf-8') as f:
                            gvars = yaml_loader.load(f) or {}
                    except Exception:
                        continue
                    if not isinstance(gvars, dict):
                        continue
                    if gvars.get('connectionSecret'):
                        return gvars['connectionSecret'], True
                    if gvars.get('ansible_ssh_private_key_file') or gvars.get('ansible_password'):
                        return gvars.get('connectionSecret'), True
        return None, False
    except Exception as e:
        app.logger.debug(f"[resolve_connection_secret_for_host] {host_name}: {e}")
        return None, False



def get_projects_with_auto_check_hosts():
    """ project_id, auto_check_all_hosts."""
    result = []
    try:
        for entry in PROJECTS_DIR.iterdir():
            if not entry.is_dir():
                continue
            project_id = entry.name
            config = load_project_config(project_id) or {}
            if config.get('host_status', {}).get('auto_check_all_hosts'):
                result.append(project_id)
    except Exception as e:
        app.logger.warning(f"[get_projects_with_auto_check_hosts] {e}")
    return result


def get_project_hosts_list(project_id: str):
    """ inventory ( get_all_data)."""
    try:
        project_dir = get_project_dir(project_id)
        inventories_dir = get_project_inventories_dir(project_id)
        inventory_files_to_use = []
        for name in ['inventory.yaml', 'inventory.yml', 'hosts.yaml', 'hosts.yml', 'hosts', 'hosts.ini']:
            p = inventories_dir / name
            if p.exists():
                inventory_files_to_use.append(str(p))
        for inv_file in inventories_dir.rglob('*'):
            if inv_file.is_file() and inv_file.name in ['inventory.yaml', 'inventory.yml', 'hosts.yaml', 'hosts.yml', 'hosts', 'hosts.ini']:
                try:
                    rel = inv_file.relative_to(inventories_dir)
                except ValueError:
                    continue
                if 'group_vars' not in rel.parts and 'host_vars' not in rel.parts:
                    path_str = str(inv_file)
                    if path_str not in inventory_files_to_use:
                        inventory_files_to_use.append(path_str)
        if not inventory_files_to_use and (project_dir / 'repo' / 'inventories').exists():
            inv_dir = project_dir / 'repo' / 'inventories'
            for name in ['inventory.yaml', 'inventory.yml', 'hosts.yaml', 'hosts.yml', 'hosts', 'hosts.ini']:
                for p in inv_dir.rglob(name):
                    if p.is_file():
                        try:
                            r = p.relative_to(inv_dir)
                            if 'group_vars' not in r.parts and 'host_vars' not in r.parts:
                                inventory_files_to_use.append(str(p))
                                break
                        except ValueError:
                            pass
        hosts = get_inventory_hosts(inventory_files_to_use) if inventory_files_to_use else []
        return [h['name'] for h in hosts]
    except Exception as e:
        app.logger.warning(f"[get_project_hosts_list] {project_id}: {e}")
        return []


def _get_host_status_scheduler_interval():
    """Interval (seconds) for the host-status background tick, configurable at runtime."""
    try:
        from utils.scheduler_settings import get_interval
        return get_interval('host_status_scheduler_interval')
    except Exception:
        try:
            return max(30, int(os.environ.get('HOST_STATUS_SCHEDULER_INTERVAL') or 300))
        except (TypeError, ValueError):
            return 300


_host_status_scheduler_interval = _get_host_status_scheduler_interval()  # seconds
_host_status_scheduler_thread = None


def _host_status_auto_check_loop():
    """ : N - TTL auto_check_all_hosts."""
    while True:
        try:
            time.sleep(_get_host_status_scheduler_interval())
            host_status_auto_check_tick()
        except Exception as e:
            app.logger.warning(f"[host_status_auto_check_loop] {e}")


def host_status_auto_check_tick():
    """
 : auto_check_all_hosts TTL.
 /api/check_hosts ( /api/check_host).
    """
    for project_id in get_projects_with_auto_check_hosts():
        try:
            hosts = get_project_hosts_list(project_id)
            to_check = []
            connection_secrets = {}
            for host in hosts:
                # /. ,
                # , (expired),
                # unknown (, ).
                st = get_host_check_status(project_id, host)
                if st:
                    is_expired = bool(st.get('expired'))
                    status_str = (st.get('status') or 'unknown').lower()
                    if not is_expired and status_str not in ('unknown',):
                        # — 
                        continue
                secret_name, has_conn = resolve_connection_secret_for_host(project_id, host)
                if not has_conn and not secret_name:
                    continue
                to_check.append(host)
                if secret_name:
                    connection_secrets[host] = secret_name
            to_check = to_check[:20]
            if not to_check:
                continue
            from auth.middleware import get_internal_call_secret
            internal_secret = get_internal_call_secret()
            try:
                with app.test_client() as client:
                    rv = client.post(
                        '/api/check_hosts',
                        json={
                            'hosts': to_check,
                            'project_id': project_id,
                            'connection_secrets': connection_secrets,
                            'inventory_files': ['inventory.yml'],
                            'ansible_config': 'ansible_local_execute.cfg',
                        },
                        headers={'Content-Type': 'application/json', 'X-Internal-Call': internal_secret},
                    )
                if rv.status_code == 200:
                    app.logger.info(f"[host_status_auto_check] Queued check for {project_id}: {len(to_check)} host(s)")
                else:
                    app.logger.warning(f"[host_status_auto_check] check_hosts returned {rv.status_code} for {project_id}")
            except Exception as e:
                app.logger.debug(f"[host_status_auto_check] {project_id}: {e}")
        except Exception as e:
            app.logger.warning(f"[host_status_auto_check_tick] project {project_id}: {e}")


# Host status auto-check background thread ( TTL - )
def _start_host_status_scheduler():
    global _host_status_scheduler_thread
    if _host_status_scheduler_thread is not None:
        return
    _host_status_scheduler_thread = threading.Thread(target=_host_status_auto_check_loop, daemon=True)
    _host_status_scheduler_thread.start()
    app.logger.info("[host_status] Background auto-check scheduler started (interval=%ss)", _host_status_scheduler_interval)


# Initialize AutosyncScheduler (after load_project_config and save_project_config are defined)
if autosync_scheduler is None:
    from services.autosync_scheduler import AutosyncScheduler
    autosync_scheduler = AutosyncScheduler(
        source_sync_service=source_sync_service,
        load_project_config_func=load_project_config,
        get_project_dir_func=get_project_dir
    )
    # Set save function to avoid circular dependency
    autosync_scheduler._save_project_config = save_project_config
    # Start scheduler
    autosync_scheduler.start()
# Host status auto-check scheduler ( , )
_start_host_status_scheduler()


# Initialize PlaybookScheduler (after playbook_storage is defined)
playbook_scheduler = None

def _create_scheduled_run(project_id: str, playbook_id: str) -> Tuple[bool, Optional[str]]:
    """
    Helper function to create a run for scheduled playbook execution.
    Used by PlaybookScheduler.
    
    Returns:
        (success, error_message)
    """
    try:
        # Call the API function logic directly (without HTTP request)
        # This is essentially the same as api_run_playbook but without HTTP layer
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return False, 'Project not found'
        
        # playbook
        playbook = playbook_storage.get_playbook(project_id, playbook_id)
        if not playbook:
            return False, 'Playbook not found'
        
        # , disabled playbook
        if playbook.get('disabled') or playbook.get('metadata', {}).get('disabled'):
            app.logger.info(f"[PlaybookScheduler] Skipping disabled playbook {playbook_id} in project {project_id}")
            return False, 'Playbook is disabled'
        
        # inventory_files
        inventory_files = find_inventory_files_for_playbook(project_id, playbook)
        if not inventory_files:
            inventory_files = ['inventory.yml']
        
        # default ansible_config 
        project_dir = get_project_dir(project_id)
        ansible_config_dir = project_dir / 'ansible-config'
        ansible_config = None
        if ansible_config_dir.exists():
            primary = ansible_config_dir / 'ansible.cfg'
            if primary.exists():
                ansible_config = 'ansible-config/ansible.cfg'
            else:
                config_files = list(ansible_config_dir.glob('*.cfg'))
                if config_files:
                    ansible_config = f'ansible-config/{config_files[0].name}'
        
        if not ansible_config:
            app.logger.warning(f"[PlaybookScheduler] No ansible_config found for scheduled run of playbook {playbook_id}")
            return False, 'ansible_config is required'
        
        # YAML playbook
        try:
            yaml_content = playbook_generator.generate(playbook)
        except Exception as e:
            app.logger.error(f"[PlaybookScheduler] Error generating YAML from playbook {playbook_id}: {e}")
            return False, f'Failed to generate playbook YAML: {str(e)}'
        
        if not yaml_content or not isinstance(yaml_content, str) or not yaml_content.strip():
            return False, 'Playbook is empty'
        
        # execution record
        execution_id = str(uuid.uuid4())
        settings = load_execution_settings()
        
        if settings.get('save_history', True):
            # inventory snapshot
            inventory_snapshot = {'groups': []}
            try:
                repo_dir = project_dir / 'repo'
                inv_files_list = []
                for inv_file in inventory_files:
                    if inv_file.startswith('inventories/') or ('.' in inv_file and '/' in inv_file):
                        inv_path = repo_dir / inv_file
                    elif inv_file == 'inventory.yml':
                        inv_path = repo_dir / 'inventory.yml'
                    else:
                        inventories_dir = repo_dir / 'inventories'
                        if inventories_dir.exists():
                            found = False
                            for inv_file_search in inventories_dir.rglob(inv_file):
                                if inv_file_search.is_file():
                                    inv_path = inv_file_search
                                    found = True
                                    break
                            if not found:
                                inv_path = repo_dir / inv_file
                        else:
                            inv_path = repo_dir / inv_file
                    
                    if inv_path.exists():
                        inv_files_list.append(str(inv_path))
                
                if not inv_files_list:
                    default_inv = get_project_inventory_file(project_id)
                    if default_inv.exists():
                        inv_files_list = [str(default_inv)]
                
                if inv_files_list:
                    groups = get_inventory_groups(inv_files_list)
                    for group_name, group_data in groups.items():
                        group_hosts = group_data.get('hosts', [])
                        inventory_snapshot['groups'].append({
                            'groupId': group_name,
                            'groupName': group_name,
                            'hosts': [{'hostId': host, 'ip': host} for host in group_hosts]
                        })
            except Exception as e:
                app.logger.warning(f"[PlaybookScheduler] Error creating inventory snapshot: {e}")
            
            # hosts roles playbook
            all_hosts = set()
            all_roles = []
            for play in playbook.get('plays', []):
                hosts_value = play.get('hosts', '')
                if hosts_value and hosts_value != 'all':
                    if isinstance(hosts_value, list):
                        for host in hosts_value:
                            if host and host != 'all':
                                all_hosts.add(host)
                    elif isinstance(hosts_value, str) and hosts_value != 'all':
                        all_hosts.add(hosts_value)
                
                for role in play.get('roles', []):
                    if isinstance(role, dict):
                        role_name = role.get('role', '')
                    else:
                        role_name = str(role)
                    if role_name:
                        all_roles.append(role_name)
            
            # play ( worker: Mitogen strategy_plugins)
            plays_list = playbook.get('plays', [])
            strategy = plays_list[0].get('strategy', 'linear') if plays_list else 'linear'
            
            # runParams
            run_params = {
                'inventory_files': inventory_files,
                'ansible_config': ansible_config,
                'strategy': strategy,
            }
            
            execution_data = {
                'projectId': project_id,
                'playbook': {
                    'playbookId': playbook_id,
                    'playbookName': playbook.get('name', 'playbook')
                },
                'stats': {
                    'hostsTargeted': len(all_hosts) if all_hosts else 0,
                    'totalRoleExecutions': len(all_roles)
                },
                'warnings': [],
                'status': 'QUEUED',
                'runParams': run_params,
                'scheduled': True  # Mark as scheduled run
            }
            
            execution_id_created = create_execution_record(execution_data, project_id=project_id, execution_id=execution_id)
            if execution_id_created:
                execution_id = execution_id_created
                
                # , 
                executions_dir = get_project_executions_dir(project_id)
                execution_file = executions_dir / f'{execution_id}.json'
                if execution_file.exists():
                    try:
                        with open(execution_file, 'r', encoding='utf-8') as f:
                            created_execution = json.load(f)
                            status = created_execution.get('status', 'UNKNOWN')
                            queued_at = created_execution.get('queuedAt', 'NOT SET')
                            app.logger.info(f"[PlaybookScheduler] ✓ Execution file created: {execution_file}, status={status}, queuedAt={queued_at}")
                    except Exception as e:
                        app.logger.error(f"[PlaybookScheduler] Error reading created execution file: {e}")
                else:
                    app.logger.error(f"[PlaybookScheduler] ✗ Execution file NOT FOUND after creation: {execution_file}")
        
        if execution_id:
            executions_dir = get_project_executions_dir(project_id)
            app.logger.info(f"[PlaybookScheduler] ✓ Successfully created QUEUED execution {execution_id} for playbook {playbook_id} in project {project_id}. File location: {executions_dir}/{execution_id}.json. Worker should pick it up.")
        else:
            app.logger.warning(f"[PlaybookScheduler] Created execution record but got no execution_id for playbook {playbook_id} in project {project_id}")
        
        return True, None
        
    except Exception as e:
        app.logger.error(f"[PlaybookScheduler] Error creating scheduled run for playbook {playbook_id} in project {project_id}: {e}", exc_info=True)
        return False, str(e)


def _call_api_run_playbook(project_id: str, playbook_id: str) -> Tuple[bool, Optional[str]]:
    """
    Call API endpoint for running playbook (like clicking Run button).
    Calls the API endpoint function directly with Flask request context.
    Automatically determines ansible_config and inventory_files like the UI does.
    
    Returns:
        (success, error_message)
    """
    try:
        # Auto-determine ansible_config (like the UI does)
        project_dir = get_project_dir(project_id)
        ansible_config_dir = project_dir / 'ansible-config'
        ansible_config = None
        if ansible_config_dir.exists():
            if (ansible_config_dir / 'ansible.cfg').exists():
                ansible_config = 'ansible-config/ansible.cfg'
            else:
                config_files = list(ansible_config_dir.glob('*.cfg'))
                if config_files:
                    ansible_config = f'ansible-config/{config_files[0].name}'
        
        if not ansible_config:
            app.logger.error(f"[PlaybookScheduler] No ansible_config found for scheduled run of playbook {playbook_id}")
            return False, 'ansible_config is required'
        
        # Auto-determine inventory_files from playbook
        playbook = playbook_storage.get_playbook(project_id, playbook_id)
        if not playbook:
            return False, 'Playbook not found'
        
        # Check if playbook is disabled - scheduler should not run disabled playbooks
        if playbook.get('disabled') or playbook.get('metadata', {}).get('disabled'):
            app.logger.info(f"[PlaybookScheduler] ⏸️ Skipping disabled playbook {playbook_id} in project {project_id}")
            return False, 'Playbook is disabled'
        
        inventory_files = find_inventory_files_for_playbook(project_id, playbook)
        if not inventory_files:
            inventory_files = ['inventory.yml']
        
        # Create Flask request context to simulate HTTP request
        # This is the same as clicking the Run button in the UI
        with app.test_request_context(
            f'/api/projects/{project_id}/playbooks/{playbook_id}/run',
            method='POST',
            json={
                'inventory_files': inventory_files,
                'ansible_config': ansible_config
            }
        ):
            # Call the API endpoint function directly
            # This is exactly what happens when user clicks Run button
            response = api_run_playbook(project_id, playbook_id)
            
            # Parse Flask response
            # Flask route can return either:
            # 1. Tuple: (response_object, status_code)
            # 2. Response object directly
            # 3. Just response object (status_code defaults to 200)
            
            response_obj = None
            status_code = 200
            
            if isinstance(response, tuple):
                # Flask response tuple (response_object, status_code)
                response_obj, status_code = response
            elif hasattr(response, 'status_code'):
                # Direct Response object
                response_obj = response
                status_code = response.status_code
            else:
                # Unexpected format
                app.logger.error(f"[PlaybookScheduler] Unexpected response format: {type(response)}")
                return False, f'Unexpected response format: {type(response)}'
            
            # Parse JSON response
            try:
                if hasattr(response_obj, 'get_json'):
                    result = response_obj.get_json()
                elif hasattr(response_obj, 'get_data'):
                    # Try to get data and parse as JSON
                    data = response_obj.get_data(as_text=True)
                    if data:
                        import json
                        result = json.loads(data)
                    else:
                        result = {}
                else:
                    result = {}
                
                if status_code == 200:
                    if result and result.get('success'):
                        execution_id = result.get('executionId')
                        app.logger.info(f"[PlaybookScheduler] ✅ API call successful (like Run button), execution_id: {execution_id}")
                        return True, None
                    else:
                        error = result.get('error', 'Unknown error') if result else 'No response data'
                        app.logger.error(f"[PlaybookScheduler] ✗ API call failed: {error}")
                        return False, error
                else:
                    # Error status code
                    error = result.get('error', f'HTTP {status_code}') if result else f'HTTP {status_code}'
                    app.logger.error(f"[PlaybookScheduler] ✗ API call failed: {error}")
                    return False, error
            except Exception as e:
                app.logger.error(f"[PlaybookScheduler] Error parsing API response: {e}", exc_info=True)
                return False, f"Error parsing response: {str(e)}"
    except Exception as e:
        app.logger.error(f"[PlaybookScheduler] Error calling API endpoint: {e}", exc_info=True)
        return False, str(e)


# Initialize PlaybookScheduler only once using module-level lock
_playbook_scheduler_lock = threading.Lock()
if playbook_scheduler is None:
    with _playbook_scheduler_lock:
        # Double-check after acquiring lock (double-checked locking pattern)
        if playbook_scheduler is None:
            try:
                # Check if required dependencies are available
                import pytz
                from croniter import croniter
                from services.playbook_scheduler import PlaybookScheduler
                # Use lock file directory in data/locks for cross-process synchronization
                lock_file_dir = DATA_DIR / 'locks'
                playbook_scheduler = PlaybookScheduler(
                    playbook_storage=playbook_storage,
                    api_run_playbook_func=_call_api_run_playbook,  # Use API call instead of direct function
                    lock_file_dir=lock_file_dir  # For cross-process synchronization
                )
                # Start scheduler
                playbook_scheduler.start()
                app.logger.info("Playbook scheduler initialized and started (singleton)")
            except ImportError as e:
                app.logger.warning(f"PlaybookScheduler dependencies not available ({e}). Schedule feature will work but automatic execution is disabled. Install: pip install croniter pytz")
                playbook_scheduler = None
            except Exception as e:
                app.logger.error(f"Failed to initialize PlaybookScheduler: {e}", exc_info=True)
                playbook_scheduler = None
        else:
            app.logger.debug("Playbook scheduler already initialized by another thread")



# Phase 3 refactor: sources / repo-layout / sync helpers extracted to
# backend/services/sources_service.py. Re-exported for backward compatibility.
from services.sources_service import (  # noqa: F401
    get_default_sources,
    normalize_sources,
    validate_repo_layout,
    validate_sources,
    resolve_project_source,
    _resolve_legacy_source,
    get_project_storage_path,
    sync_source_push,
    sync_source_pull,
)

# ============================================================================
# Phase 3 refactor: 8 sources sync + autosync + ensure-dirs + resolve routes moved to backend/api/sources_routes.py
# (registered via api.register_blueprints).
# ============================================================================


# Phase 3 refactor: project DELETE / restore / switch routes moved to
# backend/api/projects_routes.py.


# ============================================================================
# PLAYBOOK API ENDPOINTS
# ============================================================================

# ============================================================================
# Phase 3 refactor: 18 playbook routes moved to backend/api/playbooks_routes.py
# (registered via api.register_blueprints).

# Phase 3 refactor: queue / search / capabilities / project queue-stats routes
# moved to backend/api/queue_search_routes.py.

# Worker/execution dispatcher helpers extracted to
# services/execution_dispatcher.py. Re-exported so existing callers
# (`from app import _attach_worker_transfer_payload`, blueprints via
# LazyProxy, tests) keep working unchanged.
from services.execution_dispatcher import (  # noqa: E402,F401
    _check_execution_requirements,
    _resolve_execution_inventory_path,
    _read_text_file_for_worker,
    _collect_inventory_var_payloads,
    _attach_worker_transfer_payload,
)


_EMPTY_QUEUE_DISK_SCAN_AT = {}
_EMPTY_QUEUE_DISK_SCAN_INTERVAL_SECONDS = int(os.environ.get('EMPTY_QUEUE_DISK_SCAN_INTERVAL_SECONDS', '60'))


def server_claim_next_execution(worker_id, worker_data, project_id=None, max_concurrency=1, tags=None):
    """
 claim QUEUED execution
 race condition
 requirements, runs
    
    Args:
        worker_id: ID worker
 worker_data: worker (capabilities, tags)
 project_id: ID 
 max_concurrency: runs worker
 tags: Tags worker ( )
    
    Returns:
 tuple: (execution_id, execution_data, project_id) (None, None, None)
    """
    import fcntl
    import os
    from pathlib import Path
    try:
        from services.worker_registry import get_worker_active_runs_count
    except ImportError:
        from services.worker_registry import get_worker_active_runs_count
    
    try:
        # worker
        active_runs = get_worker_active_runs_count(worker_id)
        if active_runs >= max_concurrency:
            # Self-heal: the index may hold RUNNING rows for executions that
            # already finished (worker crashed / SIGTERM before finish call).
            # Cross-check against the JSON files and prune the stale rows.
            try:
                from storage import index_db as _index_db
                pruned = _index_db.prune_stale_running_for_worker(
                    worker_id,
                    PROJECTS_DIR,
                    current_execution_id=None,
                    mark_orphaned_failed=True,
                )
                if pruned:
                    active_runs = get_worker_active_runs_count(worker_id)
            except Exception:
                pass
        if active_runs >= max_concurrency:
            app.logger.debug(f"Worker {worker_id} has {active_runs} active runs, maxConcurrency={max_concurrency}")
            return None, None, None
        
        worker_capabilities = worker_data.get('capabilities', {})
        worker_tags = tags or worker_data.get('tags', [])
        
        # Fast path: consult the SQLite index (WAL) so we only open files for
        # executions that are actually QUEUED, instead of globbing every
        # execution file on disk each poll. Falls back to a full scan if the
        # index is unavailable or empty (self-healing at startup).
        from storage import index_db as _index_db

        queued_runs = []
        total_checked = 0
        use_index = _index_db.is_ready()
        project_dirs = []

        def _scan_queued_from_disk(backfill_index=True):
            scanned = []
            checked = 0
            dirs = [PROJECTS_DIR / project_id] if project_id else [d for d in PROJECTS_DIR.iterdir() if d.is_dir()]
            for proj_dir in dirs:
                if not proj_dir.exists() or not proj_dir.is_dir():
                    continue
                proj_id = proj_dir.name
                executions_dir = proj_dir / 'history' / 'executions'
                if not executions_dir.exists():
                    legacy_dir = proj_dir / 'executions'
                    if legacy_dir.exists():
                        executions_dir = legacy_dir
                    else:
                        continue
                for exec_file in executions_dir.glob('*.json'):
                    try:
                        with open(exec_file, 'r', encoding='utf-8') as f:
                            try:
                                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                            except IOError:
                                continue
                            try:
                                execution = json.load(f)
                                status = execution.get('status')
                                if status != 'QUEUED':
                                    continue
                                if backfill_index:
                                    try:
                                        _index_db.add_queued_execution(
                                            execution.get('id') or exec_file.stem,
                                            proj_id,
                                            execution.get('queuedAt') or execution.get('createdAt') or 0,
                                        )
                                    except Exception:
                                        pass
                                if not _check_execution_requirements(execution, worker_capabilities, worker_tags, worker_id):
                                    continue
                                queued_at = execution.get('queuedAt', execution.get('createdAt', 0))
                                scanned.append((queued_at, exec_file, execution, proj_id))
                                checked += 1
                            finally:
                                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except Exception as e:
                        app.logger.warning(f"Error reading execution file {exec_file}: {e}")
                        continue
            return scanned, checked, dirs

        if use_index:
            candidates = _index_db.list_queued(project_id=project_id, limit=50)
            for exec_id, proj_id, queued_at in candidates:
                exec_file = PROJECTS_DIR / proj_id / 'history' / 'executions' / f'{exec_id}.json'
                if not exec_file.exists():
                    legacy_file = PROJECTS_DIR / proj_id / 'executions' / f'{exec_id}.json'
                    if legacy_file.exists():
                        exec_file = legacy_file
                    else:
                        # Stale index row -> self-heal
                        _index_db.remove_queued_execution(exec_id)
                        continue
                try:
                    with open(exec_file, 'r', encoding='utf-8') as f:
                        try:
                            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except IOError:
                            continue
                        try:
                            execution = json.load(f)
                            if execution.get('status') != 'QUEUED':
                                _index_db.remove_queued_execution(exec_id)
                                continue
                            if not _check_execution_requirements(execution, worker_capabilities, worker_tags, worker_id):
                                continue
                            qa = execution.get('queuedAt', execution.get('createdAt', queued_at))
                            queued_runs.append((qa, exec_file, execution, proj_id))
                            total_checked += 1
                        finally:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception as e:
                    app.logger.warning(f"Error reading execution file {exec_file}: {e}")
                    continue
            # If the index is empty/incomplete/stale, self-heal by scanning the
            # project execution folders occasionally. Do not scan on every idle
            # worker poll; with many execution history JSON files that kept the
            # backend at 100% CPU while the queue was empty.
            if not queued_runs:
                scan_key = project_id or '__all__'
                now = time.time()
                last_scan = _EMPTY_QUEUE_DISK_SCAN_AT.get(scan_key, 0)
                if now - last_scan >= _EMPTY_QUEUE_DISK_SCAN_INTERVAL_SECONDS:
                    _EMPTY_QUEUE_DISK_SCAN_AT[scan_key] = now
                    scanned, checked, project_dirs = _scan_queued_from_disk(backfill_index=True)
                    queued_runs.extend(scanned)
                    total_checked += checked
        else:
            # Legacy slow path: full directory scan. Also opportunistically
            # backfills the index so subsequent polls take the fast path.
            queued_runs, total_checked, project_dirs = _scan_queued_from_disk(backfill_index=True)
        
        if not queued_runs:
            app.logger.debug(f"[Server Claim] No QUEUED runs found (checked {total_checked} execution files in {len(project_dirs) if 'project_dirs' in dir() else 0} project(s))")
            return None, None, None
        
        app.logger.debug(f"[Server Claim] Found {len(queued_runs)} QUEUED run(s) (checked {total_checked} files), attempting to claim...")
        
        app.logger.debug(f"[Server Claim] Found {len(queued_runs)} QUEUED run(s) (checked {total_checked} files), attempting to claim...")
        
        # queuedAt ( - FIFO)
        queued_runs.sort(key=lambda x: x[0])
        
        # claim run
        for queued_at, exec_file, execution, proj_id in queued_runs:
            execution_id = execution.get('id')
            if not execution_id:
                continue
            
            # RUNNING
            try:
                with open(exec_file, 'r+', encoding='utf-8') as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    
                    try:
                        # ( )
                        f.seek(0)
                        execution = json.load(f)
                        
                        # QUEUED
                        if execution.get('status') != 'QUEUED':
                            continue
                        
                        # ( )
                        active_runs = get_worker_active_runs_count(worker_id)
                        if active_runs >= max_concurrency:
                            try:
                                pruned = _index_db.prune_stale_running_for_worker(
                                    worker_id,
                                    PROJECTS_DIR,
                                    current_execution_id=None,
                                    mark_orphaned_failed=True,
                                )
                                if pruned:
                                    active_runs = get_worker_active_runs_count(worker_id)
                            except Exception:
                                pass
                            if active_runs >= max_concurrency:
                                continue
                        
                        # RUNNING
                        execution['status'] = 'RUNNING'
                        execution['startedAt'] = time.time()
                        execution['statusUpdatedAt'] = execution['startedAt']
                        execution['workerId'] = worker_id
                        
                        # snapshot worker claim
                        execution['workerName'] = worker_data.get('name', 'Unknown')
                        execution['workerTags'] = worker_data.get('tags', [])
                        
                        f.seek(0)
                        f.truncate()
                        json.dump(execution, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                        try:
                            _index_db.mark_running_execution(
                                execution_id,
                                proj_id,
                                worker_id,
                                execution.get('startedAt') or time.time(),
                            )
                        except Exception:
                            pass
                        
                        app.logger.info(f"Server claimed run {execution_id} for project {proj_id} by worker {worker_id}")
                        execution_for_worker = json.loads(json.dumps(execution))
                        _attach_worker_transfer_payload(execution_for_worker)
                        return execution_id, execution_for_worker, proj_id
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except IOError:
                # , 
                continue
            except Exception as e:
                app.logger.error(f"Error claiming run {execution_id}: {e}")
                continue
        
        return None, None, None
        
    except Exception as e:
        app.logger.error(f"Error in server_claim_next_execution: {e}", exc_info=True)
        return None, None, None


# Worker routes moved to api/worker_routes.py



def server_recover_stuck_executions(max_age_minutes=30, grace_period_minutes=5, heartbeat_ttl_seconds=60,
                                    max_cancel_minutes=5):
    """
 executions
    
 recovery:
 - RUNNING: startedAt > MAX_RUN_TIME → FAILED (timeout)
 - CANCELING: cancelRequestedAt > MAX_CANCEL_TIME → CANCELED (timeout)
    
    Args:
 max_age_minutes: RUNNING execution timeout
 grace_period_minutes: Grace period offline workers recovery
 heartbeat_ttl_seconds: TTL heartbeat ( worker offline heartbeat > TTL)
 max_cancel_minutes: CANCELING execution force cancel
    """
    from services.worker_registry import load_all_workers, is_worker_online
    try:
        from storage.executions_store import (
            update_execution_record,
            get_execution,
            is_final_status,
            validate_status_transition,
        )
    except ImportError:
        from storage.executions_store import (
            update_execution_record,
            get_execution,
            is_final_status,
            validate_status_transition,
        )

    import fcntl
    
    try:
        recovered = 0
        workers = load_all_workers()
        now = time.time()
        
        # RUNNING CANCELING executions
        for proj_dir in PROJECTS_DIR.iterdir():
            if not proj_dir.is_dir():
                continue
            
            project_id = proj_dir.name
            executions_dir = proj_dir / 'history' / 'executions'
            if not executions_dir.exists():
                executions_dir = proj_dir / 'executions'
            if not executions_dir.exists():
                continue
            
            for exec_file in executions_dir.glob('*.json'):
                try:
                    with open(exec_file, 'r+', encoding='utf-8') as f:
                        try:
                            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except IOError:
                            # , 
                            continue
                        
                        try:
                            execution = json.load(f)
                            
                            status = execution.get('status')
                            execution_id = execution.get('id')
                            
                            if is_final_status(status):
                                continue
                            
                            # RUNNING → FAILED (timeout)
                            if status == 'RUNNING':
                                worker_id = execution.get('workerId')
                                started_at = execution.get('startedAt')
                                if not started_at:
                                    continue
                                
                                # execution
                                age_minutes = (now - started_at) / 60
                                
                                # worker
                                worker_online = is_worker_online(worker_id, ttl_seconds=heartbeat_ttl_seconds) if worker_id else False
                                
                                # recovery:
                                # Execution max_age_minutes → FAILED (timeout)
                                should_recover = False
                                recovery_reason = ""
                                
                                if age_minutes > max_age_minutes:
                                    should_recover = True
                                    recovery_reason = f"timeout (age: {age_minutes:.1f} min)"
                                
                                if should_recover:
                                    try:
                                        validate_status_transition('RUNNING', 'FAILED')
                                    except ValueError as e:
                                        app.logger.warning(f"Invalid transition for recovery: {e}")
                                        continue
                                    
                                    # FAILED
                                    execution['status'] = 'FAILED'
                                    execution['finishedAt'] = now
                                    execution['duration'] = int(now - started_at)
                                    execution['statusUpdatedAt'] = now
                                    execution['error'] = f"Execution timeout after {age_minutes:.1f} minutes"
                                    
                                    f.seek(0)
                                    f.truncate()
                                    json.dump(execution, f, indent=2, ensure_ascii=False)
                                    f.flush()
                                    
                                    append_execution_log(execution_id, f"[recovery] Execution timeout after {age_minutes:.1f} minutes, marked as FAILED\n", project_id=project_id)
                                    
                                    app.logger.info(f"Recovered stuck RUNNING execution {execution_id} → FAILED ({recovery_reason}, worker: {worker_id})")
                                    recovered += 1
                            
                            # CANCELING → CANCELED (timeout)
                            elif status == 'CANCELING':
                                cancel_requested_at = execution.get('cancelRequestedAt')
                                if not cancel_requested_at:
                                    continue
                                
                                # CANCELING
                                cancel_age_minutes = (now - cancel_requested_at) / 60
                                
                                if cancel_age_minutes > max_cancel_minutes:
                                    try:
                                        validate_status_transition('CANCELING', 'CANCELED')
                                    except ValueError as e:
                                        app.logger.warning(f"Invalid transition for recovery: {e}")
                                        continue
                                    
                                    # CANCELED
                                    execution['status'] = 'CANCELED'
                                    execution['canceledAt'] = now
                                    execution['cancelReason'] = 'timeout'
                                    execution['statusUpdatedAt'] = now
                                    
                                    # duration startedAt
                                    started_at = execution.get('startedAt')
                                    if started_at:
                                        execution['duration'] = int(now - started_at)
                                        execution['finishedAt'] = now
                                    
                                    f.seek(0)
                                    f.truncate()
                                    json.dump(execution, f, indent=2, ensure_ascii=False)
                                    f.flush()
                                    
                                    append_execution_log(execution_id, f"[recovery] Force canceled after {cancel_age_minutes:.1f} minutes timeout\n", project_id=project_id)
                                    
                                    app.logger.info(f"Recovered stuck CANCELING execution {execution_id} → CANCELED (timeout: {cancel_age_minutes:.1f} min)")
                                    recovered += 1
                        finally:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception as e:
                    app.logger.warning(f"Error checking execution {exec_file}: {e}")
        
        if recovered > 0:
            app.logger.info(f"Recovered {recovered} stuck executions")
        
        return recovered
    except Exception as e:
        app.logger.error(f"Error in server_recover_stuck_executions: {e}", exc_info=True)
        return 0


# ============================================================================
# BACKGROUND TASKS
# ============================================================================

def start_recovery_task():
    """ recovery executions"""
    def recovery_worker():
        import time
        def _recovery_interval():
            try:
                from utils.scheduler_settings import get_interval
                return get_interval('execution_recovery_interval')
            except Exception:
                try:
                    return max(60, int(os.environ.get('EXECUTION_RECOVERY_INTERVAL') or 300))
                except (TypeError, ValueError):
                    return 300
        while True:
            try:
                time.sleep(_recovery_interval())
                server_recover_stuck_executions(
                    max_age_minutes=30,
                    grace_period_minutes=5,
                    heartbeat_ttl_seconds=60,
                    max_cancel_minutes=5
                )
            except Exception as e:
                app.logger.error(f"Error in recovery task: {e}", exc_info=True)
    
    recovery_thread = threading.Thread(target=recovery_worker, daemon=True)
    recovery_thread.start()
    app.logger.info("Recovery task started")


# recovery task 
start_recovery_task()

# legacy workers (default worker — POST /api/worker/register)
try:
    try:
        from services.worker_registry import migrate_legacy_workers
    except ImportError:
        from services.worker_registry import migrate_legacy_workers
    migrate_legacy_workers()
except Exception as e:
    app.logger.error(f"Error initializing workers: {e}", exc_info=True)

# Initialize the SQLite index (libSQL-compatible, WAL) used to keep
# /api/worker/claim and token verification off the JSON scan hot path.
try:
    from storage import index_db as _index_db
    from services.worker_registry import WORKERS_DIR as _WORKERS_DIR
    _index_db.backfill_if_empty(PROJECTS_DIR, _WORKERS_DIR)
except Exception as e:
    app.logger.warning(f"index_db init/backfill failed (falling back to JSON scans): {e}")


# Project Storage directories are created on demand per project

# ============================================================================
# GLOBAL SECRETS API ENDPOINTS
# ============================================================================

# Phase 3 refactor: global secrets and encryption-key routes moved to
# backend/api/global_secrets_routes.py.


# ============================================================================
# PROJECT SECRETS API ENDPOINTS (unchanged)
# ============================================================================

# Project secret CRUD routes moved to api/secrets_routes.py


# ============================================================================
# CONNECTION SECRETS API ( Hosts & Groups)
# ============================================================================

# Project secret meta route moved to api/secrets_routes.py




# Phase 4 refactor: inventory groups/hosts + connection-secret routes moved
# to backend/api/inventory_groups_hosts_routes.py.

# Finalize /api/v2 auto-proxies: every v1 route is now on the URL map, so
# scan and mount /api/v2/* mirrors for the flask-smorest spec. Safe no-op
# if the pilot module didn't initialize.
try:
    from api_v2 import finalize_api_v2 as _finalize_api_v2
    _finalize_api_v2(app)
except Exception as _v2_final_err:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        f"/api/v2 auto-proxy finalize skipped: {_v2_final_err}"
    )

if __name__ == '__main__':

    
    # ( )
    _initialize_projects()
    
    # : , admin
    try:
        seed_default_roles(role_service, permission_service, DATA_DIR)
        # admin ( )
        seed_default_user(user_service, role_service, DATA_DIR)
        app.logger.info("Authentication system initialized successfully")
    except Exception as e:
        app.logger.error(f"Error initializing authentication system: {e}", exc_info=True)
    
    # retention policy 
    apply_retention_policy()
    
    app.logger.info("Starting web service...")
    app.logger.info(f"BASE_DIR: {BASE_DIR}")
    app.logger.info(f"PROJECTS: {PROJECTS_DIR}")
    app.logger.info("Note: All data (executions, drafts, vars, configs) are now project-scoped in Project Storage")
    
    # debug 
    settings = load_execution_settings()
    debug_mode = os.getenv('FLASK_DEBUG', str(settings.get('debug_mode', False))).lower()
    if debug_mode == 'true' or debug_mode == '1':
        debug_mode = True
    else:
        debug_mode = False
    
    initial_log_level = os.getenv('FLASK_LOG_LEVEL', settings.get('log_level', 'INFO'))
    set_log_level(initial_log_level)
    
    # max_log_size_mb 
    # ( get_logging_settings())
    max_log_size_mb = settings.get('max_log_size_mb', 10)
    app.logger.debug(f"Log rotation configured: maxBytes={max_log_size_mb}MB, backupCount=5")
    
    max_upload_size_mb = settings.get('max_upload_size_mb', 10)
    if max_upload_size_mb < 1:
        max_upload_size_mb = 1  # 1MB
    elif max_upload_size_mb > 1024:
        max_upload_size_mb = 1024  # 1GB
    app.config['MAX_CONTENT_LENGTH'] = max_upload_size_mb * 1024 * 1024
    
    app.logger.info(f"Debug mode: {'ENABLED' if debug_mode else 'DISABLED'}")
    app.logger.info(f"Log level: {initial_log_level.upper()}")
    app.logger.info(f"Max upload file size: {max_upload_size_mb}MB")
    app.logger.info("Open in browser: http://localhost:5000")
    
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)

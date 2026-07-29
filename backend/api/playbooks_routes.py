"""Playbook API routes blueprint.

Moved from ``backend/app.py`` during the phased refactor. URLs, methods,
view-function names, response shapes, and auth decorators are preserved
verbatim. All references to app.py globals (playbook_storage, load_projects,
list_executions, etc.) are resolved lazily through ``_LazyProxy`` so this
module can be imported before app.py finishes initializing.
"""
from __future__ import annotations

import os
import re
import json
import sys
import uuid
import time
import shutil
import zipfile
import tarfile
import threading
import subprocess
import warnings
from io import StringIO, BytesIO
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytz
from croniter import croniter
from ruamel.yaml import YAML

from flask import Blueprint, jsonify, request, Response, stream_with_context, send_file

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth


bp = Blueprint("playbooks_api", __name__)


DOCKER_MODULE_KEYS = {
    'community.docker.docker_login',
    'community.docker.docker_image',
    'community.docker.docker_container',
    'community.docker.docker_network',
    'community.docker.docker_volume',
    'docker_login',
    'docker_image',
    'docker_container',
    'docker_network',
    'docker_volume',
}
DOCKER_DEPS_TASK_PREFIX = 'OpenSible preflight | Ensure Python deps for community.docker'


def _task_uses_docker_module(task) -> bool:
    if not isinstance(task, dict):
        return False
    for key, value in task.items():
        if isinstance(key, str) and (key in DOCKER_MODULE_KEYS or key.startswith('community.docker.')):
            return True
        if isinstance(value, (dict, list)) and _object_uses_docker_module(value):
            return True
    return False


def _object_uses_docker_module(value) -> bool:
    if isinstance(value, dict):
        return any(
            (isinstance(key, str) and (key in DOCKER_MODULE_KEYS or key.startswith('community.docker.')))
            or _object_uses_docker_module(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_object_uses_docker_module(item) for item in value)
    if isinstance(value, str):
        return 'community.docker.' in value or any(f'{key}:' in value for key in DOCKER_MODULE_KEYS)
    return False


def _task_installs_docker_python_deps(task) -> bool:
    if not isinstance(task, dict):
        return False
    if str(task.get('name') or '').startswith(DOCKER_DEPS_TASK_PREFIX):
        return True
    for module_name in ('ansible.builtin.apt', 'apt', 'ansible.builtin.package', 'package'):
        args = task.get(module_name)
        if not isinstance(args, dict):
            continue
        names = args.get('name')
        if isinstance(names, str):
            names_text = names
        elif isinstance(names, list):
            names_text = ' '.join(str(name) for name in names)
        else:
            names_text = ''
        if 'python3-docker' in names_text and 'python3-requests' in names_text:
            return True
    return False


def _docker_python_deps_tasks():
    return [
        {
            'name': f'{DOCKER_DEPS_TASK_PREFIX} (Debian)',
            'become': True,
            'ansible.builtin.apt': {
                'name': ['python3-docker', 'python3-requests'],
                'state': 'present',
                'update_cache': True,
            },
            'when': "ansible_os_family == 'Debian'",
        },
        {
            'name': f'{DOCKER_DEPS_TASK_PREFIX} (RedHat)',
            'become': True,
            'ansible.builtin.package': {
                'name': ['python3-docker', 'python3-requests'],
                'state': 'present',
            },
            'when': "ansible_os_family == 'RedHat'",
        },
    ]


def _ensure_docker_python_deps_in_yaml(yaml_content: str) -> str:
    """Patch per-run YAML so saved old Docker templates still run safely."""
    if 'community.docker.' not in yaml_content and not any(f'{key}:' in yaml_content for key in DOCKER_MODULE_KEYS):
        return yaml_content

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    try:
        parsed = yaml.load(yaml_content)
    except Exception as e:
        app.logger.warning(f"[api_run_playbook] Could not inspect YAML for Docker dependency preflight: {e}")
        return yaml_content

    plays = [parsed] if isinstance(parsed, dict) else [p for p in (parsed or []) if isinstance(p, dict)]
    changed = False
    for play in plays:
        tasks = play.get('tasks')
        if not isinstance(tasks, list) or not any(_task_uses_docker_module(task) for task in tasks):
            continue
        pre_tasks = play.get('pre_tasks') if isinstance(play.get('pre_tasks'), list) else []
        if any(_task_installs_docker_python_deps(task) for task in pre_tasks + tasks):
            continue

        first_docker_idx = next((idx for idx, task in enumerate(tasks) if _task_uses_docker_module(task)), 0)
        for offset, dep_task in enumerate(_docker_python_deps_tasks()):
            tasks.insert(first_docker_idx + offset, dep_task)
        changed = True

    if not changed:
        return yaml_content

    out = StringIO()
    yaml.dump(parsed, out)
    return out.getvalue()


def _app_module():
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "playbook_storage"):
            return mod
    import app as _a  # noqa
    return _a


class _LazyProxy:
    """Deferred attribute lookup against the app.py module.

    Route handlers reference many app.py globals (functions, module-level
    singletons, classes). Because this blueprint is imported before app.py
    is fully executed, we install a proxy per-name. The proxy forwards
    calls, attribute access, item access, and iteration to the real object
    resolved at first use.
    """
    __slots__ = ("_lp_name",)

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_lp_name", name)

    def _lp_real(self):
        return getattr(_app_module(), object.__getattribute__(self, "_lp_name"))

    def __call__(self, *args, **kwargs):
        return self._lp_real()(*args, **kwargs)

    def __getattr__(self, item):
        return getattr(self._lp_real(), item)

    def __setattr__(self, item, value):
        setattr(self._lp_real(), item, value)

    def __getitem__(self, item):
        return self._lp_real()[item]

    def __iter__(self):
        return iter(self._lp_real())

    def __bool__(self):
        try:
            return bool(self._lp_real())
        except Exception:
            return False

    def __repr__(self):
        return f"<_LazyProxy {object.__getattribute__(self, '_lp_name')}>"


_APP_NAMES = (
    "app",
    "TEMP_DIR",
    "playbook_storage",
    "playbook_parser",
    "playbook_generator",
    "playbook_scheduler",
    "load_projects",
    "find_inventory_files_for_playbook",
    "get_inventory_groups",
    "scan_roles_storage",
    "create_execution_record",
    "list_executions",
    "PlaybookValidator",
    "PlaybookParseError",
    "PlaybookScheduler",
    "get_project_dir",
    "get_project_inventory_file",
    "get_project_generated_playbook_path",
    "load_execution_settings",
    "resolve_ansible_config_path",
)

for _n in _APP_NAMES:
    globals().setdefault(_n, _LazyProxy(_n))
del _n


# ---------------------------------------------------------------------------
# Routes (verbatim from app.py, @app.route -> @bp.route)
# ---------------------------------------------------------------------------
@bp.route('/api/projects/<project_id>/playbooks', methods=['GET'])
@require_auth
def api_list_playbooks(project_id):
    """API: playbooks """
    try:
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        playbooks = playbook_storage.list_playbooks(project_id)
        
        # inventory roles 
        project_inventory_file = get_project_inventory_file(project_id)
        inventory_files = [str(project_inventory_file)] if project_inventory_file.exists() else []
        inventory_groups = get_inventory_groups(inventory_files) if inventory_files else {}
        
        # roles
        roles_tree = scan_roles_storage(project_id)
        available_roles = []
        def extract_roles(node):
            if node.get('type') == 'role':
                role_path = node.get('path', '')
                if role_path:
                    available_roles.append(role_path)
                else:
                    role_id = node.get('id', '')
                    if role_id:
                        available_roles.append(role_id)
                    else:
                        available_roles.append(node.get('name', ''))
            for child in node.get('children', []):
                extract_roles(child)
        for role_node in roles_tree:
            extract_roles(role_node)
        
        # playbook 
        validator = PlaybookValidator(
            inventory_groups=inventory_groups,
            available_roles=available_roles
        )
        
        for playbook in playbooks:
            try:
                # playbook 
                full_playbook = playbook_storage.get_playbook(project_id, playbook['id'])
                if full_playbook:
                    validation_result = validator.validate(full_playbook)
                    # : True playbook_can_run, False
                    playbook['validation_status'] = validation_result['summary']['playbook_can_run']
                    playbook['validation_errors_count'] = validation_result['summary']['total_errors']
                else:
                    playbook['validation_status'] = False
                    playbook['validation_errors_count'] = 0
            except Exception as e:
                app.logger.warning(f"Error validating playbook {playbook['id']}: {e}")
                playbook['validation_status'] = False
                playbook['validation_errors_count'] = 0
        
        sort = request.args.get('sort', 'updated_at')
        order = request.args.get('order', 'desc')
        
        if sort == 'name':
            playbooks.sort(key=lambda x: x.get('name', '').lower(), reverse=(order == 'desc'))
        elif sort == 'created_at':
            playbooks.sort(key=lambda x: x.get('created_at', ''), reverse=(order == 'desc'))
        else:  # updated_at
            playbooks.sort(key=lambda x: x.get('updated_at', ''), reverse=(order == 'desc'))
        
        return jsonify({
            'success': True,
            'playbooks': playbooks,
            'total': len(playbooks)
        })
    except Exception as e:
        app.logger.error(f"Error listing playbooks for project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/playbooks', methods=['POST'])
@require_auth
def api_create_playbook(project_id):
    """API: playbook"""
    try:
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        data = request.json or {}
        name = data.get('name', '').strip()
        
        if not name:
            return jsonify({'success': False, 'error': 'Playbook name is required'}), 400
        
        if playbook_storage.check_name_conflict(project_id, name):
            return jsonify({'success': False, 'error': f'Playbook with name "{name}" already exists'}), 409
        
        description = data.get('description', '').strip()
        playbook = playbook_storage.create_playbook(project_id, name, description)
        
        if playbook:
            return jsonify({'success': True, 'playbook': playbook}), 201
        else:
            return jsonify({'success': False, 'error': 'Failed to create playbook'}), 500
            
    except Exception as e:
        app.logger.error(f"Error creating playbook for project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/playbooks/<playbook_id>', methods=['GET'])
@require_auth
def api_get_playbook(project_id, playbook_id):
    """API: playbook ID"""
    try:
        playbook = playbook_storage.get_playbook(project_id, playbook_id)
        
        if not playbook:
            return jsonify({'success': False, 'error': 'Playbook not found'}), 404
        
        return jsonify({'success': True, 'playbook': playbook})
    except Exception as e:
        app.logger.error(f"Error getting playbook {playbook_id} for project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/playbooks/<playbook_id>', methods=['PUT'])
@require_auth
def api_update_playbook(project_id, playbook_id):
    """API: playbook"""
    try:
        # playbook
        existing_playbook = playbook_storage.get_playbook(project_id, playbook_id)
        if not existing_playbook:
            return jsonify({'success': False, 'error': 'Playbook not found'}), 404
        
        data = request.json or {}
        
        if 'name' in data:
            new_name = data['name'].strip()
            if not new_name:
                return jsonify({'success': False, 'error': 'Playbook name cannot be empty'}), 400
            
            # ( playbook)
            if playbook_storage.check_name_conflict(project_id, new_name, exclude_playbook_id=playbook_id):
                return jsonify({'success': False, 'error': f'Playbook with name "{new_name}" already exists'}), 409
            
            existing_playbook['name'] = new_name
        
        if 'description' in data:
            existing_playbook['description'] = data['description'].strip()
        
        if 'plays' in data:
            existing_playbook['plays'] = data['plays']
        
        if 'tags' in data:
            existing_playbook['tags'] = data['tags']
        if 'metadata' in data:
            if 'metadata' not in existing_playbook:
                existing_playbook['metadata'] = {}
            # , 
            existing_playbook['metadata'].update(data['metadata'])
        if 'tag' in data:
            existing_playbook['tag'] = data['tag']
        
        # disabled 
        if 'disabled' in data:
            if 'metadata' not in existing_playbook:
                existing_playbook['metadata'] = {}
            existing_playbook['metadata']['disabled'] = data['disabled']
            existing_playbook['disabled'] = data['disabled']
        
        if playbook_storage.save_playbook(project_id, existing_playbook):
            return jsonify({'success': True, 'playbook': existing_playbook})
        else:
            return jsonify({'success': False, 'error': 'Failed to save playbook'}), 500
            
    except Exception as e:
        app.logger.error(f"Error updating playbook {playbook_id} for project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/playbooks/<playbook_id>', methods=['DELETE'])
@require_auth
def api_delete_playbook(project_id, playbook_id):
    """API: playbook"""
    try:
        # playbook
        if not playbook_storage.playbook_exists(project_id, playbook_id):
            return jsonify({'success': False, 'error': 'Playbook not found'}), 404
        
        # TODO: executions ( )
        
        if playbook_storage.delete_playbook(project_id, playbook_id):
            return jsonify({'success': True}), 204
        else:
            return jsonify({'success': False, 'error': 'Failed to delete playbook'}), 500
            
    except Exception as e:
        app.logger.error(f"Error deleting playbook {playbook_id} for project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/playbooks/upload', methods=['POST'])
@require_auth
def api_upload_playbook(project_id):
    """API: YAML playbook """
    try:
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if not (file.filename.endswith('.yml') or file.filename.endswith('.yaml')):
            return jsonify({'success': False, 'error': 'Only .yml and .yaml files are allowed'}), 400
        
        yaml_content = file.read().decode('utf-8')
        
        # YAML 
        is_valid, error_message = playbook_parser.validate_yaml_structure(yaml_content)
        if not is_valid:
            return jsonify({'success': False, 'error': error_message}), 400
        
        # YAML
        playbook_name = request.form.get('name') or file.filename.rsplit('.', 1)[0]
        try:
            playbook = playbook_parser.parse(yaml_content, playbook_name)
        except PlaybookParseError as e:
            return jsonify({'success': False, 'error': str(e)}), 400
        
        if playbook_storage.check_name_conflict(project_id, playbook['name']):
            return jsonify({'success': False, 'error': f'Playbook with name "{playbook["name"]}" already exists'}), 409
        
        # inventory roles 
        project_inventory_file = get_project_inventory_file(project_id)
        inventory_files = [str(project_inventory_file)] if project_inventory_file.exists() else []
        inventory_groups = get_inventory_groups(inventory_files) if inventory_files else {}
        
        # roles
        roles_tree = scan_roles_storage(project_id)
        available_roles = []
        def extract_roles(node):
            if node.get('type') == 'role':
                # (pack/role), 
                role_path = node.get('path', '')
                if role_path:
                    available_roles.append(role_path)
                else:
                    # Fallback: path , id (pack/role)
                    role_id = node.get('id', '')
                    if role_id:
                        available_roles.append(role_id)
                    else:
                        # fallback: 
                        available_roles.append(node.get('name', ''))
            for child in node.get('children', []):
                extract_roles(child)
        for role_node in roles_tree:
            extract_roles(role_node)
        
        # playbook
        validator = PlaybookValidator(
            inventory_groups=inventory_groups,
            available_roles=available_roles
        )
        validation_result = validator.validate(playbook)
        
        # playbook
        playbook_id = str(uuid.uuid4())
        playbook['id'] = playbook_id
        playbook['project_id'] = project_id
        
        if not playbook_storage.save_playbook(project_id, playbook):
            return jsonify({'success': False, 'error': 'Failed to save playbook'}), 500
        
        warnings = []
        for play_validation in validation_result['plays']:
            warnings.extend(play_validation['warnings'])
        warnings.extend(validation_result['playbook']['warnings'])
        
        return jsonify({
            'success': True,
            'playbook': playbook,
            'warnings': [w for w in warnings]
        }), 201
        
    except Exception as e:
        app.logger.error(f"Error uploading playbook for project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/playbooks/<playbook_id>/clone', methods=['POST'])
@require_auth
def api_clone_playbook(project_id, playbook_id):
    """API: playbook"""
    try:
        data = request.json or {}
        new_name = data.get('name', '').strip()
        
        if not new_name:
            return jsonify({'success': False, 'error': 'New playbook name is required'}), 400
        
        if playbook_storage.check_name_conflict(project_id, new_name):
            return jsonify({'success': False, 'error': f'Playbook with name "{new_name}" already exists'}), 409
        
        new_description = data.get('description', '').strip()
        new_playbook = playbook_storage.clone_playbook(project_id, playbook_id, new_name, new_description)
        
        if new_playbook:
            return jsonify({'success': True, 'playbook': new_playbook}), 201
        else:
            return jsonify({'success': False, 'error': 'Failed to clone playbook'}), 500
            
    except Exception as e:
        app.logger.error(f"Error cloning playbook {playbook_id} for project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/playbooks/<playbook_id>/download', methods=['GET'])
@require_auth
def api_download_playbook(project_id, playbook_id):
    """API: playbook YAML"""
    try:
        playbook = playbook_storage.get_playbook(project_id, playbook_id)
        
        if not playbook:
            return jsonify({'success': False, 'error': 'Playbook not found'}), 404
        
        # YAML
        yaml_content = playbook_generator.generate(playbook)
        
        # response YAML
        playbook_name = playbook.get('name', 'playbook')
        filename = f"{playbook_name}.yml"
        
        response = Response(
            yaml_content,
            mimetype='text/yaml',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"'
            }
        )
        
        return response
        
    except Exception as e:
        app.logger.error(f"Error downloading playbook {playbook_id} for project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/playbooks/<playbook_id>/validate', methods=['POST'])
@require_auth
def api_validate_playbook(project_id, playbook_id):
    """API: playbook"""
    try:
        data = request.get_json(silent=True) or {}
        playbook = data.get('playbook')
        inventory_groups = data.get('inventory_groups', {})
        available_roles = data.get('available_roles', [])

        # If caller didn't pass the playbook body, load it from storage so the
        # request doesn't 500 (used by the Preview dialog which sends no body).
        if not playbook:
            playbook = playbook_storage.get_playbook(project_id, playbook_id)
        if not playbook:
            return jsonify({'success': False, 'error': 'Playbook not found'}), 404

        validator = PlaybookValidator(
            inventory_groups=inventory_groups,
            available_roles=available_roles
        )

        validation_result = validator.validate(playbook)

        return jsonify({
            'success': True,
            'validation': validation_result
        })

    except Exception as e:
        app.logger.error(f"Error validating playbook {playbook_id} for project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500



@bp.route('/api/projects/<project_id>/playbooks/<playbook_id>/preview', methods=['POST'])
@require_auth
def api_preview_playbook(project_id, playbook_id):
    """API: YAML playbook"""
    try:
        data = request.json or {}
        playbook = data.get('playbook')
        
        if not playbook:
            return jsonify({'success': False, 'error': 'Playbook data is required'}), 400
        
        # YAML
        yaml_content = playbook_generator.generate(playbook)
        
        return jsonify({
            'success': True,
            'yaml': yaml_content
        })
        
    except Exception as e:
        app.logger.error(f"Error previewing playbook {playbook_id} for project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/playbooks/<playbook_id>/yaml', methods=['PUT'])
@require_auth
def api_update_playbook_yaml(project_id, playbook_id):
    """Replace the playbook's plays by parsing a raw YAML body."""
    try:
        existing = playbook_storage.get_playbook(project_id, playbook_id)
        if not existing:
            return jsonify({'success': False, 'error': 'Playbook not found'}), 404
        data = request.json or {}
        yaml_content = data.get('yaml', '')
        if not isinstance(yaml_content, str) or not yaml_content.strip():
            return jsonify({'success': False, 'error': 'yaml body is required'}), 400
        is_valid, error_message = playbook_parser.validate_yaml_structure(yaml_content)
        if not is_valid:
            return jsonify({'success': False, 'error': error_message}), 400
        try:
            parsed = playbook_parser.parse(yaml_content, existing.get('name'))
        except PlaybookParseError as e:
            return jsonify({'success': False, 'error': str(e)}), 400
        existing['plays'] = parsed.get('plays', [])
        if playbook_storage.save_playbook(project_id, existing):
            return jsonify({'success': True, 'playbook': existing})
        return jsonify({'success': False, 'error': 'Failed to save playbook'}), 500
    except Exception as e:
        app.logger.error(f"Error updating playbook YAML {playbook_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500



@bp.route('/api/projects/<project_id>/playbooks/reorder', methods=['POST'])
@require_auth
def api_reorder_playbooks(project_id):
    """API: playbooks"""
    try:
        data = request.json or {}
        playbook_orders = data.get('playbook_orders', [])
        
        if not playbook_orders:
            return jsonify({'success': False, 'error': 'playbook_orders is required'}), 400
        
        # playbook
        updated_count = 0
        for item in playbook_orders:
            playbook_id = item.get('playbook_id')
            order = item.get('order')
            
            if playbook_id is None or order is None:
                continue
            
            # playbook
            playbook = playbook_storage.get_playbook(project_id, playbook_id)
            if not playbook:
                continue
            
            # order 
            if 'metadata' not in playbook:
                playbook['metadata'] = {}
            playbook['metadata']['order'] = order
            
            # playbook (save_playbook updated_at, )
            if playbook_storage.save_playbook(project_id, playbook):
                updated_count += 1
        
        return jsonify({
            'success': True,
            'updated_count': updated_count
        })
        
    except Exception as e:
        app.logger.error(f"Error reordering playbooks for project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/playbooks/<playbook_id>/run', methods=['POST'])
@require_auth
def api_run_playbook(project_id, playbook_id):
    """API: playbook - QUEUED run"""
    try:
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        # playbook
        playbook = playbook_storage.get_playbook(project_id, playbook_id)
        if not playbook:
            return jsonify({'success': False, 'error': 'Playbook not found'}), 404
        
        data = request.json or {}
        inventory_files = data.get('inventory_files')
        ansible_config = data.get('ansible_config')
        # Execution parameters (optional)
        check_mode = data.get('check_mode', False)
        verbosity = (data.get('verbosity') or '').strip() or None  # '', '-v', '-vv', '-vvv', '-vvvv'
        forks = data.get('forks')
        force_handlers = data.get('force_handlers', False)
        connection_timeout = data.get('connection_timeout')
        play_name = (data.get('play_name') or '').strip() or None
        # Lifecycle stage: init | validate | plan | apply (default apply = normal run)
        stage = (data.get('stage') or 'apply').strip().lower()
        if stage not in ('init', 'validate', 'plan', 'apply'):
            stage = 'apply'

        # Derived flags per stage (worker branches on these)
        syntax_check = data.get('syntax_check', False) or (stage == 'validate')
        galaxy_install = data.get('galaxy_install', False) or (stage == 'init')
        diff_mode = data.get('diff_mode', False) or (stage == 'plan')
        if stage == 'plan':
            check_mode = True
        
        # inventory_files , / 
        if not inventory_files:
            found_inventory_files = find_inventory_files_for_playbook(project_id, playbook)
            if found_inventory_files:
                inventory_files = found_inventory_files
                app.logger.info(f"[api_run_playbook] Auto-detected inventory files from playbook: {inventory_files}")
            else:
                # Fallback default inventory
                inventory_files = ['inventory.yml']
                app.logger.info(f"[api_run_playbook] No inventory files found for playbook hosts/groups, using default: {inventory_files}")
        else:
            app.logger.info(f"[api_run_playbook] Using inventory files from request: {inventory_files}")
        
        # project_dir 
        project_dir = get_project_dir(project_id)
        
        app.logger.info(f"[api_run_playbook] ========== Playbook Run Request ==========")
        app.logger.info(f"[api_run_playbook] Project ID: {project_id}")
        app.logger.info(f"[api_run_playbook] Playbook ID: {playbook_id}")
        app.logger.info(f"[api_run_playbook] Playbook Name: {playbook.get('name', 'Unknown')}")
        app.logger.info(f"[api_run_playbook] Inventory Files: {inventory_files}")
        app.logger.info(f"[api_run_playbook] Ansible Config: {ansible_config}")
        app.logger.info(f"[api_run_playbook] Project Directory: {project_dir}")
        
        repo_dir = project_dir / 'repo'
        inventory_paths = []
        for inv_file in inventory_files:
            # repo (, "inventories/invent.yaml")
            # (, "inventory.yml")
            if inv_file.startswith('inventories/') or ('.' in inv_file and '/' in inv_file):
                inv_path = repo_dir / inv_file
            elif inv_file == 'inventory.yml':
                inv_path = repo_dir / 'inventory.yml'
            else:
                # inventories 
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
                inventory_paths.append(str(inv_path.resolve()))
            else:
                inventory_paths.append(f"{inv_file} (not found at {inv_path})")
        app.logger.info(f"[api_run_playbook] Inventory File Paths: {inventory_paths}")
        
        # ansible.cfg
        ansible_config_path = None
        if ansible_config:
            ansible_config_path = resolve_ansible_config_path(project_id, ansible_config)
        
        if ansible_config_path and ansible_config_path.exists():
            app.logger.info(f"[api_run_playbook] Ansible Config Path: {ansible_config_path.resolve()}")
        else:
            app.logger.warning(f"[api_run_playbook] Ansible Config Path: {ansible_config_path} (not found, will use default)")
        
        app.logger.info(f"[api_run_playbook] ==========================================")
        
        # ansible_config , 
        if not ansible_config:
            app.logger.error(f"[api_run_playbook] ansible_config not provided in request")
            return jsonify({'success': False, 'error': 'ansible_config is required'}), 400
        
        # YAML playbook — prefer the raw YAML on disk for YAML-only playbooks
        # (templates, git-synced, imports) so we execute exactly what the user
        # authored. Fall back to generating from the structured model.
        yaml_content = playbook.get('_yaml_raw') if isinstance(playbook, dict) else None
        if not (isinstance(yaml_content, str) and yaml_content.strip()):
            try:
                yaml_content = playbook_generator.generate(playbook)
            except Exception as e:
                app.logger.error(f"Error generating YAML from playbook {playbook_id}: {e}")
                return jsonify({'success': False, 'error': f'Failed to generate playbook YAML: {str(e)}'}), 500

        if not yaml_content or not isinstance(yaml_content, str) or not yaml_content.strip():
            return jsonify({'success': False, 'error': 'Playbook is empty'}), 400

        yaml_content = _ensure_docker_python_deps_in_yaml(yaml_content)

        
        # repo inventory 
        repo_dir = project_dir / 'repo'
        
        # execution record QUEUED ( execution, execution_id)
        execution_id = None
        settings = load_execution_settings()
        if settings.get('save_history', True):
            # inventory snapshot
            inventory_snapshot = {'groups': []}
            try:
                # inventory 
                inv_files_list = []
                for inv_file in inventory_files:
                    # repo (, "inventories/invent.yaml")
                    # (, "inventory.yml")
                    if inv_file.startswith('inventories/') or ('.' in inv_file and '/' in inv_file):
                        inv_path = repo_dir / inv_file
                    elif inv_file == 'inventory.yml':
                        inv_path = repo_dir / 'inventory.yml'
                    else:
                        # inventories 
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
                app.logger.warning(f"Error creating inventory snapshot: {e}")
            
            # hosts playbook plays
            all_hosts = set()
            for play in playbook.get('plays', []):
                hosts_value = play.get('hosts', '')
                if hosts_value and hosts_value != 'all':
                    if isinstance(hosts_value, list):
                        for host in hosts_value:
                            if host and host != 'all':
                                all_hosts.add(str(host).strip())
                    elif isinstance(hosts_value, str):
                        if ',' in hosts_value:
                            all_hosts.update(h.strip() for h in hosts_value.split(','))
                        else:
                            all_hosts.add(hosts_value.strip())
                    else:
                        all_hosts.add(str(hosts_value).strip())
            
            # roles playbook plays
            all_roles = []
            for play in playbook.get('plays', []):
                for role in play.get('roles', []):
                    role_name = role.get('role_name') or role.get('role', '')
                    if role_name and role_name not in all_roles:
                        all_roles.append(role_name)
            
            # execution_id , playbook execution record
            # race condition: worker claim execution runParams
            execution_id = str(uuid.uuid4())
            
            # playbook runtime/generated_playbooks/<execution_id>.yml
            generated_playbook_path = get_project_generated_playbook_path(project_id, execution_id)
            with open(generated_playbook_path, 'w', encoding='utf-8') as f:
                f.write(yaml_content)
            temp_playbook = generated_playbook_path
            
            # play ( worker: Mitogen strategy_plugins)
            plays_list = playbook.get('plays', [])
            strategy = plays_list[0].get('strategy', 'linear') if plays_list else 'linear'
            # vault_id play vars_files ( worker: --vault-password-file)
            vault_id = None
            for p in plays_list:
                if p.get('vars_files') and p.get('vault_id'):
                    vault_id = p.get('vault_id')
                    break

            # runParams playbook 
            run_params = {
                'temp_playbook': str(temp_playbook),
                'inventory_files': inventory_files,
                'ansible_config': ansible_config,
                'project_dir': str(get_project_dir(project_id)),
                'check_mode': bool(check_mode),
                'verbosity': verbosity,
                'forks': int(forks) if forks is not None and str(forks).strip() != '' else None,
                'force_handlers': bool(force_handlers),
                'connection_timeout': int(connection_timeout) if connection_timeout is not None and str(connection_timeout).strip() != '' else None,
                'strategy': strategy,
                'vault_id': vault_id,
                'stage': stage,
                'syntax_check': bool(syntax_check),
                'galaxy_install': bool(galaxy_install),
                'diff_mode': bool(diff_mode),
            }

            # Optional worker targeting: pin this run to a specific worker.
            _target_worker = (data.get('worker_id') or data.get('target_worker_id') or '').strip() if isinstance(data.get('worker_id') or data.get('target_worker_id'), str) else (data.get('worker_id') or data.get('target_worker_id'))
            if _target_worker:
                run_params['target_worker_id'] = _target_worker
                reqs = run_params.get('requirements') or {}
                reqs['worker_id'] = _target_worker
                run_params['requirements'] = reqs
            
            # runParams, worker'
            app.logger.info(f"[api_run_playbook] Execution ID: {execution_id}")
            app.logger.info(f"[api_run_playbook] Run Parameters (runParams):")
            app.logger.info(f"[api_run_playbook]   - temp_playbook: {run_params['temp_playbook']}")
            app.logger.info(f"[api_run_playbook]   - inventory_files: {run_params['inventory_files']}")
            app.logger.info(f"[api_run_playbook]   - ansible_config: {run_params['ansible_config']}")
            app.logger.info(f"[api_run_playbook]   - project_dir: {run_params['project_dir']}")
            
            # execution record runParams ( race condition)
            _cu = getattr(request, "current_user", None) or {}
            _triggered_by = _cu.get("username") or _cu.get("email") or _cu.get("user_id") or ""
            execution_data = {
                'playbookName': playbook.get('name', 'playbook'),
                'playbookId': playbook_id,
                'mode': 'PER_GROUP',
                'inventorySnapshot': inventory_snapshot,
                'selectionSnapshot': {
                    'playbookId': playbook_id,
                    'playbookName': playbook.get('name', 'playbook')
                },
                'stats': {
                    'hostsTargeted': len(all_hosts) if all_hosts else 0,
                    'totalRoleExecutions': len(all_roles)
                },
                'warnings': [],
                'status': 'QUEUED',
                'runParams': run_params,  # runParams
                'play_name': play_name or f"[{stage}] {playbook.get('name', 'playbook')}",
                'triggeredBy': _triggered_by,
                'triggeredByUserId': _cu.get("user_id") or "",
            }

            
            # execution record execution_id runParams
            execution_id_created = create_execution_record(execution_data, project_id=project_id, execution_id=execution_id)
            if execution_id_created:
                app.logger.debug(f"Created QUEUED execution record: {execution_id_created} for playbook {playbook_id} with runParams")
            else:
                app.logger.error(f"Failed to create execution record for playbook {playbook_id}")
                execution_id = None
                # Fallback: execution_id , 
                temp_playbook = TEMP_DIR / f'playbook_{uuid.uuid4().hex[:8]}.yaml'
                temp_playbook.parent.mkdir(exist_ok=True)
                with open(temp_playbook, 'w', encoding='utf-8') as f:
                    f.write(yaml_content)
        
        response_data = {
            'success': True,
            'message': f'Playbook "{playbook.get("name", "playbook")}" queued',
            'status': 'queued',
            'executionId': execution_id
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        app.logger.error(f"Error queuing playbook {playbook_id} for project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e), 'status': 'error'}), 500


@bp.route('/api/projects/<project_id>/playbooks/<playbook_id>/schedule', methods=['GET'])
@require_auth
def api_get_playbook_schedule(project_id, playbook_id):
    """API: schedule playbook"""
    try:
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        # playbook
        if not playbook_storage.playbook_exists(project_id, playbook_id):
            return jsonify({'success': False, 'error': 'Playbook not found'}), 404
        
        # schedule
        schedule = playbook_storage.get_schedule(project_id, playbook_id)
        
        if schedule:
            return jsonify({
                'success': True,
                'schedule': schedule
            })
        else:
            # Schedule - success: false ( 404, )
            return jsonify({
                'success': False,
                'schedule': None,
                'error': 'Schedule not configured'
            })
            
    except Exception as e:
        app.logger.error(f"Error getting schedule for playbook {playbook_id} in project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/playbooks/<playbook_id>/schedule', methods=['PUT'])
@require_auth
def api_save_playbook_schedule(project_id, playbook_id):
    """API: schedule playbook"""
    try:
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        # playbook
        if not playbook_storage.playbook_exists(project_id, playbook_id):
            return jsonify({'success': False, 'error': 'Playbook not found'}), 404
        
        data = request.json or {}
        enabled = data.get('enabled', False)
        cron = data.get('cron', '').strip()
        timezone = data.get('timezone', 'UTC')
        
        if enabled:
            if not cron:
                return jsonify({'success': False, 'error': 'Cron expression is required when schedule is enabled'}), 400
            
            # cron expression
            try:
                from services.playbook_scheduler import PlaybookScheduler
                is_valid, error_msg = PlaybookScheduler.validate_cron(cron)
                if not is_valid:
                    return jsonify({'success': False, 'error': error_msg or 'Invalid cron expression'}), 400
            except ImportError as e:
                # Fallback validation if scheduler module not available
                app.logger.warning(f"Cannot import PlaybookScheduler for validation: {e}. Using basic validation.")
                # Basic cron format check (5 fields: minute hour day month weekday)
                import re
                cron_pattern = r'^(\*|([0-9]|[1-5][0-9])|\*\/([0-9]|[1-5][0-9])|([0-9]|[1-5][0-9])-([0-9]|[1-5][0-9])|([0-9]|[1-5][0-9])(,([0-9]|[1-5][0-9]))+)\s+(\*|([0-9]|1[0-9]|2[0-3])|\*\/([0-9]|1[0-9]|2[0-3])|([0-9]|1[0-9]|2[0-3])-([0-9]|1[0-9]|2[0-3])|([0-9]|1[0-9]|2[0-3])(,([0-9]|1[0-9]|2[0-3]))+)\s+(\*|([1-9]|[12][0-9]|3[01])|\*\/([1-9]|[12][0-9]|3[01])|([1-9]|[12][0-9]|3[01])-([1-9]|[12][0-9]|3[01])|([1-9]|[12][0-9]|3[01])(,([1-9]|[12][0-9]|3[01]))+)\s+(\*|([1-9]|1[0-2])|\*\/([1-9]|1[0-2])|([1-9]|1[0-2])-([1-9]|1[0-2])|([1-9]|1[0-2])(,([1-9]|1[0-2]))+)\s+(\*|([0-6])|\*\/([0-6])|([0-6])-([0-6])|([0-6])(,([0-6]))+)$'
                if not re.match(cron_pattern, cron):
                    return jsonify({'success': False, 'error': 'Invalid cron expression format. Expected: minute hour day month weekday'}), 400
            
            # timezone
            try:
                import pytz
                pytz.timezone(timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                return jsonify({'success': False, 'error': f'Unknown timezone: {timezone}'}), 400
        
        # schedule
        schedule_data = {
            'enabled': enabled,
            'cron': cron if enabled else '',
            'timezone': timezone
        }
        
        if playbook_storage.save_schedule(project_id, playbook_id, schedule_data):
            app.logger.info(f"Schedule saved for playbook {playbook_id} in project {project_id}: enabled={enabled}, cron={cron}, timezone={timezone}")
            return jsonify({
                'success': True,
                'schedule': schedule_data
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to save schedule'}), 500
            
    except Exception as e:
        app.logger.error(f"Error saving schedule for playbook {playbook_id} in project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/playbooks/<playbook_id>/schedule/next-run', methods=['GET'])
@require_auth
def api_get_next_run_time(project_id, playbook_id):
    """API: schedule"""
    try:
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        # playbook
        if not playbook_storage.playbook_exists(project_id, playbook_id):
            return jsonify({'success': False, 'error': 'Playbook not found'}), 404
        
        # schedule
        schedule = playbook_storage.get_schedule(project_id, playbook_id)
        
        if not schedule or not schedule.get('enabled', False):
            return jsonify({'success': False, 'error': 'Schedule not enabled'}), 404
        
        cron_expr = schedule.get('cron', '')
        timezone_str = schedule.get('timezone', 'UTC')
        
        if not cron_expr:
            return jsonify({'success': False, 'error': 'Cron expression not set'}), 400
        
        try:
            import pytz
            from croniter import croniter
            from datetime import datetime
            
            # Get timezone
            try:
                tz = pytz.timezone(timezone_str)
            except pytz.exceptions.UnknownTimeZoneError:
                app.logger.warning(f"Unknown timezone {timezone_str}, using UTC")
                tz = pytz.UTC
            
            # Get current time in target timezone
            current_utc = datetime.utcnow()
            current_in_tz = current_utc.replace(tzinfo=pytz.UTC).astimezone(tz)
            current_naive = current_in_tz.replace(tzinfo=None)
            
            # Calculate next execution time
            cron = croniter(cron_expr, current_naive)
            next_time = cron.get_next(datetime)
            
            # Convert back to UTC for response
            # next_time is already in target timezone (naive), so we need to localize it
            next_time_aware = tz.localize(next_time) if next_time.tzinfo is None else next_time.replace(tzinfo=tz)
            next_time_utc = next_time_aware.astimezone(pytz.UTC)
            
            return jsonify({
                'success': True,
                'next_run_time': next_time_utc.isoformat()
            })
            
        except ImportError as e:
            app.logger.warning(f"Cannot calculate next run time: {e}")
            return jsonify({'success': False, 'error': 'Cannot calculate next run time (dependencies not available)'}), 500
        except Exception as e:
            app.logger.error(f"Error calculating next run time: {e}", exc_info=True)
            return jsonify({'success': False, 'error': f'Error calculating next run time: {str(e)}'}), 500
            
    except Exception as e:
        app.logger.error(f"Error getting next run time for playbook {playbook_id} in project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/playbooks/<playbook_id>/schedule', methods=['DELETE'])
@require_auth
def api_delete_playbook_schedule(project_id, playbook_id):
    """API: schedule playbook ()"""
    try:
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        # playbook
        if not playbook_storage.playbook_exists(project_id, playbook_id):
            return jsonify({'success': False, 'error': 'Playbook not found'}), 404
        
        # schedule ()
        if playbook_storage.delete_schedule(project_id, playbook_id):
            app.logger.info(f"Schedule disabled for playbook {playbook_id} in project {project_id}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Failed to delete schedule'}), 500
            
    except Exception as e:
        app.logger.error(f"Error deleting schedule for playbook {playbook_id} in project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# - , 
# ansible-playbook worker.py


# ============================================================================
# QUEUE API - runs
# ============================================================================

@bp.route('/api/projects/<project_id>/playbooks/<playbook_id>/runs', methods=['GET'])
@require_auth
def api_list_playbook_runs(project_id, playbook_id):
    """API: runs playbook"""
    try:
        limit = request.args.get('limit', 50, type=int)
        status_filter = request.args.get('status')
        
        executions = list_executions(
            limit=limit,
            playbook_id=playbook_id,
            project_id=project_id
        )
        
        if status_filter:
            executions = [e for e in executions if e.get('status') == status_filter.upper()]
        
        return jsonify({
            'success': True,
            'runs': executions,
            'count': len(executions)
        })
    except Exception as e:
        app.logger.error(f"Error listing runs for playbook {playbook_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


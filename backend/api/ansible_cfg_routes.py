"""Ansible config routes.

Moved from ``backend/app.py`` during the phased refactor. URLs, methods,
view-function names, response shapes, and auth decorators are preserved.
"""
from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request, send_file

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from utils.project_paths import get_project_dir, resolve_ansible_config_path
from utils.request_ctx import (
    get_project_id_from_request as _get_pid_raw,
    require_project_id_from_request,
)
from utils.yaml_io import create_backup


bp = Blueprint("ansible_cfg_api", __name__)

DEPRECATED_ANSIBLE_CALLBACKS = {'profile_tasks', 'ansible.posix.profile_tasks'}


def _normalize_ansible_config_content(content: str) -> str:
    """Normalize saved ansible.cfg content for clean, deterministic execution."""
    text = str(content or '')
    lines = text.splitlines()
    normalized = []
    for line in lines:
        stripped = line.strip()
        if '=' not in line or stripped.startswith(('#', ';')):
            normalized.append(line)
            continue
        key, value = line.split('=', 1)
        normalized_key = key.strip().lower()
        if normalized_key == 'interpreter_discovery':
            continue
        if normalized_key == 'interpreter_python' and value.strip() in {'auto', 'auto_silent', 'auto_legacy', 'auto_legacy_silent'}:
            normalized.append(f"{key.rstrip()} = /usr/bin/python3")
            continue
        if normalized_key != 'callbacks_enabled':
            normalized.append(line)
            continue

        callbacks = [item.strip() for item in value.split('#', 1)[0].split(';', 1)[0].split(',') if item.strip()]
        callbacks = [item for item in callbacks if item not in DEPRECATED_ANSIBLE_CALLBACKS]
        if callbacks:
            normalized.append(f"{key.rstrip()} = {','.join(callbacks)}")
        # If profile_tasks was the only callback, omit callbacks_enabled entirely.
    return '\n'.join(normalized) + ('\n' if text.endswith('\n') else '')


def _get_project_id_from_request():
    # Preserve legacy signature: no fallback resolver in this handler group.
    return _get_pid_raw(lambda: None)


@bp.route('/api/ansible_config/list', methods=['GET'])
@require_auth
def list_ansible_config_files():
    """ .cfg Project Storage"""
    try:
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        config_files = []
        project_dir = get_project_dir(project_id)
        ansible_config_dir = project_dir / 'ansible-config'
        ansible_config_dir.mkdir(parents=True, exist_ok=True)
        for file_path in sorted(ansible_config_dir.glob('*.cfg')):
            config_files.append({
                'name': file_path.name,
                'path': f'ansible-config/{file_path.name}',
                'is_primary': file_path.name == 'ansible.cfg'
            })
        if not config_files:
            config_files.append({
                'name': 'ansible.cfg',
                'path': 'ansible-config/ansible.cfg',
                'is_primary': True
            })

        config_files.sort(key=lambda x: x['name'])

        selected_config = None
        selection_file = project_dir / 'data' / 'ansible-config-selection.json'
        if selection_file.exists():
            try:
                with open(selection_file, 'r', encoding='utf-8') as f:
                    selection_data = json.load(f)
                    selected_config = selection_data.get('selected_config')
            except Exception:
                pass
        if not selected_config and config_files:
            selected_config = 'ansible-config/ansible.cfg'
        return jsonify({
            'success': True,
            'files': config_files,
            'selected_config': selected_config
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/ansible_config/get', methods=['GET'])
@require_auth
def get_ansible_config():
    """ ansible config Project Storage"""
    try:
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        file_name = request.args.get('file', 'ansible.cfg')
        if '..' in file_name or file_name.startswith('/'):
            return jsonify({'success': False, 'error': 'Invalid file path'}), 400
        file_path = resolve_ansible_config_path(project_id, file_name)
        if not file_path.exists():
            return jsonify({'success': False, 'error': f'File {file_name} not found in Project Storage'}), 404
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({'success': True, 'content': content, 'file': file_name})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/ansible_config/save', methods=['POST'])
@require_auth
def save_ansible_config():
    """ ansible config Project Storage"""
    try:
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        data = request.json
        if not data:
            return jsonify({'success': False, 'error': 'No data'}), 400

        file_name = data.get('file', 'ansible.cfg')
        content = _normalize_ansible_config_content(data.get('content', ''))

        current_app.logger.info(f"User saving Ansible config file: {file_name} to project {project_id}")

        if not file_name:
            return jsonify({'success': False, 'error': 'File name not specified'}), 400

        if '..' in file_name or file_name.startswith('/'):
            return jsonify({'success': False, 'error': 'Invalid file path'}), 400

        file_path = resolve_ansible_config_path(project_id, file_name)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if file_path.exists():
            create_backup(file_path)

        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        current_app.logger.info(f"Ansible config saved to Project Storage: {file_path}")
        return jsonify({'success': True, 'message': f'File {file_name} successfully saved to Project Storage'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/ansible_config/download', methods=['GET'])
@require_auth
def download_ansible_config():
    """ ansible config Project Storage"""
    try:
        project_id = _get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        file_name = request.args.get('file', 'ansible.cfg')
        if '..' in file_name or file_name.startswith('/'):
            return jsonify({'success': False, 'error': 'Invalid file path'}), 400
        file_path = resolve_ansible_config_path(project_id, file_name)
        if not file_path.exists():
            return jsonify({'success': False, 'error': f'File {file_name} not found in Project Storage'}), 404
        return send_file(
            str(file_path),
            mimetype='text/plain',
            as_attachment=True,
            download_name=file_name
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/ansible_config/delete', methods=['POST'])
@require_auth
def delete_ansible_config():
    """ ansible config Project Storage"""
    try:
        project_id = _get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        data = request.json or {}
        file_name = data.get('file')

        if not file_name:
            return jsonify({'success': False, 'error': 'File name not specified'}), 400

        if '..' in file_name or file_name.startswith('/'):
            return jsonify({'success': False, 'error': 'Invalid file path'}), 400

        project_dir = get_project_dir(project_id)
        file_path = resolve_ansible_config_path(project_id, file_name)
        if not file_path.exists():
            return jsonify({'success': False, 'error': f'File {file_name} not found in Project Storage'}), 404
        create_backup(file_path)

        file_path.unlink()

        selection_file = project_dir / 'data' / 'ansible-config-selection.json'
        if selection_file.exists():
            try:
                with open(selection_file, 'r', encoding='utf-8') as f:
                    selection_data = json.load(f)
                selected_config = selection_data.get('selected_config')
                if selected_config and (
                    selected_config == file_name
                    or selected_config == file_path.name
                    or selected_config.endswith('/' + file_name)
                    or selected_config.endswith('/' + file_path.name)
                ):
                    selection_data['selected_config'] = None
                    selection_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(selection_file, 'w', encoding='utf-8') as f:
                        json.dump(selection_data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                current_app.logger.warning(f"Failed to update selection after deletion: {e}")

        current_app.logger.info(f"Ansible config deleted from Project Storage: {file_path}")
        return jsonify({'success': True, 'message': f'File {file_name} successfully deleted from Project Storage'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/ansible_config/select', methods=['POST'])
@require_auth
def select_ansible_config():
    """ ansible config """
    try:
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        data = request.json or {}
        file_path = data.get('file')

        if not file_path:
            return jsonify({'success': False, 'error': 'File path not specified'}), 400

        if '..' in file_path or file_path.startswith('/'):
            return jsonify({'success': False, 'error': 'Invalid file path'}), 400

        project_dir = get_project_dir(project_id)
        selection_file = project_dir / 'data' / 'ansible-config-selection.json'

        selection_data = {
            'selected_config': file_path,
            'updated_at': datetime.now().isoformat()
        }

        selection_file.parent.mkdir(parents=True, exist_ok=True)
        with open(selection_file, 'w', encoding='utf-8') as f:
            json.dump(selection_data, f, indent=2, ensure_ascii=False)

        current_app.logger.info(f"Selected ansible config saved for project {project_id}: {file_path}")
        return jsonify({'success': True, 'message': 'Selection saved', 'selected_config': file_path})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

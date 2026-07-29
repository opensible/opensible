"""Project-scoped secrets API routes.

Moved from ``backend/app.py`` during the phased refactor. URLs, methods,
view-function names, response shapes, and auth decorators are preserved.

``get_secret_file_path`` still lives in ``app.py`` and is resolved lazily
via the initialized app module (``__main__`` or ``app``) to avoid
circular imports.
"""
from __future__ import annotations

import json
import os
import sys
import time

from flask import Blueprint, current_app, jsonify, request

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from utils.json_io import safe_log_error
from utils.paths import slugify_secret_name
from utils.project_paths import get_project_dir, get_project_secrets_dir
from utils.request_ctx import get_project_id_from_request as _get_pid_raw
from utils.ssh_keys import validate_ssh_private_key


bp = Blueprint("secrets_api", __name__)


def _get_project_id_from_request():
    return _get_pid_raw(lambda: None)


def _app_module():
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "get_secret_file_path"):
            return mod
    import app as _a  # noqa
    return _a


def _get_secret_file_path(project_id, secret_name, secret_type=None):
    return _app_module().get_secret_file_path(project_id, secret_name, secret_type=secret_type)


def _sanitize_project_secret_for_api(secret_data, fallback_name=None):
    """Return metadata only; never expose secret material."""
    if not isinstance(secret_data, dict):
        secret_data = {}
    name = secret_data.get('name') or fallback_name or ''
    secret_type = secret_data.get('type', 'unknown')
    material = {}
    if secret_type == 'ssh_key':
        private_key = secret_data.get('privateKey') or ''
        passphrase = secret_data.get('passphrase') or ''
        material = {
            'privateKey': {'present': bool(private_key), 'length': len(private_key) if private_key else 0},
            'passphrase': {'present': bool(passphrase), 'length': len(passphrase) if passphrase else 0},
        }
    elif secret_type == 'login_password':
        password = secret_data.get('password') or ''
        material = {
            'password': {'present': bool(password), 'length': len(password) if password else 0},
        }
    return {
        'name': name,
        'type': secret_type,
        'username': secret_data.get('username', ''),
        'description': secret_data.get('description', ''),
        'createdAt': secret_data.get('createdAt', ''),
        'updatedAt': secret_data.get('updatedAt', ''),
        'version': secret_data.get('version', 1),
        'material': material,
    }


def _find_secret_file(secrets_dir, secret_name):
    """Locate a secret file across ssh_keys/, git_auth/, vault/."""
    ssh_key_file = secrets_dir / 'ssh_keys' / f"{secret_name}.json"
    if ssh_key_file.exists():
        return ssh_key_file
    git_auth_file = secrets_dir / 'git_auth' / f"{secret_name}.json"
    if git_auth_file.exists():
        return git_auth_file
    if secret_name == 'vault_pass':
        vault_file = secrets_dir / 'vault' / 'vault_pass'
        if vault_file.exists():
            return vault_file
    return None


@bp.route('/api/secrets', methods=['GET'])
@require_auth
def api_list_secrets():
    """List all project secrets (metadata only)."""
    try:
        project_id = _get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        secrets_dir = get_project_secrets_dir(project_id)
        secrets = []
        secrets_set = set()

        ssh_keys_dir = secrets_dir / 'ssh_keys'
        ssh_keys_dir.mkdir(parents=True, exist_ok=True)
        for secret_file in ssh_keys_dir.glob('*.json'):
            try:
                with open(secret_file, 'r', encoding='utf-8') as f:
                    secret_data = json.load(f)
                secret_name = secret_data.get('name', secret_file.stem)
                if secret_name not in secrets_set:
                    secrets_set.add(secret_name)
                    secrets.append({
                        'name': secret_name,
                        'type': secret_data.get('type', 'ssh_key'),
                        'username': secret_data.get('username', ''),
                        'description': secret_data.get('description', ''),
                        'createdAt': secret_data.get('createdAt', secret_data.get('updatedAt', '')),
                        'updatedAt': secret_data.get('updatedAt', ''),
                        'version': secret_data.get('version', 1),
                    })
            except Exception as e:
                current_app.logger.warning(f"Error reading secret file {secret_file}: {e}")
                continue

        git_auth_dir = secrets_dir / 'git_auth'
        git_auth_dir.mkdir(parents=True, exist_ok=True)
        for secret_file in git_auth_dir.glob('*.json'):
            try:
                with open(secret_file, 'r', encoding='utf-8') as f:
                    secret_data = json.load(f)
                secret_name = secret_data.get('name', secret_file.stem)
                if secret_name not in secrets_set:
                    secrets_set.add(secret_name)
                    secrets.append({
                        'name': secret_name,
                        'type': secret_data.get('type', 'git_auth'),
                        'username': secret_data.get('username', ''),
                        'description': secret_data.get('description', ''),
                        'createdAt': secret_data.get('createdAt', secret_data.get('updatedAt', '')),
                        'updatedAt': secret_data.get('updatedAt', ''),
                        'version': secret_data.get('version', 1),
                    })
            except Exception as e:
                current_app.logger.warning(f"Error reading secret file {secret_file}: {e}")
                continue

        vault_dir = secrets_dir / 'vault'
        vault_dir.mkdir(parents=True, exist_ok=True)
        vault_pass_file = vault_dir / 'vault_pass'
        if vault_pass_file.exists() and 'vault_pass' not in secrets_set:
            secrets_set.add('vault_pass')
            secrets.append({
                'name': 'vault_pass',
                'type': 'vault_password',
                'username': '',
                'description': 'Vault password',
                'createdAt': '',
                'updatedAt': '',
                'version': 1,
            })

        secrets.sort(key=lambda x: x['name'].lower())
        return jsonify({'success': True, 'secrets': secrets})
    except Exception as e:
        current_app.logger.error(safe_log_error("Error listing secrets", e))
        return jsonify({'success': False, 'error': 'Failed to list secrets'}), 500


@bp.route('/api/secrets/<secret_name>', methods=['GET'])
@require_auth
def api_get_secret(secret_name):
    """Get a single project secret (metadata only)."""
    try:
        project_id = _get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        secrets_dir = get_project_secrets_dir(project_id)
        secret_file = _find_secret_file(secrets_dir, secret_name)

        # Legacy vault_pass shortcut preserves original response shape.
        if secret_file and secret_file.name == 'vault_pass' and secret_file.suffix != '.json':
            with open(secret_file, 'r', encoding='utf-8') as f:
                vault_pass = f.read().strip()
            return jsonify({
                'success': True,
                'secret': {
                    'name': 'vault_pass',
                    'type': 'vault_password',
                    'username': '',
                    'description': 'Vault password',
                    'createdAt': '',
                    'updatedAt': '',
                    'version': 1,
                    'material': {
                        'password': {'present': bool(vault_pass), 'length': len(vault_pass) if vault_pass else 0}
                    },
                },
            })

        if not secret_file:
            secret_file = _get_secret_file_path(project_id, secret_name)

        if not secret_file.exists():
            return jsonify({'success': False, 'error': 'Secret not found'}), 404

        if secret_file.suffix == '.json':
            with open(secret_file, 'r', encoding='utf-8') as f:
                secret_data = json.load(f)
        else:
            with open(secret_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            secret_data = {'name': secret_file.stem, 'type': 'vault_password', 'content': content}

        return jsonify({
            'success': True,
            'secret': _sanitize_project_secret_for_api(secret_data, fallback_name=secret_name),
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        current_app.logger.error(safe_log_error("Error getting secret", e))
        return jsonify({'success': False, 'error': 'Failed to get secret'}), 500


@bp.route('/api/secrets', methods=['POST'])
@require_auth
def api_create_secret():
    """Create a new project secret."""
    data = None
    try:
        project_id = _get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        data = request.json or {}
        secret_name = data.get('name', '').strip()
        if not secret_name:
            return jsonify({'success': False, 'error': 'Secret name is required'}), 400

        try:
            safe_name = slugify_secret_name(secret_name)
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 400

        secret_type = data.get('type', '').strip()
        if secret_type not in ['ssh_key', 'login_password']:
            return jsonify({'success': False, 'error': 'Invalid secret type. Must be "ssh_key" or "login_password"'}), 400

        secret_file = _get_secret_file_path(project_id, safe_name, secret_type=secret_type)
        if secret_file.exists():
            return jsonify({'success': False, 'error': 'Secret with this name already exists'}), 409

        private_key = None
        password = None
        if secret_type == 'ssh_key':
            private_key = data.get('privateKey', '').strip()
            if not private_key:
                return jsonify({'success': False, 'error': 'Private key is required for SSH key secret'}), 400
            is_valid, error_msg = validate_ssh_private_key(private_key)
            if not is_valid:
                return jsonify({'success': False, 'error': f'Invalid private key format: {error_msg}'}), 400
        elif secret_type == 'login_password':
            password = data.get('password', '').strip()
            if not password:
                return jsonify({'success': False, 'error': 'Password is required for login/password secret'}), 400

        current_time = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        secret_data = {
            'version': 1,
            'type': secret_type,
            'name': safe_name,
            'username': data.get('username', '').strip(),
            'description': data.get('description', '').strip(),
            'createdAt': current_time,
            'updatedAt': current_time,
        }
        if secret_type == 'ssh_key':
            secret_data['privateKey'] = private_key
            if data.get('passphrase'):
                secret_data['passphrase'] = data.get('passphrase', '').strip()
        elif secret_type == 'login_password':
            secret_data['password'] = password

        with open(secret_file, 'w', encoding='utf-8') as f:
            json.dump(secret_data, f, indent=2, ensure_ascii=False)
        os.chmod(secret_file, 0o600)

        current_app.logger.info(f"Secret created: {safe_name} in project {project_id}")
        return jsonify({
            'success': True,
            'secret': _sanitize_project_secret_for_api(secret_data, fallback_name=safe_name),
            'message': 'Secret created successfully',
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        current_app.logger.error(safe_log_error("Error creating secret", e, data))
        return jsonify({'success': False, 'error': 'Failed to create secret'}), 500


@bp.route('/api/secrets/<secret_name>', methods=['PUT'])
@require_auth
def api_update_secret(secret_name):
    """Update an existing project secret."""
    data = None
    try:
        project_id = _get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        secrets_dir = get_project_secrets_dir(project_id)
        secret_file = _find_secret_file(secrets_dir, secret_name)
        if not secret_file:
            secret_file = _get_secret_file_path(project_id, secret_name)
        if not secret_file.exists():
            return jsonify({'success': False, 'error': 'Secret not found'}), 404

        data = request.json or {}

        if secret_file.suffix == '.json':
            with open(secret_file, 'r', encoding='utf-8') as f:
                existing_secret = json.load(f)
        else:
            with open(secret_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            existing_secret = {'name': secret_file.stem, 'type': 'vault_password', 'content': content}

        if 'username' in data:
            existing_secret['username'] = data.get('username', '').strip()
        if 'description' in data:
            existing_secret['description'] = data.get('description', '').strip()
        if 'createdAt' not in existing_secret:
            existing_secret['createdAt'] = existing_secret.get(
                'updatedAt', time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            )

        if existing_secret.get('type') == 'ssh_key':
            if 'privateKey' in data and str(data.get('privateKey') or '').strip():
                private_key = str(data.get('privateKey') or '').strip()
                is_valid, error_msg = validate_ssh_private_key(private_key)
                if not is_valid:
                    return jsonify({'success': False, 'error': f'Invalid private key format: {error_msg}'}), 400
                existing_secret['privateKey'] = private_key
            if 'passphrase' in data and str(data.get('passphrase') or '').strip():
                existing_secret['passphrase'] = str(data.get('passphrase') or '').strip()
        elif existing_secret.get('type') == 'login_password':
            if 'password' in data and str(data.get('password') or '').strip():
                existing_secret['password'] = str(data.get('password') or '').strip()

        existing_secret['updatedAt'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

        if secret_file.suffix == '.json':
            with open(secret_file, 'w', encoding='utf-8') as f:
                json.dump(existing_secret, f, indent=2, ensure_ascii=False)
        else:
            if 'password' in data:
                with open(secret_file, 'w', encoding='utf-8') as f:
                    f.write(data.get('password', '').strip())
            elif 'content' in data:
                with open(secret_file, 'w', encoding='utf-8') as f:
                    f.write(data.get('content', '').strip())

        os.chmod(secret_file, 0o600)
        current_app.logger.info(f"Secret updated: {secret_name} in project {project_id}")
        return jsonify({
            'success': True,
            'secret': _sanitize_project_secret_for_api(existing_secret, fallback_name=secret_name),
            'message': 'Secret updated successfully',
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        current_app.logger.error(safe_log_error("Error updating secret", e, data))
        return jsonify({'success': False, 'error': 'Failed to update secret'}), 500


@bp.route('/api/secrets/<secret_name>', methods=['DELETE'])
@require_auth
def api_delete_secret(secret_name):
    """Delete a project secret."""
    try:
        project_id = _get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        secrets_dir = get_project_secrets_dir(project_id)
        secret_file = _find_secret_file(secrets_dir, secret_name)
        if not secret_file:
            secret_file = _get_secret_file_path(project_id, secret_name)
        if not secret_file.exists():
            return jsonify({'success': False, 'error': 'Secret not found'}), 404

        secret_file.unlink()
        current_app.logger.info(f"Secret deleted: {secret_name} in project {project_id}")
        return jsonify({'success': True, 'message': 'Secret deleted successfully'})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        current_app.logger.error(safe_log_error("Error deleting secret", e))
        return jsonify({'success': False, 'error': 'Failed to delete secret'}), 500


@bp.route('/api/secrets/meta', methods=['GET'])
@require_auth
def api_get_secrets_meta():
    """List secret metadata (name/type/username) for host/group binding UIs."""
    try:
        project_id = _get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        secrets_dir = get_project_secrets_dir(project_id)
        # Ensure project dir helper is exercised for parity with legacy code path.
        _ = get_project_dir(project_id)
        secrets = []
        secrets_set = set()

        ssh_keys_dir = secrets_dir / 'ssh_keys'
        ssh_keys_dir.mkdir(parents=True, exist_ok=True)
        for secret_file in ssh_keys_dir.glob('*.json'):
            try:
                with open(secret_file, 'r', encoding='utf-8') as f:
                    secret_data = json.load(f)
                secret_name = secret_data.get('name', secret_file.stem)
                if secret_name not in secrets_set:
                    secrets_set.add(secret_name)
                    secrets.append({
                        'name': secret_name,
                        'type': secret_data.get('type', 'ssh_key'),
                        'username': secret_data.get('username', ''),
                    })
            except Exception as e:
                current_app.logger.warning(f"Error reading secret file {secret_file}: {e}")
                continue

        git_auth_dir = secrets_dir / 'git_auth'
        git_auth_dir.mkdir(parents=True, exist_ok=True)
        for secret_file in git_auth_dir.glob('*.json'):
            try:
                with open(secret_file, 'r', encoding='utf-8') as f:
                    secret_data = json.load(f)
                secret_name = secret_data.get('name', secret_file.stem)
                if secret_name not in secrets_set:
                    secrets_set.add(secret_name)
                    secrets.append({
                        'name': secret_name,
                        'type': secret_data.get('type', 'git_auth'),
                        'username': secret_data.get('username', ''),
                    })
            except Exception as e:
                current_app.logger.warning(f"Error reading secret file {secret_file}: {e}")
                continue

        secrets.sort(key=lambda x: x['name'].lower())
        return jsonify({'success': True, 'secrets': secrets})
    except Exception as e:
        current_app.logger.error(safe_log_error("Error getting secrets meta", e))
        return jsonify({'success': False, 'error': 'Failed to get secrets meta'}), 500

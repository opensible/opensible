"""Roles storage / role editor API routes.

Moved from ``backend/app.py`` during the phased refactor. URLs, methods,
view-function names, response shapes, and auth decorators are preserved.

Heavy helpers (``scan_roles_storage``, ``load_roles_config``,
``save_roles_config``, ``get_role_details``, ``resolve_project_source``,
``get_project_dir``, ``_decrypt_content_if_encrypted``, ``_load_vaults``,
``_encrypt_content_if_requested``) still live in ``app.py``. They are
resolved lazily via ``_app_module()`` so this blueprint never triggers
a circular import.
"""
from __future__ import annotations

import shutil
import sys

from flask import Blueprint, jsonify, request

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from utils.request_ctx import get_project_id_from_request as _get_pid_raw


def get_project_id_from_request():
    # Preserve legacy no-arg signature (no default project fallback).
    return _get_pid_raw(lambda: None)


bp = Blueprint("roles_api", __name__)


def _app_module():
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "scan_roles_storage"):
            return mod
    import app as _a  # noqa
    return _a


def _logger():
    return _app_module().app.logger


def _resolve_roles_storage_path(project_id):
    a = _app_module()
    source = a.resolve_project_source(project_id, 'repo')
    return source['rootPath'] / 'roles'


def _resolve_role_target(project_id, pack_id, role_name, sub_path=''):
    """Return (target_path, role_dir) safely scoped within roles storage."""
    roles_storage_path = _resolve_roles_storage_path(project_id)
    if pack_id and role_name and pack_id != role_name:
        role_dir = roles_storage_path / pack_id / role_name
    else:
        role_dir = roles_storage_path / (role_name or pack_id)
    target = role_dir / sub_path if sub_path else role_dir
    try:
        target.resolve().relative_to(role_dir.resolve().parent if not sub_path else role_dir.resolve())
    except ValueError:
        raise ValueError('Path escapes role directory')
    return target, role_dir


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@bp.route('/api/roles/storage', methods=['GET'])
@require_auth
def api_get_roles_storage():
    """Return the roles tree for the current project's storage."""
    try:
        a = _app_module()
        project_id = get_project_id_from_request()
        tree = a.scan_roles_storage(project_id)

        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        try:
            source = a.resolve_project_source(project_id, 'repo')
            repo_path = source['rootPath']
            storage_root = repo_path / 'roles'
        except Exception as e:
            _logger().error(f"[api_scan_roles_storage] Failed to resolve Project Storage: {e}")
            return jsonify({'success': False, 'error': f'Failed to resolve Project Storage: {e}'}), 500

        project_dir = a.get_project_dir(project_id)
        storage_root_relative = (
            str(storage_root.relative_to(project_dir))
            if storage_root.is_relative_to(project_dir) else 'repo/roles'
        )

        return jsonify({
            'success': True,
            'storageRoot': storage_root_relative,
            'tree': tree
        })
    except Exception as e:
        _logger().error(f"Error scanning roles-storage: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/roles/config', methods=['GET'])
@require_auth
def api_get_roles_config():
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        config = _app_module().load_roles_config(project_id)
        return jsonify({'success': True, 'config': config})
    except Exception as e:
        _logger().error(f"Error loading roles configuration: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/roles/config', methods=['POST'])
@require_auth
def api_save_roles_config():
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        data = request.json or {}
        config = data.get('config', {})

        if _app_module().save_roles_config(config, project_id):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Error saving configuration'}), 500
    except Exception as e:
        _logger().error(f"Error saving roles configuration: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/roles/<pack_id>/<role_name>', methods=['GET'])
@require_auth
def api_get_role_details(pack_id, role_name):
    try:
        project_id = get_project_id_from_request()
        details = _app_module().get_role_details(pack_id, role_name, project_id)
        if details:
            return jsonify({'success': True, 'role': details})
        return jsonify({'success': False, 'error': 'Role not found'}), 404
    except Exception as e:
        _logger().error(f"Error getting role details: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/roles/files/<path:role_path>', methods=['GET'])
@require_auth
def api_get_role_files(role_path):
    """List all files inside a role directory."""
    try:
        a = _app_module()
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        _logger().info(f"[api_get_role_files] role_path={role_path}, project_id={project_id}")

        parts = role_path.split('/')
        pack_id = None
        role_name = None

        if len(parts) == 1:
            role_name = parts[0]
            pack_id = role_name
        elif len(parts) >= 2:
            pack_id = parts[0]
            role_name = '/'.join(parts[1:])
        else:
            return jsonify({'success': False, 'error': f'Invalid role path: {role_path}'}), 400

        if '..' in (pack_id or '') or '..' in (role_name or ''):
            return jsonify({'success': False, 'error': 'Invalid path'}), 400

        try:
            source = a.resolve_project_source(project_id, 'repo')
            repo_path = source['rootPath']
            roles_storage_path = repo_path / 'roles'
        except Exception as e:
            _logger().error(f"[api_get_role_files] Failed to resolve Project Storage: {e}")
            return jsonify({'success': False, 'error': f'Failed to resolve Project Storage: {e}'}), 500

        if len(parts) == 1:
            role_path_full = roles_storage_path / role_name
        else:
            role_path_full = roles_storage_path / pack_id / role_name

        if not role_path_full.exists() or not role_path_full.is_dir():
            return jsonify({'success': False, 'error': 'Role not found'}), 404

        files = []

        def collect_files(directory, base_path=''):
            try:
                for item in directory.iterdir():
                    if item.name.startswith('.'):
                        continue
                    relative_path = f"{base_path}/{item.name}" if base_path else item.name
                    if item.is_file():
                        files.append({
                            'path': relative_path,
                            'name': item.name,
                            'type': 'file',
                            'size': item.stat().st_size
                        })
                    elif item.is_dir():
                        collect_files(item, relative_path)
            except PermissionError:
                _logger().warning(f"Permission denied accessing {directory}")

        collect_files(role_path_full)

        return jsonify({
            'success': True,
            'files': files,
            'pack': pack_id,
            'role': role_name
        })
    except Exception as e:
        _logger().error(f"Error getting role files: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/roles/file/<pack_id>/<path:role_name>/<path:file_path>', methods=['GET'])
@require_auth
def api_get_role_file(pack_id, role_name, file_path):
    try:
        a = _app_module()
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        if '..' in pack_id or '..' in role_name or '..' in file_path:
            return jsonify({'success': False, 'error': 'Invalid path'}), 400

        try:
            source = a.resolve_project_source(project_id, 'repo')
            repo_path = source['rootPath']
            roles_storage_path = repo_path / 'roles'
        except Exception as e:
            _logger().error(f"[api_get_role_file] Failed to resolve Project Storage: {e}")
            return jsonify({'success': False, 'error': f'Failed to resolve Project Storage: {e}'}), 500

        if pack_id == role_name:
            file_path_full = roles_storage_path / role_name / file_path
            role_dir = roles_storage_path / role_name
        else:
            file_path_full = roles_storage_path / pack_id / role_name / file_path
            role_dir = roles_storage_path / pack_id / role_name

        try:
            file_path_full.resolve().relative_to(role_dir.resolve())
        except ValueError:
            return jsonify({'success': False, 'error': 'File path outside role directory'}), 400

        if not file_path_full.exists():
            return jsonify({'success': False, 'error': f'File not found: {file_path}'}), 404
        if not file_path_full.is_file():
            return jsonify({'success': False, 'error': f'Path is not a file: {file_path}'}), 400

        try:
            with open(file_path_full, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(file_path_full, 'rb') as f:
                content = f.read().decode('utf-8', errors='replace')
        except Exception as e:
            _logger().error(f"Error reading file {file_path_full}: {e}")
            return jsonify({'success': False, 'error': f'Error reading file: {e}'}), 500

        vault_id = request.args.get('vault_id') or request.args.get('vaultId')
        resp = {'success': True, 'content': content, 'path': file_path, 'pack': pack_id, 'role': role_name}
        decrypted, was_encrypted, vault_id_used, vault_id_required = a._decrypt_content_if_encrypted(
            project_id, content, vault_id
        )
        if was_encrypted:
            if vault_id_required:
                vaults = a._load_vaults(project_id)
                return jsonify({
                    'success': True,
                    'encrypted': True,
                    'vaultIdRequired': True,
                    'vaults': vaults,
                    'path': file_path,
                    'pack': pack_id,
                    'role': role_name
                })
            if decrypted is None:
                return jsonify({'success': False, 'error': 'Failed to decrypt vault-encrypted file. Check vault key.'}), 500
            resp['content'] = decrypted
            resp['encrypted'] = True
            resp['vaultId'] = vault_id_used

        return jsonify(resp)
    except Exception as e:
        _logger().error(f"Error getting role file: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/roles/file/<pack_id>/<path:role_name>/<path:file_path>', methods=['PUT'])
@require_auth
def api_save_role_file(pack_id, role_name, file_path):
    try:
        a = _app_module()
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        if '..' in pack_id or '..' in role_name or '..' in file_path:
            return jsonify({'success': False, 'error': 'Invalid path'}), 400

        data = request.get_json()
        if not data or 'content' not in data:
            return jsonify({'success': False, 'error': 'Content is required'}), 400

        content = data['content']
        vault_id = data.get('vaultId') or data.get('vault_id')

        if vault_id:
            encrypted_content, err = a._encrypt_content_if_requested(project_id, content, vault_id)
            if err:
                return jsonify({'success': False, 'error': err}), 400
            content = encrypted_content

        try:
            source = a.resolve_project_source(project_id, 'repo')
            repo_path = source['rootPath']
            roles_storage_path = repo_path / 'roles'
        except Exception as e:
            _logger().error(f"[api_save_role_file] Failed to resolve Project Storage: {e}")
            return jsonify({'success': False, 'error': f'Failed to resolve Project Storage: {e}'}), 500

        if pack_id == role_name:
            file_path_full = roles_storage_path / role_name / file_path
            role_dir = roles_storage_path / role_name
        else:
            file_path_full = roles_storage_path / pack_id / role_name / file_path
            role_dir = roles_storage_path / pack_id / role_name

        try:
            file_path_full.resolve().relative_to(role_dir.resolve())
        except ValueError:
            return jsonify({'success': False, 'error': 'File path outside role directory'}), 400

        file_path_full.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(file_path_full, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            _logger().error(f"Error writing file {file_path_full}: {e}")
            return jsonify({'success': False, 'error': f'Error writing file: {e}'}), 500

        return jsonify({
            'success': True,
            'path': file_path,
            'pack': pack_id,
            'role': role_name
        })
    except Exception as e:
        _logger().error(f"Error saving role file: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/roles/file/<pack_id>/<path:role_name>/<path:file_path>', methods=['POST'])
@require_auth
def api_create_role_file(pack_id, role_name, file_path):
    """Create a new (optionally empty) file inside a role."""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        if '..' in pack_id or '..' in role_name or '..' in file_path:
            return jsonify({'success': False, 'error': 'Invalid path'}), 400
        data = request.get_json(silent=True) or {}
        content = data.get('content', '')
        target, _ = _resolve_role_target(project_id, pack_id, role_name, file_path)
        if target.exists():
            return jsonify({'success': False, 'error': 'File already exists'}), 409
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'success': True, 'path': file_path})
    except Exception as e:
        _logger().error(f"Error creating role file: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/roles/file/<pack_id>/<path:role_name>/<path:file_path>', methods=['DELETE'])
@require_auth
def api_delete_role_file(pack_id, role_name, file_path):
    """Delete a file inside a role."""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        if '..' in pack_id or '..' in role_name or '..' in file_path:
            return jsonify({'success': False, 'error': 'Invalid path'}), 400
        target, _ = _resolve_role_target(project_id, pack_id, role_name, file_path)
        if not target.exists():
            return jsonify({'success': False, 'error': 'File not found'}), 404
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return jsonify({'success': True})
    except Exception as e:
        _logger().error(f"Error deleting role file: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/roles/folder/<pack_id>/<path:role_name>/<path:folder_path>', methods=['POST'])
@require_auth
def api_create_role_folder(pack_id, role_name, folder_path):
    """Create a directory inside a role."""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        if '..' in pack_id or '..' in role_name or '..' in folder_path:
            return jsonify({'success': False, 'error': 'Invalid path'}), 400
        target, _ = _resolve_role_target(project_id, pack_id, role_name, folder_path)
        target.mkdir(parents=True, exist_ok=True)
        keep = target / '.gitkeep'
        if not any(target.iterdir()):
            keep.touch()
        return jsonify({'success': True, 'path': folder_path})
    except Exception as e:
        _logger().error(f"Error creating role folder: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/roles/folder/<pack_id>/<path:role_name>/<path:folder_path>', methods=['DELETE'])
@require_auth
def api_delete_role_folder(pack_id, role_name, folder_path):
    """Delete a directory inside a role (recursive)."""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        if '..' in pack_id or '..' in role_name or '..' in folder_path:
            return jsonify({'success': False, 'error': 'Invalid path'}), 400
        target, _ = _resolve_role_target(project_id, pack_id, role_name, folder_path)
        if not target.exists():
            return jsonify({'success': False, 'error': 'Folder not found'}), 404
        shutil.rmtree(target)
        return jsonify({'success': True})
    except Exception as e:
        _logger().error(f"Error deleting role folder: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/roles/role', methods=['POST'])
@require_auth
def api_create_role():
    """Create a new role skeleton. Body: { name, pack? }."""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()
        pack = (data.get('pack') or '').strip()
        if not name or '/' in name or '..' in name:
            return jsonify({'success': False, 'error': 'Invalid role name'}), 400
        if pack and ('/' in pack or '..' in pack):
            return jsonify({'success': False, 'error': 'Invalid pack name'}), 400
        roles_storage_path = _resolve_roles_storage_path(project_id)
        role_root = (roles_storage_path / pack / name) if pack else (roles_storage_path / name)
        if role_root.exists():
            return jsonify({'success': False, 'error': 'Role already exists'}), 409
        for sub in ('tasks', 'handlers', 'defaults', 'vars', 'meta', 'templates', 'files'):
            (role_root / sub).mkdir(parents=True, exist_ok=True)
        (role_root / 'tasks' / 'main.yml').write_text('---\n# tasks file for {}\n'.format(name), encoding='utf-8')
        (role_root / 'handlers' / 'main.yml').write_text('---\n# handlers file for {}\n'.format(name), encoding='utf-8')
        (role_root / 'defaults' / 'main.yml').write_text('---\n# defaults file for {}\n'.format(name), encoding='utf-8')
        (role_root / 'vars' / 'main.yml').write_text('---\n# vars file for {}\n'.format(name), encoding='utf-8')
        (role_root / 'meta' / 'main.yml').write_text(
            '---\ngalaxy_info:\n  role_name: {}\n  author: ""\n  description: ""\ndependencies: []\n'.format(name),
            encoding='utf-8'
        )
        (role_root / 'README.md').write_text(f'# {name}\n', encoding='utf-8')
        return jsonify({'success': True, 'pack': pack or name, 'role': name})
    except Exception as e:
        _logger().error(f"Error creating role: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/roles/role/<path:role_path>', methods=['DELETE'])
@require_auth
def api_delete_role(role_path):
    """Delete an entire role directory."""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        if '..' in role_path:
            return jsonify({'success': False, 'error': 'Invalid path'}), 400
        roles_storage_path = _resolve_roles_storage_path(project_id)
        target = roles_storage_path / role_path
        try:
            target.resolve().relative_to(roles_storage_path.resolve())
        except ValueError:
            return jsonify({'success': False, 'error': 'Path escapes roles storage'}), 400
        if not target.exists() or not target.is_dir():
            return jsonify({'success': False, 'error': 'Role not found'}), 404
        shutil.rmtree(target)
        return jsonify({'success': True})
    except Exception as e:
        _logger().error(f"Error deleting role: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

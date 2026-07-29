"""Vault, vault-key, and vault-file API routes blueprint.

Moved from ``backend/app.py``. URLs, methods, view-function names, response
shapes, and auth decorators are preserved verbatim. All references to
app.py globals (helpers and storage utilities) are resolved lazily via
``_LazyProxy`` so this blueprint can be imported before app.py finishes
initializing.
"""
from __future__ import annotations

import os
import time
import re
import sys
import json
import uuid
import shutil
from pathlib import Path
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

try:
    from utils.vault_utils import (
        is_ansible_vault_encrypted,
        parse_vault_id_from_header,
        ansible_vault_decrypt,
        ansible_vault_encrypt,
    )
except ImportError:  # pragma: no cover
    from ..utils.vault_utils import (
        is_ansible_vault_encrypted,
        parse_vault_id_from_header,
        ansible_vault_decrypt,
        ansible_vault_encrypt,
    )


bp = Blueprint("vaults_api", __name__)


def _app_module():
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "_load_vaults"):
            return mod
    import app as _a  # noqa
    return _a


class _LazyProxy:
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
    "load_projects",
    "load_project_config",
    "save_project_config",
    "get_project_vault_keys_dir",
    "get_project_vaults_file",
    "_load_vault_file_keys",
    "_save_vault_file_key",
    "_scan_repo_vault_files",
    "_load_vaults",
    "_save_vaults",
    "_enrich_vault_vault_id_from_content",
    "_resolve_vault_file_path",
    "_decrypt_content_if_encrypted",
    "_encrypt_content_if_requested",
    "get_key_password_by_key_id",
)

for _n in _APP_NAMES:
    globals().setdefault(_n, _LazyProxy(_n))
del _n


# ---------------------------------------------------------------------------
# Routes (verbatim from app.py, @app.route -> @bp.route)
# ---------------------------------------------------------------------------
@bp.route('/api/projects/<project_id>/vault-keys', methods=['GET'])
@require_auth
def api_list_vault_keys(project_id):
    """API: vault keys ( )"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404

        vault_keys_dir = get_project_vault_keys_dir(project_id)
        keys = []
        for meta_file in vault_keys_dir.glob('*.json'):
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                keys.append({
                    'id': meta.get('id'),
                    'name': meta.get('name', ''),
                    'type': meta.get('type', 'vault_password'),
                    'createdAt': meta.get('createdAt'),
                    'updatedAt': meta.get('updatedAt')
                })
            except Exception as e:
                app.logger.warning(f"Error reading vault key {meta_file}: {e}")
                continue

        keys.sort(key=lambda k: (k.get('name') or '').lower())
        return jsonify({'success': True, 'keys': keys})
    except Exception as e:
        app.logger.error(f"Error listing vault keys: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/vault-keys', methods=['POST'])
@require_auth
def api_create_vault_key(project_id):
    """API: vault key"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404

        data = request.json or {}
        name = (data.get('name') or '').strip()
        password = data.get('password', '').strip()
        key_type = data.get('type', 'vault_password')

        if not name:
            return jsonify({'success': False, 'error': 'Name is required'}), 400
        if not password:
            return jsonify({'success': False, 'error': 'Password is required'}), 400

        vault_keys_dir = get_project_vault_keys_dir(project_id)
        key_id = str(uuid.uuid4())
        meta_file = vault_keys_dir / f'{key_id}.json'
        pass_file = vault_keys_dir / f'{key_id}.pass'

        now = time.time()
        meta = {
            'id': key_id,
            'name': name,
            'type': key_type,
            'createdAt': now,
            'updatedAt': now
        }
        with open(meta_file, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        with open(pass_file, 'w', encoding='utf-8') as f:
            f.write(password)
            if not password.endswith('\n'):
                f.write('\n')
        os.chmod(pass_file, 0o600)

        app.logger.info(f"Vault key created: {key_id} in project {project_id}")
        return jsonify({'success': True, 'key': meta})
    except Exception as e:
        app.logger.error(f"Error creating vault key: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/vault-keys/<key_id>', methods=['GET'])
@require_auth
def api_get_vault_key(project_id, key_id):
    """API: vault key (, )"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404

        vault_keys_dir = get_project_vault_keys_dir(project_id)
        meta_file = vault_keys_dir / f'{key_id}.json'
        if not meta_file.exists():
            return jsonify({'success': False, 'error': 'Key not found'}), 404

        with open(meta_file, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        return jsonify({'success': True, 'key': meta})
    except Exception as e:
        app.logger.error(f"Error getting vault key: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/vault-keys/<key_id>', methods=['PUT'])
@require_auth
def api_update_vault_key(project_id, key_id):
    """API: vault key (, , )"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404

        vault_keys_dir = get_project_vault_keys_dir(project_id)
        meta_file = vault_keys_dir / f'{key_id}.json'
        pass_file = vault_keys_dir / f'{key_id}.pass'
        if not meta_file.exists():
            return jsonify({'success': False, 'error': 'Key not found'}), 404

        data = request.json or {}
        with open(meta_file, 'r', encoding='utf-8') as f:
            meta = json.load(f)

        if 'name' in data:
            name = (data.get('name') or '').strip()
            if not name:
                return jsonify({'success': False, 'error': 'Name cannot be empty'}), 400
            meta['name'] = name
        if 'type' in data:
            meta['type'] = data.get('type', 'vault_password')
        if 'password' in data:
            password = data.get('password', '').strip()
            if password:
                with open(pass_file, 'w', encoding='utf-8') as f:
                    f.write(password)
                    if not password.endswith('\n'):
                        f.write('\n')
                os.chmod(pass_file, 0o600)

        meta['updatedAt'] = time.time()
        with open(meta_file, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        app.logger.info(f"Vault key updated: {key_id} in project {project_id}")
        return jsonify({'success': True, 'key': meta})
    except Exception as e:
        app.logger.error(f"Error updating vault key: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/vault-keys/<key_id>', methods=['DELETE'])
@require_auth
def api_delete_vault_key(project_id, key_id):
    """API: vault key"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404

        vault_keys_dir = get_project_vault_keys_dir(project_id)
        meta_file = vault_keys_dir / f'{key_id}.json'
        pass_file = vault_keys_dir / f'{key_id}.pass'
        if not meta_file.exists():
            return jsonify({'success': False, 'error': 'Key not found'}), 404

        # : vaults?
        vaults_file = get_project_vaults_file(project_id)
        if vaults_file.exists():
            with open(vaults_file, 'r', encoding='utf-8') as f:
                vaults = json.load(f)
            if any(v.get('keyId') == key_id for v in vaults):
                return jsonify({'success': False, 'error': 'Key is used by one or more vaults. Remove vault bindings first.'}), 400

        meta_file.unlink()
        if pass_file.exists():
            pass_file.unlink()

        app.logger.info(f"Vault key deleted: {key_id} in project {project_id}")
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f"Error deleting vault key: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# Vaults API (secrets/vault/)
# ============================================================================

@bp.route('/api/projects/<project_id>/vaults', methods=['GET'])
@require_auth
def api_list_vaults(project_id):
    """API: vaults """
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404

        vaults = _load_vaults(project_id)
        vaults = [_enrich_vault_vault_id_from_content(v) for v in vaults]
        vault_keys_dir = get_project_vault_keys_dir(project_id)
        vault_files = _scan_repo_vault_files(project_id)

        for v in vaults:
            key_id = v.get('keyId')
            v['keyName'] = None
            if key_id:
                key_meta = vault_keys_dir / f'{key_id}.json'
                if key_meta.exists():
                    try:
                        with open(key_meta, 'r', encoding='utf-8') as f:
                            v['keyName'] = json.load(f).get('name')
                    except Exception:
                            pass

        vault_label_to_key = {}
        vault_label_to_id = {}
        for v in vaults:
            key_name = v.get('keyName')
            vid = v.get('id')
            for lbl in (v.get('vaultId'), v.get('name')):
                if lbl:
                    if key_name:
                        vault_label_to_key[lbl] = key_name
                    vault_label_to_id[lbl] = vid
        path_to_key = _load_vault_file_keys(project_id)
        for f in vault_files:
            label = f.get('key')
            path = f.get('path', '')
            key_name = vault_label_to_key.get(label) if label else None
            if not key_name and path:
                stored_key_id = path_to_key.get(path)
                if stored_key_id:
                    key_meta = vault_keys_dir / f'{stored_key_id}.json'
                    if key_meta.exists():
                        try:
                            with open(key_meta, 'r', encoding='utf-8') as kf:
                                key_name = json.load(kf).get('name')
                        except Exception:
                            pass
            f['keyName'] = key_name
            f['vaultId'] = vault_label_to_id.get(label) if label else None

        return jsonify({'success': True, 'vaults': vaults, 'vaultFiles': vault_files})
    except Exception as e:
        app.logger.error(f"Error listing vaults: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/vaults', methods=['POST'])
@require_auth
def api_create_vault(project_id):
    """API: vault key"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404

        data = request.json or {}
        name = (data.get('name') or '').strip()
        key_id = data.get('keyId') or data.get('key_id', '').strip()
        vault_id_label = (data.get('vaultId') or data.get('vault_id') or '').strip() or None

        if not name:
            return jsonify({'success': False, 'error': 'Name is required'}), 400
        if not key_id:
            return jsonify({'success': False, 'error': 'Key is required'}), 400

        vault_keys_dir = get_project_vault_keys_dir(project_id)
        key_meta = vault_keys_dir / f'{key_id}.json'
        if not key_meta.exists():
            return jsonify({'success': False, 'error': 'Key not found'}), 404

        vaults = _load_vaults(project_id)
        now = time.time()
        vault = {
            'id': str(uuid.uuid4()),
            'name': name,
            'keyId': key_id,
            'vaultId': vault_id_label,
            'createdAt': now,
            'updatedAt': now
        }
        vaults.append(vault)
        _save_vaults(project_id, vaults)

        app.logger.info(f"Vault created: {vault['id']} in project {project_id}")
        return jsonify({'success': True, 'vault': vault})
    except Exception as e:
        app.logger.error(f"Error creating vault: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/vaults/<vault_id>', methods=['GET'])
@require_auth
def api_get_vault(project_id, vault_id):
    """API: vault ID"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404

        vaults = _load_vaults(project_id)
        vault = next((v for v in vaults if v.get('id') == vault_id), None)
        if not vault:
            return jsonify({'success': False, 'error': 'Vault not found'}), 404

        vault = _enrich_vault_vault_id_from_content(vault)
        return jsonify({'success': True, 'vault': vault})
    except Exception as e:
        app.logger.error(f"Error getting vault: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/vaults/<vault_id>', methods=['PUT'])
@require_auth
def api_update_vault(project_id, vault_id):
    """API: vault"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404

        vaults = _load_vaults(project_id)
        vault = next((v for v in vaults if v.get('id') == vault_id), None)
        if not vault:
            return jsonify({'success': False, 'error': 'Vault not found'}), 404

        data = request.json or {}
        if 'name' in data:
            name = (data.get('name') or '').strip()
            if not name:
                return jsonify({'success': False, 'error': 'Name cannot be empty'}), 400
            vault['name'] = name
        if 'keyId' in data or 'key_id' in data:
            new_key_id = data.get('keyId') or data.get('key_id', '').strip()
            if new_key_id:
                vault_keys_dir = get_project_vault_keys_dir(project_id)
                if not (vault_keys_dir / f'{new_key_id}.json').exists():
                    return jsonify({'success': False, 'error': 'Key not found'}), 404
                vault['keyId'] = new_key_id
        if 'vaultId' in data:
            vault['vaultId'] = (data.get('vaultId') or '').strip() or None
        if 'content' in data:
            vault['content'] = data.get('content') or ''

        vault['updatedAt'] = time.time()
        _save_vaults(project_id, vaults)

        app.logger.info(f"Vault updated: {vault_id} in project {project_id}")
        return jsonify({'success': True, 'vault': vault})
    except Exception as e:
        app.logger.error(f"Error updating vault: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/vaults/<vault_id>', methods=['DELETE'])
@require_auth
def api_delete_vault(project_id, vault_id):
    """API: vault"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404

        vaults = _load_vaults(project_id)
        vault = next((v for v in vaults if v.get('id') == vault_id), None)
        if not vault:
            return jsonify({'success': False, 'error': 'Vault not found'}), 404

        vaults = [v for v in vaults if v.get('id') != vault_id]
        _save_vaults(project_id, vaults)

        app.logger.info(f"Vault deleted: {vault_id} in project {project_id}")
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f"Error deleting vault: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/vaults/<vault_id>/encrypt', methods=['POST'])
@require_auth
def api_vault_encrypt(project_id, vault_id):
    """API: vault' (ansible-vault backend)"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        data = request.json or {}
        content = data.get('content', '')
        encrypted, err = _encrypt_content_if_requested(project_id, content, vault_id)
        if err:
            return jsonify({'success': False, 'error': err}), 400
        return jsonify({'success': True, 'content': encrypted})
    except Exception as e:
        app.logger.error(f"Error encrypting: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/vaults/<vault_id>/decrypt', methods=['POST'])
@require_auth
def api_vault_decrypt(project_id, vault_id):
    """API: vault' (ansible-vault backend)"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        data = request.json or {}
        content = data.get('content', '')
        decrypted, was_enc, _, _ = _decrypt_content_if_encrypted(project_id, content, vault_id)
        if was_enc and decrypted is None:
            return jsonify({'success': False, 'error': 'Failed to decrypt. Check vault key.'}), 400
        return jsonify({'success': True, 'content': decrypted or content})
    except Exception as e:
        app.logger.error(f"Error decrypting: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/vault-files/get', methods=['GET'])
@require_auth
def api_vault_file_get(project_id):
    """API: vault- (group_vars/host_vars)"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        path_param = request.args.get('path', '')
        full_path, err = _resolve_vault_file_path(project_id, path_param)
        if err:
            return jsonify({'success': False, 'error': err}), 400
        if not full_path.exists() or not full_path.is_file():
            return jsonify({'success': False, 'error': 'File not found'}), 404
        with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        vault_id = request.args.get('vault_id') or request.args.get('vaultId')
        key_id = request.args.get('keyId') or request.args.get('key_id')
        resp = {'success': True, 'content': content, 'path': path_param}
        if key_id and content and is_ansible_vault_encrypted(content):
            password = get_key_password_by_key_id(project_id, key_id)
            if not password:
                return jsonify({'success': False, 'error': 'Key not found'}), 404
            vault_id_header = parse_vault_id_from_header(content)
            ok, decrypted = ansible_vault_decrypt(content, password, vault_id=vault_id_header)
            if not ok:
                return jsonify({'success': False, 'error': decrypted or 'Failed to decrypt'}), 500
            resp['content'] = decrypted
            resp['keyId'] = key_id
        else:
            decrypted, was_encrypted, vault_id_used, vault_id_required = _decrypt_content_if_encrypted(project_id, content, vault_id)
            if was_encrypted:
                if vault_id_required:
                    vaults = _load_vaults(project_id)
                    keys = []
                    try:
                        vault_keys_dir = get_project_vault_keys_dir(project_id)
                        for meta_file in vault_keys_dir.glob('*.json'):
                            with open(meta_file, 'r', encoding='utf-8') as f:
                                meta = json.load(f)
                            kid = meta.get('id') or meta_file.stem
                            keys.append({'id': kid, 'name': meta.get('name', '')})
                    except Exception:
                        pass
                    return jsonify({
                        'success': True, 'encrypted': True, 'vaultIdRequired': True,
                        'vaults': vaults, 'keys': keys, 'path': path_param, 'content': content
                    })
                if decrypted is None:
                    return jsonify({'success': False, 'error': 'Failed to decrypt'}), 500
                resp['content'] = decrypted
                resp['encrypted'] = True
                resp['vaultId'] = vault_id_used
        return jsonify(resp)
    except Exception as e:
        app.logger.error(f"Error getting vault file: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/vault-files/encrypt', methods=['POST'])
@require_auth
def api_vault_file_encrypt(project_id):
    """API: keyId + vaultId (ansible-vault label)"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        data = request.json or {}
        content = data.get('content', '')
        key_id = data.get('keyId') or data.get('key_id')
        vault_label = (data.get('vaultId') or data.get('vault_id') or '').strip() or None
        if not key_id:
            return jsonify({'success': False, 'error': 'keyId required'}), 400
        if not vault_label:
            return jsonify({'success': False, 'error': 'vaultId (ansible-vault label) required'}), 400
        password = get_key_password_by_key_id(project_id, key_id)
        if not password:
            return jsonify({'success': False, 'error': 'Key not found'}), 404
        ok, result = ansible_vault_encrypt(content, password, vault_id=vault_label)
        if not ok:
            return jsonify({'success': False, 'error': result or 'Encryption failed'}), 400
        return jsonify({'success': True, 'content': result})
    except Exception as e:
        app.logger.error(f"Error encrypting vault file: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/vault-files/decrypt', methods=['POST'])
@require_auth
def api_vault_file_decrypt(project_id):
    """API: keyId"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        data = request.json or {}
        content = data.get('content', '')
        key_id = data.get('keyId') or data.get('key_id')
        if not key_id:
            return jsonify({'success': False, 'error': 'keyId required'}), 400
        if not content or not is_ansible_vault_encrypted(content):
            return jsonify({'success': True, 'content': content})
        password = get_key_password_by_key_id(project_id, key_id)
        if not password:
            return jsonify({'success': False, 'error': 'Key not found'}), 404
        vault_id_header = parse_vault_id_from_header(content)
        ok, result = ansible_vault_decrypt(content, password, vault_id=vault_id_header)
        if not ok:
            return jsonify({'success': False, 'error': result or 'Decryption failed'}), 400
        return jsonify({'success': True, 'content': result})
    except Exception as e:
        app.logger.error(f"Error decrypting vault file: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/vault-files/save', methods=['POST'])
@require_auth
def api_vault_file_save(project_id):
    """API: vault- ( )"""
    try:
        projects = load_projects()
        if not any(p.get('id') == project_id for p in projects):
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        data = request.json or {}
        path_param = data.get('path', '')
        content = data.get('content', '')
        vault_id = data.get('vaultId') or data.get('vault_id')
        key_id = data.get('keyId') or data.get('key_id')
        full_path, err = _resolve_vault_file_path(project_id, path_param)
        if err:
            return jsonify({'success': False, 'error': err}), 400
        if key_id and vault_id:
            password = get_key_password_by_key_id(project_id, key_id)
            if not password:
                return jsonify({'success': False, 'error': 'Key not found'}), 404
            ok, encrypted = ansible_vault_encrypt(content, password, vault_id=vault_id)
            if not ok:
                return jsonify({'success': False, 'error': encrypted or 'Encryption failed'}), 400
            content = encrypted
        elif vault_id:
            encrypted, err = _encrypt_content_if_requested(project_id, content, vault_id)
            if err:
                return jsonify({'success': False, 'error': err}), 400
            content = encrypted
        if key_id:
            _save_vault_file_key(project_id, path_param, key_id)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'success': True, 'path': path_param})
    except Exception as e:
        app.logger.error(f"Error saving vault file: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


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



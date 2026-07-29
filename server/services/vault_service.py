"""Vault and vault-file helpers.

Extracted from ``backend/app.py`` during the refactor. Behavior is
preserved verbatim; ``app.py`` re-imports these names so external callers
(``from app import _decrypt_content_if_encrypted`` etc.) keep working.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from utils.project_paths import (
    get_project_dir,
    get_project_secrets_dir,
    get_project_vault_keys_dir,
    get_project_vaults_file,
)
from utils.vault_utils import (
    ansible_vault_decrypt,
    ansible_vault_encrypt,
    is_ansible_vault_encrypted,
    parse_vault_id_from_header,
)

logger = logging.getLogger(__name__)


def _load_vault_file_keys(project_id):
    """Return mapping path -> keyId for vault-encrypted files (used by Save Key)."""
    f = get_project_vaults_file(project_id).parent / 'vault_file_keys.json'
    if not f.exists():
        return {}
    try:
        with open(f, 'r', encoding='utf-8') as fp:
            return json.load(fp)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_vault_file_key(project_id, path, key_id):
    """Persist mapping path -> keyId for vault-encrypted files."""
    mapping = _load_vault_file_keys(project_id)
    if key_id:
        mapping[path] = key_id
    else:
        mapping.pop(path, None)
    f = get_project_vaults_file(project_id).parent / 'vault_file_keys.json'
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, 'w', encoding='utf-8') as fp:
        json.dump(mapping, fp, indent=2, ensure_ascii=False)


def _scan_repo_vault_files(project_id):
    """Scan repo/ for vault-encrypted files under group_vars/host_vars/vars/."""
    repo_dir = get_project_dir(project_id) / 'repo'
    if not repo_dir.exists():
        return []
    vault_files = []
    try:
        for fp in repo_dir.rglob('*'):
            if not fp.is_file():
                continue
            parts = fp.relative_to(repo_dir).parts
            if 'group_vars' not in parts and 'host_vars' not in parts and 'vars' not in parts:
                continue
            try:
                with open(fp, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read(4096)
                if not content.strip().startswith('$ANSIBLE_VAULT'):
                    continue
                rel = fp.relative_to(repo_dir)
                path_str = str(rel).replace('\\', '/')
                key_label = parse_vault_id_from_header(content) or None
                st = fp.stat()
                created_ts = getattr(st, 'st_birthtime', None) or st.st_ctime
                updated_ts = st.st_mtime
                vault_files.append({
                    'path': path_str,
                    'key': key_label,
                    'created': datetime.utcfromtimestamp(created_ts).isoformat() + 'Z',
                    'updated': datetime.utcfromtimestamp(updated_ts).isoformat() + 'Z',
                })
            except (IOError, OSError):
                continue
    except Exception as e:
        logger.warning(f"Error scanning repo for vault files: {e}")
    return sorted(vault_files, key=lambda x: x['path'])


def _load_vaults(project_id):
    """Load vaults from secrets/vault/vaults.json (fallback: secrets/vaults.json)."""
    vaults_file = get_project_vaults_file(project_id)
    if not vaults_file.exists():
        old_path = get_project_secrets_dir(project_id) / 'vaults.json'
        if old_path.exists():
            vaults_file = old_path
        else:
            return []
    try:
        with open(vaults_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _enrich_vault_vault_id_from_content(vault):
    """Populate vaultId from the ansible-vault header when missing."""
    vid = (vault.get('vaultId') or '').strip()
    if vid:
        return vault
    content = vault.get('content') or ''
    if not content or not is_ansible_vault_encrypted(content):
        return vault
    parsed = parse_vault_id_from_header(content)
    if not parsed:
        return vault
    out = dict(vault)
    out['vaultId'] = parsed
    return out


def _save_vaults(project_id, vaults):
    """Persist vaults to secrets/vault/vaults.json."""
    vaults_file = get_project_vaults_file(project_id)
    vaults_file.parent.mkdir(parents=True, exist_ok=True)
    with open(vaults_file, 'w', encoding='utf-8') as f:
        json.dump(vaults, f, indent=2, ensure_ascii=False)


def get_key_password_for_vault(project_id, vault_uuid):
    """Resolve the password for a vault by its uuid.

    Args:
        project_id: project identifier.
        vault_uuid: vault uuid.

    Returns:
        Password string, or None if vault/key not found.
    """
    vaults = _load_vaults(project_id)
    vault = next((v for v in vaults if v.get('id') == vault_uuid), None)
    if not vault:
        return None
    key_id = vault.get('keyId')
    if not key_id:
        return None
    return get_key_password_by_key_id(project_id, key_id)


def get_key_password_by_key_id(project_id, key_id):
    """Return the password for the given key_id."""
    if not key_id:
        return None
    vault_keys_dir = get_project_vault_keys_dir(project_id)
    pass_file = vault_keys_dir / f'{key_id}.pass'
    if not pass_file.exists():
        return None
    try:
        with open(pass_file, 'r', encoding='utf-8') as f:
            return f.read().rstrip('\n')
    except Exception:
        return None


def _get_default_vault_password(project_id):
    """Legacy fallback: read secrets/vault/vault_pass (no vault_id)."""
    vault_dir = get_project_secrets_dir(project_id) / 'vault'
    pass_file = vault_dir / 'vault_pass'
    if not pass_file.exists():
        return None
    try:
        with open(pass_file, 'r', encoding='utf-8') as f:
            return f.read().rstrip('\n')
    except Exception:
        return None


def _decrypt_content_if_encrypted(project_id, content, vault_id=None):
    """Decrypt ``content`` if it is ansible-vault encrypted.

    Auto-resolves the vault when ``vault_id`` is not supplied by matching the
    header label against configured vaults, and finally falls back to trying
    every configured vault so legacy files remain visible.

    Returns:
        (decrypted_content, encrypted: bool, vault_id_used, vault_id_required: bool)
    """
    if not content or not is_ansible_vault_encrypted(content):
        return content, False, None, False

    vault_id_header = parse_vault_id_from_header(content)
    use_vault_id = vault_id_header if vault_id_header else None

    if vault_id:
        password = get_key_password_for_vault(project_id, vault_id)
        if not password:
            return None, True, None, False
        ok, result = ansible_vault_decrypt(content, password, use_vault_id)
        if ok:
            return result, True, vault_id, False
        return None, True, None, False

    vaults = _load_vaults(project_id)
    candidates = []
    if vault_id_header:
        for vault in vaults:
            labels = {str(vault.get('vaultId') or '').strip(), str(vault.get('name') or '').strip()}
            if vault_id_header in labels:
                candidates.append(vault)

    seen = {v.get('id') for v in candidates}
    candidates.extend(v for v in vaults if v.get('id') not in seen)

    for vault in candidates:
        key_id = vault.get('keyId')
        password = get_key_password_by_key_id(project_id, key_id)
        if not password:
            continue
        ok, result = ansible_vault_decrypt(content, password, use_vault_id)
        if ok:
            return result, True, vault.get('id'), False

    return None, True, None, True


def _encrypt_content_if_requested(project_id, content, vault_id):
    """Encrypt ``content`` with the given vault (uuid). Uses vault name as ansible-vault label."""
    if not vault_id:
        return content, None
    password = get_key_password_for_vault(project_id, vault_id)
    if not password:
        return None, 'Vault or its key not found'
    vaults = _load_vaults(project_id)
    vault = next((v for v in vaults if v.get('id') == vault_id), None)
    vault_label = (vault.get('vaultId') or vault.get('name')) if vault else None
    ok, result = ansible_vault_encrypt(content, password, vault_id=vault_label)
    if not ok:
        return None, result or 'Encryption failed'
    return result, None


def _resolve_vault_file_path(project_id, path_param):
    """Resolve a repo-relative path within group_vars/host_vars, safely."""
    if not path_param or '..' in path_param:
        return None, 'Invalid path'
    path_param = path_param.replace('\\', '/').strip('/')
    parts = path_param.split('/')
    if 'group_vars' not in parts and 'host_vars' not in parts:
        return None, 'Path must be within group_vars or host_vars'
    repo_dir = get_project_dir(project_id) / 'repo'
    full_path = (repo_dir / path_param).resolve()
    try:
        full_path.relative_to(repo_dir.resolve())
    except ValueError:
        return None, 'Path outside repo'
    return full_path, None

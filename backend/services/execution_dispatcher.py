"""Worker/execution dispatcher helpers.

Extracted from ``backend/app.py`` during the refactor. Behavior is
preserved verbatim; ``app.py`` re-exports these helpers so external
callers (including tests and blueprints) keep working.
"""
from __future__ import annotations

import json as _json
import logging
from pathlib import Path
from typing import Optional

from utils.project_paths import get_project_vault_keys_dir, get_project_vaults_file

logger = logging.getLogger(__name__)


def _check_execution_requirements(
    execution: dict,
    worker_capabilities: dict,
    worker_tags: list,
    worker_id: str = None,
) -> bool:
    """Return True when this worker satisfies the execution's requirements.

    Handles targeted worker_id pinning, required tags, and required
    capability key/value matches.
    """
    run_params = execution.get('runParams', {})
    requirements = run_params.get('requirements', {})

    required_worker_id = requirements.get('worker_id') or run_params.get('target_worker_id')
    if required_worker_id and worker_id and str(required_worker_id) != str(worker_id):
        return False

    required_tags = requirements.get('tags', [])
    if required_tags:
        worker_tags_set = set(worker_tags or [])
        required_tags_set = set(required_tags)
        if not required_tags_set.issubset(worker_tags_set):
            return False

    required_caps = requirements.get('capabilities', {})
    if required_caps:
        for cap_key, cap_value in required_caps.items():
            worker_cap_value = worker_capabilities.get(cap_key)
            if worker_cap_value != cap_value:
                return False

    return True


def _resolve_execution_inventory_path(project_dir: Path, inv_file: str) -> Path:
    repo_dir = project_dir / 'repo'
    inv_value = str(inv_file or 'inventory.yml')
    candidate = Path(inv_value)
    if candidate.is_absolute():
        return candidate
    if inv_value.startswith('inventories/') or ('.' in inv_value and '/' in inv_value):
        return repo_dir / inv_value
    if inv_value == 'inventory.yml':
        direct = repo_dir / 'inventory.yml'
        if direct.exists():
            return direct

    for root in (repo_dir / 'IaC' / 'ansible' / 'inventories', repo_dir / 'inventories'):
        if root.exists():
            found = next((p for p in root.rglob(Path(inv_value).name) if p.is_file()), None)
            if found:
                return found

    return repo_dir / inv_value


def _read_text_file_for_worker(path: Path) -> Optional[str]:
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding='utf-8')
    except Exception as e:
        logger.debug(f"[Worker Claim] Could not read transfer file {path}: {e}")
    return None


def _collect_inventory_var_payloads(inv_path: Path, dirname: str) -> list:
    var_dir = inv_path.parent / dirname
    payloads = []
    if not var_dir.exists() or not var_dir.is_dir():
        return payloads
    for item in var_dir.rglob('*'):
        if not item.is_file():
            continue
        content = _read_text_file_for_worker(item)
        if content is None:
            continue
        try:
            rel = str(item.relative_to(var_dir))
        except ValueError:
            rel = item.name
        payloads.append({'relative_path': rel, 'content': content})
    return payloads


def _attach_worker_transfer_payload(execution: dict) -> None:
    """Attach transient file contents so remote workers do not need backend-local paths."""
    try:
        run_params = execution.get('runParams') if isinstance(execution, dict) else None
        if not isinstance(run_params, dict):
            return

        temp_playbook = run_params.get('temp_playbook')
        if temp_playbook and not run_params.get('playbook_content'):
            content = _read_text_file_for_worker(Path(temp_playbook))
            if content is not None:
                run_params['playbook_content'] = content

        project_dir_raw = run_params.get('project_dir')
        project_dir = Path(project_dir_raw) if project_dir_raw else None
        if project_dir and project_dir.exists() and not run_params.get('inventory_payloads'):
            inventory_payloads = []
            for inv_file in run_params.get('inventory_files') or []:
                inv_path = _resolve_execution_inventory_path(project_dir, inv_file)
                content = _read_text_file_for_worker(inv_path)
                if content is None:
                    continue
                inventory_payloads.append({
                    'requested': inv_file,
                    'filename': inv_path.name,
                    'content': content,
                    'group_vars': _collect_inventory_var_payloads(inv_path, 'group_vars'),
                    'host_vars': _collect_inventory_var_payloads(inv_path, 'host_vars'),
                })
            if inventory_payloads:
                run_params['inventory_payloads'] = inventory_payloads

            ansible_config = run_params.get('ansible_config')
            if ansible_config and not run_params.get('ansible_config_content'):
                if str(ansible_config).startswith('ansible-config/'):
                    cfg_path = project_dir / str(ansible_config)
                elif str(ansible_config) == 'ansible.cfg' or str(ansible_config).endswith('.cfg'):
                    repo_cfg = project_dir / 'repo' / 'ansible.cfg'
                    cfg_path = repo_cfg if repo_cfg.exists() else project_dir / 'ansible-config' / str(ansible_config)
                else:
                    cfg_path = project_dir / 'ansible-config' / str(ansible_config)
                cfg_content = _read_text_file_for_worker(cfg_path)
                if cfg_content is not None:
                    run_params['ansible_config_content'] = cfg_content

        # Vault credentials: remote workers can't read the backend's
        # secrets/vault_keys/*.pass files, so ship them in the claim payload.
        if not run_params.get('vault_credentials'):
            try:
                project_id_for_vault = execution.get('projectId') if isinstance(execution, dict) else None
                if project_id_for_vault:
                    vaults_file = get_project_vaults_file(project_id_for_vault)
                    vault_keys_dir = get_project_vault_keys_dir(project_id_for_vault)
                    vaults_list = []
                    if vaults_file.exists():
                        try:
                            with open(vaults_file, 'r', encoding='utf-8') as vf:
                                vaults_list = _json.load(vf) or []
                        except Exception:
                            vaults_list = []
                    creds = []
                    for v in vaults_list:
                        key_id = v.get('keyId')
                        if not key_id:
                            continue
                        pass_file = vault_keys_dir / f'{key_id}.pass'
                        if not pass_file.exists():
                            continue
                        try:
                            with open(pass_file, 'r', encoding='utf-8') as pf:
                                password = pf.read().rstrip('\n')
                        except Exception:
                            continue
                        if not password:
                            continue
                        label = (v.get('vaultId') or v.get('name') or '').strip()
                        creds.append({
                            'id': str(v.get('id') or ''),
                            'label': label,
                            'password': password,
                        })
                    if creds:
                        run_params['vault_credentials'] = creds
            except Exception as _ve:
                logger.warning(f"[Worker Claim] Could not attach vault credentials: {_ve}")
    except Exception as e:
        logger.warning(f"[Worker Claim] Could not attach transfer payload: {e}")

"""Project-directory path helpers.
"""
from __future__ import annotations

from pathlib import Path

from app_context import get_projects_dir


def get_project_dir(project_id):
    """Return the on-disk directory for a project."""
    return get_projects_dir() / project_id


# ---------- simple per-project subpaths ----------

def get_project_executions_dir(project_id):
    """Executions directory (Project Storage layout: ``history/executions/``)."""
    return get_project_dir(project_id) / 'history' / 'executions'


def get_project_logs_dir(project_id):
    """Logs directory (``history/logs/``)."""
    return get_project_dir(project_id) / 'history' / 'logs'


def get_project_roles_config_file(project_id):
    """Path to the project's ``data/roles-config.json``."""
    return get_project_dir(project_id) / 'data' / 'roles-config.json'


def get_project_secrets_dir(project_id):
    """Secrets directory (``secrets/``), created if missing."""
    secrets_dir = get_project_dir(project_id) / 'secrets'
    secrets_dir.mkdir(parents=True, exist_ok=True)
    return secrets_dir


def get_project_vault_dir(project_id):
    """Vault directory (``secrets/vault/``), created if missing."""
    vault_dir = get_project_secrets_dir(project_id) / 'vault'
    vault_dir.mkdir(parents=True, exist_ok=True)
    return vault_dir


def get_project_vault_keys_dir(project_id):
    """Vault-keys directory (``secrets/vault_keys/``), created if missing."""
    vault_keys_dir = get_project_secrets_dir(project_id) / 'vault_keys'
    vault_keys_dir.mkdir(parents=True, exist_ok=True)
    return vault_keys_dir


def get_project_vaults_file(project_id):
    """Path to ``secrets/vault/vaults.json``."""
    return get_project_vault_dir(project_id) / 'vaults.json'


def get_project_config_file(project_id):
    """Path to ``project.json``."""
    return get_project_dir(project_id) / 'project.json'


def get_project_host_status_file(project_id):
    """Path to ``host_status.json``."""
    return get_project_dir(project_id) / 'host_status.json'


def get_project_generated_playbook_path(project_id, execution_id):
    """Path to a generated playbook (``runtime/generated_playbooks/<execution_id>.yml``),
    parent directories created as needed."""
    playbook_path = get_project_dir(project_id) / 'runtime' / 'generated_playbooks' / f'{execution_id}.yml'
    playbook_path.parent.mkdir(parents=True, exist_ok=True)
    return playbook_path


# ---------- ansible-config path resolution ----------

def resolve_ansible_config_path(project_id, selected_ansible_config):
    """Resolve an ansible.cfg path under ``<project>/ansible-config/`` safely.

    Accepts either a bare filename (``ansible.cfg``) or a relative path already
    prefixed with ``ansible-config/``. Raises ``ValueError`` on traversal.
    """
    project_dir = get_project_dir(project_id)
    project_resolved = project_dir.resolve()
    if selected_ansible_config and selected_ansible_config.startswith('ansible-config/'):
        candidate = (project_dir / selected_ansible_config).resolve()
    else:
        candidate = (project_dir / 'ansible-config' / (selected_ansible_config or 'ansible.cfg')).resolve()
    try:
        candidate.relative_to(project_resolved)
    except ValueError:
        raise ValueError(f"invalid ansible_config path: {selected_ansible_config!r}")
    return candidate


# ---------- repo-layout entity resolution ----------

def resolve_repo_path(project_id, entity_type):
    """Return the on-disk path to an entity directory in Project Storage.

    Always returns the LOCAL path; ``repoLayout`` values are only consulted
    during Git sync (not here). ``entity_type`` must be one of ``playbooks``,
    ``roles``, ``inventories``. Prefers ``IaC/ansible/<entity>`` when present.
    """
    valid_entity_types = ['playbooks', 'roles', 'inventories']
    if entity_type not in valid_entity_types:
        raise ValueError(f"Invalid entity_type: {entity_type}. Must be one of {valid_entity_types}")

    project_dir = get_project_dir(project_id)
    repo_base = project_dir / 'repo'
    iac_ansible_path = repo_base / 'IaC' / 'ansible' / entity_type
    if iac_ansible_path.exists():
        return iac_ansible_path
    return repo_base / entity_type


def get_project_inventories_dir(project_id):
    """Inventories directory (via ``resolve_repo_path``)."""
    return resolve_repo_path(project_id, 'inventories')


def get_project_roles_dir(project_id):
    """Roles directory (via ``resolve_repo_path``)."""
    return resolve_repo_path(project_id, 'roles')


def get_project_playbooks_dir(project_id):
    """Playbooks directory (via ``resolve_repo_path``)."""
    return resolve_repo_path(project_id, 'playbooks')


def get_project_inventory_file(project_id):
    """Locate the primary inventory file for a project.

    Checks ``inventory.yaml``, ``inventory.yml``, ``hosts.yaml``, ``hosts.yml``,
    ``hosts``, ``hosts.ini`` at the inventories root first, then any of those
    names anywhere under the inventories tree (excluding ``group_vars`` /
    ``host_vars`` subtrees). Falls back to ``<inventories>/inventory.yml``.
    """
    inventories_dir = get_project_inventories_dir(project_id)
    inventory_names = ['inventory.yaml', 'inventory.yml', 'hosts.yaml', 'hosts.yml', 'hosts', 'hosts.ini']

    for name in inventory_names:
        root_inventory = inventories_dir / name
        if root_inventory.exists():
            return root_inventory

    if inventories_dir.exists():
        for inv_file in sorted(inventories_dir.rglob('*')):
            if inv_file.is_file() and inv_file.name in inventory_names:
                rel = inv_file.relative_to(inventories_dir)
                if 'group_vars' not in rel.parts and 'host_vars' not in rel.parts:
                    return inv_file
        for inv_file in sorted(inventories_dir.rglob('*')):
            if not inv_file.is_file():
                continue
            if inv_file.suffix.lower() not in ('.yml', '.yaml', '.ini'):
                continue
            rel = inv_file.relative_to(inventories_dir)
            if 'group_vars' in rel.parts or 'host_vars' in rel.parts:
                continue
            return inv_file

    return inventories_dir / 'inventory.yml'


def resolve_inventory_file_path(project_id, file_param, must_exist=False):
    """Resolve a client-supplied inventory file reference to an on-disk path.
    """
    inventories_dir = get_project_inventories_dir(project_id)
    repo_dir = get_project_dir(project_id) / 'repo'

    if not file_param:
        return get_project_inventory_file(project_id)

    raw = str(file_param).strip().lstrip('/')
    while raw.startswith('./'):
        raw = raw[2:]
    if '..' in Path(raw).parts:
        raise ValueError(f'invalid inventory file path: {file_param!r}')

    stripped = raw
    for prefix in ('IaC/ansible/inventories/', 'ansible/inventories/', 'inventories/'):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break

    candidates = [
        inventories_dir / stripped,
        inventories_dir / raw,
        repo_dir / raw,
        inventories_dir / Path(raw).name,
    ]

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    if must_exist:
        return None
    return inventories_dir / stripped

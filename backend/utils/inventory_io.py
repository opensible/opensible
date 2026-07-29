"""Inventory I/O helpers (INI parsing).

Extracted from app.py during Phase 1 of the refactor. Only pure,
dependency-free parsing helpers live here. Higher-level helpers that
depend on project state remain in app.py until Phase 2.
"""
from __future__ import annotations

from pathlib import Path


def is_ini_inventory_file(file_path) -> bool:
    """True if the file looks like an Ansible INI inventory (``*.ini`` or the
    conventional ``hosts`` file)."""
    p = Path(file_path) if not isinstance(file_path, Path) else file_path
    return p.suffix == '.ini' or p.name == 'hosts'


def parse_ini_key_value(line: str):
    """Parse a ``key=value`` line. Strips matching surrounding single or
    double quotes from the value. Returns ``(None, None)`` if there is no
    ``=`` at a valid position."""
    eq = line.find('=')
    if eq <= 0:
        return None, None
    key = line[:eq].strip()
    val = line[eq + 1:].strip()
    if len(val) >= 2 and (
        (val.startswith("'") and val.endswith("'"))
        or (val.startswith('"') and val.endswith('"'))
    ):
        val = val[1:-1]
    return key, val


def parse_ini_inventory(file_path):
    """Parse an Ansible INI inventory (``hosts.ini``/``hosts``) into the same
    dict shape used by the YAML inventory path.

    Supports ``[group]``, ``[group:children]``, ``[all:vars]``, ``[group:vars]``
    sections and inline host vars (``host k=v k2=v2``).
    """
    path = Path(file_path)
    if not path.exists():
        return None
    groups_raw = {}
    current_section = None
    current_kind = None
    all_vars = {}
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line_stripped = line.strip()
            if not line_stripped or line_stripped.startswith('#'):
                continue
            if line_stripped.startswith('[') and line_stripped.endswith(']'):
                inner = line_stripped[1:-1].strip()
                if ':' in inner:
                    name, kind = inner.split(':', 1)
                    name, kind = name.strip(), kind.strip().lower()
                    current_section = name
                    current_kind = kind
                    groups_raw.setdefault(name, {'hosts': [], 'children': [], 'vars': {}})
                    if kind == 'vars':
                        if name == 'all':
                            all_vars = {}
                        else:
                            groups_raw[name]['vars'] = {}
                    elif kind == 'children':
                        groups_raw[name]['children'] = []
                else:
                    current_section = inner
                    current_kind = None
                    groups_raw.setdefault(inner, {'hosts': [], 'children': [], 'vars': {}})
                continue
            if current_section is None:
                continue
            if current_kind == 'vars':
                key, val = parse_ini_key_value(line_stripped)
                if key is not None:
                    if current_section == 'all':
                        all_vars[key] = val
                    else:
                        groups_raw[current_section]['vars'][key] = val
                continue
            if current_kind == 'children':
                group_name = line_stripped.split()[0] if line_stripped else ''
                if group_name and not group_name.startswith('#'):
                    groups_raw[current_section]['children'].append(group_name)
                continue
            parts = line_stripped.split()
            if not parts or parts[0].startswith('#'):
                continue
            host_name = parts[0]
            if ':' in host_name and not host_name.startswith('['):
                host_name = host_name.split(':')[0]
            host_vars = {}
            for part in parts[1:]:
                if '=' in part:
                    k, v = parse_ini_key_value(part)
                    if k is not None:
                        host_vars[k] = v
            if host_vars:
                groups_raw[current_section]['hosts'].append((host_name, host_vars))
            else:
                groups_raw[current_section]['hosts'].append(host_name)
    if all_vars:
        groups_raw.setdefault('all', {'hosts': [], 'children': [], 'vars': {}})
        groups_raw['all']['vars'] = all_vars

    def build_group(name, raw):
        hosts = {}
        for h in raw['hosts']:
            if isinstance(h, tuple):
                hosts[h[0]] = h[1]
            else:
                hosts[h] = None
        children = {}
        for child_name in raw.get('children') or []:
            if child_name in groups_raw and child_name != 'all':
                children[child_name] = build_group(child_name, groups_raw[child_name])
        result = {'hosts': hosts, 'children': children}
        if raw.get('vars'):
            result['vars'] = raw['vars']
        return result

    all_children = {name: build_group(name, raw) for name, raw in groups_raw.items() if name != 'all'}
    root = {'children': all_children}
    if all_vars:
        root['vars'] = all_vars
    return {'all': root}


import logging as _logging

_logger = _logging.getLogger(__name__)


def extract_hosts_from_group(group_data, hosts_set, hosts_list, parent_path='', inventory_file=None):
    """Recursively collect hosts from an inventory group dict.

    ``hosts_set`` is used for de-duplication; ``hosts_list`` receives entries
    of shape ``{'name', 'vars_file', 'inventory_file', 'vars'?}``. When a host
    already exists, its ``inventory_file`` (and any inline vars) are updated to
    reflect the latest occurrence.
    """
    if not isinstance(group_data, dict):
        return

    if 'hosts' in group_data:
        hosts = group_data['hosts']
        if isinstance(hosts, dict):
            for host, config in hosts.items():
                if isinstance(host, str) and host.startswith('#'):
                    continue
                if not isinstance(host, str):
                    continue
                if host not in hosts_set:
                    vars_file = None
                    inline_vars = {}
                    if isinstance(config, dict):
                        vars_file = config.get('vars_file')
                        inline_vars = {k: v for k, v in config.items() if k != 'vars_file' and v is not None}
                    if not vars_file:
                        vars_file = f'host_vars/{host}.yml'
                    entry = {'name': host, 'vars_file': vars_file, 'inventory_file': inventory_file}
                    if inline_vars:
                        entry['vars'] = inline_vars
                    hosts_list.append(entry)
                    hosts_set.add(host)
                else:
                    for existing_host in hosts_list:
                        if existing_host['name'] == host:
                            existing_host['inventory_file'] = inventory_file
                            if isinstance(config, dict):
                                if config.get('vars_file'):
                                    existing_host['vars_file'] = config['vars_file']
                                inline_vars = {k: v for k, v in config.items() if k != 'vars_file' and v is not None}
                                if inline_vars:
                                    existing_host['vars'] = inline_vars
                            break
        elif isinstance(hosts, list):
            for host in hosts:
                if isinstance(host, str) and not host.startswith('#'):
                    if host not in hosts_set:
                        hosts_list.append({
                            'name': host,
                            'vars_file': f'host_vars/{host}.yml',
                            'inventory_file': inventory_file,
                        })
                        hosts_set.add(host)
                    else:
                        for existing_host in hosts_list:
                            if existing_host['name'] == host:
                                old_file = existing_host['inventory_file']
                                existing_host['inventory_file'] = inventory_file
                                _logger.debug(f"Updated host {host} inventory_file from {old_file} to {inventory_file}")
                                break

    if 'children' in group_data:
        children = group_data['children']
        if isinstance(children, dict):
            for child_name, child_data in children.items():
                if isinstance(child_data, dict):
                    extract_hosts_from_group(
                        child_data, hosts_set, hosts_list,
                        f"{parent_path}.{child_name}" if parent_path else child_name,
                        inventory_file,
                    )


def extract_groups_from_inventory(group_data, groups_dict, parent_path='', inventory_file=None):
    """Recursively collect groups from an inventory dict into ``groups_dict``.

    Each entry is ``{'hosts': [...], 'children': [...], 'inventory_file': ..., 'vars': {...}?}``.
    """
    if not isinstance(group_data, dict):
        return
    current_group = parent_path if parent_path else 'all'
    if 'vars' in group_data and isinstance(group_data['vars'], dict) and group_data['vars']:
        if current_group not in groups_dict:
            groups_dict[current_group] = {'hosts': [], 'children': [], 'inventory_file': inventory_file}
        groups_dict[current_group]['vars'] = group_data['vars']
    if 'hosts' in group_data:
        hosts = group_data['hosts']
        host_list = []
        if isinstance(hosts, dict):
            for host, _config in hosts.items():
                if isinstance(host, str) and not host.startswith('#'):
                    host_list.append(host)
        elif isinstance(hosts, list):
            for host in hosts:
                if isinstance(host, str) and not host.startswith('#'):
                    host_list.append(host)
        if host_list:
            current_group = parent_path if parent_path else 'all'
            if current_group not in groups_dict:
                groups_dict[current_group] = {'hosts': [], 'children': [], 'inventory_file': inventory_file}
            groups_dict[current_group]['hosts'].extend(host_list)
            if inventory_file and not groups_dict[current_group].get('inventory_file'):
                groups_dict[current_group]['inventory_file'] = inventory_file

    if 'children' in group_data:
        children = group_data['children']
        children_list = []
        if isinstance(children, dict):
            for child_name, child_data in children.items():
                if isinstance(child_name, str) and not child_name.startswith('#'):
                    children_list.append(child_name)
                    if isinstance(child_data, dict):
                        child_path = child_name
                        if child_path not in groups_dict:
                            groups_dict[child_path] = {'hosts': [], 'children': [], 'inventory_file': inventory_file}
                        extract_groups_from_inventory(child_data, groups_dict, child_path, inventory_file)
        elif isinstance(children, list):
            for child_item in children:
                if isinstance(child_item, str) and not child_item.startswith('#'):
                    children_list.append(child_item)
                elif isinstance(child_item, dict):
                    for child_name, child_data in child_item.items():
                        if isinstance(child_name, str) and not child_name.startswith('#'):
                            children_list.append(child_name)
                            if isinstance(child_data, dict):
                                child_path = child_name
                                if child_path not in groups_dict:
                                    groups_dict[child_path] = {'hosts': [], 'children': [], 'inventory_file': inventory_file}
                                extract_groups_from_inventory(child_data, groups_dict, child_path, inventory_file)
        if children_list:
            current_group = parent_path if parent_path else 'all'
            if current_group not in groups_dict:
                groups_dict[current_group] = {'hosts': [], 'children': [], 'inventory_file': inventory_file}
            groups_dict[current_group]['children'].extend(children_list)
            if inventory_file and not groups_dict[current_group].get('inventory_file'):
                groups_dict[current_group]['inventory_file'] = inventory_file


def find_host_in_inventory(inv_data, host_name):
    """Locate a host within a parsed inventory dict.

    Returns the host's vars dict (``{}`` when the host exists without vars) or
    ``None`` if not found. Searches ``all.hosts`` first, then descends into
    ``all.children`` recursively.
    """
    if not inv_data or 'all' not in inv_data:
        _logger.debug("[find_host_in_inventory] No 'all' section in inventory data")
        return None

    all_section = inv_data['all']

    if 'hosts' in all_section and isinstance(all_section['hosts'], dict):
        if host_name in all_section['hosts']:
            _logger.debug(f"[find_host_in_inventory] Found host {host_name} directly in all.hosts")
            host_value = all_section['hosts'][host_name]
            return host_value if host_value is not None else {}

    if 'children' in all_section and isinstance(all_section['children'], dict):
        for group_name, group_data in all_section['children'].items():
            _logger.debug(f"[find_host_in_inventory] Checking group {group_name}, type: {type(group_data)}")
            if isinstance(group_data, dict) and 'hosts' in group_data:
                hosts_in_group = group_data['hosts']
                if isinstance(hosts_in_group, dict):
                    if host_name in hosts_in_group:
                        _logger.info(f"[find_host_in_inventory] Found host {host_name} in group {group_name}")
                        host_value = hosts_in_group[host_name]
                        return host_value if host_value is not None else {}
                elif isinstance(hosts_in_group, list):
                    if host_name in hosts_in_group:
                        _logger.info(f"[find_host_in_inventory] Found host {host_name} in group {group_name} (list)")
                        return {}
            if isinstance(group_data, dict) and 'children' in group_data:
                result = find_host_in_inventory({'all': group_data}, host_name)
                if result is not None:
                    _logger.info(f"[find_host_in_inventory] Found host {host_name} in nested children of group {group_name}")
                    return result

    _logger.debug(f"[find_host_in_inventory] Host {host_name} not found in inventory")
    return None

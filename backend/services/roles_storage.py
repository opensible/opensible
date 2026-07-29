"""Roles storage + role-details service (Phase 3 extraction).

Extracted from backend/app.py.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import re as _re
import sys

import yaml
from flask import jsonify

from utils.project_paths import get_project_dir, get_project_roles_config_file  # noqa: F401

logger = logging.getLogger("app")


def _app():
    for module_name in ("__main__", "app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "get_default_project_id"):
            return module
    return importlib.import_module("app")


def get_default_project_id():
    return _app().get_default_project_id()


def resolve_project_source(project_id, source_key):
    try:
        from services.sources_service import resolve_project_source as _resolve_project_source
        return _resolve_project_source(project_id, source_key)
    except Exception:
        return _app().resolve_project_source(project_id, source_key)


def scan_roles_storage(project_id=None):
    """ roles-storage ( Source Resolver)"""
    def build_tree(path, parent_id='', base_path=None):
        """ """
        if base_path is None:
            base_path = path
        nodes = []
        if not path.exists() or not path.is_dir():
            return nodes
        
        try:
            for item in sorted(path.iterdir()):
                if not item.is_dir():
                    continue
                
                item_name = item.name
                item_id = f"{parent_id}/{item_name}" if parent_id else item_name
                
                # : pack ( ) role/folder
                # , tasks/main.yaml tasks/main.yml - 
                has_tasks_main = (item / 'tasks' / 'main.yaml').exists() or (item / 'tasks' / 'main.yml').exists()
                
                if not parent_id:
                    # : tasks/main.yaml, , pack
                    if has_tasks_main:
                        node_type = "role"
                    else:
                        node_type = "pack"
                else:
                    # : tasks/main.yaml, , folder
                    if has_tasks_main:
                        node_type = "role"
                    else:
                        node_type = "folder"
                
                # Return project-relative path only (no absolute paths)
                # Path is relative to base_path (which is Project Storage root)
                node = {
                    'id': item_id,
                    'name': item_name,
                    'path': str(item.relative_to(base_path)),
                    'type': node_type,
                    'children': []
                }
                
                children = build_tree(item, item_id, base_path)
                node['children'] = children
                
                nodes.append(node)
        except PermissionError:
            logger.warning(f"Permission denied reading {path}")
        except Exception as e:
            logger.error(f"Error scanning {path}: {e}")
        
        return nodes
    
    # Resolve repo source for roles
    # REQUIRED: project_id must be provided
    if not project_id:
        logger.error("[scan_roles_storage] project_id is required")
        return []
    
    # resolve_project_source() ALWAYS returns Project Storage path
    try:
        source = resolve_project_source(project_id, 'repo')
        repo_path = source['rootPath']
        roles_storage_path = repo_path / 'roles'
    except Exception as e:
        logger.error(f"[scan_roles_storage] Failed to resolve Project Storage: {e}")
        return []  # Fail-fast, no fallback
    
    if not roles_storage_path.exists():
        return []
    
    return build_tree(roles_storage_path, base_path=roles_storage_path)


def load_roles_config(project_id=None):
    """ """
    default_config = {
        'storageRoot': 'roles-storage',
        'packs': [],
        'configByPackId': {}
    }
    
    # project_id , Default Project
    if not project_id:
        project_id = get_default_project_id()
    
    try:
        config_file = get_project_roles_config_file(project_id)
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # , 
                result = {
                    'storageRoot': config.get('storageRoot', 'roles-storage'),
                    'packs': config.get('packs', []),
                    'configByPackId': {}
                }
                
                # requiredRoles roleColors , 
                for pack_id, pack_config in config.get('configByPackId', {}).items():
                    result['configByPackId'][pack_id] = {
                        'enabled': pack_config.get('enabled', True),
                        'rolesEnabled': pack_config.get('rolesEnabled', {}),
                        'requiredRoles': pack_config.get('requiredRoles', []),
                        'roleColors': pack_config.get('roleColors', {})
                    }
                
                return result
    except Exception as e:
        logger.error(f"Error loading roles configuration: {e}")
    
    return default_config


def save_roles_config(config, project_id=None):
    """ """
    try:
        # project_id , Default Project
        if not project_id:
            project_id = get_default_project_id()
        
        sanitized_config = {
            'storageRoot': str(config.get('storageRoot', 'roles-storage')),
            'packs': config.get('packs', []),
            'configByPackId': {}
        }
        
        # configByPackId - 
        for pack_id, pack_config in config.get('configByPackId', {}).items():
            # directory traversal
            if '..' in pack_id or '/' in pack_id.lstrip('/'):
                continue
            
            sanitized_config['configByPackId'][pack_id] = {
                'enabled': bool(pack_config.get('enabled', True)),
                'rolesEnabled': {
                    role_id: bool(enabled) 
                    for role_id, enabled in pack_config.get('rolesEnabled', {}).items()
                    if '..' not in role_id
                },
                'requiredRoles': [
                    role_id 
                    for role_id in pack_config.get('requiredRoles', [])
                    if '..' not in role_id and isinstance(role_id, str)
                ],
                'roleColors': {
                    role_id: str(color)
                    for role_id, color in pack_config.get('roleColors', {}).items()
                    if '..' not in role_id and isinstance(color, str) and color.startswith('#') and len(color) == 7
                }
            }
        
        config_file = get_project_roles_config_file(project_id)
        config_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(sanitized_config, f, indent=2, ensure_ascii=False)
        
        return True
    except Exception as e:
        logger.error(f"Error saving roles configuration: {e}")
        return False


# Phase 3 refactor: roles storage & config routes moved to
# backend/api/roles_routes.py.



# ==================== Role Details & Variables Extraction ====================

import re
from collections import defaultdict

# Ansible, 
ANSIBLE_SYSTEM_VARS = {
    'ansible_', 'inventory_hostname', 'group_names', 'groups', 'hostvars',
    'play_hosts', 'playbook_dir', 'role_path', 'role_name', 'ansible_version'
}

# , 
ANSIBLE_LITERALS = {
    'false', 'true', 'present', 'absent', 'yes', 'no', 'on', 'off',
    'Ubuntu', 'Debian', 'CentOS', 'RedHat', 'Fedora',
    'password', 'key', 'root', 'inet', 'no', 'yes'
}

def extract_variables_from_yaml_text(text, file_path=''):
    """ YAML (Jinja2 + when )"""
    variables = defaultdict(lambda: {
        'occurrences': [],
        'files': set(),
        'examples': []
    })
    
    lines = text.split('\n')
    for line_num, line in enumerate(lines, 1):
        if line.strip().startswith('#'):
            continue
        
        # 1. {{ ... }}
        jinja_pattern = r'\{\{([^}]+)\}\}'
        jinja_matches = re.finditer(jinja_pattern, line)
        
        for jinja_match in jinja_matches:
            jinja_content = jinja_match.group(1).strip()
            
            if not jinja_content or jinja_content.startswith('#'):
                continue
            
            var_names = []
            
            # 1. : {{ var }}, {{ var_name }}
            simple_var = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)', jinja_content)
            if simple_var:
                var_name = simple_var.group(1)
                # ( )
                base_var = var_name.split('.')[0]
                # : 
                if (base_var not in ANSIBLE_SYSTEM_VARS and 
                    base_var not in ANSIBLE_LITERALS and
                    not any(base_var.startswith(sys_var) for sys_var in ANSIBLE_SYSTEM_VARS) and
                    not any(base_var.lower() == lit.lower() for lit in ANSIBLE_LITERALS)):
                    var_names.append((base_var, 'direct'))
            
            # 2. : {{ var | default(...) }}, {{ var | other }}
            filter_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\s*\|', jinja_content)
            if filter_match:
                var_name = filter_match.group(1)
                base_var = var_name.split('.')[0]
                # : 
                if (base_var not in ANSIBLE_SYSTEM_VARS and 
                    base_var not in ANSIBLE_LITERALS and
                    not any(base_var.startswith(sys_var) for sys_var in ANSIBLE_SYSTEM_VARS) and
                    not any(base_var.lower() == lit.lower() for lit in ANSIBLE_LITERALS)):
                    var_names.append((base_var, 'default'))
            
            # 3. : {{ dict[key] }}, {{ dict[var] }}
            dict_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\[([^\]]+)\]', jinja_content)
            if dict_match:
                dict_name = dict_match.group(1)
                key_expr = dict_match.group(2).strip()
                # key - ( )
                if not (key_expr.startswith('"') or key_expr.startswith("'") or key_expr.startswith("'")):
                    # key
                    key_var_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)', key_expr)
                    if key_var_match:
                        key_var = key_var_match.group(1)
                        base_key_var = key_var.split('.')[0]
                        if base_key_var not in ANSIBLE_SYSTEM_VARS:
                            var_names.append((base_key_var, 'dict_access'))
                # dict 
                base_dict = dict_name.split('.')[0]
                if base_dict not in ANSIBLE_SYSTEM_VARS and base_dict != 'hostvars':
                    var_names.append((base_dict, 'dict'))
            
            # 4. hostvars[inventory_hostname].my_var
            hostvars_match = re.match(r'hostvars\[([^\]]+)\]\.([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)', jinja_content)
            if hostvars_match:
                host_key = hostvars_match.group(1)
                var_name = hostvars_match.group(2)
                base_var = var_name.split('.')[0]
                if base_var not in ANSIBLE_SYSTEM_VARS:
                    var_names.append((base_var, 'hostvars'))
            
            for var_name, var_type in var_names:
                variables[var_name]['occurrences'].append({
                    'line': line_num,
                    'context': line.strip()[:100],
                    'type': var_type,
                    'full_match': jinja_match.group(0)
                })
                variables[var_name]['files'].add(file_path)
                
                # ( 2)
                if len(variables[var_name]['examples']) < 2:
                    variables[var_name]['examples'].append({
                        'line': line_num,
                        'context': line.strip()[:80]
                    })
        
        # 2. when : when: var_name, when: var_name == ..., when: not var_name, when: var_name is defined
        # : when: var_name | default(false)
        when_match = re.search(r'when:\s*(.+?)(?:\n|$)', line, re.IGNORECASE)
        if when_match:
            when_expr = when_match.group(1).strip()
            
            # : var_name | default(...)
            filter_pattern = r'\b([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\s*\|\s*default\s*\('
            filter_matches = re.finditer(filter_pattern, when_expr)
            for filter_match in filter_matches:
                var_name = filter_match.group(1)
                base_var = var_name.split('.')[0]
                if base_var and base_var not in ANSIBLE_SYSTEM_VARS and not any(base_var.startswith(sys_var) for sys_var in ANSIBLE_SYSTEM_VARS):
                    variables[base_var]['occurrences'].append({
                        'line': line_num,
                        'context': line.strip()[:100],
                        'type': 'when',
                        'full_match': when_expr[:80]
                    })
                    variables[base_var]['files'].add(file_path)
                    if len(variables[base_var]['examples']) < 2:
                        variables[base_var]['examples'].append({
                            'line': line_num,
                            'context': line.strip()[:80]
                        })
            
            # when 
            when_var_patterns = [
                r'\b([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\s*(?:==|!=|>|<|is\s+(?:not\s+)?defined|in\s+|not\s+in)',
                r'(?:^|\s|\(|!|not\s+)([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)(?:\s|$|\)|==|!=|>|<)',
            ]
            for pattern in when_var_patterns:
                when_vars = re.finditer(pattern, when_expr)
                for when_var_match in when_vars:
                    var_name = when_var_match.group(1) if when_var_match.lastindex >= 1 else when_var_match.group(0).strip()
                    var_name = re.sub(r'^\s*(?:not|!)\s+', '', var_name).strip()
                    if '|' in when_expr and var_name in when_expr:
                        continue
                    base_var = var_name.split('.')[0]
                    # : 
                    if (base_var and 
                        base_var not in ANSIBLE_SYSTEM_VARS and 
                        base_var not in ANSIBLE_LITERALS and
                        not any(base_var.startswith(sys_var) for sys_var in ANSIBLE_SYSTEM_VARS) and
                        not any(base_var.lower() == lit.lower() for lit in ANSIBLE_LITERALS)):
                        variables[base_var]['occurrences'].append({
                            'line': line_num,
                            'context': line.strip()[:100],
                            'type': 'when',
                            'full_match': when_expr[:80]
                        })
                        variables[base_var]['files'].add(file_path)
                        if len(variables[base_var]['examples']) < 2:
                            variables[base_var]['examples'].append({
                                'line': line_num,
                                'context': line.strip()[:80]
                            })
    
    # JSON
    result = []
    for var_name, data in sorted(variables.items()):
        result.append({
            'variable': var_name,
            'source': 'tasks',
            'occurrences': len(data['occurrences']),
            'files': list(data['files']),
            'examples': data['examples'][:2]  # 2 
        })
    
    return result


def get_role_details(pack_id, role_name, project_id=None):
    """ : , , defaults, vars ( Source Resolver)"""
    try:
        if '..' in pack_id or '..' in role_name:
            return None
        
        # Resolve roles_playbooks source
        # REQUIRED: project_id must be provided
        if not project_id:
            logger.error("[get_role_details] project_id is required")
            return None
        
        # resolve_project_source() ALWAYS returns Project Storage path
        try:
            source = resolve_project_source(project_id, 'repo')
            repo_path = source['rootPath']
            roles_storage_path = repo_path / 'roles'
        except Exception as e:
            logger.error(f"[get_role_details] Failed to resolve Project Storage path: {e}")
            return jsonify({'success': False, 'error': f'Failed to resolve Project Storage: {e}'}), 500
        
        # pack_id == role_name, 
        # : roles/role_name ( )
        if pack_id == role_name:
            role_path = roles_storage_path / role_name
            logger.info(f"[get_role_details] Role at first level: role_path={role_path}")
        else:
            # pack
            role_path = roles_storage_path / pack_id / role_name
            logger.info(f"[get_role_details] Role in pack: role_path={role_path}")
        
        if not role_path.exists() or not role_path.is_dir():
            logger.warning(f"[get_role_details] Role not found: {role_path}")
            return None
        
        # Return project-relative path only (no absolute paths, no BASE_DIR)
        project_dir = get_project_dir(project_id)
        role_path_relative = str(role_path.relative_to(project_dir)) if role_path.is_relative_to(project_dir) else f'roles-playbooks/{pack_id}/{role_name}'
        
        result = {
            'pack': pack_id,
            'role': role_name,
            'path': role_path_relative,
            'task_files': [],  # , tasks 
            'variables': [],
            'defaults': {},
            'vars': {},
            'has_defaults': False,
            'has_vars': False
        }
        
        # ⚠️ : tasks . - defaults/*.yml defaults/*.yaml
        
        # defaults/ (*.yml *.yaml)
        defaults_dir = role_path / 'defaults'
        defaults_descriptions = {}  # var_name -> description 
        defaults_files_info = []
        all_defaults = {}  # defaults 
        
        if defaults_dir.exists() and defaults_dir.is_dir():
            # *.yml *.yaml 
            defaults_files = []
            for ext in ['*.yml', '*.yaml']:
                defaults_files.extend(list(defaults_dir.glob(ext)))
            
            if defaults_files:
                result['has_defaults'] = True
                
                for defaults_file in sorted(defaults_files):
                    if not defaults_file.is_file():
                        continue
                    
                    try:
                        with open(defaults_file, 'r', encoding='utf-8') as f:
                            defaults_content = f.read()
                            file_data = yaml.safe_load(defaults_content) or {}
                            
                            if file_data and not file_data.get('_error'):
                                # ( )
                                all_defaults.update(file_data)
                                defaults_files_info.append({
                                    'name': defaults_file.name,
                                    'path': f'defaults/{defaults_file.name}',
                                    'content': defaults_content
                                })
                                
                                # inline 
                                lines = defaults_content.split('\n')
                                for line in lines:
                                    line_stripped = line.strip()
                                    if not line_stripped or line_stripped.startswith('#'):
                                        continue
                                    
                                    # : var_name: value # 
                                    # value 
                                    if '#' in line_stripped:
                                        # # 
                                        quote_count_before_hash = line_stripped[:line_stripped.index('#')].count('"') + line_stripped[:line_stripped.index('#')].count("'")
                                        if quote_count_before_hash % 2 == 0:  # = # 
                                            # : 
                                            parts = line_stripped.split('#', 1)
                                            if len(parts) == 2:
                                                key_value_part = parts[0].strip()
                                                comment_part = parts[1].strip()
                                                # key_value_part
                                                key_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:', key_value_part)
                                                if key_match:
                                                    var_name = key_match.group(1)
                                                    # ( )
                                                    if var_name not in defaults_descriptions:
                                                        defaults_descriptions[var_name] = comment_part
                    
                    except yaml.YAMLError as e:
                        logger.error(f"Error parsing YAML in {defaults_file}: {e}")
                        continue
                    except Exception as e:
                        logger.warning(f"Error reading {defaults_file}: {e}")
                        continue
                
                # defaults
                result['defaults'] = all_defaults
                result['defaultsFiles'] = defaults_files_info
                
                # ( )
                variable_order = []
                for file_info in defaults_files_info:
                    lines = file_info['content'].split('\n')
                    for line in lines:
                        line_stripped = line.strip()
                        if not line_stripped or line_stripped.startswith('#'):
                            continue
                        key_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:', line_stripped)
                        if key_match:
                            var_name = key_match.group(1)
                            if var_name in all_defaults and var_name not in variable_order:
                                variable_order.append(var_name)
                
                # , dict (Python 3.7+)
                if not variable_order:
                    variable_order = list(all_defaults.keys())
                
                variable_groups = {
                    'connection': {
                        'name': '🔌 Connection',
                        'icon': 'fa-plug',
                        'prefixes': ['initial_', 'init_'],
                        'variables': []
                    },
                    'ssh': {
                        'name': '🔐 SSH and Security',
                        'icon': 'fa-shield-alt',
                        'prefixes': ['disable_', 'change_', 'new_ssh'],
                        'variables': []
                    },
                    'users': {
                        'name': '👥 Users',
                        'icon': 'fa-users',
                        'prefixes': ['system_user', 'change_root', 'new_root', 'enable_system', 'pub_keys'],
                        'variables': []
                    },
                    'system': {
                        'name': '⚙️ System Settings',
                        'icon': 'fa-cog',
                        'prefixes': ['hostname', 'configure_', 'additional_', 'default_', 'timezone', 'ntp_'],
                        'variables': []
                    },
                    'other': {
                        'name': '📋 General Settings',
                        'icon': 'fa-list',
                        'prefixes': [],
                        'variables': []
                    }
                }
                
                for var_name in variable_order:
                    if var_name in ['_error']:
                        continue
                    
                    default_value = all_defaults[var_name]
                    
                    # (), 
                    var_files = []
                    for file_info in defaults_files_info:
                        try:
                            file_data = yaml.safe_load(file_info['content']) or {}
                            if var_name in file_data:
                                var_files.append(file_info['path'])
                        except:
                            pass
                    
                    var_type = 'string'
                    if isinstance(default_value, bool):
                        var_type = 'boolean'
                    elif isinstance(default_value, (int, float)):
                        var_type = 'number'
                    elif isinstance(default_value, str):
                        # , 
                        var_name_lower = var_name.lower()
                        if any(keyword in var_name_lower for keyword in ['password', 'secret', 'token', 'key']):
                            var_type = 'secret'
                        else:
                            var_type = 'string'
                    elif isinstance(default_value, list):
                        var_type = 'array'
                    elif isinstance(default_value, dict):
                        var_type = 'object'
                    
                    var_info = {
                        'name': var_name,
                        'variable': var_name,
                        'type': var_type,
                        'defaultValue': default_value,
                        'description': defaults_descriptions.get(var_name, '') or 'No description',
                        'scope': 'group_vars',
                        'source': 'defaults',
                        'sourceFile': var_files[0] if var_files else (defaults_files_info[0]['path'] if defaults_files_info else 'defaults/unknown'),
                        'files': var_files if var_files else ([defaults_files_info[0]['path']] if defaults_files_info else [])
                    }
                    
                    result['variables'].append(var_info)
                    
                    assigned = False
                    for group_key, group_info in variable_groups.items():
                        if group_key == 'other':
                            continue
                        for prefix in group_info['prefixes']:
                            if var_name.startswith(prefix) or prefix in var_name:
                                group_info['variables'].append(var_info)
                                assigned = True
                                break
                        if assigned:
                            break
                    
                    if not assigned:
                        variable_groups['other']['variables'].append(var_info)
                
                # ( )
                result['variableGroups'] = {
                    k: {
                        'name': v['name'],
                        'icon': v['icon'],
                        'variables': v['variables']
                    }
                    for k, v in variable_groups.items()
                    if v['variables']
                }
        
        # ⚠️ vars . defaults/main.yaml
        # defaults/main.yaml ( )
        
        return result
    except Exception as e:
        logger.error(f"Error getting role details {pack_id}/{role_name}: {e}", exc_info=True)
        return None


# Phase 3 refactor: role details / file / folder / role CRUD routes moved
# to backend/api/roles_routes.py.



# PROJECT API ENDPOINTS

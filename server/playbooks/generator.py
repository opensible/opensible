#!/usr/bin/env python3
"""Documentation."""
import yaml
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)


class PlaybookGenerator:
    """Documentation."""
    
    def __init__(self):
        pass
    
    def generate(self, playbook: Dict[str, Any]) -> str:
        """Documentation."""
        try:
            plays = playbook.get('plays', [])
            
            if not plays:
                # Comment removed.
                return ''
            
            # Comment removed.
            play_list = []
            for play in plays:
                play_dict = self._generate_play_dict(play)
                if play_dict:
                    play_list.append(play_dict)
            
            if not play_list:
                return ''
            
            # Comment removed.
            yaml_str = yaml.dump(
                play_list,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                indent=2
            )
            
            return yaml_str.strip()
            
        except Exception as e:
            logger.error(f"Error generating YAML: {e}")
            raise
    
    def _generate_play_dict(self, play: Dict[str, Any]) -> Dict[str, Any]:
        """Documentation."""
        try:
            play_dict = {}
            
            # Comment removed.
            name = play.get('name', 'Unnamed Play')
            play_dict['name'] = name
            
            # Comment removed.
            hosts = play.get('hosts', [])
            if isinstance(hosts, list):
                # Comment removed.
                if len(hosts) == 0:
                    # Comment removed.
                    play_dict['hosts'] = []
                elif len(hosts) == 1:
                    play_dict['hosts'] = hosts[0]
                elif len(hosts) > 1:
                    # Comment removed.
                    # Comment removed.
                    play_dict['hosts'] = hosts
            else:
                # Comment removed.
                play_dict['hosts'] = hosts if hosts else []
            
            # Comment removed.
            remote_user = play.get('remote_user')
            if remote_user:
                play_dict['remote_user'] = remote_user
            
            # Comment removed.
            become = play.get('become', False)
            if become:
                play_dict['become'] = True
            
            # Comment removed.
            strategy = play.get('strategy', 'linear')
            if strategy and strategy != 'linear':
                play_dict['strategy'] = strategy
            
            # Comment removed.
            pre_tasks = play.get('pre_tasks', [])
            if pre_tasks:
                play_dict['pre_tasks'] = [self._generate_task(task) for task in pre_tasks]
            
            # Comment removed.
            roles = play.get('roles', [])
            if roles:
                play_dict['roles'] = [self._generate_role(role) for role in roles]
            else:
                play_dict['roles'] = []
            
            # Comment removed.
            post_tasks = play.get('post_tasks', [])
            if post_tasks:
                play_dict['post_tasks'] = [self._generate_task(task) for task in post_tasks]
            
            # Comment removed.
            vars_files = play.get('vars_files', [])
            if vars_files and isinstance(vars_files, list):
                play_dict['vars_files'] = [p for p in vars_files if p and isinstance(p, str)]
            
            return play_dict
            
        except Exception as e:
            logger.error(f"Error generating play dict: {e}")
            raise
    
    def _generate_role(self, role: Dict[str, Any]) -> Dict[str, Any]:
        """Documentation."""
        try:
            role_dict = {}
            
            # Comment removed.
            role_name = role.get('role_name', '')
            if not role_name:
                logger.warning("Role missing 'role_name', skipping")
                return {}
            
            role_dict['role'] = role_name
            
            # Comment removed.
            tag = role.get('tag', '')
            if tag:
                # Comment removed.
                role_dict['tags'] = [tag] if isinstance(tag, str) else tag
            else:
                # Comment removed.
                role_dict['tags'] = []
            
            # Comment removed.
            vars_override = role.get('vars_override')
            if vars_override and isinstance(vars_override, dict) and vars_override:
                role_dict['vars'] = vars_override
            
            # Comment removed.
            
            return role_dict
            
        except Exception as e:
            logger.error(f"Error generating role YAML: {e}")
            return {}
    
    def _generate_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Documentation."""
        try:
            # Comment removed.
            yaml_override = task.get('yaml_override')
            if yaml_override and yaml_override.strip():
                # Comment removed.
                try:
                    parsed = yaml.safe_load(yaml_override)
                    if isinstance(parsed, dict):
                        return parsed
                    elif isinstance(parsed, list) and len(parsed) > 0:
                        return parsed[0]
                except Exception as e:
                    logger.warning(f"Failed to parse yaml_override, using regular fields: {e}")
            
            # Comment removed.
            task_dict = {}
            
            # Comment removed.
            name = task.get('name', '')
            if name:
                task_dict['name'] = name
            
            # Comment removed.
            module = task.get('module', '')
            if not module:
                logger.warning("Task missing 'module', skipping")
                return {}
            
            # Comment removed.
            args = task.get('args', {})
            if isinstance(args, dict):
                # Comment removed.
                # Comment removed.
                task_dict[module] = args if args else {}
            else:
                task_dict[module] = args
            
            # Comment removed.
            when = task.get('when')
            if when:
                task_dict['when'] = when
            
            # Comment removed.
            tags = task.get('tags', [])
            if tags:
                task_dict['tags'] = tags if isinstance(tags, list) else [tags]
            
            # Comment removed.
            become = task.get('become', False)
            if become:
                task_dict['become'] = True
            
            return task_dict
            
        except Exception as e:
            logger.error(f"Error generating task YAML: {e}")
            return {}

#!/usr/bin/env python3
"""Documentation."""
import yaml
import uuid
import re
from typing import Dict, List, Optional, Any, Tuple
import logging

logger = logging.getLogger(__name__)


class PlaybookParseError(Exception):
    """Documentation."""
    pass


class PlaybookParser:
    """Documentation."""
    
    def __init__(self):
        pass
    
    def parse(self, yaml_content: str, playbook_name: Optional[str] = None) -> Dict[str, Any]:
        """Documentation."""
        try:
            # Comment removed.
            yaml_data = yaml.safe_load(yaml_content)
            
            if yaml_data is None:
                raise PlaybookParseError("YAML file is empty")
            
            # Comment removed.
            # Comment removed.
            if not isinstance(yaml_data, list):
                yaml_data = [yaml_data]
            
            # Comment removed.
            if not playbook_name:
                if yaml_data and isinstance(yaml_data[0], dict):
                    playbook_name = yaml_data[0].get('name', 'Imported Playbook')
                else:
                    playbook_name = 'Imported Playbook'
            
            # Comment removed.
            plays = []
            for play_data in yaml_data:
                if not isinstance(play_data, dict):
                    continue
                
                play = self._parse_play(play_data)
                if play:
                    plays.append(play)
            
            # Comment removed.
            playbook = {
                'name': playbook_name,
                'description': '',
                'plays': plays,
                'metadata': {
                    'version': 1
                }
            }
            
            return playbook
            
        except yaml.YAMLError as e:
            raise PlaybookParseError(f"Invalid YAML syntax: {str(e)}")
        except Exception as e:
            raise PlaybookParseError(f"Error parsing playbook: {str(e)}")
    
    def _parse_play(self, play_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Documentation."""
        try:
            # Comment removed.
            play = {
                'id': str(uuid.uuid4()),
                'name': play_data.get('name', 'Unnamed Play'),
                'hosts': self._parse_hosts(play_data.get('hosts', 'all')),
                'remote_user': play_data.get('remote_user'),
                'become': play_data.get('become', False),
                'strategy': play_data.get('strategy', 'linear'),
                'roles': []
            }
            
            # Comment removed.
            roles_data = play_data.get('roles', [])
            if roles_data:
                for role_data in roles_data:
                    role = self._parse_role(role_data)
                    if role:
                        play['roles'].append(role)
            
            return play
            
        except Exception as e:
            logger.error(f"Error parsing play: {e}")
            return None
    
    def _parse_hosts(self, hosts_value: Any) -> Any:
        """Documentation."""
        if isinstance(hosts_value, str):
            return hosts_value
        elif isinstance(hosts_value, list):
            # Comment removed.
            if len(hosts_value) == 1:
                return hosts_value[0]  # Comment removed.
            elif len(hosts_value) > 1:
                return hosts_value  # Comment removed.
            else:
                return 'all'
        else:
            return str(hosts_value) if hosts_value else 'all'
    
    def _parse_role(self, role_data: Any) -> Optional[Dict[str, Any]]:
        """Documentation."""
        try:
            role = {
                'role_name': '',
                'tag': '',
                'auto': True,
                'vars_override': None
            }
            
            # Comment removed.
            if isinstance(role_data, str):
                role['role_name'] = role_data
                role['tag'] = role_data  # Comment removed.
                return role
            
            # Comment removed.
            if isinstance(role_data, dict):
                # Comment removed.
                role_name = role_data.get('role') or role_data.get('name')
                if not role_name:
                    logger.warning("Role data missing 'role' or 'name' field")
                    return None
                
                role['role_name'] = role_name
                
                # Comment removed.
                tags = role_data.get('tags', [])
                if isinstance(tags, list) and tags:
                    # Comment removed.
                    role['tag'] = tags[0] if len(tags) == 1 else ','.join(str(t) for t in tags)
                elif isinstance(tags, str):
                    role['tag'] = tags
                else:
                    role['tag'] = role_name  # Comment removed.
                
                # Comment removed.
                vars_data = role_data.get('vars')
                if vars_data and isinstance(vars_data, dict):
                    role['vars_override'] = vars_data
                
                # Comment removed.
                role['auto'] = True
                
                return role
            
            logger.warning(f"Unexpected role data type: {type(role_data)}")
            return None
            
        except Exception as e:
            logger.error(f"Error parsing role: {e}")
            return None
    
    def validate_yaml_structure(self, yaml_content: str) -> Tuple[bool, Optional[str]]:
        """Documentation."""
        try:
            yaml_data = yaml.safe_load(yaml_content)
            
            if yaml_data is None:
                return False, "YAML file is empty"
            
            # Comment removed.
            if not isinstance(yaml_data, (list, dict)):
                return False, "YAML must contain a list of plays or a single play"
            
            # Comment removed.
            if isinstance(yaml_data, list):
                for i, play_data in enumerate(yaml_data):
                    if not isinstance(play_data, dict):
                        return False, f"Play at index {i} must be a dictionary"
                    
                    # Comment removed.
                    if 'hosts' not in play_data:
                        return False, f"Play at index {i} missing required field 'hosts'"
            
            # Comment removed.
            elif isinstance(yaml_data, dict):
                if 'hosts' not in yaml_data:
                    return False, "Play missing required field 'hosts'"
            
            return True, None
            
        except yaml.YAMLError as e:
            return False, f"Invalid YAML syntax: {str(e)}"
        except Exception as e:
            return False, f"Error validating YAML structure: {str(e)}"

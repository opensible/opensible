#!/usr/bin/env python3
"""Documentation."""
import json
import uuid
import time
import re
from pathlib import Path
from typing import List, Dict, Optional, Any
import logging

logger = logging.getLogger(__name__)

# Comment removed.
try:
    from playbooks.generator import PlaybookGenerator
    from playbooks.parser import PlaybookParser, PlaybookParseError
except ImportError:
    from playbooks.generator import PlaybookGenerator
    from playbooks.parser import PlaybookParser, PlaybookParseError


class PlaybookStorage:
    """Documentation."""
    
    def __init__(self, projects_dir: Path):
        """Documentation."""
        self.projects_dir = projects_dir
        self.playbook_generator = PlaybookGenerator()
        self.playbook_parser = PlaybookParser()
    
    def _sanitize_filename(self, name: str) -> str:
        """Documentation."""
        # Comment removed.
        name = re.sub(r'[<>:"/\\|?*]', '_', name)
        # Comment removed.
        name = name.strip()
        # Comment removed.
        name = re.sub(r'\s+', '_', name)
        # Comment removed.
        if not name:
            name = 'playbook'
        return name
    
    def get_repo_playbooks_dir(self, project_id: str) -> Path:
        """Documentation."""
        project_dir = self.projects_dir / project_id
        repo_playbooks_dir = project_dir / 'repo' / 'playbooks'
        repo_playbooks_dir.mkdir(parents=True, exist_ok=True)
        return repo_playbooks_dir
    
    def get_repo_playbook_file(self, project_id: str, playbook_name: str) -> Path:
        """Documentation."""
        repo_playbooks_dir = self.get_repo_playbooks_dir(project_id)
        sanitized_name = self._sanitize_filename(playbook_name)
        return repo_playbooks_dir / f'{sanitized_name}.yml'
    
    def get_playbooks_dir(self, project_id: str) -> Path:
        """Documentation."""
        project_dir = self.projects_dir / project_id
        # Comment removed.
        playbooks_dir = project_dir / 'ui' / 'playbooks'
        playbooks_dir.mkdir(parents=True, exist_ok=True)
        return playbooks_dir
    
    def get_playbook_file(self, project_id: str, playbook_id: str) -> Path:
        """Documentation."""
        playbooks_dir = self.get_playbooks_dir(project_id)
        return playbooks_dir / f'{playbook_id}.json'
    
    def list_playbooks(self, project_id: str) -> List[Dict[str, Any]]:
        """Documentation."""
        try:
            playbooks = []
            playbook_ids_seen = set()
            
            # Comment removed.
            playbooks_dir = self.get_playbooks_dir(project_id)
            if playbooks_dir.exists():
                for playbook_file in playbooks_dir.glob('*.json'):
                    try:
                        with open(playbook_file, 'r', encoding='utf-8') as f:
                            playbook = json.load(f)
                        
                        # Comment removed.
                        playbook_id = playbook_file.stem
                        plays_count = len(playbook.get('plays', []))
                        
                        metadata = playbook.get('metadata', {})
                        
                        # Comment removed.
                        tags = []
                        if playbook.get('tags') and isinstance(playbook.get('tags'), list):
                            tags = playbook.get('tags')
                        elif metadata.get('tags') and isinstance(metadata.get('tags'), list):
                            tags = metadata.get('tags')
                        elif playbook.get('tag') or metadata.get('tag'):
                            # Comment removed.
                            tag_str = playbook.get('tag') or metadata.get('tag', '')
                            if tag_str:
                                tags = [t.strip() for t in str(tag_str).split(',') if t.strip()]
                        
                        # Comment removed.
                        disabled = playbook.get('disabled') or metadata.get('disabled', False)
                        
                        playbooks.append({
                            'id': playbook_id,
                            'project_id': project_id,
                            'name': playbook.get('name', 'Unnamed Playbook'),
                            'description': playbook.get('description', ''),
                            'plays_count': plays_count,
                            'created_at': metadata.get('created_at'),
                            'updated_at': metadata.get('updated_at'),
                            'version': metadata.get('version', 1),
                            'tags': tags,
                            'tag': ', '.join(tags) if tags else '',  # Comment removed.
                            'disabled': disabled,
                            'metadata': metadata  # Comment removed.
                        })
                        playbook_ids_seen.add(playbook_id)
                    except Exception as e:
                        logger.error(f"Error reading playbook {playbook_file}: {e}")
                        continue
            
            # Comment removed.
            repo_playbooks_dir = self.get_repo_playbooks_dir(project_id)
            if repo_playbooks_dir.exists():
                for yaml_file in repo_playbooks_dir.glob('*.yml'):
                    try:
                        # Comment removed.
                        yaml_name = yaml_file.stem
                        
                        # Comment removed.
                        with open(yaml_file, 'r', encoding='utf-8') as f:
                            yaml_content = f.read()
                        
                        if not yaml_content.strip():
                            continue
                        
                        # Comment removed.
                        try:
                            playbook = self.playbook_parser.parse(yaml_content, yaml_name)
                        except Exception as e:
                            # Template-generated playbooks (notably kubeadm/k3s)
                            # intentionally contain rich Ansible YAML that the
                            # lightweight UI parser may not fully understand.
                            # Do not hide them from the app: expose a metadata
                            # wrapper so get_playbook() can return _yaml_raw and
                            # the runner executes the exact YAML on disk.
                            logger.warning(f"YAML playbook {yaml_file} unparseable ({e}); listing as raw YAML")
                            playbook = {
                                'name': yaml_name,
                                'description': '',
                                'plays': [],
                                'metadata': {'version': 1, 'raw_yaml': True},
                            }
                        
                        # Comment removed.
                        playbook_name = playbook.get('name', yaml_name)
                        existing_playbook = next(
                            (p for p in playbooks if p.get('name') == playbook_name),
                            None
                        )
                        
                        if existing_playbook:
                            # Comment removed.
                            continue
                        
                        # Comment removed.
                        playbook_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"playbook:{project_id}:{playbook_name}"))
                        
                        # Comment removed.
                        plays_count = len(playbook.get('plays', []))
                        metadata = playbook.get('metadata', {})
                        
                        # Comment removed.
                        tags = []
                        if playbook.get('tags') and isinstance(playbook.get('tags'), list):
                            tags = playbook.get('tags')
                        elif metadata.get('tags') and isinstance(metadata.get('tags'), list):
                            tags = metadata.get('tags')
                        
                        # Comment removed.
                        disabled = playbook.get('disabled') or metadata.get('disabled', False)
                        
                        # Comment removed.
                        file_mtime = yaml_file.stat().st_mtime
                        updated_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(file_mtime))
                        created_at = updated_at  # Comment removed.
                        
                        playbooks.append({
                            'id': playbook_id,
                            'project_id': project_id,
                            'name': playbook_name,
                            'description': playbook.get('description', ''),
                            'plays_count': plays_count,
                            'created_at': metadata.get('created_at', created_at),
                            'updated_at': metadata.get('updated_at', updated_at),
                            'version': metadata.get('version', 1),
                            'tags': tags,
                            'tag': ', '.join(tags) if tags else '',
                            'disabled': disabled,
                            'metadata': metadata,
                            '_yaml_file': str(yaml_file)  # Comment removed.
                        })
                    except Exception as e:
                        logger.error(f"Error reading YAML playbook {yaml_file}: {e}")
                        continue
            
            # Comment removed.
            # Comment removed.
            playbooks_with_order = []
            playbooks_without_order = []
            
            for playbook in playbooks:
                order = playbook.get('metadata', {}).get('order')
                if order is not None:
                    playbooks_with_order.append(playbook)
                else:
                    playbooks_without_order.append(playbook)
            
            # Comment removed.
            playbooks_with_order.sort(key=lambda x: x.get('metadata', {}).get('order', 0))
            
            # Comment removed.
            playbooks_without_order.sort(key=lambda x: x.get('updated_at') or '', reverse=True)
            
            # Comment removed.
            playbooks = playbooks_with_order + playbooks_without_order
            return playbooks
            
        except Exception as e:
            logger.error(f"Error listing playbooks for project {project_id}: {e}")
            return []
    
    def get_playbook(self, project_id: str, playbook_id: str) -> Optional[Dict[str, Any]]:
        """Documentation."""
        try:
            # Comment removed.
            playbook_file = self.get_playbook_file(project_id, playbook_id)
            
            if playbook_file.exists():
                with open(playbook_file, 'r', encoding='utf-8') as f:
                    playbook = json.load(f)
                
                # Comment removed.
                playbook['id'] = playbook_id
                playbook['project_id'] = project_id
                
                return playbook
            
            # Comment removed.
            # Comment removed.
            all_playbooks = self.list_playbooks(project_id)
            playbook_meta = next((p for p in all_playbooks if p.get('id') == playbook_id), None)
            
            if playbook_meta and playbook_meta.get('_yaml_file'):
                yaml_file = Path(playbook_meta['_yaml_file'])
                if yaml_file.exists():
                    with open(yaml_file, 'r', encoding='utf-8') as f:
                        yaml_content = f.read()

                    # Try structured parse; some external/template YAMLs use
                    # constructs the parser can't map to the internal model, in
                    # which case fall back to a raw wrapper so the run endpoint
                    # can execute the YAML as-is via _yaml_raw.
                    try:
                        playbook = self.playbook_parser.parse(yaml_content, playbook_meta.get('name'))
                    except Exception as e:
                        logger.warning(f"YAML playbook {yaml_file} unparseable ({e}); using raw content")
                        playbook = {'name': playbook_meta.get('name'), 'plays': []}
                    playbook['id'] = playbook_id
                    playbook['project_id'] = project_id
                    playbook['_yaml_file'] = str(yaml_file)
                    playbook['_yaml_raw'] = yaml_content
                    return playbook

            
            return None
            
        except Exception as e:
            logger.error(f"Error getting playbook {playbook_id} for project {project_id}: {e}")
            return None
    
    def create_playbook(self, project_id: str, name: str, description: str = '') -> Optional[Dict[str, Any]]:
        """Documentation."""
        try:
            playbook_id = str(uuid.uuid4())
            current_time = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            
            playbook = {
                'id': playbook_id,
                'project_id': project_id,
                'name': name,
                'description': description,
                'plays': [],
                'metadata': {
                    'created_at': current_time,
                    'updated_at': current_time,
                    'version': 1
                }
            }
            
            playbook_file = self.get_playbook_file(project_id, playbook_id)
            with open(playbook_file, 'w', encoding='utf-8') as f:
                json.dump(playbook, f, indent=2, ensure_ascii=False)
            
            # Comment removed.
            self._save_playbook_yaml(project_id, playbook)
            
            logger.info(f"Created playbook {playbook_id} for project {project_id}")
            return playbook
            
        except Exception as e:
            logger.error(f"Error creating playbook for project {project_id}: {e}")
            return None
    
    def save_playbook(self, project_id: str, playbook: Dict[str, Any]) -> bool:
        """Documentation."""
        try:
            playbook_id = playbook.get('id')
            if not playbook_id:
                logger.error("Playbook must have 'id' field")
                return False
            
            # Comment removed.
            current_time = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            if 'metadata' not in playbook:
                playbook['metadata'] = {}
            
            if 'created_at' not in playbook['metadata']:
                playbook['metadata']['created_at'] = current_time
            
            playbook['metadata']['updated_at'] = current_time
            playbook['metadata']['version'] = playbook['metadata'].get('version', 1) + 1
            
            # Comment removed.
            playbook['project_id'] = project_id
            
            playbook_file = self.get_playbook_file(project_id, playbook_id)
            with open(playbook_file, 'w', encoding='utf-8') as f:
                json.dump(playbook, f, indent=2, ensure_ascii=False)
            
            # Comment removed.
            self._save_playbook_yaml(project_id, playbook)
            
            logger.info(f"Saved playbook {playbook_id} for project {project_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving playbook {playbook.get('id')} for project {project_id}: {e}")
            return False
    
    def _save_playbook_yaml(self, project_id: str, playbook: Dict[str, Any]) -> bool:
        """Documentation."""
        try:
            playbook_name = playbook.get('name', 'Unnamed Playbook')
            yaml_file = self.get_repo_playbook_file(project_id, playbook_name)
            
            # Comment removed.
            yaml_content = self.playbook_generator.generate(playbook)
            
            # Comment removed.
            yaml_file.parent.mkdir(parents=True, exist_ok=True)
            with open(yaml_file, 'w', encoding='utf-8') as f:
                f.write(yaml_content)
            
            logger.info(f"Saved playbook YAML {playbook_name} to {yaml_file}")
            return True
        except Exception as e:
            logger.error(f"Error saving playbook YAML for project {project_id}: {e}")
            return False
            
        except Exception as e:
            logger.error(f"Error saving playbook {playbook.get('id')} for project {project_id}: {e}")
            return False
    
    def delete_playbook(self, project_id: str, playbook_id: str) -> bool:
        """Documentation."""
        try:
            playbook_file = self.get_playbook_file(project_id, playbook_id)
            deleted_any = False

            # Resolve name first (needed to also remove the YAML sidecar).
            playbook_name = None
            if playbook_file.exists():
                try:
                    with open(playbook_file, 'r', encoding='utf-8') as f:
                        pb = json.load(f)
                    playbook_name = pb.get('name')
                except Exception:
                    playbook_name = None
                try:
                    playbook_file.unlink()
                    deleted_any = True
                except Exception as e:
                    logger.error(f"Error unlinking playbook JSON {playbook_file}: {e}")

            # YAML-only playbook (imported / template-generated / git-synced):
            # resolve via list_playbooks which computes a uuid5-based id.
            if not playbook_name:
                for meta in self.list_playbooks(project_id):
                    if meta.get('id') == playbook_id:
                        playbook_name = meta.get('name')
                        yaml_path = meta.get('_yaml_file')
                        if yaml_path:
                            try:
                                Path(yaml_path).unlink(missing_ok=True)
                                deleted_any = True
                            except Exception as e:
                                logger.error(f"Error unlinking YAML playbook {yaml_path}: {e}")
                        break

            # Remove the mirrored YAML in repo/playbooks/<name>.yml if any.
            if playbook_name:
                yaml_file = self.get_repo_playbook_file(project_id, playbook_name)
                if yaml_file.exists():
                    try:
                        yaml_file.unlink()
                        deleted_any = True
                        logger.info(f"Deleted playbook YAML {playbook_name} from repo/playbooks/")
                    except Exception as e:
                        logger.error(f"Error unlinking mirror YAML {yaml_file}: {e}")

            if deleted_any:
                logger.info(f"Deleted playbook {playbook_id} for project {project_id}")
            else:
                logger.warning(f"Playbook {playbook_id} not found for project {project_id}")
            return deleted_any

        except Exception as e:
            logger.error(f"Error deleting playbook {playbook_id} for project {project_id}: {e}")
            return False

    def clone_playbook(self, project_id: str, source_playbook_id: str, new_name: str, new_description: str = '') -> Optional[Dict[str, Any]]:
        """Documentation."""
        try:
            source_playbook = self.get_playbook(project_id, source_playbook_id)
            if not source_playbook:
                return None
            
            # Comment removed.
            import copy
            new_playbook = copy.deepcopy(source_playbook)
            
            # Comment removed.
            new_playbook_id = str(uuid.uuid4())
            new_playbook['id'] = new_playbook_id
            
            # Comment removed.
            new_playbook['name'] = new_name
            new_playbook['description'] = new_description
            
            # Comment removed.
            for play in new_playbook.get('plays', []):
                play['id'] = str(uuid.uuid4())
            
            # Comment removed.
            current_time = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            new_playbook['metadata'] = {
                'created_at': current_time,
                'updated_at': current_time,
                'version': 1,
                'cloned_from': source_playbook_id
            }
            
            # Comment removed.
            if self.save_playbook(project_id, new_playbook):
                return new_playbook
            else:
                return None
                
        except Exception as e:
            logger.error(f"Error cloning playbook {source_playbook_id} for project {project_id}: {e}")
            return None
    
    def playbook_exists(self, project_id: str, playbook_id: str) -> bool:
        """Documentation."""
        playbook_file = self.get_playbook_file(project_id, playbook_id)
        if playbook_file.exists():
            return True
        # YAML-only playbooks (uuid5-based id) — check via listing.
        try:
            for meta in self.list_playbooks(project_id):
                if meta.get('id') == playbook_id:
                    return True
        except Exception:
            pass
        return False

    
    def check_name_conflict(self, project_id: str, name: str, exclude_playbook_id: Optional[str] = None) -> bool:
        """Documentation."""
        playbooks = self.list_playbooks(project_id)
        for playbook in playbooks:
            if playbook['name'] == name:
                if exclude_playbook_id and playbook['id'] == exclude_playbook_id:
                    continue
                return True
        return False
    
    def get_schedule(self, project_id: str, playbook_id: str) -> Optional[Dict[str, Any]]:
        """Documentation."""
        try:
            playbook = self.get_playbook(project_id, playbook_id)
            if not playbook:
                return None
            
            # Comment removed.
            metadata = playbook.get('metadata', {})
            schedule = metadata.get('schedule')
            
            if not schedule or not schedule.get('enabled', False):
                return None
            
            return {
                'enabled': schedule.get('enabled', False),
                'cron': schedule.get('cron', ''),
                'timezone': schedule.get('timezone', 'UTC')
            }
        except Exception as e:
            logger.error(f"Error getting schedule for playbook {playbook_id} in project {project_id}: {e}")
            return None
    
    def save_schedule(self, project_id: str, playbook_id: str, schedule: Dict[str, Any]) -> bool:
        """Documentation."""
        try:
            playbook = self.get_playbook(project_id, playbook_id)
            if not playbook:
                logger.error(f"Playbook {playbook_id} not found in project {project_id}")
                return False
            
            # Comment removed.
            if 'metadata' not in playbook:
                playbook['metadata'] = {}
            
            # Comment removed.
            playbook['metadata']['schedule'] = {
                'enabled': schedule.get('enabled', False),
                'cron': schedule.get('cron', ''),
                'timezone': schedule.get('timezone', 'UTC'),
                'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            }
            
            # Comment removed.
            return self.save_playbook(project_id, playbook)
        except Exception as e:
            logger.error(f"Error saving schedule for playbook {playbook_id} in project {project_id}: {e}")
            return False
    
    def delete_schedule(self, project_id: str, playbook_id: str) -> bool:
        """Documentation."""
        try:
            playbook = self.get_playbook(project_id, playbook_id)
            if not playbook:
                logger.error(f"Playbook {playbook_id} not found in project {project_id}")
                return False
            
            # Comment removed.
            if 'metadata' not in playbook:
                playbook['metadata'] = {}
            
            # Comment removed.
            if 'schedule' in playbook['metadata']:
                playbook['metadata']['schedule'] = {
                    'enabled': False,
                    'cron': playbook['metadata']['schedule'].get('cron', ''),
                    'timezone': playbook['metadata']['schedule'].get('timezone', 'UTC'),
                    'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                }
            
            # Comment removed.
            return self.save_playbook(project_id, playbook)
        except Exception as e:
            logger.error(f"Error deleting schedule for playbook {playbook_id} in project {project_id}: {e}")
            return False
    
    def list_all_schedules(self) -> List[Dict[str, Any]]:
        """Documentation."""
        schedules = []
        try:
            if not self.projects_dir.exists():
                return schedules
            
            # Comment removed.
            for project_dir in self.projects_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                
                project_id = project_dir.name
                
                try:
                    # Comment removed.
                    playbooks = self.list_playbooks(project_id)
                    
                    for playbook in playbooks:
                        playbook_id = playbook['id']
                        
                        # Skip disabled playbooks - scheduler should not run them
                        if playbook.get('disabled') or playbook.get('metadata', {}).get('disabled'):
                            continue
                        
                        schedule = self.get_schedule(project_id, playbook_id)
                        
                        if schedule and schedule.get('enabled', False):
                            schedules.append({
                                'project_id': project_id,
                                'playbook_id': playbook_id,
                                'schedule': schedule
                            })
                except Exception as e:
                    logger.error(f"Error listing schedules for project {project_id}: {e}")
                    continue
        except Exception as e:
            logger.error(f"Error listing all schedules: {e}")
        
        return schedules

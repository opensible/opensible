"""Backup and backup-settings API routes.

Moved from ``backend/app.py`` during the phased refactor. URLs, methods,
view-function names, response shapes, and auth decorators are preserved.

Path helpers (``get_project_dir``, ``get_project_group_vars_dir``, ...),
the ``BACKUPS_DIR`` / ``BACKUP_ARCHIVES_DIR`` roots, the
``playbook_storage`` singleton, and the shared ``create_backup`` helper
still live in ``app.py``. They are resolved lazily via ``_app_module()``
so this blueprint never triggers a circular import.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tarfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from utils.backup_settings import (
    load_backup_settings,
    save_backup_settings,
    get_project_backup_enabled,
    cleanup_old_archives,
)
from utils.request_ctx import get_project_id_from_request as _get_pid_raw


def get_project_id_from_request():
    return _get_pid_raw(lambda: None)


bp = Blueprint("backups_api", __name__)


def _app_module():
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "get_project_dir"):
            return mod
    import app as _a  # noqa
    return _a


def _logger():
    return _app_module().app.logger


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@bp.route('/api/backups/list', methods=['GET'])
@require_auth
def list_backups():
    """List backups for a project."""
    try:
        a = _app_module()
        BACKUPS_DIR = a.BACKUPS_DIR

        project_id = request.args.get('project_id')
        if not project_id:
            project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        backups = []
        backup_type = request.args.get('type', 'all')

        if backup_type in ['all', 'group_vars']:
            group_vars_backup_dir = BACKUPS_DIR / 'group_vars' / project_id
            if group_vars_backup_dir.exists():
                for backup_file in sorted(group_vars_backup_dir.glob('*.yml'), reverse=True):
                    backups.append({
                        'name': backup_file.name,
                        'path': f'group_vars/{project_id}/{backup_file.name}',
                        'type': 'group_vars',
                        'size': backup_file.stat().st_size,
                        'modified': backup_file.stat().st_mtime
                    })

            project_group_vars_dir = a.get_project_group_vars_dir(project_id)
            legacy_backup_dir = project_group_vars_dir / 'backups'
            if legacy_backup_dir.exists():
                for backup_file in sorted(legacy_backup_dir.glob('*.yml'), reverse=True):
                    if not any(b['name'] == backup_file.name for b in backups):
                        backups.append({
                            'name': backup_file.name,
                            'path': f'group_vars/backups/{backup_file.name}',
                            'type': 'group_vars',
                            'size': backup_file.stat().st_size,
                            'modified': backup_file.stat().st_mtime
                        })

        if backup_type in ['all', 'host_vars']:
            host_vars_backup_dir = BACKUPS_DIR / 'host_vars' / project_id
            if host_vars_backup_dir.exists():
                for backup_file in sorted(host_vars_backup_dir.glob('*.yml'), reverse=True):
                    backups.append({
                        'name': backup_file.name,
                        'path': f'host_vars/{project_id}/{backup_file.name}',
                        'type': 'host_vars',
                        'size': backup_file.stat().st_size,
                        'modified': backup_file.stat().st_mtime
                    })

            project_host_vars_dir = a.get_project_host_vars_dir(project_id)
            legacy_backup_dir = project_host_vars_dir / 'backups'
            if legacy_backup_dir.exists():
                for backup_file in sorted(legacy_backup_dir.glob('*.yml'), reverse=True):
                    if not any(b['name'] == backup_file.name for b in backups):
                        backups.append({
                            'name': backup_file.name,
                            'path': f'host_vars/backups/{backup_file.name}',
                            'type': 'host_vars',
                            'size': backup_file.stat().st_size,
                            'modified': backup_file.stat().st_mtime
                        })

        if backup_type in ['all', 'inventory']:
            inventory_backup_dir = BACKUPS_DIR / 'inventory' / project_id
            if inventory_backup_dir.exists():
                for backup_file in sorted(inventory_backup_dir.glob('*.yml'), reverse=True):
                    backups.append({
                        'name': backup_file.name,
                        'path': f'inventory/{project_id}/{backup_file.name}',
                        'type': 'inventory',
                        'size': backup_file.stat().st_size,
                        'modified': backup_file.stat().st_mtime
                    })

        if backup_type in ['all', 'playbooks']:
            playbooks_backup_dir = BACKUPS_DIR / 'playbooks' / project_id
            if playbooks_backup_dir.exists():
                for backup_file in sorted(playbooks_backup_dir.glob('*.json'), reverse=True):
                    backups.append({
                        'name': backup_file.name,
                        'path': f'playbooks/{project_id}/{backup_file.name}',
                        'type': 'playbooks',
                        'size': backup_file.stat().st_size,
                        'modified': backup_file.stat().st_mtime
                    })

        backups.sort(key=lambda x: x['modified'], reverse=True)
        return jsonify({'success': True, 'backups': backups})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/backups/download', methods=['GET'])
@require_auth
def download_backup():
    """Download a backup file."""
    try:
        a = _app_module()
        BACKUPS_DIR = a.BACKUPS_DIR

        project_id = request.args.get('project_id')
        if not project_id:
            project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        backup_path = request.args.get('path')
        if not backup_path:
            return jsonify({'success': False, 'error': 'File path not specified'}), 400

        rel = Path(str(backup_path))
        if rel.is_absolute() or '..' in rel.parts:
            return jsonify({'success': False, 'error': 'Invalid path'}), 400

        file_path = None

        if backup_path.startswith('group_vars/') and f'/{project_id}/' in backup_path:
            backup_name = backup_path.split('/')[-1]
            file_path = BACKUPS_DIR / 'group_vars' / project_id / backup_name
        elif backup_path.startswith('host_vars/') and f'/{project_id}/' in backup_path:
            backup_name = backup_path.split('/')[-1]
            file_path = BACKUPS_DIR / 'host_vars' / project_id / backup_name
        elif backup_path.startswith('inventory/') and f'/{project_id}/' in backup_path:
            backup_name = backup_path.split('/')[-1]
            file_path = BACKUPS_DIR / 'inventory' / project_id / backup_name
        elif backup_path.startswith('playbooks/') and f'/{project_id}/' in backup_path:
            backup_name = backup_path.split('/')[-1]
            file_path = BACKUPS_DIR / 'playbooks' / project_id / backup_name
        else:
            project_dir = a.get_project_dir(project_id)
            file_path = (project_dir / rel).resolve()

            allowed_dirs = [
                (a.get_project_group_vars_dir(project_id) / 'backups').resolve(),
                (a.get_project_host_vars_dir(project_id) / 'backups').resolve(),
            ]
            if not any(str(file_path).startswith(str(d) + os.sep) or file_path == d for d in allowed_dirs):
                return jsonify({'success': False, 'error': 'Access denied'}), 403

        if file_path:
            file_path = file_path.resolve()
            if not (str(file_path).startswith(str(BACKUPS_DIR.resolve()) + os.sep) or
                    str(file_path).startswith(str(a.get_project_dir(project_id).resolve()) + os.sep)):
                return jsonify({'success': False, 'error': 'Access denied'}), 403

        if not file_path or not file_path.exists():
            return jsonify({'success': False, 'error': 'File not found'}), 404

        mimetype = 'text/yaml'
        if file_path.suffix == '.json':
            mimetype = 'application/json'

        return send_file(
            str(file_path),
            mimetype=mimetype,
            as_attachment=True,
            download_name=file_path.name
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/backups/restore', methods=['POST'])
@require_auth
def restore_backup():
    """Restore a specific backup file back into the project."""
    try:
        a = _app_module()
        BACKUPS_DIR = a.BACKUPS_DIR

        project_id = request.args.get('project_id')
        if not project_id:
            data = request.json or {}
            project_id = data.get('project_id')
        if not project_id:
            project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        data = request.json or {}
        backup_path = data.get('path')
        if not backup_path:
            return jsonify({'success': False, 'error': 'Backup path not specified'}), 400

        rel = Path(str(backup_path))
        if rel.is_absolute() or '..' in rel.parts:
            return jsonify({'success': False, 'error': 'Invalid path'}), 400

        backup_file_path = None
        target_file = None

        if backup_path.startswith('group_vars/') and f'/{project_id}/' in backup_path:
            backup_name = backup_path.split('/')[-1]
            backup_file_path = BACKUPS_DIR / 'group_vars' / project_id / backup_name
            target_file = a.get_project_group_vars_dir(project_id) / 'all.yml'
        elif backup_path.startswith('host_vars/') and f'/{project_id}/' in backup_path:
            backup_name = backup_path.split('/')[-1]
            backup_file_path = BACKUPS_DIR / 'host_vars' / project_id / backup_name
            backup_name_stem = Path(backup_name).stem
            host_match = re.match(r'^(.+?)_\d{8}_\d{6}$', backup_name_stem)
            hostname = host_match.group(1) if host_match else backup_name_stem
            target_file = a.get_project_host_vars_dir(project_id) / f'{hostname}.yml'
        elif backup_path.startswith('inventory/') and f'/{project_id}/' in backup_path:
            backup_name = backup_path.split('/')[-1]
            backup_file_path = BACKUPS_DIR / 'inventory' / project_id / backup_name
            backup_name_stem = Path(backup_name).stem
            inventory_match = re.match(r'^(.+?)_\d{8}_\d{6}$', backup_name_stem)
            inventory_name = (inventory_match.group(1) + '.yml') if inventory_match else 'inventory.yml'
            target_file = a.get_project_inventory_file(project_id).parent / inventory_name
        elif backup_path.startswith('playbooks/') and f'/{project_id}/' in backup_path:
            backup_name = backup_path.split('/')[-1]
            backup_file_path = BACKUPS_DIR / 'playbooks' / project_id / backup_name
            backup_name_stem = Path(backup_name).stem
            playbook_match = re.match(r'^(.+?)_\d{8}_\d{6}$', backup_name_stem)
            playbook_id = playbook_match.group(1) if playbook_match else backup_name_stem
            target_file = a.playbook_storage.get_playbook_file(project_id, playbook_id)
        else:
            project_dir = a.get_project_dir(project_id)
            backup_file_path = (project_dir / rel).resolve()

            allowed_dirs = [
                (a.get_project_group_vars_dir(project_id) / 'backups').resolve(),
                (a.get_project_host_vars_dir(project_id) / 'backups').resolve(),
            ]
            if not any(str(backup_file_path).startswith(str(d) + os.sep) or backup_file_path == d for d in allowed_dirs):
                return jsonify({'success': False, 'error': 'Access denied'}), 403

            if 'group_vars/backups' in str(backup_file_path):
                target_file = a.get_project_group_vars_dir(project_id) / 'all.yml'
            elif 'host_vars/backups' in str(backup_file_path):
                backup_name = backup_file_path.stem
                host_match = re.match(r'^(.+?)_\d{8}_\d{6}$', backup_name)
                hostname = host_match.group(1) if host_match else backup_name
                target_file = a.get_project_host_vars_dir(project_id) / f'{hostname}.yml'
            else:
                return jsonify({'success': False, 'error': 'Unknown backup type'}), 400

        if backup_file_path:
            backup_file_path = backup_file_path.resolve()
            if not (str(backup_file_path).startswith(str(BACKUPS_DIR.resolve()) + os.sep) or
                    str(backup_file_path).startswith(str(a.get_project_dir(project_id).resolve()) + os.sep)):
                return jsonify({'success': False, 'error': 'Access denied'}), 403

        if not backup_file_path or not backup_file_path.exists():
            return jsonify({'success': False, 'error': 'Backup not found'}), 404

        if not target_file:
            return jsonify({'success': False, 'error': 'Failed to determine target file for restoration'}), 400

        if target_file.exists():
            a.create_backup(target_file, force=True)

        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_file_path, target_file)

        _logger().info(f"Restored backup {backup_path} to {target_file} for project {project_id}")
        return jsonify({'success': True, 'message': 'Backup restored successfully'})
    except Exception as e:
        _logger().error(f"Error restoring backup: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/backup-settings', methods=['GET'])
@require_auth
def get_backup_settings():
    try:
        settings = load_backup_settings()
        return jsonify({'success': True, 'settings': settings})
    except Exception as e:
        _logger().error(f"Error loading backup settings: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/backup-settings', methods=['PUT'])
@require_auth
def update_backup_settings():
    try:
        data = request.json or {}
        settings = load_backup_settings()

        if 'max_depth' in data:
            max_depth = int(data['max_depth'])
            if max_depth < 1:
                return jsonify({'success': False, 'error': 'Backup depth must be greater than 0'}), 400
            settings['max_depth'] = max_depth

        if 'projects' in data:
            if not isinstance(data['projects'], dict):
                return jsonify({'success': False, 'error': 'Invalid projects settings format'}), 400

            if 'projects' not in settings:
                settings['projects'] = {}

            for project_id, project_settings in data['projects'].items():
                if not isinstance(project_settings, dict):
                    continue
                settings['projects'][project_id] = {
                    'enabled': bool(project_settings.get('enabled', False))
                }

        if save_backup_settings(settings):
            return jsonify({'success': True, 'settings': settings})
        else:
            return jsonify({'success': False, 'error': 'Error saving settings'}), 500
    except Exception as e:
        _logger().error(f"Error updating backup settings: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/backups/create', methods=['POST'])
@require_auth
def create_project_backup():
    """Create a full tar.gz archive of a project's directory."""
    try:
        a = _app_module()
        BACKUP_ARCHIVES_DIR = a.BACKUP_ARCHIVES_DIR

        project_id = get_project_id_from_request()
        if not project_id:
            project_id = request.json.get('project_id') if request.json else None
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        reason = request.json.get('reason', 'auto') if request.json else 'auto'
        if reason != 'manual' and not get_project_backup_enabled(project_id):
            return jsonify({'success': False, 'error': 'Automatic backup is not enabled for this project'}), 400

        project_dir = a.get_project_dir(project_id)
        if not project_dir.exists():
            return jsonify({'success': False, 'error': 'Project directory not found'}), 404

        archives_project_dir = BACKUP_ARCHIVES_DIR / project_id
        archives_project_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        archive_name = f'{timestamp}.tar.gz'
        archive_path = archives_project_dir / archive_name

        file_count = 0
        with tarfile.open(archive_path, 'w:gz') as tf:
            for item in project_dir.rglob('*'):
                if item.is_file():
                    arcname = item.relative_to(project_dir)
                    tf.add(item, arcname=str(arcname))
                    file_count += 1

        settings = load_backup_settings()
        max_depth = settings.get('max_depth', 100)
        if max_depth < 1:
            max_depth = 1
        cleanup_old_archives(archives_project_dir, max_depth)

        _logger().info(f"Created full archive for project {project_id}: {archive_name} ({file_count} files)")
        return jsonify({
            'success': True,
            'message': 'Backup created successfully',
            'archive': archive_name,
            'path': f'archives/{project_id}/{archive_name}',
            'files_count': file_count
        })
    except Exception as e:
        _logger().error(f"Error creating project backup: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/backups/archives/list', methods=['GET'])
@require_auth
def list_project_archives():
    """List tar.gz archives available for restore for a project."""
    try:
        a = _app_module()
        BACKUP_ARCHIVES_DIR = a.BACKUP_ARCHIVES_DIR

        project_id = request.args.get('project_id')
        if not project_id:
            return jsonify({'success': False, 'error': 'project_id is required'}), 400
        project_dir = a.get_project_dir(project_id)
        if not project_dir.exists():
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        archives_dir = BACKUP_ARCHIVES_DIR / project_id
        archives = []
        if archives_dir.exists():
            for f in sorted(archives_dir.glob('*.tar.gz'), key=lambda x: x.stat().st_mtime, reverse=True):
                st = f.stat()
                archives.append({
                    'name': f.name,
                    'path': f'archives/{project_id}/{f.name}',
                    'size': st.st_size,
                    'modified': st.st_mtime,
                })
        return jsonify({'success': True, 'archives': archives})
    except Exception as e:
        _logger().error(f"Error listing project archives: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/backups/archives/restore', methods=['POST'])
@require_auth
def restore_project_archive():
    """Restore a project from a tar.gz archive."""
    try:
        a = _app_module()
        BACKUP_ARCHIVES_DIR = a.BACKUP_ARCHIVES_DIR

        data = request.json or {}
        project_id = data.get('project_id') or request.args.get('project_id')
        if not project_id:
            return jsonify({'success': False, 'error': 'project_id is required'}), 400
        path = data.get('path') or data.get('archive')
        if not path:
            return jsonify({'success': False, 'error': 'path (archive filename) is required'}), 400
        archive_name = path.split('/')[-1] if '/' in path else path
        if not archive_name.endswith('.tar.gz'):
            return jsonify({'success': False, 'error': 'Invalid archive path'}), 400
        archive_path = (BACKUP_ARCHIVES_DIR / project_id / archive_name).resolve()
        if not archive_path.is_file():
            return jsonify({'success': False, 'error': 'Archive not found'}), 404
        if not str(archive_path).startswith(str(BACKUP_ARCHIVES_DIR.resolve())):
            return jsonify({'success': False, 'error': 'Invalid archive path'}), 400
        project_dir = a.get_project_dir(project_id).resolve()
        project_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, 'r:gz') as tf:
            tf.extractall(path=project_dir)
        _logger().info(f"Restored project {project_id} from archive {archive_name}")
        return jsonify({'success': True, 'message': 'Project restored successfully'})
    except Exception as e:
        _logger().error(f"Error restoring project archive: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/backups/archives/download', methods=['GET'])
@require_auth
def download_project_archive():
    """Download a project archive tar.gz."""
    try:
        a = _app_module()
        BACKUP_ARCHIVES_DIR = a.BACKUP_ARCHIVES_DIR

        project_id = request.args.get('project_id')
        path = request.args.get('path')
        if not project_id or not path:
            return jsonify({'success': False, 'error': 'project_id and path are required'}), 400
        archive_name = path.split('/')[-1] if '/' in path else path
        if not archive_name.endswith('.tar.gz'):
            return jsonify({'success': False, 'error': 'Invalid archive path'}), 400
        archive_path = (BACKUP_ARCHIVES_DIR / project_id / archive_name).resolve()
        if not archive_path.is_file():
            return jsonify({'success': False, 'error': 'Archive not found'}), 404
        if not str(archive_path).startswith(str(BACKUP_ARCHIVES_DIR.resolve())):
            return jsonify({'success': False, 'error': 'Invalid path'}), 400
        return send_file(archive_path, as_attachment=True, download_name=archive_name)
    except Exception as e:
        _logger().error(f"Error downloading archive: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

"""Admin worker-management API routes.

Moved from ``backend/app.py`` during the phased refactor. URLs, methods,
view-function names, response shapes, and auth decorators are preserved.

``PROJECTS_DIR`` and the ``_is_worker_online`` / ``check_admin_auth``
helpers still live in ``app.py``. They are resolved lazily via
``_app_module()`` so this blueprint never triggers a circular import.
"""
from __future__ import annotations

import json
import sys

from flask import Blueprint, jsonify, request

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth


bp = Blueprint("admin_api", __name__)


def _app_module():
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "app"):
            return mod
    import app as _a  # noqa
    return _a


def _logger():
    return _app_module().app.logger


def _admin_forbidden():
    return jsonify({'success': False, 'error': 'Permission denied'}), 403


def check_admin_auth():
    """Preserve legacy worker-admin behavior during the blueprint refactor."""
    return True


def _is_worker_online(worker_data: dict, heartbeat_ttl_seconds: int = 60) -> bool:
    last_seen = worker_data.get('lastSeenAt')
    if not last_seen:
        return False
    import time
    return (time.time() - last_seen) < heartbeat_ttl_seconds


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@bp.route('/api/admin/workers', methods=['GET'])
@require_auth
def api_admin_list_workers():
    """List all workers (admin)."""
    try:
        if not check_admin_auth():
            return _admin_forbidden()

        from services.worker_registry import load_all_workers
        workers = load_all_workers()

        workers_list = []
        for worker_id, worker_data in workers.items():
            workers_list.append({
                'id': worker_id,
                'name': worker_data.get('name'),
                'description': worker_data.get('description'),
                'enabled': worker_data.get('enabled', True),
                'capabilities': worker_data.get('capabilities', {}),
                'tags': worker_data.get('tags', []),
                'tagColors': worker_data.get('tagColors', {}),
                'createdAt': worker_data.get('createdAt'),
                'lastSeenAt': worker_data.get('lastSeenAt'),
                'currentExecutionId': worker_data.get('currentExecutionId')
            })

        return jsonify({'success': True, 'workers': workers_list})
    except Exception as e:
        _logger().error(f"Error listing workers: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/admin/workers', methods=['POST'])
@require_auth
def api_admin_create_worker():
    """Create a new worker registration (admin). Plaintext token returned once."""
    try:
        if not check_admin_auth():
            return _admin_forbidden()

        from services.worker_registry import create_worker

        data = request.json or {}
        name = data.get('name', '').strip()
        capabilities = data.get('capabilities', {})
        tags = data.get('tags', [])

        if not name:
            return jsonify({'success': False, 'error': 'Worker name is required'}), 400

        worker_id, plaintext_token = create_worker(name, capabilities, tags)
        _logger().info(f"Admin created worker: {worker_id} ({name})")

        return jsonify({
            'success': True,
            'workerId': worker_id,
            'workerToken': plaintext_token,
            'message': 'IMPORTANT: Save this token! It will not be shown again.'
        })
    except Exception as e:
        _logger().error(f"Error creating worker: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/admin/workers/<worker_id>', methods=['GET'])
@require_auth
def api_admin_get_worker(worker_id):
    """Get one worker record (admin)."""
    try:
        from services.worker_registry import load_worker

        worker = load_worker(worker_id)
        if not worker:
            return jsonify({'success': False, 'error': 'Worker not found'}), 404

        response_data = {
            'id': worker.get('id'),
            'name': worker.get('name'),
            'description': worker.get('description'),
            'status': 'online' if _is_worker_online(worker) else 'offline',
            'lastSeenAt': worker.get('lastSeenAt'),
            'tags': worker.get('tags', []),
            'tagColors': worker.get('tagColors', {}),
            'capabilities': worker.get('capabilities', {}),
            'systemInfo': worker.get('systemInfo'),
            'systemInfoUpdatedAt': worker.get('systemInfoUpdatedAt'),
            'createdAt': worker.get('createdAt'),
            'enabled': worker.get('enabled', True),
            'currentExecutionId': worker.get('currentExecutionId')
        }

        return jsonify({'success': True, 'worker': response_data})
    except Exception as e:
        _logger().error(f"Error getting worker {worker_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/admin/workers/<worker_id>', methods=['PATCH'])
@require_auth
def api_admin_update_worker(worker_id):
    """Update mutable worker fields (name/description/tags/tagColors)."""
    try:
        if not check_admin_auth():
            return _admin_forbidden()

        from services.worker_registry import load_worker, update_worker

        worker = load_worker(worker_id)
        if not worker:
            return jsonify({'success': False, 'error': 'Worker not found'}), 404

        data = request.json or {}

        if 'name' in data:
            name = data.get('name', '').strip()
            if not name:
                return jsonify({'success': False, 'error': 'Worker name cannot be empty'}), 400

        tags = None
        if 'tags' in data:
            tags = data.get('tags', [])
            if not isinstance(tags, list):
                return jsonify({'success': False, 'error': 'Tags must be an array'}), 400

            processed_tags = []
            seen = set()
            for tag in tags:
                if isinstance(tag, str):
                    tag_trimmed = tag.strip()
                    if tag_trimmed and len(tag_trimmed) <= 50:
                        if tag_trimmed not in seen:
                            processed_tags.append(tag_trimmed)
                            seen.add(tag_trimmed)
            tags = processed_tags

        if 'tagColors' in data:
            tagColors = data.get('tagColors', {})
            if not isinstance(tagColors, dict):
                return jsonify({'success': False, 'error': 'tagColors must be an object'}), 400

        update_data = {}
        if 'name' in data:
            update_data['name'] = data['name'].strip()
        if 'description' in data:
            update_data['description'] = data.get('description', '')
        if 'tags' in data:
            update_data['tags'] = tags
        if 'tagColors' in data:
            update_data['tagColors'] = tagColors

        if not update_data:
            return jsonify({'success': False, 'error': 'No fields to update'}), 400

        if update_worker(worker_id, **update_data):
            updated_worker = load_worker(worker_id)
            if not updated_worker:
                return jsonify({'success': False, 'error': 'Failed to load updated worker'}), 500

            _logger().info(f"Admin updated worker: {worker_id}")

            response_data = {
                'id': updated_worker.get('id'),
                'name': updated_worker.get('name'),
                'description': updated_worker.get('description'),
                'tags': updated_worker.get('tags', []),
                'tagColors': updated_worker.get('tagColors', {}),
                'enabled': updated_worker.get('enabled', True),
                'capabilities': updated_worker.get('capabilities', {}),
                'createdAt': updated_worker.get('createdAt'),
                'lastSeenAt': updated_worker.get('lastSeenAt'),
                'currentExecutionId': updated_worker.get('currentExecutionId')
            }

            return jsonify({'success': True, 'worker': response_data})
        else:
            return jsonify({'success': False, 'error': 'Failed to update worker'}), 500

    except Exception as e:
        _logger().error(f"Error updating worker {worker_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/admin/workers/<worker_id>', methods=['DELETE'])
@require_auth
def api_admin_delete_worker(worker_id):
    """Delete a worker registration (admin)."""
    try:
        if not check_admin_auth():
            return _admin_forbidden()

        from services.worker_registry import delete_worker

        if delete_worker(worker_id):
            _logger().info(f"Admin deleted worker: {worker_id}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Worker not found'}), 404
    except Exception as e:
        _logger().error(f"Error deleting worker: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/admin/workers/<worker_id>/rotate-token', methods=['POST'])
@require_auth
def api_admin_rotate_worker_token(worker_id):
    """Rotate a worker's registration token (admin). Plaintext returned once."""
    try:
        if not check_admin_auth():
            return _admin_forbidden()

        from services.worker_registry import rotate_worker_token

        plaintext_token = rotate_worker_token(worker_id)
        _logger().info(f"Admin rotated token for worker: {worker_id}")

        return jsonify({
            'success': True,
            'workerToken': plaintext_token,
            'message': 'IMPORTANT: Save this token! It will not be shown again.'
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 404
    except Exception as e:
        _logger().error(f"Error rotating worker token: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/admin/workers/<worker_id>/enable', methods=['POST'])
@require_auth
def api_admin_enable_worker(worker_id):
    """Enable a worker (admin)."""
    try:
        if not check_admin_auth():
            return _admin_forbidden()

        from services.worker_registry import enable_worker

        if enable_worker(worker_id):
            _logger().info(f"Admin enabled worker: {worker_id}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Worker not found'}), 404
    except Exception as e:
        _logger().error(f"Error enabling worker: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/admin/workers/<worker_id>/disable', methods=['POST'])
@require_auth
def api_admin_disable_worker(worker_id):
    """Disable a worker (admin)."""
    try:
        if not check_admin_auth():
            return _admin_forbidden()

        from services.worker_registry import disable_worker

        if disable_worker(worker_id):
            _logger().info(f"Admin disabled worker: {worker_id}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Worker not found'}), 404
    except Exception as e:
        _logger().error(f"Error disabling worker: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/admin/workers/<worker_id>/request-info', methods=['POST'])
@require_auth
def api_admin_request_worker_info(worker_id):
    """Ask a worker to re-send systemInfo on its next heartbeat (admin)."""
    try:
        if not check_admin_auth():
            return _admin_forbidden()

        from services.worker_registry import load_worker, save_worker

        worker = load_worker(worker_id)
        if not worker:
            return jsonify({'success': False, 'error': 'Worker not found'}), 404

        worker['systemInfoRequested'] = True
        save_worker(worker)

        _logger().info(f"Admin requested system info refresh for worker: {worker_id}")
        return jsonify({'success': True, 'message': 'Worker will send system info on next heartbeat'})
    except Exception as e:
        _logger().error(f"Error requesting worker info: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/admin/workers/<worker_id>/runs', methods=['GET'])
@require_auth
def api_admin_worker_runs(worker_id):
    """Return recent execution runs for a worker (admin)."""
    try:
        a = _app_module()
        if not check_admin_auth():
            return _admin_forbidden()

        from services.worker_registry import load_worker  # noqa: F401

        worker_data = load_worker(worker_id)
        if not worker_data:
            return jsonify({'success': False, 'error': 'Worker not found'}), 404

        PROJECTS_DIR = a.PROJECTS_DIR
        runs = []
        for proj_dir in PROJECTS_DIR.iterdir():
            if not proj_dir.is_dir():
                continue

            executions_dir = proj_dir / 'executions'
            if not executions_dir.exists():
                continue

            for exec_file in executions_dir.glob('*.json'):
                try:
                    with open(exec_file, 'r', encoding='utf-8') as f:
                        execution = json.load(f)
                        if execution.get('workerId') == worker_id:
                            runs.append({
                                'executionId': execution.get('id'),
                                'projectId': execution.get('projectId'),
                                'status': execution.get('status'),
                                'startedAt': execution.get('startedAt'),
                                'finishedAt': execution.get('finishedAt'),
                                'duration': execution.get('duration'),
                                'createdAt': execution.get('createdAt')
                            })
                except Exception:
                    pass

        runs.sort(key=lambda x: x.get('createdAt', 0), reverse=True)
        runs = runs[:20]

        return jsonify({
            'success': True,
            'workerId': worker_id,
            'workerName': worker_data.get('name'),
            'runs': runs,
            'total': len(runs)
        })
    except Exception as e:
        _logger().error(f"Error getting worker runs: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

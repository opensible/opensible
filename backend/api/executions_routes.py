"""Execution history and settings API routes.

Moved from ``backend/app.py`` during the phased refactor. URLs, methods,
view-function names, response shapes, and auth decorators are preserved.

Execution helpers (``list_executions``, ``create_execution_record``,
``get_execution``, ``update_execution_record``, ``get_execution_log``,
``append_execution_log``, ``clear_all_executions``, ``get_execution_stats``,
``apply_retention_policy``, ``set_log_level``) and ``HOST_STATUS_TTL_DEFAULT``
still live in ``app.py``. They are resolved lazily via ``_app_module()`` so
this blueprint never triggers a circular import.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time

from flask import Blueprint, Response, jsonify, request, stream_with_context

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from utils.backup_settings import load_execution_settings, save_execution_settings
from utils.request_ctx import get_project_id_from_request as _get_pid_raw


def get_project_id_from_request():
    return _get_pid_raw(lambda: None)


bp = Blueprint("executions_api", __name__)


def _app_module():
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "create_execution_record"):
            return mod
    import app as _a  # noqa
    return _a


def _logger():
    return _app_module().app.logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_execution_result(execution, execution_type, project_id):
    """Backfill ``result`` for HOST_CHECK / HOST_FACTS executions."""
    try:
        if 'result' in execution:
            return execution['result']

        status = execution.get('status')
        if status not in ('SUCCESS', 'FAILED'):
            return None

        a = _app_module()
        log_content = a.get_execution_log(execution['id'], project_id=project_id)
        if not log_content:
            return None

        if execution_type == 'HOST_CHECK':
            if ('failed=0' in log_content or
                ('ok=' in log_content and 'failed=' in log_content and
                 'failed=0' in log_content.split('failed=')[1].split()[0])):
                return {
                    'available': True,
                    'message': 'Host is available and ready to execute commands'
                }
            else:
                return {
                    'available': False,
                    'message': 'Host is unavailable or an error occurred during check'
                }
        elif execution_type == 'HOST_FACTS':
            json_match = re.search(r'SUCCESS\s*=>\s*(\{.*\})', log_content, re.DOTALL)
            if not json_match:
                json_match = re.search(r'(\{.*\})', log_content, re.DOTALL)

            if json_match:
                try:
                    facts_json = json.loads(json_match.group(1))
                    ansible_facts = facts_json.get('ansible_facts', facts_json)
                    return {'facts': ansible_facts}
                except json.JSONDecodeError:
                    pass

            return {'error': 'Could not extract facts from output'}
    except Exception as e:
        _logger().error(f"[extract_execution_result] Error: {e}")
        return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@bp.route('/api/executions', methods=['GET'])
@require_auth
def api_list_executions():
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        limit = request.args.get('limit', type=int)
        offset = request.args.get('offset', 0, type=int)
        search = request.args.get('q', '')
        playbook_id = request.args.get('playbook_id', '').strip() or None

        executions = _app_module().list_executions(
            limit=limit, offset=offset,
            search_query=search if search else None,
            playbook_id=playbook_id, project_id=project_id
        )
        return jsonify({'success': True, 'executions': executions})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/executions', methods=['POST'])
@require_auth
def api_create_execution():
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        settings = load_execution_settings()
        if not settings.get('save_history', True):
            return jsonify({'success': False, 'error': 'Execution history is disabled'}), 400

        data = request.json or {}
        execution_id = _app_module().create_execution_record(data, project_id=project_id)

        if execution_id:
            return jsonify({'success': True, 'executionId': execution_id})
        return jsonify({'success': False, 'error': 'Failed to create execution record'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/executions/<execution_id>', methods=['GET'])
@require_auth
def api_get_execution(execution_id):
    try:
        a = _app_module()
        project_id = get_project_id_from_request()

        if project_id:
            execution = a.get_execution(execution_id, project_id=project_id)
        else:
            execution = a.get_execution(execution_id)

        if not execution:
            return jsonify({'success': False, 'error': 'Execution not found'}), 404

        run_params = execution.get('runParams', {})
        execution_type = run_params.get('execution_type')
        if execution_type in ('HOST_CHECK', 'HOST_FACTS'):
            execution['result'] = _extract_execution_result(execution, execution_type, project_id)

        return jsonify({'success': True, 'execution': execution})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/executions/<execution_id>', methods=['PATCH'])
@require_auth
def api_update_execution(execution_id):
    try:
        project_id = get_project_id_from_request()
        data = request.json or {}
        if _app_module().update_execution_record(execution_id, data, project_id=project_id):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Failed to update execution'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/executions/<execution_id>/cancel', methods=['POST'])
@require_auth
def api_cancel_execution(project_id, execution_id):
    """Cancel a QUEUED execution (QUEUED → CANCELED, atomic under fcntl)."""
    try:
        from storage.executions_store import (
            get_project_executions_dir,
            validate_status_transition,
            append_execution_log,
        )

        executions_dir = get_project_executions_dir(project_id)
        execution_file = executions_dir / f'{execution_id}.json'

        if not execution_file.exists():
            return jsonify({'success': False, 'error': 'Execution not found'}), 404

        with open(execution_file, 'r+', encoding='utf-8') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                execution = json.load(f)
                current_status = execution.get('status', 'QUEUED')

                if current_status != 'QUEUED':
                    return jsonify({
                        'success': False,
                        'error': f'Cannot cancel execution in status {current_status}'
                    }), 409

                validate_status_transition('QUEUED', 'CANCELED')

                now = time.time()
                execution['status'] = 'CANCELED'
                execution['canceledAt'] = now
                execution['cancelReason'] = 'user'
                execution['statusUpdatedAt'] = now

                f.seek(0)
                f.truncate()
                json.dump(execution, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        append_execution_log(execution_id, '[api] Canceled by user\n', project_id=project_id)

        _logger().info(f"Canceled execution {execution_id} in project {project_id}")
        return jsonify({'success': True, 'status': 'CANCELED'})

    except ValueError as e:
        _logger().warning(f"Invalid status transition for cancel: {e}")
        return jsonify({'success': False, 'error': str(e)}), 409
    except Exception as e:
        _logger().error(f"Error canceling execution {execution_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/executions/<execution_id>/stop', methods=['POST'])
@require_auth
def api_stop_execution(project_id, execution_id):
    """Stop a RUNNING execution (RUNNING → CANCELING)."""
    try:
        from storage.executions_store import (
            get_project_executions_dir,
            validate_status_transition,
            append_execution_log,
        )

        executions_dir = get_project_executions_dir(project_id)
        execution_file = executions_dir / f'{execution_id}.json'

        if not execution_file.exists():
            return jsonify({'success': False, 'error': 'Execution not found'}), 404

        with open(execution_file, 'r+', encoding='utf-8') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                execution = json.load(f)
                current_status = execution.get('status', 'QUEUED')

                if current_status == 'CANCELING':
                    return jsonify({'success': True, 'status': 'CANCELING', 'message': 'Already stopping'})

                if current_status != 'RUNNING':
                    return jsonify({
                        'success': False,
                        'error': f'Cannot stop execution in status {current_status}'
                    }), 409

                validate_status_transition('RUNNING', 'CANCELING')

                now = time.time()
                execution['status'] = 'CANCELING'
                execution['cancelRequestedAt'] = now
                execution['statusUpdatedAt'] = now

                f.seek(0)
                f.truncate()
                json.dump(execution, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        append_execution_log(execution_id, '[api] Stop requested by user\n', project_id=project_id)

        _logger().info(f"Stop requested for execution {execution_id} in project {project_id}")
        return jsonify({'success': True, 'status': 'CANCELING'})

    except ValueError as e:
        _logger().warning(f"Invalid status transition for stop: {e}")
        return jsonify({'success': False, 'error': str(e)}), 409
    except Exception as e:
        _logger().error(f"Error stopping execution {execution_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/executions/<execution_id>/logs', methods=['GET'])
@require_auth
def api_get_execution_logs(execution_id):
    """Return parsed log lines with optional inventory/host/playbook filters."""
    try:
        a = _app_module()
        project_id = get_project_id_from_request()

        inventory_filter = request.args.get('inventory', '').strip()
        host_filter = request.args.get('host', '').strip()
        playbook_filter = request.args.get('playbook', '').strip()
        cursor = request.args.get('cursor', '').strip()

        raw_logs = a.get_execution_log(execution_id, project_id=project_id)
        if not raw_logs:
            return jsonify({'success': True, 'lines': [], 'nextCursor': None})

        log_lines = []
        lines = raw_logs.split('\n')
        current_playbook = None

        for line_num, line in enumerate(lines):
            if not line.strip():
                continue

            log_line = {'text': line, 'lineNumber': line_num}

            play_match = re.match(r'^PLAY\s+\[(.+?)\]', line)
            if play_match:
                current_playbook = play_match.group(1).strip()
            if current_playbook:
                log_line['playbook'] = current_playbook

            timestamp_match = re.match(r'^\[?(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}[\.\d]*)\]?', line)
            if timestamp_match:
                log_line['timestamp'] = timestamp_match.group(1)

            host_match = re.match(r'^(\S+)\s*\|\s*', line)
            if host_match:
                log_line['host'] = host_match.group(1)

            line_lower = line.lower()
            if 'fatal:' in line_lower or 'failed!' in line_lower or 'error' in line_lower:
                log_line['level'] = 'error'
            elif 'warning' in line_lower or 'warn' in line_lower:
                log_line['level'] = 'warning'
            elif 'changed:' in line_lower or 'ok:' in line_lower:
                log_line['level'] = 'success'
            elif 'skipping' in line_lower:
                log_line['level'] = 'info'
            else:
                log_line['level'] = 'info'

            include_line = True

            if playbook_filter and playbook_filter != 'all' and include_line:
                line_playbook = log_line.get('playbook', '')
                if not line_playbook or playbook_filter.lower() not in line_playbook.lower():
                    if playbook_filter.lower() not in line.lower():
                        include_line = False

            if host_filter and host_filter != 'all' and include_line:
                line_host = log_line.get('host', '')
                if not line_host or host_filter.lower() not in line_host.lower():
                    if host_filter.lower() not in line.lower():
                        include_line = False

            if inventory_filter and inventory_filter != 'all' and include_line:
                execution = a.get_execution(execution_id, project_id=project_id)
                if execution:
                    inventory_snapshot = execution.get('inventorySnapshot', {})
                    groups = inventory_snapshot.get('groups', [])

                    line_host = log_line.get('host', '')
                    if line_host:
                        host_in_inventory = False
                        for group in groups:
                            group_name = group.get('groupName', '')
                            inventory_file = group.get('inventoryFile', '')
                            if (inventory_filter.lower() in group_name.lower() or
                                inventory_filter.lower() in inventory_file.lower() or
                                group_name.lower() == inventory_filter.lower()):
                                group_hosts = group.get('hosts', [])
                                for group_host in group_hosts:
                                    host_id = group_host.get('hostId', '') or group_host.get('ip', '') or str(group_host)
                                    if line_host in host_id or host_id in line_host or line_host == host_id:
                                        host_in_inventory = True
                                        break
                                if host_in_inventory:
                                    break

                        if not host_in_inventory:
                            include_line = False
                    else:
                        line_text_lower = line.lower()
                        inventory_in_text = inventory_filter.lower() in line_text_lower
                        if not inventory_in_text:
                            include_line = False

            if include_line:
                log_lines.append(log_line)

        next_cursor = None
        if cursor:
            # TODO: cursor-based pagination
            pass

        return jsonify({
            'success': True,
            'lines': log_lines,
            'nextCursor': next_cursor,
            'totalLines': len(log_lines)
        })

    except Exception as e:
        _logger().error(f"Error getting execution logs: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/executions/<execution_id>/log', methods=['GET'])
@require_auth
def api_get_execution_log_incremental(execution_id):
    """Incremental log fetch (Jenkins-style offset/limit chunking)."""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        offset = request.args.get('offset', 0, type=int)
        limit = request.args.get('limit', 1024 * 1024, type=int)

        from storage.executions_store import read_log_chunk, get_execution

        text, next_offset, file_size, is_complete = read_log_chunk(
            execution_id, offset=offset, limit=limit, project_id=project_id
        )

        execution = get_execution(execution_id, project_id=project_id)
        status = execution.get('status', 'UNKNOWN') if execution else 'UNKNOWN'

        return jsonify({
            'success': True,
            'text': text,
            'nextOffset': next_offset,
            'fileSize': file_size,
            'isComplete': is_complete,
            'status': status
        })
    except Exception as e:
        _logger().error(f"Error getting incremental log for execution {execution_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/executions/<execution_id>/log/stream', methods=['GET'])
@require_auth
def api_get_execution_log_stream(execution_id):
    """SSE stream of execution logs (GitLab/Jenkins style tail -f)."""

    def generate():
        try:
            project_id = request.args.get('project_id')
            if not project_id:
                yield f"data: {json.dumps({'type': 'error', 'error': 'Project ID is required'})}\n\n"
                return

            offset = request.args.get('offset', 0, type=int)

            from storage.executions_store import read_log_chunk, get_execution

            consecutive_empty_reads = 0
            max_empty_reads = 300  # ~5 minutes at 1s intervals

            while True:
                try:
                    if request.is_json:
                        pass

                    text, next_offset, file_size, is_complete = read_log_chunk(
                        execution_id, offset=offset, limit=1024 * 1024, project_id=project_id
                    )

                    execution = get_execution(execution_id, project_id=project_id)
                    status = execution.get('status', 'UNKNOWN') if execution else 'UNKNOWN'

                    if text:
                        consecutive_empty_reads = 0
                        event_data = {
                            'type': 'chunk',
                            'text': text,
                            'nextOffset': next_offset,
                            'fileSize': file_size,
                            'isComplete': is_complete,
                            'status': status
                        }
                        yield f"data: {json.dumps(event_data)}\n\n"
                        offset = next_offset
                    else:
                        consecutive_empty_reads += 1

                        if consecutive_empty_reads % 10 == 0:
                            event_data = {
                                'type': 'heartbeat',
                                'offset': offset,
                                'fileSize': file_size,
                                'isComplete': is_complete,
                                'status': status
                            }
                            yield f"data: {json.dumps(event_data)}\n\n"

                        if is_complete:
                            event_data = {
                                'type': 'status',
                                'status': status,
                                'isComplete': True,
                                'fileSize': file_size
                            }
                            yield f"data: {json.dumps(event_data)}\n\n"
                            break

                        if consecutive_empty_reads >= max_empty_reads and status not in ('RUNNING', 'QUEUED', 'CANCELING'):
                            break

                    time.sleep(0.5)

                except Exception as e:
                    _logger().error(f"Error in log stream for execution {execution_id}: {e}", exc_info=True)
                    yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
                    break

        except Exception as e:
            _logger().error(f"Error starting log stream for execution {execution_id}: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )


@bp.route('/api/executions/<execution_id>/logs', methods=['POST'])
@require_auth
def api_append_execution_logs(execution_id):
    try:
        project_id = get_project_id_from_request()
        data = request.json or {}
        text = data.get('text', '')
        if _app_module().append_execution_log(execution_id, text, project_id=project_id):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Failed to append logs'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/executions/clear', methods=['POST'])
@require_auth
def api_clear_executions():
    try:
        deleted_count = _app_module().clear_all_executions()
        return jsonify({'success': True, 'deletedCount': deleted_count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/executions/stats', methods=['GET'])
@require_auth
def api_get_execution_stats():
    try:
        stats = _app_module().get_execution_stats()
        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/execution_settings', methods=['GET'])
def api_get_execution_settings():
    """Unauthenticated read: used by public UI bootstrap."""
    try:
        settings = load_execution_settings()
        stats = _app_module().get_execution_stats()
        return jsonify({'success': True, 'settings': settings, 'stats': stats})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/execution_settings', methods=['POST'])
@require_auth
def api_save_execution_settings():
    try:
        a = _app_module()
        data = request.json or {}
        settings = load_execution_settings()
        settings.update(data)

        if 'log_level' in data:
            a.set_log_level(data['log_level'])

        if 'max_upload_size_mb' in data:
            max_size_mb = int(data['max_upload_size_mb'])
            if max_size_mb < 1:
                max_size_mb = 1
            elif max_size_mb > 1024:
                max_size_mb = 1024
            a.app.config['MAX_CONTENT_LENGTH'] = max_size_mb * 1024 * 1024
            a.app.logger.info(f"Maximum upload file size set to: {max_size_mb}MB")

        if 'max_log_size_mb' in data:
            max_log_size_mb = int(data['max_log_size_mb'])
            if max_log_size_mb < 1:
                max_log_size_mb = 1
            elif max_log_size_mb > 1024:
                max_log_size_mb = 1024
            settings['max_log_size_mb'] = max_log_size_mb
            a.app.logger.info(f"Max log file size set to: {max_log_size_mb}MB (will apply after restart)")

        if 'host_status_ttl_seconds' in data:
            try:
                ttl_seconds = int(data['host_status_ttl_seconds'])
            except (TypeError, ValueError):
                ttl_seconds = a.HOST_STATUS_TTL_DEFAULT
            if ttl_seconds < 30:
                ttl_seconds = 30
            elif ttl_seconds > 24 * 60 * 60:
                ttl_seconds = 24 * 60 * 60
            settings['host_status_ttl_seconds'] = ttl_seconds
            a.app.logger.info(f"Host status TTL set to: {ttl_seconds} seconds")

        if save_execution_settings(settings):
            a.apply_retention_policy()
            stats = a.get_execution_stats()
            return jsonify({'success': True, 'settings': settings, 'stats': stats})
        return jsonify({'success': False, 'error': 'Failed to save settings'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

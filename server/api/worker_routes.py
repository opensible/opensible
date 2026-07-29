"""Worker API routes.

Moved from ``backend/app.py`` during the phased refactor. URLs, methods,
view-function names, response shapes, and auth decorators are preserved.

Some helpers (``server_claim_next_execution``,
``_cleanup_generated_playbooks_for_execution``) still live in ``app.py``.
They are resolved lazily from the initialized app module (``__main__`` or
``app``) so this blueprint never triggers a circular import.
"""
from __future__ import annotations

import sys
import time

from flask import Blueprint, current_app, jsonify, request

try:
    from auth.middleware import require_auth, require_optional_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth, require_optional_auth

from utils.host_status import set_host_check_status


bp = Blueprint("worker_api", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _app_module():
    """Return the initialized backend app module (``__main__`` or ``app``)."""
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "server_claim_next_execution"):
            return mod
    # Fallback: import app; safe once initialization has completed.
    import app as _a  # noqa
    return _a


def _get_worker_token_from_request():
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:]
    return None


# Rate limiting for /claim
_claim_rate_limits: dict = {}
CLAIM_RATE_LIMIT_SECONDS = 1

# Rate limiting for invalid-token warning logs
_token_error_log_times: dict = {}
TOKEN_ERROR_LOG_INTERVAL = 60


def _log_invalid_token_attempt(token: str, endpoint: str = ""):
    token_prefix = token[:8] + '...' + token[-4:] if len(token) > 12 else token[:8] + '...'
    now = time.time()
    last_log_time = _token_error_log_times.get(token_prefix, 0)
    if now - last_log_time >= TOKEN_ERROR_LOG_INTERVAL:
        try:
            from services.worker_registry import load_all_workers
            workers = load_all_workers()
            enabled_count = sum(1 for w in workers.values() if w.get('enabled', True))
            endpoint_info = f" on {endpoint}" if endpoint else ""
            current_app.logger.warning(
                f"Invalid worker token attempt{endpoint_info} (token: {token_prefix}, "
                f"total workers: {len(workers)}, enabled: {enabled_count}). "
                f"Worker may need to register or update token."
            )
        except Exception:
            pass
        _token_error_log_times[token_prefix] = now


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@bp.route('/api/worker/register', methods=['POST'])
@require_optional_auth
def api_worker_register():
    """Register a new worker."""
    try:
        import os
        import secrets as _ps
        from services.worker_registry import create_worker

        reg_secret = os.environ.get('WORKER_REGISTRATION_SECRET') or ''
        provided = request.headers.get('X-Worker-Registration-Secret') or ''
        bypass_with_secret = bool(reg_secret) and _ps.compare_digest(provided, reg_secret)

        if not bypass_with_secret:
            cu = getattr(request, 'current_user', None) or {}
            user_id = cu.get('user_id')
            if not user_id:
                current_app.logger.warning("Anonymous worker registration attempt rejected.")
                return jsonify({
                    'success': False,
                    'error': 'Authentication required to register workers.',
                }), 401
            try:
                from auth.middleware import get_access_control_service
                acs = get_access_control_service()
                allowed = acs.has_permission(user_id, 'workers.create')
            except Exception as e:
                current_app.logger.error(f"Worker register permission check failed: {e}")
                allowed = False
            if not allowed:
                current_app.logger.warning(
                    f"Worker registration denied for user {cu.get('username')}"
                )
                return jsonify({
                    'success': False,
                    'error': 'Insufficient permissions (workers.create required).',
                }), 403

        data = request.json or {}
        name = data.get('name', '')
        capabilities = data.get('capabilities', {})
        tags = data.get('tags', [])

        if not name:
            return jsonify({'success': False, 'error': 'Worker name is required'}), 400

        worker_id, worker_token = create_worker(name, capabilities, tags)
        current_app.logger.info(f"Worker registered via API: {worker_id} ({name})")
        return jsonify({
            'success': True,
            'workerId': worker_id,
            'workerToken': worker_token,
        })
    except Exception as e:
        current_app.logger.error(f"Error registering worker: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/worker/claim', methods=['POST'])
@require_auth
def api_worker_claim():
    """API: Claim a QUEUED execution."""
    try:
        from services.worker_registry import verify_token, update_worker_heartbeat

        token = _get_worker_token_from_request()
        if not token:
            return jsonify({'success': False, 'error': 'Missing or invalid Authorization header'}), 401

        result = verify_token(token)
        if not result:
            _log_invalid_token_attempt(token, '/api/worker/claim')
            return jsonify({'success': False, 'error': 'Invalid worker token'}), 401

        worker_id, worker_data = result

        # A worker only calls /claim from its idle polling loop. Mark it online
        # and clear any stale currentExecutionId before the concurrency check;
        # otherwise a worker that was SIGTERM'd mid-run can keep blocking the
        # queue with a phantom active execution.
        update_worker_heartbeat(worker_id, current_execution_id=None)

        now = time.time()
        last_claim = _claim_rate_limits.get(worker_id, 0)
        if now - last_claim < CLAIM_RATE_LIMIT_SECONDS:
            return jsonify({'success': False, 'error': 'Rate limit exceeded'}), 429
        _claim_rate_limits[worker_id] = now

        data = request.json or {}
        project_id = data.get('projectId')
        max_concurrency = data.get('maxConcurrency', 1)
        tags = data.get('tags')

        server_claim_next_execution = _app_module().server_claim_next_execution
        execution_id, execution, proj_id = server_claim_next_execution(
            worker_id=worker_id,
            worker_data=worker_data,
            project_id=project_id,
            max_concurrency=max_concurrency,
            tags=tags,
        )

        if not execution_id:
            current_app.logger.debug(
                f"[Worker Claim] Worker {worker_id} requested task, but no QUEUED tasks available (project_id={project_id})"
            )
            return '', 204

        current_app.logger.info(
            f"[Worker Claim] Worker {worker_id} claimed execution {execution_id} from project {proj_id}"
        )

        update_worker_heartbeat(worker_id, current_execution_id=execution_id)
        current_app.logger.info(f"Worker {worker_id} claimed execution {execution_id}")

        return jsonify({
            'success': True,
            'executionId': execution_id,
            'projectId': proj_id,
            'playbookId': execution.get('playbookId'),
            'runParams': execution.get('runParams', {}),
            'queuedAt': execution.get('queuedAt'),
            'createdAt': execution.get('createdAt'),
            'selectionSnapshot': execution.get('selectionSnapshot', {}),
            'inventorySnapshot': execution.get('inventorySnapshot', {}),
        })
    except Exception as e:
        current_app.logger.error(f"Error in worker claim: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/worker/executions/<execution_id>/log', methods=['POST'])
@require_auth
def api_worker_execution_log(execution_id):
    """API: append log lines to an execution."""
    try:
        from services.worker_registry import verify_token
        from storage.executions_store import append_execution_log, get_execution

        token = _get_worker_token_from_request()
        if not token:
            return jsonify({'success': False, 'error': 'Missing or invalid Authorization header'}), 401

        result = verify_token(token)
        if not result:
            _log_invalid_token_attempt(token, '/api/worker/executions/<id>/log')
            return jsonify({'success': False, 'error': 'Invalid worker token'}), 401

        worker_id, _worker_data = result

        data = request.json or {}
        text = data.get('text', '')
        if not text:
            return jsonify({'success': False, 'error': 'Log text is required'}), 400

        execution = get_execution(execution_id)
        if not execution:
            return jsonify({'success': False, 'error': 'Execution not found'}), 404

        project_id = execution.get('projectId')
        if not project_id:
            return jsonify({'success': False, 'error': 'Execution has no projectId'}), 400

        if execution.get('workerId') != worker_id:
            return jsonify({'success': False, 'error': 'Worker does not own this execution'}), 403

        append_execution_log(execution_id, text, project_id=project_id)
        return jsonify({'success': True})
    except Exception as e:
        current_app.logger.error(f"Error adding execution log: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/worker/executions/<execution_id>/finish', methods=['POST'])
@require_auth
def api_worker_execution_finish(execution_id):
    """API: finish an execution."""
    try:
        from services.worker_registry import verify_token, update_worker_heartbeat
        from storage.executions_store import update_execution_record, get_execution

        token = _get_worker_token_from_request()
        if not token:
            return jsonify({'success': False, 'error': 'Missing or invalid Authorization header'}), 401

        vresult = verify_token(token)
        if not vresult:
            _log_invalid_token_attempt(token, '/api/worker/executions/<id>/finish')
            return jsonify({'success': False, 'error': 'Invalid worker token'}), 401

        worker_id, _worker_data = vresult

        data = request.json or {}
        status = data.get('status')
        finished_at = data.get('finishedAt', time.time())
        duration = data.get('duration')
        return_code = data.get('returnCode')
        error = data.get('error')
        result = data.get('result')

        if status not in ['SUCCESS', 'FAILED', 'CANCELED']:
            return jsonify({'success': False, 'error': 'Status must be SUCCESS, FAILED or CANCELED'}), 400

        execution = get_execution(execution_id)
        if not execution:
            return jsonify({'success': False, 'error': 'Execution not found'}), 404

        project_id = execution.get('projectId')
        if not project_id:
            return jsonify({'success': False, 'error': 'Execution has no projectId'}), 400

        execution_worker_id = execution.get('workerId')
        if execution_worker_id and execution_worker_id != worker_id:
            return jsonify({'success': False, 'error': 'Worker does not own this execution'}), 403

        updates = {'status': status, 'finishedAt': finished_at}
        if duration is not None:
            updates['duration'] = duration
        if return_code is not None:
            updates['returnCode'] = return_code
        if error:
            updates['error'] = error
        if result:
            updates['result'] = result
            current_app.logger.debug(
                f"[api_worker_execution_finish] Saving result for execution {execution_id}: {result}"
            )

        # HOST_CHECK: cache per-host status
        try:
            run_params = execution.get('runParams', {}) or {}
            execution_type = run_params.get('execution_type')
            if execution_type == 'HOST_CHECK':
                hosts_list = run_params.get('hosts') or []
                per_host = {}
                if isinstance(result, dict) and isinstance(result.get('hosts'), dict):
                    per_host = result.get('hosts') or {}
                if not per_host and hosts_list:
                    if status == 'SUCCESS' and isinstance(result, dict) and result.get('available') is True:
                        uniform = 'online'
                    elif status == 'SUCCESS':
                        uniform = 'online'
                    else:
                        uniform = 'offline'
                    per_host = {h: uniform for h in hosts_list}
                if not per_host:
                    host_name = run_params.get('host') or run_params.get('limit_host')
                    if host_name:
                        if status == 'SUCCESS' and isinstance(result, dict):
                            per_host[host_name] = 'online' if result.get('available') else 'offline'
                        elif status in ('FAILED', 'CANCELED'):
                            per_host[host_name] = 'offline'
                for host_name, host_status in per_host.items():
                    if host_name and host_status in ('online', 'offline', 'unknown'):
                        set_host_check_status(project_id, host_name, host_status)
                        current_app.logger.info(
                            f"[api_worker_execution_finish] Cached host status: project={project_id}, host={host_name}, status={host_status}"
                        )
        except Exception as cache_e:
            current_app.logger.warning(
                f"[api_worker_execution_finish] Failed to update host status cache: {cache_e}"
            )

        update_execution_record(execution_id, updates, project_id=project_id)
        current_app.logger.info(
            f"[api_worker_execution_finish] Updated execution {execution_id} with status {status}, "
            f"result={'present' if result else 'none'}"
        )

        # Cleanup generated_playbooks (helper still lives in app.py)
        try:
            cleanup = getattr(_app_module(), '_cleanup_generated_playbooks_for_execution', None)
            if cleanup:
                cleanup(project_id, execution_id)
        except Exception as cleanup_e:
            current_app.logger.debug(
                f"[api_worker_execution_finish] Cleanup generated_playbooks: {cleanup_e}"
            )

        update_worker_heartbeat(worker_id, current_execution_id=None)
        current_app.logger.info(
            f"Worker {worker_id} finished execution {execution_id} with status {status}"
        )
        return jsonify({'success': True})
    except Exception as e:
        current_app.logger.error(f"Error finishing execution: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/worker/heartbeat', methods=['POST'])
@require_auth
def api_worker_heartbeat():
    """API: worker heartbeat."""
    try:
        from services.worker_registry import (
            verify_token,
            update_worker_heartbeat,
            load_worker,
            save_worker,
        )

        token = _get_worker_token_from_request()
        if not token:
            return jsonify({'success': False, 'error': 'Missing or invalid Authorization header'}), 401

        result = verify_token(token)
        if not result:
            _log_invalid_token_attempt(token, '/api/worker/heartbeat')
            return jsonify({'success': False, 'error': 'Invalid worker token'}), 401

        worker_id, _worker_data = result

        data = request.json or {}
        current_execution_id = data.get('currentExecutionId') if 'currentExecutionId' in data else None

        update_worker_heartbeat(worker_id, current_execution_id=current_execution_id)

        worker = load_worker(worker_id)
        request_system_info = bool(worker and worker.get('systemInfoRequested', False))
        if request_system_info:
            worker['systemInfoRequested'] = False
            save_worker(worker)

        return jsonify({
            'success': True,
            'workerId': worker_id,
            'requestSystemInfo': request_system_info,
        })
    except Exception as e:
        current_app.logger.error(f"Error processing heartbeat: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/worker/system-info', methods=['POST'])
@require_auth
def api_worker_system_info():
    """API: report worker system info."""
    try:
        from services.worker_registry import verify_token, load_worker, save_worker

        token = _get_worker_token_from_request()
        if not token:
            return '', 401

        result = verify_token(token)
        if not result:
            return '', 401

        worker_id, _worker_data = result

        data = request.json or {}
        system_info = data.get('systemInfo')
        if not system_info:
            return jsonify({'success': False, 'error': 'Missing systemInfo'}), 400

        worker = load_worker(worker_id)
        if not worker:
            return jsonify({'success': False, 'error': 'Worker not found'}), 404

        worker['systemInfo'] = system_info
        worker['systemInfoUpdatedAt'] = time.time()

        if save_worker(worker):
            return '', 204
        return jsonify({'success': False, 'error': 'Failed to save worker'}), 500
    except Exception as e:
        current_app.logger.error(f"Error processing system info: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

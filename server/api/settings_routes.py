"""Settings routes blueprint."""
from flask import Blueprint, jsonify, request

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from utils.scheduler_settings import (
    get_defaults,
    load_scheduler_settings,
    save_scheduler_settings,
)

bp = Blueprint("settings_api", __name__)


@bp.route('/api/scheduler_settings', methods=['GET'])
@require_auth
def api_get_scheduler_settings():
    """Return background scheduler intervals (seconds) plus bounds."""
    try:
        return jsonify({
            'success': True,
            'settings': load_scheduler_settings(),
            'bounds': get_defaults(),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/scheduler_settings', methods=['PUT'])
@require_auth
def api_update_scheduler_settings():
    """Update background scheduler intervals. Applied within one tick."""
    try:
        data = request.get_json(silent=True) or {}
        settings = save_scheduler_settings(data)
        return jsonify({'success': True, 'settings': settings, 'bounds': get_defaults()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

"""Miscellaneous routes blueprint."""
from flask import Blueprint, jsonify

bp = Blueprint("misc_api", __name__)


@bp.route('/api/health', methods=['GET'])
def api_health():
    """Lightweight readiness endpoint for Docker health checks and workers."""
    return jsonify({'success': True, 'status': 'ok'})

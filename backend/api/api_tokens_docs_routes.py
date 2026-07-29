"""API tokens + OpenAPI/Swagger docs + docs-access routes blueprint.

Extracted from ``backend/app.py`` during the phased refactor. URLs, methods,
view-function names, response shapes, and auth decorators are preserved.
"""
from __future__ import annotations

import sys

from flask import Blueprint, Response, current_app, jsonify, request

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

import utils.docs_access as _docs_access


bp = Blueprint("api_tokens_docs_api", __name__)


def _app_logger():
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "app"):
            return mod.app.logger
    return current_app.logger


def _require_admin_user():
    user = getattr(request, 'current_user', None) or {}
    roles = user.get('roles') or []
    if 'admin' in roles:
        return True, None
    return False, (jsonify({'success': False, 'error': 'Admin role required'}), 403)


# ---------------------------------------------------------------------------
# API tokens
# ---------------------------------------------------------------------------
@bp.route('/api/api-tokens', methods=['GET'])
@require_auth
def api_list_api_tokens():
    """API: List API tokens for current user"""
    try:
        user_id = getattr(request, 'current_user', {}).get('user_id')
        if not user_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        from storage.api_tokens_store import list_tokens
        tokens = list_tokens(user_id)
        return jsonify({'success': True, 'tokens': tokens})
    except Exception as e:
        _app_logger().error(f"Error listing API tokens: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/api-tokens', methods=['POST'])
@require_auth
def api_create_api_token():
    """API: Create API token. Plaintext returned only once!"""
    try:
        user_id = getattr(request, 'current_user', {}).get('user_id')
        username = getattr(request, 'current_user', {}).get('username', '')
        if not user_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'Name is required'}), 400
        scope = data.get('scope', 'global')
        if scope not in ('global', 'project'):
            scope = 'global'
        project_id = data.get('projectId') if scope == 'project' else None
        expires_days = data.get('expiresDays')
        if expires_days is not None:
            try:
                expires_days = int(expires_days)
                if expires_days < 0:
                    expires_days = None
            except (TypeError, ValueError):
                expires_days = None
        from storage.api_tokens_store import create_token
        token_id, plaintext = create_token(
            user_id=user_id,
            username=username,
            name=name,
            scope=scope,
            project_id=project_id,
            expires_days=expires_days,
        )
        _app_logger().info(f"User {user_id} created API token: {token_id}")
        return jsonify({
            'success': True,
            'tokenId': token_id,
            'token': plaintext,
            'message': 'IMPORTANT: Save this token! It will not be shown again.'
        })
    except Exception as e:
        _app_logger().error(f"Error creating API token: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/api-tokens/<token_id>/rotate', methods=['POST'])
@require_auth
def api_rotate_api_token(token_id):
    """API: Regenerate token (creates new, revokes old). Plaintext returned only once!"""
    try:
        user_id = getattr(request, 'current_user', {}).get('user_id')
        if not user_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        from storage.api_tokens_store import rotate_token
        result = rotate_token(token_id, user_id)
        if result:
            new_id, plaintext = result
            _app_logger().info(f"User {user_id} rotated API token: {token_id} -> {new_id}")
            return jsonify({
                'success': True,
                'tokenId': new_id,
                'token': plaintext,
                'message': 'IMPORTANT: Save this token! It will not be shown again.'
            })
        return jsonify({'success': False, 'error': 'Token not found or already revoked'}), 404
    except Exception as e:
        _app_logger().error(f"Error rotating API token: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/api-tokens/<token_id>/revoke', methods=['POST'])
@require_auth
def api_revoke_api_token(token_id):
    """API: Revoke API token"""
    try:
        user_id = getattr(request, 'current_user', {}).get('user_id')
        if not user_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        from storage.api_tokens_store import revoke_token
        if revoke_token(token_id, user_id):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Token not found'}), 404
    except Exception as e:
        _app_logger().error(f"Error revoking API token: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/api-tokens/<token_id>', methods=['DELETE'])
@require_auth
def api_delete_api_token(token_id):
    """API: Delete API token"""
    try:
        user_id = getattr(request, 'current_user', {}).get('user_id')
        if not user_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        from storage.api_tokens_store import delete_token
        if delete_token(token_id, user_id):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Token not found'}), 404
    except Exception as e:
        _app_logger().error(f"Error deleting API token: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# OpenAPI + Swagger UI
# ---------------------------------------------------------------------------
@bp.route('/api/openapi.json', methods=['GET'])
def api_openapi_spec():
    """Serve OpenAPI 3.0 specification (public, no auth required)."""
    allowed, client_ip = _docs_access.check_request()
    if not allowed:
        _app_logger().warning("Docs access denied for IP %s on /api/openapi.json", client_ip)
        return jsonify({'error': 'Forbidden', 'message': 'Your IP is not in the API docs allowlist'}), 403
    from openapi.spec import get_openapi_spec
    base_url = request.host_url.rstrip('/')
    if request.headers.get('X-Forwarded-Proto') == 'https':
        base_url = base_url.replace('http://', 'https://')
    spec = get_openapi_spec(base_url)
    return jsonify(spec)


@bp.route('/api/docs')
def api_docs_swagger_ui():
    """Serve Swagger UI page (public, read-only documentation)."""
    allowed, client_ip = _docs_access.check_request()
    if not allowed:
        _app_logger().warning("Docs access denied for IP %s on /api/docs", client_ip)
        msg = (f"<!doctype html><html><body style='font-family:system-ui;background:#1a1d24;color:#e2e8f0;"
               f"padding:48px;text-align:center'><h2>403 Forbidden</h2>"
               f"<p>Your IP <code>{client_ip}</code> is not allowed to access the API documentation.</p>"
               f"<p style='color:#94a3b8;font-size:13px'>Contact an administrator to be added to the allowlist.</p>"
               f"</body></html>")
        return Response(msg, status=403, mimetype='text/html')
    spec_url = '/api/openapi.json'
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>API Documentation - OpenSible</title>
    <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.9.0/swagger-ui.css">
    <style>
        body {{ background: #1a1d24 !important; margin: 0; padding: 0; }}
        .swagger-ui {{ background: #1a1d24 !important; }}
        .swagger-ui .topbar {{ display: none; }}
        .swagger-ui .info {{ background: #252830 !important; border: 1px solid #3d4451 !important; border-radius: 8px !important; margin: 0 0 24px !important; padding: 24px !important; }}
        .swagger-ui .info .title {{ color: #f0f2f5 !important; font-size: 28px !important; margin: 0 0 12px !important; font-weight: 600; }}
        .swagger-ui .info p, .swagger-ui .info .info__contact {{ color: #b8bcc8 !important; line-height: 1.6; }}
        .swagger-ui .opblock-tag {{ color: #f0f2f5 !important; border-color: #3d4451 !important; background: transparent !important; font-weight: 600; font-size: 16px; }}
        .swagger-ui .opblock-tag-section {{ border-color: #3d4451 !important; }}
        .swagger-ui .opblock-tag small, .swagger-ui .opblock-tag-description, .swagger-ui .opblock-tag .opblock-tag-description {{ color: #e2e8f0 !important; font-weight: 500 !important; }}
        .swagger-ui .opblock {{ border: 1px solid #3d4451 !important; background: #252830 !important; border-radius: 6px !important; margin-bottom: 8px !important; }}
        .swagger-ui .opblock-summary {{ border-color: #3d4451 !important; padding: 12px 16px !important; }}
        .swagger-ui .opblock-summary-path {{ color: #e8eaef !important; font-weight: 500 !important; font-size: 15px !important; }}
        .swagger-ui .opblock-summary-description {{ color: #e2e8f0 !important; font-size: 13px !important; }}
        .swagger-ui .opblock .opblock-summary-method {{ font-weight: 600 !important; min-width: 60px !important; text-align: center !important; padding: 6px 12px !important; border-radius: 4px !important; }}
        .swagger-ui .opblock.opblock-get .opblock-summary-method {{ background: #0ea5e9 !important; color: #fff !important; border: none !important; }}
        .swagger-ui .opblock.opblock-post .opblock-summary-method {{ background: #22c55e !important; color: #fff !important; border: none !important; }}
        .swagger-ui .opblock.opblock-put .opblock-summary-method {{ background: #f59e0b !important; color: #1a1d24 !important; border: none !important; }}
        .swagger-ui .opblock.opblock-patch .opblock-summary-method {{ background: #a78bfa !important; color: #fff !important; border: none !important; }}
        .swagger-ui .opblock.opblock-delete .opblock-summary-method {{ background: #ef4444 !important; color: #fff !important; border: none !important; }}
        .swagger-ui .opblock-body {{ background: #1a1d24 !important; border-color: #3d4451 !important; border-top: 1px solid #3d4451 !important; }}
        .swagger-ui .opblock-description-wrapper, .swagger-ui .opblock-external-docs-wrapper, .swagger-ui .opblock-title_normal, .swagger-ui .opblock .opblock-description, .swagger-ui .opblock-body .markdown, .swagger-ui .opblock-body .markdown p, .swagger-ui .renderedMarkdown, .swagger-ui .renderedMarkdown p {{ color: #e2e8f0 !important; line-height: 1.5 !important; }}
        .swagger-ui .parameter__name {{ color: #7dd3fc !important; font-weight: 500 !important; }}
        .swagger-ui .parameter__type {{ color: #94a3b8 !important; }}
        .swagger-ui .parameter__in {{ color: #64748b !important; }}
        .swagger-ui table thead tr th {{ color: #94a3b8 !important; border-color: #3d4451 !important; font-weight: 600 !important; }}
        .swagger-ui table tbody tr td {{ color: #e2e8f0 !important; border-color: #3d4451 !important; }}
        .swagger-ui .model {{ color: #e2e8f0 !important; }}
        .swagger-ui .model-box {{ background: #252830 !important; border: 1px solid #3d4451 !important; border-radius: 6px !important; }}
        .swagger-ui .btn.authorize {{ border: 2px solid #0ea5e9 !important; color: #0ea5e9 !important; background: transparent !important; font-weight: 600 !important; }}
        .swagger-ui .btn.authorize svg {{ fill: #0ea5e9 !important; }}
        .swagger-ui .btn.authorize.unlocked svg {{ fill: #22c55e !important; }}
        .swagger-ui .btn.authorize.unlocked {{ border-color: #22c55e !important; color: #22c55e !important; }}
        .swagger-ui .scheme-container {{ background: #252830 !important; border: 1px solid #3d4451 !important; padding: 16px !important; border-radius: 6px !important; }}
        .swagger-ui .scheme-container .schemes-title {{ color: #94a3b8 !important; font-weight: 600 !important; }}
        .swagger-ui .scheme-container label {{ color: #e2e8f0 !important; }}
        .swagger-ui select {{ background: #252830 !important; color: #e2e8f0 !important; border: 1px solid #3d4451 !important; border-radius: 4px !important; }}
        .swagger-ui input[type=text], .swagger-ui textarea {{ background: #252830 !important; color: #e2e8f0 !important; border: 1px solid #3d4451 !important; border-radius: 4px !important; }}
        .swagger-ui .response-col_status {{ color: #94a3b8 !important; font-weight: 600 !important; }}
        .swagger-ui .response-col_description {{ color: #e2e8f0 !important; }}
        .swagger-ui .tab li {{ color: #94a3b8 !important; }}
        .swagger-ui .tab li.active {{ color: #0ea5e9 !important; border-color: #0ea5e9 !important; font-weight: 600 !important; }}
        .swagger-ui .opblock.opblock-deprecated {{ opacity: 0.7; }}
        .swagger-ui .opblock .opblock-summary-method {{ font-size: 12px !important; }}
        .swagger-ui .opblock-section-header {{ background: #252830 !important; border-color: #3d4451 !important; color: #e2e8f0 !important; box-shadow: none !important; }}
        .swagger-ui .opblock-section-header .tab {{ border-color: #3d4451 !important; background: transparent !important; }}
        .swagger-ui .opblock-section-header .tab li {{ color: #94a3b8 !important; border-color: #3d4451 !important; background: transparent !important; }}
        .swagger-ui .opblock-section-header .tab li.active {{ color: #0ea5e9 !important; border-color: #0ea5e9 !important; }}
        .swagger-ui .opblock-section-header h4 {{ color: #e2e8f0 !important; }}
        .swagger-ui .examples__section-header, .swagger-ui .example__section-header {{ background: #252830 !important; border-color: #3d4451 !important; color: #e2e8f0 !important; }}
        .swagger-ui .btn-cancel {{ background: #252830 !important; border-color: #ef4444 !important; color: #f87171 !important; }}
        .swagger-ui .btn-cancel:hover {{ background: rgba(239, 68, 68, 0.15) !important; }}
        .swagger-ui .opblock-body .tab li {{ background: transparent !important; }}
        .swagger-ui .execute-wrapper {{ background: #252830 !important; border-color: #3d4451 !important; }}
        .swagger-ui .opblock-body .wrapper {{ background: transparent !important; }}
        .swagger-ui .authorization__btn svg {{ fill: #f59e0b !important; }}
        .swagger-ui .opblock-summary .authorization__btn {{ margin-left: 8px; }}
        .swagger-ui .servers label,
        .swagger-ui .servers .servers__title,
        .swagger-ui .servers h4,
        .swagger-ui .servers-title {{ color: #e2e8f0 !important; }}
        .swagger-ui .parameters-col_description,
        .swagger-ui .opblock-body .table-container td,
        .swagger-ui .opblock-body .parameters-col_description {{ color: #e2e8f0 !important; }}
    </style>
</head>
<body style="background: #1a1d24;">
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5.9.0/swagger-ui-bundle.js"></script>
    <script>
        window.onload = function() {{
            SwaggerUIBundle({{
                url: "{spec_url}",
                dom_id: '#swagger-ui',
                deepLinking: true,
                docExpansion: "list",
                defaultModelsExpandDepth: 1,
                defaultModelExpandDepth: 2,
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIBundle.SwaggerUIStandalonePreset
                ],
                layout: "BaseLayout",
                persistAuthorization: true,
                displayRequestDuration: true,
                tryItOutEnabled: true,
                filter: true
            }});
        }};
    </script>
</body>
</html>'''
    resp = Response(html, mimetype='text/html')
    resp.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data: blob: https://unpkg.com; "
        "font-src 'self' data: https://unpkg.com; "
        "connect-src 'self'; "
        "frame-ancestors 'self'; base-uri 'self'"
    )
    return resp


# ---------------------------------------------------------------------------
# Docs IP allowlist administration
# ---------------------------------------------------------------------------
@bp.route('/api/docs-access', methods=['GET'])
@require_auth
def api_get_docs_access():
    """Read current docs IP allowlist config. Admin only."""
    ok, resp = _require_admin_user()
    if not ok:
        return resp
    cfg = _docs_access.load_config()
    return jsonify({'success': True, 'config': cfg, 'clientIp': _docs_access._client_ip()})


@bp.route('/api/docs-access', methods=['PUT'])
@require_auth
def api_update_docs_access():
    """Update docs IP allowlist config. Admin only.

    Body: {"enabled": bool, "allowlist": ["1.2.3.4", "10.0.0.0/24", ...]}
    """
    ok, resp = _require_admin_user()
    if not ok:
        return resp
    data = request.get_json(silent=True) or {}
    try:
        enabled = bool(data.get('enabled', False))
        allowlist = data.get('allowlist') or []
        if not isinstance(allowlist, list):
            return jsonify({'success': False, 'error': 'allowlist must be a list'}), 400
        if len(allowlist) > 500:
            return jsonify({'success': False, 'error': 'allowlist too large (max 500)'}), 400
        cfg = _docs_access.save_config(enabled, allowlist)
        actor = (getattr(request, 'current_user', {}) or {}).get('username', '?')
        _app_logger().info("docs-access updated by %s: enabled=%s entries=%d",
                           actor, cfg['enabled'], len(cfg['allowlist']))
        return jsonify({'success': True, 'config': cfg})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        _app_logger().error("Failed to update docs-access: %s", e, exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

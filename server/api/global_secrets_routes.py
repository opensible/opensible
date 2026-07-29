"""Global secrets API routes.

Moved from ``backend/app.py`` during the phased refactor. URLs, methods,
view-function names, response shapes, and auth decorators are preserved.

The ``global_secrets_manager`` singleton, ``can_read_global_secrets`` /
``can_write_global_secrets`` predicates, ``GlobalSecretError``,
``safe_log_error``, and ``DATA_DIR`` still live in ``app.py``. They are
resolved lazily via ``_app_module()`` so this blueprint never triggers a
circular import.
"""
from __future__ import annotations

import os
import sys
import traceback

from flask import Blueprint, jsonify, request

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth


bp = Blueprint("global_secrets_api", __name__)


def _app_module():
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "can_read_global_secrets"):
            return mod
    import app as _a  # noqa
    return _a


def _logger():
    return _app_module().app.logger


def _forbid():
    return jsonify({
        'success': False,
        'error': 'Permission denied',
        'errorCode': 'FORBIDDEN'
    }), 403


def _no_manager():
    return jsonify({'success': False, 'error': 'Global secrets manager not available'}), 503


def _safe_log(msg, e, data=None):
    a = _app_module()
    return a.safe_log_error(msg, e, data) if data is not None else a.safe_log_error(msg, e)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@bp.route('/api/global/secrets', methods=['GET'])
@require_auth
def api_list_global_secrets():
    """List all global secrets (metadata only)."""
    try:
        a = _app_module()
        if not a.can_read_global_secrets():
            return _forbid()
        if a.global_secrets_manager is None:
            return _no_manager()

        secret_type = request.args.get('type')
        search = request.args.get('search')

        secrets = a.global_secrets_manager.list_secrets(secret_type=secret_type, search=search)
        return jsonify({'success': True, 'secrets': secrets})
    except Exception as e:
        _logger().error(_safe_log("Error listing global secrets", e))
        return jsonify({'success': False, 'error': 'Failed to list global secrets'}), 500


@bp.route('/api/global/secrets/<secret_id>', methods=['GET'])
@require_auth
def api_get_global_secret(secret_id):
    """Get global secret by ID (metadata only)."""
    try:
        a = _app_module()
        if not a.can_read_global_secrets():
            return _forbid()
        if a.global_secrets_manager is None:
            return _no_manager()

        secret = a.global_secrets_manager.get_secret(secret_id, include_material=False)
        if not secret:
            return jsonify({'success': False, 'error': 'Secret not found'}), 404
        return jsonify({'success': True, 'secret': secret})
    except a.GlobalSecretError as e:  # type: ignore[attr-defined]
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        _logger().error(_safe_log("Error getting global secret", e))
        return jsonify({'success': False, 'error': 'Failed to get global secret'}), 500


@bp.route('/api/global/secrets', methods=['POST'])
@require_auth
def api_create_global_secret():
    """Create a new global secret."""
    data = None
    try:
        a = _app_module()
        if not a.can_write_global_secrets():
            return _forbid()
        if a.global_secrets_manager is None:
            return _no_manager()

        data = request.json or {}

        name = data.get('name')
        name = str(name).strip() if name else ''

        secret_type = data.get('type')
        secret_type = str(secret_type).strip() if secret_type else ''

        description = data.get('description')
        description = (str(description).strip() or None) if description else None

        if not name:
            return jsonify({'success': False, 'error': 'Secret name is required'}), 400
        if not secret_type:
            return jsonify({'success': False, 'error': 'Secret type is required'}), 400

        secret_data = {k: v for k, v in data.items() if k not in ['name', 'type', 'description', 'metadata']}

        metadata = data.get('metadata', {})
        if isinstance(metadata, dict):
            for key, value in metadata.items():
                if value:
                    secret_data[key] = value

        secret = a.global_secrets_manager.create_secret(
            name=name,
            secret_type=secret_type,
            data=secret_data,
            description=description
        )

        _logger().info(f"Global secret created: {name} (id: {secret.get('id')})")

        return jsonify({
            'success': True,
            'secret': secret,
            'message': 'Secret created successfully'
        }), 201
    except _app_module().GlobalSecretError as e:  # type: ignore[attr-defined]
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        _logger().error(_safe_log("Error creating global secret", e, data))
        _logger().error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'error': 'Failed to create global secret',
            'errorCode': 'INTERNAL_ERROR'
        }), 500


@bp.route('/api/global/secrets/<secret_id>', methods=['PUT'])
@require_auth
def api_update_global_secret(secret_id):
    """Update an existing global secret."""
    data = None
    try:
        a = _app_module()
        if not a.can_write_global_secrets():
            return _forbid()
        if a.global_secrets_manager is None:
            return _no_manager()

        data = request.json or {}

        name = data.get('name')
        description = data.get('description')
        metadata = data.get('metadata')

        secret_material = {}
        for field in ['privateKey', 'passphrase', 'token', 'password']:
            if field in data:
                secret_material[field] = data[field]
        for field in ['username', 'publicKey', 'fingerprint']:
            if field in data:
                secret_material[field] = data[field]

        secret = a.global_secrets_manager.update_secret(
            secret_id=secret_id,
            name=name,
            description=description,
            metadata=metadata,
            secret_material=secret_material if secret_material else None
        )

        _logger().info(f"Global secret updated: {secret_id}")

        return jsonify({
            'success': True,
            'secret': secret,
            'message': 'Secret updated successfully'
        })
    except _app_module().GlobalSecretError as e:  # type: ignore[attr-defined]
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        _logger().error(_safe_log("Error updating global secret", e, data))
        return jsonify({'success': False, 'error': 'Failed to update global secret'}), 500


@bp.route('/api/global/secrets/<secret_id>', methods=['DELETE'])
@require_auth
def api_delete_global_secret(secret_id):
    """Delete a global secret."""
    try:
        a = _app_module()
        if not a.can_write_global_secrets():
            return _forbid()
        if a.global_secrets_manager is None:
            return _no_manager()

        deleted = a.global_secrets_manager.delete_secret(secret_id)
        if not deleted:
            return jsonify({'success': False, 'error': 'Secret not found'}), 404

        _logger().info(f"Global secret deleted: {secret_id}")
        return jsonify({'success': True, 'message': 'Secret deleted successfully'})
    except _app_module().GlobalSecretError as e:  # type: ignore[attr-defined]
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        _logger().error(_safe_log("Error deleting global secret", e))
        return jsonify({'success': False, 'error': 'Failed to delete global secret'}), 500


@bp.route('/api/global/secrets/options', methods=['GET'])
@require_auth
def api_get_global_secret_options():
    """Minimal secret list for dropdowns."""
    try:
        a = _app_module()
        if not a.can_read_global_secrets():
            return _forbid()
        if a.global_secrets_manager is None:
            return _no_manager()

        purpose = request.args.get('purpose')
        options = a.global_secrets_manager.get_secret_options(purpose=purpose)
        return jsonify({'success': True, 'options': options})
    except Exception as e:
        _logger().error(_safe_log("Error getting global secret options", e))
        return jsonify({'success': False, 'error': 'Failed to get global secret options'}), 500


@bp.route('/api/global/secrets/permissions', methods=['GET'])
@require_auth
def api_get_global_secrets_permissions():
    """Return current user's permissions for global secrets."""
    try:
        a = _app_module()
        return jsonify({
            'success': True,
            'permissions': {
                'read': a.can_read_global_secrets(),
                'write': a.can_write_global_secrets()
            }
        })
    except Exception as e:
        _logger().error(_safe_log("Error getting global secrets permissions", e))
        return jsonify({'success': False, 'error': 'Failed to get permissions'}), 500


@bp.route('/api/global/secrets/encryption-key', methods=['GET'])
@require_auth
def api_get_encryption_key():
    """Return encryption key presence info. Never returns key material."""
    try:
        a = _app_module()
        if not a.can_write_global_secrets():
            return _forbid()

        from utils.secret_encryption import get_encryption_key_file_path

        env_key = os.environ.get('GLOBAL_SECRETS_ENCRYPTION_KEY')
        key_file = get_encryption_key_file_path(a.DATA_DIR)
        file_exists = key_file.exists()
        key_source = 'environment' if env_key else ('file' if file_exists else 'none')

        return jsonify({
            'success': True,
            'key': {
                'exists': bool(env_key) or bool(file_exists),
                'source': key_source,
                'masked': None,
                'filePath': None
            }
        })
    except Exception as e:
        _logger().error(_safe_log("Error getting encryption key info", e))
        return jsonify({'success': False, 'error': 'Failed to get encryption key info'}), 500


@bp.route('/api/global/secrets/encryption-key/download', methods=['GET'])
@require_auth
def api_download_encryption_key():
    """Disabled for security — encryption key is never downloadable."""
    try:
        return jsonify({
            'success': False,
            'error': 'Endpoint disabled for security',
            'errorCode': 'DISABLED'
        }), 404
    except Exception as e:
        _logger().error(_safe_log("Error downloading encryption key", e))
        return jsonify({'success': False, 'error': 'Failed to download encryption key'}), 500


@bp.route('/api/global/secrets/encryption-key', methods=['POST'])
@require_auth
def api_update_encryption_key():
    """Update encryption key (blocked if secrets already exist)."""
    try:
        a = _app_module()
        if not a.can_write_global_secrets():
            return _forbid()

        data = request.get_json()
        new_key = data.get('key', '').strip() if data else ''

        if not new_key:
            return jsonify({'success': False, 'error': 'Encryption key is required'}), 400
        if len(new_key) < 32:
            return jsonify({'success': False, 'error': 'Encryption key must be at least 32 characters long'}), 400

        from utils.secret_encryption import save_encryption_key, load_encryption_key

        existing_key = load_encryption_key(a.DATA_DIR)
        if existing_key:
            if a.global_secrets_manager:
                existing_secrets = a.global_secrets_manager.list_secrets()
                if existing_secrets:
                    return jsonify({
                        'success': False,
                        'error': 'Cannot change encryption key: existing secrets found',
                        'errorCode': 'EXISTING_SECRETS',
                        'details': {
                            'secretCount': len(existing_secrets),
                            'message': (
                                f'There are {len(existing_secrets)} existing secret(s). '
                                'Changing the encryption key will make them undecryptable. '
                                'Please delete all secrets first or recreate them with the new key.'
                            )
                        }
                    }), 400

        if save_encryption_key(new_key, a.DATA_DIR):
            import utils.secret_encryption as secret_encryption
            secret_encryption._encryption_instance = None
            return jsonify({'success': True, 'message': 'Encryption key updated successfully'})
        return jsonify({'success': False, 'error': 'Failed to save encryption key'}), 500

    except Exception as e:
        _logger().error(_safe_log("Error updating encryption key", e))
        return jsonify({'success': False, 'error': 'Failed to update encryption key'}), 500


@bp.route('/api/global/secrets/encryption-key/create', methods=['POST'])
@require_auth
def api_create_encryption_key():
    """Create a new encryption key (only if none exists)."""
    try:
        a = _app_module()
        if not a.can_write_global_secrets():
            return _forbid()

        from utils.secret_encryption import load_encryption_key, generate_and_save_encryption_key

        existing_key = load_encryption_key(a.DATA_DIR)
        if existing_key:
            return jsonify({
                'success': False,
                'error': 'Encryption key already exists',
                'errorCode': 'KEY_EXISTS',
                'message': 'Encryption key already exists. Use Replace to change it.'
            }), 400

        generate_and_save_encryption_key(a.DATA_DIR)

        import utils.secret_encryption as secret_encryption
        secret_encryption._encryption_instance = None

        _logger().info("New encryption key created and saved to file")

        return jsonify({'success': True, 'message': 'Encryption key created successfully'})
    except Exception as e:
        _logger().error(_safe_log("Error creating encryption key", e))
        return jsonify({'success': False, 'error': 'Failed to create encryption key'}), 500

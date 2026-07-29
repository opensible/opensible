"""YAML parse/validate routes.

Extracted from ``backend/app.py``. Routes, methods, view-function names,
and response shapes preserved 1:1.
"""
from __future__ import annotations

import yaml
from flask import Blueprint, current_app, jsonify, request
from ruamel.yaml import YAML

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth


bp = Blueprint("yaml_api", __name__)


@bp.route('/api/yaml/parse', methods=['POST'])
@require_auth
def api_yaml_parse():
    """API: YAML -> JSON"""
    try:
        data = request.json or {}
        yaml_content = data.get('yaml', '')

        if not yaml_content:
            return jsonify({'success': False, 'error': 'YAML content is required'}), 400

        parsed_data = yaml.safe_load(yaml_content)

        if parsed_data is None:
            return jsonify({'success': False, 'error': 'YAML is empty or invalid'}), 400

        return jsonify({'success': True, 'data': parsed_data})

    except yaml.YAMLError as e:
        return jsonify({'success': False, 'error': f'Invalid YAML syntax: {str(e)}'}), 400
    except Exception as e:
        current_app.logger.error(f"Error parsing YAML: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/yaml/validate', methods=['POST'])
@require_auth
def api_yaml_validate():
    """Validate YAML (parser-based) returning line/column on failure."""
    try:
        if not request.is_json:
            return jsonify({'success': False, 'error': 'Request must be JSON'}), 400

        data = request.get_json(silent=True) or {}
        content = data.get('content', '')
        context = data.get('context', 'generic')
        filename = data.get('filename', None)

        if content is None or not str(content).strip():
            return jsonify({
                'success': True,
                'valid': False,
                'error': {
                    'message': 'YAML cannot be empty',
                    'line': 1,
                    'column': 1,
                },
            })

        yaml_validator = YAML(typ='safe')

        try:
            yaml_validator.load(content)
        except Exception as e:
            mark = getattr(e, 'problem_mark', None) or getattr(e, 'context_mark', None)
            line = None
            column = None
            if mark is not None:
                try:
                    line = int(mark.line) + 1
                    column = int(mark.column) + 1
                except Exception:
                    line = None
                    column = None

            problem = getattr(e, 'problem', None)
            msg = str(problem) if problem else str(e)

            current_app.logger.info(
                f"YAML validate failed (context={context}, filename={filename}): {type(e).__name__}: {msg}"
            )

            return jsonify({
                'success': True,
                'valid': False,
                'error': {
                    'message': msg,
                    'line': line or 1,
                    'column': column or 1,
                },
            })

        return jsonify({'success': True, 'valid': True})

    except Exception as e:
        current_app.logger.error(f"Error in api_yaml_validate: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

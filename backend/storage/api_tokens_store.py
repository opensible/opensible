#!/usr/bin/env python3
"""
API Tokens store — long-lived tokens for programmatic API access.
Tokens are hashed; plaintext is returned only once at creation.
"""
import json
import uuid
import time
import logging
import hashlib
import secrets
from pathlib import Path
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)


def _get_data_dir():
    try:
        from .app import DATA_DIR
        return DATA_DIR
    except ImportError:
        BASE_DIR = Path(__file__).parent.parent.parent
        return BASE_DIR / 'data'


DATA_DIR = _get_data_dir()
API_TOKENS_DIR = DATA_DIR / 'api_tokens'
API_TOKENS_DIR.mkdir(parents=True, exist_ok=True)
TOKENS_INDEX_FILE = API_TOKENS_DIR / 'index.json'


def _hash_token(token: str, salt: str) -> str:
    return hashlib.sha256((token + salt).encode('utf-8')).hexdigest()


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _generate_salt() -> str:
    return secrets.token_urlsafe(16)


def _load_index() -> Dict:
    if not TOKENS_INDEX_FILE.exists():
        return {'tokens': [], 'by_id': {}}
    try:
        with open(TOKENS_INDEX_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading API tokens index: {e}")
        return {'tokens': [], 'by_id': {}}


def _save_index(data: Dict) -> bool:
    try:
        with open(TOKENS_INDEX_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Error saving API tokens index: {e}")
        return False


def list_tokens(user_id: str) -> List[Dict]:
    """List API tokens for a user. Never returns token value."""
    data = _load_index()
    result = []
    for t in data.get('tokens', []):
        if t.get('userId') != user_id:
            continue
        result.append({
            'id': t.get('id'),
            'name': t.get('name'),
            'scope': t.get('scope', 'global'),
            'projectId': t.get('projectId'),
            'createdAt': t.get('createdAt'),
            'lastUsedAt': t.get('lastUsedAt'),
            'expiresAt': t.get('expiresAt'),
            'status': 'revoked' if t.get('revoked') else 'active',
        })
    return result


def create_token(
    user_id: str,
    username: str,
    name: str,
    scope: str = 'global',
    project_id: Optional[str] = None,
    expires_days: Optional[int] = None,
) -> Tuple[str, str]:
    """
    Create API token. Returns (token_id, plaintext_token).
    Plaintext is returned only once!
    """
    token_id = str(uuid.uuid4())
    plaintext = _generate_token()
    salt = _generate_salt()
    token_hash = _hash_token(plaintext, salt)

    now = time.time()
    expires_at = None
    if expires_days and expires_days > 0:
        expires_at = now + (expires_days * 86400)

    entry = {
        'id': token_id,
        'name': name,
        'scope': scope,
        'projectId': project_id if scope == 'project' else None,
        'userId': user_id,
        'username': username,
        'tokenHash': token_hash,
        'tokenSalt': salt,
        'createdAt': now,
        'lastUsedAt': None,
        'expiresAt': expires_at,
        'revoked': False,
    }

    data = _load_index()
    data.setdefault('tokens', []).append(entry)
    data.setdefault('by_id', {})[token_id] = len(data['tokens']) - 1

    if _save_index(data):
        logger.info(f"Created API token {token_id} for user {user_id}")
        return token_id, plaintext
    raise Exception("Failed to save API token")


def verify_api_token(token: str) -> Optional[Tuple[str, Dict]]:
    """
    Verify API token. Returns (user_id, token_entry) if valid.
    Updates lastUsedAt.
    """
    data = _load_index()
    now = time.time()

    for t in data.get('tokens', []):
        if t.get('revoked'):
            continue
        exp = t.get('expiresAt')
        if exp and now > exp:
            continue
        if t.get('tokenHash') and t.get('tokenSalt'):
            h = _hash_token(token, t['tokenSalt'])
            if h == t['tokenHash']:
                t['lastUsedAt'] = now
                _save_index(data)
                return t.get('userId'), t
    return None


def revoke_token(token_id: str, user_id: str) -> bool:
    """Revoke token. User can only revoke own tokens."""
    data = _load_index()
    for t in data.get('tokens', []):
        if t.get('id') == token_id and t.get('userId') == user_id:
            t['revoked'] = True
            return _save_index(data)
    return False


def rotate_token(token_id: str, user_id: str) -> Optional[Tuple[str, str]]:
    """
    Regenerate token: create new token, revoke old. Returns (new_token_id, plaintext) or None.
    Plaintext returned only once!
    """
    data = _load_index()
    for t in data.get('tokens', []):
        if t.get('id') == token_id and t.get('userId') == user_id and not t.get('revoked'):
            t['revoked'] = True
            _save_index(data)
            exp = t.get('expiresAt')
            expires_days = int((exp - time.time()) / 86400) if exp and exp > time.time() else None
            new_id, plaintext = create_token(
                user_id=user_id,
                username=t.get('username', ''),
                name=t.get('name', ''),
                scope=t.get('scope', 'global'),
                project_id=t.get('projectId'),
                expires_days=expires_days,
            )
            return new_id, plaintext
    return None


def delete_token(token_id: str, user_id: str) -> bool:
    """Delete token. User can only delete own tokens."""
    data = _load_index()
    tokens = data.get('tokens', [])
    new_tokens = [t for t in tokens if not (t.get('id') == token_id and t.get('userId') == user_id)]
    if len(new_tokens) == len(tokens):
        return False
    data['tokens'] = new_tokens
    data.get('by_id', {}).pop(token_id, None)
    return _save_index(data)

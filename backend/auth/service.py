#!/usr/bin/env python3
"""
JWT authentication token utilities.

Security notes:
- JWT_SECRET_KEY MUST be set via environment variable in production. In a
  non-production environment a random ephemeral key is generated at process
  start (tokens are invalidated on restart) and a loud warning is logged.
- The token blacklist now stores each token with its *real* `exp` claim, so
  revoked tokens cannot become valid again after the blacklist purges them.
- `decode_token_unsafe()` (formerly `decode_token`) NEVER verifies the
  signature and MUST NOT be used for any authorization decision.
"""
import os
import secrets as _secrets
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from pathlib import Path
import json
import logging
import threading

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Secret key resolution (fail-hard in production)
# ---------------------------------------------------------------------------
_env_secret = os.environ.get('JWT_SECRET_KEY') or os.environ.get('JWT_SECRET')
_flask_env = (os.environ.get('FLASK_ENV') or '').lower()
_is_production = _flask_env == 'production'

if not _env_secret:
    if _is_production:
        raise RuntimeError(
            "JWT_SECRET_KEY environment variable must be set in production "
            "(legacy JWT_SECRET is also accepted). Generate one with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(64))\""
        )
    # Non-production: ephemeral key. Tokens will be invalidated on every restart.
    _env_secret = _secrets.token_urlsafe(64)
    logger.warning(
        "JWT_SECRET_KEY is not set. Using a random ephemeral key for this process. "
        "All JWTs will be invalidated when the backend restarts. "
        "Set JWT_SECRET_KEY in the environment for stable tokens."
    )
elif len(_env_secret) < 32:
    if _is_production:
        raise RuntimeError("JWT_SECRET_KEY must be at least 32 characters long.")
    logger.warning("JWT_SECRET_KEY is shorter than 32 characters — this is insecure.")

JWT_SECRET_KEY = _env_secret
JWT_ALGORITHM = 'HS256'
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get('JWT_ACCESS_TOKEN_EXPIRE_MINUTES', '1440'))
JWT_REFRESH_TOKEN_EXPIRE_DAYS = int(os.environ.get('JWT_REFRESH_TOKEN_EXPIRE_DAYS', '7'))

# ---------------------------------------------------------------------------
# Token blacklist (file-backed with in-process lock)
# ---------------------------------------------------------------------------
_blacklist_lock = threading.Lock()


def get_token_blacklist_path(data_dir: Path) -> Path:
    auth_dir = data_dir / 'auth'
    auth_dir.mkdir(exist_ok=True)
    return auth_dir / 'token_blacklist.json'


def load_token_blacklist(data_dir: Path) -> dict:
    """Load blacklist as {token: exp_ts}. Purges expired entries on read."""
    blacklist_file = get_token_blacklist_path(data_dir)
    if not blacklist_file.exists():
        return {}
    try:
        with open(blacklist_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            # Legacy format (list/set) — drop it; expired entries either way
            return {}
        now = datetime.utcnow().timestamp()
        active = {t: float(exp) for t, exp in data.items() if float(exp) > now}
        if len(active) != len(data):
            _write_blacklist(blacklist_file, active)
        return active
    except Exception as e:
        logger.error(f"Error loading token blacklist: {e}")
        return {}


def _write_blacklist(blacklist_file: Path, data: dict) -> None:
    tmp = blacklist_file.with_suffix(blacklist_file.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, blacklist_file)


def save_token_blacklist(data_dir: Path, blacklist: dict):
    """Persist {token: exp_ts} dict to disk."""
    blacklist_file = get_token_blacklist_path(data_dir)
    try:
        _write_blacklist(blacklist_file, blacklist)
    except Exception as e:
        logger.error(f"Error saving token blacklist: {e}")


def _extract_exp(token: str) -> float:
    """Best-effort extraction of the `exp` claim. Falls back to refresh expiry."""
    try:
        payload = jwt.decode(
            token, JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={'verify_exp': False},
        )
        exp = payload.get('exp')
        if exp:
            return float(exp)
    except Exception:
        pass
    return (datetime.utcnow() + timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)).timestamp()


def add_token_to_blacklist(data_dir: Path, token: str):
    """Add token to blacklist with its real exp. Thread-safe."""
    with _blacklist_lock:
        blacklist = load_token_blacklist(data_dir)
        blacklist[token] = _extract_exp(token)
        save_token_blacklist(data_dir, blacklist)


def is_token_blacklisted(data_dir: Path, token: str) -> bool:
    blacklist = load_token_blacklist(data_dir)
    return token in blacklist


# ---------------------------------------------------------------------------
# Token generation / verification
# ---------------------------------------------------------------------------
def generate_token(user_id: str, username: str, roles: list, data_dir: Path,
                   token_type: str = 'access', expires_delta: Optional[timedelta] = None) -> str:
    if expires_delta is None:
        if token_type == 'refresh':
            expires_delta = timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        else:
            expires_delta = timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)

    expire = datetime.utcnow() + expires_delta
    payload = {
        'user_id': user_id,
        'username': username,
        'roles': roles,
        'token_type': token_type,
        'exp': expire,
        'iat': datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_token(token: str, data_dir: Path, token_type: str = 'access') -> Optional[Dict[str, Any]]:
    """Verify signature, expiry, blacklist, and token_type."""
    try:
        if is_token_blacklisted(data_dir, token):
            logger.warning("Attempt to use blacklisted token")
            return None
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get('token_type') != token_type:
            logger.warning(f"Invalid token type. Expected {token_type}, got {payload.get('token_type')}")
            return None
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        return None
    except Exception as e:
        logger.error(f"Error verifying token: {e}")
        return None


def decode_token_unsafe(token: str) -> Optional[Dict[str, Any]]:
    """
    SECURITY: Decode a JWT WITHOUT verifying signature or expiry.

    Returns payload for display purposes only. NEVER use this for
    authorization, role checks, or any security decision.
    """
    try:
        # Pin algorithm even when skipping signature verification.
        return jwt.decode(
            token,
            options={'verify_signature': False, 'verify_exp': False},
            algorithms=[JWT_ALGORITHM],
        )
    except Exception as e:
        logger.warning(f"Error decoding token: {e}")
        return None


# Back-compat alias — DO NOT use for authorization decisions.
decode_token = decode_token_unsafe


def get_token_from_header(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header:
        return None
    try:
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            return None
        return parts[1]
    except Exception:
        return None

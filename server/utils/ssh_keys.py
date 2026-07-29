"""SSH private-key helpers.

Extracted from app.py during Phase 1 of the refactor. Pure functions,
no Flask / no module-level state — safe to import from anywhere.
"""
from __future__ import annotations

import re


def validate_ssh_private_key(private_key):
    """Validate an SSH private key string.

    Checks presence and consistency of BEGIN/END markers and that the
    body looks like base64. Returns (is_valid, error_message).
    """
    if not private_key or not isinstance(private_key, str):
        return False, "Private key must be a non-empty string"

    private_key = private_key.strip()

    if len(private_key) < 100:
        return False, "Private key is too short to be valid"

    begin_markers = [
        '-----BEGIN RSA PRIVATE KEY-----',
        '-----BEGIN OPENSSH PRIVATE KEY-----',
        '-----BEGIN EC PRIVATE KEY-----',
        '-----BEGIN DSA PRIVATE KEY-----',
        '-----BEGIN PRIVATE KEY-----',
    ]

    has_begin = any(marker in private_key for marker in begin_markers)
    if not has_begin:
        return False, "Private key must start with '-----BEGIN ... PRIVATE KEY-----'"

    end_markers = [
        '-----END RSA PRIVATE KEY-----',
        '-----END OPENSSH PRIVATE KEY-----',
        '-----END EC PRIVATE KEY-----',
        '-----END DSA PRIVATE KEY-----',
        '-----END PRIVATE KEY-----',
    ]

    has_end = any(marker in private_key for marker in end_markers)
    if not has_end:
        return False, "Private key must end with '-----END ... PRIVATE KEY-----'"

    begin_marker = None
    for marker in begin_markers:
        if marker in private_key:
            begin_marker = marker
            break

    if begin_marker:
        key_type = begin_marker.replace('-----BEGIN ', '').replace('-----', '')
        expected_end = f'-----END {key_type}-----'
        if expected_end not in private_key:
            return False, f"Private key END marker does not match BEGIN marker. Expected '{expected_end}'"

    begin_pos = private_key.find('-----BEGIN')
    end_pos = private_key.find('-----END')

    if begin_pos == -1 or end_pos == -1:
        return False, "Private key structure is invalid"

    if end_pos <= begin_pos:
        return False, "Private key END marker must come after BEGIN marker"

    content_start = private_key.find('-----', begin_pos + 1)
    if content_start == -1 or content_start >= end_pos:
        return False, "Private key must have content between BEGIN and END markers"

    content = private_key[content_start + 5:end_pos].strip()
    if not content:
        return False, "Private key content between markers is empty"

    base64_pattern = re.compile(r'^[A-Za-z0-9+/=\s]+$')
    if not base64_pattern.match(content):
        return False, "Private key content contains invalid characters (expected base64)"

    return True, None


def normalize_pem_key_for_file(content):
    """Normalize a PEM key body for writing to disk.

    Converts CRLF/CR line endings to LF and guarantees exactly one
    trailing newline. Prevents "error in libcrypto" from OpenSSH/libcrypto
    when the key was pasted from Windows.
    """
    if not content or not isinstance(content, str):
        return content or ''
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    content = content.rstrip('\n')
    if content and not content.endswith('\n'):
        content = content + '\n'
    return content


import hashlib as _hashlib
import logging as _logging
import os as _os
import shutil as _shutil
import uuid as _uuid

_logger = _logging.getLogger(__name__)


def copy_keys_to_generated_playbooks_dedup(temp_key_files, generated_playbooks_dir):
    """Copy SSH key files into the generated_playbooks dir with dedup by content.

    Files with identical SHA-256 content are stored once under a random
    ``key_<hex>.pem`` name (chmod 0600). Returns a dict mapping every input
    path to its final on-disk path.
    """
    key_path_map = {}
    content_hash_to_new_path = {}
    for old_key_path in temp_key_files:
        try:
            with open(old_key_path, 'rb') as f:
                content = f.read()
            content_hash = _hashlib.sha256(content).hexdigest()
            if content_hash in content_hash_to_new_path:
                key_path_map[old_key_path] = content_hash_to_new_path[content_hash]
            else:
                new_key_name = f'key_{_uuid.uuid4().hex[:8]}.pem'
                new_key_path = generated_playbooks_dir / new_key_name
                _shutil.copy2(old_key_path, new_key_path)
                _os.chmod(new_key_path, 0o600)
                new_path_str = str(new_key_path.resolve())
                content_hash_to_new_path[content_hash] = new_path_str
                key_path_map[old_key_path] = new_path_str
        except Exception as e:
            _logger.warning(f"Failed to copy key to generated_playbooks: {e}")
    return key_path_map

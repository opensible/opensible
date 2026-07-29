"""
Encryption utilities for the Global Secrets Manager.

Uses AES-GCM (authenticated encryption). The wrapping key is derived from a
server secret (GLOBAL_SECRETS_ENCRYPTION_KEY env var, or a file-stored key as
last resort).

Storage format:
  - New ("v2"): base64( b"v2" || salt(16) || nonce(12) || ciphertext+tag )
                Each secret uses its OWN random PBKDF2 salt.
  - Legacy   : base64( nonce(12) || ciphertext+tag )
                Uses the historical fixed salt. Read-only — kept so existing
                secrets remain decryptable. Re-encryption upgrades to v2.

In production, GLOBAL_SECRETS_ENCRYPTION_KEY must be set as a real secret
(Docker/K8s secret) and never stored on the data volume.
"""
import os
import base64
import secrets
from pathlib import Path
from typing import Optional, Tuple
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


_V2_PREFIX = b'v2'
_LEGACY_FIXED_SALT = b'global_secrets_salt'
_PBKDF2_ITERATIONS = 200_000  # bumped from 100k


def _derive_key(server_key: bytes, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
        backend=default_backend(),
    )
    return kdf.derive(server_key)


class SecretEncryption:
    """Encrypts/decrypts secret material with AES-GCM and a per-secret salt."""

    def __init__(self, server_key: Optional[str] = None):
        if server_key is None:
            server_key = os.environ.get('GLOBAL_SECRETS_ENCRYPTION_KEY')

        if not server_key:
            import warnings
            warnings.warn(
                "GLOBAL_SECRETS_ENCRYPTION_KEY not set. Using generated key "
                "(NOT SECURE for production).",
                UserWarning,
            )
            server_key = secrets.token_urlsafe(32)

        self._server_key_bytes = server_key.encode('utf-8')

        # Pre-compute the legacy key for backward-compatible decryption only.
        self._legacy_key = _derive_key(self._server_key_bytes, _LEGACY_FIXED_SALT)
        self._legacy_aesgcm = AESGCM(self._legacy_key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt with a fresh random 16-byte salt (v2 format)."""
        if not plaintext:
            return ""
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)
        key = _derive_key(self._server_key_bytes, salt)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode('utf-8'), None)
        blob = _V2_PREFIX + salt + nonce + ciphertext
        return base64.b64encode(blob).decode('utf-8')

    def decrypt(self, encrypted_data: str) -> str:
        if not encrypted_data:
            return ""
        try:
            data = base64.b64decode(encrypted_data.encode('utf-8'))
            if data.startswith(_V2_PREFIX):
                payload = data[len(_V2_PREFIX):]
                if len(payload) < 16 + 12 + 16:
                    raise ValueError("Encrypted data too short (v2)")
                salt = payload[:16]
                nonce = payload[16:28]
                ciphertext = payload[28:]
                key = _derive_key(self._server_key_bytes, salt)
                plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
                return plaintext.decode('utf-8')

            # Legacy path: fixed salt, no version prefix.
            if len(data) < 12 + 16:
                raise ValueError("Encrypted data too short (legacy)")
            nonce = data[:12]
            ciphertext = data[12:]
            plaintext = self._legacy_aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode('utf-8')
        except Exception as e:
            server_key = os.environ.get('GLOBAL_SECRETS_ENCRYPTION_KEY')
            if not server_key:
                raise ValueError(
                    "Decryption failed: GLOBAL_SECRETS_ENCRYPTION_KEY is not set. "
                    "Set it to the value used when the secret was created."
                ) from e
            raise ValueError(
                f"Decryption failed: key changed or data corrupted. "
                f"Original error: {e}"
            ) from e


# Global instance (initialized on first use)
_encryption_instance: Optional[SecretEncryption] = None


def get_encryption_key_file_path(data_dir: Optional[Path] = None) -> Path:
    if data_dir is None:
        data_dir = Path(os.environ.get('DATA_DIR', os.getcwd()))
    key_dir = Path(data_dir) / 'global' / 'secrets'
    key_dir.mkdir(parents=True, exist_ok=True)
    return key_dir / '.encryption_key'


def load_encryption_key(data_dir: Optional[Path] = None) -> Optional[str]:
    """Resolve key. Prefers env var; warns if falling back to on-disk file."""
    env_key = os.environ.get('GLOBAL_SECRETS_ENCRYPTION_KEY')
    if env_key:
        return env_key

    key_file = get_encryption_key_file_path(data_dir)
    if key_file.exists():
        try:
            with open(key_file, 'r', encoding='utf-8') as f:
                key = f.read().strip()
            if key:
                import warnings
                warnings.warn(
                    "Loaded encryption key from data volume "
                    f"({key_file}). For production, set "
                    "GLOBAL_SECRETS_ENCRYPTION_KEY in the environment "
                    "and remove the file.",
                    UserWarning,
                )
                return key
        except Exception:
            pass
    return None


def save_encryption_key(key: str, data_dir: Optional[Path] = None) -> bool:
    try:
        key_file = get_encryption_key_file_path(data_dir)
        with open(key_file, 'w', encoding='utf-8') as f:
            f.write(key)
        os.chmod(key_file, 0o600)
        return True
    except Exception:
        return False


def generate_and_save_encryption_key(data_dir: Optional[Path] = None) -> str:
    key = secrets.token_urlsafe(32)
    save_encryption_key(key, data_dir)
    return key


def get_encryption(data_dir: Optional[Path] = None) -> SecretEncryption:
    global _encryption_instance
    if _encryption_instance is None:
        server_key = load_encryption_key(data_dir)
        if not server_key:
            server_key = generate_and_save_encryption_key(data_dir)
            import warnings
            warnings.warn(
                "GLOBAL_SECRETS_ENCRYPTION_KEY not set and no saved key found. "
                "Generated and saved new encryption key. All existing secrets "
                "encrypted with a previous key will not be decryptable.",
                UserWarning,
            )
        _encryption_instance = SecretEncryption(server_key)
    return _encryption_instance

"""Unit tests for AES-GCM global secrets encryption."""
from __future__ import annotations

import base64

import pytest

from utils.secret_encryption import SecretEncryption, _V2_PREFIX, _derive_key, _LEGACY_FIXED_SALT
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def test_roundtrip_v2():
    enc = SecretEncryption("unit-test-server-key-for-secrets!!")
    plaintext = "super-secret-value"
    blob = enc.encrypt(plaintext)
    assert blob
    assert enc.decrypt(blob) == plaintext


def test_encrypt_empty_returns_empty():
    enc = SecretEncryption("unit-test-server-key-for-secrets!!")
    assert enc.encrypt("") == ""
    assert enc.decrypt("") == ""


def test_v2_uses_unique_ciphertext_per_call():
    enc = SecretEncryption("unit-test-server-key-for-secrets!!")
    a = enc.encrypt("same-value")
    b = enc.encrypt("same-value")
    assert a != b
    assert enc.decrypt(a) == "same-value"
    assert enc.decrypt(b) == "same-value"


def test_v2_blob_has_prefix():
    enc = SecretEncryption("unit-test-server-key-for-secrets!!")
    raw = base64.b64decode(enc.encrypt("hello").encode("utf-8"))
    assert raw.startswith(_V2_PREFIX)


def test_wrong_key_fails():
    a = SecretEncryption("unit-test-server-key-for-secrets!!")
    b = SecretEncryption("different-server-key-for-secrets!!!")
    blob = a.encrypt("payload")
    with pytest.raises(ValueError):
        b.decrypt(blob)


def test_legacy_format_still_decrypts():
    """Secrets written with the fixed-salt format must remain readable."""
    server_key = b"legacy-compat-key-for-unit-tests!!"
    key = _derive_key(server_key, _LEGACY_FIXED_SALT)
    nonce = b"\x01" * 12
    ciphertext = AESGCM(key).encrypt(nonce, b"legacy-secret", None)
    blob = base64.b64encode(nonce + ciphertext).decode("utf-8")

    enc = SecretEncryption(server_key.decode("utf-8"))
    assert enc.decrypt(blob) == "legacy-secret"

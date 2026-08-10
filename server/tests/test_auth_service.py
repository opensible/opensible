"""Unit tests for JWT issue / verify / blacklist."""
from __future__ import annotations

from datetime import timedelta

import jwt
import pytest

from auth.service import (
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    add_token_to_blacklist,
    generate_token,
    get_token_from_header,
    is_token_blacklisted,
    verify_token,
)


def test_generate_and_verify_access_token(data_dir):
    token = generate_token(
        user_id="u-1",
        username="alice",
        roles=["admin"],
        data_dir=data_dir,
        token_type="access",
    )
    payload = verify_token(token, data_dir, token_type="access")
    assert payload is not None
    assert payload["user_id"] == "u-1"
    assert payload["username"] == "alice"
    assert payload["roles"] == ["admin"]
    assert payload["token_type"] == "access"


def test_refresh_token_rejected_as_access(data_dir):
    token = generate_token(
        user_id="u-1",
        username="alice",
        roles=[],
        data_dir=data_dir,
        token_type="refresh",
    )
    assert verify_token(token, data_dir, token_type="access") is None
    assert verify_token(token, data_dir, token_type="refresh") is not None


def test_blacklisted_token_is_rejected(data_dir):
    token = generate_token(
        user_id="u-1",
        username="alice",
        roles=[],
        data_dir=data_dir,
    )
    add_token_to_blacklist(data_dir, token)
    assert is_token_blacklisted(data_dir, token) is True
    assert verify_token(token, data_dir) is None


def test_expired_token_is_rejected(data_dir):
    token = generate_token(
        user_id="u-1",
        username="alice",
        roles=[],
        data_dir=data_dir,
        expires_delta=timedelta(seconds=-1),
    )
    assert verify_token(token, data_dir) is None


def test_tampered_token_is_rejected(data_dir):
    token = generate_token(
        user_id="u-1",
        username="alice",
        roles=[],
        data_dir=data_dir,
    )
    # Flip a character in the signature segment.
    parts = token.split(".")
    sig = parts[2]
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    tampered = ".".join([parts[0], parts[1], flipped])
    assert verify_token(tampered, data_dir) is None


def test_get_token_from_header():
    assert get_token_from_header(None) is None
    assert get_token_from_header("Basic abc") is None
    assert get_token_from_header("Bearer") is None
    assert get_token_from_header("Bearer my-token") == "my-token"


def test_token_signed_with_wrong_secret_rejected(data_dir):
    bad = jwt.encode(
        {
            "user_id": "u-1",
            "username": "eve",
            "roles": ["admin"],
            "token_type": "access",
        },
        "not-the-real-secret-key-xxxxxxxxxxxx",
        algorithm=JWT_ALGORITHM,
    )
    assert JWT_SECRET_KEY != "not-the-real-secret-key-xxxxxxxxxxxx"
    assert verify_token(bad, data_dir) is None

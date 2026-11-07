"""Unit tests for the shared Git repository URL syntax check."""
from __future__ import annotations

import pytest

from services.git_source_manager import is_valid_git_url
from services.sources_service import validate_sources


@pytest.mark.parametrize("url, expected", [
    ("https://git.example.com/user/repo.git", True),
    ("http://git.example.com/user/repo.git", True),
    ("ssh://git@git.example.com:2222/user/repo.git", True),
    ("git://git.example.com/user/repo.git", True),
    ("git@git.example.com:user/repo.git", True),
    ("HTTPS://git.example.com/user/repo.git", True),
    ("file:///etc/passwd", False),
    ("https:///user/repo.git", False),
    ("/srv/other-project/.git", False),
    ("../repo", False),
    ("localhost:repo", False),
])
def test_is_valid_git_url(url, expected):
    assert is_valid_git_url(url) is expected


@pytest.mark.parametrize("url, expected", [
    ("ssh://git@git.example.com:2222/user/repo.git", True),
    ("file:///etc/passwd", False),
])
def test_validate_sources_uses_shared_url_check(url, expected):
    is_valid, _, _ = validate_sources({"repo": {"mode": "git", "git": {"repo": url}}})
    assert is_valid is expected

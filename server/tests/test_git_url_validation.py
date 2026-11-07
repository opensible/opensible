"""Unit tests for the shared Git repository URL syntax check."""
from __future__ import annotations

import pytest

from services.git_source_manager import GitSourceError, is_valid_git_url, validate_git_repo_url
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
    ("git@", False),
    ("git@host", False),
    ("git@/etc/passwd:x", False),
    ("git@[::1]:repo", False),
    ("git@x@127.0.0.1:repo", False),
    ("git@[git.example.com:2222]:user/repo.git", False),
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


@pytest.mark.parametrize("url", [
    "https://192.168.0.10/user/repo.git",
    "ssh://git@10.0.0.1:2222/user/repo.git",
    "git@172.16.0.1:user/repo.git",
    "ssh://git@[fd12::1]/user/repo.git",
])
def test_validate_git_repo_url_allows_private_networks(url):
    validate_git_repo_url(url)


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/user/repo.git",
    "http://169.254.169.254/latest/meta-data/",
    "ssh://git@[::1]/user/repo.git",
    "git@127.0.0.1:user/repo.git",
    "git@169.254.169.254:user/repo.git",
    "git@0.0.0.0:user/repo.git",
])
def test_validate_git_repo_url_blocks_loopback_and_link_local(url):
    with pytest.raises(GitSourceError):
        validate_git_repo_url(url)

#!/usr/bin/env python3
"""
Input validators for authentication.

Password policy (PCI-DSS aligned):
- Minimum 12 characters
- Must include at least 3 of: uppercase, lowercase, digit, special character
- Max 128 characters
"""
import re
from typing import Tuple, Optional


def validate_username(username: str) -> Tuple[bool, Optional[str]]:
    if not username:
        return False, "Username is required"
    if not isinstance(username, str):
        return False, "Username must be a string"
    username = username.strip()
    if len(username) < 3:
        return False, "Username must contain at least 3 characters"
    if len(username) > 50:
        return False, "Username must not exceed 50 characters"
    if not re.match(r'^[a-zA-Z0-9_-]+$', username):
        return False, "Username can only contain letters, numbers, underscores and hyphens"
    return True, None


def validate_password(password: str) -> Tuple[bool, Optional[str]]:
    if not password:
        return False, "Password is required"
    if not isinstance(password, str):
        return False, "Password must be a string"
    if len(password) < 12:
        return False, "Password must contain at least 12 characters"
    if len(password) > 128:
        return False, "Password must not exceed 128 characters"

    classes = 0
    if re.search(r'[a-z]', password):
        classes += 1
    if re.search(r'[A-Z]', password):
        classes += 1
    if re.search(r'\d', password):
        classes += 1
    if re.search(r'[^A-Za-z0-9]', password):
        classes += 1
    if classes < 3:
        return False, (
            "Password must include at least three of: uppercase, lowercase, "
            "digit, special character"
        )
    return True, None


def validate_email(email: Optional[str]) -> Tuple[bool, Optional[str]]:
    if email is None:
        return True, None
    if not isinstance(email, str):
        return False, "Email must be a string"
    email = email.strip()
    if not email:
        return True, None
    email_pattern = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    if not email_pattern.match(email):
        return False, "Invalid email address format"
    if len(email) > 255:
        return False, "Email must not exceed 255 characters"
    return True, None


def validate_role_name(name: str) -> Tuple[bool, Optional[str]]:
    if not name:
        return False, "Role name is required"
    if not isinstance(name, str):
        return False, "Role name must be a string"
    name = name.strip()
    if len(name) < 2:
        return False, "Role name must contain at least 2 characters"
    if len(name) > 100:
        return False, "Role name must not exceed 100 characters"
    if not re.match(r'^[a-zA-Z0-9_\s-]+$', name):
        return False, "Role name can only contain letters, numbers, spaces, underscores and hyphens"
    return True, None


def validate_permission_name(name: str) -> Tuple[bool, Optional[str]]:
    if not name:
        return False, "Permission name is required"
    if not isinstance(name, str):
        return False, "Permission name must be a string"
    name = name.strip()
    if len(name) < 3:
        return False, "Permission name must contain at least 3 characters"
    if len(name) > 100:
        return False, "Permission name must not exceed 100 characters"
    if not re.match(r'^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$', name):
        return False, "Permission name must be in format 'resource.action' (e.g., 'users.read')"
    return True, None

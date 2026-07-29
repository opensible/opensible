#!/usr/bin/env python3
"""
Admin password reset utility.

SECURITY: This script no longer hardcodes a password. You MUST pass the new
password as the first CLI argument; it is validated against the password
policy in auth_validators.validate_password.

Usage:
    python update_admin_password.py '<new-strong-password>'
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.user_service import UserService
from auth.validators import validate_password

_project_root = Path(__file__).parent.parent
DATA_DIR = Path(os.environ.get('DATA_DIR', str(_project_root / 'data')))

if len(sys.argv) < 2 or not sys.argv[1]:
    print("Usage: python update_admin_password.py '<new-strong-password>'")
    print("The password must satisfy the current password policy.")
    sys.exit(2)

new_password = sys.argv[1]
ok, err = validate_password(new_password)
if not ok:
    print(f"Password rejected: {err}")
    sys.exit(2)

user_service = UserService(DATA_DIR)
admin_user = user_service.get_user_by_username('admin')
if not admin_user:
    print("Admin user not found.")
    sys.exit(1)

try:
    success = user_service.set_password(admin_user.id, new_password)
    if success:
        print("Admin password updated successfully.")
    else:
        print("Failed to update admin password.")
        sys.exit(1)
except Exception as e:
    print(f"Error updating password: {e}")
    sys.exit(1)

#!/usr/bin/env python3
"""
Functions for seeding initial authentication data.

SECURITY: The first-run admin password is NEVER hardcoded.

Resolution order:
  1. ADMIN_INITIAL_PASSWORD environment variable (must satisfy policy)
  2. Randomly generated 24-character password — printed ONCE to stdout/log.
     The administrator must capture it from the boot log and change it on
     first login.
"""
import logging
import os
import secrets
import string
from pathlib import Path

logger = logging.getLogger(__name__)


def _generate_initial_password() -> str:
    """Generate a 24-char password satisfying the validator policy."""
    alphabet_lower = string.ascii_lowercase
    alphabet_upper = string.ascii_uppercase
    alphabet_digit = string.digits
    alphabet_symbol = "!@#$%^&*-_=+"
    # Guarantee one of each class
    required = [
        secrets.choice(alphabet_lower),
        secrets.choice(alphabet_upper),
        secrets.choice(alphabet_digit),
        secrets.choice(alphabet_symbol),
    ]
    pool = alphabet_lower + alphabet_upper + alphabet_digit + alphabet_symbol
    remaining = [secrets.choice(pool) for _ in range(20)]
    chars = required + remaining
    secrets.SystemRandom().shuffle(chars)
    return ''.join(chars)


def seed_default_user(user_service, role_service, data_dir: Path) -> bool:
    """Create the initial admin user on first run (when no users exist)."""
    try:
        users = user_service.get_all_users()
        if users:
            logger.info(
                f"Users already exist ({len(users)} users). "
                "Skipping default user creation."
            )
            return False

        initial_password = os.environ.get('ADMIN_INITIAL_PASSWORD')
        generated = False
        if not initial_password:
            initial_password = _generate_initial_password()
            generated = True

        admin_user = user_service.create_user(
            username='admin',
            password=initial_password,
            email=None,
            roles=[],
        )
        logger.info(f"Default admin user created: {admin_user.username} (ID: {admin_user.id})")

        if generated:
            banner = (
                "\n" + "=" * 72 + "\n"
                "  INITIAL ADMIN CREDENTIALS — RECORD AND CHANGE ON FIRST LOGIN\n"
                + "=" * 72 + "\n"
                f"  Username : admin\n"
                f"  Password : {initial_password}\n"
                + "=" * 72 + "\n"
                "  This password is shown ONCE and will not appear in logs again.\n"
                + "=" * 72 + "\n"
            )
            # Use both stdout and logger so it surfaces in containers.
            print(banner, flush=True)
            logger.warning("Initial admin password generated (see stdout banner).")
        else:
            logger.info("Initial admin password sourced from ADMIN_INITIAL_PASSWORD env var.")

        admin_role = role_service.get_role_by_name('admin')
        if admin_role:
            user_service.add_role_to_user(admin_user.id, admin_role.id)
            logger.info("Admin role assigned to default user")

        return True
    except ValueError as e:
        logger.info(f"Default admin user already exists: {e}")
        return False
    except Exception as e:
        logger.error(f"Error creating default user: {e}", exc_info=True)
        return False


def seed_default_roles(role_service, permission_service, data_dir: Path) -> bool:
    """Create base roles and permissions on first run."""
    created_any = False
    try:
        default_permissions = [
            {'name': 'users.read', 'description': 'View users', 'resource': 'users', 'action': 'read'},
            {'name': 'users.create', 'description': 'Create users', 'resource': 'users', 'action': 'create'},
            {'name': 'users.update', 'description': 'Update users', 'resource': 'users', 'action': 'update'},
            {'name': 'users.delete', 'description': 'Delete users', 'resource': 'users', 'action': 'delete'},
            {'name': 'roles.read', 'description': 'View roles', 'resource': 'roles', 'action': 'read'},
            {'name': 'roles.create', 'description': 'Create roles', 'resource': 'roles', 'action': 'create'},
            {'name': 'roles.update', 'description': 'Update roles', 'resource': 'roles', 'action': 'update'},
            {'name': 'roles.delete', 'description': 'Delete roles', 'resource': 'roles', 'action': 'delete'},
            {'name': 'permissions.read', 'description': 'View permissions', 'resource': 'permissions', 'action': 'read'},
            {'name': 'permissions.create', 'description': 'Create permissions', 'resource': 'permissions', 'action': 'create'},
            {'name': 'permissions.update', 'description': 'Update permissions', 'resource': 'permissions', 'action': 'update'},
            {'name': 'permissions.delete', 'description': 'Delete permissions', 'resource': 'permissions', 'action': 'delete'},
            {'name': 'projects.read', 'description': 'View projects', 'resource': 'projects', 'action': 'read'},
            {'name': 'projects.create', 'description': 'Create projects', 'resource': 'projects', 'action': 'create'},
            {'name': 'projects.update', 'description': 'Update projects', 'resource': 'projects', 'action': 'update'},
            {'name': 'projects.delete', 'description': 'Delete projects', 'resource': 'projects', 'action': 'delete'},
            {'name': 'playbooks.read', 'description': 'View playbooks', 'resource': 'playbooks', 'action': 'read'},
            {'name': 'playbooks.create', 'description': 'Create playbooks', 'resource': 'playbooks', 'action': 'create'},
            {'name': 'playbooks.update', 'description': 'Update playbooks', 'resource': 'playbooks', 'action': 'update'},
            {'name': 'playbooks.delete', 'description': 'Delete playbooks', 'resource': 'playbooks', 'action': 'delete'},
            {'name': 'playbooks.execute', 'description': 'Execute playbooks', 'resource': 'playbooks', 'action': 'execute'},
            {'name': 'inventory.read', 'description': 'View inventory', 'resource': 'inventory', 'action': 'read'},
            {'name': 'inventory.create', 'description': 'Create inventory', 'resource': 'inventory', 'action': 'create'},
            {'name': 'inventory.update', 'description': 'Update inventory', 'resource': 'inventory', 'action': 'update'},
            {'name': 'inventory.delete', 'description': 'Delete inventory', 'resource': 'inventory', 'action': 'delete'},
            {'name': 'secrets.read', 'description': 'View secrets', 'resource': 'secrets', 'action': 'read'},
            {'name': 'secrets.create', 'description': 'Create secrets', 'resource': 'secrets', 'action': 'create'},
            {'name': 'secrets.update', 'description': 'Update secrets', 'resource': 'secrets', 'action': 'update'},
            {'name': 'secrets.delete', 'description': 'Delete secrets', 'resource': 'secrets', 'action': 'delete'},
            {'name': 'settings.read', 'description': 'View settings', 'resource': 'settings', 'action': 'read'},
            {'name': 'settings.update', 'description': 'Update settings', 'resource': 'settings', 'action': 'update'},
            {'name': 'workers.read', 'description': 'View workers', 'resource': 'workers', 'action': 'read'},
            {'name': 'workers.create', 'description': 'Register workers', 'resource': 'workers', 'action': 'create'},
            {'name': 'workers.delete', 'description': 'Delete workers', 'resource': 'workers', 'action': 'delete'},
        ]

        permission_ids = {}
        for perm_data in default_permissions:
            try:
                existing_perm = permission_service.get_permission_by_name(perm_data['name'])
                if existing_perm:
                    permission_ids[perm_data['name']] = existing_perm.id
                    continue
                perm = permission_service.create_permission(
                    name=perm_data['name'],
                    description=perm_data['description'],
                    resource=perm_data['resource'],
                    action=perm_data['action'],
                )
                permission_ids[perm_data['name']] = perm.id
                created_any = True
                logger.info(f"Created permission: {perm.name}")
            except ValueError as e:
                existing_perm = permission_service.get_permission_by_name(perm_data['name'])
                if existing_perm:
                    permission_ids[perm_data['name']] = existing_perm.id
                logger.debug(f"Permission {perm_data['name']} already exists: {e}")
            except Exception as e:
                logger.error(f"Error creating permission {perm_data['name']}: {e}", exc_info=True)

        default_roles = [
            {
                'name': 'admin',
                'description': 'Administrator - full access to all functions',
                'permissions': list(permission_ids.values()),
            },
            {
                'name': 'user',
                'description': 'Regular user - basic access',
                'permissions': [
                    permission_ids.get('projects.read'),
                    permission_ids.get('playbooks.read'),
                    permission_ids.get('inventory.read'),
                    permission_ids.get('settings.read'),
                ],
            },
            {
                'name': 'operator',
                'description': 'Operator - can execute playbooks',
                'permissions': [
                    permission_ids.get('projects.read'),
                    permission_ids.get('playbooks.read'),
                    permission_ids.get('playbooks.execute'),
                    permission_ids.get('inventory.read'),
                ],
            },
            {
                'name': 'viewer',
                'description': 'Viewer - read-only access',
                'permissions': [
                    permission_ids.get('projects.read'),
                    permission_ids.get('playbooks.read'),
                    permission_ids.get('inventory.read'),
                    permission_ids.get('settings.read'),
                ],
            },
        ]

        for role_data in default_roles:
            role_data['permissions'] = [p for p in role_data['permissions'] if p is not None]

        for role_data in default_roles:
            try:
                existing_role = role_service.get_role_by_name(role_data['name'])
                if existing_role:
                    logger.debug(f"Role {role_data['name']} already exists")
                    continue
                role = role_service.create_role(
                    name=role_data['name'],
                    description=role_data['description'],
                    permissions=role_data['permissions'],
                )
                created_any = True
                logger.info(f"Created role: {role.name} with {len(role.permissions)} permissions")
            except ValueError as e:
                logger.debug(f"Role {role_data['name']} already exists: {e}")
            except Exception as e:
                logger.error(f"Error creating role {role_data['name']}: {e}", exc_info=True)

        return created_any
    except Exception as e:
        logger.error(f"Error seeding default roles: {e}", exc_info=True)
        return False

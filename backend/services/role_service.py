#!/usr/bin/env python3
"""Role & Permission services backed by libSQL/SQLite (WAL).

Public API is unchanged from the previous JSON-backed implementation so
callers (routes, seed, access control) keep working. Data is persisted in
``storage/auth_db`` (``roles``, ``role_permissions``, ``permissions``).
Every mutation is recorded in ``audit_log``.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

try:
    from auth.models import Permission, Role
except ImportError:
    from auth.models import Permission, Role

try:
    from storage import auth_db
except ImportError:  # pragma: no cover
    from backend.storage import auth_db  # type: ignore

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.utcnow().isoformat()


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

class RoleService:
    """RBAC role management (SQL-backed)."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        (self.data_dir / "auth").mkdir(parents=True, exist_ok=True)
        auth_db.get_conn(self.data_dir)
        auth_db.migrate_from_json_if_needed(self.data_dir)

    def _conn(self):
        return auth_db.get_conn(self.data_dir)

    def _row_to_role(self, row) -> Role:
        perm_rows = self._conn().execute(
            "SELECT permission_id FROM role_permissions WHERE role_id = ?", (row["id"],)
        ).fetchall()
        return Role(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            permissions=[r["permission_id"] for r in perm_rows],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_role(
        self, name: str, description: str, permissions: Optional[List[str]] = None,
    ) -> Role:
        conn = self._conn()
        if conn.execute("SELECT 1 FROM roles WHERE name = ?", (name,)).fetchone():
            raise ValueError(f"Role named '{name}' already exists")

        role_id = str(uuid.uuid4())
        now = _now()
        conn.execute("BEGIN")
        try:
            conn.execute(
                "INSERT INTO roles(id,name,description,is_system,created_at,updated_at) "
                "VALUES(?,?,?,0,?,?)",
                (role_id, name, description or "", now, now),
            )
            for pid in permissions or []:
                conn.execute(
                    "INSERT OR IGNORE INTO role_permissions(role_id,permission_id) VALUES(?,?)",
                    (role_id, pid),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        auth_db.audit(
            self.data_dir, "role.create", target_type="role", target_id=role_id,
            meta={"name": name, "permissions": permissions or []},
        )
        logger.info(f"Created role: {name} (ID: {role_id})")
        return self.get_role_by_id(role_id)  # type: ignore[return-value]

    def get_role_by_id(self, role_id: str) -> Optional[Role]:
        row = self._conn().execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
        return self._row_to_role(row) if row else None

    def get_role_by_name(self, name: str) -> Optional[Role]:
        row = self._conn().execute("SELECT * FROM roles WHERE name = ?", (name,)).fetchone()
        return self._row_to_role(row) if row else None

    def get_all_roles(self) -> List[Role]:
        rows = self._conn().execute("SELECT * FROM roles ORDER BY name").fetchall()
        return [self._row_to_role(r) for r in rows]

    def update_role(
        self,
        role_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        permissions: Optional[List[str]] = None,
    ) -> Optional[Role]:
        conn = self._conn()
        row = conn.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
        if not row:
            return None

        if name and name != row["name"]:
            clash = conn.execute(
                "SELECT 1 FROM roles WHERE name = ? AND id != ?", (name, role_id),
            ).fetchone()
            if clash:
                raise ValueError(f"Role named '{name}' already exists")

        now = _now()
        conn.execute("BEGIN")
        try:
            fields = []
            args: List = []
            if name is not None:
                fields.append("name = ?"); args.append(name)
            if description is not None:
                fields.append("description = ?"); args.append(description)
            fields.append("updated_at = ?"); args.append(now)
            args.append(role_id)
            conn.execute(f"UPDATE roles SET {', '.join(fields)} WHERE id = ?", args)

            if permissions is not None:
                conn.execute("DELETE FROM role_permissions WHERE role_id = ?", (role_id,))
                for pid in permissions:
                    conn.execute(
                        "INSERT OR IGNORE INTO role_permissions(role_id,permission_id) VALUES(?,?)",
                        (role_id, pid),
                    )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        auth_db.audit(
            self.data_dir, "role.update", target_type="role", target_id=role_id,
            meta={"name": name, "description": description, "permissions": permissions},
        )
        return self.get_role_by_id(role_id)

    def delete_role(self, role_id: str) -> bool:
        conn = self._conn()
        row = conn.execute("SELECT name FROM roles WHERE id = ?", (role_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM roles WHERE id = ?", (role_id,))
        auth_db.audit(
            self.data_dir, "role.delete", target_type="role", target_id=role_id,
            meta={"name": row["name"]},
        )
        return True

    def add_permission_to_role(self, role_id: str, permission_id: str) -> bool:
        conn = self._conn()
        if not conn.execute("SELECT 1 FROM roles WHERE id = ?", (role_id,)).fetchone():
            return False
        conn.execute(
            "INSERT OR IGNORE INTO role_permissions(role_id,permission_id) VALUES(?,?)",
            (role_id, permission_id),
        )
        conn.execute("UPDATE roles SET updated_at = ? WHERE id = ?", (_now(), role_id))
        auth_db.audit(
            self.data_dir, "role.permission_add", target_type="role", target_id=role_id,
            meta={"permission_id": permission_id},
        )
        return True

    def remove_permission_from_role(self, role_id: str, permission_id: str) -> bool:
        conn = self._conn()
        if not conn.execute("SELECT 1 FROM roles WHERE id = ?", (role_id,)).fetchone():
            return False
        conn.execute(
            "DELETE FROM role_permissions WHERE role_id = ? AND permission_id = ?",
            (role_id, permission_id),
        )
        conn.execute("UPDATE roles SET updated_at = ? WHERE id = ?", (_now(), role_id))
        auth_db.audit(
            self.data_dir, "role.permission_remove", target_type="role", target_id=role_id,
            meta={"permission_id": permission_id},
        )
        return True


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

class PermissionService:
    """RBAC permission catalog (SQL-backed)."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        (self.data_dir / "auth").mkdir(parents=True, exist_ok=True)
        auth_db.get_conn(self.data_dir)
        auth_db.migrate_from_json_if_needed(self.data_dir)

    def _conn(self):
        return auth_db.get_conn(self.data_dir)

    def _row_to_permission(self, row) -> Permission:
        return Permission(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            resource=row["resource"] or "",
            action=row["action"] or "",
            created_at=row["created_at"],
        )

    def create_permission(
        self, name: str, description: str, resource: str, action: str,
    ) -> Permission:
        conn = self._conn()
        if conn.execute("SELECT 1 FROM permissions WHERE name = ?", (name,)).fetchone():
            raise ValueError(f"Permission named '{name}' already exists")

        perm_id = str(uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO permissions(id,name,description,resource,action,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (perm_id, name, description or "", resource, action, now),
        )
        auth_db.audit(
            self.data_dir, "permission.create", target_type="permission", target_id=perm_id,
            meta={"name": name, "resource": resource, "action": action},
        )
        return self.get_permission_by_id(perm_id)  # type: ignore[return-value]

    def get_permission_by_id(self, perm_id: str) -> Optional[Permission]:
        row = self._conn().execute(
            "SELECT * FROM permissions WHERE id = ?", (perm_id,)
        ).fetchone()
        return self._row_to_permission(row) if row else None

    def get_permission_by_name(self, name: str) -> Optional[Permission]:
        row = self._conn().execute(
            "SELECT * FROM permissions WHERE name = ?", (name,)
        ).fetchone()
        return self._row_to_permission(row) if row else None

    def get_all_permissions(self) -> List[Permission]:
        rows = self._conn().execute("SELECT * FROM permissions ORDER BY name").fetchall()
        return [self._row_to_permission(r) for r in rows]

    def get_permissions_by_resource(self, resource: str) -> List[Permission]:
        rows = self._conn().execute(
            "SELECT * FROM permissions WHERE resource = ? ORDER BY action", (resource,),
        ).fetchall()
        return [self._row_to_permission(r) for r in rows]

    def update_permission(
        self,
        perm_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        resource: Optional[str] = None,
        action: Optional[str] = None,
    ) -> Optional[Permission]:
        conn = self._conn()
        row = conn.execute("SELECT * FROM permissions WHERE id = ?", (perm_id,)).fetchone()
        if not row:
            return None

        if name and name != row["name"]:
            clash = conn.execute(
                "SELECT 1 FROM permissions WHERE name = ? AND id != ?", (name, perm_id),
            ).fetchone()
            if clash:
                raise ValueError(f"Permission named '{name}' already exists")

        fields = []
        args: List = []
        if name is not None:
            fields.append("name = ?"); args.append(name)
        if description is not None:
            fields.append("description = ?"); args.append(description)
        if resource is not None:
            fields.append("resource = ?"); args.append(resource)
        if action is not None:
            fields.append("action = ?"); args.append(action)
        if fields:
            args.append(perm_id)
            conn.execute(f"UPDATE permissions SET {', '.join(fields)} WHERE id = ?", args)

        auth_db.audit(
            self.data_dir, "permission.update", target_type="permission", target_id=perm_id,
            meta={"name": name, "resource": resource, "action": action},
        )
        return self.get_permission_by_id(perm_id)

    def delete_permission(self, perm_id: str) -> bool:
        conn = self._conn()
        row = conn.execute("SELECT name FROM permissions WHERE id = ?", (perm_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM permissions WHERE id = ?", (perm_id,))
        auth_db.audit(
            self.data_dir, "permission.delete", target_type="permission", target_id=perm_id,
            meta={"name": row["name"]},
        )
        return True

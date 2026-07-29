#!/usr/bin/env python3
"""User storage and password authentication helpers (libSQL/SQLite backend).

Backed by ``storage.auth_db`` (WAL-mode SQLite) instead of ``users.json``.
Public method signatures and return types are unchanged so callers (routes,
seed script, permission_service, tests) continue to work without edits.

On first initialization, any legacy ``users.json`` / ``roles.json`` /
``permissions.json`` is imported into the DB and renamed to
``*.json.migrated-<epoch>`` (kept as a backup, never deleted).

Every mutating call also writes a row to the ``audit_log`` table.
"""
from __future__ import annotations

import bcrypt
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

try:
    from auth.models import User
except ImportError:
    from auth.models import User

try:
    from storage import auth_db
except ImportError:  # pragma: no cover
    from backend.storage import auth_db  # type: ignore

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.utcnow().isoformat()


class UserService:
    """Manage users in the auth SQLite database (WAL)."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        (self.data_dir / "auth").mkdir(parents=True, exist_ok=True)
        # Open the DB and run one-shot JSON import on first construction.
        auth_db.get_conn(self.data_dir)
        auth_db.migrate_from_json_if_needed(self.data_dir)

    # ------------------------------------------------------------------ helpers
    def _conn(self):
        return auth_db.get_conn(self.data_dir)

    def _hash_password(self, password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def _verify_password(self, password: str, password_hash: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except Exception as e:
            logger.error(f"Error verifying password: {e}")
            return False

    def _row_to_user(self, row) -> User:
        role_rows = self._conn().execute(
            "SELECT role_id FROM user_roles WHERE user_id = ?", (row["id"],)
        ).fetchall()
        return User(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            password_hash=row["password_hash"] or "",
            roles=[r["role_id"] for r in role_rows],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_login=row["last_login"],
        )

    # ------------------------------------------------------------------ CRUD
    def create_user(
        self,
        username: str,
        password: str,
        email: Optional[str] = None,
        roles: Optional[List[str]] = None,
    ) -> User:
        conn = self._conn()
        row = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        if row:
            raise ValueError(f"Username '{username}' already exists")

        user_id = str(uuid.uuid4())
        password_hash = self._hash_password(password)
        now = _now()

        conn.execute("BEGIN")
        try:
            conn.execute(
                "INSERT INTO users(id,username,email,password_hash,is_active,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (user_id, username, email, password_hash, 1, now, now),
            )
            for role_id in roles or []:
                conn.execute(
                    "INSERT OR IGNORE INTO user_roles(user_id,role_id,assigned_at) VALUES(?,?,?)",
                    (user_id, role_id, now),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        auth_db.audit(
            self.data_dir, "user.create", target_type="user", target_id=user_id,
            meta={"username": username, "email": email, "roles": roles or []},
        )
        logger.info(f"Created user: {username} (ID: {user_id})")
        return self.get_user_by_id(user_id)  # type: ignore[return-value]

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        row = self._conn().execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[User]:
        row = self._conn().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def get_all_users(self) -> List[User]:
        rows = self._conn().execute("SELECT * FROM users ORDER BY username").fetchall()
        return [self._row_to_user(r) for r in rows]

    def update_user(
        self,
        user_id: str,
        username: Optional[str] = None,
        email: Optional[str] = None,
        roles: Optional[List[str]] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[User]:
        conn = self._conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None

        if username and username != row["username"]:
            clash = conn.execute(
                "SELECT 1 FROM users WHERE username = ? AND id != ?", (username, user_id),
            ).fetchone()
            if clash:
                raise ValueError(f"Username '{username}' already exists")

        now = _now()
        conn.execute("BEGIN")
        try:
            fields = []
            args: List = []
            if username is not None:
                fields.append("username = ?"); args.append(username)
            if email is not None:
                fields.append("email = ?"); args.append(email)
            if is_active is not None:
                fields.append("is_active = ?"); args.append(1 if is_active else 0)
                if not is_active:
                    fields.append("disabled_at = ?"); args.append(now)
                else:
                    fields.append("disabled_at = NULL")
            fields.append("updated_at = ?"); args.append(now)
            args.append(user_id)
            conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", args)

            if roles is not None:
                conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
                for role_id in roles:
                    conn.execute(
                        "INSERT OR IGNORE INTO user_roles(user_id,role_id,assigned_at) "
                        "VALUES(?,?,?)",
                        (user_id, role_id, now),
                    )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        auth_db.audit(
            self.data_dir, "user.update", target_type="user", target_id=user_id,
            meta={"username": username, "email": email, "roles": roles, "is_active": is_active},
        )
        return self.get_user_by_id(user_id)

    def change_password(self, user_id: str, old_password: str, new_password: str) -> bool:
        row = self._conn().execute(
            "SELECT password_hash FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            return False
        if not self._verify_password(old_password, row["password_hash"] or ""):
            return False
        self._conn().execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (self._hash_password(new_password), _now(), user_id),
        )
        auth_db.audit(self.data_dir, "user.password_change", target_type="user", target_id=user_id)
        return True

    def set_password(self, user_id: str, new_password: str) -> bool:
        row = self._conn().execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return False
        self._conn().execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (self._hash_password(new_password), _now(), user_id),
        )
        auth_db.audit(self.data_dir, "user.password_set", target_type="user", target_id=user_id)
        return True

    def delete_user(self, user_id: str) -> bool:
        conn = self._conn()
        row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        auth_db.audit(
            self.data_dir, "user.delete", target_type="user", target_id=user_id,
            meta={"username": row["username"]},
        )
        logger.info(f"Deleted user: {row['username']} (ID: {user_id})")
        return True

    def authenticate(self, username: str, password: str) -> Optional[User]:
        user = self.get_user_by_username(username)
        if not user:
            return None
        if not user.is_active:
            logger.warning(f"Attempt to login inactive user: {username}")
            auth_db.audit(
                self.data_dir, "user.login_denied_inactive",
                target_type="user", target_id=user.id, meta={"username": username},
            )
            return None
        if not self._verify_password(password, user.password_hash):
            auth_db.audit(
                self.data_dir, "user.login_failed",
                target_type="user", target_id=user.id, meta={"username": username},
            )
            return None
        now = _now()
        self._conn().execute(
            "UPDATE users SET last_login = ? WHERE id = ?", (now, user.id),
        )
        user.last_login = now
        auth_db.audit(
            self.data_dir, "user.login", target_type="user", target_id=user.id,
            actor_user_id=user.id, meta={"username": username},
        )
        return user

    def add_role_to_user(self, user_id: str, role_id: str) -> bool:
        conn = self._conn()
        if not conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            return False
        conn.execute(
            "INSERT OR IGNORE INTO user_roles(user_id,role_id,assigned_at) VALUES(?,?,?)",
            (user_id, role_id, _now()),
        )
        conn.execute("UPDATE users SET updated_at = ? WHERE id = ?", (_now(), user_id))
        auth_db.audit(
            self.data_dir, "user.role_add", target_type="user", target_id=user_id,
            meta={"role_id": role_id},
        )
        return True

    def remove_role_from_user(self, user_id: str, role_id: str) -> bool:
        conn = self._conn()
        if not conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            return False
        conn.execute(
            "DELETE FROM user_roles WHERE user_id = ? AND role_id = ?", (user_id, role_id),
        )
        conn.execute("UPDATE users SET updated_at = ? WHERE id = ?", (_now(), user_id))
        auth_db.audit(
            self.data_dir, "user.role_remove", target_type="user", target_id=user_id,
            meta={"role_id": role_id},
        )
        return True

"""
libSQL/SQLite-backed authentication store (WAL mode).

Owns tables for the Users Management subsystem:

    users, roles, permissions,
    user_roles, role_permissions,
    api_tokens, sessions,
    audit_log

Design goals:
  * Standard RBAC schema with foreign keys + unique constraints.
  * Single writer connection, autocommit + explicit transactions.
  * WAL journal so readers never block writers.
  * One-shot import from the legacy JSON files (users.json / roles.json /
    permissions.json). After a successful import the JSON files are renamed
    to ``<name>.json.migrated-<epoch>`` so nothing is deleted.
  * Central audit log helper used by every mutating service call.

Public helpers exported for the service layer:

  - ``get_conn(data_dir)`` : return the shared sqlite3.Connection.
  - ``audit(...)``         : append a row to ``audit_log``.
  - ``migrate_from_json_if_needed(data_dir)`` : one-shot legacy import.
  - ``now_iso()``          : ISO-8601 UTC timestamp helper.

The connection is process-wide (module-level singleton) and thread-safe:
sqlite3 with ``check_same_thread=False`` + a ``busy_timeout`` + WAL is the
same pattern used by ``storage/index_db.py``.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_TLS = threading.local()
_DB_PATH: Optional[Path] = None
_SCHEMA_READY = False
_MIGRATED = False


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def _open(data_dir: Path) -> sqlite3.Connection:
    """Return a per-thread SQLite connection.

    SQLite connections are not safe to share concurrently across threads even
    with ``check_same_thread=False`` — concurrent ``execute()`` calls on the
    same connection raise ``SQLITE_MISUSE`` ("bad parameter or other API
    misuse"). Flask's dev server is threaded, so we hand each thread its own
    connection to the same on-disk WAL database, which is the supported
    pattern.
    """
    global _DB_PATH, _SCHEMA_READY

    conn = getattr(_TLS, "conn", None)
    if conn is not None:
        return conn

    with _LOCK:
        if _DB_PATH is None:
            auth_dir = Path(data_dir) / "auth"
            auth_dir.mkdir(parents=True, exist_ok=True)
            _DB_PATH = auth_dir / "auth.db"

    conn = sqlite3.connect(
        str(_DB_PATH),
        check_same_thread=False,
        isolation_level=None,  # autocommit; wrap multi-stmt writes in BEGIN
        timeout=10.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA temp_store=MEMORY")

    with _LOCK:
        if not _SCHEMA_READY:
            _create_schema(conn)
            _SCHEMA_READY = True
            logger.info("auth_db ready at %s (WAL)", _DB_PATH)

    _TLS.conn = conn
    return conn


def get_conn(data_dir: Path) -> sqlite3.Connection:
    return _open(data_dir)


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            username      TEXT NOT NULL UNIQUE,
            email         TEXT,
            password_hash TEXT NOT NULL,
            is_active     INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            last_login    TEXT,
            mfa_secret    TEXT,
            disabled_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
        CREATE INDEX IF NOT EXISTS idx_users_active   ON users(is_active);

        CREATE TABLE IF NOT EXISTS roles (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            is_system   INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS permissions (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            resource    TEXT NOT NULL,
            action      TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_permissions_resource ON permissions(resource);

        CREATE TABLE IF NOT EXISTS user_roles (
            user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role_id     TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
            assigned_at TEXT NOT NULL,
            PRIMARY KEY (user_id, role_id)
        );
        CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_roles(user_id);
        CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role_id);

        CREATE TABLE IF NOT EXISTS role_permissions (
            role_id       TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
            permission_id TEXT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
            PRIMARY KEY (role_id, permission_id)
        );
        CREATE INDEX IF NOT EXISTS idx_role_permissions_role ON role_permissions(role_id);

        CREATE TABLE IF NOT EXISTS api_tokens (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name         TEXT NOT NULL,
            token_hash   TEXT NOT NULL UNIQUE,
            scopes       TEXT,
            created_at   TEXT NOT NULL,
            expires_at   TEXT,
            last_used_at TEXT,
            revoked_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id);

        CREATE TABLE IF NOT EXISTS sessions (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            refresh_hash TEXT NOT NULL UNIQUE,
            ip           TEXT,
            user_agent   TEXT,
            created_at   TEXT NOT NULL,
            expires_at   TEXT NOT NULL,
            revoked_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

        CREATE TABLE IF NOT EXISTS audit_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id TEXT,
            action        TEXT NOT NULL,
            target_type   TEXT,
            target_id     TEXT,
            meta_json     TEXT,
            created_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
        CREATE INDEX IF NOT EXISTS idx_audit_target  ON audit_log(target_type, target_id);
        """
    )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def audit(
    data_dir: Path,
    action: str,
    *,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        conn = _open(data_dir)
        conn.execute(
            "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, meta_json, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                actor_user_id,
                action,
                target_type,
                target_id,
                json.dumps(meta, ensure_ascii=False) if meta else None,
                now_iso(),
            ),
        )
    except Exception as e:  # audit must never break a request
        logger.warning("audit insert failed (%s): %s", action, e)


def list_audit(
    data_dir: Path,
    *,
    limit: int = 100,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn = _open(data_dir)
    sql = "SELECT id, actor_user_id, action, target_type, target_id, meta_json, created_at FROM audit_log"
    where: List[str] = []
    args: List[Any] = []
    if target_type:
        where.append("target_type = ?")
        args.append(target_type)
    if target_id:
        where.append("target_id = ?")
        args.append(target_id)
    if actor_user_id:
        where.append("actor_user_id = ?")
        args.append(actor_user_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    rows = conn.execute(sql, args).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        meta = None
        if r["meta_json"]:
            try:
                meta = json.loads(r["meta_json"])
            except Exception:
                meta = None
        out.append(
            {
                "id": r["id"],
                "actor_user_id": r["actor_user_id"],
                "action": r["action"],
                "target_type": r["target_type"],
                "target_id": r["target_id"],
                "meta": meta,
                "created_at": r["created_at"],
            }
        )
    return out


# ---------------------------------------------------------------------------
# One-shot JSON -> SQL migration (users.json / roles.json / permissions.json)
# ---------------------------------------------------------------------------

def migrate_from_json_if_needed(data_dir: Path) -> None:
    """Import legacy JSON files into the DB when the tables are empty.

    The JSON files are renamed to ``<name>.json.migrated-<epoch>`` after a
    successful import so they act as an on-disk backup but are no longer
    read by the application.
    """
    global _MIGRATED
    if _MIGRATED:
        return
    conn = _open(data_dir)
    auth_dir = Path(data_dir) / "auth"
    users_file = auth_dir / "users.json"
    roles_file = auth_dir / "roles.json"
    perms_file = auth_dir / "permissions.json"

    try:
        counts = {
            "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "roles": conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0],
            "permissions": conn.execute("SELECT COUNT(*) FROM permissions").fetchone()[0],
        }

        conn.execute("BEGIN")
        try:
            if counts["permissions"] == 0 and perms_file.exists():
                _import_permissions(conn, perms_file)
            if counts["roles"] == 0 and roles_file.exists():
                _import_roles(conn, roles_file)
            if counts["users"] == 0 and users_file.exists():
                _import_users(conn, users_file)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        ts = int(time.time())
        for f in (perms_file, roles_file, users_file):
            if f.exists():
                target = f.with_suffix(f".json.migrated-{ts}")
                try:
                    f.rename(target)
                    logger.info("auth_db migration: renamed %s -> %s", f.name, target.name)
                except Exception as e:
                    logger.warning("auth_db migration: rename %s failed: %s", f, e)
        _MIGRATED = True
    except Exception as e:
        logger.warning("auth_db migration skipped due to error: %s", e)
        _MIGRATED = True  # avoid retrying on every call


def _import_permissions(conn: sqlite3.Connection, path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    for pid, p in data.items():
        conn.execute(
            "INSERT OR IGNORE INTO permissions(id,name,description,resource,action,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                p.get("id") or pid,
                p.get("name"),
                p.get("description", ""),
                p.get("resource", ""),
                p.get("action", ""),
                p.get("created_at") or now_iso(),
            ),
        )
        count += 1
    logger.info("auth_db migration: imported %d permissions", count)


def _import_roles(conn: sqlite3.Connection, path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    for rid, r in data.items():
        role_id = r.get("id") or rid
        conn.execute(
            "INSERT OR IGNORE INTO roles(id,name,description,is_system,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                role_id,
                r.get("name"),
                r.get("description", ""),
                1 if r.get("is_system") else 0,
                r.get("created_at") or now_iso(),
                r.get("updated_at") or now_iso(),
            ),
        )
        for perm_id in r.get("permissions") or []:
            conn.execute(
                "INSERT OR IGNORE INTO role_permissions(role_id,permission_id) VALUES(?,?)",
                (role_id, perm_id),
            )
        count += 1
    logger.info("auth_db migration: imported %d roles", count)


def _import_users(conn: sqlite3.Connection, path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    for uid, u in data.items():
        user_id = u.get("id") or uid
        conn.execute(
            "INSERT OR IGNORE INTO users("
            "id,username,email,password_hash,is_active,created_at,updated_at,last_login) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                user_id,
                u.get("username"),
                u.get("email"),
                u.get("password_hash", ""),
                1 if u.get("is_active", True) else 0,
                u.get("created_at") or now_iso(),
                u.get("updated_at") or now_iso(),
                u.get("last_login"),
            ),
        )
        for role_id in u.get("roles") or []:
            # Only link if the role exists (FK ON).
            row = conn.execute("SELECT 1 FROM roles WHERE id=?", (role_id,)).fetchone()
            if row:
                conn.execute(
                    "INSERT OR IGNORE INTO user_roles(user_id,role_id,assigned_at) VALUES(?,?,?)",
                    (user_id, role_id, now_iso()),
                )
        count += 1
    logger.info("auth_db migration: imported %d users", count)

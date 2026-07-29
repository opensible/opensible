"""SQLite (WAL) store for projects list and app settings.

Replaces the flat JSON files ``projects.json``, ``execution_settings.json``,
and ``backup_settings.json`` under ``DATA_DIR/``. Uses the same
concurrency-safe pattern as ``auth_db.py`` and ``index_db.py`` (single shared
connection, ``check_same_thread=False``, WAL journal, autocommit).

Public API — deliberately narrow so callers can be swapped in-place:

    list_projects(data_dir)                 -> list[dict]        # ordered by createdAt
    replace_all_projects(data_dir, items)   -> bool
    get_setting(data_dir, key, default)     -> Any               # JSON round-trip
    set_setting(data_dir, key, value)       -> bool
    migrate_from_json_if_needed(data_dir)   -> None              # one-shot import

On first boot the legacy JSON files are imported, then renamed to
``<name>.json.migrated-<epoch>`` so nothing is deleted.
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
_CONN: Optional[sqlite3.Connection] = None
_DB_PATH: Optional[Path] = None
_MIGRATED = False


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _open(data_dir: Path) -> sqlite3.Connection:
    global _CONN, _DB_PATH
    if _CONN is not None:
        return _CONN
    with _LOCK:
        if _CONN is not None:
            return _CONN
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        _DB_PATH = Path(data_dir) / "config.db"
        conn = sqlite3.connect(
            str(_DB_PATH),
            check_same_thread=False,
            isolation_level=None,  # autocommit
            timeout=10.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id         TEXT PRIMARY KEY,
                data_json  TEXT NOT NULL,
                created_at REAL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_projects_created ON projects(created_at);

            CREATE TABLE IF NOT EXISTS settings (
                key        TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        _CONN = conn
        logger.info("config_db ready at %s (WAL)", _DB_PATH)
        return conn


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def list_projects(data_dir: Path) -> List[Dict[str, Any]]:
    conn = _open(Path(data_dir))
    rows = conn.execute(
        "SELECT data_json FROM projects "
        "ORDER BY COALESCE(created_at, 0) ASC, id ASC"
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            out.append(json.loads(r["data_json"]))
        except Exception:
            continue
    return out


def replace_all_projects(data_dir: Path, items: List[Dict[str, Any]]) -> bool:
    """Overwrite the projects table with the supplied list.

    Mirrors the semantics of the previous ``save_projects(list)`` helper so
    callers keep working without change.
    """
    conn = _open(Path(data_dir))
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM projects")
        now = _now_iso()
        for p in items or []:
            pid = p.get("id")
            if not pid:
                continue
            created = p.get("createdAt") or p.get("created_at")
            try:
                created_f = float(created) if created is not None else None
            except (TypeError, ValueError):
                created_f = None
            conn.execute(
                "INSERT OR REPLACE INTO projects(id, data_json, created_at, updated_at) "
                "VALUES(?,?,?,?)",
                (pid, json.dumps(p, ensure_ascii=False), created_f, now),
            )
        conn.execute("COMMIT")
        return True
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        logger.error("config_db.replace_all_projects failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Settings (key/value JSON blobs)
# ---------------------------------------------------------------------------

def get_setting(data_dir: Path, key: str, default: Any = None) -> Any:
    conn = _open(Path(data_dir))
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key=?", (key,)
    ).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value_json"])
    except Exception:
        return default


def set_setting(data_dir: Path, key: str, value: Any) -> bool:
    conn = _open(Path(data_dir))
    try:
        conn.execute(
            "INSERT INTO settings(key, value_json, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
            "updated_at=excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), _now_iso()),
        )
        return True
    except Exception as e:
        logger.error("config_db.set_setting(%s) failed: %s", key, e)
        return False


# ---------------------------------------------------------------------------
# One-shot JSON -> SQL migration
# ---------------------------------------------------------------------------

_SETTINGS_FILES = {
    "execution_settings": "execution_settings.json",
    "backup_settings": "backup_settings.json",
}


def migrate_from_json_if_needed(data_dir: Path) -> None:
    global _MIGRATED
    if _MIGRATED:
        return
    conn = _open(Path(data_dir))
    dd = Path(data_dir)

    try:
        # ---- projects.json ----
        projects_count = conn.execute(
            "SELECT COUNT(*) FROM projects"
        ).fetchone()[0]
        pfile = dd / "projects.json"
        if projects_count == 0 and pfile.exists():
            try:
                raw = json.loads(pfile.read_text(encoding="utf-8"))
                items = raw.get("projects", []) if isinstance(raw, dict) else []
                if items:
                    replace_all_projects(dd, items)
                    logger.info(
                        "config_db migration: imported %d projects from projects.json",
                        len(items),
                    )
            except Exception as e:
                logger.warning("config_db migration: projects.json import failed: %s", e)

        # ---- settings JSONs ----
        for key, fname in _SETTINGS_FILES.items():
            existing = conn.execute(
                "SELECT 1 FROM settings WHERE key=?", (key,)
            ).fetchone()
            if existing:
                continue
            fpath = dd / fname
            if not fpath.exists():
                continue
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                set_setting(dd, key, data)
                logger.info("config_db migration: imported %s -> settings[%s]", fname, key)
            except Exception as e:
                logger.warning("config_db migration: %s import failed: %s", fname, e)

        # ---- rename source files as backups ----
        ts = int(time.time())
        for fname in ("projects.json", *_SETTINGS_FILES.values()):
            fpath = dd / fname
            if fpath.exists():
                target = fpath.with_suffix(f".json.migrated-{ts}")
                try:
                    fpath.rename(target)
                    logger.info(
                        "config_db migration: renamed %s -> %s", fpath.name, target.name
                    )
                except Exception as e:
                    logger.warning(
                        "config_db migration: rename %s failed: %s", fpath, e
                    )
        _MIGRATED = True
    except Exception as e:
        logger.warning("config_db migration skipped due to error: %s", e)
        _MIGRATED = True  # avoid retry storm

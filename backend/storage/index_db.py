"""
Lightweight SQLite index (libSQL-compatible, WAL mode) for the two backend
hot paths that were burning CPU:

  1. /api/worker/claim  -> "which executions are QUEUED?"
  2. /api/worker/claim  -> "how many RUNNING executions does this worker own?"
  3. /api/worker/log/finish -> "which project owns this execution id?"
  4. /api/worker/heartbeat + token verify -> "which worker owns this token?"

JSON files under data/projects/**/history/executions/ and data/workers/*.json
remain the source of truth. This module keeps a small index in sync so those
hot paths become an indexed SQL lookup instead of a full-directory scan.

If anything about the index fails, callers fall back to the legacy JSON scan.
The index is only an *optimization*, never a hard dependency.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CONN: Optional[sqlite3.Connection] = None
_DB_PATH: Optional[Path] = None
_READY = False


def _data_dir() -> Path:
    env_dir = os.environ.get("DATA_DIR")
    if env_dir:
        return Path(env_dir)
    return Path(__file__).resolve().parent.parent.parent / "data"


def _open() -> Optional[sqlite3.Connection]:
    """Open (or return the existing) connection. Safe to call repeatedly."""
    global _CONN, _DB_PATH, _READY
    if _CONN is not None:
        return _CONN
    with _LOCK:
        if _CONN is not None:
            return _CONN
        try:
            data_dir = _data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            _DB_PATH = data_dir / "index.db"
            conn = sqlite3.connect(
                str(_DB_PATH),
                check_same_thread=False,
                isolation_level=None,  # autocommit; we manage BEGIN explicitly if needed
                timeout=5.0,
            )
            # WAL + friends: readers never block writers, small mmap for hot pages.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA mmap_size=67108864")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queued_executions (
                    execution_id TEXT PRIMARY KEY,
                    project_id   TEXT NOT NULL,
                    queued_at    REAL NOT NULL,
                    requirements TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_queued_at ON queued_executions(queued_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS running_executions (
                    execution_id TEXT PRIMARY KEY,
                    project_id   TEXT NOT NULL,
                    worker_id    TEXT NOT NULL,
                    started_at   REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_running_worker ON running_executions(worker_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_locations (
                    execution_id TEXT PRIMARY KEY,
                    project_id   TEXT NOT NULL,
                    status       TEXT,
                    worker_id    TEXT,
                    updated_at   REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_execution_locations_project ON execution_locations(project_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_tokens (
                    token_hash TEXT PRIMARY KEY,
                    worker_id  TEXT NOT NULL,
                    salt       TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_worker_tokens_worker ON worker_tokens(worker_id)"
            )
            _CONN = conn
            _READY = True
            logger.info("index_db ready at %s (WAL)", _DB_PATH)
            return _CONN
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("index_db unavailable, falling back to JSON scans: %s", e)
            _READY = False
            return None


def is_ready() -> bool:
    return _READY and _CONN is not None


# ---------------------------------------------------------------------------
# Queued executions
# ---------------------------------------------------------------------------

def add_queued_execution(
    execution_id: str,
    project_id: str,
    queued_at: float,
    requirements: Optional[dict] = None,
) -> None:
    conn = _open()
    if conn is None or not execution_id or not project_id:
        return
    try:
        conn.execute(
            "INSERT OR REPLACE INTO queued_executions "
            "(execution_id, project_id, queued_at, requirements) VALUES (?, ?, ?, ?)",
            (
                execution_id,
                project_id,
                float(queued_at or time.time()),
                json.dumps(requirements or {}, ensure_ascii=False),
            ),
        )
    except Exception as e:
        logger.warning("add_queued_execution failed for %s: %s", execution_id, e)


def remove_queued_execution(execution_id: str) -> None:
    conn = _open()
    if conn is None or not execution_id:
        return
    try:
        conn.execute(
            "DELETE FROM queued_executions WHERE execution_id = ?", (execution_id,)
        )
    except Exception as e:
        logger.warning("remove_queued_execution failed for %s: %s", execution_id, e)


def upsert_execution_location(
    execution_id: str,
    project_id: str,
    status: Optional[str] = None,
    worker_id: Optional[str] = None,
    updated_at: Optional[float] = None,
) -> None:
    conn = _open()
    if conn is None or not execution_id or not project_id:
        return
    try:
        conn.execute(
            "INSERT OR REPLACE INTO execution_locations "
            "(execution_id, project_id, status, worker_id, updated_at) VALUES (?, ?, ?, ?, ?)",
            (execution_id, project_id, status, worker_id, float(updated_at or time.time())),
        )
    except Exception as e:
        logger.warning("upsert_execution_location failed for %s: %s", execution_id, e)


def find_execution_project(execution_id: str) -> Optional[str]:
    conn = _open()
    if conn is None or not execution_id:
        return None
    try:
        row = conn.execute(
            "SELECT project_id FROM execution_locations WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.warning("find_execution_project failed for %s: %s", execution_id, e)
        return None


def mark_running_execution(
    execution_id: str,
    project_id: str,
    worker_id: str,
    started_at: Optional[float] = None,
) -> None:
    conn = _open()
    if conn is None or not execution_id or not project_id or not worker_id:
        return
    ts = float(started_at or time.time())
    try:
        conn.execute(
            "INSERT OR REPLACE INTO running_executions "
            "(execution_id, project_id, worker_id, started_at) VALUES (?, ?, ?, ?)",
            (execution_id, project_id, worker_id, ts),
        )
        remove_queued_execution(execution_id)
        upsert_execution_location(execution_id, project_id, "RUNNING", worker_id, ts)
    except Exception as e:
        logger.warning("mark_running_execution failed for %s: %s", execution_id, e)


def remove_running_execution(execution_id: str) -> None:
    conn = _open()
    if conn is None or not execution_id:
        return
    try:
        conn.execute("DELETE FROM running_executions WHERE execution_id = ?", (execution_id,))
    except Exception as e:
        logger.warning("remove_running_execution failed for %s: %s", execution_id, e)


def running_count_for_worker(worker_id: str) -> Optional[int]:
    conn = _open()
    if conn is None or not worker_id:
        return None
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM running_executions WHERE worker_id = ?",
            (worker_id,),
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        logger.warning("running_count_for_worker failed for %s: %s", worker_id, e)
        return None


def list_running_for_worker(worker_id: str) -> List[Tuple[str, str, float]]:
    """Return list of (execution_id, project_id, started_at) rows for a worker."""
    conn = _open()
    if conn is None or not worker_id:
        return []
    try:
        rows = conn.execute(
            "SELECT execution_id, project_id, started_at FROM running_executions "
            "WHERE worker_id = ?",
            (worker_id,),
        ).fetchall()
        return [(r[0], r[1], float(r[2] or 0)) for r in rows]
    except Exception as e:
        logger.warning("list_running_for_worker failed for %s: %s", worker_id, e)
        return []


def prune_stale_running_for_worker(
    worker_id: str,
    projects_dir: Path,
    current_execution_id: Optional[str] = None,
    mark_orphaned_failed: bool = False,
) -> int:
    """Cross-check RUNNING index rows against on-disk execution JSON. Any row
    whose file no longer exists or is no longer RUNNING is removed. Returns
    the number of rows pruned. Used to self-heal after a worker crash/SIGTERM
    that left orphan RUNNING entries blocking future claims."""
    pruned = 0
    now = time.time()
    for exec_id, proj_id, started in list_running_for_worker(worker_id):
        exec_file = projects_dir / proj_id / "history" / "executions" / f"{exec_id}.json"
        if not exec_file.exists():
            exec_file = projects_dir / proj_id / "executions" / f"{exec_id}.json"
        stale = False
        orphaned_running = False
        if not exec_file.exists():
            stale = True
        else:
            try:
                with open(exec_file, "r+", encoding="utf-8") as fh:
                    data = json.load(fh)
                status = data.get("status")
                owner = data.get("workerId")
                if status != "RUNNING" or (owner and owner != worker_id):
                    stale = True
                elif current_execution_id != exec_id:
                    # The worker is online and polling for new work, so it is
                    # idle. A RUNNING row owned by this same worker is therefore
                    # an orphan left behind by a restart/connection reset.
                    orphaned_running = True
                    stale = True
            except Exception:
                stale = True
        if stale:
            if orphaned_running and mark_orphaned_failed and exec_file.exists():
                try:
                    with open(exec_file, "r+", encoding="utf-8") as fh:
                        data = json.load(fh)
                        if data.get("status") == "RUNNING":
                            started_at = float(data.get("startedAt") or started or now)
                            data["status"] = "FAILED"
                            data["finishedAt"] = now
                            data["statusUpdatedAt"] = now
                            data["duration"] = max(0, int(now - started_at))
                            data["error"] = data.get("error") or "Worker restarted before reporting completion"
                            fh.seek(0)
                            fh.truncate()
                            json.dump(data, fh, indent=2, ensure_ascii=False)
                            try:
                                log_dir = projects_dir / proj_id / "history" / "logs"
                                log_dir.mkdir(parents=True, exist_ok=True)
                                with open(log_dir / f"{exec_id}.log", "a", encoding="utf-8") as log_fh:
                                    log_fh.write("\n[recovery] Worker restarted before reporting completion; marked as FAILED and unblocked the queue.\n")
                            except Exception:
                                pass
                            upsert_execution_location(exec_id, proj_id, "FAILED", worker_id, now)
                except Exception as e:
                    logger.warning("failed to mark orphan RUNNING execution %s as FAILED: %s", exec_id, e)
            remove_running_execution(exec_id)
            pruned += 1
    if pruned:
        logger.info("index_db pruned %d stale RUNNING rows for worker %s", pruned, worker_id)
    return pruned


def list_queued(
    project_id: Optional[str] = None, limit: int = 50
) -> List[Tuple[str, str, float]]:
    """Return oldest-first list of (execution_id, project_id, queued_at)."""
    conn = _open()
    if conn is None:
        return []
    try:
        if project_id:
            rows = conn.execute(
                "SELECT execution_id, project_id, queued_at FROM queued_executions "
                "WHERE project_id = ? ORDER BY queued_at ASC LIMIT ?",
                (project_id, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT execution_id, project_id, queued_at FROM queued_executions "
                "ORDER BY queued_at ASC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]
    except Exception as e:
        logger.warning("list_queued failed: %s", e)
        return []


def queued_count() -> int:
    conn = _open()
    if conn is None:
        return 0
    try:
        row = conn.execute("SELECT COUNT(*) FROM queued_executions").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Worker tokens
# ---------------------------------------------------------------------------

def upsert_worker_token(worker_id: str, token_hash: str, salt: str) -> None:
    conn = _open()
    if conn is None or not worker_id or not token_hash or not salt:
        return
    try:
        # A worker only has one active token at a time; drop any prior rows for it.
        conn.execute("DELETE FROM worker_tokens WHERE worker_id = ?", (worker_id,))
        conn.execute(
            "INSERT OR REPLACE INTO worker_tokens (token_hash, worker_id, salt) "
            "VALUES (?, ?, ?)",
            (token_hash, worker_id, salt),
        )
    except Exception as e:
        logger.warning("upsert_worker_token failed for %s: %s", worker_id, e)


def remove_worker_token(worker_id: str) -> None:
    conn = _open()
    if conn is None or not worker_id:
        return
    try:
        conn.execute("DELETE FROM worker_tokens WHERE worker_id = ?", (worker_id,))
    except Exception as e:
        logger.warning("remove_worker_token failed for %s: %s", worker_id, e)


def find_worker_by_token_hash(token_hash: str) -> Optional[Tuple[str, str]]:
    """Return (worker_id, salt) matching a full token hash, or None."""
    conn = _open()
    if conn is None or not token_hash:
        return None
    try:
        row = conn.execute(
            "SELECT worker_id, salt FROM worker_tokens WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row:
            return (row[0], row[1])
    except Exception as e:
        logger.warning("find_worker_by_token_hash failed: %s", e)
    return None


def all_worker_salts() -> List[Tuple[str, str, str]]:
    """Return list of (worker_id, salt, token_hash) — used for slow-path token verify."""
    conn = _open()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT worker_id, salt, token_hash FROM worker_tokens"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]
    except Exception:
        return []


def worker_token_count() -> int:
    conn = _open()
    if conn is None:
        return 0
    try:
        row = conn.execute("SELECT COUNT(*) FROM worker_tokens").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# One-shot backfill (called at startup if the DB is empty)
# ---------------------------------------------------------------------------

def backfill_if_empty(projects_dir: Path, workers_dir: Path) -> None:
    """Walk the JSON tree once and populate the index if it's empty."""
    conn = _open()
    if conn is None:
        return
    try:
        if queued_count() == 0:
            _backfill_queued(projects_dir)
        _backfill_execution_locations(projects_dir)
        if worker_token_count() == 0:
            _backfill_workers(workers_dir)
    except Exception as e:
        logger.warning("index_db backfill failed: %s", e)


def _backfill_queued(projects_dir: Path) -> None:
    if not projects_dir or not projects_dir.exists():
        return
    count = 0
    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        exec_dir = proj_dir / "history" / "executions"
        if not exec_dir.exists():
            exec_dir = proj_dir / "executions"
            if not exec_dir.exists():
                continue
        for f in exec_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("status") != "QUEUED":
                    continue
                add_queued_execution(
                    data.get("id") or f.stem,
                    proj_dir.name,
                    float(data.get("queuedAt") or data.get("createdAt") or 0),
                    {
                        "tags": data.get("tags"),
                        "capabilities": data.get("requiredCapabilities"),
                    },
                )
                count += 1
            except Exception:
                continue
    if count:
        logger.info("index_db backfilled %d QUEUED executions", count)


def _backfill_execution_locations(projects_dir: Path) -> None:
    if not projects_dir or not projects_dir.exists():
        return
    count = 0
    running = 0
    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        exec_dir = proj_dir / "history" / "executions"
        if not exec_dir.exists():
            exec_dir = proj_dir / "executions"
            if not exec_dir.exists():
                continue
        for f in exec_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                execution_id = data.get("id") or f.stem
                status = data.get("status")
                worker_id = data.get("workerId")
                upsert_execution_location(
                    execution_id,
                    proj_dir.name,
                    status,
                    worker_id,
                    data.get("statusUpdatedAt") or data.get("createdAt") or 0,
                )
                if status == "RUNNING" and worker_id:
                    mark_running_execution(
                        execution_id,
                        proj_dir.name,
                        worker_id,
                        data.get("startedAt") or data.get("createdAt") or 0,
                    )
                    running += 1
                else:
                    remove_running_execution(execution_id)
                count += 1
            except Exception:
                continue
    if count:
        logger.info(
            "index_db backfilled %d execution locations (%d RUNNING)",
            count,
            running,
        )


def _backfill_workers(workers_dir: Path) -> None:
    if not workers_dir or not workers_dir.exists():
        return
    count = 0
    for f in workers_dir.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            wid = data.get("id")
            th = data.get("tokenHash")
            salt = data.get("tokenSalt")
            if wid and th and salt:
                upsert_worker_token(wid, th, salt)
                count += 1
        except Exception:
            continue
    if count:
        logger.info("index_db backfilled %d worker tokens", count)

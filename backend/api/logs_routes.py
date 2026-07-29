"""Server / frontend log routes blueprint.

Extracted from app.py during Phase 3 of the refactor. Behaviour and JSON
shape are unchanged, but this module intentionally does not import ``app`` at
request time: in Docker the backend is started as ``python app.py`` so the
running module is ``__main__`` and importing ``app`` creates a second, partially
initialized application module.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, current_app, jsonify, request

from auth.middleware import require_auth
from app_context import get_data_dir

logger = logging.getLogger(__name__)
bp = Blueprint("logs_api", __name__)


# ---------- local logging config helpers ----------

LOG_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _data_dir() -> Path:
    try:
        return get_data_dir()
    except Exception:
        return Path(os.environ.get("DATA_DIR", str(Path.cwd() / "data")))


def _log_dir() -> Path:
    log_dir = _data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _frontend_log_file() -> Path:
    return _log_dir() / "frontend.log"


def _get_logging_settings():
    """Load logging settings without importing app.py."""
    try:
        execution_settings_file = _data_dir() / "execution_settings.json"
        if execution_settings_file.exists():
            with open(execution_settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
            log_level = settings.get("log_level", "INFO").upper()
            max_log_size_mb = settings.get("max_log_size_mb", 10)
            return log_level, max_log_size_mb
    except Exception:
        current_app.logger.debug("Failed to read logging settings; using defaults", exc_info=True)

    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    max_log_size_mb = int(os.environ.get("MAX_LOG_SIZE_MB", "10"))
    return log_level, max_log_size_mb


# ---------- helpers ----------

def _tail_lines(path: Path, n: int, chunk: int = 65536) -> List[str]:
    """Read the last ``n`` lines from ``path`` without loading the whole file.

    Reads backwards in chunks — cheap on multi-MB log files that were
    previously being fully slurped into memory on every 5-second poll.
    """
    if n <= 0:
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            if end == 0:
                return []
            buf = bytearray()
            pos = end
            found = 0
            # +1 so we don't accidentally return a partial first line
            while pos > 0 and found <= n:
                read = min(chunk, pos)
                pos -= read
                f.seek(pos)
                data = f.read(read)
                buf[:0] = data
                found = buf.count(b"\n")
            text = buf.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        return lines[-n:]
    except Exception:
        return []




def _parse_log_timestamp(ts_str: Optional[str]):
    """Parse ``[YYYY-MM-DD HH:MM:SS(.ffffff)?]`` (and ISO variants) into a
    ``datetime``. Returns ``None`` when parsing fails."""
    if not ts_str:
        return None
    ts_str = ts_str.strip().replace(",", ".")
    if not ts_str:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ):
        try:
            s = ts_str[:26] if ".%f" in fmt else (ts_str[:19] if len(ts_str) >= 19 else ts_str[:16])
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    try:
        return datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _parse_log_line(line: str, svc: str, line_num: int) -> Dict[str, Any]:
    """Parse Python backend logs and Go slog worker logs into one shape."""
    log_entry: Dict[str, Any] = {
        "service": svc,
        "raw": line,
        "timestamp": None,
        "level": "INFO",
        "message": line,
        "line_number": line_num,
    }

    # Python backend/frontend: [2026-07-22 06:45:26] INFO: message
    timestamp_match = re.match(
        r"\[(\d{4}-\d{2}-\d{2}[\s,]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\]",
        line,
    )
    if timestamp_match:
        log_entry["timestamp"] = timestamp_match.group(1)
        remaining = line[timestamp_match.end():].strip()
        level_match = re.match(
            r"(ERROR|WARNING|WARN|INFO|DEBUG|CRITICAL|FATAL)\s*(?:in\s+\w+)?:?\s*(.*)",
            remaining,
            re.IGNORECASE,
        )
        if level_match:
            level = level_match.group(1).upper()
            log_entry["level"] = "WARNING" if level == "WARN" else level
            log_entry["message"] = level_match.group(2) if level_match.group(2) else remaining
        else:
            log_entry["message"] = remaining
        return log_entry

    # Go worker slog text: ts="2026-07-22 06:45:26" level=INFO msg="..."
    slog_match = re.search(
        r'ts=(?:"([^"]+)"|(\S+))\s+level=(\w+)\s+msg=(?:"((?:\\.|[^"])*)"|(\S+))',
        line,
    )
    if slog_match:
        ts = slog_match.group(1) or slog_match.group(2)
        level = (slog_match.group(3) or "INFO").upper()
        msg = slog_match.group(4) or slog_match.group(5) or line
        try:
            msg = bytes(msg, "utf-8").decode("unicode_escape")
        except Exception:
            pass
        log_entry["timestamp"] = ts
        log_entry["level"] = "WARNING" if level == "WARN" else level
        log_entry["message"] = msg
        return log_entry

    return log_entry


# ---------- routes ----------

@bp.route("/api/server_logs", methods=["GET"])
@require_auth
def get_server_logs():
    """Return recent log entries across worker/backend/frontend services.

    Query: ``lines``, ``service`` (``all`` or CSV), ``level``, ``search``,
    ``date_from``, ``date_to`` (``YYYY-MM-DDTHH:mm`` and variants).
    """
    try:
        lines = request.args.get("lines", 1000, type=int)
        service = request.args.get("service", "all")
        level = request.args.get("level", "all")
        search = request.args.get("search", "").strip()
        date_from_str = request.args.get("date_from", "").strip()
        date_to_str = request.args.get("date_to", "").strip()

        date_from_dt = _parse_log_timestamp(date_from_str.replace("T", " ").replace("Z", "")) if date_from_str else None
        date_to_dt = _parse_log_timestamp(date_to_str.replace("T", " ").replace("Z", "")) if date_to_str else None
        if date_to_dt and len(date_to_str) <= 16:
            date_to_dt = date_to_dt + timedelta(seconds=59, milliseconds=999)
        if date_from_dt or date_to_dt:
            lines = min(max(lines * 10, 5000), 50000)

        log_dir = _log_dir()
        log_files = {
            "worker": log_dir / "worker.log",
            "backend": log_dir / "backend.log",
            "frontend": log_dir / "frontend.log",
        }

        if service == "all":
            services_to_read = ["worker", "backend", "frontend"]
        else:
            services_to_read = [s.strip() for s in service.split(",") if s.strip()]

        all_log_entries: List[Dict[str, Any]] = []

        for svc in services_to_read:
            log_file = log_files.get(svc)
            if not log_file:
                current_app.logger.debug(f"Log file not configured for service: {svc}")
                continue
            if not log_file.exists():
                current_app.logger.debug(f"Log file does not exist: {log_file}")
                continue
            current_app.logger.debug(f"Reading logs from: {log_file}")

            try:
                # Efficient tail: seek from EOF instead of readlines() on huge files.
                recent_lines = _tail_lines(log_file, lines)
                base_line_num = 1  # exact line numbers are not used by UI

                for line_num, line in enumerate(recent_lines, start=base_line_num):
                    line = line.rstrip("\n\r")
                    if not line:
                        continue

                    log_entry = _parse_log_line(line, svc, line_num)

                    if date_from_dt is not None or date_to_dt is not None:
                        log_dt = _parse_log_timestamp(log_entry["timestamp"]) if log_entry["timestamp"] else None
                        if log_dt is None:
                            continue
                        if date_from_dt is not None and log_dt < date_from_dt:
                            continue
                        if date_to_dt is not None and log_dt > date_to_dt:
                            continue

                    if level != "all":
                        level_upper = level.upper()
                        log_level_upper = log_entry["level"].upper()
                        if level_upper not in log_level_upper and log_level_upper not in level_upper:
                            continue

                    if search and search.lower() not in line.lower():
                        continue

                    all_log_entries.append(log_entry)

            except Exception as e:
                current_app.logger.error(f"Error reading {svc} logs: {e}", exc_info=True)
                continue

        all_log_entries.sort(key=lambda x: x["timestamp"] if x["timestamp"] else "", reverse=False)

        stats = {
            "error": sum(1 for e in all_log_entries if "ERROR" in e["level"]),
            "warning": sum(1 for e in all_log_entries if "WARNING" in e["level"]),
            "info": sum(1 for e in all_log_entries if e["level"] == "INFO"),
            "debug": sum(1 for e in all_log_entries if e["level"] == "DEBUG"),
        }

        # Security: redact sensitive information
        try:
            SENSITIVE_KEYS = [
                "password", "passphrase", "token", "secret", "privatekey", "private_key",
                "ansible_ssh_pass", "global_secrets_encryption_key",
            ]
            for entry in all_log_entries:
                redacted = entry["message"]
                for k in SENSITIVE_KEYS:
                    redacted = re.sub(
                        rf'({k}\s*[:=]\s*)([^\s\'"]+)',
                        r"\1***REDACTED***",
                        redacted,
                        flags=re.IGNORECASE,
                    )
                redacted = re.sub(
                    r"-----BEGIN [^-]+-----[\s\S]*?-----END [^-]+-----",
                    "-----BEGIN ***REDACTED***-----\n***REDACTED***\n-----END ***REDACTED***-----",
                    redacted,
                )
                entry["message"] = redacted
                entry["raw"] = redacted
        except Exception:
            pass

        current_app.logger.debug(f"Returning {len(all_log_entries)} log entries, stats: {stats}")

        return jsonify({
            "success": True,
            "logs": all_log_entries,
            "stats": stats,
            "total": len(all_log_entries),
        })
    except Exception as e:
        current_app.logger.error(f"Error reading server logs: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/api/frontend_logs", methods=["POST"])
@require_auth
def api_frontend_logs():
    """Accept a batch of frontend log entries and write them to ``frontend.log``."""
    try:
        data = request.json or {}
        logs = data.get("logs", [])

        current_app.logger.debug(
            f"Received request to /api/frontend_logs with {len(logs) if isinstance(logs, list) else 0} logs"
        )

        if not isinstance(logs, list):
            current_app.logger.warning(
                f"Invalid request to /api/frontend_logs: logs is not a list, got {type(logs)}"
            )
            return jsonify({"success": False, "error": "logs must be an array"}), 400

        if not logs:
            current_app.logger.debug("No logs to process in /api/frontend_logs")
            return jsonify({"success": True, "message": "No logs to process"})

        log_level_str, _ = _get_logging_settings()
        log_level = LOG_LEVEL_MAP.get(log_level_str.upper(), logging.INFO)
        current_app.logger.debug(f"Current log level: {log_level_str} (value: {log_level})")

        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        logged_count = 0

        for log_entry in logs:
            if not isinstance(log_entry, dict):
                continue

            level = log_entry.get("level", "INFO").upper()
            message = log_entry.get("message", "")
            timestamp = log_entry.get("timestamp", "")
            stack = log_entry.get("stack")

            if level not in valid_levels:
                current_app.logger.warning(f"Invalid log level from frontend: {level}")
                continue

            level_value = LOG_LEVEL_MAP.get(level, logging.INFO)
            if level_value < log_level:
                current_app.logger.debug(
                    f"Filtered out log with level {level} (value: {level_value}) "
                    f"< current level {log_level_str} (value: {log_level})"
                )
                continue

            if timestamp:
                log_message = f"[{timestamp}] {level}: {message}"
            else:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log_message = f"[{timestamp}] {level}: {message}"

            if stack:
                log_message += f"\n{stack}"

            try:
                fl = logging.getLogger("frontend")
                if level == "DEBUG":
                    fl.debug(log_message)
                elif level == "INFO":
                    fl.info(log_message)
                elif level == "WARNING":
                    fl.warning(log_message)
                elif level == "ERROR":
                    fl.error(log_message)
                elif level == "CRITICAL":
                    fl.critical(log_message)

                logged_count += 1
                current_app.logger.debug(f"Wrote frontend log: {level} - {message[:50]}...")
            except Exception as log_error:
                current_app.logger.error(
                    f"Error writing frontend log to file {_frontend_log_file()}: {log_error}",
                    exc_info=True,
                )

        if logged_count > 0:
            current_app.logger.info(
                f"Received {len(logs)} logs from frontend, logged {logged_count} to {_frontend_log_file()}"
            )
        else:
            current_app.logger.debug(
                f"Received {len(logs)} logs from frontend, but none were logged "
                f"(filtered by log level: {log_level_str})"
            )

        return jsonify({
            "success": True,
            "logged": logged_count,
            "total": len(logs),
        })

    except Exception as e:
        current_app.logger.error(f"Error processing frontend logs: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

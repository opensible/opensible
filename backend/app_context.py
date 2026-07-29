"""Shared application context for blueprints and utils.

Holds references to module-level singletons that helpers need without
forcing them to import from the massive ``app.py``. During the phased
refactor, ``app.py`` populates this module after creating the Flask app
and computing filesystem roots; utils/blueprints read via getters.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

_app: Optional[Any] = None
_projects_dir: Optional[Path] = None
_backups_dir: Optional[Path] = None
_data_dir: Optional[Path] = None


def set_app(flask_app: Any) -> None:
    global _app
    _app = flask_app


def get_app() -> Any:
    if _app is None:
        raise RuntimeError("app_context not initialized; call set_app() from app.py first")
    return _app


def set_projects_dir(path: Path) -> None:
    global _projects_dir
    _projects_dir = Path(path)


def get_projects_dir() -> Path:
    if _projects_dir is None:
        raise RuntimeError("app_context.PROJECTS_DIR not initialized; call set_projects_dir() from app.py first")
    return _projects_dir


def set_backups_dir(path: Path) -> None:
    global _backups_dir
    _backups_dir = Path(path)


def get_backups_dir() -> Path:
    if _backups_dir is None:
        raise RuntimeError("app_context.BACKUPS_DIR not initialized; call set_backups_dir() from app.py first")
    return _backups_dir


def set_data_dir(path: Path) -> None:
    global _data_dir
    _data_dir = Path(path)


def get_data_dir() -> Path:
    if _data_dir is None:
        raise RuntimeError("app_context.DATA_DIR not initialized; call set_data_dir() from app.py first")
    return _data_dir

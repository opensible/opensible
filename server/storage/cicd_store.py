#!/usr/bin/env python3
"""File-based JSON storage for CI/CD pipelines and runs."""
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from worker.config import PROJECTS_DIR
except ImportError:
    try:
        from .worker.config import PROJECTS_DIR
    except ImportError:
        import os
        _env_data = os.environ.get('DATA_DIR')
        if _env_data:
            PROJECTS_DIR = Path(_env_data) / 'projects'
        else:
            _mod_dir = Path(__file__).resolve().parent
            if str(_mod_dir) == '/app':
                _base = _mod_dir
            elif _mod_dir.name == 'backend':
                _base = _mod_dir.parent
            else:
                _base = _mod_dir.parent.parent
            PROJECTS_DIR = _base / 'data' / 'projects'

logger = logging.getLogger(__name__)

FINAL_STATUSES = {'SUCCESS', 'FAILED', 'CANCELED'}


def _project_dir(project_id: str) -> Path:
    return PROJECTS_DIR / project_id


def _pipelines_dir(project_id: str) -> Path:
    d = _project_dir(project_id) / 'cicd' / 'pipelines'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _runs_dir(project_id: str) -> Path:
    d = _project_dir(project_id) / 'cicd' / 'runs'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _artifacts_dir(project_id: str) -> Path:
    d = _project_dir(project_id) / 'cicd' / 'artifacts'
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------

def create_pipeline(project_id: str, data: Dict[str, Any]) -> str:
    pipeline_id = data.get('id') or str(uuid.uuid4())
    record = {
        'id': pipeline_id,
        'project_id': project_id,
        'name': data.get('name', 'Untitled Pipeline'),
        'git_repo': data.get('git_repo', ''),
        'git_branch': data.get('git_branch', 'main'),
        'trigger_config': data.get('trigger_config', {}),
        'stages': data.get('stages', []),
        'created_at': time.time(),
        'updated_at': time.time(),
    }
    path = _pipelines_dir(project_id) / f'{pipeline_id}.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    logger.info(f"Created pipeline {pipeline_id} for project {project_id}")
    return pipeline_id


def list_pipelines(project_id: str) -> List[Dict[str, Any]]:
    d = _pipelines_dir(project_id)
    pipelines = []
    for f in sorted(d.glob('*.json')):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                pipelines.append(json.load(fh))
        except Exception as e:
            logger.warning(f"Error reading pipeline {f}: {e}")
    return pipelines


def get_pipeline(project_id: str, pipeline_id: str) -> Optional[Dict[str, Any]]:
    path = _pipelines_dir(project_id) / f'{pipeline_id}.json'
    if not path.exists():
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading pipeline {pipeline_id}: {e}")
        return None


def update_pipeline(project_id: str, pipeline_id: str, data: Dict[str, Any]) -> bool:
    path = _pipelines_dir(project_id) / f'{pipeline_id}.json'
    if not path.exists():
        return False
    try:
        with open(path, 'r', encoding='utf-8') as f:
            record = json.load(f)
        for key in ('name', 'git_repo', 'git_branch', 'trigger_config', 'stages'):
            if key in data:
                record[key] = data[key]
        record['updated_at'] = time.time()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Error updating pipeline {pipeline_id}: {e}")
        return False


def delete_pipeline(project_id: str, pipeline_id: str) -> bool:
    path = _pipelines_dir(project_id) / f'{pipeline_id}.json'
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Pipeline Runs
# ---------------------------------------------------------------------------

def create_pipeline_run(project_id: str, pipeline_id: str, data: Dict[str, Any]) -> str:
    run_id = data.get('id') or str(uuid.uuid4())
    # Determine next run number for this pipeline
    existing = list_pipeline_runs(project_id, pipeline_id=pipeline_id)
    run_number = max((r.get('run_number', 0) for r in existing), default=0) + 1
    record = {
        'id': run_id,
        'project_id': project_id,
        'pipeline_id': pipeline_id,
        'run_number': run_number,
        'trigger_type': data.get('trigger_type', 'manual'),
        'status': data.get('status', 'QUEUED'),
        'git_commit': data.get('git_commit', ''),
        'triggered_by': data.get('triggered_by', ''),
        'started_at': None,
        'finished_at': None,
        'created_at': time.time(),
        'steps': data.get('steps', []),
    }
    path = _runs_dir(project_id) / f'{run_id}.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    logger.info(f"Created pipeline run {run_id} (#{run_number}) for pipeline {pipeline_id}")
    return run_id


def list_pipeline_runs(project_id: str, pipeline_id: Optional[str] = None) -> List[Dict[str, Any]]:
    d = _runs_dir(project_id)
    runs = []
    for f in sorted(d.glob('*.json')):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                run = json.load(fh)
            if pipeline_id and run.get('pipeline_id') != pipeline_id:
                continue
            runs.append(run)
        except Exception as e:
            logger.warning(f"Error reading run {f}: {e}")
    # Sort by created_at desc
    runs.sort(key=lambda r: r.get('created_at', 0), reverse=True)
    return runs


def get_pipeline_run(project_id: str, run_id: str) -> Optional[Dict[str, Any]]:
    path = _runs_dir(project_id) / f'{run_id}.json'
    if not path.exists():
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading run {run_id}: {e}")
        return None


def update_pipeline_run(project_id: str, run_id: str, updates: Dict[str, Any]) -> bool:
    path = _runs_dir(project_id) / f'{run_id}.json'
    if not path.exists():
        return False
    try:
        with open(path, 'r', encoding='utf-8') as f:
            record = json.load(f)
        old_status = record.get('status', 'QUEUED')
        new_status = updates.get('status')
        if new_status and new_status != old_status:
            if old_status == 'QUEUED' and new_status == 'RUNNING':
                updates['started_at'] = updates.get('started_at', time.time())
            if new_status in FINAL_STATUSES:
                updates['finished_at'] = updates.get('finished_at', time.time())
        record.update(updates)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Error updating run {run_id}: {e}")
        return False


def delete_pipeline_run(project_id: str, run_id: str) -> bool:
    path = _runs_dir(project_id) / f'{run_id}.json'
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

def save_artifact(project_id: str, run_id: str, name: str, content: bytes) -> Path:
    d = _artifacts_dir(project_id) / run_id
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    with open(path, 'wb') as f:
        f.write(content)
    return path


def list_artifacts(project_id: str, run_id: str) -> List[str]:
    d = _artifacts_dir(project_id) / run_id
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file())


def get_artifact_path(project_id: str, run_id: str, name: str) -> Optional[Path]:
    path = _artifacts_dir(project_id) / run_id / name
    return path if path.exists() else None

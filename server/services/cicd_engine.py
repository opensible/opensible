"""CI/CD Pipeline Orchestrator.

Self-contained executor: runs pipeline steps in a background thread and
writes per-step logs into ``cicd/runs/<run_id>/step_<index>.log``.

Kept intentionally independent from the Ansible worker HTTP infrastructure
so that adding CI/CD cannot regress the existing playbook execution path.

Supported step types (v1):
  * shell   - run a shell command (config: { command: "..." })
  * ansible - reserved (marks step SUCCESS with a note, integration TBD)
  * tofu    - reserved (marks step SUCCESS with a note, integration TBD)
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import storage.cicd_store as cicd_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def trigger_pipeline_run(project_id: str, pipeline_id: str, trigger_type: str = "manual",
                         git_commit: str = "", triggered_by: str = "") -> Optional[str]:
    """Create a pipeline run and start executing it in a background thread."""
    pipeline = cicd_store.get_pipeline(project_id, pipeline_id)
    if not pipeline:
        logger.error(f"Pipeline {pipeline_id} not found")
        return None

    steps: List[Dict[str, Any]] = []
    for stage in pipeline.get("stages", []) or []:
        for step in stage.get("steps", []) or []:
            steps.append({
                "stage_name": stage.get("name", "stage"),
                "step_name": step.get("name", "step"),
                "step_type": step.get("type", "shell"),
                "config": step.get("config", {}) or {},
                "status": "PENDING",
                "started_at": None,
                "finished_at": None,
                "log_ref": None,
            })

    run_id = cicd_store.create_pipeline_run(project_id, pipeline_id, {
        "trigger_type": trigger_type,
        "status": "QUEUED",
        "git_commit": git_commit,
        "triggered_by": triggered_by,
        "steps": steps,
    })

    t = threading.Thread(
        target=_run_pipeline_thread,
        args=(project_id, run_id),
        name=f"cicd-run-{run_id[:8]}",
        daemon=True,
    )
    t.start()
    return run_id


def get_step_log(project_id: str, run_id: str, step_index: int) -> str:
    log_path = _step_log_path(project_id, run_id, step_index)
    if not log_path.exists():
        return ""
    try:
        return log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.warning(f"Error reading step log {log_path}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Internal execution
# ---------------------------------------------------------------------------

def _run_dir(project_id: str, run_id: str) -> Path:
    project_dir = cicd_store._project_dir(project_id)
    d = project_dir / "cicd" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _step_log_path(project_id: str, run_id: str, index: int) -> Path:
    return _run_dir(project_id, run_id) / f"step_{index}.log"


def _run_pipeline_thread(project_id: str, run_id: str) -> None:
    try:
        run = cicd_store.get_pipeline_run(project_id, run_id)
        if not run:
            return

        cicd_store.update_pipeline_run(project_id, run_id, {"status": "RUNNING"})
        steps: List[Dict[str, Any]] = list(run.get("steps", []))

        overall_failed = False
        for i, step in enumerate(steps):
            step["status"] = "RUNNING"
            step["started_at"] = time.time()
            step["log_ref"] = f"step_{i}.log"
            cicd_store.update_pipeline_run(project_id, run_id, {"steps": steps})

            status = _execute_step(project_id, run_id, i, step)

            step["status"] = status
            step["finished_at"] = time.time()
            cicd_store.update_pipeline_run(project_id, run_id, {"steps": steps})

            if status != "SUCCESS":
                overall_failed = True
                # Mark subsequent steps as SKIPPED
                for j in range(i + 1, len(steps)):
                    steps[j]["status"] = "SKIPPED"
                cicd_store.update_pipeline_run(project_id, run_id, {"steps": steps})
                break

        final = "FAILED" if overall_failed else "SUCCESS"
        cicd_store.update_pipeline_run(project_id, run_id, {"status": final})
        logger.info(f"Pipeline run {run_id} finished with status {final}")
    except Exception as e:
        logger.exception(f"Pipeline run {run_id} crashed: {e}")
        try:
            cicd_store.update_pipeline_run(project_id, run_id, {"status": "FAILED"})
        except Exception:
            pass


def _execute_step(project_id: str, run_id: str, index: int, step: Dict[str, Any]) -> str:
    """Execute a single step and return SUCCESS or FAILED."""
    log_path = _step_log_path(project_id, run_id, index)
    step_type = (step.get("step_type") or "shell").lower()
    config = step.get("config", {}) or {}

    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"=== Step: {step.get('step_name')} ({step_type}) ===\n")
        log.write(f"=== Stage: {step.get('stage_name')} ===\n\n")
        log.flush()

        if step_type == "shell":
            command = config.get("command") or config.get("script") or ""
            if not command.strip():
                log.write("[error] no command provided\n")
                return "FAILED"
            log.write(f"$ {command}\n\n")
            log.flush()
            try:
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=str(_run_dir(project_id, run_id)),
                    env=os.environ.copy(),
                    text=True,
                    bufsize=1,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    log.write(line)
                    log.flush()
                rc = proc.wait()
                log.write(f"\n[exit code: {rc}]\n")
                return "SUCCESS" if rc == 0 else "FAILED"
            except Exception as e:
                log.write(f"\n[exception] {e}\n")
                return "FAILED"

        if step_type in ("ansible", "tofu"):
            log.write(
                f"[info] step type '{step_type}' is not yet wired to the "
                f"worker; marking as SUCCESS placeholder.\n"
            )
            return "SUCCESS"

        log.write(f"[error] unknown step type: {step_type}\n")
        return "FAILED"

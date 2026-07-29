"""CI/CD Pipelines and Runs API.

- GET    /api/cicd/pipelines                list pipelines
- POST   /api/cicd/pipelines                create pipeline
- GET    /api/cicd/pipelines/<id>           get pipeline
- PUT    /api/cicd/pipelines/<id>           update pipeline
- DELETE /api/cicd/pipelines/<id>           delete pipeline
- POST   /api/cicd/pipelines/<id>/trigger   trigger a run

- GET    /api/cicd/runs                     list runs
- GET    /api/cicd/runs/<id>                get run detail
- DELETE /api/cicd/runs/<id>                delete run
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

import storage.cicd_store as cicd_store
from services.cicd_engine import trigger_pipeline_run, get_step_log

logger = logging.getLogger(__name__)
bp = Blueprint("cicd_api", __name__, url_prefix="/api/cicd")


def _project_id() -> Optional[str]:
    pid = request.headers.get("X-Project-Id")
    if pid:
        return pid
    body = request.get_json(silent=True) or {}
    return body.get("project_id") or request.args.get("project_id")


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------

@bp.route("/pipelines", methods=["GET"])
def list_pipelines():
    pid = _project_id()
    if not pid:
        return jsonify({"error": "project_id is required"}), 400
    pipelines = cicd_store.list_pipelines(pid)
    return jsonify({"success": True, "pipelines": pipelines})


@bp.route("/pipelines", methods=["POST"])
def create_pipeline():
    pid = _project_id()
    if not pid:
        return jsonify({"error": "project_id is required"}), 400
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    pipeline_id = cicd_store.create_pipeline(pid, data)
    return jsonify({"success": True, "pipeline_id": pipeline_id}), 201


@bp.route("/pipelines/<pipeline_id>", methods=["GET"])
def get_pipeline(pipeline_id: str):
    pid = _project_id()
    if not pid:
        return jsonify({"error": "project_id is required"}), 400
    pipeline = cicd_store.get_pipeline(pid, pipeline_id)
    if not pipeline:
        return jsonify({"error": "Pipeline not found"}), 404
    return jsonify({"success": True, "pipeline": pipeline})


@bp.route("/pipelines/<pipeline_id>", methods=["PUT"])
def update_pipeline(pipeline_id: str):
    pid = _project_id()
    if not pid:
        return jsonify({"error": "project_id is required"}), 400
    data = request.get_json(silent=True) or {}
    ok = cicd_store.update_pipeline(pid, pipeline_id, data)
    if not ok:
        return jsonify({"error": "Pipeline not found"}), 404
    return jsonify({"success": True})


@bp.route("/pipelines/<pipeline_id>", methods=["DELETE"])
def delete_pipeline(pipeline_id: str):
    pid = _project_id()
    if not pid:
        return jsonify({"error": "project_id is required"}), 400
    ok = cicd_store.delete_pipeline(pid, pipeline_id)
    if not ok:
        return jsonify({"error": "Pipeline not found"}), 404
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

@bp.route("/pipelines/<pipeline_id>/trigger", methods=["POST"])
def trigger_pipeline(pipeline_id: str):
    pid = _project_id()
    if not pid:
        return jsonify({"error": "project_id is required"}), 400
    pipeline = cicd_store.get_pipeline(pid, pipeline_id)
    if not pipeline:
        return jsonify({"error": "Pipeline not found"}), 404
    data = request.get_json(silent=True) or {}
    run_id = trigger_pipeline_run(
        project_id=pid,
        pipeline_id=pipeline_id,
        trigger_type=data.get("trigger_type", "manual"),
        git_commit=data.get("git_commit", ""),
        triggered_by=data.get("triggered_by", ""),
    )
    if not run_id:
        return jsonify({"error": "Failed to trigger pipeline run"}), 500
    run = cicd_store.get_pipeline_run(pid, run_id)
    return jsonify({"success": True, "run_id": run_id, "run_number": run.get("run_number")})


@bp.route("/runs", methods=["GET"])
def list_runs():
    pid = _project_id()
    if not pid:
        return jsonify({"error": "project_id is required"}), 400
    pipeline_id = request.args.get("pipeline_id")
    runs = cicd_store.list_pipeline_runs(pid, pipeline_id=pipeline_id or None)
    return jsonify({"success": True, "runs": runs})


@bp.route("/runs/<run_id>", methods=["GET"])
def get_run(run_id: str):
    pid = _project_id()
    if not pid:
        return jsonify({"error": "project_id is required"}), 400
    run = cicd_store.get_pipeline_run(pid, run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    return jsonify({"success": True, "run": run})


@bp.route("/runs/<run_id>", methods=["PUT"])
def update_run(run_id: str):
    pid = _project_id()
    if not pid:
        return jsonify({"error": "project_id is required"}), 400
    data = request.get_json(silent=True) or {}
    ok = cicd_store.update_pipeline_run(pid, run_id, data)
    if not ok:
        return jsonify({"error": "Run not found"}), 404
    return jsonify({"success": True})


@bp.route("/runs/<run_id>", methods=["DELETE"])
def delete_run(run_id: str):
    pid = _project_id()
    if not pid:
        return jsonify({"error": "project_id is required"}), 400
    ok = cicd_store.delete_pipeline_run(pid, run_id)
    if not ok:
        return jsonify({"error": "Run not found"}), 404
    return jsonify({"success": True})


@bp.route("/runs/<run_id>/steps/<int:step_index>/log", methods=["GET"])
def get_run_step_log(run_id: str, step_index: int):
    pid = _project_id()
    if not pid:
        return jsonify({"error": "project_id is required"}), 400
    run = cicd_store.get_pipeline_run(pid, run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    content = get_step_log(pid, run_id, step_index)
    return jsonify({"success": True, "content": content})


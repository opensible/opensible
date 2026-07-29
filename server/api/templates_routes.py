"""Deployment Templates API.

Catalog:
- GET  /api/templates                        list template catalog
- GET  /api/templates/<template_id>          template detail (with variable schema)
- POST /api/templates/<template_id>/render   render YAML preview
- POST /api/templates/<template_id>/save     render + save as playbook (GitOps)

Instances (saved template configs stored under repo/templates/*.config.json):
- GET  /api/templates/instances              list all saved instances
- GET  /api/templates/instances/detail       get one instance (?path=... or ?env=&name=&template_id=)
- DEL  /api/templates/instances              delete instance + associated playbook
- GET  /api/templates/instances/history      git log for the instance config file
- GET  /api/templates/instances/version      instance config at a specific commit sha
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

from templates import list_templates, get_template, render_template

logger = logging.getLogger(__name__)
bp = Blueprint("templates_api", __name__, url_prefix="/api/templates")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project_id() -> Optional[str]:
    pid = request.headers.get("X-Project-Id")
    if pid:
        return pid
    body = request.get_json(silent=True) or {}
    return body.get("project_id") or request.args.get("project_id")


def _projects_dir() -> Optional[Path]:
    app_mod = next(
        (m for m in (sys.modules.get("__main__"), sys.modules.get("app")) if getattr(m, "PROJECTS_DIR", None)),
        None,
    )
    if not app_mod:
        return None
    return getattr(app_mod, "PROJECTS_DIR", None)



def _repo_root() -> Optional[Path]:
    pid = _project_id()
    pdir = _projects_dir()
    if not pid or not pdir:
        return None
    return pdir / pid / "repo"


def _instances_dir() -> Optional[Path]:
    """Persistent storage for template instance sidecars.

    Kept OUTSIDE the git-managed ``repo/`` tree so git-sync (which wipes
    ``repo/`` on pull) can never erase saved Build & Deployment Jobs.
    Auto-migrates any legacy files still living in ``repo/templates/`` and
    REMOVES the legacy source so subsequent calls (or a git-sync round-trip)
    cannot resurrect a template instance that the user just deleted.
    """
    pid = _project_id()
    pdir = _projects_dir()
    if not pid or not pdir:
        return None
    d = pdir / pid / "data" / "template-instances"
    d.mkdir(parents=True, exist_ok=True)
    # Destructive migration from legacy repo/templates/*.config.json.
    legacy = pdir / pid / "repo" / "templates"
    if legacy.exists() and legacy.is_dir():
        try:
            for src in legacy.glob("*.config.json"):
                dst = d / src.name
                if not dst.exists():
                    try:
                        dst.write_bytes(src.read_bytes())
                    except Exception:
                        logger.exception("failed migrating %s", src)
                        continue
                # Remove legacy copy so it cannot be re-migrated after a delete
                # or restored by a stale git pull.
                try:
                    src.unlink()
                except OSError:
                    pass
        except Exception:
            logger.exception("legacy template-instance migration failed")
    return d


def _slug(text: str, fallback: str = "x") -> str:
    s = re.sub(r"[^a-zA-Z0-9\-_]+", "-", str(text or "").strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or fallback


def _sanitize_env(env: Optional[str]) -> str:
    """Empty / 'default' → '' (flat layout). Otherwise slug."""
    if not env or str(env).strip().lower() in ("", "default", "none", "-"):
        return ""
    return _slug(env, "default")


def _sanitize_filename(name: str) -> str:
    n = re.sub(r"[^a-zA-Z0-9\-_.]+", "-", (name or "").strip()) or "template.yml"
    if not n.endswith((".yml", ".yaml")):
        n += ".yml"
    return n


def _playbook_filename(env: str, base_name: str) -> str:
    """Playbook stays flat under repo/playbooks/ so the runner can find it."""
    base = _sanitize_filename(base_name)
    if env:
        return f"{env}-{base}"
    return base


def _config_filename(env: str, playbook_filename: str, template_id: str) -> str:
    stem = playbook_filename.rsplit(".", 1)[0]
    return f"{stem}.{template_id}.config.json"


def _safe_repo_path(repo_root: Path, rel: Optional[str]) -> Optional[Path]:
    if not rel:
        return None
    safe_rel = str(rel).lstrip("/").replace("..", "_")
    path = repo_root / safe_rel
    try:
        path.relative_to(repo_root)
    except ValueError:
        return None
    return path


def _is_stale_rendered_k8s_yaml(template_id: str, yaml_text: str) -> bool:
    """Detect saved Kubernetes template YAML from older broken HA-join renders."""
    if not isinstance(yaml_text, str):
        return False
    if template_id == "k3s-bootstrap":
        return "# OpenSible k3s template generation: 2026-07-k3s-hardened-v10" not in yaml_text
    if template_id != "k8s-cluster":
        return False
    if "# OpenSible k8s template generation: 2026-07-cgroup-fix-v7" not in yaml_text:
        return True
    return any(marker in yaml_text for marker in (
        "Wait for first control-plane apiserver to be reachable",
        "Probe first control-plane /healthz",
        "https://{{ first_cp_ip }}:6443/healthz",
        "Generate fresh worker join command",
        "Generate fresh worker join token on first control-plane",
        "delegate_to: \"{{ first_cp_ip }}\"",
        "--kubeconfig=/etc/kubernetes/admin.conf",
        "Build control-plane join argv (using first control-plane credentials)",
        "Build worker join argv (using first control-plane token)",
        "kubeadm_control_plane_join_argv",
        "kubeadm_worker_join_argv",
        "Reset dirty kubeadm/container runtime state",
        "rm -rf /etc/kubernetes /var/lib/etcd /etc/cni/net.d /var/lib/cni /var/run/calico /root/.kube/config",
    ))


def _dedupe_instance_filename(repo_root: Path, playbook_filename: str, template_id: str) -> str:
    """Return a filename that will not overwrite an existing playbook/instance."""
    pb_dir = repo_root / "playbooks"
    cfg_dir = _instances_dir() or (repo_root / "templates")
    if "." in playbook_filename:
        stem, ext = playbook_filename.rsplit(".", 1)
        ext = f".{ext}"
    else:
        stem, ext = playbook_filename, ".yml"
    candidate = playbook_filename
    idx = 2
    while (pb_dir / candidate).exists() or (cfg_dir / _config_filename("", candidate, template_id)).exists():
        candidate = f"{stem}-{idx}{ext}"
        idx += 1
    return candidate


# In-process mtime-keyed cache for parsed instance JSON files.
# Prevents re-parsing the same file on every list/detail/table poll.
# Keyed by (absolute path, mtime_ns, size) so any on-disk change auto-invalidates.
_CONFIG_CACHE: "dict[tuple[str, int, int], Dict[str, Any]]" = {}
_CONFIG_CACHE_MAX = 512


def _read_config(path: Path) -> Optional[Dict[str, Any]]:
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    cached = _CONFIG_CACHE.get(key)
    if cached is not None:
        # Return a shallow copy so callers that mutate (e.g. add `path`,
        # `playbook_id`) don't poison the cache entry.
        return dict(cached)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("updated_at", int(st.st_mtime * 1000))
    # Simple bounded cache — drop oldest half when full.
    if len(_CONFIG_CACHE) >= _CONFIG_CACHE_MAX:
        for k in list(_CONFIG_CACHE.keys())[: _CONFIG_CACHE_MAX // 2]:
            _CONFIG_CACHE.pop(k, None)
    _CONFIG_CACHE[key] = dict(data)
    return data


def _instance_id(cfg_basename: str) -> str:
    """Stable, opaque ID for an instance URL — hides the .config.json filename."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"template-instance:{cfg_basename}"))


def _instance_summary(cfg_path: Path, repo_root: Path) -> Dict[str, Any]:
    data = _read_config(cfg_path) or {}
    # Canonical `path` is the basename — instances no longer live under repo/.
    rel = cfg_path.name
    pb_name = data.get("filename") or ""
    yaml_stem = pb_name.rsplit(".", 1)[0] if pb_name else ""
    pid = _project_id() or ""
    playbook_id = (
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"playbook:{pid}:{yaml_stem}"))
        if yaml_stem and pid else None
    )
    return {
        "id": _instance_id(rel),
        "path": rel,
        "filename": pb_name or None,
        "template_id": data.get("template_id"),
        "environment": data.get("environment") or "default",
        "updated_at": data.get("updated_at"),
        "playbook_id": playbook_id,
    }


def _git_run(cwd: Path, args: List[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

@bp.route("", methods=["GET"])
def list_():
    return jsonify({"templates": list_templates()})


@bp.route("/<template_id>", methods=["GET"])
def detail(template_id: str):
    t = get_template(template_id)
    if not t:
        return jsonify({"error": "not found"}), 404
    return jsonify(t)


@bp.route("/<template_id>/render", methods=["POST"])
def render(template_id: str):
    body = request.get_json(silent=True) or {}
    values = body.get("values") or {}
    targets = body.get("targets") or {}
    try:
        return jsonify(render_template(template_id, values, targets))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("render failed")
        return jsonify({"error": str(e)}), 500


@bp.route("/<template_id>/save", methods=["POST"])
def save(template_id: str):
    body = request.get_json(silent=True) or {}
    values = body.get("values") or {}
    targets = body.get("targets") or {}
    environment = _sanitize_env(body.get("environment"))
    # Optional: user-edited YAML from the Rendered playbook editor. When
    # provided (non-empty string), we write it verbatim instead of re-rendering
    # from the template — so manual edits are actually persisted.
    edited_yaml_raw = body.get("yaml")
    edited_yaml = edited_yaml_raw.strip() if isinstance(edited_yaml_raw, str) else ""
    repo_root = _repo_root()
    if repo_root is None:
        return jsonify({"error": "project_id is required"}), 400

    try:
        result = render_template(template_id, values, targets)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Base filename (env prefix applied so instances across envs never collide)
    requested = body.get("filename") or result["filename"]
    pb_filename = _playbook_filename(environment, requested)
    pb_dir = repo_root / "playbooks"
    pb_dir.mkdir(parents=True, exist_ok=True)

    # If editing an existing saved instance and keeping the same filename, update it.
    # Otherwise protect saved instances/playbooks from accidental overwrite by
    # assigning the next free suffix (foo-2.yml, foo-3.yml, ...).
    cfg_root = _instances_dir()
    # Legacy: instance_path may point at old repo/templates/*. Accept it for
    # "editing same instance" detection.
    requested_instance_path = _safe_repo_path(repo_root, body.get("instance_path") or body.get("path"))
    initial_cfg_name = _config_filename(environment, pb_filename, template_id)
    initial_cfg_path = (cfg_root / initial_cfg_name) if cfg_root else (repo_root / "templates" / initial_cfg_name)
    # Also accept a bare basename from the frontend for the new layout.
    _basename_path = None
    _requested_raw = body.get("instance_path") or body.get("path")
    if cfg_root and _requested_raw and "/" not in str(_requested_raw):
        _basename_path = cfg_root / str(_requested_raw)
    is_same_saved_instance = bool(
        (requested_instance_path
         and requested_instance_path.exists()
         and requested_instance_path.resolve() == initial_cfg_path.resolve())
        or (_basename_path
            and _basename_path.exists()
            and _basename_path.resolve() == initial_cfg_path.resolve())
    )
    if not is_same_saved_instance:
        pb_filename = _dedupe_instance_filename(repo_root, pb_filename, template_id)
    pb_path = pb_dir / pb_filename

    # Choose what to write: user edits win over freshly rendered YAML.
    yaml_to_write = edited_yaml if edited_yaml and not _is_stale_rendered_k8s_yaml(template_id, edited_yaml) else result["yaml"]

    written: List[str] = []
    try:
        pb_path.write_text(yaml_to_write, encoding="utf-8")

        written.append(str(pb_path.relative_to(repo_root)))

        # Sidecar files declared by the template (e.g. inventories/<cluster>.yml).
        # If env is set and sidecar path starts with a well-known dir, insert env
        # subfolder to keep instances isolated.
        for rel_path, content in (result.get("sidecars") or {}).items():
            safe_rel = rel_path.lstrip("/").replace("..", "_")
            parts = safe_rel.split("/", 1)
            if environment and len(parts) == 2 and parts[0] in ("inventories", "group_vars", "host_vars"):
                safe_rel = f"{parts[0]}/{environment}/{parts[1]}"
            side = repo_root / safe_rel
            side.parent.mkdir(parents=True, exist_ok=True)
            side.write_text(content, encoding="utf-8")
            written.append(str(side.relative_to(repo_root)))

        # Config sidecar (the "instance" — persisted OUTSIDE repo/ so git-sync
        # can never wipe it on pull).
        cfg_dir = _instances_dir() or (repo_root / "templates")
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_name = _config_filename(environment, pb_filename, template_id)
        cfg_path = cfg_dir / cfg_name
        cfg_payload = {
            "template_id": template_id,
            "environment": environment or "default",
            "filename": pb_filename,
            "values": values,
            "targets": targets,
            # Persist the exact YAML that was written so re-opening the
            # instance restores the user's edits instead of re-rendering
            # from the default template.
            "rendered_yaml": yaml_to_write,
        }

        cfg_path.write_text(json.dumps(cfg_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append(cfg_path.name)
    except Exception as e:
        logger.exception("save template failed")
        return jsonify({"error": str(e)}), 500

    # PlaybookStorage.list_playbooks derives YAML-only ids by passing the
    # filename stem as `playbook_name` into PlaybookParser.parse, which then
    # uses it verbatim (it only falls back to the first play's `name` when no
    # name argument is provided). Mirror the same derivation here so the
    # caller can POST /playbooks/<id>/run right after Save & Run.
    pid = _project_id()
    yaml_stem = pb_filename.rsplit(".", 1)[0]
    playbook_name = yaml_stem
    playbook_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"playbook:{pid}:{playbook_name}"))



    return jsonify({
        "ok": True,
        "filename": pb_filename,
        "path": str(pb_path),
        "instance_path": cfg_path.name,
        "environment": environment or "default",
        "written": written,
        "template_id": template_id,
        "playbook_id": playbook_id,
    })


# ---------------------------------------------------------------------------
# Instances
# ---------------------------------------------------------------------------

@bp.route("/instances", methods=["GET"])
def list_instances():
    repo_root = _repo_root()
    cfg_dir = _instances_dir()
    if cfg_dir is None or not cfg_dir.exists():
        return jsonify({"instances": []})
    items: List[Dict[str, Any]] = []
    for p in sorted(cfg_dir.glob("*.config.json")):
        items.append(_instance_summary(p, repo_root or cfg_dir))
    # newest first
    items.sort(key=lambda x: x.get("updated_at") or 0, reverse=True)
    return jsonify({"instances": items})


def _resolve_instance_path() -> Optional[Path]:
    """Locate an instance config file.

    Accepts either the canonical basename or (for backwards compatibility)
    a repo-relative path like ``templates/xxx.config.json``. Also falls back
    to the legacy repo/templates/ location for old records.
    """
    cfg_dir = _instances_dir()
    repo_root = _repo_root()
    # Prefer opaque stable id when provided.
    iid = request.args.get("id")
    if iid and cfg_dir and cfg_dir.exists():
        for p in cfg_dir.glob("*.config.json"):
            if _instance_id(p.name) == iid:
                return p
        # fall through to path-based lookup below if id didn't match
    rel = request.args.get("path")
    if rel:
        rel_clean = rel.lstrip("/").replace("..", "_")
        # 1) basename in the new persistent dir
        if cfg_dir:
            cand = cfg_dir / Path(rel_clean).name
            if cand.exists() and cand.is_file():
                return cand
        # 2) legacy repo-relative path
        if repo_root:
            cand = repo_root / rel_clean
            if cand.exists() and cand.is_file():
                return cand
        return None
    env = _sanitize_env(request.args.get("env") or request.args.get("environment"))
    name = request.args.get("filename") or request.args.get("name")
    tid = request.args.get("template_id")
    if name and tid:
        pb_name = _playbook_filename(env, name)
        cfg_name = _config_filename(env, pb_name, tid)
        if cfg_dir:
            cand = cfg_dir / cfg_name
            if cand.exists():
                return cand
        if repo_root:
            legacy = repo_root / "templates" / cfg_name
            if legacy.exists():
                return legacy
    return None


def _instance_rel(p: Path) -> str:
    """Return the canonical `path` string for an instance file."""
    cfg_dir = _instances_dir()
    if cfg_dir and p.parent.resolve() == cfg_dir.resolve():
        return p.name
    repo_root = _repo_root()
    if repo_root:
        try:
            return str(p.relative_to(repo_root))
        except ValueError:
            pass
    return p.name


@bp.route("/instances/detail", methods=["GET"])
def instance_detail():
    p = _resolve_instance_path()
    if not p:
        return jsonify({"error": "not found"}), 404
    data = _read_config(p)
    if not data:
        return jsonify({"error": "invalid config"}), 400
    data["path"] = _instance_rel(p)
    data["id"] = _instance_id(p.name)
    # Include playbook_id so the UI can call /playbooks/<id>/run directly.
    pb_name = data.get("filename") or ""
    yaml_stem = pb_name.rsplit(".", 1)[0] if pb_name else ""
    pid = _project_id() or ""
    if yaml_stem and pid:
        data["playbook_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"playbook:{pid}:{yaml_stem}"))
    # Fallback: if the instance was saved before rendered_yaml was persisted,
    # read the current playbook file from disk so the editor still loads what
    # was last saved (not a fresh re-render from the template default).
    if not data.get("rendered_yaml") and pb_name:
        repo_root = _repo_root()
        if repo_root:
            pb_path = repo_root / "playbooks" / pb_name
            try:
                if pb_path.exists():
                    data["rendered_yaml"] = pb_path.read_text(encoding="utf-8")
            except Exception:
                pass
    return jsonify(data)



@bp.route("/instances", methods=["DELETE"])
def delete_instance():
    p = _resolve_instance_path()
    repo_root = _repo_root()
    if not p:
        return jsonify({"error": "not found"}), 404
    data = _read_config(p) or {}
    pb_name = data.get("filename")
    removed: List[str] = []
    try:
        # Saved instances were moved from repo/templates/ to the persistent
        # data/template-instances/ directory.  _instances_dir() auto-migrates
        # legacy repo/templates/*.config.json files into the new location, so
        # deleting only the new copy makes the row come back on the next list
        # refresh.  Remove every copy with the same config basename.
        cfg_dir = _instances_dir()
        config_candidates: List[Path] = []
        if cfg_dir:
            config_candidates.append(cfg_dir / p.name)
        if repo_root:
            config_candidates.append(repo_root / "templates" / p.name)
        config_candidates.append(p)

        seen_configs = set()
        for cfg in config_candidates:
            try:
                cfg_key = str(cfg.resolve())
            except Exception:
                cfg_key = str(cfg)
            if cfg_key in seen_configs:
                continue
            seen_configs.add(cfg_key)
            if cfg.exists() and cfg.is_file():
                rel = _instance_rel(cfg)
                cfg.unlink()
                removed.append(rel)

        if pb_name and repo_root:
            pb = repo_root / "playbooks" / pb_name
            if pb.exists():
                pb.unlink()
                try:
                    removed.append(str(pb.relative_to(repo_root)))
                except ValueError:
                    removed.append(pb.name)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "removed": removed})


@bp.route("/instances/history", methods=["GET"])
def instance_history():
    p = _resolve_instance_path()
    repo_root = _repo_root()
    if not p:
        return jsonify({"commits": [], "error": "not found"}), 404
    # Instances now live outside repo/ — git history only exists for legacy
    # files that are still under repo_root.
    try:
        rel = str(p.relative_to(repo_root)) if repo_root else None
    except ValueError:
        rel = None
    if not rel:
        return jsonify({"commits": [], "path": _instance_rel(p)})
    try:
        fmt = "%H%x1f%an%x1f%aI%x1f%s"
        res = _git_run(repo_root, ["log", f"--format={fmt}", "--follow", "--", rel])
    except Exception as e:
        return jsonify({"commits": [], "error": str(e)})
    commits: List[Dict[str, Any]] = []
    if res.returncode == 0:
        for line in res.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                commits.append({
                    "sha": parts[0], "author": parts[1],
                    "date": parts[2], "message": parts[3],
                })
    return jsonify({"commits": commits, "path": rel})


@bp.route("/instances/version", methods=["GET"])
def instance_version():
    p = _resolve_instance_path()
    repo_root = _repo_root()
    sha = (request.args.get("sha") or "").strip()
    if not p or not sha:
        return jsonify({"error": "path and sha required"}), 400
    if not re.fullmatch(r"[0-9a-fA-F]{4,64}", sha):
        return jsonify({"error": "invalid sha"}), 400
    try:
        rel = str(p.relative_to(repo_root)) if repo_root else None
    except ValueError:
        rel = None
    if not rel:
        return jsonify({"error": "history not tracked (instance is stored outside git repo)"}), 404
    try:
        res = _git_run(repo_root, ["show", f"{sha}:{rel}"])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if res.returncode != 0:
        return jsonify({"error": res.stderr.strip() or "git show failed"}), 404
    try:
        data = json.loads(res.stdout)
    except Exception:
        return jsonify({"error": "content is not valid JSON"}), 400
    return jsonify({"sha": sha, "path": rel, "config": data})

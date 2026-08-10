"""
Cloud Provisioning — OpenTofu/Terraform stack management.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

try:
    from auth.middleware import require_auth
    from utils.secret_encryption import get_encryption
except ImportError:  # pragma: no cover
    from auth.middleware import require_auth
    from utils.secret_encryption import get_encryption


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_THIS = Path(__file__).resolve()
BASE_DIR = _THIS.parent.parent
IAC_BYTEDC_DIR = BASE_DIR / "IaC" / "opentofu-bytedc"
IAC_HETZNER_DIR = BASE_DIR / "IaC" / "opentofu-hetzner"
IAC_CLOUDFLARE_DIR = BASE_DIR / "IaC" / "opentofu-cloudflare"
IAC_AWS_DIR = BASE_DIR / "IaC" / "opentofu-aws"
IAC_EKS_DIR = BASE_DIR / "IaC" / "opentofu-eks"
IAC_GCP_DIR = BASE_DIR / "IaC" / "opentofu-gcp"
IAC_GKE_DIR = BASE_DIR / "IaC" / "opentofu-gke"
IAC_KUBERNETES_DIR = BASE_DIR / "IaC" / "opentofu-kubernetes"
GLOBAL_ENVS_DIR = IAC_BYTEDC_DIR / "envs"
GLOBAL_TEMPLATE_DIR = GLOBAL_ENVS_DIR / "_template"
# Per-provider IaC roots. Keyed by the provider id stored in meta.json.
PROVIDER_IAC_DIRS: Dict[str, Path] = {
    "bytedc":     IAC_BYTEDC_DIR,
    "hetzner":    IAC_HETZNER_DIR,
    "cloudflare": IAC_CLOUDFLARE_DIR,
    "aws":        IAC_AWS_DIR,
    "eks":        IAC_EKS_DIR,
    "gcp":        IAC_GCP_DIR,
    "gke":        IAC_GKE_DIR,
    "kubernetes": IAC_KUBERNETES_DIR,
}
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
PROJECTS_DIR = DATA_DIR / "projects"
LEGACY_DATA_BASE = DATA_DIR / "cloud-provisioning"
LEGACY_DATA_BASE.mkdir(parents=True, exist_ok=True)
# Persisted workspace used when no project id is supplied. Lives inside DATA_DIR
# (mounted volume) so stacks survive container rebuilds — the in-image
# IaC/opentofu-*/envs/ trees are read-only template content only.
LEGACY_STACKS_ROOT = LEGACY_DATA_BASE / "default"
(LEGACY_STACKS_ROOT / "envs").mkdir(parents=True, exist_ok=True)

def _migrate_in_image_stacks_once() -> None:
    """One-time migration: copy any stacks that previously landed in the
    in-image IaC/opentofu-bytedc/envs/ tree (lost on container rebuild) into
    the persisted DATA_DIR workspace. Skips _template and existing entries."""
    marker = LEGACY_STACKS_ROOT / ".migrated_from_image"
    if marker.exists() or not GLOBAL_ENVS_DIR.exists():
        return
    dest_envs = LEGACY_STACKS_ROOT / "envs"
    try:
        for item in GLOBAL_ENVS_DIR.iterdir():
            if not item.is_dir() or item.name == "_template":
                continue
            target = dest_envs / item.name
            if target.exists():
                continue
            try:
                shutil.copytree(item, target)
            except Exception:
                pass
        marker.write_text("ok")
    except Exception:
        pass

_LOCAL_BACKEND_TF = '''terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
'''

def _migrate_empty_s3_backend() -> None:
    """Rewrite any backend.tf that uses an empty `backend "s3" {}` block to the
    local backend so `tofu init` works without S3/OBS configuration. Covers the
    in-image template, the per-workspace seeded template, and existing stacks."""
    roots = [GLOBAL_ENVS_DIR, LEGACY_STACKS_ROOT / "envs", PROJECTS_DIR]
    for root in roots:
        if not root.exists():
            continue
        for bt in root.rglob("backend.tf"):
            try:
                txt = bt.read_text()
            except Exception:
                continue
            # Match an empty s3 backend block (whitespace only inside braces).
            if re.search(r'backend\s+"s3"\s*\{\s*\}', txt):
                try:
                    bt.write_text(_LOCAL_BACKEND_TF)
                except Exception:
                    pass

_migrate_in_image_stacks_once()
_migrate_empty_s3_backend()


# Template files (relative to envs/<stack>/) that are owned by the platform
# and refreshed from the in-image _template on every stack write / run, so
# upstream module/variable changes always reach existing stacks. Per-stack
# files (terraform.tfvars, backend.hcl, credentials.*, state) are preserved.
_TEMPLATE_OWNED_FILES = {"main.tf", "variables.tf", "providers.tf", "versions.tf",
                          "backend.tf", "README.md", "credentials.auto.tfvars.example"}


def _provider_iac_dir(provider: str) -> Path:
    return PROVIDER_IAC_DIRS.get(provider or "bytedc", IAC_BYTEDC_DIR)


def _provider_template_dir(provider: str) -> Path:
    return _provider_iac_dir(provider) / "envs" / "_template"


def _sync_iac_assets(project_id: Optional[str], provider: str = "bytedc") -> None:
    """Refresh the per-workspace `modules/` tree and `_template/` from the
    in-image IaC so module/source/variable changes propagate. Also refreshes
    platform-owned .tf files in each existing stack directory of the same
    provider."""
    root = _project_stacks_root(project_id)
    root.mkdir(parents=True, exist_ok=True)

    src_iac = _provider_iac_dir(provider)
    src_tpl = _provider_template_dir(provider)

    # 1. modules/ — copy fresh into a per-provider directory so bytedc and
    #    hetzner modules don't collide. Stack .tf files reference
    #    `../../modules/<name>` for backwards compat with ByteDC layouts.
    src_mods = src_iac / "modules"
    if src_mods.exists():
        dst_mods = root / "modules"
        try:
            if dst_mods.exists():
                shutil.rmtree(dst_mods)
            shutil.copytree(src_mods, dst_mods)
        except Exception:
            pass

    # 2. envs/_template/ — copy fresh (overwrites); one template shared per
    #    workspace (bytedc). For Hetzner we don't seed a workspace _template
    #    since new hetzner stacks pull directly from the in-image template.
    if provider == "bytedc" and src_tpl.exists():
        dst_tpl = root / "envs" / "_template"
        try:
            if dst_tpl.exists():
                shutil.rmtree(dst_tpl)
            shutil.copytree(src_tpl, dst_tpl)
        except Exception:
            pass

    # 3. Refresh platform-owned .tf files in each existing stack of this provider.
    envs = root / "envs"
    if envs.exists() and src_tpl.exists():
        for stack in envs.iterdir():
            if not stack.is_dir() or stack.name.startswith(".") or stack.name == "_template":
                continue
            # Only refresh stacks matching this provider (via meta.json).
            stack_provider = _read_stack_provider(project_id, stack.name) or "bytedc"
            if stack_provider != provider:
                continue
            for fname in _TEMPLATE_OWNED_FILES:
                src = src_tpl / fname
                if not src.exists():
                    continue
                try:
                    shutil.copy2(src, stack / fname)
                except Exception:
                    pass

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,48}[a-z0-9]$")

# In-memory run registry: run_id -> {stack, action, status, log_path, started_at, finished_at, returncode}
_RUNS: Dict[str, Dict[str, Any]] = {}
_RUN_LOCK = threading.Lock()


def _get_project_id() -> Optional[str]:
    """Resolve current project id from header or query string."""
    try:
        pid = (request.headers.get("X-Project-Id")
               or request.args.get("project_id")
               or "").strip()
        return pid or None
    except RuntimeError:
        # Outside request context (e.g. background thread) — caller passes explicitly.
        return None


def _project_stacks_root(project_id: Optional[str]) -> Path:
    """Per-project synced workspace root: data/projects/<id>/stacks/.
    With no project id, falls back to DATA_DIR/cloud-provisioning/default so
    stacks persist across container restarts (the in-image IaC/ dir does not)."""
    if project_id:
        return PROJECTS_DIR / project_id / "stacks"
    return LEGACY_STACKS_ROOT


def _envs_dir(project_id: Optional[str]) -> Path:
    """Envs directory: <stacks-root>/envs/  (per-project, mirrors OpenTofu layout)."""
    root = _project_stacks_root(project_id)
    envs = root / "envs"
    envs.mkdir(parents=True, exist_ok=True)
    # Seed the workspace _template from the in-image template the first time
    # it's used so the wizard can scaffold new stacks.
    tmpl = envs / "_template"
    if not tmpl.exists() and GLOBAL_TEMPLATE_DIR.exists():
        try:
            shutil.copytree(GLOBAL_TEMPLATE_DIR, tmpl)
        except Exception:
            pass
    return envs


def _template_dir(project_id: Optional[str], provider: str = "bytedc") -> Path:
    """Template dir for a provider. For ByteDC uses per-workspace copy when
    present; for other providers reads straight from the in-image IaC."""
    if provider == "bytedc":
        per = _envs_dir(project_id) / "_template"
        if per.exists():
            return per
        return GLOBAL_TEMPLATE_DIR
    return _provider_template_dir(provider)


def _read_stack_provider(project_id: Optional[str], name: str) -> Optional[str]:
    """Read the provider id from a stack's meta.json without importing heavy helpers."""
    try:
        p = _data_base(project_id) / name / "meta.json"
        if not p.exists():
            return None
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("provider")
    except Exception:
        return None


def _data_base(project_id: Optional[str]) -> Path:
    """Per-project secrets/meta/runs storage."""
    if project_id:
        p = PROJECTS_DIR / project_id / ".cloud-provisioning"
    else:
        p = LEGACY_DATA_BASE
    p.mkdir(parents=True, exist_ok=True)
    return p



# ---------------------------------------------------------------------------
# Provider catalog — sourced from backend.services.cloud_providers registry.
# Add a new provider by dropping a module under cloud_providers/ and
# registering it in cloud_providers/__init__.py. No edits needed here.
# ---------------------------------------------------------------------------

from services import cloud_providers as _providers  # noqa: E402

PROVIDERS = _providers.catalog()





# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_name(name: str) -> bool:
    return bool(name) and bool(NAME_RE.match(name)) and name != "_template"


_NET_REUSE_KEYS = (
    "existing_vpc_id",
    "existing_public_subnet_id", "existing_app_subnet_id", "existing_data_subnet_id",
    "existing_public_ipv4_subnet_id", "existing_app_ipv4_subnet_id", "existing_data_ipv4_subnet_id",
    "existing_app_sg_id", "existing_data_sg_id",
)
_NAT_REUSE_KEYS = (
    "existing_nat_gateway_id", "create_nat_in_existing_vpc",
    "manage_existing_nat_snat_rules", "nat_floating_ip_id",
)


def _apply_reuse_toggles(values: Dict[str, Any]) -> None:
    """Clear reuse fields when their master toggle is off, so an accidentally
    filled ID never leaks into the tfvars once the user disables the section."""
    if not values.get("use_existing_network"):
        for k in _NET_REUSE_KEYS:
            values[k] = ""
    if not values.get("use_existing_nat"):
        for k in _NAT_REUSE_KEYS:
            if k in ("create_nat_in_existing_vpc", "manage_existing_nat_snat_rules"):
                values[k] = False
            else:
                values[k] = ""


def _validate_network_reuse(values: Dict[str, Any]) -> Optional[str]:
    """When reusing an existing VPC, require all three subnet IDs (and ELB IPv4 subnets if ELB is enabled)."""
    if not values.get("use_existing_network"):
        return None
    vpc = (values.get("existing_vpc_id") or "").strip()
    if not vpc:
        return "Reuse existing VPC is enabled — please provide 'Existing VPC ID' (or turn the toggle off)."
    missing = [k for k in ("existing_public_subnet_id", "existing_app_subnet_id", "existing_data_subnet_id")
               if not (values.get(k) or "").strip()]
    if missing:
        return ("When reusing an existing VPC, you must also provide: "
                + ", ".join(missing)
                + ". Otherwise new subnets will be created inside the existing VPC and may collide with your CIDRs.")
    if values.get("enable_elb"):
        missing_v4 = [k for k in ("existing_public_ipv4_subnet_id", "existing_app_ipv4_subnet_id")
                      if not (values.get(k) or "").strip()]
        if missing_v4:
            return ("ELB is enabled while reusing an existing VPC — also fill: "
                    + ", ".join(missing_v4) + " (neutron IPv4 subnet IDs from the ByteDC console).")
    if values.get("use_existing_nat") and values.get("enable_nat") and (values.get("existing_nat_gateway_id") or "").strip():
        if values.get("manage_existing_nat_snat_rules") and not (values.get("nat_floating_ip_id") or "").strip():
            return "To manage SNAT rules on an existing NAT gateway, provide NAT EIP ID as well."
    return None


def _stack_dir(project_id: Optional[str], name: str) -> Path:
    return _envs_dir(project_id) / name


def _stack_data_dir(project_id: Optional[str], name: str) -> Path:
    p = _data_base(project_id) / name
    p.mkdir(parents=True, exist_ok=True)
    return p



def _hcl_quote(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_render_value(x) for x in v) + "]"
    if isinstance(v, dict):
        parts = []
        for k, val in v.items():
            parts.append(f"  {k} = {_render_value(val)}")
        return "{\n" + "\n".join(parts) + "\n}"
    return _hcl_quote(str(v))


# Top-level keys (and their order) written to terraform.tfvars for ByteDC.
# tfvars ordering, secret keys, and platform_overrides whitelisting all live
# on the ProviderAdapter (see backend/services/cloud_providers/<id>.py). The
# helpers below just look up the current provider's adapter.

def _secret_keys_for(provider: str) -> tuple:
    return _providers.require(provider).secret_keys


def _all_secret_keys() -> tuple:
    return _providers.all_secret_keys()


def _render_tfvars(values: Dict[str, Any], provider: str = "bytedc") -> str:
    adapter = _providers.require(provider)
    values = adapter.sanitize_values(values)
    lines = ["# Cloud Provisioning UI — edit via the web UI.", ""]
    for key in adapter.tfvars_order:
        if key not in values:
            continue
        v = values[key]
        if v is None or v == "" or v == {} or v == []:
            continue
        lines.append(f"{key} = {_render_value(v)}")
    return "\n".join(lines) + "\n"



def _render_backend_hcl(stack: str) -> str:
    return (
        '# OpenTofu backend config — edit before `tofu init` to point at a remote state bucket.\n'
        'bucket = "REPLACE_ME_TFSTATE_BUCKET"\n'
        f'key    = "cloud-provisioning/{stack}.tfstate"\n'
        'region = ""\n'
    )


def _write_stack_files(project_id: Optional[str], name: str, values: Dict[str, Any], provider: str = "bytedc") -> None:
    # Always refresh modules + template-owned files first so upstream IaC
    # changes (new variables, fixed for_each, etc.) reach existing stacks.
    _sync_iac_assets(project_id, provider=provider)
    sd = _stack_dir(project_id, name)
    sd.mkdir(parents=True, exist_ok=True)
    tpl = _template_dir(project_id, provider=provider)

    # Seed from _template if available so module sources + versions resolve.
    if tpl.exists():
        for item in tpl.iterdir():
            if item.name in {"terraform.tfvars", "backend.hcl", "credentials.auto.tfvars",
                             "credentials.auto.tfvars.example", ".terraform", ".terraform.lock.hcl"}:
                continue
            dest = sd / item.name
            if dest.exists():
                # Refresh platform-owned files each write so upstream fixes propagate.
                if item.is_file() and item.name in _TEMPLATE_OWNED_FILES:
                    try:
                        shutil.copy2(item, dest)
                    except Exception:
                        pass
                continue
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

    (sd / "terraform.tfvars").write_text(_render_tfvars(values, provider=provider), encoding="utf-8")
    backend_path = sd / "backend.hcl"
    if not backend_path.exists():
        backend_path.write_text(_render_backend_hcl(name), encoding="utf-8")


def _save_secrets(project_id: Optional[str], name: str, secrets_map: Dict[str, str]) -> None:
    enc = get_encryption()
    payload = {k: enc.encrypt(v) for k, v in secrets_map.items() if v}
    out = _stack_data_dir(project_id, name) / "secrets.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass


def _load_secrets(project_id: Optional[str], name: str) -> Dict[str, str]:
    p = _stack_data_dir(project_id, name) / "secrets.json"
    if not p.exists():
        return {}
    enc = get_encryption()
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {k: enc.decrypt(v) for k, v in raw.items()}


def _materialise_credentials(project_id: Optional[str], name: str, provider: Optional[str] = None) -> Optional[Path]:
    """Write credentials.auto.tfvars for execution. Returns path or None."""
    secrets_map = _load_secrets(project_id, name)
    if not secrets_map:
        return None
    if provider is None:
        provider = _read_stack_provider(project_id, name) or "bytedc"
    sd = _stack_dir(project_id, name)
    sd.mkdir(parents=True, exist_ok=True)
    creds = sd / "credentials.auto.tfvars"
    body = []
    for k in _secret_keys_for(provider):
        if k not in secrets_map:
            continue
        # Kubernetes: kubeconfig secret is materialised as a file (kubeconfig.yaml)
        # instead of a tfvar, because the kubernetes/helm providers read it
        # via config_path before any resource is planned.
        if provider == "kubernetes" and k == "kubeconfig":
            kc = sd / "kubeconfig.yaml"
            try:
                kc.write_text(secrets_map[k], encoding="utf-8")
                os.chmod(kc, 0o600)
            except OSError:
                pass
            continue
        val = secrets_map[k]
        if isinstance(val, str):
            # Trim surrounding whitespace/newlines from pasted secrets so
            # they don't corrupt cloud provider auth headers (e.g. AWS SigV4).
            val = val.strip()
        if not val:
            continue
        body.append(f"{k} = {_hcl_quote(val)}")
    creds.write_text("\n".join(body) + "\n", encoding="utf-8")
    try:
        os.chmod(creds, 0o600)
    except OSError:
        pass
    return creds


def _latest_run_by_stack(project_id: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """Return {stack_name: latest_run_dict} by scanning recent execution files."""
    latest: Dict[str, Dict[str, Any]] = {}
    ex_dir = _project_executions_dir(project_id)
    if not ex_dir.exists():
        return latest
    try:
        files = sorted(ex_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return latest
    for f in files[:500]:
        try:
            exe = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        rp = exe.get("runParams") or {}
        if rp.get("execution_type") != "TOFU_RUN":
            continue
        stack = rp.get("stack_name")
        if not stack or stack in latest:
            continue
        latest[stack] = {**_exec_to_run(exe), "mtime": int(f.stat().st_mtime)}
    return latest


_TFVARS_LOCATION_RE = re.compile(r'^\s*(?:location|region)\s*=\s*"([^"]*)"', re.MULTILINE)


def _list_stacks(project_id: Optional[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    envs = _envs_dir(project_id)
    if not envs.exists():
        return out
    latest_runs = _latest_run_by_stack(project_id)
    for entry in sorted(envs.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".") or entry.name == "_template":
            continue
        tfvars = entry / "terraform.tfvars"
        meta_path = _stack_data_dir(project_id, entry.name) / "meta.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        cloud_project = None
        region = None
        if tfvars.exists():
            try:
                text = tfvars.read_text(encoding="utf-8")
                m = _TFVARS_PROJECT_RE.search(text)
                if m: cloud_project = m.group(1)
                m2 = _TFVARS_LOCATION_RE.search(text)
                if m2: region = m2.group(1)
            except Exception:
                pass
        run = latest_runs.get(entry.name) or {}
        # Prefer live status from most recent execution over stale meta.json.
        last_action = run.get("action") or meta.get("last_action")
        last_status = run.get("status") or meta.get("last_status")
        out.append({
            "name": entry.name,
            "provider": meta.get("provider", "bytedc"),
            "env": meta.get("env"),
            "cloud_project": cloud_project or meta.get("project_name"),
            "region": region,
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at"),
            "last_action": last_action,
            "last_status": last_status,
            "last_run_id": run.get("run_id") or meta.get("last_run_id"),
            "last_run_finished_at": run.get("finished_at"),
            "has_tfvars": tfvars.exists(),
            "drift_enabled": meta.get("drift_enabled") is True,
            "drift_status": (_drift_status(project_id, entry.name)["status"]
                             if meta.get("drift_enabled") is True else "disabled"),
            "policy_enabled": meta.get("policy_enabled") is True,
        })
    return out


def _save_meta(project_id: Optional[str], name: str, **patch: Any) -> None:
    p = _stack_data_dir(project_id, name) / "meta.json"
    meta = {}
    if p.exists():
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    meta.update(patch)
    if "created_at" not in meta:
        meta["created_at"] = int(time.time())
    meta["updated_at"] = int(time.time())
    p.write_text(json.dumps(meta, indent=2), encoding="utf-8")




# ---------------------------------------------------------------------------
# Blueprint + routes
# ---------------------------------------------------------------------------

bp = Blueprint("cloud_provisioning", __name__, url_prefix="/api/cloud")


@bp.route("/providers", methods=["GET"])
@require_auth
def list_providers():
    return jsonify({"providers": PROVIDERS})


_PROVIDER_SCHEMAS: Dict[str, Dict[str, Any]] = _providers.schemas()


@bp.route("/bytedc/schema", methods=["GET"])
@require_auth
def bytedc_schema():
    # Legacy route kept for the older wizard frontend.
    return jsonify(_PROVIDER_SCHEMAS["bytedc"])


@bp.route("/<provider>/schema", methods=["GET"])
@require_auth
def provider_schema(provider):
    schema = _PROVIDER_SCHEMAS.get(provider)
    if not schema:
        return jsonify({"error": f"Unknown provider '{provider}'."}), 404
    return jsonify(schema)



@bp.route("/stacks", methods=["GET"])
@require_auth
def stacks_list():
    pid = _get_project_id()
    return jsonify({"stacks": _list_stacks(pid)})


@bp.route("/runs", methods=["GET"])
@require_auth
def all_runs_list():
    """Aggregate list of all OpenTofu (TOFU_RUN) executions for this project,
    across every stack. Powers the Provisioning Summary dashboard."""
    pid = _get_project_id()
    items: List[Dict[str, Any]] = []
    stack_info_cache: Dict[str, Dict[str, Any]] = {}
    ex_dir = _project_executions_dir(pid)
    if ex_dir.exists():
        files = sorted(ex_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files[:500]:
            try:
                exe = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            rp = exe.get("runParams") or {}
            if rp.get("execution_type") != "TOFU_RUN":
                continue
            run = _exec_to_run(exe)
            stack = run.get("stack")
            if stack:
                if stack not in stack_info_cache:
                    stack_info_cache[stack] = _stack_info(pid, stack)
                info = stack_info_cache[stack]
                run["env"] = info.get("env")
                run["cloud_project"] = info.get("cloud_project")
                run["provider"] = info.get("provider") or "bytedc"
            items.append({**run, "mtime": int(f.stat().st_mtime)})

            if len(items) >= 200:
                break
    return jsonify({"runs": items})


_TFVARS_PROJECT_RE = re.compile(r'^\s*project_name\s*=\s*"([^"]*)"', re.MULTILINE)


def _stack_info(project_id: Optional[str], name: str) -> Dict[str, Any]:
    """Lightweight (env, cloud_project, provider) lookup for a stack — for runs listing."""
    info: Dict[str, Any] = {"env": None, "cloud_project": None, "provider": None}
    try:
        meta_path = _stack_data_dir(project_id, name) / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            info["env"] = meta.get("env")
            info["cloud_project"] = meta.get("project_name")
            info["provider"] = meta.get("provider")

        if not info["cloud_project"]:
            tfvars = _stack_dir(project_id, name) / "terraform.tfvars"
            if tfvars.exists():
                m = _TFVARS_PROJECT_RE.search(tfvars.read_text(encoding="utf-8"))
                if m:
                    info["cloud_project"] = m.group(1)
        if not info["env"]:
            tfvars = _stack_dir(project_id, name) / "terraform.tfvars"
            if tfvars.exists():
                m = re.search(r'^\s*env\s*=\s*"([^"]*)"', tfvars.read_text(encoding="utf-8"), re.MULTILINE)
                if m:
                    info["env"] = m.group(1)
    except Exception:
        pass
    return info


@bp.route("/stacks", methods=["POST"])
@require_auth
def stacks_create():
    pid = _get_project_id()
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip().lower()
    provider = body.get("provider") or "bytedc"
    values = body.get("values") or {}
    if not _valid_name(name):
        return jsonify({"error": "Invalid stack name. Use lowercase letters, digits, '-' or '_' (3-50 chars)."}), 400
    if provider not in PROVIDER_IAC_DIRS:
        return jsonify({"error": f"Provider '{provider}' is not yet supported."}), 400
    if _stack_dir(pid, name).exists():
        return jsonify({"error": f"Stack '{name}' already exists."}), 409

    # ByteDC-specific network reuse validation.
    if provider == "bytedc":
        _apply_reuse_toggles(values)
        err = _validate_network_reuse(values)
        if err:
            return jsonify({"error": err}), 400

    # Separate secrets from plain values (secret keys are provider-scoped).
    all_secrets = _all_secret_keys()
    secrets_map = {k: values.pop(k) for k in list(values.keys()) if k in all_secrets}
    _write_stack_files(pid, name, values, provider=provider)
    _save_secrets(pid, name, secrets_map)
    _save_meta(pid, name, provider=provider, env=values.get("env"), project_name=values.get("project_name"))
    current_app.logger.info(f"[cloud] Stack '{name}' created (provider={provider}, project={pid}).")
    return jsonify({"ok": True, "name": name}), 201


@bp.route("/stacks/<name>", methods=["GET"])
@require_auth
def stacks_get(name):
    pid = _get_project_id()
    if not _valid_name(name) or not _stack_dir(pid, name).exists():
        return jsonify({"error": "Not found"}), 404
    sd = _stack_dir(pid, name)
    tfvars = (sd / "terraform.tfvars").read_text(encoding="utf-8") if (sd / "terraform.tfvars").exists() else ""
    backend = (sd / "backend.hcl").read_text(encoding="utf-8") if (sd / "backend.hcl").exists() else ""
    has_secrets = (_stack_data_dir(pid, name) / "secrets.json").exists()
    meta_path = _stack_data_dir(pid, name) / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    files = sorted([p.name for p in sd.iterdir() if p.is_file()])
    try:
        rel = str(sd.relative_to(BASE_DIR))
    except ValueError:
        rel = str(sd)
    return jsonify({
        "name": name,
        "path": rel,
        "files": files,
        "terraform_tfvars": tfvars,
        "backend_hcl": backend,
        "has_secrets": has_secrets,
        "meta": meta,
        "provider": meta.get("provider") or "bytedc",
        "drift": _drift_status(pid, name),
    })


@bp.route("/stacks/<name>", methods=["PUT"])
@require_auth
def stacks_update(name):
    pid = _get_project_id()
    if not _valid_name(name) or not _stack_dir(pid, name).exists():
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(silent=True) or {}
    values = body.get("values") or {}
    provider = _read_stack_provider(pid, name) or "bytedc"
    if provider == "bytedc":
        _apply_reuse_toggles(values)
        err = _validate_network_reuse(values)
        if err:
            return jsonify({"error": err}), 400
    all_secrets = _all_secret_keys()
    secrets_map = {k: values.pop(k) for k in list(values.keys()) if k in all_secrets}
    _write_stack_files(pid, name, values, provider=provider)
    if secrets_map:
        existing = _load_secrets(pid, name)
        existing.update(secrets_map)
        _save_secrets(pid, name, existing)
    _save_meta(pid, name, env=values.get("env"), project_name=values.get("project_name"))
    return jsonify({"ok": True, "name": name})


@bp.route("/stacks/<name>", methods=["DELETE"])
@require_auth
def stacks_delete(name):
    pid = _get_project_id()
    if not _valid_name(name) or not _stack_dir(pid, name).exists():
        return jsonify({"error": "Not found"}), 404
    force = request.args.get("force") in ("1", "true", "yes")
    state_file = _stack_dir(pid, name) / "terraform.tfstate"
    if state_file.exists() and not force:
        return jsonify({"error": "Local state present. Pass ?force=true to delete anyway."}), 409
    shutil.rmtree(_stack_dir(pid, name))
    sd = _data_base(pid) / name
    if sd.exists():
        shutil.rmtree(sd)
    return jsonify({"ok": True})




# ---- tofu execution (dispatched to workers, like Ansible) ------------------

_VALID_ACTIONS = {"init", "plan", "apply", "destroy", "validate", "fmt", "refresh", "drift"}


def _tofu_cmd(action: str) -> List[str]:
    if action == "init":
        return ["tofu", "init", "-input=false", "-no-color"]
    if action == "plan":
        return ["tofu", "plan", "-input=false", "-no-color", "-out=tfplan"]
    if action == "apply":
        return ["tofu", "apply", "-input=false", "-no-color", "-auto-approve"]
    if action == "destroy":
        return ["tofu", "destroy", "-input=false", "-no-color", "-auto-approve"]
    if action == "validate":
        return ["tofu", "validate", "-no-color"]
    if action == "fmt":
        return ["tofu", "fmt", "-recursive"]
    if action == "refresh":
        # apply -refresh-only updates state to match real-world resources
        # without changing infrastructure. Recovers from drift; will NOT
        # re-populate state that was deleted/lost.
        return ["tofu", "apply", "-refresh-only", "-input=false", "-no-color", "-auto-approve"]
    if action == "drift":
        # Read-only drift detection: refresh in-memory only and report whether
        # the real world still matches state. -detailed-exitcode yields
        # 0 = in sync, 2 = drift detected, 1 = error. Never writes state.
        return ["tofu", "plan", "-refresh-only", "-input=false", "-no-color", "-detailed-exitcode"]
    raise ValueError(action)



def _project_logs_dir(project_id: Optional[str]) -> Path:
    pid = project_id or "default"
    return PROJECTS_DIR / pid / "history" / "logs"


def _project_executions_dir(project_id: Optional[str]) -> Path:
    pid = project_id or "default"
    return PROJECTS_DIR / pid / "history" / "executions"


def _create_execution(project_id: Optional[str], stack: str, action: str, worker_id: Optional[str] = None, triggered_by: Optional[str] = None, triggered_by_user_id: Optional[str] = None) -> str:
    """Enqueue a TOFU_RUN execution that any online worker can claim."""
    import sys as _sys
    _app_mod = _sys.modules.get("app") or _sys.modules.get("__main__")
    create_execution_record = getattr(_app_mod, "create_execution_record", None)
    if create_execution_record is None:
        # Last-resort: load app.py by file path (works regardless of how the
        # package was imported — flat 'app', package 'app/__init__.py', etc.).
        import importlib.util as _ilu, pathlib as _pl
        _app_py = _pl.Path(__file__).resolve().parent.parent / "app.py"
        _spec = _ilu.spec_from_file_location("_backend_app", _app_py)
        _mod = _ilu.module_from_spec(_spec)  # type: ignore
        _spec.loader.exec_module(_mod)  # type: ignore
        create_execution_record = _mod.create_execution_record


    # Refresh modules/template before every run so upstream IaC fixes apply
    # without requiring users to delete & recreate stacks.
    provider = _read_stack_provider(project_id, stack) or "bytedc"
    _sync_iac_assets(project_id, provider=provider)
    sd = _stack_dir(project_id, stack)
    secrets_map = _load_secrets(project_id, stack)
    run_params = {
        "execution_type": "TOFU_RUN",
        "tofu_action": action,
        "stack_name": stack,
        "stack_dir": str(sd),
        "project_id": project_id,
        "provider": provider,
        "secrets": secrets_map,
        "secret_keys": list(_secret_keys_for(provider)),
        "env": {"TF_IN_AUTOMATION": "1"},
    }

    if _policy_enabled(project_id, stack) and action in ("plan", "apply", "destroy"):
        run_params["policy"] = _policy_config(project_id, stack)
    if not worker_id:

        try:
            from services.worker_registry import load_all_workers, is_worker_online
            candidates = []
            for wid, w in (load_all_workers() or {}).items():
                tags = [str(t).lower() for t in (w.get("tags") or [])]
                if ("local" in tags or "default" in tags) and is_worker_online(wid, ttl_seconds=60):
                    candidates.append(wid)
            if candidates:
                worker_id = candidates[0]
        except Exception as _e:
            current_app.logger.warning(f"[cloud] local-worker autoselect failed: {_e}")
    if worker_id:
        run_params["target_worker_id"] = worker_id
        run_params["requirements"] = {"worker_id": worker_id}
    data = {
        "status": "QUEUED",
        "playbookName": f"tofu {action} · {stack}",
        "mode": "TOFU",
        "runName": f"{stack}/{action}",
        "tag": "tofu",
        "runParams": run_params,
    }
    if triggered_by:
        data["triggeredBy"] = triggered_by
    if triggered_by_user_id:
        data["triggeredByUserId"] = triggered_by_user_id
    eid = create_execution_record(data, project_id=(project_id or "default"))
    if not eid:
        raise RuntimeError("Failed to create execution record")
    return eid


@bp.route("/stacks/<name>/actions", methods=["POST"])
@require_auth
def stacks_action(name):
    pid = _get_project_id()
    if not _valid_name(name) or not _stack_dir(pid, name).exists():
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(silent=True) or {}
    action = (body.get("action") or "").strip().lower()
    if action not in _VALID_ACTIONS:
        return jsonify({"error": f"Unsupported action. Allowed: {sorted(_VALID_ACTIONS)}"}), 400
    if action == "drift" and not _drift_enabled(pid, name):
        return jsonify({"error": "Drift detection is disabled for this stack. Enable it in the stack's Drift detection panel first."}), 409
    worker_id = (body.get("worker_id") or body.get("target_worker_id") or "").strip() or None
    _cu = getattr(request, "current_user", {}) or {}
    _tb = _cu.get("username") or _cu.get("email") or _cu.get("user_id") or ""
    _tbid = _cu.get("user_id") or ""

    _mutating = action in _cloud_state.MUTATING_ACTIONS
    _dd = _stack_data_dir(pid, name)
    if _mutating:
        _existing = _cloud_state.read_lock(_dd, _get_execution_record, pid)
        if _existing:
            return jsonify({
                "error": f"State is locked by {_existing.get('who')} "
                         f"({_existing.get('operation')}). Wait for that run to finish, "
                         f"or force-unlock from the State management panel.",
                "lock": _existing,
            }), 409
        pass

    try:
        eid = _create_execution(pid, name, action, worker_id=worker_id, triggered_by=_tb, triggered_by_user_id=_tbid)
    except Exception as e:
        current_app.logger.error(f"[cloud] enqueue {action} for {name} failed: {e}")
        return jsonify({"error": f"Failed to queue run: {e}"}), 500
    if _mutating:
        _cloud_state.snapshot_state(_stack_dir(pid, name), _dd,
                                    actor=_tb or "unknown", reason=f"pre-{action}",
                                    run_id=eid)
        _cloud_state.acquire_lock(_dd, actor=_tb or "unknown", operation=action,
                                  run_id=eid, get_execution=_get_execution_record,
                                  project_id=pid)
    _cloud_state.append_audit(_dd, "run.queued", _tb or "unknown", action=action, run_id=eid)
    _save_meta(pid, name, last_action=action, last_status="queued", last_run_id=eid)

    return jsonify({
        "ok": True,
        "run_id": eid,
        "execution_id": eid,
        "project_id": pid or "default",
        "status": "queued",
        "message": "Queued. Waiting for a worker to claim this run.",
    }), 202


# ---------------------------------------------------------------------------
# Drift detection (opt-in per stack, disabled by default)
# ---------------------------------------------------------------------------

def _read_meta(project_id: Optional[str], name: str) -> Dict[str, Any]:
    p = _stack_data_dir(project_id, name) / "meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _drift_enabled(project_id: Optional[str], name: str) -> bool:
    """Drift detection is OFF unless the stack explicitly opted in."""
    return bool(_read_meta(project_id, name).get("drift_enabled") is True)


def _latest_drift_run(project_id: Optional[str], name: str) -> Optional[Dict[str, Any]]:
    ex_dir = _project_executions_dir(project_id)
    if not ex_dir.exists():
        return None
    files = sorted(ex_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[:300]:
        try:
            exe = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        rp = exe.get("runParams") or {}
        if rp.get("execution_type") != "TOFU_RUN" or rp.get("stack_name") != name:
            continue
        if rp.get("tofu_action") != "drift":
            continue
        return exe
    return None


def _drift_status(project_id: Optional[str], name: str) -> Dict[str, Any]:
    """Derive drift state from the most recent `drift` run of this stack.

    OpenTofu's `-detailed-exitcode` semantics:
      0 -> in sync, 2 -> drift detected, anything else -> error.
    """
    out: Dict[str, Any] = {
        "enabled": _drift_enabled(project_id, name),
        "status": "unknown",
        "last_run_id": None,
        "last_checked_at": None,
        "returncode": None,
        "run_status": None,
    }
    exe = _latest_drift_run(project_id, name)
    if not exe:
        return out
    rc = exe.get("returnCode")
    run_status = _status_to_ui(exe.get("status", ""))
    out["last_run_id"] = exe.get("id")
    out["last_checked_at"] = int(exe.get("finishedAt") or exe.get("startedAt") or exe.get("createdAt") or 0) or None
    out["returncode"] = rc
    out["run_status"] = run_status
    if run_status in ("queued", "running"):
        out["status"] = "checking"
    elif run_status == "canceled":
        out["status"] = "unknown"
    elif rc == 0:
        out["status"] = "in_sync"
    elif rc == 2:
        out["status"] = "drifted"
    elif rc is None:
        out["status"] = "unknown"
    else:
        out["status"] = "error"
    return out


@bp.route("/stacks/<name>/drift", methods=["GET"])
@require_auth
def drift_get(name):
    pid = _get_project_id()
    if not _valid_name(name) or not _stack_dir(pid, name).exists():
        return jsonify({"error": "Not found"}), 404
    return jsonify(_drift_status(pid, name))


@bp.route("/stacks/<name>/drift", methods=["PUT"])
@require_auth
def drift_set(name):
    """Enable/disable drift detection for a single stack. Default: disabled."""
    pid = _get_project_id()
    if not _valid_name(name) or not _stack_dir(pid, name).exists():
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(silent=True) or {}
    enabled = body.get("enabled")
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in ("1", "true", "yes", "on")
    if not isinstance(enabled, bool):
        return jsonify({"error": "Body must include boolean 'enabled'."}), 400
    _save_meta(pid, name, drift_enabled=enabled)
    return jsonify({"ok": True, **_drift_status(pid, name)})


# ---------------------------------------------------------------------------
# Policy-as-code gate (opt-in per stack, disabled by default)
# ---------------------------------------------------------------------------

try:
    from services.cloud_policy import (
        policy_config_from_meta as _policy_config_from_meta,
        policy_enabled_from_meta as _policy_enabled_from_meta,
        register_policy_routes as _register_policy_routes,
    )
except ImportError:  # pragma: no cover
    from .cloud_policy import (  # type: ignore
        policy_config_from_meta as _policy_config_from_meta,
        policy_enabled_from_meta as _policy_enabled_from_meta,
        register_policy_routes as _register_policy_routes,
    )


def _policy_enabled(project_id: Optional[str], name: str) -> bool:
    return _policy_enabled_from_meta(_read_meta(project_id, name))


def _policy_config(project_id: Optional[str], name: str) -> Dict[str, Any]:
    return _policy_config_from_meta(_read_meta(project_id, name))


_register_policy_routes(
    bp,
    require_auth=require_auth,
    get_project_id=lambda: _get_project_id(),
    valid_name=lambda n: _valid_name(n),
    stack_dir=lambda pid, n: _stack_dir(pid, n),
    read_meta=lambda pid, n: _read_meta(pid, n),
    save_meta=lambda pid, n, **kw: _save_meta(pid, n, **kw),
    project_executions_dir=lambda pid: _project_executions_dir(pid),
)


# ---------------------------------------------------------------------------
# State management — locking visibility, versioning/rollback, remote backend.
# ---------------------------------------------------------------------------

try:
    from services import cloud_state as _cloud_state
except ImportError:  # pragma: no cover
    from . import cloud_state as _cloud_state  # type: ignore


def _current_actor() -> str:
    cu = getattr(request, "current_user", {}) or {}
    return str(cu.get("username") or cu.get("email") or cu.get("user_id") or "unknown")


def _get_execution_record(execution_id, project_id=None):
    try:
        from services.execution_history import get_execution as _ge
    except ImportError:  # pragma: no cover
        return None
    try:
        return _ge(execution_id, project_id=project_id or "default")
    except Exception:
        return None


_cloud_state.register_state_routes(
    bp,
    require_auth=require_auth,
    get_project_id=lambda: _get_project_id(),
    valid_name=lambda n: _valid_name(n),
    stack_dir=lambda pid, n: _stack_dir(pid, n),
    stack_data_dir=lambda pid, n: _stack_data_dir(pid, n),
    current_actor=_current_actor,
    get_execution=_get_execution_record,
)


def _status_to_ui(s: str) -> str:
    return {
        "QUEUED": "queued", "RUNNING": "running",
        "SUCCESS": "succeeded", "FAILED": "failed",
        "CANCELING": "canceling", "CANCELED": "canceled",
    }.get((s or "").upper(), (s or "").lower())


def _exec_to_run(exe: Dict[str, Any]) -> Dict[str, Any]:
    rp = exe.get("runParams") or {}
    return {
        "run_id": exe.get("id"),
        "execution_id": exe.get("id"),
        "stack": rp.get("stack_name"),
        "action": rp.get("tofu_action"),
        "status": _status_to_ui(exe.get("status", "")),
        "returncode": exe.get("returnCode"),
        "worker_id": exe.get("workerId"),
        "started_at": int(exe.get("startedAt") or exe.get("createdAt") or 0),
        "finished_at": int(exe.get("finishedAt") or 0) or None,
        "triggered_by": exe.get("triggeredBy") or "",
        "triggered_by_user_id": exe.get("triggeredByUserId") or "",
    }


@bp.route("/stacks/<name>/runs", methods=["GET"])
@require_auth
def runs_list(name):
    pid = _get_project_id()
    if not _valid_name(name):
        return jsonify({"error": "Not found"}), 404
    items: List[Dict[str, Any]] = []
    ex_dir = _project_executions_dir(pid)
    if ex_dir.exists():
        files = sorted(ex_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files[:200]:
            try:
                exe = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            rp = exe.get("runParams") or {}
            if rp.get("execution_type") != "TOFU_RUN" or rp.get("stack_name") != name:
                continue
            items.append({**_exec_to_run(exe), "mtime": int(f.stat().st_mtime)})
            if len(items) >= 50:
                break
    return jsonify({"runs": items})


@bp.route("/stacks/<name>/runs/<run_id>", methods=["GET"])
@require_auth
def run_get(name, run_id):
    pid = _get_project_id() or "default"
    if not _valid_name(name):
        return jsonify({"error": "Not found"}), 404
    ex_file = _project_executions_dir(pid) / f"{run_id}.json"
    if not ex_file.exists():
        return jsonify({"error": "Run not found"}), 404
    try:
        exe = json.loads(ex_file.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({"error": "Run unreadable"}), 500
    log_text = ""
    log_file = _project_logs_dir(pid) / f"{run_id}.log"
    if log_file.exists():
        try:
            log_text = log_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            log_text = ""
    out = _exec_to_run(exe)
    if out["status"] == "queued" and not log_text:
        log_text = "[waiting for a worker to claim this run…]\n"
    out["log"] = log_text
    return jsonify(out)


@bp.route("/stacks/<name>/runs/<run_id>/stream", methods=["GET"])
@require_auth
def run_stream(name, run_id):
    """SSE tail of the worker-written log."""
    pid = _get_project_id() or "default"
    if not _valid_name(name):
        return jsonify({"error": "Not found"}), 404
    ex_file = _project_executions_dir(pid) / f"{run_id}.json"
    if not ex_file.exists():
        return jsonify({"error": "Run not found"}), 404
    log_path = _project_logs_dir(pid) / f"{run_id}.log"

    @stream_with_context
    def gen():
        waited = 0
        while not log_path.exists() and waited < 60:
            yield ": waiting for worker\n\n"
            time.sleep(1.0)
            waited += 1
        if not log_path.exists():
            yield "event: end\ndata: timeout\n\n"
            return
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            while True:
                line = f.readline()
                if line:
                    yield f"data: {line.rstrip()}\n\n"
                    continue
                try:
                    status = (json.loads(ex_file.read_text(encoding="utf-8")).get("status") or "").upper()
                except Exception:
                    status = ""
                if status in ("SUCCESS", "FAILED", "CANCELED"):
                    yield f"event: end\ndata: {_status_to_ui(status)}\n\n"
                    return
                time.sleep(0.5)

    return Response(gen(), mimetype="text/event-stream")


# ---------------------------------------------------------------------------
# VM Inventory — parsed from terraform.tfstate after `apply`
# ---------------------------------------------------------------------------

def _build_inventory_from_state(state: Dict[str, Any], provider: str = "bytedc") -> Dict[str, Any]:
    """Delegate to the provider adapter's tfstate parser (see cloud_providers/)."""
    adapter = _providers.get(provider)
    if adapter is None:
        try:
            current_app.logger.warning(
                f"[cloud] no inventory builder registered for provider={provider!r}; returning empty inventory"
            )
        except Exception:
            pass
        return {"vms": [], "vpcs": [], "subnets": [], "eips": [], "count": 0}
    return adapter.build_inventory(state)





@bp.route("/stacks/<name>/inventory", methods=["GET"])
@require_auth
def stacks_inventory(name):
    """Return VM inventory parsed from terraform.tfstate.

    Persists a snapshot to <stack-data>/inventory.json on every parse so the
    inventory survives container rebuilds even if tfstate is removed.
    """
    pid = _get_project_id()
    if not _valid_name(name) or not _stack_dir(pid, name).exists():
        return jsonify({"error": "Not found"}), 404

    cache_path = _stack_data_dir(pid, name) / "inventory.json"
    state_file = _stack_dir(pid, name) / "terraform.tfstate"
    # When the stack uses a remote backend (e.g. S3/OBS) tfstate isn't written
    # locally. The worker drops a snapshot via `tofu state pull` after each
    # successful apply/destroy/refresh so the UI keeps an inventory.
    snapshot_file = _stack_dir(pid, name) / "terraform.tfstate.json"
    refresh = request.args.get("refresh", "1") not in ("0", "false", "no")

    source_file = state_file if state_file.exists() else (snapshot_file if snapshot_file.exists() else None)
    inv_provider = _read_stack_provider(pid, name) or "bytedc"
    if refresh and source_file is not None:
        try:
            state = json.loads(source_file.read_text(encoding="utf-8"))

            inv = _build_inventory_from_state(state, provider=inv_provider)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"generated_at": int(time.time()), **inv}
            cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            # Auto-save a timestamped cost report so users get one per Apply
            # without having to open the cost calculator.
            try:
                from storage import cost_store
                provider = inv_provider or "bytedc"
                env = None
                cloud_project = None
                try:
                    meta_file = _stack_dir(pid, name) / "stack.json"
                    if meta_file.exists():
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        if isinstance(meta, dict):
                            provider = (meta.get("provider") or provider)
                            env = meta.get("env")
                            cloud_project = meta.get("cloud_project") or meta.get("project")
                except Exception:
                    pass
                resources = cost_store.resources_from_inventory(payload)
                if resources:
                    result = cost_store.estimate_cost(provider, resources)
                    cost_store.save_report(
                        pid, provider, name, resources, result,
                        source="apply", env=env, cloud_project=cloud_project,
                    )
            except Exception as ce:
                current_app.logger.warning(f"[cost] auto-report failed for {name}: {ce}")

        except Exception as e:
            current_app.logger.warning(f"[cloud] inventory parse failed for {name}: {e}")

    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            data["state_present"] = state_file.exists() or snapshot_file.exists()
            return jsonify(data)
        except Exception:
            pass

    return jsonify({
        "vms": [], "vpcs": [], "subnets": [], "eips": [], "count": 0,
        "state_present": state_file.exists() or snapshot_file.exists(),
        "message": "No inventory yet. Run Apply to provision resources.",
    })



@bp.route("/stacks/<name>/state", methods=["GET"])
@require_auth
def stacks_state(name):
    """Inspect the local terraform.tfstate.

    Returns whether state exists, resource count, and a flat list of
    resource addresses (`module.x.hcs_ecs_compute_instance.this[\"foo\"]`).
    Lets the UI explain "Destroy 0 destroyed" when state is empty.
    """
    pid = _get_project_id()
    if not _valid_name(name) or not _stack_dir(pid, name).exists():
        return jsonify({"error": "Not found"}), 404
    sf = _stack_dir(pid, name) / "terraform.tfstate"
    if not sf.exists():
        return jsonify({
            "state_present": False, "resource_count": 0, "resources": [],
            "message": "No terraform.tfstate on disk. The local state was never written "
                       "or has been removed (e.g. data volume not persisted).",
        })
    try:
        state = json.loads(sf.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({
            "state_present": True, "resource_count": 0, "resources": [],
            "error": f"state file unreadable: {e}",
        }), 200

    addresses: List[str] = []
    for res in (state.get("resources") or []):
        module = res.get("module") or ""
        rtype = res.get("type", "")
        rname = res.get("name", "")
        for inst in (res.get("instances") or []):
            ikey = inst.get("index_key")
            suffix = ""
            if isinstance(ikey, str):
                suffix = f'["{ikey}"]'
            elif isinstance(ikey, int):
                suffix = f"[{ikey}]"
            base = f"{rtype}.{rname}{suffix}"
            addresses.append(f"{module}.{base}" if module else base)
    return jsonify({
        "state_present": True,
        "resource_count": len(addresses),
        "resources": addresses,
        "serial": state.get("serial"),
        "lineage": state.get("lineage"),
        "tofu_version": state.get("terraform_version"),
    })




# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register(app) -> None:
    """Register the Cloud Provisioning blueprint with the Flask app."""
    app.register_blueprint(bp)
    app.logger.info("[cloud] Cloud Provisioning routes registered at /api/cloud/*")

"""Cost Analysis storage — pricing catalogs and estimate history.

Pricing catalogs are global (per provider). Estimates are scoped per project.
All persisted as JSON under DATA_DIR/cost/.
"""
from __future__ import annotations

import json
import os
import time
import uuid
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
COST_DIR = DATA_DIR / "cost"
PRICING_DIR = COST_DIR / "pricing"
ESTIMATES_DIR = COST_DIR / "estimates"
REPORTS_DIR = COST_DIR / "reports"
PRICING_DIR.mkdir(parents=True, exist_ok=True)
ESTIMATES_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_PROVIDERS = ["aws", "eks", "gcp", "gke", "azure", "hetzner", "cloudflare", "bytedc", "kubernetes"]

# ---- Default pricing seeds (USD) -------------------------------------------------

def _default_catalog(provider: str) -> Dict[str, Any]:
    # Sensible mid-market defaults; admins are expected to tune.
    base = {
        "provider": provider,
        "currency": "USD",
        "version": 1,
        "effective_date": time.strftime("%Y-%m-%d"),
        "updated_at": time.time(),
        "compute": {
            "vcpu_hour": 0.025,
            "ram_gb_hour": 0.005,
            "gpu_hour": 1.20,
            "instance_templates": [
                {"name": "small",  "vcpu": 2, "ram_gb": 4,  "monthly": 25},
                {"name": "medium", "vcpu": 4, "ram_gb": 8,  "monthly": 50},
                {"name": "large",  "vcpu": 8, "ram_gb": 16, "monthly": 110},
            ],
        },
        "storage": {
            "ssd_gb_month": 0.10,
            "hdd_gb_month": 0.04,
            "object_gb_month": 0.023,
            "snapshot_gb_month": 0.05,
        },
        "network": {
            "public_ip_month": 3.50,
            "bandwidth_gb": 0.09,
            "nat_gateway_hour": 0.045,
            "load_balancer_hour": 0.025,
            "vpn_gateway_month": 36.00,
        },
        "managed": {
            "kubernetes_cluster_month": 73.00,
            "database_instance_month": 60.00,
            "dns_zone_month": 0.50,
            "cdn_gb": 0.085,
            "waf_month": 20.00,
        },
    }
    overrides = {
        "bytedc":     {"compute": {"vcpu_hour": 0.018, "ram_gb_hour": 0.004}},
        "hetzner":    {"compute": {"vcpu_hour": 0.016, "ram_gb_hour": 0.005}, "network": {"public_ip_month": 1.19, "bandwidth_gb": 0.00}},
        "aws":        {"network": {"public_ip_month": 3.65}},
        "eks":        {"managed": {"kubernetes_cluster_month": 73.0}, "network": {"public_ip_month": 3.65}},
        "gcp":        {"compute": {"vcpu_hour": 0.031, "ram_gb_hour": 0.004}, "network": {"public_ip_month": 2.92}},
        "gke":        {"managed": {"kubernetes_cluster_month": 73.0}, "compute": {"vcpu_hour": 0.031, "ram_gb_hour": 0.004}},
        "azure":      {"managed": {"kubernetes_cluster_month": 75.0}},
        "cloudflare": {"network": {"bandwidth_gb": 0.00}, "managed": {"waf_month": 25.0}},
        "kubernetes": {"compute": {"vcpu_hour": 0.010, "ram_gb_hour": 0.002}, "managed": {"kubernetes_cluster_month": 0.0}},
    }
    o = overrides.get(provider, {})
    for cat, vals in o.items():
        base[cat].update(vals)  # type: ignore[arg-type]
    return base


# ---- Pricing CRUD -----------------------------------------------------------------

def _pricing_file(provider: str) -> Path:
    return PRICING_DIR / f"{provider}.json"

def _history_file(provider: str) -> Path:
    return PRICING_DIR / f"{provider}.history.json"

def get_pricing(provider: str) -> Dict[str, Any]:
    provider = provider.lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    f = _pricing_file(provider)
    if not f.exists():
        cat = _default_catalog(provider)
        save_pricing(provider, cat, record_history=False)
        return cat
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Failed to read pricing {provider}: {e}")
        return _default_catalog(provider)

def list_pricing() -> List[Dict[str, Any]]:
    return [get_pricing(p) for p in SUPPORTED_PROVIDERS]

def save_pricing(provider: str, catalog: Dict[str, Any], record_history: bool = True) -> Dict[str, Any]:
    provider = provider.lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    prev: Optional[Dict[str, Any]] = None
    f = _pricing_file(provider)
    if f.exists():
        try: prev = json.loads(f.read_text(encoding="utf-8"))
        except Exception: prev = None
    catalog["provider"] = provider
    catalog.setdefault("currency", "USD")
    catalog["version"] = int((prev or {}).get("version", 0)) + 1 if record_history else int(catalog.get("version", 1))
    catalog["updated_at"] = time.time()
    catalog.setdefault("effective_date", time.strftime("%Y-%m-%d"))
    f.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    if record_history and prev:
        hf = _history_file(provider)
        history: List[Dict[str, Any]] = []
        if hf.exists():
            try: history = json.loads(hf.read_text(encoding="utf-8"))
            except Exception: history = []
        history.append({"version": prev.get("version", 1), "saved_at": prev.get("updated_at"), "catalog": prev})
        history = history[-50:]
        hf.write_text(json.dumps(history, indent=2), encoding="utf-8")
    return catalog

def get_pricing_history(provider: str) -> List[Dict[str, Any]]:
    hf = _history_file(provider.lower())
    if not hf.exists(): return []
    try: return json.loads(hf.read_text(encoding="utf-8"))
    except Exception: return []


# ---- Estimate engine --------------------------------------------------------------

def estimate_cost(provider: str, resources: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute monthly/yearly cost given a flat resource list.

    Each resource: { kind, name?, quantity?, vcpu?, ram_gb?, gpu?, size_gb?,
                     hours_per_month?, bandwidth_gb? }
    kind is one of: instance, gpu, ssd, hdd, object_storage, snapshot,
                    public_ip, bandwidth, nat_gateway, load_balancer, vpn_gateway,
                    kubernetes_cluster, database, dns_zone, cdn, waf
    """
    cat = get_pricing(provider)
    HPM = 730.0  # average hours per month
    line_items: List[Dict[str, Any]] = []
    warnings: List[str] = []
    total = 0.0

    for r in resources or []:
        kind = (r.get("kind") or "").lower().strip()
        qty = float(r.get("quantity") or 1)
        hours = float(r.get("hours_per_month") or HPM)
        unit = "month"
        unit_price = 0.0
        monthly = 0.0
        name = r.get("name") or kind

        if kind == "instance":
            vcpu = float(r.get("vcpu") or 0)
            ram = float(r.get("ram_gb") or 0)
            unit_price = vcpu * cat["compute"]["vcpu_hour"] + ram * cat["compute"]["ram_gb_hour"]
            unit = "hour"
            monthly = unit_price * hours * qty
            if vcpu >= 16 or ram >= 64:
                warnings.append(f"Resource '{name}' looks overprovisioned (vCPU={vcpu}, RAM={ram}GB)")
        elif kind == "gpu":
            unit_price = cat["compute"]["gpu_hour"]; unit = "hour"
            monthly = unit_price * hours * qty
        elif kind == "ssd":
            size = float(r.get("size_gb") or 0)
            unit_price = cat["storage"]["ssd_gb_month"]; unit = "GB-month"
            monthly = size * unit_price * qty
        elif kind == "hdd":
            size = float(r.get("size_gb") or 0)
            unit_price = cat["storage"]["hdd_gb_month"]; unit = "GB-month"
            monthly = size * unit_price * qty
        elif kind == "object_storage":
            size = float(r.get("size_gb") or 0)
            unit_price = cat["storage"]["object_gb_month"]; unit = "GB-month"
            monthly = size * unit_price * qty
        elif kind == "snapshot":
            size = float(r.get("size_gb") or 0)
            unit_price = cat["storage"]["snapshot_gb_month"]; unit = "GB-month"
            monthly = size * unit_price * qty
        elif kind == "public_ip":
            unit_price = cat["network"]["public_ip_month"]; monthly = unit_price * qty
        elif kind == "bandwidth":
            gb = float(r.get("bandwidth_gb") or r.get("size_gb") or 0)
            unit_price = cat["network"]["bandwidth_gb"]; unit = "GB"
            monthly = gb * unit_price * qty
        elif kind == "nat_gateway":
            unit_price = cat["network"]["nat_gateway_hour"]; unit = "hour"
            monthly = unit_price * hours * qty
        elif kind == "load_balancer":
            unit_price = cat["network"]["load_balancer_hour"]; unit = "hour"
            monthly = unit_price * hours * qty
        elif kind == "vpn_gateway":
            unit_price = cat["network"]["vpn_gateway_month"]; monthly = unit_price * qty
        elif kind == "kubernetes_cluster":
            unit_price = cat["managed"]["kubernetes_cluster_month"]; monthly = unit_price * qty
        elif kind == "database":
            unit_price = cat["managed"]["database_instance_month"]; monthly = unit_price * qty
        elif kind == "dns_zone":
            unit_price = cat["managed"]["dns_zone_month"]; monthly = unit_price * qty
        elif kind == "cdn":
            gb = float(r.get("bandwidth_gb") or r.get("size_gb") or 0)
            unit_price = cat["managed"]["cdn_gb"]; unit = "GB"
            monthly = gb * unit_price * qty
        elif kind == "waf":
            unit_price = cat["managed"]["waf_month"]; monthly = unit_price * qty
        else:
            warnings.append(f"Unknown resource kind '{kind}' — skipped")
            continue

        line_items.append({
            "kind": kind, "name": name, "quantity": qty,
            "unit": unit, "unit_price": round(unit_price, 6),
            "monthly": round(monthly, 2), "yearly": round(monthly * 12, 2),
        })
        total += monthly

    # Smart insights
    insights: List[str] = []
    lb_count = sum(1 for li in line_items if li["kind"] == "load_balancer")
    instance_count = sum(li["quantity"] for li in line_items if li["kind"] == "instance")
    if instance_count >= 2 and lb_count == 0:
        insights.append("Multiple compute instances without a load balancer — consider HA.")
    if instance_count == 1:
        insights.append("Single compute instance is a potential single point of failure.")
    if any(li["kind"] == "public_ip" for li in line_items) and not any(li["kind"] == "nat_gateway" for li in line_items):
        insights.append("Public IPs detected without NAT gateway — verify egress topology.")

    return {
        "provider": provider,
        "currency": cat.get("currency", "USD"),
        "monthly_total": round(total, 2),
        "yearly_total": round(total * 12, 2),
        "one_time_total": 0.0,
        "line_items": line_items,
        "warnings": warnings,
        "insights": insights,
        "computed_at": time.time(),
    }


# ---- Estimate persistence ---------------------------------------------------------

def _estimates_file(project_id: str) -> Path:
    safe = "".join(c for c in project_id if c.isalnum() or c in "-_") or "default"
    return ESTIMATES_DIR / f"{safe}.json"

def list_estimates(project_id: str) -> List[Dict[str, Any]]:
    f = _estimates_file(project_id)
    if not f.exists(): return []
    try: return json.loads(f.read_text(encoding="utf-8"))
    except Exception: return []

def save_estimate(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    items = list_estimates(project_id)
    record = {
        "id": str(uuid.uuid4()),
        "created_at": time.time(),
        **payload,
    }
    items.insert(0, record)
    items = items[:100]
    _estimates_file(project_id).write_text(json.dumps(items, indent=2), encoding="utf-8")
    return record

def delete_estimate(project_id: str, estimate_id: str) -> bool:
    items = list_estimates(project_id)
    new_items = [x for x in items if x.get("id") != estimate_id]
    if len(new_items) == len(items): return False
    _estimates_file(project_id).write_text(json.dumps(new_items, indent=2), encoding="utf-8")
    return True


# ---- Flavor → vCPU/RAM map (Huawei Cloud Stack / OpenStack-ish) -------------------

_FLAVOR_MAP: Dict[str, Dict[str, float]] = {
    # s3 / s6 generations: parsed as <gen>.<size>.<ratio>
    "s3.small.1":   {"vcpu": 1, "ram_gb": 1},
    "s3.medium.1":  {"vcpu": 1, "ram_gb": 4},
    "s3.large.1":   {"vcpu": 2, "ram_gb": 2},
    "s3.large.2":   {"vcpu": 2, "ram_gb": 4},
    "s3.large.4":   {"vcpu": 2, "ram_gb": 8},
    "s3.xlarge.2":  {"vcpu": 4, "ram_gb": 8},
    "s3.xlarge.4":  {"vcpu": 4, "ram_gb": 16},
    "s3.2xlarge.2": {"vcpu": 8, "ram_gb": 16},
    "s3.2xlarge.4": {"vcpu": 8, "ram_gb": 32},
    "s6.medium.2":  {"vcpu": 1, "ram_gb": 2},
    "s6.large.2":   {"vcpu": 2, "ram_gb": 4},
    "s6.xlarge.2":  {"vcpu": 4, "ram_gb": 8},
    "s6.2xlarge.2": {"vcpu": 8, "ram_gb": 16},
    "c6.large.2":   {"vcpu": 2, "ram_gb": 4},
    "c6.xlarge.2":  {"vcpu": 4, "ram_gb": 8},
    "m6.large.8":   {"vcpu": 2, "ram_gb": 16},
}

def flavor_specs(flavor_id: Optional[str]) -> Dict[str, float]:
    """Look up vCPU / RAM for a known flavor; default to 2/4."""
    if not flavor_id:
        return {"vcpu": 2, "ram_gb": 4}
    f = flavor_id.lower().strip()
    if f in _FLAVOR_MAP:
        return _FLAVOR_MAP[f]
    # Heuristic: parse trailing ".N.M" suffix
    try:
        parts = f.split(".")
        if len(parts) >= 3:
            size = parts[-2]  # small/medium/large/xlarge/2xlarge
            ratio = float(parts[-1])
            base_vcpu = {"small": 1, "medium": 1, "large": 2, "xlarge": 4, "2xlarge": 8, "4xlarge": 16}.get(size, 2)
            return {"vcpu": float(base_vcpu), "ram_gb": float(base_vcpu) * ratio}
    except Exception:
        pass
    return {"vcpu": 2, "ram_gb": 4}


# ---- Build a resource list from a VM-inventory snapshot --------------------------

def resources_from_inventory(inv: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert the VM-inventory shape (vms / eips / subnets) into estimate resources."""
    out: List[Dict[str, Any]] = []
    vms = inv.get("vms") or []
    for vm in vms:
        specs = flavor_specs(vm.get("flavor_id"))
        out.append({
            "kind": "instance",
            "name": vm.get("hostname") or vm.get("instance_id") or "vm",
            "quantity": 1,
            "vcpu": specs["vcpu"],
            "ram_gb": specs["ram_gb"],
        })
        disk_size = float(vm.get("system_disk_size") or 0)
        if disk_size > 0:
            disk_kind = "hdd" if (vm.get("system_disk_type") or "").lower().startswith("sata") else "ssd"
            out.append({
                "kind": disk_kind,
                "name": f"{vm.get('hostname') or 'vm'}-root",
                "quantity": 1,
                "size_gb": disk_size,
            })
    eips = inv.get("eips") or []
    if eips:
        out.append({"kind": "public_ip", "name": "public-ips", "quantity": len(eips)})
    return out


# ---- Cost reports (per-project, timestamped) -------------------------------------

def _project_reports_dir(project_id: str) -> Path:
    safe = "".join(c for c in project_id if c.isalnum() or c in "-_") or "default"
    d = REPORTS_DIR / safe
    d.mkdir(parents=True, exist_ok=True)
    return d

def save_report(
    project_id: str,
    provider: str,
    stack: str,
    resources: List[Dict[str, Any]],
    result: Dict[str, Any],
    *,
    source: str = "apply",
    run_id: Optional[str] = None,
    env: Optional[str] = None,
    cloud_project: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist a timestamped cost report under reports/<project_id>/."""
    ts = int(time.time())
    rid = str(uuid.uuid4())
    safe_stack = "".join(c for c in (stack or "stack") if c.isalnum() or c in "-_") or "stack"
    fname = f"{ts}_{safe_stack}_{rid[:8]}.json"
    rec = {
        "id": rid,
        "filename": fname,
        "created_at": ts,
        "provider": provider,
        "stack": stack,
        "env": env,
        "cloud_project": cloud_project,
        "source": source,
        "run_id": run_id,
        "resources": resources,
        "result": result,
        "monthly_total": result.get("monthly_total", 0),
        "yearly_total": result.get("yearly_total", 0),
        "currency": result.get("currency", "USD"),
        "resource_count": len(resources),
    }
    (_project_reports_dir(project_id) / fname).write_text(
        json.dumps(rec, indent=2), encoding="utf-8"
    )
    return rec

def list_reports(project_id: str, stack: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
    d = _project_reports_dir(project_id)
    items: List[Dict[str, Any]] = []
    for f in sorted(d.glob("*.json"), reverse=True)[:limit]:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            if stack and rec.get("stack") != stack:
                continue
            # Slim down — list view doesn't need full breakdown.
            items.append({
                "id": rec.get("id"),
                "filename": rec.get("filename"),
                "created_at": rec.get("created_at"),
                "provider": rec.get("provider"),
                "stack": rec.get("stack"),
                "env": rec.get("env"),
                "cloud_project": rec.get("cloud_project"),
                "source": rec.get("source"),
                "run_id": rec.get("run_id"),
                "monthly_total": rec.get("monthly_total"),
                "yearly_total": rec.get("yearly_total"),
                "currency": rec.get("currency", "USD"),
                "resource_count": rec.get("resource_count", len(rec.get("resources") or [])),
            })
        except Exception:
            continue
    return items

def get_report(project_id: str, report_id: str) -> Optional[Dict[str, Any]]:
    for f in _project_reports_dir(project_id).glob("*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            if rec.get("id") == report_id:
                return rec
        except Exception:
            continue
    return None

def delete_report(project_id: str, report_id: str) -> bool:
    for f in _project_reports_dir(project_id).glob("*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            if rec.get("id") == report_id:
                f.unlink()
                return True
        except Exception:
            continue
    return False

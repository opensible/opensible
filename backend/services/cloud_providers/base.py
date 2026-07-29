"""ProviderAdapter — contract each cloud provider must implement.

One file per provider under this package. The main `cloud_provisioning`
module discovers adapters through the registry in `__init__.py` and never
branches on `provider == "..."` strings itself. Add a new provider by
dropping a new module here and registering it — no core edits required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class ProviderAdapter:
    id: str
    label: str
    description: str
    logo: str
    schema: Dict[str, Any]
    tfvars_order: List[str]
    secret_keys: Tuple[str, ...]
    # Whitelist of keys allowed inside object() members of map/object tfvars
    # (e.g. platform_overrides.<role>.<key>). Stripped at render time.
    platform_override_keys: set
    # (state_dict) -> inventory_dict
    build_inventory: Callable[[Dict[str, Any]], Dict[str, Any]]
    enabled: bool = True
    category: str = "cloud"

    def sanitize_values(self, values: Dict[str, Any]) -> Dict[str, Any]:
        """Drop stale/foreign keys from platform_overrides so a stack that was
        edited under a different provider schema can't produce invalid HCL."""
        out = dict(values)
        po = out.get("platform_overrides")
        if isinstance(po, dict):
            clean: Dict[str, Any] = {}
            for role, override in po.items():
                if not isinstance(override, dict):
                    continue
                clean[role] = {
                    k: v for k, v in override.items()
                    if k in self.platform_override_keys and v not in (None, "")
                }
            out["platform_overrides"] = clean
        return out

    def to_catalog_entry(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "enabled": self.enabled,
            "logo": self.logo,
            "category": self.category,
        }

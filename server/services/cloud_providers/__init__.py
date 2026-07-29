"""Cloud provider registry.

Each provider is a self-contained module exposing an `ADAPTER: ProviderAdapter`.
The main `cloud_provisioning` service discovers providers through the helpers
below and never branches on provider id strings itself.

Add a new provider in 3 steps:
  1. Create `backend/services/cloud_providers/<id>.py` with an `ADAPTER`.
  2. Import it here and add to `_ADAPTERS`.
  3. (Optional) add a "coming soon" stub to `_STUB_CATALOG` before enabling.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .base import ProviderAdapter
from . import bytedc, hetzner, cloudflare, aws, gcp, gke, eks, kubernetes


# Ordered so the wizard picker displays: AWS | Google Cloud | GKE | Hetzner | Cloudflare | ByteDC | Kubernetes.
# (Azure is a "coming soon" stub injected by the frontend between GCP and Hetzner.)
_ADAPTERS: Dict[str, ProviderAdapter] = {
    aws.ADAPTER.id: aws.ADAPTER,
    eks.ADAPTER.id: eks.ADAPTER,
    gcp.ADAPTER.id: gcp.ADAPTER,
    gke.ADAPTER.id: gke.ADAPTER,
    hetzner.ADAPTER.id: hetzner.ADAPTER,
    cloudflare.ADAPTER.id: cloudflare.ADAPTER,
    bytedc.ADAPTER.id: bytedc.ADAPTER,
    kubernetes.ADAPTER.id: kubernetes.ADAPTER,
}

# "Coming soon" providers that don't have an adapter yet. Shown in the picker
# with enabled=False so users see the roadmap.
_STUB_CATALOG: List[Dict[str, Any]] = []

_DEFAULT_PROVIDER = "bytedc"


def get(provider: Optional[str]) -> Optional[ProviderAdapter]:
    return _ADAPTERS.get((provider or _DEFAULT_PROVIDER).lower())


def require(provider: Optional[str]) -> ProviderAdapter:
    a = get(provider) or _ADAPTERS[_DEFAULT_PROVIDER]
    return a


def known_ids() -> Tuple[str, ...]:
    return tuple(_ADAPTERS.keys())


def catalog() -> List[Dict[str, Any]]:
    """Provider list for the wizard picker (adapters first, stubs after)."""
    return [a.to_catalog_entry() for a in _ADAPTERS.values()] + list(_STUB_CATALOG)


def schemas() -> Dict[str, Dict[str, Any]]:
    return {aid: a.schema for aid, a in _ADAPTERS.items()}


def all_secret_keys() -> Tuple[str, ...]:
    seen: List[str] = []
    for a in _ADAPTERS.values():
        for k in a.secret_keys:
            if k not in seen:
                seen.append(k)
    return tuple(seen)


def default_provider() -> str:
    return _DEFAULT_PROVIDER

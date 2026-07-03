"""Canonical-engine PIT-provider wiring (audit P2, add-pit-analyzer-routing).

ONE shared, unit-testable entry for how `Pipeline` / `WalkForwardEngine`
construct the optional ``PITDataProvider`` from configuration:

* ``delisted_registry_path`` empty  → ``None`` — no provider, the analyzers
  run their legacy WARN path; today's behavior, identity-preserving.
* non-empty → construct a ``PITDataProvider`` bound to the CALLER's runtime
  labels (its ``provider_uri`` + its ``data_adjust_mode`` declaration, so
  ``init_qlib_canonical`` sees the same config and no-ops). A missing or
  malformed registry FAILS LOUD at construction (``PITDataProviderError``
  from the provider itself) — never a silent fall-through to the WARN path.
"""
from __future__ import annotations

from typing import Any


def build_pit_provider(
    *,
    delisted_registry_path: str,
    provider_uri: str,
    data_adjust_mode: str,
    region: str = "cn",
) -> Any | None:
    """Construct the run's PIT provider from config, or ``None`` when opted out.

    Called ONCE at run start by each engine; the same instance threads to every
    analyzer that accepts ``pit_provider``.
    """
    if not str(delisted_registry_path or "").strip():
        return None
    from src.pit.query import PITDataProvider

    return PITDataProvider(
        provider_uri=provider_uri,
        delisted_registry_path=delisted_registry_path,
        data_adjust_mode=data_adjust_mode,
        region=region,
    )

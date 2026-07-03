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
    from src.core.qlib_runtime import _normalize_provider_uri
    from src.pit.query import PITDataProvider

    # Normalize HERE so every caller is immune: the provider's own
    # calendars/day.txt existence check runs on the raw path BEFORE
    # QlibRuntimeConfig would expand ``~`` — an un-normalized "~/bundle"
    # that canonical init accepts would fail PIT construction (codex P2
    # on #320). Idempotent for already-normalized runtime values.
    return PITDataProvider(
        provider_uri=_normalize_provider_uri(str(provider_uri)),
        delisted_registry_path=delisted_registry_path,
        data_adjust_mode=data_adjust_mode,
        region=region,
    )

"""Thin wrapper around :mod:`tushare`'s ``pro_api``.

Why this module exists
----------------------
- Centralises ``TUSHARE_TOKEN`` discovery so every caller goes through
  the same env-var lookup. Hard-coding the token in YAML or constructor
  arguments is forbidden — secrets do not belong in committed config.
- Lazy-imports ``tushare`` so importing the package in a contract-only
  test environment (no network, no token, no extras) does not blow up.
- Normalises Tushare's mixed error surface (``TushareError`` for some
  paths, generic ``Exception`` for others, plain ``None`` returns for
  rate-limit failures) into a single :class:`TushareClientError`.

This module does NOT cache, retry, or implement any per-API knowledge.
Higher layers (e.g. :class:`TushareIndustryPublisher`) own those
concerns; the client is just a typed boundary.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from src.core.logger import get_logger

_logger = get_logger(__name__)


_TOKEN_ENV_VAR = "TUSHARE_TOKEN"


class TushareClientError(RuntimeError):
    """Raised on any Tushare-side failure: missing token, import error,
    rate limit, malformed payload. The error message names the cause
    so callers don't have to guess from stack traces."""


@dataclass(frozen=True)
class TushareClient:
    """Thin handle bound to a Tushare token.

    Construct via :meth:`from_environment` to enforce the
    "secrets via env, not via constructor literal" boundary. The
    raw constructor exists so unit tests can inject a known token
    without depending on the environment, but production code should
    not call it directly.
    """

    token: str

    @classmethod
    def from_environment(cls) -> "TushareClient":
        """Read ``TUSHARE_TOKEN`` from the environment and build a client.

        Raises :class:`TushareClientError` if the variable is unset,
        empty, or whitespace-only. We refuse to silently fall back to
        an "anonymous" client because Tushare's anonymous tier rate-
        limits to a uselessly low ceiling and the user almost certainly
        wanted to authenticate.
        """
        token = os.environ.get(_TOKEN_ENV_VAR, "").strip()
        if not token:
            raise TushareClientError(
                f"Environment variable {_TOKEN_ENV_VAR!r} is not set or is "
                "empty. Tushare's authenticated APIs are required for the "
                "industry publisher; anonymous access cannot return Shenwan "
                "L2 classification. Export your token first: "
                f"export {_TOKEN_ENV_VAR}='your_pro_token_here'."
            )
        return cls(token=token)

    def call(self, api_name: str, **params: Any) -> Any:
        """Invoke a Tushare ``pro_api`` endpoint and return the DataFrame.

        Lazy-imports ``tushare`` on first call so module import does not
        require the dependency. Translates every Tushare-side exception
        into :class:`TushareClientError` with the API name embedded so
        the caller can distinguish "industry call failed" from "members
        call failed" without reading a stack trace.
        """
        try:
            import tushare as ts  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise TushareClientError(
                "tushare is not installed. Run: python -m pip install -e \".[tushare]\""
            ) from exc

        try:
            pro = ts.pro_api(self.token)
        except Exception as exc:
            raise TushareClientError(
                f"Failed to construct Tushare pro client (api='{api_name}'): "
                f"{type(exc).__name__}: {exc}. Check that TUSHARE_TOKEN is "
                "valid and your account has Pro permissions."
            ) from exc

        method = getattr(pro, api_name, None)
        if method is None or not callable(method):
            raise TushareClientError(
                f"Tushare pro_api has no callable named {api_name!r}. "
                "Check the API name spelling in the Tushare docs."
            )

        try:
            result = method(**params)
        except Exception as exc:
            raise TushareClientError(
                f"Tushare API '{api_name}' raised {type(exc).__name__}: "
                f"{exc}. Common causes: rate limit (account tier too low), "
                "missing parameter, or transient network error."
            ) from exc

        if result is None:
            # Tushare returns None on some rate-limited / quota-exceeded
            # responses without raising. Treat as an explicit failure
            # rather than letting an empty DataFrame propagate.
            raise TushareClientError(
                f"Tushare API '{api_name}' returned None. This typically "
                "means rate-limit / insufficient account points. "
                "Wait and retry, or upgrade the Tushare account tier."
            )

        return result

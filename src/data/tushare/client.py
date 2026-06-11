"""Thin wrapper around :mod:`tushare`'s ``pro_api``.

Why this module exists
----------------------
- Centralises ``TUSHARE_TOKEN`` discovery so every caller goes through
  the same env-var lookup. Hard-coding the token in YAML or constructor
  arguments is forbidden — secrets do not belong in committed config.
- Lazy-imports ``tushare`` so importing the package in a contract-only
  test environment (no network, no token, no extras) does not blow up.
- Reuses the underlying ``pro_api`` handle per wrapper instance so long
  fetch loops do not rebuild the client for every date/API call.
- Normalises Tushare's mixed error surface (``TushareError`` for some
  paths, generic ``Exception`` for others, plain ``None`` returns for
  rate-limit failures) into a single :class:`TushareClientError`, and
  STAMPS each error with a structured ``kind`` classified from the RAW
  vendor failure (P3-7). The kind — not the human-readable message — is
  what retry policy keys on: the previous design appended a generic
  "Common causes: rate limit … or transient network error" suffix to
  every message, which made the fetcher's substring-based retryability
  check classify EVERY failure (including invalid token / missing
  permission / bad params) as retryable, so the P3-4a fast-abort path
  for non-retryable errors was unreachable in production.

This module does NOT retry or implement any per-API knowledge. Higher layers
(e.g. :class:`TushareFetcher`) own those concerns; the client is just a typed
boundary.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from src.core.logger import get_logger

_logger = get_logger(__name__)


_TOKEN_ENV_VAR = "TUSHARE_TOKEN"

# Structured failure kinds stamped on :class:`TushareClientError` (P3-7).
# Retry POLICY lives in the fetcher (`TushareFetcher._is_retryable_error`);
# the client only states the classified FACT.
KIND_RATE_LIMIT = "rate_limit"  # quota window exhaustion — time recovers it
KIND_NETWORK = "network"  # transport-level transient (DNS / reset / timeout)
KIND_SERVER_ERROR = "server_error"  # 5xx / vendor-side transient
KIND_AUTH = "auth"  # invalid token / missing permission / tier — operator action
KIND_PARAM = "param"  # malformed or missing parameter — caller bug
KIND_ENVIRONMENT = "environment"  # local setup (no token in env, SDK missing)
KIND_UNKNOWN = "unknown"  # classified, but matched no known failure shape


class TushareClientError(RuntimeError):
    """Raised on any Tushare-side failure: missing token, import error,
    rate limit, malformed payload. The error message names the cause so
    callers don't have to guess from stack traces.

    ``kind`` is the structured classification (one of the module-level
    ``KIND_*`` constants), stamped at wrap time from the RAW underlying
    failure — before any wrapper prose could pollute substring matching.
    ``kind=None`` marks an error constructed without classification
    (legacy / direct constructions); consumers fall back to message
    heuristics for those only.
    """

    def __init__(self, message: str, *, kind: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind


def classify_tushare_failure(error_text: str) -> str:
    """Classify a RAW Tushare/SDK failure string into a ``KIND_*`` constant.

    ``error_text`` should be ``f"{type(exc).__name__}: {exc}"`` of the
    UNDERLYING exception — never an already-wrapped message — so the
    substrings examined are the vendor's own words (plus the exception
    type name, which carries the signal for message-less transport errors
    like ``ConnectionError``).

    Precedence matters and is deliberate:

    1. SPECIFIC rate-limit phrases first. Tushare's real quota message
       ("抱歉，您每分钟最多访问该接口500次，权限的具体详情访问…") also
       contains "权限", so checking auth tokens first would misclassify
       routine quota exhaustion as a fatal permission error and abort
       multi-hour runs on a transient.
    2. Auth / permission / account-tier ("token", "权限", "积分", …) —
       recovery requires operator action, not time.
    3. Parameter errors — caller bugs; identical on every retry.
    4. BROAD legacy rate-limit tokens ("rate", "limit", "returned none").
    5. Transient network (type names + transport phrases).
    6. 5xx / vendor-side transient (numeric codes checked late so e.g. a
       ticker "000503.SZ" quoted inside an auth/param message cannot
       shadow the earlier, more specific classes).

    Anything unmatched is :data:`KIND_UNKNOWN` — the fetcher treats that
    as non-retryable (P3-4a stance: an unrecognized failure aborts fast
    and loud rather than burning the retry budget per unit).
    """
    msg = error_text.lower()
    if any(t in msg for t in (
        "每分钟最多访问", "每天最多访问", "最多访问该接口", "访问频率",
        "rate limit", "too many requests", "429",
    )):
        return KIND_RATE_LIMIT
    if any(t in msg for t in (
        "token", "权限", "permission", "unauthorized", "认证", "积分",
    )):
        return KIND_AUTH
    if any(t in msg for t in ("参数", "parameter", "missing", "required")):
        return KIND_PARAM
    if any(t in msg for t in ("rate", "limit", "returned none", "返回 none")):
        return KIND_RATE_LIMIT
    if any(t in msg for t in (
        "connection", "timeout", "timed out", "max retries exceeded",
        "httpconnectionpool", "httpsconnectionpool", "网络",
    )):
        return KIND_NETWORK
    if any(t in msg for t in (
        "502", "503", "504", "bad gateway", "gateway time-out",
        "service unavailable", "服务异常", "服务繁忙",
    )):
        return KIND_SERVER_ERROR
    return KIND_UNKNOWN


@dataclass(frozen=True)
class TushareClient:
    """Thin handle bound to a Tushare token.

    Construct via :meth:`from_environment` to enforce the
    "secrets via env, not via constructor literal" boundary. The
    raw constructor exists so unit tests can inject a known token
    without depending on the environment, but production code should
    not call it directly.

    The default ``@dataclass`` ``__repr__`` would print
    ``TushareClient(token='1a2b3c...')`` verbatim, which lands the
    secret in any logger / exception message that quotes the client.
    We override ``__repr__`` to mask the token so it is safe to
    surface in tracebacks and ``logging.error("client=%r", client)``
    style logs.
    """

    token: str
    _pro_client: Any = field(default=None, init=False, repr=False, compare=False)

    def __repr__(self) -> str:
        # Show only the token's length and a 4-char prefix so a
        # debugger can still confirm "this is the right env var"
        # without leaking the full secret. Empty-token clients are
        # rejected upstream by ``from_environment``; the raw
        # constructor allows them for test injection, in which case
        # we still mask defensively.
        token = getattr(self, "token", "") or ""
        if not token:
            return "TushareClient(token='<empty>')"
        prefix = token[:4]
        return f"TushareClient(token='{prefix}***' len={len(token)})"

    @classmethod
    def from_environment(cls) -> TushareClient:
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
                f"export {_TOKEN_ENV_VAR}='your_pro_token_here'.",
                kind=KIND_ENVIRONMENT,
            )
        return cls(token=token)

    def call(self, api_name: str, **params: Any) -> Any:
        """Invoke a Tushare ``pro_api`` endpoint and return the DataFrame.

        Lazy-imports ``tushare`` on first call so module import does not
        require the dependency. Translates every Tushare-side exception
        into :class:`TushareClientError` with the API name embedded and a
        structured ``kind`` classified from the RAW underlying failure.
        The original vendor error text is PRESERVED verbatim — no generic
        "common causes" prose is appended (P3-7: that suffix used to make
        every failure substring-match as retryable downstream).
        """
        try:
            import tushare as ts
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise TushareClientError(
                "tushare is not installed. Run: python -m pip install -e \".[tushare]\"",
                kind=KIND_ENVIRONMENT,
            ) from exc

        pro = self._pro_client
        if pro is None:
            try:
                pro = ts.pro_api(self.token)
            except Exception as exc:
                raise TushareClientError(
                    f"Failed to construct Tushare pro client (api='{api_name}'): "
                    f"{type(exc).__name__}: {exc}. Check that TUSHARE_TOKEN is "
                    "valid and your account has Pro permissions.",
                    kind=classify_tushare_failure(f"{type(exc).__name__}: {exc}"),
                ) from exc
            object.__setattr__(self, "_pro_client", pro)

        method = getattr(pro, api_name, None)
        if method is None or not callable(method):
            raise TushareClientError(
                f"Tushare pro_api has no callable named {api_name!r}. "
                "Check the API name spelling in the Tushare docs.",
                kind=KIND_PARAM,
            )

        try:
            result = method(**params)
        except Exception as exc:
            raise TushareClientError(
                f"Tushare API '{api_name}' raised {type(exc).__name__}: {exc}",
                kind=classify_tushare_failure(f"{type(exc).__name__}: {exc}"),
            ) from exc

        if result is None:
            # Tushare returns None on some rate-limited / quota-exceeded
            # responses without raising. Treat as an explicit failure
            # rather than letting an empty DataFrame propagate.
            raise TushareClientError(
                f"Tushare API '{api_name}' returned None. This typically "
                "means rate-limit / insufficient account points. "
                "Wait and retry, or upgrade the Tushare account tier.",
                kind=KIND_RATE_LIMIT,
            )

        return result

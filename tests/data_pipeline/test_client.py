"""Tests for ``src.data.tushare.client`` — structured failure classification.

P3-7: the client stamps every :class:`TushareClientError` with a ``kind``
classified from the RAW vendor failure, and no longer appends the generic
"Common causes: rate limit … or transient network error." prose that used to
make every wrapped message substring-match as retryable downstream.

Covered:

- ``classify_tushare_failure`` on REAL-FORM Tushare / requests error bodies
  (Chinese quota / permission / token / param messages, transport exception
  type names, 5xx bodies) — including the precedence trap: Tushare's genuine
  rate-limit body also contains "权限".
- ``TushareClient.call`` wrap behaviour against a faked ``tushare`` module:
  original vendor text preserved verbatim, no "Common causes" prose, correct
  ``kind`` on every failure path (SDK exception / None return / no-such-API).
- ``from_environment`` missing-token path carries ``kind=environment``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tushare.client import (  # noqa: E402
    KIND_AUTH,
    KIND_ENVIRONMENT,
    KIND_NETWORK,
    KIND_PARAM,
    KIND_RATE_LIMIT,
    KIND_SERVER_ERROR,
    KIND_UNKNOWN,
    TushareClient,
    TushareClientError,
    classify_tushare_failure,
)


class ClassifyTushareFailureTests(unittest.TestCase):
    """Table-driven classification of real-form failure strings.

    Inputs are shaped as the client builds them: ``f"{type(exc).__name__}:
    {exc}"`` of the UNDERLYING exception — the type name carries the signal
    for message-poor transport errors.
    """

    CASES: tuple[tuple[str, str], ...] = (
        # --- rate limit: the REAL Tushare quota bodies. Note both contain
        # "权限" / tier wording — the specific quota phrases must win.
        ("Exception: 抱歉，您每分钟最多访问该接口500次，权限的具体详情访问："
         "https://tushare.pro/document/1?doc_id=108", KIND_RATE_LIMIT),
        ("Exception: 抱歉，您每天最多访问该接口20000次，权限的具体详情访问："
         "https://tushare.pro/document/1?doc_id=108", KIND_RATE_LIMIT),
        ("Exception: HTTP 429 Too Many Requests", KIND_RATE_LIMIT),
        # --- auth / permission / tier: operator action required.
        ("Exception: 抱歉，您没有访问该接口的权限", KIND_AUTH),
        ("Exception: token无效，请确认设置的token是否正确", KIND_AUTH),
        ("Exception: 请设置tushare pro的token凭证码，如果没有可以注册申请",
         KIND_AUTH),
        ("Exception: 您的积分不足，无法调取该接口", KIND_AUTH),
        ("Exception: 401 Unauthorized", KIND_AUTH),
        # --- param: caller bug, identical on every retry.
        ("Exception: 抱歉，参数错误，请检查输入参数", KIND_PARAM),
        ("TypeError: daily() missing 1 required positional argument",
         KIND_PARAM),
        # --- network: type name carries the signal for transport errors.
        ("ConnectionError: HTTPSConnectionPool(host='api.waditu.com', "
         "port=443): Max retries exceeded with url: /dataapi",
         KIND_NETWORK),
        ("ConnectionResetError: [WinError 10054] 远程主机强迫关闭了一个现有的连接",
         KIND_NETWORK),
        ("ReadTimeout: HTTPSConnectionPool(host='api.waditu.com', port=443): "
         "Read timed out. (read timeout=30)", KIND_NETWORK),
        ("Exception: 网络异常，请稍后重试", KIND_NETWORK),
        # --- server-side transient.
        ("Exception: 502 Bad Gateway", KIND_SERVER_ERROR),
        ("Exception: 服务繁忙，请稍后重试", KIND_SERVER_ERROR),
        ("Exception: 服务异常", KIND_SERVER_ERROR),
        # --- unknown: matched nothing → downstream treats as non-retryable.
        ("SomeVendorError: quux frobnicated", KIND_UNKNOWN),
    )

    def test_real_form_messages_classify_correctly(self) -> None:
        for raw, expected in self.CASES:
            with self.subTest(raw=raw[:48]):
                self.assertEqual(classify_tushare_failure(raw), expected)

    def test_quota_phrase_outranks_auth_tokens(self) -> None:
        # The precedence trap in isolation: a string containing BOTH the
        # specific quota phrase AND auth words must be rate_limit.
        self.assertEqual(
            classify_tushare_failure("每分钟最多访问 … 权限 … token"),
            KIND_RATE_LIMIT,
        )

    def test_auth_outranks_param_and_broad_tokens(self) -> None:
        # A token error that also mentions a parameter must be auth — the
        # operator-action signal dominates.
        self.assertEqual(
            classify_tushare_failure("Exception: token无效 (missing parameter?)"),
            KIND_AUTH,
        )


class _FakePro:
    """Duck-typed ``pro_api`` handle: attribute lookup returns the injected
    endpoint callables; anything else is absent (→ the no-such-API path)."""

    def __init__(self, **endpoints):
        for name, fn in endpoints.items():
            setattr(self, name, fn)


def _fake_tushare(pro: _FakePro) -> SimpleNamespace:
    return SimpleNamespace(pro_api=lambda token: pro)


class ClientCallWrapTests(unittest.TestCase):
    """``TushareClient.call`` against a faked SDK: message fidelity + kind."""

    def _call_and_catch(self, pro: _FakePro, api: str) -> TushareClientError:
        client = TushareClient(token="test-token-123")
        with patch.dict(sys.modules, {"tushare": _fake_tushare(pro)}):
            with self.assertRaises(TushareClientError) as ctx:
                client.call(api, ts_code="600000.SH")
        return ctx.exception

    def test_vendor_exception_preserved_verbatim_no_boilerplate(self) -> None:
        def boom(**params):
            raise Exception("抱歉，您没有访问该接口的权限")

        err = self._call_and_catch(_FakePro(daily=boom), "daily")
        text = str(err)
        self.assertIn("抱歉，您没有访问该接口的权限", text)  # original kept
        self.assertIn("daily", text)  # api name embedded
        self.assertNotIn("Common causes", text)  # boilerplate gone (P3-7)
        self.assertNotIn("rate limit (account tier too low)", text)
        self.assertEqual(err.kind, KIND_AUTH)

    def test_quota_exception_wraps_as_rate_limit(self) -> None:
        def boom(**params):
            raise Exception(
                "抱歉，您每分钟最多访问该接口500次，权限的具体详情访问："
                "https://tushare.pro/document/1?doc_id=108"
            )

        err = self._call_and_catch(_FakePro(daily=boom), "daily")
        self.assertEqual(err.kind, KIND_RATE_LIMIT)

    def test_transport_exception_wraps_as_network(self) -> None:
        def boom(**params):
            raise ConnectionError(
                "HTTPSConnectionPool(host='api.waditu.com', port=443): "
                "Max retries exceeded"
            )

        err = self._call_and_catch(_FakePro(daily=boom), "daily")
        self.assertEqual(err.kind, KIND_NETWORK)

    def test_none_return_wraps_as_rate_limit(self) -> None:
        err = self._call_and_catch(
            _FakePro(daily=lambda **params: None), "daily",
        )
        self.assertEqual(err.kind, KIND_RATE_LIMIT)
        self.assertIn("returned None", str(err))

    def test_missing_api_wraps_as_param(self) -> None:
        err = self._call_and_catch(_FakePro(), "definitely_not_an_api")
        self.assertEqual(err.kind, KIND_PARAM)

    def test_from_environment_missing_token_is_environment_kind(self) -> None:
        with patch.dict("os.environ", {"TUSHARE_TOKEN": ""}, clear=False):
            with self.assertRaises(TushareClientError) as ctx:
                TushareClient.from_environment()
        self.assertEqual(ctx.exception.kind, KIND_ENVIRONMENT)

    def test_legacy_construction_without_kind_defaults_none(self) -> None:
        # Existing call sites construct TushareClientError(message) directly;
        # they must keep working and carry kind=None (→ substring fallback
        # downstream).
        err = TushareClientError("bare legacy message")
        self.assertIsNone(err.kind)


if __name__ == "__main__":
    unittest.main()

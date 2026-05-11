"""Tests for ``src.data.tushare.client`` — Tushare API boundary."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tushare.client import (  # noqa: E402
    _TOKEN_ENV_VAR,
    TushareClient,
    TushareClientError,
)

# ---------------------------------------------------------------------
# Helpers — install a fake ``tushare`` module so the client's lazy
# import resolves to a known shape during tests.
# ---------------------------------------------------------------------


def _install_fake_tushare(pro_factory):
    """Build a fake ``tushare`` module with ``pro_api(token) -> object``.

    ``pro_factory`` is a callable ``(token) -> any-object``; the returned
    module will be the one ``import tushare`` resolves to inside the
    client's lazy import. Tests use this to inject specific behaviours
    (rate-limit None, attribute-error endpoint, etc.) without going
    near the network.
    """
    fake = types.ModuleType("tushare")
    fake.pro_api = pro_factory  # type: ignore[attr-defined]
    return fake


class TushareClientFromEnvironmentTests(unittest.TestCase):
    """``from_environment`` is the only construction path production
    code is meant to use; bypassing it lets tokens slip into source
    files. These tests pin its env-var contract.
    """

    def test_reads_token_from_env(self) -> None:
        with patch.dict("os.environ", {_TOKEN_ENV_VAR: "abc123"}, clear=False):
            client = TushareClient.from_environment()
        self.assertEqual(client.token, "abc123")

    def test_strips_whitespace(self) -> None:
        with patch.dict("os.environ", {_TOKEN_ENV_VAR: "  abc123  "}, clear=False):
            client = TushareClient.from_environment()
        self.assertEqual(client.token, "abc123")

    def test_rejects_unset_env_var(self) -> None:
        env_without_token = {
            k: v for k, v in __import__("os").environ.items()
            if k != _TOKEN_ENV_VAR
        }
        with patch.dict("os.environ", env_without_token, clear=True):
            with self.assertRaisesRegex(TushareClientError, _TOKEN_ENV_VAR):
                TushareClient.from_environment()

    def test_rejects_empty_string(self) -> None:
        with patch.dict("os.environ", {_TOKEN_ENV_VAR: ""}, clear=False):
            with self.assertRaisesRegex(TushareClientError, _TOKEN_ENV_VAR):
                TushareClient.from_environment()

    def test_rejects_whitespace_only(self) -> None:
        with patch.dict("os.environ", {_TOKEN_ENV_VAR: "    "}, clear=False):
            with self.assertRaisesRegex(TushareClientError, _TOKEN_ENV_VAR):
                TushareClient.from_environment()


class TushareClientCallTests(unittest.TestCase):
    """``client.call`` must normalise every Tushare-side failure into
    :class:`TushareClientError` with the API name embedded."""

    def test_happy_path_returns_dataframe_like(self) -> None:
        captured: dict = {}

        class _FakePro:
            def index_classify(self, **params):
                captured.update(params)
                return "DF"

        with patch.dict("sys.modules", {
            "tushare": _install_fake_tushare(lambda token: _FakePro()),
        }):
            client = TushareClient(token="t")
            result = client.call("index_classify", level="L2")
        self.assertEqual(result, "DF")
        self.assertEqual(captured, {"level": "L2"})

    def test_reuses_pro_api_handle_between_calls(self) -> None:
        constructed_tokens: list[str] = []

        class _FakePro:
            def daily(self, **params):
                return params["value"]

            def adj_factor(self, **params):
                return params["value"]

        def _factory(token):
            constructed_tokens.append(token)
            return _FakePro()

        with patch.dict("sys.modules", {
            "tushare": _install_fake_tushare(_factory),
        }):
            client = TushareClient(token="t")
            self.assertEqual(client.call("daily", value=1), 1)
            self.assertEqual(client.call("adj_factor", value=2), 2)

        self.assertEqual(constructed_tokens, ["t"])

    def test_unknown_api_name_raises(self) -> None:
        class _FakePro:
            pass  # no methods at all

        with patch.dict("sys.modules", {
            "tushare": _install_fake_tushare(lambda token: _FakePro()),
        }):
            with self.assertRaisesRegex(TushareClientError, "no callable named"):
                TushareClient(token="t").call("nonexistent")

    def test_pro_api_construction_failure_raises(self) -> None:
        def _broken_factory(token):
            raise RuntimeError("invalid token")

        with patch.dict("sys.modules", {
            "tushare": _install_fake_tushare(_broken_factory),
        }):
            with self.assertRaisesRegex(
                TushareClientError, "Failed to construct Tushare pro client"
            ):
                TushareClient(token="t").call("any_api")

    def test_api_exception_wrapped_with_context(self) -> None:
        class _FakePro:
            def index_member(self, **params):
                raise ValueError("rate limited")

        with patch.dict("sys.modules", {
            "tushare": _install_fake_tushare(lambda token: _FakePro()),
        }):
            with self.assertRaisesRegex(
                TushareClientError, "index_member.*ValueError.*rate limited"
            ):
                TushareClient(token="t").call("index_member", index_code="X")

    def test_none_response_raises(self) -> None:
        """Tushare returns ``None`` on some quota-exceeded responses
        without raising. The client must turn that into an error so
        callers don't propagate an empty DataFrame."""
        class _FakePro:
            def index_classify(self, **params):
                return None

        with patch.dict("sys.modules", {
            "tushare": _install_fake_tushare(lambda token: _FakePro()),
        }):
            with self.assertRaisesRegex(
                TushareClientError, "returned None"
            ):
                TushareClient(token="t").call("index_classify")


class TushareClientReprMaskingTests(unittest.TestCase):
    """Pin the secret-masking behaviour of ``TushareClient.__repr__``.

    The dataclass auto-generated ``__repr__`` would print the raw token
    verbatim, which lands the secret in:

    - exception messages (``raise SomeError(f"client={client!r} ...")``)
    - logger calls (``_logger.error("client=%r", client)``)
    - REPL / pytest tracebacks

    The custom ``__repr__`` must mask everything past a 4-char prefix.
    """

    def test_repr_masks_full_token(self) -> None:
        client = TushareClient(token="super_secret_pro_token_12345")
        rendered = repr(client)
        self.assertNotIn("super_secret_pro_token_12345", rendered)
        # Prefix and length should be visible — those help debugging
        # ("did the env var resolve?") without disclosing the secret.
        self.assertIn("supe***", rendered)
        self.assertIn(f"len={len('super_secret_pro_token_12345')}", rendered)

    def test_repr_handles_short_token(self) -> None:
        """A 1-3 char token still must not leak the full string."""
        client = TushareClient(token="abc")
        rendered = repr(client)
        # The prefix is whatever ``token[:4]`` returns (here, the
        # whole token). The mask + length disclosure is what stops
        # this from being a secret leak: no logger reader can
        # reconstruct a longer token from a shortened repr.
        self.assertIn("***", rendered)
        self.assertIn("len=3", rendered)

    def test_repr_handles_empty_token_defensively(self) -> None:
        """The raw constructor allows an empty token for test injection
        (``from_environment`` rejects it). Empty case still must not
        crash repr."""
        client = TushareClient(token="")
        rendered = repr(client)
        self.assertIn("<empty>", rendered)


if __name__ == "__main__":
    unittest.main()

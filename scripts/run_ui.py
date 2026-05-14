#!/usr/bin/env python
"""Launch the Streamlit operator UI for the qlib trading system.

Usage:
    python scripts/run_ui.py
    python scripts/run_ui.py --server.port 8502
    python scripts/run_ui.py --server.address 0.0.0.0  # explicit remote access
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_PATH = _REPO_ROOT / "web" / "operator_ui" / "app.py"
_SERVER_ADDRESS_FLAG = "--server.address"
_DEFAULT_SERVER_ADDRESS = "127.0.0.1"


def _streamlit_args(argv: list[str]) -> list[str]:
    """Build Streamlit CLI args, defaulting the UI to loopback only."""
    has_address = any(
        arg == _SERVER_ADDRESS_FLAG or arg.startswith(f"{_SERVER_ADDRESS_FLAG}=")
        for arg in argv
    )
    address_args = [] if has_address else [_SERVER_ADDRESS_FLAG, _DEFAULT_SERVER_ADDRESS]
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(_APP_PATH),
        *address_args,
        *argv,
    ]


def main() -> None:
    result = subprocess.run(
        _streamlit_args(sys.argv[1:]),
        cwd=_REPO_ROOT,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()

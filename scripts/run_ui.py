#!/usr/bin/env python
"""Launch the Streamlit operator UI for the qlib trading system.

Usage:
    python scripts/run_ui.py
    python scripts/run_ui.py --server.port 8502
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_PATH = _REPO_ROOT / "web" / "operator_ui" / "app.py"


def main() -> None:
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(_APP_PATH), *sys.argv[1:]],
        cwd=_REPO_ROOT,
    )


if __name__ == "__main__":
    main()

"""The ONE way to spawn a python child whose stdout is UTF-8.

A child python inherits the OS locale and ENCODES its output with it
(cp936 on a CN Windows box), so pinning only the parent-side
``encoding="utf-8"`` trades mojibake for a ``UnicodeDecodeError`` on the
first em dash. Both ends of the pipe must be pinned.

Why this is a function in one module rather than a dict literal at each
call site: the governance gate
(``tests/governance/test_subprocess_text_pins_utf8.py``) has to be able to
PROVE a spawn is safe, and proving properties of arbitrary inline dicts by
AST is a losing game — ``{"PYTHONIOENCODING": "utf-8", **os.environ}``
looks pinned and is not (the later unpacking wins), a helper with a
conditional return can fall through to ``None``, a parameter can shadow a
module constant. Concentrating the logic here turns the gate's job into an
unfoolable syntactic check ("does the call pass ``env=utf8_child_env()``?")
and moves the CORRECTNESS question to a behavioral test that actually
spawns a child and reads back a non-ASCII round trip
(``tests/logic/test_child_env.py``).

The pin is written with an ASSIGNMENT after the copy, never as a dict
literal entry, so no merge-order subtlety can silently drop it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: The env var that fixes a python child's stdout/stderr encoder.
CHILD_ENCODING_VAR = "PYTHONIOENCODING"
CHILD_ENCODING = "utf-8"


def utf8_child_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """``base`` (default: the current environment) with the child's stdout
    encoder pinned to UTF-8.

    Pass the result as ``env=`` to any ``subprocess`` call whose child is a
    PYTHON interpreter. Not needed for ``git`` children: git emits UTF-8
    regardless of locale.
    """
    env = dict(os.environ if base is None else base)
    env[CHILD_ENCODING_VAR] = CHILD_ENCODING  # last write wins, by construction
    return env

"""Console-encoding safety for the pv_incremental_v1 CLI tools.

These tools are fail-loud by design: every refusal carries the REASON
in its message. That guarantee is only as good as the console's
ability to print it — and the messages are not pure ASCII. They carry
``—`` in nearly every refusal, ``∪``/``②`` in the exporter, and the
Chinese ledger text in the registrar.

Which of those survive depends entirely on the machine:

* the operator's box runs a cp936 console — all of the above encode;
* the GitHub Windows runner runs cp1252 — ``—`` encodes, ``∪``/``②``
  and every Chinese character raise ``UnicodeEncodeError``;
* a redirected pipe under a POSIX ``C`` locale falls back to ASCII —
  then even ``—`` raises.

An unencodable character in a refusal message does not merely garble
output: ``print`` raises, so the operator is handed a codec traceback
INSTEAD of the reason the batch was refused, and a tool that was
partway through its work dies on the message rather than on the
condition. codex #404 caught this on the ``--help`` path (argparse
renders the module docstring, which contains ``→``); the refusal path
has exactly the same exposure.

``backslashreplace`` is the minimal fix that cannot regress anything:
characters the console CAN encode still render verbatim (cp936 keeps
showing the Chinese ledger text as before), and the rest degrade to
``\\uXXXX`` escapes instead of aborting. Nothing is silenced.
"""

from __future__ import annotations

import sys


def make_console_safe() -> None:
    """Make stdout/stderr degrade rather than raise on unencodable text.

    Call as the first statement of a CLI ``main()`` — before argparse
    can print help or usage, and before any refusal is rendered.
    Idempotent, and a no-op on streams that cannot be reconfigured
    (pytest's capture objects, ``StringIO``, a detached stream).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError):
            # Detached, or a text wrapper that refuses reconfiguration:
            # nothing to do, and failing here would defeat the purpose.
            continue

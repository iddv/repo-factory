"""Which Claude Code binary repo-factory runs (WZ-DESIGN §2.8).

``build.claude_command`` in loop.yaml, written by ``repo-factory setup --claude PATH`` or by the wizard
when it found Claude Code off PATH (the native installer's ``~/.local/bin/claude``, npm's global
folder), else ``claude`` on PATH. Builds (the sandbox), Open in Claude Code and the single-shot
calls (the pitch, the research desk) all ask here, so one setting moves all three.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def configured(cfg=None) -> str | None:
    """``build.claude_command`` (``~`` expanded); None when it is not set or loop.yaml cannot be
    read. ``cfg``: a loaded ``Config`` (else the loop home's)."""
    if cfg is None:
        try:
            from .settings.store import Config

            cfg = Config()
        except Exception:  # noqa: BLE001 - a broken loop.yaml is the doctor's to say
            return None
    got = (cfg.get("build") or {}).get("claude_command")
    return str(Path(str(got)).expanduser()) if got else None


def configured_exe(cfg=None) -> str | None:
    """The configured binary when it is an executable file; None otherwise (then PATH is used,
    and ``repo-factory doctor`` says the setting names nothing)."""
    c = configured(cfg)
    return c if c and os.path.isfile(c) and os.access(c, os.X_OK) else None


def find(cfg=None) -> str | None:
    """The binary to run: the configured one, else ``claude`` on PATH; None when neither."""
    return configured_exe(cfg) or shutil.which("claude")

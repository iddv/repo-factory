"""Small helpers the login and sandbox tests share. No model, no network."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest


def canary(tag: str = "tok") -> str:
    """A token-shaped value made at run time (never a literal)."""
    return tag + "-" + "x" + secrets.token_hex(16)


def needs_bwrap():
    """Skip without bubblewrap, except where it is required (CI's sandbox job)."""
    from repo_factory.harness.isolation import isolation

    if isolation() == "bwrap":
        return
    if os.environ.get("REPO_FACTORY_BWRAP_REQUIRED") == "1":
        import tempfile

        from repo_factory.harness.isolation import bwrap_cmd

        why = "no bwrap on PATH"
        if shutil.which("bwrap"):
            with tempfile.TemporaryDirectory() as tmp:
                cmd = bwrap_cmd(shutil.which("bwrap"), Path(tmp)) + ["-I", "-c", "pass"]
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                why = f"exit {p.returncode}: {p.stderr.strip()[:500]}; command: {cmd}"
        pytest.fail(
            "bubblewrap is required here (REPO_FACTORY_BWRAP_REQUIRED=1) and its "
            f"probe fails: {why}"
        )
    pytest.skip("needs bubblewrap")

"""repo-factory's shell prefix for every ``claude`` with a shell (``CLAUDE_CODE_SHELL_PREFIX``): the
script ``factory-shell`` beside this module, which runs each tool command, and each MCP server,
without a Claude login and, inside bubblewrap, in a nested user, pid and mount namespace (its
header says how). Builds use one namespace per sandbox (``session``) and keep the session's
variables but the login's; Open in Claude Code uses one per command (``command``) with only a
short list of variables. Without bubblewrap (``plain``) only the environment is scrubbed.

``install`` puts an executable copy outside the package (a wheel need not keep the file's
mode); the sandbox binds it read-only at ``INSIDE``."""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

SCRIPT = Path(__file__).with_name("factory-shell")
INSIDE = "/opt/repo_factory/bin/factory-shell"  # sandbox.OPT/bin
KINDS = {
    "build": ("session", "keep"),
    "term": ("command", "strict"),
    "plain": ("plain", "keep"),
    "term-plain": ("plain", "strict"),
}


def install(dest: Path | None = None) -> Path:
    """An executable copy of the script (0755): ``dest``, else one named by its content under
    ``~/.cache/repo_factory/shell/``. Rewritten only when missing or different."""
    data = SCRIPT.read_bytes()
    if dest is None:
        home = Path(os.environ.get("HOME") or Path.home())
        dest = (
            home
            / ".cache"
            / "repo_factory"
            / "shell"
            / f"factory-shell-{hashlib.sha256(data).hexdigest()[:12]}"
        )
    dest = Path(dest)
    try:
        if dest.read_bytes() == data and os.access(dest, os.X_OK):
            return dest
    except OSError:
        pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o755)
    os.replace(tmp, dest)
    return dest


def env(kind: str = "build", prefix: str = INSIDE) -> dict:
    """The variables that put a claude's tool commands through the prefix: ``kind`` one of
    ``KINDS``; ``prefix``: where the script is, as the claude process sees it."""
    mode, keep = KINDS[kind]
    return {
        "CLAUDE_CODE_SHELL_PREFIX": str(prefix),
        "REPO_FACTORY_SHELL": mode,
        "REPO_FACTORY_SHELL_ENV": keep,
    }

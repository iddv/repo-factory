"""repo-factory's own store for the Claude login builds use (builds run on Claude Code and
the person's Claude plan by default).

``claude setup-token`` prints a long-lived Claude login token; Settings › Claude keeps it
here, so nothing else needs to change:

    <home>/claude/                   0700 (``$REPO_FACTORY_CLAUDE_DIR`` for tests)
        login.env                    CLAUDE_CODE_OAUTH_TOKEN=… at 0600
        login.json                   when it was kept, and how: no secret, ever

The token is read at each launch by ``harness/handoff.py`` and goes to ``claude`` through a pipe,
never in an environment or on a command line (R1). It never reaches a page, a log, argv or this
process's environment: ``status`` is an allowlist of fields. The build sandbox binds no part
of the home's ``claude/`` folder. A file other users can read is not used (the page says to
chmod it). Nothing here calls a model."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

DIR_ENV = "REPO_FACTORY_CLAUDE_DIR"
VAR = "CLAUDE_CODE_OAUTH_TOKEN"
# one line of printable ASCII without spaces or '=' tricks: it cannot add a second line to
# login.env. A Claude login token is about a hundred characters.
_SHAPE = re.compile(r"[A-Za-z0-9_\-.~+/]{20,400}")
API_KEY_PREFIX = "sk-ant-api"


class LoginError(ValueError):
    """A token repo-factory will not keep, in words for the page and the command line."""


def store_dir(env: dict | None = None, home: Path | None = None) -> Path:
    """``$REPO_FACTORY_CLAUDE_DIR``, else ``<home>/claude`` (``home``: the repo-factory home,
    default ``$REPO_FACTORY_HOME`` or ``~/.repo-factory``)."""
    from . import paths

    env = os.environ if env is None else env
    if env.get(DIR_ENV):
        return Path(env[DIR_ENV]).expanduser()
    return paths.Layout(paths.home(home or env.get(paths.HOME_ENV))).claude


def check(token: str) -> str:
    """The token as it will be kept, or LoginError."""
    token = (token or "").strip()
    if not token:
        raise LoginError("paste the token 'claude setup-token' printed")
    if token.startswith(API_KEY_PREFIX):
        raise LoginError(
            "that is a Claude API key, not a login token: run 'claude "
            "setup-token' for your Claude plan"
        )
    if not _SHAPE.fullmatch(token):
        raise LoginError("that does not look like a Claude login token: one line, no spaces")
    return token


class Store:
    """``login.env`` and ``login.json`` in the store's folder."""

    def __init__(self, root: Path | None = None, env: dict | None = None):
        self.root = Path(root) if root else store_dir(env)

    login_env = property(lambda self: self.root / "login.env")
    login_json = property(lambda self: self.root / "login.json")

    def loose(self) -> bool:
        """``login.env`` is there and other users can read it (it is then not used)."""
        try:
            return bool(self.login_env.stat().st_mode & 0o077)
        except OSError:
            return False

    def token(self) -> str | None:
        """The kept token, or None (no file, a file others can read, or no token line)."""
        try:
            if self.login_env.stat().st_mode & 0o077:
                return None
            text = self.login_env.read_text()
        except OSError:
            return None
        for line in text.splitlines():
            k, _, v = line.partition("=")
            if k.strip() == VAR and v.strip():
                return v.strip()
        return None

    def keep(self, token: str, how: str = "page") -> dict:
        """Keep ``token`` (``check``ed) at 0600 in a 0700 folder; returns ``status``."""
        token = check(token)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        _write(self.login_env, f"{VAR}={token}\n")
        _write(self.login_json, json.dumps({"kept": time.time(), "how": how}) + "\n")
        return self.status()

    def forget(self) -> bool:
        had = self.login_env.exists()
        for p in (self.login_env, self.login_json):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        return had

    def status(self) -> dict:
        """What the page and the doctor may know: whether a token is kept, since when and how
        (never the token)."""
        try:
            meta = json.loads(self.login_json.read_text())
        except (OSError, ValueError):
            meta = {}
        meta = meta if isinstance(meta, dict) else {}
        kept = self.token() is not None
        return {
            "stored": kept,
            "loose": self.loose(),
            "kept_at": meta.get("kept") if kept else None,
            "how": meta.get("how") if kept else None,
            "path": str(self.login_env),
        }


def _write(path: Path, text: str) -> None:
    """Write ``text`` at mode 600, atomically (as tokens.py and github_app do)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

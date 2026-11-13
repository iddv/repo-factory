"""The ``gh`` command line as repo-factory calls it: ``Gh`` (the owner's login, a token file, or
repo-factory's own GitHub App's token for each subprocess) and ``token_env``. Every part of repo-factory that
acts on GitHub (publishing, maintenance, projects, the GitHub App, lessons) uses this one
wrapper. ``PublishError`` is the error of a ``gh`` call or a publishing step."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


class PublishError(RuntimeError):
    pass


class TokenError(RuntimeError):
    """A ``token`` callable could not give a token (repo-factory's own GitHub App: ``AppError``). Its
    message is safe to show: it names no token and no key."""


TOKEN_KEYS = ("GH_TOKEN", "GITHUB_TOKEN")


def token_env(path: str | Path) -> dict[str, str]:
    """``GH_TOKEN`` from a token env file (``KEY=VALUE`` lines, ``export`` and quotes allowed;
    ``GITHUB_TOKEN`` is read as ``GH_TOKEN``). The file must be the owner's alone (mode 0600,
    as ssh asks of a key). Only ``Gh``'s subprocess environment ever holds the value: it is
    read for each call and never kept, logged or put in an error message."""
    p = Path(path).expanduser()
    try:
        mode = p.stat().st_mode
        text = p.read_text()
    except OSError as e:
        raise PublishError(f"the token file {p} cannot be read ({e.strerror or 'error'})") from None
    if mode & 0o077:
        raise PublishError(f"the token file {p} is readable by other users: chmod 600 it")
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("export "):
            s = s[len("export ") :].strip()
        key, eq, value = s.partition("=")
        if eq and key.strip() in TOKEN_KEYS and value.strip().strip("'\""):
            return {"GH_TOKEN": value.strip().strip("'\"")}
    raise PublishError(f"the token file {p} has no GH_TOKEN= line")


class Gh:
    """The ``gh`` CLI (logged in as the owner). ``run(args, cwd) -> (code, out, err)`` is
    replaceable for tests. ``env_file``: a token env file (``token_env``) whose ``GH_TOKEN``
    the ``gh`` subprocess gets instead of the login (MAINTAIN.md §6.3, decision D1).
    ``token``: a callable that returns a token for each subprocess instead (repo-factory's own
    GitHub App, ``github_app.GitHubApp.gh``; it raises ``TokenError`` when it cannot), with
    ``login`` the name that identity has without a call (the bot's)."""

    def __init__(
        self,
        run=None,
        cmd: list[str] | None = None,
        env_file: str | Path | None = None,
        token=None,
        login: str | None = None,
    ):
        self.cmd = list(cmd or ["gh"])
        self.env_file = Path(env_file).expanduser() if env_file else None
        self.token, self.login_name = token, login
        self._run = run or self._subprocess

    @property
    def credential(self) -> str | None:
        """``token`` (a token file), ``app`` (repo-factory's own GitHub App) or None (the login)."""
        return "app" if self.token else "token" if self.env_file else None

    def env(self) -> dict | None:
        """The environment for a subprocess that acts on GitHub (``gh``, or ``git`` through
        ``gh auth git-credential``): this process's, plus the token when there is a token
        file or an app; None (inherit) otherwise. The token lives in that one dict."""
        if not self.env_file and not self.token:
            return None
        env = {k: v for k, v in os.environ.items() if k not in TOKEN_KEYS}
        env.update(token_env(self.env_file) if self.env_file else {"GH_TOKEN": self.token()})
        return env

    def git_auth(self) -> list[str]:
        """``git -c`` arguments that make git ask this ``gh`` for GitHub credentials (with
        ``env()``), so a push uses the token file or the app's token too; none without."""
        if not self.env_file and not self.token:
            return []
        helper = "!" + " ".join(shlex.quote(c) for c in self.cmd) + " auth git-credential"
        return [
            "-c",
            "credential.https://github.com.helper=",
            "-c",
            f"credential.https://github.com.helper={helper}",
        ]

    def _subprocess(self, args: list[str], cwd: str | None = None) -> tuple[int, str, str]:
        try:
            env = self.env()
        except PublishError as e:  # a broken token file fails like gh would
            return 4, "", str(e)
        except TokenError as e:  # the app cannot mint: say why
            return 4, "", str(e)
        p = subprocess.run(
            [*self.cmd, *args], cwd=cwd, capture_output=True, text=True, timeout=300, env=env
        )
        return p.returncode, p.stdout, p.stderr

    def __call__(self, *args: str, cwd: str | Path | None = None, check: bool = True) -> str:
        code, out, err = self._run(list(args), str(cwd) if cwd else None)
        if check and code != 0:
            raise PublishError(f"gh {' '.join(args[:3])} failed: {(err or out).strip()[:400]}")
        return out if code == 0 else ""

    def login(self) -> str:
        """The login: the bot's name for an app (``GET /user`` is not for an installation
        token), else one ``gh api user`` call."""
        if self.login_name:
            return self.login_name
        return self("api", "user", "-q", ".login").strip()

    def exists(self, full: str) -> bool:
        code, _, _ = self._run(["repo", "view", full, "--json", "name"], None)
        return code == 0

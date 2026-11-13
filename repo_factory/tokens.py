"""Who is calling repo-factory: local tokens, one per door.

The server decides who acts from a credential, never from a request's body. Each door has a
random token under ``<home>/tokens/`` (a directory at mode 700, files at mode 600):

    index.json        {"version": 1, "tokens": {<origin>: {sha256, created, actions, note}}}
    ui.token          the browser's; it reaches the page only as the HttpOnly session cookie
    cli.token         the command line's (scripts run by the owner)
    agent-RC.token    a named agent's
    launch/<sha256>   one-time codes that sign a browser in (``repo-factory ui`` prints one),
                      with their expiry

The index keeps only each token's SHA-256, so the server never needs the values; a client
reads its own value from its file. A token's name is its origin: ``ui``, ``cli`` or
``agent:<name>``; ``actions`` is the list of registry actions an agent may run (null: the
default).

``$REPO_FACTORY_TOKENS_DIR`` names the directory outright (tests). Standard library only.

What this is not: a wall between processes of the same Unix user. Anything running as the
owner can read these files. It makes the caller explicit and attributed, keeps the doors'
limits (the confirm class) from being skipped by claiming another door in a request body, and
keeps other websites and other users out. The build sandboxes never see the home's tokens.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path

DOORS = ("ui", "cli")
COOKIE = "factory_session"
CODE_TTL_S = 600
AGENT = re.compile(r"agent:([A-Za-z0-9][A-Za-z0-9_.-]{0,39})")
BROWSER = re.compile(r"browser:([a-z0-9][a-z0-9_.-]{0,39})")
# what a one-time code is for: signing a browser into the page (``browser`` is kept for a
# paired client)
CODE_PURPOSES = ("ui", "browser")
DIR_ENV = "REPO_FACTORY_TOKENS_DIR"


class TokenError(ValueError):
    """A token that cannot be added, rotated or revoked; the message says why."""


def tokens_dir(env: dict | None = None, home: Path | None = None) -> Path:
    """``$REPO_FACTORY_TOKENS_DIR``, else ``<home>/tokens`` (``home``: the repo-factory home,
    default ``paths.home()``)."""
    from . import paths

    env = os.environ if env is None else env
    if env.get(DIR_ENV):
        return Path(env[DIR_ENV]).expanduser()
    return paths.Layout(paths.home(home)).tokens


def cookie_name(port: int | None = None) -> str:
    """The session cookie's name. Browsers send a cookie to every port of a host, so each
    port gets its own (a second instance on another port never replaces the first one's)."""
    return f"{COOKIE}_{int(port)}" if port else COOKIE


def valid_name(name: str) -> bool:
    """A door (``ui``, ``cli``), ``agent:<name>`` (letters, digits,
    ``_.-``) or ``browser:<name>`` (the same, lowercase)."""
    return (
        name in DOORS
        or bool(AGENT.fullmatch(str(name or "")))
        or bool(BROWSER.fullmatch(str(name or "")))
    )


def file_name(name: str) -> str:
    return name.replace(":", "-") + ".token"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _write(path: Path, text: str) -> None:
    """Write ``text`` at mode 600, atomically."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class Store:
    """The token directory. Every method re-reads the index when it changed on disk, so a
    token added or revoked from the command line counts in the running server at once."""

    def __init__(self, root: str | Path | None = None, home: str | Path | None = None):
        self.root = Path(root) if root is not None else tokens_dir(home=home)
        self._cache: tuple[int, dict] | None = None

    # -- the files -------------------------------------------------------------------------
    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    def path(self, name: str) -> Path:
        """Where ``name``'s value is kept (clients read it from there)."""
        return self.root / file_name(name)

    def _mkdir(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    @contextlib.contextmanager
    def _locked(self):
        self._mkdir()
        fd = os.open(self.root / ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _index(self) -> dict:
        try:
            st = self.index_path.stat()
        except OSError:
            return {}
        key = st.st_mtime_ns ^ st.st_size
        if self._cache and self._cache[0] == key:
            return self._cache[1]
        try:
            raw = json.loads(self.index_path.read_text())
            toks = raw.get("tokens") if isinstance(raw, dict) else None
            toks = {
                k: v
                for k, v in (toks or {}).items()
                if valid_name(k) and isinstance(v, dict) and v.get("sha256")
            }
        except (OSError, ValueError):
            toks = {}
        self._cache = (key, toks)
        return toks

    def _save(self, toks: dict) -> None:
        _write(self.index_path, json.dumps({"version": 1, "tokens": toks}, indent=1) + "\n")
        self._cache = None

    # -- managing --------------------------------------------------------------------------
    def _set(self, toks: dict, name: str, actions=None, note: str = "") -> str:
        value = secrets.token_urlsafe(32)
        if not BROWSER.fullmatch(name):  # a browser's value lives in its extension only
            _write(self.path(name), value + "\n")
        toks[name] = {
            "sha256": digest(value),
            "created": time.time(),
            "actions": list(actions) if actions is not None else None,
            "note": " ".join(str(note or "").split())[:200],
        }
        return value

    def ensure_doors(self) -> list[str]:
        """Make the doors' tokens that are missing (or whose file is gone); returns their
        names. Never changes one that exists."""
        made = []
        with self._locked():
            toks = dict(self._index())
            for name in DOORS:
                if name not in toks or not self.path(name).is_file():
                    self._set(toks, name)
                    made.append(name)
            if made:
                self._save(toks)
        return made

    def add(self, name: str, actions: list[str] | None = None, note: str = "") -> Path:
        """A new agent token; returns its file (the value is never printed)."""
        if not AGENT.fullmatch(str(name or "")):
            raise TokenError(
                "an agent token is named agent:<name> (letters, digits, _ . -; up to 40)"
            )
        with self._locked():
            toks = dict(self._index())
            if name in toks:
                raise TokenError(f"{name} exists: rotate it, or revoke it first")
            self._set(toks, name, actions, note)
            self._save(toks)
        return self.path(name)

    def add_browser(self, stem: str, note: str = "") -> tuple[str, str]:
        """A new ``browser:<stem>`` token (``-2``, ``-3`` … when the name is taken); returns
        ``(name, value)``. The value is not kept here: the caller hands it to the extension
        once."""
        base = re.sub(r"[^a-z0-9_.-]+", "-", str(stem or "").lower()).strip("-._")[:34] or "browser"
        with self._locked():
            toks = dict(self._index())
            name, n = f"browser:{base}", 1
            while name in toks:
                n += 1
                name = f"browser:{base}-{n}"
            value = self._set(toks, name, None, note)
            self._save(toks)
        return name, value

    def rotate(self, name: str) -> Path:
        """A new value for ``name`` (the old one stops working at once). Rotating ``ui``
        signs every browser out."""
        with self._locked():
            toks = dict(self._index())
            if name not in toks:
                raise TokenError(f"no token {name}")
            if BROWSER.fullmatch(name):
                raise TokenError(f"{name} is a browser's: forget it and pair the browser again")
            old = toks[name]
            self._set(toks, name, old.get("actions"), old.get("note") or "")
            self._save(toks)
        return self.path(name)

    def set_actions(self, name: str, actions: list[str] | None) -> None:
        with self._locked():
            toks = dict(self._index())
            if not AGENT.fullmatch(name) or name not in toks:
                raise TokenError(f"no agent token {name}")
            toks[name] = {**toks[name], "actions": list(actions) if actions is not None else None}
            self._save(toks)

    def revoke(self, name: str) -> bool:
        """Remove an agent's token (its file too). A door's token is rotated, not revoked:
        repo-factory would make it again on its next start."""
        if name in DOORS:
            raise TokenError(f"{name} is a door: rotate it instead")
        with self._locked():
            toks = dict(self._index())
            had = toks.pop(name, None) is not None
            self.path(name).unlink(missing_ok=True)
            if had:
                self._save(toks)
        return had

    def list(self) -> list[dict]:
        """Every token, without its value: ``[{name, origin, created, actions, note, file}]``."""
        return [
            {
                "name": k,
                "origin": k,
                "created": v.get("created"),
                "actions": v.get("actions"),
                "note": v.get("note") or "",
                "file": None if BROWSER.fullmatch(k) else str(self.path(k)),
            }
            for k, v in sorted(self._index().items(), key=lambda kv: (kv[0] not in DOORS, kv[0]))
        ]

    def value(self, name: str) -> str | None:
        """A client's own token, read from its file; None when there is none."""
        try:
            v = self.path(name).read_text().strip()
        except OSError:
            return None
        return v or None

    # -- checking --------------------------------------------------------------------------
    def identify(self, value: str | None) -> dict | None:
        """The identity a presented token stands for: ``{"origin", "name", "actions"}``;
        None when it is not a current token."""
        if not value or len(value) > 200:
            return None
        d = digest(value.strip())
        for name, rec in self._index().items():
            if hmac.compare_digest(d, str(rec.get("sha256"))):
                m = AGENT.fullmatch(name) or BROWSER.fullmatch(name)
                return {
                    "origin": name,
                    "name": m.group(1) if m else name,
                    "actions": rec.get("actions"),
                }
        return None

    # -- signing a browser in --------------------------------------------------------------
    def mint_code(self, ttl_s: float = CODE_TTL_S, purpose: str = "ui") -> str:
        """A one-time code that signs one browser in within ``ttl_s`` (``repo-factory ui``
        prints one). A code is good for its purpose only."""
        if purpose not in CODE_PURPOSES:
            raise TokenError(f"a code is for one of {', '.join(CODE_PURPOSES)}")
        self.ensure_doors()
        d = self.root / "launch"
        d.mkdir(mode=0o700, exist_ok=True)
        now = time.time()
        for old in d.iterdir():  # expired codes go
            with contextlib.suppress(OSError, ValueError, IndexError):
                if float((old.read_text().split() or ["0"])[0]) < now:
                    old.unlink()
        code = secrets.token_urlsafe(18)
        # the purpose after the expiry: an older release reads the whole line as a number,
        # fails, and so never signs a page in with a pairing code
        _write(
            d / digest(code),
            f"{now + ttl_s}\n" if purpose == "ui" else f"{now + ttl_s} {purpose}\n",
        )
        return code

    def redeem_code(self, code: str | None, purpose: str = "ui") -> bool:
        """Whether ``code`` is a valid, unexpired, unused code for ``purpose`` (used up here).
        A code for another purpose is left as it was."""
        if not code or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", str(code)):
            return False
        f = self.root / "launch" / digest(code)
        try:
            words = f.read_text().split()
            until = float(words[0])
        except (OSError, ValueError, IndexError):
            return False
        if (words[1] if len(words) > 1 else "ui") != purpose:
            return False
        try:
            f.unlink()  # one time: a second press finds nothing
        except OSError:
            return False  # another request used it first
        return until >= time.time()

    def redeem(self, code: str | None) -> str | None:
        """The UI token for a valid, unexpired, unused sign-in code (used up here); else
        None."""
        return self.value("ui") if self.redeem_code(code, "ui") else None

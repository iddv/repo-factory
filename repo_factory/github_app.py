"""repo-factory's own identity on GitHub: a GitHub App made by this install (GA-PLAN.md, MAINTAIN D1).

Settings › Publishing › GitHub creates an app named ``repo-factory-<login>`` through GitHub's manifest flow:
the page posts ``manifest()`` to github.com, GitHub sends the browser back to repo-factory on
127.0.0.1 with a one-hour code, and ``exchange`` turns the code into the app's id and private
key. The owner then installs the app on the repositories repo-factory maintains; ``refresh`` reads
the installation back. From then on ``GitHubApp.token`` mints one-hour installation tokens
from a signed JWT, each narrowed to the fewest permissions and the one repository the call
acts on (``SCOPES``), kept in memory only. Without an app nothing here is used: maintenance
acts as the ``gh`` login, as before.

**The store**, ``~/.config/repo-factory/github/`` (0700; ``github.<unit>`` under ``$REPO_FACTORY_UNIT``,
``$REPO_FACTORY_GITHUB_DIR`` for tests; ``store_dir`` is the one place that resolves it):

    app.json       the app's facts (ids, slug, permissions, the installation, its repos);
                   no secret, ever
    app.pem        the private key, 0600, read by ``_sign`` and by nothing else
    pending.json   one creation in flight: the state the callback must match (10 minutes);
                   the page waits five of them, or until Cancel, then says what happened
    job.json       the background job's state (the exchange, a refresh, a link, a removal)

An app made on GitHub without repo-factory hearing back (a closed tab, a callback that never came)
is linked, never made twice: ``link`` takes its id and a key generated on GitHub, checks them
with ``GET /app`` and reads the installation back.

The key never reaches an environment, a command line, a page or a record: ``Gh`` asks the
app for a token for each subprocess, and ``clean`` removes anything token-shaped from an
error line. ``cryptography`` (the ``github`` extra; the ``repo-factory`` package carries it) signs
the JWT; without it ``available()`` says so and nothing else changes.
"""

from __future__ import annotations

import base64
import contextlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .gh import TokenError

API = "https://api.github.com"
WEB = "https://github.com"
API_ENV = "REPO_FACTORY_GITHUB_API"  # tests: the fake GitHub's base URL (loopback)
DIR_ENV = "REPO_FACTORY_GITHUB_DIR"
DEFAULT_UNIT = "repo-factory.service"
UPSTREAM_URL = "https://github.com/iddv/repo-factory"
DESCRIPTION = (
    "repo-factory's own identity on this account: maintenance, project issues, its own bug reports."
)
# The manifest's permissions (decisions D2 and D3): fixed here, pinned by a test. Never
# administration, workflows, secrets, actions write, nor any organisation permission.
PERMISSIONS = {
    "metadata": "read",
    "issues": "write",
    "pull_requests": "write",
    "contents": "write",
    "checks": "read",
    "actions": "read",
}
NEVER = (
    "administration",
    "workflows",
    "secrets",
    "members",
    "organization_administration",
    "organization_secrets",
    "organization_hooks",
    "organization_projects",
    "organization_self_hosted_runners",
    "repository_hooks",
    "repository_projects",
    "environments",
    "deployments",
    "packages",
    "pages",
)
# What each call asks for (the review's rule: the fewest permissions, one repository):
#   maintain: the poll on one repo (labels, comments, pull requests, releases, CI reads);
#   issues: filing and reading issues (factory issues, Projects); push: a fix's git push;
#   fix: the fix process's API calls (the issue, the pull request, its CI); read: reads.
SCOPES = {
    "maintain": dict(PERMISSIONS),
    "issues": {"issues": "write", "metadata": "read"},
    "push": {"contents": "write", "metadata": "read"},
    "fix": {
        "contents": "write",
        "pull_requests": "write",
        "issues": "write",
        "metadata": "read",
        "checks": "read",
        "actions": "read",
    },
    "read": {
        "metadata": "read",
        "contents": "read",
        "issues": "read",
        "pull_requests": "read",
        "checks": "read",
        "actions": "read",
    },
}
JWT_BACK_S = 60  # iat: a minute back, for clock drift (GitHub's advice)
JWT_LIFE_S = 9 * 60  # exp: under GitHub's ten-minute maximum
TOKEN_LIFE_S = 3600  # an installation token lives an hour
REFRESH_WITHIN_S = 300  # minted again when under five minutes remain
PENDING_TTL_S = 600  # a creation in flight expires after ten minutes
WAIT_S = 300  # the page waits five of them for GitHub, then puts Create back
ATTEMPT_SHOWN_S = 3600  # a try that never came back is mentioned for an hour (a code's life)
REDIRECT_HOSTS = ("127.0.0.1", "localhost")
TIMEOUT_S = 20
# GitHub's token shapes, the new stateless ``ghs_<app>_<jwt>`` form included (identity.clean
# and the secrets scan use the same)
TOKEN_SHAPES = re.compile(r"\b(gh[pousr]_[A-Za-z0-9_.-]{16,}|github_pat_[A-Za-z0-9_]{20,})")
PEM_HEAD = re.compile(r"-----BEGIN (RSA |EC |)PRIVATE KEY-----")
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,99}")
STATES = ("none", "creating", "created", "installed", "failed")


class AppError(TokenError):
    """The app cannot act; the message is safe to show (no token, no key)."""

    def __init__(self, msg: str, *, status: int | None = None, kind: str = "error"):
        super().__init__(clean(msg))
        self.status, self.kind = status, kind


def clean(text: str, limit: int = 300) -> str:
    """One line safe to show: anything token-shaped or key-shaped removed."""
    s = " ".join(str(text or "").split())
    s = re.sub(r"-----BEGIN[^-]*-----.*?-----END[^-]*-----", "[key]", s)
    s = re.sub(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", "[jwt]", s)
    return TOKEN_SHAPES.sub("[token]", s)[:limit]


def available() -> str | None:
    """Why the app cannot be used at all, else None: the ``github`` extra is missing."""
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return (
            "repo-factory's GitHub App needs the cryptography package: install repo-factory[github] "
            "(the repo-factory package includes it)"
        )
    return None


# -- the store ---------------------------------------------------------------------------------
def store_dir(env: dict | None = None, home: Path | None = None) -> Path:
    """``$REPO_FACTORY_GITHUB_DIR``, else ``<home>/github`` (``home``: the repo-factory home,
    default ``$REPO_FACTORY_HOME`` or ``~/.repo-factory``)."""
    from . import paths

    env = os.environ if env is None else env
    if env.get(DIR_ENV):
        return Path(env[DIR_ENV]).expanduser()
    return paths.Layout(paths.home(home or env.get(paths.HOME_ENV))).github


def _write(path: Path, text: str) -> None:
    """Write ``text`` at mode 600, atomically (as tokens.py does)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


# the facts app.json keeps (an allowlist: the page serialises these and nothing else)
APP_FIELDS = (
    "app_id",
    "client_id",
    "slug",
    "name",
    "html_url",
    "owner_login",
    "owner_type",
    "bot_login",
    "bot_user_id",
    "permissions",
    "events",
    "installation_id",
    "repository_selection",
    "repos",
    "repos_read_at",
    "created_at",
    "key_fingerprint",
    "installed_at",
)
SECRET_FIELDS = ("pem", "client_secret", "webhook_secret", "token", "key")


class Store:
    """The directory. Every read re-reads the files (a change from the command line counts in
    the running server at once; they are tiny)."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else store_dir()

    @property
    def app_path(self) -> Path:
        return self.root / "app.json"

    @property
    def key_path(self) -> Path:
        return self.root / "app.pem"

    @property
    def pending_path(self) -> Path:
        return self.root / "pending.json"

    @property
    def job_path(self) -> Path:
        return self.root / "job.json"

    def _mkdir(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    # -- the app's facts ---------------------------------------------------------------------
    def read(self) -> dict | None:
        """``app.json``, or None when there is no app."""
        d = _read_json(self.app_path)
        if not d or not d.get("app_id"):
            return None
        return {k: d.get(k) for k in APP_FIELDS}

    def write_app(self, rec: dict) -> dict:
        """Keep the app's facts; a secret-named field is refused, never written."""
        bad = [k for k in SECRET_FIELDS if k in rec]
        if bad:
            raise AppError(f"app.json never holds {', '.join(bad)}")
        out = {k: rec.get(k) for k in APP_FIELDS}
        self._mkdir()
        _write(self.app_path, json.dumps(out, indent=1, sort_keys=True) + "\n")
        return out

    def update_app(self, **fields) -> dict:
        cur = self.read() or {}
        return self.write_app({**cur, **fields})

    # -- the key -----------------------------------------------------------------------------
    def has_key(self) -> bool:
        return self.key_path.is_file()

    def write_key(self, pem: str) -> str:
        """Keep a private key (PEM) at 0600; returns its fingerprint (SHA-256 of the DER
        public key, the shape GitHub shows)."""
        pem = str(pem or "").strip() + "\n"
        if not PEM_HEAD.search(pem) or "PRIVATE KEY-----" not in pem.split("\n")[-2]:
            raise AppError(
                "that is not a private key in PEM form (-----BEGIN RSA PRIVATE "
                "KEY----- … -----END RSA PRIVATE KEY-----)"
            )
        fp = fingerprint(pem)
        self._mkdir()
        _write(self.key_path, pem)
        return fp

    def _key_bytes(self) -> bytes:
        """The key's bytes, for ``_sign`` only. A key other users can read is refused."""
        try:
            st = self.key_path.stat()
        except OSError:
            raise AppError(
                "repo-factory's GitHub App has no private key here: create the app again "
                "under Settings › Publishing › GitHub",
                kind="no_key",
            ) from None
        if st.st_mode & 0o077:
            raise AppError(
                f"the key file {self.key_path} is readable by other users: chmod 600 it",
                kind="no_key",
            )
        return self.key_path.read_bytes()

    # -- the creation in flight ----------------------------------------------------------------
    def pending_write(self, host: str, port: int | None, now: float | None = None) -> str:
        """Start one creation: a fresh state, kept with where the browser must come back."""
        state = secrets.token_urlsafe(32)
        self._mkdir()
        _write(
            self.pending_path,
            json.dumps({"state": state, "host": host, "port": port, "created": now or time.time()})
            + "\n",
        )
        return state

    def pending(self, now: float | None = None) -> dict | None:
        d = _read_json(self.pending_path)
        if not d or not d.get("state"):
            return None
        if (now or time.time()) - float(d.get("created") or 0) > PENDING_TTL_S:
            return None
        return d

    def waiting(self, now: float | None = None) -> dict | None:
        """The creation the page still waits for: in flight, not cancelled, under five minutes
        old. Past that the page puts Create back; the callback is still taken until the
        pending record's ten minutes are up, so an app GitHub made late is kept, not lost."""
        d = self.pending(now)
        if not d or d.get("cancelled"):
            return None
        if (now or time.time()) - float(d.get("created") or 0) > WAIT_S:
            return None
        return d

    def cancel(self, now: float | None = None) -> bool:
        """Stop waiting for GitHub (the tab was closed, or the person changed their mind).
        The record stays, marked, so a callback GitHub still sends is taken rather than
        leaving an app on GitHub that repo-factory never heard of."""
        d = _read_json(self.pending_path)
        if not d or not d.get("state") or d.get("cancelled"):
            return False
        _write(self.pending_path, json.dumps({**d, "cancelled": now or time.time()}) + "\n")
        return True

    def attempt(self, now: float | None = None) -> dict | None:
        """The last creation that never came back, for an hour (``ATTEMPT_SHOWN_S``):
        ``{"at", "how"}`` with how ``cancelled`` or ``no_answer``; None while the page still
        waits, after a callback (it deletes the record) or when there is an app."""
        d = _read_json(self.pending_path)
        if not d or not d.get("state") or self.waiting(now) or self.read() is not None:
            return None
        at = float(d.get("created") or 0)
        if (now or time.time()) - at > ATTEMPT_SHOWN_S:
            return None
        return {"at": at, "how": "cancelled" if d.get("cancelled") else "no_answer"}

    def pending_take(self, state: str | None, now: float | None = None) -> dict | None:
        """The pending record when ``state`` matches it (constant time), used up: the file is
        deleted first, so a second attempt with the same state finds nothing. None when there
        is no creation in flight, it is stale, or the state is wrong."""
        d = _read_json(self.pending_path)
        with contextlib.suppress(OSError):
            self.pending_path.unlink()
        if not d or not d.get("state") or not state:
            return None
        if (now or time.time()) - float(d.get("created") or 0) > PENDING_TTL_S:
            return None
        if not hmac.compare_digest(str(d["state"]), str(state)):
            return None
        return d

    # -- the background job --------------------------------------------------------------------
    def job(self) -> dict:
        """``job.json``; a ``running`` job whose server is gone (a restart cut it off: its
        record's ``pid`` and ``start`` no longer run) reads as ``failed``, saying so."""
        from . import proc

        cur = _read_json(self.job_path) or {"state": "none"}
        if (
            cur.get("state") == "running"
            and cur.get("start") is not None
            and not proc.record_alive(cur)
        ):
            cur = {
                **cur,
                "state": "failed",
                "kind": "restart",
                "error": "repo-factory restarted before this step finished: try it again",
            }
        return cur

    def set_job(self, what: str, state: str, **rec) -> dict:
        cur = self.job()
        if cur.get("what") != what:
            cur = {}
        cur.update(rec, what=what, state=state, at=time.time())
        if "error" in cur and cur["error"]:
            cur["error"] = clean(cur["error"])
        self._mkdir()
        _write(self.job_path, json.dumps(cur) + "\n")
        return cur

    # -- removal -------------------------------------------------------------------------------
    def clear(self) -> None:
        """Forget the app: every file goes (the registration on GitHub stays; no API deletes
        it)."""
        for p in (self.app_path, self.key_path, self.pending_path, self.job_path):
            with contextlib.suppress(OSError):
                p.unlink()

    def state(self) -> str:
        """``none`` (no app), ``creating`` (a creation in flight or the exchange running),
        ``created`` (an app, not installed yet), ``installed``, ``failed`` (the last job)."""
        job = self.job()
        rec = self.read()
        if (
            job.get("state") == "running"
            and job.get("what") == "exchange"
            and time.time() - float(job.get("at") or 0) < 120
        ):
            return "creating"
        if rec is None:
            if job.get("state") == "failed" and job.get("what") in ("exchange", "link"):
                return "failed"
            return "creating" if self.waiting() else "none"
        if rec.get("installation_id"):
            return "installed"
        return "created"


# -- the manifest -------------------------------------------------------------------------------
def check_host(host: str | None) -> str:
    """The request's ``Host`` without its port, accepted only on loopback by name (the page
    must be open on 127.0.0.1 or localhost: GitHub sends the browser back there)."""
    h = str(host or "").strip().lower()
    if h.startswith("["):
        h = h.split("]")[0].lstrip("[")
    else:
        h = h.split(":")[0]
    if h not in REDIRECT_HOSTS:
        raise AppError(
            "open repo-factory on http://127.0.0.1:<port>/ (or localhost) to create the "
            f"app: GitHub sends the browser back to that address, and {h or 'this'} "
            "is not one it can reach",
            kind="host",
        )
    return h


def app_name(login: str | None) -> str:
    """``repo-factory-<login>`` (decision D1), a valid app name: GitHub allows 34 characters."""
    s = re.sub(r"[^A-Za-z0-9-]+", "-", str(login or "").strip()).strip("-")
    return ("repo-factory-" + s)[:34].rstrip("-") if s else "repo-factory"


def manifest(host: str, port: int | None, login: str | None, mount: str = "") -> dict:
    """What the page posts to ``https://github.com/settings/apps/new?state=…`` as the
    ``manifest`` form field. No webhook, no events, private to the account."""
    h = check_host(host)
    base = f"http://{h}:{int(port)}" if port else f"http://{h}"
    m = mount.rstrip("/")
    return {
        "name": app_name(login),
        "url": UPSTREAM_URL,
        "description": DESCRIPTION,
        "redirect_url": f"{base}{m}/api/github/app/callback",
        "setup_url": f"{base}{m}/api/github/app/setup",
        "setup_on_update": True,
        "public": False,
        "default_events": [],
        "default_permissions": dict(PERMISSIONS),
    }


def post_url(state: str, org: str | None = None) -> str:
    """Where the form goes: the account's app page, or an organisation's (decision D8)."""
    where = f"/organizations/{org}/settings/apps/new" if org else "/settings/apps/new"
    return f"{WEB}{where}?state={state}"


# -- the JWT and the transport ------------------------------------------------------------------
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def fingerprint(pem: str | bytes) -> str:
    """``SHA256:<base64>`` of the DER public key, as GitHub lists a key."""
    import hashlib

    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(
        pem if isinstance(pem, bytes) else pem.encode(), password=None
    )
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return "SHA256:" + base64.b64encode(hashlib.sha256(der).digest()).decode().rstrip("=")


def _sign(data: bytes, pem: bytes) -> bytes:
    """RS256 over ``data`` with the private key; the only function that touches its bytes."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(pem, password=None)
    return key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def make_jwt(issuer: str, pem: bytes, now: float | None = None) -> str:
    """The app's JWT: ``iat`` a minute back, ``exp`` nine minutes on, ``iss`` the app's
    client id, RS256."""
    now = int(now or time.time())
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(
        json.dumps(
            {"iat": now - JWT_BACK_S, "exp": now + JWT_LIFE_S, "iss": issuer}, separators=(",", ":")
        ).encode()
    )
    signing = f"{head}.{body}".encode()
    return f"{head}.{body}.{_b64(_sign(signing, pem))}"


def api_base() -> str:
    return (os.environ.get(API_ENV) or API).rstrip("/")


def api(
    method: str,
    path: str,
    *,
    auth: str | None = None,
    payload: dict | None = None,
    timeout: float = TIMEOUT_S,
) -> tuple[int, dict, object]:
    """One call to GitHub's REST API: ``(status, headers, json or None)``. The auth value
    (a JWT or a token) goes in the header and nowhere else; an error names the status and
    the path, never the body."""
    url = api_base() + path
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "repo-factory",
    }
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, hdrs, raw = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        status, hdrs = e.code, dict(e.headers)
        raw = e.read() if e.fp else b""
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise AppError(
            f"GitHub could not be reached ({method} {path.split('?')[0]}: "
            f"{clean(getattr(e, 'reason', e))})",
            kind="network",
        ) from None
    body = None
    if raw:
        with contextlib.suppress(ValueError, UnicodeDecodeError):
            body = json.loads(raw.decode())
    return status, {k.lower(): v for k, v in hdrs.items()}, body


def _refused(status: int, path: str, body) -> AppError:
    msg = (body or {}).get("message") if isinstance(body, dict) else None
    where = path.split("?")[0]
    if status in (401, 403):
        return AppError(
            f"GitHub refused repo-factory's app ({status} on {where}): it was uninstalled, "
            "suspended, or its key was replaced. Settings › Publishing › GitHub says more.",
            status=status,
            kind="refused",
        )
    if status == 404:
        return AppError(
            f"GitHub refused repo-factory's app (404 on {where}): it was uninstalled, "
            "suspended, or its key was replaced, or the app itself is gone. "
            "Settings › Publishing › GitHub says more.",
            status=status,
            kind="gone",
        )
    return AppError(
        f"GitHub answered {status} on {where}" + (f": {clean(msg, 120)}" if msg else ""),
        status=status,
    )


def _epoch(s: str | None) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return time.time() + TOKEN_LIFE_S


# -- the app ------------------------------------------------------------------------------------
_APPS: dict[Path, GitHubApp] = {}
_APPS_LOCK = threading.Lock()


def app_for(root: str | Path | None = None) -> GitHubApp:
    """The one ``GitHubApp`` of a store in this process, so every caller shares its token
    cache (the UI and the loop lane are one process)."""
    store = Store(root)
    with _APPS_LOCK:
        a = _APPS.get(store.root)
        if a is None:
            a = _APPS[store.root] = GitHubApp(store)
        return a


class GitHubApp:
    """The app of this install: its facts from the store, tokens minted and kept in memory."""

    def __init__(self, store: Store | None = None, now=None):
        self.store = store or Store()
        self._now = now or time.time
        self._tokens: dict[tuple, dict] = {}  # (scope, repos) -> {token, expires_at}
        self._lock = threading.Lock()

    # -- facts (no network) --------------------------------------------------------------------
    @property
    def rec(self) -> dict | None:
        return self.store.read()

    def exists(self) -> bool:
        return self.rec is not None

    def installed(self) -> bool:
        r = self.rec
        return bool(r and r.get("installation_id") and self.store.has_key())

    def state(self) -> str:
        return self.store.state()

    @property
    def bot_login(self) -> str | None:
        r = self.rec
        return f"{r['slug']}[bot]" if r and r.get("slug") else None

    @property
    def bot_email(self) -> str | None:
        """The bot's noreply address: commits with it get the bot's badge on GitHub."""
        r = self.rec
        if not r or not r.get("slug") or not r.get("bot_user_id"):
            return None
        return f"{r['bot_user_id']}+{r['slug']}[bot]@users.noreply.github.com"

    def reaches(self, full: str) -> bool | None:
        """Whether the installation covers ``owner/name``: True, False, or None when the
        app is not installed (nothing to say)."""
        r = self.rec
        if not r or not r.get("installation_id"):
            return None
        owner = str(full).split("/")[0].lower()
        if r.get("repository_selection") == "all":
            return owner == str(r.get("owner_login") or "").lower()
        return str(full).lower() in {str(x).lower() for x in r.get("repos") or []}

    def why_not(self, full: str | None = None) -> str | None:
        """Why the app cannot act (on ``full``), in words; None when it can."""
        missing = available()
        if missing:
            return missing
        r = self.rec
        if r is None:
            return "repo-factory has no GitHub App yet: create one under Settings › Publishing › GitHub"
        if not self.store.has_key():
            return f"repo-factory's app {r.get('name')} has no private key here: Settings › Publishing › GitHub"
        if not r.get("installation_id"):
            return (
                f"repo-factory's app {r.get('name')} is not installed on any repository yet: "
                "choose its repositories under Settings › Publishing › GitHub"
            )
        if full and self.reaches(full) is False:
            return (
                f"repo-factory's app {r.get('name')} is not installed on {full}: add it under "
                "Settings › Publishing › GitHub › Add repositories"
            )
        return None

    # -- the JWT and the tokens ------------------------------------------------------------------
    def jwt(self) -> str:
        r = self.rec
        if r is None:
            raise AppError(
                "repo-factory has no GitHub App yet: Settings › Publishing › GitHub", kind="none"
            )
        issuer = str(r.get("client_id") or r.get("app_id"))
        return make_jwt(issuer, self.store._key_bytes(), self._now())

    def token(self, scope: str = "maintain", repos: list[str] | tuple | None = None) -> str:
        """An installation token for ``scope`` (``SCOPES``) on ``repos`` (``owner/name``
        names; None: every repository of the installation), minted and kept in memory with
        its expiry, minted again when under five minutes remain."""
        if scope not in SCOPES:
            raise AppError(f"no such token scope {scope!r}: one of {', '.join(SCOPES)}")
        why = self.why_not()
        if why:
            raise AppError(why, kind="none")
        names = tuple(sorted({str(x) for x in (repos or ())}))
        for full in names:
            if self.reaches(full) is False:
                raise AppError(self.why_not(full) or f"{full} is out of reach", kind="reach")
        key = (scope, names)
        with self._lock:
            hit = self._tokens.get(key)
            if hit and hit["expires_at"] - self._now() > REFRESH_WITHIN_S:
                return hit["token"]
            rec = self._mint(scope, names)
            self._tokens[key] = rec
            return rec["token"]

    def _mint(self, scope: str, names: tuple) -> dict:
        r = self.rec
        payload: dict = {"permissions": dict(SCOPES[scope])}
        if names:
            payload["repositories"] = [n.split("/", 1)[1] for n in names]
        status, _, body = api(
            "POST",
            f"/app/installations/{r['installation_id']}/access_tokens",
            auth=self.jwt(),
            payload=payload,
        )
        if status != 201 or not isinstance(body, dict) or not body.get("token"):
            if status == 422:
                raise AppError(
                    f"GitHub refused the token for {', '.join(names) or 'the app'}: "
                    "the installation does not reach it, or a permission is not "
                    "granted. Settings › Publishing › GitHub › Refresh.",
                    status=422,
                    kind="reach",
                )
            raise _refused(status, "/app/installations/…/access_tokens", body)
        return {
            "token": str(body["token"]),
            "expires_at": _epoch(body.get("expires_at")),
            "minted": self._now(),
        }

    def forget(self, scope: str | None = None, repos=None) -> None:
        """Drop cached tokens (every one, or one scope's): a 401 on use mints again once."""
        with self._lock:
            if scope is None:
                self._tokens.clear()
            else:
                names = tuple(sorted({str(x) for x in (repos or ())}))
                self._tokens.pop((scope, names), None)

    def gh(self, scope: str = "maintain", repos=None, cmd: list[str] | None = None):
        """A ``publish.Gh`` whose subprocesses get a token for ``scope`` on ``repos``; its
        login is the bot's, without a call."""
        from .gh import Gh

        names = tuple(sorted({str(x) for x in (repos or ())}))
        g = Gh(cmd=cmd, token=lambda: self.token(scope, names), login=self.bot_login)
        g.app, g.scope, g.repos = self, scope, names
        return g

    # -- the flows (each a network call: run them in the background) ---------------------------
    def check(self) -> dict:
        """``GET /app`` with a JWT: the key and the id agree. Returns the app as GitHub has it."""
        status, _, body = api("GET", "/app", auth=self.jwt())
        if status != 200 or not isinstance(body, dict):
            raise _refused(status, "/app", body)
        return body

    def refresh(self) -> dict:
        """Read the installation back: the one on the owner's account, a token for it, its
        repositories, the bot's user id. Writes app.json; returns it."""
        r = self.rec
        if r is None:
            raise AppError(
                "repo-factory has no GitHub App yet: Settings › Publishing › GitHub", kind="none"
            )
        status, _, body = api("GET", "/app/installations?per_page=100", auth=self.jwt())
        if status != 200 or not isinstance(body, list):
            raise _refused(status, "/app/installations", body)
        mine = [
            x
            for x in body
            if isinstance(x, dict)
            and str((x.get("account") or {}).get("login") or "").lower()
            == str(r.get("owner_login") or "").lower()
        ]
        inst = (mine or [x for x in body if isinstance(x, dict)] or [None])[0]
        if inst is None:
            self.forget()
            self.store.update_app(
                installation_id=None,
                repository_selection=None,
                repos=[],
                repos_read_at=self._now(),
                installed_at=None,
            )
            raise AppError(
                f"repo-factory's app {r.get('name')} is not installed on any account: "
                "choose its repositories on GitHub",
                kind="not_installed",
            )
        rec = self.store.update_app(
            installation_id=inst.get("id"),
            repository_selection=inst.get("repository_selection"),
            installed_at=r.get("installed_at") or self._now(),
            owner_login=(inst.get("account") or {}).get("login") or r.get("owner_login"),
            owner_type=(inst.get("account") or {}).get("type") or r.get("owner_type"),
            permissions=inst.get("permissions") or r.get("permissions"),
        )
        self.forget()
        token = self.token("read")
        repos: list[str] = []
        page = 1
        while page < 20:
            status, _, body = api(
                "GET", f"/installation/repositories?per_page=100&page={page}", auth=token
            )
            if status != 200 or not isinstance(body, dict):
                raise _refused(status, "/installation/repositories", body)
            got = [
                x.get("full_name")
                for x in body.get("repositories") or []
                if isinstance(x, dict) and x.get("full_name")
            ]
            repos += got
            if len(got) < 100:
                break
            page += 1
        fields = {"repos": sorted(repos), "repos_read_at": self._now()}
        if not rec.get("bot_user_id") and rec.get("slug"):
            status, _, body = api("GET", f"/users/{rec['slug']}%5Bbot%5D", auth=token)
            if status == 200 and isinstance(body, dict) and body.get("id"):
                fields["bot_user_id"] = body["id"]
                fields["bot_login"] = body.get("login") or f"{rec['slug']}[bot]"
        return self.store.update_app(**fields)

    def replace_key(self, pem: str) -> dict:
        """A new private key, checked against GitHub (``GET /app``) before it replaces the
        old one. Returns the app's facts with the new fingerprint."""
        r = self.rec
        if r is None:
            raise AppError(
                "repo-factory has no GitHub App yet: Settings › Publishing › GitHub", kind="none"
            )
        pem = str(pem or "").strip() + "\n"
        if not PEM_HEAD.search(pem):
            raise AppError("that is not a private key in PEM form")
        issuer = str(r.get("client_id") or r.get("app_id"))
        status, _, body = api("GET", "/app", auth=make_jwt(issuer, pem.encode(), self._now()))
        if status != 200 or not isinstance(body, dict) or str(body.get("id")) != str(r["app_id"]):
            raise AppError(
                "GitHub did not accept that key for repo-factory's app: generate one under "
                f"the app's settings ({r.get('html_url')}) and paste the whole file",
                status=status,
                kind="key",
            )
        fp = self.store.write_key(pem)
        self.forget()
        return self.store.update_app(key_fingerprint=fp)

    def remove(self) -> dict:
        """Uninstall on GitHub (best effort) and forget the app here. Returns what happened
        and where the owner deletes the registration (no API does)."""
        r = self.rec
        out = {"uninstalled": False, "delete_url": None}
        if r is None:
            self.store.clear()
            return out
        out["delete_url"] = f"{r.get('html_url') or WEB + '/settings/apps/' + str(r.get('slug'))}"
        if r.get("installation_id") and self.store.has_key():
            with contextlib.suppress(AppError):
                status, _, _ = api(
                    "DELETE", f"/app/installations/{r['installation_id']}", auth=self.jwt()
                )
                out["uninstalled"] = status in (204, 404)
        self.forget()
        self.store.clear()
        return out


def exchange(code: str, store: Store | None = None, now=None) -> dict:
    """The manifest's code (one hour, one use) for the app: ``POST
    /app-manifests/<code>/conversions``. The key goes to ``app.pem`` first, then the facts to
    ``app.json``; the client secret and webhook secret in the answer are dropped unread."""
    store = store or Store()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", str(code or "")):
        raise AppError("the code GitHub sent back does not look right: start again", kind="code")
    status, _, body = api("POST", f"/app-manifests/{code}/conversions")
    if status != 201 or not isinstance(body, dict) or not body.get("id") or not body.get("pem"):
        if status == 404:
            raise AppError(
                "GitHub no longer knows that code (it lives an hour and works "
                "once): click Create repo-factory's GitHub App again",
                status=404,
                kind="code",
            )
        raise _refused(status, "/app-manifests/…/conversions", body)
    fp = store.write_key(str(body["pem"]))
    owner = body.get("owner") or {}
    perms = body.get("permissions") or {}
    rec = store.write_app(
        {
            "app_id": body["id"],
            "client_id": body.get("client_id"),
            "slug": body.get("slug"),
            "name": body.get("name"),
            "html_url": body.get("html_url"),
            "owner_login": owner.get("login"),
            "owner_type": owner.get("type"),
            "bot_login": f"{body.get('slug')}[bot]" if body.get("slug") else None,
            "bot_user_id": None,
            "permissions": {k: v for k, v in perms.items()},
            "events": list(body.get("events") or []),
            "installation_id": None,
            "repository_selection": None,
            "repos": [],
            "repos_read_at": None,
            "created_at": (now or time.time)(),
            "key_fingerprint": fp,
            "installed_at": None,
        }
    )
    app_for(store.root).forget()
    return rec


def link(app_id: str | int, pem: str, store: Store | None = None, now=None) -> dict:
    """An app that already exists on GitHub, linked instead of made again: its id and a key
    generated under its settings are checked with ``GET /app`` (GitHub answers only when the
    key is that app's), then the key goes to ``app.pem``, GitHub's facts to ``app.json``,
    and the installation is read back. Nothing is created on GitHub."""
    store = store or Store()
    if store.read() is not None:
        raise AppError(
            "repo-factory already has a GitHub App: remove it first to link another", kind="exists"
        )
    aid = str(app_id or "").strip()
    if not re.fullmatch(r"[0-9]{1,12}", aid):
        raise AppError(
            "the App ID is the number on the app's settings page on GitHub (General, About)",
            kind="key",
        )
    pem = str(pem or "").strip() + "\n"
    if not PEM_HEAD.search(pem):
        raise AppError("paste the whole key file, from -----BEGIN to -----END", kind="key")
    status, _, body = api("GET", "/app", auth=make_jwt(aid, pem.encode(), (now or time.time)()))
    if status != 200 or not isinstance(body, dict) or str(body.get("id")) != aid:
        raise AppError(
            f"GitHub did not accept that key for app {aid}: check the App ID, then "
            "generate a new private key under the app's settings and paste the "
            "whole file",
            status=status,
            kind="key",
        )
    fp = store.write_key(pem)
    owner = body.get("owner") or {}
    slug = body.get("slug")
    store.write_app(
        {
            "app_id": body["id"],
            "client_id": body.get("client_id"),
            "slug": slug,
            "name": body.get("name"),
            "html_url": body.get("html_url"),
            "owner_login": owner.get("login"),
            "owner_type": owner.get("type"),
            "bot_login": f"{slug}[bot]" if slug else None,
            "bot_user_id": None,
            "permissions": dict(body.get("permissions") or {}),
            "events": list(body.get("events") or []),
            "installation_id": None,
            "repository_selection": None,
            "repos": [],
            "repos_read_at": None,
            "created_at": (now or time.time)(),
            "key_fingerprint": fp,
            "installed_at": None,
        }
    )
    with contextlib.suppress(OSError):
        store.pending_path.unlink()
    app = app_for(store.root)
    app.forget()
    try:
        return app.refresh()
    except AppError as e:
        if e.kind != "not_installed":
            raise
        return store.read() or {}


# -- the background jobs (no request waits on GitHub: PA's rule) ---------------------------
SPAWN_ENV = "REPO_FACTORY_GITHUB_SPAWN"  # 0: run a job inline (tests)
JOBS = ("exchange", "refresh", "key", "link", "remove")


def run_job(store: Store, what: str, fn, inline: bool | None = None) -> dict:
    """Run ``fn()`` as the store's one background job ``what`` (a thread of this process;
    its state in ``job.json``: running, done, failed with a clean error), refused in words
    while another runs. A thread job of ``proc``: the record keeps this server's ``proc.me()``,
    so one a restart cut off reads as failed (``Store.job``)."""
    from . import proc

    if what not in JOBS:
        raise AppError(f"no such job {what!r}")
    cur = store.job()
    if (
        cur.get("state") == "running"
        and cur.get("pid") == os.getpid()
        and time.time() - float(cur.get("at") or 0) < 120
    ):
        raise AppError(
            f"repo-factory is still busy with the last step ({cur.get('what')}): wait a moment",
            kind="busy",
        )
    store.set_job(what, "running", **proc.me(), error=None, started=time.time())

    def go():
        try:
            out = fn()
            store.set_job(
                what,
                "done",
                error=None,
                finished=time.time(),
                result=out if isinstance(out, dict) else None,
            )
        except AppError as e:
            store.set_job(what, "failed", error=str(e), finished=time.time(), kind=e.kind)
        except Exception as e:  # noqa: BLE001 - said, never a traceback
            store.set_job(
                what,
                "failed",
                error=f"{type(e).__name__}: {clean(str(e), 200)}",
                finished=time.time(),
                kind="error",
            )

    if inline is None:
        inline = os.environ.get(SPAWN_ENV) == "0"
    if inline:
        go()
    else:
        proc.thread(go, f"github-app-{what}")
    return store.job()


def start_exchange(code: str, store: Store | None = None) -> dict:
    """The manifest's code into the app, in the background."""
    store = store or Store()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", str(code or "")):
        raise AppError("the code GitHub sent back does not look right: start again", kind="code")
    return run_job(store, "exchange", lambda: exchange(code, store))


def start_refresh(store: Store | None = None) -> dict:
    store = store or Store()
    return run_job(store, "refresh", lambda: app_for(store.root).refresh())


def start_key(pem: str, store: Store | None = None) -> dict:
    """A pasted key, checked against GitHub before it replaces the old one. The key goes to
    the job's closure and nowhere else (never into job.json)."""
    store = store or Store()
    if not PEM_HEAD.search(str(pem or "")):
        raise AppError("paste the whole key file, from -----BEGIN to -----END", kind="key")
    text = str(pem)
    return run_job(
        store,
        "key",
        lambda: {"key_fingerprint": app_for(store.root).replace_key(text).get("key_fingerprint")},
    )


def start_link(app_id: str | int, pem: str, store: Store | None = None) -> dict:
    """An existing app linked by its id and a pasted key, in the background; the key goes to
    the job's closure and nowhere else."""
    store = store or Store()
    if store.read() is not None:
        raise AppError(
            "repo-factory already has a GitHub App: remove it first to link another", kind="exists"
        )
    if not re.fullmatch(r"[0-9]{1,12}", str(app_id or "").strip()):
        raise AppError(
            "the App ID is the number on the app's settings page on GitHub (General, About)",
            kind="key",
        )
    if not PEM_HEAD.search(str(pem or "")):
        raise AppError("paste the whole key file, from -----BEGIN to -----END", kind="key")
    text = str(pem)
    return run_job(store, "link", lambda: {"app_id": link(app_id, text, store).get("app_id")})


def start_remove(store: Store | None = None) -> dict:
    store = store or Store()
    return run_job(store, "remove", lambda: app_for(store.root).remove())


def forward_page(path: str, to: str = "#/settings/github", note: str = "") -> str:
    """A small page that sends the browser on to the app's settings page: relative to where
    the request landed (``/api/github/app/callback``, or the same under a path prefix), so
    it works wherever the UI is mounted; a refresh, not a
    30x (``handle`` sets no headers, so no cookie is involved)."""
    from html import escape

    depth = max(0, len([p for p in path.strip("/").split("/") if p]) - 1)
    rel = escape(("../" * depth or "./") + to, quote=True)
    return (
        '<!doctype html><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0; url={rel}"><title>repo-factory</title>'
        f'<p>{escape(note) or "Back to repo-factory…"} <a href="{rel}">Open Settings › Publishing › GitHub</a>.</p>'
    )


def public_view(store: Store | None = None) -> dict:
    """What ``GET /api/github/app`` answers: the state, the facts (never a secret), the job,
    the links. Safe to serialise."""
    store = store or Store()
    rec = store.read()
    job = store.job()
    out = {
        "state": store.state(),
        "available": available() is None,
        "unavailable_reason": available(),
        "app": None,
        "job": {
            k: job.get(k) for k in ("what", "state", "error", "at", "finished", "kind") if k in job
        },
        "pending": bool(store.waiting()),
        "attempt": store.attempt(),
        "wait_s": WAIT_S,
        "permissions": dict(PERMISSIONS),
        "never": list(NEVER),
        "scopes": {k: dict(v) for k, v in SCOPES.items()},
    }
    if rec:
        out["app"] = {
            **rec,
            "install_url": (
                f"{rec['html_url']}/installations/new" if rec.get("html_url") else None
            ),
            "manage_url": (
                f"{WEB}/settings/installations/{rec['installation_id']}"
                if rec.get("installation_id")
                else None
            ),
            "settings_url": rec.get("html_url"),
            "has_key": store.has_key(),
        }
    return out

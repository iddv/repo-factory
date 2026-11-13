"""repo-factory's own connection to the person's GitHub account (WZ-DESIGN §3, the owner's D3 and D7).

One OAuth App the owner registers once ("repo-factory", device flow ticked; its client id is public
and there is no secret) lets each install sign in with GitHub's **device flow**: repo-factory shows a
code, the person types it at github.com/login/device, repo-factory polls until GitHub grants it. The
token then publishes builds, lists and adopts the person's repositories and downloads the
private release, through the same ``Gh`` token-file seam maintenance already uses
(``gh.connection``). repo-factory's own GitHub App (``github_app``) stays the bot identity; with no
connection the person's ``gh`` login is the fallback, so an install that never connects
behaves exactly as before.

**The store** is the App's folder, ``~/.config/repo-factory/github/`` (0700; ``github_app.store_dir``):

    user.env           GH_TOKEN=… at 0600: read by ``gh.token_env`` into the gh subprocess's
                       environment for one call, and by nothing else
    user.json          the login, the scopes, the client id, when, the token's expiry and the
                       last renewal's outcome: no secret, ever
    user-refresh.env   REFRESH_TOKEN=… at 0600, when GitHub gave one (an OAuth App with expiring
                       tokens: the token lasts eight hours, the refresh token six months)
    user.lock          held while a renewal runs, so two processes never spend one refresh token
    user-pending.json  one flow in flight (0600: it holds the device code), ten minutes

**Renewal** (``refresh``): an expiring token is renewed in the last ``REFRESH_WITHIN_S`` before
it ends, by the loop's tick in the background and before any use (``connection``). GitHub's
refresh tokens are single-use: the new pair replaces both files at once, under the lock. A
renewal GitHub refuses, or a token GitHub refused on a call (``mark_refused``), is kept in
``user.json``; the page and ``repo-factory doctor`` then say "click Connect again", and repo-factory acts as
the person's gh login until they do.

The token never reaches a page, a log, a command line, argv or this process's environment:
``status`` is an allowlist of fields, error lines go through ``github_app.clean``, and the
build sandbox binds neither config folder. Disconnect deletes the file; revoking the grant is
the person's click at github.com/settings/applications (an OAuth App token cannot be revoked
by API without the client secret, which repo-factory never has). Nothing here calls a model.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import github_app
from .gh import Gh
from .github_app import AppError, clean

# The OAuth App whose device flow connects repo-factory to GitHub as itself. No app is registered
# for repo-factory yet, so the device flow is off and the gh login is used; an install that has
# its own app sets ``github.client_id`` in loop.yaml, or $REPO_FACTORY_GITHUB_CLIENT_ID (a client id
# is public by GitHub's definition; there is no client secret and repo-factory never uses one).
CLIENT_ID = ""
CLIENT_ID_ENV = "REPO_FACTORY_GITHUB_CLIENT_ID"
WEB_ENV = "REPO_FACTORY_GITHUB_WEB"  # tests: the fake GitHub's base URL (loopback)
SCOPES = ("repo", "workflow", "read:org")
PENDING_TTL_S = 600
SECRET_FIELDS = (*github_app.SECRET_FIELDS, "access_token", "device_code", "refresh_token")
REVOKE_URL = "https://github.com/settings/applications"
INSTALL_LOGIN = "install-login.env"  # what install.sh's device flow leaves for repo-factory setup
REFRESH_WITHIN_S = 1800  # an expiring token is renewed in its last half hour
AGAIN = "click Connect again (Settings › Publishing › GitHub)"


def web_base() -> str:
    return (os.environ.get(WEB_ENV) or github_app.WEB).rstrip("/")


def grant_url(client: str) -> str:
    """The OAuth App's page in the person's GitHub settings (what it may do, Revoke)."""
    return f"{github_app.WEB}/settings/connections/applications/{client}"


def client_id(cfg=None) -> str:
    """``$REPO_FACTORY_GITHUB_CLIENT_ID``, else ``github.client_id`` in the loop's settings, else the
    release's ``CLIENT_ID``. Either setting set to "" turns the device flow off: "" means none
    is known (the device flow is not offered; the gh login is used)."""
    found = os.environ.get(CLIENT_ID_ENV)
    if found is None and cfg is not None:
        found = ((cfg.get("github") or {}) if hasattr(cfg, "get") else {}).get("client_id")
    return str(CLIENT_ID if found is None else found).strip()


class Store:
    """``user.env``, ``user.json`` and ``user-pending.json`` in the App's folder."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else github_app.store_dir()

    user_env = property(lambda self: self.root / "user.env")
    user_json = property(lambda self: self.root / "user.json")
    pending_json = property(lambda self: self.root / "user-pending.json")
    refresh_env = property(lambda self: self.root / "user-refresh.env")
    lock_path = property(lambda self: self.root / "user.lock")

    def _dir(self) -> Path:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        if self.root.parent.name == "repo-factory" and self.root.parent.stat().st_mode & 0o077:
            os.chmod(self.root.parent, 0o700)
        return self.root

    def connected(self) -> bool:
        """A token file that is the user's alone; a loose one is not used (``gh.token_env``
        would refuse it) and ``status`` says to chmod it."""
        try:
            return not (self.user_env.stat().st_mode & 0o077)
        except OSError:
            return False

    def record(self) -> dict:
        """``user.json`` without any secret field, whatever the file holds."""
        try:
            rec = json.loads(self.user_json.read_text())
        except (OSError, ValueError):
            return {}
        return (
            {k: v for k, v in rec.items() if k not in SECRET_FIELDS}
            if isinstance(rec, dict)
            else {}
        )

    def save(
        self, token: str, login: str, scopes: list[str], client: str, grant: dict | None = None
    ) -> dict:
        """Keep a new sign-in. ``grant``: GitHub's ``refresh_token``, ``expires_in`` and
        ``refresh_token_expires_in`` when the OAuth App's tokens expire (none: a token that
        lasts until revoked)."""
        self._dir()
        github_app._write(self.user_env, f"GH_TOKEN={token}\n")
        rec = {
            "login": login,
            "scopes": sorted(scopes),
            "client_id": client,
            "created": _iso(time.time()),
            "checked": None,
        }
        rec.update(self._keep_grant(grant or {}, time.time()))
        github_app._write(self.user_json, json.dumps(rec, indent=2) + "\n")
        self.pending_json.unlink(missing_ok=True)
        return rec

    def _keep_grant(self, grant: dict, now: float) -> dict:
        """The refresh token to its own 0600 file; the expiry fields for ``user.json``."""
        refresh = str(grant.get("refresh_token") or "")
        if refresh:
            github_app._write(self.refresh_env, f"REFRESH_TOKEN={refresh}\n")
        else:
            self.refresh_env.unlink(missing_ok=True)
        return {
            "expires_at": _after(grant.get("expires_in"), now),
            "refresh_expires_at": _after(grant.get("refresh_token_expires_in"), now)
            if refresh
            else None,
            "renews": bool(refresh),
            "refreshed": None,
            "refresh_error": None,
            "refused": None,
        }

    def update(self, **fields) -> None:
        """Change fields of ``user.json`` (never a secret one)."""
        try:
            rec = json.loads(self.user_json.read_text())
        except (OSError, ValueError):
            return
        if isinstance(rec, dict):
            rec.update({k: v for k, v in fields.items() if k not in SECRET_FIELDS})
            github_app._write(self.user_json, json.dumps(rec, indent=2) + "\n")

    def refresh_token(self) -> str:
        """The kept refresh token, read here for the one renewal call; "" when none or when
        the file is loose."""
        try:
            if self.refresh_env.stat().st_mode & 0o077:
                return ""
            text = self.refresh_env.read_text()
        except OSError:
            return ""
        for ln in text.splitlines():
            k, _, v = ln.strip().partition("=")
            if k.strip() == "REFRESH_TOKEN":
                return v.strip().strip("'\"")
        return ""

    def disconnect(self) -> bool:
        had = self.user_env.exists() or self.user_json.exists()
        for p in (self.user_env, self.user_json, self.pending_json, self.refresh_env):
            p.unlink(missing_ok=True)
        return had


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _after(seconds, now: float) -> float | None:
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return None
    return now + s if s > 0 else None


# -- GitHub ---------------------------------------------------------------------------------------


def post_form(path: str, fields: dict, timeout: float = github_app.TIMEOUT_S) -> dict:
    """One form POST to github.com (the device flow's two endpoints), JSON back. An error
    names the path, never the body."""
    req = urllib.request.Request(
        web_base() + path,
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Accept": "application/json", "User-Agent": "repo-factory"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raise AppError(f"GitHub answered {e.code} to {path}", status=e.code) from None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise AppError(
            f"GitHub could not be reached ({path}: {clean(getattr(e, 'reason', e))})",
            kind="network",
        ) from None
    try:
        out = json.loads(body.decode())
    except (ValueError, UnicodeDecodeError):
        raise AppError(f"GitHub's answer to {path} was not JSON") from None
    return out if isinstance(out, dict) else {}


def whoami(token: str) -> tuple[str, list[str], int | None]:
    """``GET /user`` with ``token``: the login, its OAuth scopes and the rate limit left."""
    status, headers, body = github_app.api("GET", "/user", auth=token)
    if status == 401:
        raise AppError(
            "GitHub refused the token (revoked, or never granted)", status=401, kind="refused"
        )
    if status != 200 or not isinstance(body, dict) or not body.get("login"):
        raise AppError(f"GitHub answered {status} to GET /user", status=status)
    scopes = [s.strip() for s in (headers.get("x-oauth-scopes") or "").split(",") if s.strip()]
    left = headers.get("x-ratelimit-remaining")
    return body["login"], scopes, int(left) if str(left or "").isdigit() else None


def wider(scopes) -> list[str]:
    """What GitHub granted beyond ``SCOPES`` (refused: nothing is kept)."""
    return sorted(set(scopes) - set(SCOPES))


def start(client: str, store: Store | None = None) -> dict:
    """Ask GitHub for a device code. Returns what the person needs (the code, the page, how
    long); the device code itself stays in ``user-pending.json``."""
    if not client:
        raise AppError(
            "no client id is known for repo-factory's OAuth App yet: use your gh login "
            "(gh auth login) until the release carries one",
            kind="unconfigured",
        )
    store = store or Store()
    got = post_form("/login/device/code", {"client_id": client, "scope": " ".join(SCOPES)})
    if got.get("error") or not got.get("device_code"):
        raise AppError(
            f"GitHub did not start the sign-in: {clean(got.get('error') or 'no code')}"
            + (
                " (is Device Flow ticked on the OAuth App?)"
                if got.get("error") == "device_flow_disabled"
                else ""
            )
        )
    now = time.time()
    pend = {
        "device_code": got["device_code"],
        "user_code": got["user_code"],
        "verification_uri": got.get("verification_uri") or "https://github.com/login/device",
        "interval": int(got.get("interval") or 5),
        "client_id": client,
        "started": now,
        "expires": now + min(int(got.get("expires_in") or PENDING_TTL_S), PENDING_TTL_S * 2),
    }
    store._dir()
    github_app._write(store.pending_json, json.dumps(pend) + "\n")
    return public_pending(pend, now)


def public_pending(pend: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    return {
        "user_code": pend["user_code"],
        "verification_uri": pend["verification_uri"],
        "interval": pend["interval"],
        "expires_in": max(0, int(pend["expires"] - now)),
    }


def poll(store: Store | None = None, now: float | None = None) -> dict:
    """One poll of the flow in flight: ``{"state": pending|connected|expired|denied|none,
    ...}``. ``slow_down`` adds five seconds to the interval, as GitHub asks. Connected: the
    token is checked (``GET /user``), refused when GitHub granted wider scopes than asked."""
    store = store or Store()
    try:
        pend = json.loads(store.pending_json.read_text())
    except (OSError, ValueError):
        return {"state": "none"}
    now = time.time() if now is None else now
    if now >= (pend.get("expires") or 0):
        store.pending_json.unlink(missing_ok=True)
        return {"state": "expired", "line": "the code expired: start again"}
    got = post_form(
        "/login/oauth/access_token",
        {
            "client_id": pend["client_id"],
            "device_code": pend["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
    )
    err = got.get("error")
    if err in ("authorization_pending", "slow_down"):
        if err == "slow_down":
            pend["interval"] = int(got.get("interval") or pend["interval"] + 5)
            github_app._write(store.pending_json, json.dumps(pend) + "\n")
        return {"state": "pending", **public_pending(pend, now)}
    if err:
        store.pending_json.unlink(missing_ok=True)
        if err == "access_denied":
            return {"state": "denied", "line": "the sign-in was cancelled on GitHub"}
        if err == "expired_token":
            return {"state": "expired", "line": "the code expired: start again"}
        return {"state": "failed", "line": clean(f"GitHub said {err}")}
    token = got.get("access_token") or ""
    return {
        "state": "connected",
        **keep(
            token,
            pend["client_id"],
            store,
            granted=(got.get("scope") or "").replace(",", " ").split(),
            grant=got,
        ),
    }


def keep(
    token: str,
    client: str,
    store: Store,
    granted: list[str] | None = None,
    grant: dict | None = None,
) -> dict:
    """Check ``token`` with GitHub and keep it: the login and scopes from ``GET /user``;
    scopes wider than ``SCOPES`` are refused and nothing is written. ``grant``: GitHub's
    answer, whose refresh token (if any) is kept for ``refresh``."""
    login, scopes, left = whoami(token)
    extra = wider([*scopes, *(granted or [])])
    if extra:
        store.pending_json.unlink(missing_ok=True)
        raise AppError(
            f"GitHub granted wider scopes than repo-factory asked for ({', '.join(extra)}); "
            "nothing was kept",
            kind="scopes",
        )
    rec = store.save(token, login, scopes, client, grant)
    return {"login": login, "scopes": rec["scopes"], "rate_left": left}


def refresh(
    store: Store | None = None, *, now: float | None = None, force: bool = False, clock=time.time
) -> dict:
    """Renew an expiring token in its last ``REFRESH_WITHIN_S`` (``force``: now). ``{"state":
    none|fresh|refreshed|failed, "line"}``: ``none`` is a token that does not expire (or no
    connection). Under ``user.lock``: a second process waits, then finds the token fresh. A
    refusal from GitHub is kept in ``user.json`` (``refresh_error``) for the page and the
    doctor; a network failure is kept too, and the next tick tries again."""
    store = store or Store()
    if not store.connected():
        return {"state": "none", "line": "not connected"}
    now = clock() if now is None else now
    rec = store.record()
    if not rec.get("expires_at") and not force:
        return {"state": "none", "line": "the token does not expire"}
    if not force and rec["expires_at"] - now > REFRESH_WITHIN_S:
        return {"state": "fresh", "line": f"valid until {_iso(rec['expires_at'])}"}
    store._dir()
    fd = os.open(store.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        rec = store.record()  # another process may have renewed it
        if not force and rec.get("expires_at") and rec["expires_at"] - now > REFRESH_WITHIN_S:
            return {"state": "fresh", "line": f"valid until {_iso(rec['expires_at'])}"}
        return _renew(store, rec, now)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _renew(store: Store, rec: dict, now: float) -> dict:
    def failed(line: str, kind: str) -> dict:
        store.update(refresh_error={"line": line, "kind": kind, "at": _iso(now)})
        return {"state": "failed", "line": line, "kind": kind}

    old = store.refresh_token()
    if not old:
        return failed(
            f"repo-factory's GitHub sign-in cannot renew itself (no refresh token was kept): "
            f"{AGAIN}",
            "refused",
        )
    if rec.get("refresh_expires_at") and rec["refresh_expires_at"] <= now:
        return failed(
            f"repo-factory's GitHub sign-in was not renewed for six months and lapsed: {AGAIN}",
            "refused",
        )
    try:
        got = post_form(
            "/login/oauth/access_token",
            {
                "client_id": rec.get("client_id") or client_id(),
                "grant_type": "refresh_token",
                "refresh_token": old,
            },
        )
    except AppError as e:
        return failed(
            f"GitHub could not renew repo-factory's sign-in ({e}); repo-factory tries again",
            "network" if e.kind == "network" else "error",
        )
    if got.get("error") or not got.get("access_token"):
        return failed(
            f"GitHub refused to renew repo-factory's sign-in "
            f"({clean(got.get('error') or 'no token')}): {AGAIN}",
            "refused",
        )
    extra = wider((got.get("scope") or "").replace(",", " ").split())
    if extra:
        return failed(
            f"GitHub renewed repo-factory's sign-in with wider scopes ({', '.join(extra)}); "
            f"nothing was kept: {AGAIN}",
            "refused",
        )
    # both single-use halves replaced together: the old pair no longer works
    github_app._write(store.user_env, f"GH_TOKEN={got['access_token']}\n")
    fields = store._keep_grant(got, now)
    if not got.get("refresh_token"):  # GitHub kept the old one
        github_app._write(store.refresh_env, f"REFRESH_TOKEN={old}\n")
        fields.update(renews=True, refresh_expires_at=rec.get("refresh_expires_at"))
    fields["refreshed"] = _iso(now)
    store.update(**fields)
    return {
        "state": "refreshed",
        "line": f"renewed; valid until {_iso(fields['expires_at'] or now)}",
    }


def mark_refused(store: Store | None = None, line: str = "") -> None:
    """GitHub refused the connection's token on a call (401): kept, so the page and the doctor
    say to click Connect again and ``connection`` stops using it."""
    store = store or Store()
    if store.connected() and not store.record().get("refused"):
        store.update(
            refused={
                "at": _iso(time.time()),
                "line": clean(line)[:200] or "GitHub refused the token",
            }
        )


def needs_connect(rec: dict, now: float | None = None) -> str:
    """Why the kept token no longer works ("" when it may): GitHub refused it, or it expired
    and could not be renewed."""
    now = time.time() if now is None else now
    if rec.get("refused"):
        return f"GitHub refused repo-factory's sign-in (expired or revoked): {AGAIN}"
    err = rec.get("refresh_error") or {}
    if rec.get("expires_at") and rec["expires_at"] <= now:
        if err.get("kind") == "refused":
            return err.get("line") or f"repo-factory's GitHub sign-in expired: {AGAIN}"
        if not rec.get("renews"):
            return f"repo-factory's GitHub sign-in expired and cannot renew itself: {AGAIN}"
        return f"repo-factory's GitHub sign-in expired and is not renewed yet ({err.get('line') or 'waiting'})"
    return ""


def ensure_fresh(store: Store | None = None) -> dict:
    """``refresh`` that never raises: the loop's tick and every ``connection``."""
    try:
        return refresh(store)
    except Exception as e:  # noqa: BLE001 - a renewal must never break the caller
        return {"state": "failed", "line": clean(e), "kind": "error"}


def wait(
    client: str, store: Store | None = None, *, say=print, sleep=time.sleep, clock=time.time
) -> dict:
    """The whole flow for a terminal (``repo-factory setup github``, ``repo-factory github connect``): print
    the code and the page, poll at GitHub's interval until it ends."""
    store = store or Store()
    first = start(client, store)
    say(
        f"  open {first['verification_uri']} and enter   {first['user_code']}"
        f"   (waiting · {first['expires_in'] // 60} minutes)"
    )
    interval = first["interval"]
    while True:
        sleep(interval)
        got = poll(store, now=clock())
        if got["state"] != "pending":
            return got
        interval = got["interval"]


def import_login(path: Path, client: str = "", store: Store | None = None) -> dict:
    """Keep the login install.sh's device flow left (``GH_TOKEN=…`` at 0600) as this repo-factory's
    connection, so the person signs in once; the file is removed whatever happens. Its
    ``GH_REFRESH_TOKEN``, ``GH_TOKEN_EXPIRES_IN`` and ``GH_REFRESH_EXPIRES_IN`` lines, when
    GitHub gave them, are kept for ``refresh``."""
    store = store or Store()
    p = Path(path).expanduser()
    try:
        if p.stat().st_mode & 0o077:
            raise AppError(f"{p} is readable by other users; it was not used")
        text = p.read_text()
    except OSError as e:
        raise AppError(f"{p} cannot be read ({e.strerror or 'error'})") from None
    finally:
        p.unlink(missing_ok=True)
    lines = {}
    for ln in text.splitlines():
        k, sep, v = ln.strip().partition("=")
        if sep:
            lines.setdefault(k.strip(), v.strip().strip("'\""))
    token = lines.get("GH_TOKEN") or text.strip().partition("=")[2].strip().strip("'\"")
    if not token:
        raise AppError(f"{p} has no GH_TOKEN= line")
    grant = {
        "refresh_token": lines.get("GH_REFRESH_TOKEN"),
        "expires_in": lines.get("GH_TOKEN_EXPIRES_IN"),
        "refresh_token_expires_in": lines.get("GH_REFRESH_EXPIRES_IN"),
    }
    return keep(token, client or client_id(), store, grant=grant)


keep_handoff = import_login  # what repo-factory setup calls with install.sh's file


def status(store: Store | None = None, *, check: bool = False) -> dict:
    """What a page or a doctor line may show: ``{"connected", "login", "scopes", "since",
    "client_id_known", "loose", "valid", "rate_left", "revoke_url", "expires_at", "renews",
    "refreshed", "refresh_error", "needs_connect"}``; never the token. ``check`` asks GitHub
    once whether it still works. ``needs_connect``: the token no longer works (refused, or
    expired and not renewed), with the line that says to click Connect again."""
    store = store or Store()
    rec = store.record()
    out = {
        "connected": store.connected(),
        "login": rec.get("login"),
        "scopes": rec.get("scopes") or [],
        "since": rec.get("created"),
        "client_id_known": bool(client_id()),
        "loose": False,
        "valid": None,
        "rate_left": None,
        "revoke_url": REVOKE_URL,
        "line": "",
        "expires_at": _iso(rec["expires_at"]) if rec.get("expires_at") else None,
        "renews": bool(rec.get("renews")),
        "refreshed": rec.get("refreshed"),
        "refresh_error": (rec.get("refresh_error") or {}).get("line"),
        "needs_connect": False,
    }
    if store.user_env.exists() and not out["connected"]:
        out["loose"] = True
        out["line"] = f"{store.user_env} is readable by other users: chmod 600 it"
        return out
    if check and out["connected"]:
        from .gh import token_env

        try:
            login, scopes, left = whoami(token_env(store.user_env)["GH_TOKEN"])
            out.update(valid=True, login=login, scopes=scopes, rate_left=left)
            if rec.get("refused"):  # it works again (renewed elsewhere)
                store.update(refused=None)
                rec = store.record()
        except AppError as e:
            out.update(valid=False, line=str(e))
            if e.kind == "refused":
                mark_refused(store, str(e))
                rec = store.record()
        except Exception as e:  # noqa: BLE001 - a status, never a crash
            out.update(valid=False, line=clean(e))
    why = needs_connect(rec) if out["connected"] else ""
    if why:
        out.update(needs_connect=True, valid=False, line=why)
    if not out["line"]:
        out["line"] = (
            f"connected as {out['login']} ({', '.join(out['scopes'])})"
            if out["connected"]
            else "not connected"
        )
    return out


def device_app(cfg=None) -> str:
    """The OAuth App repo-factory signs in with (``client_id``): "" until one is known."""
    return client_id(cfg)


def connected_file() -> Path | None:
    """The connection's token file when repo-factory is connected (0600), else None: what a
    caller hands gh when ``update.token_env_file`` names nothing. Renewed first when it
    is about to expire; None when it no longer works (the gh login is used instead)."""
    store = Store()
    if not store.connected():
        return None
    ensure_fresh(store)
    return None if needs_connect(store.record()) else store.user_env


def stale(gh) -> bool:
    """Whether a ``connection`` made earlier no longer says who acts: it used the sign-in and
    that token stopped working, or it fell back to the gh login and the person has signed in
    again since. Anything else (a test's fake, a command) is never stale."""
    if type(gh) is not Gh:
        return False
    store = Store()
    if getattr(gh, "connected_as", None) is not None:
        return not store.connected() or bool(needs_connect(store.record()))
    return gh.env_file is None and store.connected() and not needs_connect(store.record())


def connection(cfg) -> Gh:
    """The ``Gh`` that acts as the person (WZ-DESIGN §3.6): this connection (its token file,
    read into each gh subprocess) when there is one, else the person's ``gh`` login, exactly
    as before. Publishing, adopting, the repository list and the loop's own ``gh`` use it;
    maintenance asks ``identity.resolve`` first, which prefers repo-factory's GitHub App. An expiring
    token is renewed first; one that no longer works (``needs_connect``) is not used: the gh
    login acts until the person presses Connect again."""
    cmd = cfg["publish"].get("gh_command")
    store = Store()
    if store.connected():
        ensure_fresh(store)
    if store.connected() and not needs_connect(store.record()):
        g = Gh(cmd=cmd, env_file=store.user_env)
        g.connected_as = store.record().get("login")
        return g
    return Gh(cmd=cmd)

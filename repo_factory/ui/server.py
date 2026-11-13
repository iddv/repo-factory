"""``repo-factory ui``: the local web page, and the loop daemon in the same process.

A stdlib server around ``api.handle``: this file only binds, guards and writes the responses.
Binds 127.0.0.1 only: a page that can start paid runs must not be on the network. A request
whose Host is not a loopback name is refused on every method (400), so a page on another name
that a DNS rebinding points here can neither read nor write; a write needs an ``Origin`` (or
``Referer``) equal to the Host, port included (403).

A POST needs a repo-factory token: ``Authorization: Bearer`` (the command line, an agent) or
the page's session cookie, which only a one-time sign-in link sets (``/auth/open?code=``;
``repo-factory ui`` prints one at start). The token says who acts.

``serve`` runs the page in a thread and the loop (``Loop.run``: the queue, the builds, the
gate, publishing on the owner's press) in the main thread, so one start does both; Ctrl-C
stops the loop (running builds keep going and the next start adopts them), then the page.
"""

from __future__ import annotations

import errno
import json
import logging
import re
import sys
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import tokens as tokens_mod
from . import api

# The names this module had before the API moved to api.py (tests and callers use them).
from .api import (  # noqa: F401
    APP_PAGE,
    BRB,
    CALL_TYPES,
    DEFAULT_POLICY,
    DONE,
    GROUP,
    HOST,
    NEEDS_YOU,
    POLICIES,
    REPO,
    RUNNING,
    SKIP,
    SPAWN_GRACE_S,
    App,
    HttpError,
    is_task,
    scan,
    tail,
)

LOOPBACK = ("127.0.0.1", "localhost")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
MAX_DRAIN = 1 << 20
# /history renders its own inline style; it may load nothing else.
HISTORY_CSP = "default-src 'none'; style-src 'unsafe-inline'"
# Every other answer: scripts only from this origin and never inline; github.com is the one
# form the page posts elsewhere.
PAGE_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
    "form-action 'self' https://github.com; frame-ancestors 'none'"
)
JSON = "application/json; charset=utf-8"
COOKIE_MAX_AGE = 400 * 86400
WRITE_TIMEOUT_S = 5.0  # a live stream whose page stops reading is dropped
log = logging.getLogger(__name__)


def _hostname(netloc: str) -> str:
    """The host part of a Host header or URL netloc, lowercased, without port."""
    try:
        return (urlparse(f"//{netloc}").hostname or "").lower()
    except ValueError:
        return ""


def make_handler(app: App, store: tokens_mod.Store | None = None, require: bool = True):
    store = store or tokens_mod.Store()

    class Handler(BaseHTTPRequestHandler):
        server_version = "repo-factory-ui"

        def log_message(self, fmt, *a):  # quiet
            pass

        @property
        def cookie_name(self) -> str:
            return tokens_mod.cookie_name(self.server.server_address[1])

        def identify(self) -> tuple[dict | None, str]:
            """``(the caller, why not)`` from the bearer token or the session cookie."""
            authz = self.headers.get("Authorization") or ""
            if authz[:7].lower() == "bearer ":
                ident = store.identify(authz[7:].strip())
                return (
                    ({**ident, "via": "bearer"}, "")
                    if ident
                    else (None, "its bearer token is not a current repo-factory token")
                )
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie") or "")
            except Exception:  # noqa: BLE001 - a broken header: no cookie
                pass
            morsel = jar.get(self.cookie_name)
            if morsel is not None:
                ident = store.identify(morsel.value)
                if ident and ident["origin"] == "ui":
                    return {**ident, "via": "cookie"}, ""
                return None, "its session cookie is not current"
            return None, "it carries no repo-factory token"

        def signed_in(self, value: str) -> dict:
            return {
                "Set-Cookie": f"{self.cookie_name}={value}; Max-Age={COOKIE_MAX_AGE}; "
                "Path=/; HttpOnly; SameSite=Strict"
            }

        def send(self, code: int, ctype: str, data: bytes, headers: dict | None = None):
            headers = {"Content-Security-Policy": PAGE_CSP, **(headers or {})}
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def refused(self) -> bool:
            """Answer and return True when the guard refuses the request: a Host that is not
            a loopback name (every method), or a write whose Origin, else Referer, is not the
            Host itself, port included."""
            host = self.headers.get("Host") or ""
            if _hostname(host) not in LOOPBACK_HOSTS:
                self.drain()
                self.send(400, "text/plain; charset=utf-8", b"unknown host")
                return True
            if self.command not in SAFE_METHODS:
                origin = self.headers.get("Origin") or self.headers.get("Referer") or ""
                try:
                    netloc = urlparse(origin).netloc.lower()
                except ValueError:
                    netloc = ""
                if netloc != host.lower():
                    self.drain()
                    self.send(403, JSON, b'{"error": "cross-origin request refused"}')
                    return True
            return False

        def drain(self) -> None:
            """Read (bounded) the body of a refused request, so the client is not cut off
            while it still writes."""
            try:
                left = min(max(int(self.headers.get("Content-Length") or 0), 0), MAX_DRAIN)
            except ValueError:
                return
            while left > 0:
                chunk = self.rfile.read(min(left, 65536))
                if not chunk:
                    break
                left -= len(chunk)

        def respond(self, method: str, body: bytes = b"", identity: dict | None = None):
            u = urlparse(self.path)
            code, ctype, data = api.handle(
                method, u.path, u.query, body, app=app, identity=identity
            )
            self.send(
                code,
                ctype,
                data,
                {"Content-Security-Policy": HISTORY_CSP} if u.path == "/history" else None,
            )

        def do_GET(self):
            if self.refused():
                return
            u = urlparse(self.path)
            if u.path == "/auth/open":  # the one-time sign-in link
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                value = store.redeem(q.get("code"))
                if not value:
                    return self.send(
                        400,
                        "text/html; charset=utf-8",
                        b"<!doctype html><p>This "
                        b"sign-in link has expired or was used already. For a new one, run "
                        b"<code>repo-factory link</code>.</p>",
                    )
                nxt = q.get("next") or "/"
                nxt = nxt if re.fullmatch(r"/(?!/)[\w./#?=&%-]{0,300}", nxt) else "/"
                return self.send(
                    303,
                    "text/plain; charset=utf-8",
                    b"",
                    {"Location": nxt, **self.signed_in(value)},
                )
            if u.path == "/api/whoami":
                ident, _ = self.identify()
                return self.send(
                    200,
                    JSON,
                    json.dumps(
                        {
                            "origin": (ident or {}).get("origin"),
                            "via": (ident or {}).get("via"),
                            "require_token": require,
                            "signin": "repo-factory ui",
                        }
                    ).encode(),
                )
            if u.path.rstrip("/") == api.STREAM_PATH:
                return self.stream(u.query)
            self.respond("GET")

        def stream(self, query: str):
            """The live feed: headers now, then each event as it comes, until the page goes
            away (a write that fails or stalls for WRITE_TIMEOUT_S ends it)."""
            try:
                events = api.stream(
                    api.STREAM_PATH, query, app=app, last=self.headers.get("Last-Event-ID")
                )
            except api.HttpError as e:
                return self.send(e.code, JSON, json.dumps({"error": str(e)}).encode())
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", PAGE_CSP)
            self.end_headers()
            self.close_connection = True
            self.connection.settimeout(WRITE_TIMEOUT_S)
            try:
                for chunk in events:
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except OSError:  # the page went away, or stalled
                pass
            finally:
                events.close()

        def do_HEAD(self):
            if self.refused():
                return
            self.respond("HEAD")

        def do_POST(self):
            # Same-origin only: a cross-site page must not start paid runs.
            if self.refused():
                return
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            path = urlparse(self.path).path
            if path == "/auth/code":  # the page's sign-in box
                try:
                    code = (json.loads(body or b"{}") or {}).get("code")
                except (ValueError, AttributeError):
                    code = None
                value = store.redeem(str(code or "").strip())
                if not value:
                    return self.send(
                        400,
                        JSON,
                        b'{"error": "that code has expired or was used '
                        b"already: start repo-factory ui again for a new "
                        b'link"}',
                    )
                return self.send(200, JSON, b'{"ok": true, "origin": "ui"}', self.signed_in(value))
            ident, why = self.identify()
            if ident is None and require:
                log.warning("refused POST %s: %s", path, why)
                return self.send(
                    401,
                    JSON,
                    json.dumps(
                        {
                            "error": f"not signed in: {why}. In a browser, open the sign-in link "
                            "repo-factory ui prints; scripts and agents send 'Authorization: "
                            "Bearer' with their own token"
                        }
                    ).encode(),
                )
            self.respond("POST", body, ident)

    return Handler


DEFAULT_PORT = 8790
PORT_TRIES = 20


def require_token(loop_home: Path | None) -> bool:
    """``api.require_token`` in loop.yaml (default true)."""
    from ..settings.store import Config

    try:
        return bool((Config(loop_home).get("api") or {}).get("require_token", True))
    except Exception:  # noqa: BLE001 - no loop config: the default
        return True


def make_server(
    root: Path,
    port: int = DEFAULT_PORT,
    harness: str = "claude-code",
    tries: int = 1,
    loop_home: Path | None = None,
    tokens: tokens_mod.Store | None = None,
    require: bool | None = None,
):
    """Bind ``port``; if it is in use, try the next ports (up to ``tries`` in all).
    ``tokens``: the token store (default ``<home>/tokens``; its door tokens are made when
    missing); ``require``: writes need a token (default: ``api.require_token``)."""
    app = App(root, harness, loop_home)
    store = tokens or tokens_mod.Store(home=loop_home)
    store.ensure_doors()
    need = require_token(loop_home) if require is None else require
    for p in range(port, port + max(tries, 1)):
        try:
            srv = ThreadingHTTPServer((HOST, p), make_handler(app, store, need))
            break
        except OSError as e:
            if e.errno != errno.EADDRINUSE or p == port + max(tries, 1) - 1 or port == 0:
                raise
    srv.daemon_threads = True
    srv.app = app
    srv.tokens = store
    return srv


def serve(
    root: Path,
    port: int = DEFAULT_PORT,
    harness: str = "claude-code",
    loop_home: Path | None = None,
    loop: bool = True,
) -> None:
    """Serve the page; with ``loop``, run the loop daemon in this process too."""
    try:
        srv = make_server(root, port, harness, tries=PORT_TRIES, loop_home=loop_home)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        raise SystemExit(
            f"repo-factory ui: ports {port}-{port + PORT_TRIES - 1} are all in use; "
            "pick one with --port"
        )
    bound = srv.server_address[1]
    if bound != port:
        print(f"repo-factory ui: port {port} is in use; using {bound}", file=sys.stderr)
    mode = " (demo: fake harness, no tokens)" if harness == "fake" else ""
    print(f"repo-factory ui: http://{HOST}:{bound}/  root {srv.app.root}{mode}", file=sys.stderr)
    # the link signs one browser in, once, within 10 minutes (its code is used up then)
    print(
        f"Open this link to sign this browser in: http://{HOST}:{bound}/auth/open"
        f"?code={srv.tokens.mint_code()}",
        file=sys.stderr,
    )
    print(
        "Loopback only. From another machine: ssh -L "
        f"{bound}:localhost:{bound} <this host>, then open the link above with localhost",
        file=sys.stderr,
    )
    # where the page is, for `repo-factory link` (a new sign-in link for another browser)
    from ..paths import home as _home

    where = _home(loop_home) / URL_FILE
    url_file: Path | None = where
    try:
        where.write_text(f"http://{HOST}:{bound}/\n")
    except OSError:
        url_file = None
    import threading

    t = threading.Thread(target=srv.serve_forever, name="page", daemon=True)
    t.start()
    try:
        if loop:
            run_loop(loop_home)
        t.join()
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
        srv.server_close()
        if url_file is not None:
            url_file.unlink(missing_ok=True)


URL_FILE = "ui.url"


def sign_in_link(home: Path | None = None) -> str:
    """A new one-time sign-in link for the page that is running from ``home``; SystemExit in
    words when none is."""
    from ..paths import home as _home

    h = _home(home)
    try:
        url = (h / URL_FILE).read_text().strip()
    except OSError:
        raise SystemExit(
            f"repo-factory link: no page is running from {h}: start it with "
            "repo-factory ui (it prints a link)"
        ) from None
    return f"{url.rstrip('/')}/auth/open?code={tokens_mod.Store(home=home).mint_code()}"


def run_loop(home: Path | None) -> None:
    """The loop daemon (``repo-factory loop run``) in this process; when another process holds
    the loop already, the page alone is served."""
    from ..errors import LoopError
    from ..loop.daemon import Loop
    from ..settings.store import Config

    try:
        Loop(Config(home)).run()
    except LoopError as e:
        print(f"repo-factory ui: {e}; serving the page only", file=sys.stderr)
        return
    raise KeyboardInterrupt  # the loop stopped (Ctrl-C, or loop stop)

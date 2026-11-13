"""A fake github.com and api.github.com for repo-factory's GitHub connection (``github_user``) and the
repository picker, on 127.0.0.1 at an ephemeral port (``$REPO_FACTORY_GITHUB_WEB`` and
``$REPO_FACTORY_GITHUB_API`` point at it). The device flow answers ``answers`` in order (then grants);
the token is ``"gho_" + secrets.token_hex(18)``, made at run time. ``GET /user`` reports
``scopes`` in ``X-OAuth-Scopes``; ``/user/repos``, ``/user/orgs``, ``/orgs/<o>/repos`` and
``/installation/repositories`` page ``repos``. Nothing here touches the network; nothing is
ever written."""

from __future__ import annotations

import json
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeGitHubUser:
    def __init__(self, login: str = "ann", scopes: str = "repo, workflow, read:org"):
        self.login, self.scopes = login, scopes
        self.token = "gho_" + secrets.token_hex(18)
        self.answers: list[dict] = []  # the access-token poll's answers before grant
        self.granted_scope = "repo,workflow,read:org"
        self.repos: list[dict] = []
        self.org_repos: dict[str, list[dict]] = {}
        self.orgs: list[str] = []
        self.requests: list[tuple[str, str, str]] = []  # method, path, auth kind
        self.revoked = False
        self.device_error: str | None = None
        # an OAuth App with expiring tokens: eight hours, and a single-use refresh token
        self.expiring = False
        self.refresh = "ghr_" + secrets.token_hex(30)
        self.spent: set[str] = set()  # refresh tokens used once already
        self.refreshes: list[dict] = []  # each renewal's form, without the token
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, headers=None):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
                fake.requests.append(("POST", self.path, "form"))
                if self.path == "/login/device/code":
                    if fake.device_error:
                        return self._send(200, {"error": fake.device_error})
                    return self._send(
                        200,
                        {
                            "device_code": "dev-" + secrets.token_hex(8),
                            "user_code": "ABCD-1234",
                            "interval": 5,
                            "expires_in": 900,
                            "verification_uri": "https://github.com/login/device",
                            "scope_asked": form.get("scope"),
                        },
                    )
                if self.path == "/login/oauth/access_token":
                    if form.get("grant_type") == "refresh_token":
                        fake.refreshes.append(
                            {k: v for k, v in form.items() if k != "refresh_token"}
                        )
                        old = form.get("refresh_token")
                        if fake.answers:
                            return self._send(200, fake.answers.pop(0))
                        if old != fake.refresh or old in fake.spent:
                            return self._send(200, {"error": "bad_refresh_token"})
                        fake.spent.add(old)  # single use: the old pair stops working
                        fake.token = "gho_" + secrets.token_hex(18)
                        fake.refresh = "ghr_" + secrets.token_hex(30)
                        return self._send(200, fake.grant())
                    if fake.answers:
                        return self._send(200, fake.answers.pop(0))
                    return self._send(200, fake.grant())
                return self._send(404, {"message": "Not Found"})

            def do_GET(self):
                auth = self.headers.get("Authorization") or ""
                ok = auth == f"Bearer {fake.token}" and not fake.revoked
                fake.requests.append(("GET", self.path, "token" if ok else "none"))
                if not ok:
                    return self._send(401, {"message": "Bad credentials"})
                u = urllib.parse.urlparse(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                head = {"X-OAuth-Scopes": fake.scopes, "X-RateLimit-Remaining": "4999"}
                if u.path == "/user":
                    return self._send(200, {"login": fake.login}, head)
                if u.path == "/user/orgs":
                    return self._send(200, [{"login": o} for o in fake.orgs], head)
                if u.path == "/user/repos":
                    return self._send(200, fake.page(fake.repos, q), head)
                if u.path.startswith("/orgs/") and u.path.endswith("/repos"):
                    org = u.path.split("/")[2]
                    return self._send(200, fake.page(fake.org_repos.get(org, []), q), head)
                if u.path == "/installation/repositories":
                    rows = fake.page(fake.repos, q)
                    return self._send(
                        200, {"total_count": len(fake.repos), "repositories": rows}, head
                    )
                return self._send(404, {"message": "Not Found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def grant(self) -> dict:
        out = {"access_token": self.token, "token_type": "bearer", "scope": self.granted_scope}
        if self.expiring:
            out.update(
                expires_in=28800, refresh_token=self.refresh, refresh_token_expires_in=15897600
            )
        return out

    @staticmethod
    def page(rows: list[dict], q: dict) -> list[dict]:
        per, n = int(q.get("per_page") or 30), int(q.get("page") or 1)
        return rows[(n - 1) * per : n * per]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def repo(
    full: str,
    *,
    pushed: str = "2026-10-15T10:00:00Z",
    fork: bool = False,
    archived: bool = False,
    private: bool = False,
    size: int = 120,
    language: str = "Python",
    issues: int = 0,
) -> dict:
    owner, name = full.split("/")
    return {
        "full_name": full,
        "name": name,
        "owner": {"login": owner},
        "fork": fork,
        "archived": archived,
        "private": private,
        "size": size,
        "language": language,
        "pushed_at": pushed,
        "open_issues_count": issues,
        "permissions": {"admin": True, "push": True, "pull": True},
    }

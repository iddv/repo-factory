"""A fake GitHub REST API for the app's tests, on 127.0.0.1 at an ephemeral port
(``$REPO_FACTORY_GITHUB_API`` points ``github_app`` at it). It implements the manifest conversion,
the app's JWT endpoints, installation tokens, the installation's repositories, the bot
user, and the uninstall. Every JWT is verified against the public half of a key generated
when the fake starts (``rsa.generate_private_key``: never a literal in the repo), and every
token is ``"ghs_" + secrets.token_hex(20)``. Nothing here touches the network."""

from __future__ import annotations

import base64
import json
import re
import secrets
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class FakeGitHub:
    """``codes``: manifest codes that convert (each once); ``installation``: the one
    installation (None until ``install``); ``requests``: every request ``(method, path,
    auth kind, body)``; ``token_requests``: the access-token bodies in order; ``revoked``:
    every token call answers 401 (uninstalled, suspended); ``now``: a replaceable clock."""

    def __init__(self, owner: str = "iddv", slug: str = "factory-iddv", app_id: int = 4242):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        self.owner, self.slug, self.app_id = owner, slug, app_id
        self.client_id = "Iv1." + secrets.token_hex(8)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode()
        self.public = key.public_key()
        self.codes: set[str] = set()
        self.installation: dict | None = None
        self.tokens: dict[str, dict] = {}
        self.requests: list[tuple] = []
        self.token_requests: list[dict] = []
        self.jwt_errors: list[str] = []
        self.revoked = False
        self.gone = False  # the app registration was deleted
        self.bot_user_id = 90000000 + secrets.randbelow(9999999)
        self.now = time.time
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, body=None, headers=None):
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if data:
                    self.wfile.write(data)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    return json.loads(raw) if raw else {}
                except ValueError:
                    return {}

            def do_GET(self):
                self._any("GET")

            def do_POST(self):
                self._any("POST")

            def do_DELETE(self):
                self._any("DELETE")

            def _any(self, method):
                body = self._body() if method == "POST" else {}
                try:
                    code, out = fake.handle(method, self.path, self.headers, body)
                except Exception as e:  # noqa: BLE001 - the fake's own bug, said plainly
                    code, out = 500, {"message": f"fake github: {type(e).__name__}: {e}"}
                self._send(code, out)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    # -- helpers for tests -------------------------------------------------------------------
    def code(self) -> str:
        c = secrets.token_urlsafe(16)
        self.codes.add(c)
        return c

    def install(self, repos: list[str], selection: str = "selected", iid: int = 777) -> dict:
        self.installation = {
            "id": iid,
            "repository_selection": selection,
            "repos": list(repos),
            "account": {"login": self.owner, "type": "User"},
        }
        return self.installation

    def uninstall(self) -> None:
        self.installation = None
        self.revoked = True

    def expire_tokens(self) -> None:
        for t in self.tokens.values():
            t["expires_at"] = self.now() - 1

    def app_record(self) -> dict:
        return {
            "id": self.app_id,
            "client_id": self.client_id,
            "slug": self.slug,
            "name": self.slug,
            "html_url": f"https://github.com/apps/{self.slug}",
            "owner": {"login": self.owner, "type": "User", "id": 1},
            "permissions": {
                "metadata": "read",
                "issues": "write",
                "pull_requests": "write",
                "contents": "write",
                "checks": "read",
                "actions": "read",
            },
            "events": [],
        }

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # -- the JWT check -------------------------------------------------------------------------
    def verify_jwt(self, auth: str | None) -> dict | None:
        """The payload of a valid app JWT, else None (and why in ``jwt_errors``)."""
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        if not auth or not auth.startswith("Bearer "):
            self.jwt_errors.append("no bearer")
            return None
        tok = auth[len("Bearer ") :]
        parts = tok.split(".")
        if len(parts) != 3:
            self.jwt_errors.append("not a jwt")
            return None
        try:
            head = json.loads(_b64d(parts[0]))
            payload = json.loads(_b64d(parts[1]))
            self.public.verify(
                _b64d(parts[2]),
                f"{parts[0]}.{parts[1]}".encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except (ValueError, InvalidSignature):
            self.jwt_errors.append("bad signature")
            return None
        now = self.now()
        if head.get("alg") != "RS256":
            self.jwt_errors.append("alg")
            return None
        if str(payload.get("iss")) not in (self.client_id, str(self.app_id)):
            self.jwt_errors.append("iss")
            return None
        if not payload.get("exp") or payload["exp"] < now or payload["exp"] > now + 600:
            self.jwt_errors.append("exp")
            return None
        if not payload.get("iat") or payload["iat"] > now + 60:
            self.jwt_errors.append("iat")
            return None
        return payload

    def _token(self, auth: str | None) -> dict | None:
        if not auth or not auth.startswith("Bearer "):
            return None
        t = self.tokens.get(auth[len("Bearer ") :])
        if not t or t["expires_at"] < self.now() or self.revoked:
            return None
        return t

    # -- the endpoints -------------------------------------------------------------------------
    def handle(self, method: str, path: str, headers, body: dict) -> tuple[int, dict | list]:
        auth = headers.get("Authorization")
        kind = "jwt" if auth and auth.count(".") == 2 else "token" if auth else None
        self.requests.append((method, path, kind, body))
        p = path.split("?")[0]
        m = re.fullmatch(r"/app-manifests/([^/]+)/conversions", p)
        if method == "POST" and m:
            if self.gone:
                return 404, {"message": "Not Found"}
            with self.lock:
                if m.group(1) not in self.codes:
                    return 404, {"message": "Not Found"}
                self.codes.discard(m.group(1))
            return 201, {
                **self.app_record(),
                "pem": self.pem,
                "client_secret": "fake-" + secrets.token_hex(8),
                "webhook_secret": None,
            }
        if p.startswith("/app") and not p.startswith("/app-manifests"):
            if self.verify_jwt(auth) is None:
                return 401, {"message": "A JSON web token could not be decoded"}
            if self.gone:
                return 404, {"message": "Not Found"}
            if method == "GET" and p == "/app":
                return 200, self.app_record()
            if method == "GET" and p == "/app/installations":
                if not self.installation:
                    return 200, []
                i = self.installation
                return 200, [
                    {
                        "id": i["id"],
                        "account": i["account"],
                        "repository_selection": i["repository_selection"],
                        "permissions": self.app_record()["permissions"],
                    }
                ]
            m = re.fullmatch(r"/app/installations/(\d+)/access_tokens", p)
            if method == "POST" and m:
                if not self.installation or int(m.group(1)) != self.installation["id"]:
                    return 404, {"message": "Not Found"}
                self.token_requests.append(body)
                want = body.get("repositories")
                if want is not None:
                    names = {r.split("/")[1] for r in self.installation["repos"]}
                    if self.installation["repository_selection"] != "all" and any(
                        w not in names for w in want
                    ):
                        return 422, {
                            "message": "There is at least one repository that does "
                            "not exist or is not accessible to the parent "
                            "installation."
                        }
                perms = body.get("permissions") or self.app_record()["permissions"]
                granted = self.app_record()["permissions"]
                for k, v in perms.items():
                    if k not in granted or (v == "write" and granted[k] != "write"):
                        return 422, {"message": f"The permission {k}:{v} is not granted"}
                tok = "ghs_" + secrets.token_hex(20)
                exp = self.now() + 3600
                self.tokens[tok] = {
                    "expires_at": exp,
                    "permissions": perms,
                    "repos": list(want) if want is not None else None,
                }
                return 201, {
                    "token": tok,
                    "permissions": perms,
                    "expires_at": datetime.fromtimestamp(exp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "repository_selection": "selected" if want else "all",
                }
            m = re.fullmatch(r"/app/installations/(\d+)", p)
            if method == "DELETE" and m:
                if not self.installation or int(m.group(1)) != self.installation["id"]:
                    return 404, {"message": "Not Found"}
                self.uninstall()
                return 204, None
            return 404, {"message": "Not Found"}
        # everything else needs an installation token
        t = self._token(auth)
        if t is None:
            return 401, {"message": "Bad credentials"}
        if method == "GET" and p == "/installation/repositories":
            i = self.installation or {"repos": []}
            return 200, {
                "total_count": len(i["repos"]),
                "repositories": [{"full_name": r, "name": r.split("/")[1]} for r in i["repos"]],
            }
        m = re.fullmatch(r"/users/([^/]+)", p)
        if method == "GET" and m:
            name = m.group(1).replace("%5B", "[").replace("%5D", "]")
            if name == f"{self.slug}[bot]":
                return 200, {"id": self.bot_user_id, "login": name, "type": "Bot"}
            return 404, {"message": "Not Found"}
        m = re.fullmatch(r"/repos/([^/]+)/([^/]+)", p)
        if method == "GET" and m:
            full = f"{m.group(1)}/{m.group(2)}"
            if t["repos"] is not None and m.group(2) not in t["repos"]:
                return 404, {"message": "Not Found"}
            return 200, {
                "full_name": full,
                "permissions": {"push": True, "pull": True, "admin": False},
            }
        return 404, {"message": "Not Found"}

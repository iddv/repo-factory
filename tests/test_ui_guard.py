"""``repo-factory ui``'s guard: a Host that is not a loopback name is refused on every
method (a DNS-rebound page cannot read specs, builds or transcripts, nor sign in), and a write
needs an Origin, else a Referer, equal to the Host, port included. Loopback only, port 0."""

import http.client
import json
import threading

import pytest

from repo_factory import tokens as tokens_mod
from repo_factory.ui import server


@pytest.fixture
def ui(tmp_path):
    root = tmp_path / "tasks"
    root.mkdir()
    store = tokens_mod.Store(tmp_path / "tokens")
    srv = server.make_server(
        root, 0, harness="fake", tokens=store, require=True, loop_home=tmp_path / "loop"
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, srv.server_address[1], store
    srv.shutdown()
    srv.server_close()


def ask(port, method, path, headers, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest(method, path, skip_host=True)
    for k, v in headers.items():
        c.putheader(k, v)
    data = json.dumps(body).encode() if body is not None else b""
    if method == "POST":
        c.putheader("Content-Type", "application/json")
        c.putheader("Content-Length", str(len(data)))
    c.endheaders(data if method == "POST" else None)
    r = c.getresponse()
    out = r.status, r.read()
    c.close()
    return out


def test_a_foreign_host_is_refused_on_every_method(ui):
    srv, port, store = ui
    for host in (f"attacker.example:{port}", "attacker.example", f"192.168.1.20:{port}", ""):
        assert ask(port, "GET", "/api/header", {"Host": host})[0] == 400
        assert ask(port, "HEAD", "/", {"Host": host})[0] == 400
        assert ask(port, "GET", f"/auth/open?code={store.mint_code()}", {"Host": host})[0] == 400
        assert (
            ask(port, "POST", "/api/act", {"Host": host, "Origin": f"http://{host}"}, {})[0] == 400
        )
    for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"LOCALHOST:{port}"):
        assert ask(port, "GET", "/api/whoami", {"Host": host})[0] == 200


def test_a_write_needs_its_own_origin_port_included(ui):
    srv, port, store = ui
    host = f"127.0.0.1:{port}"
    bearer = {"Authorization": "Bearer " + store.value("cli")}
    for origin in (
        {},
        {"Origin": "http://localhost:3000"},
        {"Origin": "http://127.0.0.1"},
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
        {"Referer": "http://127.0.0.1:1/x"},
    ):
        code, body = ask(port, "POST", "/api/act", {"Host": host, **bearer, **origin}, {})
        assert code == 403 and b"cross-origin" in body, origin
    for same in ({"Origin": f"http://{host}"}, {"Referer": f"http://{host}/#/today"}):
        code, body = ask(port, "POST", "/api/act", {"Host": host, **bearer, **same}, {})
        assert b"cross-origin" not in body and b"unknown host" not in body, (same, code, body)
        assert b"error" in body  # the API's own answer to an empty act


def test_a_refused_write_is_read_before_the_answer(ui):
    srv, port, _ = ui
    big = {"pad": "x" * 200_000}
    for _ in range(20):
        assert ask(port, "POST", "/api/act", {"Host": f"127.0.0.1:{port}"}, big)[0] == 403
        assert ask(port, "POST", "/api/act", {"Host": "evil.example"}, big)[0] == 400

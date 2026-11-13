"""Characterisation of ``api.handle``'s routing: every route, GET and POST,
driven on the fake app, with what it answers: the status, the content type and the shape (a
JSON object's top-level keys, or its error text). The snapshot in
``fixtures/api_routes.json`` pins today's matching order, so moving the ``if`` chains into a
route table (and the handlers into modules) can't send a request somewhere else.

``REPO_FACTORY_SNAPSHOT=write pytest tests/test_api_routes.py`` rewrites the snapshot: only for a
change that means to move a route, and the diff says which.
"""

import json
import os
import re
from pathlib import Path

import pytest
import yaml

from repo_factory.ui import api

from .loop_common import make_loop

SNAPSHOT = Path(__file__).parent / "fixtures" / "api_routes.json"
IDEA = "i0123abcd"

GET = [
    "/api/details",
    "/api/details?sel=bnope",
    "/api/github/app",
    "/api/github/app/callback",
    "/api/github/app/callback?code=x&state=y",
    "/api/github/app/setup",
    "/api/github/app/setup?installation_id=1&setup_action=install",
    "/",
    "/index.html",
    "/classic",
    "/classic/",
    "/loop",
    "/loop/",
    "/static/theme.js",
    "/static/repo-factory.css",
    "/static/brand/favicon-16.png",
    "/static/nope.js",
    "/static/../api.py",
    "/static/",
    "/history",
    "/help",
    "/loop/feed.xml",
    "/api/loop",
    "/api/loop/settings",
    "/api/models",
    "/api/spend/breakdown",
    "/api/prompts",
    "/api/prompts/",
    "/api/prompts/house_rules",
    "/api/prompts/house_rules/restore",
    "/api/prompts/house_rules/versions/1",
    "/api/prompts/house_rules/versions/1/note",
    "/api/prompts/repo_factory/nosuch",
    "/api/prompts/repo_factory/nosuch/reset",
    "/api/prompts/repo_factory/nosuch/versions/1",
    "/api/prompts/license",
    "/api/prompts/preview",
    "/api/prompts/other",
    "/api/spend/caps",
    "/api/maintain/summary",
    "/api/maintain/lessons",
    "/api/maintain/repos",
    "/api/maintain/repo?repo=someone/thing",
    "/api/maintain/repo",
    "/api/maintain/repo/issues?repo=someone/thing",
    f"/api/maintain/{IDEA}",
    "/api/maintain/not-an-idea",
    "/api/maintain/",
    "/api/state",
    "/api/projects",
    "/api/project?id=nope",
    "/api/project",
    "/api/projects/settings",
    "/api/changes",
    "/api/projects/preview?key=autonomy.preset&value=%22quiet%22",
    "/api/projects/preview",
    "/api/project/charter?id=nope",
    "/api/actions",
    "/api/actions?kind=idea&id=nope",
    "/api/outcomes",
    "/api/header",
    "/api/build?dir=nope",
    "/api/build",
    "/api/build/runs?dir=nope",
    "/api/build/runs",
    "/api/run?dir=nope&n=1",
    "/api/run",
    "/api/info",
    "/api/builds",
    "/api/events?dir=nope",
    "/api/events",
    "/api/text?dir=nope&what=report",
    "/api/text?start=x",
    "/api/nothing",
    "/nothing",
    "",
    # gone with the cut: each is a 404 now
    "/api/repo-factory",
    "/api/term",
    "/api/chat",
    "/api/capabilities",
    "/api/plugins",
    "/api/traces",
    "/api/trace",
]

POST = [
    ("/api/github/app", {}),
    ("/api/prompts", {}),
    ("/api/prompts/house_rules/restore", {}),
    ("/api/prompts/house_rules/versions/1", {}),
    ("/api/prompts/house_rules/versions/1/note", {}),
    ("/api/prompts/license", {}),
    ("/api/prompts/license", {"license": "nope"}),
    ("/api/prompts/preview", {}),
    ("/api/prompts/repo_factory/nosuch", {}),
    ("/api/prompts/repo_factory/nosuch/versions/1", {}),
    ("/api/prompts/other", {}),
    ("/api/models", {}),
    ("/api/models", {"role": "nope"}),
    ("/api/spend", {"daily_usd": "x"}),
    ("/api/maintain/settings", {}),
    ("/api/maintain/settings", {"nope": 1}),
    ("/api/projects/settings", {}),
    ("/api/maintain/act", {}),
    ("/api/maintain/repo/nope", {"repo": "someone/thing"}),
    ("/api/act", {}),
    ("/api/act", {"kind": "nope"}),
    ("/api/loop/add", {}),
    ("/api/loop/nope", {}),
    ("/api/loop/send", {"id": "--help"}),
    ("/api/new", {}),
    ("/api/start", {}),
    ("/api/start", {"dir": "nope"}),
    ("/api/answer", {"dir": "nope"}),
    ("/api/stop", {"dir": "nope"}),
    ("/api/verdict", {"dir": "nope"}),
    ("/api/nothing", {}),
    ("/api/nothing", {"dir": "nope"}),
    ("/nothing", {}),
    ("/api/chat/send", {}),
    ("/api/capabilities", {}),
]

# a token's caller: the owner's doors write anywhere; an agent only runs actions; a door
# that left acts nowhere
AS = [
    ("/api/spend", {"daily_usd": "x"}, {"origin": "agent:helper"}),
    ("/api/act", {"kind": "nope"}, {"origin": "agent:helper"}),
    ("/api/act", {"kind": "nope"}, {"origin": "mcp"}),
    ("/api/loop/add", {}, {"origin": "cli"}),
    ("/api/models", {}, {"origin": "chat"}),
    ("/api/nothing", {}, {"origin": "ui"}),
]

OTHER = [("PUT", "/"), ("DELETE", "/api/act"), ("HEAD", "/api/info"), ("HEAD", "/nothing")]


@pytest.fixture
def app(tmp_path):
    lp = make_loop(tmp_path)
    (lp.cfg.home / "loop.yaml").write_text(yaml.safe_dump(lp.cfg.data))
    lp.cfg.tasks_dir.mkdir(parents=True, exist_ok=True)
    return api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)


def shape(code: int, ctype: str, data: bytes, tmp: str) -> list:
    kind = ctype.split(";")[0]
    out: list = [code, kind]
    if kind == "application/json":
        body = json.loads(data)
        if isinstance(body, dict) and set(body) == {"error"}:
            out.append(
                "error: "
                + re.sub(r"\S*" + re.escape(tmp) + r"\S*", "<tmp>", str(body["error"]))[:160]
            )
        elif isinstance(body, dict):
            out.append(sorted(body))
        else:
            out.append(type(body).__name__)
    return out


def snapshot(app, tmp: str) -> dict:
    seen = {}
    for path in GET:
        p, _, q = path.partition("?")
        seen[f"GET {path}"] = shape(*api.handle("GET", p, q or None, None, app=app), tmp)
    for path, body in POST:
        key = f"POST {path} {json.dumps(body, sort_keys=True)}"
        seen[key] = shape(*api.handle("POST", path, None, json.dumps(body).encode(), app=app), tmp)
    for path, body, who in AS:
        key = f"POST {path} {json.dumps(body, sort_keys=True)} as {who['origin']}"
        seen[key] = shape(
            *api.handle("POST", path, None, json.dumps(body).encode(), app=app, identity=who), tmp
        )
    for method, path in OTHER:
        seen[f"{method} {path}"] = shape(*api.handle(method, path, None, None, app=app), tmp)
    seen["POST /api/act (not JSON)"] = shape(
        *api.handle("POST", "/api/act", None, b"{", app=app), tmp
    )
    seen["POST /api/act (a list)"] = shape(
        *api.handle("POST", "/api/act", None, b"[]", app=app), tmp
    )
    return seen


def test_every_route_answers_as_it_did(app, tmp_path):
    got = snapshot(app, str(tmp_path))
    if os.environ.get("REPO_FACTORY_SNAPSHOT") == "write":
        SNAPSHOT.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n")
    want = json.loads(SNAPSHOT.read_text())
    assert list(got) == list(want), "the requests changed: rewrite the snapshot"
    moved = {k: (want[k], got[k]) for k in want if want[k] != got[k]}
    assert not moved, json.dumps(moved, indent=1, ensure_ascii=False)


def test_a_500_says_little_and_logs_the_rest(app, monkeypatch, caplog):
    """An unexpected error answers repo-factory's error JSON (RCA B3): its id, its type, the version
    and the report's address; the message (a path, a command line) goes to the server's log,
    once for the id however often it happens (CR-REVIEW C-1)."""
    import logging

    from repo_factory import __version__, rowsafe

    monkeypatch.setattr(rowsafe, "_seen", {})

    def boom():
        raise RuntimeError("git failed in /home/someone/private/repo")

    monkeypatch.setattr(app, "header", boom)
    with caplog.at_level(logging.ERROR):
        answers = [api.handle("GET", "/api/header", app=app) for _ in range(3)]
    for code, ctype, data in answers:
        assert code == 500 and ctype.startswith("application/json")
        body = json.loads(data)
        assert body["exception"] == "RuntimeError" and body["version"] == __version__
        assert (
            f"(error {body['error_id']})" in body["error"] and "/home/someone" not in data.decode()
        )
        assert body["report"] == f"/api/errors/{body['error_id']}"
    assert len({json.loads(d)["error_id"] for _, _, d in answers}) == 1
    logged = [
        r
        for r in caplog.records
        if "/home/someone/private" in r.getMessage()
        or (r.exc_info and "/home/someone/private" in str(r.exc_info[1]))
    ]
    assert len(logged) == 1 and "GET /api/header" in logged[0].getMessage()

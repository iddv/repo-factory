"""The rows Settings › Building and Publishing said were "set by hand in your settings":
written through the one writer (POST api/loop/settings) with a check each, and the sensitive
ones only with the page's confirm. Paths must exist, the package indexes are host names, the pipeline a bundled one, the overrides within the defaults."""

import json

import pytest
import yaml

from repo_factory.settings import write as W
from repo_factory.ui import api

from .loop_common import make_loop

UI = {"origin": "ui", "name": "ui", "actions": None}
AGENT = {"origin": "agent:RC", "name": "RC", "actions": None}


@pytest.fixture
def app(tmp_path):
    lp = make_loop(tmp_path)
    a = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    a.loop, a.loop_cfg = (lambda: lp), (lambda: lp.cfg)
    return a, lp


def post(a, body, who=UI):
    code, _, data = api.handle(
        "POST", "/api/loop/settings", None, json.dumps(body).encode(), app=a, identity=who
    )
    return code, json.loads(data)


def saved(lp, *keys):
    d = yaml.safe_load(lp.cfg.layout.settings.read_text()) or {}
    for k in keys:
        d = (d or {}).get(k)
    return d


def test_each_hand_key_is_written_with_a_check(app, tmp_path):
    a, lp = app
    folder = tmp_path / "tasks2"
    folder.mkdir()
    code, out = post(a, {"key": "tasks_dir", "value": str(folder), "confirm": True})
    assert code == 200 and saved(lp, "tasks_dir") == str(folder)
    assert out["rules"]["tasks_dir"]["kind"] == "path" and "confirm" in out["rules"]["tasks_dir"]
    for bad, why in (
        (str(tmp_path / "nope"), "no folder"),
        ("relative/dir", "whole path"),
        ("", "is a folder"),
    ):
        code, out = post(a, {"key": "tasks_dir", "value": bad, "confirm": True})
        assert code == 400 and why in out["error"], bad
    # the package indexes: host names, one per word, lower case, no scheme
    code, _ = post(
        a,
        {
            "key": "exam.indexes",
            "value": "PyPI.org  files.pythonhosted.org,pypi.org",
            "confirm": True,
        },
    )
    assert code == 200 and saved(lp, "exam", "indexes") == ["pypi.org", "files.pythonhosted.org"]
    for bad in ("https://pypi.org", "pypi.org:443", "pypi.org/simple", "localhost"):
        code, out = post(a, {"key": "exam.indexes", "value": bad, "confirm": True})
        assert code == 400 and "host names" in out["error"], bad
    assert post(a, {"key": "exam.indexes", "value": "", "confirm": True})[0] == 200
    assert saved(lp, "exam", "indexes") == []
    # the pipeline: a bundled one, by its name; the page reads it back by name
    code, out = post(a, {"key": "build.policy", "value": "v5", "confirm": True})
    assert code == 200 and saved(lp, "build", "policy").endswith("/v5")
    assert "/" not in out["rules"]["build.policy"]["value"]  # read back by its name
    assert "v5" in out["rules"]["build.policy"]["choices"]
    code, out = post(a, {"key": "build.policy", "value": "../../etc", "confirm": True})
    assert code == 400 and "is one of" in out["error"]
    # Publish anyway's overrides: within the defaults, never the holds that are never overridden
    code, _ = post(
        a, {"key": "publish.anyway.may_override", "value": "tests safe_defaults", "confirm": True}
    )
    assert code == 200 and saved(lp, "publish", "anyway", "may_override") == [
        "tests",
        "safe_defaults",
    ]
    code, out = post(
        a, {"key": "publish.anyway.may_override", "value": "security", "confirm": True}
    )
    assert code == 400


def test_a_sensitive_key_needs_the_pages_confirm_and_the_page_or_command_line(app, tmp_path):
    a, lp = app
    f = lp.cfg.layout.settings
    before = f.read_text() if f.exists() else None
    for key, value in (
        ("tasks_dir", str(tmp_path)),
        ("build.policy", "v5"),
        ("exam.indexes", "pypi.org"),
        ("publish.anyway.may_override", "tests"),
        ("build.allow_api_key", True),
    ):
        code, out = post(a, {"key": key, "value": value})
        assert code == 409 and "confirm" in out["error"] and W.SENSITIVE[key][:20] in out["error"]
        code, out = post(a, {"key": key, "value": value, "confirm": True}, who=AGENT)
        assert code == 403
    assert (f.read_text() if f.exists() else None) == before
    # an ordinary key needs no confirm, as before
    assert post(a, {"key": "concurrency", "value": 2})[0] == 200

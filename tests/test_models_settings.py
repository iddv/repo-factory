"""Settings › Watching › Models and Settings › Spend (2026-10-08): the one model catalog, every role
and where its setting lives, the loop.yaml writer (comments kept), the build stages' models
laid over the v5 policy through $REPO_FACTORY_V5_MODELS, and the build caps. Offline: no model,
no network, no build is started."""

import json
import re
from pathlib import Path

import pytest
import yaml

from repo_factory import models
from repo_factory.loop import builds
from repo_factory.resources import POLICIES
from repo_factory.settings import write as settings
from repo_factory.settings.store import DEFAULTS, Config
from repo_factory.ui import api
from repo_factory.v5 import pipeline

UI = Path(api.__file__).parent

LIVE = """\
# The loop's settings (any subset of repo_factory/settings/store.py DEFAULTS).
# Written by hand on the owner's decisions.
concurrency: 1
# one build at a time, published only on the owner's press
publish:
  license: MIT
"""


def call(app, method, path, body=None):
    code, ctype, data = api.handle(
        method, path, None, json.dumps(body).encode() if body is not None else None, app=app
    )
    return code, (json.loads(data) if ctype.startswith("application/json") else data)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "loop"
    h.mkdir()
    (h / "loop.yaml").write_text(LIVE)
    monkeypatch.delenv(models.V5_MODELS_ENV, raising=False)
    return h


@pytest.fixture
def app(home, tmp_path):
    (tmp_path / "tasks").mkdir()
    return api.App(tmp_path / "tasks", "fake", home)


# -- the catalog and the roles ----------------------------------------------------------------
def test_the_catalog_is_one_list_with_both_names():
    cat = models.catalog()
    ids = [m["id"] for m in cat]
    assert len(ids) == len(set(ids)) and {
        "claude-haiku-5-5",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
        "claude-fable-5-1",
    } <= set(ids)
    for m in cat:
        assert m["router"].startswith("anthropic/") and m["note"]
        # no prices: a price is a point-in-time fact about one provider (2026-10-09)
        assert not {"in_usd", "out_usd"} & m.keys() and "$" not in m["note"]
        assert models.MODEL_NAME.fullmatch(m["id"]) and models.MODEL_NAME.fullmatch(m["router"])
    assert (
        next(m for m in cat if m["id"] == "claude-haiku-5-5")["router"]
        == "anthropic/claude-haiku-5.5"
    )


def test_no_default_points_at_an_old_model():
    assert DEFAULTS["pitch"]["summary_model"] == "claude-haiku-5-5"
    assert DEFAULTS["publish"]["honesty_model"] == "claude-haiku-5-5"
    text = json.dumps(DEFAULTS) + (POLICIES / "v5" / "rules.yaml").read_text()
    assert not re.search(r"claude-(haiku|sonnet|opus)-4", text)


def test_every_role_says_where_it_is_set(home):
    roles = {r["role"]: r for r in models.roles(Config(home))}
    assert set(roles) == {
        "pitch",
        "summary",
        "honesty",
        "build",
        "claims",
        "verify",
        "oracle",
        "repair",
        "readiness",
        "triage",
        "charter",
        "evolve",
    }
    assert roles["charter"]["setting"] == "projects.charter.model" and not roles["charter"]["set"]
    assert roles["summary"]["model"] == "claude-haiku-5-5" and not roles["summary"]["set"]
    assert (
        roles["triage"]["setting"] == "maintain.triage_model" and roles["triage"]["kind"] == "loop"
    )
    assert roles["pitch"]["model"] is None and roles["pitch"]["setting"] == "pitch.model"
    assert roles["verify"]["setting"] == "build.models.verify"
    assert set(pipeline.MODEL_SECTIONS) == {k for k, *_, kind in models.ROLES if kind == "build"}


def test_the_models_other_pages_set_are_chosen_here_too(app, home):
    """The charter's and the checks' models are roles: set through the same writer, into the
    keys their own pages read."""
    for role, path in (
        ("charter", ("projects", "charter", "model")),
        ("evolve", ("projects", "evolve", "model")),
    ):
        code, m = call(app, "POST", "/api/models", body={"role": role, "model": "claude-haiku-5-5"})
        assert code == 200, (role, m)
        got = Config(home).data
        for k in path:
            got = got[k]
        assert got == "claude-haiku-5-5", role


# -- the API round trip -------------------------------------------------------------------------
def test_models_round_trip_keeps_the_owners_comments(app, home):
    code, m = call(app, "GET", "/api/models")
    assert code == 200 and m["file"].endswith("loop.yaml") and m["policy"] == "v5"
    assert len(m["catalog"]) == len(models.CATALOG)
    code, m = call(app, "POST", "/api/models", {"role": "verify", "model": "claude-opus-5-5"})
    assert code == 200
    verify = next(r for r in m["roles"] if r["role"] == "verify")
    assert verify["model"] == "claude-opus-5-5" and verify["set"]
    code, m = call(app, "POST", "/api/models", {"role": "summary", "model": "claude-sonnet-5-5"})
    assert code == 200
    text = (home / "loop.yaml").read_text()
    assert text.startswith(LIVE.split("concurrency")[0])  # the header comment
    assert "# one build at a time, published only on the owner's press" in text
    assert "license: MIT" in text
    cfg = Config(home)
    assert cfg["build"]["models"] == {"verify": "claude-opus-5-5"}
    assert cfg["pitch"]["summary_model"] == "claude-sonnet-5-5"
    assert cfg["pitch"]["min_buildability"] == DEFAULTS["pitch"]["min_buildability"]
    # back to the default: the key goes, and an emptied mapping stays a mapping
    for role in ("verify", "summary"):
        assert call(app, "POST", "/api/models", {"role": role, "model": None})[0] == 200
    data = yaml.safe_load((home / "loop.yaml").read_text())
    assert data["build"] == {"models": {}} and data["pitch"] == {}
    cfg = Config(home)
    assert cfg["build"]["models"] == {} and cfg["pitch"]["summary_model"] == "claude-haiku-5-5"
    assert cfg["build"]["policy"] == DEFAULTS["build"]["policy"]


@pytest.mark.parametrize(
    "body, why",
    [
        ({"role": "judge", "model": "claude-haiku-5-5"}, "no such role"),
        ({"role": "verify", "model": "rm -rf /"}, "a model is a name"),
        ({"role": "verify", "model": "x\ny: 1"}, "a model is a name"),
        ({"role": "verify", "model": 5}, "a model is a name"),
    ],
)
def test_models_refuses_what_it_cannot_write(app, home, body, why):
    code, out = call(app, "POST", "/api/models", body)
    assert code == 400 and why in out["error"]
    assert (home / "loop.yaml").read_text() == LIVE


def test_build_caps_round_trip(app, home):
    code, out = call(app, "POST", "/api/spend", {"daily_usd": 25, "build_max_usd": 12.5})
    assert code == 200 and {k: out[k] for k in ("daily_usd", "build_max_usd")} == {
        "daily_usd": 25.0,
        "build_max_usd": 12.5,
    }
    assert out["caps"]["daily_usd"] == 25.0  # every cap, for Settings › Spend
    cfg = Config(home)
    assert cfg["daily_usd"] == 25.0 and cfg["build"]["max_usd"] == 12.5
    assert builds.command(cfg, Path("/t"))[-2:] == ["--max-cost", "12.5"]
    code, out = call(app, "POST", "/api/spend", {"daily_usd": None, "build_max_usd": ""})
    assert {k: out[k] for k in ("daily_usd", "build_max_usd")} == {
        "daily_usd": None,
        "build_max_usd": None,
    }
    assert "--max-cost" not in builds.command(Config(home), Path("/t"))
    assert "daily_usd: null" in (home / "loop.yaml").read_text()
    for bad in (
        {"daily_usd": 0},
        {"daily_usd": -3},
        {"daily_usd": "5"},
        {"daily_usd": True},
        {"other": 1},
        {},
    ):
        assert call(app, "POST", "/api/spend", bad)[0] == 400
    assert Config(home)["daily_usd"] is None


# -- the stage models reach the build -------------------------------------------------------------
def test_the_loops_builds_carry_the_stage_models(home, monkeypatch, tmp_path):
    settings.write(home / "loop.yaml", ("build", "models", "verify"), "claude-opus-5-5")
    settings.write(home / "loop.yaml", ("build", "models", "readiness"), "claude-sonnet-5-5")
    seen = {}

    class Proc:
        pid = 4242

        def wait(self):
            return 0

    def popen(cmd, **kw):
        seen.update(kw)
        return Proc()

    monkeypatch.setattr(builds.proc.subprocess, "Popen", popen)
    builds.start(Config(home), tmp_path / "task")
    builds._children.pop(4242, None)
    overlay = json.loads(seen["env"][models.V5_MODELS_ENV])
    assert overlay == {"verify": "claude-opus-5-5", "readiness": "claude-sonnet-5-5"}
    # no choice: the build inherits the environment as before
    settings.write(home / "loop.yaml", ("build", "models", "verify"))
    settings.write(home / "loop.yaml", ("build", "models", "readiness"))
    builds.start(Config(home), tmp_path / "task")
    builds._children.pop(4242, None)
    assert seen["env"] is None


def test_a_build_started_from_the_page_carries_them_too(app, home, monkeypatch, tmp_path):
    settings.write(home / "loop.yaml", ("build", "models", "build"), "claude-opus-5-5")
    env = app.build_env()
    assert json.loads(env[models.V5_MODELS_ENV]) == {"build": "claude-opus-5-5"}


def test_the_pipeline_lays_the_models_over_the_policy(monkeypatch):
    plain = pipeline.load_rules(POLICIES / "v5")
    assert plain["build"]["model"] is None
    # a loop.yaml from before still sets the retired mutants role: dropped, not refused
    monkeypatch.setenv(
        models.V5_MODELS_ENV,
        json.dumps(
            {
                "build": "claude-opus-5-5",
                "verify": "claude-sonnet-5-5",
                "oracle": None,
                "mutants": "claude-sonnet-5",
                "readiness": "claude-haiku-5-5",
            }
        ),
    )
    rules = pipeline.load_rules(POLICIES / "v5")
    assert rules["build"]["model"] == "claude-opus-5-5"
    assert rules["build"]["max_usd"] == plain["build"]["max_usd"]  # caps untouched
    assert rules["verify"]["model"] == "claude-sonnet-5-5"
    assert rules["oracle"]["model"] is None  # the CLI default
    assert "mutants" not in rules
    assert {rules["readiness"][p]["model"] for p in ("security", "checks", "repair")} == {
        "claude-haiku-5-5"
    }
    assert rules["claims"]["model"] is None and rules["oracle"]["model"] is None
    for bad in ('{"judge": "m"}', '["m"]', '{"build": 5}'):
        monkeypatch.setenv(models.V5_MODELS_ENV, bad)
        with pytest.raises(ValueError, match=models.V5_MODELS_ENV):
            pipeline.load_rules(POLICIES / "v5")


# -- the writer --------------------------------------------------------------------------------
def test_the_writer_edits_text_and_falls_back_on_flow_style(tmp_path):
    f = tmp_path / "loop.yaml"
    f.write_text("# head\nbuild:\n  retries: 2   # keep\n\n# note\nchat: {model: x}\n")
    settings.write(f, ("build", "retries"), 3)
    assert f.read_text() == "# head\nbuild:\n  retries: 3   # keep\n\n# note\nchat: {model: x}\n"
    settings.write(f, ("build", "models", "oracle"), "claude-haiku-5-5")
    assert yaml.safe_load(f.read_text())["build"] == {
        "retries": 3,
        "models": {"oracle": "claude-haiku-5-5"},
    }
    assert "# note" in f.read_text()
    settings.write(f, ("chat", "model"), "claude-sonnet-5-5")  # flow style: parsed, rewritten
    data = yaml.safe_load(f.read_text())
    assert data["chat"] == {"model": "claude-sonnet-5-5"} and f.read_text().startswith("# head\n")
    settings.write(f, ("daily_usd",), None)
    assert yaml.safe_load(f.read_text())["daily_usd"] is None
    g = tmp_path / "new.yaml"
    settings.write(g, ("publish", "honesty_model"), "claude-haiku-5-5")
    assert yaml.safe_load(g.read_text()) == {"publish": {"honesty_model": "claude-haiku-5-5"}}


# -- the page -------------------------------------------------------------------------------------

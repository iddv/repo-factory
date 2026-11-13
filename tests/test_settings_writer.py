"""The one settings writer:``POST api/loop/settings`` ``{key, value}`` checked by
``settings.write.RULES``, read back by ``GET api/loop/rules``; the rest of loop.yaml is set in
the file on purpose (commands, paths, credentials, the network allow-lists)."""

import pytest
import yaml

from repo_factory.settings import store
from repo_factory.settings import write as W

from .app_common import call, make_app


def at(d, key):
    for k in key.split("."):
        d = d[k]
    return d


def test_every_rule_is_a_key_factory_reads_and_its_default_passes_its_check():
    for key, rule in W.RULES.items():
        if rule[0] == "path":  # a folder must exist on this machine: its own test
            continue
        assert (
            W.checked(rule, key, at(store.DEFAULTS, key)) == at(store.DEFAULTS, key)
            or rule[0] == "float"
        ), key


# build.policy, tasks_dir and exam.indexes are written from the page, with a check each and the
# page's confirm (tests/test_settings_hand_keys.py). Commands and credentials stay set by hand.
@pytest.mark.parametrize(
    "key",
    [
        "build.command",
        "build.revise_command",
        "publish.gh_command",
        "maintain.gh_command",
        "maintain.token_env_file",
        "maintain.net_hosts",
    ],
)
def test_what_is_set_by_hand_on_purpose_is_never_written_from_a_request(key):
    with pytest.raises(ValueError, match="set it in loop.yaml"):
        W.check(key, "x")


def test_a_value_is_checked_and_clear_is_back_to_the_default(tmp_path, monkeypatch):
    lp, app, _ = make_app(tmp_path, monkeypatch)
    f = lp.cfg.home / "loop.yaml"
    f.write_text("# the owner's note\n" + f.read_text())
    for key, good, bad in (
        ("concurrency", 1, 9),
        ("pitch.min_insight", 0.5, 1.5),
        ("pitch.min_insight", None, "lots"),
        ("daily_usd", "12", 0),
        ("maintain.repos", "a/b c/d", "x"),
        ("projects.evolve.every_days", 14, 0),
    ):
        code, out = call(app, "POST", "/api/loop/settings", body={"key": key, "value": good})
        assert code == 200, (key, out)
        code, out = call(app, "POST", "/api/loop/settings", body={"key": key, "value": bad})
        assert code == 400 and key in out["error"], (key, out)
    assert f.read_text().startswith("# the owner's note")
    data = yaml.safe_load(f.read_text())
    assert data["daily_usd"] == 12.0 and data["maintain"]["repos"] == ["a/b", "c/d"]
    assert store.Config(lp.cfg.home)["daily_usd"] == 12.0  # the loop's next tick reads it
    code, out = call(app, "POST", "/api/loop/settings", body={"key": "concurrency", "clear": True})
    assert code == 200 and "concurrency" not in yaml.safe_load(
        (lp.cfg.home / "loop.yaml").read_text()
    )
    assert out["rules"]["concurrency"]["value"] == out["rules"]["concurrency"]["default"]
    code, out = call(
        app, "POST", "/api/loop/settings", body={"key": "build.command", "value": ["sh"]}
    )
    assert code == 400 and "set it in loop.yaml" in out["error"]


def test_every_cap_spend_shows_has_the_one_writer(tmp_path, monkeypatch):
    """Settings › Spend holds every cap: each goes through the one writer; null is no cap."""
    lp, app, _ = make_app(tmp_path, monkeypatch)
    for key in ("daily_usd", "build.max_usd", "maintain.max_usd", "projects.budget.monthly_usd"):
        code, out = call(app, "POST", "/api/loop/settings", body={"key": key, "value": 2.5})
        assert code == 200 and out["rules"][key]["value"] == 2.5, (key, out)
        code, out = call(app, "POST", "/api/loop/settings", body={"key": key, "value": None})
        assert code == 200 and out["rules"][key]["value"] is None, (key, out)
    code, out = call(app, "POST", "/api/loop/settings", body={"key": "daily_usd", "value": -1})
    assert code == 400 and "daily_usd" in out["error"]


def test_the_rules_say_how_each_key_is_edited(tmp_path, monkeypatch):
    _, app, _ = make_app(tmp_path, monkeypatch)
    code, out = call(app, "GET", "/api/loop/rules")
    assert code == 200 and out["file"].endswith("loop.yaml")
    r = out["rules"]
    assert r["concurrency"]["kind"] == "int" and r["concurrency"]["default"] == 1
    assert r["pitch.min_insight"]["kind"] == "float" and r["pitch.min_insight"]["empty"]
    assert r["daily_usd"]["kind"] == "usd" and r["daily_usd"]["default"] == 50.0

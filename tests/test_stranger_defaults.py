"""A stranger's defaults: publishing asks first, repositories are private, two spend caps, and
maintenance off. A home written before these defaults (or by someone who chose otherwise)
still loads with its own values."""

from __future__ import annotations

import json

from repo_factory.loop import gate
from repo_factory.settings.store import DEFAULTS, Config


def test_the_defaults_a_stranger_gets():
    assert DEFAULTS["publish"]["approval"] == "required"  # a ready build waits for a press
    assert DEFAULTS["daily_usd"] == 50.0
    assert DEFAULTS["build"]["max_usd"] == 25.0
    assert DEFAULTS["maintain"]["enabled"] is False
    assert DEFAULTS["projects"]["enabled"] is False


def test_a_home_without_the_new_keys_takes_the_defaults(tmp_path):
    (tmp_path / "loop.yaml").write_text("concurrency: 1\nbuild:\n  retries: 1\n")
    cfg = Config(tmp_path)
    assert cfg["daily_usd"] == 50.0 and cfg["build"]["max_usd"] == 25.0
    assert cfg.problems == []


def test_a_home_that_chose_no_caps_keeps_its_choice(tmp_path):
    (tmp_path / "loop.yaml").write_text("daily_usd: null\nbuild:\n  max_usd: null\n")
    cfg = Config(tmp_path)
    assert cfg["daily_usd"] is None and cfg["build"]["max_usd"] is None
    assert cfg.problems == []


def test_publishing_on_its_own_is_private_and_a_separate_setting_from_making_public():
    from repo_factory.loop.daemon import _publishes_alone

    assert DEFAULTS["publish"]["when"] == "ask"
    assert not _publishes_alone({"publish": {"when": "ask", "approval": "required"}})
    assert _publishes_alone({"publish": {"when": "ready", "approval": "required"}})
    # the page writes publish.when only; publish.approval: auto (public too) is hand-set
    from repo_factory.settings.write import RULES

    assert RULES["publish.when"] == ("enum", ("ask", "ready"))


def test_a_capped_stage_is_named_first(tmp_path):
    den = tmp_path / ".den"
    den.mkdir()
    events = [
        {"type": "v5_stage", "stage": "build", "status": "capped", "build": 1},
        {"type": "v5_stage", "stage": "verify", "status": "capped", "build": 1},
        {"type": "v5_stage", "stage": "claims", "status": "done", "build": 1},
    ]
    (den / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    assert gate.capped_stages(tmp_path, 1) == ["build", "verify"]
    assert gate.capped_stages(tmp_path, 2) == []

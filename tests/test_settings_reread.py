"""A running loop takes what Settings (or a hand edit of loop.yaml) wrote from its next tick on:
the page says "From the next build", and the loop must not keep the settings it started with."""

import yaml

from repo_factory.settings.store import Config

from .loop_common import make_loop


def test_the_running_loop_reads_loop_yaml_again_when_it_changed(tmp_path):
    lp = make_loop(tmp_path)
    f = lp.cfg.layout.settings
    f.write_text(yaml.safe_dump({"build": {"flow": "v5", "max_usd": 4}}))
    lp.cfg = lp.cfg.reread()
    over = lp.cfg.overrides
    assert lp.cfg["build"]["flow"] == "v5" and not lp.cfg.changed()
    f.write_text(yaml.safe_dump({"build": {"flow": "lean", "max_usd": 9}}))
    assert lp.cfg.changed()
    lp.tick()
    assert lp.cfg["build"]["flow"] == "lean" and lp.cfg["build"]["max_usd"] == 9
    assert lp.cfg.overrides == over  # the loop's own overrides are kept


def test_a_model_changed_on_settings_is_made_again_without_a_restart(tmp_path, monkeypatch):
    from repo_factory.loop import models

    made: list[tuple[str | None, str]] = []

    def fake_claude(model=None, purpose="pitch", timeout_s=600, max_usd=None):
        made.append((model, purpose))
        return lambda prompt: None

    monkeypatch.setattr(models, "claude", fake_claude)
    lp = make_loop(tmp_path)
    handed = lp.summary_model  # make_loop hands its fakes in: those are never replaced
    lp._model = lp._lessons = None
    f = lp.cfg.layout.settings
    f.write_text(yaml.safe_dump({"pitch": {"model": "first"}, "lessons": {"model": "first"}}))
    lp.tick()
    assert lp.model is lp.model and lp.lessons_model is not None
    assert ("first", "pitch") in made and ("first", "lessons") in made
    f.write_text(yaml.safe_dump({"pitch": {"model": "second"}, "lessons": {"model": "second"}}))
    lp.tick()
    lp.model, lp.lessons_model  # noqa: B018 - made again at their next use
    assert ("second", "pitch") in made and ("second", "lessons") in made
    assert lp.summary_model is handed


def test_an_unchanged_file_keeps_the_settings_object(tmp_path):
    lp = make_loop(tmp_path)
    before = lp.cfg
    lp.tick()
    assert lp.cfg is before


def test_a_missing_file_is_no_change(tmp_path):
    cfg = Config(tmp_path / "home")
    assert cfg.text is None and not cfg.changed()

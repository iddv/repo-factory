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


def test_an_unchanged_file_keeps_the_settings_object(tmp_path):
    lp = make_loop(tmp_path)
    before = lp.cfg
    lp.tick()
    assert lp.cfg is before


def test_a_missing_file_is_no_change(tmp_path):
    cfg = Config(tmp_path / "home")
    assert cfg.text is None and not cfg.changed()

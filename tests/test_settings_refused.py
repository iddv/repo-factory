"""The page's settings writer never makes repositories public without a press, and does not
switch on the parts that are in the code but not in this version: each answers 400 in words
and loop.yaml is unchanged."""

from __future__ import annotations

import json

import pytest

from repo_factory.settings import write
from repo_factory.ui import api

from .loop_common import make_loop

REFUSED = [
    ({"key": "publish.approval", "value": "auto"}, "publishing asks you first"),
    ({"key": "maintain.enabled", "value": True}, "maintenance is not in this version"),
    ({"key": "projects.enabled", "value": True}, "projects are not in this version"),
]


@pytest.mark.parametrize("body, said", REFUSED)
def test_the_writer_refuses(tmp_path, body, said):
    lp = make_loop(tmp_path / "lp")
    before = lp.cfg.layout.settings.read_text() if lp.cfg.layout.settings.exists() else None
    a = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    a.loop, a.loop_cfg = (lambda: lp), (lambda: lp.cfg)
    code, _, out = api.handle("POST", "/api/loop/settings", None, json.dumps(body).encode(), app=a)
    text = out.decode() if isinstance(out, bytes) else str(out)
    assert code == 400 and said in text, text
    after = lp.cfg.layout.settings.read_text() if lp.cfg.layout.settings.exists() else None
    assert after == before


def test_what_stays_writable():
    assert write.check("publish.approval", "required") == "required"
    assert write.check("maintain.enabled", False) is False
    with pytest.raises(ValueError, match="not in this version"):
        write.project_value("enabled", True)

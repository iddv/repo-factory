"""What used to be swallowed is now said in the log (CR-PLAN P3-4; review C-5), and the
sandbox probes are bounded: a hung probe means "unavailable", not a hung build."""

import logging
import subprocess

from repo_factory import events
from repo_factory.harness import isolation


def test_a_broken_subscriber_is_logged_and_the_event_still_written(tmp_path, caplog):
    def broken(ev):
        raise RuntimeError("renderer broke")

    log = events.EventLog(tmp_path / "events.jsonl", "t", 1, subscribers=[broken])
    with caplog.at_level(logging.WARNING, logger="repo_factory.events"):
        ev = log.emit("build_started")
    assert ev["type"] == "build_started"
    assert (tmp_path / "events.jsonl").read_text().count("build_started") == 1
    assert any("renderer broke" in r.getMessage() for r in caplog.records)


def test_a_hung_or_missing_probe_is_unavailable(monkeypatch):
    def hang(cmd, **kw):
        assert kw.get("timeout") == 30
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(isolation.subprocess, "run", hang)
    assert isolation._probe(["bwrap"]) is False
    monkeypatch.setattr(
        isolation.subprocess, "run", lambda cmd, **kw: (_ for _ in ()).throw(OSError("gone"))
    )
    assert isolation._probe(["unshare"]) is False

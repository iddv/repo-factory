"""A fix that stopped says so in a sentence (UC-DESIGN §2 F8, §4 "One attempt at a product"):
"Run 1 stopped in tests: …", and without a stage nothing reads "stopped in None"."""

from repo_factory.maintain import revise
from repo_factory.ui.api import App


def status(monkeypatch, res: dict) -> tuple[str, str]:
    monkeypatch.setattr(revise, "result", lambda d: res)
    return App.fix_status(None, "unused", {"state": "done"}, {}, {})  # type: ignore[arg-type]


def test_a_stopped_fix_names_its_run_and_stage(monkeypatch):
    assert status(monkeypatch, {"attempt": 1, "stage": "tests", "why": "a suite failed"}) == (
        "tried",
        "Run 1 stopped in tests: a suite failed",
    )


def test_no_stage_and_no_reason_leave_no_python_words(monkeypatch):
    assert status(monkeypatch, {"attempt": 2, "stage": None}) == ("tried", "Run 2 stopped")
    said = status(monkeypatch, {"stage": None, "why": "out of time"})[1]
    assert said == "Run stopped: out of time" and "None" not in said

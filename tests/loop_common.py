"""Shared fakes for the loop's tests: a scripted model, a loop config in a temp dir, and the
fake ``repo-factory build`` and ``gh`` scripts."""

import json
import sys
import time
from pathlib import Path

from repo_factory.loop.daemon import Loop
from repo_factory.settings.store import Config

FIX = Path(__file__).parent / "fixtures" / "loop"
FAKE_BUILD = FIX / "fake_build.py"
FAKE_GH = FIX / "fake_gh.py"


class FakeModel:
    """``model(prompt) -> (text, usd)``: ``reply`` is a string, a list (one per call; the
    last repeats) or a function of the prompt."""

    def __init__(self, reply, usd: float = 0.05):
        self.reply, self.usd, self.prompts = reply, usd, []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        r = self.reply
        if callable(r):
            r = r(prompt)
        elif isinstance(r, list):
            r = r[min(len(self.prompts) - 1, len(r) - 1)]
        return r, self.usd


def pitch_json(verdict="accept", product="Clinicbook", buildability=0.85, **kw):
    d = {
        "verdict": verdict,
        "product": product,
        "product_type": "web app",
        "one_liner": "Appointment booking and reminders for small clinics.",
        "who": "receptionists at small clinics",
        "why_now": "paper calendars still common",
        "buildability": buildability,
        "buildability_reasons": ["CRUD plus a scheduler", "no outside APIs needed"],
        "risks": ["SMS needs a provider"],
        "brief": (
            "small clinics still schedule patients on paper. build something a "
            "receptionist can install to book appointments and send reminders, so "
            "nobody double-books and patients show up."
        ),
        "reject_reason": "" if verdict == "accept" else "no product here",
    }
    d.update(kw)
    return json.dumps(d)


HONEST = json.dumps({"ok": True, "issues": []})
DESCRIBE = "You write the public description"  # the describe prompt's first words


class TextModel:
    """The pre-push checks' two model calls in one fake (the loop uses one model for both):
    the public text written from the product (``describe``: its reply, a dict, a string or a
    function of the prompt; None: an unusable reply, so the product's text is kept) and the
    one review (``review(prompt) -> [issues]``). ``calls``: "describe" or "review", in order."""

    def __init__(self, describe=None, review=None, usd: float = 0.01):
        self.describe, self.review, self.usd = describe, review or (lambda p: []), usd
        self.calls, self.prompts = [], []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if prompt.startswith(DESCRIBE):
            self.calls.append("describe")
            d = self.describe(prompt) if callable(self.describe) else self.describe
            return (d if isinstance(d, str) else json.dumps(d) if d else "no reply"), self.usd
        self.calls.append("review")
        issues = self.review(prompt)
        return json.dumps({"ok": not issues, "issues": issues}), self.usd

    def of(self, kind: str) -> list[str]:
        return [p for c, p in zip(self.calls, self.prompts) if c == kind]


def make_cfg(tmp: Path, **over) -> Config:
    base = {
        "tasks_dir": str(tmp / "tasks"),
        "tick_s": 0.2,
        # auto_repair 0: the tests written before automatic repair rounds see a held build
        # wait for the owner (test_auto_repair turns it on)
        "build": {
            "command": [sys.executable, str(FAKE_BUILD)],
            "policy": str(tmp),
            "revise_command": [sys.executable, str(FAKE_BUILD)],
            "auto_repair": 0,
        },
        # the tick runs the pre-push checks itself here (REPO_FACTORY_PRECHECK_INLINE, with the
        # fake honesty model); live, they run in a process of their own
        "publish": {
            "enabled": False,
            "gh_command": [sys.executable, str(FAKE_GH)],
            "copyright": "Test Owner",
        },
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return Config(tmp / "home", overrides=base)


def make_loop(tmp: Path, model=None, honesty=None, **over) -> Loop:
    return Loop(
        make_cfg(tmp, **over),
        model=model or FakeModel(pitch_json()),
        summary_model=FakeModel("A page about clinics."),
        honesty_model=honesty or FakeModel(HONEST),
    )


def wait(cond, timeout=30, step=0.1, tick=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if tick:
            tick()
        v = cond()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("timed out")

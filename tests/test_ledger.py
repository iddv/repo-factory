"""The cost ledger: every single-shot ``claude -p`` call made inside a build lands in
``<den>/ledger.jsonl`` with its caller, purpose, category and cost; outside a build nothing
is recorded."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from repo_factory.costs import ledger
from repo_factory.rules.llm import ClaudeLLM


def fake_claude(costs: list):
    """``subprocess.run`` for ``claude -p --output-format json``: each call costs the next
    of ``costs`` (None: the call fails)."""
    calls = []

    def run(cmd, input=None, timeout=None, **kw):
        calls.append({"cmd": cmd, "prompt": input})
        usd = costs[len(calls) - 1]
        if usd is None:
            return SimpleNamespace(returncode=1, stdout="", stderr="overloaded")
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                {
                    "result": '{"results": {}}',
                    "total_cost_usd": usd,
                    "is_error": False,
                    "usage": {
                        "input_tokens": 10,
                        "cache_read_input_tokens": 90,
                        "output_tokens": 7,
                    },
                    "modelUsage": {"claude-sonnet-5": {}},
                }
            ),
        )

    return run, calls


def test_every_single_shot_caller_is_recorded(tmp_path, monkeypatch):
    run, calls = fake_claude([0.30, 0.40, 0.50])
    monkeypatch.setattr(subprocess, "run", run)
    with ledger.activate(tmp_path / "ledger.jsonl", build=7):
        llm = ClaudeLLM("sonnet")
        llm("[factory-rules task=mine mode=edges]\nmine")
        llm("[factory-rules task=check]\ncheck")
        llm("[factory-rules task=arbitrate]\narbitrate")
    recs = ledger.read(tmp_path)
    assert all(r["build"] == 7 for r in recs)
    got = [(r["caller"], r.get("purpose"), r["category"], r["usd"], r["ok"]) for r in recs]
    assert got == [
        ("miner", "mine:edges", "mining", 0.3, True),
        ("validator", "check", "mining", 0.4, True),
        ("arbiter", "arbitrate", "mining", 0.5, True),
    ]
    assert recs[0]["in_tokens"] == 100 and recs[0]["out_tokens"] == 7
    assert recs[0]["model"] == "sonnet" and recs[0]["wall_s"] >= 0
    s = ledger.summarize(recs)
    assert s["total_usd"] == pytest.approx(1.2) and s["breakdown"]["mining"] == pytest.approx(1.2)
    assert llm.usd == pytest.approx(1.2)
    # no active ledger: nothing is recorded
    run2, _ = fake_claude([1.0])
    monkeypatch.setattr(subprocess, "run", run2)
    ClaudeLLM("sonnet")("[factory-rules task=check]\noutside a build")
    assert len(ledger.read(tmp_path)) == len(recs)

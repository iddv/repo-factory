"""Model backends for the rules engine: one callable ``llm(prompt) -> text``.

- ``ClaudeLLM``: ``claude -p`` (the same command as ``scoring.builder.run_claude_text``),
  also reading the call's cost so a build can report what the rules cost.
- ``FixtureLLM``: replays files from a fixture dir (``_fake_builder/rules/``), for fake
  builds.
- ``ScriptedLLM``: a function per task, for unit tests.

Every prompt starts with a tag line ``[factory-rules task=<task> mode=<mode>]`` (task: mine,
check, arbitrate), which the fakes dispatch on."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from pathlib import Path

TAG = re.compile(r"^\[factory-rules task=(\w+)(?: mode=(\w+))?\]")


def tag(task: str, mode: str | None = None) -> str:
    return f"[factory-rules task={task}" + (f" mode={mode}" if mode else "") + "]"


def parse_tag(prompt: str) -> tuple[str | None, str | None]:
    m = TAG.match(prompt)
    return (m.group(1), m.group(2)) if m else (None, None)


class BaseLLM:
    """Counts calls and cost. Thread-safe: with background mining (``mining.async``) a
    pass can call the model while the main thread's closing loop does too."""

    def __init__(self):
        self.calls = 0
        self.usd = 0.0
        self.log: list[dict] = []  # {"task", "mode", "usd", "chars"}
        self.lock = threading.Lock()

    def __call__(self, prompt: str) -> str:
        task, mode = parse_tag(prompt)
        with self.lock:
            self.calls += 1
        from ..costs.ledger import track

        # A live call_* row for each rules call, fixture-backed ones included (the
        # ClaudeLLM's own call_claude folds into it).
        with track(CALLER.get(task, "miner"), f"{task}:{mode}" if mode else task) as span:
            text, usd = self.complete(prompt, task, mode)
            if not isinstance(self, ClaudeLLM):
                span.add(usd)
        with self.lock:
            self.usd += usd or 0.0
            self.log.append({"task": task, "mode": mode, "usd": usd, "chars": len(prompt)})
        return text

    def complete(self, prompt: str, task, mode) -> tuple[str, float]:
        raise NotImplementedError


# The ledger's caller for each rules task (costs.ledger: all three are "mining" cost).
CALLER = {
    "mine": "miner",
    "replace": "miner",
    "check": "validator",
    "arbitrate": "arbiter",
    # build v5's no-tools calls (repo_factory/v5/)
    "claims": "claims",
    "dedupe": "claims",
    "oracle": "oracle",
    "interpret": "oracle",
    "pair": "pair",
    "postmortem": "postmortem",
    "scope": "scope",
}


class ClaudeLLM(BaseLLM):
    """``claude -p`` through the cost ledger's one helper (``costs.ledger.call_claude``)."""

    def __init__(self, model: str | None = None, timeout_s: int = 600, env: dict | None = None):
        super().__init__()
        self.model, self.timeout_s, self.env = model, timeout_s, env

    def complete(self, prompt, task, mode):
        from ..costs.ledger import call_claude

        text, data = call_claude(
            prompt,
            self.model,
            self.timeout_s,
            caller=CALLER.get(task, "miner"),
            purpose=f"{task}:{mode}" if mode else task,
            **({"env": self.env} if self.env else {}),
        )
        return text, float(data.get("total_cost_usd") or 0.0)


class ScriptedLLM(BaseLLM):
    """``handlers``: task -> fn(prompt, mode) -> text (or a fixed string)."""

    def __init__(self, handlers: dict[str, Callable | str]):
        super().__init__()
        self.handlers = handlers
        self.prompts: list[str] = []

    def complete(self, prompt, task, mode):
        self.prompts.append(prompt)
        h = self.handlers.get(task)
        if h is None:
            return "", 0.0
        return (h(prompt, mode) if callable(h) else h), 0.0


class FixtureLLM(BaseLLM):
    """Replays ``<dir>/mine_<mode>_<n>.md`` (the n-th call in that mode; falls back to
    ``mine_<mode>.md``, then to nothing), ``replace_<mode>_<n>.md`` likewise for the
    replacement of a promoted held rule, ``check.json`` (default: agree with everything)
    and ``arbitrate.json`` (rule title -> verdict; default ``code_wrong``)."""

    def __init__(self, root: Path):
        super().__init__()
        self.root = Path(root)
        self.n: dict[str, int] = {}

    def complete(self, prompt, task, mode):
        if task in ("mine", "replace"):
            with self.lock:
                k = self.n[f"{task}:{mode}"] = self.n.get(f"{task}:{mode}", 0) + 1
            for name in (f"{task}_{mode}_{k}.md", f"{task}_{mode}.md" if k == 1 else None):
                if name and (self.root / name).exists():
                    return (self.root / name).read_text(), 0.0
            return "", 0.0
        if task == "check":
            ids = re.findall(r"^### rule (\S+?):", prompt, re.M)
            over = (
                json.loads((self.root / "check.json").read_text())
                if (self.root / "check.json").exists()
                else {}
            )
            titles = dict(re.findall(r"^### rule (\S+): (.*)$", prompt, re.M))
            return json.dumps(
                {
                    "results": {
                        i: over.get(titles.get(i, ""), {"verdict": "agree", "reason": "fixture"})
                        for i in ids
                    }
                }
            ), 0.0
        if task == "arbitrate":
            titles = dict(re.findall(r"^### rule (\S+): (.*)$", prompt, re.M))
            over = (
                json.loads((self.root / "arbitrate.json").read_text())
                if (self.root / "arbitrate.json").exists()
                else {}
            )
            return json.dumps(
                {
                    "results": {
                        i: over.get(t, {"verdict": "code_wrong", "reason": "fixture"})
                        for i, t in titles.items()
                    }
                }
            ), 0.0
        return "", 0.0


def make_llm(backend: str, model: str | None = None, fixture: Path | None = None):
    """``claude`` -> ClaudeLLM; otherwise a FixtureLLM when the fixture dir exists, else
    None (no rules engine)."""
    if backend == "claude":
        return ClaudeLLM(model)
    if fixture is not None and Path(fixture).is_dir():
        return FixtureLLM(fixture)
    return None

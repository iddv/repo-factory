"""Deterministic canned harness for tests and CI: a repo task's run copies the task's
canned solution (or a milestone overlay) over the workspace and reports a tiny cost. No model
is called."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .base import HarnessResult

RUN_USD = 0.001  # a fake repo run's own cost


class FakeHarness:
    name = "fake"

    def __init__(self, failure_rate: int = 10, solution: Path | None = None):
        self.failure_rate = failure_rate  # 1 in N runs is broken; 0 disables
        self.solution = Path(solution) if solution else None  # repo tasks: canned code
        self.runs: list[dict] = []  # repo runs: milestone, per-run caps, session
        self.session_costs: dict[str, float] = {}  # session id -> cumulative cost (repo)

    def milestone_overlay(self, meta: dict) -> Path | None:
        """Policy v2 runs: ``<taskdir>/_fake_milestones/<id>/`` is copied over the workspace
        (which already holds the parent's code), so a fixture can build up code milestone by
        milestone. ``<id>.broken/`` is used instead on a milestone's first attempt, so a
        fixture can make a gate fail once and pass on the retry."""
        mid = meta.get("milestone")
        if meta.get("oneshot") and mid and self.solution:
            # Build v4: ``<taskdir>/_fake_oneshot/<id>/`` for the one-shot build (``build``,
            # ``build.bt1`` after a design backtrack) or a hardening repair (``repair2``,
            # ``repair1.bt1``). Without one, a build copies ``_fake_solution`` and a repair
            # changes nothing.
            d = self.solution.parent / "_fake_oneshot" / mid
            if d.is_dir() or meta["oneshot"] != "build":
                return d
            return None
        root = self.solution.parent / "_fake_milestones" if self.solution else None
        if not mid or root is None or not root.is_dir():
            return None
        # Build v3 §3 task runs: ``<task>.h<hint>/`` (a design task's fork), then
        # ``<task>/`` (e.g. ``m2.t1/``), then the milestone's, each ``.broken`` first.
        names = [mid]
        if meta.get("task"):
            names = (
                [f"{meta['task']}.h{meta['hint']}"] if meta.get("hint") is not None else []
            ) + [meta["task"], mid]
        if meta.get("epoch"):
            # Build v3 §4: after backtrack k, ``<name>.bt<k>/`` first (the rebuilt code).
            names = [f"{n}.bt{meta['epoch']}" for n in names] + names
        for name in names:
            broken = root / f"{name}.broken"
            if broken.is_dir() and not meta.get("retry"):
                return broken
            if name != mid and (root / name).is_dir():
                return root / name
        return root / mid  # stored-shape: the fake harness's own task name

    def run_repo(
        self, prompt: str, workspace: Path, meta: dict, t0: float, opts: dict | None = None
    ) -> HarnessResult:
        """Repo tasks: copy the task's canned solution over the workspace (or a milestone
        overlay, see ``milestone_overlay``). ``minimal`` adds a comment only, so its
        fingerprint matches the others'."""
        if self.solution is None or not self.solution.is_dir():
            raise ValueError("fake harness on a repo task needs solution=<dir>")
        opts = opts or {}
        overlay = self.milestone_overlay(meta)
        if overlay is not None:
            if overlay.is_dir():
                shutil.copytree(overlay, workspace, dirs_exist_ok=True)
        else:
            shutil.copytree(self.solution, workspace, dirs_exist_ok=True)
        session = opts.get("session") or {}
        # Like Claude Code, a resumed session reports its cost cumulatively over the whole
        # session history; the run's own cost is that minus the parent's (``run_cost``).
        from .claude_code import run_cost

        cumulative = (
            round(self.session_costs.get(session.get("resume"), 0.0) + RUN_USD, 7)
            if session.get("resume")
            else RUN_USD
        )
        if session.get("id"):
            self.session_costs[session["id"]] = cumulative
        record = {
            "type": "fake",
            "problem": "repo",
            "milestone": meta.get("milestone"),
            **(
                {
                    "task": meta["task"],
                    "hint": meta.get("hint"),
                    "workspace": str(workspace),
                    "lineage": session.get("lineage"),
                }
                if meta.get("task")
                else {}
            ),
            "max_turns": opts.get("max_turns"),
            "max_usd": opts.get("max_usd"),
            "timeout_s": opts.get("timeout_s"),
            "session_id": session.get("id"),
            "resume": session.get("resume"),
            "base_cost_usd": session.get("base_cost_usd"),
            "total_cost_usd": cumulative,
            **({"tools": opts["tools"]} if opts.get("tools") else {}),
        }
        self.runs.append(record)
        if meta.get("milestone"):
            record = {**record, "prompt": prompt}  # tests check what a milestone run saw
        if meta.get("strategy") == "minimal" and overlay is None:
            for p in sorted(self.solution.rglob("*.py")):
                dest = workspace / p.relative_to(self.solution)
                dest.write_text("# minimal change\n" + dest.read_text())
        transcript = workspace / "transcript.jsonl"
        transcript.write_text(
            json.dumps(
                record if overlay is not None or opts else {"type": "fake", "problem": "repo"}
            )
            + "\n"
        )
        return HarnessResult(
            artifact_dir=workspace,
            tool_calls=2,
            input_tokens=len(prompt) // 4,
            output_tokens=100,
            cost_usd=run_cost(cumulative, session),
            transcript_path=transcript,
            exit_ok=True,
            wall_s=time.time() - t0,
            session_id=session.get("id"),
            session_cost_usd=cumulative,
            model="fake-model",
        )

    def run(self, prompt: str, workspace: Path, timeout_s: int, **opts) -> HarnessResult:
        t0 = time.time()
        workspace = Path(workspace)
        meta = json.loads((workspace / "meta.json").read_text())
        if meta.get("problem") == "repo":
            return self.run_repo(
                prompt, workspace, meta, t0, {**opts, "timeout_s": timeout_s} if opts else None
            )
        raise ValueError("the fake harness runs repo tasks only (meta.json problem: repo)")

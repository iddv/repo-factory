"""A product folder with three Builds in its event log, for the tests that read one."""

from __future__ import annotations

import json


def _product(tasks, name="sidedoor"):
    """A product with three Builds in its event log: a fresh one, a docs revision of it, and a
    second revision stopped while it packaged."""
    d = tasks / name
    (d / ".den").mkdir(parents=True)
    (d / "spec.md").write_text(f"# {name}\n")
    lines, clock = [], [1_791_500_000.0]

    def ev(n, type_, **f):
        clock[0] += 60
        lines.append(
            json.dumps({"v": 1, "ts": clock[0], "task": name, "build": n, "type": type_, **f})
        )

    every = [
        "scope",
        "build",
        "claims",
        "verify",
        "oracle",
        "repair",
        "reverify",
        "readiness",
        "deliver",
    ]
    ev(1, "build_started", pipeline="v5", stages=every, title="Sidedoor")
    for s in every:
        ev(1, "v5_stage", stage=s, status="started")
        ev(1, "v5_stage", stage=s, status="done", wall_s=60)
    ev(1, "build_finished", status="ok", cost_usd=1.0, wall_s=600)
    for n in (2, 3):
        ev(
            n,
            "build_started",
            pipeline="v5",
            kind="revise",
            revises=1,
            origin="ui",
            mode="docs",
            stages=["build", "verify", "repair", "reverify", "deliver"],
            instructions="a word",
        )
        for s in ("scope", "claims", "oracle", "readiness"):
            ev(n, "v5_stage", stage=s, status="skipped", why="carried from round 1")
        ev(n, "v5_stage", stage="build", status="started")
        ev(n, "v5_stage", stage="build", status="done", wall_s=30)
        ev(n, "v5_stage", stage="deliver", status="started")
        if n == 2:
            ev(n, "v5_stage", stage="deliver", status="done")
            ev(n, "build_finished", status="ok", cost_usd=0.1, wall_s=300)
    (d / ".den" / "events.jsonl").write_text("\n".join(lines) + "\n")
    return d

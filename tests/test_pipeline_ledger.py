"""Builds with the ledger and ideas in the workspace (WORKSPACE.md 2.2, 2.3, 2.9, 6.1;
fleet2 track W4): api/state's build rows carry their pipeline (the stage strip the row's state
already folded), the six-cell ledger and its miniature, the build pane's sections and
outline, the idea pane, Ideas and Builds as grouped lists. The components are read whole;
what they do is pressed in Vitest beside them (pages/builds/pipe.test.ts,
pages/ideas/ideas.svelte.test.ts, lib/app/grading.test.ts)."""

import os
from pathlib import Path

from repo_factory.events import EventLog
from repo_factory.ui import server
from repo_factory.ui.v5view import STAGES

GONE = 4194304 + 7  # above Linux's largest pid: never a live process


def task(root: Path, name: str, pid: int, **started) -> EventLog:
    d = root / name
    d.mkdir(parents=True)
    (d / "spec.md").write_text(f"# {name}\n")
    log = EventLog(d / ".den" / "events.jsonl", name, 1)
    log.emit("build_started", pid=pid, pipeline="v5", policy="v5.0", **started)
    return log


# -- api/state: pipeline on a build row (6.1) ---------------------------------------------------
def test_build_rows_carry_their_pipeline(tmp_path):
    root = tmp_path / "root"
    # planned: a live run that listed its stages at the start, the first one running
    log = task(root, "planned", os.getpid(), stages=list(STAGES))
    log.emit("v5_stage", stage="scope", status="started")
    # partial: a run that stopped in verify (its process is gone); scope and readiness never ran
    log = task(root, "partial", GONE)
    for s in ("build", "claims"):
        log.emit("v5_stage", stage=s, status="started")
        log.emit("v5_stage", stage=s, status="done", usd=0.2, wall_s=30)
    log.emit("v5_stage", stage="verify", status="started")
    # pre-v5: a build with no v5 events at all
    d = root / "old"
    d.mkdir(parents=True)
    (d / "spec.md").write_text("# old\n")
    EventLog(d / ".den" / "events.jsonl", "old", 1).emit("build_started", pid=GONE, pipeline="v4")
    app = server.App(root, "claude-code")  # api/state's rows, as snapshot() makes them
    rows = {d.name: app.build_row(d, None) for d in sorted(root.iterdir())}
    planned = rows["planned"]["pipeline"]
    assert [x["stage"] for x in planned] == list(STAGES)
    assert planned[0] == {"stage": "scope", "status": "running"}
    assert {x["status"] for x in planned[1:]} == {"pending"}
    partial = {x["stage"]: x["status"] for x in rows["partial"]["pipeline"]}
    assert list(partial) == [s for s in STAGES if s not in ("scope", "readiness")]
    assert partial["build"] == partial["claims"] == "done" and partial["verify"] == "stopped"
    assert {partial[s] for s in ("oracle", "repair", "reverify", "deliver")} == {"not_run"}
    assert "pipeline" not in rows["old"]
    # only the stage and its status: the row stays small (no tokens, no money)
    assert all(set(x) == {"stage", "status"} for x in planned)


# -- the ledger (2.3) ---------------------------------------------------------------------------


# -- the build pane (2.3) -----------------------------------------------------------------------
# -- Builds and Ideas as lists ------------------------------------------------------------------


# walking past New idea with j leaves the cursor in the list: pressed in lib/app/grading.test.ts
# ("New idea's field takes the cursor when opened; walked past with j, the cursor stays in the list")
# and pages/ideas/ideas.svelte.test.ts ("New idea: the field takes the cursor when opened, …")

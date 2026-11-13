"""The build loop's runs as they go: its half of the activity read.

A **run** of the loop is a tick's step that did work, never a tick that only watched: a brief
(one run a task), a maintenance poll that acted, a publish. Each is written to ``loop.db``'s ``activity`` table (migration 3) as it
goes: ``started`` with the process that runs it (a run whose process is gone reads as cut off,
never as running forever), ``ended`` with its state, its error and its spend.

The rest of the loop's work keeps the records it already has, and is listed from them: a
build (its den's events, folded by ``status``), the pre-push checks of a build (its
``publish.precheck``), a project's evolve or charter job (``project_job:`` in ``meta``).
Nothing here reads a transcript.

Every word kept passes the secrets scrubber (``secrets.scrub``) before it is written; nothing
calls a model."""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

from .. import proc

KEEP_S = 30 * 86400  # the loop's marks kept
RECENT_S = 86400
WORDS_MAX = 300

# kind: (running, done, verb when it failed)
WORDS = {
    "loop.pitch": (
        "Writing the brief of {title}",
        "Wrote the brief of {title}",
        "Write the brief again",
    ),
    "loop.maintain": ("Looking after your repositories", "Looked after your repositories", None),
    "loop.publish": ("Publishing {title}", "Published {title}", "Publish again"),
    "loop.precheck": (
        "The pre-push checks of {title}",
        "Checked {title} before pushing",
        "Run the checks again",
    ),
    "project.evolve": (
        "{title}: what could improve",
        "{title}: looked for what could improve",
        "Run again",
    ),
    "project.charter": ("Drafting {title}'s charter", "Drafted {title}'s charter", "Run again"),
    "build": ("Building {title}", "Built {title}", "Retry"),
    # a press that did not happen: its title is the action's and the subject's
    "press": ("{title}", "{title}", None),
}


def _clip(text) -> str | None:
    if text is None:
        return None
    from .secrets import scrub

    s = scrub(" ".join(str(text).split()))
    return s if len(s) <= WORDS_MAX else s[: WORDS_MAX - 1] + "…"


def title_of(kind: str, state: str, title: str | None) -> str:
    row = WORDS.get(kind)
    t = title or "an idea"
    if row is None:
        return kind.replace(".", " ").replace("_", " ").capitalize()
    return (row[0] if state in ("running", "waiting") else row[1]).format(title=t)


class Step:
    """One step of a tick in progress: ``worked`` says whether it did work (an ``always``
    False step that did nothing leaves no row); ``words`` its stage; ``usd`` its spend."""

    def __init__(
        self, ledger, kind: str, run: str, *, title=None, idea=None, project=None, repo=None
    ) -> None:
        self.ledger, self.kind, self.run = ledger, kind, run
        self.title, self.idea, self.project, self.repo = title, idea, project, repo
        self.worked = True
        self.words: str | None = None
        self.usd: float | None = None
        self.error: str | None = None  # a failure the step's own code caught and kept
        self.started = time.time()


def _write(ledger, row: dict) -> None:
    cols = (
        "run",
        "kind",
        "ts",
        "mark",
        "state",
        "title",
        "words",
        "error",
        "idea",
        "project",
        "repo",
        "pid",
        "pstart",
        "usd",
        "detail",
    )
    with ledger.db() as c:
        c.execute(
            f"INSERT INTO activity ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            tuple(row.get(k) for k in cols),
        )


def _spent(ledger, since: float, idea: str | None) -> float | None:
    """The loop's own spend since the step began (its ``spend`` rows: a pitch, a summary)."""
    try:
        with ledger.db() as c:
            r = c.execute(
                "SELECT SUM(usd) AS u FROM spend WHERE ts >= ?" + (" AND idea = ?" if idea else ""),
                (since, idea) if idea else (since,),
            ).fetchone()
        return round(float(r["u"]), 6) if r and r["u"] else None
    except Exception:  # noqa: BLE001 - the spend is left out
        return None


@contextlib.contextmanager
def step(
    ledger,
    kind: str,
    *,
    title: str | None = None,
    idea: str | None = None,
    project: str | None = None,
    repo: str | None = None,
    always: bool = True,
) -> Iterator[Step]:
    """A tick's step as a run. ``always`` False: written only when the block set ``worked``
    (a poll that did nothing is not a run); its ``started`` is then written at the end,
    with the time it began."""
    s = Step(
        ledger,
        kind,
        f"{kind}-{uuid.uuid4().hex[:12]}",
        title=_clip(title),
        idea=idea,
        project=project,
        repo=repo,
    )
    me = proc.me()
    base = {
        "run": s.run,
        "kind": kind,
        "title": s.title,
        "idea": idea,
        "project": project,
        "repo": repo,
    }
    if always:
        _write(
            ledger,
            {**base, "ts": s.started, "mark": "started", "pid": me["pid"], "pstart": me["start"]},
        )
    else:
        s.worked = False
    error = None
    try:
        yield s
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        s.worked = True
        raise
    finally:
        error = error or s.error
        if s.worked and s.usd is None:
            s.usd = _spent(ledger, s.started, idea)
        if s.worked:
            with contextlib.suppress(Exception):  # a mark never fails the tick
                if not always:
                    _write(
                        ledger,
                        {
                            **base,
                            "ts": s.started,
                            "mark": "started",
                            "pid": me["pid"],
                            "pstart": me["start"],
                        },
                    )
                _write(
                    ledger,
                    {
                        **base,
                        "ts": time.time(),
                        "mark": "ended",
                        "state": "failed" if error else "ok",
                        "error": _clip(error),
                        "words": _clip(s.words),
                        "usd": s.usd,
                    },
                )


def refused(
    ledger, kind: str, title: str, why: str, *, idea: str | None = None, name: str | None = None
) -> None:
    """A press that was refused or failed, as an ended run in Activity: "Publish despite the
    holds · Repo Recall", failed, with why (ASKF: nothing recorded why before)."""
    me = proc.me()
    now = time.time()
    run = f"press-{uuid.uuid4().hex[:12]}"
    base = {
        "run": run,
        "kind": "press",
        "title": _clip(f"{title} · {name}" if name else title),
        "idea": idea,
        "detail": json.dumps({"action": kind}),
    }
    _write(ledger, {**base, "ts": now, "mark": "started", "pid": me["pid"], "pstart": me["start"]})
    _write(
        ledger,
        {
            **base,
            "ts": now,
            "mark": "ended",
            "state": "failed",
            "error": _clip(f"did not happen: {why}"),
        },
    )


# -- the loop's half of the read ----------------------------------------------------------------


def _run_row(
    kind: str,
    rid: str,
    state: str,
    *,
    title=None,
    words=None,
    started=None,
    finished=None,
    usd=None,
    error=None,
    trace=None,
    stoppable=False,
    idea=None,
    subject=None,
    now=None,
) -> dict:
    now = now or time.time()
    end = finished or now
    return {
        "id": rid,
        "uuid": rid.split(":", 1)[-1],
        "kind": kind,
        "title": title_of(kind, state, title),
        "words": words,
        "state": state,
        "trigger": {"origin": "loop", "by": None, "at": _iso(started)},
        "subject": subject or ({"kind": "idea", "id": idea, "name": title} if idea else None),
        "feature": "loop" if kind.startswith(("loop.", "project.")) or kind == "press" else kind,
        "started": _iso(started),
        "finished": _iso(finished),
        "elapsed_s": round(end - started, 1) if started else None,
        "usd": round(float(usd), 4) if usd else 0.0,
        "calls": None,
        "tokens": None,
        "progress": None,
        "trace": trace,
        "stoppable": stoppable,
        "stopping": False,
        "needs": None,
        "error": error if state == "failed" else None,
        "why": None,
        "verb": (WORDS.get(kind) or (None, None, "Run again"))[2] if state == "failed" else None,
    }


def _iso(t) -> str | None:
    if not t:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(t)))


def steps(ledger, now: float | None = None) -> tuple[list[dict], list[dict]]:
    """The tick's steps: ``(running, recent)``. A started step whose process is gone is cut
    off (recent, ``stopped``)."""
    now = now or time.time()
    try:
        with ledger.db() as c:
            rows = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM activity WHERE ts >= ? ORDER BY id", (now - RECENT_S,)
                )
            ]
    except Exception:  # noqa: BLE001 - a ledger without the table yet
        return [], []
    by: dict[str, dict] = {}
    for r in rows:
        d = by.setdefault(r["run"], {})
        d[r["mark"]] = r
    from .. import rowsafe

    running: list[dict] = []
    recent: list[dict] = []
    for rid, d in by.items():
        st, en = d.get("started"), d.get("ended")
        if st is None:
            continue
        # one step that cannot be read is listed (in recent) as such (RCA A3)
        with rowsafe.row(
            recent,
            "run",
            f"loop:{rid}",
            kind=st.get("kind") or "loop",
            state="failed",
            finished=(en or st).get("ts"),
        ):
            base = dict(
                kind=st["kind"],
                rid=f"loop:{rid}",
                title=st["title"],
                idea=st["idea"],
                started=st["ts"],
                now=now,
                trace=None if st["kind"] == "press" else f"#/loop/{rid}/trace",
            )
            if en is not None:
                recent.append(
                    _run_row(
                        state=en["state"] or "ok",
                        finished=en["ts"],
                        words=en["words"],
                        usd=en["usd"],
                        error=en["error"],
                        **base,
                    )
                )
            elif proc.record_alive({"pid": st["pid"], "start": st["pstart"]}):
                running.append(_run_row(state="running", **base))
            else:
                recent.append(
                    _run_row(
                        state="stopped",
                        finished=st["ts"],
                        words="cut off: repo-factory restarted; it runs again at its time",
                        **base,
                    )
                )
    return running, recent


def _is_fix(task_dir: Path) -> bool:
    """A maintenance fix's task (its ``loop.json`` says kind ``fix``): the top bar said
    "Revising" for one, and says it still."""
    try:
        j = json.loads((task_dir / "loop.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(j, dict) and j.get("kind") == "fix"


def detached(loop, key_of, now: float | None = None) -> list[dict]:
    """The loop's work that runs in a process or a session of its own, from its own records:
    builds, pre-push checks, project jobs."""
    now = now or time.time()
    out: list[dict] = []
    lp = loop
    try:
        status = lp.status()
    except Exception:  # noqa: BLE001 - the rest still shows
        status = {"running": []}
    for b in status.get("running") or []:
        key = key_of(Path(b["task_dir"])) if b.get("task_dir") else b["id"]
        words = " · ".join(str(x) for x in (b.get("stage"), b.get("phase")) if x) or None
        row = _run_row(
            "build",
            f"build:{key}",
            "running",
            title=b.get("title"),
            words=words,
            started=b.get("started"),
            usd=b.get("cost_usd"),
            trace=f"#/builds/{key}",
            stoppable=True,
            idea=b["id"],
            now=now,
        )
        if b.get("task_dir") and _is_fix(Path(b["task_dir"])):
            row["title"] = f"Revising {b.get('title') or 'a fix'}"  # a maintenance fix
        out.append(row)
    for idea in lp.ledger.all(("ready", "held")):
        pc = (idea.get("publish") or {}).get("precheck") or {}
        if (
            pc.get("state") == "running"
            and pc.get("pid")
            and proc.record_alive({"pid": pc.get("pid"), "start": pc.get("start")})
        ):
            out.append(
                _run_row(
                    "loop.precheck",
                    f"pid:{pc['pid']}",
                    "running",
                    title=idea.get("title"),
                    started=pc.get("started"),
                    idea=idea["id"],
                    stoppable=True,
                    now=now,
                )
            )
    with contextlib.suppress(Exception):
        names = {}
        with lp.ledger.db() as c:
            names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM projects")}
        for k, rec in (lp.ledger.metas("project_job:") or {}).items():
            _, what, proj = (k.split(":", 2) + ["", ""])[:3]
            if (
                isinstance(rec, dict)
                and rec.get("state") == "running"
                and rec.get("pid")
                and proc.record_alive(rec)
            ):
                out.append(
                    _run_row(
                        f"project.{what}",
                        f"pid:{rec['pid']}",
                        "running",
                        title=names.get(proj) or "a project",
                        started=rec.get("started"),
                        stoppable=True,
                        now=now,
                        subject={"kind": "project", "id": proj, "name": names.get(proj)},
                    )
                )
    return out


def stop_pid(pid: int, grace_s: float = 20.0) -> bool:
    """Stop a detached job of the loop's: SIGTERM, SIGKILL after ``grace_s`` (as a build's
    stop). False when it was not running."""
    import signal
    import threading

    if not proc.alive(pid):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False

    def later():
        time.sleep(grace_s)
        if proc.alive(pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)

    threading.Thread(target=later, name=f"stop-{pid}", daemon=True).start()
    return True


def prune(ledger, now: float | None = None, keep_s: float = KEEP_S) -> int:
    """The loop's marks older than ``keep_s`` removed (a working record, like ``meta``)."""
    now = now or time.time()
    with ledger.db() as c:
        return c.execute("DELETE FROM activity WHERE ts < ?", (now - keep_s,)).rowcount

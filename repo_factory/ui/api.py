"""repo-factory's web API as one plain function: ``handle(method, path, query, body)``.

``repo-factory ui``'s stdlib server (``server.py``) calls it. The function knows nothing about
the server: it takes the path and returns ``(status, content_type, body_bytes)``. Guards stay
with the server: loopback only, and no cross-origin POST (403). Who is calling is the server's
to say too: it checks the request's token and passes ``identity``; a write acts as that
caller, never as the body's ``origin``.

The page holds no state of its own. Reads fold ``<den>/events.jsonl`` with
``repo_factory.events`` and read ``REPORT.md`` / ``git diff`` / ``build.log``, and the loop's
ledger; every action shells out to the ``repo-factory`` CLI, so the page can never do something the
CLI can't.

Pages, all served from files (no inline script: the content policy refuses it): ``/`` the
page (``app.html`` + the built app under ``static/app/`` + ``static/repo-factory.css``),
``/help`` the longer explanations."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

from .. import gitcmd, paths, proc, rowsafe
from ..errors import LoopError
from ..events import load_state, log_path, read_events
from .http import HttpError, _file, _json  # noqa: F401  (HttpError: callers import it here)
from .routes import ROUTES
from .routes.base import Req, route
from .routes.static import APP_PAGE, MOVED, STATIC, TYPES, UI_DIR  # noqa: F401

API_VERSION = 1
HOST = "127.0.0.1"
BRB = [sys.executable, "-m", "repo_factory.cli"]
SKIP = {"out", "repo", "tests", "reference", "node_modules", "__pycache__"}
NEEDS_YOU, RUNNING, DONE = "needs_you", "running", "done"
GROUP = {
    "new": NEEDS_YOU,
    "asking": NEEDS_YOU,
    "delivered": NEEDS_YOU,
    "running": RUNNING,
    "accepted": DONE,
    "rejected": DONE,
    "stopped": DONE,
    "failed": DONE,
    "closed": DONE,
}
SPAWN_GRACE_S = 3.0
# Settings › Publishing › Maintenance's identity before GitHub has answered once
WAITING_ID = {
    "status": "warn",
    "pending": True,
    "line": "still asking GitHub who this is; open the page again in a moment",
}
# The policy the page offers: v5, the product pipeline (`repo-factory build`'s default).
REPO = Path(__file__).resolve().parents[2]
POLICIES = ("v5",)
DEFAULT_POLICY = "v5"
CALL_TYPES = ("call_started", "call_finished")
IDEA_ID = re.compile(r"i[0-9a-f]{8}\d*")
# The loop's ledger states in the page's words.
LOOP_BUILD = {
    "queued": "queued",
    "building": "running",
    "paused": "paused",
    "held": "held",
    "ready": "ready",
    "published": "published",
    "public": "public",
    "closed": "closed",
}
# a build the owner is stopping (``build.kill``, until the loop sees it gone), in words
CONTROL_REASON = {
    "stop": "Stopping… (Stop round): its workers are being stopped",
    "pause": "Stopping… (Stop round): its workers are being stopped",  # older
    "restart": "Stopping… to restart the revise round with your new instructions",
}
LOOP_IDEA = {
    "new": "open",
    "rejected": "passed",
    "skipped": "skipped",
    "error": "failed",
    "closed": "closed",
}
log = logging.getLogger(__name__)


def started_by(idea: dict | None) -> dict | None:
    """Who asked for the idea's current (or last) round: ``{origin, at, round}`` from its
    build record (``Loop.start_builds``); None before any round started."""
    b = (idea or {}).get("build") or {}
    sb = b.get("started_by")
    if not isinstance(sb, dict):
        return None
    rnd = b.get("round")
    by_round = (b.get("rounds_by") or {}).get(str(rnd)) if rnd is not None else None
    sb = by_round if isinstance(by_round, dict) and b.get("status") != "running" else sb
    return {"origin": sb.get("origin"), "at": sb.get("at"), "round": rnd}


def undo_view(u: dict | None) -> dict | None:
    """The Undo the page may still offer: ``{of, until, round}`` while its window is open."""
    if not isinstance(u, dict) or not u.get("of") or time.time() > float(u.get("until") or 0):
        return None
    return {"of": u["of"], "until": u["until"], "round": u.get("round")}


# Reject's reason chips for a build: a closed vocabulary, so a chip is never read as one of
# `repo-factory loop reject`'s options.
REJECT_CHIPS = ("wrong idea", "wrong scope", "bugs", "install or README", "security", "not for me")


def idea_title(i: dict) -> str:
    """A task's own title, never its product's name: the brief renames the ledger row to the
    product, which names the build; the task keeps the words it was added with."""
    card = i.get("card") or {}
    product = (i.get("pitch") or {}).get("product")
    if card.get("title"):
        return card["title"]
    t = i.get("title") or ""
    if not t or t == product:
        from ..loop.ledger import first_line

        return first_line(i.get("text") or "") or t or i["id"]
    return t


def installed_version() -> str:
    """This repo-factory's version (a build page says which version judged a build)."""
    from ..v5.exam import factory_version

    return factory_version()


def is_task(d: Path) -> bool:
    return (d / "spec.md").is_file() or (d / ".den" / "events.jsonl").is_file()


def scan(root: Path) -> list[Path]:
    """Task dirs at ROOT and up to two levels below; a task dir's own subfolders are not
    searched."""
    found: list[Path] = []

    def walk(d: Path, depth: int):
        if is_task(d):
            found.append(d)
            return
        if depth == 2:
            return
        try:
            kids = sorted(p for p in d.iterdir() if p.is_dir())
        except OSError:
            return
        for k in kids:
            if not k.name.startswith((".", "_")) and k.name not in SKIP:
                walk(k, depth + 1)

    walk(root, 0)
    return found


def tail(path: Path, n: int = 200) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def ledger_tokens(den: Path) -> tuple[dict | None, dict]:
    """A task's tokens from its cost ledger (``<den>/ledger.jsonl``): ``(total, {build
    round: total})``, each ``{"in", "out"}``. Only lines with token fields count; a total
    with none is None (absent from the dict), never zero."""
    from ..costs import ledger
    from ..loop.ledger import add_tokens, tokens_of

    total, by = None, {}
    for r in ledger.read(den):
        t = tokens_of(r)
        if t is None:
            continue
        total = add_tokens(total, t)
        by[r.get("build")] = add_tokens(by.get(r.get("build")), t)
    return total, by


def iso_ts(s: str | None) -> float | None:
    """An ISO time as epoch seconds."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def is_build_of(pid: int, d: Path) -> bool:
    """``pid`` is still a build of the task folder ``d``: its command line names the folder
    (the page's and the loop's builds) or it runs in it (``repo-factory build .`` from a terminal). A
    recycled pid is neither, so Stop never signals a stranger."""
    if proc.is_job(pid, d):
        return True
    where = proc.cwd(pid)
    return where is not None and proc.alive(pid) and where.resolve() == Path(d).resolve()


# Needs you by rule: a project's asks are one row (minor asks older than
# projects.ask_days leave its count); repo-factory's own pull requests are not Needs you rows (they are on
# the project's page); an AGENTS.md drift or a missing AGENTS.md folds into its project's row
ASK_DAYS = 14
ASKS_OPEN_MAX = 3
NOT_NEEDS = ("factory_pr", "factory_pr_draft")
FOLDED = ("drift", "agents_md")


def needs_rows(maint: list[dict], asks: list[dict]) -> int:
    """How many Needs you rows the maintenance rows and the project asks make (the UI's
    ``projectRows`` and ``maintShown`` count the same)."""
    with_row = {(a.get("project") or {}).get("id") for a in asks if not a.get("stale")}
    with_row.discard(None)
    kept = [
        m
        for m in maint
        if m.get("kind") not in NOT_NEEDS
        and not (m.get("kind") in FOLDED and (m.get("project") or {}).get("id") in with_row)
    ]
    return len(with_row) + len(kept)


class App:
    def __init__(self, root: Path, harness: str = "claude-code", loop_home: Path | None = None):
        self.root = Path(root).resolve()
        self.harness = harness
        self.loop_home = loop_home  # None: $REPO_FACTORY_HOME, else ~/.repo-factory
        self.procs: dict[str, tuple[subprocess.Popen, float]] = {}
        self.lock = threading.Lock()
        self._rows: dict[str, tuple[tuple, dict]] = {}  # build rows of finished builds
        self._gates: dict[str, tuple[tuple, dict]] = {}
        self._ideas: tuple[tuple, dict] = ((), {})
        self._maint = None  # (key, maintain.github.Reader): the page's GitHub reads

    def loop_ideas(self) -> dict[str, dict]:
        """The loop ledger's ideas that have a task folder, by resolved folder: read-only,
        cached on the database's time; empty when there is no ledger. The build list and
        page use it so a build shows its product's name and the ledger's state (a closed
        idea's task is Closed, not waiting for you)."""
        import sqlite3

        from ..settings.store import Config

        try:
            db = Config(self.loop_home).db
            sig = tuple(
                f.stat().st_mtime_ns if f.exists() else 0
                for f in (db, db.with_name(db.name + "-wal"))
            )
        except Exception:  # noqa: BLE001 - no loop here
            return {}
        if not sig[0]:
            return {}
        if self._ideas[0] == sig:
            return self._ideas[1]
        out = {}
        try:
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            try:
                for iid, state, title, pitch, task in c.execute(
                    "SELECT id, state, title, pitch, task_dir FROM ideas WHERE task_dir IS NOT NULL"
                ):
                    p = json.loads(pitch) if pitch else {}
                    out[str(Path(task).resolve())] = {
                        "id": iid,
                        "state": state,
                        "title": p.get("product") or title,
                    }
            finally:
                c.close()
        except (sqlite3.Error, ValueError):
            return {}
        self._ideas = (sig, out)
        return out

    # ---- reading ---------------------------------------------------------------------
    def task_dir(self, d: str | None) -> Path:
        """``d`` must be one of the scanned dirs; anything else is a 404."""
        if d:
            for t in scan(self.root):
                if str(t) == d:
                    return t
        raise HttpError(404, "no such build under ROOT")

    def den(self, d: Path) -> Path:
        return d / ".den"

    def state(self, d: Path) -> dict:
        """Everything the page shows for one build, with a single derived ``state``."""
        st = load_state(d)
        own = self.procs.get(str(d))
        if st is None:
            state = "new"
        elif st["status"] == "error":
            state = "failed"
        elif st["status"] == "interrupted":
            state = "stopped"
        elif st["status"] == "running":
            self.mark_pending(d, st)
            asking = [q for q in st["questions"] if not q["answered"] and not q["pending"]]
            state = "asking" if asking else "running"
        else:
            v = (st.get("verdict") or {}).get("verdict")
            state = {"accept": "accepted", "reject": "rejected"}.get(v, "delivered")
        error = st.get("error") if st else None
        if own:
            p, t0 = own
            rc = p.poll()
            fresh = st is None or (st.get("started") or 0) < t0
            if rc is None and state not in ("asking", "running") and fresh:
                state = "running"  # spawned, not logged yet
            elif rc not in (None, 0) and fresh:
                state, error = (
                    "failed",
                    f"repo-factory exited with code {rc} before the build started",
                )
        title, tid = (st or {}).get("title") or d.name, (st or {}).get("task") or d.name
        idea = self.loop_ideas().get(str(d.resolve()))
        if idea:
            # A loop build: the product's name, and the folder (not the task id the task
            # was first created with); a closed idea's task is closed, not waiting for you.
            title, tid = idea["title"] or title, d.name
            if (
                idea["state"] != "building"
                and state in ("running", "asking")
                and not (own and own[0].poll() is None)
            ):
                # the loop says it does not run: a round the owner stopped or paused (its
                # log has no end), or one queued to resume
                state = "stopped"
            if idea["state"] == "closed" and state not in ("running", "asking"):
                state = "closed"
        out = {
            "dir": str(d),
            "id": tid,
            "state": state,
            "waiting": state in ("asking", "delivered"),
            "group": GROUP[state],
            "title": title,
            "error": error,
            "loop_idea": (idea or {}).get("id"),
            "updated": self.updated(d),
            "build": st,
            "now": round(time.time(), 3),
        }
        if st and state == "delivered":
            from ..progress import summary

            out["summary"] = summary(st, d).split("\nNext:")[0].rstrip()
        if state in ("failed", "stopped"):
            out["log"] = tail(self.den(d) / "build.log", 30)
        if st and st.get("v5"):
            from .v5view import view

            out["v5"] = view(d, st)
            if out["v5"]["label"]:  # the phase pill: "Verifying: 23 scenarios"
                st["activity"]["stage"] = out["v5"]["label"]
        return out

    def mark_pending(self, d: Path, st: dict) -> None:
        """An answer file beside its waiting question file has been given but not yet
        picked up by the build (it polls once a second): don't ask for it again."""
        qdir = self.den(d) / "human"
        for q in st["questions"]:
            stem = f"{st['task']}.{q['id']}"
            q["pending"] = (
                not q["answered"]
                and (qdir / f"{stem}.question").exists()
                and (qdir / f"{stem}.answer").exists()
            )

    def updated(self, d: Path) -> float | None:
        p = log_path(self.den(d))
        for f in (p, d / "spec.md"):
            if f.exists():
                return round(f.stat().st_mtime, 3)
        return None

    def builds(self) -> list[dict]:
        rows: list[dict] = []
        for d in scan(self.root):
            # one build that cannot be read is listed as such
            with rowsafe.row(rows, "build", d.name, dir=str(d), title=d.name, updated=None):
                s = self.state(d)
                act = (s["build"] or {}).get("activity") or {}
                rows.append(
                    {
                        k: s[k]
                        for k in ("dir", "id", "title", "state", "waiting", "group", "updated")
                    }
                    | {
                        "open_questions": sum(
                            1
                            for q in (s["build"] or {}).get("questions") or []
                            if not q["answered"] and not q.get("pending")
                        ),
                        "phase": act.get("phase"),
                        "progress": (act.get("progress") or {}).get("fraction"),
                        "running_n": len(act.get("running") or []),
                    }
                )
        rows.sort(key=lambda r: -(r.get("updated") or 0))
        return rows

    def events(self, d: Path, since: int) -> dict:
        """New events since ``since`` plus the folded state. The many ``call_*`` lines are
        left out of ``events`` (the state's ``activity`` already folds them); ``next``
        still counts every line."""
        evs = read_events(log_path(self.den(d)))
        return {
            "events": [e for e in evs[since:] if e["type"] not in CALL_TYPES],
            "next": len(evs),
            "state": self.state(d),
        }

    def text(self, d: Path, what: str, start: int = 0) -> str:
        st = load_state(d) or {}
        dl = st.get("delivered") or {}
        out = Path(dl.get("out") or d / "out")
        if what == "spec":
            return (d / "spec.md").read_text(errors="replace") if (d / "spec.md").exists() else ""
        if what == "report":
            p = Path(dl.get("report") or out / "REPORT.md")
            return p.read_text(errors="replace") if p.exists() else "(no REPORT.md yet)"
        if what == "diff":
            from .v5view import is_repo

            if not is_repo(out):
                return "(no delivery repo yet)"
            branch = dl.get("branch") or "HEAD"
            p = gitcmd.run(["diff", f"main..{branch}"], cwd=out, capture_output=True, text=True)
            return p.stdout or p.stderr or "(no changes)"
        if what == "log":
            if start:  # the window around a fail line (faillog.py)
                from .faillog import window

                return window(self.den(d) / "build.log", start)
            return tail(self.den(d) / "build.log")
        if what == "readme":
            return self.readme(out, dl.get("branch")) or "(no README.md on the delivered branch)"
        if what == "assumptions":
            from .v5view import assumptions_text

            return assumptions_text(d, st) or "(no ASSUMPTIONS.md yet)"
        if what == "scope":
            from .v5view import scope_text

            return scope_text(d, st) or "(no SCOPE.md: the input was used as the spec)"
        if what in ("security", "readiness"):
            from .v5view import delivered_text

            name = f"{what.upper()}.md"
            return delivered_text(d, st, name) or f"(no {name}: no readiness stage yet)"
        raise HttpError(
            400,
            "what must be spec, report, assumptions, scope, security, "
            "readiness, readme, diff or log",
        )

    @staticmethod
    def readme(out: Path, branch: str | None) -> str | None:
        """The README the world will see: the delivered branch's, else the working copy's."""
        from .v5view import is_repo

        if is_repo(out):
            for ref in (r for r in (branch, "HEAD") if r and not r.startswith("-")):
                p = gitcmd.run(
                    ["show", f"{ref}:README.md"], cwd=out, capture_output=True, text=True
                )
                if p.returncode == 0:
                    return p.stdout
        f = out / "README.md"
        return f.read_text(errors="replace") if f.is_file() else None

    # ---- acting (always through the command line) ---------------------------------------
    def run(
        self,
        args: list[str],
        cwd: Path | None = None,
        stdin: str | None = None,
        timeout: float = 120,
    ) -> str:
        # a verb run for a request (any door, the command line's token too) never waits on a
        # model: Loop.may_wait reads REPO_FACTORY_NO_WAIT
        env = {**os.environ, "REPO_FACTORY_NO_WAIT": "1"}
        if self.loop_home:
            env[paths.HOME_ENV] = str(self.loop_home)
        p = subprocess.run(
            [*BRB, *args],
            cwd=cwd or self.root,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin,
            env=env,
        )
        if p.returncode != 0:
            raise HttpError(400, (p.stderr or p.stdout).strip() or f"repo-factory {args[0]} failed")
        return (p.stdout + p.stderr).strip()

    def spawn(self, d: Path, args: list[str]) -> dict:
        """Start a build/revise detached, output to ``<den>/build.log``. Waits briefly so
        an immediate failure (bad task, dirty delivery repo) comes back as an error."""
        with self.lock:
            s = self.state(d)
            if s["state"] in ("asking", "running"):
                raise HttpError(409, f"{d.name} is already {s['state']}")
            env = self.build_env()  # refuses before anything is written
            if self.harness == "claude-code":  # no Claude login it may use, no start
                from ..harness import handoff

                login = handoff.build_login(cfg=self.loop_cfg())
                if login["on"] is None:
                    raise HttpError(409, handoff.HOLD)
            den = self.den(d)
            p = proc.spawn_detached(
                [*BRB, *args],
                cwd=d,
                log=den / "build.log",
                env=env,
                banner=f"repo-factory {' '.join(args)}",
            )
            self.procs[str(d)] = (p, time.time())
        deadline = time.time() + SPAWN_GRACE_S
        while time.time() < deadline and p.poll() is None:
            time.sleep(0.1)
        if p.returncode not in (None, 0):
            raise HttpError(
                400, tail(den / "build.log", 15) or f"repo-factory exited {p.returncode}"
            )
        return {"ok": True, "pid": p.pid}

    def new(self, text: str) -> dict:
        """``POST api/new {text}``: a typed task (or a file's contents) into the ledger, as
        ``repo-factory loop add`` does; its brief is written on the loop's next tick, then it is
        queued. ``{"id", "created"}``."""
        text = (text or "").strip()
        if not text:
            raise HttpError(400, "describe what you want built")
        lp = self.loop()
        from ..registry import current_origin

        lp.ledger.origin = current_origin() or "ui"
        try:
            idea, created = lp.add(text, "ui")
        except ValueError as e:
            raise HttpError(400, str(e))
        if not created and idea["state"] in ("rejected", "error", "skipped", "closed"):
            idea = lp.send(idea["id"])
        self._ideas = ((), {})
        return {"id": idea["id"], "created": created, "state": idea["state"]}

    def new_folder(self, text: str, seed: str = "", tests: str = "", budget_usd=None) -> dict:
        """``repo-factory new`` with the whole text as spec.md (``--spec -``): a task folder for
        a build started by hand; its first line names the task. ``seed``/``tests`` are paths
        on this machine (``--seed``/``--tests``); ``budget_usd`` is an optional spend ceiling
        (blank/None = unlimited)."""
        text = (text or "").strip()
        if not text:
            raise HttpError(400, "describe what you want built")
        name = text.splitlines()[0].lstrip("#").strip()
        args = ["new", name, "--spec", "-"]
        for value, flag, label in ((seed, "--seed", "Existing code"), (tests, "--tests", "Tests")):
            value = (value or "").strip()
            if not value:
                continue
            p = Path(value).expanduser()
            if not p.is_absolute():
                raise HttpError(400, f"{label}: give an absolute path (or ~/...), got {value}")
            if not p.is_dir():
                raise HttpError(400, f"{label}: {p} does not exist or is not a directory")
            args += [flag, str(p.resolve())]
        if budget_usd not in (None, ""):
            try:
                b = float(str(budget_usd).strip().lstrip("$"))
            except ValueError:
                raise HttpError(400, f"Budget: give a number of dollars, got {budget_usd}")
            if b <= 0:
                raise HttpError(400, "Budget: must be more than 0 (leave blank for unlimited)")
            args += ["--budget-usd", f"{b:g}"]
        before = {str(t) for t in scan(self.root)}
        self.run(args, cwd=self.root, stdin=text + "\n")
        made = [t for t in scan(self.root) if str(t) not in before]
        if not made:
            raise HttpError(500, "repo-factory new ran but no new task folder appeared under ROOT")
        return {"dir": str(made[0])}

    def policy_args(self, d: Path, policy: str | None) -> list[str]:
        """``--policy <repo>/policies/<x>`` for a policy the page offers; nothing (``repo-factory
        build``'s own default) when the request names none. A v5 policy asks nobody unless
        its ``interpret.mode`` is ``ask`` (binary rules are settings), and takes
        the task's budget as its cost cap."""
        if not policy:
            return ["--owner", "queue"]
        if policy not in POLICIES:
            raise HttpError(400, f"policy must be one of {', '.join(POLICIES)}")
        from ..resources import POLICIES as BUNDLED  # an installed wheel has no repo

        path = (REPO / "policies" if (REPO / "policies").is_dir() else BUNDLED) / policy
        if not (path / "rules.yaml").is_file():
            raise HttpError(400, f"policy {policy} not found at {path}")
        import yaml

        rules = yaml.safe_load((path / "rules.yaml").read_text()) or {}
        args = ["--policy", str(path)]
        if rules.get("pipeline") != "v5":
            return args + ["--owner", "queue"]
        if self.harness == "fake":
            raise HttpError(400, "v5 has no fake harness: a v5 build would spend real money.")
        ask = (rules.get("interpret") or {}).get("mode") == "ask"
        args += ["--owner", "queue" if ask else "none"]
        from ..tasks import load_config

        try:
            budget = load_config(d).budget_usd
        except Exception:  # noqa: BLE001  a bad task.yaml: the build says
            budget = None
        if budget:
            args += ["--max-cost", f"{float(budget):g}"]
        return args

    def start(self, d: Path, policy: str | None = None) -> dict:
        return self.spawn(
            d, ["build", str(d), *self.policy_args(d, policy), "--harness", self.harness]
        )

    def answer(self, d: Path, qid: str, text: str) -> dict:
        if not re.fullmatch(r"[\w.-]+", qid or ""):
            raise HttpError(400, "no such question")
        # The question event can land a moment before its .question file: wait for it.
        st = load_state(d) or {}
        qdir = self.den(d) / "human"
        deadline = time.time() + 5
        while time.time() < deadline and not list(qdir.glob(f"*.{qid}.question")):
            time.sleep(0.1)
        return {
            "ok": True,
            "out": self.run(["answer", str(d), qid, text or ""], cwd=d),
            "task": st.get("task"),
        }

    def stop(self, d: Path) -> dict:
        if self.state(d)["state"] not in ("asking", "running"):
            raise HttpError(409, "nothing is running")  # build.pid may be stale
        pidf = self.den(d) / "build.pid"
        pid = None
        if pidf.exists():
            try:
                pid = int(pidf.read_text().strip())
            except ValueError:
                pid = None
        own = self.procs.get(str(d))
        if pid is None and own and own[0].poll() is None:
            pid = own[0].pid
        if pid is None or not is_build_of(pid, d):
            raise HttpError(409, "nothing is running")  # gone, or the pid was recycled
        proc.signal_tree(pid, signal.SIGTERM)  # the build and its workers
        return {"ok": True, "pid": pid}

    def verdict(self, d: Path, verdict: str, note: str) -> dict:
        note = (note or "").strip()
        if verdict in ("accept", "reject"):
            return {
                "ok": True,
                "out": self.run(["verdict", "--task", str(d), verdict, note], cwd=d),
            }
        if verdict == "edit":
            if not note:
                raise HttpError(400, "say what to change in the note")
            # repo-factory revise records the edit verdict itself, then runs another round.
            return self.spawn(d, ["revise", str(d), note, "--harness", self.harness])
        raise HttpError(400, "verdict must be accept, reject or edit")

    # ---- the loop (repo_factory/loop; reads the ledger, acts through `repo-factory loop`) ---------------
    def loop(self):
        from ..loop.daemon import Loop
        from ..settings.store import Config

        return Loop(Config(self.loop_home))

    # ---- Settings › Building › Models and Settings › Spend (the loop's loop.yaml) --------------------
    def loop_cfg(self):
        from ..settings.store import Config

        return Config(self.loop_home)

    def build_env(self) -> dict | None:
        """A build started here runs with the stage models chosen in Settings › Building › Models, as
        the loop's builds do."""
        from ..models import build_env
        from ..settings.schema import describe, refuses_builds

        try:
            cfg = self.loop_cfg()
        except Exception as e:  # noqa: BLE001 - unreadable: say so, start nothing
            raise HttpError(
                400, f"loop.yaml can't be read ({type(e).__name__}); fix it, then start the build"
            ) from None
        bad = refuses_builds(cfg.problems)
        if bad:
            raise HttpError(400, f"{describe(bad)}; fix loop.yaml, then start the build")
        return build_env(cfg)

    def models(self) -> dict:
        from .. import models

        cfg = self.loop_cfg()
        return {
            "catalog": models.catalog(),
            "roles": models.roles(cfg),
            "file": str(cfg.layout.settings),
            "policy": Path(cfg["build"]["policy"]).name,
        }

    def set_model(self, role: str, model) -> dict:
        """``model`` empty or null: back to the role's default."""
        from .. import models
        from ..settings import write as settings

        try:
            path = models.role_path(role)
        except KeyError:
            raise HttpError(400, f"no such role: {role or '(none)'}")
        try:
            name = models.check_model(model)
        except ValueError as e:
            raise HttpError(400, str(e))
        settings.write(
            self.loop_cfg().layout.settings, path, settings.UNSET if name is None else name
        )
        return self.models()

    def set_caps(self, body: dict) -> dict:
        """The build caps: ``daily_usd`` and ``build_max_usd``, each USD or null (no cap);
        ``stage_caps``: ``{stage: USD or null}`` (null: back to the policy's, which is no cap
        unless the policy sets one)."""
        from ..settings import write as settings
        from ..v5.common import REVISE_STAGES, STAGES

        todo = {k: v for k, v in body.items() if k in settings.CAPS}
        stages = body.get("stage_caps")
        if not todo and stages is None:
            raise HttpError(400, "nothing to set: send daily_usd, build_max_usd or stage_caps")
        if stages is not None and not isinstance(stages, dict):
            raise HttpError(400, "stage_caps is an object of stage to USD (or null)")
        bad = sorted(set(stages or {}) - set(STAGES) - set(REVISE_STAGES))
        if bad:
            raise HttpError(400, f"no such stage: {', '.join(bad)}")
        try:
            values = {k: settings.usd_or_none(v, k) for k, v in todo.items()}
            per = {k: settings.usd_or_none(v, f"stage_caps.{k}") for k, v in (stages or {}).items()}
        except ValueError as e:
            raise HttpError(400, str(e))
        home = self.loop_cfg().home
        for k, v in values.items():
            settings.write(paths.Layout(home).settings, settings.CAPS[k], v)
        for k, v in per.items():
            settings.write(
                paths.Layout(home).settings,
                ("build", "stage_caps", k),
                settings.UNSET if v is None else v,
            )
        cfg = self.loop_cfg()
        return {
            "daily_usd": cfg["daily_usd"],
            "build_max_usd": cfg["build"].get("max_usd"),
            "caps": self.caps(cfg),
        }

    def caps(self, cfg=None) -> dict:
        """Every spend cap a build runs under, for Settings › Spend: ``daily_usd``, the
        build's ``max_usd`` (the loop's, else the policy's), ``stage_caps`` (each v5 stage:
        USD or null for none, with where it is set), and each session's ``max_usd``,
        ``max_turns`` and ``timeout_s`` (the runaway limits). Null everywhere is the default:
        spend is uncapped."""
        from ..v5 import common, pipeline, readiness

        cfg = cfg or self.loop_cfg()
        b = cfg["build"]
        out = {
            "daily_usd": cfg.get("daily_usd"),
            "build_max_usd": b.get("max_usd"),
            "policy": Path(b["policy"]).name,
            "file": str(cfg.layout.settings),
        }
        try:
            rules = pipeline.load_rules(Path(b["policy"]).expanduser())
        except Exception as e:  # noqa: BLE001 - a broken policy: say so
            return {**out, "error": f"the policy can't be read: {e}"}
        try:
            caps = common.stage_caps(rules, b.get("stage_caps") or {})
        except ValueError as e:
            caps, out["error"] = common.stage_caps(rules), str(e)
        loop_caps = b.get("stage_caps") or {}
        policy_caps = (rules.get("build") or {}).get("stage_caps") or {}
        out["policy_max_usd"] = pipeline.default_cap(rules)
        out["stage_caps"] = [
            {
                "stage": s,
                "usd": caps.get(s),
                "set_in": "loop.yaml"
                if loop_caps.get(s) is not None
                else "policy"
                if s in caps and policy_caps.get(s) is not None
                else None,
            }
            for s in rules["stages"]
        ]
        sessions = []
        for name in ("build", "verify", "repair"):
            c = rules.get(name) or {}
            sessions.append(
                {
                    "session": name,
                    "max_usd": common.usd_or_none(c.get("max_usd")),
                    "max_turns": c.get("max_turns"),
                    "timeout_s": c.get("timeout_s"),
                }
            )
        for part in ("security", "checks", "repair"):
            c = readiness.cfg(SimpleNamespace(rules=rules), part)
            sessions.append(
                {
                    "session": f"readiness.{part}",
                    "max_usd": common.usd_or_none(c.get("max_usd")),
                    "max_turns": c.get("max_turns"),
                    "timeout_s": c.get("timeout_s"),
                }
            )
        out["sessions"] = sessions
        out["uncapped"] = (
            out["daily_usd"] is None
            and out["build_max_usd"] is None
            and out["policy_max_usd"] is None
            and not caps
            and all(s["max_usd"] is None for s in sessions)
        )
        return out

    # ---- what repo-factory is doing: one read ----------------------------------------------
    ACTIVITY_TTL = 2.0

    def activity(self, q: dict) -> dict:
        """``GET api/activity``: the loop's steps, the builds and its detached jobs; cached
        ``ACTIVITY_TTL`` (the header, the sheet and every line on a page share one read)."""
        from urllib.parse import urlencode

        keep = {
            k: str(q[k])
            for k in ("subject", "kind", "limit", "all", "hours", "run")
            if q.get(k) not in (None, "")
        }
        key = urlencode(sorted(keep.items()))
        hit = getattr(self, "_activity", {}).get(key)
        now = time.time()
        if hit and now - hit[0] < self.ACTIVITY_TTL:
            return hit[1]
        out = self._activity_read(keep, key)
        cache = getattr(self, "_activity", None)
        if cache is None:
            cache = self._activity = {}
        if len(cache) > 32:
            cache.clear()
        cache[key] = (now, out)
        return out

    def _activity_read(self, keep: dict, key: str) -> dict:
        from ..loop import activity as A

        lp = self.loop()
        running, recent = A.steps(lp.ledger)
        running += A.detached(lp, self.key)
        subject = keep.get("subject")
        if subject:
            k, _, i = subject.partition(":")
            running = [
                r
                for r in running
                if (r.get("subject") or {}).get("kind") == k
                and str((r.get("subject") or {}).get("id")) == i
            ]
            recent = [
                r
                for r in recent
                if (r.get("subject") or {}).get("kind") == k
                and str((r.get("subject") or {}).get("id")) == i
            ]
        if keep.get("kind"):
            running = [r for r in running if r["kind"] == keep["kind"]]
            recent = [r for r in recent if r["kind"] == keep["kind"]]
        limit = max(1, min(int(keep.get("limit") or 50), 500))
        recent = sorted(recent, key=lambda r: r.get("finished") or "", reverse=True)[:limit]
        building = sum(1 for r in running if r["kind"] == "build")
        quiet = None
        if not running:
            quiet = "Nothing is running."
            if building == 0 and lp.ledger.all("queued"):
                quiet += " Builds are queued."
        return {
            "seq": 0,
            "running": running,
            "waiting": [],
            "next": [],
            "recent": recent,
            "folded": [],
            "failing": [],
            "incidents": [],
            "counts": {
                "running": len(running),
                "waiting": 0,
                "failed_recent": sum(1 for r in recent if r["state"] == "failed"),
                "failing": 0,
                "building": building,
                "incidents": 0,
            },
            "quiet": quiet,
            "scheduler": False,
            "paused": lp.cfg.paused(),
            "now": time.time(),
            "health": None,
        }

    def activity_stop(self, body: dict) -> dict:
        """``POST api/activity/stop {id}``: the owner's Stop, where the run can stop: a build by
        its Stop round; a detached job of the loop by SIGTERM (SIGKILL after 20 s)."""
        from ..loop import activity as A

        rid = str(body.get("id") or "")
        src, _, rest = rid.partition(":")
        self._activity = {}
        if src == "build" and rest:
            lp = self.loop()
            idea = next(
                (
                    i
                    for i in lp.ledger.all("building")
                    if i.get("task_dir") and self.key(Path(i["task_dir"])) == rest
                ),
                None,
            )
            if idea is None:
                raise HttpError(409, "this build is not running")
            return {
                **self.loop_act("stop-round", {"id": idea["id"]}),
                "stopping": True,
                "words": "Stopping its round: the workers stop; Resume build goes on.",
            }
        if src == "pid" and rest.isdigit():
            known = {r["id"] for r in A.detached(self.loop(), self.key)}
            if rid not in known:
                raise HttpError(409, "this job is not running")
            A.stop_pid(int(rest))
            return {
                "ok": True,
                "stopping": True,
                "words": "Stopping it: asked to end now, ended in 20 s if it does not.",
            }
        raise HttpError(404, "no such run, or it cannot be stopped")

    def prompts(self) -> dict:
        """The build's prompts, the house rules and the settings shown beside them."""
        from .. import custom_prompts as cp

        st = cp.store()
        return {
            "prompts": cp.registry(st),
            "repos": {"repo_factory": {"ok": True, "count": len(cp.REGISTRY)}},
            "groups": [cp.PITCH, cp.BUILD, cp.MAINT],
            "house_rules": cp.house_rules_view(st),
            "settings": cp.settings(self.loop_home),
            "defaults_only": cp.defaults_only(),
            "store": str(st.root),
        }

    def prompt_get(self, repo: str, pid: str) -> dict:
        from .. import custom_prompts as cp

        if repo != cp.REPO:
            raise HttpError(404, f"no repo {repo!r}: repo_factory")
        return cp.detail(pid)

    def prompt_post(self, repo: str, pid: str, body: dict, act: str = "") -> dict:
        """A save (``act`` empty), ``reset`` or ``restore``."""
        from .. import custom_prompts as cp

        if repo != cp.REPO:
            raise HttpError(404, f"no repo {repo!r}: repo_factory")
        return {"": cp.save, "reset": cp.reset, "restore": cp.restore}[act](pid, body)

    def prompt_version(self, repo: str, pid: str, n: int, note: dict | None = None) -> dict:
        """Version ``n`` of a prompt (GET), or its note set (``note``: the POST body)."""
        from .. import custom_prompts as cp

        if repo != cp.REPO:
            raise HttpError(404, f"no repo {repo!r}: repo_factory")
        return cp.set_note(pid, n, note) if note is not None else cp.version_view(pid, n)

    def prompt_preview(self, body: dict) -> dict:
        from .. import custom_prompts as cp

        repo, pid, subject = (
            body.get("repo") or cp.REPO,
            str(body.get("id") or ""),
            body.get("subject"),
        )
        if repo != cp.REPO:
            raise HttpError(404, f"no repo {repo!r}: repo_factory")
        e = cp.entry(pid)
        values, about = self.prompt_values(e, subject)
        return cp.preview(pid, values, about)

    def prompt_values(self, e, subject) -> tuple[dict, dict]:
        """A preview's placeholder values from a real item: an idea (``i…``, the loop's
        ledger, read-only) or a build (its key or folder). Read-only; anything the item
        can't give shows as a described stand-in."""
        from ..v5 import scope as scope_mod

        if isinstance(subject, dict):
            subject = subject.get("id") or subject.get("key") or subject.get("dir")
        subject = str(subject or "").strip()
        if not subject:
            return {}, {}
        idea, d = None, None
        if IDEA_ID.fullmatch(subject):
            idea = self.loop_idea(subject)
            if idea is None:
                raise HttpError(404, f"no idea {subject} in the loop's ledger")
            if idea.get("task_dir") and Path(idea["task_dir"]).is_dir():
                d = Path(idea["task_dir"])
        else:
            d = next(
                (t for t in scan(self.root) if self.key(t) == subject or str(t) == subject), None
            )
            if d is None:
                raise HttpError(404, f"no build {subject!r} under ROOT")
        about = {
            "kind": "idea" if idea else "build",
            "id": subject,
            **({"build": self.key(d)} if d is not None else {}),
        }
        spec_md = (
            (d / "spec.md").read_text(errors="replace")
            if d is not None and (d / "spec.md").is_file()
            else None
        )
        idea_text = (
            spec_md
            or ((idea or {}).get("pitch") or {}).get("brief")
            or (idea or {}).get("text")
            or ""
        )
        scope_md = self.latest_scope(d) if d is not None else None
        spec = scope_mod.spec_text(idea_text, scope_md) if idea_text else None
        vals: dict = {}
        if e.id == "pitch" and idea is not None:
            from ..loop.pitch import idea_block

            vals["IDEA"] = idea_block(idea)
        elif e.id == "scope":
            from ..custom_prompts import effective, sections

            vals["idea"] = idea_text.strip() or None
            vals["judge"] = sections(effective("scope", noted=False).text).get("judge", "").strip()
        elif e.id in ("reproduce", "fix"):
            vals["issue"] = spec_md
            vals["product"] = d.name if d is not None else None
        elif "spec" in e.placeholders:
            vals["spec"] = spec.strip() if spec else None
        return vals, about

    def loop_idea(self, iid: str) -> dict | None:
        import sqlite3

        from ..loop.ledger import Ledger
        from ..settings.store import Config

        db = Config(self.loop_home).db
        if not db.exists():
            return None
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        try:
            r = c.execute("SELECT * FROM ideas WHERE id = ?", (iid,)).fetchone()
        finally:
            c.close()
        return Ledger._row(r) if r else None

    @staticmethod
    def latest_scope(d: Path) -> str | None:
        rounds = (
            sorted(
                (p for p in (d / ".den" / "v5").glob("r*") if p.name[1:].isdigit()),
                key=lambda p: int(p.name[1:]),
            )
            if (d / ".den" / "v5").is_dir()
            else []
        )
        for r in reversed(rounds):
            f = r / "SCOPE.md"
            if f.is_file():
                return f.read_text(errors="replace")
        return None

    def spend_breakdown(self, now: float | None = None, builds: int = 30, days: int = 14) -> dict:
        """Settings › Spend's breakdown (UI-v4.md 2.5) over the loop's ledger and the task
        folders, read the way ``spend_summary`` (the header and Today) reads them, so every
        column sums to its total. By build: each loop idea with spend, its build's model calls
        (the task's ``.den/ledger.jsonl``) and the loop's own calls for it (pitch, checks,
        Claude Code sessions, kept as ``chat`` in older ledgers), this week and in all; the ``builds`` most recent, the rest as one ``other`` row,
        and the loop's calls tied to no idea as ``unattributed``. By day: the last ``days``
        local days, builds and the loop's own calls."""
        from ..loop.ledger import day_start, spend_summary, week_start

        led = self.loop().ledger
        now = now or time.time()
        week = week_start(now)
        starts = [day_start(now - i * 86400) for i in range(days)]  # newest first
        ideas = {i["id"]: i for i in led.all()}
        with led.db() as c:
            own = c.execute("SELECT idea, ts, kind, usd FROM spend").fetchall()
        calls: dict[str, list[tuple[float, float]]] = {}
        for iid, i in ideas.items():
            p = Path(i["task_dir"]) / ".den" / "ledger.jsonl" if i.get("task_dir") else None
            if p is None or not p.exists():
                continue
            for line in p.read_text(errors="replace").splitlines():
                try:
                    r = json.loads(line)
                    if not isinstance(r, dict) or not r.get("usd"):
                        continue  # a line that is not a call's record
                    call = (float(r.get("ts") or 0), float(r["usd"]))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                calls.setdefault(iid, []).append(call)
        rows: dict[str, dict] = {}

        def row(iid: str) -> dict:
            if iid not in rows:
                i = ideas[iid]
                rows[iid] = {
                    "idea": iid,
                    "title": i.get("title") or iid,
                    "product": (i.get("pitch") or {}).get("product"),
                    "state": i.get("state"),
                    "key": self.key(Path(i["task_dir"])) if i.get("task_dir") else None,
                    "builds_usd": 0.0,
                    "loop_usd": 0.0,
                    "week_usd": 0.0,
                    "last": 0.0,
                }
            return rows[iid]

        unattributed = {"week_usd": 0.0, "total_usd": 0.0}
        day = [{"start": s, "builds": 0.0, "loop": 0.0, "chat": 0.0} for s in starts]

        def by_day(ts: float, usd: float, what: str, chat: bool = False) -> None:
            for d in day:  # newest first: the day it falls in
                if ts >= d["start"]:
                    d[what] += usd
                    d["chat"] += usd if chat else 0.0
                    return

        for iid, ts, kind, usd in own:
            usd = float(usd or 0)
            by_day(ts, usd, "loop", kind == "chat")
            if iid in ideas:
                r = row(iid)
                r["loop_usd"] += usd
                r["week_usd"] += usd if ts >= week else 0.0
                r["last"] = max(r["last"], ts)
            else:
                unattributed["total_usd"] += usd
                unattributed["week_usd"] += usd if ts >= week else 0.0
        for iid, xs in calls.items():
            r = row(iid)
            for ts, usd in xs:
                by_day(ts, usd, "builds")
                r["builds_usd"] += usd
                r["week_usd"] += usd if ts >= week else 0.0
                r["last"] = max(r["last"], ts)
        ordered = sorted(rows.values(), key=lambda r: -r["last"])
        shown, rest = ordered[:builds], ordered[builds:]
        rnd = lambda v: round(v, 4)  # noqa: E731
        for r in ordered:
            r["total_usd"] = rnd(r["builds_usd"] + r["loop_usd"])
            r["builds_usd"], r["loop_usd"], r["week_usd"] = (
                rnd(r["builds_usd"]),
                rnd(r["loop_usd"]),
                rnd(r["week_usd"]),
            )
        return {
            "now": now,
            "week_start": week,
            "summary": spend_summary(led, now),
            "builds": shown,
            "other": {
                "ideas": len(rest),
                "week_usd": rnd(sum(r["week_usd"] for r in rest)),
                "total_usd": rnd(sum(r["total_usd"] for r in rest)),
            },
            "unattributed": {k: rnd(v) for k, v in unattributed.items()},
            "days": [
                {
                    "day": time.strftime("%Y-%m-%d", time.localtime(d["start"])),
                    "start": d["start"],
                    "builds": rnd(d["builds"]),
                    "loop": rnd(d["loop"]),
                    "chat": rnd(d["chat"]),
                    "total": rnd(d["builds"] + d["loop"]),
                }
                for d in day
            ],
            "caps": self.caps(),
            "projects": self.project_spend(led, now),
        }

    @staticmethod
    def project_spend(led, now: float) -> list[dict]:
        """Settings › Spend's Projects rows: each project's own calls (evolve, charter, its
        fixes' sessions where recorded) this month and in all, from ``spend.project``, and the
        stats' build and fix spend."""
        from ..projects.record import Projects

        try:
            ps = Projects(led).all()
        except Exception:  # noqa: BLE001 - Spend draws without them
            return []
        month = App.month_start(now)
        out = []
        with led.db() as c:
            for p in ps:
                kinds = {
                    r[0]: round(float(r[1]), 4)
                    for r in c.execute(
                        "SELECT kind, SUM(usd) FROM spend WHERE project = ? GROUP BY kind",
                        (p["id"],),
                    )
                }
                mon = float(
                    c.execute(
                        "SELECT COALESCE(SUM(usd), 0) FROM spend WHERE project = ? AND ts >= ?",
                        (p["id"], month),
                    ).fetchone()[0]
                )
                st = (p.get("stats") or {}).get("spend") or {}
                out.append(
                    {
                        "id": p["id"],
                        "name": p["name"],
                        "kinds": kinds,
                        "month_usd": round(mon, 4),
                        "all_usd": round(sum(kinds.values()), 4),
                        "stats_month_usd": st.get("month"),
                        "stats_all_usd": st.get("all"),
                    }
                )
        return sorted(out, key=lambda r: -r["all_usd"])

    # ---- maintenance (MAINTAIN.md §8): read from GitHub, kept maintain.cache_s, never stored --
    def maint_reader(self):
        """The page's GitHub reads (``maintain.github.Reader``), made once per ``gh`` command
        and cache time, kept in ``<home>/maintain/reads.json`` for that identity."""
        from ..github_user import stale
        from ..maintain.github import Reader
        from ..maintain.poll import gh_for

        lp = self.loop()
        if getattr(lp, "_gh", None) is not None and stale(lp._gh):
            lp._gh = None  # signed out, or signed in again: re-read
        gh = gh_for(lp)
        key = (
            tuple(gh.cmd),
            gh.credential,
            getattr(gh, "login_name", None),
            str(getattr(gh, "env_file", None) or ""),  # the token file, or the sign-in's
            float(lp.cfg["maintain"].get("cache_s") or 0),
        )
        if getattr(self, "_maint", None) is None or self._maint[0] != key:
            self._maint = (
                key,
                Reader(
                    gh,
                    key[-1],
                    path=lp.cfg.layout.maintain / "reads.json",
                    ns=json.dumps(list(key[:4])),
                ),
            )
        return self._maint[1]

    def maint_target(self, repo: str | None = None, idea: str | None = None) -> dict:
        """A repo maintenance looks at, by name or by the idea that published it; anything
        else is a 404, so no request names an arbitrary repository."""
        from ..maintain.poll import targets

        for t in targets(self.loop()):
            if (repo and t["repo"] == repo) or (idea and t["idea"] == idea):
                return t
        raise HttpError(404, "not a maintained repository")

    @staticmethod
    def month_start(now: float | None = None) -> float:
        t = time.localtime(now or time.time())
        return time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1))

    def maint_spend(self, idea: str | None, since: float) -> dict:
        from ..loop.ledger import MAINTAIN_KINDS

        # tokens: {"in", "out"} over the rows that carry them; None when no row does (rows
        # from before tokens were recorded have NULL token columns)
        q = (
            f"SELECT kind, COUNT(*), COALESCE(SUM(usd), 0), "
            f"SUM(CASE WHEN in_tokens IS NOT NULL OR out_tokens IS NOT NULL THEN 1 ELSE 0 END),"
            f" COALESCE(SUM(in_tokens), 0), COALESCE(SUM(out_tokens), 0) FROM spend "
            f"WHERE ts >= ? AND kind IN ({','.join('?' * len(MAINTAIN_KINDS))})"
        )
        args: list = [since, *MAINTAIN_KINDS]
        if idea:
            q += " AND idea = ?"
            args.append(idea)
        with self.loop().ledger.db() as c:
            rows = c.execute(q + " GROUP BY kind", args).fetchall()
        by = {
            k: {
                "calls": n,
                "usd": round(float(u), 4),
                "tokens": {"in": int(ti), "out": int(to)} if nt else None,
            }
            for k, n, u, nt, ti, to in rows
        }
        tokens = None
        for x in by.values():
            if x["tokens"] is not None:
                tokens = {
                    "in": (tokens or {}).get("in", 0) + x["tokens"]["in"],
                    "out": (tokens or {}).get("out", 0) + x["tokens"]["out"],
                }
        return {
            "usd": round(sum(x["usd"] for x in by.values()), 4),
            "tokens": tokens,
            "by_kind": by,
        }

    def maintain_box(self, idea_id: str) -> dict:
        """The build page's Maintenance box: the policy, the latest release, CI on main, the
        open issues by label and the pull requests waiting, each with the GitHub search it
        came from, and this month's maintenance spend for the build."""
        from ..maintain import policy as P
        from ..maintain.github import BOTS, PENDING, GitHubError, Repo
        from ..maintain.poll import counts

        t = self.maint_target(idea=idea_id)
        full, rd = t["repo"], self.maint_reader()
        repo = Repo(rd.gh, full)
        out: dict = {"repo": full, "url": f"https://github.com/{full}", "errors": []}
        fns = {
            "policy": lambda: P.load(rd.gh, full),
            "open": lambda: repo.issues(state="open"),
            "release": repo.latest_release,
            "ci": lambda: repo.ci_status("main"),
        }
        rd.ask({(full, k): fn for k, fn in fns.items()})  # all four at once

        def read(what, default=None):
            try:
                v = rd.cached((full, what), fns[what], default=PENDING, wait=0)
            except (GitHubError, P.PolicyError) as e:
                out["errors"].append(f"{what}: {e}")
                return default
            if v is PENDING:
                out["errors"].append(f"{what}: still asking GitHub")
                return default
            return v

        pol = read("policy")
        items = read("open", []) or []
        out["release"] = read("release")
        out["ci"] = read("ci")
        out["policy"] = pol and {
            "status": pol["status"],
            "trigger": pol["revise"]["trigger"],
            "auto_go": pol["revise"]["auto_go"],
            "factory_patch": pol["merge"]["factory_patch"],
            "dependabot": pol["merge"]["dependabot"],
            "on_merge": pol["release"]["on_merge"],
            "note": pol["spend"]["note"],
        }
        out["counts"] = counts(items)
        prs = [i for i in items if i["is_pr"]]
        keep = ("number", "title", "url", "labels", "draft")
        out["factory_prs"] = [{k: p[k] for k in keep} for p in prs if "repo-factory" in p["labels"]]
        out["dependabot"] = [{k: p[k] for k in keep} for p in prs if p["author"] in BOTS]
        out["claimed"] = [
            {
                "number": i["number"],
                "title": i["title"],
                "url": i["url"],
                "by": "repo-factory"
                if "claim:repo-factory" in i["labels"]
                else "an agent"
                if "claim:agent" in i["labels"]
                else ", ".join(i["assignees"]),
            }
            for i in items
            if not i["is_pr"]
            and (i["assignees"] or any(x.startswith("claim:") for x in i["labels"]))
        ]
        out["spend"] = self.maint_spend(idea_id, self.month_start())
        from ..maintain import doctor
        from ..maintain.github import Cache

        rec = Cache(self.loop().cfg.home).repo(full)
        out["polled"] = rec.get("last_poll")
        out["doctor"] = doctor.summary(rec)
        return out

    def maintain_summary(self) -> dict:
        """Settings › Publishing › Maintenance: the ``maintain:`` settings, the identity in use, every
        maintained repo with its policy (from GitHub, cached) and what the last poll saw, and
        this month's maintenance spend."""
        from ..maintain import current, doctor, mergepolicy, pauses, recheck
        from ..maintain import policy as P
        from ..maintain.github import PENDING, Cache, GitHubError
        from ..maintain.poll import gh_for, targets
        from ..maintain.rows import overlay

        lp = self.loop()
        m = lp.cfg["maintain"]
        ts = targets(lp)
        rd = self.maint_reader() if ts else None
        cache = Cache(lp.cfg.home)
        repos = []
        mine = ts
        # every repo's policy asked for at once, in the background; known ones come back now
        pols = {t["repo"]: (lambda r=t["repo"]: P.load(rd.gh, r)) for t in mine}
        if rd is not None:
            rd.ask(
                {
                    **{(r, "policy"): fn for r, fn in pols.items()},
                    ("", "identity"): self.maint_identity_read,
                }
            )
        for t in mine:
            rec = cache.repo(t["repo"])
            err = None
            try:
                pol = rd.cached((t["repo"], "policy"), pols[t["repo"]], default=PENDING, wait=0)
                if pol is PENDING:  # not read yet: the poll's last reading
                    raise P.PolicyError("still reading the policy from GitHub")
                policy = {
                    "status": pol["status"],
                    "trigger": pol["revise"]["trigger"],
                    "factory_patch": pol["merge"]["factory_patch"],
                    "merge_kinds": list(pol["merge"].get("kinds") or []),
                    "max_lines": pol["merge"].get("max_lines"),
                    "dependabot": pol["merge"]["dependabot"],
                    "on_merge": pol["release"]["on_merge"],
                    "note": pol["spend"]["note"],
                }
            except (GitHubError, P.PolicyError) as e:
                policy, err = rec.get("policy"), str(e)
            held = [x for x in rec.get("rows") or [] if x.get("kind") == "agents_md"]
            repos.append(
                {
                    "repo": t["repo"],
                    "idea": t["idea"],
                    "product": t["product"],
                    "policy": policy,
                    "error": err or rec.get("error"),
                    "agents_md": overlay(lp.cfg.home, [{**held[0], "repo": t["repo"]}])[0]
                    if held
                    else None,
                    "doctor": doctor.summary(rec),
                    "recheck": recheck.job_state(lp.cfg.home, t["repo"]),
                    "queue": rec.get("queue"),
                    "prs": current.summary(rec),
                    "merge_policy": mergepolicy.job_state(lp.cfg.home, t["repo"]),
                    "last_poll": rec.get("last_poll"),
                    "counts": rec.get("counts"),
                    # maintenance paused on its project, in repo-factory (maintain/pauses.py)
                    "paused": pauses.words(t.get("paused")),
                    "edit_url": f"https://github.com/{t['repo']}/blob/main/.github/repo-factory.yml",
                }
            )
        cmd = m.get("gh_command")
        identity = (
            {"kind": "command", "command": cmd if isinstance(cmd, str) else " ".join(cmd)}
            if cmd
            else {
                "kind": "owner",
                "command": " ".join(lp.cfg["publish"].get("gh_command") or ["gh"]),
            }
        )
        from ..maintain.identity import token_refused, token_skipped

        identity["token_file"] = m.get("token_env_file")
        # GitHub refused it (expired or revoked): the page offers Remove this token setting
        identity["refused_token"] = token_refused(lp.cfg)
        identity["token_skipped"] = bool(token_skipped(lp.cfg))
        if identity["refused_token"]:  # what the house dialog says before it goes
            from ..settings.write import SENSITIVE

            identity["remove_confirm"] = SENSITIVE["maintain.token_env_file"]
        if identity["token_file"] and not identity["token_skipped"]:
            identity["kind"] = "token"
        elif identity["kind"] == "owner" and (g_conn := getattr(lp.gh, "connected_as", None)):
            identity.update(kind="connection", login=g_conn)
        g = rd.gh if rd is not None else gh_for(lp)
        if g.credential == "app":
            identity["kind"] = "app"
            identity["bot_login"] = g.login_name
        identity["mode"] = m.get("identity") or "auto"
        identity["github"] = self.maint_identity()
        return {
            "settings": {
                k: m.get(k)
                for k in (
                    "enabled",
                    "poll_s",
                    "cache_s",
                    "triage_model",
                    "triage_max_per_poll",
                    "repos",
                    "gh_command",
                    "token_env_file",
                    "identity",
                    "concurrency",
                    "max_usd",
                    "net",
                    "net_hosts",
                    "lesson_label",
                    "doctor_days",
                    "refresh_max",
                    "identity_fallback_to_login",
                )
            },
            "identity": identity,
            "repos": repos,
            "last_poll": lp.ledger.meta("maintain_last_poll"),
            "spend": self.maint_spend(None, self.month_start()),
            "file": str(lp.cfg.layout.settings),
        }

    def maintain_lessons(self) -> dict:
        """Lessons from user reports: the closed issues
        labelled ``lesson`` on the maintained repos (one search, kept ``maintain.cache_s``) by
        kind and how many were read, and the lessons they produced in the candidate file, a
        probation one "waiting for the bench"."""
        from ..lessons import postmortem, store
        from ..maintain import identity
        from ..maintain.github import PENDING
        from ..maintain.poll import targets

        lp = self.loop()
        repos = [t["repo"] for t in targets(lp)]
        out: dict = {
            "repos": len(repos),
            "issues": None,
            "by_kind": {},
            "lessons": [],
            "errors": [],
            "file": str(store.candidate_path()),
        }
        if repos:
            rd = self.maint_reader()
            try:
                items = rd.cached(
                    ("", "lesson-issues"),
                    lambda: postmortem.lesson_issues(rd.gh, repos),
                    default=PENDING,
                )
                if items is PENDING:  # not back yet: the next open has them
                    out["errors"].append("still asking GitHub for the lesson issues")
                    items = []
                else:
                    out["issues"] = len(items)
                for i in items:
                    k = "security" if "security" in i["labels"] else "bug"
                    out["by_kind"][k] = out["by_kind"].get(k, 0) + 1
            except Exception as e:  # noqa: BLE001 - the page shows the rest
                out["errors"].append(identity.clean(str(e)))
        try:
            data = store.load(store.candidate_path())
        except Exception as e:  # noqa: BLE001
            data = store.empty()
            out["errors"].append(f"the candidate lessons file: {e}")
        word = {"probation": "waiting for the bench", "active": "in use", "retired": "retired"}

        def lesson(x: dict) -> dict:
            ref = str(x["born"].get("issue"))
            full, _, n = ref.partition("#")
            return {
                "id": x["id"],
                "text": x["text"],
                "applies_to": x["applies_to"],
                "status": x["status"],
                "state": word.get(x["status"], x["status"]),
                "issue": ref,
                "url": f"https://github.com/{full}/issues/{n}",
                "kind": x["born"].get("kind"),
                "miss": x["born"].get("miss"),
                "date": x["born"].get("date"),
            }

        # one lesson that cannot be read is listed as such
        out["lessons"] = rowsafe.each(postmortem.issue_lessons(data), lesson, "lesson")
        out["version"] = data["version"]
        return out

    def maint_identity(self) -> dict | None:
        """Who maintenance is on GitHub (``identity.describe``: the kind, login, scopes or
        fine-grained, expiry, maintained repos reached), kept ``maintain.cache_s``. Nothing
        here reads the token: ``gh`` does. Never asked yet and not back in a moment: says
        so, and the next open has it."""
        from ..maintain import identity

        rd = self.maint_reader()
        try:
            return rd.cached(("", "identity"), self.maint_identity_read, default=WAITING_ID)
        except Exception as e:  # noqa: BLE001 - the page still shows the rest
            return {"status": "warn", "line": f"could not ask GitHub: {identity.clean(str(e))}"}

    def maint_identity_read(self) -> dict:
        from ..maintain import identity
        from ..maintain.poll import targets

        lp = self.loop()
        return identity.describe(
            self.maint_reader().gh,
            owner_gh=lp.gh,
            owner_login=lp.cfg["publish"].get("owner"),
            repos=[t["repo"] for t in targets(lp)],
            home=lp.cfg.home,
        )

    def set_maintain(self, body: dict) -> dict:
        from ..settings import write as settings

        todo = {k: v for k, v in body.items() if k in settings.MAINTAIN}
        if not todo or len(todo) != len(body):
            raise HttpError(400, "send one or more of: " + ", ".join(settings.MAINTAIN))
        try:
            values = {k: settings.check(f"maintain.{k}", v) for k, v in todo.items()}
        except ValueError as e:
            raise HttpError(400, str(e))
        home = self.loop_cfg().home
        for k, v in values.items():
            settings.write(paths.Layout(home).settings, ("maintain", k), v)
        self._maint = None
        return self.maintain_summary()

    def maintain_act(self, body: dict) -> dict:
        """Today's buttons on a maintenance row: ``retry`` removes ``needs-you`` (repo-factory may try
        again), ``close`` closes the issue as not planned. Through the host's ``gh``."""
        from ..maintain.github import Cache, GitHubError, Repo

        t = self.maint_target(repo=str(body.get("repo") or ""))
        try:
            n = int(body.get("number"))
        except (TypeError, ValueError):
            raise HttpError(400, "number must be an issue number")
        what = body.get("what")
        rd = self.maint_reader()
        repo = Repo(rd.gh, t["repo"])
        try:
            if what == "retry":
                repo.set_labels(n, remove=["needs-you"])
                Cache(self.loop().cfg.home).drop_row(t["repo"], "needs_you", n)
            elif what == "close":
                repo.close(n, "not_planned")
                for k in ("needs_you", "untriaged"):
                    Cache(self.loop().cfg.home).drop_row(t["repo"], k, n)
            else:
                raise HttpError(400, "what must be retry or close")
        except GitHubError as e:
            raise HttpError(502, str(e))
        rd.forget(t["repo"])
        return {"ok": True, "repo": t["repo"], "number": n, "what": what}

    def maintain_issue_create(self, body: dict) -> dict:
        """Cut a ticket: one issue on a maintained repo, with labels
        from the scheme; ``go`` only when the repo's policy lets repo-factory fix issues (else it is
        dropped, and said). The body carries repo-factory's ticket marker, so a fix of it is never
        counted as a user-found lesson."""
        from ..maintain import policy as P
        from ..maintain.github import GitHubError, Repo
        from ..maintain.revise import TICKET

        t = self.maint_target(repo=str(body.get("repo") or ""))
        title = " ".join(str(body.get("title") or "").split())[:120]
        text = str(body.get("body") or "").strip()[:20000]
        from ..registry import TICKET_LABELS

        labels = [x for x in dict.fromkeys(body.get("labels") or []) if x in TICKET_LABELS]
        if not title or not text:
            raise HttpError(400, "an issue needs a title and a body")
        rd = self.maint_reader()
        dropped = False
        if "go" in labels:
            try:
                pol = P.load(rd.gh, t["repo"])
                ok = pol["status"] == "maintained" and pol["revise"]["trigger"] != "off"
            except (GitHubError, P.PolicyError):
                ok = False
            if not ok:
                labels.remove("go")
                dropped = True
        try:
            r = Repo(rd.gh, t["repo"]).create_issue(
                title, f"{text}\n\n_Opened from repo-factory by the owner._\n\n{TICKET}\n", labels
            )
        except GitHubError as e:
            raise HttpError(502, str(e))
        rd.forget(t["repo"])
        return {"repo": t["repo"], "labels": labels, "dropped_go": dropped, **r}

    def maintain_open_issues(self, repo: str) -> list[dict]:
        """A maintained repo's open issues and pull requests (cached ``maintain.cache_s``)."""
        from ..maintain.github import Repo

        t = self.maint_target(repo=repo)
        rd = self.maint_reader()
        return rd.cached(
            (t["repo"], "open"), lambda: Repo(rd.gh, t["repo"]).issues(state="open"), default=[]
        )

    def maintain_merge_policy(self, repo: str, on: bool, kinds: list, origin: str = "ui") -> dict:
        """The owner's switch (registry ``maintain_merge_policy``): one pull request on the
        product's ``.github/repo-factory.yml`` turning ``merge.factory_patch`` on (``ci-green``, for
        ``kinds``) or off, opened in the background; the owner merges it."""
        from ..maintain import mergepolicy

        full = self.maint_target(repo=" ".join(str(repo or "").split()))["repo"]
        try:
            mergepolicy.start(self.loop(), full, bool(on), kinds or ["bug", "docs"], by=origin)
        except mergepolicy.PolicyPRError as e:
            raise HttpError(409, str(e))
        return {
            "repo": full,
            "say": (
                f"Opening the policy pull request for {full} in the background: "
                + (
                    "repo-factory merges its own fixes when CI is green"
                    if on
                    else "repo-factory stops merging its own fixes"
                )
                + " once you merge it on GitHub. repo-factory never merges it itself."
            ),
        }

    def maintain_recheck(self, repo: str, origin: str = "ui") -> dict:
        """Re-check (registry ``maintain_recheck``): main's head, the drift doctor and the
        project's drift of one maintained repo, read again in the background (reads only); its
        state is on the repo's drift row."""
        from ..maintain import recheck

        k = " ".join(str(repo or "").split())
        try:
            r = recheck.start(self.loop(), k, by=origin)
        except recheck.RecheckError as e:
            raise HttpError(409, str(e))
        if not r["started"]:
            return {
                "repo": r["repo"],
                "say": f"{r['repo']} is being checked already (since "
                f"{time.strftime('%H:%M', time.localtime(r.get('since') or 0))}); its row "
                "shows the result.",
            }
        return {
            "repo": r["repo"],
            "say": f"Checking {r['repo']} again in the background: main's "
            "head, AGENTS.md against the tree and the README, and the project's drift. Its "
            "row shows the result in a few seconds.",
        }

    @staticmethod
    def fix_of(d: Path) -> dict | None:
        """A maintenance fix's task (``loop.json`` kind ``fix``), else None."""
        try:
            j = json.loads((Path(d) / "loop.json").read_text())
        except (OSError, ValueError):
            return None
        return j if isinstance(j, dict) and j.get("kind") == "fix" else None

    def fix_status(self, d: Path, s: dict, fix: dict, v5: dict) -> tuple[str, str]:
        """A fix row's status and reason: ``running``; ``fixed`` (its pull request waits for
        the owner on GitHub); ``closed`` once a later poll no longer sees that pull request
        open (merged or closed: GitHub says which); ``tried`` (an attempt ended without one:
        the issue's comments say why, and repo-factory tries again or labels it ``needs-you``). From
        the fix's result and the poll's cache: never a GitHub call."""
        from ..maintain.github import Cache
        from ..maintain.revise import result

        if s["state"] in ("running", "asking"):
            return "running", v5.get("label") or "Fixing"
        res = result(d) or {}
        if res.get("outcome") == "pr":
            rec = Cache(self.loop().cfg.home).repo(fix.get("key") or fix.get("repo") or "")
            open_prs = {
                r.get("number")
                for r in rec.get("rows") or []
                if r["kind"] in ("factory_pr", "factory_pr_draft", "pr_ci")
            }
            if (rec.get("last_poll") or 0) > (res.get("finished") or 0) + 1 and res.get(
                "pr"
            ) not in open_prs:
                return "closed", f"PR #{res.get('pr')} is merged or closed on GitHub"
            return "fixed", f"PR #{res.get('pr')} waits for your review on GitHub"
        if res.get("outcome") == "cannot_reproduce":
            return "tried", f"Could not reproduce it: {res.get('why') or ''}".strip()
        if res.get("outcome") == "skipped":
            return "closed", f"Not started: {res.get('why') or 'nothing to do'}"
        if res.get("outcome") == "held":
            return (
                "tried",
                "Held, not counted as an attempt: "
                f"{(res.get('why') or 'AGENTS.md does not run').splitlines()[0]}",
            )
        if res:
            # "Run 1 stopped in tests: …"; a result with no stage says so without one (it read
            # "Attempt 1 stopped in None" once)
            n, stage = res.get("attempt"), res.get("stage")
            said = " ".join(
                w
                for w in ("Run", str(n) if n else "", "stopped", f"in {stage}" if stage else "")
                if w
            )
            return "tried", f"{said}: {res.get('why') or ''}".rstrip(": ")
        return "tried", s.get("error") or "Stopped before it finished"

    def maintain_today(self, keys: dict | None = None) -> dict:
        """Today's maintenance rows, from the poll's cache (no GitHub call here)."""
        from ..maintain.poll import today

        try:
            d = today(self.loop().cfg.home)
        except Exception as e:  # noqa: BLE001 - the rest still shows
            return {"rows": [], "polled": None, "error": f"{type(e).__name__}: {e}"}
        for r in d["rows"]:
            r["build"] = (keys or {}).get(r.get("idea"))
        d["onboarding"] = []
        return d

    def loop_state(self) -> dict:
        from .loopview import view

        return view(self.loop(), self.root)

    # ---- the one settings writer: Settings › Building ------------------------------------------
    def loop_rules(self) -> dict:
        """``api/loop/rules``: every setting the app writes (``settings.write.RULES``), with its
        check, its value now and its default; the rest of loop.yaml is set in the file."""
        from ..settings import write as settings
        from ..settings.store import DEFAULTS

        cfg = self.loop_cfg()

        def at(d, key):
            for k in key.split("."):
                d = d.get(k) if isinstance(d, dict) else None
            if key == "build.policy" and isinstance(d, str):  # the pipeline, by its name
                d = d.rstrip("/").split("/")[-1]
            return d

        return {
            "rules": {
                k: {
                    **settings.rule_view(r),
                    "value": at(cfg.data, k),
                    "default": at(DEFAULTS, k),
                    **({"confirm": settings.SENSITIVE[k]} if k in settings.SENSITIVE else {}),
                }
                for k, r in settings.RULES.items()
            },
            "file": str(cfg.layout.settings),
        }

    def set_setting(self, body: dict) -> dict:
        """``POST api/loop/settings`` ``{key, value}`` (or ``{key, clear: true}``: back to the
        default): checked by ``settings.write.check`` and written to loop.yaml, which the loop
        reads on its next tick; a key in ``settings.write.SENSITIVE`` also needs ``confirm:
        true``. Never a model."""
        from ..settings import write as settings

        key = str(body.get("key") or "")
        if key in settings.SENSITIVE and body.get("confirm") is not True:
            # the page's dialog says what changes first; a bare request writes nothing
            raise HttpError(409, f"{key} needs confirm: true. {settings.SENSITIVE[key]}")
        try:
            value = (
                settings.UNSET
                if body.get("clear") is True and key in settings.RULES
                else settings.check(key, body.get("value"))
            )
        except ValueError as e:
            raise HttpError(400, str(e))
        settings.write(paths.Layout(self.loop_cfg().home).settings, tuple(key.split(".")), value)
        log.info(
            "loop.yaml: %s %s (%s)",
            key,
            "back to the default" if value is settings.UNSET else f"set to {value!r}",
            body.get("origin") or "ui",
        )
        self._maint = None
        return {"ok": True, "key": key, **self.loop_rules()}

    def loop_act(self, what: str, body: dict) -> dict:
        """One ``repo-factory loop`` verb for the page (``api/loop/<what>``) or an action from the
        registry (``api/act``). Each verb gets ``--origin``: the
        body's, else the running action's (``registry.current_origin``), else ``ui``."""
        from .. import registry

        home = ["loop", "--home", str(self.loop().cfg.home)]
        origin = body.get("origin") or registry.current_origin() or "ui"
        if not registry.valid_origin(origin):
            raise HttpError(
                400, "origin must be one of " + ", ".join(registry.ORIGINS) + ", or agent:<name>"
            )
        o = f"--origin={origin}"
        if what == "add":
            text = (body.get("text") or "").strip()
            if not text:
                raise HttpError(400, "say what to build")
            out = self.run([*home, "add", "--source", "ui", "--file", "-", o], stdin=text)
            m = IDEA_ID.search(out)
            iid = m.group(0) if m else None
            return {"ok": True, "out": out, "id": iid}
        if what == "pause":
            return {
                "ok": True,
                "out": self.run([*home, "pause" if body.get("paused") else "resume"]),
            }
        iid = str(body.get("id") or "")
        if not IDEA_ID.fullmatch(iid):
            raise HttpError(400, "no such task")
        if what == "send":
            out = self.run(
                [*home, "send", iid, *(["--build-anyway"] if body.get("anyway") else []), o]
            )
            m = IDEA_ID.search(out)
            return {"ok": True, "out": out, "id": m.group(0) if m else iid}
        if what == "close":
            reason = " ".join(str(body.get("reason") or "").split())
            if not reason:
                raise HttpError(400, "say why you close it, in a few words")
            return {"ok": True, "out": self.run([*home, "close", iid, "--reason", reason, o])}
        if what == "verdict":  # Reject on a build, with its chips
            if body.get("verdict") != "reject":
                raise HttpError(
                    400, "verdict must be reject (Publish approves; Keep private keeps)"
                )
            reasons = body.get("reasons") or []
            if not isinstance(reasons, list) or any(r not in REJECT_CHIPS for r in reasons):
                raise HttpError(400, "reasons must be chips from: " + ", ".join(REJECT_CHIPS))
            note = " ".join(str(body.get("note") or "").split())[:300]
            if not reasons and not note:
                raise HttpError(400, "say why: pick a reason, or write a few words")
            return {
                "ok": True,
                "out": self.run(
                    [*home, "reject", iid, *[f"--reason={r}" for r in reasons], f"--note={note}", o]
                ),
            }
        if what == "keep":
            return {"ok": True, "out": self.run([*home, "keep", iid, o])}
        if what == "later":
            try:
                days = float(body.get("days") or 7)
            except (TypeError, ValueError):
                raise HttpError(400, "days must be a number")
            out = self.run([*home, "later", iid, f"--days={days:g}", o])
            m = IDEA_ID.search(out)
            return {"ok": True, "out": out, "id": m.group(0) if m else iid}
        if what == "approve":
            return {"ok": True, "out": self.run([*home, "approve", iid, o], timeout=600)}
        if what == "publish":  # a ready build: a private repo (the press)
            return {"ok": True, "out": self.run([*home, "publish", iid, o], timeout=600)}
        if what == "publish-anyway":  # the registry passes yes only on confirm
            reason = " ".join(str(body.get("reason") or "").split())
            holds = body.get("holds")
            if holds is not None and (
                not isinstance(holds, list)
                or not all(isinstance(h, str) and re.fullmatch(r"[a-z_]{1,40}", h) for h in holds)
            ):
                raise HttpError(400, "holds is a list of check names")
            # no model runs here: the pre-push checks ran when the build stopped (precheck)
            return {
                "ok": True,
                "out": self.run(
                    [
                        *home,
                        "publish-anyway",
                        iid,
                        "--reason",
                        reason,
                        *(["--yes"] if body.get("yes") is True else []),
                        *([f"--holds={','.join(holds)}"] if holds is not None else []),
                        o,
                    ],
                    timeout=600,
                ),
            }
        if what == "revise":  # Ask for changes
            text = (body.get("text") or "").strip()
            mode = body.get("mode") or "auto"
            if mode not in ("auto", "docs", "code"):
                raise HttpError(400, "mode is auto, docs or code")
            if not text:
                raise HttpError(400, "say what should change")
            return {
                "ok": True,
                "out": self.run(
                    [*home, "revise", iid, "--file", "-", f"--mode={mode}", o], stdin=text
                ),
            }
        if what in ("stop-round", "resume-build", "cancel"):
            # one build's controls: each returns at once; a kill finishes in the loop
            return {"ok": True, "out": self.run([*home, what, iid, o])}
        if what == "undo":  # the flash's Undo: no kill, no model
            of = body.get("of")
            if of is not None and of not in registry.UNDOABLE:
                raise HttpError(400, "of is one of " + ", ".join(registry.UNDOABLE))
            return {
                "ok": True,
                "out": self.run([*home, "undo", iid, *([f"--of={of}"] if of else []), o]),
            }
        if what == "restart":  # Restart with new instructions
            text = (body.get("text") or "").strip()
            mode = body.get("mode") or "auto"
            if mode not in ("auto", "docs", "code"):
                raise HttpError(400, "mode is auto, docs or code")
            if not text:
                raise HttpError(400, "say what should change")
            return {
                "ok": True,
                "out": self.run(
                    [*home, "restart", iid, "--file", "-", f"--mode={mode}", o], stdin=text
                ),
            }
        if what == "repitch":
            return {"ok": True, "out": self.run([*home, "repitch", iid, "--later", o])}
        if what == "claim":  # Claims: accept or edit a reading (no model)
            k = str(body.get("claim") or "")
            if not re.fullmatch(r"k\d{1,3}", k):
                raise HttpError(400, "claim is a claim's id (k1, k2, ...)")
            if body.get("accept"):
                return {"ok": True, "out": self.run([*home, "claim", iid, "--accept", k, o])}
            text = " ".join(str(body.get("text") or "").split())
            if not text:
                raise HttpError(400, "say how the claim is read")
            d = " ".join(str(body.get("default") or "").split())[:80]
            return {
                "ok": True,
                "out": self.run(
                    [
                        *home,
                        "claim",
                        iid,
                        "--edit",
                        k,
                        "--file",
                        "-",
                        *([f"--default={d}"] if d else []),
                        o,
                    ],
                    stdin=text,
                ),
            }
        if what == "release-text":  # saved now, used as it is (no model)
            text = " ".join(str(body.get("text") or "").split())
            if not text:
                raise HttpError(400, "write the release text")
            return {
                "ok": True,
                "out": self.run([*home, "release-text", iid, "--file", "-", o], stdin=text),
            }
        if what == "recheck":  # starts in the background, returns at once
            return {"ok": True, "out": self.run([*home, "recheck", iid, o])}
        if what == "retry":  # note: one line kept in the idea's history
            note = " ".join(str(body.get("note") or "").split())[:300]
            return {
                "ok": True,
                "out": self.run([*home, "retry", iid, *([f"--note={note}"] if note else []), o]),
            }
        if what == "edit":
            text = (body.get("text") or "").strip()
            if not text:
                raise HttpError(400, "write the new brief")
            return {"ok": True, "out": self.run([*home, "edit", iid, "--file", "-", o], stdin=text)}
        raise HttpError(404, "not found")

    # ---- the registry's doors (repo_factory/registry.py) -----------------------------------------
    @staticmethod
    def row_actions(rows: list[dict], ideas: list[dict], cfg: dict) -> None:
        """Each build row's ``actions``: the registry's, for the page's action row (its
        order, labels, keys, and why a hidden one is hidden)."""
        from .. import registry as R

        by = {i["id"]: i for i in ideas}
        for r in rows:
            s = R.Subject("build", by.get(r.get("idea")), r, cfg, r["key"])
            r["actions"] = [
                {
                    k: a.get(k)
                    for k in (
                        "kind",
                        "title",
                        "says",
                        "click",
                        "key",
                        "enabled",
                        "why",
                        "mode",
                        "page",
                        "plan",
                        "instructions",
                    )
                    if k in a
                }
                for a in R.available(s)
            ]

    def actions(self, kind: str | None, ref: str | None) -> dict:
        """``api/actions?kind=build|idea&id=``: every action for the subject, each enabled or
        with why not."""
        from .. import registry as R

        try:
            s = R.subject_of(self, kind, ref)
        except R.ActionError as e:
            raise HttpError(e.code, str(e))
        except LookupError as e:
            raise HttpError(404, str(e))
        return {"subject": {"kind": s.kind, "id": s.id}, "actions": R.available(s)}

    def outcomes(self, kind: str | None, ref: str | None) -> dict:
        """``api/outcomes?kind=build|idea&id=``: the last presses on the subject, newest first,
        each ok, refused or failed, with why when it did not happen ("why did my press
        fail"; ASKF)."""
        from .. import registry as R

        if not kind and not ref:  # the last presses on anything (PAR)
            return {
                "subject": {"kind": "any", "id": None, "build": None, "title": None},
                "state": None,
                "reason": None,
                "outcomes": R.outcomes(self.loop(), None, None),
                "now": [],
            }
        try:
            s = R.subject_of(self, kind or "", ref)
        except R.ActionError as e:
            raise HttpError(e.code, str(e))
        except LookupError as e:
            raise HttpError(404, str(e))
        iid = R.target(s)
        got = R.outcomes(self.loop(), iid, (s.row or {}).get("key"))
        # what each press that did not happen would meet now: its offer (enabled, or why
        # not) and, for Publish anyway, the holds as the repo-factory installed judges them today
        failed = list(
            dict.fromkeys(o["kind"] for o in got if o["outcome"] != "ok" and o["kind"] in R.ACTIONS)
        )
        now = []
        for a in R.available(s) if failed else []:
            if a["kind"] in failed:
                x = {
                    "kind": a["kind"],
                    "title": a["title"],
                    "enabled": a["enabled"],
                    "why": a["why"],
                }
                if a.get("plan"):
                    x["holds"] = [
                        f"{h['label']}: {h['detail']}"
                        + ("" if h["may"] else f" (never: {h['why']})")
                        for h in a["plan"]["holds"]
                    ]
                    x["rejudged"] = a["plan"].get("rejudged")
                now.append(x)
        return {
            "subject": {
                "kind": s.kind,
                "id": s.id,
                "build": (s.row or {}).get("key"),
                "title": (s.row or {}).get("title") or (s.idea or {}).get("title"),
            },
            "state": (s.idea or {}).get("state") or (s.row or {}).get("status"),
            "reason": (s.row or {}).get("reason") or (s.idea or {}).get("reason"),
            "outcomes": got,
            "now": now,
        }

    def act(self, body: dict, identity: dict | None = None) -> dict:
        """``api/act {kind, subject: {kind, id}, fields, confirm}``: one action through the
        registry, as the page's buttons and agents run it. Who acts is ``identity``, the
        caller the server found from the request's token; the body's
        ``origin`` counts only without one (an in-process call, or a host with
        ``api.require_token: false``). Each door runs what ``registry.door_refuses`` allows
        (403 otherwise); a confirm-class kind without ``confirm: true`` is refused (409)."""
        from .. import registry as R

        if identity is not None:
            origin = str(identity.get("origin") or "")
        else:
            origin = body.get("origin") or "ui"
            if origin not in ("ui", "cli"):
                raise HttpError(400, "origin must be ui or cli")
        kind = str(body.get("kind") or "")
        if kind not in R.ACTIONS:
            raise HttpError(400, f"unknown action {kind!r}")
        why = R.door_refuses(origin, kind, (identity or {}).get("actions"), self.loop().cfg.data)
        if why:
            raise HttpError(403, why)
        subj, fields = body.get("subject") or {}, body.get("fields") or {}
        if not isinstance(subj, dict) or not isinstance(fields, dict):
            raise HttpError(400, "subject and fields are JSON objects")
        if body.get("check") is True:
            # what a press would refuse, without pressing
            try:
                s = R.subject_of(self, str(subj.get("kind") or ""), subj.get("id"))
                R.validate(kind, fields)
            except R.ActionError as e:
                raise HttpError(e.code, str(e))
            except LookupError as e:
                raise HttpError(404, str(e))
            return {"ok": True, "checked": True, "why_not_now": R.check(kind, s)}
        try:
            s = R.subject_of(self, subj.get("kind"), subj.get("id"))
            out = R.run(R.Ctx(self, origin, confirm=body.get("confirm") is True), kind, fields, s)
        except R.ActionError as e:
            raise HttpError(e.code, str(e))
        except LookupError as e:
            raise HttpError(404, str(e))
        res = out.get("result") or {}
        log.info(
            "action %s on %s %s by %s: %s",
            kind,
            subj.get("kind"),
            subj.get("id"),
            origin,
            out.get("what") or out.get("say") or "done",
        )
        return {
            "say": out.get("say"),
            "what": out.get("what"),
            "url": out.get("url"),
            "subject": out.get("subject"),
            "outcome": out.get("outcome"),
            "result": {k: res.get(k) for k in ("id", "out", "form") if k in res},
            "by": origin,
            **({"confirm": True} if out.get("confirm") else {}),
            **({"undo": out["undoable"]} if (out.get("undoable") or {}).get("until") else {}),
        }

    # ---- the unified UI: one snapshot for the five tabs ------------------------------------
    def key(self, d: Path) -> str:
        try:
            return Path(d).resolve().relative_to(self.root).as_posix() or Path(d).name
        except ValueError:
            return Path(d).name

    def hand_gate(self, d: Path) -> dict | None:
        """S2: the loop's release gate over a build started by hand, so it shows as Ready
        or Held by the same rule as loop builds. Cached on the event log's size and time, in
        memory and in ``.den/cache/hand_gate.json`` (SL: the gate runs ``git``, so a restart
        does not run it again for every build on the first page)."""
        from ..loop import gate

        f = log_path(self.den(d))
        try:
            st = f.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            return None
        hit = self._gates.get(str(d))
        if hit and hit[0] == sig:
            return hit[1]
        kept = self.den(d) / "cache" / "hand_gate.json"
        try:
            k = json.loads(kept.read_text())
            if tuple(k["sig"]) == sig and isinstance(k["gate"], dict):
                self._gates[str(d)] = (sig, k["gate"])
                return k["gate"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        g = gate.evaluate(d)
        self._gates[str(d)] = (sig, g)
        try:
            kept.parent.mkdir(parents=True, exist_ok=True)
            tmp = kept.with_name(f".hand_gate.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"sig": list(sig), "gate": g}))
            os.replace(tmp, kept)
        except (OSError, TypeError, ValueError):
            pass
        return g

    def precheck_of(self, idea: dict | None) -> dict | None:
        if not idea:
            return None
        from ..loop import precheck

        try:
            return precheck.summary(idea, self.loop().cfg)
        except Exception:  # noqa: BLE001 - the row draws without it
            return None

    def claims_of(self, idea: dict | None) -> dict | None:
        """The build page's Claims section (``claims.view``: the readings made at build start
        and the release text), with what the pre-push checks wrote and reworded
        (``reworded_of``) and, for a published build, the release text on GitHub."""
        if not idea or not (idea.get("pitch") or {}).get("product"):
            return None
        from ..loop import claims

        try:
            out = claims.view(idea)
            pub_ = idea.get("publish") or {}
            out["public"] = self.reworded_of(idea)
            if pub_.get("url"):
                rec = dict(pub_.get("textcheck") or {})
                out["sent"] = {
                    "state": rec.get("state") or "none",
                    "error": rec.get("error"),
                    "published": pub_.get("release_text"),
                }
            return out
        except Exception:  # noqa: BLE001 - the row draws without it
            return None

    @staticmethod
    def reworded_of(idea: dict | None) -> dict | None:
        """What the pre-push checks did to the public text (``describe.run``'s record): the
        description written from the product, and each sentence the review reworded. The
        published record's once the build is published, else the current checks'."""
        pub_ = (idea or {}).get("publish") or {}
        d = (
            pub_.get("described")
            if pub_.get("url")
            else (
                (pub_.get("precheck") or {}).get("described")
                if (pub_.get("precheck") or {}).get("state") == "done"
                else None
            )
        )
        if not isinstance(d, dict):
            return None
        w = d.get("written") or {}
        return {
            "written": bool(w.get("ok")),
            "description": w.get("description"),
            "was": (d.get("was") or {}).get("readme"),
            "release_text": d.get("release_text"),
            "release_by": d.get("release_by"),
            "reworded": [
                {k: r.get(k) for k in ("file", "was", "now", "why")}
                for r in d.get("reworded") or []
            ][:20],
        }

    @staticmethod
    def recheck_of(gate: dict | None) -> dict | None:
        """The last re-check: its summary with its words; a running one whose
        process died says so."""
        from ..loop import builds as lb
        from ..loop import recheck

        rec = recheck.summary((gate or {}).get("recheck"))
        if not rec:
            return None
        if rec.get("state") == "running" and not lb.alive(rec.get("pid")):
            rec = {**rec, "state": "failed", "error": "the re-check stopped before it finished"}
        rec["words"] = recheck.words(rec)
        return rec

    def build_row(self, d: Path, idea: dict | None) -> dict:
        """One row of the Builds list: status in the UI's words, the reason, cost, time,
        source, and the loop idea behind it."""
        f = log_path(self.den(d))
        try:
            sig = (f.stat().st_mtime_ns, f.stat().st_size)
        except OSError:
            sig = (0, 0)
        sig += ((idea or {}).get("updated"), (idea or {}).get("state"))
        hit = self._rows.get(str(d))
        if (
            hit
            and hit[0] == sig
            and hit[1]["status"] not in ("running", "queued")
            and hit[1].get("kind") != "fix"
        ):  # a fix's PR state comes from the poll
            return hit[1]
        s = self.state(d)
        st = s["build"] or {}
        v5 = s.get("v5") or {}
        act = st.get("activity") or {}
        live = s["state"] in ("running", "asking")
        cost = (
            (v5.get("cost") or {}).get("total")
            or st.get("cost_usd")
            or (act.get("cost") or {}).get("total")
        )
        wall = st.get("wall_s")
        if wall is None and st.get("started"):
            wall = (st.get("finished") or time.time()) - st["started"]
        p = (idea or {}).get("pitch") or {}
        if idea:
            status, src, gate, reason = self._loop_status(idea, v5, act)
        else:
            status, src, gate, reason = self._hand_status(d, s, st, v5, act)
        title = p.get("product") or s["title"] or d.name
        fix = self.fix_of(d) if not idea else None
        if fix:  # a maintenance fix: kind fix
            status, reason = self.fix_status(d, s, fix, v5)
            src, title = "fix", f"Fix #{fix.get('issue')}: {fix.get('title') or ''}"
            gate = None  # a fix's gate is the owner's review on GitHub
        toks, by_round = ledger_tokens(self.den(d))
        last_round = ((idea or {}).get("build") or {}).get("round")
        last_toks = by_round.get(last_round) if last_round is not None else None
        fail = self._fail_of(d, s, status, idea, v5)
        row = {
            "key": self.key(d),
            "dir": str(d),
            "status": status,
            "state": s["state"],
            "fail": fail,
            "idea_title": idea_title(idea) if idea else None,
            "title": title,
            "desc": p.get("one_liner") or "",
            "reason": reason,
            "source": src,
            "cost_usd": round(cost, 4) if cost else None,
            "wall_s": round(wall) if wall else None,
            "updated": s["updated"],
            "live": live,
            "idea": (idea or {}).get("id"),
            "idea_state": (idea or {}).get("state"),
            "last_cost_usd": ((idea or {}).get("build") or {}).get("cost_usd"),
            # {"in", "out"}: every call in the task's ledger (all rounds); None when no
            # ledger line carries token fields (builds from before tokens were recorded)
            "tokens": toks,
            # the same for the round last_cost_usd is of (the idea's last build record)
            "last_tokens": last_toks,
            **self._idea_fields(idea),
            "question": (
                next(
                    (
                        q
                        for q in st.get("questions") or []
                        if not q["answered"] and not q.get("pending")
                    ),
                    None,
                )
                if s["state"] == "asking"
                else None
            ),
            # judged_by: the repo-factory version that built it; installed: this one; recheck:
            # the last "Re-check with current repo-factory"
            # gaps: what repo-factory could not measure, said beside the checks, never a hold
            "gate": {
                "verdict": gate.get("verdict"),
                "reasons": gate.get("reasons") or [],
                "checks": gate.get("checks") or {},
                "round": gate.get("round"),
                "gaps": gate.get("gaps") or [],
                "judged_by": gate.get("judged_by"),
                "installed": installed_version(),
                "recheck": self.recheck_of(gate),
            }
            if gate
            else None,
        }
        # a hold no revise round can change (repo-factory's own sandbox): said on the page and in
        # Needs you; Revise is not offered, Publish anyway and Close are
        row["cant_revise"] = None
        if idea and gate and status == "held":
            from ..loop import gate as gate_mod

            try:  # today's gate too: Publish anyway's
                now = gate_mod.judged(d, gate.get("round"), gate)
            except Exception:  # noqa: BLE001 - the stored verdict's
                now = gate
            row["cant_revise"] = gate_mod.cant_revise(gate, d, now)
            if row["cant_revise"]:  # every list says it, not only the page
                row["reason"] = row["cant_revise"]
        if v5.get("stages"):
            # the stage strip for the row's pipeline miniature: the stages
            # the state already folded, in order; a build from before v5 has no key
            row["pipeline"] = [{"stage": x["stage"], "status": x["status"]} for x in v5["stages"]]
        # the product's Builds in brief ({n, latest}): the list's status sentence
        row["builds"] = self.runs_brief(d, idea) if not fix else None
        latest = (row["builds"] or {}).get("latest") or {}
        if row["fail"] and latest and latest.get("outcome") not in ("failed", "interrupted"):
            # build.log's last failure belongs to an earlier Build: never shown as this
            # one's
            row["fail"] = None
        if fix:
            from ..maintain.revise import result

            res = result(d) or {}
            row.update(
                kind="fix",
                desc=fix.get("product") or fix.get("repo") or "",
                repo=fix.get("repo"),
                url=fix.get("url"),
                issue={"repo": fix.get("repo"), "number": fix.get("issue"), "url": fix.get("url")},
                pr={"number": res.get("pr"), "url": res.get("url")} if res.get("pr") else None,
            )
        self._rows[str(d)] = (sig, row)
        return row

    # build_row's sections ------------------------------------------------------------------------
    @staticmethod
    def _loop_status(idea: dict, v5: dict, act: dict) -> tuple:
        """A loop build's ``(status, source, gate, reason)``: the idea's state in the list's
        words."""
        status = LOOP_BUILD.get(idea["state"], "queued")
        src = "typed"
        gate = idea.get("gate") or None
        pub = idea.get("publish") or {}
        reason = {
            "queued": "Waiting for a build slot"
            + (
                f"; it resumes round {(idea.get('build') or {}).get('resume_round')}"
                if (idea.get("build") or {}).get("resume_round")
                else ""
            ),
            "published": f"Published privately: {pub.get('repo') or ''}, "
            f"v{pub.get('version') or '0.1.0'}; Make public when you want.",
            "public": f"Public: {pub.get('url') or pub.get('repo') or ''}",
        }.get(status) or idea.get("reason")
        if idea["state"] == "ready":
            reason = "Ready: passed the gate. Publish makes it a private repository."
        if idea["state"] == "published" and pub.get("kept"):
            status = "kept"  # the owner kept it private: not needs you
            reason = (
                f"Kept private by you: {pub.get('repo') or ''}, "
                f"v{pub.get('version') or '0.1.0'}. Publish still makes it public."
            )
        if status == "running" and idea["state"] == "building":
            reason = v5.get("label") or act.get("stage") or act.get("phase") or "Starting"
        kill = (idea.get("build") or {}).get("kill")
        if idea["state"] == "building" and kill:
            reason = CONTROL_REASON.get(kill.get("what"), "Stopping…")
        if idea["state"] == "paused":
            rr = (idea.get("build") or {}).get("resume_round")
            reason = (
                f"Paused by you: round {rr} is kept and continues when you click Resume"
                if rr
                else "Paused by you: it starts again when you click Resume"
            )
        return status, src, gate, reason

    def _hand_status(self, d: Path, s: dict, st: dict, v5: dict, act: dict) -> tuple:
        """A build started by hand: ``(status, source, gate, reason)`` from its own state
        (and, once delivered, the release gate it would meet)."""
        gate = None
        src = "hand"
        q = next(
            (q for q in st.get("questions") or [] if not q["answered"] and not q.get("pending")),
            None,
        )
        status = {
            "new": "queued",
            "running": "running",
            "asking": "held",
            "failed": "failed",
            "stopped": "failed",
            "accepted": "closed",
            "rejected": "closed",
        }.get(s["state"], "held")
        reason = None
        if s["state"] == "asking" and q:
            reason = f"Asks: {q.get('text')}"
        elif s["state"] == "running":
            reason = v5.get("label") or act.get("stage") or act.get("phase") or "Running"
        elif s["state"] == "delivered":
            gate = self.hand_gate(d)
            if gate and gate["verdict"] == "ready":
                status, reason = "ready", "Delivered, and the release gate passes"
            else:
                status = "held"
                why = (gate or {}).get("reasons") or ["no gate result"]
                reason = (
                    "Delivered; the release gate would hold it: "
                    + why[0]
                    + (f" (and {len(why) - 1} more)" if len(why) > 1 else "")
                )
        elif s["state"] in ("failed", "stopped"):
            reason = s.get("error") or (
                "Stopped before it finished" if s["state"] == "stopped" else "Failed"
            )
        elif s["state"] in ("accepted", "rejected"):
            reason = f"{s['state'].capitalize()} by you"
        elif s["state"] == "new":
            reason = "Not started"
        return status, src, gate, reason

    def _fail_of(self, d: Path, s: dict, status: str, idea: dict | None, v5: dict):
        """Why it failed, where the owner first looks: the build log's line, and the stage
        that stopped; None unless it is held or failed after a failure."""
        if status not in ("held", "failed") or not (
            s["state"] in ("failed", "stopped") or ((idea or {}).get("build") or {}).get("failures")
        ):
            return None
        from .faillog import fail_line

        fail = fail_line(self.den(d) / "build.log")
        stuck = next(
            (
                x["stage"]
                for x in v5.get("stages") or []
                if x.get("status") in ("failed", "stopped", "held")
            ),
            None,
        )
        if fail and stuck:
            fail["stage"] = stuck
        return fail

    def _idea_fields(self, idea: dict | None) -> dict:
        """The row's fields that come from the loop idea behind it (None and empty for a
        build started by hand)."""
        return {
            "repo": ((idea or {}).get("publish") or {}).get("repo"),
            "url": ((idea or {}).get("publish") or {}).get("url"),
            # Ask for changes: the revise round asked for (queued or running), and the
            # ones that ran; Publish anyway: the override the repo was published over
            "change": ((idea or {}).get("build") or {}).get("change"),
            "changes": ((idea or {}).get("build") or {}).get("changes") or [],
            # only when it was published: a press that held again published nothing (a
            # record written before 0.3.4 kept the override either way)
            "override": ((idea or {}).get("publish") or {}).get("override")
            if ((idea or {}).get("publish") or {}).get("url")
            else None,
            # the owner's per-build controls: a stop or pause under way ({what, at}), and
            # the round they stopped ({round, resumable}: Resume continues it)
            "control": {
                k: v
                for k, v in (((idea or {}).get("build") or {}).get("kill") or {}).items()
                if k in ("what", "at", "origin")
            }
            or None,
            "stopped": {
                k: v
                for k, v in (((idea or {}).get("build") or {}).get("stopped") or {}).items()
                if k in ("round", "resumable", "at")
            }
            or None,
            # who asked for the round that runs or ran last: {origin, at, round};
            # origin None is repo-factory itself (the brief accepted it, or a retry it made)
            "started_by": started_by(idea),
            "round": ((idea or {}).get("build") or {}).get("round"),
            # the flash's Undo after Stop round or Cancel: {of, until}
            "undo": undo_view((idea or {}).get("undo")),
            # the pre-push checks of a held or ready build, run when it stopped:
            # {state, since, holds: [[check, detail]], error}; Publish anyway lists them
            "precheck": self.precheck_of(idea),
            # Claims: how the task's claims are read, repo-factory's release text,
            # and the text check's state; None for a build with no pitch (a hand build)
            "claims": self.claims_of(idea),
            # Ask for changes spends from what the build's cap has left (None: no cap, or
            # revise.max_usd of its own)
            "revise_left": self.revise_left(idea),
        }

    def revise_left(self, idea: dict | None) -> float | None:
        """What a revise round of this build may spend: the build's cap less what the build
        and its revise rounds spent (``v5.change.build_left``); None without a cap, or with
        ``revise.max_usd`` set (the round's own cap)."""
        task = (idea or {}).get("task_dir")
        rnd = ((idea or {}).get("gate") or {}).get("round")
        if not task or not rnd:
            return None
        try:
            if (self.loop_cfg().get("revise") or {}).get("max_usd") is not None:
                return None
            from ..v5.change import build_left

            return build_left(Path(task) / ".den", int(rnd))
        except Exception:  # noqa: BLE001 - a number on the page, never an error
            return None

    def idea_row(self, i: dict, tasks: dict) -> dict:
        """A task in the ledger (an "idea" row): ``state`` ``new`` and no ``build`` is the
        Brief stage on the page."""
        p = i.get("pitch") or {}
        status = LOOP_IDEA.get(i["state"], "accepted")
        later = i.get("later_until") if (i.get("later_until") or 0) > time.time() else None
        return {
            "id": i["id"],
            "status": status,
            "state": i["state"],
            "title": idea_title(i),
            "product": p.get("product") or None,
            "later_until": later if status == "open" else None,
            "source": "typed",
            "text": (i.get("text") or "")[:4000],
            "reason": i.get("reason"),
            "created": i["created"],
            "updated": i["updated"],
            "sent": bool(p.get("sent")) and i["state"] == "new",
            "pitch": {
                k: p.get(k)
                for k in (
                    "verdict",
                    "product",
                    "product_type",
                    "one_liner",
                    "who",
                    "why_now",
                    "buildability",
                    "buildability_reasons",
                    "brief",
                    "reject_reason",
                    "risks",
                    "overridden",
                    # the scores, the gate that rejected it, the reason in plain words, the
                    # facts carried on
                    "insight",
                    "differentiation",
                    "grounding_score",
                    "gated",
                    "reject_words",
                    "existing",
                    "moment",
                    "grounding",
                    # the person's task: the brief's doubt, kept as advice
                    "advice",
                )
            }
            if p.get("verdict") or p.get("product")
            else None,
            "build": tasks.get(str(Path(i["task_dir"]).resolve())) if i.get("task_dir") else None,
            # a Cancel that sent it back can still be undone
            "undo": undo_view(i.get("undo")),
        }

    def snapshot(self) -> dict:
        """``GET api/state``: everything the pages show, in one read: the loop's status and
        spend, every build, every task in the ledger, the recent history."""
        lp = self.loop()
        ideas = lp.ledger.all()
        undos = lp.ledger.metas("undo:")
        for i in ideas:
            i["undo"] = undos.get(i["id"]) or None
        by_dir = {str(Path(i["task_dir"]).resolve()): i for i in ideas if i.get("task_dir")}
        # one build or task that cannot be read is listed as such, never the page;
        # what reads every row (the actions, the projects) reads the readable ones
        built = rowsafe.each(
            scan(self.root),
            lambda d: self.build_row(d, by_dir.get(str(d.resolve()))),
            "build",
            ident=lambda d: d.name,
            keep=lambda d: {"dir": str(d), "key": d.name, "title": d.name},
        )
        rows = [r for r in built if not rowsafe.is_unreadable(r)]
        rows.sort(key=lambda r: -(r["updated"] or 0))
        tasks = {r["dir"]: r["key"] for r in rows}
        out_ideas = rowsafe.each(
            ideas,
            lambda i: self.idea_row(i, tasks),
            "idea",
            keep=lambda i: {"title": idea_title(i), "state": i.get("state")},
        )
        readable = [i for i in out_ideas if not rowsafe.is_unreadable(i)]
        self.row_actions(rows, readable, lp.cfg.data)
        keys = {
            i["id"]: tasks.get(str(Path(i["task_dir"]).resolve()))
            for i in ideas
            if i.get("task_dir")
        }
        maint = self.maintain_today(keys)
        self.project_links(lp, rows, maint["rows"])
        names = {i["id"]: i.get("title") or i["id"] for i in ideas}
        hist = [
            {
                "id": h["id"],
                "idea": h["idea"],
                "title": names.get(h["idea"]),
                "build": keys.get(h["idea"]),
                "ts": h["ts"],
                "state": h["state"],
                "note": h["note"],
            }
            for h in lp.ledger.history(limit=40, ideas_only=True)
            if h.get("note")
        ]
        status = lp.status()
        status["min_buildability"] = lp.cfg["pitch"].get("min_buildability")
        return {
            "api_version": API_VERSION,
            "now": time.time(),
            "root": str(self.root),
            "harness": self.harness,
            "status": status,
            "builds": rows + [r for r in built if rowsafe.is_unreadable(r)],
            "ideas": out_ideas,
            "history": hist,
            "maintain": maint,
            "project_asks": self.project_asks(lp),
        }

    # ---- projects (repo_factory/projects/; off and unlisted): the ledger and the poll's cache --
    def projects(self) -> dict:
        """Desk › Projects: one row per project, by health (``record.listing``)."""
        from ..projects import record

        return record.listing(self.loop())

    def project(self, pid: str) -> dict:
        """A project's page (``record.page``), with its build's key for the link."""
        from ..projects import record

        if not re.fullmatch(r"p[0-9a-f]{8}", pid or ""):
            raise HttpError(404, "no such project")
        try:
            out = record.page(self.loop(), pid)
        except LookupError as e:
            raise HttpError(404, str(e))
        i = out.get("idea")
        if i and i.get("task_dir"):
            i["build"] = self.key(Path(i["task_dir"]))
        from ..maintain import mergepolicy  # the switch's policy PR, while it opens

        for r in out.get("repos") or []:
            if r.get("merge") is not None:
                r["merge"]["job"] = mergepolicy.job_state(self.loop().cfg.home, r["repo"])
        from ..projects import jobs

        led = self.loop().ledger
        out["jobs"] = {
            w: {k: v for k, v in jobs.state(led, w, pid).items() if k != "pid"} for w in jobs.WHATS
        }
        from ..projects import changes as X
        from ..projects import evolve
        from ..projects.steps import step

        lp = self.loop()
        fixes = self.fix_rows()
        out["ideas"] = [
            {
                "id": ch["id"],
                "title": ch["title"],
                "kind": ch["kind"],
                "class": ch["class"],
                "state": ch["state"],
                **{
                    k: ch["card"].get(k)
                    for k in (
                        "what",
                        "why_now",
                        "evidence",
                        "scope_test",
                        "test",
                        "effort",
                        "confidence",
                        "duplicate_of",
                        "duplicate_url",
                        "declined",
                        "ask",
                        "gate",
                        "raised",
                        "charter_change",
                        "accepted",
                    )
                },
                "runs": (ch["card"].get("runs") or {}).get("agreeing"),
                "first": (ch["card"].get("runs") or {}).get("first"),
                "filed": ch.get("filed"),
                "later_until": ch.get("later_until"),
                "updated": ch["updated"],
                # where it is now: its issue, its fix's run, its pull request
                "step": step(
                    {
                        **ch,
                        "declined": ch["card"].get("declined"),
                        "accepted": ch["card"].get("accepted"),
                    },
                    fixes.get(self.issue_key(ch.get("filed"))),
                ),
            }
            for ch in X.all_of(led, pid)
        ]
        last = evolve.last_run(led, pid)
        out["evolve"] = (
            {
                k: last.get(k)
                for k in (
                    "at",
                    "checked",
                    "model",
                    "proposed",
                    "new",
                    "agreed",
                    "tracked",
                    "skipped",
                    "notes",
                    "dropped",
                    "tokens",
                )
            }
            if last
            else None
        )
        p = record_get(lp, pid)
        if p and last:
            every = float(X.setting(lp.cfg.data, p, "evolve.every_days", 7) or 7) * 86400
            out["evolve"]["next"] = float(last.get("checked") or last.get("at")) + every
        out["autonomy"] = X.autonomy(lp.cfg.data, p)
        return out

    @staticmethod
    def issue_key(filed: dict | None) -> tuple | None:
        f = filed or {}
        return (
            (str(f.get("repo") or "").lower(), int(f["number"]))
            if f.get("repo") and f.get("number")
            else None
        )

    def fix_rows(self) -> dict:
        """Every maintenance fix's build row, by ``(repo, issue)``: its status (running, fixed:
        a PR waits, closed: that PR is no longer open, tried) and its PR (cached rows, as the
        list reads them; never a GitHub call)."""
        out: dict = {}
        for d in scan(self.root):
            fix = self.fix_of(d)
            if not fix or not fix.get("repo") or not fix.get("issue"):
                continue
            row = self.build_row(d, None)
            out[(str(fix["repo"]).lower(), int(fix["issue"]))] = row
        return out

    def _project(self, pid: str) -> dict:
        from ..projects.record import Projects

        if not re.fullmatch(r"p[0-9a-f]{8}", str(pid or "")):
            raise HttpError(404, "no such project")
        p = Projects(self.loop().ledger).get(pid)
        if p is None:
            raise HttpError(404, f"no project {pid}")
        return p

    def project_charter(self, pid: str, v=None) -> dict:
        """``api/project/charter?id=&v=``: a version's text (the head by default), every
        version's line, and the diff from the version before it."""
        from ..projects import charter as C

        p, led = self._project(pid), self.loop().ledger
        try:
            n = int(v) if v not in (None, "") else int(p.get("charter_v") or 0)
        except (TypeError, ValueError):
            raise HttpError(400, "v is a version number")
        cur = C.version(led, pid, n) if n else None
        if n and cur is None:
            raise HttpError(404, f"no charter v{n}")
        prev = C.version(led, pid, n - 1) if n > 1 else None
        text = C.to_text(cur["charter"]) if cur else ""
        return {
            "id": pid,
            "v": n,
            "head": p.get("charter_v") or 0,
            "charter": cur["charter"] if cur else None,
            "text": text,
            "by": (cur or {}).get("by"),
            "note": (cur or {}).get("note"),
            "at": (cur or {}).get("at"),
            "versions": C.versions(led, pid),
            "diff": C.diff(C.to_text(prev["charter"]), text) if prev else None,
        }

    def charter_edit(self, pid: str, text: str, note: str = "", origin: str = "ui") -> dict:
        from ..projects import charter as C

        self._project(pid)
        lp = self.loop()
        lp.ledger.origin = origin
        try:
            return C.edit(lp, pid, text, note)
        except C.CharterError as e:
            raise HttpError(400, str(e))

    def charter_restore(self, pid: str, v: int, origin: str = "ui") -> dict:
        from ..projects import charter as C
        from ..projects.record import Projects

        self._project(pid)
        lp = self.loop()
        lp.ledger.origin = origin
        try:
            return C.restore(Projects(lp.ledger), pid, v)
        except C.CharterError as e:
            raise HttpError(404, str(e))

    def project_state(self, pid: str, state: str, origin: str = "ui") -> dict:
        """Pause, resume or archive a project (no model; nothing on GitHub)."""
        from ..projects.record import STATES, Projects

        p, lp = self._project(pid), self.loop()
        if state not in STATES:
            raise HttpError(400, "state is active, paused or archived")
        if p["state"] == state:
            raise HttpError(409, f"it is already {state}")
        lp.ledger.origin = origin
        ps = Projects(lp.ledger)
        p = ps.update(pid, state=state)
        ps.event(
            pid,
            "state",
            {"active": "resumed", "paused": "paused", "archived": "archived"}[state]
            + f" ({origin})",
            state=state,
        )
        return p

    def maintenance_state(self, pid: str, paused: bool, origin: str = "ui") -> dict:
        """Pause or resume maintenance on a project's repositories, in repo-factory, at once (the
        owner, 2026-10-29; ``maintain/pauses.py``): no pull request, the repo's
        ``.github/repo-factory.yml`` untouched. An adopted repo's profile is paused too (as Pause on
        Build › Repos), and restored on resume only when this paused it."""
        from ..maintain import pauses
        from ..maintain import profile as Pf
        from ..maintain.github import Cache

        p, lp = self._project(pid), self.loop()
        repos = [r["repo"] for r in p.get("repos") or [] if r.get("repo")]
        if not repos:
            raise HttpError(409, f"{p['name']} has no repository in maintenance")
        home = lp.cfg.home
        held = pauses.read(home)
        if paused and all(r in held for r in repos):
            raise HttpError(409, f"maintenance is already paused on {p['name']}")
        if not paused and not any(r in held for r in repos):
            raise HttpError(409, f"maintenance is not paused on {p['name']}")
        running: list[int] = []
        for full in repos:
            prof = Pf.load(home, full)
            if paused:
                if full in held:
                    continue
                mine = prof is not None and prof.get("status") not in ("paused", "detached")
                if mine:

                    def fn(x):
                        x.setdefault("added", {})["before_pause"] = x["status"]
                        x["status"] = "paused"

                    Pf.update(home, full, fn)
                pauses.pause(home, full, by=origin, project=pid, profile=mine)
                rec = Cache(home).repo(full)
                running += [int(n) for n in (rec.get("revises") or {})]
                if rec:
                    rec["rows"] = []  # nothing to do while paused
                    Cache(home).write_repo(full, rec)
            else:
                was = pauses.resume(home, full)
                if (
                    was
                    and was.get("profile")
                    and prof is not None
                    and prof.get("status") == "paused"
                ):

                    def back(x):
                        x["status"] = (x.get("added") or {}).pop("before_pause", None) or "watch"

                    Pf.update(home, full, back)
        lp.ledger.origin = origin
        from ..projects.record import Projects

        Projects(lp.ledger).event(
            pid,
            "maintenance",
            "maintenance " + ("paused" if paused else "resumed") + f" ({origin})",
        )
        self._maint = None
        return {
            "id": p["id"],
            "name": p["name"],
            "repos": repos,
            "paused": paused,
            "running": running[0] if running else None,
            "words": pauses.words(pauses.get(home, repos[0])) if paused else None,
        }

    def evolve_now(self, pid: str, origin: str = "ui") -> dict:
        """Check for ideas now: ``repo-factory projects evolve ID`` detached; returns at once."""
        from ..projects import jobs

        p, lp = self._project(pid), self.loop()
        if not p.get("charter"):
            raise HttpError(409, "it has no charter yet: repo-factory drafts it first")
        try:
            jobs.spawn(lp.cfg, lp.ledger, "evolve", pid, by=origin)
        except LoopError as e:
            raise HttpError(409, str(e))
        return p

    def _change(self, cid: str):
        from ..projects import changes as X

        if not re.fullmatch(r"c[0-9a-f]{8}", str(cid or "")):
            raise HttpError(404, "no such change idea")
        lp = self.loop()
        ch = X.get(lp.ledger, cid)
        if ch is None:
            raise HttpError(404, f"no change idea {cid}")
        return lp, ch

    def change_accept(self, cid: str, origin: str = "ui") -> dict:
        from ..projects import changes as X

        lp, _ = self._change(cid)
        lp.ledger.origin = origin
        try:
            return X.accept(lp, cid, origin)
        except LoopError as e:
            raise HttpError(409, str(e))

    def change_undo(self, cid: str, origin: str = "ui") -> dict:
        from ..projects import filing

        lp, _ = self._change(cid)
        lp.ledger.origin = origin
        try:
            return filing.undo(lp, cid, origin=origin)
        except LoopError as e:
            raise HttpError(409, str(e))

    def change_decline(self, cid: str, how: str, why: str = "", origin: str = "ui") -> dict:
        from ..projects import changes as X

        lp, _ = self._change(cid)
        lp.ledger.origin = origin
        try:
            return X.decline(lp, cid, how, why, origin)
        except LoopError as e:
            raise HttpError(409, str(e))

    def change_charter(self, cid: str, accept: bool, why: str = "", origin: str = "ui") -> dict:
        from ..projects import changes as X

        lp, _ = self._change(cid)
        lp.ledger.origin = origin
        try:
            return X.charter_change(lp, cid, accept, why, origin)
        except LoopError as e:
            raise HttpError(409, str(e))

    def projects_settings(self) -> dict:
        """Settings › Publishing › Projects: the values, each key's check, and the autonomy now."""
        from ..projects import changes as X
        from ..settings import write as settings

        cfg = self.loop_cfg()
        rules = {
            k: {
                "kind": r[0],
                **({"choices": [c for c in r[1]]} if r[0] in ("enum", "list") else {}),
                **({"min": r[1], "max": r[2]} if r[0] in ("int", "float") else {}),
                "value": X.setting(cfg.data, None, k),
                "wide": k in settings.PROJECT_WIDE,
            }
            for k, r in settings.PROJECTS.items()
        }
        return {
            "settings": rules,
            "autonomy": X.autonomy(cfg.data),
            "file": str(cfg.layout.settings),
        }

    def set_projects_settings(self, body: dict) -> dict:
        """``{key, value}`` factory-wide (loop.yaml › projects), or with ``project`` that project's
        own override (``value`` null: back to the factory-wide one). Checked; never a model."""
        from ..settings import write as settings

        key, pid = str(body.get("key") or ""), body.get("project")
        clear = bool(pid) and body.get("value") in (None, "") and key in settings.PROJECTS
        try:
            value = None if clear else settings.project_value(key, body.get("value"))
        except ValueError as e:
            raise HttpError(400, str(e))
        if pid:
            from ..projects.record import Projects

            if key in settings.PROJECT_WIDE:
                raise HttpError(400, f"projects.{key} is factory-wide, not per project")
            p = self._project(str(pid))
            over = dict(p.get("settings") or {})
            if clear:
                over.pop(key, None)
            else:
                over[key] = value
            Projects(self.loop().ledger).update(p["id"], settings=over)
            return self.project(p["id"])
        path = ("projects", *key.split("."))
        settings.write(
            self.loop_cfg().layout.settings,
            path,
            settings.UNSET
            if value is None and key.startswith("autonomy.") and key != "autonomy.preset"
            else value,
        )
        return self.projects_settings()

    def projects_preview(self, key: str, value, pid: str | None = None) -> dict:
        """ "With these settings, N of M current ideas would flow now": ``changes.decide`` on
        every proposed idea with one setting changed (no write, no model)."""
        import copy

        from ..projects import changes as X
        from ..projects.evolve import owned
        from ..projects.record import Projects
        from ..settings import write as settings

        lp = self.loop()
        cfg = copy.deepcopy(lp.cfg.data)
        if key:
            try:
                v = settings.project_value(key, value)
            except ValueError as e:
                raise HttpError(400, str(e))
            node = cfg.setdefault("projects", {})
            parts = key.split(".")
            for k in parts[:-1]:
                node = node.setdefault(k, {})
            node[parts[-1]] = v
        out = {"file": 0, "ask": 0, "list": 0}
        for p in Projects(lp.ledger).all("active"):
            if pid and p["id"] != pid:
                continue
            if pid and key:  # the project's own override, as Save writes it
                p = {**p, "settings": {**(p.get("settings") or {}), key: v}}
            mine = owned(lp, p)
            for ch in X.all_of(lp.ledger, p["id"], ("new",)):
                out[X.decide(cfg, p, ch["card"], owned=mine)[0]] += 1
        out["total"] = sum(out.values())
        return out

    def changes(self) -> dict:
        """Every change idea with its project's id and
        name, newest first. Their home is the project's page; this only lists them."""
        from ..projects import changes as X
        from ..projects.record import Projects

        lp = self.loop()
        names = {p["id"]: p["name"] for p in Projects(lp.ledger).all()}
        out = rowsafe.each(
            X.all_of(lp.ledger),
            lambda ch: {
                "id": ch["id"],
                "title": ch["title"],
                "kind": ch["kind"],
                "class": ch["class"],
                "state": ch["state"],
                "ask": bool(ch["card"].get("ask")),
                "project": {"id": ch["project"], "name": names.get(ch["project"])},
                "updated": ch["updated"],
            },
            "change",
            keep=lambda ch: {"title": ch.get("title")},
        )
        out.sort(key=lambda x: -(x.get("updated") or 0))
        return {"changes": out}

    def project_asks(self, lp) -> list[dict]:
        """What the projects ask: a minor ask (Build it) or a scope ask (the
        charter diff), never a model call. Needs you folds them into one row per project
        (GRD-DESIGN §7.3); ``stale``: a minor or patch ask older than ``projects.ask_days`` (14)
        leaves that row's count and stays on the project's page; a scope ask never ages."""
        from ..projects import changes as X
        from ..projects.charter import diff, to_text
        from ..projects.record import Projects

        try:
            rows = X.asks(lp.ledger)
        except Exception:  # noqa: BLE001 - Today draws without them
            return []
        ps, out, now = Projects(lp.ledger), [], time.time()
        for ch in rows:
            with rowsafe.row(
                out,
                "change",
                ch.get("id"),
                title=ch.get("title"),
                project={"id": ch.get("project"), "name": None},
            ):
                p = ps.get(ch["project"]) or {}
                c = ch["card"]
                d = None
                if ch["class"] == "scope" and p.get("charter"):
                    new = X.apply(p["charter"], c.get("charter_change"))
                    d = (
                        [x for x in diff(to_text(p["charter"]), to_text(new)) if x["op"] != " "]
                        if new
                        else None
                    )
                since = (c.get("ask") or {}).get("since")
                days = X.setting(lp.cfg.data, p, "ask_days", ASK_DAYS)
                stale = (
                    ch["class"] != "scope"
                    and isinstance(since, int | float)
                    and isinstance(days, int | float)
                    and now - since > float(days) * 86400
                )
                out.append(
                    {
                        "id": ch["id"],
                        "project": {"id": p.get("id"), "name": p.get("name")},
                        "class": ch["class"],
                        "kind": ch["kind"],
                        "title": ch["title"],
                        "what": c.get("what"),
                        "why": (c.get("gate") or {}).get("why"),
                        "since": since,
                        "runs": (c.get("runs") or {}).get("agreeing"),
                        "evidence": len(c.get("evidence") or []),
                        "diff": d,
                        "stale": bool(stale),
                    }
                )
        return out

    def charter_redraft(self, pid: str, origin: str = "ui") -> dict:
        """Starts ``repo-factory projects charter ID`` detached and returns at once (no model here)."""
        from ..projects import jobs

        p, lp = self._project(pid), self.loop()
        try:
            jobs.spawn(lp.cfg, lp.ledger, "charter", pid, by=origin)
        except LoopError as e:
            raise HttpError(409, str(e))
        return p

    @staticmethod
    def project_links(lp, rows: list[dict], maint: list[dict] | None = None) -> None:
        """Each build, fix and maintenance row's ``project`` (``{id, name}``, else None): by its
        idea, else by its repo."""
        from ..projects.record import Projects

        try:
            ps = Projects(lp.ledger).all()
        except Exception:  # noqa: BLE001 - the rows draw without it
            ps = []
        by_idea = {p["idea"]: p for p in ps if p.get("idea")}
        by_repo = {str(r.get("repo")).lower(): p for p in ps for r in p["repos"]}
        for r in [*rows, *(maint or [])]:
            p = by_idea.get(r.get("idea")) or by_repo.get(str(r.get("repo") or "").lower())
            r["project"] = {"id": p["id"], "name": p["name"]} if p else None

    def header(self) -> dict:
        """What the header shows: running and queued builds, what needs you, open tasks, and
        whether the loop is paused."""
        s = self.snapshot()
        n = {
            k: sum(1 for b in s["builds"] if b["status"] == k and b.get("kind") != "fix")
            for k in ("running", "queued")
        }
        return {
            "api_version": API_VERSION,
            "paused": s["status"]["paused"],
            "running": n["running"],
            "queued": n["queued"],
            "revising": sum(
                1 for b in s["builds"] if b.get("kind") == "fix" and b["status"] == "running"
            ),
            "needs_you": sum(1 for b in s["builds"] if b["status"] in ("ready", "held", "failed"))
            + needs_rows(s["maintain"]["rows"], s.get("project_asks") or []),
            "open_ideas": sum(1 for i in s["ideas"] if i["status"] == "open"),
            "onboarding": list(s["maintain"].get("onboarding") or []),
        }

    # ---- a product's Builds (ui/runs.py): one record per attempt -------------------------------
    def _idea_of(self, lp, d: Path) -> dict | None:
        return next(
            (
                i
                for i in lp.ledger.all()
                if i.get("task_dir") and Path(i["task_dir"]).resolve() == d.resolve()
            ),
            None,
        )

    def build_runs(self, d: Path) -> dict:
        """``api/build/runs``: every Build of the product at ``d``, newest first."""
        from .runs import runs

        lp = self.loop()
        idea = self._idea_of(lp, d)
        hist = list(reversed(lp.ledger.history(idea["id"]))) if idea else []
        return {"key": self.key(d), "runs": list(reversed(runs(d, idea, hist)))}

    def build_run(self, d: Path, n) -> dict:
        """``api/run``: one Build, its stages and why each did not run, its repair rounds."""
        from .runs import run

        try:
            n = int(n)
        except (TypeError, ValueError):
            raise HttpError(400, "n must be a Build's number")
        lp = self.loop()
        idea = self._idea_of(lp, d)
        hist = list(reversed(lp.ledger.history(idea["id"]))) if idea else []
        try:
            return {"key": self.key(d), **run(d, n, idea, hist)}
        except LookupError as e:
            raise HttpError(404, str(e))

    def runs_brief(self, d: Path, idea: dict | None) -> dict | None:
        """The row's Builds in brief: how many, and the latest's record (the list says
        "Build 10 stopped by you" from it). None for a folder with no Build yet."""
        from .runs import runs

        hist = []
        if idea:
            try:
                hist = list(reversed(self.loop().ledger.history(idea["id"])))
            except Exception:  # noqa: BLE001 - the row draws without it
                hist = []
        rs = runs(d, idea, hist)
        return {"n": len(rs), "latest": rs[-1]} if rs else None

    def build_page(self, d: Path) -> dict:
        """One build: the folded state and the v5 view, its row, the loop idea behind it
        with that idea's history (the gate and publish steps)."""
        lp = self.loop()
        idea = next(
            (
                i
                for i in lp.ledger.all()
                if i.get("task_dir") and Path(i["task_dir"]).resolve() == d.resolve()
            ),
            None,
        )
        if idea:
            idea["undo"] = lp.ledger.meta(f"undo:{idea['id']}") or None
        row = self.build_row(d, idea)
        self.project_links(lp, [row])
        s = self.state(d)
        hist = lp.ledger.history(idea["id"]) if idea else []
        return {
            "row": row,
            "state": s,
            "idea": self.idea_row(idea, {str(d.resolve()): row["key"]}) if idea else None,
            "history": [
                {"ts": h["ts"], "state": h["state"], "note": h["note"]}
                for h in reversed(hist)
                if h.get("note")
            ],
        }


# ---- the one function ------------------------------------------------------------------------
_default: App | None = None


def record_get(lp, pid: str) -> dict | None:
    from ..projects.record import Projects

    return Projects(lp.ledger).get(pid)


def configure(
    root: str | Path | None = None,
    harness: str = "claude-code",
    loop_home: str | Path | None = None,
) -> App:
    """The app ``handle`` serves when it is given none. ``root`` defaults to the tasks folder
    (``<home>/tasks``), where the loop's builds and the owner's own are."""
    global _default
    if root is None:
        from ..settings.store import Config

        root = Config(loop_home).tasks_dir
    Path(root).expanduser().mkdir(parents=True, exist_ok=True)
    _default = App(
        Path(root).expanduser(), harness, Path(loop_home).expanduser() if loop_home else None
    )
    # each error kept once by its id, in the home
    rowsafe.configure(loop_home)
    return _default


def default_app() -> App:
    return _default or configure()


def _query(query) -> dict:
    if query is None:
        return {}
    if isinstance(query, (bytes, str)):
        query = parse_qs(query.decode() if isinstance(query, bytes) else query)
    return {k: (v[0] if isinstance(v, list) and v else v) for k, v in dict(query).items()}


def _body(body) -> dict:
    if isinstance(body, dict):
        return body
    if not body:
        return {}
    try:
        out = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HttpError(400, "body must be JSON")
    if not isinstance(out, dict):
        raise HttpError(400, "body must be a JSON object")
    return out


def handle(
    method: str,
    path: str,
    query=None,
    body=None,
    app: App | None = None,
    identity: dict | None = None,
) -> tuple[int, str, bytes]:
    """One request: ``path`` is below the mount point (``/api/builds``, ``/``). ``query``
    is a dict or a query string; ``body`` is the raw JSON body (or a dict). Returns
    ``(status, content_type, body)``. Never raises.

    ``identity``: the caller the server found from the request's token (``{"origin":
    "ui"|"cli"|"agent:<name>", "actions"}``). A write then acts as that caller, whatever the
    body's ``origin`` says (a mismatch is logged). None is a call from inside this process
    (the tests, ``api.require_token: false``): the body's ``origin`` is taken."""
    app = app or default_app()
    try:
        if method in ("GET", "HEAD"):
            if identity is None:  # in-process, or a host that sends none
                return _get(app, path or "/", _query(query))
            return _get(app, path or "/", _query(query), identity)
        if method == "POST":
            return _post(app, path or "/", _body(body), identity)
        return _json(405, {"error": "method not allowed"})
    except HttpError as e:
        return _json(e.code, {"error": str(e)})
    except (LoopError, SystemExit) as e:  # a loop step said no
        return _json(400, {"error": str(e)})
    except Exception as e:  # noqa: BLE001
        # the details go to the log once by the error's id, never to the client; the answer
        # is repo-factory's error JSON: the id and the version
        rec = rowsafe.note(e, f"{method} {_pattern(path)}", kind="response")
        from .. import __version__

        return _json(
            500,
            {
                "error": rowsafe.said(rec["id"]),
                "error_id": rec["id"],
                "exception": rec["exception"],
                "version": __version__,
                "report": f"/api/errors/{rec['id']}",
            },
        )


def _pattern(path: str) -> str:
    """A path as its pattern for the error's record: its words, never an id or a name."""
    parts = [
        p if not p or re.fullmatch(r"[a-z][a-z_-]{0,30}", p) else "…"
        for p in (path or "/").split("?")[0].split("/")
    ]
    return "/".join(parts)[:80]


# what the self-check leaves out of the reads, each with its reason: what reaches GitHub or
# another site, or runs a program, and the live feed
SELFCHECK_EXCLUDED = {
    "/api/github/repos": "asks GitHub",
    "/api/github/user": "asks GitHub",
    "/api/github/app": "asks GitHub",
    "/api/github/app/callback": "GitHub's redirect",
    "/api/github/app/setup": "GitHub's redirect",
    "/api/maintain/summary": "asks GitHub for each repository's policy",
    "/api/maintain/lessons": "searches GitHub's issues",
    "/api/claude/login": "runs claude to read its login",
    "/api/stream": "the live feed: it never ends",
}
SELFCHECK_BUILDS = 50  # the newest builds' pages; their list reads them all


def selfcheck_paths(app: App | None = None) -> list[str]:
    """Every GET of this API the self-check reads: each route that takes nothing (each list's first page, the header, the settings'
    reads), the newest builds' pages and every project's page, never ``SELFCHECK_EXCLUDED``."""
    from urllib.parse import quote

    from .routes import ROUTES

    app = app or default_app()
    out = [
        p
        for method, p, _ in ROUTES
        if method == "GET"
        and isinstance(p, str)
        and p.startswith("/api/")
        and not p.endswith("/")
        and p not in SELFCHECK_EXCLUDED
    ]
    try:
        dirs = sorted(scan(app.root), key=lambda d: -(app.updated(d) or 0))[:SELFCHECK_BUILDS]
        out += [f"/api/build?dir={quote(str(d))}" for d in dirs]
    except Exception as e:  # noqa: BLE001 - the builds list says it
        log.warning("the self-check's build pages: %s", e)
    try:
        from ..projects.record import Projects

        out += [f"/api/project?id={p['id']}" for p in Projects(app.loop().ledger).all()]
    except Exception as e:  # noqa: BLE001 - the projects list says it
        log.warning("the self-check's project pages: %s", e)
    return list(dict.fromkeys(out))


def handle_with_headers(
    method: str,
    path: str,
    query=None,
    body=None,
    app: App | None = None,
    identity: dict | None = None,
) -> tuple[int, str, bytes, dict]:
    """``handle``, plus the response headers the host should add: the page's versioned
    scripts and styles (``static/v<version>/...``) may be cached for good; everything else
    says nothing (the host keeps ``no-store``)."""
    from .routes.static import IMMUTABLE, VERSIONED

    status, ctype, data = handle(method, path, query, body, app, identity)
    headers = {}
    if status == 200 and method in ("GET", "HEAD") and VERSIONED.fullmatch(path or ""):
        headers["cache-control"] = IMMUTABLE
    return status, ctype, data, headers


STREAM_PATH = "/api/stream"


def stream(path: str, query=None, app: App | None = None, last: str | None = None):
    """The live feed (``ui/live.py``): for ``/api/stream``, an iterator of server-sent-event
    bytes that blocks between events, for the host to write as they come; None for every
    other path (``handle`` answers those). ``last``: the ``Last-Event-ID`` a reconnecting
    page sends. When every stream place is taken, a stream that says ``busy`` and ends (the
    page polls). A loopback GET like every read: it
    needs no token and carries no secret."""
    if (path or "").rstrip("/") != STREAM_PATH:
        return None
    from . import live

    q = _query(query)
    seen = q.get("last") or last
    try:
        seen = int(seen) if seen not in (None, "") else None
    except (TypeError, ValueError):
        seen = None
    try:
        return live.HUB.stream(app or default_app(), seen)
    except live.Busy as e:
        return live.busy_events(str(e))


def caller(identity: dict | None, body: dict, path: str) -> str | None:
    """The origin a write acts as: the token's (``identity``), never the body's; a body that
    claims another is logged, without any token. None: no identity (an in-process call)."""
    if identity is None:
        return None
    origin = str(identity.get("origin") or "")
    from .. import registry as R

    if not R.valid_origin(origin) or origin in R.LOOP_ORIGINS:
        raise HttpError(403, "that token does not act here")
    claimed = body.get("origin")
    if claimed is not None and claimed != origin:
        log.warning(
            "POST %s: the body says origin %r, the token is %s: acting as %s",
            path,
            str(claimed)[:40],
            origin,
            origin,
        )
    if origin not in ("ui", "cli") and path != "/api/act":
        raise HttpError(403, f"the {origin} token only runs actions (POST api/act)")
    return origin


def _get(app: App, path: str, q: dict, identity: dict | None = None) -> tuple[int, str, bytes]:
    return route(ROUTES, app, Req("GET", path, q, identity))


def _post(app: App, path: str, body: dict, identity: dict | None = None) -> tuple[int, str, bytes]:
    origin = caller(identity, body, path)
    if origin is not None and path.startswith("/api/loop/"):
        body = {**body, "origin": origin}
    return route(ROUTES, app, Req("POST", path, body, identity))

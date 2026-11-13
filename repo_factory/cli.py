"""``repo-factory``: the command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__

LEDGER_COLUMNS = [
    "ts",
    "build",
    "caller",
    "purpose",
    "category",
    "model",
    "node",
    "milestone",
    "in_tokens",
    "out_tokens",
    "usd",
    "session_usd",
    "base_usd",
    "wall_s",
    "ok",
    "error",
]


def cmd_costs(args):
    """The cost ledger of a build's den (every ``claude -p`` call of the build): per call as
    CSV, or a summary as JSON."""
    import csv

    from .costs import ledger

    p = Path(args.ledger or args.taskdir or ".")
    p = p / ".den" if (p / ".den").is_dir() else p
    recs = ledger.read(p, args.build)
    if args.summary:
        print(json.dumps(ledger.summarize(recs), indent=2))
        return
    out = open(args.out, "w", newline="") if args.out else sys.stdout
    w = csv.writer(out)
    w.writerow(LEDGER_COLUMNS)
    for r in recs:
        w.writerow([r.get(k) for k in LEDGER_COLUMNS])
    if args.out:
        out.close()


def _renderer(args):
    from .progress import Renderer

    return Renderer(interactive=getattr(args, "owner", None) == "human")


def _finish(args, out: dict) -> None:
    """``--json``: the result dict on stdout; else the plain-English summary."""
    from .events import load_state
    from .progress import summary

    if args.json:
        print(json.dumps(out, indent=2))
        return
    st = load_state(Path(out["events"]), build=out["round"])
    print("\n" + summary(st, args.taskdir))


def _friendly(renderer, fn, *a, **kw):
    """Run a build; on failure print one error line (unless the renderer already did) and
    exit 1 without a traceback (``REPO_FACTORY_DEBUG=1`` re-raises)."""
    import os

    try:
        return fn(*a, **kw)
    except Exception as e:
        if os.environ.get("REPO_FACTORY_DEBUG"):
            raise
        if not renderer.failed:
            print(f"✗ {type(e).__name__}: {e}", file=sys.stderr)
        print("Rerun with REPO_FACTORY_DEBUG=1 for a traceback.", file=sys.stderr)
        sys.exit(1)


def cmd_build(args):
    from .policy import resolve_policy

    den = Path(args.den) if args.den != "den" else None
    r = _renderer(args)
    policy = resolve_policy(args.policy)
    from .v5.pipeline import is_v5

    if not is_v5(policy):
        sys.exit(
            f"repo-factory build: {policy} is not a v5 policy (rules.yaml pipeline: v5); "
            "the bundled ones are policies/v5 and policies/lean"
        )
    if args.harness == "fake":  # v5 runs real sessions: never spend under "fake"
        sys.exit(
            "repo-factory build: a build has no fake harness (it runs real Claude Code "
            "sessions); drop --harness fake"
        )
    from .v5.pipeline import build as build_v5

    out = _friendly(
        r,
        build_v5,
        Path(args.taskdir),
        den=den,
        owner=args.owner,
        policy=policy,
        max_usd=args.max_cost,
        out=Path(args.out) if args.out else None,
        subscribers=[r],
        resume=getattr(args, "resume", None),
        carry=not getattr(args, "fresh", False),
        **({"from_round": Path(args.prebuilt)} if getattr(args, "prebuilt", None) else {}),
    )
    _finish(args, out)


def _v5_task(taskdir: Path, den: Path | None) -> bool:
    """The task's latest round is a v5 round (it has ``<den>/v5/r<N>/``)."""
    v5 = (den or Path(taskdir) / ".den") / "v5"
    return v5.is_dir() and any(p.name[1:].isdigit() for p in v5.glob("r*"))


def cmd_revise(args):
    den = Path(args.den) if args.den != "den" else None
    r = _renderer(args)
    if not _v5_task(Path(args.taskdir), den):
        sys.exit(
            f"repo-factory revise: {args.taskdir} has no build yet (repo-factory build it first)"
        )
    from .v5 import change

    note = args.note or ""
    holds = None
    if args.note_file:
        nf = Path(args.note_file)
        note = sys.stdin.read() if args.note_file == "-" else nf.read_text()
        hf = nf.with_suffix(".holds.json")
        if args.note_file != "-" and hf.is_file():
            holds = json.loads(hf.read_text())
    if args.resume is None and not note.strip():
        sys.exit("repo-factory revise: say what should change (the note, or --note-file)")
    if args.harness == "fake":
        sys.exit("repo-factory revise: a revise has no fake harness (it runs a real session)")
    out = _friendly(
        r,
        change.revise,
        Path(args.taskdir),
        note,
        mode=args.mode,
        origin=args.origin,
        den=den,
        resume=args.resume,
        subscribers=[r],
        holds=holds,
    )
    if out.get("failed"):
        sys.exit(f"repo-factory revise: {out['failed']['stage']}: {out['failed']['reason']}")
    print(
        f"Revise round {out['round']} of round {out['revises']} ({out['mode']}): "
        f"{'delivered on ' + out['branch'] if out['finished'] else 'saved'}"
    )


def cmd_status(args):
    from .events import load_state, resolve_log
    from .progress import status_text

    target = args.den if args.target is None and args.den != "den" else (args.target or ".")
    st = load_state(target)
    if args.json:
        print(json.dumps(st, indent=2))
        return
    if st is None:
        print(
            f"No build events at {resolve_log(target)}. Start one with: repo-factory build <taskdir>"
        )
        return
    print(status_text(st, st.get("taskdir")))


def cmd_answer(args):
    from .owner import write_answer
    from .tasks import load_config

    cfg = load_config(Path(args.taskdir))
    den = Path(args.den) if args.den != "den" else cfg.taskdir / ".den"
    qdir = den / "human"
    waiting = (
        sorted(
            p.name[len(cfg.id) + 1 : -len(".question")] for p in qdir.glob(f"{cfg.id}.*.question")
        )
        if qdir.is_dir()
        else []
    )
    if args.qid not in waiting:
        msg = f"no question {args.qid!r} is waiting for {cfg.id}"
        sys.exit(
            msg
            + (
                f" (waiting: {', '.join(waiting)})"
                if waiting
                else " (nothing is waiting; see repo-factory status)"
            )
        )
    write_answer(qdir, cfg.id, args.qid, args.text)
    what = (
        f"answered {args.qid}"
        if args.text.strip()
        else f"skipped {args.qid} (no preference; the workers will use their judgement)"
    )
    print(f"{cfg.id}: {what}", file=sys.stderr)


from .tasks import NEW_TASK_BUDGET_USD, new_task  # noqa: E402


def cmd_new(args):
    from .progress import short_path

    dest = new_task(
        args.description,
        Path(args.dir) if args.dir else None,
        Path(args.seed) if args.seed else None,
        Path(args.tests) if args.tests else None,
        args.budget_usd,
        spec_text=(sys.stdin.read() if args.spec == "-" else Path(args.spec).read_text())
        if args.spec
        else None,
    )
    d = short_path(dest)
    has = [
        x
        for x, ok in (("seed code in repo/", args.seed), ("your tests in tests/", args.tests))
        if ok
    ]
    print(f"Created task {d}/ (spec.md, task.yaml" + "".join(", " + h for h in has) + ").")
    print()
    print("Next:")
    print(f"  1. Describe what you want:   $EDITOR {d}/spec.md")
    if not args.tests:
        print(f"     (optional) add pytest tests that must pass under {d}/tests/")
    print(f"  2. Build it:                 repo-factory build {d}")
    print(f"  3. Check on it any time:     repo-factory status {d}")


def _delivered_node(den, task_id: str) -> int | None:
    """The node delivered by the task's latest round (its stop node's ``deliver``), if the den
    records one."""
    stops = [n for n in den.nodes(task_id) if n["kind"] == "stop"]
    return stops[-1]["action"].get("deliver") if stops else None


def cmd_verdict(args):
    from .den.store import Den
    from .tasks import load_config

    task_id, den = args.task, Path(args.den)
    if Path(args.task).is_dir():
        cfg = load_config(Path(args.task))
        task_id = cfg.id
        den = den if args.den != "den" else cfg.taskdir / ".den"
    d = Den(den)
    node = args.node if args.node is not None else _delivered_node(d, task_id)
    value = {"verdict": args.verdict, "note": args.note, "source": "human"}
    d.add_verdict(task_id, node, "owner_verdict", value)
    from .events import EventLog, latest_build, log_path, read_events

    build_id = latest_build(read_events(log_path(d.root)))
    if build_id is not None:
        EventLog(log_path(d.root), task_id, build_id).emit("verdict", node=node, **value)
    print(json.dumps({"task": task_id, "node": node, **value}))
    hint = ""
    if args.verdict == "edit":
        hint = (
            f"\nTo have repo-factory make the change: repo-factory revise {args.task} "
            f'"{args.note or "what to change"}"'
        )
    print(f"Recorded {args.verdict} for {task_id} (node {node}).{hint}", file=sys.stderr)


def cmd_ui(args):
    """The page on 127.0.0.1 and the loop daemon, in one process."""
    from .settings.store import Config
    from .ui.server import serve

    cfg = Config(args.home).ensure()
    root = Path(args.root).expanduser().resolve() if args.root else cfg.tasks_dir
    root.mkdir(parents=True, exist_ok=True)
    serve(root, args.port, args.harness, cfg.home, loop=not args.no_loop)


def cmd_doctor(args):
    """One line each: Claude Code signed in, bubblewrap usable, git, gh logged in. Exit 1 when
    claude, bubblewrap or git is missing (gh is only for publishing)."""
    from .doctor import run

    sys.exit(run())


def _origin(text: str) -> str:
    """``repo-factory revise --origin``: the loop passes the door that asked for the round, an agent's
    (``agent:<name>``) too."""
    from .loop.cli import origin_arg

    return origin_arg(text)


def cmd_link(args):
    from .ui.server import sign_in_link

    print("Open this link to sign a browser in (once, within 10 minutes):")
    print(sign_in_link(Path(args.home).expanduser() if args.home else None))


def main(argv=None):
    av = sys.argv[1:] if argv is None else list(argv)
    p = argparse.ArgumentParser(
        prog="repo-factory",
        description="A typed task through a brief, a sandboxed Claude Code build, checks and a "
        "gate, to a private GitHub repository published on your press. "
        "'repo-factory <command> --help' for one command.",
    )
    p.add_argument("--version", action="version", version=f"repo-factory {__version__}")
    p.add_argument("--den", default="den", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    dr = sub.add_parser(
        "doctor", help="check what repo-factory needs: Claude Code signed in, bubblewrap, git, gh"
    )
    dr.set_defaults(fn=cmd_doctor)

    c = sub.add_parser(
        "costs", help="every model call of a build (its cost ledger) as CSV, or a summary"
    )
    c.add_argument("taskdir", nargs="?", default=None, help="the task folder (default: .)")
    c.add_argument("--out", default=None)
    c.add_argument("--ledger", default=None, metavar="TASKDIR_OR_DEN", help=argparse.SUPPRESS)
    c.add_argument("--build", type=int, default=None, help="one build only")
    c.add_argument("--summary", action="store_true", help="totals by part and by caller, as JSON")
    c.set_defaults(fn=cmd_costs)

    def factory_opts(sp):
        sp.add_argument(
            "--harness",
            default="claude-code",
            choices=["fake", "claude-code"],
            help=argparse.SUPPRESS,
        )
        sp.add_argument(
            "--policy",
            default=None,
            help="default: policies/v5; policies/lean is the lean flow (unproven)",
        )
        sp.add_argument(
            "--max-cost",
            type=float,
            default=None,
            help="USD cap on the build (default: task.yaml budget_usd)",
        )
        sp.add_argument("--out", default=None, help="delivery repo (default: <taskdir>/out)")
        sp.add_argument(
            "--json", action="store_true", help="print the result as JSON instead of the summary"
        )

    b = sub.add_parser("build", help="build a task end to end (a branch and REPORT.md)")
    b.add_argument("taskdir")
    b.add_argument(
        "--owner",
        choices=["human", "queue", "none"],
        default="human",
        help="who answers questions: human (terminal), queue (files under "
        "<den>/human, answered with `repo-factory answer`), none (nobody is "
        "asked)",
    )
    b.add_argument(
        "--resume",
        type=int,
        default=None,
        metavar="ROUND",
        help="continue round ROUND from its saved state (after a crash or a stop) "
        "instead of starting a new build",
    )
    b.add_argument(
        "--fresh",
        action="store_true",
        help="a new round starts from nothing, even when an earlier round's build "
        "stage stopped with work in its tree (by default a new round continues "
        "that work)",
    )
    b.add_argument(
        "--prebuilt",
        default=None,
        metavar="ROUND_DIR",
        help="start after the build with an earlier round's plan and build "
        "(ROUND_DIR is its <den>/v5/r<N>): two flows compared on one plan and "
        "one build",
    )
    factory_opts(b)
    b.set_defaults(fn=cmd_build)

    rv = sub.add_parser("revise", help="one more round on the delivered code, from your note")
    rv.add_argument("taskdir")
    rv.add_argument("note", nargs="?", default=None, help="what to change, in plain words")
    rv.add_argument(
        "--note-file",
        default=None,
        help="read the note from this file ('-' = stdin); a <name>.holds.json "
        "beside it carries the loop's hold reasons",
    )
    rv.add_argument(
        "--mode",
        choices=["auto", "docs", "code"],
        default="auto",
        help="docs changes Markdown only; code changes code and docs; auto "
        "picks docs when only the pre-push wording checks held it",
    )
    rv.add_argument("--origin", type=_origin, default="cli", help=argparse.SUPPRESS)
    rv.add_argument(
        "--resume",
        type=int,
        default=None,
        metavar="ROUND",
        help="continue revise round ROUND from its saved state",
    )
    factory_opts(rv)
    rv.set_defaults(fn=cmd_revise)

    nw = sub.add_parser("new", help="start a task folder from a one-sentence description")
    nw.add_argument("description")
    nw.add_argument("--dir", default=None, help="task folder (default: ./<slug>)")
    nw.add_argument("--seed", default=None, help="existing code to start from (copied to repo/)")
    nw.add_argument("--tests", default=None, help="pytest tests that must pass (copied to tests/)")
    nw.add_argument(
        "--budget-usd",
        type=float,
        default=NEW_TASK_BUDGET_USD,
        help="optional ceiling on worker spend per build (default: unlimited)",
    )
    nw.add_argument(
        "--spec",
        default=None,
        help="file holding the full spec.md text ('-' = stdin); the description "
        "then only names the task",
    )
    nw.set_defaults(fn=cmd_new)

    st = sub.add_parser("status", help="state of the latest build of a task")
    st.add_argument(
        "target", nargs="?", default=None, help="task dir, den dir or events.jsonl (default: .)"
    )
    st.add_argument("--json", action="store_true", help="print the folded state as JSON")
    st.set_defaults(fn=cmd_status)

    an = sub.add_parser(
        "answer", help="answer a waiting question (repo-factory build --owner queue)"
    )
    an.add_argument("taskdir")
    an.add_argument("qid")
    an.add_argument(
        "text",
        nargs="?",
        default="",
        help="your answer; empty = no preference (recorded as no answer)",
    )
    an.set_defaults(fn=cmd_answer)

    v = sub.add_parser("verdict", help="record your verdict on a delivered task")
    v.add_argument("--task", required=True, help="task id, or a task dir (uses <taskdir>/.den)")
    v.add_argument("--node", type=int, default=None, help="default: the delivered node")
    v.add_argument("verdict", choices=["accept", "reject", "edit"])
    v.add_argument("note", nargs="?", default="")
    v.set_defaults(fn=cmd_verdict)

    ui = sub.add_parser(
        "ui", help="the local web page (127.0.0.1 only) and the build loop, in one process"
    )
    ui.add_argument(
        "root",
        nargs="?",
        default=None,
        help="folder holding task dirs (default: the tasks folder, <home>/tasks)",
    )
    ui.add_argument(
        "--home", default=None, help="the home (default: $REPO_FACTORY_HOME, else ~/.repo-factory)"
    )
    ui.add_argument(
        "--port",
        type=int,
        default=8790,
        help="default 8790; if busy, the next free port is used and printed",
    )
    ui.add_argument(
        "--harness", default="claude-code", choices=["fake", "claude-code"], help=argparse.SUPPRESS
    )
    ui.add_argument(
        "--no-loop",
        action="store_true",
        help="serve the page only; no builds start (another process runs the loop)",
    )
    ui.set_defaults(fn=cmd_ui)

    lk = sub.add_parser(
        "link",
        help="a new one-time sign-in link for the running page (for "
        "another browser, or when the first one expired)",
    )
    lk.add_argument(
        "--home", default=None, help="the home (default: $REPO_FACTORY_HOME, else ~/.repo-factory)"
    )
    lk.set_defaults(fn=cmd_link)

    from .loop.cli import add_loop_group

    add_loop_group(sub)  # `repo-factory loop ...`: tasks in, published repositories out

    # in the tree, off and unlisted until a later version turns them on
    from .lessons.cli import add_lessons_group

    add_lessons_group(sub)
    from .maintain.cli import add_maintain_group

    add_maintain_group(sub)
    from .projects.cli import add_projects_group

    add_projects_group(sub)
    from .github_cli import add_github_group

    add_github_group(sub)
    # argparse lists a subcommand registered with help=SUPPRESS as "==SUPPRESS==": leave the
    # hidden ones out of the listing altogether (they still run when named)
    sub._choices_actions = [a for a in sub._choices_actions if a.dest not in HIDDEN]
    sub.metavar = "{" + ",".join(n for n in sub.choices if n not in HIDDEN) + "}"

    args = p.parse_args(av)
    args.argv = argv
    if args.cmd in MEASURING:
        # a measurement stays honest: the lessons loop (and every build it starts) uses the
        # shipped prompts, never the owner's customisations
        from .custom_prompts import use_defaults

        use_defaults()
    from .errors import as_exit

    as_exit(args.fn)(args)  # a step that said no (LoopError): its words, exit 1


# The commands that measure the factory (custom_prompts: defaults only)
MEASURING = ("lessons",)
# In the tree but off and unlisted until a later version turns them on
HIDDEN = {"lessons", "maintain", "projects", "github"}


if __name__ == "__main__":
    main()

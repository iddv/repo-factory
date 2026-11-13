"""``repo-factory loop ...``: the closed loop's command line (docs/loop.md).

repo-factory loop add [--file spec.md] TEXT       a task into the ledger (its brief is written
                                               by the daemon)
repo-factory loop pitch [ID ...] [--limit N]      write the brief of new tasks now (or these)
repo-factory loop repitch ID [--later]            write a rejected task's brief again
repo-factory loop list [--state S] | show ID      the ledger
repo-factory loop run [--once|--until-idle] [--only ID ...]   the daemon
repo-factory loop status [--json]                 what runs, what waits, spend today and this week
repo-factory loop pause | resume | stop           the kill switch (stop also stops running builds)
repo-factory loop send ID [--build-anyway]        send a task to build again
repo-factory loop close ID --reason "..."         say no: close a task or its build
repo-factory loop edit ID --file F|-              replace a task's text before its build starts
repo-factory loop retry ID [--rebuild] [--note T] a held build goes again
repo-factory loop revise ID --file F|--text T [--mode auto|docs|code]   Ask for changes
repo-factory loop stop-round ID | resume-build ID | cancel ID   one build
repo-factory loop restart ID --file F|--text T [--mode M]   a running revise round, new instructions
repo-factory loop gate ID | publish ID            run those steps by hand
repo-factory loop approve ID                      make a published (private) repo public
repo-factory loop publish-anyway ID --reason R --yes   publish a held build over its holds (private)
repo-factory loop precheck ID                     run a held or ready build's pre-push checks now
repo-factory loop recheck ID                      re-check a build with the installed version
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def add_loop_group(sub) -> None:
    lp = sub.add_parser("loop", help="the build loop: tasks in, published repositories out")
    lp.add_argument(
        "--home", default=None, help="the home (default: $REPO_FACTORY_HOME, else ~/.repo-factory)"
    )
    ls = lp.add_subparsers(dest="loop_cmd", required=True)

    a = ls.add_parser("add", help="add a task: what to build, in plain words")
    a.add_argument("text", nargs="*")
    a.add_argument("--file", default=None, help="read the task from this file ('-' = stdin)")
    a.add_argument("--source", default="cli", help=argparse.SUPPRESS)
    a.set_defaults(fn=cmd_add)

    p = ls.add_parser("pitch", help="write the brief of new tasks now (one no-tools call each)")
    p.add_argument("ids", nargs="*")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_pitch)

    rp = ls.add_parser("repitch", help="write a rejected (or failed) task's brief again")
    rp.add_argument("id")
    rp.add_argument("--later", action="store_true", help="mark it new; the daemon writes it")
    rp.set_defaults(fn=cmd_repitch)

    li = ls.add_parser("list", help="the tasks in the ledger")
    li.add_argument("--state", default=None)
    li.add_argument("--json", action="store_true")
    li.set_defaults(fn=cmd_list)

    sh = ls.add_parser("show", help="one task, with its history, as JSON")
    sh.add_argument("id")
    sh.set_defaults(fn=cmd_show)

    r = ls.add_parser("run", help="the daemon: watch, publish, brief, build")
    r.add_argument("--once", action="store_true", help="one tick, then exit")
    r.add_argument(
        "--until-idle",
        action="store_true",
        help="exit once nothing is queued, building or waiting to publish",
    )
    r.add_argument(
        "--only",
        nargs="*",
        default=None,
        metavar="ID",
        help="brief, build and publish only these tasks (id prefixes)",
    )
    r.set_defaults(fn=cmd_run)

    st = ls.add_parser("status", help="the loop at a glance")
    st.add_argument("--json", action="store_true")
    st.set_defaults(fn=cmd_status)

    for name, fn, help_ in (
        ("pause", cmd_pause, "kill switch: no new briefs or builds"),
        ("resume", cmd_resume, "undo pause"),
        (
            "stop",
            cmd_stop,
            "stop the daemon and the running builds (their rounds resume on the next run)",
        ),
    ):
        x = ls.add_parser(name, help=help_)
        x.set_defaults(fn=fn)

    sd = ls.add_parser(
        "send", help="send a task to build again; its brief is written on the next tick"
    )
    sd.add_argument("id")
    sd.add_argument(
        "--build-anyway",
        action="store_true",
        help="build a task the brief passed on, from its product and brief",
    )
    sd.set_defaults(fn=cmd_send)

    cl = ls.add_parser(
        "close",
        help="say no: close a task or its build (stops a running build); it leaves 'needs you'",
    )
    cl.add_argument("id")
    cl.add_argument("--reason", required=True, help="why, in a few words (kept in the history)")
    cl.set_defaults(fn=cmd_close)

    rj = ls.add_parser(
        "reject",
        help="say no to a build, with reason chips: it is closed and the chips stay in its history",
    )
    rj.add_argument("id")
    rj.add_argument(
        "--reason",
        action="append",
        default=[],
        help="a reason chip (wrong scope, bugs, ...); repeat for more",
    )
    rj.add_argument("--note", default="", help="a few words more")
    rj.set_defaults(fn=cmd_reject)

    kp = ls.add_parser(
        "keep",
        help="Keep private: a published repo stays private and leaves "
        "Needs you (approve still makes it public later)",
    )
    kp.add_argument("id")
    kp.set_defaults(fn=cmd_keep)

    lt = ls.add_parser(
        "later", help="put an open task off: no brief, not waiting for you, for some days"
    )
    lt.add_argument("id")
    lt.add_argument("--days", type=float, default=7.0)
    lt.set_defaults(fn=cmd_later)

    ed = ls.add_parser("edit", help="replace a task's text before its build starts")
    ed.add_argument("id")
    ed.add_argument("--file", required=True, help="read the new text from this file ('-' = stdin)")
    ed.set_defaults(fn=cmd_edit)

    rt = ls.add_parser(
        "retry",
        help="Build again, or Resume Build N: a held build goes again "
        "(resume, rebuild or re-publish)",
    )
    rt.add_argument("id")
    rt.add_argument("--rebuild", action="store_true", help="always a fresh build")
    rt.add_argument(
        "--note",
        default=None,
        help="one line kept with the retry in the task's history (why it is retried)",
    )
    rt.set_defaults(fn=cmd_retry)

    rv = ls.add_parser(
        "revise",
        help="Ask for changes: a revise round of a held or ready build that has no repo yet",
    )
    rv.add_argument("id")
    src = rv.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="read what should change from this file ('-' = stdin)")
    src.add_argument("--text", help="what should change, in plain words")
    rv.add_argument(
        "--mode",
        choices=["auto", "docs", "code"],
        default="auto",
        help="docs: Markdown only; code: code and docs; auto: docs when only the "
        "pre-push wording checks held it",
    )
    rv.set_defaults(fn=cmd_revise)

    # one build's controls: none waits; the loop's watch finishes a stop once it is gone
    sr = ls.add_parser(
        "stop-round",
        help="stop one build's round: its workers are killed, the "
        "round is kept (only resume-build continues it) and "
        "it goes back to held or ready as before the round",
    )
    sr.add_argument("id")
    sr.set_defaults(fn=cmd_stop_round)
    rs = ls.add_parser(
        "resume-build", help="queue a round you stopped to continue where it stopped"
    )
    rs.add_argument("id")
    rs.set_defaults(fn=cmd_resume_build)
    cn = ls.add_parser("cancel", help="withdraw a queued build before it starts")
    cn.add_argument("id")
    cn.set_defaults(fn=cmd_cancel)
    rr = ls.add_parser(
        "restart",
        help="stop a running revise round and ask for changes again with new instructions",
    )
    rr.add_argument("id")
    rsrc = rr.add_mutually_exclusive_group(required=True)
    rsrc.add_argument("--file", help="read the new instructions from this file ('-' = stdin)")
    rsrc.add_argument("--text", help="the new instructions, in plain words")
    rr.add_argument("--mode", choices=["auto", "docs", "code"], default="auto")
    rr.set_defaults(fn=cmd_restart)
    ud = ls.add_parser(
        "undo",
        help="undo the last stop-round or cancel of a build, within "
        "seconds of it (the page's Undo)",
    )
    ud.add_argument("id")
    ud.add_argument(
        "--of",
        choices=["stop_round", "cancel"],
        default=None,
        help="the control to undo (default: the last one)",
    )
    ud.set_defaults(fn=cmd_undo)

    g = ls.add_parser("gate", help="run the release gate on a build again")
    g.add_argument("id")
    g.set_defaults(fn=cmd_gate)

    pb = ls.add_parser("publish", help="publish a ready build now (a private repo)")
    pb.add_argument("id")
    pb.set_defaults(fn=cmd_publish)

    ap = ls.add_parser(
        "approve", help="Make public: the approval gate; a published repo goes public"
    )
    ap.add_argument("id")
    ap.set_defaults(fn=cmd_approve)

    pa = ls.add_parser(
        "publish-anyway",
        help="publish a held build over its holds, as a private repo (the override is disclosed)",
    )
    pa.add_argument("id")
    pa.add_argument(
        "--reason",
        required=True,
        help="why, in 12 to 300 characters (kept in the "
        "history, the NOTICE says what was overridden)",
    )
    pa.add_argument("--yes", action="store_true", help="confirm: it publishes over a hold")
    pa.add_argument(
        "--holds",
        default=None,
        help="the holds you were shown, comma-separated: refused if they changed",
    )
    pa.set_defaults(fn=cmd_publish_anyway)

    pc = ls.add_parser(
        "precheck",
        help="run the pre-push checks of a held or ready build now "
        "(the loop runs them when a build stops)",
    )
    pc.add_argument("id")
    pc.add_argument("--json", action="store_true")
    pc.set_defaults(fn=cmd_precheck)

    rc = ls.add_parser(
        "recheck",
        help="re-check a held or ready build with the installed "
        "repo-factory (no model); changes nothing",
    )
    rc.add_argument("id")
    rc.add_argument("--json", action="store_true")
    rc.set_defaults(fn=cmd_recheck)

    # Claims: what a claim means (read at build start), and the release text
    # the owner sets (used as it is); none waits on a model
    cm = ls.add_parser("claim", help="accept or edit how one of a build's claims is read")
    cm.add_argument("id")
    cmw = cm.add_mutually_exclusive_group(required=True)
    cmw.add_argument("--accept", metavar="K", help="accept claim K's reading as it is")
    cmw.add_argument("--edit", metavar="K", help="set claim K's reading (--text or --file)")
    cm.add_argument("--text", help="the reading, in plain words")
    cm.add_argument("--file", help="read the reading from this file ('-' = stdin)")
    cm.add_argument("--default", default=None, help="the default of the claim's setting")
    cm.set_defaults(fn=cmd_claim)
    rl = ls.add_parser(
        "release-text",
        help="set repo-factory's one-line text about a build (the repo "
        "description and the release notes' first line)",
    )
    rl.add_argument("id")
    rls = rl.add_mutually_exclusive_group(required=True)
    rls.add_argument("--file", help="read it from this file ('-' = stdin)")
    rls.add_argument("--text", help="the text, one line")
    rl.set_defaults(fn=cmd_release_text)

    # the owner's verbs say which door they came through (the registry's origins); every
    # history row they write carries it
    for x in (a, rp, sd, cl, rj, kp, lt, ed, rt, ap, pb, pa, rv, sr, rs, cn, rr, ud, rc, cm, rl):
        x.add_argument("--origin", type=origin_arg, default="cli", help=argparse.SUPPRESS)


ORIGINS = ("ui", "cli", "honesty", "auto")  # registry.ORIGINS


def origin_arg(text: str) -> str:
    """``--origin``: one of the registry's origins, or ``agent:<name>``."""
    from ..registry import valid_origin

    if not valid_origin(text):
        raise argparse.ArgumentTypeError(f"origin is one of {', '.join(ORIGINS)}, or agent:<name>")
    return text


# The daemon starts a home wherever it is told; every other verb starts the default or
# configured home ($REPO_FACTORY_HOME, else ~/.repo-factory) on first use, as repo-factory ui
# does, so `repo-factory loop add "..."` works as a first command. A --home that does not
# exist is refused (a typo never leaves an empty second home behind).
CREATES_HOME = ("run",)


def _loop(args):
    from ..settings.store import Config, home
    from .daemon import Loop

    where = home(args.home)
    if not where.is_dir():
        if args.home and getattr(args, "loop_cmd", None) not in CREATES_HOME:
            raise SystemExit(
                f"repo-factory loop: no home at {where}; check --home, or leave "
                "it out to use the default home"
            )
        where.mkdir(parents=True, exist_ok=True)
    lp = Loop(Config(args.home))
    lp.ledger.origin = getattr(args, "origin", None)
    return lp


def money(x) -> str:
    return f"${float(x or 0):,.2f}"


def line(i: dict) -> str:
    bits = [i["id"], f"{i['state']:<9}", i.get("title") or ""]
    p = i.get("pitch") or {}
    if p.get("product_type"):
        bits.append(f"[{p['product_type']}, buildability {p.get('buildability')}]")
    if i.get("reason") and i["state"] in ("rejected", "held", "error", "skipped", "closed"):
        bits.append(f"- {i['reason']}")
    if (i.get("publish") or {}).get("url"):
        bits.append(i["publish"]["url"])
    return "  ".join(str(b) for b in bits)


def cmd_add(args):
    text = " ".join(args.text).strip()
    if args.file:
        text = (sys.stdin.read() if args.file == "-" else Path(args.file).read_text()).strip()
    lp = _loop(args)
    try:
        idea, new = lp.add(text, args.source)
    except ValueError as e:
        sys.exit(f"repo-factory loop add: {e}")
    print(("Added " if new else "Already in the ledger: ") + line(idea))
    if new:
        print(
            "The daemon writes its brief on its next tick (or now: repo-factory loop pitch "
            f"{idea['id']})."
        )


def cmd_pitch(args):
    lp = _loop(args)
    res = lp.pitch(args.ids or None, args.limit)
    if args.json:
        print(json.dumps(res, indent=1, default=str))
        return
    if not res:
        print("Nothing to brief (no new tasks).")
    for i in res:
        print(line(i))
        p = i.get("pitch") or {}
        if i["state"] == "queued":
            print(f"    brief: {p.get('brief')}")
            print(f"    task:  {i['task_dir']}")


def cmd_repitch(args):
    i = _loop(args).repitch(args.id, now=not args.later)
    print(line(i))


def cmd_list(args):
    ideas = _loop(args).ledger.all(args.state)
    if args.json:
        print(json.dumps(ideas, indent=1, default=str))
        return
    for i in ideas:
        print(line(i))
    if not ideas:
        print('No tasks yet. Add one: repo-factory loop add "<what to build>".')


def cmd_show(args):
    lp = _loop(args)
    i = lp.ledger.must(args.id)
    i["history"] = lp.ledger.history(i["id"])
    print(json.dumps(i, indent=1, default=str))


def cmd_run(args):
    _loop(args).run(once=args.once, until_idle=args.until_idle, only=args.only)


def cmd_status(args):
    s = _loop(args).status()
    if args.json:
        print(json.dumps(s, indent=1, default=str))
        return
    d = s["daemon_pid"]
    print(
        f"Home {s['home']}: "
        + ("PAUSED" if s["paused"] else "not paused")
        + (f"; daemon pid {d}" if d else "; no daemon running (repo-factory loop run)")
    )
    sp = s["spend"]
    print(
        f"Spend: today {money(sp['today']['total'])}, this week {money(sp['week']['total'])}"
        + (f" (daily cap {money(s['daily_usd'])})" if s["daily_usd"] is not None else " (no cap)")
    )
    print("Tasks: " + (", ".join(f"{k} {v}" for k, v in sorted(s["counts"].items())) or "none"))
    for r in s["running"]:
        print(
            f"  building {r['id']} {r['title']}: {r.get('stage') or r.get('phase') or '?'}, "
            f"{money(r.get('cost_usd'))} so far"
        )
    for a in s["awaiting_approval"]:
        print(
            f"  waiting for approval {a['id']} {a['title']}: {a['url']} "
            f"(repo-factory loop approve {a['id']})"
        )
    for h in s["held"]:
        print(f"  held {h['id']} {h['title']}: {h['reason']}")
    if s.get("settings_problems"):
        from ..settings.schema import describe, refuses_builds

        pr = s["settings_problems"]
        print(describe(pr) + ("; no build starts until it is fixed" if refuses_builds(pr) else ""))


def cmd_pause(args):
    _loop(args).pause()
    print(
        "Paused: no new briefs or builds; running builds continue. repo-factory loop resume "
        "to undo."
    )


def cmd_resume(args):
    _loop(args).resume()
    print("Resumed.")


def cmd_stop(args):
    stopped = _loop(args).stop()
    print(
        f"Stop requested; stopped {len(stopped)} build(s)"
        + (f": {', '.join(stopped)}" if stopped else "")
        + ". The next repo-factory loop run resumes them."
    )


def cmd_send(args):
    i = _loop(args).send(args.id, build_anyway=args.build_anyway)
    print(line(i))
    if i["state"] == "new":
        print("Sent: the brief is written on the daemon's next tick (repo-factory loop run).")
    elif i["state"] == "queued":
        print(f"Queued to build anyway: task {i['task_dir']}")


def cmd_close(args):
    print(line(_loop(args).close(args.id, args.reason)))


def cmd_reject(args):
    print(line(_loop(args).reject(args.id, args.reason, args.note)))


def cmd_keep(args):
    print(line(_loop(args).keep(args.id)))


def cmd_later(args):
    print(line(_loop(args).later(args.id, args.days)))


def cmd_edit(args):
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
    print(line(_loop(args).edit(args.id, text)))
    print("Brief saved.")


def cmd_retry(args):
    print(line(_loop(args).retry(args.id, rebuild=args.rebuild, note=args.note)))


def cmd_revise(args):
    text = (
        args.text
        if args.text is not None
        else (sys.stdin.read() if args.file == "-" else Path(args.file).read_text())
    )
    i = _loop(args).revise(args.id, text, mode=args.mode, origin=args.origin)
    print(line(i))
    print(
        "Queued: a revise round runs when a build slot is free; the gate and the pre-push "
        "checks run again."
    )


def _controlled(i: dict) -> None:
    print(line(i))
    k = (i.get("build") or {}).get("kill")
    if i["state"] == "building" and k:
        print(
            f"{k['what'].capitalize()} requested: its workers got SIGTERM (SIGKILL in a few "
            "seconds if they are still there); the loop finishes the change once it is gone."
        )


def cmd_stop_round(args):
    _controlled(_loop(args).stop_round(args.id, origin=args.origin))


def cmd_resume_build(args):
    print(line(_loop(args).resume_build(args.id, origin=args.origin)))


def cmd_cancel(args):
    print(line(_loop(args).cancel(args.id, origin=args.origin)))


def cmd_restart(args):
    text = (
        args.text
        if args.text is not None
        else (sys.stdin.read() if args.file == "-" else Path(args.file).read_text())
    )
    _controlled(_loop(args).restart_revise(args.id, text, mode=args.mode, origin=args.origin))


def cmd_undo(args):
    print(line(_loop(args).undo(args.id, of=args.of, origin=args.origin)))


def cmd_gate(args):
    lp = _loop(args)
    i = lp.ledger.must(args.id)
    if i["state"] not in ("held", "ready"):
        sys.exit(f"{i['id']} is {i['state']}; the gate runs on a built idea (held or ready)")
    i = lp.gate(i["id"], repair=False)  # judging again starts no automatic round
    print(line(i))
    for k, v in (i.get("gate") or {}).get("checks", {}).items():
        print(f"  {'ok  ' if v['ok'] else 'FAIL'} {k}: {v['detail']}")


def cmd_publish(args):
    print(line(_loop(args).publish(args.id)))


def cmd_approve(args):
    print(line(_loop(args).approve(args.id)))


def cmd_publish_anyway(args):
    holds = None if args.holds is None else [h for h in args.holds.split(",") if h.strip()]
    i = _loop(args).publish_anyway(
        args.id, args.reason, origin=args.origin, yes=args.yes, holds=holds
    )
    print(line(i))
    if i["state"] == "published":
        print(
            "Published over the hold, as a private repo. repo-factory loop approve makes it public."
        )
    else:
        print("Not published: held again; nothing was pushed.")


def cmd_recheck(args):
    from .recheck import words

    idea = _loop(args).recheck(args.id)
    rec = (idea.get("gate") or {}).get("recheck") or {}
    if args.json:
        print(json.dumps(rec, indent=1, default=str))
        return
    if rec.get("state") == "running":
        print(
            f"Re-checking with repo-factory {rec.get('version')} in the background "
            f"(pid {rec.get('pid')}): the build page shows the result."
        )
    else:
        print(words(rec) or f"The re-check failed: {rec.get('error')}")


def cmd_precheck(args):
    from .precheck import holds

    rec = _loop(args).precheck(args.id)
    if args.json:
        print(json.dumps(rec, indent=1, default=str))
        return
    if rec.get("state") == "done" and "text" in rec:  # a published build's text
        print(
            "The release text on GitHub "
            + ("now says: " + rec["text"] if rec.get("updated") else "is unchanged.")
        )
    elif rec.get("state") == "done":
        hs = holds(rec)
        print(
            "The pre-push checks are done: "
            + ("; ".join(f"{k}: {d}" for k, d in hs) if hs else "nothing holds the push")
        )
    elif rec.get("state") == "running":
        print("The pre-push checks are running in another process.")
    else:
        sys.exit(f"The pre-push checks failed: {rec.get('error')}")


def cmd_claim(args):
    lp = _loop(args)
    if args.accept:
        i = lp.claim_accept(args.id, args.accept, origin=args.origin)
    else:
        text = (
            args.text
            if args.text is not None
            else (
                (sys.stdin.read() if args.file == "-" else Path(args.file).read_text())
                if args.file
                else None
            )
        )
        if text is None:
            sys.exit("repo-factory loop claim --edit K needs --text or --file")
        i = lp.claim_edit(args.id, args.edit, text, args.default, origin=args.origin)
    print(line(i))
    print("Saved.")


def cmd_release_text(args):
    text = (
        args.text
        if args.text is not None
        else (sys.stdin.read() if args.file == "-" else Path(args.file).read_text())
    )
    i = _loop(args).set_release_text(args.id, text, origin=args.origin)
    print(line(i))
    print("Saved; it is used as it is (a published repo gets it in the background).")

"""``repo-factory maintain labels|policy|doctor|tick|status|...`` (off and unlisted in this
version: maintenance of what repo-factory published).

    repo-factory maintain labels REPO [--dry-run] | --print
    repo-factory maintain policy REPO | --file F | --template
    repo-factory maintain doctor PATH | --repo REPO
    repo-factory maintain whoami [--home H] [--json]     the identity in use: login or token, scope,
                                                expiry, maintained repos reached
    repo-factory maintain tick [--home H] [--json]       one poll now (``watch.tick``)
    repo-factory maintain status [--home H] [--json]     what the last poll saw (no GitHub call)
    repo-factory maintain revise REPO N [--home H]       fix issue N now (the tick runs this detached)
    repo-factory maintain recheck REPO [--home H]        read one repo again now (reads only)
    repo-factory maintain setup REPO [--home H]          the setup pull request (the switch runs it)
    repo-factory maintain propose REPO [--home H]        this week's proposals (the tick runs it)

``gh`` runs as the logged-in user (``--gh`` replaces the command, as ``publish.gh_command``)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..errors import host_group

DESCRIBE_MODEL = "claude-sonnet-5-5"


@host_group
def add_maintain_group(sub) -> None:
    import argparse

    mt = sub.add_parser("maintain", help=argparse.SUPPRESS)
    mt.add_argument("--gh", default=None, help='the gh command (default "gh")')
    msub = mt.add_subparsers(dest="maintain_cmd", required=True)

    la = msub.add_parser("labels", help="create or update the label scheme on a repo")
    la.add_argument("repo", nargs="?", default=None, help="owner/name")
    la.add_argument("--dry-run", action="store_true", help="say what would change; change nothing")
    la.add_argument("--print", action="store_true", help="print .github/labels.yml and exit")
    la.set_defaults(fn=cmd_labels)

    po = msub.add_parser(
        "policy", help="the effective .github/repo-factory.yml of a repo or a file"
    )
    po.add_argument("repo", nargs="?", default=None)
    po.add_argument("--file", default=None, help="a local repo-factory.yml to validate")
    po.add_argument("--template", action="store_true", help="print the default repo-factory.yml")
    po.set_defaults(fn=cmd_policy)

    do = msub.add_parser(
        "doctor",
        help="AGENTS.md drift and self-hosted runners, in a checkout or on GitHub (no model)",
    )
    do.add_argument("path", nargs="?", default=".")
    do.add_argument(
        "--repo",
        default=None,
        help="owner/name: check its main on GitHub instead (as the weekly poll does)",
    )
    do.set_defaults(fn=cmd_doctor)

    wh = msub.add_parser(
        "whoami",
        help="who maintenance is on GitHub: the login or the token "
        "(maintain.token_env_file), its scope and expiry",
    )
    wh.add_argument("--home", default=None)
    wh.add_argument("--json", action="store_true")
    wh.set_defaults(fn=cmd_whoami)

    rc = msub.add_parser(
        "recheck",
        help="read one maintained repo again now: main's head, the "
        "drift doctor and its project's drift (reads only)",
    )
    rc.add_argument("repo", help="owner/name")
    rc.add_argument("--home", default=None)
    rc.add_argument("--json", action="store_true")
    rc.add_argument(
        "--detach",
        action="store_true",
        help="the page's button (maintain_recheck): run it in the background through repo-factory",
    )
    rc.add_argument(
        "--url",
        default=None,
        help="with --detach: act through the running repo-factory (http://127.0.0.1:8790)",
    )
    rc.set_defaults(fn=cmd_recheck)

    tk = msub.add_parser(
        "tick",
        help="poll the repositories maintenance is switched on for, once now "
        "(what the loop's tick does every maintain.poll_s)",
    )
    tk.add_argument(
        "--home", default=None, help="the home (default $REPO_FACTORY_HOME or ~/.repo-factory)"
    )
    tk.add_argument("--json", action="store_true")
    tk.set_defaults(fn=cmd_tick)

    st = msub.add_parser("status", help="what the last poll saw, per repo (no GitHub call)")
    st.add_argument("--home", default=None)
    st.add_argument("--json", action="store_true")
    st.set_defaults(fn=cmd_status)

    rv = msub.add_parser(
        "revise",
        help="fix one issue now: reproduce, fix, verify, open a PR "
        "(the tick starts this, detached, for a go issue)",
    )
    rv.add_argument("repo", help="owner/name")
    rv.add_argument("number", help="the issue")
    rv.add_argument("--home", default=None)
    rv.add_argument("--json", action="store_true")
    rv.set_defaults(fn=cmd_revise)

    su = msub.add_parser(
        "setup",
        help="the setup pull request of a repo maintenance was switched on for: labels, "
        "policy, AGENTS.md and CI (the switch starts this, detached)",
    )
    su.add_argument("repo", help="owner/name")
    su.add_argument("--home", default=None)
    su.set_defaults(fn=cmd_setup)

    pp = msub.add_parser(
        "propose",
        help="this week's proposals for a repo maintenance is on for: one capped no-tools call "
        "(the tick starts this weekly, detached); nothing is built from one without your press",
    )
    pp.add_argument("repo", help="owner/name")
    pp.add_argument("--home", default=None)
    pp.set_defaults(fn=cmd_propose)


def _gh(args):
    from ..gh import Gh
    from ..github_user import connection

    if args.gh:
        return Gh(cmd=args.gh.split())
    from ..settings.store import Config

    return connection(Config(getattr(args, "home", None)))


def cmd_labels(args) -> None:
    from . import labels

    if args.print:
        print(labels.labels_yaml(), end="")
        return
    if not args.repo:
        sys.exit("repo-factory maintain labels: give a repo (owner/name) or --print")
    res = labels.apply(_gh(args), args.repo, dry_run=args.dry_run)
    print(json.dumps(res, indent=1))
    cmds = labels.prune_commands(args.repo, res["extra"])
    if cmds:
        print("to prune GitHub's default labels that ours replace:", *cmds, sep="\n  ")


def cmd_policy(args) -> None:
    from . import policy

    if args.template:
        print(policy.render(), end="")
        return
    try:
        if args.file:
            p = policy.parse(Path(args.file).read_text())
        elif args.repo:
            p = policy.load(_gh(args), args.repo)
        else:
            sys.exit("repo-factory maintain policy: give a repo, --file or --template")
    except policy.PolicyError as e:
        sys.exit(f"repo-factory maintain policy: {e}")
    print(json.dumps(p, indent=1))


def cmd_whoami(args) -> None:
    from ..github_user import connection
    from ..settings.store import Config
    from . import identity

    cfg = Config(args.home)
    owner = connection(cfg)
    d = identity.describe(
        identity.resolve(cfg) or owner,
        owner_gh=owner,
        owner_login=cfg["publish"].get("owner"),
        repos=identity.maintained_repos(cfg),
    )
    if args.json:
        print(json.dumps(d, indent=1))
    else:
        print(f"{d['status']}: {d['line']}")
    if d["status"] == "fail":
        sys.exit(1)


def cmd_doctor(args) -> None:
    from . import agentsmd

    if args.repo:
        from .doctor import check_repo
        from .github import GitHubError, Repo

        try:
            problems = check_repo(Repo(_gh(args), args.repo))
        except GitHubError as e:
            sys.exit(f"repo-factory maintain doctor: {e}")
        for p in problems:
            print(f"- {p}")
        print("AGENTS.md: current" if not problems else f"AGENTS.md: {len(problems)} problem(s)")
        if problems:
            sys.exit(1)
        return
    root = Path(args.path).expanduser()
    problems = agentsmd.check(root)
    for p in problems:
        print(f"- {p}")
    print("AGENTS.md: current" if not problems else f"AGENTS.md: {len(problems)} problem(s)")
    if problems:
        sys.exit(1)


def _loop(args):
    from ..loop.daemon import Loop
    from ..settings.store import Config

    lp = Loop(Config(args.home))
    if args.gh:
        from ..gh import Gh

        lp._gh = Gh(cmd=args.gh.split())
        lp.cfg.data.setdefault("maintain", {})["gh_command"] = None
    return lp


def cmd_tick(args) -> None:
    from .watch import tick

    lp = _loop(args)
    if lp.cfg.paused():
        sys.exit("repo-factory maintain tick: the loop is paused (PAUSE); resume it first")
    res = tick(lp)
    if args.json:
        print(json.dumps(res, indent=1, default=str))
        return
    if res.get("off"):
        print("maintenance is switched on for no published repository (or every one is paused)")
    if res.get("spend"):
        print(res["spend"])
    for r in res["repos"]:
        print(f"{r['repo']}: {r.get('waiting', 0)} waiting for you")
        for n in r.get("started") or []:
            print(f"  fixing #{n} (detached; repo-factory maintain status shows it)")
        for e in r.get("ended") or []:
            print(
                f"  the fix of #{e['number']} ended: {e.get('outcome')}"
                + (f", PR #{e['pr']}" if e.get("pr") else f": {e.get('why')}")
            )
        for n in r.get("merged") or []:
            print(f"  merged #{n}")
        if (r.get("release") or {}).get("tag"):
            print(f"  released {r['release']['tag']}: {r['release'].get('url')}")
        for e in r.get("errors") or []:
            print(f"  error: {e}")


def cmd_status(args) -> None:
    import time

    from ..settings import store as config
    from .github import Cache

    d = Cache(config.home(args.home)).read()
    if args.json:
        print(json.dumps(d, indent=1))
        return
    if not d["repos"]:
        print("no poll yet (repo-factory maintain tick, or the loop's tick every maintain.poll_s)")
    for full, rec in sorted(d["repos"].items()):
        p, c = rec.get("policy") or {}, rec.get("counts") or {}
        ago = (
            f"{(time.time() - rec['last_poll']) / 60:.0f} min ago"
            if rec.get("last_poll")
            else "never"
        )
        print(
            f"{full}: {p.get('status', '?')}, polled {ago}; {c.get('issues', 0)} open issues "
            f"({c.get('go_yours', 0)} labelled go by you), {c.get('prs', 0)} pull requests, "
            f"{c.get('waiting', 0)} waiting for you"
        )
        for r in rec.get("rows") or []:
            print(f"  {r['kind']}: " + (f"#{r['number']} " if r.get("number") else "") + r["title"])
        for n, x in (rec.get("revises") or {}).items():
            print(f"  fixing #{n}: pid {x.get('pid')}, {x.get('task')}")
        if rec.get("error"):
            print(f"  error: {rec['error']}")


def cmd_revise(args) -> None:
    from .revise import Fix

    lp = _loop(args)
    if not str(args.number).isdigit():
        sys.exit(f"repo-factory maintain revise: {args.number} is not an issue number")
    res = Fix(lp, args.repo, int(args.number)).run()
    if args.json:
        print(json.dumps(res, indent=1, default=str))
    else:
        print(
            f"{args.repo}#{args.number}: {res['outcome']}"
            + (
                f", PR #{res['pr']} {res.get('url')}"
                if res.get("pr")
                else f" in {res.get('stage')}: {res.get('why')}"
            )
            + f" (${res.get('usd', 0):.2f})"
        )
    if res["outcome"] != "pr":
        sys.exit(1)


def cmd_setup(args) -> None:
    """The setup pull request (``backfill`` with maintenance's rules): AGENTS.md's check in
    the sandbox, no repository setting changed, nothing written once maintenance is switched
    off or paused, and its state in ``backfill.state_path`` for the page."""
    import time

    from . import backfill, pauses, revise, switch

    lp = _loop(args)
    repo, home = args.repo, lp.cfg.home

    def still() -> str | None:
        if not switch.is_on(home, repo):
            return f"maintenance was switched off for {repo}"
        if pauses.get(home, repo):
            return f"maintenance was paused for {repo}"
        if lp.cfg.paused():
            return "the loop was paused"
        return None

    def check(work):
        return revise.run_agents_check(work, revise.default_venv(work))

    why = still()
    res: dict = {}
    if not why:
        try:
            res = backfill.backfill(
                lp.gh,
                repo,
                lp.cfg.layout.maintain / "setup" / repo.replace("/", "-"),
                check=check,
                still=still,
            )
        except Exception as e:  # noqa: BLE001 - said on the page
            why = f"{type(e).__name__}: {e}"
    if not why and not res.get("ok"):
        chk = res.get("check") or {}
        why = res.get("stopped") or (
            "the check of AGENTS.md failed in the sandbox: " + (chk.get("summary") or "")
            if chk and not chk.get("ok")
            else "the drift doctor found: " + "; ".join(res.get("doctor") or [])
        )
    pr = res.get("pr") or {}
    backfill.write_state(
        home,
        repo,
        state="failed" if why else "done",
        finished=time.time(),
        why=why,
        pr=pr.get("number"),
        url=pr.get("url"),
        nothing_to_add=bool(res.get("nothing_to_add")),
    )
    print(f"{repo}: " + (f"stopped: {why}" if why else (pr.get("url") or "labels applied")))
    if why:
        sys.exit(1)


def cmd_propose(args) -> None:
    import time

    from . import proposals

    lp = _loop(args)
    try:
        rec = proposals.run(lp, args.repo)
    except Exception as e:  # noqa: BLE001 - kept for the page
        proposals._update(
            lp.cfg.home,
            args.repo,
            state="failed",
            error=f"{type(e).__name__}: {e}"[:300],
            tried=time.time(),
        )
        raise
    n = len(rec.get("items") or [])
    tail = f": {rec['error']}" if rec.get("error") else ""
    print(f"{args.repo}: {rec.get('state')}, {n} proposal(s){tail}")


# -- one door, two ways in ------------------------------------------------------------------------
def _http(url: str, method: str, path: str, body=None, query=None, token: str | None = None):
    """``(status, JSON)`` of one request to the running repo-factory on loopback."""
    import urllib.error
    import urllib.request
    from urllib.parse import urlencode

    full = url.rstrip("/") + path + (("?" + urlencode(query)) if query else "")
    data = json.dumps(body or {}).encode() if method != "GET" else None
    req = urllib.request.Request(
        full,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 - loopback only
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"error": str(e)}
    except (OSError, ValueError) as e:
        sys.exit(f"repo-factory maintain: {url}: {e}")


def _call(args, method: str, path: str, body=None, query=None) -> dict:
    """One request to repo-factory's API: with ``--url``, the running service on loopback with
    the command line's own token (``cli``; never printed); else in this process on ``--home``,
    acting as ``cli``."""
    from ..ui import api

    if getattr(args, "url", None):
        from ..tokens import Store

        code, out = _http(args.url, method, path, body, query, Store().value("cli"))
    else:
        from ..settings.store import Config

        cfg = Config(args.home)
        app = api.App(cfg.tasks_dir, "claude-code", cfg.home)
        code, _, raw = api.handle(
            method,
            path,
            query,
            json.dumps(body).encode() if body is not None else None,
            app=app,
            identity={"origin": "cli", "actions": None},
        )
        out = json.loads(raw)
    if code >= 400:
        sys.exit(f"repo-factory maintain: {out.get('error') if isinstance(out, dict) else out}")
    return out


def _act(args, kind: str, fields: dict, confirm: bool = False) -> dict:
    return _call(
        args,
        "POST",
        "/api/act",
        {
            "kind": kind,
            "subject": {"kind": "new"},
            "fields": fields,
            "confirm": confirm,
            "origin": "cli",
        },
    )


def _say(args, out: dict) -> None:
    print(json.dumps(out, indent=1, default=str) if args.json else (out.get("say") or out))


def describer(model_id: str):
    """``describe({path: first lines}) -> {path: one sentence}`` with one no-tools call."""
    from ..loop import models
    from ..loop.pitch import parse

    call = models.claude(model_id, purpose="maintain")

    def describe(mods: dict) -> dict:
        listing = "\n\n".join(f"--- {p}\n" + "\n".join(lines) for p, lines in mods.items())
        text, usd = call(
            "For each module below, write one plain sentence (at most 20 words) saying what it "
            "holds, for a table in a developer guide. No marketing, no internal ids. Reply with "
            "one JSON object mapping each path to its sentence.\n\n<<<document>>>\n"
            + listing
            + "\n<<<end document>>>"
        )
        print(f"described {len(mods)} module(s) for ${usd:.3f}", file=sys.stderr)
        got = parse(text) or {}
        return {k: v for k, v in got.items() if k in mods and isinstance(v, str)}

    return describe


def cmd_recheck(args) -> None:
    import os
    import time

    from .. import proc
    from . import recheck

    if args.detach:
        _say(args, _act(args, "maintain_recheck", {"repo": args.repo}))
        return
    lp = _loop(args)
    home = lp.cfg.home
    # what the row shows while it runs and after (the same file whether pressed or typed)
    st = recheck.read_state(home, args.repo) or {}
    mine = st.get("pid") == os.getpid()
    recheck.write_state(
        home,
        args.repo,
        state="running",
        pid=os.getpid(),
        start=proc.start_time(os.getpid()),
        finished=None,
        why=None,
        since=st.get("since") if mine else time.time(),
        by=st.get("by") if mine else "cli",
    )
    from .github import GitHubError

    try:
        res = recheck.run(lp, args.repo)
    except BaseException as e:
        said = isinstance(e, (recheck.RecheckError, GitHubError))
        recheck.write_state(
            home,
            args.repo,
            state="failed",
            finished=time.time(),
            why=(str(e) if said else f"{type(e).__name__}: {e}")[:600],
        )
        if said:
            sys.exit(f"repo-factory maintain recheck: {e}")
        raise
    recheck.write_state(
        home,
        args.repo,
        state="failed" if res["errors"] else "done",
        finished=time.time(),
        why="; ".join(res["errors"])[:600] or None,
    )
    if args.json:
        print(json.dumps(res, indent=1, default=str))
        return
    print(f"{res['repo']}: main at {(res.get('head') or '?')[:7]}")
    if "problems" in res:
        print(
            "AGENTS.md: current"
            if not res["problems"]
            else f"AGENTS.md: {len(res['problems'])} problem(s)"
        )
        for p in res["problems"]:
            print(f"  {p}")
    for pid in res["projects"]:
        print(f"project {pid}: clone, drift and numbers refreshed")
    for e in res["errors"]:
        print(f"error: {e}")

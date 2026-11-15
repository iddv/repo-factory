"""``repo-factory projects ...``: the projects of a home (off and unlisted in this version).

    repo-factory projects list [--home H] [--json]        every project, by health
    repo-factory projects show ID [--home H] [--json]     one project: charter, repos, timeline
    repo-factory projects create ID [--home H]            a project for a published build (i…)
    repo-factory projects backfill [--home H] [--dry-run] [--json]
                                                 a project for every published build that has
                                                 none

Each reads or writes the loop's ledger on ``--home`` (``$REPO_FACTORY_HOME``, else ~/.repo-factory);
none calls a model or GitHub."""

from __future__ import annotations

import json
import re
import sys
import time


def add_projects_group(sub) -> None:
    import argparse

    pj = sub.add_parser("projects", help=argparse.SUPPRESS)
    psub = pj.add_subparsers(dest="projects_cmd", required=True)

    def home(p):
        p.add_argument("--home", default=None, help="the home (default ~/.repo-factory)")
        return p

    ls = home(psub.add_parser("list", help="every project, by health"))
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(fn=cmd_list)
    sh = home(psub.add_parser("show", help="one project"))
    sh.add_argument("id")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(fn=cmd_show)
    cr = home(psub.add_parser("create", help="a project for a published build"))
    cr.add_argument("ref", help="a build's id (i…)")
    cr.set_defaults(fn=cmd_create)
    bf = home(psub.add_parser("backfill", help="a project for every published build that has none"))
    bf.add_argument(
        "--dry-run", action="store_true", help="say what would be created; write nothing"
    )
    bf.add_argument("--json", action="store_true")
    bf.add_argument(
        "--no-charters",
        dest="charters",
        action="store_false",
        help="make the projects only; the loop's tick drafts their charters later",
    )
    bf.set_defaults(fn=cmd_backfill)


def _loop(args):
    from ..loop.daemon import Loop
    from ..settings.store import Config

    return Loop(Config(args.home))


def _ago(ts: float | None) -> str:
    if not ts:
        return ""
    s = max(0, time.time() - ts)
    return (
        f"{s / 86400:.0f}d" if s >= 86400 else f"{s / 3600:.0f}h" if s >= 3600 else f"{s / 60:.0f}m"
    )


def cmd_list(args) -> None:
    from . import record

    out = record.listing(_loop(args))
    if args.json:
        print(json.dumps(out, indent=1, default=str))
        return
    if not out["projects"]:
        print(
            "no projects yet: a project appears when a build is published "
            "(repo-factory projects backfill makes the missing ones)"
        )
        return
    for r in out["projects"]:
        print(
            f"{r['id']}  {r['health']:<9} {r['kind']:<7} {r['name']:<22} "
            f"{r['repo'] or '':<32} {r['version'] or '':<8} charter v{r['charter_v']}  "
            f"{_ago(r['last'])}"
        )


def cmd_show(args) -> None:
    from . import record

    try:
        out = record.page(_loop(args), args.id)
    except LookupError as e:
        sys.exit(f"repo-factory projects: {e}")
    if args.json:
        print(json.dumps(out, indent=1, default=str))
        return
    p = out["project"]
    print(f"{p['name']} ({p['id']}, {p['kind']}): {p['health']}: {p['why']}")
    if p.get("pitch"):
        print(f"  {p['pitch']}")
    for r in out["repos"]:
        print(f"  repo {r['repo']} ({r.get('visibility') or '?'}) {r.get('release') or ''}")
    print(f"  charter v{out['charter_v']}" if out["charter"] else "  no charter yet")
    for e in out["timeline"][:15]:
        print(f"  {time.strftime('%Y-%m-%d %H:%M', time.localtime(e['ts']))} {e['note']}")


def cmd_create(args) -> None:
    from . import record

    lp = _loop(args)
    ps = record.Projects(lp.ledger)
    if not re.fullmatch(r"i[0-9a-f]{8}\d*", args.ref):
        sys.exit(f"repo-factory projects: {args.ref} is not a build's id (i…)")
    idea = lp.ledger.must(args.ref)
    if idea["state"] not in ("published", "public"):
        sys.exit(
            f"repo-factory projects: {idea['id']} is {idea['state']}; a project starts "
            "when a build is published"
        )
    p, made = record.create_for_idea(ps, idea, note="created by hand")
    print(f"{'created' if made else 'already a project'}: {p['id']} {p['name']}")


def cmd_backfill(args) -> None:
    """The projects, then (the command line may wait on a model) each missing charter
    drafted, one call each. No evolve run starts: the first runs on the loop's cadence, so
    charters come before ideas."""
    from .life import backfill

    out = backfill(_loop(args), dry_run=args.dry_run, charters=args.charters)
    if args.json:
        print(json.dumps(out, indent=1))
        return
    made = [r for r in out if r["created"]]
    for r in out:
        word = ("would create" if args.dry_run else "created") if r["created"] else "exists"
        print(f"{word:<13} {r['id']} {r['kind']:<7} {r['name']} ({r['repo']})")
        for k, label in (("charter", "charter"), ("charter_error", "charter not drafted")):
            if r.get(k):
                print(f"{'':<14}{label}: {r[k]}")
    print(
        f"{len(made)} {'to create' if args.dry_run else 'created'}, "
        f"{len(out) - len(made)} already there"
    )

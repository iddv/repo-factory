"""``repo-factory lessons list|postmortem|credit`` (off and unlisted in this version).

    repo-factory lessons list [--candidate | --file F] [--pack P] [--lint] [--json]
    repo-factory lessons postmortem RUN_DIR | --score-dir DIR --pack P [--claims F] [--findings F]
                           [--no-apply] [--candidate F] [--model M] [--out F]
    repo-factory lessons postmortem --issues [--repo R ...] [--home H] [--reread] [--no-apply]
                           [--candidate F] [--model M]   (user-found bugs)
    repo-factory lessons credit RUN_DIR [--candidate F]

Post-mortems and credit change the candidate (``policies/v5/lessons.next.yaml``, version
N+1); the default changes only by hand."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import store


def add_lessons_group(sub) -> None:
    import argparse

    ls = sub.add_parser("lessons", help=argparse.SUPPRESS)
    lsub = ls.add_subparsers(dest="lessons_cmd", required=True)

    li = lsub.add_parser("list", help="the lessons file (default, or the candidate)")
    li.add_argument("--file", default=None)
    li.add_argument("--candidate", action="store_true", help="the candidate (version N+1)")
    li.add_argument("--pack", default=None, help="only what a build of this pack would be shown")
    li.add_argument("--lint", action="store_true", help="lint the file; exit 1 on problems")
    li.add_argument("--json", action="store_true")
    li.set_defaults(fn=cmd_list)

    pm = lsub.add_parser(
        "postmortem",
        help="one no-tools call: miss types and up to three new "
        "lessons from a scored run's hidden failures",
    )
    pm.add_argument(
        "run_dir",
        nargs="?",
        default=None,
        help="a scored v5 arm dir (score.json, traces/, run.json, claims/findings)",
    )
    pm.add_argument("--score-dir", default=None, help="a score dir instead (score.json, traces/)")
    pm.add_argument("--pack", default=None, help="with --score-dir: pack id or directory")
    pm.add_argument("--claims", default=None)
    pm.add_argument("--findings", default=None)
    pm.add_argument("--lessons", default=None, help="the current lessons (default policies/v5)")
    pm.add_argument(
        "--candidate",
        default=None,
        help="where new lessons go (default policies/v5/lessons.next.yaml)",
    )
    pm.add_argument(
        "--no-apply", dest="apply", action="store_false", help="write postmortem.json only"
    )
    pm.add_argument(
        "--model", default=None, help="default: the run's builder model, else the CLI default"
    )
    pm.add_argument("--out", default=None, help="default: <run or score dir>/postmortem.json")
    pm.add_argument(
        "--issues",
        action="store_true",
        help="instead: the closed issues labelled lesson on the published repos, "
        "one call each, once (the lesson-read comment)",
    )
    pm.add_argument(
        "--repo",
        action="append",
        default=[],
        help="with --issues: owner/name (repeatable; default: the repos the loop maintains)",
    )
    pm.add_argument("--home", default=None, help="with --issues: the loop home")
    pm.add_argument(
        "--reread",
        action="store_true",
        help="with --issues: read issues that already have the lesson-read comment",
    )
    pm.add_argument("--json", action="store_true")
    pm.set_defaults(fn=cmd_postmortem)

    cr = lsub.add_parser(
        "credit", help="update the lessons' counters from a v5 run's claims and findings"
    )
    cr.add_argument("run_dir")
    cr.add_argument("--pack", default=None, help="default: the run's pack (run.json)")
    cr.add_argument("--candidate", default=None)
    cr.add_argument("--json", action="store_true")
    cr.set_defaults(fn=cmd_credit)


# -- commands ---------------------------------------------------------------------------------
def cmd_list(args) -> None:
    path = (
        Path(args.file)
        if args.file
        else (store.candidate_path() if args.candidate else store.default_path())
    )
    data = store.load(path)
    if args.pack is not None:
        shown = store.for_build(args.pack or None, path)
    else:
        shown = data["lessons"]
    probs = []
    if args.lint:
        from .lint import lint_file

        probs = lint_file(data)
    if args.json:
        print(
            json.dumps(
                {"file": str(path), "version": data["version"], "lessons": shown, "lint": probs},
                indent=1,
                default=str,
            )
        )
    else:
        live = len(store.live(data))
        print(f"{path} (version {data['version']}, {live} live of cap {data['cap']})")
        if args.pack is not None:
            print(f"shown to a build of {args.pack or 'a new spec'}:")
            print(store.render(shown) or "(none)")
        for x in [] if args.pack is not None else shown:
            s = x["stats"]
            print(
                f"{x['id']} [{x['status']:9}] {x.get('applies_to', ''):8} "
                f"hit {x['hit_rate']:.2f} ({s['caught']}/{s['exposures']}, "
                f"false alarms {s['false_alarms']}) born {x['born'].get('pack')}: {x['text']}"
            )
        for p in probs:
            print(f"LINT {p}")
    if probs:
        sys.exit(1)


def _run_ref(path: Path) -> str:
    from .pack import bench_root

    path = Path(path).resolve()
    try:
        return str(path.relative_to(bench_root().resolve()))
    except ValueError:
        return str(path)


def cmd_postmortem_issues(args) -> None:
    from ..github_user import connection
    from ..maintain import identity
    from ..settings.store import Config
    from . import postmortem as pmod

    cfg = Config(args.home)
    repos = args.repo or identity.maintained_repos(cfg)
    if not repos:
        sys.exit(
            "repo-factory lessons postmortem --issues: no repo (nothing published; give --repo)"
        )
    gh = identity.resolve(cfg, scope="issues") or connection(cfg)
    data, cand = store.open_candidate(
        current=Path(args.lessons) if args.lessons else None,
        candidate=Path(args.candidate) if args.candidate else None,
    )
    llm = pmod.ClaudeCall(args.model or pmod.ISSUE_MODEL)
    try:
        res = pmod.issues_postmortem(
            gh,
            repos,
            data,
            llm=llm,
            reread=args.reread,
            apply=args.apply,
            log=lambda m: print(m, file=sys.stderr),
        )
    except RuntimeError as e:
        sys.exit(f"repo-factory lessons postmortem --issues: {e}")
    if args.apply and any(x.get("lesson") for x in res["read"]):
        store.save(cand, data)
    res.update(candidate=str(cand), version=data["version"])
    if res["usd"] and Path(cfg.db).exists():
        from ..loop.ledger import Ledger
        from ..loop.models import spend_kw

        Ledger(cfg.db).spend("lesson", res["usd"], None, **spend_kw(getattr(llm, "tokens", None)))
    if args.json:
        print(json.dumps(res, indent=1, default=str))
    else:
        for x in res["read"]:
            print(
                f"{x['issue']}: "
                + (
                    f"{x['lesson']} ({x['miss']}): {x['text']}"
                    if x["lesson"]
                    else f"no lesson ({x['why'] or 'none proposed'})"
                )
                + (f"  [dropped: {'; '.join(x['dropped']['problems'])}]" if x["dropped"] else "")
            )
        for x in res["skipped"]:
            print(f"{x['issue']}: skipped, {x['why']}")
        for e in res["errors"]:
            print(f"error: {e}")
        added = [x["lesson"] for x in res["read"] if x["lesson"]]
        print(
            f"{len(res['read'])} read, ${res['usd']:.2f}; "
            + (
                f"added {', '.join(added)} to {cand} (v{data['version']}), waiting for the bench"
                if added and args.apply
                else "nothing added"
            )
        )
    if res["errors"]:
        sys.exit(1)


def cmd_postmortem(args) -> None:
    if args.issues:
        return cmd_postmortem_issues(args)
    from . import postmortem as pmod
    from .pack import Pack, PackError

    run = None
    if args.run_dir:
        d = Path(args.run_dir).expanduser()
        score_p = pmod.locate(d, "score.json")
        run = pmod.read_json(d / "run.json") or {}
        pack_ref = (
            args.pack
            or run.get("pack")
            or (d.parent.parent.name if d.parent.name[:1] == "s" else None)
        )
        claims_p = Path(args.claims) if args.claims else pmod.locate(d, "claims.json")
        findings_p = Path(args.findings) if args.findings else pmod.locate(d, "findings.json")
        out = Path(args.out) if args.out else d / "postmortem.json"
    elif args.score_dir:
        d = Path(args.score_dir).expanduser()
        score_p = d / "score.json"
        pack_ref = args.pack
        claims_p = Path(args.claims) if args.claims else None
        findings_p = Path(args.findings) if args.findings else None
        out = Path(args.out) if args.out else d / "postmortem.json"
    else:
        sys.exit("repo-factory lessons postmortem: give a RUN_DIR or --score-dir DIR --pack P")
    if not score_p or not Path(score_p).is_file():
        sys.exit(f"repo-factory lessons postmortem: no score.json under {d}")
    if not pack_ref:
        sys.exit("repo-factory lessons postmortem: --pack is required")
    try:
        pack = Pack.load(pack_ref)
    except PackError as e:
        sys.exit(f"repo-factory lessons postmortem: {e}")
    score = json.loads(Path(score_p).read_text())
    model = args.model or ((run or {}).get("profile") or {}).get("model")
    try:
        pm = pmod.run(
            score,
            pack,
            traces=Path(score_p).parent / "traces",
            claims=pmod.read_json(claims_p),
            findings=pmod.read_json(findings_p),
            lessons_path=Path(args.lessons) if args.lessons else None,
            model=model,
            run_ref=_run_ref(d),
        )
    except pmod.PostmortemError as e:
        sys.exit(f"repo-factory lessons postmortem: {e}")
    if args.apply:
        data, cand = store.open_candidate(
            current=Path(args.lessons) if args.lessons else None,
            candidate=Path(args.candidate) if args.candidate else None,
        )
        added = pmod.apply(pm, data)
        store.save(cand, data)
        pm["applied"] = {
            "candidate": str(cand),
            "version": data["version"],
            "added": [x["id"] for x in added],
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pm, indent=1, default=str))
    if args.json:
        print(json.dumps(pm, indent=1, default=str))
    else:
        print(render(pm))
    print(f"wrote {out}", file=sys.stderr)


def render(pm: dict) -> str:
    L = [
        f"post-mortem of {pm.get('run')} ({pm['pack']} v{pm['pack_version']}): "
        f"{len(pm['failures'])} failing rows, ${pm.get('usd', 0):.2f}"
    ]
    if pm["inputs"].get("note"):
        L.append(f"note: {pm['inputs']['note']}")
    for f in pm["failures"]:
        L.append(
            f"- {f['row']} [{f['scored_as']}]: {f['miss_type']}"
            + (f" (model said {f['coerced_from']})" if f.get("coerced_from") else "")
            + (f"; existing lesson {f['existing_lesson']}" if f.get("existing_lesson") else "")
        )
        if f.get("new_lesson"):
            L.append(f"    new lesson ({f['new_lesson']['applies_to']}): {f['new_lesson']['text']}")
    for x in pm.get("dropped") or []:
        L.append(f"  dropped ({x['row']}): {x['text']}  <- {'; '.join(x['problems'])}")
    L.append("summary: " + ", ".join(f"{k} {v}" for k, v in pm["summary"].items() if v))
    if pm.get("applied"):
        a = pm["applied"]
        L.append(
            f"added {', '.join(a['added']) or 'nothing'} to {a['candidate']} (v{a['version']})"
        )
    return "\n".join(L)


def cmd_credit(args) -> None:
    from . import credit as cmod
    from .postmortem import locate, read_json

    d = Path(args.run_dir).expanduser()
    run = read_json(d / "run.json") or {}
    pack = args.pack or run.get("pack")
    if not pack:
        sys.exit("repo-factory lessons credit: --pack is required (no run.json)")
    claims = read_json(locate(d, "claims.json"))
    findings = read_json(locate(d, "findings.json"))
    if claims is None and findings is None:
        sys.exit(f"repo-factory lessons credit: no claims.json or findings.json under {d}")
    oracle = read_json(locate(d, "oracle.json"))
    data, cand = store.open_candidate(candidate=Path(args.candidate) if args.candidate else None)
    injected = cmod.injected_ids(claims=claims, run=run, data=data, pack=pack)
    res = cmod.credit(
        data,
        pack=pack,
        claims=claims,
        findings=findings,
        injected=injected,
        run_ref=_run_ref(d),
        oracle=oracle,
    )
    store.save(cand, data)
    res["candidate"] = str(cand)
    print(json.dumps(res, indent=1, default=str))

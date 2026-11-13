"""Stage 6: repair, acceptance and the conflict protocol (docs/design/build-v5.md §3.5).

The builder's session is resumed (``--resume --fork-session``) with ``prompts/repair.md``
and a findings brief; with no builder session (a prebuilt repo without one) a fresh session
gets the same brief. The repairer makes **one commit per finding** (``fix(<id>): ...``), so
a regression can be traced to the commit that caused it and only that finding reverted.

``run_round`` measures the head, and for every regression: finds the commit that broke it
(the measure after each commit), asks the pair panel, applies the authority order
(``accept.resolve``), runs at most one fix-up session for the pairs that need one, then
reverts the commits that lose. A repair is never thrown away whole because two of the
factory's own checks disagree, nor because one revert conflicts (``drop``), and settings
are accepted on their own, never with the defect fixes (``keep_settings``)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .. import gitcmd
from . import accept

# The workspace's git is the session's: every call goes through gitcmd (hooks, fsmonitor,
# filters and the repo's own commands off). GIT is the same command line for a test's own
# calls.
IDENTITY = (
    "-c",
    "user.name=repo_factory",
    "-c",
    "user.email=repo_factory@localhost",
    "-c",
    "commit.gpgsign=false",
)
GIT = gitcmd.argv(*IDENTITY)
EXCLUDE = (
    "acceptance/",
    "_verify/",
    ".venv/",
    "venv/",
    "node_modules/",
    ".pytest_cache/",
    "__pycache__/",
    "transcript.jsonl",
    "stderr.txt",
)
FIX = re.compile(r"^\s*fix(?:up)?\(([^)]*)\)", re.I)


# -- git ----------------------------------------------------------------------------------------
def git(ws: Path, *args: str, check: bool = True) -> str:
    p = gitcmd.run([*IDENTITY, *args], cwd=ws, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout


def ensure_repo(ws: Path, message: str = "v5: base") -> str:
    """Make ``ws`` a git repo if it isn't one, keep the factory's scaffolding out of it,
    and commit anything uncommitted. Returns the head sha."""
    ws = Path(ws)
    if not (ws / ".git").exists():
        git(ws, "init", "-q")
    ex = ws / ".git" / "info" / "exclude"
    ex.parent.mkdir(parents=True, exist_ok=True)
    have = ex.read_text() if ex.exists() else ""
    add = [e for e in EXCLUDE if e not in have.split()]
    if add:
        ex.write_text(
            have
            + ("" if not have or have.endswith("\n") else "\n")
            + "".join(f"{e}\n" for e in add)
        )
    commit_all(ws, message)
    return head(ws)


def head(ws: Path) -> str:
    return git(ws, "rev-parse", "HEAD").strip()


def commit_all(ws: Path, message: str) -> str | None:
    """Commit everything uncommitted; returns the new sha, or None if nothing changed."""
    git(ws, "add", "-A")
    if (
        not git(ws, "status", "--porcelain").strip()
        and git(ws, "rev-parse", "--verify", "-q", "HEAD", check=False).strip()
    ):
        return None
    git(ws, "commit", "-q", "--allow-empty", "-m", message)
    return head(ws)


def commits_since(ws: Path, base: str) -> list[dict]:
    """``[{"sha", "subject", "findings": [ids]}]``, oldest first."""
    out = []
    for line in git(ws, "log", "--reverse", "--format=%H%x09%s", f"{base}..HEAD").splitlines():
        sha, _, subj = line.partition("\t")
        out.append({"sha": sha, "subject": subj, "findings": attributed(subj)})
    return out


def attributed(subject: str) -> list[str]:
    m = FIX.match(subject or "")
    if not m:
        return []
    return [x.strip() for x in re.split(r"[,\s]+", m.group(1)) if x.strip()]


def revert(ws: Path, sha: str) -> bool:
    p = gitcmd.run([*IDENTITY, "revert", "--no-edit", sha], cwd=ws, capture_output=True, text=True)
    if p.returncode != 0:
        gitcmd.run([*IDENTITY, "revert", "--abort"], cwd=ws, capture_output=True)
        return False
    return True


def reverse_hunks(ws: Path, sha: str, message: str) -> bool:
    """Undo commit ``sha`` hunk by hunk with no context lines (``git apply -R
    --unidiff-zero``): its added lines are removed and its removed lines restored where
    they still stand, even when later commits changed the lines *next to* them, which is
    what makes ``git revert``'s 3-way merge conflict (the lending run: seven settings added
    side by side to one README table, one settings module and one test file). All or
    nothing: when a later commit changed the commit's own lines, nothing is applied. The
    caller measures the result."""
    diff = git(ws, "diff", "-U0", "--binary", f"{sha}^", sha)
    if diff.strip():
        p = gitcmd.run(
            [*IDENTITY, "apply", "-R", "--unidiff-zero", "--index", "-"],
            cwd=ws,
            input=diff,
            capture_output=True,
            text=True,
        )
        if p.returncode != 0:
            git(ws, "reset", "-q", "--hard", "HEAD")
            return False
    git(ws, "commit", "-q", "--allow-empty", "-m", message)
    return True


def cherry_pick(ws: Path, sha: str) -> bool:
    p = gitcmd.run(
        [*IDENTITY, "cherry-pick", "--allow-empty", "--keep-redundant-commits", sha],
        cwd=ws,
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        gitcmd.run([*IDENTITY, "cherry-pick", "--abort"], cwd=ws, capture_output=True)
        return False
    return True


def rebuild(ws: Path, base: str, commits: list[dict], drop: set[str]) -> list[dict]:
    """``base`` plus every commit not in ``drop``, in order; a commit that no longer applies
    (it built on a dropped one) is skipped. Returns the skipped commits."""
    reset(ws, base)
    return [c for c in commits if c["sha"] not in drop and not cherry_pick(ws, c["sha"])]


def undo_md(ws: Path, commits: list[dict]) -> str:
    """The commits to take out, for the undo brief: subject and files touched."""
    L = []
    for c in commits:
        stat = git(ws, "show", "--stat", "--format=", c["sha"], check=False).strip()
        L.append(
            f"- commit {c['sha'][:10]} `{c['subject']}`\n"
            + "\n".join(f"    {ln.strip()}" for ln in stat.splitlines())
        )
    return "\n".join(L)


def drop(ctx, k: int, commits: list[dict], losers: list[str], session: bool = True) -> dict:
    """Take the commits ``losers`` out of ``ctx.ws``'s head and nothing else (never the
    whole round). For each, newest first: ``git revert``; if that conflicts, the zero-context
    reverse (``reverse_hunks``). What is still in after both goes, once, to a narrow
    session (``ctx.undo_brief``, the fix-up's cap) that removes only those changes. Returns
    ``{"done": [{"sha", "findings", "how"}], "failed": [shas]}``; the caller measures."""
    ws = Path(ctx.ws)
    by = {c["sha"]: c for c in commits}
    order = [c["sha"] for c in commits]
    done, failed = [], []
    for sha in sorted(set(losers), key=order.index, reverse=True):
        c = by[sha]
        how = (
            "revert"
            if revert(ws, sha)
            else "hunks"
            if reverse_hunks(
                ws, sha, f"revert({','.join(c['findings'])}): undo {sha[:10]} hunk by hunk"
            )
            else None
        )
        if how:
            done.append({"sha": sha, "findings": c["findings"], "how": how})
        else:
            failed.append(sha)
    if failed and session and hasattr(ctx, "undo_brief") and ctx.allows(ctx.fixup_usd):
        before = head(ws)
        todo = [by[s] for s in failed]
        ctx.session(ctx.undo_brief(todo), f"undo{k}", "repair")
        shutil.rmtree(ws / "acceptance" / "findings", ignore_errors=True)
        ids = sorted({f for c in todo for f in c["findings"]})
        commit_all(ws, f"revert({','.join(ids)}): uncommitted changes after the undo {k}")
        if head(ws) != before:
            done += [{"sha": s, "findings": by[s]["findings"], "how": "session"} for s in failed]
            failed = []
    return {"done": done, "failed": failed}


def reset(ws: Path, sha: str) -> None:
    git(ws, "reset", "-q", "--hard", sha)


def checkout_copy(ws: Path, sha: str, dest: Path) -> Path:
    """A copy of ``ws`` at commit ``sha`` (untracked and ignored files kept: a build may
    need its ignored data files to run)."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        ws,
        dest,
        ignore=shutil.ignore_patterns(
            ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"
        ),
        symlinks=True,
    )
    git(dest, "checkout", "-q", "--force", "--detach", sha)
    return dest


# -- the brief ------------------------------------------------------------------------------------
def finding_md(d: dict) -> str:
    """One finding for the repairer: the claim, how to reproduce it, observed against
    expected, the panel's quotes, the scenario file."""
    o = d.get("origin") or {}
    L = [f"### {d['id']}: {d.get('title') or ''}".rstrip(": ")]
    if d.get("class") == "interpretation":
        L.append(
            f"Open question: {d.get('question')}\nChosen reading: {d.get('chosen_text')}"
            + (f" (chosen by {d['chosen_by']})" if d.get("chosen_by") else "")
        )
        return "\n".join(L)
    if d.get("class") == "incomplete":
        fl = ", ".join(o.get("flows") or [])
        L.append(
            "Incomplete: "
            + (
                f"flow {fl} of SCOPE.md's definition of done doesn't work end to end"
                if fl
                else "a stub, a dead control or placeholder data on a user's path"
            )
            + ". Build it for real, through the interface users use; don't stub or hide "
            "it."
        )
    if d.get("source"):
        L.append(f"Spec: {d['source']}")
    L.append(f"Expected: {d.get('expect') or o.get('expected') or ''}")
    if o.get("observed"):
        L.append(f"Observed: {o['observed']}")
    if o.get("repro"):
        L.append(f"Reproduce: `{o['repro']}`")
    if o.get("suspect"):
        L.append(f"Verifier suspects: {o['suspect']} ({o.get('scope') or 'local'})")
    if d.get("quotes"):
        L.append("Panel: " + " | ".join(str(q) for q in d["quotes"][:2]))
    if d.get("test_file"):
        L.append(f"Scenario: /work/exam/_verify/scenarios/{d['test_file']}")
    if d.get("detail"):
        L.append(f"Failure:\n```\n{str(d['detail'])[-900:]}\n```")
    return "\n".join(L)


def pairs_md(pairs: list[dict]) -> str:
    L = []
    for p in pairs:
        y = p["y"]
        what = (
            "Both must hold: make the change for the finding keep this check passing."
            if p["action"] == "fixup"
            else "The finding wins: this test of yours is out of date. Update it to the "
            "finding's expectation (don't delete it)."
        )
        L.append(
            f"- Finding(s) {', '.join(p['x'])} (commit {p['commit'][:10]}) broke "
            f"`{y['id']}` ({y['group']}). {what}"
        )
    return "\n".join(L)


# -- one round --------------------------------------------------------------------------------
def x_item(ctx, ids: list[str]) -> dict:
    """The fixed side of a pair: the findings of one commit; its rank is their best."""
    items = [ctx.item(i) for i in ids] or [{"kind": "scenario", "prose": "(unattributed)"}]
    best = min(items, key=accept.rank)
    return {**best, "prose": "\n\n".join(ctx_prose(i) for i in items)}


def ctx_prose(item: dict) -> str:
    from .oracle import prose

    return prose(item)


def documented(ws: Path, key: str) -> bool:
    """The setting ``key`` (or its environment variable) is named in the README."""
    from .settings import env_name

    for p in sorted(Path(ws).glob("README*")):
        if p.is_file():
            text = p.read_text(errors="replace")
            if key in text or env_name(key) in text:
                return True
    return False


def setting_results(ws: Path, items: list[dict], m: dict) -> list[dict]:
    """Addendum A's acceptance of each setting item on the measure ``m``: fixed when the
    README documents it and every scenario of the item passes. A scenario pinned to a
    value runs with that value forced; the others run under the default, so the default
    reproduces the old behaviour wherever that was the expected reading. The builder's own
    tests for both values are part of ``m``'s regressions check."""
    out = []
    for it in items:
        scen = it.get("scenarios") or []
        bad = [
            i
            for i in scen
            if accept.status_of(m, "scenarios", i) != "passed"
            and accept.status_of(m, "held", i) != "passed"
        ]
        doc = documented(ws, it["key"])
        why = "; ".join(
            x
            for x in (
                "" if doc else "not documented in the README",
                f"failing: {', '.join(bad)}" if bad else "",
            )
            if x
        ) or (f"{len(scen)} scenario(s) pass" if scen else "documented")
        out.append({"id": it["id"], "key": it["key"], "fixed": doc and not bad, "why": why})
    return out


def run_round(ctx, k: int, defects: list[dict]) -> dict:
    """One repair round on ``ctx.ws``. ``defects``: findings to send (each ``{"id",
    "class": defect|interpretation|setting, ...}``; scenario ids for defects; a setting
    item lists its scenarios in ``scenarios``). ``ctx`` provides:
    ``ws``, ``measure(dir)``, ``session(prompt, purpose, cap) -> result | None``,
    ``brief(defects)``, ``fixup_brief(pairs)``, ``export(ids)``, ``pair(x, y)``,
    ``item(id)``, ``reg_item(reg)``, ``retire(id, why)``,
    ``promote(id)``, ``emit(type, **f)``, ``allows(usd)``, ``fixup_usd``, ``scratch``; and
    optionally ``rescue(regressions, settings) -> [ids]`` (re-pin scenarios a changed
    default broke), ``pair_setting(x ids, y, setting)`` (an undecidable pair that is a
    binary policy) and ``undo_brief(commits)`` (the narrow undo session, see ``drop``).

    **Settings are accepted on their own** (Addendum A). A setting commit that breaks a
    check under its default is dropped by itself: no pair panel, no fix-up (a setting never
    contradicts the spec; it must leave the default behaviour as it was), and a later round
    adds it again. When the defect fixes can't be accepted, the settings that work are kept
    without them (``keep_settings``).

    **A conflicting revert never undoes the round.** Losing commits go through ``drop``:
    ``git revert``, then a zero-context reverse of the commit's own hunks, then one narrow
    session told to take out only that change. If the commit still can't come out: when
    every check it broke is rank 5 (the factory's own) the commit stays and those checks
    are retired (recorded in ``tolerated``); otherwise the head is rebuilt from the base
    without it (commits that built on it are skipped, recorded in ``dropped``). Either way
    the final head is measured, and accepted only when nothing else regressed.

    Returns ``{"round", "sent", "fixed", "regressions", "pairs", "reverted", "dropped",
    "tolerated", "accepted", "why", "base", "head", "now", "interpretations", "settings",
    "usd"}``; ``now`` is the measure of ``head``.

    The round is two steps, so a run can stop and resume between them (a repair session
    alone can take most of a 10-minute slice): ``start_round`` (the session and its
    commits) and ``finish_round`` (acceptance, from ``start_round``'s record)."""
    return finish_round(ctx, k, defects, start_round(ctx, k, defects))


def scenario_ids(defects: list[dict]) -> list[str]:
    """The scenarios a round is judged on: the defects' and the settings' own (a readiness
    item without a test, ``check``, has none: ``ctx.extra_fixed`` judges it)."""
    return [
        d["id"]
        for d in defects
        if d.get("class") not in ("interpretation", "setting") and not d.get("check")
    ] + [i for d in defects if d.get("class") == "setting" for i in d.get("scenarios") or []]


def extra(ctx, ws: Path, defects: list[dict], commits: list[dict]) -> list[str]:
    """The items without a scenario the round fixed (``ctx.extra_fixed``; readiness)."""
    items = [d for d in defects if d.get("check")]
    if not items or not hasattr(ctx, "extra_fixed"):
        return []
    return list(ctx.extra_fixed(ws, items, commits))


def start_round(ctx, k: int, defects: list[dict]) -> dict:
    """Round ``k``'s first step: the head before (measured), the brief, the repair session
    and its commits. Returns the round's record so far (plain JSON)."""
    ws = Path(ctx.ws)
    base = ensure_repo(ws, f"v5: before repair {k}")
    measure_at(ctx, base)
    sent = [d["id"] for d in defects]
    ctx.export(scenario_ids(defects))
    res = ctx.session(ctx.brief(defects), f"repair{k}", "repair")
    usd = float(getattr(res, "cost_usd", 0) or 0)
    shutil.rmtree(ws / "acceptance" / "findings", ignore_errors=True)
    commit_all(ws, f"fix({','.join(sent)}): uncommitted changes after repair {k}")
    return {
        "round": k,
        "sent": sent,
        "base": base,
        "commits": commits_since(ws, base),
        "pairs": [],
        "reverted": [],
        "dropped": [],
        "tolerated": [],
        "interpretations": [],
        "settings": [],
        "usd": usd,
    }


def finish_round(ctx, k: int, defects: list[dict], out: dict) -> dict:
    """Round ``k``'s second step: acceptance, the conflict protocol and the drops, on the
    record ``start_round`` returned. Starts from the head the repair left (a step that died
    half way through is redone from there)."""
    ws = Path(ctx.ws)
    out = {**out, "commits": [dict(c) for c in out["commits"]]}
    base, sent, commits = out["base"], out["sent"], out["commits"]
    for op in ("cherry-pick", "revert"):
        gitcmd.run([*IDENTITY, op, "--abort"], cwd=ws, capture_output=True)
    reset(ws, commits[-1]["sha"] if commits else base)
    before = measure_at(ctx, base)
    items = [d for d in defects if d.get("class") == "setting"]
    sids = {d["id"] for d in items}
    scen = scenario_ids(defects)
    for c in commits:
        c["setting"] = bool(c["findings"]) and set(c["findings"]) <= sids
    if not commits:
        out.update(
            fixed=[],
            regressions=[],
            accepted=False,
            head=base,
            now=before,
            why="the repair changed nothing",
        )
        return out
    after = measure_at(ctx, head(ws))
    d = accept.decide(before, after, scen)
    if (
        d["regressions"]
        and items
        and hasattr(ctx, "rescue")
        and ctx.rescue(d["regressions"], items)
    ):
        after = measure_at(ctx, head(ws))  # re-pinned scenarios: measure again
        d = accept.decide(before, after, scen)
    out.update(fixed=d["fixed"], regressions=d["regressions"])
    sres = setting_results(ws, items, after)
    added = [s["key"] for s in sres if s["fixed"]]
    ex = extra(ctx, ws, defects, commits)
    if d["action"] == "no_fix" and (added or ex):
        d.update(
            action="accept",
            why="nothing broken; "
            + "; ".join(
                x
                for x in (
                    "settings added: " + ", ".join(added) if added else "",
                    "fixed without a test: " + ", ".join(ex) if ex else "",
                )
                if x
            ),
        )
    elif d["action"] == "accept" and (added or ex):
        d["why"] += (f"; {len(added)} setting(s) added" if added else "") + (
            f"; {len(ex)} fixed without a test" if ex else ""
        )
    if d["action"] == "accept":
        out.update(
            accepted=True,
            head=head(ws),
            why=d["why"],
            before=before,
            after=after,
            now=after,
            settings=sres,
            extra=ex,
        )
        return out
    out.update(before=before, after=after)
    if d["action"] in ("no_fix", "reject"):
        return keep_settings(ctx, k, out, commits, items, scen, d["why"])
    # The conflict protocol: every regression a defect commit caused goes to the pair panel.
    prefixes = [measure_at(ctx, c["sha"]) for c in commits]
    pairs = []
    for reg in d["regressions"]:
        c = commits[accept.blame(prefixes, reg)]
        xs = c["findings"] or sent
        if reg["group"] == "held":
            ctx.promote(reg["id"])
        yi = ctx.reg_item(reg)
        if c["setting"]:
            p = {
                "x": xs,
                "commit": c["sha"],
                "y": reg,
                "verdict": "setting",
                "votes": None,
                "x_rank": 5,
                "y_rank": accept.rank(yi),
                "action": "drop_setting",
            }
            pairs.append(p)
            ctx.emit(
                "conflict",
                round=k,
                x=xs,
                y=reg["id"],
                group=reg["group"],
                verdict=p["verdict"],
                action=p["action"],
                x_rank=5,
                y_rank=p["y_rank"],
            )
            continue
        xi = x_item(ctx, xs)
        v = ctx.pair(xi, yi)
        act = accept.resolve(v["verdict"], accept.rank(xi), accept.rank(yi))
        p = {
            "x": xs,
            "commit": c["sha"],
            "y": reg,
            "verdict": v["verdict"],
            "votes": v.get("votes"),
            "x_rank": accept.rank(xi),
            "y_rank": accept.rank(yi),
            "action": act,
        }
        if act == "interpretation":
            # Addendum A: an undecidable pair is never voted on. A binary policy becomes a
            # setting (default: the earlier behaviour, Y; X's side behind the setting, added
            # by a later round); anything else is complex: the build keeps its reading.
            item = {
                "id": f"i{k}-{len(out['interpretations']) + 1}",
                "question": f"Does the spec require this, at the cost of the other check? "
                f"{xi['prose'][:400]}",
                "literal": f"Keep the earlier behaviour: {yi.get('prose', reg['id'])[:400]}",
                "expected": f"Change to: {xi['prose'][:400]}",
                "stakes": "low",
                "source": "undecidable pair",
                "x": xs,
                "y": reg["id"],
                "mode": "auto",
                "reading": "literal",
                "chosen": "literal",
                "kind": "complex",
                "how": "complex: the build's earlier reading is kept",
            }
            if v.get("setting") and hasattr(ctx, "pair_setting"):
                sid = ctx.pair_setting(xs, reg, v["setting"])
                item.update(
                    kind="binary",
                    setting=sid,
                    how=f"binary: setting {v['setting']['key']} ({sid}), default the "
                    "earlier behaviour",
                )
                p["action"] = "setting"
            else:
                p["action"] = "revert_x"
            p["interpretation"] = item["id"]
            out["interpretations"].append(item)
        pairs.append(p)
        ctx.emit(
            "conflict",
            round=k,
            x=xs,
            y=reg["id"],
            group=reg["group"],
            verdict=v["verdict"],
            action=p["action"],
            x_rank=p["x_rank"],
            y_rank=p["y_rank"],
        )
    out["pairs"] = pairs
    need = [
        p
        for p in pairs
        if p["action"] == "fixup" or (p["action"] == "keep_x" and p["y"]["group"] == "tests")
    ]
    pre_fixup = head(ws)
    if need and ctx.allows(ctx.fixup_usd):
        fx = ctx.session(ctx.fixup_brief(need), f"fixup{k}", "repair")
        out["usd"] += float(getattr(fx, "cost_usd", 0) or 0)
        out["fixup"] = True
        shutil.rmtree(ws / "acceptance" / "findings", ignore_errors=True)
        commit_all(
            ws,
            f"fixup({','.join(sorted({x for p in need for x in p['x']}))}): "
            f"uncommitted changes after the fix-up {k}",
        )
    fixups = [{**c, "setting": False} for c in commits_since(ws, pre_fixup)]
    mid = measure_at(ctx, head(ws)) if need else after
    losers = []
    for p in pairs:
        y = p["y"]
        still = accept.status_of(mid, y["group"], y["id"]) != "passed"
        if p["action"] in ("setting", "drop_setting"):  # the default is Y's side
            if p["commit"] not in losers:
                losers.append(p["commit"])
        elif p["action"] == "revert_x" or (p["action"] == "fixup" and still):
            if p["commit"] not in losers:
                losers.append(p["commit"])
            if p["action"] == "revert_x":  # X lost on the spec (or on rank): retired
                for x in p["x"]:
                    ctx.retire(
                        x,
                        f"lost to {y['id']} ({p['verdict']}, rank {p['x_rank']} vs {p['y_rank']})",
                    )
        elif p["action"] == "keep_x" and y["group"] in ("scenarios", "held"):
            ctx.retire(y["id"], f"the pair panel ruled for {', '.join(p['x'])}")
    # A commit made only for findings that lose (a fix-up, a second commit for the same
    # finding) edits their lines: it goes with them.
    allc = commits + fixups
    lost = {x for p in pairs if p["commit"] in losers for x in p["x"]}
    losers += [
        c["sha"]
        for c in allc
        if c["findings"] and set(c["findings"]) <= lost and c["sha"] not in losers
    ]
    tolerated = {p["y"]["id"] for p in pairs if p["action"] == "keep_x"}
    if losers:
        r = drop(ctx, k, allc, losers)
        out["reverted"] = r["done"]
        if r["failed"]:
            stuck = [p for p in pairs if p["commit"] in r["failed"]]
            if stuck and all(
                p["y_rank"] == 5 and p["y"]["group"] in ("scenarios", "held") for p in stuck
            ):
                # The last resort, by the authority order: the checks the commit broke are
                # the factory's own (rank 5); the commit stays and they are retired.
                for p in stuck:
                    ctx.retire(
                        p["y"]["id"],
                        f"broken by {', '.join(p['x'])}, which could "
                        "not be undone on its own (rank 5)",
                    )
                    tolerated.add(p["y"]["id"])
                    out["tolerated"].append({"y": p["y"]["id"], "x": p["x"], "commit": p["commit"]})
            else:
                skipped = rebuild(ws, base, allc, set(losers))
                by = {c["sha"]: c for c in allc}
                out["reverted"] = [
                    {"sha": s, "findings": by[s]["findings"], "how": "rebuild"} for s in losers
                ]
                out["dropped"] = [
                    {
                        "sha": c["sha"],
                        "findings": c["findings"],
                        "why": "it built on a dropped commit",
                    }
                    for c in skipped
                ]
    final = measure_at(ctx, head(ws)) if (losers or need) else after
    d2 = accept.decide(before, final, scen)
    left = [r for r in d2["regressions"] if r["id"] not in tolerated]
    sres = setting_results(ws, items, final)
    gone = {x for v in out["reverted"] + out["dropped"] for x in v["findings"]}
    kept_bad = {x for t in out["tolerated"] for x in t["x"]}
    for s in sres:
        if s["id"] in gone:
            s.update(fixed=False, why="dropped: it broke a check under its default")
        elif s["id"] in kept_bad:
            s.update(fixed=False, why="kept, but it broke a check under its default")
    ex = [x for x in extra(ctx, ws, defects, allc) if x not in gone]
    out.update(fixed=d2["fixed"], after=final, open_regressions=left, settings=sres, extra=ex)
    if left or not d2["valid"]:
        why = (
            (
                f"{len(left)} regression(s) remain after the conflict protocol: "
                + ", ".join(r["id"] for r in left[:6])
            )
            if left
            else "the user's tests fail"
        )
        return keep_settings(ctx, k, out, allc, items, scen, why, gone)
    if (
        not d2["fixed"]
        and not any(p["action"] == "keep_x" for p in pairs)
        and not any(s["fixed"] for s in sres)
        and not ex
    ):
        reset(ws, base)
        out.update(
            accepted=False, head=base, now=before, why="nothing fixed after the conflict protocol"
        )
        return out
    rv = list(dict.fromkeys(x for r in out["reverted"] for x in r["findings"]))
    ns = sum(1 for s in sres if s["fixed"])
    out.update(
        accepted=True,
        head=head(ws),
        now=final,
        why=f"{len(d2['fixed'])} fixed"
        + (f"; {ns} setting(s) added" if ns else "")
        + (f"; {len(ex)} fixed without a test" if ex else "")
        + (f"; reverted {', '.join(rv)}" if rv else "")
        + (
            f"; dropped {', '.join(x for c in out['dropped'] for x in c['findings'])}"
            if out["dropped"]
            else ""
        )
        + (
            f"; kept with a regression: {', '.join(t['y'] for t in out['tolerated'])}"
            if out["tolerated"]
            else ""
        )
        + f"; {len(pairs)} conflict(s) resolved",
    )
    return out


def keep_settings(
    ctx,
    k: int,
    out: dict,
    commits: list[dict],
    items: list[dict],
    scen: list[str],
    why: str,
    gone=(),
) -> dict:
    """The defect fixes of round ``k`` can't be accepted (``why``): keep the settings that
    work on their own (Addendum A: a setting never rides on a defect repair). Every commit
    but the setting commits comes out of the head (``drop``, without a session; else a
    rebuild from the base); the head is measured and kept if a setting works and nothing
    regressed. Otherwise the round is undone."""
    ws = Path(ctx.ws)
    base, before = out["base"], out["before"]
    keep = [c for c in commits if c.get("setting") and not set(c["findings"]) & set(gone)]
    if not keep or not items:
        reset(ws, base)
        out.update(accepted=False, head=base, now=before, why=why)
        return out
    others = [c["sha"] for c in commits if c not in keep]
    reset(ws, commits[-1]["sha"])  # the round's whole head, then take out
    r = drop(ctx, k, commits, others, session=False) if others else {"done": [], "failed": []}
    skipped = []
    if r["failed"]:
        skipped = rebuild(ws, base, commits, set(others))
    m = measure_at(ctx, head(ws))
    d = accept.decide(before, m, scen)
    sres = setting_results(ws, items, m)
    kept = {f for c in keep if c not in skipped for f in c["findings"]}
    for s in sres:
        if s["id"] not in kept:
            s.update(fixed=False, why="not kept: the round's defect fixes were undone")
    ok = [s["key"] for s in sres if s["fixed"]]
    if d["regressions"] or not d["valid"] or not ok:
        reset(ws, base)
        out.update(
            accepted=False,
            head=base,
            now=before,
            settings=sres,
            why=why
            + (
                "; no setting works on its own"
                if not ok
                else f"; the settings alone break {len(d['regressions'])} check(s)"
                if d["regressions"]
                else "; the settings alone fail the user's tests"
            ),
        )
        return out
    by = {c["sha"]: c for c in commits}
    out.update(
        accepted=True,
        head=head(ws),
        now=m,
        after=m,
        fixed=d["fixed"],
        settings=sres,
        kept="settings",
        dropped=out["dropped"]
        + [
            {
                "sha": c["sha"],
                "findings": c["findings"],
                "why": "a setting that no longer applies without the defect fixes",
            }
            for c in skipped
        ],
        reverted=[
            {"sha": s, "findings": by[s]["findings"], "how": "settings only"} for s in others
        ],
        why=f"{why}; the defect fixes are undone and {len(ok)} setting(s) kept: " + ", ".join(ok),
    )
    return out


def measure_at(ctx, sha: str) -> dict:
    """The measure of commit ``sha`` of ``ctx.ws``, cached on ``ctx`` by sha and the
    active scenario set (re-verify adds scenarios)."""
    cache = getattr(ctx, "_measures", None)
    if cache is None:
        cache = {}
        ctx._measures = cache
    store = getattr(ctx, "store", None)
    key = (sha, store.key(store.active()) if store is not None else None)
    if key not in cache:
        d = checkout_copy(Path(ctx.ws), sha, Path(ctx.scratch) / f"at-{sha[:12]}")
        try:
            cache[key] = ctx.measure(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    return cache[key]

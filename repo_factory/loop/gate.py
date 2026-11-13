"""The release gate, after delivery: is this build ready to publish?

It reads the build's events (``build_finished`` ok, a delivered branch), and from
``<den>/v5/r<round>/``: ``readiness.json`` (the security review's findings and the final
readiness scorecard, the same data as SECURITY.md and READINESS.md), ``findings.json``
(completeness). The round's evidence
(``exam/evidence/``: the security and readiness reports) must exist; a round from before
the exam moved out delivered SECURITY.md and READINESS.md in its tree instead.

**ready** when all of these hold, otherwise **held** with every failing reason:
- no open critical or high security finding (from a review that ran);
- the documented test commands run green (the scorecard's ``tests_green``);
- completeness: no flow in the definition of done is broken (SCOPE.md's, or for a spec that
  passed through, the flows drawn from its own text; with neither it is a gap);
- it listens on localhost and the README's quick start works as written (``SAFE_ITEMS``: the
  harness's ``commands`` run; the session's install items are advisory, AU1 #6).

There is no ``p_hidden`` check any more (AU1 #5): it was arithmetic on the claims, not a
measurement, and in 36 recorded rounds it never held a build. A ``gate.json`` from before
still lists it; it is shown as it was recorded.

Only the product holds a build (AU1 #1). What repo-factory itself failed to measure is a **gap**,
never a hold: a flow of the definition of done the harness could not measure (``environment``:
its sandbox fails the flow on the build and on the verifier's own copy, ``Run.triage``;
``unchecked``: no scenario checked it), and a security review that did not run (a cap or a
cut-off build). No round of the product could change those, so the gate judges the rest and
says what it could not measure: the verdict's ``gaps``, shown beside the checks and written
into the release notes. A round where nothing at all was measured (no flow, no security
review: a cut-off build) leaves nothing to judge, and holds on completeness."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .. import gitcmd
from ..events import load_state

GATE_FILE = "gate.json"  # the verdict beside its round (UXI-1): .den/v5/r<N>/gate.json
# The scorecard items that hold a build when red: it listens on localhost unless configured
# (the checks session's, still: the harness sees only ports, not the address bound), and the
# README's quick start works as written (``commands``: the harness runs it on a fresh copy).
# The session's own ``install``, ``empty_start`` and ``first_admin`` are advisory rows of the
# scorecard: it said "install green" in 31 of 32 scorecards while the harness's quick start
# was red in 22 of 34 (AU1 #6).
SAFE_ITEMS = ("bind_localhost", "commands")
# v5's finding statuses (readiness.py): fixed (a test checks it), passes (its test passes
# after other fixes), unconfirmed (its probe passed in the harness: not reproduced) are
# closed; open, and addressed (changed with no test to show it), are not.
CLOSED = ("fixed", "passes", "unconfirmed")
# A flow's status (``Run.completeness``) that says the harness could not measure it: a gap,
# never a hold (AU1 #1), with what the gap line says of it
NOT_MEASURED = {
    "environment": "the sandbox fails {} in both copies",
    "unchecked": "no scenario checked {}",
}
NOTHING_MEASURED = (
    "nothing was measured: no flow was checked and the security review did "
    "not run; Retry builds it again"
)
NO_DOD = (
    "not scored: no definition of done (the scope wrote none and none could be drawn from the spec)"
)


def completeness_check(
    flows: list[tuple], done: int | None = None, total: int | None = None
) -> dict:
    """The completeness check from ``(id, title, status)`` rows: ``works``, a status of
    ``NOT_MEASURED`` (a gap) or anything else (broken: the hold). ``{"ok", "detail"}``, with
    ``gap`` (one line per cause) when a flow was not measured. ``done``/``total``: the
    record's own counts, when it has them."""
    done = sum(1 for _, _, s in flows if s == "works") if done is None else int(done)
    total = len(flows) if total is None else int(total)
    broken = [f"{i} {t}" for i, t, s in flows if s != "works" and s not in NOT_MEASURED]
    gaps = []
    for s, said in NOT_MEASURED.items():
        names = [f"{i} {t}" for i, t, st in flows if st == s]
        if names:
            gaps.append(
                f"{', '.join(names)} not measured: "
                + said.format("it" if len(names) == 1 else "them")
            )
    n = sum(1 for _, _, s in flows if s in NOT_MEASURED)
    out = {
        "ok": not broken,
        "detail": f"{done}/{total} flows work end to end"
        + (f"; not yet: {', '.join(broken)}" if broken else "")
        + (f"; {n} not measured" if n else ""),
    }
    if gaps:
        out["gap"] = "; ".join(gaps)
    return out


def _json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def delivered_files(out: Path, branch: str) -> set[str]:
    p = gitcmd.run(
        ["ls-tree", "-r", "--name-only", branch], cwd=out, capture_output=True, text=True
    )
    return set(p.stdout.split()) if p.returncode == 0 else set()


def evaluate(task: Path, *, build: int | None = None) -> dict:
    """``{"verdict": ready|held, "reasons": [...], "checks": {name: {"ok", "detail"}},
    "round", "branch", "out", "cost_usd"}``."""
    task = Path(task)
    st = load_state(task, build=build) if build else load_state(task)
    checks: dict[str, dict] = {}

    def put(name, ok, detail):
        checks[name] = {"ok": bool(ok), "detail": detail}

    if st is None:
        put("delivered", False, "no build events")
        return _verdict(checks, None, None, None, None)
    dl = st.get("delivered") or {}
    rnd = st.get("build")
    root = task / ".den" / "v5" / f"r{rnd}"
    out = Path(dl.get("out") or task / "out")
    branch = dl.get("branch")
    capped = capped_stages(task, rnd)
    if capped:
        stage = capped[0]
        put(
            "spend",
            False,
            f"the spend cap stopped it before the {stage} stage could run "
            f"(${float(st.get('cost_usd') or 0):.2f} spent); raise the cap in Settings › Spend, "
            "then Retry",
        )
    put(
        "delivered",
        st["status"] == "ok" and branch and dl.get("node") is not None,
        f"build {st['status']}" + (f", branch {branch}" if branch else ", nothing delivered"),
    )

    files = delivered_files(out, branch) if branch and (out / ".git").exists() else set()
    checks.update(record_checks(root, files))
    v = _verdict(checks, rnd, branch, str(out), st.get("cost_usd"))
    # EX-PLAN §7: the gate reads records, so its verdict is the builder's version's
    from .recheck import built_by

    v["judged_by"] = built_by(root)
    return v


def capped_stages(task: Path, rnd) -> list[str]:
    """The stages of round ``rnd`` that the spend cap kept from running (``v5_stage`` events
    with status ``capped``), in order. A capped build is held with the cap named first, so
    the reason a person reads is the money, not the empty tree it left."""
    from ..events import log_path, read_events

    out: list[str] = []
    for e in read_events(log_path(Path(task) / ".den")):
        if (
            e.get("type") == "v5_stage"
            and e.get("status") == "capped"
            and (rnd is None or e.get("build") in (None, rnd))
            and e.get("stage") not in out
        ):
            out.append(str(e.get("stage")))
    return out


_NOW: dict[tuple, tuple] = {}


def _records_sig(task: Path, rnd) -> tuple:
    """What the gate reads, by size and time: the den's event log, the round's records and
    the delivered branch's head."""
    from ..events import log_path

    den = task / ".den"
    paths = [
        log_path(den),
        den / "v5" / f"r{rnd}" / "findings.json",
        den / "v5" / f"r{rnd}" / "readiness.json",
        den / "v5" / f"r{rnd}" / "exam",
        task / "out" / ".git" / "HEAD",
        task / "out" / ".git" / "refs" / "heads",
    ]
    sig: list[tuple[int, int] | None] = []
    for p in paths:
        try:
            st = p.stat()
            sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append(None)
    return tuple(sig)


def now(task, rnd: int | None) -> dict:
    """The gate on round ``rnd`` as the **installed** repo-factory judges it (``evaluate``), cached
    on its records (ASKF). Publish anyway's dialog and its press both read this, so what the
    owner is shown is what the press checks, whatever version judged the build when it
    stopped. ``stored``-only differences (an upgrade that changed a check) show in the dialog;
    they never refuse the press."""
    task = Path(task or ".")
    key = (str(task), rnd)
    sig = _records_sig(task, rnd)
    hit = _NOW.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    g = evaluate(task, build=rnd)
    if len(_NOW) > 64:
        _NOW.clear()
    _NOW[key] = (sig, g)
    return g


def judged(task, rnd: int | None, stored: dict | None) -> dict:
    """The gate Publish anyway's dialog and press both read (ASKF): ``now`` when the round's
    records are there, else the ``stored`` verdict as it was recorded (a round from before
    the records moved under ``.den/v5``)."""
    if task and rnd and (Path(task) / ".den" / "v5" / f"r{rnd}").is_dir():
        return now(task, rnd)
    return dict(stored or {})


def failing(g: dict | None) -> list[str]:
    """The names of a gate result's failing checks, in order."""
    return [
        k
        for k, c in ((g or {}).get("checks") or {}).items()
        if isinstance(c, dict) and not c.get("ok")
    ]


CANT_REVISE = {"environment": "fails in the sandbox", "unchecked": "was not checked"}


def cant_revise(g: dict | None, task, now: dict | None = None) -> str | None:
    """ "revise can't fix this: F5 Detect drift fails in the sandbox …" when **every** failing
    check of the verdict ``g`` is a flow repo-factory itself could not measure (``environment``,
    ``unchecked`` in the round's ``findings.json``) and the gate as the installed repo-factory judges
    the round (``now``, ``judged``) holds on nothing a revise could change: no round of the
    product changes it, so neither the owner's Revise nor the automatic repair round is
    offered (ASKF; a verdict stored before AU1 #1 still holds on such a flow). It ends with the
    press that helps: Publish anyway or Close, or Re-check when today's gate holds on nothing.
    None: a revise may help (Repo Recall on 2026-10-28: today's gate held it on a README
    command, which the console's round 6 fixed)."""
    bad, rnd = failing(g), (g or {}).get("round")
    if bad != ["completeness"] or not task or not rnd:
        return None
    today = failing(now) if now is not None else bad
    if [k for k in today if k != "completeness"]:
        return None  # something a revise can change holds it today
    rd = Path(task) / ".den" / "v5" / f"r{rnd}"
    comp = (_json(rd / "findings.json") or {}).get("completeness") or {}
    flows = [
        f for f in comp.get("flows") or [] if isinstance(f, dict) and f.get("status") != "works"
    ]
    if not flows or any(f.get("status") not in CANT_REVISE for f in flows):
        return None
    said = "; ".join(
        f"{f.get('id')} {f.get('title')} {CANT_REVISE[f['status']]}".strip() for f in flows
    )
    then = (
        "the repo-factory you run now does not hold on it: Re-check with current repo-factory"
        if not today
        else "Publish anyway, or Close"
    )
    return f"revise can't fix this: {said} (repo-factory's own sandbox, not the product); {then}"


def with_item(rd: Path, files: set, item: dict, at: Path) -> dict:
    """The record checks on a round's records with one final scorecard item replaced
    (``item``, measured again now: the README's quick start), in a copy at ``at``. The corpus
    replay and Re-check use it; the round's own records are never written."""
    at.mkdir(parents=True)
    for name in ("findings.json", "prediction.json"):
        if (Path(rd) / name).is_file():
            shutil.copy2(Path(rd) / name, at / name)
    if (Path(rd) / "exam" / "evidence").is_dir():
        shutil.copytree(Path(rd) / "exam" / "evidence", at / "exam" / "evidence")
    ready = _json(Path(rd) / "readiness.json") or {}
    final = ready.get("final") or {}
    ready["final"] = {
        **final,
        "items": [i for i in final.get("items") or [] if i.get("id") != item["id"]] + [item],
    }
    (at / "readiness.json").write_text(json.dumps(ready, indent=1))
    return record_checks(at, files)


def record_checks(root: Path, files: set | None = None) -> dict:
    """The gate's checks that read the round's records (``<den>/v5/r<N>/``, or a corpus
    entry's copy): documents, security, tests, safe_defaults, completeness.
    ``files``: the delivered tree's files (a round from before the evidence: its documents
    were in the tree)."""
    root = Path(root)
    files = set(files or ())
    checks: dict[str, dict] = {}

    def put(name, ok, detail):
        checks[name] = {"ok": bool(ok), "detail": detail}

    # EX-PLAN §4.4: the reports are the round's evidence, beside the tree (a round from
    # before the exam moved out delivered them into it instead)
    ev = root / "exam" / "evidence"
    have = [n for n in ("security.md", "readiness.md") if (ev / n).is_file()]
    if len(have) == 2:
        put(
            "documents",
            True,
            "the evidence (security and readiness reports, the checks) is kept with the build",
        )
    else:
        missing = [n for n in ("SECURITY.md", "READINESS.md") if n not in files]
        put(
            "documents",
            not missing,
            "SECURITY.md and READINESS.md delivered"
            if not missing
            else "no evidence for the delivered tree (the security and readiness reports)",
        )

    ready = _json(root / "readiness.json") or {}
    sec = ready.get("security") or {}
    if not sec.get("ran"):
        # a factory gap (AU1 #1): a cap or a cut-off build, nothing the product can change
        why = str(sec.get("why") or "").strip()
        put("security", True, "not reviewed: the security review did not run")
        checks["security"]["gap"] = "the security review did not run" + (f" ({why})" if why else "")
    else:
        open_ = [
            f
            for f in sec.get("findings") or []
            if str(f.get("severity")).lower() in ("critical", "high")
            and str(f.get("status")).lower() not in CLOSED
        ]
        put(
            "security",
            not open_,
            "no open critical or high finding"
            if not open_
            else "open: " + "; ".join(f"{f.get('severity')}: {f.get('title')}" for f in open_),
        )

    final = {i.get("id"): i for i in (ready.get("final") or {}).get("items") or []}
    t = final.get("tests_green")
    put(
        "tests",
        t is not None and t.get("status") == "green",
        (t or {}).get("why") or "no result for the documented test commands",
    )
    red = [i for k, i in final.items() if k in SAFE_ITEMS and i.get("status") == "red"]
    seen = [k for k in SAFE_ITEMS if k in final]
    put(
        "safe_defaults",
        final and not red,
        ("localhost and quick start: " + ", ".join(f"{k} {final[k].get('status')}" for k in seen))
        if final and not red
        else (
            "red: " + "; ".join(f"{i.get('title')} ({i.get('why')})" for i in red)
            if red
            else "no readiness scorecard"
        ),
    )
    checks["safe_defaults"]["red"] = [
        k for k in SAFE_ITEMS if k in final and final[k].get("status") == "red"
    ]

    comp = (_json(root / "findings.json") or {}).get("completeness")
    if comp and comp.get("total") and comp.get("flows"):
        checks["completeness"] = completeness_check(
            [(f.get("id"), f.get("title"), f.get("status")) for f in comp["flows"]],
            comp.get("done"),
            comp["total"],
        )
        # a gap is judged around; with no flow measured and no security review there is
        # nothing left to judge: the build was cut off, and a new build is what helps
        if not sec.get("ran") and all(f.get("status") in NOT_MEASURED for f in comp["flows"]):
            put("completeness", False, NOTHING_MEASURED)
    elif comp and comp.get("total"):  # a record without its flows: the count
        put(
            "completeness",
            comp["done"] == comp["total"],
            f"{comp['done']}/{comp['total']} flows work end to end",
        )
    elif not sec.get("ran"):
        put("completeness", False, NOTHING_MEASURED)
    else:
        # EV7: no definition of done (none in the scope, none drawn from the spec) is repo-factory's
        # own gap, not the product's (AU1 #1, as FX1's environment gaps)
        put("completeness", True, "not scored: no definition of done")
        checks["completeness"]["gap"] = NO_DOD
    return checks


# Publish anyway: the holds the owner may override, by default, as
# ``publish.anyway.may_override``. These are never overridable, whatever the setting says:
# nothing delivered, an open critical or high security finding (a review that did not run is
# a gap, not a hold), a secrets hit, and a product that listens beyond loopback by
# default (a red ``bind_localhost``: a network exposure, treated like security).
# (wording, the honesty check and the jargon scan, are never holds)
MAY_OVERRIDE = ("tests", "completeness", "safe_defaults")
NEVER = {
    "delivered": "nothing was delivered: there is nothing to publish",
    "security": "an open critical or high security finding is never overridden",
    "secrets": "secrets are never pushed",
    "bind_localhost": "it listens beyond loopback by default: a network exposure is "
    "never overridden",
}
PREPUSH = {"honesty check": "honesty", "jargon scan": "jargon", "secrets scan": "secrets"}


def prepush_holds(reason: str | None) -> list[str]:
    """The pre-push checks behind a hold, from the idea's ``reason`` (``not pushed: secrets
    scan: …; honesty check: …``): ``["secrets", "honesty"]``."""
    r = reason or ""
    if not r.startswith("not pushed:"):
        return []
    return [v for k, v in PREPUSH.items() if k in r]


def override_plan(checks: dict, prepush: list[str], may_override=None) -> dict:
    """What publishing over a hold would override, and what refuses it: ``{"overrides":
    [(name, detail)], "refused": [(name, why)]}``. ``checks``: the gate's (only failing ones
    count); ``prepush``: the pre-push checks that would hold its push (``precheck.holds``;
    their detail here is the short one NOTICE discloses)."""
    allowed = set(MAY_OVERRIDE if may_override is None else may_override)
    overrides, refused = [], []
    for k, c in (checks or {}).items():
        if c.get("ok"):
            continue
        detail = c.get("detail") or ""
        if k in NEVER:
            refused.append((k, NEVER[k]))
        elif k == "safe_defaults" and "bind_localhost" in (c.get("red") or []):
            refused.append(("bind_localhost", NEVER["bind_localhost"]))
        elif k in allowed:
            overrides.append((k, detail))
        else:
            refused.append(
                (k, f"{k} is not among the holds you may override (publish.anyway.may_override)")
            )
    for k in prepush:
        if k in NEVER:
            refused.append((k, NEVER[k]))
        elif k in allowed:
            overrides.append((k, f"the pre-push {k} check"))
        else:
            refused.append(
                (k, f"{k} is not among the holds you may override (publish.anyway.may_override)")
            )
    return {"overrides": overrides, "refused": refused}


def gaps_of(checks: dict) -> list[str]:
    """What the factory could not measure (AU1 #1), from the checks' ``gap``: never a hold."""
    return [f"{k}: {v['gap']}" for k, v in (checks or {}).items() if (v or {}).get("gap")]


def _verdict(checks, rnd, branch, out, cost) -> dict:
    reasons = [f"{k}: {v['detail']}" for k, v in checks.items() if not v["ok"]]
    return {
        "verdict": "held" if reasons else "ready",
        "reasons": reasons,
        "checks": checks,
        "gaps": gaps_of(checks),
        "round": rnd,
        "branch": branch,
        "out": out,
        "cost_usd": cost,
    }


def record(taskdir: Path, g: dict) -> None:
    """The gate's verdict on its round, kept beside the round (additive: one small file per
    judged round, so a later round never hides an earlier one's verdict). Never raises."""
    n = g.get("round")
    if not n:
        return
    try:
        d = Path(taskdir) / ".den" / "v5" / f"r{int(n)}"
        d.mkdir(parents=True, exist_ok=True)
        rec = {k: g.get(k) for k in ("verdict", "reasons", "round", "judged_by")}
        rec["gaps"] = list(g.get("gaps") or [])
        rec["checks"] = {
            k: {
                "ok": bool((v or {}).get("ok")),
                "detail": (v or {}).get("detail"),
                **({"gap": v["gap"]} if (v or {}).get("gap") else {}),
            }
            for k, v in (g.get("checks") or {}).items()
        }
        rec["at"] = round(time.time(), 3)
        tmp = d / (GATE_FILE + ".tmp")
        tmp.write_text(json.dumps(rec, indent=1))
        tmp.replace(d / GATE_FILE)
    except (OSError, TypeError, ValueError):
        pass

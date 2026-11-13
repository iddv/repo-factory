"""The readiness stage: after re-verify, before delivery (docs/design/build-v5.md, Addendum C).

A verified v1 still isn't well set up to become a product if anyone on the network can
exhaust its memory, it starts LAN-exposed with demo data, or nobody can install it from its
README (the h2h review). This stage checks that by doing it, then repairs what it can:

1. **security**: a fresh full-tools session on a copy of the build writes a threat model
   (entry points, who reaches them, assets), probes the running product (authN/authZ,
   hostile HTTP, injection, auth, secrets and defaults, dependencies, error pages) and
   reports findings with a severity, a hand repro and a **probe**: a pytest test that asserts
   the secure behaviour. Probes join the scenario store, so the harness confirms them (a
   probe that passes in the harness's run is *unconfirmed*), guards them as regressions and
   delivers them in the acceptance suite.
2. **checks**: a second fresh session does the operational checks (a clean install from the
   README, safe defaults, configuration, backup and restore, restart, shutdown, health, logs,
   upgrade story), with probes for the red ones it can test; the harness checks what it can
   itself (``DETERMINISTIC``: CI workflow, lockfile, ``.gitignore``, README sections).
3. **repair** (two resumable steps, ``repair.start_round``/``finish_round``): critical and
   high findings (class ``security``, rank 2), medium ones when the budget allows a full
   round (``medium: if_budget``), and fixable red readiness items (class ``readiness``,
   rank 3; the safe defaults are the owner's standing rule, rank 1) in one round on the
   builder's session. The repairer sees every visible scenario and may update one that only
   encoded an old unsafe default (``harvest``).
4. Delivery (``finalize``) re-checks the delivered tree and writes the security and
   readiness reports (``render_security``, ``render_readiness``) into the evidence, beside
   the tree, never in it (EX-PLAN §4.4).

The record is ``Run.ready`` (saved with the run's state) and ``<run>/readiness.json``."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import yaml

from ..harness import links
from . import acceptance, repair, verify
from .common import BudgetExceeded, prompt

SEVERITIES = ("critical", "high", "medium", "low")
STATUS = ("green", "amber", "red", "n/a")
# The scorecard, in order: id -> (title, how it is checked: "session" (the checks session,
# by doing it), "harness" (deterministic, here), "run" (the harness runs the README's
# commands as written: ``commands.py``), "delivery" (from the delivered tree)).
CHECKS = {
    "install": ("Clean install from the README", "session"),
    "commands": ("The README's quick-start commands work as written (install, run, backup)", "run"),
    "bind_localhost": ("Listens on localhost unless configured", "session"),
    "empty_start": ("Starts empty; demo data only on an explicit flag or command", "session"),
    "first_admin": ("First-run admin setup, no default password", "session"),
    "config": ("Configuration by environment or file, every option documented", "session"),
    "data_location": ("Data location documented", "session"),
    "backup_restore": ("Backup and restore work end to end", "session"),
    "restart": ("Data survives a restart", "session"),
    "restart_now": ("Starts again at once after a stop or a crash (services)", "run"),
    "shutdown": ("Graceful shutdown", "session"),
    "health": ("Health endpoint (services)", "session"),
    "logs": ("Readable logs, no secrets or personal data", "session"),
    "upgrade": ("Schema changes and upgrades keep the data", "session"),
    "ci": ("CI workflow installs the project and runs the tests", "harness"),
    "lockfile": ("Dependencies locked or pinned", "harness"),
    "gitignore": ("Sensible .gitignore, no build artefacts in the repository", "harness"),
    "readme": (
        "README covers install, configure, run, upgrade, backup and restore, "
        "troubleshooting and limitations",
        "harness",
    ),
    "tests_green": ("The documented test commands run green, as written", "run"),
    "adopted": ("Every fix comes with a test of the product's own", "harness"),
    "docs_plain": ("User-facing docs in plain words", "delivery"),
    "intermittent": ("Checks that failed only some of the time", "delivery"),
    "verifiable": ("Steps the check's sandbox can't run (not verifiable here)", "delivery"),
}
DETERMINISTIC = tuple(k for k, (_, how) in CHECKS.items() if how == "harness")
RUN = tuple(k for k, (_, how) in CHECKS.items() if how == "run")
# The owner's standing rule (Addendum C): safe defaults outrank the factory's own scope.
SAFE_DEFAULTS = ("bind_localhost", "empty_start", "first_admin")
# A service that can't start again at once after a stop or a crash loses requests (the
# round-2 webhook relay: ~60 s): a reliability bug, rank 2 like a security finding.
RELIABILITY = ("restart", "restart_now")
# max_usd None: the step's session has no cap of its own (the owner's rule); a number caps it
# and gives the step a window of its own (``Budget.window``).
DEFAULTS = {
    "security": {"max_usd": None, "max_turns": 60, "timeout_s": 1200},
    "checks": {"max_usd": None, "max_turns": 40, "timeout_s": 900},
    "repair": {
        "max_usd": None,
        "max_turns": 60,
        "timeout_s": 1500,
        "min_usd": 0.5,
        "resume": False,
    },
    "medium": "if_budget",
    "start_usd": 0.3,
    # the README's commands (commands.py): per quick-start step, per test command,
    # all test commands, a start, and serving again after a restart
    "commands": {"step_s": 60, "test_s": 900, "tests_total_s": 1500, "start_s": 25, "serve_s": 15},
}


# What each step typically costs: the reserve rule's figures under a global cap when the
# step has no max_usd of its own. Never a cap.
TYPICAL_USD = {"security": 1.2, "checks": 0.6, "repair": 1.5}


def step_usd(run, part: str) -> float:
    """The step's max_usd, or what it typically costs: for the reserve rule only."""
    v = cfg(run, part).get("max_usd")
    return float(v) if v else TYPICAL_USD[part]


def step_cap(run, part: str) -> float | None:
    """The step's own cap (its max_usd), or None."""
    v = cfg(run, part).get("max_usd")
    return float(v) if v else None


def cfg(run, part: str | None = None):
    c = run.rules.get("readiness") or {}
    if part is None:
        return c
    if isinstance(DEFAULTS.get(part), dict):
        return {**DEFAULTS[part], **(c.get(part) or {})}
    return c.get(part, DEFAULTS.get(part))


def new_record() -> dict:
    return {
        "next": None,
        "security": {"ran": False},
        "checks": {"ran": False},
        "sent": [],
        "round": None,
        "skipped": None,
        "scenarios_updated": [],
        "final": None,
    }


# -- deterministic checks -------------------------------------------------------------------
def _item(
    cid: str,
    status: str,
    why: str,
    next_: str = "",
    fixable: bool = True,
    evidence: str = "",
    source: str = "harness",
) -> dict:
    return {
        "id": cid,
        "title": CHECKS[cid][0],
        "status": status,
        "why": why,
        "next": next_ if status != "green" else "",
        "fixable": fixable and status != "green",
        "evidence": evidence,
        "source": source,
    }


def _tracked(root: Path) -> list[str]:
    """The files of ``root``: git's tracked list in a work tree, else every file."""
    if (root / ".git").exists():
        try:
            return repair.git(root, "ls-files").splitlines()
        except RuntimeError:
            pass
    return [
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts
    ]


TEST_CMD = re.compile(r"pytest|unittest|\btest\b")
INSTALL = re.compile(
    r"pip install|uv (?:sync|pip|run|tool)|poetry install|setup-python|"
    r"npm (?:ci|install)|pnpm install|yarn|go mod|cargo (?:build|test)|"
    r"bundle install|install\.sh"
)


def check_ci(root: Path) -> dict:
    wf = root / ".github" / "workflows"
    files = sorted([*wf.glob("*.yml"), *wf.glob("*.yaml")]) if wf.is_dir() else []
    if not files:
        return _item(
            "ci",
            "red",
            "no CI workflow",
            "add .github/workflows/test.yml: on push "
            "and pull request, install the project exactly as the README says and "
            "run the whole test suite",
        )
    good, bad = [], []
    for f in files:
        t = f.read_text(errors="replace")
        try:
            y = yaml.safe_load(t)
        except yaml.YAMLError:
            bad.append(f"{f.name} is not valid YAML")
            continue
        lines = "\n".join(x for x in t.splitlines() if not re.match(r"\s*(-\s*)?name\s*:", x))
        if isinstance(y, dict) and TEST_CMD.search(lines) and INSTALL.search(lines):
            good.append(f.name)
    if good:
        return _item("ci", "green", f".github/workflows/{good[0]} installs and runs the tests")
    return _item(
        "ci",
        "amber",
        "; ".join(bad) or "a workflow exists but doesn't install the project and run its tests",
        "make .github/workflows/test.yml install the project and run the whole test suite",
    )


LOCKS = (
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "pdm.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "go.sum",
    "Gemfile.lock",
    "composer.lock",
    "requirements.lock",
)


def _pyproject_deps(root: Path) -> list[str] | None:
    p = root / "pyproject.toml"
    if not p.is_file():
        return None
    try:
        import tomllib

        d = tomllib.loads(p.read_text(errors="replace"))
    except Exception:
        return None
    return list((d.get("project") or {}).get("dependencies") or []) + list(
        ((d.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    )


def check_lockfile(root: Path) -> dict:
    have = [n for n in LOCKS if (root / n).is_file()]
    if have:
        return _item("lockfile", "green", f"{have[0]} pins the dependencies")
    reqs = sorted(root.glob("requirements*.txt"))
    lines = [
        x.split("#")[0].strip() for r in reqs for x in r.read_text(errors="replace").splitlines()
    ]
    lines = [x for x in lines if x and not x.startswith(("-", "--"))]
    loose = [x for x in lines if "==" not in x and "@" not in x]
    deps = [d for d in _pyproject_deps(root) or [] if str(d).lower() not in ("python",)]
    pkg = root / "package.json"
    node = {}
    if pkg.is_file():
        try:
            j = json.loads(pkg.read_text())
            node = {**(j.get("dependencies") or {}), **(j.get("devDependencies") or {})}
        except ValueError:
            pass
    if lines and not loose and not node:
        return _item("lockfile", "green", f"{reqs[0].name} pins every dependency with ==")
    if not lines and not deps and not node:
        if (
            (root / "pyproject.toml").is_file()
            or any(root.glob("*.py"))
            or any(root.glob("*/__init__.py"))
        ):
            return _item(
                "lockfile", "green", "no third-party runtime dependencies (standard library only)"
            )
        return _item(
            "lockfile",
            "amber",
            "no dependency manifest found",
            "declare the dependencies (or state that there are none) in a manifest",
        )
    what = ", ".join((loose or deps or list(node))[:4])
    return _item(
        "lockfile",
        "red",
        f"dependencies without a lockfile or exact pins ({what})",
        "commit a lockfile (uv.lock, package-lock.json, ...) or pin every dependency with ==",
    )


JUNK = re.compile(
    r"(^|/)([^/]+\.egg-info|__pycache__|\.pytest_cache|node_modules|"
    r"\.venv|venv|dist|build)(/|$)|\.pyc$"
)


def check_gitignore(root: Path) -> dict:
    files = _tracked(root)
    junk = sorted({m.group(2) or m.group(0) for f in files if (m := JUNK.search(f))})
    p = root / ".gitignore"
    py = any(f.endswith(".py") for f in files)
    if not p.is_file():
        return _item(
            "gitignore",
            "red",
            "no .gitignore"
            + (f"; build artefacts in the repository: {', '.join(junk[:4])}" if junk else ""),
            "add a .gitignore for caches, virtual environments, build output and local "
            "data files" + ("; remove the artefacts" if junk else ""),
        )
    t = p.read_text(errors="replace")
    miss = []
    if py and not re.search(r"__pycache__|\*\.py\[?c", t):
        miss.append("Python caches")
    if py and not re.search(r"(^|/)\.?venv", t, re.M):
        miss.append("virtual environments")
    if junk or miss:
        return _item(
            "gitignore",
            "amber",
            "; ".join(
                ([f".gitignore misses {', '.join(miss)}"] if miss else [])
                + ([f"build artefacts in the repository: {', '.join(junk[:4])}"] if junk else [])
            ),
            "remove the artefacts and extend .gitignore",
        )
    return _item("gitignore", "green", ".gitignore covers caches and environments")


README_SECTIONS = {
    "install": ("install", "setup", "set up", "getting started", "quick start", "quickstart"),
    "configure": ("config", "setting", "environment", "option"),
    "run": ("run", "usage", "start", "using", "operat", "daily", "how to use"),
    "upgrade": ("upgrad", "migrat", "updat"),
    "backup and restore": ("backup", "back up", "restore"),
    "troubleshooting": ("troubleshoot", "faq", "problem", "debug", "common issues"),
    "limitations": (
        "limitation",
        "known issue",
        "not supported",
        "out of scope",
        "caveat",
        "not in v1",
        "non-goal",
        "what it doesn't",
        "what it does not",
    ),
}


def readme_sections(text: str) -> dict[str, bool]:
    heads = [h.lower() for h in re.findall(r"^#{1,6}\s+(.+)$", text, re.M)]
    return {k: any(w in h for h in heads for w in words) for k, words in README_SECTIONS.items()}


def check_readme(root: Path) -> dict:
    ps = [p for p in sorted(root.glob("README*")) if p.is_file()]
    if not ps:
        return _item(
            "readme",
            "red",
            "no README",
            "write a README: install, configure, run, "
            "upgrade, backup and restore, troubleshooting, limitations",
        )
    text = "\n".join(p.read_text(errors="replace") for p in ps)
    have = readme_sections(text)
    miss = [k for k, v in have.items() if not v]
    from . import jargon

    hits = jargon.scan_text(text, jargon.patterns({}))
    why = []
    if miss:
        why.append(f"missing sections: {', '.join(miss)}")
    if hits:
        why.append(
            f"{len(hits)} line(s) of build-process jargon or stale references "
            f'(e.g. "{hits[0][2][:80]}")'
        )
    status = "green" if not why else "red" if len(miss) >= 3 else "amber"
    return _item(
        "readme",
        status,
        "; ".join(why) or "every section is there",
        ("add " + ", ".join(miss) + " sections" if miss else "")
        + ("; " if miss and hits else "")
        + ("rewrite the jargon in plain words" if hits else ""),
    )


def deterministic(root: Path, run=None) -> dict[str, dict]:
    """The checks the harness does itself on ``root``; with ``run`` also ``adopted`` (from
    its git history)."""
    root = Path(root)
    out = {
        c["id"]: c
        for c in (check_ci(root), check_lockfile(root), check_gitignore(root), check_readme(root))
    }
    if run is not None:
        out["adopted"] = check_adopted(run, root)
    return out


def check_adopted(run, root: Path | None = None) -> dict:
    """``exam.adopt`` (EX-PLAN §4.3): every finding a ``fix(<id>)`` commit names comes with a
    test of the product's own (``exam.adoption``, from git). Amber with the findings'
    titles when one doesn't; the readiness repair is asked for the missing tests."""
    from . import exam

    ws = Path(root) if root is not None and (Path(root) / ".git").exists() else run.ws
    a = exam.adoption(run, ws)
    if a["mode"] == "off":
        return _item("adopted", "n/a", "not checked (exam.adopt: off)", fixable=False)
    if not a["fixed"]:
        return _item("adopted", "n/a", "no repair changed the code", fixable=False)
    if not a["missing"]:
        return _item(
            "adopted",
            "green",
            f"each of the {len(a['fixed'])} fix(es) comes with a test of the product's own",
        )

    def title(i):
        r = run.store.rules.get(i)
        s = next((x for x in getattr(run, "settings", []) or [] if x.get("id") == i), None)
        return r.title if r is not None else f"the setting {s['key']}" if s else i

    names = "; ".join(f"{i} ({title(i)})" for i in a["missing"][:6])
    return _item(
        "adopted",
        "amber",
        f"{len(a['missing'])} of {len(a['fixed'])} fix(es) came "
        f"without a test of the product's own: {names}",
        f"add a test of your own for each, in your own suite, naming it in the commit "
        f"(fix(<id>): ...): {names}",
    )


# -- the sessions' reports ------------------------------------------------------------------
def load_json(p: Path) -> dict:
    try:
        obj = json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def norm_severity(s) -> str:
    s = str(s or "").strip().lower()
    return (
        s
        if s in SEVERITIES
        else {
            "crit": "critical",
            "info": "low",
            "informational": "low",
            "moderate": "medium",
            "med": "medium",
        }.get(s, "medium")
    )


def _dicts(x) -> list[dict]:
    return [d for d in x or [] if isinstance(d, dict)] if isinstance(x, list) else []


def norm_security(obj: dict) -> dict:
    tm = obj.get("threat_model") if isinstance(obj.get("threat_model"), dict) else {}
    found = []
    for n, f in enumerate(_dicts(obj.get("findings")), 1):
        found.append(
            {
                "ref": str(f.get("id") or f"s{n}"),
                "title": str(f.get("title") or "").strip()[:200] or "(untitled)",
                "severity": norm_severity(f.get("severity")),
                "category": str(f.get("category") or ""),
                "where": f.get("where"),
                "repro": f.get("repro"),
                "observed": f.get("observed"),
                "expected": f.get("expected"),
                "probe": f.get("probe"),
                "no_probe": f.get("no_probe"),
            }
        )
    found.sort(key=lambda f: SEVERITIES.index(f["severity"]))
    for n, f in enumerate(found, 1):
        f["id"] = f"S{n}"
    audit = obj.get("dependency_audit") or obj.get("audit")
    return {
        "threat_model": {
            "summary": str(tm.get("summary") or ""),
            "entry_points": _dicts(tm.get("entry_points")),
            "assets": _dicts(tm.get("assets")),
        },
        "checks": _dicts(obj.get("checks")),
        "findings": found,
        "audit": audit if isinstance(audit, dict) else {},
        "limits": [str(x) for x in obj.get("limits") or [] if x]
        if isinstance(obj.get("limits"), list)
        else [],
    }


def norm_checks(obj: dict) -> dict:
    items = {}
    for c in _dicts(obj.get("checks")):
        cid = str(c.get("id") or "").strip()
        if cid not in CHECKS or CHECKS[cid][1] != "session":
            continue
        st = str(c.get("status") or "").strip().lower()
        st = {"na": "n/a", "n.a.": "n/a", "not applicable": "n/a", "yellow": "amber"}.get(st, st)
        # EX-PLAN §5.2: a step that needs what the sandbox lacks (systemd, Docker, root,
        # the internet) is not verifiable here: never red
        nv = st.replace(" ", "_").replace("-", "_") in ("not_verifiable", "unverifiable")
        st = "n/a" if nv else st if st in STATUS else "amber"
        why = str(c.get("why") or c.get("evidence") or "")[:400]
        items[cid] = {
            "id": cid,
            "title": CHECKS[cid][0],
            "status": st,
            "why": (f"not verifiable here: {why}" if nv else why),
            **({"not_verifiable": True} if nv else {}),
            "evidence": str(c.get("evidence") or "")[:1500],
            "next": str(c.get("next") or "") if st != "green" else "",
            "fixable": bool(c.get("fixable", True)) and st in ("red", "amber"),
            "probe": c.get("probe"),
            "source": "session",
        }
    run = obj.get("run") if isinstance(obj.get("run"), dict) else None
    return {
        "service": obj.get("service") if isinstance(obj.get("service"), bool) else None,
        "install": obj.get("install") if isinstance(obj.get("install"), dict) else {},
        "run": run if run and run.get("start") else None,
        "items": items,
    }


# -- the sessions ---------------------------------------------------------------------------
def prepare(run, name: str, purpose: str, stage: str = "readiness") -> tuple[Path, Path]:
    """For one session: a copy of the build (bound at /work/product) and its exam
    workspace (bound read-write at /work/exam, EX-PLAN §3.1) with the verifier's helpers at
    ``_verify/`` (probes reuse its driver), the spec, and an empty ``_verify/scenarios/``.
    A resumed step whose session already ran keeps what the session worked in. Returns
    ``(product copy, exam workspace)``."""
    ws = run.root / "ready" / name
    ex = run.root / "ready" / f"{name}-exam"
    if ws.is_dir() and ex.is_dir() and run.finished_session(stage, purpose) is not None:
        return ws, ex
    ws = verify.copy_repo(run.ws, ws)
    shutil.rmtree(ws / verify.VERIFY_DIR, ignore_errors=True)
    v = ex / verify.VERIFY_DIR
    if v.exists():
        shutil.rmtree(v)
    ex.mkdir(parents=True, exist_ok=True)
    if run.helpers is not None and Path(run.helpers).is_dir():
        links.copytree(run.helpers, v)
    (v / "scenarios").mkdir(parents=True, exist_ok=True)
    (v / "__init__.py").touch()
    (v / "spec.md").write_text(run.spec)
    return ws, ex


IMPORTS = re.compile(r"^\s*(?:from\s+_verify\.([\w.]+)\s+import|import\s+_verify\.([\w.]+))", re.M)


def needed_modules(src: Path, codes: list[str]) -> set[str]:
    """The ``_verify`` modules ``codes`` import, and the ones those import in turn."""
    todo = [m for c in codes for m in (a or b for a, b in IMPORTS.findall(c))]
    seen: set[str] = set()
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        p = Path(src) / (m.replace(".", "/") + ".py")
        if p.is_file():
            todo += [x for x in (a or b for a, b in IMPORTS.findall(p.read_text(errors="replace")))]
    return seen


def merge_helpers(run, ws: Path, codes: list[str]) -> None:
    """Helper modules a session added to ``_verify/`` that its probes (``codes``) import
    join the helpers every scenario run overlays; the session's other scripts don't (they
    would ship in the acceptance suite). Existing helpers are kept as they are: probes must
    use them unchanged."""
    src = Path(ws) / verify.VERIFY_DIR
    if run.helpers is None or not Path(run.helpers).is_dir():
        run.helpers = run.root / "helpers"
        run.helpers.mkdir(parents=True, exist_ok=True)
        (run.helpers / "__init__.py").touch()
    for m in needed_modules(src, codes):
        rel = Path(m.replace(".", "/") + ".py")
        p, dest = src / rel, Path(run.helpers) / rel
        if p.is_file() and not p.is_symlink() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            links.copy_file(p, dest)


def _probe_name(p, prefix: str) -> str | None:
    if not p:
        return None
    name = Path(str(p)).name
    return name if name.startswith("test_") and name.endswith(".py") else None


def import_probes(run, ws: Path, wanted: dict[str, dict], kind: str) -> dict[str, str]:
    """The session's probes (``_verify/scenarios/test_*.py``) into the store, validated by
    the harness's own two runs on the build. ``wanted``: probe file name -> findings.json-like
    fields (``status`` fail for a finding); other test files are imported as passing checks
    (regression guards). File names get the ``sec_``/``ready_`` prefix so they never take a
    verifier scenario's id. Returns ``{file name: rule id}`` for the probes that are active."""
    sdir = Path(ws) / verify.VERIFY_DIR / "scenarios"
    pre = "sec_" if kind == "security" else "ready_"
    entries = []  # (the file name the session gave, entry)
    for p in sorted(sdir.glob("test_*.py")) if sdir.is_dir() else []:
        orig, stem = p.name, p.stem[len("test_") :]
        if not stem.startswith(pre):
            stem = pre + stem
            p = p.rename(p.with_name(f"test_{stem}.py"))
        e = dict(wanted.get(orig) or {})
        e.update(
            id=stem,
            file=f"{verify.VERIFY_DIR}/scenarios/{p.name}",
            claim=kind,
            status=e.get("status") or "pass",
        )
        entries.append((orig, e))
    if not entries:
        return {}
    merge_helpers(run, ws, [(Path(ws) / e["file"]).read_text(errors="replace") for _, e in entries])
    verify.import_scenarios(
        run.store, ws, {"scenarios": [e for _, e in entries]}, run.claims, by=kind
    )
    new = run.store.select("candidate")
    res = verify.validate(run.store, new, run.spec, run.runner(run.ws))
    for i, v in res["results"].items():
        run.status[i] = v.get("status")
    by_scen = {
        r.origin.get("scenario"): r
        for r in run.store.rules.values()
        if r.origin.get("mode") == "v5"
    }
    out = {}
    for orig, e in entries:
        r = by_scen.get(e["id"])
        if r is None:
            continue
        r.origin.update(
            basis="owner_rule"
            if e.get("check") in SAFE_DEFAULTS
            else "reliability"
            if e.get("check") in RELIABILITY
            else kind,
            ready=kind,
            **{k: e[k] for k in ("severity", "finding", "check") if e.get(k)},
        )
        if r.status == "active":
            out[orig] = r.id
    run.store.save()
    return out


def security(run) -> None:
    """Step 1: the security review."""
    rd = run.ready
    c = cfg(run, "security")
    product, ws = prepare(run, "security", "security")
    text = prompt(run.policy, "security", spec=run.spec.strip())
    try:
        run.session(
            "security",
            text,
            product,
            "readiness",
            c,
            "security",
            caller="security",
            exam_mount=(ws, False),
        )
    except BudgetExceeded as e:
        rd["security"] = {"ran": False, "why": str(e)}
        raise
    finally:
        if rd["security"].get("why") is None:
            ingest_security(run, ws)


def ingest_security(run, ws: Path, step: str = "review") -> dict:
    """What the security session left in its exam workspace ``ws`` (``security.json`` and
    its probes): the probes into the store (the harness's two runs confirm them), each
    finding's status (``confirm``), ``run.ready["security"]`` and the ``security`` event."""
    rd = run.ready
    sec = norm_security(load_json(ws / verify.VERIFY_DIR / "security.json"))
    wanted = {}
    for f in sec["findings"]:
        name = _probe_name(f.get("probe"), "sec_")
        if name:
            wanted[name] = {
                "status": "fail",
                "class": "security",
                "repro": f["repro"],
                "observed": f["observed"],
                "expected": f["expected"],
                "suspect": f["where"],
                "severity": f["severity"],
                "finding": f["id"],
            }
    probes = import_probes(run, ws, wanted, "security")
    for f in sec["findings"]:
        name = _probe_name(f.get("probe"), "sec_")
        f["rule"] = probes.get(name) if name else None
    sec["ran"] = (ws / verify.VERIFY_DIR / "security.json").exists()
    sec["guards"] = sorted(set(probes.values()) - {f["rule"] for f in sec["findings"]})
    rd["security"] = sec
    confirm(run)
    save(run)
    counts = {s: sum(1 for f in sec["findings"] if f["severity"] == s) for s in SEVERITIES}
    run.emit(
        "security",
        step=step,
        ran=sec["ran"],
        **counts,
        confirmed=sum(1 for f in sec["findings"] if f.get("status") == "open"),
        unconfirmed=sum(1 for f in sec["findings"] if f.get("status") == "unconfirmed"),
        probes=sum(1 for f in sec["findings"] if f.get("rule")),
        audit=(sec.get("audit") or {}).get("status"),
    )
    return sec


def confirm(run) -> None:
    """Each finding's status before the repair: ``open`` (its probe fails in the harness's
    run, or it has no probe and was reproduced by hand), ``unconfirmed`` (its probe passes in
    the harness's run: not reproduced there)."""
    for f in run.ready["security"].get("findings") or []:
        rid = f.get("rule")
        if rid and rid in run.store.rules and run.store.rules[rid].status == "active":
            f["status"] = "unconfirmed" if run.status.get(rid) == "passed" else "open"
            f["confirmed_by"] = "test"
        else:
            f["rule"] = None
            f["status"] = "open" if f.get("repro") else "unconfirmed"
            f["confirmed_by"] = "hand" if f.get("repro") else None


def checks(run) -> None:
    """Step 2: the operational checks (a session, by doing them) and the deterministic ones
    (the harness, on the build)."""
    rd = run.ready
    c = cfg(run, "checks")
    product, ws = prepare(run, "checks", "readiness-checks")
    items_md = "\n".join(f"- `{k}`: {t}" for k, (t, how) in CHECKS.items() if how == "session")
    text = prompt(run.policy, "readiness", spec=run.spec.strip(), checks=items_md)
    rec = {"ran": False, "items": {}}
    try:
        run.session(
            "readiness",
            text,
            product,
            "readiness",
            c,
            "readiness-checks",
            caller="readiness",
            exam_mount=(ws, False),
        )
    except BudgetExceeded as e:
        rec["why"] = str(e)
        raise
    finally:
        ingest_checks(run, ws, rec)


def ingest_checks(run, ws: Path, rec: dict | None = None) -> dict:
    """What the checks session left in its exam workspace ``ws`` (``readiness.json`` and
    its probes), plus the harness's deterministic items, as ``run.ready["checks"]``; the
    ``readiness`` event. ``rec``: the step's record so far (``why`` when it stopped)."""
    rd = run.ready
    rec = rec if rec is not None else {"ran": False, "items": {}}
    got = norm_checks(load_json(ws / verify.VERIFY_DIR / "readiness.json"))
    wanted = {}
    for cid, it in got["items"].items():
        name = _probe_name(it.get("probe"), "ready_")
        if name and it["status"] in ("red", "amber"):
            wanted[name] = {
                "status": "fail",
                "class": "readiness",
                "check": cid,
                "observed": it["why"],
                "expected": CHECKS[cid][0],
            }
    probes = import_probes(run, ws, wanted, "readiness") if got["items"] else {}
    for cid, it in got["items"].items():
        name = _probe_name(it.get("probe"), "ready_")
        rid = probes.get(name) if name else None
        it["rule"] = rid if rid and run.status.get(rid) != "passed" else None
    rec.update(got, ran=(ws / verify.VERIFY_DIR / "readiness.json").exists())
    for cid, (title, how) in CHECKS.items():
        if how == "session" and cid not in rec["items"]:
            rec["items"][cid] = {
                "id": cid,
                "title": title,
                "status": "amber",
                "why": "not checked" + (f" ({rec['why']})" if rec.get("why") else ""),
                "next": "check it by hand before production",
                "fixable": False,
                "source": "session",
            }
    rec["items"].update(deterministic(run.ws, run))
    rd["checks"] = rec
    save(run)
    run.emit("readiness", step="checks", **scorecard_counts(rec["items"].values()))
    return rec


# -- the README's commands, run as written (commands.py) ------------------------------------
def run_commands(
    run,
    src: Path,
    phases=("quick", "restart", "tests"),
    deliver: bool = False,
    rec: dict | None = None,
):
    """The README's commands of ``src`` (the tree as it will be delivered: nothing of repo-factory's
    is in it, EX-PLAN §4.2). The quick start and the restart run in ``commands.run``'s
    sandbox, cached by the tree's content and the phases, in memory and on disk
    (``ready/cmdcache/``: a step redone after an interruption doesn't run them again). The
    test commands are the exam record's ``own`` section (``exam.run(readme=True)``, the
    flake protocol on): the same run every other consumer reads, never a second one.
    ``deliver`` is kept for callers; the tree is used as given either way. ``rec``: an exam
    record of this tree that already ran the test commands (delivery's)."""
    from . import commands

    c = cfg(run, "commands")
    tree = Path(src)
    run = getattr(run, "_run", run)
    want = tuple(p for p in phases if p != "tests")
    key = (commands.tree_key(tree), want)
    cache = run.__dict__.setdefault("_cmd_runs", {})
    disk = run.root / "ready" / "cmdcache" / f"{key[0]}-{'-'.join(want)}.json"
    if key not in cache and disk.is_file():
        cache[key] = load_json(disk) or None
        if cache[key] is None:
            del cache[key]
    if key not in cache:
        try:
            venv = run.venv
        except Exception as e:  # noqa: BLE001  no venv: a build cut off
            cache[key] = {
                "ran": False,
                "why": f"no environment to run them in ({e})",
                "phases": list(phases),
                "extract": commands._brief(commands.extract(tree)),
                "quick": [],
            }
        else:
            # the build backends an install script's pip or pipx builds with (the exam's,
            # cached per den): the check has no network
            from . import exam, pyinstall

            wheels = pyinstall.install(run, tree, exam.settings(run)).get("wheels")
            cache[key] = commands.run(
                tree,
                venv,
                want,
                (run.ready.get("checks") or {}).get("run"),
                {k: v for k, v in c.items() if k in commands.TIMEOUTS},
                wheels=wheels,
            )
            if cache[key].get("ran"):
                disk.parent.mkdir(parents=True, exist_ok=True)
                disk.write_text(json.dumps(cache[key], default=str))
    res = dict(cache[key])
    if "tests" in phases and res.get("ran"):
        from . import exam

        if rec is None or rec.get("own") is None:  # the delivered tree's: already run
            rec = exam.run(run, tree, rules=[], readme=True, flake=True)
        own = rec.get("own") or {"commands": [], "green": False, "ran": False}
        res.update(
            tests=[
                {
                    k: r.get(k)
                    for k in (
                        "cmd",
                        "where",
                        "kind",
                        "status",
                        "rc",
                        "output",
                        "wall_s",
                        "why",
                        "runs",
                        "verdict",
                    )
                }
                for r in own["commands"]
            ],
            own=own,
            exam=rec.get("key"),
            phases=list(phases),
        )
    return res


def command_items(run, res: dict, *, tests=True, restart=True) -> dict[str, dict]:
    from . import commands

    return commands.items(
        res, (run.ready.get("checks") or {}).get("service"), tests=tests, restart=restart
    )


def commands_brief(res: dict) -> dict:
    """What ``readiness.json`` keeps of a run: the commands and their statuses."""

    def rows(rs):
        return [
            {
                k: r.get(k)
                for k in (
                    "cmd",
                    "where",
                    "kind",
                    "status",
                    "rc",
                    "wall_s",
                    "ports",
                    "why",
                    "runs",
                    "verdict",
                )
            }
            for r in rs or []
        ]

    rs = res.get("restart") or {}
    return {
        "ran": res.get("ran"),
        "why": res.get("why"),
        "extract": res.get("extract"),
        "quick": rows(res.get("quick")),
        "tests": rows(res.get("tests")),
        "restart": {k: rs.get(k) for k in ("ran", "port", "graceful", "why")}
        | {
            k: {x: (rs.get(k) or {}).get(x) for x in ("ok", "why", "serve_s", "started_after_s")}
            for k in ("restart", "crash")
            if rs.get(k)
        }
        | ({"start": (rs.get("recipe") or {}).get("start")} if rs.get("recipe") else {}),
    }


def commands_step(run) -> None:
    """Step 3: the README's commands, run as written on a fresh copy of the tree as it will
    be delivered (``commands.py``): the quick start, the immediate restart (services) and
    every documented test command. Their items join the checks."""
    rd = run.ready
    res = run_commands(run, run.ws)
    its = command_items(run, res)
    rd["commands"] = commands_brief(res)
    rd["checks"].setdefault("items", {}).update(its)
    save(run)
    run.emit(
        "readiness",
        step="commands",
        ran=bool(res.get("ran")),
        **{k: its[k]["status"] for k in its},
        **scorecard_counts(rd["checks"]["items"].values()),
    )


def recheck(run, ws: Path, refs: set[str]) -> dict[str, dict]:
    """The command items ``refs`` again, on the repaired ``ws`` (cached per tree)."""
    phases = (
        ("quick",)
        + (("restart",) if "restart_now" in refs else ())
        + (("tests",) if "tests_green" in refs else ())
    )
    res = run_commands(run, ws, phases)
    return command_items(run, res, tests="tests_green" in refs, restart="restart_now" in refs)


# -- the repair round -----------------------------------------------------------------------
def plan(run) -> list[dict]:
    """The items for the round: open critical and high findings; medium ones when the
    policy says so (``if_budget``: the full round is affordable); fixable red readiness items
    (and amber ones the harness checks itself: cheap, documentation-sized fixes)."""
    rd = run.ready
    full = run.budget.allows(step_usd(run, "repair"))
    med = str(cfg(run, "medium") or "if_budget")
    out = []
    for f in rd["security"].get("findings") or []:
        f["sent"] = False
        if f.get("status") != "open":
            continue
        if f["severity"] in ("critical", "high") or (
            f["severity"] == "medium" and (med == "always" or (med == "if_budget" and full))
        ):
            f["sent"] = True
            out.append(sec_item(run, f))
    ready = []
    for it in (rd["checks"].get("items") or {}).values():
        it["sent"] = False
        if not it.get("fixable"):
            continue
        if it["status"] == "red" or (it["status"] == "amber" and it["id"] in DETERMINISTIC + RUN):
            it["sent"] = True
            ready.append(check_item(run, it))
    # most severe first: the owner's safe defaults (rank 1), reliability (2), the rest (3)
    from .accept import rank

    ready.sort(key=lambda d: rank({"kind": "scenario", "basis": basis(d)}))
    return out + ready


def sec_item(run, f: dict) -> dict:
    rid = f.get("rule")
    r = run.store.rules.get(rid) if rid else None
    return {
        "id": rid or f["id"],
        "class": "security",
        "severity": f["severity"],
        "title": f["title"],
        "ref": f["id"],
        "where": f.get("where"),
        "repro": f.get("repro"),
        "observed": f.get("observed"),
        "expected": f.get("expected"),
        "category": f.get("category"),
        "test_file": r.test_file if r is not None else None,
        "check": r is None,
        "origin": dict(r.origin) if r is not None else {},
    }


def check_item(run, it: dict) -> dict:
    rid = it.get("rule")
    r = run.store.rules.get(rid) if rid else None
    return {
        "id": rid or it["id"],
        "class": "readiness",
        "ref": it["id"],
        "title": it["title"],
        "now": it["status"],
        "why": it.get("why"),
        "next": it.get("next"),
        "test_file": r.test_file if r is not None else None,
        "check": r is None,
        "owner_rule": it["id"] in SAFE_DEFAULTS,
        "detail": it.get("detail") if it["id"] in RUN else None,
        "origin": dict(r.origin) if r is not None else {},
    }


def item_md(d: dict) -> str:
    """One item of the readiness repair brief."""
    if d["class"] == "security":
        L = [f"### {d['id']} (security, {d['severity']}): {d['title']}"]
        for k, label in (("where", "Where"), ("observed", "Observed"), ("expected", "Expected")):
            if d.get(k):
                L.append(f"{label}: {d[k]}")
        if d.get("repro"):
            L.append(f"Reproduce: `{d['repro']}`")
    else:
        tag = (
            ", the safe-defaults rule"
            if d.get("owner_rule")
            else ", reliability"
            if d.get("ref") in RELIABILITY
            else ""
        )
        L = [
            f"### {d['id']} (readiness{tag}): {d['title']}",
            f"Now: {d.get('now')}: {d.get('why') or ''}".rstrip(": "),
        ]
        if d.get("next"):
            L.append(f"Do: {d['next']}")
        if d.get("detail"):
            L.append(
                "What the harness ran, exactly as the README says, on a fresh copy of the "
                "tree as it will be delivered:\n" + str(d["detail"])[:2500]
            )
        if d.get("ref") == "restart_now":
            L.append(
                "Reproduce: start it, make a few requests, stop it (SIGTERM), and start "
                "it again at once; then kill it (SIGKILL) and start it again at once. It "
                "must serve right away both times."
            )
    if d.get("test_file"):
        L.append(
            f"Scenario: `/work/exam/_verify/scenarios/{d['test_file']}` fails now and must pass."
        )
    elif d.get("ref") in RUN:
        L.append(
            "No test: after your commit the harness runs the README's commands again, as "
            "written, on a fresh copy (name the item in the commit)."
        )
    else:
        L.append("No test: the harness re-checks it after your commit (name the item in it).")
    return "\n".join(L)


def brief(run, items: list[dict]) -> str:
    ns = sum(1 for d in items if d["class"] == "security")
    nr = len(items) - ns
    ctx = (
        " and ".join(
            x
            for x in (
                f"{ns} security finding(s)" if ns else "",
                f"{nr} readiness gap(s)" if nr else "",
            )
            if x
        )
        + " below."
    )
    return prompt(
        run.policy,
        "readiness",
        "repair",
        context=ctx,
        spec=run.spec.strip(),
        findings="\n\n".join(item_md(d) for d in items),
    )


def extra_fixed(run, ws: Path, items: list[dict], commits: list[dict]) -> list[str]:
    """Items without a test that the round fixed: a harness-checked item that now passes,
    or another item a commit names (the change was made; no test re-checks it)."""
    named = {x for c in commits for x in c.get("findings") or []}
    det = deterministic(ws, run)
    refs = {
        d["ref"]
        for d in items
        if d.get("check") and d["class"] == "readiness" and d.get("ref") in RUN
    }
    ran = recheck(run, ws, refs) if refs else {}
    out = []
    for d in items:
        if not d.get("check"):
            continue
        if d["class"] == "readiness" and d["ref"] in det:
            if det[d["ref"]]["status"] == "green":
                out.append(d["id"])
        elif d["class"] == "readiness" and d["ref"] in ran:
            if ran[d["ref"]]["status"] == "green":  # the commands, run again as written
                out.append(d["id"])
        elif d["id"] in named or d.get("ref") in named:
            out.append(d["id"])
    return out


def proposals_dir(run) -> Path:
    """Where the readiness repair may propose a scenario's new start-up (bound read-write at
    /work/exam-proposals, EX-PLAN §3.5); the exam itself is read-only for it."""
    return run.root / "ready" / "proposals"


def harvest(run) -> list[str]:
    """After the repair session: scenarios the repairer proposed again in
    ``/work/exam-proposals/`` because they encoded an old unsafe default (how they start the
    product) become new versions in the store, still active, by ``readiness-repair``. The
    probes the round must fix can't be changed. The exam is materialised again after.
    Returns the ids updated."""
    d = proposals_dir(run)
    if not d.is_dir():
        return []
    sent = {x["id"] for x in run.ready.get("sent") or []}
    out = []
    for r in run.store.active("visible"):
        p = d / r.test_file
        if r.id in sent or not p.is_file():
            continue
        code = p.read_text(errors="replace")
        if code.rstrip() == r.code.rstrip() or not code.strip():
            continue
        with run.store.lock:
            r.code, r.version = code.rstrip() + "\n", r.version + 1
            run.store._log(
                id=r.id,
                version=r.version,
                by="readiness-repair",
                reason="updated for the safe defaults",
            )
            run.store.save()
        run.store._changed(r, r.status, r.status, "updated for the safe defaults")
        out.append(r.id)
    run.ready.setdefault("scenarios_updated", []).extend(
        {"id": i, "title": run.store.rules[i].title} for i in out
    )
    shutil.rmtree(d, ignore_errors=True)
    if out:
        from . import exam

        exam.materialise(run)
    return out


def after_round(run, r: dict) -> None:
    """The items' statuses after the round (the harness's run of the kept head)."""
    rd = run.ready
    kept = bool(r.get("accepted"))
    reverted = {
        x for v in (r.get("reverted") or []) + (r.get("dropped") or []) for x in v["findings"]
    }
    extra = set(r.get("extra") or [])
    for f in rd["security"].get("findings") or []:
        if not f.get("sent"):
            continue
        key = f.get("rule") or f["id"]
        if f.get("rule"):
            ok = kept and run.status.get(f["rule"]) == "passed"
            f["status"] = "fixed" if ok else "open"
        else:
            f["status"] = "addressed" if kept and key in extra and key not in reverted else "open"
    det = deterministic(run.ws, run)
    refs = {
        it["id"]
        for it in (rd["checks"].get("items") or {}).values()
        if it.get("sent") and it["id"] in RUN and not it.get("rule")
    }
    ran = recheck(run, run.ws, refs) if refs and kept else {}
    for it in (rd["checks"].get("items") or {}).values():
        if not it.get("sent"):
            continue
        if it["id"] in det:
            it.update({k: det[it["id"]][k] for k in ("status", "why", "next", "fixable")})
        elif it["id"] in ran:
            new, old = ran[it["id"]], it.get("why")
            it.update({k: new.get(k) for k in ("status", "why", "next", "fixable", "detail")})
            if new["status"] == "green":
                it["why"] = f"fixed in this version (run again as written: {new['why']})" + (
                    f"; it was: {old}" if old else ""
                )
        elif it.get("rule"):
            if kept and run.status.get(it["rule"]) == "passed":
                it.update(status="green", why=fixed_why(it.get("why")), next="", fixable=False)
        elif kept and it["id"] in extra and it["id"] not in reverted:
            it.update(
                status="amber",
                why=f"changed in the repair, not re-checked: {it['why']}",
                next="check it by hand",
            )


def step(run) -> bool:
    """One step of the stage (``Run.go``'s plan); True when the stage is over."""
    rd = run.ready
    if "readiness" not in run.rules["stages"]:
        run.emit("v5_stage", stage="readiness", status="skipped", usd=0.0, wall_s=0.0)
        rd["next"] = "done"
        return True
    nxt = rd.get("next") or "security"
    if nxt == "security":
        start = float(cfg(run, "start_usd") or 0.3)
        # money kept for the review since the first session (Budget.keep): the session
        # before may have run a little over its own allowance, as real sessions do
        kept = getattr(run.budget, "keep", {}).get("readiness")
        if not run.budget.allows(start / 2 if kept else start):
            why = (
                f"the build's cap: ${run.budget.spent():.2f} spent of ${run.budget.cap:.2f}, "
                f"the stage needs ${start:.2f} to start"
            )
            rd.update(skipped=why, next="done")
            rd["security"] = {"ran": False, "why": why}
            rd["checks"] = {"ran": False, "why": why, "items": deterministic(run.ws)}
            save(run)
            run.emit("v5_stage", stage="readiness", status="skipped", usd=0.0, wall_s=0.0, why=why)
            return True
        run.budget.window("readiness", step_cap(run, "security"))
        run.stage("readiness", lambda: security(run))
        rd["next"] = "checks"
        return False
    if nxt == "checks":
        run.budget.window("readiness", step_cap(run, "checks"))
        run.stage("readiness", lambda: checks(run))
        rd["next"] = "commands"
        return False
    if nxt == "commands":  # no model call: the harness runs them
        if not rd["checks"].get("items"):
            rd["checks"] = {**rd["checks"], "items": deterministic(run.ws)}
        run.stage("readiness", lambda: commands_step(run))
        rd["next"] = "repair"
        return False
    if nxt == "repair":
        if not rd["checks"].get("items"):
            rd["checks"] = {**rd["checks"], "items": deterministic(run.ws)}
        items = plan(run)
        rd["sent"] = items
        c = cfg(run, "repair")
        if not items:
            rd.update(skipped="nothing to repair", next="done")
            save(run)
            return True
        if "repair" not in run.rules["stages"] or not run.budget.allows(float(c["min_usd"])):
            rd.update(
                skipped=f"the build's cap: ${run.budget.spent():.2f} spent of "
                f"${run.budget.cap:.2f}, a repair round needs "
                f"${float(c['min_usd']):.2f}"
                if "repair" in run.rules["stages"]
                else "repair is off in this policy",
                next="done",
            )
            for x in rd["security"].get("findings") or []:
                x["sent"] = False
            for x in (rd["checks"].get("items") or {}).values():
                x["sent"] = False
            save(run)
            run.emit(
                "readiness", step="repair", status="skipped", why=rd["skipped"], sent=len(items)
            )
            return True
        # The measure before the round, now (the probes changed the scenario set): the
        # session's step then holds only the session (a step must fit a time slice).
        repair.measure_at(ReadyView(run), repair.ensure_repo(run.ws, "v5: before readiness"))
        rd["next"] = "session"
        save(run)
        return False
    if nxt == "session":
        c = cfg(run, "repair")
        run.budget.window("readiness", step_cap(run, "repair"))
        items = rd["sent"]
        part = {}
        run.emit("readiness", step="repair", status="repairing", sent=len(items))
        run.stage("readiness", lambda: part.update(repair.start_round(ReadyView(run), 1, items)))
        if not part:
            rd.update(skipped="the readiness stage hit its cap", next="done")
            save(run)
            return True
        rd.update(partial=part, next="accept")
        return False
    if nxt == "accept":
        r = {}
        run.stage(
            "readiness",
            lambda: r.update(repair.finish_round(ReadyView(run), 1, rd["sent"], rd["partial"])),
        )
        part = rd.pop("partial", None) or {}
        if not r and part.get("base"):  # stopped half way (the cap): undone
            repair.reset(run.ws, part["base"])
            rd["skipped"] = "the acceptance stopped at the build's cap; the round was undone"
        if r:
            now = r.get("now") or r.get("after")
            if now:
                for g in ("scenarios", "held"):
                    run.status.update(now.get(g) or {})
            after_round(run, r)
            rd["round"] = {
                k: r.get(k)
                for k in (
                    "sent",
                    "fixed",
                    "regressions",
                    "pairs",
                    "reverted",
                    "dropped",
                    "tolerated",
                    "accepted",
                    "why",
                    "extra",
                    "usd",
                )
            }
            run.emit(
                "readiness",
                step="repair",
                status="done",
                sent=len(rd["sent"]),
                fixed=len(r.get("fixed") or []) + len(r.get("extra") or []),
                regressions=len(r.get("regressions") or []),
                accepted=bool(r.get("accepted")),
                why=r.get("why"),
                updated=len(rd.get("scenarios_updated") or []),
            )
            if r.get("accepted"):
                run.delivered = run.candidates
        rd["next"] = "done"
        save(run)
        return True
    return True


class ReadyView:
    """``repair.run_round``'s context for the readiness round: the run, with the session in
    the ``readiness`` stage (its own caps), the readiness brief, every visible scenario
    exported (so the repairer can run them after changing a default), the scenario updates
    harvested before acceptance, and ``extra_fixed`` for items without a test."""

    def __init__(self, run):
        self.__dict__["_run"] = run

    def __getattr__(self, name):
        run = self._run
        if name == "session":

            def session(text, purpose, stage):
                from . import exam

                c = cfg(run, "repair")
                if purpose.startswith("repair"):
                    run.repair_session = None  # fix-ups resume this round's session
                    shutil.rmtree(proposals_dir(run), ignore_errors=True)
                run._exam_mount = (exam.visible(run), True, proposals_dir(run))
                try:
                    res = run.session_for_repair(
                        text,
                        purpose,
                        "readiness",
                        caps=c,
                        prefix="ready-",
                        fresh=not c.get("resume"),
                    )
                finally:
                    run._exam_mount = None
                if purpose.startswith("repair"):
                    harvest(run)
                return res

            return session
        if name == "brief":
            return lambda items: brief(run, items)
        if name == "export":
            from . import exam

            return lambda ids: exam.visible(run) and []
        if name == "extra_fixed":
            return lambda ws, items, commits: extra_fixed(run, ws, items, commits)
        return getattr(run, name)

    def __setattr__(self, name, value):
        setattr(self._run, name, value)


def item_for(run, i: str) -> dict | None:
    """``Run.item`` for a readiness item without a test (the conflict protocol's prose)."""
    for d in run.ready.get("sent") or []:
        if d["id"] == i or d.get("ref") == i:
            return {
                "kind": "scenario",
                "basis": basis(d),
                "prose": f"{d['title']} "
                f"({'security, ' + d['severity'] if d['class'] == 'security' else 'readiness'})",
            }
    return None


def basis(d: dict) -> str:
    if d["class"] == "security":
        return "security"
    if d.get("owner_rule"):
        return "owner_rule"
    return "reliability" if d.get("ref") in RELIABILITY else "readiness"


# -- delivery -------------------------------------------------------------------------------
def passes_on(run, rec: dict):
    """``passes(rule id)`` on the delivered tree: the exam's record of it says so, else the
    harness's last run."""
    scen = (rec or {}).get("scenarios") or {}

    def passes(rid):
        if rid in scen:
            return scen[rid].get("status") == "passed"
        return run.status.get(rid) == "passed"

    return passes


def settle(run, rec: dict) -> None:
    """Before the security report: a finding that wasn't repaired but whose test passes on
    the delivered tree is ``passes``, not ``fixed``: another item's fix may have fixed it (a
    localhost default closes a remote first-run takeover), or voided the test's premise (an
    empty start removed the demo token the test posted to: the real check's webhooks S6).
    The report asks for a check by hand."""
    passes = passes_on(run, rec)
    for f in run.ready["security"].get("findings") or []:
        if f.get("rule") and f.get("status") in ("open", "addressed") and passes(f["rule"]):
            f["status"] = "passes"


def finalize(run, art: Path, rec: dict, docs_hits: list[dict]) -> dict:
    """The scorecard of the delivered tree, from its exam record (``exam.final``): the
    harness's checks again, the probes' status on the delivered head, the README's test
    commands as written (``tests_green``: the product's own tests in its own runner; repo-factory's
    scenarios are never part of it), the provenance scan (``docs_plain``), the adoption of
    the fixes, the intermittent items and what could not be verified here."""
    from . import exam

    rd = run.ready
    passes = passes_on(run, rec)
    items = {k: dict(v) for k, v in (rd["checks"].get("items") or {}).items()}
    items.update(deterministic(art, run))
    for it in items.values():
        rid = it.get("rule")
        if rid and passes(rid) and it["status"] != "green":
            it.update(status="green", why=fixed_why(it.get("why")), next="", fixable=False)
    res = run_commands(run, art, ("quick", "tests"), deliver=True, rec=rec)
    got = command_items(run, res, restart=False)
    rd["commands_final"] = commands_brief(res)
    items["commands"] = got["commands"]
    tg = got.get("tests_green") or _item(
        "tests_green",
        "amber",
        rec.get("why") or "not run",
        "run the README's test commands by hand",
        fixable=False,
    )
    items["tests_green"] = {**tg, "source": "delivery"}
    inter = exam.intermittent(rec)
    if inter:
        items["intermittent"] = _item(
            "intermittent",
            "amber",
            "; ".join(
                (f"`{x['cmd']}`" if x["kind"] == "command" else f'"{_title(run, x["id"])}"')
                + f" failed {_rate(x)}"
                for x in inter[:6]
            ),
            "look at these before production: each failed some runs, fewer than "
            f"{exam.settings(run)['intermittent_hold_min']} of 5",
            fixable=False,
            source="delivery",
        )
    nv = not_verifiable(res, items)
    if nv:
        items["verifiable"] = _item("verifiable", "n/a", nv, fixable=False, source="delivery")
    items["docs_plain"] = _item(
        "docs_plain",
        "green" if not docs_hits else "amber",
        "no build-process words in the README, AGENTS.md, SCOPE.md, SECURITY.md or docs/"
        if not docs_hits
        else f"{len(docs_hits)} line(s) of build-process words: "
        + "; ".join(f"{h['file']}:{h['line']}" for h in docs_hits[:6]),
        "rewrite those lines in plain words",
        source="delivery",
    )
    order = {k: n for n, k in enumerate(CHECKS)}
    rd["final"] = {
        "items": sorted(items.values(), key=lambda x: order.get(x["id"], 99)),
        "counts": scorecard_counts(items.values()),
        "exam": rec.get("key"),
    }
    save(run)
    run.emit(
        "readiness",
        step="final",
        **rd["final"]["counts"],
        security={
            s: sum(
                1
                for f in rd["security"].get("findings") or []
                if f["severity"] == s and f.get("status") != "fixed"
            )
            for s in SEVERITIES
        },
    )
    return rd["final"]


def _title(run, rid: str) -> str:
    r = run.store.rules.get(rid)
    from .acceptance import rule_title

    return rule_title(r).rstrip(".") if r is not None else rid


def _rate(x: dict) -> str:
    runs = x.get("runs") or []
    fails = sum(1 for s in runs if s not in ("passed", "ok"))
    return f"{fails} of {len(runs)} runs" if runs else f"at a rate of {x.get('rate')}"


def not_verifiable(res: dict, items: dict) -> str:
    """The README steps and checks the sandbox can't run, by what they need (EX-PLAN §5.2):
    "needs systemd: 2 commands" ...; empty when there are none."""
    ex = res.get("extract") or {}
    needs: dict[str, int] = {}
    from .commands import NEEDS

    for e in (
        (ex.get("quick") or [])
        + (ex.get("tests") or [])
        + (ex.get("skipped") or [])
        + (ex.get("not_verifiable") or [])
    ):
        why = e.get("skip")
        if why in NEEDS:
            needs[why] = needs.get(why, 0) + 1
    out = [f"{k}: {n} command(s)" for k, n in sorted(needs.items())]
    out += [
        f"{it['title']}: {it['why'][len('not verifiable here: ') :] or 'not verifiable'}"
        for it in items.values()
        if it.get("not_verifiable")
    ]
    return "; ".join(out)


def fixed_why(why) -> str:
    """A fixed item's line: what it was, said as the past."""
    why = str(why or "").strip()
    if why.startswith("fixed in this version"):
        return why
    return "fixed in this version (its test passes)" + (f"; it was: {why}" if why else "")


def scorecard_counts(items) -> dict:
    items = list(items)
    return {s.replace("/", ""): sum(1 for i in items if i["status"] == s) for s in STATUS}


def save(run) -> None:
    (run.root / "readiness.json").write_text(json.dumps(run.ready, indent=1, default=str))


# -- SECURITY.md and READINESS.md --------------------------------------------------------------
def _cell(x) -> str:
    return str(x if x is not None else "").replace("|", "\\|").replace("\n", " ").strip()


FINDING_REF = re.compile(r"\b(findings?)\s+([a-z]{1,2}\d+)\b", re.I)
VERIFY_PATH = re.compile(r"(?:\bprobe\s+)?`?\b_verify/[\w./-]+`?")


def plain(text, refs: dict[str, str] | None = None) -> str:
    """The review session's free text in plain words for SECURITY.md: its own finding refs
    (``finding s1``) become the delivered ids (``S1, below``) and its workspace paths
    (``_verify/scenarios/test_sec_1.py``) "an automated test" (``acceptance.scan`` flags both
    as process jargon)."""
    t = str(text if text is not None else "")
    refs = {k.lower(): v for k, v in (refs or {}).items()}

    def ref(m):
        rid = refs.get(m.group(2).lower())
        return f"{rid}, below" if rid else "see Findings"

    t = FINDING_REF.sub(ref, t)
    t = VERIFY_PATH.sub(
        lambda m: (
            "checked by an automated test"
            if m.group(0).lower().startswith("probe")
            else "an automated test"
        ),
        t,
    )
    return acceptance.ID_ASIDE.sub("", t)


STATUS_WORDS = {
    "open": "open",
    "fixed": "fixed (a test checks it)",
    "addressed": "changed (no automated test)",
    "unconfirmed": "not reproduced by an automated test",
    "passes": "its test passes after other fixes; confirm by hand",
    None: "open",
}


def render_security(product: str, rd: dict, acc_files: dict[str, str]) -> str:
    sec = rd.get("security") or {}
    L = [
        "# Security",
        "",
        f"How {product or 'this product'} was reviewed for security before its first "
        "release, what was found, and what is still open.",
        "",
    ]
    if not sec.get("ran"):
        L += [
            "**This version has not had a security review**"
            + (f" ({sec['why']})." if sec.get("why") else "."),
            "",
            "Treat it as unreviewed: run it only on a trusted network until it is reviewed.",
            "",
        ]
        return "\n".join(L + report_md())
    refs = {
        str(f.get("ref")): str(f.get("id"))
        for f in sec.get("findings") or []
        if f.get("ref") and f.get("id")
    }

    def c(x):  # a cell of the session's own words
        return _cell(plain(x, refs))

    tm = sec.get("threat_model") or {}
    L += ["## Threat model", ""]
    if tm.get("summary"):
        L += [plain(tm["summary"].strip(), refs), ""]
    if tm.get("entry_points"):
        L += [
            "### Entry points",
            "",
            "| entry point | who can reach it | authentication | notes |",
            "|---|---|---|---|",
        ]
        L += [
            f"| {c(e.get('name'))} | {c(e.get('reach'))} | {c(e.get('auth'))} | "
            f"{c(e.get('notes'))} |"
            for e in tm["entry_points"]
        ] + [""]
    if tm.get("assets"):
        L += ["### Assets", "", "| asset | kind | where |", "|---|---|---|"]
        L += [
            f"| {c(a.get('name'))} | {c(a.get('kind'))} | {c(a.get('where'))} |"
            for a in tm["assets"]
        ] + [""]
    if sec.get("checks"):
        L += [
            "## What was checked, and how",
            "",
            "The product was started as its README says and probed while running.",
            "",
            "| area | check | result | evidence |",
            "|---|---|---|---|",
        ]
        L += [
            f"| {c(k.get('area'))} | {c(k.get('what'))} | {c(k.get('result'))} | "
            f"{c(str(k.get('evidence') or '')[:300])} |"
            for k in sec["checks"]
        ] + [""]
    found = sec.get("findings") or []
    L += ["## Findings", ""]
    if not found:
        L += ["None.", ""]
    else:
        L += ["| | severity | finding | status |", "|---|---|---|---|"]
        L += [
            f"| {f['id']} | {f['severity']} | {c(f['title'])} | "
            f"{STATUS_WORDS.get(f.get('status'), f.get('status'))} |"
            for f in found
        ] + [""]
        for f in found:
            if f.get("status") == "fixed":
                continue
            L += [f"### {f['id']} ({f['severity']}): {plain(f['title'], refs)}", ""]
            for k, label in (
                ("where", "Where"),
                ("observed", "What happens"),
                ("expected", "What should happen"),
            ):
                if f.get(k):
                    L.append(f"- {label}: {c(f[k])}")
            if f.get("repro"):
                L.append(f"- Reproduce: `{c(f['repro'])}`")
            rid = f.get("rule")
            if rid and acc_files.get(rid):
                L.append(
                    {
                        "unconfirmed": f"- Check: `{acc_files[rid]}` passes in an isolated "
                        "sandbox; the problem was seen by hand only",
                        "passes": f"- Check: `{acc_files[rid]}` passes since other fixes "
                        "went in, but this finding wasn't repaired on its own: "
                        "a changed default can also void the test's premise. "
                        "Reproduce it by hand before closing it.",
                    }.get(
                        f.get("status"),
                        f"- Check: `{acc_files[rid]}` (still fails on this version)",
                    )
                )
            L.append("")
    au = sec.get("audit") or {}
    L += ["## Dependency audit", ""]
    if au:
        L += [f"- Tool: {_cell(au.get('tool') or '-')}; result: {_cell(au.get('status') or '-')}."]
        if au.get("output"):
            L.append(f"- {_cell(str(au['output'])[:500])}")
        L.append("")
    else:
        L += ["Not run.", ""]
    L += [
        "## Known limits",
        "",
        "- This was a time-boxed review of v1 by one reviewer with the code and a running "
        "copy: scripted probes and manual checks, not a penetration test or an external "
        "audit.",
    ]
    L += [f"- {plain(x)}" for x in sec.get("limits") or []]
    L += [""]
    return "\n".join(L + report_md())


def report_md() -> list[str]:
    return [
        "## Reporting a security issue",
        "",
        "Please report security problems privately, not in a public issue: use the "
        "repository host's private vulnerability reporting (on GitHub: Security, then "
        "Report a vulnerability), or write to the maintainers directly. Include the "
        "version, the steps to reproduce and what an attacker could do. You will get an "
        "answer within a few working days.",
        "",
    ]


MARK = {"green": "green", "amber": "amber", "red": "red", "n/a": "n/a"}


def render_readiness(product: str, rd: dict, opens: list[dict]) -> str:
    fin = rd.get("final") or {}
    items = fin.get("items") or []
    c = fin.get("counts") or {}
    L = [
        "# Readiness",
        "",
        f"Where {product or 'this product'} stands on the way to production: each item was "
        "checked by doing it (installing from the README, starting it, backing it up and "
        "restoring it, ...) or by reading the repository. Green is ready, amber needs "
        "attention before production, red is a gap.",
        "",
        f"**{c.get('green', 0)} green, {c.get('amber', 0)} amber, {c.get('red', 0)} red**"
        + (f", {c.get('na', 0)} not applicable" if c.get("na") else "")
        + ".",
        "",
        "| item | status | why | next step to production |",
        "|---|---|---|---|",
    ]
    L += [
        f"| {_cell(i['title'])} | {MARK.get(i['status'], i['status'])} | {_cell(i.get('why'))} "
        f"| {_cell(i.get('next')) or ('-' if i['status'] in ('green', 'n/a') else '')} |"
        for i in items
    ] + [""]
    sec = rd.get("security") or {}
    open_sec = [f for f in sec.get("findings") or [] if f.get("status") not in ("fixed",)]
    L += [
        "## Security",
        "",
        (
            f"See `security.md`: {len(sec.get('findings') or [])} finding(s), "
            f"{len(open_sec)} not fixed by a tested change"
            + (
                " ("
                + ", ".join(
                    f"{s} {sum(1 for f in open_sec if f['severity'] == s)}"
                    for s in SEVERITIES
                    if any(f["severity"] == s for f in open_sec)
                )
                + ")"
                if open_sec
                else ""
            )
            + "."
            if sec.get("ran")
            else "This version has not had a security review: see `security.md`."
        ),
        "",
    ]
    if opens:
        L += [
            "## Checks that still fail",
            "",
            "These end-to-end checks (in `exam/`, beside this report) fail on this version:",
            "",
        ]
        L += [f"- `{Path(o['file']).name}`: {o['reason']}" for o in opens] + [""]
    return "\n".join(L)


def report_section(rd: dict) -> list[str]:
    """REPORT.md's readiness section (the factory's report: ids are fine here)."""
    if not rd or rd.get("next") is None and not rd.get("final"):
        return []
    sec, fin = rd.get("security") or {}, rd.get("final") or {}
    L = ["## Readiness", ""]
    if rd.get("skipped") and not sec.get("ran"):
        L += [f"Skipped: {rd['skipped']}.", ""]
    found = sec.get("findings") or []
    if found:
        L += ["| finding | severity | status | sent | test |", "|---|---|---|---|---|"]
        L += [
            f"| {f['id']}: {_cell(f['title'])} | {f['severity']} | {f.get('status')} | "
            f"{'yes' if f.get('sent') else 'no'} | {f.get('rule') or '-'} |"
            for f in found
        ]
        L.append("")
    elif sec.get("ran"):
        L += ["Security review: no findings.", ""]
    r = rd.get("round") or {}
    if r:
        L += [
            f"Readiness repair: {len(r.get('sent') or [])} item(s) sent, "
            f"{len(r.get('fixed') or []) + len(r.get('extra') or [])} fixed, "
            f"{len(r.get('regressions') or [])} regression(s); "
            f"{'accepted' if r.get('accepted') else 'not accepted'} ({_cell(r.get('why'))}).",
            "",
        ]
    elif rd.get("skipped"):
        L += [f"Readiness repair: not run ({rd['skipped']}).", ""]
    if rd.get("scenarios_updated"):
        L += [
            "Scenarios the repair updated for the safe defaults: "
            + ", ".join(f"{x['id']} ({_cell(x['title'])})" for x in rd["scenarios_updated"])
            + ".",
            "",
        ]
    run_items = {i["id"]: i for i in fin.get("items") or [] if i["id"] in RUN}
    if run_items:
        L += [
            "The README's commands, run as written on a fresh copy: "
            + "; ".join(
                f"{k.replace('_', ' ')} {run_items[k]['status']} ({_cell(run_items[k].get('why'))})"
                for k in RUN
                if k in run_items
            )
            + ".",
            "",
        ]
    if fin.get("counts"):
        c = fin["counts"]
        L += [
            f"Scorecard: {c.get('green', 0)} green, {c.get('amber', 0)} amber, "
            f"{c.get('red', 0)} red, {c.get('na', 0)} n/a (the evidence's `readiness.md`; "
            "security in `security.md`).",
            "",
        ]
    return L

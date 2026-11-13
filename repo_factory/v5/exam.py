"""repo-factory's exam, outside the product (EX-PLAN §3).

A round's **exam** is its rule store materialised as files in the den, with the verifier's
driver: ``<round>/exam/_verify/`` (every active scenario, held ones too) and
``<round>/exam/visible/_verify/`` (what a repair session may see), hashed into
``manifest.json``. It is never copied into the product's tree. Sessions that write it (the
verifier, the security and checks sessions) get their own exam workspace read-write at
``/work/exam``; repair sessions get ``visible/`` read-only there. The product is always at
``/work/product``, so a scenario runs the same in a session and in the harness.

``run`` is the one way the harness runs anything against a tree: the product's own tests
(pytest per test, and the README's test commands as written) and the exam's scenarios, in one
sandbox invocation (``examrunner.py`` inside bwrap), cached by **tree hash** in
``<round>/exam/results/<key>.json``. Every consumer reads that record: validate, measure,
re-verify, readiness, delivery, the gate, a revise round's baseline.

The flake protocol (§3.3): with ``flake=True`` a failing scenario (or own test command) is run
again alone, ``exam.reruns`` more times (5 runs in all), stopping once the verdict is
decided. One failure in five: **flaky**, repo-factory's own problem: the scenario is retired and
listed (``flaky.jsonl``), never a finding. At least ``exam.intermittent_hold_min`` (3)
failures: it fails. In between: it passes, with an amber "intermittent" row and its rate.
Nothing is ever marked xfail in a tree."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ..harness import links

DIR = "exam"
VERIFY = "_verify"
SCENARIOS = f"{VERIFY}/scenarios"
EXAM_AT = "/work/exam"
PRODUCT_AT = "/work/product"
PROPOSALS_AT = "/work/exam-proposals"
SRC_AT = "/work/src"
OUT_AT = "/work/out"
BIN_AT = "/work/bin"
DEPS_AT = "/work/deps"
HOME_AT = "/home/user"
SETTINGS_ENV = "REPO_FACTORY_EXAM"
DEFAULTS = {
    "reruns": 4,
    "intermittent_hold_min": 3,
    "flaky_retire": True,
    "adopt": "fixes",
    "indexes": ["pypi.org", "files.pythonhosted.org", "registry.npmjs.org"],
    "evidence_asset": True,
}
TIMEOUTS = {
    "pytest_s": 900,
    "scenarios_s": 600,
    "rerun_s": 300,
    "test_s": 900,
    "tests_total_s": 1500,
}
SKIP = shutil.ignore_patterns(
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "node_modules",
    "*.pyc",
    "transcript.jsonl",
    "stderr.txt",
)
LAYOUTS = ("exam", "inside")  # inside: a round from before the exam moved out


class ExamTampered(RuntimeError):
    pass


# -- settings and identity --------------------------------------------------------------------
def settings(run=None) -> dict:
    """``exam.*``: the defaults, the policy's ``exam:`` section, then the loop's settings
    (``$REPO_FACTORY_EXAM``, a JSON object the loop passes to the build)."""
    out = dict(DEFAULTS)
    rules = getattr(_unwrap(run), "rules", None) if run is not None else None
    out.update((rules or {}).get("exam") or {})
    try:
        env = json.loads(os.environ.get(SETTINGS_ENV) or "{}")
    except ValueError:
        env = {}
    if isinstance(env, dict):
        out.update({k: v for k, v in env.items() if k in DEFAULTS})
    out["reruns"] = max(0, int(out["reruns"]))
    out["intermittent_hold_min"] = max(1, int(out["intermittent_hold_min"]))
    return out


def factory_version() -> str:
    try:
        from .. import __version__

        return str(__version__)
    except ImportError:  # pragma: no cover
        return "0.0.0+unknown"


def _unwrap(run):
    return getattr(run, "_run", run) if run is not None else None


def layout(run) -> str:
    return getattr(_unwrap(run), "layout", None) or "exam"


def digest(path: Path | None) -> str:
    """sha256 over the sorted (relative path, bytes) of every file under ``path``."""
    h = hashlib.sha256()
    if path is None or not Path(path).is_dir():
        return "none"
    for p in sorted(Path(path).rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            h.update(p.relative_to(path).as_posix().encode() + b"\0")
            h.update(p.read_bytes())
    return h.hexdigest()


def tree_key(root: Path) -> str:
    from .commands import tree_key as tk

    return tk(Path(root))


def is_doc(path: str) -> bool:
    """Documentation, for the exam's reuse: Markdown. (A Markdown file the product executes,
    a doctest, would have to count as code.)"""
    return str(path).lower().endswith(".md")


def code_key(root: Path) -> str:
    """``tree_key`` over the tree's non-documentation files: two trees that differ only in
    Markdown have the same one. The top-level ``.gitignore`` does not count either: a build
    measures its tree with repo-factory's own one, which the branch never gets (``_strip_scaffold``),
    and nothing in the exam reads it."""
    h = hashlib.sha256()
    root = Path(root)
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if (
            p.is_file()
            and not {".git", "__pycache__", ".pytest_cache"} & set(p.parts)
            and not is_doc(p.name)
            and rel != ".gitignore"
        ):
            h.update(rel.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:20]


def _sha(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


# -- materialising ------------------------------------------------------------------------------
def write_exam(dest: Path, rules: list, helpers: Path | None) -> Path:
    """``dest/_verify/``: the helpers exactly as the verifier wrote them (no remap) and
    ``scenarios/test_rule_<id>.py`` per rule. Returns ``dest``."""
    dest = Path(dest)
    v = dest / VERIFY
    if v.exists():
        shutil.rmtree(v)
    if helpers is not None and Path(helpers).is_dir():
        links.copytree(
            helpers,
            v,
            ignore=shutil.ignore_patterns(
                "scenarios",
                "findings.json",
                "claims.json",
                "spec.md",
                "security.json",
                "readiness.json",
                "__pycache__",
                "*.pyc",
            ),
        )
    v.mkdir(parents=True, exist_ok=True)
    (v / "__init__.py").touch()
    s = v / "scenarios"
    s.mkdir(exist_ok=True)
    for r in rules:
        (s / r.test_file).write_text(r.code.rstrip() + "\n")
    return dest


def exam_for(run, rules: list) -> tuple[Path, str]:
    """The exam for exactly ``rules`` (validate runs candidates, measure the active set),
    under ``<round>/exam/sets/<key>/``: written once per rule set and helpers. Returns the
    directory and its ``exam_hash``."""
    run = _unwrap(run)
    hd = digest(run.helpers) if run.helpers else "none"
    k = _sha(run.store.key(rules), hd)[:16]
    d = run.root / DIR / "sets" / k
    ok = d / ".ok"
    if not ok.is_file():
        write_exam(d, rules, run.helpers)
        ok.write_text(digest(d / VERIFY))
    return d, ok.read_text().strip()


def materialise(run) -> dict:
    """The round's exam: ``exam/_verify`` (every active scenario), ``exam/visible/_verify``
    (the visible ones) and ``manifest.json``. Rebuilt after a store change; the results,
    ``flaky.jsonl`` and the evidence stay. Returns the manifest."""
    run = _unwrap(run)
    root = run.root / DIR
    root.mkdir(parents=True, exist_ok=True)
    rules = run.store.active()
    write_exam(root, rules, run.helpers)
    write_exam(root / "visible", [r for r in rules if r.visibility == "visible"], run.helpers)
    from ..custom_prompts import build_versions

    try:
        prompts = build_versions(run.policy)
    except Exception:  # noqa: BLE001 - a policy without versions
        prompts = {}
    man = {
        "factory_version": factory_version(),
        "profile": _profile(),
        "prompts": prompts,
        "layout": layout(run),
        "exam_hash": digest(root / VERIFY),
        "visible_hash": digest(root / "visible" / VERIFY),
        "helpers_hash": digest(run.helpers) if run.helpers else "none",
        "scenarios": [
            {
                "id": r.id,
                "scenario": r.origin.get("scenario"),
                "visibility": r.visibility,
                "version": r.version,
                "digest": r.digest(),
                "file": f"{SCENARIOS}/{r.test_file}",
            }
            for r in rules
        ],
    }
    (root / "manifest.json").write_text(json.dumps(man, indent=1))
    return man


def _profile() -> str:
    try:
        from ..harness.profile import PROFILE

        return PROFILE
    except Exception:  # noqa: BLE001
        return "unknown"


def visible(run) -> Path:
    """The read-only exam a repair session gets at ``/work/exam`` (``visible/``), fresh."""
    materialise(run)
    return _unwrap(run).root / DIR / "visible"


# -- the sessions' mounts -----------------------------------------------------------------------
def session_mounts(exam_dir: Path | None, rw: bool, proposals: Path | None = None) -> list:
    """Worker-sandbox binds for a session: the exam at ``/work/exam`` (read-write for the
    sessions that write it, read-only for repairs) and, for the readiness repair, the
    proposals folder at ``/work/exam-proposals``."""
    out = []
    if exam_dir is not None:
        out.append(
            {
                "kind": "rw" if rw else "ro",
                "src": str(Path(exam_dir).resolve()),
                "dst": EXAM_AT,
                "why": "repo-factory's exam" + ("" if rw else " (read-only)"),
            }
        )
    if proposals is not None:
        Path(proposals).mkdir(parents=True, exist_ok=True)
        out.append(
            {
                "kind": "rw",
                "src": str(Path(proposals).resolve()),
                "dst": PROPOSALS_AT,
                "why": "proposed start-ups of a scenario (readiness repair)",
            }
        )
    return out


def mounted(opts: dict, at: str = EXAM_AT) -> Path | None:
    """For test doubles: the host directory a session's launch binds at ``at``."""
    lc = (opts or {}).get("launch")
    for m in getattr(lc, "mounts", None) or []:
        if m.get("dst") == at:
            return Path(m["src"])
    return None


def check_unchanged(path: Path, before: str, stage: str) -> None:
    """After a session that had the exam mounted read-only: the host directory's hash must
    be what it was. A change ends the round ``error`` (``StageFailed``); the bind is
    read-only, so this is the belt."""
    now = digest(path)
    if now != before:
        from .pipeline import StageFailed

        raise StageFailed(
            stage,
            "the exam changed during the session (repo-factory's sandbox let a "
            "write through: a factory issue, exam/tampered)",
        )


# -- running ---------------------------------------------------------------------------------------
def readme_tests(tree: Path) -> tuple[list[dict], dict]:
    """The README's test commands that can run here (``commands.extract``), and the extract."""
    from . import commands

    ex = commands.extract(tree)
    return [e for e in ex["tests"] if not e.get("skip")], ex


def run(
    run,
    tree: Path,
    *,
    rules: list | None = None,
    own: bool = False,
    readme: bool = False,
    reverse: bool = False,
    flake: bool = False,
) -> dict:
    """The exam record of ``tree`` (cached by tree hash, exam hash and what was asked).
    ``rules``: the scenarios to run (default: every active one; ``[]``: none); ``own``: the
    product's pytest per test; ``readme``: the README's test commands as written;
    ``reverse``: the scenarios in reverse order (validate's second run); ``flake``: the
    rerun protocol."""
    run = _unwrap(run)
    tree = Path(tree)
    rules = run.store.active() if rules is None else list(rules)
    cfg = settings(run)
    tk = tree_key(tree)
    exam_dir, ehash = exam_for(run, rules) if rules else (None, None)
    cmds, ex = readme_tests(tree) if readme else ([], None)
    parts = _parts(run, own, cmds, rules, reverse, flake)
    key = _sha(tk, ehash, parts)[:24]
    cache = run.__dict__.setdefault("_exam_runs", {})
    if key in cache:
        return cache[key]
    path = run.root / DIR / "results" / f"{key}.json"
    if path.is_file():
        try:
            cache[key] = json.loads(path.read_text())
            return cache[key]
        except ValueError:
            pass
    rec = _invoke(run, tree, tk, rules, exam_dir, ehash, cmds, own, reverse, cfg if flake else None)
    rec.update(key=key, parts=parts, extract=_brief(ex) if ex else None, code=code_key(tree))
    _save(run, rec)
    with open(run.root / DIR / "runs.jsonl", "a") as f:  # one line per sandbox run
        f.write(
            json.dumps(
                {
                    "key": key,
                    "tree": tk,
                    "exam_hash": ehash,
                    "at": rec["at"],
                    "wall_s": rec.get("wall_s"),
                    "rules": len(rules),
                    "own": own,
                    "readme": bool(cmds),
                    "ran": rec.get("ran"),
                }
            )
            + "\n"
        )
    try:
        run.emit(
            "exam_run",
            tree=tk,
            exam_hash=(ehash or "")[:16],
            rules=len(rules),
            own=bool(own),
            readme=len(cmds),
            wall_s=rec.get("wall_s"),
            failed=sum(1 for v in rec["scenarios"].values() if v["status"] != "passed"),
            flaky=len(rec.get("flaky") or []),
        )
    except Exception:  # noqa: BLE001 - a run without an event log
        pass
    return rec


def _parts(run, own: bool, cmds: list[dict], rules: list, reverse: bool, flake: bool) -> dict:
    """What a record measured, beside the tree and the exam (part of its key)."""
    cfg = settings(run)
    # ``node``: a record made before the own-suite run ran ``npm test`` is not reused
    return {
        "own": bool(own),
        **({"node": 1} if own else {}),
        "readme": [c["cmd"] for c in cmds],
        "rules": len(rules),
        "reverse": bool(reverse),
        "layout": layout(run),
        "flake": [cfg["reruns"], cfg["intermittent_hold_min"], cfg["flaky_retire"]]
        if flake
        else None,
    }


def _save(run, rec: dict) -> None:
    """``results/<key>.json`` and the in-memory cache."""
    path = run.root / DIR / "results" / f"{rec['key']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, default=str))
    tmp.replace(path)
    run.__dict__.setdefault("_exam_runs", {})[rec["key"]] = rec


def reuse(r, art: Path, parent: tuple[int, dict]) -> dict | None:
    """A docs round's delivered tree (AU1 #8): when its non-documentation files are those of
    the round it revises (``code_key``) and the exam and its settings are the same, that
    round's record (``parent``: its number and record) stands for this tree. If the README's
    test commands changed, only they run again. The record says so (``reused``). None: the
    record does not fit; run everything."""
    r0 = _unwrap(r)
    rnd, prev = parent
    if not prev.get("ran") or not prev.get("code") or prev["code"] != code_key(art):
        return None
    rules = r0.store.active()
    ehash = exam_for(r0, rules)[1] if rules else None
    cmds, ex = readme_tests(art)
    parts = _parts(r0, True, cmds, rules, False, True)
    pp = prev.get("parts") or {}
    if prev.get("exam_hash") != ehash or any(
        pp.get(k) != v for k, v in parts.items() if k != "readme"
    ):
        return None
    rec = json.loads(json.dumps(prev))
    rerun = parts["readme"] != pp.get("readme")
    if rerun:
        rec["own"] = run(r0, art, rules=[], readme=True, flake=True).get("own") if cmds else None
    tk = tree_key(art)
    rec.update(
        tree=tk,
        key=_sha(tk, ehash, parts)[:24],
        parts=parts,
        extract=_brief(ex),
        at=time.time(),
        reused={"round": rnd, "key": prev.get("key"), "readme": "run again" if rerun else "reused"},
    )
    _save(r0, rec)
    try:
        r0.emit("exam_reused", tree=tk, round=rnd, readme=rec["reused"]["readme"])
    except Exception:  # noqa: BLE001 - a run without an event log
        pass
    return rec


def reused_words(rec: dict) -> str | None:
    """The plain words for a reused record (the timeline and the report)."""
    u = rec.get("reused") or {}
    if not u:
        return None
    return f"Exam reused from round {u.get('round')}: only documentation changed" + (
        "; the README's test commands ran again" if u.get("readme") == "run again" else ""
    )


def _brief(ex: dict) -> dict:
    from .commands import _brief as b

    return b(ex)


def _invoke(run, tree, tk, rules, exam_dir, ehash, cmds, own, reverse, flake) -> dict:
    from ..harness.isolation import isolation, require_sandbox, sandbox_cmd

    rec = {
        "tree": tk,
        "exam_hash": ehash,
        "factory_version": factory_version(),
        "at": time.time(),
        "layout": layout(run),
        "ran": False,
        "why": None,
        "scenarios": {},
        "flaky": [],
        "own": None,
        "tests": None,
        "errors": None,
        "details": None,
    }
    try:
        venv = Path(run.venv)
    except Exception as e:  # noqa: BLE001 - no venv: a build cut off
        rec["why"] = f"no environment to run it in ({e})"
        rec["scenarios"] = {
            r.id: {"status": "error", "detail": rec["why"], "broken": None} for r in rules
        }
        return rec
    t0 = time.time()
    sandboxed = isolation() == "bwrap"
    if not sandboxed:
        require_sandbox("The exam")
    with tempfile.TemporaryDirectory(prefix="repo-factory-exam-") as tmp:
        tmp = Path(tmp)
        src, product, out, bindir = tmp / "src", tmp / "product", tmp / "out", tmp / "bin"
        links.copytree(tree, src, ignore=SKIP)
        for d in (product, out, bindir):
            d.mkdir()
        here = Path(__file__).parent
        shutil.copy(here / "examrunner.py", bindir / "examrunner.py")
        shutil.copy(here / "cmdrunner.py", bindir / "cmdrunner.py")
        deps = install_deps(run, tree)
        rec["install"] = {k: v for k, v in deps.items() if k not in ("dirs", "wheels")}
        wheels = deps.get("wheels")
        files = [f"{SCENARIOS}/{r.test_file}" for r in rules]

        def at(host, inside):
            return inside if sandboxed else str(host)

        home = HOME_AT if sandboxed else str(tmp / "home")
        if not sandboxed:
            Path(home).mkdir()
        dep_dirs = {name: at(p, f"{DEPS_AT}/{name}") for name, p in deps["dirs"].items()}
        env = {
            "HOME": home,
            "USER": "user",
            "LOGNAME": "user",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TERM": "dumb",
            "CI": "1",
            "TMPDIR": "/tmp",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PATH": f"{venv}/bin:/usr/local/bin:/usr/bin:/bin",
            "REPO_FACTORY_PRODUCT_DIR": at(product, PRODUCT_AT),
        }
        if wheels:
            from .pyinstall import AT as WHEELS_AT
            from .pyinstall import env as pip_env

            env.update(pip_env(at(wheels, WHEELS_AT)))
        plan = {
            "src": at(src, SRC_AT),
            "product": at(product, PRODUCT_AT),
            "exam": at(exam_dir, EXAM_AT) if exam_dir else None,
            "out": at(out, OUT_AT),
            "deps": dep_dirs,
            "layout": layout(run),
            "own_pytest": bool(own),
            "own_node": bool(own) and node_suite(tree),
            "own_cmds": [{"cmd": c["cmd"], "where": c.get("where")} for c in cmds],
            "scenarios": files,
            "reverse": bool(reverse),
            "sandboxed": sandboxed,
            "path_prefix": [str(venv / "bin")],
            "env": env,
            "flake": {"reruns": flake["reruns"], "stop_at": flake["intermittent_hold_min"]}
            if flake
            else None,
            "timeouts": _timeouts(run),
        }
        (out / "plan.json").write_text(json.dumps(plan))
        py = str(venv / "bin" / "python")
        if sandboxed:
            binds = [(src, SRC_AT, True), (out, OUT_AT, False), (bindir, BIN_AT, True)]
            if exam_dir:
                binds.append((exam_dir, EXAM_AT, True))
            for name, p in deps["dirs"].items():
                binds.append((p, f"{DEPS_AT}/{name}", True))
            if wheels:
                binds.append((wheels, WHEELS_AT, True))
            cmd = sandbox_cmd(
                product,
                ro=(venv,),
                interp=py,
                network=False,
                binds=binds,
                etc=True,
                home=HOME_AT,
                work_at=PRODUCT_AT,
            ) + [f"{BIN_AT}/examrunner.py", f"{OUT_AT}/plan.json"]
            penv = None
        else:
            cmd = [py, str(bindir / "examrunner.py"), str(out / "plan.json")]
            penv = env
        t = plan["timeouts"]
        total = (
            t["pytest_s"]
            + t["tests_total_s"] * 2
            + t["scenarios_s"]
            + t["rerun_s"] * (flake["reruns"] if flake else 0) * min(len(rules), 12)
            + 300
        )
        try:
            p = subprocess.run(
                cmd, cwd=product, capture_output=True, text=True, timeout=total, env=penv
            )
            err = (p.stderr or "")[-1500:]
        except subprocess.TimeoutExpired:
            err = f"the exam run itself timed out after {total} s"
        links.scrub(out)  # the product's own code ran with it writable
        f = out / "result.json"
        if not f.is_file():
            rec["why"] = "the exam runner failed: " + err
            rec["scenarios"] = {
                r.id: {"status": "error", "detail": rec["why"], "broken": None} for r in rules
            }
            return rec
        got = json.loads(f.read_text())
        rec["ran"] = True
        rec["isolation"] = isolation()
        if own:
            rec.update(_own_pytest(got.get("own_pytest") or {}, out, plan["product"]))
        if got.get("own_node"):
            node = _own_node(got["own_node"], out, plan["product"])
            rec["tests"] = {**(rec.get("tests") or {}), **node["tests"]}
            rec["details"] = {**(rec.get("details") or {}), **node["details"]}
            rec["own_node"] = {k: v for k, v in node.items() if k not in ("tests", "details")}
        if cmds:
            rec["own"] = own_summary(got.get("own_cmds") or [], flake)
        if rules:
            rec["scenarios"], rec["flaky"] = _scenarios(got, rules, out, plan, flake)
    rec["wall_s"] = round(time.time() - t0, 1)
    rec["env"] = {
        "isolation": "bwrap" if sandboxed else "none",
        "python": _pyver(venv),
        "install": rec.get("install"),
    }
    return rec


def _timeouts(run) -> dict:
    from . import readiness

    c = readiness.cfg(run, "commands") or {}
    t = dict(TIMEOUTS)
    t["test_s"] = int(c.get("test_s", t["test_s"]))
    t["tests_total_s"] = int(c.get("tests_total_s", t["tests_total_s"]))
    t.update({k: v for k, v in (settings(run).get("timeouts") or {}).items() if k in t})
    return t


def _pyver(venv: Path) -> str | None:
    cfg = Path(venv) / "pyvenv.cfg"
    try:
        m = re.search(r"^version(?:_info)?\s*=\s*(\S+)", cfg.read_text(), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


NPM_DEFAULT_TEST = "no test specified"


def node_suite(tree: Path) -> bool:
    """The tree has its own Node test script (``package.json`` ``scripts.test``, not npm's
    placeholder): the own-suite run runs it beside pytest (track LEAN2: a Node product's
    flows were "unchecked" because the run was pytest only)."""
    try:
        d = json.loads((Path(tree) / "package.json").read_text())
    except (OSError, ValueError):
        return False
    t = ((d.get("scripts") or {}) if isinstance(d, dict) else {}).get("test")
    return isinstance(t, str) and bool(t.strip()) and NPM_DEFAULT_TEST not in t


def _own_node(got: dict, out: Path, product: str) -> dict:
    """``npm test``'s result: per test from Node's junit report, ``file::name`` (the file
    relative to the product, as pytest's ids are). A suite whose runner writes no report
    (jest, mocha) gives only its exit; its tests stay unknown."""
    import xml.etree.ElementTree as ET

    from ..harness.venv import _detail

    tests, details = {}, {}
    junit = out / (got.get("junit") or "own-node.xml")
    if not got.get("timeout") and junit.is_file():
        try:
            cases = list(ET.parse(junit).getroot().iter("testcase"))
        except ET.ParseError:
            cases = []
        for tc in cases:
            file = tc.get("file") or tc.get("classname") or ""
            if file.startswith(product.rstrip("/") + "/"):
                file = file[len(product.rstrip("/")) + 1 :]
            tid = f"{file}::{tc.get('name', '')}"
            fail, err = tc.find("failure"), tc.find("error")
            if fail is not None or err is not None:
                tests[tid] = "failed" if fail is not None else "error"
                details[tid] = _detail(fail if fail is not None else err, Path(product))
            else:
                tests[tid] = "skipped" if tc.find("skipped") is not None else "passed"
    return {
        "tests": tests,
        "details": details,
        "returncode": got.get("rc"),
        "timeout": bool(got.get("timeout")),
        "output": (got.get("output") or "")[-1500:],
    }


def _own_pytest(got: dict, out: Path, product: str) -> dict:
    from ..harness.venv import parse_junit

    junit = out / (got.get("junit") or "own.xml")
    if got.get("timeout") or not junit.is_file():
        return {
            "tests": {},
            "errors": [],
            "details": {},
            "returncode": -1,
            "own_output": got.get("output"),
        }
    tests, errors, details = parse_junit(junit, Path(product))
    return {"tests": tests, "errors": errors, "details": details, "returncode": got.get("rc")}


def own_summary(cmds: list[dict], flake: dict | None) -> dict:
    """The README's test commands: each with its runs and verdict. A command that fails at
    least ``intermittent_hold_min`` times of its series (or once, without the flake
    protocol), or times out, is red; one that failed less often is green with an
    ``intermittent`` note. ``green``: none is red; ``ran``: one ran at all."""
    hold = int((flake or {}).get("intermittent_hold_min", 1))
    rows, inter = [], []
    for c in cmds:
        runs = c.get("runs") or ([c.get("status")] if c.get("status") else [])
        fails = sum(1 for x in runs if x != "ok")
        row = {k: c.get(k) for k in ("cmd", "where", "status", "rc", "output", "wall_s", "why")}
        row["runs"] = runs
        if (
            c.get("status") in ("timeout", "not_run")
            or (fails and fails >= hold)
            or (fails and not flake)
        ):
            row["verdict"] = "red"
            if c.get("status") == "ok":  # its last run passed; the series didn't
                row["status"] = "failed"
        elif fails:
            row.update(verdict="intermittent", status="ok", rate=round(fails / len(runs), 2))
            inter.append(row)
        else:
            row["verdict"] = "green"
        rows.append(row)
    return {
        "commands": rows,
        "green": bool(rows) and all(r["verdict"] != "red" for r in rows),
        "ran": any(r["status"] not in ("not_run", "skipped") for r in rows),
        "intermittent": [{"cmd": r["cmd"], "rate": r["rate"], "runs": r["runs"]} for r in inter],
    }


def _scenarios(got: dict, rules: list, out: Path, plan: dict, flake: dict | None):
    """Per-rule outcomes (``score.outcomes``), with the flake protocol's verdicts."""
    from ..harness.venv import parse_junit
    from ..rules.score import outcomes

    s = got.get("scenarios") or {}
    junit = out / (s.get("junit") or "scenarios.xml")
    if s.get("timeout") or not junit.is_file():
        res = {"tests": {}, "errors": [], "details": {}, "returncode": -1}
    else:
        tests, errors, details = parse_junit(junit, Path(plan["exam"] or plan["product"]))
        res = {"tests": tests, "errors": errors, "details": details, "returncode": s.get("rc")}
    outs = outcomes(res, rules, SCENARIOS)
    flaky = []
    reruns = got.get("reruns") or {}
    hold = int((flake or {}).get("intermittent_hold_min", 3))
    retire = bool((flake or {}).get("flaky_retire", True))
    for r in rules:
        runs = reruns.get(f"{SCENARIOS}/{r.test_file}")
        o = outs.get(r.id)
        if not runs or o is None or o["status"] == "passed":
            continue
        fails = sum(1 for x in runs if x != "passed")
        o["runs"] = runs
        o["rate"] = round(fails / len(runs), 2)  # stored-shape: not runs continued above
        if fails == len(runs):  # stored-shape: not runs continued above
            o["class"] = "consistent"
        elif fails == 1 and retire:
            o.update(status="passed", flaky=True, **{"class": "flaky"})
            flaky.append(r.id)
        elif fails >= hold:
            o["class"] = "intermittent"
        else:
            o.update(status="passed", intermittent=True, **{"class": "intermittent"})
    return outs, flaky


# -- what the record says ----------------------------------------------------------------------
def outcomes_of(rec: dict) -> dict:
    """The record's per-rule outcomes, in ``run_rules``' format."""
    return {k: dict(v) for k, v in (rec.get("scenarios") or {}).items()}


def suite_of(rec: dict) -> dict:
    """The product's own pytest run, in ``run_pytest``'s format."""
    return {
        "tests": rec.get("tests") or {},
        "errors": rec.get("errors") or [],
        "details": rec.get("details") or {},
        "returncode": rec.get("returncode", -1 if not rec.get("ran") else 0),
    }


def intermittent(rec: dict) -> list[dict]:
    """The amber rows: scenarios and own commands that failed some runs, not enough to
    fail."""
    out = [
        {"kind": "scenario", "id": k, "rate": v.get("rate"), "runs": v.get("runs")}
        for k, v in (rec.get("scenarios") or {}).items()
        if v.get("intermittent")
    ]
    out += [{"kind": "command", **x} for x in ((rec.get("own") or {}).get("intermittent") or [])]
    return out


def absorb(run, rec: dict) -> list[str]:
    """The flaky scenarios of a record leave the exam: retired with the reason (``flaky:
    failed once in 5 runs``), logged to ``flaky.jsonl`` with their runs; never a finding.
    Returns the ids retired now."""
    run = _unwrap(run)
    out = []
    for i in rec.get("flaky") or []:
        r = run.store.rules.get(i)
        if r is None or r.status != "active":
            continue
        o = (rec.get("scenarios") or {}).get(i) or {}
        n = len(o.get("runs") or [])
        run.store.set_status(i, "retired", f"flaky: failed once in {n} runs", "exam-flaky")
        with open(run.root / DIR / "flaky.jsonl", "a") as f:
            f.write(
                json.dumps(
                    {
                        "id": i,
                        "scenario": r.origin.get("scenario"),
                        "title": r.title,
                        "runs": o.get("runs"),
                        "tree": rec.get("tree"),
                        "detail": (o.get("detail") or "")[-1500:],
                        "at": time.time(),
                        "factory_version": factory_version(),
                    }
                )
                + "\n"
            )
        out.append(i)
    if out:
        run.store.save()
    return out


def flaky_rows(run) -> list[dict]:
    p = _unwrap(run).root / DIR / "flaky.jsonl"
    rows = []
    try:
        for line in p.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return rows


# -- delivery (§4) ----------------------------------------------------------------------------------
def final(r, art: Path, parent: tuple[int, dict] | None = None) -> dict:
    """The delivered tree's record: the product's own tests (per test, and the README's test
    commands as written) and every active scenario, the flake protocol on; flaky scenarios
    leave the exam, and the exam is materialised for the evidence. ``parent``: a docs
    round's parent record, reused when only documentation changed (``reuse``)."""
    rec = (reuse(r, art, parent) if parent else None) or run(
        r, art, own=True, readme=True, flake=True
    )
    absorb(r, rec)
    materialise(r)
    return rec


TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs", "testing"}
TEST_FILE = re.compile(
    r"(^tests?(?:_[\w.-]+|\.\w+)|.*_test\.\w+|.*\.(?:test|spec)\.[jt]sx?"
    r"|.*Test\.\w+)$"
)


def is_test_path(path: str) -> bool:
    """A file of the product's own tests: under a tests directory, or named like one."""
    parts = Path(path).parts
    return bool(set(parts[:-1]) & TEST_DIRS) or bool(TEST_FILE.match(parts[-1] if parts else ""))


def adoption(r, ws: Path | None = None) -> dict:
    """``exam.adopt: fixes`` (§4.3): every finding a ``fix(<id>)`` commit names comes with a
    test of the product's own, in some commit naming it. Checked from git, no model:
    ``{"mode", "fixed": [ids], "missing": [ids], "with": [ids]}``."""
    from . import repair

    r = _unwrap(r)
    mode = str(settings(r).get("adopt") or "fixes")
    ws = Path(ws or r.ws)
    out = {"mode": mode, "fixed": [], "missing": [], "with": []}
    if mode == "off" or not (ws / ".git").exists():
        return out
    try:
        log = repair.git(ws, "log", "--format=%H%x09%s").splitlines()
    except RuntimeError:
        return out
    named: dict[str, list[str]] = {}
    reverted: set[str] = set()
    for line in reversed(log):  # oldest first
        sha, _, subj = line.partition("\t")
        if subj.lower().startswith("revert("):
            reverted |= set(repair.attributed(subj.replace("revert(", "fix(", 1)))
            continue
        ids = repair.attributed(subj)
        if not ids:
            continue
        try:
            files = repair.git(ws, "show", "--name-only", "--format=", sha).split()
        except RuntimeError:
            files = []
        for i in ids:
            reverted.discard(i)
            named.setdefault(i, []).extend(files)
    for i, files in named.items():
        if i in reverted:
            continue
        out["fixed"].append(i)
        (out["with"] if any(is_test_path(f) for f in files) else out["missing"]).append(i)
    return out


EVIDENCE_README = """# The checks behind {product}

An autonomous software build made this product and checked it before the release. The
checks are not part of the product's code; they are published beside it, so anyone can see
what was checked and run it again.

- `exam/_verify/scenarios/`: one end-to-end check per behaviour, through the product's real
  interface (its command line, HTTP on 127.0.0.1, or its library), and in `exam/_verify/`
  the driver they share.
- `results.json`: what happened on the released tree: the product's own tests (as its README
  documents them) and every check, with the reruns of anything that failed.
- `readiness.md`: the operational readiness scorecard.
- `security.md`: the threat model, what was probed, and the findings.
- `assumptions.md`: the questions the brief left open, and what this version does.
- `manifest.json`: which build checked it, the checks' hash and the tree they ran on.

Run the checks against a checkout of the product (they need pytest):

    REPO_FACTORY_PRODUCT_DIR=/path/to/checkout PYTHONPATH=/path/to/checkout:exam \\
        python -m pytest -p no:cacheprovider exam/_verify/scenarios

They were written against the product at `/work/product`; a check that names that path
expects the checkout there (a symbolic link is enough).
"""


def evidence(r, rec: dict, docs: dict[str, str], product: str) -> Path:
    """``<round>/exam/evidence/``: what the release asset holds (§4.4). ``docs``:
    ``readiness.md``, ``security.md``, ``assumptions.md`` by name."""
    r = _unwrap(r)
    root = r.root / DIR
    ev = root / "evidence"
    if ev.exists():
        shutil.rmtree(ev)
    ev.mkdir(parents=True)
    if (root / VERIFY).is_dir():
        links.copytree(
            root / VERIFY,
            ev / "exam" / VERIFY,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    (ev / "results.json").write_text(json.dumps(rec, indent=1, default=str))
    for name, text in docs.items():
        if text:
            (ev / name).write_text(text)
    (ev / "README.md").write_text(EVIDENCE_README.format(product=product or "this product"))
    man = (
        json.loads((root / "manifest.json").read_text())
        if (root / "manifest.json").is_file()
        else {}
    )
    man.update(
        tree=rec.get("tree"),
        results=rec.get("key"),
        product=product,
        flaky=[x.get("id") for x in flaky_rows(r)],
    )
    (ev / "manifest.json").write_text(json.dumps(man, indent=1))
    return ev


def set_commit(r, commit: str | None) -> None:
    """The delivered commit in the evidence's manifest (after the branch is written)."""
    p = _unwrap(r).root / DIR / "evidence" / "manifest.json"
    if p.is_file() and commit:
        m = json.loads(p.read_text())
        m["commit"] = commit
        p.write_text(json.dumps(m, indent=1))


def evidence_tarball(ev: Path, dest: Path, name: str) -> Path:
    """``<name>-checks.tar.gz`` of the evidence folder, its entries under ``<name>-checks/``
    (reproducible: sorted, no owner, a fixed mtime). Regular files and folders only: a link,
    a pipe or a device is left out, never followed."""
    import tarfile

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    def norm(ti):
        if not (ti.isreg() or ti.isdir()):
            return None
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        ti.mtime = 0
        return ti

    with tarfile.open(dest, "w:gz") as t:
        for p in sorted(Path(ev).rglob("*")):
            if p.is_symlink() or not (p.is_file() or p.is_dir()):
                continue
            t.add(
                p,
                arcname=f"{name}-checks/{p.relative_to(ev).as_posix()}",
                recursive=False,
                filter=norm,
            )
    return dest


LEGACY_README = """# The checks behind {product}

An autonomous software build made this product and checked it before the release. These are
the checks as that build delivered them, before they moved out of the repository: its
end-to-end tests (`exam/tests/acceptance/`), the readiness scorecard, the security review and
the open questions.

- `exam/tests/acceptance/`: one end-to-end check per behaviour, as delivered (the repository
  keeps its own copy as the product's tests).
- `results.json`: the build's last record of each check.
- `readiness.md`, `security.md`, `assumptions.md`: the reports, as delivered.

Run the checks against a checkout (they need pytest): copy `exam/tests/acceptance/` into it and
run `python -m pytest tests/acceptance` from its root.
"""


def legacy_evidence(round_dir: Path, product: str) -> Path | None:
    """The checks asset of a round: its evidence when it has one (EX-PLAN §4.4); for a round
    from before the exam moved out, one made from what it delivered into the tree
    (``<round>/exam/evidence-legacy/``: the acceptance suite, the three reports, the build's
    record). None when the round delivered nothing."""
    rd = Path(round_dir)
    ev = rd / DIR / "evidence"
    if (ev / "results.json").is_file():
        return ev
    art = rd / "deliver"
    if not art.is_dir():
        return None
    out = rd / DIR / "evidence-legacy"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    acc = art / "tests" / "acceptance"
    if acc.is_dir():
        links.copytree(
            acc,
            out / "exam" / "tests" / "acceptance",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    for src, dst in (
        ("READINESS.md", "readiness.md"),
        ("SECURITY.md", "security.md"),
        ("ASSUMPTIONS.md", "assumptions.md"),
    ):
        if (art / src).is_file():
            links.copy_file(art / src, out / dst)
    try:
        st = json.loads((rd / "state.json").read_text())
    except (OSError, ValueError):
        st = {}
    from ..loop.recheck import built_by

    (out / "results.json").write_text(
        json.dumps(
            {
                "scenarios": {k: {"status": v} for k, v in (st.get("status") or {}).items()},
                "note": "the build's last record of each check (before the exam moved out)",
            },
            indent=1,
        )
    )
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "factory_version": built_by(rd),
                "round": rd.name,
                "product": product,
                "layout": "inside",
            },
            indent=1,
        )
    )
    (out / "README.md").write_text(LEGACY_README.format(product=product or "this product"))
    return out


# -- what earlier versions wrote into a tree (§8.1) ------------------------------------------------
ACC_SECTION = re.compile(
    r"\n*## Acceptance tests\n\nEnd-to-end checks of the product's behaviour "
    r"are in `tests/acceptance/`.*?(?=\n## |\Z)",
    re.S,
)


def legacy_files(root: Path) -> list[str]:
    """The files repo-factory itself wrote into a delivered tree before the exam moved out: the
    acceptance suite (``tests/acceptance/`` with its ``_helpers/``), READINESS.md,
    ASSUMPTIONS.md and the review SECURITY.md, recognised by their own words."""
    root = Path(root)
    out = []
    acc = root / "tests" / "acceptance"
    conf = acc / "conftest.py"
    if (
        (acc / "_helpers").is_dir()
        and conf.is_file()
        and "python -m pytest tests/acceptance" in conf.read_text(errors="replace")
    ):
        out.append("tests/acceptance")
    for name in ("READINESS.md", "ASSUMPTIONS.md", "SECURITY.md"):
        p = root / name
        if not p.is_file():
            continue
        t = p.read_text(errors="replace")
        if (
            name == "READINESS.md"
            and t.startswith("# Readiness")
            and "on the way to production" in t
            or name == "ASSUMPTIONS.md"
            and t.startswith("# Assumptions")
            and "leaves these questions open" in t
            or name == "SECURITY.md"
            and t.startswith("# Security")
            and "was reviewed for security before its first release" in t
        ):
            out.append(name)
    return out


SUITE_INLINE = re.compile(r"`[^`\n]*\b(?:pytest|unittest)\b[^`\n]*tests/acceptance[^`\n]*`")


def suite_commands_out(text: str) -> str:
    """README text without the command lines that run ``tests/acceptance`` (in a fenced or
    indented code block, or a prose line with the command in backticks): the suite left with
    repo-factory's files, so a command for it would fail."""
    out, fence = [], False
    for ln in text.splitlines(keepends=True):
        if ln.lstrip().startswith(("```", "~~~")):
            fence = not fence
        elif (
            "tests/acceptance" in ln
            and (fence or ln.startswith(("    ", "\t")))
            or not fence
            and SUITE_INLINE.search(ln)
        ):
            continue
        out.append(ln)
    return "".join(out)


def strip_legacy(root: Path) -> list[str]:
    """Remove ``legacy_files``, the README section repo-factory added for the suite and the README's
    command lines that run it. Returns what was removed."""
    root = Path(root)
    gone = legacy_files(root)
    for rel in gone:
        p = root / rel
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    for p in sorted(root.glob("README*")):
        if p.is_file():
            t = p.read_text(errors="replace")
            new = ACC_SECTION.sub("", t)
            if new != t:
                gone.append(f"{p.name} (its acceptance-tests section)")
            if "tests/acceptance" in gone:
                cut = suite_commands_out(new)
                if cut != new:
                    gone.append(f"{p.name} (its commands for the acceptance suite)")
                    new = cut
            if new != t:
                p.write_text(new.rstrip() + "\n")
    return gone


# -- dependencies (the install phase, §5.1) ------------------------------------------------------
def install_deps(run, tree: Path) -> dict:
    """What the product needs installed beyond its Python venv: Node packages through the
    index proxy (``nodeinstall.install``), cached per lockfile, and the wheels a user's
    ``pip install .`` builds with (``pyinstall.install``). ``{"dirs": {name: host dir},
    "node": status, "wheels": host dir or None, "python": status}``."""
    from . import nodeinstall, pyinstall

    cfg = settings(run)
    out = nodeinstall.install(_unwrap(run), Path(tree), cfg)
    out.update(pyinstall.install(_unwrap(run), Path(tree), cfg))
    return out

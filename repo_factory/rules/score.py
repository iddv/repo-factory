"""Running rules against a candidate and scoring the result.

``run_rules`` runs a set of rules (visible and held alike: this is the factory's own run,
never a workspace) on a candidate's code under the solver sandbox and returns one
outcome per rule. ``summarize`` turns outcomes into the rule pass rate and its failing
rules; ``regressions`` compares two outcomes; ``gate`` is the milestone gate's rule part.

Every candidate is scored against *all* active rules accumulated so far: earlier
milestones' rules act as regression tests and interaction rules apply as soon as every
milestone they touch is built (``RuleStore.select(due=...)``)."""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from pathlib import Path

from .model import Rule
from .tree import broken_reason

RULES_DIR = "_factory_rules"
RUN_TIMEOUT_S = 600


def _files_key(rules: list[Rule]) -> str:
    return hashlib.sha256(
        json.dumps(sorted((r.id, r.version, r.digest()) for r in rules)).encode()
    ).hexdigest()[:16]


def run_rules(
    code_dir: Path | None,
    rules: list[Rule],
    venv: Path,
    reverse: bool = False,
    cache: Path | None = None,
    digest: str | None = None,
    timeout_s: int = RUN_TIMEOUT_S,
    overlays: dict | None = None,
    network: bool | None = None,
    rules_dir: str = RULES_DIR,
) -> dict[str, dict]:
    """Rule id -> {"status": passed|failed|error, "detail": failure text, "broken": reason
    or None}. ``reverse`` runs the files in reverse order (the determinism check runs both
    orders, so a rule that depends on another rule's leftovers shows up as flaky).
    ``cache``/``digest``: cache by (artifact, rule set, order). ``overlays`` (build v5: the
    verifier's ``_verify/driver.py``): more ``run_pytest`` overlays, relative dest -> source,
    copied next to the rule files; not cached. ``network``: as ``run_pytest``.
    ``rules_dir``: where the rule files go, relative to the code (build v5 runs scenarios
    where the verifier wrote them, ``_verify/scenarios``, so a scenario that finds the repo
    from its own path, e.g. to start the server, finds it in the harness's run too); it is
    laid down after the overlays."""
    from ..harness.venv import run_pytest

    if not rules:
        return {}
    path = None
    if cache is not None and digest and not overlays:
        path = Path(cache) / f"{digest}-{_files_key(rules)}{'-rev' if reverse else ''}.json"
        if path.exists():
            return json.loads(path.read_text())
    with tempfile.TemporaryDirectory(prefix="factory-rules-") as tmp:
        d = Path(tmp) / "rules"
        d.mkdir()
        for r in rules:
            (d / r.test_file).write_text(r.code.rstrip() + "\n")
        files = [f"{rules_dir}/{r.test_file}" for r in rules]
        targets = list(reversed(files)) if reverse else [rules_dir]
        res = run_pytest(
            code_dir,
            {"acceptance": None, **(overlays or {}), rules_dir: d},
            targets,
            venv,
            timeout_s=timeout_s,
            network=network,
        )
    out = outcomes(res, rules, rules_dir)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(out, sort_keys=True))
        tmp.replace(path)
    return out


def outcomes(res: dict, rules: list[Rule], rules_dir: str = RULES_DIR) -> dict[str, dict]:
    """Per-rule outcome from a ``run_pytest`` result."""
    details = res.get("details") or {}
    errors = set(res.get("errors") or [])
    out = {}
    for r in rules:
        f = f"{rules_dir}/{r.test_file}"
        if f in errors or res.get("returncode") == -1 and not res.get("tests"):
            text = details.get(f, "timeout" if res.get("returncode") == -1 else "")
            out[r.id] = {
                "status": "error",
                "detail": text[-1500:],
                "broken": broken_reason(text, r.test_file)
                or ("collection error in the rule" if _own_syntax(text, r) else None),
            }
            continue
        mine = {t: s for t, s in (res.get("tests") or {}).items() if t.startswith(f + "::")}
        if not mine:
            out[r.id] = {
                "status": "error",
                "detail": "no test collected",
                "broken": "no test collected",
            }
            continue
        bad = {t: s for t, s in mine.items() if s not in ("passed",)}
        if not bad:
            out[r.id] = {"status": "passed", "detail": "", "broken": None}
            continue
        if all(s == "skipped" for s in bad.values()):
            out[r.id] = {"status": "failed", "detail": "skipped", "broken": "the rule skips"}
            continue
        t = next(iter(sorted(bad)))
        text = details.get(t, "")
        out[r.id] = {
            "status": "failed" if bad[t] == "failed" else "error",
            "detail": text[-1500:],
            "broken": broken_reason(text, r.test_file),
        }
    return out


def _own_syntax(text: str, r: Rule) -> bool:
    return "SyntaxError" in text and r.test_file in text


def summarize(results: dict[str, dict], rules: list[Rule]) -> dict:
    """{"passed", "total", "rate", "failing": [ids]} over ``rules`` (missing: failed)."""
    ids = [r.id for r in rules]
    passed = [i for i in ids if (results.get(i) or {}).get("status") == "passed"]
    return {
        "passed": len(passed),
        "total": len(ids),
        "rate": round(len(passed) / len(ids), 6) if ids else None,
        "failing": [i for i in ids if i not in passed],
    }


def regressions(before: dict[str, dict] | None, after: dict[str, dict]) -> list[str]:
    """Rules that passed in ``before`` and do not pass in ``after`` (rules only in one of
    the two are not regressions)."""
    if not before:
        return []
    return sorted(
        i
        for i, v in before.items()
        if v.get("status") == "passed" and i in after and after[i].get("status") != "passed"
    )


def gate(
    results: dict[str, dict],
    rules: list[Rule],
    milestone: str,
    parent: dict[str, dict] | None,
    min_rate: float,
    fix_items=(),
) -> dict:
    """The rule part of a milestone gate: the rules touching ``milestone`` pass at
    ``min_rate`` or better (none: passes), and no rule that passed on the parent fails now.
    ``rules``: the due active rules (visible and held: the gate is the factory's).
    ``fix_items``: rule ids the run was asked to fix (late rules from background mining
    that failed on the code it started from); they count as part of its slice and must
    all pass."""
    fix = set(fix_items)
    mine = [r for r in rules if milestone in r.milestones or r.id in fix]
    s = summarize(results, mine)
    reg = regressions(parent, {k: v for k, v in results.items() if k in {r.id for r in rules}})
    fix_failing = [i for i in s["failing"] if i in fix]
    ok = (s["rate"] is None or s["rate"] >= min_rate) and not reg and not fix_failing
    whole = summarize(results, rules)
    out = {
        "passed": ok,
        "slice": [s["passed"], s["total"]],
        "rate": s["rate"],
        "failing": s["failing"],
        "regressions": reg,
        "all": [whole["passed"], whole["total"]],
    }
    if fix:
        out["fix_items"] = sorted(fix & {r.id for r in rules})
        out["fix_failing"] = fix_failing
    return out


def failing_md(rules: list[Rule], results: dict[str, dict], limit: int = 2500) -> str:
    """Failing visible rules as prompt text: title, source, expectation, failure tail."""
    L = []
    for r in rules:
        v = results.get(r.id) or {}
        if v.get("status") == "passed":
            continue
        tail = (v.get("detail") or "").strip()[-limit // max(1, len(rules)) :]
        L.append(
            f"### {r.id}: {r.title}\nSpec: {r.source}\nExpected: {r.expect}\n"
            f"Rule file: acceptance/rules/{r.test_file}\n"
            + (f"Failure:\n```\n{tail}\n```" if tail else "")
        )
    return "\n\n".join(L)

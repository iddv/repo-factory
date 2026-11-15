"""The post-mortem (build v5 §5.3): one no-tools call per scored v5 run on a **dev** pack.

Input: the spec; each failing hidden row as ``{row id, title, class, driver-call trace,
expected vs observed}``; the run's ``claims.json`` and ``findings.json``; the current lessons.
Output: one ``miss_type`` per failure from a closed set, and at most three new lessons, each
passing the lint, which enter the candidate file as ``probation``.

The model's answer is checked, not trusted:
- every failing row gets exactly one entry; a row the model left out is ``no_claim`` with
  ``defaulted: true``;
- a ``miss_type`` the evidence can't support is coerced (``coerced_from``). Without
  ``claims.json`` and ``findings.json`` nothing was claimed or probed, so every failure is
  ``no_claim``; without findings nothing was found, retired, passed or repaired;
- ``existing_lesson`` must name a live lesson, and then no new lesson is taken;
- new lessons: one per failure, three per run, lint-clean, else dropped (``dropped``);
- ``summary`` is recounted from the failures."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import lint as lint_mod
from . import store

MISS_TYPES = (
    "no_claim",
    "claim_wrong_expectation",
    "retired_by_oracle",
    "passed_wrongly",
    "found_not_fixed",
    "repair_regression",
    "interpretation",
)
NEEDS_CLAIMS = ("claim_wrong_expectation", "interpretation")
NEEDS_FINDINGS = ("retired_by_oracle", "passed_wrongly", "found_not_fixed", "repair_regression")
MAX_NEW = 3
TRACE_CALLS = 20  # driver calls shown before a row's check
TRACE_LINE_CHARS = 400
PROMPT = "postmortem.md"
ABSENT = (
    "(absent: this run has no {what}. Nothing was claimed or probed, so every failure "
    "is `no_claim`.)"
)


class PostmortemError(Exception):
    pass


# -- inputs -----------------------------------------------------------------------------------
def failing_rows(
    score: dict, pack, traces: Path | None = None, scores: tuple[str, ...] = ("core", "interp")
) -> list[dict]:
    """The failing rows that count in ``scores`` (core: settled; interp: ruled rows whose
    reading the owner chose), in manifest order, each with its trace excerpt."""
    out = []
    for r in pack.manifest:
        where = pack.scored_as(r)
        if where not in scores or score["rows"].get(r["id"]) != "fail":
            continue
        label = r["id"].split("/", 1)[1]
        out.append(
            {
                "row": r["id"],
                "title": title_of(r),
                "class": r["class"],
                "scored_as": where,
                "expected": r.get("quote") or "",
                "reading": r.get("reading") if r["class"] == "ruled" else None,
                "observed": _observed(score, r["id"]),
                "trace": trace_excerpt(Path(traces) / f"{r['scenario']}.jsonl", label)
                if traces
                else [],
            }
        )
    return out


def title_of(row: dict) -> str:
    if row.get("title"):
        return str(row["title"])
    origin = str(row.get("origin") or "")
    if ": " in origin:
        return origin.split(": ", 1)[1]
    return row["id"].split("/", 1)[1].replace("_", " ")


def _observed(score: dict, rid: str) -> str:
    bits = []
    ev = (score.get("evidence") or {}).get(rid)
    if ev not in (None, ""):
        bits.append(f"evidence {ev}")
    why = (score.get("why") or {}).get(rid)
    if why:
        bits.append(why)
    return "; ".join(bits) or "the check failed"


def trace_excerpt(path: Path, label: str, n: int = TRACE_CALLS) -> list[str]:
    """The last ``n`` driver calls before the row's check (the whole tail if the row never
    reported), one compact line each."""
    if not path.is_file():
        return []
    recs = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    end = next((i for i, r in enumerate(recs) if r.get("check") == label), len(recs))
    calls = [r for r in recs[:end] if "op" in r][-n:]
    lines = [_call_line(r) for r in calls]
    if end < len(recs):
        c = recs[end]
        lines.append(
            f"CHECK {label}: ok={c.get('ok')} evidence={json.dumps(c.get('evidence'), default=str)}"
        )
    return [x[:TRACE_LINE_CHARS] for x in lines]


def _call_line(r: dict) -> str:
    args = ", ".join(
        [json.dumps(a, default=str) for a in r.get("args") or []]
        + [f"{k}={json.dumps(v, default=str)}" for k, v in (r.get("kwargs") or {}).items()]
    )
    res = r.get("result")
    if isinstance(res, dict) and "ok" in res:
        shown = {k: res.get(k) for k in ("ok", "status", "body", "error")}
        if not res.get("body") and res.get("raw") is not None:
            shown["raw"] = res["raw"]
        res = shown
    for k in ("unsupported", "exception"):
        if k in r and "result" not in r:
            res = {"raised": f"{k}: {r[k]}"}
    return f"{r['op']}({args}) -> {json.dumps(res, default=str)}"


def render_failures(rows: list[dict]) -> str:
    parts = []
    for r in rows:
        head = [
            f"### {r['row']}",
            f"title: {r['title']}",
            f"class: {r['class']} (scored in {r['scored_as']})"
            + (f", the owner's ruling: the {r['reading']} reading" if r.get("reading") else ""),
            f"expected: {r['expected']}",
            f"observed: {r['observed']}",
            "trace:",
        ]
        parts.append("\n".join(head + [f"  {x}" for x in r["trace"] or ["(no trace)"]]))
    return "\n\n".join(parts)


def build_prompt(
    template: str,
    *,
    spec: str,
    rows: list[dict],
    claims: dict | None,
    findings: dict | None,
    lessons: list[dict],
    terms: dict[str, list[str]],
) -> str:
    fill = {
        "spec": spec.strip(),
        "failures": render_failures(rows),
        "claims": json.dumps(claims, indent=1)
        if claims is not None
        else ABSENT.format(what="claims.json"),
        "findings": json.dumps(findings, indent=1)
        if findings is not None
        else ABSENT.format(what="findings.json"),
        "lessons": "\n".join(
            f"- {x['id']} [{x['status']}, {x.get('applies_to')}]: {x['text']}" for x in lessons
        )
        or "(none yet)",
    }
    text = template
    for k, v in fill.items():
        text = text.replace(f"<<{k}>>", v)
    text += (
        "\n\n=== Words a lesson must not contain ===\nThese are the domain terms of every "
        "spec pack (any inflection counts). A lesson using one is dropped:\n"
        + ", ".join(sorted(terms))
        + "\n"
        "Also: no digits, no identifiers or file names, no snake_case, no backticks.\n"
    )
    return text


# -- the call ---------------------------------------------------------------------------------
class ClaudeCall:
    """``claude -p`` with no tools through the cost ledger (caller ``postmortem``, category
    ``other``). ``usd`` accumulates, and ``tokens`` (``{"in", "out"}``, None until a call
    reports usage)."""

    def __init__(self, model: str | None = None, timeout_s: int = 600):
        self.model, self.timeout_s, self.usd, self.data = model, timeout_s, 0.0, None
        self.tokens: dict | None = None

    def __call__(self, prompt: str) -> str:
        from ..costs.ledger import call_claude, usage_of

        text, data = call_claude(
            prompt, self.model, self.timeout_s, caller="postmortem", purpose="postmortem"
        )
        self.usd += float(data.get("total_cost_usd") or 0.0)
        if data.get("usage"):
            u = usage_of(data)
            t = self.tokens or {"in": 0, "out": 0}
            self.tokens = {"in": t["in"] + u["in_tokens"], "out": t["out"] + u["out_tokens"]}
        self.data = data
        return text


def parse_json(text: str) -> dict:
    try:
        return json.loads(text[text.index("{") : text.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError) as e:
        raise PostmortemError(f"the post-mortem reply is not JSON: {text[:300]!r}") from e


# -- checking the answer ----------------------------------------------------------------------
def allowed_types(claims: dict | None, findings: dict | None) -> tuple[str, ...]:
    return tuple(
        t
        for t in MISS_TYPES
        if t == "no_claim"
        or (t in NEEDS_CLAIMS and claims is not None)
        or (t in NEEDS_FINDINGS and findings is not None)
    )


def check(
    reply: dict,
    rows: list[dict],
    *,
    claims: dict | None,
    findings: dict | None,
    lessons: list[dict],
    terms: dict[str, list[str]],
    hidden: list[str],
) -> dict:
    allowed = allowed_types(claims, findings)
    live_ids = {x["id"] for x in lessons if x.get("status") in store.LIVE}
    by_row = {}
    for f in reply.get("failures") or []:
        if (
            isinstance(f, dict)
            and f.get("row") in {r["row"] for r in rows}
            and f["row"] not in by_row
        ):
            by_row[f["row"]] = f
    failures, dropped, taken = [], [], []
    existing_texts = [x["text"] for x in lessons]
    for r in rows:
        f = by_row.get(r["row"])
        e = {"row": r["row"], "class": r["class"], "scored_as": r["scored_as"]}
        if f is None:
            e.update(
                miss_type="no_claim",
                existing_lesson=None,
                new_lesson=None,
                policy_note="not classified by the post-mortem",
                defaulted=True,
            )
            failures.append(e)
            continue
        mt = f.get("miss_type")
        if mt not in allowed:
            e["coerced_from"] = mt
            mt = "no_claim"
        e["miss_type"] = mt
        ex = f.get("existing_lesson")
        if ex and ex not in live_ids:
            e["unknown_existing_lesson"] = ex
            ex = None
        e["existing_lesson"] = ex or None
        e["policy_note"] = str(f.get("policy_note") or "")
        e["new_lesson"] = None
        nl = f.get("new_lesson")
        if isinstance(nl, dict) and nl.get("text"):
            text, at = str(nl["text"]).strip(), nl.get("applies_to") or "claims"
            probs = []
            if ex:
                probs.append(f"an existing lesson ({ex}) should have caught it")
            if at not in store.APPLIES_TO:
                probs.append(f"applies_to {at!r} not in {store.APPLIES_TO}")
            if len(taken) >= MAX_NEW:
                probs.append(f"over the limit of {MAX_NEW} new lessons per run")
            probs += lint_mod.lint(
                text,
                terms=terms,
                hidden=hidden,
                existing=existing_texts + [t["text"] for t in taken],
            )
            if probs:
                dropped.append({"row": r["row"], "text": text, "applies_to": at, "problems": probs})
            else:
                e["new_lesson"] = {"text": text, "applies_to": at}
                taken.append(e["new_lesson"])
        failures.append(e)
    summary = {t: sum(1 for f in failures if f["miss_type"] == t) for t in MISS_TYPES}
    return {
        "failures": failures,
        "summary": summary,
        "dropped": dropped,
        "model_summary": reply.get("summary"),
    }


def hidden_texts(rows: list[dict], pack) -> list[str]:
    """What a lesson may not quote: row ids, titles and quotes of every hidden row of the pack."""
    out = []
    for r in pack.manifest:
        out += [r["id"], title_of(r), str(r.get("quote") or "")]
    return out + [r["title"] for r in rows]


# -- the whole post-mortem --------------------------------------------------------------------
def run(
    score: dict,
    pack,
    *,
    traces: Path | None,
    claims: dict | None = None,
    findings: dict | None = None,
    lessons_path: Path | None = None,
    llm=None,
    model: str | None = None,
    policy: Path | None = None,
    run_ref: str | None = None,
    terms: dict[str, list[str]] | None = None,
) -> dict:
    """Post-mortem of one scored run. ``llm(prompt) -> text`` (default: ``claude -p`` on
    ``model``); returns the checked result (``postmortem.json``'s content)."""
    if pack.split == "heldout":
        raise PostmortemError(
            f"{pack.id} is a held-out pack: post-mortems run on dev packs only (§5.3)"
        )
    rows = failing_rows(score, pack, traces)
    terms = lint_mod.domain_terms() if terms is None else terms
    lessons = store.load(lessons_path)["lessons"]
    base = {
        "pack": pack.id,
        "pack_version": pack.version,
        "run": run_ref,
        "date": date.today().isoformat(),
        "inputs": {
            "claims": claims is not None,
            "findings": findings is not None,
            "rows": len(rows),
            "lessons_version": store.load(lessons_path)["version"],
        },
    }
    unknown = sorted(set(score.get("rows") or {}) - {r["id"] for r in pack.manifest})
    if unknown:
        raise PostmortemError(
            f"{len(unknown)} scored rows are not in {pack.id} v{pack.version}'s "
            f"manifest (e.g. {unknown[0]}): re-score on this pack version"
        )
    if score.get("pack_version") not in (None, pack.version):
        base["inputs"]["pack_version_note"] = (
            f"scored on v{score['pack_version']}, read with v{pack.version}'s manifest (same rows)"
        )
    if claims is None and findings is None:
        base["inputs"]["note"] = (
            "no claims.json or findings.json: nothing was claimed or "
            "probed, so every failure is no_claim input"
        )
    if not rows:
        return {
            **base,
            "failures": [],
            "summary": {t: 0 for t in MISS_TYPES},
            "dropped": [],
            "usd": 0.0,
            "model": model,
        }
    from ..custom_prompts import policy_text

    template = policy_text(Path(policy or store.POLICY), Path(PROMPT).stem)
    prompt = build_prompt(
        template,
        spec=(pack.root / "spec.md").read_text(),
        rows=rows,
        claims=claims,
        findings=findings,
        lessons=[x for x in lessons if x.get("status") in store.LIVE],
        terms=terms,
    )
    llm = llm or ClaudeCall(model)
    usd0 = float(getattr(llm, "usd", 0.0) or 0.0)
    reply = parse_json(llm(prompt))
    out = check(
        reply,
        rows,
        claims=claims,
        findings=findings,
        lessons=lessons,
        terms=terms,
        hidden=hidden_texts(rows, pack),
    )
    return {
        **base,
        **out,
        "usd": round(float(getattr(llm, "usd", 0.0) or 0.0) - usd0, 4),
        "model": model,
        "prompt_chars": len(prompt),
    }


def apply(
    pm: dict, data: dict, *, terms: dict[str, list[str]] | None = None, today: str | None = None
) -> list[dict]:
    """Add the post-mortem's new lessons to the candidate ``data`` as ``probation``; returns
    the lessons added. Re-linted against the candidate (duplicates). A run already applied is
    skipped (``postmortems`` in the file lists them)."""
    done = data.setdefault("postmortems", [])
    if pm.get("run") and pm["run"] in done:
        return []
    added = []
    for f in pm["failures"]:
        nl = f.get("new_lesson")
        if not nl:
            continue
        if lint_mod.lint(nl["text"], terms=terms, existing=[x["text"] for x in data["lessons"]]):
            continue
        x, _ = store.add(
            data,
            nl["text"],
            nl["applies_to"],
            {"pack": pm["pack"], "run": pm.get("run"), "row": f["row"]},
            today,
        )
        added.append(x)
    if pm.get("run"):
        done.append(pm["run"])
    return added


def locate(run_dir: Path, name: str) -> Path | None:
    """``claims.json`` / ``findings.json`` / ``score.json`` of a run: at the top of the arm
    dir, else the newest one below it (the v5 pipeline writes them under ``den/v5/r<N>/``)."""
    run_dir = Path(run_dir)
    if (run_dir / name).is_file():
        return run_dir / name
    found = sorted(run_dir.rglob(name), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


def read_json(path: Path | None) -> dict | None:
    return json.loads(Path(path).read_text()) if path and Path(path).is_file() else None

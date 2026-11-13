"""``repo-factory bench history [--html]`` (build v5 §5.8): what the owner sees.

Writes ``$REPO_FACTORY_BENCH/history.jsonl`` (one row per scored run or snapshot, rebuilt from
``runs/``), ``HISTORY.md`` and, with ``--html``, ``history.html``; ``repo-factory ui`` serves the same
page at ``/history``. Sections:

1. Headline numbers for the drift worry: the share of promoted changes whose author is a
   machine (``CHANGELOG.md`` entries' ``author:`` lines), and days since the last human edit
   under ``policies/v5/`` (git commits without the ``Policy-Author: lessons-loop`` trailer).
2. Policy table: one row per policy version and split: mean core failures, with vanilla and
   matched self-review on the same base builds, cost as a multiple of vanilla, the sign-flip p
   against matched review, and the calibration error.
3. Lessons table. 4. Miss-type histogram per policy version. 5. Cumulative spend.

The history row is §5.8's, plus ``src`` (the run's path under the bench, the row's key),
``round``, ``usd_arm`` and ``measurement_usd``."""

from __future__ import annotations

import html
import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from .. import gitcmd
from . import store
from .postmortem import MISS_TYPES

BASELINES = ("vanilla", "self-review", "fresh-review")
MATCH_SLACK = 1.05
MACHINE_AUTHORS = ("lessons-loop",)
TRAILER = "Policy-Author: lessons-loop"
FIELDS = (
    "ts",
    "tag",
    "pack",
    "split",
    "seed",
    "arm",
    "policy",
    "lessons_version",
    "core_failed",
    "core_total",
    "interp_passed",
    "usd",
    "wall_s",
    "p_hidden",
    "profile_hash",
)


def bench_root() -> Path:
    from .pack import bench_root as root

    return root()


def _read(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text()) if p.is_file() else None
    except (OSError, json.JSONDecodeError):
        return None


def _split(pack: str, cache: dict) -> str | None:
    if pack not in cache:
        from .pack import Pack, PackError

        try:
            cache[pack] = Pack.load(pack).split
        except PackError:
            cache[pack] = None
    return cache[pack]


def _num(x) -> float | None:
    if isinstance(x, dict):
        x = x.get("total")
    return float(x) if isinstance(x, (int, float)) else None


def policy_label(arm: str, run: dict) -> str | None:
    """``v4``, ``v5.0``, ``v5.0+L3``; None for the baselines."""
    base = arm.split("@")[0]
    if base in BASELINES:
        return None
    pol = str(run.get("policy") or re.sub(r"-L\d+$", "", base))
    lv = run.get("lessons_version")
    if lv is None:
        m = re.search(r"-L(\d+)$", base)
        lv = int(m.group(1)) if m else None
    return f"{pol}+L{lv}" if lv else pol


# -- rows -------------------------------------------------------------------------------------
def collect(bench: Path | None = None) -> list[dict]:
    """One row per scored run (or review snapshot) under ``runs/``."""
    bench = Path(bench or bench_root())
    runs, rows, splits = bench / "runs", [], {}
    if not runs.is_dir():
        return rows
    for tag_d in sorted(p for p in runs.iterdir() if p.is_dir()):
        for pack_d in sorted(p for p in tag_d.iterdir() if p.is_dir()):
            for unit in sorted(
                p for p in pack_d.iterdir() if p.is_dir() and re.fullmatch(r"s\d+", p.name)
            ):
                base = _read(unit / "base" / "run.json") or {}
                for arm_d in sorted(p for p in unit.iterdir() if p.is_dir() and p.name != "base"):
                    snaps = sorted(
                        (
                            p
                            for p in arm_d.iterdir()
                            if p.is_dir() and re.fullmatch(r"r\d+", p.name)
                        ),
                        key=lambda p: int(p.name[1:]),
                    ) or [arm_d]
                    for d in snaps:
                        sc = _read(d / "score.json")
                        if sc is None:
                            continue
                        run = _read(d / "run.json") or (base if arm_d.name == "vanilla" else {})
                        rows.append(
                            row(
                                bench,
                                tag_d.name,
                                pack_d.name,
                                unit,
                                arm_d.name,
                                d,
                                sc,
                                run,
                                base,
                                _split(pack_d.name, splits),
                            )
                        )
    return rows


def row(bench, tag, pack, unit, arm, d, sc, run, base, split) -> dict:
    rnd = int(d.name[1:]) if re.fullmatch(r"r\d+", d.name) else None
    usd = run.get("usd") or {}
    wall = run.get("wall_s") or {}
    # p_hidden: an old run's prediction.json, kept as data (AU1 #5: no round writes one now)
    pred = (
        _read(d / "prediction.json")
        or next((_read(p) for p in sorted(d.glob("den/**/prediction.json"))), None)
        or {}
    )
    label = f"{arm}@r{rnd}" if rnd is not None else arm
    return {
        "ts": sc.get("scored_at"),
        "tag": tag,
        "pack": pack,
        "split": split,
        "seed": int(unit.name[1:]),
        "arm": label,
        "policy": policy_label(arm, run),
        "lessons_version": run.get("lessons_version"),
        "core_failed": int(sc["core"]["total"]) - int(sc["core"]["passed"]),
        "core_total": int(sc["core"]["total"]),
        "interp_passed": (sc.get("interp") or {}).get("passed"),
        "usd": _num(usd),
        "wall_s": (
            round(sum(v for v in wall.values() if isinstance(v, (int, float))), 1)
            if isinstance(wall, dict)
            else wall
        ),
        "p_hidden": pred.get("p_hidden"),
        "profile_hash": (run.get("profile") or {}).get("hash"),
        "src": str(d.relative_to(bench)),
        "round": rnd,
        "usd_arm": usd.get("arm")
        if isinstance(usd, dict)
        else _num(usd),  # a number: its own build
        "measurement_usd": sc.get("measurement_usd") or 0.0,
    }


def write_jsonl(bench: Path, rows: list[dict]) -> Path:
    """Rewrite ``history.jsonl`` from ``rows``, keeping earlier rows whose run is gone from
    ``runs/`` (or that were added by hand, without ``src``)."""
    p = bench / "history.jsonl"
    have = {r["src"] for r in rows}
    keep = []
    if p.is_file():
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not r.get("src") or (r["src"] not in have and not (bench / r["src"]).is_dir()):
                keep.append(r)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, default=str) + "\n" for r in keep + rows))
    return p


def read_jsonl(p: Path) -> list[dict]:
    out = []
    if p.is_file():
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# -- tables -----------------------------------------------------------------------------------
def _unit(r: dict) -> tuple:
    return (r["tag"], r["pack"], r["seed"])


def matched_review(rows: list[dict], unit: tuple, usd: float | None) -> dict | None:
    """The last self-review snapshot (vanilla as round 0) costing <= 1.05 x ``usd``."""
    if usd is None:
        return None
    snaps = [r for r in rows if _unit(r) == unit and r["arm"] == "vanilla"]
    snaps += sorted(
        (r for r in rows if _unit(r) == unit and r["arm"].split("@")[0] == "self-review"),
        key=lambda r: r.get("round") or 0,
    )
    ok = [r for r in snaps if r.get("usd") is not None and r["usd"] <= MATCH_SLACK * usd]
    return ok[-1] if ok else None


def policy_table(rows: list[dict]) -> list[dict]:
    from . import stats

    out = []
    labels = sorted({r["policy"] for r in rows if r.get("policy")}, key=_version_key)
    for label in labels:
        for split in ("dev", "heldout", None):
            mine = [r for r in rows if r.get("policy") == label and r.get("split") == split]
            if not mine:
                continue
            van = {_unit(r): r for r in rows if r["arm"] == "vanilla"}
            v = [van[_unit(r)] for r in mine if _unit(r) in van]
            pairs = [(r, matched_review(rows, _unit(r), r.get("usd"))) for r in mine]
            pairs = [(r, m) for r, m in pairs if m is not None]
            mu, vu = {}, {}
            for r in mine:
                if r.get("usd") is not None and _unit(r) in van and van[_unit(r)].get("usd"):
                    mu.setdefault(r["pack"], []).append(r["usd"])
                    vu.setdefault(r["pack"], []).append(van[_unit(r)]["usd"])
            ratios = stats.cost_ratios(mu, vu)
            cal = [
                abs(r["p_hidden"] - (r["core_total"] - r["core_failed"]) / r["core_total"])
                for r in mine
                if r.get("p_hidden") is not None and r["core_total"]
            ]
            out.append(
                {
                    "policy": label,
                    "split": split or "?",
                    "runs": len(mine),
                    "core_failures": mean(r["core_failed"] for r in mine),
                    "vanilla": mean(x["core_failed"] for x in v) if v else None,
                    "review_matched": mean(m["core_failed"] for _, m in pairs) if pairs else None,
                    "cost_x_vanilla": mean(ratios.values()) if ratios else None,
                    "perm_p": stats.sign_flip_p(
                        [r["core_failed"] - m["core_failed"] for r, m in pairs]
                    )
                    if pairs
                    else None,
                    "calibration_mae": mean(cal) if cal else None,
                }
            )
    return out


def _version_key(label: str):
    nums = [int(x) for x in re.findall(r"\d+", label)]
    return nums, label


def changelog_authors(policy: Path) -> list[tuple[str, str | None]]:
    """``(entry heading, author)`` for each ``## `` entry of ``CHANGELOG.md``."""
    p = Path(policy) / "CHANGELOG.md"
    if not p.is_file():
        return []
    out, head = [], None
    author = None
    for line in p.read_text().splitlines():
        if line.startswith("## "):
            if head is not None:
                out.append((head, author))
            head, author = line[3:].strip(), None
        elif head is not None and author is None:
            m = re.match(r"^author:\s*(.+?)\s*$", line)
            if m:
                author = m.group(1)
    if head is not None:
        out.append((head, author))
    return out


def is_machine(author: str | None) -> bool:
    return bool(author) and author.split()[0] in MACHINE_AUTHORS


def last_human_edit(policy: Path) -> float | None:
    """Unix time of the newest commit touching ``policy`` without the lessons loop's trailer;
    None outside git or with no such commit. Uncommitted edits are not counted."""
    policy = Path(policy)
    if not policy.is_dir():
        return None
    try:
        p = gitcmd.run(
            ["log", "--format=%ct%x1f%B%x1e", "--", "."],
            cwd=policy,
            capture_output=True,
            text=True,
            timeout=30,
            discover=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    for rec in p.stdout.split("\x1e"):
        rec = rec.strip()
        if not rec or "\x1f" not in rec:
            continue
        ts, body = rec.split("\x1f", 1)
        if TRAILER not in body:
            return float(ts)
    return None


def headline(policy: Path, now: float | None = None) -> dict:
    ents = changelog_authors(policy)
    machine = sum(1 for _, a in ents if is_machine(a))
    t = last_human_edit(policy)
    now = time.time() if now is None else now
    return {
        "promoted_changes": len(ents),
        "machine_authored": machine,
        "machine_share": machine / len(ents) if ents else None,
        "last_human_edit": datetime.fromtimestamp(t, UTC).date().isoformat() if t else None,
        "days_since_human_edit": int((now - t) // 86400) if t else None,
    }


def lessons_rows(policy: Path) -> list[dict]:
    out = []
    for src, path in (
        ("default", store.default_path(policy)),
        ("candidate", store.candidate_path(policy)),
    ):
        if not path.is_file():
            continue
        data = store.load(path)
        for x in data["lessons"]:
            out.append(
                {
                    "file": f"{src} v{data['version']}",
                    "id": x["id"],
                    "text": x["text"],
                    "applies_to": x.get("applies_to"),
                    "born": x["born"].get("pack"),
                    "exposures": x["stats"]["exposures"],
                    "caught": x["stats"]["caught"],
                    "hit_rate": x["hit_rate"],
                    "status": x["status"],
                }
            )
    return out


def miss_histogram(bench: Path) -> dict[str, dict[str, int]]:
    """Miss types summed per policy version, from every run's ``postmortem.json``."""
    out: dict[str, dict[str, int]] = {}
    runs = bench / "runs"
    if not runs.is_dir():
        return out
    for pm_path in sorted(runs.glob("*/*/s*/*/postmortem.json")):
        pm = _read(pm_path) or {}
        arm = pm_path.parent.name
        label = policy_label(arm, _read(pm_path.parent / "run.json") or {}) or arm
        h = out.setdefault(label, {t: 0 for t in MISS_TYPES})
        for f in pm.get("failures") or []:
            if f.get("miss_type") in h:
                h[f["miss_type"]] += 1
    return out


def spend(bench: Path, rows: list[dict]) -> dict:
    """Cumulative build cost (each base once, plus each arm's own final cost), measurement
    (adapters, triage) and objective (pack building) cost, plus post-mortems."""
    build = 0.0
    runs = bench / "runs"
    for b in sorted(runs.glob("*/*/s*/base/run.json")) if runs.is_dir() else []:
        build += float(((_read(b) or {}).get("usd") or {}).get("total") or 0.0)
    last: dict[tuple, float] = {}
    for r in rows:
        if r.get("usd_arm") is not None:
            k = (_unit(r), r["arm"].split("@")[0])
            last[k] = max(last.get(k, 0.0), float(r["usd_arm"]))
    build += sum(last.values())
    measurement = sum(float(r.get("measurement_usd") or 0.0) for r in rows)
    objective = 0.0
    from .pack import packs_root

    root = packs_root()
    for prov in sorted(root.glob("*/provenance.json")) if root.is_dir() else []:
        d = _read(prov) or {}
        u = d.get("usd")
        objective += (
            float(u)
            if isinstance(u, (int, float))
            else sum(
                float(c.get("usd") or 0.0)
                for c in d.get("model_calls") or []
                if isinstance(c, dict)
            )
        )
    lessons = sum(
        float((_read(p) or {}).get("usd") or 0.0)
        for p in (runs.glob("*/*/s*/*/postmortem.json") if runs.is_dir() else [])
    )
    return {
        "build": round(build, 2),
        "measurement": round(measurement, 2),
        "objective": round(objective, 2),
        "postmortem": round(lessons, 2),
    }


# -- the page ---------------------------------------------------------------------------------
def gather(
    bench: Path | None = None,
    policy: Path | None = None,
    rows: list[dict] | None = None,
    now: float | None = None,
) -> dict:
    bench = Path(bench or bench_root())
    policy = Path(policy or store.POLICY)
    rows = collect(bench) if rows is None else rows
    return {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "bench": str(bench),
        "headline": headline(policy, now),
        "policies": policy_table(rows),
        "lessons": lessons_rows(policy),
        "misses": miss_histogram(bench),
        "spend": spend(bench, rows),
        "runs": len(rows),
    }


def _f(x, nd: int = 2, pct: bool = False) -> str:
    if x is None:
        return "–"
    if pct:
        return f"{100 * x:.0f}%"
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


def _headline_text(h: dict) -> tuple[str, str]:
    share = (
        f"{_f(h['machine_share'], pct=True)} ({h['machine_authored']} of {h['promoted_changes']})"
        if h["promoted_changes"]
        else "no promoted changes yet"
    )
    days = (
        f"{h['days_since_human_edit']} days (last {h['last_human_edit']})"
        if h["days_since_human_edit"] is not None
        else "no human edit on record"
    )
    return share, days


POLICY_COLS = (
    ("policy", "policy"),
    ("split", "split"),
    ("runs", "base builds"),
    ("core_failures", "core failures"),
    ("vanilla", "vanilla"),
    ("review_matched", "self-review@matched"),
    ("cost_x_vanilla", "cost × vanilla"),
    ("perm_p", "p vs review"),
    ("calibration_mae", "calibration MAE"),
)
LESSON_COLS = (
    ("id", "id"),
    ("text", "text"),
    ("applies_to", "for"),
    ("born", "born on"),
    ("exposures", "exposures"),
    ("caught", "caught"),
    ("hit_rate", "hit rate"),
    ("status", "status"),
    ("file", "file"),
)


def render_md(ctx: dict) -> str:
    def esc(s) -> str:
        return str(s).replace("|", "\\|").replace("\n", " ")

    share, days = _headline_text(ctx["headline"])
    L = [
        "# repo_factory history",
        "",
        f"*Generated {ctx['generated']} from `{ctx['bench']}` ({ctx['runs']} scored runs).*",
        "",
        f"- **Promoted changes authored by a machine:** {share}",
        f"- **Days since the last human edit under `policies/v5/`:** {days}",
        "",
        "## Policies",
        "",
        "Mean core failures per base build; vanilla and matched review on "
        "the same base builds. Inference is conditional on these packs (§2.7).",
        "",
    ]
    L += ["| " + " | ".join(h for _, h in POLICY_COLS) + " |", "|" + "---|" * len(POLICY_COLS)]
    for r in ctx["policies"]:
        L.append(
            "| "
            + " | ".join(esc(_f(r[k], 3 if k == "perm_p" else 2)) for k, _ in POLICY_COLS)
            + " |"
        )
    if not ctx["policies"]:
        L.append("| (no scored factory runs yet) |" + " |" * (len(POLICY_COLS) - 1))
    L += ["", "## Lessons", ""]
    L += ["| " + " | ".join(h for _, h in LESSON_COLS) + " |", "|" + "---|" * len(LESSON_COLS)]
    for r in ctx["lessons"]:
        L.append("| " + " | ".join(esc(_f(r[k])) for k, _ in LESSON_COLS) + " |")
    if not ctx["lessons"]:
        L.append("| (no lessons yet) |" + " |" * (len(LESSON_COLS) - 1))
    L += ["", "## Miss types per policy version", ""]
    if ctx["misses"]:
        L += ["| policy | " + " | ".join(MISS_TYPES) + " |", "|" + "---|" * (len(MISS_TYPES) + 1)]
        for label, h in ctx["misses"].items():
            L.append(f"| {esc(label)} | " + " | ".join(str(h[t]) for t in MISS_TYPES) + " |")
    else:
        L.append("No post-mortems yet.")
    s = ctx["spend"]
    L += [
        "",
        "## Spend (cumulative, USD)",
        "",
        "| build | measurement | objective | post-mortems |",
        "|---|---|---|---|",
        f"| {s['build']:.2f} | {s['measurement']:.2f} | {s['objective']:.2f} "  # stored-shape: sums
        f"| {s['postmortem']:.2f} |",
        "",
    ]
    return "\n".join(L)


CSS = """
:root{color-scheme:light dark;--fg:#1d1d1f;--mut:#6e6e73;--bg:#fbfbfd;--card:#fff;--line:#e5e5ea;
--acc:#0a64d6;--bar:#7aa7e6}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--fg:#f2f2f7;--mut:#a1a1a6;
--bg:#141416;--card:#1c1c1f;--line:#2c2c30;--acc:#5ea2ff;--bar:#3b6fb5}}
:root[data-theme=dark]{color-scheme:dark;--fg:#f2f2f7;--mut:#a1a1a6;--bg:#141416;--card:#1c1c1f;
--line:#2c2c30;--acc:#5ea2ff;--bar:#3b6fb5}:root[data-theme=light]{color-scheme:light}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
color:var(--fg);background:var(--bg)}main{max-width:1100px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 10px}
.sub{color:var(--mut);font-size:13px;margin:0 0 24px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.card .k{color:var(--mut);font-size:13px}.card .v{font-size:28px;font-weight:600;margin-top:4px}
.card .n{color:var(--mut);font-size:13px}
.tw{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:500;white-space:nowrap}tr:last-child td{border-bottom:0}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;border:1px solid var(--line)}
.active{color:#1a7f37}.probation{color:#9a6700}.retired{color:var(--mut)}
.bar{height:10px;background:var(--bar);border-radius:3px;min-width:2px}
.note{color:var(--mut);font-size:13px}
"""


def render_html(ctx: dict) -> str:
    e = html.escape
    share, days = _headline_text(ctx["headline"])
    h = ctx["headline"]
    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        # the appearance chosen in Settings, as every served page (QA B18: it drew in the OS's)
        '<script src="static/theme.js"></script>',
        "<title>repo_factory history</title><style>",
        CSS,
        "</style></head><body><main>",
        "<h1>repo_factory history</h1>",
        f"<p class=sub>Generated {e(ctx['generated'])} from <code>{e(ctx['bench'])}</code> "
        f"({ctx['runs']} scored runs)</p>",
        "<div class=cards>",
        "<div class=card><div class=k>Promoted changes authored by a machine</div>"
        f"<div class=v>{e(_f(h['machine_share'], pct=True))}</div><div class=n>{e(share)}</div></div>",
        "<div class=card><div class=k>Days since the last human edit under policies/v5/</div>"
        f"<div class=v>{e(_f(h['days_since_human_edit']))}</div><div class=n>{e(days)}</div></div>",
        "</div>",
        "<h2>Policies</h2><p class=note>Mean core failures per base build; vanilla and matched "
        "review on the same base builds. Inference is conditional on these packs.</p>",
        _table(
            POLICY_COLS,
            ctx["policies"],
            "no scored factory runs yet",
            nums={
                "runs",
                "core_failures",
                "vanilla",
                "review_matched",
                "cost_x_vanilla",
                "perm_p",
                "calibration_mae",
            },
        ),
        "<h2>Lessons</h2>",
        _table(
            LESSON_COLS,
            ctx["lessons"],
            "no lessons yet",
            nums={"exposures", "caught", "hit_rate"},
            pill="status",
        ),
        "<h2>Miss types per policy version</h2>",
        _misses(ctx["misses"]),
        "<h2>Spend (cumulative, USD)</h2>",
        _table(
            (
                ("build", "build"),
                ("measurement", "measurement"),
                ("objective", "objective"),
                ("postmortem", "post-mortems"),
            ),
            [ctx["spend"]],
            "",
            nums={"build", "measurement", "objective", "postmortem"},
        ),
        "</main></body></html>",
    ]
    return "\n".join(parts)


def _table(cols, rows, empty: str, nums=(), pill: str | None = None) -> str:
    e = html.escape
    out = ["<div class=tw><table><thead><tr>"] + [f"<th>{e(h)}</th>" for _, h in cols]
    out.append("</tr></thead><tbody>")
    if not rows:
        out.append(f"<tr><td colspan={len(cols)} class=note>{e(empty)}</td></tr>")
    for r in rows:
        out.append("<tr>")
        for k, _ in cols:
            v = e(_f(r.get(k), 3 if k == "perm_p" else 2))
            if k == pill:
                cls = r.get(k) if r.get(k) in store.STATUSES else ""
                out.append(f"<td><span class='pill {cls}'>{v}</span></td>")
            else:
                out.append(f"<td class=num>{v}</td>" if k in nums else f"<td>{v}</td>")
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def _misses(m: dict) -> str:
    e = html.escape
    if not m:
        return "<p class=note>No post-mortems yet.</p>"
    out = []
    for label, hist in m.items():
        top = max(hist.values()) or 1
        out.append(
            f"<h3 style='font-size:15px;margin:16px 0 6px'>{e(label)}</h3><div class=tw><table><tbody>"
        )
        for t in MISS_TYPES:
            w = int(100 * hist[t] / top)
            out.append(
                f"<tr><td style='width:220px'>{e(t)}</td><td class=num style='width:50px'>{hist[t]}</td>"
                f"<td><div class=bar style='width:{w}%'></div></td></tr>"
            )
        out.append("</tbody></table></div>")
    return "".join(out)


def write(bench: Path | None = None, policy: Path | None = None, html_out: bool = False) -> dict:
    bench = Path(bench or bench_root())
    rows = collect(bench)
    paths = {"jsonl": write_jsonl(bench, rows)}
    ctx = gather(bench, policy, rows)
    paths["md"] = bench / "HISTORY.md"
    paths["md"].write_text(render_md(ctx))
    if html_out:
        paths["html"] = bench / "history.html"
        paths["html"].write_text(render_html(ctx))
    return {"ctx": ctx, "paths": paths}


def page(bench: Path | None = None, policy: Path | None = None) -> str:
    """The ``/history`` page, rendered fresh (reads only; writes nothing)."""
    return render_html(gather(bench, policy))


# -- CLI hook (hsuite/cli.HOOKS) ---------------------------------------------------------------
def add_bench_commands(bsub) -> None:
    h = bsub.add_parser(
        "history",
        help="every tag over time: policies, lessons, miss types, "
        "spend (writes history.jsonl and HISTORY.md)",
    )
    h.add_argument("--html", action="store_true", help="also write history.html")
    h.add_argument("--policy", default=None, help="default: policies/v5")
    h.add_argument("--json", action="store_true")
    h.set_defaults(fn=cmd_history)


def cmd_history(args) -> None:
    res = write(policy=Path(args.policy) if args.policy else None, html_out=args.html)
    if args.json:
        print(json.dumps(res["ctx"], indent=1, default=str))
    else:
        print(render_md(res["ctx"]))
    import sys

    print("wrote " + ", ".join(str(p) for p in res["paths"].values()), file=sys.stderr)

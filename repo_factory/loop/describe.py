"""The product's public text, written from the product (the owner's decision, 2026-10-16).

The text a stranger reads first (the paragraph under the README's title, AGENTS.md's "what
this is", the repository description and the release notes' first line) is written **last**,
at the pre-push checks, from what was built: the staged tree's files and code, its command-line
help (run in the sandbox, ``help_text``), its tests and what the checks found (``facts``). The
pitch and the
spec describe what the product was meant to do; they are inputs for building and are never
copied into public text (``verbatim``: a sentence of theirs in the reply is dropped).

Then **one review pass** (the honesty prompt) reads the README, AGENTS.md and repo-factory's release
text for overclaims, and its replacements are applied (``review``). Wording is never a hold:
what was changed is listed on the build page and in the release notes ("repo-factory reworded 3
sentences"), and the owner edits it later like any repo. The release text the owner set
(``pitch.release.by: owner``) is used as it is: neither written nor reviewed here."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from string import Template

from .tree import readme_path

CODE_CAP, FILE_CAP, HELP_CAP, TESTS_CAP = 24000, 6000, 6000, 4000
DESC_MAX, LINE_MIN, LINE_MAX = 700, 20, 300
SKIP = {
    ".git",
    ".github",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".den",
    ".pytest_cache",
    "dist",
    "build",
}
DOCS = (
    "README",
    "SCOPE",
    "SPEC",
    "ASSUMPTIONS",
    "AGENTS",
    "CLAUDE",
    "NOTICE",
    "LICENSE",
    "SECURITY",
    "READINESS",
    "CHANGELOG",
    "CONTRIBUTING",
)
CODE_EXT = (".py", ".js", ".ts", ".go", ".rs", ".rb", ".sh", ".toml", ".json", ".cfg")


# -- what was built ----------------------------------------------------------------------------
def _files(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if (
            p.is_file()
            and not (set(rel.parts[:-1]) & SKIP)
            and not any(part.endswith(".egg-info") for part in rel.parts)
        ):
            out.append(p)
    return out


def _is_doc(p: Path) -> bool:
    return p.suffix.lower() in (".md", ".rst", ".txt", "") and p.stem.upper().startswith(DOCS)


def _is_test(rel: Path) -> bool:
    return any(x in ("tests", "test") for x in rel.parts[:-1]) or rel.name.startswith("test_")


def _rank(rel: Path) -> tuple:
    """Entry points first (a CLI's parser, ``__main__``, the package's ``__init__``), then
    the manifest, then the rest, shortest paths first."""
    n = rel.name
    first = n in (
        "__main__.py",
        "cli.py",
        "main.py",
        "app.py",
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
    )
    return (not first, n != "__init__.py", len(rel.parts), str(rel))


def code_text(root: Path, cap: int = CODE_CAP) -> str:
    """Excerpts of the product's own code, entry points first, never its documents."""
    picked, used = [], 0
    files = [
        p
        for p in _files(root)
        if p.suffix in CODE_EXT
        and not _is_doc(p)
        and not _is_test(p.relative_to(root))
        and p.name != "package-lock.json"
    ]
    for p in sorted(files, key=lambda p: _rank(p.relative_to(root))):
        if used >= cap:
            break
        body = p.read_text(errors="replace")[: min(4000, cap - used)]
        picked.append(f"--- {p.relative_to(root)}\n{body}")
        used += len(body)
    return "\n\n".join(picked) or "(no code files)"


def files_text(root: Path, cap: int = FILE_CAP) -> str:
    rows = [str(p.relative_to(root)) for p in _files(root)]
    out = "\n".join(rows)
    return (out[:cap] + "\n...") if len(out) > cap else (out or "(none)")


TEST_DEF = re.compile(r"^\s*(?:async\s+)?def (test_\w+)|^\s*(?:it|test)\(\s*['\"]([^'\"]+)", re.M)


def tests_text(root: Path, cap: int = TESTS_CAP) -> str:
    rows = []
    for p in _files(root):
        rel = p.relative_to(root)
        if not _is_test(rel) or p.suffix not in (".py", ".js", ".ts"):
            continue
        names = [a or b for a, b in TEST_DEF.findall(p.read_text(errors="replace"))]
        if names:
            rows.append(f"{rel}: " + ", ".join(names[:40]))
    out = "\n".join(rows)
    return (out[:cap] + "\n...") if len(out) > cap else (out or "(no tests found)")


SCRIPTS = re.compile(r"^\[project\.scripts\]\s*$(.*?)(?=^\[|\Z)", re.M | re.S)
SCRIPT = re.compile(r"""^\s*["']?([\w.-]+)["']?\s*=\s*["']([\w.]+):([\w.]+)["']""", re.M)
SUBS = re.compile(r"\{([a-z][\w-]*(?:,[a-z][\w-]*)+)\}")
LISTED = re.compile(r"^    ([a-z][\w-]*)(?:\s{2,}\S|$)", re.M)  # argparse's subcommand rows
HELP_S, HELP_TOTAL_S = 10, 40
CALL = (
    "import sys, importlib; sys.argv = [{name!r}] + sys.argv[1:]; "
    "sys.exit(importlib.import_module({mod!r}).{func}())"
)


def entry_points(root: Path) -> list[tuple[str, list[str]]]:
    """``[(name, interpreter args)]``: the product's commands, from ``pyproject.toml``'s
    ``[project.scripts]``, else a top-level package with ``__main__.py`` (``-m pkg``)."""
    out = []
    pp = root / "pyproject.toml"
    m = SCRIPTS.search(pp.read_text(errors="replace")) if pp.is_file() else None
    for name, mod, func in SCRIPT.findall(m.group(1)) if m else []:
        out.append((name, ["-c", CALL.format(name=name, mod=mod, func=func)]))
    if not out:
        out = [(p.parent.name, ["-m", p.parent.name]) for p in sorted(root.glob("*/__main__.py"))]
    return out[:3]


def run_help(root: Path, argv: list[str], timeout: float = HELP_S) -> str | None:
    """``python <argv> --help`` on a copy of the product, in the sandbox (no network, a
    private /tmp and home); None where bubblewrap does not work: product code never runs on
    the host here."""
    import shutil
    import tempfile

    from ..harness import links
    from ..harness.isolation import isolation, sandbox_cmd

    if isolation() != "bwrap":
        return None
    with tempfile.TemporaryDirectory(prefix="repo-factory-help-") as tmp:
        work = Path(tmp) / "product"
        links.copytree(root, work, ignore=shutil.ignore_patterns(".git", "node_modules"))
        cmd = sandbox_cmd(work, home="/tmp/home") + [*argv, "--help"]
        try:
            p = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
    out = (p.stdout or "") if p.returncode == 0 else ""
    return out.strip()[:3000] or None


def help_text(root: Path, cap: int = HELP_CAP, runner=run_help) -> str:
    """The product's command-line help: each command's ``--help``, and its first six
    subcommands' (argparse's ``{a,b,c}``, or the rows it lists under "positional arguments"),
    run in the sandbox. None runs here: the argument parser in the code stands for it
    (``code_text``)."""
    import time

    got, t0 = [], time.time()
    for name, argv in entry_points(root):
        top = runner(root, argv)
        if not top:
            continue
        got.append(f"$ {name} --help\n{top}")
        m = SUBS.search(top)
        subs = (
            m.group(1).split(",") if m else LISTED.findall(top.split("positional arguments", 1)[-1])
        )
        for sub in [x for x in subs if x != "help"][:6]:
            if time.time() - t0 > HELP_TOTAL_S:
                break
            h = runner(root, [*argv, sub])
            if h:
                got.append(f"$ {name} {sub} --help\n{h}")
    out = "\n\n".join(got)
    return out[:cap] if out else "(no help could be run here: see the argument parser in the code)"


def _json(p: Path) -> dict:
    import json

    try:
        d = json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def results_text(idea: dict, rdir: Path | None) -> str:
    """What the checks found, from the round's records: the gate's checks, the exam's run of
    the product's own tests (each documented command, its verdict and the end of its output;
    how many tests pass) and how many scenarios and definition-of-done flows pass."""
    lines = []
    for k, c in ((idea.get("gate") or {}).get("checks") or {}).items():
        lines.append(f"- {k}: {'passes' if c.get('ok') else 'fails'}: {c.get('detail')}")
    if rdir is None:
        return "\n".join(lines) or "(no records)"
    rec = _json(Path(rdir) / "exam" / "evidence" / "results.json")
    for c in ((rec.get("own") or {}).get("commands") or [])[:4]:
        tail = " ".join(str(c.get("output") or "").split()[-12:])
        lines.append(
            f"- `{c.get('cmd')}`: {c.get('verdict') or c.get('status')}"
            + (f" ({tail})" if tail else "")
        )
    tests = rec.get("tests") or {}
    if tests:
        ok = sum(1 for v in tests.values() if v == "passed")
        lines.append(f"- the product's own tests: {ok} of {len(tests)} pass")
    scen = rec.get("scenarios") or {}
    if scen:
        ok = sum(1 for v in scen.values() if (v or {}).get("status") == "passed")
        lines.append(f"- the hidden acceptance scenarios: {ok} of {len(scen)} pass")
    comp = _json(Path(rdir) / "findings.json").get("completeness") or {}
    if comp.get("total"):
        lines.append(
            f"- definition-of-done flows: {comp.get('done')} of {comp['total']} work end to end"
        )
    return "\n".join(lines) or "(no records)"


def facts(root: Path, idea: dict, rdir: Path | None, runner=run_help) -> dict:
    p = idea.get("pitch") or {}
    return {
        "PRODUCT": p.get("product") or idea.get("title") or root.name,
        "FILES": files_text(root),
        "CODE": code_text(root),
        "HELP": help_text(root, runner=runner),
        "TESTS": tests_text(root),
        "RESULTS": results_text(idea, rdir),
    }


# -- never the pitch's words ---------------------------------------------------------------------
def _words(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(s or "").lower()))


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?:;])\s+|\n+", str(text or "")) if s.strip()]


def pitch_sentences(idea: dict, task: Path | None = None) -> list[str]:
    """The sentences public text must never copy: the pitch's (its one-liner, brief, who and
    why) and the spec's, five words or more each."""
    p = idea.get("pitch") or {}
    src = [p.get(k) or "" for k in ("one_liner", "brief", "who", "why_now")]
    if task is not None:
        for n in ("spec.md",):
            f = Path(task) / n
            if f.is_file():
                src.append(f.read_text(errors="replace")[:20000])
    out = []
    for s in src:
        out += [w for w in (_words(x) for x in split_sentences(s)) if len(w.split()) >= 5]
    return list(dict.fromkeys(out))


def verbatim(text: str, banned: list[str]) -> list[str]:
    """The sentences of ``text`` that carry one of ``banned`` word for word."""
    return [s for s in split_sentences(text) if any(b and b in _words(s) for b in banned)]


def scrub(text: str, banned: list[str]) -> str:
    bad = set(verbatim(text, banned))
    return " ".join(s for s in split_sentences(text) if s not in bad).strip()


# -- the writer ------------------------------------------------------------------------------
def _line(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def check(res, banned: list[str]) -> list[str]:
    """What is wrong with a reply (empty: usable)."""
    if not isinstance(res, dict):
        return ["the reply had no JSON object"]
    bad = []
    d, r = _line(res.get("description"), 10_000), _line(res.get("release_text"), 10_000)
    if not 20 <= len(d) <= DESC_MAX:
        bad.append(f"description must be 20 to {DESC_MAX} characters")
    if not LINE_MIN <= len(r) <= LINE_MAX:
        bad.append(f"release_text must be {LINE_MIN} to {LINE_MAX} characters")
    for k in ("description", "does", "release_text"):
        for s in verbatim(res.get(k) or "", banned):
            bad.append(f'{k} copies the brief: "{s}"; describe the product from its code')
    return bad


def write(
    root: Path,
    idea: dict,
    model,
    rdir: Path | None = None,
    task: Path | None = None,
    runner=run_help,
) -> dict:
    """The product's description, written from ``facts``: ``{"ok", "description", "does",
    "release_text", "usd", "problems", "prompt"}``. One call, asked once more when the reply
    breaks the contract; a sentence still copied from the pitch is dropped."""
    from ..custom_prompts import effective
    from .pitch import parse

    eff = effective("describe")
    body = Template(eff.text).safe_substitute(**facts(root, idea, rdir, runner))
    banned = pitch_sentences(idea, task)
    usd, problems, res = 0.0, [], None
    for attempt in range(2):
        text, cost = _call(
            model,
            body
            if not attempt
            else body + "\n\nYour previous reply "
            "broke the contract: "
            + "; ".join(problems)
            + ". Reply again with the whole JSON object.",
        )
        usd += cost or 0.0
        res = parse(text)
        problems = check(res, banned)
        if not problems:
            break
    meta = {"id": "describe", "version": eff.version, "n": eff.n}
    if not isinstance(res, dict):
        return {"ok": False, "usd": round(usd, 4), "problems": problems, "prompt": meta}
    out = {
        k: scrub(_line(res.get(k), n), banned)
        for k, n in (("description", DESC_MAX), ("does", LINE_MAX), ("release_text", LINE_MAX))
    }
    ok = len(out["description"]) >= 20 and LINE_MIN <= len(out["release_text"]) <= LINE_MAX
    return {
        "ok": ok,
        **out,
        "usd": round(usd, 4),
        "problems": [] if ok else problems,
        "prompt": meta,
    }


def _call(model, prompt: str) -> tuple[str, float]:
    r = model(prompt)
    return (r[0], r[1]) if isinstance(r, tuple) else (str(r), 0.0)


# -- putting it in place ---------------------------------------------------------------------
def _lead(lines: list[str]) -> tuple[int, int]:
    """The description under a title: the first paragraph after it, ending at a blank line, a
    heading, a code fence, a table, a list or a badge. ``(start, end)`` line indexes;
    start == end: there is none (the description goes there)."""
    at = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), None)
    start = 0 if at is None else at + 1
    while start < len(lines) and not lines[start].strip():
        start += 1
    end = start
    while (
        end < len(lines)
        and lines[end].strip()
        and not lines[end]
        .lstrip()
        .startswith(("#", "```", "~~~", "|", "- ", "* ", "<", "1. ", "![", "[!", ">"))
    ):
        end += 1
    return start, end


def _replace_lead(lines: list[str], text: str) -> list[str]:
    start, end = _lead(lines)
    tail = lines[end:]
    while tail and not tail[0].strip():
        tail = tail[1:]
    return lines[:start] + [text.strip(), ""] + tail


def put_readme(path: Path, text: str) -> str | None:
    """The README's description replaced by ``text``; returns what it said before."""
    lines = (path.read_text(errors="replace") if path.exists() else "").split("\n")
    start, end = _lead(lines)
    old = "\n".join(lines[start:end]).strip()
    path.write_text("\n".join(_replace_lead(lines, text)).rstrip() + "\n")
    return old or None


DOES = re.compile(r"^(- \*\*Does:\*\*)\s*(.*)$", re.M)


def put_agents(path: Path, old_lead: str | None, description: str, does: str) -> dict:
    """AGENTS.md's "what this is": its ``**Does:**`` line, and the paragraph under its title
    when it repeats the README's old description. Returns ``{"does", "lead"}``: what each
    said before (None: not there)."""
    if not path.is_file():
        return {}
    text = path.read_text(errors="replace")
    was: dict = {}
    m = DOES.search(text)
    if m and does:
        was["does"] = m.group(2)
        text = text[: m.start()] + f"{m.group(1)} {does}" + text[m.end() :]
    lines = text.split("\n")
    start, end = _lead(lines)
    lead = "\n".join(lines[start:end]).strip()
    if lead and old_lead and _words(lead) == _words(old_lead):
        was["lead"] = lead
        text = "\n".join(_replace_lead(lines, " ".join(description.split())))
    path.write_text(text.rstrip() + "\n")
    return was


def apply(root: Path, idea: dict, written: dict) -> dict:
    """``write``'s text put in place in the staged tree. ``{"readme", "agents"}``: what each
    said before. The release text is the caller's (``release``)."""
    if not written.get("ok"):
        return {}
    readme = readme_path(root)
    old = put_readme(readme, written["description"])
    agents = put_agents(root / "AGENTS.md", old, written["description"], written.get("does"))
    return {"readme": old, "agents": agents}


def release(idea: dict, written: dict | None) -> tuple[str, str]:
    """``(text, by)``: the owner's release text as it is; else the one written from the
    product; else the earlier one (repo-factory's rewording or the pitch's) when nothing was
    written."""
    from . import claims as cl

    rel = (idea.get("pitch") or {}).get("release") or {}
    if isinstance(rel, dict) and rel.get("by") == "owner" and rel.get("text"):
        return cl.release_text(idea), "owner"
    if written and written.get("ok") and written.get("release_text"):
        return written["release_text"], "product"
    return cl.release_text(idea), "pitch"


# -- the one review pass ---------------------------------------------------------------------
def review(
    root: Path, model, release_text: str | None, product: str, jargon: list[dict] | None = None
) -> dict:
    """One pass of the honesty prompt over README, AGENTS.md and (when repo-factory wrote it) the
    release text; every replacement whose quote is found is applied. Never a hold.
    ``{"reworded": [{"file", "was", "now", "why"}], "release_text", "noted": [...], "usd",
    "prompt"}``: ``noted``, what it raised that could not be applied (no quote found)."""
    from ..custom_prompts import effective
    from . import claims as cl
    from .pitch import parse

    eff = effective("honesty")
    readme = readme_path(root)
    parts = []
    for p, cap in ((readme, 16000), (root / "AGENTS.md", 12000)):
        if p.exists():
            parts.append(f"--- {p.name}\n{p.read_text(errors='replace')[:cap]}")
    if release_text:
        parts.append(
            f"--- {cl.RELEASE_FILE}\n"
            + cl.release_block(release_text, cl.release_notes(product, release_text))
        )
    words = [
        h for h in jargon or [] if str(h.get("file") or "").upper().startswith(("README", "AGENTS"))
    ]
    if words:
        parts.append(
            "--- BUILD WORDS\nThese lines carry the build's own words (its ids or "
            "tools); replace each with plain words:\n"
            + "\n".join(
                f"- {h.get('file')} line {h.get('line')}: {h.get('kind')}" for h in words[:20]
            )
        )
    text, cost = _call(model, Template(eff.text).safe_substitute(TEXT="\n\n".join(parts)))
    res = parse(text)
    meta = {"id": "honesty", "version": eff.version, "n": eff.n}
    out = {
        "reworded": [],
        "release_text": release_text,
        "noted": [],
        "usd": round(cost or 0, 4),
        "prompt": meta,
        "usable": res is not None,
    }
    for i in (res or {}).get("issues") or []:
        if not isinstance(i, dict) or not i.get("quote"):
            continue
        q, new = str(i["quote"]), str(i.get("replacement") or "")
        f = str(i.get("file") or "").upper()
        row = {"file": i.get("file"), "was": q, "now": new, "why": i.get("problem")}
        if f.startswith(cl.RELEASE_FILE):
            if not release_text:  # the owner's: never shown, never touched
                continue
            cur = out["release_text"] or ""
            if release_text and q in cur:
                fixed = " ".join(cur.replace(q, new).split())
                if LINE_MIN <= len(fixed) <= LINE_MAX:
                    out["release_text"] = fixed
                    out["reworded"].append({**row, "file": cl.RELEASE_FILE})
                    continue
            out["noted"].append(row)
            continue
        if f.startswith(("NOTICE", "CLAIMS", "LICENSE")):
            continue
        # the named file first, then the other: the description written from the product
        # stands in both, and a sentence fixed in one is fixed wherever it is (Sidedoor's
        # first publish kept AGENTS.md's copy of a sentence the review fixed in the README)
        named = root / "AGENTS.md" if f.startswith("AGENTS") else readme
        done = []
        for target in dict.fromkeys((named, readme, root / "AGENTS.md")):
            body = target.read_text(errors="replace") if target.is_file() else ""
            if q in body:
                target.write_text(body.replace(q, new))
                done.append(target.name)
        if done:
            out["reworded"].append({**row, "file": " and ".join(done)})
        else:
            out["noted"].append(row)
    return out


def notes_line(reworded: list[dict]) -> str | None:
    """The release notes' disclosure of what the review changed (none: None)."""
    if not reworded:
        return None
    n = len(reworded)
    shown = "; ".join(
        f'{r["file"]}: "{_line(r["was"], 120)}" → "{_line(r["now"], 120)}"' for r in reworded[:5]
    )
    more = f"; and {n - 5} more" if n > 5 else ""
    return (
        f"repo-factory reworded {n} sentence{'s' if n != 1 else ''} before this release: "
        f"{shown}{more}."
    )


def run(
    root: Path,
    idea: dict,
    model,
    rdir: Path | None,
    task: Path | None,
    product: str,
    jargon: list[dict] | None = None,
    runner=run_help,
) -> dict:
    """``write``, ``apply``, the release text, then ``review``: the record the pre-push checks
    keep as ``described``. Nothing here ever holds a push."""
    if model is None:
        text, by = release(idea, None)
        return {
            "written": None,
            "was": {},
            "release_text": text,
            "release_by": by,
            "reworded": [],
            "noted": [],
            "usd": 0.0,
        }
    w = write(root, idea, model, rdir, task, runner)
    was = apply(root, idea, w)
    text, by = release(idea, w)
    rv = review(root, model, text if by != "owner" else None, product, jargon)
    if by != "owner":
        text = rv["release_text"] or text
    return {
        "written": {k: w.get(k) for k in ("ok", "description", "does", "release_text", "problems")},
        "was": was,
        "release_text": text,
        "release_by": by,
        "reworded": rv["reworded"],
        "noted": rv["noted"][:10],
        "usd": round(w["usd"] + rv["usd"], 4),
        "prompts": [w["prompt"], rv["prompt"]],
    }

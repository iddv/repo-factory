"""AGENTS.md (MAINTAIN.md §3): render it from a product's own documents, and report drift.

``values_from_tree(root)`` reads the values without a model: product, one-liner and audience
from the README's *At a glance*, commands from its install, tests and command sections, the
flow numbers from ``SCOPE.md`` (never its sentences: it is the pitch's spec), open items from ``tests/acceptance/README.md``, settings from
``ASSUMPTIONS.md``, and the architecture rows from the README's layout list and each module's
docstring (a ``describe`` callable fills any module that has neither). ``render(values)`` fills
the template; ``check(root)`` is the drift doctor: commands that the README does not have,
architecture rows that point nowhere, modules the table does not name, self-hosted runners."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .. import gitcmd
from . import render as _render

TEMPLATE = "AGENTS.md.tmpl"
# who runs the *Setup, run, test* fence: CI, when repo-factory's ci.yml is in the repo (a backfill,
# ``maintenance_files: all``); else repo-factory itself, before each fix (a product's own CI)
COMMANDS_RUN_BY_CI = (
    "These commands are run by CI exactly as written (`.github/workflows/ci.yml`, job `agents-md`)."
)
COMMANDS_RUN_BY_FACTORY = (
    "repo-factory runs these commands exactly as written before it proposes a fix."
)
# repo-factory's own command for the acceptance suite: AGENTS.md names it, the README need not
ACCEPTANCE_RUN = "python -m pytest tests/acceptance"
# "Does:" in a rendered AGENTS.md: a pointer, never a sentence of SCOPE.md's; at publish the
# public text written from the product replaces it (loop/describe.py, put_agents)
DOES = "what the README's description says (README.md)."
NO_TEST = "# none found: the README and CI name no unit test command; add it to both"
SKIP_DIRS = {"tests", "test", "docs", "data", "scripts", ".github", ".git", "__pycache__"}


# -- reading the product's documents -------------------------------------------------------------
def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def sections(text: str, level: int = 2) -> list[tuple[str, str]]:
    """``[(heading, body)]`` for every heading of exactly ``level``."""
    mark = "#" * level + " "
    out, head, buf = [], None, []
    for ln in text.splitlines():
        if ln.startswith(mark):
            if head is not None:
                out.append((head, "\n".join(buf)))
            head, buf = ln[len(mark) :].strip(), []
        elif head is not None:
            if re.match(r"^#{1,%d} " % (level - 1), ln) if level > 1 else False:
                out.append((head, "\n".join(buf)))
                head, buf = None, []
                continue
            buf.append(ln)
    if head is not None:
        out.append((head, "\n".join(buf)))
    return out


def fences(text: str) -> list[list[str]]:
    """The lines of every fenced code block (``` or an indented block is not counted)."""
    return [
        [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
        for m in re.finditer(r"^\s*```[a-z]*\s*$(.*?)^\s*```", text, re.M | re.S)
    ]


def _strip_comment(cmd: str) -> str:
    return re.split(r"\s+#\s", cmd, maxsplit=1)[0].strip()


def sentences(text: str) -> list[str]:
    """Paragraphs and bullets joined across wrapped lines, then split into sentences, with the
    Markdown emphasis and bullet marks removed."""
    paras, cur = [], []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith(("#", "|", "```")) or re.match(r"^[-*] ", s):
            if cur:
                paras.append(" ".join(cur))
            cur = [re.sub(r"^[-*] ", "", s)] if re.match(r"^[-*] ", s) else []
            continue
        cur.append(s)
    if cur:
        paras.append(" ".join(cur))
    out = []
    for p in paras:
        p = re.sub(r"^\*\*[^*]{1,40}:\*\*\s*|^\*\*[^*]{1,40}\*\*:\s*", "", p).replace("**", "")
        p = p[:1].upper() + p[1:]
        out += [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z`])", p) if x.strip()]
    return out


def glance(readme: str, label: str) -> str | None:
    m = re.search(r"^\s*-\s*\*\*" + re.escape(label) + r":?\*\*:?\s*(.+)$", readme, re.M)
    return m.group(1).strip() if m else None


def first_paragraph(text: str) -> str:
    for block in re.split(r"\n\s*\n", text):
        b = block.strip()
        if b and not b.startswith(("#", "|", "-", "```", ">")):
            return " ".join(b.split())
    return ""


def launcher(readme: str) -> str | None:
    """The install-and-start command: the first fenced line under an *Install* or *Start*
    heading, after any ``git clone … && cd …``."""
    heads = [
        (h, b)
        for h, b in sections(readme)
        if re.search(r"\b(install|start|run)\b", h, re.I) and not re.search(r"test", h, re.I)
    ]
    for head, body in heads:
        for lines in fences(body):
            for ln in lines:
                if ln.startswith("#"):
                    continue
                part = _strip_comment(ln.split("&&")[-1])
                if part and not part.startswith(("git ", "cd ")):
                    return part
    # no fence: an inline command in the section's prose ("Start it with `./bin/x start`.")
    for head, body in heads:
        for s in sentences(body):
            for span in re.findall(r"`([^`\n]+)`", s):
                part = _strip_comment(span.split("&&")[-1]).rstrip(" .;,")
                if RUNNERS.match(part + " ") and not re.search(r"\btests?\b|pip install", part):
                    return part
    return None


# The first word of a command a README gives inline ("Run the tests: `python3 -m unittest …`").
RUNNERS = re.compile(r"^(?:\./\S+|bin/\S+|python3?|make|sh|bash|uv|tox|nox|npm|node|go|cargo)\s")
# What AGENTS.md's unit-test line must not be: the acceptance suite (pytest, its own line).
NOT_UNIT = re.compile(r"\bpytest\b|acceptance")


def _inline_tests(readme: str, start: str | None) -> list[str]:
    """Inline code spans that run the product's tests: in a sentence about tests, or naming
    ``unittest`` itself, in the README's order."""
    out = []
    for s in sentences(readme):
        about = re.search(r"\btests?\b", s, re.I) and not re.search(r"acceptance", s, re.I)
        for span in re.findall(r"`([^`\n]+)`", s):
            cmd = _strip_comment(span).rstrip(" .;,")
            if len(cmd.split()) < 2 or NOT_UNIT.search(cmd):
                continue
            if not (RUNNERS.match(cmd + " ") or (start and cmd.startswith(start + " "))):
                continue
            if about or "unittest" in cmd:
                out.append(cmd)
    return out


def ci_test_command(root: Path | None) -> str | None:
    """The unit-test line of the repository's own CI (``run:`` in ``.github/workflows/``),
    when the README names none."""
    wf = Path(root) / ".github" / "workflows" if root is not None else None
    for f in sorted(wf.glob("*.y*ml")) if wf is not None and wf.is_dir() else []:
        for m in re.finditer(r"^\s*(?:-\s+)?run:\s*(.*)$((?:\n\s{6,}\S.*)*)", _read(f), re.M):
            lines = [m.group(1)] if m.group(1).strip() not in ("|", ">", "") else []
            lines += [x.strip() for x in m.group(2).splitlines() if x.strip()]
            for ln in lines:
                cmd = _strip_comment(ln.strip().strip("'\""))
                if (
                    re.search(r"\bunittest\b|\btest\b", cmd)
                    and not NOT_UNIT.search(cmd)
                    and "check_agents_md" not in cmd
                    and not cmd.startswith(("#", "echo "))
                ):
                    return cmd
    return None


def unit_test_command(readme: str, start: str | None, root: Path | None = None) -> str | None:
    """The product's own test command, exactly as its documents give it: a fenced line under
    a *Tests* heading, an inline command in a sentence about tests, ``<launcher> test``, else
    the repository's CI (``root``). None when none of them names one: a default is never
    invented (the Beliefline case, 2026-10-13: ``python -m unittest`` ran 0 tests)."""
    for head, body in sections(readme):
        if re.search(r"\btests?\b", head, re.I) and not re.search(r"acceptance", head, re.I):
            for lines in fences(body):
                for ln in lines:
                    if not ln.startswith("#") and "pytest" not in ln:
                        return _strip_comment(ln)
    inline = _inline_tests(readme, start)
    if inline:
        return inline[0]
    if start and re.search(re.escape(start) + r" test\b", readme):
        return f"{start} test"
    return ci_test_command(root)


def find_command(readme: str, start: str | None, word: str) -> str | None:
    """The first ``<launcher> <word> …`` in a code span or a fenced line."""
    if not start:
        return None
    pat = re.compile(re.escape(start) + r" " + word + r"\b[^`\n]*")
    spans = re.findall(r"`([^`\n]+)`", readme) + [ln for f in fences(readme) for ln in f]
    for s in spans:
        m = pat.search(s)
        if m:
            return _strip_comment(m.group(0)).rstrip(" .;,")
    return None


def flows(scope: str) -> list[tuple[str, str]]:
    return [
        (m.group(1), m.group(2).strip())
        for m in re.finditer(r"^#{2,4}\s+(F\d+)[:.]?\s+(.+)$", scope, re.M)
    ]


def open_items(acceptance_readme: str) -> list[dict]:
    body = ""
    for head, b in sections(acceptance_readme):
        if "open item" in head.lower():
            body = b
    return [
        {"file": m.group(1), "why": m.group(2).strip()}
        for m in re.finditer(r"^\s*-\s*`([^`]+\.py)`:?\s*(.*)$", body, re.M)
    ]


def _table_rows(text: str) -> list[list[str]]:
    rows = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("|") and not re.match(r"^\|[\s:|-]+\|$", s):
            rows.append([c.strip() for c in s.strip("|").split("|")])
    return rows[1:] if rows else []


def operator_settings(assumptions: str, service: bool = True) -> str:
    """``service``: a server has an in-app Settings page; a command-line product's settings
    are its config file, flags and environment."""
    live, later = [], []
    for head, body in sections(assumptions):
        if head.lower() != "settings":
            continue
        parts = re.split(r"^### +(.+)$", body, flags=re.M)
        live = _table_rows(parts[0])
        for i in range(1, len(parts), 2):
            rows = _table_rows(parts[i + 1])
            (later if re.search(r"not (available|in)", parts[i], re.I) else live).extend(rows)
    lines = []
    if live:
        where = (
            "Set by an Admin on the in-app Settings page; each has an environment variable "
            "that wins when set"
            if service
            else "Set in the config file, a flag or an environment variable"
        )
        lines += [
            f"{where} (README › Configuration). `ASSUMPTIONS.md` says why each exists.",
            "",
            "| Setting | Values | Default |",
            "|---|---|---|",
        ]
        lines += [f"| {r[0]} | {r[1]} | {r[2]} |" for r in live if len(r) >= 3]
    if later:
        if lines:
            lines.append("")
        lines.append(
            "Planned as settings but **not in this version** (the product behaves one "
            "fixed way; see `ASSUMPTIONS.md`): " + ", ".join(r[0] for r in later if r) + "."
        )
    if not lines:
        lines.append("None yet. A new one follows the operator-settings convention above.")
    return "\n".join(lines)


def docstring(p: Path) -> str | None:
    try:
        doc = ast.get_docstring(ast.parse(_read(p)))
    except (SyntaxError, ValueError):
        return None
    if not doc:
        return None
    first = " ".join(doc.strip().split("\n\n")[0].split())
    return first.rstrip(".") + "." if first else None


def defines(p: Path) -> str | None:
    """A module described by what it defines, with no model: its public top-level classes and
    functions (``Defines `Store`, `open_db`.``), else None. Never a placeholder the doctor
    then refuses (AU3 finding 3)."""
    try:
        tree = ast.parse(_read(p))
    except (SyntaxError, ValueError):
        return None
    names = [
        n.name
        for n in tree.body
        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith("_")
    ]
    if not names:
        return None
    more = f" and {len(names) - 4} more" if len(names) > 4 else ""
    return "Defines " + ", ".join(f"`{n}`" for n in names[:4]) + more + "."


def layout_notes(readme: str) -> dict[str, str]:
    """``{path: description}`` from the README's *Layout* section (a bullet list of
    ```path`: text`` or a code block of ``path   text``)."""
    out = {}
    for head, body in sections(readme):
        if not re.search(r"layout|structure|architecture", head, re.I):
            continue
        for m in re.finditer(r"^\s*-\s*`([^`]+)`\s*[:—-]\s*(.+)$", body, re.M):
            out[m.group(1).rstrip("/")] = m.group(2).strip()
        for lines in fences(body):
            for ln in lines:
                m = re.match(r"^(\S+)\s{2,}(.+)$", ln)
                if m:
                    out[m.group(1).rstrip("/")] = m.group(2).strip()
    return out


def module_paths(root: Path) -> list[str]:
    """What the architecture table must name: top-level scripts and the modules of every
    top-level package (``__init__.py`` is the package itself and is left out)."""
    out = []
    for p in sorted(root.iterdir()):
        if p.name.startswith(".") or p.name in SKIP_DIRS:
            continue
        if p.is_file() and p.suffix in (".py", ".sh"):
            out.append(p.name)
        elif p.is_dir() and (p / "__init__.py").exists():
            out += [f"{p.name}/{m.name}" for m in sorted(p.glob("*.py")) if m.name != "__init__.py"]
    return out


def _sentence(text: str | None) -> str | None:
    t = " ".join((text or "").split()).rstrip(".")
    return (t[:1].upper() + t[1:] + ".") if t else None


def architecture(
    root: Path,
    readme: str,
    describe=None,
    start: str | None = None,
    subcommands: list[str] | None = None,
    service: bool = True,
) -> list[tuple[str, str]]:
    """``service``: the product is a server on 127.0.0.1 (its README names the address); a
    command-line product's launcher installs or runs it instead of starting a service."""
    notes = layout_notes(readme)
    script = (start or "").split()[-1].lstrip("./") if start else ""
    rows, missing = [], []
    for rel in module_paths(root):
        if rel == script:
            text = (
                f"The launcher the README uses: `{start}` "
                + ("starts the service" if service else "is its first command")
                + (
                    f"; {', '.join(f'`{c}`' for c in subcommands)} are its other commands"
                    if subcommands
                    else ""
                )
                + "."
            )
        elif rel.endswith("/__main__.py") and not docstring(root / rel):
            # what a package's __main__ is for is fixed by Python itself
            text = f"Runs the package as a command: `python -m {rel.split('/')[0]}`."
        else:
            text = (docstring(root / rel) if rel.endswith(".py") else None) or _sentence(
                notes.get(rel)
            )
        rows.append([rel, text])
        if not text:
            missing.append(rel)
    if missing and describe is not None:
        got = describe({rel: _read(root / rel).splitlines()[:40] for rel in missing}) or {}
        for r in rows:
            if not r[1] and got.get(r[0]):
                r[1] = " ".join(str(got[r[0]]).split())
    for r in rows:
        if not r[1] and r[0].endswith(".py"):
            r[1] = defines(root / r[0]) or f"The `{Path(r[0]).stem}` module."
    tests = root / "tests"
    for p in sorted(tests.glob("test_*.py")) if tests.is_dir() else []:
        rows.append(
            [f"tests/{p.name}", docstring(p) or "Unit tests (the product's own test command)."]
        )
    if (tests / "acceptance").is_dir():
        rows.append(
            [
                "tests/acceptance/",
                "The acceptance suite: one file per behaviour, through "
                "the real interface (pytest).",
            ]
        )
        if (tests / "acceptance" / "_helpers").is_dir():
            rows.append(
                [
                    "tests/acceptance/_helpers/",
                    "Drivers the acceptance tests share ("
                    + (
                        "start the service, sign in, submit forms)."
                        if service
                        else "run the command with its arguments, read what it prints and writes)."
                    ),
                ]
            )
    return [(a, b or "(describe this module)") for a, b in rows]


DATA_RULES = [
    r"append-only",
    r"can't be changed|cannot be changed|never (updated|edited)",
    r"never deleted",
    r"not deleted",
]
RULE_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "can",
    "cannot",
    "changed",
    "deleted",
    "edited",
    "in",
    "is",
    "never",
    "not",
    "of",
    "or",
    "t",
    "the",
    "to",
    "updated",
}


def data_rules(candidates: list[str], n: int = 2) -> list[str]:
    """Up to ``n`` sentences that say what is never changed or deleted, the strongest
    wording first, skipping one that mostly repeats another."""

    def words(s):
        return set(re.findall(r"\w+", s.lower())) - RULE_WORDS

    picked: list[str] = []
    for pat in DATA_RULES:
        for s in candidates:
            if (
                len(picked) >= n
                or not re.search(pat, s, re.I)
                or s in picked
                or not s.endswith(".")
                or re.match(r"^[A-Z]\d+:", s)
            ):
                continue
            w = words(s)
            if any(len(w & words(p)) > 0.6 * max(len(w), 1) for p in picked):
                continue
            picked.append(s)
    return picked


def _git(root: Path, *args: str) -> str:
    p = gitcmd.run(args, cwd=root, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ""


def repo_slug(root: Path) -> str | None:
    url = _git(root, "remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None


def values_from_tree(root: Path, repo: str | None = None, describe=None) -> dict:
    """Every value the templates need, from the tree as it is (no model unless ``describe``
    is given and a module has neither a README note nor a docstring)."""
    root = Path(root)
    readme = _read(root / "README.md")
    scope = _read(root / "SCOPE.md")
    title = re.search(r"^# (.+)$", readme, re.M)
    product = title.group(1).strip() if title else root.name
    one = glance(readme, "What it is") or first_paragraph(readme) or product
    who = glance(readme, "Who it's for") or "see SCOPE.md › Users"
    start = launcher(readme)
    test_cmd = unit_test_command(readme, start, root)
    pymin = re.search(r"Python (3\.\d+)", readme)
    fl = flows(scope)
    port = re.search(r"127\.0\.0\.1:(\d+)", readme)
    port_env = re.search(r"\b([A-Z][A-Z0-9]*_PORT)\b", readme) or re.search(
        r"\b([A-Z][A-Z0-9_]*_PORT)\b", readme
    )
    conf = sorted(root.glob("*.conf.example"))
    data_dir = "./data"
    for ln in readme.splitlines():
        if "DATA_DIR" in ln:
            m = re.search(r"`(\./[^`\s]+)`", ln)
            if m:
                data_dir = m.group(1)
                break
    # what the product says of itself, never SCOPE.md's: it was written from the pitch before
    # the build (AU2 #1: Sidedoor's AGENTS.md said what SCOPE.md meant, not what was built)
    append_only = " ".join(data_rules(sentences(readme))) or (
        "SCOPE.md's domain rules say which records may never be changed or deleted."
    )
    notice = _read(root / "NOTICE")
    notice_summary = (
        "Generated by an autonomous software build from a short product brief, "
        "with automated checks (its tests, a walk of every documented flow, a "
        "security review and a readiness review) before release."
        if "autonomous" in notice.lower()
        else ""
    )
    tag = _git(root, "describe", "--tags", "--abbrev=0")
    date = (_git(root, "log", "-1", "--format=%cs", tag) if tag else "") or _git(
        root, "log", "--reverse", "--format=%cs"
    ).split("\n")[0]
    sub = {w: find_command(readme, start, w) for w in ("demo", "backup", "restore")}
    subcommands = [c for c in [test_cmd, *sub.values()] if c and start and c.startswith(start)]
    # A server's README names its address; a command-line product's does not, and then no
    # line is marked `# serve` (the check would wait for an answer that never comes).
    service = port is not None
    rows = architecture(
        root, readme, describe, start, [c.split("[")[0].strip() for c in subcommands], service
    )
    install = start or "(see README › Install)"
    has_pip = any(root.glob("requirements*.txt")) or (root / "pyproject.toml").exists()
    return {
        "product": product,
        "repo": repo or repo_slug(root) or f"OWNER/{root.name}",
        "one_liner": one if one.endswith((".", "!", "?")) else one + ".",
        "who": who,
        "does": DOES,
        "python_min": pymin.group(1) if pymin else "3.12",
        "flow_range": f"{fl[0][0]}–{fl[-1][0]}" if fl else "F1–Fn",
        "flows": fl,
        "install_command": install,
        "port": port.group(1) if port else "",
        "port_note": f" (port: {port_env.group(1)})" if port_env and service else "",
        "install_line": (
            f"{install}   # serve: http://127.0.0.1:{port.group(1)}/"
            + (f" (port: {port_env.group(1)})" if port_env else "")
            if service
            else f"{install}   # install"
        ),
        "stack": (
            "standard library only, SQLite, one process on 127.0.0.1"
            if service
            else "standard library only, a command-line tool"
        ),
        "server_convention": (
            "- The server checks roles and CSRF on every state-changing "
            "request; templates escape everything.\n"
            if service
            else ""
        ),
        # none found: a comment line the check skips and the doctor reports, never a guess
        "unit_test_command": test_cmd or NO_TEST,
        # only where the suite exists (notehatch and nextread have none: AU3 finding 3)
        "acceptance_line": (
            f"\npython -m pip install pytest && {ACCEPTANCE_RUN}   # the acceptance suite"
            if (root / "tests" / "acceptance").is_dir()
            else ""
        ),
        "ci_test_command": test_cmd or 'echo "AGENTS.md names no unit test command" >&2; exit 1',
        "commands_run_by": COMMANDS_RUN_BY_CI,
        "demo_command": sub["demo"] or "see README",
        "backup_command": sub["backup"] or "see README",
        "restore_command": sub["restore"] or "see README",
        "conf_file": conf[0].name[: -len(".example")] if conf else "a config file",
        "architecture_rows": "\n".join(f"| `{a}` | {b.replace('|', '/')} |" for a, b in rows),
        "architecture": rows,
        "data_dir": data_dir,
        "migration_mechanism": (
            "the schema version is stored with the data; pending migrations run at start"
            if re.search(r"migrat", readme, re.I)
            else "see README"
        ),
        "append_only_note": append_only,
        "money_convention": (
            "- Money is integer cents; every total is computed from ledger rows, never stored.\n"
            if re.search(r"\bcents\b", readme)
            else ""
        ),
        "operator_settings": operator_settings(_read(root / "ASSUMPTIONS.md"), service),
        "settings_where": (
            "the Settings page, the README table"
            if service
            else "the config file and flags, the README table"
        ),
        "open_items": open_items(_read(root / "tests" / "acceptance" / "README.md")),
        "notice_summary": notice_summary,
        "first_version": (tag or "v0.1.0").lstrip("v"),
        "release_date": date or "unreleased",
        "pip_block": (
            "  - package-ecosystem: pip\n    directory: /\n    schedule: {interval: "
            "monthly}\n    labels: [dependency]\n    open-pull-requests-limit: 3\n"
            if has_pip
            else ""
        ),
    }


def render(values: dict) -> str:
    return _render(TEMPLATE, values)


# -- the drift doctor ------------------------------------------------------------------------------
def _checker():
    """The repo-side check (``templates/check_agents_md.py``) as a module: the doctor reads
    AGENTS.md with its readers, so the two never disagree on which heading, fence or path
    counts (the owner, 2026-10-17: nextread's "## Set up, run, test" over a bare fence was
    flagged "no sh fence"). Compiled here, never imported: no ``__pycache__`` in templates/."""
    import types

    path = Path(__file__).with_name("templates") / "check_agents_md.py"
    mod = types.ModuleType("factory_check_agents_md")
    mod.__file__ = str(path)
    exec(compile(path.read_text(), str(path), "exec"), mod.__dict__)  # noqa: S102 - repo-factory's own file
    return mod


CHECKER = _checker()
NO_FENCE = CHECKER.NO_FENCE
NO_ARCH = 'AGENTS.md has no architecture section (such as "## Architecture in brief")'


def fence_commands(agents: str) -> list[tuple[str, str]]:
    """``[(command, comment)]`` under the setup heading, as the check runs them."""
    return CHECKER.fence_lines(agents) or []


def skipped(agents: str) -> list[tuple[str, str]]:
    """``[(command, why)]``: the lines the check leaves unrun (a server, a placeholder, an
    install onto the machine)."""
    return [(c, w) for c, n in fence_commands(agents) for w in [CHECKER.skip_reason(c, n)] if w]


def table_paths(agents: str) -> list[str] | None:
    """The paths the architecture section names; None when it has none."""
    return CHECKER.arch_paths(agents)


def documented(cmd: str, readme: str, root: Path) -> bool:
    """Whether the README has ``cmd``: exactly; with fewer trailing arguments, down to its
    program and subcommand (``python3 -m unittest tests.test_format`` where the README says
    ``python3 -m unittest discover -s tests``); or it runs the repo's own package
    (``python3 -m nextread --help``)."""
    if cmd in readme:
        return True
    w = cmd.split()
    least = 3 if len(w) > 2 and w[1] == "-m" else 2
    if any(" ".join(w[:n]) in readme for n in range(len(w) - 1, least - 1, -1)):
        return True
    if least == 3 and re.match(r"^python3?$", w[0]):
        top = w[2].split(".")[0]
        return (root / top / "__main__.py").exists() or (root / f"{top}.py").exists()
    return False


def self_hosted(workflow: str) -> bool:
    """Whether a workflow's ``runs-on`` names a self-hosted runner, in any of its forms (a
    word, a list, a block list, ``{labels: …}``)."""
    if re.search(r"runs-on:.*self-hosted", workflow):
        return True
    try:
        import yaml

        doc = yaml.safe_load(workflow)
    except Exception:  # noqa: BLE001 - unparsable: the regex decided
        return False
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    for job in jobs.values() if isinstance(jobs, dict) else []:
        ro = job.get("runs-on") if isinstance(job, dict) else None
        if isinstance(ro, dict):
            ro = ro.get("labels")
        if any("self-hosted" in str(x) for x in (ro if isinstance(ro, list) else [ro])):
            return True
    return False


def check(root: Path) -> list[str]:
    """Drift between AGENTS.md and the tree, without a model and without running anything.
    ``[]`` means current."""
    root = Path(root)
    agents = _read(root / "AGENTS.md")
    if not agents:
        return ["AGENTS.md is missing"]
    readme = _read(root / "README.md")
    problems = []
    cmds = fence_commands(agents)
    if not cmds:
        problems.append(NO_FENCE)
    if NO_TEST in agents:
        problems.append(
            "AGENTS.md names no unit test command: neither the README nor CI says "
            "how to run the product's own tests"
        )
    for cmd, _ in cmds:
        for part in (p.strip() for p in cmd.split("&&")):
            if (
                part
                and "pip install" not in part
                and part != ACCEPTANCE_RUN
                and not documented(part, readme, root)
            ):
                problems.append(f"`{part}` is in AGENTS.md but not in the README")
    paths = table_paths(agents)
    if paths is None:
        problems.append(NO_ARCH)
    for p in paths or []:
        if not (root / p.rstrip("/")).exists():
            problems.append(f"the architecture section names `{p}`, which does not exist")
    named = {p.rstrip("/") for p in paths or []}
    for rel in module_paths(root) if paths is not None else []:
        # what a package's __main__ is for is fixed by Python itself (architecture() above)
        if (
            rel not in named
            and not any(rel.startswith(n + "/") for n in named)
            and not rel.endswith("/__main__.py")
        ):
            problems.append(f"`{rel}` is not in AGENTS.md's architecture section")
    if "(describe this module)" in agents:
        problems.append("AGENTS.md's architecture section still has a module without a description")
    wf = root / ".github" / "workflows"
    for f in sorted(wf.glob("*.y*ml")) if wf.is_dir() else []:
        if self_hosted(_read(f)):
            problems.append(
                f".github/workflows/{f.name} uses a self-hosted runner; product CI "
                "runs on GitHub-hosted runners only"
            )
    for name in ("CLAUDE.md", ".github/repo-factory.yml", ".github/labels.yml"):
        if not (root / name).exists():
            problems.append(f"{name} is missing")
    return problems

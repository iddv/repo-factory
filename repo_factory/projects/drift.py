"""Drift checks: facts about a project's repo that code can establish for free (PJ-PLAN §4.2).

The experiment showed a model run missing drift a plain comparison finds every time (Receipts'
pyproject said 1.0.0 while the tags stopped at v0.1.3 and the CHANGELOG at 0.1.0). So these
facts are computed here, with no model, no network and no GitHub call, and handed to the
evolve run as findings it can cite.

Each check takes the clone's root (and the inputs it needs) and returns a list of findings
``{"check", "severity", "what", "where", "fix_hint"}`` in plain words. Inputs that need a
network read (the repo's tags, the latest release of a tool) are passed in by the caller,
which caches them; ``run`` skips a check whose input is None rather than guessing it."""

from __future__ import annotations

import ast
import datetime
import json
import re
import tomllib
from importlib import resources
from pathlib import Path

import yaml

DOCS = ("README.md", "AGENTS.md", "READINESS.md", "SECURITY.md", "CONTRIBUTING.md")
PATH_EXTS = ("py", "yml", "yaml", "md", "toml", "json", "sh", "txt", "cfg", "ini", "js", "ts")
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "site-packages",
}
WORKFLOWS = ".github/workflows"
SOON_DAYS = 90


def finding(check: str, severity: str, what: str, where: str, fix_hint: str) -> dict:
    return {
        "check": check,
        "severity": severity,
        "what": what,
        "where": where,
        "fix_hint": fix_hint,
    }


def _read(root: Path, rel: str) -> str:
    p = Path(root) / rel
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    except OSError:
        return ""


def _workflows(root: Path) -> list[Path]:
    d = Path(root) / WORKFLOWS
    return sorted([*d.glob("*.yml"), *d.glob("*.yaml")]) if d.is_dir() else []


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


# ---------------------------------------------------------------- versions

VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?([-+.]?\w[\w.+-]*)?$")


def vkey(v: str) -> tuple | None:
    """A sort key for ``1.2.3`` / ``v1.2`` / ``1.0.0-rc1`` (a pre-release sorts before its
    release); None for anything that is not a version."""
    m = VERSION.match(v.strip())
    if not m:
        return None
    return (int(m[1]), int(m[2]), int(m[3] or 0), 0 if m[4] and m[4][0] == "-" else 1)


def bare(v: str) -> str:
    return v[1:] if v[:1] in "vV" else v


def newest_tag(tags: list[str]) -> str | None:
    versions = [t for t in tags if vkey(t)]
    return max(versions, key=vkey) if versions else None


def package_versions(root: Path) -> list[tuple[str, str]]:
    """``[(where, version)]`` from pyproject, a package ``__init__.__version__`` and
    package.json, in that order."""
    root = Path(root)
    out = []
    text = _read(root, "pyproject.toml")
    if text:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            data = {}
        v = data.get("project", {}).get("version") or data.get("tool", {}).get("poetry", {}).get(
            "version"
        )
        if isinstance(v, str):
            out.append(("pyproject.toml", v))
    for init in _package_inits(root):
        v, line = _dunder_version(init)
        if v:
            out.append((f"{_rel(root, init)}:{line}", v))
    text = _read(root, "package.json")
    if text:
        try:
            v = json.loads(text).get("version")
        except (ValueError, AttributeError):
            v = None
        if isinstance(v, str):
            out.append(("package.json", v))
    return out


def _package_inits(root: Path) -> list[Path]:
    cands = [*root.glob("*/__init__.py"), *root.glob("src/*/__init__.py")]
    return sorted(p for p in cands if p.parent.name not in SKIP_DIRS | {"tests", "test"})


def _dunder_version(path: Path) -> tuple[str | None, int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return None, 0
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value, node.lineno
    return None, 0


# ---------------------------------------------------------------- the changelog

HEADING = re.compile(r"^(#{1,3})\s+(.*?)\s*$")
HEAD_VERSION = re.compile(r"^\[?v?(\d+\.\d+(?:\.\d+)?(?:-[\w.]+)?)\]?")


def changelog_sections(text: str) -> list[dict]:
    """``[{"title", "version", "unreleased", "line", "items": [(line, text)]}]`` for each
    ``##``/``###`` heading of a CHANGELOG; an item is a bullet with its continuation lines."""
    sections, cur = [], None
    for n, line in enumerate(text.splitlines(), 1):
        m = HEADING.match(line)
        if m and len(m[1]) >= 2:
            title = m[2]
            v = HEAD_VERSION.match(title)
            cur = {
                "title": title,
                "version": v[1] if v else None,
                "line": n,
                "items": [],
                "unreleased": bool(re.search(r"\bunreleased\b", title, re.I)),
            }
            sections.append(cur)
            continue
        if cur is None or not line.strip():
            continue
        if re.match(r"^\s*[-*+]\s+", line):
            cur["items"].append((n, re.sub(r"^\s*[-*+]\s+", "", line).strip()))
        elif line[:1].isspace() and cur["items"]:
            ln, prev = cur["items"][-1]
            cur["items"][-1] = (ln, f"{prev} {line.strip()}")
    return sections


def _changelog(root: Path) -> tuple[str, list[dict]]:
    for name in ("CHANGELOG.md", "CHANGES.md", "HISTORY.md"):
        text = _read(root, name)
        if text:
            return name, changelog_sections(text)
    return "", []


def releases_by_factory(root: Path) -> bool:
    """``.github/repo-factory.yml`` › ``release.on_merge`` is on: repo-factory's release step cuts this repo's
    releases, and it writes the tag and nothing else (``maintain/release.py``). The tag is the
    version then, and a number stated in a file falling behind it is repo-factory's own doing, not
    drift (AU3 finding 2: 7 of 8 projects were flagged for it)."""
    try:
        doc = yaml.safe_load(_read(root, ".github/repo-factory.yml"))
    except yaml.YAMLError:
        return False
    if not isinstance(doc, dict):
        return False  # no file, or an empty one: nothing is released
    rel = doc.get("release") if isinstance(doc.get("release"), dict) else {}
    on = rel.get("on_merge", "patch")  # maintain.policy's default
    return on not in ("off", False, None)


def version_tags(root: Path, tags: list[str] | None) -> list[dict]:
    """The package version against the newest tag and the CHANGELOG's newest version heading:
    one finding naming all of them when they disagree."""
    root = Path(root)
    found = [(where, v) for where, v in package_versions(root) if vkey(v)]
    tag = newest_tag(tags or [])
    log_name, sections = _changelog(root)
    log = next((s for s in sections if s["version"]), None)
    parts = [f"{where.split(':')[0]} says {v}" for where, v in found]
    if tag:
        parts.append(f"the newest tag is {tag}")
    if log:
        parts.append(f"the newest version heading in {log_name} is {log['version']}")
    values = (
        {vkey(v) for _, v in found}
        | ({vkey(tag)} if tag else set())
        | ({vkey(log["version"])} if log else set())
    )
    values.discard(None)
    if len(values) < 2:
        return []
    pkg = found[0][1] if found else None
    hints = []
    if pkg and tag and vkey(pkg) != vkey(tag):
        hints.append(
            f"no release carries {pkg}: set the package version to {bare(tag)} "
            f"(or tag the release it was bumped for)"
            if vkey(pkg) > vkey(tag)
            else f"bump the package version to {bare(tag)} so it reports the release it came from"
        )
    if len({vkey(v) for _, v in found}) > 1:
        hints.append("make every place that states the version say the same")
    ref = tag or pkg
    if log and ref and vkey(log["version"]) < vkey(ref):
        hints.append(f"add {log_name} headings for the releases after {log['version']}")
    severity = "medium" if pkg and tag and vkey(pkg) != vkey(tag) else "low"
    what = "The version numbers disagree: " + ", ".join(parts) + "."
    where = found[0][0] if found else (f"{log_name}:{log['line']}" if log else "")
    hint = "; ".join(hints)
    return [
        finding(
            "version_tags",
            severity,
            what,
            where,
            hint[:1].upper() + hint[1:] + "."
            if hint
            else "Make the version, the tags and the CHANGELOG agree.",
        )
    ]


def _short(text: str, n: int = 100) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------- paths named in documents

CODE_SPAN = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I)
_EXT = "|".join(PATH_EXTS)
PROSE_PATH = re.compile(rf"(?<![\w/.~$@:\\-])((?:\.?[\w-]+/)+[\w.-]*\w\.(?:{_EXT}))(?![\w/])")
EXAMPLE_CUE = re.compile(r"\b(e\.g\.|for example|for instance|such as)", re.I)
NOT_A_PATH = re.compile(r"[<>{}*?\[\]$=…\s|,;\"']|\.\.\.|://")


def _candidate(token: str) -> str | None:
    """The repo-relative file a document token names, or None when it is not one: URLs,
    globs, placeholders, home or absolute paths, commands, directories."""
    t = token.strip().rstrip(".,;:)")
    t = re.sub(r":\d+(?:-\d+)?$", "", t)  # receipts/cli.py:483-488
    t = t.split("#")[0]
    if t.startswith("./"):
        t = t[2:]
    if not t or t[0] in "~/" or NOT_A_PATH.search(t) or t.endswith("/"):
        return None
    if "/" not in t and t.startswith("."):
        return None  # a bare dotfile is usually the user's own (.aider*, .netrc)
    if not re.search(rf"\.(?:{_EXT})$", t):
        return None
    return t


def _doc_tokens(text: str):
    """``(line, token)`` for each backticked token, link target and slash-path in prose,
    outside fenced code blocks."""
    fence = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence or EXAMPLE_CUE.search(line):
            continue
        for m in CODE_SPAN.finditer(line):
            yield n, m[1]
        for m in LINK.finditer(line):
            yield n, m[1]
        prose = URL.sub(" ", LINK.sub(" ", CODE_SPAN.sub(" ", line)))
        for m in PROSE_PATH.finditer(prose):
            yield n, m[1]


def _tree_names(root: Path) -> set[str]:
    names = set()
    for p in Path(root).rglob("*"):
        if not any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            names.add(p.name)
    return names


def doc_paths(root: Path) -> list[dict]:
    """Files named in README, AGENTS.md, READINESS.md, SECURITY.md and CONTRIBUTING.md that
    do not exist. A bare file name counts if it exists anywhere in the tree."""
    root = Path(root)
    names = None
    out, seen = [], set()
    for doc in DOCS:
        text = _read(root, doc)
        for n, token in _doc_tokens(text):
            path = _candidate(token)
            if not path or (doc, path) in seen:
                continue
            seen.add((doc, path))
            if "/" in path:
                if (root / path).exists():
                    continue
            else:
                names = _tree_names(root) if names is None else names
                if path in names:
                    continue
            out.append(
                finding(
                    "doc_paths",
                    "low",
                    f"{doc} names {path}, which does not exist in the repository.",
                    f"{doc}:{n}",
                    _path_hint(root, path),
                )
            )
    return out


def _path_hint(root: Path, path: str) -> str:
    parent = (root / path).parent
    ext = Path(path).suffix
    near = sorted(p.name for p in parent.glob(f"*{ext}")) if parent.is_dir() else []
    if near and len(near) <= 3:
        where = parent.relative_to(root).as_posix()
        return (
            f"Point the document at the file that exists ({', '.join(near)} in "
            f"{where if where != '.' else 'the root'}), or remove the mention."
        )
    return "Correct the path to the file that exists, or remove the mention."


# ---------------------------------------------------------------- end of life


def load_eol() -> dict:
    """The end-of-life table (``projects/eol.yaml``): ``{runtime: {version: date}}``."""
    text = resources.files(__package__).joinpath("eol.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    return {rt: {str(k): v for k, v in (vs or {}).items()} for rt, vs in data.items()}


PY_FLOOR = [
    re.compile(r"Python\s*(?:≥|>=)\s*(3\.\d+)"),
    re.compile(r"Python\s+(3\.\d+)\s*(?:\+|or\s+(?:newer|later|above))"),
    re.compile(r"(?:needs|requires)\s+(?:only\s+)?Python\s+(3\.\d+)", re.I),
]


def python_floors(root: Path) -> list[tuple[str, str, str]]:
    """``[(where, version, quote)]``: pyproject's ``requires-python`` and the README's floor."""
    root = Path(root)
    out = []
    text = _read(root, "pyproject.toml")
    for n, line in enumerate(text.splitlines(), 1):
        m = re.match(r"\s*requires-python\s*=\s*[\"']\s*(?:>=|~=|==)?\s*(3\.\d+)", line)
        if m:
            out.append((f"pyproject.toml:{n}", m[1], line.strip()))
            break
    for n, line in enumerate(_read(root, "README.md").splitlines(), 1):
        plain = line.replace("*", "").replace("_", " ")
        m = next((m for p in PY_FLOOR if (m := p.search(plain))), None)
        if m:
            out.append((f"README.md:{n}", m[1], _short(plain.strip(), 120)))
            break
    return out


def ci_pythons(root: Path) -> list[tuple[str, str]]:
    """``[(where, version)]`` for each ``python-version`` a workflow tests (a list, a
    multi-line list or a single value; ``${{ matrix.* }}`` references are skipped)."""
    root = Path(root)
    out = []
    for wf in _workflows(root):
        lines = wf.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"(\s*)python-version\s*:\s*(.*)$", line)
            if not m:
                continue
            value, n = m[2], i + 1
            if not value.strip():
                block = []
                for j in range(i + 1, len(lines)):
                    if not re.match(r"\s*-\s*", lines[j]):
                        break
                    block.append(lines[j])
                value = " ".join(block)
            for v in re.findall(r"(?<![\d.])(3\.\d+)(?![\d])", value.split("#")[0]):
                out.append((f"{_rel(root, wf)}:{n}", v))
    return out


def _pykey(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def python_floor(
    root: Path, today: datetime.date | None = None, eol: dict | None = None
) -> list[dict]:
    """The promised Python floor and the CI matrix against the end-of-life table."""
    root = Path(root)
    today = today or datetime.date.today()
    table = (eol if eol is not None else load_eol()).get("python", {})
    floors = python_floors(root)
    tested = ci_pythons(root)
    out = []
    floor = floors[0][1] if floors else None
    if len({v for _, v, _ in floors}) > 1:
        a, b = floors[0], floors[1]
        out.append(
            finding(
                "python_floor",
                "medium",
                f"The Python floor disagrees: {a[0].split(':')[0]} says {a[1]}, "
                f"{b[0].split(':')[0]} says {b[1]}.",
                b[0],
                "Make the README state the same floor as requires-python.",
            )
        )
    if floor:
        out += _floor_eol(floor, floors, tested, table, today)
    floor_ci = [w for w, v in tested if v == floor]
    for where, v in tested:
        end = table.get(v)
        if v != floor and end and end <= today:
            out.append(
                finding(
                    "python_floor",
                    "medium",
                    f"CI still tests Python {v}, which reached end of life on {end.isoformat()}.",
                    where,
                    f"Drop {v} from the matrix (or replace it with a current "
                    "Python); this is a workflow file, so it is the owner's change.",
                )
            )
        if floor and _pykey(v) < _pykey(floor):
            out.append(
                finding(
                    "python_floor",
                    "low",
                    f"CI tests Python {v}, below the promised floor {floor}.",
                    where,
                    f"Test the floor ({floor}) instead of {v}.",
                )
            )
    if floor and tested and not floor_ci:
        out.append(
            finding(
                "python_floor",
                "low",
                f"The promised floor, Python {floor}, is not in the CI matrix "
                f"({', '.join(sorted({v for _, v in tested}, key=_pykey))}).",
                tested[0][0],
                f"Add {floor} to the matrix so the promise is tested.",
            )
        )
    return out


def _floor_eol(floor, floors, tested, table, today) -> list[dict]:
    end = table.get(floor)
    if not end:
        return []
    promised = "; ".join(f'{w} says "{q}"' for w, v, q in floors if v == floor)
    in_ci = [w for w, v in tested if v == floor]
    ci = f" CI still tests it ({in_ci[0]})." if in_ci else ""
    horizon = today + datetime.timedelta(days=SOON_DAYS)
    newer = sorted((v for v, d in table.items() if d > horizon), key=_pykey)
    if end <= today:  # stored-shape: not end returned above
        return [
            finding(
                "python_floor",
                "high",
                f"Python {floor} reached end of life on {end.isoformat()}, and the "
                f"project still promises it: {promised}.{ci}",
                floors[0][0],
                f"Raising the floor (to {newer[0] if newer else 'a supported Python'}) "
                "changes a promise, so it is the owner's call; until then, add a "
                "current Python to the CI matrix.",
            )
        ]
    if (end - today).days <= SOON_DAYS:  # stored-shape: not end returned above
        return [
            finding(
                "python_floor",
                "low",
                f"Python {floor}, the promised floor, reaches end of life on "
                f"{end.isoformat()}.{ci}",
                floors[0][0],
                "Plan the floor change with the owner before that date.",
            )
        ]
    return []


# ---------------------------------------------------------------- pins

PIN = re.compile(r"(?<![\w.-])([A-Za-z][\w.-]*)==(\d+(?:\.\d+)*)\b")
# the acceptance suite's install line that repo-factory's own templates wrote until 2026-10-16
# (AGENTS.md and ci.yml): repo-factory's pin, not the product's (AU3 finding 2: 11 findings)
TEMPLATE_PIN = re.compile(
    r"python -m pip install pytest==[\d.]+(?:\s*&&\s*python -m pytest "
    r"tests/acceptance\b|\s*$)"
)


def _pin_sources(root: Path) -> list[tuple[str, str]]:
    root = Path(root)
    out = [
        (_rel(root, wf), wf.read_text(encoding="utf-8", errors="replace"))
        for wf in _workflows(root)
    ]
    agents = _read(root, "AGENTS.md")
    if agents:
        out.append(("AGENTS.md", agents))
    return out


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(root: Path, latest: dict[str, str] | None) -> list[dict]:
    """Exact pins (``name==X.Y.Z``) in the workflows and AGENTS.md that are behind the latest
    release the caller read (``latest``: name to version). Names not in ``latest`` are left."""
    if not latest:
        return []
    newest = {_canon(k): v for k, v in latest.items()}
    out, seen = [], set()
    for rel, text in _pin_sources(root):
        for n, line in enumerate(text.splitlines(), 1):
            for m in PIN.finditer(line):
                name, have = m[1], m[2]
                want = newest.get(_canon(name))
                if (
                    TEMPLATE_PIN.search(line)
                    or not want
                    or not vkey(have)
                    or not vkey(want)
                    or (rel, name) in seen
                ):
                    continue
                if vkey(have) >= vkey(want):
                    continue
                seen.add((rel, name))
                out.append(_pin_finding(rel, n, name, have, want))
    return out


def _pin_finding(rel: str, n: int, name: str, have: str, want: str) -> dict:
    majors = vkey(want)[0] - vkey(have)[0]
    behind = (
        "a major version behind"
        if majors == 1
        else f"{majors} major versions behind"
        if majors > 1
        else "behind"
    )
    what = f"{name} is pinned to {have} in {rel}, {behind} the latest {want}."
    if rel.startswith(WORKFLOWS):
        hint = (
            f"{Path(rel).name} is the owner's file: fix PRs may not touch "
            f"{WORKFLOWS}/, so the owner moves the pin (check that {name} {want} still "
            "supports the oldest Python the project promises)."
        )
    else:
        hint = (
            f"Update the pin in {rel} together with the CI pin, so the documented "
            "command matches what CI runs."
        )
    return finding("pins", "medium" if majors >= 1 else "low", what, f"{rel}:{n}", hint)


USES = re.compile(r"uses:\s*([\w.-]+/[\w.-]+)(?:/[\w./-]*)?@([0-9a-f]{40})\s*#\s*(v?\d[\w.]*)")


def actions_pins(root: Path, latest: dict[str, str] | None) -> list[dict]:
    """SHA-pinned actions whose version comment is behind ``latest`` (``owner/action`` to
    version). Report only: Dependabot moves these on products."""
    if not latest:
        return []
    root = Path(root)
    out, seen = [], set()
    for wf in _workflows(root):
        rel = _rel(root, wf)
        for n, line in enumerate(wf.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = USES.search(line)
            if not m:
                continue
            action, have = m[1], m[3]
            want = latest.get(action)
            if not want or (rel, action, have) in seen or not vkey(have) or not vkey(want):
                continue
            if vkey(have) >= vkey(want):
                continue
            seen.add((rel, action, have))
            out.append(
                finding(
                    "actions_pins",
                    "low",
                    f"{action} is pinned at {have} in {rel}; the latest is {want}.",
                    f"{rel}:{n}",
                    "Let Dependabot's Actions update land (or move the SHA and its "
                    "comment together); a workflow file is the owner's.",
                )
            )
    return out


# ---------------------------------------------------------------- the charter's promises

ANCHOR_WORDS = 8


def _words(text: str) -> list[str]:
    return re.sub(r"[\W_]+", " ", text.lower()).split()


def anchor_of(promise) -> str:
    """A promise's anchor: its own ``anchor`` when it has one, else its first eight words."""
    if isinstance(promise, dict):
        if promise.get("anchor"):
            return str(promise["anchor"])
        promise = promise.get("text") or promise.get("promise") or ""
    return " ".join(str(promise).split()[:ANCHOR_WORDS])


def anchor_present(text: str, anchor: str) -> bool:
    """True when the anchor's words appear in ``text`` together and in order, ignoring case,
    punctuation and line breaks."""
    want = _words(anchor)
    if not want:
        return True
    have = _words(text)
    k = len(want)
    return any(have[i : i + k] == want for i in range(len(have) - k + 1))


def anchors_in_readme(root: Path, charter: dict | None) -> list[str]:
    """The charter promises' anchors the README says now."""
    readme = _read(root, "README.md")
    return [
        a
        for a in (anchor_of(p) for p in (charter or {}).get("promises") or [])
        if a and anchor_present(readme, a)
    ]


def readme_promises(root: Path, charter: dict | None, seen: set[str] | None = None) -> list[dict]:
    """Each charter promise whose anchor sentence is no longer in the README. ``seen``: only
    the anchors the README was seen to say before (a drafted promise often paraphrases the
    README, so an anchor it never had is not drift); None checks every promise."""
    if not charter:
        return []
    readme = _read(root, "README.md")
    out = []
    for i, promise in enumerate(charter.get("promises") or []):
        anchor = anchor_of(promise)
        if seen is not None and anchor not in seen:
            continue
        if anchor and not anchor_present(readme, anchor):
            out.append(
                finding(
                    "readme_promises",
                    "high",
                    f'The README no longer says the charter\'s promise {i + 1}: "{anchor}…".',
                    "README.md",
                    "Put the promise back in the README, or change the charter "
                    "(a promise change is the owner's call).",
                )
            )
    return out


# ---------------------------------------------------------------- all of them


def run(
    root: Path,
    *,
    tags: list[str] | None = None,
    latest: dict[str, str] | None = None,
    charter: dict | None = None,
    today: datetime.date | None = None,
    seen_anchors: set[str] | None = None,
) -> list[dict]:
    """Every check that has its inputs; a check whose input is None is skipped."""
    root = Path(root)
    out = doc_paths(root) + python_floor(root, today)
    if tags is not None and not releases_by_factory(root):
        out = version_tags(root, tags) + out
    if latest is not None:
        out += pins(root, latest) + actions_pins(root, latest)
    if charter is not None:
        out += readme_promises(root, charter, seen_anchors)
    return out

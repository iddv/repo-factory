"""The sources of a project's evolve run. First the repo's own backlog and a
code digest, both read from the clone repo-factory keeps, with no model and no network.

The backlog is the to-do list the repo already writes down (SECURITY.md findings still open,
READINESS.md amber rows, the acceptance suite's known open items, CHANGELOG › Unreleased,
expected-failure marks, TODO/FIXME counts): the evolve run de-duplicates against it instead of
rediscovering it. The digest is a compact map of the code made with ``ast`` (never imported or
run) so the model can cite ``file:line`` rather than hedge "may already be handled".

The other source is read, never written: the open issues and pull requests from the poll's
cache (``issues``). ``gather`` puts them together for one run, with a fingerprint of what
changed since the last run so a quiet project is skipped with no model call."""

from __future__ import annotations

import ast
import hashlib
import re
import time
from pathlib import Path

from repo_factory.projects.drift import SKIP_DIRS, changelog_sections

DIGEST_SKIP = SKIP_DIRS | {"tests", "test", "fixtures", "env", ".eggs"}
CODE_EXTS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rb",
    ".rs",
    ".sh",
    ".java",
    ".php",
    ".c",
    ".h",
    ".cpp",
    ".cs",
    ".kt",
    ".swift",
}
CONST_CHARS = 300


def _read(root: Path, rel: str) -> str:
    p = Path(root) / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def _cells(line: str) -> list[str]:
    s = line.strip()
    if not s.startswith("|"):
        return []
    s = s.strip("|")
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def _rows(text: str):
    """``(line, header, cells)`` for each data row of each markdown table."""
    header = None
    for n, line in enumerate(text.splitlines(), 1):
        cells = _cells(line)
        if not cells:
            header = None
        elif header is None:
            header = [c.lower() for c in cells]
        elif not all(re.fullmatch(r":?-{2,}:?|", c) for c in cells):
            yield n, header, cells


# ---------------------------------------------------------------- the backlog

SEC_HEAD = re.compile(r"^#{2,4}\s+(S\d+)\s*(?:\((\w+)\))?\s*[:.-]\s*(.+?)\s*$")
CLOSED = re.compile(r"^\s*(fixed|closed|resolved|done|won'?t fix|not a bug)\b", re.I)


def security(root: Path) -> list[dict]:
    """SECURITY.md findings whose status is not fixed: from a findings table
    (``| S4 | low | title | open |``) and from ``### S3 (low): title`` sections."""
    text = _read(root, "SECURITY.md")
    found: dict[str, dict] = {}
    for n, header, cells in _rows(text):
        if not cells or not re.fullmatch(r"S\d+", cells[0]):
            continue
        col = {h: i for i, h in enumerate(header)}
        status = (
            cells[col["status"]] if "status" in col and col["status"] < len(cells) else cells[-1]
        )
        title = (
            cells[col["finding"]]
            if "finding" in col and col["finding"] < len(cells)
            else cells[2]
            if len(cells) > 2
            else ""
        )
        sev = cells[col["severity"]] if "severity" in col and col["severity"] < len(cells) else ""
        found[cells[0]] = {
            "id": cells[0],
            "title": title,
            "status": status,
            "severity": sev.lower(),
            "line": n,
        }
    section = ""
    lines = text.splitlines()
    for n, line in enumerate(lines, 1):
        if re.match(r"^##\s", line):
            section = line.lower()
        m = SEC_HEAD.match(line)
        if not m or m[1] in found:
            continue
        status = _section_status(lines, n) or ("fixed" if "fixed" in section else "open")
        found[m[1]] = {
            "id": m[1],
            "title": m[3],
            "status": status,
            "severity": (m[2] or "").lower(),
            "line": n,
        }
    items = [f for f in found.values() if not CLOSED.match(f["status"])]
    return sorted(items, key=lambda f: int(f["id"][1:]))


def _section_status(lines: list[str], start: int) -> str:
    for line in lines[start:]:
        if line.startswith("#"):
            break
        m = re.match(r"^\s*[-*]\s*\**status\**\s*:\s*(.+)$", line, re.I)
        if m:
            return m[1].strip()
    return ""


def readiness(root: Path) -> list[dict]:
    """READINESS.md rows whose status is amber."""
    out = []
    for n, header, cells in _rows(_read(root, "READINESS.md")):
        if not any(c.lower() == "amber" for c in cells):
            continue
        row = dict(zip(header, cells))
        out.append(
            {
                "item": cells[0],
                "why": row.get("why", ""),
                "next_step": next((v for k, v in row.items() if k.startswith("next")), ""),
                "line": n,
            }
        )
    return out


def known_open(root: Path) -> list[dict]:
    """The bullets under "Known open items" in the acceptance suite's README (or the tests'
    README, or READINESS.md when neither has the section)."""
    for rel in ("tests/acceptance/README.md", "tests/README.md", "READINESS.md"):
        items = _bullets_under(_read(root, rel), r"known open items")
        if items:
            return [{"file": rel, "line": n, **_split_item(text)} for n, text in items]
    return []


def _bullets_under(text: str, title: str) -> list[tuple[int, str]]:
    out, inside = [], False
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("#"):
            inside = bool(re.search(title, line, re.I))
            continue
        if inside and re.match(r"^\s*[-*]\s+", line):
            out.append((n, re.sub(r"^\s*[-*]\s+", "", line).strip()))
    return out


def _split_item(text: str) -> dict:
    m = re.match(r"^`([^`]+)`\s*:\s*(.*)$", text)
    return {"test": m[1], "what": m[2]} if m else {"test": "", "what": text}


def unreleased(root: Path) -> list[str]:
    """The lines under CHANGELOG › Unreleased."""
    for name in ("CHANGELOG.md", "CHANGES.md", "HISTORY.md"):
        text = _read(root, name)
        if text:
            sec = next((s for s in changelog_sections(text) if s["unreleased"]), None)
            return [t for _, t in sec["items"]] if sec else []
    return []


XFAIL = re.compile(r"\bmark\.xfail\b|\bexpectedFailure\b")
REASON = re.compile(r"reason\s*=\s*(?P<q>['\"])(?P<r>.*?)(?<!\\)(?P=q)")


def _files(root: Path, exts: set[str], skip: set[str]):
    root = Path(root)
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).parts
        if (
            p.suffix in exts
            and p.is_file()
            and not any(part in skip or part.startswith(".") for part in rel[:-1])
        ):
            yield p


def xfail(root: Path) -> list[dict]:
    """Expected-failure marks in the tests: ``pytest.mark.xfail`` (also through an alias, as
    the acceptance suites write it) and ``unittest.expectedFailure``."""
    root = Path(root)
    out = []
    for p in _files(root, {".py"}, SKIP_DIRS):
        rel = p.relative_to(root).as_posix()
        parts = rel.split("/")
        if not (set(parts[:-1]) & {"tests", "test"} or parts[-1].startswith("test_")):
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if XFAIL.search(line) and not line.lstrip().startswith("#"):
                m = REASON.search(line)
                out.append({"file": rel, "line": n, "reason": m["r"] if m else ""})
    return out


def todo_counts(root: Path) -> dict[str, int]:
    counts = {"TODO": 0, "FIXME": 0}
    for p in _files(root, CODE_EXTS, SKIP_DIRS):
        text = p.read_text(encoding="utf-8", errors="replace")
        for k in counts:
            counts[k] += len(re.findall(rf"\b{k}\b", text))
    return counts


def backlog(root: Path) -> dict:
    """The repo's own to-do list. Missing files give empty lists."""
    root = Path(root)
    return {
        "security": security(root),
        "readiness": readiness(root),
        "known_open": known_open(root),
        "unreleased": unreleased(root),
        "xfail": xfail(root),
        "todo": todo_counts(root),
    }


# ---------------------------------------------------------------- the code digest

CONST_NAME = re.compile(r"^_?[A-Z][A-Z0-9_]*$")


def _modules(root: Path) -> list[Path]:
    out = []
    for p in _files(root, {".py"}, DIGEST_SKIP):
        if p.name.startswith("test_") or p.name.endswith("_test.py") or p.name == "conftest.py":
            continue
        if any(part.endswith(".egg-info") for part in p.parts):
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.relative_to(root).as_posix())


def _first_line(doc: str | None) -> str:
    return doc.strip().splitlines()[0].strip() if doc and doc.strip() else ""


def _const(node: ast.stmt) -> tuple[str, ast.expr] | None:
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        name, value = node.targets[0].id, node.value
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
        name, value = node.target.id, node.value
    else:
        return None
    if not CONST_NAME.match(name) or not isinstance(
        value, (ast.List, ast.Tuple, ast.Set, ast.Dict)
    ):
        return None
    return name, value


def _const_text(value: ast.expr, full: bool) -> str | None:
    try:
        text = repr(ast.literal_eval(value))
    except (ValueError, SyntaxError, TypeError, RecursionError):
        if not isinstance(value, ast.Dict):
            return None
        keys = [k.value for k in value.keys if isinstance(k, ast.Constant)]
        if not keys:
            return None
        text = f"dict with keys {keys!r}"
    if full or len(text) <= CONST_CHARS:
        return text
    return text[: CONST_CHARS - 1] + f"… ({len(text)} chars)"


def module_digest(root: Path, path: Path, constants: set[str]) -> tuple[str, bool]:
    """One module's lines, and whether it holds one of the named ``constants``."""
    rel = path.relative_to(root).as_posix()
    src = path.read_text(encoding="utf-8", errors="replace")
    n_lines = len(src.splitlines())
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return f"{rel} ({n_lines} lines): not parseable\n", False
    doc = _first_line(ast.get_docstring(tree))
    lines = [f"{rel} ({n_lines} lines)" + (f": {doc}" if doc else "")]
    fns, named = [], False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith(
            "_"
        ):
            fns.append(f"{node.name}:{node.lineno}")
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            methods = [
                m.name
                for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not m.name.startswith("_")
            ]
            lines.append(
                f"  class {node.name}:{node.lineno}"
                + (f" ({', '.join(methods)})" if methods else "")
            )
        elif c := _const(node):
            name, value = c
            full = name in constants
            text = _const_text(value, full)
            if text is not None:
                named = named or full
                lines.append(f"  {name}:{node.lineno} = {text}")
    if fns:
        lines.insert(1, "  def " + ", ".join(fns))
    return "\n".join(lines) + "\n", named


def digest(root: Path, *, max_chars: int = 12000, constants: list[str] | None = None) -> str:
    """A compact, deterministic text map of the repo's Python code (see the module docstring),
    at most ``max_chars`` long. Modules holding a named constant are kept first when the map
    is cut; the rest follow in path order until the budget runs out."""
    root = Path(root)
    wanted = set(constants or [])
    blocks = [module_digest(root, p, wanted) for p in _modules(root)]
    if not blocks:
        return ""
    total = sum(len(b) for b, _ in blocks)
    if total <= max_chars:
        return "".join(b for b, _ in blocks)
    keep = [False] * len(blocks)
    budget = max_chars - len(f"… ({len(blocks)} more modules)")
    for i, (b, named) in enumerate(blocks):
        if named and len(b) <= budget:
            keep[i], budget = True, budget - len(b)
    for i, (b, _) in enumerate(blocks):
        if not keep[i] and len(b) <= budget:
            keep[i], budget = True, budget - len(b)
        elif not keep[i]:
            break
    text = "".join(b for (b, _), k in zip(blocks, keep) if k)
    rest = keep.count(False)
    return (text + f"… ({rest} more modules)")[:max_chars] if rest else text[:max_chars]


# ---------------------------------------------------------------- issues from the poll's cache

ISSUE_ROWS = 15


def issues(recs: list[tuple[str, dict]]) -> dict:
    """The open issues and pull requests the poll's cache knows for the project's repos
    (``record.cache_recs``): ``{"counts", "items": [{"repo", "number", "kind", "title", "url",
    "updated"}], "text"}``. The cache counts every open item but keeps rows only for what
    waits (needs you, repo-factory's PRs, Dependabot updates, untriaged issues); ``text`` is the short
    list for the prompt and says plainly when nothing is open."""
    from .record import counts

    items, lines = [], []
    for full, rec in recs:
        lines.append(_issue_line(full, rec))
        for r in rec.get("rows") or []:
            if r.get("number") is not None:
                items.append(
                    {
                        "repo": full,
                        "number": r["number"],
                        "kind": r.get("kind"),
                        "title": r.get("title") or "",
                        "url": r.get("url"),
                        "updated": r.get("updated"),
                    }
                )
    for i in items[:ISSUE_ROWS]:
        lines.append(
            f"- {i['repo']}#{i['number']} ({str(i['kind']).replace('_', ' ')}): {i['title'][:140]}"
        )
    if len(items) > ISSUE_ROWS:
        lines.append(f"- … and {len(items) - ISSUE_ROWS} more")
    return {
        "counts": counts(recs),
        "items": items,
        "text": "\n".join(lines) or "No repository to read issues from.",
    }


def _issue_line(full: str, rec: dict) -> str:
    c = rec.get("counts") or {}
    n_i, n_p = int(c.get("issues") or 0), int(c.get("prs") or 0)
    if not rec.get("last_poll"):
        return f"{full}: not polled yet, so its issues are not known."
    if not n_i and not n_p:
        return f"{full}: no open issues or pull requests."
    extra = [
        f"{int(c[k])} {w}"
        for k, w in (("dependabot", "from Dependabot"), ("factory_prs", "by repo-factory"))
        if c.get(k)
    ]
    return (
        f"{full}: {n_i} open issue{'s' if n_i != 1 else ''}, {n_p} open pull "
        f"request{'s' if n_p != 1 else ''}" + (f" ({', '.join(extra)})" if extra else "") + "."
    )


STOP = set(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "for",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "into",
        "is",
        "are",
        "be",
        "it",
        "its",
        "this",
        "that",
        "your",
        "you",
        "their",
        "them",
        "they",
        "what",
        "which",
        "who",
        "how",
        "when",
        "where",
        "without",
        "within",
        "over",
        "under",
        "every",
        "each",
        "one",
        "two",
        "any",
        "all",
        "more",
        "most",
        "less",
        "than",
        "then",
        "so",
        "as",
        "not",
        "no",
        "only",
        "just",
        "also",
        "make",
        "makes",
        "made",
        "tool",
        "tools",
        "app",
        "apps",
        "does",
        "done",
    ]
)


def keywords(text: str | None, n: int = 6) -> list[str]:
    """The pitch's nouns, roughly: its longer words in order, without filler."""
    out: list[str] = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]*[A-Za-z0-9+#]", text or ""):
        lw = w.lower()
        if len(lw) >= 4 and lw not in STOP and lw not in out:
            out.append(lw)
    return out[:n]


# ---------------------------------------------------------------- what the evolve run reads

CONSTANT = re.compile(r"\b_?[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")


def charter_constants(charter: dict | None) -> list[str]:
    """Constant names (``SKIP_MARKERS``) the charter's constraints, promises and scope
    mention: the digest prints those tables in full."""
    c = charter or {}
    text = " ".join(
        str(x.get("text") if isinstance(x, dict) else x)
        for k in ("constraints", "promises", "scope")
        for x in c.get(k) or []
    )
    return sorted(set(CONSTANT.findall(text)))


def fingerprint(
    *,
    at: float,
    obs: list[dict],
    issue_items: list[dict],
    drift: list[dict],
    charter_v: int,
    head: str | None,
) -> dict:
    """What a run saw, small enough to keep: ``{"at", "head", "charter_v", "drift" (a hash of
    the findings set), "issues" ({"repo#n": updated}), "observations" (the newest id)}``."""
    from .stats import finding_key

    keys = "\n".join(sorted(finding_key(f) for f in drift))
    return {
        "at": at,
        "head": head,
        "charter_v": int(charter_v or 0),
        "drift": hashlib.sha256(keys.encode()).hexdigest()[:16],
        "issues": {f"{i['repo']}#{i['number']}": i.get("updated") for i in issue_items},
        "observations": max((o["id"] for o in obs if isinstance(o.get("id"), int)), default=0),
    }


def changed(fp: dict, last: dict | None, new_observations: int) -> dict:
    """What changed since the last run's fingerprint (None: everything is new):
    ``{"observations": n, "issues": ["repo#n"], "drift", "charter", "head", "any"}``."""
    last = last or {}
    old = last.get("issues") or {}
    iss = sorted(k for k, v in fp["issues"].items() if k not in old or old[k] != v)
    out = {
        "observations": new_observations,
        "issues": iss,
        "drift": fp["drift"] != last.get("drift"),
        "charter": fp["charter_v"] != last.get("charter_v"),
        "head": fp["head"] != last.get("head"),
    }
    out["any"] = bool(new_observations or iss or out["drift"] or out["charter"] or out["head"])
    return out


def _clone_root(loop, p: dict, gh, git_fn) -> tuple[Path, str, list[str]]:
    """The tree the tick keeps (cloned here only when there is none), its head and tags."""
    from . import clones

    run = git_fn or clones.git
    root = clones.root_of(loop.cfg.home, p)
    if not root.is_dir():
        c = clones.refresh(loop, p, gh=gh, git_fn=git_fn)
        root = Path(c["path"]) / (clones.primary(p).get("subdir") or "")
    head = run("rev-parse", "HEAD", cwd=root).strip()[:40]
    return root, head, [t for t in run("tag", "-l", cwd=root).split() if t]


def gather(
    loop,
    p: dict,
    *,
    gh=None,
    now: float | None = None,
    cache: dict | None = None,
    last: dict | None = None,
    latest=None,
    git_fn=None,
) -> dict:
    """Everything an evolve run reads for project ``p``, with no model and no write:
    ``{"backlog", "drift", "issues", "observations", "parent", "digest", "charter_v", "head",
    "fingerprint", "changed", "errors"}``. ``last``: the fingerprint of the last run (the
    caller keeps it): ``changed`` says what moved since it (``observations`` and ``parent``
    stay empty: nothing outside the repository is read). The drift findings are the tick's when they are for this head and charter, else
    run now. A part that cannot be read is empty, and ``errors`` says why."""
    from . import stats as S
    from .record import cache_recs

    now = now or time.time()
    errors: list[str] = []
    root, head, tags = None, None, []
    try:
        root, head, tags = _clone_root(loop, p, gh, git_fn)
    except Exception as e:  # noqa: BLE001 - said; the rest is read
        errors.append(f"clone: {type(e).__name__}: {e}"[:300])
    st, found = p.get("stats") or {}, []
    if head:
        if not S.drift_due(st, head, p.get("charter_v") or 0):
            found = (st.get("drift") or {}).get("findings") or []
        else:
            found = S.run_drift(
                loop.cfg.home,
                p,
                {"path": str(root), "head": head, "tags": tags},
                latest=latest,
                now=now,
            )["findings"]
    iss = issues(cache_recs(loop.cfg.home, p, cache))
    obs: list[dict] = []
    par: list[dict] = []
    charter_v = int(p.get("charter_v") or 0)
    fp = fingerprint(
        at=now, obs=obs, issue_items=iss["items"], drift=found, charter_v=charter_v, head=head
    )
    seen = int((last or {}).get("observations") or 0)
    fresh = sum(1 for o in obs if not isinstance(o.get("id"), int) or o["id"] > seen)
    have = root is not None and head is not None
    return {
        "backlog": backlog(root) if have else {},
        "drift": found,
        "issues": iss,
        "observations": obs,
        "parent": par,
        "digest": digest(root, constants=charter_constants(p.get("charter"))) if have else "",
        "charter_v": charter_v,
        "head": head,
        "fingerprint": fp,
        "changed": changed(fp, last, fresh),
        "errors": errors,
    }

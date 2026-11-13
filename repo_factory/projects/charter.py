"""A project's charter (PJ-PLAN.md §3): one page in plain words saying what the project is, for
whom, what it promises, what it will not do, which ways it may grow, and the tests a reviewer
asks of any change. Everything else about a project reads or writes this page.

**Drafted, never asked for.** One no-tools call (``projects.charter.model``, the registered
prompt ``charter``) from a seed (the idea, its pitch and card, how its claims are read, the
spec) and the repo's own documents, fenced as data. The reply's shape is checked in code
(``check``): every field, the pitch at most 30 words, 4 to 8 promises, non-goals with a reason.
A product's draft is made in the background pre-push process when the gate says ready and kept
on the idea (``publish.charter_draft``) until the publish makes the project; an adopted repo's
in onboarding's ``charter`` stage, until its profile is confirmed; the backfill's by
``repo-factory projects charter ID`` in a process of its own.

**Versions only grow** (``charter_versions``): every change is a new version with who made it
(``repo-factory``, ``owner``, ``check``: NG's Accept on an interpretation), a note and its sources; the
project's ``charter`` is the head. "Use this version" restores an old one as a new version;
a redraft is a new version by repo-factory, and the owner's earlier edits stay a click away.

A promise is ``{"text", "anchor", "claim"?}``: its anchor is its first eight words (the PR-time
guard looks for it in the README, ``drift.readme_promises``), and a promise that came from one
of the idea's claims carries the claim's id, so NG's interpretations are the promises."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from string import Template

from ..stored import kept
from ..stored.projects import CharterVersion

FIELDS = (
    "pitch",
    "spirit",
    "users",
    "promises",
    "scope",
    "non_goals",
    "open_directions",
    "constraints",
    "scope_tests",
)
TITLES = {
    "pitch": "Pitch",
    "spirit": "Spirit",
    "users": "Users",
    "promises": "Promises",
    "scope": "Scope",
    "non_goals": "Non-goals",
    "open_directions": "Open directions",
    "constraints": "Constraints",
    "scope_tests": "Scope tests",
}
BY = ("repo-factory", "owner", "check")
DOCS = (
    "README.md",
    "SCOPE.md",
    "AGENTS.md",
    "SECURITY.md",
    "tests/acceptance/README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
)
DOC_MAX, REPO_MAX, SPEC_MAX = 14_000, 60_000, 12_000
ANCHOR_WORDS = 8
MAX_TRIES = 2


class CharterError(ValueError):
    """A charter that cannot be used, with why in words."""


# -- what the model reads ----------------------------------------------------------------------
def _fence(name: str, text: str) -> str:
    return f'<<<document name="{name}">>>\n{text.rstrip()}\n<<<end>>>'


def seed_text(idea: dict | None, adopted: str | None = None) -> str:
    """The originating idea as the model reads it: the idea's text, its pitch, the card's
    thesis, how its claims are read, and the spec it was built from. ``adopted``: an adopted
    repo's name (no idea: the repository is its own seed)."""
    if not idea:
        return (
            f"No originating idea: {adopted or 'this repository'} was adopted for "
            "maintenance, not built by repo-factory. Take its spirit from what the repository "
            "itself says."
        )
    p, card = idea.get("pitch") or {}, idea.get("card") or {}
    out = [f"Idea: {(idea.get('text') or '').strip()[:4000]}"]
    if card.get("thesis"):
        out.append(f"Card thesis: {card['thesis']}")
    keep = {
        k: p.get(k)
        for k in (
            "product",
            "product_type",
            "one_liner",
            "who",
            "why_now",
            "buildability_reasons",
            "risks",
            "brief",
        )
        if p.get(k)
    }
    if keep:
        out.append("Pitch:\n" + json.dumps(keep, indent=1))
    claims = [c for c in p.get("claims") or [] if isinstance(c, dict)]
    if claims:
        out.append(
            "How the claims are read (the promises must not say more than these):\n"
            + "\n".join(
                f'- {c.get("id")}: "{c.get("claim")}" is read as: {c.get("interpreted")}'
                for c in claims
            )
        )
    spec = Path(idea["task_dir"]) / "spec.md" if idea.get("task_dir") else None
    if spec and spec.is_file():
        out.append("Spec:\n" + spec.read_text(errors="replace")[:SPEC_MAX])
    return "\n\n".join(out)


def repo_text(root: Path | None, extra: dict | None = None) -> str:
    """The repo's own documents (``DOCS``, those that exist), each fenced and cut, plus
    ``extra`` ``{name: text}`` (an adopted repo's facts)."""
    parts, total = [], 0
    for name in DOCS:
        f = Path(root) / name if root else None
        if not f or not f.is_file():
            continue
        text = f.read_text(errors="replace")[:DOC_MAX]
        if total + len(text) > REPO_MAX:
            break
        total += len(text)
        parts.append(_fence(name, text))
    for name, text in (extra or {}).items():
        parts.append(_fence(name, str(text)[:DOC_MAX]))
    return "\n\n".join(parts) or "(the repository has none of the usual documents)"


def prompt(seed: str, repo: str) -> str:
    from ..custom_prompts import text

    return Template(text("charter")).safe_substitute(SEED=seed, REPO=repo)


def parse(reply: str) -> dict | None:
    """The JSON object in the reply (inside a ```json fence, or bare)."""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", reply or "", re.S)
    raw = m.group(1) if m else (reply or "")[(reply or "").find("{") :]
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return d if isinstance(d, dict) else None


# -- the shape -----------------------------------------------------------------------------------
def _words(t: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(t or "").lower())


def anchor_of(text: str) -> str:
    """A promise's anchor: its first eight words, as written."""
    return " ".join(str(text or "").split()[:ANCHOR_WORDS])


def _line(v, n: int = 600) -> str:
    return " ".join(str(v or "").split())[:n]


def link_claims(promises: list[dict], claims: list[dict]) -> None:
    """Give each promise the id of the claim it reads (most shared words, at least a third
    of the shorter text), so an Accept on that claim updates it."""
    for c in claims or []:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        cw = set(_words(f"{c.get('claim')} {c.get('interpreted')}"))
        best, score = None, 0.0
        for p in promises:
            if p.get("claim"):
                continue
            pw = set(_words(p["text"]))
            if not pw or not cw:
                continue
            s = len(pw & cw) / min(len(pw), len(cw))
            if s > score:
                best, score = p, s
        if best is not None and score >= 0.34:
            best["claim"] = c["id"]


def normalize(raw: dict, claims: list[dict] | None = None, old: dict | None = None) -> dict:
    """A charter from a model's reply or the owner's edit: every field cleaned, promises as
    ``{text, anchor, claim?}`` (a promise kept from ``old`` keeps its claim)."""
    c: dict = {
        "name": _line(raw.get("name"), 120) or None,
        "pitch": _line(raw.get("pitch"), 400),
        "spirit": _line(raw.get("spirit"), 1200),
    }
    c["users"] = [
        {"who": _line(u.get("who"), 300), "needs": _line(u.get("needs"), 400)}
        if isinstance(u, dict)
        else {"who": _line(u, 300), "needs": ""}
        for u in raw.get("users") or []
    ]
    kept = {
        p.get("anchor"): p.get("claim")
        for p in ((old or {}).get("promises") or [])
        if isinstance(p, dict) and p.get("claim")
    }
    proms = []
    for p in raw.get("promises") or []:
        text = _line(p.get("text") if isinstance(p, dict) else p, 400)
        if not text:
            continue
        a = (
            _line(p.get("anchor"), 200)
            if isinstance(p, dict) and p.get("anchor")
            else anchor_of(text)
        )
        d = {"text": text, "anchor": a}
        claim = (p.get("claim") if isinstance(p, dict) else None) or kept.get(a)
        if claim:
            d["claim"] = str(claim)
        proms.append(d)
    c["promises"] = proms
    for k in ("scope", "non_goals", "constraints", "scope_tests"):
        c[k] = [x for x in (_line(v, 400) for v in raw.get(k) or []) if x]
    c["open_directions"] = [
        {
            "direction": _line(d.get("direction"), 300),
            "why_in_spirit": _line(d.get("why_in_spirit") or d.get("why"), 400),
        }
        if isinstance(d, dict)
        else {"direction": _line(d, 300), "why_in_spirit": ""}
        for d in raw.get("open_directions") or []
    ]
    if claims:
        link_claims(c["promises"], claims)
    return c


def _reasoned(line: str) -> bool:
    return bool(re.search(r"[:—–]|\s-\s|\(|\bbecause\b", line))


def check(c: dict, claims: list[dict] | None = None) -> list[str]:
    """What is wrong with a charter's shape, in words; [] when it is usable."""
    out = []
    for k in FIELDS:
        if not c.get(k):
            out.append(f"{TITLES[k].lower()} is missing")
    if c.get("pitch") and len(c["pitch"].split()) > 30:
        out.append(f"the pitch has {len(c['pitch'].split())} words; at most 30")
    n = len(c.get("promises") or [])
    if c.get("promises") and not 4 <= n <= 8:
        out.append(f"{n} promises; between 4 and 8")
    ng = c.get("non_goals") or []
    if ng and not 5 <= len(ng) <= 12:
        out.append(f"{len(ng)} non-goals; between 5 and 12")
    bare = [x for x in ng if not _reasoned(x)]
    if bare:
        out.append(f'non-goal without a reason: "{bare[0]}"')
    if c.get("users") and any(not u.get("who") for u in c["users"]):
        out.append("a user kind has no who")
    if c.get("scope_tests") and not 3 <= len(c["scope_tests"]) <= 5:
        out.append(f"{len(c['scope_tests'])} scope tests; between 3 and 5")
    if claims is not None:
        from ..loop.claims import absolutes

        covered = " ".join(
            f"{k.get('claim')} {k.get('interpreted')}" for k in claims if isinstance(k, dict)
        ).lower()
        for p in c.get("promises") or []:
            bad = [w for w in absolutes(p["text"]) if w not in covered]
            if bad and not p.get("claim"):
                out.append(
                    f'the promise "{anchor_of(p["text"])}…" says {bad[0]!r}, which '
                    "no interpretation covers"
                )
    return out


# -- drafting ------------------------------------------------------------------------------------
def draft(model, seed: str, repo: str, claims: list[dict] | None = None) -> dict:
    """One no-tools call (a second when the first reply's shape is wrong, with what was wrong):
    ``{"charter", "usd", "tokens", "problems", "calls"}``; ``charter`` None when no reply was
    usable."""
    from ..loop.models import add, split

    text = prompt(seed, repo)
    usd, tokens, problems, c = 0.0, None, ["no reply"], None
    for n in range(1, MAX_TRIES + 1):
        reply, u, t = split(model(text))
        usd, tokens = usd + float(u or 0), add(tokens, t)
        raw = parse(reply)
        if raw is None:
            problems = ["the reply was not one JSON object"]
        else:
            c = normalize(raw, claims)
            problems = check(c)
        if not problems:
            # an absolute no interpretation covers is a hint on the page, not a retry
            return {
                "charter": c,
                "usd": round(usd, 6),
                "tokens": tokens,
                "problems": [],
                "notes": [x for x in check(c, claims or []) if "no interpretation" in x],
                "calls": n,
            }
        text = (
            prompt(seed, repo)
            + "\n\nYour previous reply could not be used: "
            + "; ".join(problems)
            + ". Reply again with the corrected JSON object."
        )
    # a reply whose only problems are counts is still a charter: kept, with what is off
    usable = c is not None and all(f in c and c[f] for f in ("pitch", "promises", "non_goals"))
    return {
        "charter": c if usable else None,
        "usd": round(usd, 6),
        "tokens": tokens,
        "problems": problems,
        "calls": MAX_TRIES,
    }


def model_for(cfg):
    """The charter's model (``projects.charter.model``): a no-tools ``claude -p`` call."""
    from ..loop.models import claude

    m = ((cfg.get("projects") or {}).get("charter") or {}).get("model")
    return claude(m, purpose="charter", timeout_s=600)


# -- versions -------------------------------------------------------------------------------------
def versions(ledger, pid: str) -> list[dict]:
    with ledger.db() as c:
        rows = c.execute(
            "SELECT v, by, note, at FROM charter_versions WHERE project = ? ORDER BY v DESC", (pid,)
        ).fetchall()
    return [dict(r) for r in rows]


def version(ledger, pid: str, v: int) -> dict | None:
    with ledger.db() as c:
        r = c.execute(
            "SELECT * FROM charter_versions WHERE project = ? AND v = ?", (pid, int(v))
        ).fetchone()
    if not r:
        return None
    d = dict(r)
    d["charter"] = kept(CharterVersion, d["charter"])
    return d


def _version_text(body: dict) -> str:
    """A version's text: the charter as written, checked against its contract."""
    text = json.dumps(body)
    CharterVersion.parse(text)
    return text


def save(
    projects, pid: str, charter: dict, by: str, note: str = "", sources: dict | None = None
) -> dict:
    """A new version (never an edit): ``charter_versions`` gains v N+1 and the project's head
    moves to it; one Timeline line. Returns the project."""
    if by not in BY:
        raise ValueError(f"by is one of {', '.join(BY)}")
    led = projects.ledger
    body = {**charter, **({"sources": sources} if sources else {})}
    with led.db() as c:
        v = (
            (
                c.execute(
                    "SELECT COALESCE(MAX(v), 0) FROM charter_versions WHERE project = ?", (pid,)
                ).fetchone()[0]
            )
            + 1
        )
        c.execute(
            "INSERT INTO charter_versions (project, v, charter, by, note, at) VALUES (?,?,?,?,?,?)",
            (pid, v, _version_text(body), by, note or None, time.time()),
        )
    p = projects.update(pid, charter=body, charter_v=v)
    who = {"repo-factory": "repo-factory", "owner": "you", "check": "the claims check"}[by]
    projects.event(
        pid, "charter", f"charter v{v} by {who}" + (f": {note}" if note else ""), v=v, by=by
    )
    return p


def edit(loop, pid: str, text: str, note: str = "") -> dict:
    """Edit charter: the owner's text as a new version by them. Only a missing field or a
    pitch over 30 words is refused (CharterError, in words): the counts are repo-factory's guide, not
    the owner's. No run starts; the next evolve run reads the new version."""
    from .record import Projects

    ps = Projects(loop.ledger)
    p = ps.must(pid)
    idea = loop.ledger.get(p["idea"]) if p.get("idea") else None
    claims = ((idea or {}).get("pitch") or {}).get("claims") or []
    c = from_text(text, p.get("charter"), claims)
    problems = [x for x in check(c) if "missing" in x or "pitch has" in x]
    if problems:
        raise CharterError("; ".join(problems))
    if p.get("charter") and {k: c.get(k) for k in FIELDS} == {
        k: p["charter"].get(k) for k in FIELDS
    }:
        raise CharterError("nothing changed")
    return save(ps, pid, c, "owner", " ".join(str(note or "").split())[:80])


def restore(projects, pid: str, v: int, by: str = "owner") -> dict:
    """ "Use this version": version ``v``'s text as a new version."""
    old = version(projects.ledger, pid, v)
    if old is None:
        raise CharterError(f"charter v{v} does not exist")
    return save(projects, pid, old["charter"], by, f"restored v{v}")


# -- the text the owner edits ---------------------------------------------------------------------
def to_text(c: dict) -> str:
    """The charter as plain text with one heading per field (Edit charter's box)."""
    out = []
    for k in FIELDS:
        out.append(f"# {TITLES[k]}")
        v = c.get(k)
        if k in ("pitch", "spirit"):
            out.append(str(v or ""))
        elif k == "users":
            out += [
                f"- {u.get('who')}: {u.get('needs')}" if u.get("needs") else f"- {u.get('who')}"
                for u in v or []
            ]
        elif k == "promises":
            out += [f"- {p['text']}" for p in v or []]
        elif k == "open_directions":
            out += [
                f"- {d.get('direction')}: {d.get('why_in_spirit')}"
                if d.get("why_in_spirit")
                else f"- {d.get('direction')}"
                for d in v or []
            ]
        else:
            out += [f"- {x}" for x in v or []]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def from_text(text: str, old: dict | None = None, claims: list[dict] | None = None) -> dict:
    """The owner's edited text back into a charter (unknown headings refused in words)."""
    by_title = {v.lower(): k for k, v in TITLES.items()}
    cur, raw = None, {k: [] for k in FIELDS}
    for line in (text or "").splitlines():
        m = re.match(r"\s*#+\s*(.+?)\s*$", line)
        if m:
            cur = by_title.get(m.group(1).lower().rstrip(":"))
            if cur is None:
                raise CharterError(
                    f'unknown heading "{m.group(1)}": the headings are '
                    + ", ".join(TITLES.values())
                )
            continue
        if cur is None or not line.strip():
            continue
        raw[cur].append(re.sub(r"^\s*[-*]\s+", "", line).strip())
    out: dict = {
        "name": (old or {}).get("name"),
        "pitch": " ".join(raw["pitch"]),
        "spirit": " ".join(raw["spirit"]),
    }

    def pair(x, a, b):
        head, _, rest = x.partition(": ")
        return {a: head.strip(), b: rest.strip()}

    out["users"] = [pair(x, "who", "needs") for x in raw["users"]]
    out["open_directions"] = [pair(x, "direction", "why_in_spirit") for x in raw["open_directions"]]
    for k in ("promises", "scope", "non_goals", "constraints", "scope_tests"):
        out[k] = raw[k]
    return normalize(out, claims, old)


def diff(a: str, b: str) -> list[dict]:
    """Two texts' lines as ``[{op: " "|"-"|"+", line}]`` (the Versions view)."""
    import difflib

    out = []
    for d in difflib.ndiff(a.splitlines(), b.splitlines()):
        if d[:1] in (" ", "-", "+"):
            out.append({"op": d[0], "line": d[2:]})
    return out


# -- the jobs -------------------------------------------------------------------------------------
def draft_for_idea(loop, idea: dict, root: Path | None, model=None) -> dict:
    """A product's draft from the idea and its staged tree: ``{charter, at, model, usd,
    problems, error?}``; its spend is recorded (kind ``charter``) on the idea."""
    from ..loop.models import spend_kw

    m = ((loop.cfg.get("projects") or {}).get("charter") or {}).get("model")
    claims = (idea.get("pitch") or {}).get("claims") or []
    out = draft(model or model_for(loop.cfg), seed_text(idea), repo_text(root), claims)
    loop.ledger.spend("charter", out["usd"], idea["id"], **spend_kw(out["tokens"]))
    return {
        "charter": out["charter"],
        "at": time.time(),
        "model": m,
        "usd": out["usd"],
        "problems": out["problems"],
        **({} if out["charter"] else {"error": "; ".join(out["problems"])}),
    }


def clone_of(home, repo: str) -> Path | None:
    """The clone repo-factory keeps of a repo (maintenance's work folder), when there is one."""
    from .clones import work_dir

    base = work_dir(home, repo.split("/")[0] + "/" + repo.split("/")[1])
    for cand in (base / "clone", base):
        if (cand / ".git").exists() or (cand / "README.md").is_file():
            return cand
    return None


def draft_for_project(
    loop, pid: str, model=None, by_note: str = "", root: Path | None = None
) -> dict:
    """``repo-factory projects charter ID``: draft from the origin idea (or the adopted repo) and the
    repo's documents, then save it as a new version by repo-factory. The documents come from ``root``,
    else the clone repo-factory keeps, else GitHub's copy of each (one read each)."""
    from ..loop.models import spend_kw
    from .record import Projects

    ps = Projects(loop.ledger)
    p = ps.must(pid)
    idea = loop.ledger.get(p["idea"]) if p.get("idea") else None
    repo = p["repos"][0]["repo"]
    extra = None
    root = root or clone_of(loop.cfg.home, repo)
    if root is None:
        extra = github_docs(loop, repo)
    claims = ((idea or {}).get("pitch") or {}).get("claims") or []
    seed = seed_text(idea, adopted=None if idea else repo)
    out = draft(model or model_for(loop.cfg), seed, repo_text(root, extra), claims)
    loop.ledger.spend(
        "charter", out["usd"], (idea or {}).get("id"), project=pid, **spend_kw(out["tokens"])
    )
    if out["charter"] is None:
        raise CharterError("the draft could not be used: " + "; ".join(out["problems"]))
    m = ((loop.cfg.get("projects") or {}).get("charter") or {}).get("model")
    note = by_note or ("drafted" if not p.get("charter") else "redrafted from the repo now")
    return save(
        ps,
        pid,
        out["charter"],
        "repo-factory",
        note,
        sources={
            "model": m,
            "usd": out["usd"],
            "from": "clone" if root else "github",
            "problems": out["problems"] or None,
        },
    )


def github_docs(loop, repo: str) -> dict:
    """The repo's documents read from GitHub (no clone here): ``{name: text}``."""
    from ..maintain.github import GitHubError, Repo
    from ..maintain.poll import gh_for

    r, out = Repo(gh_for(loop), repo), {}
    for name in DOCS:
        try:
            t = r.file(name)
        except GitHubError:
            t = None
        if t:
            out[name] = t
    return out


def apply_claim(loop, idea_id: str, k: str) -> dict | None:
    """NG's Accept or edit on claim ``k``: the promise that carries ``k`` takes the claim's
    reading in a new version (by ``check``). None when no project or promise carries it."""
    from .record import Projects

    ps = Projects(loop.ledger)
    p = ps.by_idea(idea_id)
    if not p or not p.get("charter"):
        return None
    idea = loop.ledger.must(idea_id)
    claim = next(
        (c for c in (idea.get("pitch") or {}).get("claims") or [] if c.get("id") == k), None
    )
    c = json.loads(json.dumps(p["charter"]))
    hit = next((x for x in c.get("promises") or [] if x.get("claim") == k), None)
    if not claim or hit is None or hit["text"] == claim.get("interpreted"):
        return None
    hit["text"] = _line(claim["interpreted"], 400)
    hit["anchor"] = anchor_of(hit["text"])
    return save(ps, p["id"], c, "check", f"promise {k} reads as the accepted interpretation")

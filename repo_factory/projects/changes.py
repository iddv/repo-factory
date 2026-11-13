"""A project's change ideas and who decides them (PJ-PLAN.md §2.1, §4.5, §5).

A change idea is a row of the ledger's ``changes`` table: its project, a fingerprint (``fp``:
the kind and the title's content words, so a later run that proposes the same thing finds it),
its kind (``customer_ask``, ``feature``, ``maintenance``), its class (``patch``, ``minor``,
``scope``), its state and its card (what, why now, evidence, the scope test, the test, effort,
confidence, the runs that agreed, the model, the gate's verdict, a decline). The states are the
ledger's words with their meanings: ``new`` (listed on the project page), ``queued`` (accepted:
the issue or the request is filed, or about to be), ``building`` (a fix runs), ``held`` (the
fix needs the owner), ``closed`` (merged, declined or superseded), ``skipped`` (a duplicate),
``later`` ("not now", until ``later_until``).

**The class decides who decides** (``decide``): the autonomy preset (``projects.autonomy``:
``quiet`` files a patch on its own, asks once for a minor change and always for a scope change;
``hands-on`` asks for all; ``off`` lists only), each class overridable factory-wide and per
project; a scope change is never filed without the owner. The gate's numbers are settings too:
the least confidence, the runs that must agree per class, the kinds of evidence required. On a
repo the owner does not own, ``auto`` becomes ``ask`` (MX's rule). Nothing here calls a model."""

from __future__ import annotations

import hashlib
import json
import re
import time

from ..errors import LoopError
from ..stored import kept
from ..stored import projects as stored_projects

# each JSON column's contract (``repo_factory.stored.projects``)
CONTRACTS = {"card": stored_projects.ChangeCard, "filed": stored_projects.Filed}


def _stored(t, v) -> str:
    """A JSON column's text: the writer's value as it gave it, checked against its
    contract (a value the type cannot read is noted once)."""
    text = json.dumps(v)
    t.parse(text)
    return text


STATES = ("new", "queued", "building", "held", "closed", "skipped", "later")
KINDS = ("customer_ask", "feature", "maintenance")
CLASSES = ("patch", "minor", "scope")
PRESETS = {
    "quiet": {"patch": "auto", "minor": "ask", "scope": "ask"},
    "hands-on": {"patch": "ask", "minor": "ask", "scope": "ask"},
    "off": {"patch": "list", "minor": "list", "scope": "list"},
}
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
        "as",
        "when",
        "that",
        "this",
        "so",
        "than",
        "then",
        "make",
        "makes",
        "add",
        "adds",
        "use",
    ]
)
OPEN = ("new", "queued", "building", "held", "later")


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if w not in STOP]


def fp(kind: str, title: str) -> str:
    """The idea's fingerprint: its kind and the title's content words, sorted."""
    return hashlib.sha1(f"{kind}:{' '.join(sorted(set(words(title))))}".encode()).hexdigest()[:12]


def similar(a: str, b: str) -> float:
    """Token-set similarity (Jaccard) of two titles."""
    x, y = set(words(a)), set(words(b))
    return len(x & y) / len(x | y) if x and y else 0.0


# -- the store ------------------------------------------------------------------------------------
def _row(r) -> dict:
    d = dict(r)
    d["card"] = kept(stored_projects.ChangeCard, d.get("card"))
    d["filed"] = kept(stored_projects.Filed, d.get("filed"), none=True)
    return d


def get(ledger, cid: str) -> dict | None:
    with ledger.db() as c:
        r = c.execute("SELECT * FROM changes WHERE id = ?", (cid,)).fetchone()
    return _row(r) if r else None


def must(ledger, cid: str) -> dict:
    ch = get(ledger, cid)
    if ch is None:
        raise LoopError(f"no change idea {cid!r}")
    return ch


def all_of(ledger, pid: str | None = None, states: tuple | None = None) -> list[dict]:
    q, args = "SELECT * FROM changes WHERE 1 = 1", []
    if pid:
        q += " AND project = ?"
        args.append(pid)
    if states:
        q += f" AND state IN ({','.join('?' * len(states))})"
        args += list(states)
    with ledger.db() as c:
        return [_row(r) for r in c.execute(q + " ORDER BY created, id", args)]


def by_fp(ledger, pid: str, f: str) -> dict | None:
    with ledger.db() as c:
        r = c.execute("SELECT * FROM changes WHERE project = ? AND fp = ?", (pid, f)).fetchone()
    return _row(r) if r else None


def add(ledger, pid: str, card: dict, state: str = "new") -> dict:
    f = fp(card["kind"], card["title"])
    now = time.time()
    cid = "c" + hashlib.sha1(f"{pid}:{f}".encode()).hexdigest()[:8]
    with ledger.db() as c:
        c.execute(
            "INSERT INTO changes (id, project, fp, title, kind, class, state, card, created,"
            " updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                cid,
                pid,
                f,
                card["title"],
                card["kind"],
                card["class"],
                state,
                _stored(stored_projects.ChangeCard, card),
                now,
                now,
            ),
        )
    return must(ledger, cid)


def update(ledger, cid: str, **fields) -> dict:
    if "state" in fields and fields["state"] not in STATES:
        raise ValueError(f"state is one of {', '.join(STATES)}")
    sets = {
        k: (_stored(CONTRACTS[k], v) if k in CONTRACTS and v is not None else v)
        for k, v in fields.items()
    }
    sets["updated"] = time.time()
    with ledger.db() as c:
        c.execute(
            f"UPDATE changes SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
            (*sets.values(), cid),
        )
    return must(ledger, cid)


def counts(ledger, pid: str) -> dict:
    """``{"new", "asks", "accepted", "declined", "merged", ...}`` for the list and the stats."""
    out = {"new": 0, "asks": 0, "accepted": 0, "declined": 0, "tracked": 0}
    for ch in all_of(ledger, pid):
        c = ch["card"]
        if ch["state"] == "new" and c.get("ask"):
            out["asks"] += 1
        elif ch["state"] == "new":
            out["new"] += 1
        elif ch["state"] in ("queued", "building", "held"):
            out["accepted"] += 1
        elif ch["state"] == "skipped":
            out["tracked"] += 1
        elif c.get("declined"):
            out["declined"] += 1
    return out


# -- who decides ----------------------------------------------------------------------------------
def setting(cfg: dict, p: dict | None, dotted: str, default=None):
    """``projects.<dotted>``: the project's own override (its page's Settings), else the
    factory-wide value, else ``default``."""
    over = (p or {}).get("settings") or {}
    if dotted in over and over[dotted] is not None:
        return over[dotted]
    node = (cfg or {}).get("projects") or {}
    for k in dotted.split("."):
        node = node.get(k) if isinstance(node, dict) else None
        if node is None:
            return default
    return node


def autonomy(cfg: dict, p: dict | None = None) -> dict:
    """``{patch, minor, scope}``: each ``auto``, ``ask`` or ``list`` (the preset's, unless a
    class is set on its own); a scope change is never ``auto``."""
    preset = setting(cfg, p, "autonomy.preset", "quiet")  # a retired one reads as quiet
    out = dict(PRESETS.get(preset) or PRESETS["quiet"])
    for k in CLASSES:
        v = setting(cfg, p, f"autonomy.{k}")
        if v in ("auto", "ask", "list"):
            out[k] = v
    if out["scope"] == "auto":
        out["scope"] = "ask"
    return out


def evidence_kinds(card: dict) -> set[str]:
    kinds = {card.get("kind")}
    if any(str(e).startswith(("issue:", "discussion:")) for e in card.get("evidence") or []):
        kinds.add("customer_ask")
    return kinds


def decide(cfg: dict, p: dict, card: dict, owned: bool = True) -> tuple[str, str]:
    """``(verdict, why)``: ``file`` (on its own), ``ask`` (one row for the owner) or ``list``
    (on the page only), with why in words."""
    cls = card.get("class") or "minor"
    a = autonomy(cfg, p)[cls]
    if a == "list":
        return "list", f"a {cls} change is listed only ({setting(cfg, p, 'autonomy.preset')})"
    conf = float(card.get("confidence") or 0)
    least = float(setting(cfg, p, "gate.min_confidence", 0.5) or 0)
    if conf < least:
        return "list", f"confidence {conf:g} is under {least:g}"
    runs = int((card.get("runs") or {}).get("agreeing") or 1)
    # gate.min_agree_runs.<class>: a project's own override is kept under that whole key
    need = int(setting(cfg, p, f"gate.min_agree_runs.{cls}", 1) or 1)
    if cls != "scope" and runs < need:
        return "list", f"proposed by {runs} run{'s' if runs != 1 else ''}; it asks after {need}"
    req = set(setting(cfg, p, "gate.require_evidence_kinds", []) or [])
    if req and not req & evidence_kinds(card):
        return "list", "waits for evidence of a kind you require: " + ", ".join(sorted(req))
    if a == "auto" and not owned:
        return "ask", "the repository is not yours: repo-factory asks first"
    return (
        ("file", "filed on its own (autonomy)") if a == "auto" else ("ask", "waits for your answer")
    )  # the row names the class already (QA B17)


# -- the owner's answers ------------------------------------------------------------------------------
def accept(loop, cid: str, origin: str = "ui") -> dict:
    """Build it (a minor or patch ask, or a listed idea): the idea is ``queued`` and its issue
    or request is filed (``filing.file``, PJ-7) at once or on the next tick."""
    from .record import Projects

    ch = must(loop.ledger, cid)
    if ch["state"] not in ("new", "later"):
        raise LoopError(f"it is already {ch['state']}")
    if ch["class"] == "scope":
        raise LoopError("a scope change is accepted with its charter change (charter_change)")
    card = {**ch["card"], "accepted": {"at": time.time(), "by": origin}}
    card.pop("ask", None)
    ch = update(loop.ledger, cid, state="queued", card=card, later_until=None)
    Projects(loop.ledger).event(
        ch["project"], "change", f"accepted: {ch['title']}", change=cid, did="accept", by=origin
    )
    ch = file_now(loop, ch)
    if not ch.get("filed"):
        # filed on a later tick (GitHub refused, or no filer): the press is as undoable as a
        # filed one, and undoing it has nothing to close (QA B6: no Undo was offered)
        undo_s = float(((loop.cfg.get("build") or {}).get("undo_s")) or 10)
        ch = update(loop.ledger, cid, card={**ch["card"], "undo": {"until": time.time() + undo_s}})
    return ch


def file_now(loop, ch: dict) -> dict:
    """File an accepted idea when the filer is there (PJ-7); else it stays queued."""
    try:
        from .filing import file
    except ImportError:
        return ch
    return file(loop, ch)


def decline(loop, cid: str, how: str, why: str = "", origin: str = "ui") -> dict:
    """Not now (asleep ``projects.decline_days``) or Never (closed; recorded with why, so a
    later run that proposes it again is counted, not shown)."""
    from .record import Projects

    if how not in ("not_now", "never"):
        raise LoopError("how is not_now or never")
    ch = must(loop.ledger, cid)
    if ch["state"] not in ("new", "later"):
        raise LoopError(f"it is already {ch['state']}")
    p = Projects(loop.ledger).must(ch["project"])
    why = " ".join(str(why or "").split())[:300]
    card = {**ch["card"], "declined": {"at": time.time(), "by": origin, "how": how, "why": why}}
    card.pop("ask", None)
    if how == "never":
        ch = update(loop.ledger, cid, state="closed", card=card)
        note = f"declined for good: {ch['title']}" + (f" ({why})" if why else "")
    else:
        days = float(setting(loop.cfg.data, p, "decline_days", 30) or 30)
        ch = update(
            loop.ledger, cid, state="later", card=card, later_until=time.time() + days * 86400
        )
        note = f"not now ({days:g} days): {ch['title']}"
    Projects(loop.ledger).event(ch["project"], "change", note, change=cid, did=how, by=origin)
    return ch


def charter_change(loop, cid: str, accept_it: bool, why: str = "", origin: str = "ui") -> dict:
    """A scope ask: Accept writes the charter change as a new version by the owner, then the
    idea is accepted like any other (filed, never with go by repo-factory); Decline is Never."""
    from . import charter as C
    from .record import Projects

    ch = must(loop.ledger, cid)
    if ch["class"] != "scope":
        raise LoopError("only a scope change changes the charter")
    if not accept_it:
        return decline(loop, cid, "never", why, origin)
    if ch["state"] not in ("new", "later"):
        raise LoopError(f"it is already {ch['state']}")
    ps = Projects(loop.ledger)
    p = ps.must(ch["project"])
    new = apply(p.get("charter") or {}, ch["card"].get("charter_change"))
    if new is not None:
        C.save(ps, p["id"], new, "owner", f"accepted: {ch['title']}"[:80])
    card = {**ch["card"], "accepted": {"at": time.time(), "by": origin}}
    card.pop("ask", None)
    ch = update(loop.ledger, cid, state="queued", card=card, later_until=None)
    ps.event(
        p["id"],
        "change",
        f"accepted a scope change: {ch['title']}",
        change=cid,
        did="accept",
        by=origin,
    )
    return file_now(loop, ch)


FIELDS = ("promises", "non_goals", "constraints", "scope", "users", "open_directions")


def apply(charter: dict, change: dict | None) -> dict | None:
    """The charter with one line changed (``{field, from, to}``), or None when the change says
    nothing usable. Lines match by their words, so case and punctuation do not matter."""
    if not isinstance(change, dict) or change.get("field") not in FIELDS or not charter:
        return None
    f, old, to = change["field"], change.get("from"), change.get("to")
    c = json.loads(json.dumps(charter))
    lines = c.get(f) or []

    def text(x):
        return (
            x.get("text")
            if isinstance(x, dict) and "text" in x
            else x.get("direction")
            if isinstance(x, dict) and "direction" in x
            else x.get("who")
            if isinstance(x, dict)
            else x
        )

    def make(t):
        from .charter import anchor_of

        if f == "promises":
            return {"text": t, "anchor": anchor_of(t)}
        if f == "open_directions":
            return {"direction": t, "why_in_spirit": ""}
        if f == "users":
            return {"who": t, "needs": ""}
        return t

    at = next((i for i, x in enumerate(lines) if old and similar(text(x), old) >= 0.6), None)
    if old and at is None:
        return None
    if at is not None and to:
        keep = lines[at] if isinstance(lines[at], dict) else {}
        lines[at] = {**keep, **make(to)} if isinstance(keep, dict) and keep else make(to)
    elif at is not None:
        del lines[at]
    elif to:
        lines.append(make(to))
    else:
        return None
    c[f] = lines
    return c


def asks(ledger, pid: str | None = None) -> list[dict]:
    """The ideas that wait for the owner: new, with the gate's ask."""
    return [ch for ch in all_of(ledger, pid, ("new",)) if ch["card"].get("ask")]

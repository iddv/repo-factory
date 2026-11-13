"""The evolve run: *given this repo, its charter and its issues, what can we improve?* One no-tools call (``projects.evolve.model``, the registered prompt ``evolve``) a
project, on a cadence, in a process of its own (``repo-factory projects evolve ID``; never in a request).

1. **Gather** (``sources.gather``, no model): the repo's documents and own open items, the drift
   checks, its issues and pull requests, a code digest, and what changed since the last
   run. With
   ``evolve.skip_when_quiet`` a run whose sources did not change makes **no call**.
2. **Ask** the model, with the charter and the ideas already known (and the ones declined, with
   why) as context; at most ``evolve.max_ideas`` proposals, each with evidence, a kind and a
   class.
3. **Guard the class in code** (``guard``): a feature is never a patch; a proposal whose words
   match a non-goal is a scope change. Code only ever raises a class.
4. **Merge** (``merge``): a proposal with a known fingerprint updates its idea (one more
   agreeing run, the newest words, the evidence added); one declined for good is counted and
   dropped; one that matches an open issue or pull request (``evolve.dup_sim``) is kept as
   "already tracked"; the rest are new.
5. **Decide** (``changes.decide``): each new idea is filed on its own, asked about, or listed,
   by the class's autonomy and the gate's numbers.

Spend is recorded as kind ``evolve`` with the project's id. The run's summary is kept in the
ledger's meta (``project_evolve:<id>``) and one Timeline line."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from string import Template

from ..errors import LoopError
from . import changes as X

MAX_DROPPED = 6


def model_for(cfg, p: dict | None = None):
    """``projects.evolve.model``, or the project's own (its page's Settings)."""
    from ..loop.models import claude

    m = X.setting(cfg.data if hasattr(cfg, "data") else cfg, p, "evolve.model")
    return claude(m, purpose="evolve", timeout_s=900)


def model_of(loop, p: dict | None = None):
    """The run's model (``p``: the project, for its own override), or None where project model
    calls are off (projects disabled, or ``$REPO_FACTORY_CHARTER=0``: the tests, which give a
    fake)."""
    m = getattr(loop, "_evolve", None)
    if m is not None:
        return m
    if (
        not (loop.cfg.get("projects") or {}).get("enabled", False)
        or os.environ.get("REPO_FACTORY_CHARTER") == "0"
    ):
        return None
    return model_for(loop.cfg, p)


# -- the prompt -------------------------------------------------------------------------------------
def _cut(text: str, n: int) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


def known_text(ledger, pid: str) -> str:
    rows = X.all_of(ledger, pid)
    if not rows:
        return "No ideas have been proposed for this project yet."
    out = []
    for ch in rows:
        c = ch["card"]
        d = c.get("declined") or {}
        out.append(
            f"- [{ch['class']}, {ch['state']}] {ch['title']}"
            + (
                f" (declined {'for good' if d.get('how') == 'never' else 'for now'}"
                + (f": {d.get('why')}" if d.get("why") else "")
                + ")"
                if d
                else ""
            )
            + (f" (already tracked as {c['duplicate_of']})" if c.get("duplicate_of") else "")
        )
    return "\n".join(out[:60])


def repo_text(loop, p: dict, g: dict) -> str:
    from . import charter as C
    from . import clones

    root = clones.root_of(loop.cfg.home, p)
    parts = [C.repo_text(root if root.is_dir() else None)]
    parts.append(
        "## The repository's own open items\n" + json.dumps(g.get("backlog") or {}, indent=1)[:8000]
    )
    # the drift findings are not shown: code files them as maintenance (the
    # project's health and Timeline, Settings › Publishing › Maintenance), so the model never sees a chore
    # as a candidate idea; they still count as a change for skip_when_quiet (sources.gather)
    parts.append(
        "## Open issues and pull requests\n" + ((g.get("issues") or {}).get("text") or "none")
    )
    if g.get("digest"):
        parts.append("## The code, mapped (no bodies)\n" + g["digest"])
    return "\n\n".join(parts)


def world_text(g: dict) -> str:
    """The world outside the repository: nothing is read from outside it in this version."""
    return "Nothing from outside the repository was gathered for this project."


def prompt(loop, p: dict, g: dict) -> str:
    from ..custom_prompts import text
    from . import charter as C

    mx = int(X.setting(loop.cfg.data, p, "evolve.max_ideas", 8) or 8)
    world = world_text(g)
    return Template(text("evolve")).safe_substitute(
        CHARTER=C.to_text(p["charter"])
        if p.get("charter")
        else "(no charter yet: use the repository's README and scope as the charter)",
        REPO=repo_text(loop, p, g),
        WORLD=world,
        KNOWN=known_text(loop.ledger, p["id"]),
        MAX=str(mx),
    )


def parse(reply: str) -> dict | None:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", reply or "", re.S)
    raw = m.group(1) if m else (reply or "")[(reply or "").find("{") :]
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("ideas"), list) else None


def _line(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def clean(raw) -> dict | None:
    """One proposal from the reply, cleaned; None when it is not one (no title, an unknown
    kind or class, no evidence)."""
    if not isinstance(raw, dict):
        return None
    title = _line(raw.get("title"), 120)
    kind, cls = raw.get("kind"), raw.get("class")
    ev = [_line(e, 300) for e in raw.get("evidence") or [] if _line(e, 300)]
    if not title or kind not in X.KINDS or cls not in X.CLASSES or not ev:
        return None
    try:
        conf = max(0.0, min(1.0, float(raw.get("confidence") or 0)))
    except (TypeError, ValueError):
        conf = 0.0
    cc = raw.get("charter_change")
    return {
        "title": title,
        "kind": kind,
        "class": cls,
        "scope_test": _line(raw.get("scope_test"), 400),
        "what": _line(raw.get("what"), 1200),
        "why_now": _line(raw.get("why_now"), 800),
        "evidence": ev[:12],
        "duplicate_of": _line(raw.get("duplicate_of"), 120) or None,
        "charter_change": {k: (_line(cc.get(k), 400) or None) for k in ("field", "from", "to")}
        if isinstance(cc, dict)
        else None,
        "effort": raw.get("effort") if raw.get("effort") in ("S", "M", "L") else None,
        "confidence": round(conf, 2),
        "test": _line(raw.get("test"), 600),
        "risk": _line(raw.get("risk"), 400),
    }


# -- the class, guarded in code ---------------------------------------------------------------------
def guard(card: dict, charter: dict | None) -> dict:
    """Raise the class where code can tell (never lower it): a feature is at least minor; a
    proposal whose words cover half of a non-goal's is a scope change (``raised`` says why)."""
    order = {c: i for i, c in enumerate(X.CLASSES)}
    out = dict(card)

    def up(cls, why):
        if order[cls] > order[out["class"]]:
            out["class"], out["raised"] = cls, why

    if out["kind"] == "feature":
        up("minor", "a feature is never a patch")
    mine = set(X.words(f"{out['title']} {out['what']}"))
    for ng in (charter or {}).get("non_goals") or []:
        head = set(X.words(re.split(r"[:—–(]", ng)[0]))
        if len(head) >= 2 and len(head & mine) / len(head) >= 0.5:
            up("scope", f"it matches a non-goal: {ng}")
            break
    return out


def cap_maintenance(cards: list[dict], most) -> tuple[list[dict], list[str]]:
    """At most ``evolve.max_maintenance`` proposals of kind ``maintenance`` (the first ones,
    in the reply's order); the rest are dropped, their titles returned. None: no limit."""
    if most is None:
        return cards, []
    try:
        most = max(0, int(most))
    except (TypeError, ValueError):
        most = 1
    keep, over, n = [], [], 0
    for c in cards:
        if c["kind"] == "maintenance":
            n += 1
            if n > most:
                over.append(c["title"])
                continue
        keep.append(c)
    return keep, over


# -- merge --------------------------------------------------------------------------------------------
def tracked(card: dict, items: list[dict], at: float) -> dict | None:
    """The open issue or pull request this proposal duplicates (title similarity ≥ ``at``)."""
    best, score = None, 0.0
    for it in items or []:
        s = X.similar(card["title"], it.get("title") or "")
        if s > score:
            best, score = it, s
    return best if best is not None and score >= at else None


def merge(loop, p: dict, cards: list[dict], g: dict, run: dict, now: float) -> dict:
    """Fold a run's proposals into the project's ideas (see the module's doc, step 4)."""
    out = {"new": [], "agreed": [], "tracked": [], "declined": 0}
    at = float(X.setting(loop.cfg.data, p, "evolve.dup_sim", 0.6) or 0.6)
    items = (g.get("issues") or {}).get("items") or []
    for card in cards:
        old = X.by_fp(loop.ledger, p["id"], X.fp(card["kind"], card["title"]))
        if old:
            c = old["card"]
            if (c.get("declined") or {}).get("how") == "never":
                out["declined"] += 1
                continue
            runs = dict(c.get("runs") or {})
            if runs.get("run") == run["id"]:
                continue
            runs.update(agreeing=int(runs.get("agreeing") or 1) + 1, last=now, run=run["id"])
            ev = list(dict.fromkeys([*(c.get("evidence") or []), *card["evidence"]]))[:20]
            keep = {
                k: c[k] for k in ("accepted", "declined", "ask", "gate", "filed_note") if k in c
            }
            if old["state"] == "skipped":  # already tracked: it keeps the issue it tracks
                keep.update({k: c[k] for k in ("duplicate_of", "duplicate_url") if k in c})
            X.update(
                loop.ledger,
                old["id"],
                card={
                    **card,
                    **keep,
                    "evidence": ev,
                    "runs": runs,
                    "model": run["model"],
                    "run_id": run["id"],
                    "charter_v": p.get("charter_v") or 0,
                },
                title=card["title"],
            )
            out["agreed"].append(old["id"])
            continue
        card = {
            **card,
            "runs": {"first": now, "last": now, "agreeing": 1, "run": run["id"]},
            "model": run["model"],
            "run_id": run["id"],
            "charter_v": p.get("charter_v") or 0,
        }
        dup = tracked(card, items, at)
        if dup:
            card["duplicate_of"] = (
                f"{'PR' if dup.get('kind') in ('pr', 'factory_pr') else 'issue'}"
                f" #{dup.get('number')}"
            )
            card["duplicate_url"] = dup.get("url")
            ch = X.add(loop.ledger, p["id"], card, state="skipped")
            out["tracked"].append(ch["id"])
            continue
        out["new"].append(X.add(loop.ledger, p["id"], card)["id"])
    return out


def owned(loop, p: dict) -> bool:
    """A repo repo-factory built is the owner's; an adopted one is when its profile says so."""
    if p["kind"] != "adopted":
        return True
    from ..maintain import profile as Pf

    repo = next((r.get("repo") for r in p.get("repos") or [] if r.get("repo")), None)
    if repo is None:  # no repo recorded: nothing says it is the owner's
        return False
    prof = Pf.load(loop.cfg.home, repo) or {}
    return bool((prof.get("access") or {}).get("owned"))


def gate(loop, p: dict, now: float | None = None) -> dict:
    """Decide each new idea of the project (``changes.decide``): file, ask or list. Also wakes
    a "not now" idea whose time came. Returns ``{"filed", "asked", "listed"}`` ids."""
    now = now or time.time()
    out = {"filed": [], "asked": [], "listed": []}
    mine = owned(loop, p)
    for ch in X.all_of(loop.ledger, p["id"], ("later",)):
        if (ch.get("later_until") or 0) <= now and not (ch["card"].get("declined") or {}).get(
            "how"
        ) == "never":
            X.update(loop.ledger, ch["id"], state="new", later_until=None)
    for ch in X.all_of(loop.ledger, p["id"], ("new",)):
        verdict, why = X.decide(loop.cfg.data, p, ch["card"], owned=mine)
        card = {**ch["card"], "gate": {"verdict": verdict, "why": why, "at": now}}
        if verdict == "ask":
            card["ask"] = ch["card"].get("ask") or {"since": now}
            X.update(loop.ledger, ch["id"], card=card)
            out["asked"].append(ch["id"])
        elif verdict == "file":
            card.pop("ask", None)
            X.update(loop.ledger, ch["id"], card=card)
            try:
                X.accept(loop, ch["id"], origin="repo-factory")
                out["filed"].append(ch["id"])
            except LoopError:
                pass
        else:
            card.pop("ask", None)
            X.update(loop.ledger, ch["id"], card=card)
            out["listed"].append(ch["id"])
    return out


# -- the run ------------------------------------------------------------------------------------------
def last_run(ledger, pid: str) -> dict:
    return dict(ledger.meta(f"project_evolve:{pid}") or {})


def run(
    loop,
    pid: str,
    model=None,
    *,
    now: float | None = None,
    force: bool = False,
    gh=None,
    cache=None,
    git_fn=None,
    latest=None,
) -> dict:
    """One evolve run of project ``pid`` (see the module's doc). ``force``: run even when
    nothing changed (Check for ideas now). Returns the run's summary."""
    from ..loop.models import spend_kw, split
    from . import sources
    from .record import Projects

    now = now or time.time()
    ps = Projects(loop.ledger)
    p = ps.must(pid)
    last = last_run(loop.ledger, pid)
    g = sources.gather(
        loop,
        p,
        gh=gh,
        now=now,
        cache=cache,
        last=last.get("fingerprint"),
        git_fn=git_fn,
        latest=latest,
    )
    quiet = X.setting(loop.cfg.data, p, "evolve.skip_when_quiet", True)
    if not force and quiet and last.get("fingerprint") and not g["changed"].get("any"):
        rec = {**last, "checked": now, "skipped": "nothing changed"}
        loop.ledger.meta(f"project_evolve:{pid}", rec)
        return {"skipped": "nothing changed", "since": last.get("at")}
    # the loop stops asking while you have not answered: with
    # projects.asks_open_max (3) minor asks unanswered, a run proposes nothing new and makes no
    # call; Check for ideas now (force) still runs
    most = X.setting(loop.cfg.data, p, "asks_open_max", 3)
    open_asks = [ch for ch in X.asks(loop.ledger, pid) if ch["class"] != "scope"]
    if (
        not force
        and isinstance(most, int)
        and not isinstance(most, bool)
        and most > 0
        and len(open_asks) >= most
    ):
        said = f"{len(open_asks)} asks open; not proposing more"
        loop.ledger.meta(f"project_evolve:{pid}", {**last, "checked": now, "skipped": said})
        return {"skipped": said}
    cap = X.setting(loop.cfg.data, p, "budget.monthly_usd")
    spent = float(((p.get("stats") or {}).get("spend") or {}).get("month") or 0)
    if cap is not None and spent >= float(cap):
        return {"skipped": f"over the project's monthly cap ({spent:.2f} of {float(cap):.2f})"}
    model = model or model_of(loop, p)
    if model is None:
        return {"skipped": "project model calls are off here"}
    run_rec = {"id": uuid.uuid4().hex[:12], "model": X.setting(loop.cfg.data, p, "evolve.model")}
    text, usd, tokens, out = prompt(loop, p, g), 0.0, None, None
    from ..loop.models import add as add_tokens

    for _ in range(2):  # one more call when the reply is not usable
        reply, u, t = split(model(text))
        usd, tokens = usd + float(u or 0), add_tokens(tokens, t)
        out = parse(reply)
        if out is not None:
            break
        text += (
            '\n\nYour previous reply was not one JSON object with an "ideas" list. '
            "Reply again with only that object."
        )
    loop.ledger.spend("evolve", usd, p.get("idea"), project=pid, **spend_kw(tokens))
    if out is None:
        rec = {**last, "checked": now, "error": "the reply was not usable twice"}
        loop.ledger.meta(f"project_evolve:{pid}", rec)
        raise LoopError(f"{pid}: the evolve reply was not usable twice")
    mx = int(X.setting(loop.cfg.data, p, "evolve.max_ideas", 8) or 8)
    cards, over = cap_maintenance(
        [guard(c, p.get("charter")) for c in (clean(r) for r in out["ideas"]) if c],
        X.setting(loop.cfg.data, p, "evolve.max_maintenance", 1),
    )
    cards = cards[:mx]
    got = merge(loop, p, cards, g, run_rec, now)
    decided = gate(loop, ps.must(pid), now)
    dropped = [
        {"title": _line(d.get("title"), 200), "why": _line(d.get("why"), 300)}
        for d in out.get("considered_and_dropped") or []
        if isinstance(d, dict)
    ]
    rec = {
        "fingerprint": g["fingerprint"],
        "at": now,
        "checked": now,
        "run": run_rec["id"],
        "model": run_rec["model"],
        "usd": round(usd, 6),
        "tokens": tokens,
        "proposed": len(cards),
        "new": len(got["new"]),
        "agreed": len(got["agreed"]),
        "tracked": len(got["tracked"]),
        "declined": got["declined"],
        "filed": len(decided["filed"]),
        "asked": len(decided["asked"]),
        "dropped": dropped[:MAX_DROPPED],
        "maintenance_dropped": over,
        "notes": _line(out.get("notes"), 1200),
        "errors": g.get("errors") or None,
        "search": None,
    }
    loop.ledger.meta(f"project_evolve:{pid}", rec)
    bits = [f"{len(got['new'])} new", f"{len(got['agreed'])} proposed again"]
    if got["tracked"]:
        bits.append(f"{len(got['tracked'])} already tracked")
    if got["declined"]:
        bits.append(f"{got['declined']} you declined")
    ps.event(
        pid,
        "evolve",
        "checked for ideas: " + ", ".join(bits),
        run=run_rec["id"],
        model=run_rec["model"],
        proposed=len(cards),
    )
    return rec


def due(loop, p: dict, now: float | None = None) -> str | None:
    """Why project ``p``'s run is due now, else None: the cadence (``evolve.every_days`` after
    the last run, or after the project was made), or sooner (never closer than
    ``evolve.min_hours_between``) when the owner edited the charter since the last run."""
    now = now or time.time()
    if not p.get("charter") or p.get("state") != "active":
        return None
    last = last_run(loop.ledger, p["id"])
    every = float(X.setting(loop.cfg.data, p, "evolve.every_days", 7) or 7) * 86400
    since = float(last.get("checked") or last.get("at") or p.get("created") or now)
    if now - since >= every:
        return "the weekly check" if every == 7 * 86400 else "the cadence"
    gap = float(X.setting(loop.cfg.data, p, "evolve.min_hours_between", 24) or 0) * 3600
    if last.get("at") and now - float(last.get("checked") or last["at"]) >= gap:
        from .charter import versions

        v = versions(loop.ledger, p["id"])
        if v and v[0]["by"] == "owner" and v[0]["at"] > float(last["at"]):
            return "you edited the charter"
    return None

"""The Brief stage (the pitch): one strong no-tools call turns a typed task into a product
name and a broad brief.

The contract (``check``): ``verdict`` accept|reject; ``product``; ``product_type`` (web
app, cli, api, library); ``one_liner``; ``who``; ``why_now``; ``buildability`` in [0, 1]
with ``buildability_reasons``; and for an accepted task a ``brief`` of 2-4 sentences. A
reply that breaks it is asked again once. A task the person typed is never rejected and has
no floor (``owner_pick``): a reply that would have passed is kept as ``advice``. ``run``
also applies ``pitch.min_buildability`` and the score gates (``pitch.min_insight``,
``pitch.min_differentiation``, ``pitch.min_grounding``; unset: no gate) to a task that is not
the person's pick, with ``reject_words``: the reason in plain words for the build's page.

An accepted task becomes a task folder (``repo-factory new``'s ``new_task``) in the tasks
dir: ``spec.md`` is ``# <product>``, a ``Shape: <users>, <interface>, <money>`` line computed
from ``product_type`` and ``who``, the brief, and the pitch's ``grounding`` lines (when it
returns them) as a ``Grounding:`` block; v5's scope stage takes it from there. ``loop.json``
in the task dir records the task and its brief."""

from __future__ import annotations

import json
import re
from pathlib import Path
from string import Template

from .ledger import Ledger

PROMPT = Path(__file__).with_name("prompts") / "pitch.md"
PRODUCT_TYPES = ("web app", "cli", "api", "library")
TYPE_ALIASES = {
    "webapp": "web app",
    "web": "web app",
    "web application": "web app",
    "command-line tool": "cli",
    "command line tool": "cli",
    "http api": "api",
    "service": "api",
    "python library": "library",
}
# the scores a pitch prompt may return beside buildability, and the loop.yaml key that gates each
SCORES = (
    ("insight", "min_insight"),
    ("differentiation", "min_differentiation"),
    ("grounding_score", "min_grounding"),
    ("durability", "min_durability"),
    ("breadth", "min_breadth"),
)
MAX_GROUNDING = 8  # lines carried into spec.md


def _score(v) -> str:
    """A 0..1 score as the notes say it; "not scored" when the pitch gave none."""
    return f"{v:.2f}" if isinstance(v, (int, float)) and not isinstance(v, bool) else "not scored"


def idea_block(idea: dict) -> str:
    """The task as the pitch reads it: the person's words, and that they asked for it."""
    return SENT + "\n\n" + idea_text(idea)


# every task was typed by the person who runs repo-factory: the pitch writes its brief
SENT = (
    "The owner typed this task and sent it to build: write its brief; do not reject it. "
    "Put any doubt in risks."
)


def idea_text(idea: dict) -> str:
    out = (idea.get("text") or "").strip()
    if idea.get("context"):
        out += "\n\nMore context:\n" + idea["context"].strip()
    return out


def owner_pick(idea: dict) -> bool:
    """A task the person typed (every task here): the pitch writes its brief and never
    rejects it, with no floors."""
    return True


def as_brief(p: dict, idea: dict) -> dict:
    """A pitch reply that passed on the person's task, made its brief: the pitch's words where
    it wrote them, else the task as written; its doubt kept as advice for the build's page."""
    advice = reject_words(p)
    brief = (p.get("brief") or "").strip() or (idea.get("text") or "").strip()
    return {
        **{k: v for k, v in p.items() if k not in ("gated", "reject_words")},
        "verdict": "accept",
        "product": p.get("product") or idea["title"],
        "one_liner": p.get("one_liner") or "",
        "who": p.get("who") or "",
        "brief": brief,
        "advice": f"The pitch would have passed: {advice}",
        "reject_reason": None,
    }


def prompt(idea: dict) -> str:
    """``prompts/pitch.md`` with the owner's house rules and additions (``custom_prompts``)."""
    from ..custom_prompts import text

    return Template(text("pitch")).safe_substitute(IDEA=idea_block(idea))


def prompt_record() -> dict:
    """The pitch prompt's id and version, kept with the pitch (what produced it)."""
    from ..custom_prompts import effective

    eff = effective("pitch", noted=False)
    return {"id": "pitch", "version": eff.version, "n": eff.n, "custom": eff.custom}


def parse(text: str) -> dict | None:
    """The first JSON object in the reply (fenced or bare)."""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    cand = m.group(1) if m else t[t.find("{") : t.rfind("}") + 1] if "{" in t else ""
    try:
        obj = json.loads(cand)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def sentences(s: str) -> int:
    parts = [
        p
        for p in re.split(r"(?<=[.!?])\s+|(?<=[a-z0-9])\s*\n+\s*", (s or "").strip())
        if re.search(r"\w", p)
    ]
    return len(parts)


def normalise(p: dict) -> dict:
    p = dict(p)
    pt = str(p.get("product_type") or "").strip().lower()
    p["product_type"] = TYPE_ALIASES.get(pt, pt)
    v = str(p.get("verdict") or "").strip().lower()
    p["verdict"] = {"accepted": "accept", "rejected": "reject"}.get(v, v)
    try:
        p["buildability"] = float(p.get("buildability"))
    except (TypeError, ValueError):
        pass
    for k, _ in SCORES:  # only a prompt that scores them returns them
        if p.get(k) is None:
            p.pop(k, None)
            continue
        try:
            p[k] = float(p[k])
        except (TypeError, ValueError):
            pass
    g = p.get("grounding")
    if isinstance(g, str):
        g = [g]
    if isinstance(g, list):
        p["grounding"] = [
            " ".join(str(x).split())[:400]
            for x in g
            if isinstance(x, (str, int, float)) and str(x).strip()
        ][:MAX_GROUNDING]
    else:
        p.pop("grounding", None)
    for k in ("buildability_reasons", "risks"):
        if isinstance(p.get(k), str):
            p[k] = [p[k]]
    for k in ("product", "one_liner", "who", "why_now", "brief", "reject_reason"):
        if isinstance(p.get(k), str):
            p[k] = " ".join(p[k].split()) if k != "brief" else p[k].strip()
    # how each claim that can't be honoured as written is read (a reply without
    # the field, or with entries that are not readings, records none)
    from . import claims

    cs = p.get("claims")
    if isinstance(cs, list):
        p["claims"] = claims.merge(
            [], [c for c in (claims.clean(x, "pitch") for x in cs[: claims.MAX_CLAIMS]) if c]
        )
    else:
        p.pop("claims", None)
    return p


BUSINESS = re.compile(
    r"\b(staff|customers?|(?<!chat )(?<!api )(?<!mcp )(?<!email )clients?|"
    r"patients?|clinics?|shops?|stores?|offices?|front desk|receptionists?|"
    r"employees?|tenants?|businesses?|restaurants?|salons?)\b",
    re.I,
)
TEAM = re.compile(r"\b(teams?|maintainers|colleagues|crews?|squads?)\b", re.I)
ONE_PERSON = re.compile(r"^\s*(a|an|one)\b", re.I)  # "A solo developer who ...", not "Teams"
# money only when the product itself takes, makes or records payments (scope v1's `money`)
MONEY = re.compile(
    r"\b(invoic\w*|billing|payments?|refunds?|checkout|subscriptions?|"
    r"charg(e|es|ed|ing) (its |their )?(customers|clients|members|patients|users))\b",
    re.I,
)


def shape(p: dict) -> str | None:
    """``Shape: <users>, <interface>, <money>`` for the scope, computed in code:
    users from ``who`` (``business`` when it names staff, customers, a clinic or a shop;
    ``small team`` when teams or maintainers, not one person on a team; else ``single
    user``), the interface from ``product_type``, money only when the one-liner or brief has
    the product take or record payments (invoices, billing, refunds, subscriptions). None
    without a known product type or a ``who``."""
    pt, who = p.get("product_type"), str(p.get("who") or "")
    if pt not in PRODUCT_TYPES or not who.strip():
        return None
    users = (
        "business"
        if BUSINESS.search(who)
        else "small team"
        if TEAM.search(who) and not ONE_PERSON.match(who)
        else "single user"
    )
    words = f"{p.get('one_liner') or ''} {p.get('brief') or ''}"
    return f"Shape: {users}, {pt}, {'money' if MONEY.search(words) else 'no money'}"


def spec_text(p: dict) -> str:
    """The task's ``spec.md``: the product's name, its shape, the brief, the facts from the
    sources the build must keep (``Grounding:``, when the pitch returns them), and how its
    claims are read."""
    from . import claims

    sh = shape(p)
    out = (
        f"# {p['product']}\n\n"
        + (f"{sh}\n\n" if sh else "")
        + f"{(p.get('brief') or '').strip()}\n"
    )
    lines = [x for x in p.get("grounding") or [] if isinstance(x, str) and x.strip()]
    if lines:
        out += "\nGrounding:\n" + "".join(f"- {x}\n" for x in lines[:MAX_GROUNDING])
    extra = claims.spec_section(p.get("claims") or [])
    return out + ("\n" + extra if extra else "")


def check(p: dict) -> list[str]:
    """Contract problems (empty: the reply is usable)."""
    bad = []
    if p.get("verdict") not in ("accept", "reject"):
        bad.append("verdict must be accept or reject")
    if not isinstance(p.get("buildability"), float) or not 0 <= p["buildability"] <= 1:
        bad.append("buildability must be a number from 0 to 1")
    if not p.get("buildability_reasons"):
        bad.append("buildability_reasons must list the reasons for the score")
    for k, _ in SCORES:
        if k in p and (not isinstance(p[k], float) or not 0 <= p[k] <= 1):
            bad.append(f"{k} must be a number from 0 to 1")
    for k in ("product", "who"):
        if not p.get(k):
            bad.append(f"{k} is missing")
    if p.get("verdict") == "accept":
        if p.get("product_type") not in PRODUCT_TYPES:
            bad.append(f"product_type must be one of {', '.join(PRODUCT_TYPES)}")
        for k in ("one_liner", "why_now", "brief"):
            if not p.get(k):
                bad.append(f"{k} is missing")
        n = sentences(p.get("brief") or "")
        if p.get("brief") and not 2 <= n <= 4:
            bad.append(f"brief must be 2-4 sentences (it has {n})")
        if len((p.get("brief") or "").split()) > 120:
            bad.append("brief must be short: under 120 words")
        if re.search(r"^\s*(#|[-*] )", p.get("brief") or "", re.M):
            bad.append("brief must be plain sentences: no headings or bullets")
    elif p.get("verdict") == "reject" and not p.get("reject_reason"):
        bad.append("reject_reason is missing")
    return bad


LABELS = {
    "buildability": "buildability",
    "insight": "insight",
    "differentiation": "differentiation",
    "grounding_score": "grounding",
    "durability": "durability",
    "breadth": "breadth",
}


def gates_of(cfg_pitch: dict | None) -> dict:
    """``{score: minimum}`` from loop.yaml's ``pitch.min_insight``, ``min_differentiation``
    and ``min_grounding``; a key unset (null) is no gate."""
    c, out = cfg_pitch or {}, {}
    for k, key in SCORES:
        try:
            if c.get(key) is not None and not isinstance(c[key], bool):
                out[k] = float(c[key])
        except (TypeError, ValueError):
            pass  # Config.problems names a wrong value; it gates nothing
    return out


ALREADY = re.compile(
    r"already (?:exists|shipped|ships|covered)\s*(?:by|as|in)?\s*:?\s*"
    r"([^.;,(]*)",
    re.I,
)
LOWER_FIRST = {
    "it",
    "the",
    "this",
    "a",
    "an",
    "no",
    "not",
    "there",
    "its",
    "too",
    "already",
    "only",
    "one",
    "nothing",
}


def first_existing(p: dict, reason_first: bool = False) -> str | None:
    """The existing tool to name: the one the reject reason names (``already exists: X``)
    or the first of ``existing``, in that order when ``reason_first``, else the other way."""
    m = ALREADY.search(p.get("reject_reason") or "")
    said = m.group(1).strip() if m and m.group(1).strip() else None
    listed = None
    for x in p.get("existing") or []:
        name = x.get("name") if isinstance(x, dict) else x if isinstance(x, str) else None
        if name and str(name).strip():
            listed = re.split(r"\s*[(,;]", str(name).strip())[0] or str(name).strip()
            break
    return (said or listed) if reason_first else (listed or said)


def reject_words(p: dict, gated: str | None = None) -> str:
    """Why the task was rejected, in plain words, for the build's page
    (``Rejected: <this>``)."""
    pitched = gated is None and ALREADY.search(p.get("reject_reason") or "")
    if gated == "differentiation" or pitched:
        name = first_existing(p, reason_first=bool(pitched))
        return (
            f"too close to {name}, which already does this"
            if name
            else "too close to tools that already do this"
        )
    if gated == "insight":
        return "nothing new in it: it restates a trend its sources already show"
    if gated == "grounding_score":
        return "its specifics don't come from its sources"
    if gated == "buildability":
        return "too big or too vague to build in one go"
    why = " ".join(str(p.get("reject_reason") or "").split())
    if not why:
        return "the pitch passed on it"
    why = re.split(r"(?<=[.!?])\s", why, maxsplit=1)[0].rstrip(".")
    first = why.split()[0]
    return why[0].lower() + why[1:] if first.lower() in LOWER_FIRST else why


def run(
    idea: dict,
    model,
    min_buildability: float = 0.6,
    gates: dict | None = None,
    body: str | None = None,
) -> dict:
    """``{"decision": accepted|rejected|malformed, "pitch": {...}, "usd", "problems",
    "tokens"}`` (``tokens``: ``{"in", "out"}`` over the calls, None when not reported).
    ``gates``: ``{score: minimum}`` (``gates_of``) applied, after buildability, to an
    accepted task whose reply carries that score. ``body``: the prompt as already rendered
    (a candidate prompt), instead of ``prompt(idea)``."""
    from .models import add, split

    body = body if body is not None else prompt(idea)
    used = prompt_record()
    usd, problems, p, tokens = 0.0, [], None, None
    for attempt in range(2):
        text, cost, tok = split(
            model(
                body
                if attempt == 0
                else body + "\n\nYour previous reply "
                "broke the contract: " + "; ".join(problems) + ". Reply "
                "again with the whole JSON object."
            )
        )
        usd += cost or 0.0
        tokens = add(tokens, tok)
        raw = parse(text)
        if raw is None:
            problems = ["the reply had no JSON object"]
            continue
        p = normalise(raw)
        problems = check(p)
        if not problems:
            break
    if p is None or problems:
        return {
            "decision": "malformed",
            "pitch": p,
            "usd": usd,
            "problems": problems,
            "tokens": tokens,
            "prompt": used,
        }
    floors = {"buildability": float(min_buildability), **(gates or {})}
    reasons = {
        k: (p.get("score_reasons") or {}).get(LABELS[k])
        if isinstance(p.get("score_reasons"), dict)
        else None
        for k in floors
    }
    reasons["buildability"] = "; ".join(p.get("buildability_reasons") or [])
    for k, floor in floors.items():
        if p["verdict"] != "accept":
            break
        if isinstance(p.get(k), float) and p[k] < floor:
            p = {
                **p,
                "verdict": "reject",
                "gated": {"score": k, "value": p[k], "min": floor},
                "reject_reason": f"{LABELS[k]} {p[k]:.2f} is under the loop's minimum "
                f"{floor:.2f}" + (f": {reasons[k]}" if reasons.get(k) else ""),
            }
    if p["verdict"] == "reject":
        p = {**p, "reject_words": reject_words(p, (p.get("gated") or {}).get("score"))}
    return {
        "decision": "accepted" if p["verdict"] == "accept" else "rejected",
        "pitch": p,
        "usd": usd,
        "problems": [],
        "tokens": tokens,
        "prompt": used,
    }


def make_task(idea: dict, p: dict, tasks_dir: Path) -> Path:
    """``repo-factory new`` in the tasks dir: ``<product slug>``, then ``-2``, ``-3`` if taken."""
    from ..tasks import new_task, slug

    tasks_dir = Path(tasks_dir).expanduser()
    tasks_dir.mkdir(parents=True, exist_ok=True)
    base = "-".join(slug(p["product"]).split("-")[:5])[:40].strip("-") or "product"
    dest, n = tasks_dir / base, 2
    while dest.exists():
        dest, n = tasks_dir / f"{base}-{n}", n + 1
    new_task(
        p["product"],
        dest=dest,  # the task id (and branch) from the name
        spec_text=spec_text(p),
    )
    (dest / "loop.json").write_text(
        json.dumps(
            {
                "idea": idea["id"],
                "source": idea["source"],
                "source_ref": idea.get("source_ref"),
                "title": idea.get("title"),
                "urls": idea.get("urls") or [],
                "pitch": p,
            },
            indent=1,
        )
    )
    return dest


def sent_mark(pitch: dict | None) -> dict:
    """``{"sent": ts}`` when the owner sent the task (Send to build) and its brief is not
    written yet: kept across a failed pitch."""
    p = pitch or {}
    return {k: p[k] for k in ("sent", "by") if p.get(k)} if p.get("sent") else {}


def pitch_idea(ledger: Ledger, idea: dict, model, cfg) -> dict:
    """Write one task's brief and record the outcome: ``rejected`` (with the reason),
    ``queued`` (with its task folder), or a counted error (``error`` after
    ``pitch.max_errors``)."""
    from .models import spend_kw

    pick = owner_pick(idea)
    # the person's task has no floor (min_buildability 0, no score gates)
    res = run(
        idea,
        model,
        0.0 if pick else float(cfg["pitch"]["min_buildability"]),
        {} if pick else gates_of(cfg["pitch"]),
    )
    if pick and res["decision"] == "rejected" and res["pitch"]:
        res = {**res, "decision": "accepted", "pitch": as_brief(res["pitch"], idea)}
    ledger.spend("pitch", res["usd"], idea["id"], **spend_kw(res.get("tokens")))
    p = res["pitch"]
    if res["decision"] == "malformed":
        prev = idea.get("pitch") or {}
        errors = int(prev.get("errors", 0)) + 1
        state = "error" if errors >= int(cfg["pitch"]["max_errors"]) else "new"
        return ledger.update(
            idea["id"],
            state=state,
            pitch={"errors": errors, "last": p, "problems": res["problems"], **sent_mark(prev)},
            reason="the pitch reply broke the contract: " + "; ".join(res["problems"]),
            note=f"pitch failed ({errors})",
        )
    record = {
        **p,
        "usd": round(res["usd"], 4),
        "prompt": res.get("prompt"),
        # who sent it to build: its first round says so
        **({"by": (idea.get("pitch") or {})["by"]} if (idea.get("pitch") or {}).get("by") else {}),
    }
    if not (cfg.get("pitch") or {}).get("interpret_claims", True):
        record.pop("claims", None)
        p = {k: v for k, v in p.items() if k != "claims"}
    from . import claims

    if res["decision"] == "rejected":
        out = ledger.update(
            idea["id"],
            state="rejected",
            pitch=record,
            reason=p.get("reject_reason") or "not buildable",
            note=f"rejected: {p.get('reject_reason')}",
        )
    elif claims.needs_owner(record):
        # no reading of a claim keeps the task's spirit: it can't be built as
        # stated; held before building, with the proposal for the owner to accept or edit
        task = make_task(idea, p, cfg.tasks_dir)
        why = claims.hold_words(claims.needs_owner(record))
        out = ledger.update(
            idea["id"],
            state="held",
            pitch=record,
            task_dir=str(task),
            reason=why,
            title=p["product"],
            gate={"verdict": "held", "reasons": [why], "checks": {}, "before_build": True},
            note=f"accepted as {p['product']}, held before building: {why}",
        )
    else:
        task = make_task(idea, p, cfg.tasks_dir)
        n = len(claims.of(record))
        out = ledger.update(
            idea["id"],
            state="queued",
            pitch=record,
            task_dir=str(task),
            reason=None,
            title=p["product"],
            note=f"accepted as {p['product']} ({p['product_type']}, "
            f"buildability {_score(p.get('buildability'))}); "
            f"task {task.name}" + (f"; {n} claim(s) read as recorded under Claims" if n else ""),
        )
    return out

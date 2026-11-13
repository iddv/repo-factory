"""What a claim means: repo-factory reads it at build start (NG-PLAN.md).

An idea's literal claims are a starting point, not a contract (the owner, 2026-10-11). When a
claim can't be honoured or verified as written ("permanently", "every", "exactly", "never
fails"), the pitch records an **interpretation** that keeps the idea's core value, and the
build uses it: the spec (``spec_section``), so the scope, the README and the hidden suite
read it, and where the reading turns on a number or a yes/no, an operator setting with a
default. This is the only place claims are negotiated: at build start, against the spec,
where it shapes what gets built (the owner, 2026-10-16). Publishing never negotiates: the
public text is written from the product (``describe.py``) and wording is never a hold.

The record is ``ideas.pitch.claims``, one dict per claim::

    {"id": "k1", "claim": "...", "interpreted": "...",
     "setting": {"key": "retention.days", "default": "90", "means": "..."} | None,
     "spirit": {"keeps": True, "why": "..."}, "source": "pitch" | "check" | "owner",
     "status": "standing" | "proposed" | "accepted" | "needs_owner", "at": ts}

``standing`` (repo-factory recorded it; in force), ``proposed`` (a check's reading, from before
2026-10-16; in force), ``accepted`` (the owner's), ``needs_owner`` (no reading keeps the
idea's spirit: the idea is held before building with the proposal until the owner accepts or
edits it).

The release text (``release_text``) is the owner's when they set one (used as it is), else
the one written from the product at the pre-push checks, else the pitch's one-liner until
then."""

from __future__ import annotations

import hashlib
import json
import re
import time

STATUSES = ("standing", "proposed", "accepted", "needs_owner")
IN_FORCE = ("standing", "proposed", "accepted")
SOURCES = ("pitch", "check", "owner")
RELEASE_FILE = "RELEASE"  # the honesty check's name for repo-factory's release text
REPO_FACTORY_FILES = (RELEASE_FILE, "NOTICE", "LICENSE")
MAX_CLAIMS = 8
TEXT_MIN, TEXT_MAX = 20, 300
SETTING_KEY = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+")
# words that often state more than software can promise; shown beside the release text on
# the build page when no interpretation covers them (a hint, never a hold)
ABSOLUTES = re.compile(
    r"\b(permanent(?:ly)?|forever|every|exactly|always|never|guarantee[sd]?|"
    r"100\s?%|complete(?:ly)?|perfect(?:ly)?|instant(?:ly)?|unlimited|"
    r"any (?:language|format|device|platform|size))\b",
    re.I,
)


# -- the record ----------------------------------------------------------------------------
def words(text: str) -> str:
    """Lower-case words only: what a fingerprint and a comparison read."""
    return " ".join(re.findall(r"[a-z]+", str(text or "").lower()))


def fp(text: str) -> str:
    return hashlib.sha1(words(text).encode()).hexdigest()[:12]


def same(a: str, b: str, at: float = 0.8) -> bool:
    """Whether two texts say the same thing, word for word (Jaccard of their word sets)."""
    x, y = set(words(a).split()), set(words(b).split())
    if not x or not y:
        return x == y
    return len(x & y) / len(x | y) >= at


def overlaps(a: str, b: str, at: float = 0.8) -> bool:
    """Whether one text is mostly the other (the share of the shorter one's words in both):
    a reworded sentence that keeps the flagged claim is the same claim."""
    x, y = set(words(a).split()), set(words(b).split())
    if not x or not y:
        return False
    return len(x & y) / min(len(x), len(y)) >= at


def absolutes(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).lower() for m in ABSOLUTES.finditer(text or "")))


def _line(v, n: int = TEXT_MAX) -> str:
    return " ".join(str(v or "").split())[:n]


def clean_setting(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    key = _line(raw.get("key"), 80).strip("`")
    default = _line(raw.get("default"), 80)
    if not SETTING_KEY.fullmatch(key) or not default:
        return None
    return {"key": key, "default": default, "means": _line(raw.get("means"), 200)}


def clean(raw, source: str = "pitch") -> dict | None:
    """One interpretation from a model's reply or the owner's edit, or None when it is not
    one (no claim, no interpreted claim, or the two say the same)."""
    if not isinstance(raw, dict):
        return None
    claim, interp = _line(raw.get("claim")), _line(raw.get("interpreted"))
    if len(claim) < 3 or len(interp) < 3 or words(claim) == words(interp):
        return None
    sp = raw.get("spirit") if isinstance(raw.get("spirit"), dict) else {}
    keeps = sp.get("keeps", raw.get("keeps_spirit"))
    why = _line(sp.get("why") or raw.get("why"), 240)
    keeps = keeps is not False  # a reply that says nothing keeps the spirit
    st = (
        raw.get("status")
        if raw.get("status") in STATUSES
        else ("standing" if keeps else "needs_owner")
    )
    return {
        "claim": claim,
        "interpreted": interp,
        "setting": clean_setting(raw.get("setting")),
        "spirit": {"keeps": keeps, "why": why},
        "source": source if source in SOURCES else "pitch",
        "status": st,
        "at": raw.get("at") or time.time(),
    }


def of(idea_or_pitch: dict) -> list[dict]:
    """The idea's interpretations (``pitch.claims``), each with its id."""
    p = idea_or_pitch.get("pitch") if "pitch" in idea_or_pitch else idea_or_pitch
    out = []
    for n, c in enumerate((p or {}).get("claims") or [], 1):
        if isinstance(c, dict) and c.get("claim"):
            out.append({**c, "id": c.get("id") or f"k{n}"})
    return out


def in_force(idea: dict) -> list[dict]:
    return [c for c in of(idea) if c.get("status") in IN_FORCE]


def needs_owner(idea: dict) -> list[dict]:
    return [c for c in of(idea) if c.get("status") == "needs_owner"]


def merge(old: list[dict], new: list[dict]) -> list[dict]:
    """``old`` with ``new`` added: a new reading of the same claim replaces repo-factory's own
    (standing, proposed, needs_owner) but never the owner's (accepted); ids stay stable."""
    out = [dict(c) for c in old]
    for c in new:
        hit = next((o for o in out if same(o["claim"], c["claim"])), None)
        if hit is None:
            out.append(dict(c))
        elif hit.get("status") != "accepted":
            hit.update({k: v for k, v in c.items() if k != "id"})
    out = out[-MAX_CLAIMS:] if len(out) > MAX_CLAIMS else out
    used = {c.get("id") for c in out if c.get("id")}
    n = 1
    for c in out:
        if not c.get("id"):
            while f"k{n}" in used:
                n += 1
            c["id"] = f"k{n}"
            used.add(c["id"])
    return out


def open_for(idea: dict) -> bool:
    """Whether the owner may accept or edit a reading now: before the build starts (queued and
    never run, or held before building because no reading kept the idea's spirit), or once it
    is published, where a reading is its project's charter promise (PJ-PLAN.md §5.5). Never
    between a build and its publish: publishing never negotiates claims (the owner,
    2026-10-16)."""
    st, g, b = idea.get("state"), idea.get("gate"), idea.get("build")
    if st == "held":
        return isinstance(g, dict) and bool(g.get("before_build"))
    if st == "queued":
        return not (isinstance(b, dict) and b.get("runs"))
    return st in ("published", "public")


def release_text(idea: dict) -> str:
    """repo-factory's one-line text about the product: the repository's description and the first
    line of its release notes. The owner's or a check's rewording when there is one, else the
    pitch's one-liner."""
    p = idea.get("pitch") or {}
    rel = p.get("release") if isinstance(p.get("release"), dict) else {}
    return _line(rel.get("text") or p.get("one_liner") or p.get("product") or "")


def set_release(pitch: dict, text: str, by: str) -> dict:
    """``pitch`` with a new release text (the earlier ones kept in ``was``)."""
    p = dict(pitch or {})
    rel = dict(p.get("release") or {})
    prev = rel.get("text") or p.get("one_liner")
    was = list(rel.get("was") or [])
    if prev and prev != text:
        was = (was + [prev])[-5:]
    p["release"] = {"text": _line(text), "was": was, "by": by, "at": time.time()}
    return p


def release_block(text: str, notes: str) -> str:
    """The honesty check's ``RELEASE`` block: what repo-factory itself publishes about the product."""
    return f"Repository description: {text}\nRelease notes: {notes.strip()}"


def release_notes(product: str, text: str) -> str:
    """The release notes' opening (``publish.push`` adds the checks asset and any override)."""
    return f"First release of {product}: {text}\n\nInstall and run it as the README says."


def first_line(product: str, text: str) -> str:
    return f"First release of {product}: {text}"


def spec_section(claims: list[dict]) -> str:
    """``spec.md``'s "How the claims are read": the build, the scope, the README and the
    hidden suite read the interpreted claims, and a setting becomes an operator setting."""
    cs = [c for c in claims if c.get("status") in IN_FORCE]
    if not cs:
        return ""
    lines = [
        "## How the claims are read",
        "",
        "Some claims above can't be honoured as written; build to these readings and "
        "describe the product by them:",
        "",
    ]
    for c in cs:
        s = c.get("setting")
        lines.append(
            f'- "{c["claim"]}" means: {c["interpreted"]}'
            + (
                f". Make it the operator setting `{s['key']}` with the default "
                f"{s['default']}" + (f" ({s['means']})" if s.get("means") else "")
                if s
                else ""
            )
            + "."
        )
    return "\n".join(lines) + "\n"


def hold_words(cs: list[dict]) -> str:
    """The hold of an idea no reading of which keeps its spirit: plain, with the proposal."""
    parts = [
        f'"{c["claim"]}" (proposed: "{c["interpreted"]}"'
        + (f"; {c['spirit']['why']}" if (c.get("spirit") or {}).get("why") else "")
        + ")"
        for c in cs
    ]
    return (
        "can't be built as stated: "
        + "; ".join(parts)
        + ". Accept or edit the proposal under Claims on the build page"
    )


# -- who wrote it ----------------------------------------------------------------------------
def author(file: str | None, factory_files: list[str] | tuple = ()) -> str:
    """``repo-factory`` or ``product``: who wrote the text a finding quotes. ``factory_files``: the
    files repo-factory wrote into the staged tree (the maintenance files)."""
    f = str(file or "").strip().lstrip("./")
    if not f:
        return "product"
    if f.upper().startswith(REPO_FACTORY_FILES) or f in set(factory_files or ()):
        return "repo-factory"
    return "product"


def view(idea: dict) -> dict:
    """The build page's Claims section: the interpretations read at build start and the
    release text (whose it is)."""
    t = release_text(idea)
    covered = " ".join(c["claim"] + " " + c["interpreted"] for c in in_force(idea))
    rel = (idea.get("pitch") or {}).get("release") or {}
    return {
        "claims": of(idea),
        "release": {
            "text": t,
            "by": rel.get("by") or "pitch",
            "was": rel.get("was") or [],
            "at": rel.get("at"),
        },
        "absolutes": [w for w in absolutes(t) if w not in covered.lower()],
    }


def dumps(x) -> str:
    return json.dumps(x, sort_keys=True, default=str)

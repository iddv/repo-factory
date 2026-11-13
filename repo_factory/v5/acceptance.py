"""Plain names for repo-factory's scenarios, and what the maintenance lane still reads from the
acceptance suites earlier versions delivered.

Since EX-PLAN (2026-10-11) nothing of repo-factory's exam goes into a delivered product: the
scenarios, their results and the reports live in the den and ship as the release's checks
asset (``exam.py``). What is left here: a scenario's plain title (``rule_title``, for the
evidence's reports and the open items), its file slug, the plain reason of an open item, and
for published repos that kept the suite they were delivered with (cleanup PRs keep it, §8.1)
the known-open-item marks the maintenance lane removes once a fix makes a test pass
(``unmark``, ``readme_remove_open_item``). The jargon scan moved to ``jargon.py`` (exact ids,
never shapes)."""

from __future__ import annotations

import re
from pathlib import Path

ACC = "tests/acceptance"
OPEN_MARK = "# Known open item: expected to fail until it is fixed"
STOP = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "to",
    "is",
    "are",
    "be",
    "it",
    "its",
    "in",
    "on",
    "for",
    "with",
    "that",
    "this",
    "when",
    "then",
    "by",
    "as",
    "at",
    "from",
}
# repo-factory's own ids, as the verifier, the claims, the scenarios and the findings write them (c12,
# a3, e39, x1, m4, s1 and S1, F2, sec_1, flow_F1; q and k from older rounds)
REPO_FACTORY_ID = r"(?:[acefkmqsx]m?\d{1,3}|sec_\d+|flow_F\d+)"
ID_REF = r"\b(?:fix|finding|claim|scenario|rule|defect)s?\s+" + REPO_FACTORY_ID + r"\b"
ID_ASIDE = re.compile(r"\s*\([^)]*" + ID_REF + r"[^)]*\)", re.I)
ID_PREFIX = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*|(?:flow\s+)?F\d+\s*[:.)\-–—]\s*|"
    r"[a-z]{1,3}\d+[a-z]?\s*[:.)\-–—]\s*)",
    re.I,
)
BARE_FLOW = re.compile(r"^(?:flow[_\s]*)?(F\d+)$", re.I)


def rule_title(rule, flows: dict[str, str] | None = None) -> str:
    """A scenario's title without ids; a flow scenario titled only by its id (``flow_F1``)
    gets the flow's name from SCOPE.md."""
    t = str(rule.title or "").strip()
    m = BARE_FLOW.match(t)
    if m:
        fid = m.group(1).upper()
        name = (flows or {}).get(fid)
        return f"Flow {fid}: {name}" if name else f"Flow {fid} end to end"
    return plain_title(t)


def plain_title(title: str) -> str:
    """A scenario's title without leading ids (``F3: ...``, ``c42 - ...``, ``[x1] ...``)."""
    t = str(title or "").strip()
    for _ in range(3):
        t2 = ID_PREFIX.sub("", t, count=1).strip()
        if t2 == t:
            break
        t = t2
    t = ID_ASIDE.sub("", t).strip()  # "(neighbour of fix e44)"
    return t[:1].upper() + t[1:] if t else t


def slug(text: str, maxlen: int = 60) -> str:
    """``test_<slug>.py``'s slug: lower-case words joined by ``_``, without filler words,
    cut at a word boundary."""
    words = re.findall(r"[a-z0-9]+", re.sub(r"['’]s\b", "", str(text or "").lower()))
    keep = [w for w in words if w not in STOP] or words
    out = ""
    for w in keep:
        nxt = f"{out}_{w}" if out else w
        if len(nxt) > maxlen:
            break
        out = nxt
    if out and out[0].isdigit():
        out = "n" + out
    return out or "behaviour"


def reason(rule, panel_class: str | None = None, flows: dict[str, str] | None = None) -> str:
    """Why an open item still fails, in plain words."""
    o = rule.origin or {}
    title = rule_title(rule, flows).rstrip(".")
    kind = o.get("ready") or o.get("basis")
    if kind == "security":
        sev = o.get("severity")
        return f"Known security issue ({sev}): {title}" if sev else f"Known security issue: {title}"
    if kind == "readiness":
        return f"Known readiness gap: {title}"
    if panel_class == "environment":
        return f"Fails only in an isolated test sandbox, not a product defect: {title}"
    if panel_class == "incomplete" or o.get("incomplete"):
        return f"Not complete yet: {title}"
    return f"Known open issue: {title}"


# -- suites earlier versions delivered (the maintenance lane) -------------------------------
def unmark(code: str) -> str:
    return code.split("\n\n\n" + OPEN_MARK)[0].rstrip() + "\n" if OPEN_MARK in code else code


def readme_remove_open_item(text: str, name: str) -> str:
    """The acceptance README without the known-open-item line of test file ``name`` (a fix
    made it pass). When it was the last item, the section says every test passes instead.
    Unchanged when the README does not list it."""
    base = Path(name).name
    lines = text.splitlines()
    keep = [ln for ln in lines if not re.match(r"^\s*[-*]\s+`" + re.escape(base) + r"`", ln)]
    if len(keep) == len(lines):
        return text
    out = "\n".join(keep) + ("\n" if text.endswith("\n") else "")
    m = re.search(r"^## Known open items\s*$(.*?)(?=^## |\Z)", out, re.M | re.S)
    if m and not re.search(r"^\s*[-*]\s+`", m.group(1), re.M):
        out = (
            out[: m.start()] + "Every test passes on this version.\n\n" + out[m.end() :]
        ).rstrip() + "\n"
    return out

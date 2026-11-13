"""The lessons lint (build v5 §5.1 hard rules), run whenever a lesson is written.

- at most 240 characters, one or two sentences, imperative;
- no domain nouns: no word of the text, lower-cased and lemmatised, may be in the
  ``domain_terms`` of **any** pack under ``$REPO_FACTORY_PACKS``;
- no identifiers, file names, numbers, or quotes of a hidden row;
- at most 20 lessons with status ``probation`` or ``active`` (``lint_file``).

The lemmatiser is a small suffix stripper, enough for English nouns and verbs: a word matches
a term when it is the term or one of its inflections (``holds``, ``copies``, ``renewed``,
``checking``...). It errs towards rejecting, which only ever drops a lesson."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

MAX_CHARS = 240
MAX_SENTENCES = 2
QUOTE_WORDS = 5  # this many consecutive words shared with a hidden row = a quote
WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
# Words that open a clause with a subject instead of a verb: not imperative.
NOT_IMPERATIVE = {
    "the",
    "a",
    "an",
    "this",
    "that",
    "these",
    "those",
    "it",
    "its",
    "we",
    "you",
    "i",
    "they",
    "there",
    "our",
    "your",
    "their",
    "he",
    "she",
    "one",
    "lessons",
    "builds",
    "systems",
    "code",
    "factory",
    "should",
    "must",
    "always",
    "never",
}
SUBORDINATE = {
    "when",
    "whenever",
    "if",
    "for",
    "before",
    "after",
    "once",
    "where",
    "while",
    "unless",
    "until",
    "since",
    "as",
    "on",
    "in",
    "across",
    "between",
    "wherever",
}


def packs_root() -> Path:
    from .pack import packs_root as root

    return root()


def domain_terms(root: Path | None = None) -> dict[str, list[str]]:
    """``{term: [pack ids]}`` from the ``domain_terms`` of every ``pack.yaml`` under
    ``$REPO_FACTORY_PACKS``, whatever the pack's status or split."""
    root = Path(root) if root else packs_root()
    out: dict[str, list[str]] = {}
    if not root.is_dir():
        return out
    for p in sorted(root.iterdir()):
        f = p / "pack.yaml"
        if not f.is_file():
            continue
        try:
            meta = yaml.safe_load(f.read_text()) or {}
        except yaml.YAMLError:
            continue
        for t in meta.get("domain_terms") or []:
            t = str(t).strip().lower()
            if t:
                out.setdefault(t, []).append(str(meta.get("id") or p.name))
    return out


# Irregular forms of verbs likely to be domain terms (hold -> held, lend -> lent).
IRREGULAR = {
    "hold": ("held",),
    "lend": ("lent",),
    "pay": ("paid",),
    "buy": ("bought",),
    "sell": ("sold",),
    "send": ("sent",),
    "keep": ("kept",),
    "spend": ("spent",),
    "take": ("took", "taken"),
    "give": ("gave", "given"),
    "bring": ("brought",),
    "write": ("wrote", "written"),
    "child": ("children",),
    "person": ("people",),
    "index": ("indices",),
}


def inflections(term: str) -> set[str]:
    """The term and its inflections (plural, -ed, -ing, -er, possessive, a few irregulars)."""
    t = term.lower()
    out = {t, t + "s", t + "es", t + "'s", t + "ed", t + "ing", t + "er", t + "ers"}
    out |= set(IRREGULAR.get(t, ()))
    if t.endswith("y") and len(t) > 2 and t[-2] not in "aeiou":
        out |= {t[:-1] + "ies", t[:-1] + "ied"}
    if t.endswith("e"):
        out |= {t + "d", t[:-1] + "ing", t + "r", t + "rs"}
    if t.endswith("f"):
        out.add(t[:-1] + "ves")  # shelf -> shelves
    if t.endswith("fe"):
        out.add(t[:-2] + "ves")
    if len(t) >= 3 and t[-1] not in "aeiouwxy" and t[-2] in "aeiou" and t[-3] not in "aeiou":
        out |= {t + t[-1] + "ed", t + t[-1] + "ing", t + t[-1] + "er"}  # stop -> stopped
    return out


def words(text: str) -> list[str]:
    return [w.lower().strip("'-") for w in WORD.findall(text)]


def domain_hits(text: str, terms: dict[str, list[str]]) -> list[tuple[str, str, list[str]]]:
    """``(word, term, packs)`` for every word of ``text`` that is a domain term. Multi-word
    terms match as a phrase."""
    ws = words(text)
    joined = " " + " ".join(ws) + " "
    index: dict[str, str] = {}
    hits = []
    for t, packs in terms.items():
        parts = t.split()
        if len(parts) > 1:
            if f" {' '.join(parts)} " in joined:
                hits.append((t, t, packs))
            continue
        for f in inflections(t):
            index.setdefault(f, t)
    seen = set()
    for w in ws:
        t = index.get(w)
        if t and w not in seen:
            seen.add(w)
            hits.append((w, t, terms[t]))
    return hits


def sentences(text: str) -> int:
    parts = [p for p in re.split(r"[.!?]+(?:\s+|$)", text.strip()) if p.strip()]
    return len(parts)


def imperative(text: str) -> bool:
    """A cheap check: the main clause must not open with a subject. ``When X, test Y`` and
    ``Test Y`` pass; ``The system should Y`` fails."""
    first = text.strip().split(".")[0]
    ws = words(first)
    if not ws:
        return False
    if ws[0] in SUBORDINATE and "," in first:
        ws = words(first.split(",", 1)[1])
        if not ws:
            return False
    return ws[0] not in NOT_IMPERATIVE


def identifiers(text: str) -> list[str]:
    """Tokens that look like code or files: snake_case, camelCase, dotted names, paths,
    backticks, brackets, anything with a digit."""
    bad = []
    for tok in re.findall(r"\S+", text):
        core = tok.strip(".,;:!?\"'()")
        if not core:
            continue
        if (
            "`" in tok
            or "_" in core
            or "/" in core
            or "\\" in core
            or "{" in core
            or "[" in core
            or "=" in core
            or "#" in core
            or re.search(r"\w\.\w", core)
            or re.search(r"[a-z][A-Z]", core)
        ):
            bad.append(core)
    return bad


def quoted(text: str, hidden: list[str], n: int = QUOTE_WORDS) -> str | None:
    """The first run of ``n`` consecutive words ``text`` shares with any hidden text."""
    ws = words(text)
    grams = {" ".join(ws[i : i + n]) for i in range(len(ws) - n + 1)}
    for h in hidden:
        hw = words(re.sub(r"[_/]", " ", h or ""))
        for i in range(len(hw) - n + 1):
            g = " ".join(hw[i : i + n])
            if g in grams:
                return g
    return None


def lint(
    text: str,
    *,
    terms: dict[str, list[str]] | None = None,
    hidden: list[str] = (),
    existing: list[str] = (),
) -> list[str]:
    """Problems with one lesson text; empty when it may be written. ``terms`` defaults to
    every pack's ``domain_terms``; ``hidden`` are the texts of hidden rows (ids, titles,
    quotes) the lesson must not quote; ``existing`` are the current lessons' texts."""
    text = (text or "").strip()
    terms = domain_terms() if terms is None else terms
    probs = []
    if not text:
        return ["empty"]
    if len(text) > MAX_CHARS:
        probs.append(f"{len(text)} characters (at most {MAX_CHARS})")
    if sentences(text) > MAX_SENTENCES:
        probs.append(f"{sentences(text)} sentences (at most {MAX_SENTENCES})")
    if not imperative(text):
        probs.append("not imperative: start with a verb (optionally after a 'When ...,' clause)")
    for w, t, packs in domain_hits(text, terms):
        probs.append(f"domain word {w!r} (term {t!r} of {', '.join(packs)})")
    if re.search(r"\d", text):
        probs.append("contains a number")
    ids = identifiers(text)
    if ids:
        probs.append(f"identifier-like tokens: {', '.join(ids)}")
    q = quoted(text, list(hidden))
    if q:
        probs.append(f"quotes a hidden row: {q!r}")
    norm = " ".join(words(text))
    if any(" ".join(words(e)) == norm for e in existing):
        probs.append("duplicates an existing lesson")
    return probs


def lint_file(data: dict, terms: dict[str, list[str]] | None = None) -> list[str]:
    """Problems with a whole lessons file: the cap, ids, statuses, and every live text."""
    from .store import APPLIES_TO, LIVE, STATUSES

    terms = domain_terms() if terms is None else terms
    probs = []
    live = [x for x in data["lessons"] if x.get("status") in LIVE]
    if len(live) > int(data.get("cap") or 20):
        probs.append(f"{len(live)} probation/active lessons (cap {data.get('cap')})")
    seen = set()
    for x in data["lessons"]:
        lid = x.get("id")
        if lid in seen:
            probs.append(f"duplicate id {lid}")
        seen.add(lid)
        if x.get("status") not in STATUSES:
            probs.append(f"{lid}: status {x.get('status')!r}")
        if x.get("applies_to") not in APPLIES_TO:
            probs.append(f"{lid}: applies_to {x.get('applies_to')!r}")
        if x.get("status") in LIVE:
            probs += [f"{lid}: {p}" for p in lint(x.get("text", ""), terms=terms)]
    return probs

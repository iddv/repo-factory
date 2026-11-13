"""Stage 0: scope a short idea into a v1 product brief, ``SCOPE.md`` (before the build).

The owner triggers the factory on short, broad ideas ("libraries need software to manage their
inventory..."). Given only that, a builder delivers the obvious slice: stubs, dead buttons,
missing flows. A full spec (the lending spec) needs nothing; an idea needs a product brief.

- **When** (``rules.yaml`` ``scope: auto|always|off``): ``auto`` asks ``classify``, a cheap
  deterministic heuristic (length, numbers, interfaces, rule words, structure). A spec passes
  through with no call; an idea, or a borderline input, goes to the scope call, where the model
  may still answer ``PASS`` (the input is concrete enough). ``always`` scopes whatever the
  input; ``off`` never scopes.
- **How**: one no-tools call with the builder's model (``prompts/scope.md``) that writes
  ``SCOPE.md``: users, numbered end-to-end flows, must-have vs later, domain rules with
  defaults, operator settings (Addendum A), money and data integrity, the install story, the
  non-functional basics and a definition of done (one checklist line per flow).
- **Use**: the build prompt is the idea, "Here is the product scope for v1:", ``SCOPE.md``,
  then the vanilla suffix (``build_prompt``); claims, the verifier, the panel and the repair
  read the idea plus ``SCOPE.md`` as the spec (``spec_text``); ``SCOPE.md`` ships in the repo.
  A scoped build no longer shares the vanilla base build (recorded as ``shared_base: false``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .common import prompt

MODES = ("auto", "always", "off")
HEADER = "Here is the product scope for v1:"
FILE = "SCOPE.md"

# -- the heuristic -----------------------------------------------------------------------------
WORD = re.compile(r"[A-Za-z0-9€$£%][\w€$£%'.,/-]*")
NUMBER = re.compile(r"(?<![A-Za-z])[€$£]?\d+(?:[.,]\d+)?\s*(?:%|€|\$|£)?")
INTERFACE = re.compile(
    r"`[^`\n]+`"  # code spans
    r"|\b[a-z_][a-z0-9_]*\([^)\n]{0,40}\)"  # call(args)
    r"|\b[\w-]+/[\w./-]+\.[a-z]{1,4}\b"  # a/path/file.ext
    r"|\b(?:GET|POST|PUT|PATCH|DELETE)\s+/"  # HTTP endpoints
    r"|(?<!\w)/api/\S*"
    r"|(?<!\w)--[a-z][\w-]+"
)  # CLI flags
RULE = re.compile(
    r"\b(?:must(?: not)?|shall|at most|at least|no more than|never|only if|"
    r"cannot|can't|max(?:imum)?|min(?:imum)?|up to|per (?:day|week|month|hour|"
    r"loan|member|user|order|item))\b|[≥≤<>]=?",
    re.I,
)
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S", re.M)
HEADING = re.compile(r"^\s*(?:#{1,6}\s+\S|[A-Z][A-Za-z ]{2,30}:?\s*$)", re.M)

IDEA_WORDS = 120  # an idea is short ...
IDEA_SCORE = 4  # ... and has almost no concrete rules, numbers or interfaces
BORDER_SCORE = 10  # this many concrete details make a spec, whatever the length
SPEC_INTERFACES = 2  # a short task that names its functions or files is a spec too


SHAPE = re.compile(r"^Shape:\s*(.+?)\s*$", re.M)
GROUNDING = re.compile(r"^Grounding:\s*\n(?:[ \t]*[-*][^\n]*(?:\n|$))+", re.M)
# the pitch's "How the claims are read" (``claims.spec_section``): readings, not a spec's rules
CLAIMS = re.compile(r"^## How the claims are read\n.*?(?=^#{1,2}\s|\Z)", re.M | re.S)


def carried(text: str) -> dict:
    """What the pitch carries in ``spec.md`` beside the brief (``pitch.spec_text``):
    ``{"core": the text without them (nor the claims' readings), "shape": the Shape line's
    value or None, "grounding": [lines]}``."""
    t = text or ""
    m = SHAPE.search(t)
    g = GROUNDING.search(t)
    lines = (
        [re.sub(r"^\s*[-*]\s*", "", x).strip() for x in g.group(0).splitlines()[1:]] if g else []
    )
    core = CLAIMS.sub("", GROUNDING.sub("", SHAPE.sub("", t, count=1), count=1), count=1)
    return {
        "core": re.sub(r"\n{3,}", "\n\n", core),
        "shape": m.group(1) if m else None,
        "grounding": [x for x in lines if x],
    }


def classify(text: str) -> dict:
    """``{"verdict": idea|borderline|spec, "why", "words", "numbers", "interfaces",
    "rules", "structure", "score"}``. Deterministic and free: the scope call (``auto``)
    happens only for ``idea`` and ``borderline``, and the model may still pass a borderline
    input through. A pitched idea's Shape line, Grounding block and claims' readings are not
    counted: they are carried for the scope, not a spec's rules (their bullets, numbers and
    setting names made pitched ideas look like specs, so the scope never ran)."""
    t = carried(text)["core"]
    words = len(WORD.findall(t))
    numbers = len(NUMBER.findall(t))
    interfaces = len(INTERFACE.findall(t))
    rules = len(RULE.findall(t))
    lines = [ln for ln in t.splitlines() if ln.strip()]
    structure = len(BULLET.findall(t)) + sum(1 for ln in lines[1:] if HEADING.match(ln))
    score = numbers + 2 * interfaces + rules + structure
    sig = {
        "words": words,
        "numbers": numbers,
        "interfaces": interfaces,
        "rules": rules,
        "structure": structure,
        "score": score,
    }
    if score >= BORDER_SCORE or interfaces >= SPEC_INTERFACES:
        verdict, why = (
            "spec",
            (
                f"{words} words with concrete details (score {score}, "
                f"{interfaces} named interface(s)): a spec, passed through "
                "unchanged"
            ),
        )
    elif words <= IDEA_WORDS and score < IDEA_SCORE:
        verdict, why = (
            "idea",
            (
                f"{words} words with almost no concrete rules, numbers or "
                f"interfaces (score {score} < {IDEA_SCORE})"
            ),
        )
    else:
        verdict, why = (
            "borderline",
            (f"{words} words, few concrete details (score {score}); the scope call decides"),
        )
    return {"verdict": verdict, "why": why, **sig}


def should_call(mode: str, verdict: str) -> bool:
    if mode == "always":
        return True
    if mode == "auto":
        return verdict in ("idea", "borderline")
    return False


# -- the prompt and its reply -------------------------------------------------------------------
REPLY = "Reply with the text of `SCOPE.md` only"


def scope_prompt(policy: Path, idea: str, mode: str, extra: str = "") -> str:
    """The scope prompt. ``extra``: a flow's own rules for its brief (the lean flow's
    ``prompts/scope_rules.md``), placed before the reply line; none for v5 (its bytes)."""
    judge = prompt(policy, "scope", "judge") if mode != "always" else ""
    text = prompt(policy, "scope", idea=idea.strip(), judge=judge)
    if not extra.strip():
        return text
    at = text.rfind(REPLY)
    extra = extra.strip() + "\n\n"
    return text[:at] + extra + text[at:] if at >= 0 else text.rstrip() + "\n\n" + extra


def _section(md: str, title: str, level: int = 2) -> str:
    """The body of the first ``## <title>...`` heading (to the next heading of that level
    or higher)."""
    hs = "#" * level
    m = re.search(
        rf"^{hs}\s+{re.escape(title)}[^\n]*\n(.*?)(?=^#{{1,{level}}}\s|\Z)", md, re.M | re.S | re.I
    )
    return m.group(1) if m else ""


FLOW = re.compile(r"^###\s+(F\d+)\s*[:.\-–—]\s*(.+?)\s*$", re.M)
DOD = re.compile(r"^\s*[-*]\s*\[[ xX]?\]\s*\**(F\d+)\**\s*[:.\-–—]?\s*(.*?)\s*$", re.M)
MUST = re.compile(r"^\s*[-*]\s*\**(M\d+)\**\s*[:.\-–—]\s*(.+?)\s*$", re.M)
LATER = re.compile(r"^\s*[-*]\s*(.+?)\s*$", re.M)


def _cell(s: str) -> str:
    return s.strip().strip("`*").strip()


def settings_of(md: str) -> list[dict]:
    """The operator-settings table: ``[{"key", "values", "default", "means"}]`` (rows with
    exactly two values)."""
    out = []
    for ln in _section(md, "Operator settings").splitlines():
        cells = [c for c in ln.strip().strip("|").split("|")]
        if len(cells) < 3 or set(ln.replace("|", "").strip()) <= set("-: "):
            continue
        key = _cell(cells[0])
        if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+", key):
            continue  # the header row, or prose
        vals = [_cell(v) for v in re.split(r",|/|\bor\b", cells[1]) if _cell(v)]
        if len(vals) != 2:
            continue
        out.append(
            {
                "key": key,
                "values": vals,
                "default": _cell(cells[2]),
                "means": _cell(cells[3]) if len(cells) > 3 else "",
            }
        )
    return out


def parse(text: str) -> dict:
    """The scope call's reply: ``{"decision": scoped|pass|malformed, "why", "md", "title",
    "flows": [{"id", "title"}], "dod": [{"id", "title", "done_when"}], "must": [{"id",
    "text"}], "later": [str], "settings": [...]}``."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$", r"\1", t, flags=re.S)
    if re.match(r"^PASS\b", t):
        return {
            "decision": "pass",
            "why": t.split(":", 1)[-1].strip()[:300]
            or "the model judged the input concrete enough",
            "md": None,
        }
    i = t.find("# Scope")
    if i < 0:
        return {"decision": "malformed", "why": "no '# Scope' heading in the reply", "md": None}
    md = t[i:].rstrip() + "\n"
    title = md.splitlines()[0].split(":", 1)[-1].strip() or "the product"
    flows = [
        {"id": f.upper(), "title": n.strip("* ")}
        for f, n in FLOW.findall(_section(md, "Core flows"))
    ]
    names = {f["id"]: f["title"] for f in flows}
    dod, seen = [], set()
    for f, rest in DOD.findall(_section(md, "Definition of done")):
        f = f.upper()
        if f in seen:
            continue
        seen.add(f)
        parts = re.split(r"\s+[—–-]+\s+", rest.strip(), maxsplit=1)  # "<name> — <end state>"
        name, done = parts if len(parts) == 2 else (rest.strip(), rest.strip())
        dod.append({"id": f, "title": names.get(f) or name, "done_when": done})
    feats = _section(md, "Features")
    must = [
        {"id": m.upper(), "text": x}
        for m, x in MUST.findall(_section(feats + "\n", "Must", 3) or feats)
    ]
    later = [x for x in LATER.findall(_section(feats + "\n", "Later", 3))]
    out = {
        "decision": "scoped",
        "why": "",
        "md": md,
        "title": title,
        "flows": flows,
        "dod": dod,
        "must": must,
        "later": later,
        "settings": settings_of(md),
    }
    if not dod:
        out.update(decision="malformed", why="no definition-of-done flows (- [ ] F<n>: ...)")
    return out


# -- the word budget (PE2 §6.4), enforced in code ------------------------------------------------
# rules.yaml ``scope_size``: the limit for a single-user tool or a library, and for a team or a
# business; trim_max_usd: what the one shortening pass may cost (estimated before it starts from
# the first call's cost: its input is the first reply, so it costs no more than that call);
# 0: no shortening pass; null: no cap on it.
SIZE = {"single_words": 1800, "team_words": 2500, "trim_max_usd": 1.0}
TRIM = """[the scope's word budget]
The SCOPE.md below is {words} words. The limit for this product ({why}) is {limit} words.
Rewrite it to at most {limit} words. Keep every section heading in the same order, F1 and the
core flows with their steps, the Grounding facts it names, the operator settings table and one
Definition of done line per flow. Cut repetition, rules said twice, invented roles, money flows
and reports the idea does not name, and the least essential must-haves (move them to Later).
Do not add anything. Reply with the text of SCOPE.md only, starting with the line `# Scope: `.

{md}"""


def words(md: str | None) -> int:
    return len((md or "").split())


def limit_of(idea: str, md: str | None, size: dict) -> tuple[int, str]:
    """``(limit, why)``: the shape from the idea's Shape line (the pitch's), else the scope's
    own (scope v1 states one), else the larger limit."""
    sh = carried(idea)["shape"]
    src = "the pitch's shape"
    if not sh:  # scope v1 states it in its first paragraph, maybe mid-line
        m = re.search(r"Shape:\s*`?([^`\n]+?)`?\s*(?:\.\s|\.?$)", md or "", re.M)
        sh, src = (m.group(1).strip("`* ") if m else None), "the scope's shape"
    if sh and re.search(r"single user|\blibrary\b", sh, re.I):
        return int(size["single_words"]), f"{src}: {sh}"
    if sh:
        return int(size["team_words"]), f"{src}: {sh}"
    return int(size["team_words"]), "no shape given"


def budget_pass(
    llm, idea: str, res: dict, size: dict | None, budget=None, first_usd: float | None = None
) -> dict:
    """At most one more call when ``SCOPE.md`` is over its limit: the reply with its word
    count and the limit, asking for the same scope within it. Kept only when it is a usable
    scope and shorter. Returns ``res`` with ``size``: ``{"limit", "why", "words_first",
    "words", "trimmed", "trim_usd", "note"}``."""
    size = {**SIZE, **(size or {})}
    first = words(res.get("md"))
    limit, why = limit_of(idea, res.get("md"), size)
    rec = {
        "limit": limit,
        "why": why,
        "words_first": first,
        "words": first,
        "trimmed": False,
        "trim_usd": None,
        "note": None,
    }
    out = {**res, "size": rec}
    if first <= limit:
        return out
    cap = size.get("trim_max_usd")
    if cap is not None and float(cap) <= 0:
        rec["note"] = "over the limit; the shortening pass is off (scope_size.trim_max_usd: 0)"
        return out
    if cap is not None and first_usd is not None and first_usd > float(cap):
        rec["note"] = (
            f"over the limit; not shortened: the pass would cost about "
            f"${first_usd:.2f}, over its cap ${float(cap):.2f}"
        )
        return out
    need = first_usd or 0.01
    if budget is not None and (budget.stage_left("scope") < need or not budget.allows(need)):
        rec["note"] = "over the limit; not shortened: the build's or the stage's cap is too close"
        return out
    from ..costs import ledger
    from ..rules.llm import tag

    try:
        spent0 = getattr(llm, "usd", None)
        with ledger.tags(stage="scope"):
            text = llm(
                f"{tag('scope', 'trim')}\n"
                + TRIM.format(words=first, limit=limit, why=why, md=res["md"].strip())
            )
        if spent0 is not None:
            rec["trim_usd"] = round(float(getattr(llm, "usd", spent0)) - float(spent0), 6)
    except Exception as e:  # noqa: BLE001 - the first scope stands
        rec["note"] = f"over the limit; the shortening pass failed: {type(e).__name__}: {e}"[:300]
        return out
    got = parse(text)
    n = words(got.get("md"))
    if got["decision"] != "scoped" or not got.get("dod") or n >= first:
        rec["note"] = (
            "over the limit; the shortening pass's reply was not used: "
            + (got.get("why") or f"{n} words, not shorter")
        )[:300]
        return out
    rec.update(
        words=n,
        trimmed=True,
        note=None if n <= limit else f"shortened, still over the limit by {n - limit}",
    )
    keep = {k: v for k, v in res.items() if k not in got}
    return {**keep, **got, "size": rec}


def run(
    llm,
    policy: Path,
    idea: str,
    mode: str,
    budget=None,
    retries: int = 1,
    size: dict | None = None,
    extra: str = "",
) -> dict:
    """The scope stage's decision and, when scoped, ``SCOPE.md`` and its parsed parts. One
    no-tools call (one retry if the reply has no definition of done), then at most one
    shortening pass when the scope is over its word budget (``budget_pass``; ``size``:
    rules.yaml's ``scope_size``). ``mode``: ``auto``, ``always`` or ``off``. Returns
    ``parse``'s dict plus ``mode``, ``heuristic``, ``called`` and, when scoped, ``size``."""
    mode = mode if mode in MODES else "auto"
    h = classify(idea)
    base = {"mode": mode, "heuristic": h, "called": False}
    if not should_call(mode, h["verdict"]):
        why = "scope: off" if mode == "off" else h["why"]
        return {**base, "decision": "pass", "why": why, "md": None}
    from ..costs import ledger
    from ..rules.llm import tag

    body = f"{tag('scope')}\n{scope_prompt(policy, idea, mode, extra)}"
    res: dict = {}
    usd = None
    for attempt in range(retries + 1):
        if budget is not None:
            budget.check("scope")
        spent0 = getattr(llm, "usd", None)
        with ledger.tags(stage="scope"):
            out = llm(
                body
                if attempt == 0
                else body + "\n\nYour previous reply had no "
                "definition of done with one `- [ ] F<n>: ...` line per core flow. Reply "
                "with the whole SCOPE.md again, in the structure given."
            )
        if spent0 is not None:
            usd = float(getattr(llm, "usd", spent0)) - float(spent0)
        res = parse(out)
        if res["decision"] != "malformed":
            break
    if mode == "always" and res["decision"] == "pass":
        res = {**res, "decision": "malformed", "why": "PASS in mode always"}
    if res["decision"] == "malformed" and res.get("md") and res.get("flows"):
        res["decision"] = "scoped"  # usable brief; no checklist to score against
    if res["decision"] == "scoped":
        res = budget_pass(llm, idea, res, size, budget, usd)
    return {**base, **res, "called": True}


# -- traits: what kind of product it is ------------------------------------------------------
TRAITS = ("web", "cli", "library", "api")
# The scope's own line: "Interfaces: web UI, command line"
INTERFACES = re.compile(r"^\**Interfaces\**\s*:\s*(.+)$", re.M | re.I)
TRAIT_WORDS = {
    "web": r"web[ -]?(?:ui|app|application|interface|page|site|frontend|front-end|dashboard)s?"
    r"|browser|front[ -]?end|single[ -]page|html (?:page|form|ui)|the (?:page|screen)s?\b"
    r"|\bscreens?\b|\bdashboard\b|\bwebsite\b",
    "cli": r"command[ -]line|\bcli\b|\bterminal\b|\bshell command|subcommands?",
    "library": r"\blibrary\b|\bsdk\b|importable",
    "api": r"(?:http|rest(?:ful)?|json|web) api|\bapi endpoints?|\bendpoints?\b|webhooks?\b"
    r"|graphql|(?:serves?|exposes?) an? (?:\w+ )?api",
}
LINE_WORDS = {
    "web": r"\bweb\b|\bui\b|browser|front",
    "cli": r"command|\bcli\b|terminal",
    "library": r"librar|package|sdk",
    "api": r"\bapi\b|http|rest|endpoint",
}


def traits(idea: str, md: str | None = None) -> dict:
    """``{"traits": [web|cli|library|api ...], "from": "scope"|"words"|"none"}``: the
    scope's ``Interfaces:`` line when it has one, else the words of the idea and SCOPE.md
    (its Later section left out: what v1 does not do is no trait). Deterministic, no
    model."""
    m = INTERFACES.search(md or "")
    if m:
        line = m.group(1).lower()
        got = [t for t in TRAITS if re.search(LINE_WORDS[t], line)]
        if got:
            return {"traits": got, "from": "scope"}
    text = (idea or "") + "\n" + (md or "")
    later = re.search(r"^###\s+Later[^\n]*\n.*?(?=^#{1,3}\s|\Z)", text, re.M | re.S | re.I)
    if later:
        text = text[: later.start()] + text[later.end() :]
    text = text.lower()
    got = [t for t in TRAITS if re.search(TRAIT_WORDS[t], text)]
    return {"traits": got, "from": "words" if got else "none"}


def spec_text(idea: str, md: str | None) -> str:
    """What claims, the verifier, the panel and the repair read as the spec: the idea, then
    the scope."""
    if not md:
        return idea
    return f"{idea.rstrip()}\n\n{HEADER}\n\n{md.strip()}\n"


def build_prompt(
    idea: str, md: str | None, suffix: str, policy: Path | None = None, product_rules: bool = True
) -> str:
    """The build prompt: the raw idea, then the scope, then the vanilla suffix (a spec that
    passed through gets the vanilla prompt byte for byte). For the shipped policy it is
    ``prompts/build.md`` with the owner's house rules and additions (``custom_prompts``);
    with neither, the same bytes. ``product_rules``: a scoped build is also told what a
    product carries (``prompts/product.md``); the lean flow leaves that to its harden step."""
    from ..custom_prompts import policy_entry
    from ..custom_prompts import text as custom_text
    from .common import prompt_home

    if policy is not None:
        policy = prompt_home(policy, "build")  # the simple flow builds with today's prompt
    spec = spec_text(idea, md).rstrip()
    if (
        policy is None
        or policy_entry(policy, "build") is None
        or suffix != "Work in the current directory."
    ):
        return spec + "\n\n" + suffix + "\n"
    text = custom_text("build").replace("<<spec>>", spec)
    if md and product_rules and policy_entry(policy, "product") is not None:
        # EX-PLAN §4: a scoped build's product ships as the builder leaves it, so it is told
        # what a product carries (its own tests, AGENTS.md, SECURITY.md, CI ...)
        rules = custom_text("product").strip()
        at = text.rfind(suffix)
        text = text[:at] + rules + "\n\n" + text[at:] if at >= 0 else text + "\n" + rules
    return text


# -- a spec's own definition of done (EV7): a spec that passes through gets flows too -----------
# Without one, completeness could not be scored and every precise spec was held for it (EVI).
# Drawn from the spec's text, no model: its acceptance lines, else its numbered or bulleted
# requirements, else its sentences that state a behaviour (split at "; " when the parts name
# their own commands or endpoints), in the spec's order.
ACCEPT = re.compile(
    r"^\s*(?:[-*]\s*)?\**(?:acceptance(?: criteri(?:a|on))?|done when|accepted "
    r"when)\**\s*[:.\-–—]\s*(.+?)\s*$",
    re.M | re.I,
)
NUMBERED = re.compile(r"^\s*(?:\d+[.)]|[A-Z]?R\d+[:.)])\s+(.+?)\s*$", re.M)
BULLETED = re.compile(r"^\s*(?:[-*•]|- \[[ xX]?\])\s+(.+?)\s*$", re.M)
FENCE = re.compile(r"^```.*?^```[^\n]*$", re.M | re.S)
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`\"'(])")
BEHAVIOUR = re.compile(
    r"\b(?:answers?|returns?|prints?|exits?|writes?|creates?|lists?|keeps?|"
    r"reports?|shows?|says|checks?|runs?|opens?|closes?|counts?|reads?|"
    r"replaced|kept|preserved|ignores?|resolved|reused|cached|survives?|"
    r"listens?|generates?|explains?|deletes?|removes?|goes|error)\b",
    re.I,
)
SPEC_FLOWS = 12  # more requirements than this share flows, in order


def _clause(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().rstrip(";").strip()


def _requirement(s: str) -> bool:
    return bool(INTERFACE.search(s) or RULE.search(s) or BEHAVIOUR.search(s))


def _sentences(t: str) -> list[str]:
    paras = re.split(r"\n\s*\n", t)
    out = []
    for p in paras:
        lines = [ln for ln in p.splitlines() if ln.strip() and not re.match(r"^\s*#", ln)]
        if not lines:
            continue
        for s in SENTENCE.split(" ".join(ln.strip() for ln in lines)):
            parts = [x for x in re.split(r";\s+", s) if x.strip()]
            # "`POST /notes` creates ...; `GET /notes` lists ...": one flow per interface
            if len(parts) > 1 and all(INTERFACE.search(x) for x in parts):
                out += parts
            else:
                out.append(s)
    return out


def spec_dod(text: str, limit: int = SPEC_FLOWS) -> list[dict]:
    """The definition of done of a spec that passed through: ``[{"id", "title",
    "done_when"}]`` in the shape the scope's has, from the spec's own words (no model);
    ``[]`` when nothing in it reads as a requirement. Deterministic."""
    t = FENCE.sub("", carried(text)["core"])
    reqs = [_clause(x) for x in ACCEPT.findall(t)]
    if not reqs:
        for pat in (NUMBERED, BULLETED):
            got = [_clause(x) for x in pat.findall(t)]
            if len(got) >= 2:
                reqs = got
                break
    if reqs:
        reqs = [r for r in reqs if len(WORD.findall(r)) >= 3 and _requirement(r)]
    else:  # prose: every sentence of a spec is its contract
        reqs = [r for r in map(_clause, _sentences(t)) if len(WORD.findall(r)) >= 4]
    if not reqs:
        return []
    groups = [[r] for r in reqs]
    while len(groups) > max(1, int(limit)):  # the two shortest neighbours share a flow
        i = min(
            range(len(groups) - 1),
            key=lambda k: sum(map(len, groups[k])) + sum(map(len, groups[k + 1])),
        )
        groups[i : i + 2] = [groups[i] + groups[i + 1]]
    out = []
    for group in groups:
        done = " ".join(g if g[-1:] in ".!?" else g + "." for g in group)
        words_ = group[0].replace("`", "").split()
        title = " ".join(words_[:9]).rstrip(".,;:") + (" …" if len(words_) > 9 else "")
        out.append({"id": f"F{len(out) + 1}", "title": title, "done_when": done[:600]})
    return out


def flows_md(dod: list[dict]) -> str:
    return "\n".join(
        f"- {f['id']}: {f['title']}"
        + (
            f" — done when: {f['done_when']}"
            if f.get("done_when") and f["done_when"] != f["title"]
            else ""
        )
        for f in dod
    )


def record(res: dict) -> dict:
    """The run's record of the stage (state, ``scope.json``, the ``scope`` event, run.json):
    everything but the markdown."""
    return {k: v for k, v in res.items() if k != "md"}


def write(root: Path, res: dict) -> None:
    (Path(root) / "scope.json").write_text(json.dumps(record(res), indent=1))
    if res.get("md"):
        (Path(root) / FILE).write_text(res["md"])

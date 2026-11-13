"""The owner's prompts: the prompts of the key steps, which the owner can see and steer.

The owner can inspect and customise what the agents are told, without a setting per
preference. This module holds:

- **The registry** (``REGISTRY``): every owner-facing prompt of repo_factory with its title, the
  step it drives, its file, its placeholders (each with a short description, and whether a
  full override must keep it) and ``editable``: ``full`` (additions and a full override) or
  ``additions-only`` (a checker: a full override could quietly switch a quality check off).
- **House rules** (``prompts.house_rules``): one free-text block of the owner's standing
  preferences, inserted at ``<!-- slot: house_rules -->`` in the prompts that decide what
  gets built or how (pitch, scope, the build stage, readiness's repair and the fix).
  Settings win over prose for mechanical things: the licence is ``publish.license``, written
  by code, and ``settings()`` reports it next to the house rules; prose is never parsed into
  settings.
- **Additions**: an optional owner block per prompt, at ``<!-- slot: additions -->`` or else
  at the end of the prompt's main part (before its first ``<!-- section: x -->``). A checker's
  additions sit under a heading that says they cannot relax its rules.
- **Full overrides** (``editable: full`` only): the complete template, stored with the
  default's hash at the time of the edit, so an upgrade that changes the default under an
  override is reported (``default_changed``) with the old default, the new one and the
  owner's.

**The store** is files in the home, never in a repo, append-only:
``<home>/prompts/<repo>/<id>/v<N>.json`` (the version: its template or none, its
additions, the default's hash, when), ``v<N>.md`` (its template, when it has one),
``v<N>.default.md`` (the default it was edited against) and ``active.json`` (the version in
use). House rules are ``<home>/prompts/house_rules/``. A reset is a new version, never a
delete. **Going back** is a restore: version N's template and additions copied into a new
version (action ``restore``, ``from: N``), checked like a save; the composed text is the same,
so its hash (the version provenance records) is too. An optional short note per version
(``notes.jsonl``, append-only, the last line per version wins) helps find a good one again.

The home is ``$REPO_FACTORY_PROMPTS_HOME`` (tests), else the home (``paths.home``:
``$REPO_FACTORY_HOME``, else ``~/.repo-factory``).

**Defaults only**: ``$REPO_FACTORY_PROMPTS=defaults`` (``defaults_only()``) gives every prompt as
shipped: no override, no additions, no house rules, so a measurement stays honest.

**Provenance**: every text served for a model call is noted (``note``): inside a build (an
active ledger with an event log) it emits one ``prompt`` event per prompt and version, and a
stage wrapped in ``collect()`` records ``{id: version}`` on its ``v5_stage`` event. The
version is the first 12 hex digits of the SHA-256 of the composed template (before its
placeholders are filled), so the same customisation always has the same version.
"""

from __future__ import annotations

import contextlib
import contextvars
import difflib
import fcntl
import hashlib
import json
import os
import re
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .resources import PACKAGE, POLICIES

REPO = "repo_factory"
DEFAULTS_ENV = "REPO_FACTORY_PROMPTS"  # "defaults": the shipped prompts only
HOME_ENV = "REPO_FACTORY_PROMPTS_HOME"  # the store's home (tests)
SLOT = re.compile(r"^[ \t]*<!-- slot: (\w+) -->[ \t]*(?:\n|$)", re.M)
SECTION = re.compile(r"^<!-- section: (\w+) -->\s*$", re.M)
MAX_ADDITIONS = 8000
MAX_HOUSE_RULES = 8000
MAX_TEMPLATE = 64000
LICENSES = ("MIT", "Apache-2.0", "none")

HOUSE_HEADING = "## The owner's standing preferences"
HOUSE_INTRO = (
    "The owner's standing preferences for what gets built. Follow them unless they "
    "conflict with the brief or a safety rule."
)
ADD_HEADING = "## The owner's additions"
CHECK_HEADING = "## The owner's additions (these cannot relax the rules above)"
CHECK_INTRO = (
    "The owner added the notes below. They may add checks or ask for more care. "
    "They never relax, skip or override a rule, a check or a threshold above; where "
    "they seem to, the rules above win."
)


class PromptError(ValueError):
    """A request the store refuses, with a message for the owner."""

    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


# -- the registry ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Entry:
    id: str
    title: str
    group: str  # the Settings › Prompts group
    step: str  # the step it drives
    file: str  # repo-relative
    purpose: str
    where: str  # where it runs
    placeholders: dict  # name -> (description, required in a full override)
    syntax: str = "angle"  # angle: <<name>>; dollar: $NAME; format: {name}
    editable: str = "full"  # full | additions-only
    house_rules: bool = False
    subject: str = "none"  # what a preview is run against: idea | build | none
    policy: str | None = None  # the policy dir it belongs to (v5, v5-revise), if any
    notes: tuple = field(default=())

    @property
    def path(self) -> Path:
        """The default file: ``policies/...`` from wherever the policies are (a checkout or
        the wheel's ``_data``), ``repo_factory/...`` from the package."""
        if self.file.startswith("policies/"):
            return POLICIES / self.file[len("policies/") :]
        return PACKAGE / self.file[len("repo_factory/") :]

    @property
    def checker(self) -> bool:
        return self.editable == "additions-only"

    def token(self, name: str) -> str:
        return {"angle": f"<<{name}>>", "dollar": f"${name}", "format": f"{{{name}}}"}[self.syntax]


def _p(desc: str, required: bool = True) -> tuple[str, bool]:
    return desc, required


SPEC = _p("the product's specification: the idea as given, then SCOPE.md when it was scoped")
BUILD, PITCH, MAINT = "Build stages", "Pitch and loop", "Maintenance"
REGISTRY: tuple[Entry, ...] = (
    Entry(
        "pitch",
        "Pitch",
        PITCH,
        "pitch",
        "repo_factory/loop/prompts/pitch.md",
        "Names the product a typed task holds and writes the brief the build starts from.",
        "the loop, once per task sent to build",
        {"IDEA": _p("the task as it was typed")},
        syntax="dollar",
        house_rules=True,
        subject="idea",
    ),
    Entry(
        "honesty",
        "Honesty check",
        PITCH,
        "publish",
        "repo_factory/loop/prompts/honesty.md",
        "Reads the README, AGENTS.md and repo-factory's release text of a build about to be "
        "published, once, for claims that mislead; its replacements are applied and listed "
        "(never a hold).",
        "the pre-push checks, in the background, after the public text is written",
        {"TEXT": _p("the README and AGENTS.md being published")},
        syntax="dollar",
        editable="additions-only",
        subject="build",
    ),
    Entry(
        "describe",
        "Public text",
        PITCH,
        "publish",
        "repo_factory/loop/prompts/describe.md",
        "Writes a built product's public text (the README's description, AGENTS.md's "
        "what-this-is, the repository description and the release notes' first line) from "
        "the product itself: its files, code, recorded help, tests and checks; never from "
        "the pitch.",
        "the pre-push checks, in the background, before the review",
        {
            "PRODUCT": _p("the product's name"),
            "FILES": _p("the staged tree's files"),
            "CODE": _p("excerpts of its code, entry points first"),
            "HELP": _p("the command-line help the exam recorded", False),
            "TESTS": _p("its tests, by file", False),
            "RESULTS": _p("what the gate's checks and the flows found", False),
        },
        syntax="dollar",
        subject="build",
    ),
    Entry(
        "lesson_issue",
        "Lesson from a user-found bug",
        PITCH,
        "learning",
        "repo_factory/loop/prompts/lesson_issue.md",
        "Reads a fixed, user-reported bug the checks missed and proposes at most one general "
        "lesson for the claims writer or the verifier.",
        "the learning job, per closed issue labelled lesson",
        {
            "kind": _p("the issue's kind (bug, ...)", False),
            "miss": _p("which check missed it, as the fix recorded it", False),
            "issue": _p("the issue's title and body, fenced as a document"),
            "test": _p("the acceptance test the fix added", False),
            "diff": _p("the fix's diff"),
            "lessons": _p("the lessons already live", False),
        },
    ),
    Entry(
        "scope",
        "Scope",
        BUILD,
        "scope",
        "policies/v5/prompts/scope.md",
        "Turns a short idea into SCOPE.md, the v1 product brief: users, flows, must-haves, "
        "rules, settings and the definition of done.",
        "a build's first stage",
        {
            "idea": _p("the idea or brief, as given"),
            "judge": _p(
                "the 'pass a concrete spec through' instructions (the judge section), "
                "empty when scope is always on",
                False,
            ),
        },
        house_rules=True,
        subject="idea",
        policy="v5",
    ),
    Entry(
        "build",
        "Build",
        BUILD,
        "build",
        "policies/v5/prompts/build.md",
        "What the builder session receives: the brief, SCOPE.md when it was scoped, and the "
        "instruction to work in the current directory.",
        "a build's build stage",
        {"spec": SPEC},
        house_rules=True,
        subject="build",
        policy="v5",
        notes=(
            "With no house rules and no additions the builder receives exactly the brief "
            "and SCOPE.md, as before.",
        ),
    ),
    Entry(
        "product",
        "What ships with the product",
        BUILD,
        "build",
        "policies/v5/prompts/product.md",
        "What a scoped build's product must carry, since nothing is added to it after the "
        "build: its own tests per flow, a README with a quick start, AGENTS.md "
        "and CLAUDE.md, a reporting SECURITY.md, CI, .gitignore and a CHANGELOG.",
        "a build's build stage, after the brief (scoped builds)",
        {},
        subject="build",
        policy="v5",
        notes=("A spec that passes through unscoped gets the vanilla prompt without it.",),
    ),
    Entry(
        "claims",
        "Claims",
        BUILD,
        "claims",
        "policies/v5/prompts/claims.md",
        "Lists the testable claims the specification makes, and its ambiguities.",
        "after the build",
        {
            "spec": SPEC,
            "lessons": _p("the lessons the claims writer has learnt", False),
            "max_claims": _p("the most claims to list", False),
            "max_ambiguities": _p("the most ambiguities to list", False),
        },
        subject="build",
        policy="v5",
    ),
    Entry(
        "verifier",
        "Verifier",
        BUILD,
        "verify",
        "policies/v5/prompts/verifier.md",
        "An independent session that tries the product and writes failing scenarios.",
        "after claims",
        {
            "spec": SPEC,
            "explore_pct": _p("the share of effort for exploring beyond the claims", False),
            "lessons": _p("the lessons the verifier has learnt", False),
            "scope": _p("the walk through SCOPE.md's flows, when scoped", False),
        },
        subject="build",
        policy="v5",
    ),
    Entry(
        "oracle",
        "Oracle panel",
        BUILD,
        "oracle",
        "policies/v5/prompts/oracle.md",
        "Judges whether a failing scenario is a real defect the specification requires.",
        "after verify, per failing scenario",
        {
            "spec": SPEC,
            "claims": _p("the failing scenarios' claims"),
            "answers": _p("the owner's answers so far", False),
            "settings": _p("the operator settings already decided", False),
        },
        editable="additions-only",
        subject="build",
        policy="v5",
    ),
    Entry(
        "pair",
        "Pairwise conflict",
        BUILD,
        "oracle",
        "policies/v5/prompts/pair.md",
        "Decides between two scenarios that the same code cannot both satisfy.",
        "repair rounds, on a conflict",
        {
            "spec": SPEC,
            "x": _p("the first scenario"),
            "y": _p("the second scenario"),
            "answers": _p("the owner's answers so far", False),
        },
        subject="build",
        policy="v5",
    ),
    Entry(
        "repair",
        "Repair",
        BUILD,
        "repair",
        "policies/v5/prompts/repair.md",
        "Has the builder fix the confirmed defects, one commit each.",
        "repair rounds",
        {
            "spec": SPEC,
            "findings": _p("the defects to fix"),
            "context": _p("what happened in earlier rounds", False),
            "settings": _p("the binary choices made into operator settings", False),
        },
        subject="build",
        policy="v5",
    ),
    Entry(
        "reverify",
        "Re-verify",
        BUILD,
        "reverify",
        "policies/v5/prompts/reverify.md",
        "Has the verifier check the repairs and look for what they broke.",
        "after each repair round",
        {
            "fixes": _p("the fixes the repair made"),
            "flows": _p("the walk through SCOPE.md's flows, when scoped", False),
            "settings": _p("the operator settings to check", False),
        },
        subject="build",
        policy="v5",
    ),
    Entry(
        "security",
        "Security review",
        BUILD,
        "readiness",
        "policies/v5/prompts/security.md",
        "Reviews the product for security problems and writes probes for them.",
        "the readiness stage",
        {"spec": SPEC},
        editable="additions-only",
        subject="build",
        policy="v5",
    ),
    Entry(
        "readiness",
        "Readiness",
        BUILD,
        "readiness",
        "policies/v5/prompts/readiness.md",
        "Checks by doing that the product is ready for production (install, defaults, "
        "backup, restart, ...), then has the builder fix what is not.",
        "the readiness stage",
        {"spec": SPEC, "checks": _p("the checks to run, with their ids")},
        editable="additions-only",
        house_rules=True,
        subject="build",
        policy="v5",
        notes=(
            "The checks decide whether a build is ready, so the whole file is "
            "additions-only. House rules go to its repair part, which changes the code.",
        ),
    ),
    Entry(
        "postmortem",
        "Post-mortem",
        BUILD,
        "learning",
        "policies/v5/prompts/postmortem.md",
        "Explains why the factory's checks missed what a benchmark found, and proposes lessons.",
        "the learning job on benchmark runs",
        {
            "spec": SPEC,
            "failures": _p("the failures to explain"),
            "claims": _p("claims.json", False),
            "findings": _p("findings.json", False),
            "lessons": _p("the lessons already live", False),
        },
        policy="v5",
    ),
    Entry(
        "triage",
        "Issue triage",
        MAINT,
        "triage",
        "repo_factory/loop/prompts/triage.md",
        "Gives a new issue on a published repo a kind, a priority and one comment.",
        "maintenance, per new issue",
        {
            "PRODUCT": _p("the product's name", False),
            "ONE_LINER": _p("what the product is", False),
            "OPEN": _p("the other open issues", False),
            "README": _p("the product's README", False),
            "WHAT": _p("issue or pull request", False),
            "NUMBER": _p("its number", False),
            "TITLE": _p("its title"),
            "BODY": _p("its text and the reporter's comments"),
        },
        syntax="dollar",
    ),
    Entry(
        "reproduce",
        "Reproduce",
        MAINT,
        "reproduce",
        "policies/v5-revise/prompts/reproduce.md",
        "Writes the failing acceptance test that reproduces an issue, before any fix.",
        "a maintenance fix's first stage",
        {
            "issue": _p("the issue, fenced as a document"),
            "product": _p("the product's name", False),
        },
        policy="v5-revise",
    ),
    Entry(
        "fix",
        "Fix",
        MAINT,
        "fix",
        "policies/v5-revise/prompts/fix.md",
        "Makes the reproducing test pass with the smallest change.",
        "a maintenance fix",
        {
            "issue": _p("the issue, fenced as a document"),
            "test": _p("the failing test to make pass (or the documentation note)"),
            "product": _p("the product's name", False),
        },
        house_rules=True,
        policy="v5-revise",
    ),
    Entry(
        "change",
        "Ask for changes",
        BUILD,
        "revise",
        "policies/v5-change/prompts/change.md",
        "Makes the changes the owner asked for on a held or ready build, in a revise round "
        "of that build (docs mode: Markdown only).",
        "a revise round's build stage",
        {
            "instructions": _p("the owner's request, fenced as a document"),
            "holds": _p("why the release was held, fenced as a document", False),
            "test": _p("the product's failing own tests (code mode) or the docs-mode note", False),
            "product": _p("the product's name", False),
        },
        house_rules=True,
        policy="v5-change",
    ),
    Entry(
        "charter",
        "Project charter",
        MAINT,
        "charter",
        "repo_factory/projects/prompts/charter.md",
        "Drafts a project's charter: what it is, for whom, what it promises and will not do, "
        "the ways it may grow and the tests a reviewer asks of any change.",
        "once per project: when a build is ready, at an adopted repo's onboarding, and on "
        "Redraft charter",
        {
            "SEED": _p("the originating idea, its pitch and how its claims are read"),
            "REPO": _p("the repository's own documents, fenced"),
        },
        syntax="dollar",
    ),
    Entry(
        "evolve",
        "What can we improve?",
        MAINT,
        "evolve",
        "repo_factory/projects/prompts/evolve.md",
        "Asks, for one project, what can be improved given its repo, its charter and its "
        "sources, and classes each evidence-cited proposal by kind and by class.",
        "per project, weekly (skipped when nothing changed), and on Check for ideas now",
        {
            "CHARTER": _p("the project's charter"),
            "REPO": _p("the repo's documents, open items, drift, issues and code map"),
            "WORLD": _p("the observations gathered for the project", False),
            "KNOWN": _p("the ideas already proposed and the ones declined", False),
            "MAX": _p("the most ideas to propose", False),
        },
        syntax="dollar",
    ),
)
BY_ID = {e.id: e for e in REGISTRY}
# The policy prompts by (policy dir name, file stem).
BY_POLICY = {(e.policy, Path(e.file).stem): e for e in REGISTRY if e.policy}


def entry(pid: str) -> Entry:
    try:
        return BY_ID[pid]
    except KeyError:
        raise PromptError(f"no prompt {pid!r} in repo_factory's registry", 404) from None


def policy_entry(policy: Path | str, name: str) -> Entry | None:
    """The registry entry for ``<policy>/prompts/<name>.md`` when ``policy`` is the shipped
    policy it belongs to (a copied or older policy keeps its own files untouched)."""
    p = Path(policy)
    e = BY_POLICY.get((p.name, name))
    if e is None:
        return None
    try:
        return e if p.resolve() == (POLICIES / e.policy).resolve() else None
    except OSError:
        return None


# -- the repo-factory home and the flag --------------------------------------------------------------
def defaults_only() -> bool:
    return os.environ.get(DEFAULTS_ENV, "").strip().lower() in ("defaults", "default", "off")


def use_defaults() -> None:
    """For a measurement: this process and every build it starts use the shipped prompts."""
    os.environ[DEFAULTS_ENV] = "defaults"


def factory_home() -> Path:
    """Where the store lives (the module's doc): ``$REPO_FACTORY_PROMPTS_HOME``, else the home
    (``paths.home``)."""
    return paths.home(os.environ.get(HOME_ENV))


def store() -> Store:
    return Store(factory_home() / "prompts")


# -- the store ----------------------------------------------------------------------------------
def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Store:
    """Append-only versions under ``root`` (``<repo-factory home>/prompts``): ``<parts>/v<N>.json``
    and friends, and ``active.json``. Nothing is ever rewritten but the pointer."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def dir(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def versions(self, *parts: str) -> list[dict]:
        d = self.dir(*parts)
        out = []
        for p in d.glob("v*.json") if d.is_dir() else []:
            if re.fullmatch(r"v\d+\.json", p.name):
                try:
                    out.append(json.loads(p.read_text()))
                except (OSError, ValueError):
                    continue
        return sorted(out, key=lambda v: v["n"])

    def active(self, *parts: str) -> dict | None:
        """The version in use, with its ``template`` text (None: the default) and the
        ``base`` (the default it was edited against); None when nothing was ever saved."""
        d = self.dir(*parts)
        try:
            n = int(json.loads((d / "active.json").read_text())["version"])
            meta = json.loads((d / f"v{n}.json").read_text())
        except (OSError, ValueError, KeyError, TypeError):
            return None
        meta["template"] = (d / f"v{n}.md").read_text() if meta.get("has_template") else None
        base = d / f"v{n}.default.md"
        meta["base"] = base.read_text() if base.exists() else None
        return meta

    def text(self, parts: tuple, n: int) -> str | None:
        p = self.dir(*parts) / f"v{int(n)}.md"
        return p.read_text() if p.exists() else None

    def version(self, parts: tuple, n: int) -> dict:
        """Version ``n`` as ``active()`` gives it (its ``template`` and ``base``), or a 404."""
        d = self.dir(*parts)
        try:
            meta = json.loads((d / f"v{int(n)}.json").read_text())
        except (OSError, ValueError, TypeError):
            raise PromptError(f"no version {n}", 404) from None
        meta["template"] = (
            (d / f"v{meta['n']}.md").read_text() if meta.get("has_template") else None
        )
        base = d / f"v{meta['n']}.default.md"
        meta["base"] = base.read_text() if base.exists() else None
        return meta

    def notes(self, *parts: str) -> dict[int, str]:
        """``{n: note}``: the owner's short note per version. ``notes.jsonl`` is append-only
        too: the last line for a version wins (an empty note clears it)."""
        out: dict[int, str] = {}
        try:
            lines = (self.dir(*parts) / "notes.jsonl").read_text().splitlines()
        except OSError:
            return out
        for line in lines:
            try:
                rec = json.loads(line)
                out[int(rec["n"])] = str(rec.get("note") or "")
            except (ValueError, KeyError, TypeError):
                continue
        return {n: t for n, t in out.items() if t}

    def set_note(self, parts: tuple, n: int, note: str) -> None:
        d = self.dir(*parts)
        if not (d / f"v{int(n)}.json").exists():
            raise PromptError(f"no version {n}", 404)
        with open(d / ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with open(d / "notes.jsonl", "a") as f:
                f.write(json.dumps({"n": int(n), "note": note, "at": _now()}) + "\n")

    def append(
        self,
        parts: tuple,
        *,
        action: str,
        template: str | None,
        additions: str,
        default: str | None = None,
        extra: dict | None = None,
        note: str = "",
    ) -> dict:
        """Write version N+1 and make it active. ``default``: the default text the version
        was made against (kept beside an override, for the three-way view)."""
        d = self.dir(*parts)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            n = max((v["n"] for v in self.versions(*parts)), default=0) + 1
            meta = {
                "n": n,
                "at": _now(),
                "action": action,
                "has_template": template is not None,
                "additions": additions or "",
                "default_hash": sha(default) if default is not None else None,
                **(extra or {}),
            }
            if template is not None:
                _create(d / f"v{n}.md", template)
                if default is not None:
                    _create(d / f"v{n}.default.md", default)
            _create(d / f"v{n}.json", json.dumps(meta, indent=1) + "\n")
            if note:
                with open(d / "notes.jsonl", "a") as f:
                    f.write(json.dumps({"n": n, "note": note, "at": meta["at"]}) + "\n")
            tmp = d / f".active.{os.getpid()}.tmp"
            tmp.write_text(json.dumps({"version": n, "at": meta["at"]}) + "\n")
            os.replace(tmp, d / "active.json")
        return meta


def _create(path: Path, text: str) -> None:
    """A new file, never an existing one (the versions are append-only)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


MAX_NOTE = 80


def clean_note(note) -> str:
    """A version's note: one line, at most ``MAX_NOTE`` characters (empty: none)."""
    if note is None:
        return ""
    if not isinstance(note, str):
        raise PromptError("note must be a string (empty: none)")
    note = " ".join(note.split())
    if len(note) > MAX_NOTE:
        raise PromptError(f"a note is at most {MAX_NOTE} characters")
    return note


def _version_n(n) -> int:
    try:
        n = int(n) if not isinstance(n, bool) else 0
    except (TypeError, ValueError):
        n = 0
    if n < 1:
        raise PromptError("version must be a version number (1 or more)")
    return n


# -- house rules and the settings beside them ------------------------------------------------
def house_rules(st: Store | None = None) -> str:
    if defaults_only():
        return ""
    st = st or store()
    a = st.active("house_rules")
    return (a or {}).get("text", "") or ""


def house_rules_view(st: Store | None = None) -> dict:
    st = st or store()
    a = st.active("house_rules")
    notes = st.notes("house_rules")
    vs = st.versions("house_rules")
    first: dict[str, int] = {}
    for v in vs:
        first.setdefault(sha(v.get("text") or ""), v["n"])
    return {
        "text": (a or {}).get("text", "") or "",
        "version": (a or {}).get("n", 0),
        "versions": [
            {k: v.get(k) for k in ("n", "at", "action")}
            | {
                "chars": len(v.get("text") or ""),
                "from": v.get("from"),
                "note": notes.get(v["n"], ""),
                "hash": sha(v.get("text") or ""),
                "same_as": _same_as(first, sha(v.get("text") or ""), v["n"]),
                "active": bool(a and v["n"] == a["n"]),
            }
            for v in vs
        ],
        "used_by": [e.id for e in REGISTRY if e.house_rules],
        "wrapped_as": f"{HOUSE_HEADING}\n\n{HOUSE_INTRO}",
        "settings": settings(),
        "defaults_only": defaults_only(),
        "max_chars": MAX_HOUSE_RULES,
        "max_note": MAX_NOTE,
    }


def _same_as(first: dict, h: str, n: int) -> int | None:
    """The first earlier version with the same hash (a restore's original), or None."""
    m = first.get(h)
    return m if m is not None and m < n else None


def set_house_rules(text, st: Store | None = None, note=None) -> dict:
    if not isinstance(text, str):
        raise PromptError("text must be a string (empty: no house rules)")
    if len(text) > MAX_HOUSE_RULES:
        raise PromptError(f"house rules are at most {MAX_HOUSE_RULES} characters")
    note = clean_note(note)
    st = st or store()
    text = text.strip()
    st.append(
        ("house_rules",),
        action="reset" if not text else "house_rules",
        template=None,
        additions="",
        extra={"text": text},
        note=note,
    )
    return house_rules_view(st)


def house_rules_version(n, st: Store | None = None) -> dict:
    """House rules version ``n``: its text and a diff against the text in use."""
    st = st or store()
    v = st.version(("house_rules",), _version_n(n))
    a = st.active("house_rules") or {}
    mine, cur = v.get("text") or "", a.get("text") or ""
    return {
        "n": v["n"],
        "at": v.get("at"),
        "action": v.get("action"),
        "from": v.get("from"),
        "note": st.notes("house_rules").get(v["n"], ""),
        "text": mine,
        "hash": sha(mine),
        "active_n": a.get("n", 0),
        "active_hash": sha(cur),
        "same_as_active": sha(mine) == sha(cur),
        "diff": _diff(cur, mine, f"in use (v{a.get('n', 0)})", f"v{v['n']}"),
        "restorable": True,
        "why_not": None,
    }


def restore_house_rules(body: dict | None, st: Store | None = None) -> dict:
    """``{"version": N}``: version N's text as a new version (action ``restore``)."""
    st = st or store()
    n = _version_n((body or {}).get("version"))
    v = st.version(("house_rules",), n)
    text = v.get("text") or ""
    if len(text) > MAX_HOUSE_RULES:
        raise PromptError(f"not restored: v{n} is over {MAX_HOUSE_RULES} characters")
    st.append(
        ("house_rules",),
        action="restore",
        template=None,
        additions="",
        extra={"text": text, "from": n},
        note=clean_note((body or {}).get("note")),
    )
    return house_rules_view(st)


def set_house_rules_note(n, body: dict | None, st: Store | None = None) -> dict:
    st = st or store()
    st.set_note(("house_rules",), _version_n(n), clean_note((body or {}).get("note")))
    return house_rules_view(st)


def settings(loop_home: Path | None = None) -> dict:
    """The mechanical choices code makes, shown beside the house rules: prose never sets
    them. ``publish.license``: the LICENSE the publish step writes."""
    from .settings.store import DEFAULTS, Config

    try:
        cfg = Config(loop_home)
        value = cfg["publish"].get("license")
        raw = cfg.layout.settings
        import yaml

        user = (yaml.safe_load(raw.read_text()) or {}) if raw.exists() else {}
        source = "setting" if "license" in (user.get("publish") or {}) else "default"
    except Exception:  # noqa: BLE001 - a bad loop.yaml: the default
        value, source = DEFAULTS["publish"]["license"], "default"
    return {
        "publish.license": {
            "value": value,
            "source": source,
            "options": list(LICENSES),
            "label": f"licence: {value} ({'setting' if source == 'setting' else 'default'})",
            "does": "The LICENSE file the publish step writes into every published repo. A "
            "setting, not prose: house rules never change it.",
            "set_with": 'POST api/prompts/license {"license": "MIT" | "Apache-2.0" | "none"}',
        }
    }


def set_license(value, loop_home: Path | None = None) -> dict:
    from .settings import write as settings_mod
    from .settings.store import Config

    if value not in LICENSES:
        raise PromptError(f"license must be one of {', '.join(LICENSES)}")
    settings_mod.write(Config(loop_home).layout.settings, ("publish", "license"), value)
    return settings(loop_home)


# -- composing a prompt ---------------------------------------------------------------------------
def escape(e: Entry, text: str) -> str:
    """Owner text made inert for the template's own placeholder syntax."""
    if e.syntax == "format":
        return text.replace("{", "{{").replace("}", "}}")
    if e.syntax == "dollar":
        return text.replace("$", "$$")
    return text


def house_block(e: Entry, text: str) -> str:
    return f"{HOUSE_HEADING}\n\n{HOUSE_INTRO}\n\n{escape(e, text.strip())}" if text.strip() else ""


def additions_block(e: Entry, text: str) -> str:
    if not text.strip():
        return ""
    if e.checker:
        return f"{CHECK_HEADING}\n\n{CHECK_INTRO}\n\n{escape(e, text.strip())}"
    return f"{ADD_HEADING}\n\n{escape(e, text.strip())}"


def _fill_slot(text: str, name: str, block: str) -> tuple[str, bool]:
    """``text`` with the first ``<!-- slot: name -->`` line replaced by ``block`` (followed
    by a blank line), or removed when ``block`` is empty; other lines of that slot go."""
    ms = [m for m in SLOT.finditer(text) if m.group(1) == name]
    if not ms:
        return text, False
    out, last = [], 0
    for i, m in enumerate(ms):
        out.append(text[last : m.start()])
        if i == 0 and block:
            out.append(block.rstrip() + "\n\n")
        last = m.end()
    out.append(text[last:])
    return "".join(out), True


def _append_main(text: str, block: str) -> str:
    """``block`` at the end of the main part: before the first section marker, else at the
    end."""
    m = SECTION.search(text)
    if m is None:
        return text.rstrip("\n") + "\n\n" + block.rstrip() + "\n"
    return text[: m.start()].rstrip("\n") + "\n\n" + block.rstrip() + "\n\n" + text[m.start() :]


_blocks: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "factory_prompt_blocks", default=None
)


@contextlib.contextmanager
def blocks(**named: str):
    """``with blocks(capabilities=text):``: every prompt composed inside the block gets
    ``text`` at its ``<!-- slot: capabilities -->`` (composed like the house rules). Empty text: nothing changes. The prompt's version (the
    hash of the composed text) records it."""
    tok = _blocks.set({**(_blocks.get() or {}), **{k: v for k, v in named.items() if v}})
    try:
        yield
    finally:
        _blocks.reset(tok)


def compose(e: Entry, template: str, additions: str = "", house: str = "") -> str:
    """The template with the house rules and additions in their slots (or appended to the
    main part), every other slot marker removed. With neither, the default composes to the
    text the code sent before the slots existed. A capabilities block (``blocks``) goes at
    ``<!-- slot: capabilities -->``; a template without that slot (another call in the same
    stage, or an owner's override that dropped it) gets none."""
    text = template
    cb = (_blocks.get() or {}).get("capabilities") or ""
    if cb.strip():
        text, _ = _fill_slot(text, "capabilities", escape(e, cb.strip()))
    hb = house_block(e, house) if e.house_rules else ""
    text, found = _fill_slot(text, "house_rules", hb)
    if hb and not found:
        text = _append_main(text, hb)
    ab = additions_block(e, additions)
    text, found = _fill_slot(text, "additions", ab)
    if ab and not found:
        text = _append_main(text, ab)
    return SLOT.sub("", text)


@dataclass
class Effective:
    id: str
    text: str  # the composed template (placeholders not filled)
    version: str  # sha of text
    n: int  # the store version in use (0: never customised)
    template: str  # "default" | "override"
    additions: bool
    house_rules: bool
    defaults_only: bool

    @property
    def custom(self) -> bool:
        return self.template == "override" or self.additions or self.house_rules

    def record(self) -> dict:
        return {
            "id": self.id,
            "repo": REPO,
            "version": self.version,
            "n": self.n,
            "custom": self.custom,
            "template": self.template,
            "additions": self.additions,
            "house_rules": self.house_rules,
        }


def default_text(e: Entry) -> str:
    return e.path.read_text()


def effective(
    pid: str, st: Store | None = None, *, defaults: bool | None = None, noted: bool = True
) -> Effective:
    """The text a model call of prompt ``pid`` gets (before its placeholders are filled).
    ``defaults``: the shipped prompt (default: the ``$REPO_FACTORY_PROMPTS`` flag). ``noted``: record
    it for provenance (``note``); a preview passes False."""
    e = entry(pid)
    dflt = default_text(e)
    only = defaults_only() if defaults is None else defaults
    a, house = None, ""
    if not only:
        try:
            st = st or store()
            a = st.active(REPO, e.id)
            house = house_rules(st) if e.house_rules else ""
        except OSError:
            a, house = None, ""
    tpl = a["template"] if a and a.get("template") is not None and not e.checker else None
    adds = (a or {}).get("additions") or ""
    text = compose(e, tpl if tpl is not None else dflt, adds, house)
    eff = Effective(
        e.id,
        text,
        sha(text),
        int((a or {}).get("n") or 0),
        "override" if tpl is not None else "default",
        bool(adds.strip()),
        bool(house.strip()),
        only,
    )
    if noted:
        note(eff)
    return eff


def text(pid: str) -> str:
    """The composed template of ``pid`` for a model call (noted for provenance)."""
    return effective(pid).text


def policy_text(policy: Path | str, name: str) -> str:
    """``<policy>/prompts/<name>.md`` as a model call gets it: composed when it is a
    registered prompt of the shipped policy, else the file as it is."""
    e = policy_entry(policy, name)
    if e is None:
        return (Path(policy) / "prompts" / f"{name}.md").read_text()
    return effective(e.id).text


# -- provenance -----------------------------------------------------------------------------------
_collect: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "factory_prompts_used", default=None
)
# keyed by the ledger itself, not id(): a later build's ledger can be given a freed one's id
_emitted: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


@contextlib.contextmanager
def collect():
    """``with collect() as used:``: ``used`` fills with ``{id: version}`` for every prompt
    served inside the block (threads started with ``contextvars.copy_context`` included)."""
    used: dict = {}
    tok = _collect.set(used)
    try:
        yield used
    finally:
        _collect.reset(tok)


def used_now() -> dict:
    """``{id: version}`` of the prompts served so far inside the current ``collect()`` block
    (empty outside one)."""
    return dict(_collect.get() or {})


def note(eff: Effective) -> None:
    used = _collect.get()
    if used is not None:
        used[eff.id] = eff.version
    from .costs import ledger

    led = ledger.active()
    if led is None or getattr(led, "events", None) is None:
        return
    seen = _emitted.setdefault(led, set())
    if (eff.id, eff.version) in seen:
        return
    seen.add((eff.id, eff.version))
    try:
        led.events.emit("prompt", stage=ledger.current_tags().get("stage"), **eff.record())
    except Exception:  # noqa: BLE001 - provenance never stops a build
        pass


def build_versions(policy: Path | str) -> dict:
    """``{id: version}`` of ``policy``'s registered prompts as they are now (not noted), for
    ``build_started``; ``{}`` for a policy that isn't a shipped one. Never raises."""
    try:
        name = Path(policy).name
        return {
            e.id: effective(e.id, noted=False).version
            for e in REGISTRY
            if e.policy == name and policy_entry(policy, Path(e.file).stem) is not None
        }
    except Exception:  # noqa: BLE001 - provenance never stops a build
        return {}


# -- placeholders and checks ----------------------------------------------------------------------
def placeholders_in(e: Entry, text: str) -> set[str]:
    if e.syntax == "angle":
        return set(re.findall(r"<<([a-z_]+)>>", text))
    if e.syntax == "dollar":
        return set(re.findall(r"\$\{?([A-Z_]+)\}?", text.replace("$$", "")))
    import string

    try:
        return {f for _, f, _, _ in string.Formatter().parse(text) if f}
    except ValueError:
        return set()


def sections(text: str) -> dict[str, str]:
    parts = SECTION.split(text)
    out = {"": parts[0]}
    for i in range(1, len(parts) - 1, 2):
        out[parts[i]] = parts[i + 1]
    return out


def check_template(e: Entry, template: str) -> list[str]:
    """Why a full override can't be saved (empty: it can)."""
    if e.checker:
        return [
            f"{e.title} is a checker: its rules can't be replaced, only added to (additions-only)"
        ]
    if not template.strip():
        return ["the template is empty"]
    if len(template) > MAX_TEMPLATE:
        return [f"the template is over {MAX_TEMPLATE} characters"]
    probs = []
    dflt, mine = sections(default_text(e)), sections(template)
    have = placeholders_in(e, mine[""])
    for name, (desc, required) in e.placeholders.items():
        if required and name not in have:
            probs.append(f"the template must keep {e.token(name)} ({desc})")
    for name, body in dflt.items():
        if not name:
            continue
        if name not in mine:
            probs.append(
                f"the template must keep its '<!-- section: {name} -->' part: the code asks for it"
            )
            continue
        for ph in sorted(placeholders_in(e, body) - placeholders_in(e, mine[name])):
            probs.append(f"the '{name}' part must keep {e.token(ph)}")
    if e.syntax == "format":
        import string

        try:
            list(string.Formatter().parse(template))
        except ValueError as x:
            probs.append(
                f"the template's braces don't parse ({x}); write a literal brace as {{{{ or }}}}"
            )
    return probs


# -- the API's views ------------------------------------------------------------------------------
def summary(e: Entry, st: Store | None = None) -> dict:
    st = st or store()
    a = st.active(REPO, e.id)
    eff = effective(e.id, st, noted=False)
    return {
        "repo": REPO,
        "id": e.id,
        "title": e.title,
        "group": e.group,
        "step": e.step,
        "file": e.file,
        "purpose": e.purpose,
        "where": e.where,
        "editable": e.editable,
        "house_rules": e.house_rules,
        "subject": e.subject,
        "customised": bool(a and (a.get("has_template") or (a.get("additions") or "").strip())),
        "override": bool(a and a.get("has_template")),
        "additions": bool(a and (a.get("additions") or "").strip()),
        "version": eff.version,
        "n": int((a or {}).get("n") or 0),
        "default_changed": _default_changed(e, a),
    }


def _default_changed(e: Entry, a: dict | None) -> bool:
    return bool(
        a
        and a.get("has_template")
        and a.get("default_hash")
        and a["default_hash"] != sha(default_text(e))
    )


def registry(st: Store | None = None) -> list[dict]:
    st = st or store()
    return [summary(e, st) for e in REGISTRY]


def detail(pid: str, st: Store | None = None) -> dict:
    e = entry(pid)
    st = st or store()
    a = st.active(REPO, e.id)
    dflt = default_text(e)
    eff = effective(e.id, st, noted=False)
    yours = a.get("template") if a else None
    out = {
        **summary(e, st),
        "placeholders": [
            {"name": k, "token": e.token(k), "description": d, "required": r}
            for k, (d, r) in e.placeholders.items()
        ],
        "syntax": e.syntax,
        "notes": list(e.notes),
        "slots": sorted({m.group(1) for m in SLOT.finditer(yours or dflt)}),
        "sections": [s for s in sections(dflt) if s],
        "default": dflt,
        "default_hash": sha(dflt),
        "template": yours,
        "additions_text": (a or {}).get("additions") or "",
        "active": eff.text,
        "defaults_only": defaults_only(),
        "versions": _version_rows(e, st, a, dflt),
        "max_note": MAX_NOTE,
        "headings": {
            "house_rules": HOUSE_HEADING if e.house_rules else None,
            "additions": CHECK_HEADING if e.checker else ADD_HEADING,
        },
    }
    if e.checker:
        out["checker_note"] = "additions only: this step's rules can't be relaxed"
    if e.house_rules:
        out["settings"] = settings()
    if out["default_changed"]:
        out["three_way"] = {
            "old_default": a.get("base"),
            "new_default": dflt,
            "yours": yours,
            "diff_default": _diff(a.get("base") or "", dflt, "old default", "new default"),
        }
        out["notice"] = "the default changed since you edited this"
    if yours is not None:
        out["diff"] = _diff(dflt, yours, "default", "yours")
    return out


def _diff(a: str, b: str, an: str, bn: str) -> str:
    return "".join(
        difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True), an, bn)
    )


# -- versions: see one, restore one, note one ----------------------------------------------------
def _composed(e: Entry, v: dict, dflt: str, house: str) -> str:
    """The text version ``v`` gives a call now (today's default and house rules), so its hash
    is the version provenance records; a restore of it has the same hash."""
    tpl = v.get("template") if v.get("has_template") and not e.checker else None
    return compose(e, tpl if tpl is not None else dflt, v.get("additions") or "", house)


def _house(e: Entry, st: Store) -> str:
    return house_rules(st) if e.house_rules else ""


def _version_rows(e: Entry, st: Store, a: dict | None, dflt: str) -> list[dict]:
    notes, house = st.notes(REPO, e.id), _house(e, st)
    rows, first = [], {}
    for v in st.versions(REPO, e.id):
        if v.get("has_template"):
            v["template"] = st.text((REPO, e.id), v["n"])
        h = sha(_composed(e, v, dflt, house))
        rows.append(
            {k: v.get(k) for k in ("n", "at", "action", "has_template", "default_hash", "from")}
            | {
                "additions_chars": len(v.get("additions") or ""),
                "active": bool(a and v["n"] == a["n"]),
                "note": notes.get(v["n"], ""),
                "hash": h,
                "same_as": _same_as(first, h, v["n"]),
            }
        )
        first.setdefault(h, v["n"])
    return rows


def _restore_problems(e: Entry, v: dict) -> list[str]:
    """Why version ``v`` can't be restored now (empty: it can): the same checks as a save."""
    if v.get("template") is None:
        return []
    if e.checker:
        return [
            f"{e.title} is a checker: additions only, so v{v['n']}'s full template can't "
            "be restored"
        ]
    return check_template(e, v["template"])


def version_view(pid: str, n, st: Store | None = None) -> dict:
    """Version ``n``: its template (None: the default), its additions, the text it gives a
    call and a unified diff of that against the version in use."""
    e = entry(pid)
    st = st or store()
    v = st.version((REPO, e.id), _version_n(n))
    a = st.active(REPO, e.id)
    dflt, house = default_text(e), _house(e, st)
    mine = _composed(e, v, dflt, house)
    cur = effective(e.id, st, noted=False)
    probs = _restore_problems(e, v)
    return {
        "repo": REPO,
        "id": e.id,
        "n": v["n"],
        "at": v.get("at"),
        "action": v.get("action"),
        "from": v.get("from"),
        "note": st.notes(REPO, e.id).get(v["n"], ""),
        "template": v.get("template"),
        "template_kind": "override" if v.get("template") is not None else "default",
        "additions": v.get("additions") or "",
        "text": mine,
        "hash": sha(mine),
        "active": bool(a and a["n"] == v["n"]),
        "active_n": cur.n,
        "active_hash": cur.version,
        "same_as_active": sha(mine) == cur.version,
        "diff": _diff(cur.text, mine, f"in use (v{cur.n})", f"v{v['n']}"),
        "restorable": not probs,
        "why_not": ("can't restore: " + "; ".join(probs)) if probs else None,
    }


def restore(pid: str, body: dict | None, st: Store | None = None) -> dict:
    """``{"version": N}``: version N's template and additions as a new version N+1 (action
    ``restore``, ``from: N``), checked like a save. Nothing moves back, nothing is deleted."""
    e = entry(pid)
    st = st or store()
    n = _version_n((body or {}).get("version"))
    note = clean_note((body or {}).get("note"))
    v = st.version((REPO, e.id), n)
    probs = _restore_problems(e, v)
    if probs:
        raise PromptError(f"not restored: v{n} can't be used now: " + "; ".join(probs))
    tpl = v.get("template")
    if tpl is not None and tpl == default_text(e):
        tpl = None
    st.append(
        (REPO, e.id),
        action="restore",
        template=tpl,
        additions=v.get("additions") or "",
        default=(v.get("base") or default_text(e)) if tpl is not None else None,
        extra={"from": n},
        note=note,
    )
    return detail(pid, st)


def set_note(pid: str, n, body: dict | None, st: Store | None = None) -> dict:
    """``{"note": str}`` (≤ 80 characters; empty clears it) on version ``n``."""
    e = entry(pid)
    st = st or store()
    st.set_note((REPO, e.id), _version_n(n), clean_note((body or {}).get("note")))
    return detail(pid, st)


def save(pid: str, body: dict, st: Store | None = None) -> dict:
    """``{"additions": str}`` and/or ``{"template": str}`` (``template`` null: drop the
    override) as a new version, with an optional ``note``."""
    e = entry(pid)
    if not isinstance(body, dict) or not ({"additions", "template"} & set(body)):
        raise PromptError("send additions, template, or both")
    note = clean_note(body.get("note"))
    st = st or store()
    a = st.active(REPO, e.id) or {}
    adds = a.get("additions") or ""
    tpl = a.get("template")
    base = a.get("base")
    if "additions" in body:
        if not isinstance(body["additions"], str):
            raise PromptError("additions must be a string (empty: none)")
        if len(body["additions"]) > MAX_ADDITIONS:
            raise PromptError(f"additions are at most {MAX_ADDITIONS} characters")
        adds = body["additions"].strip()
    if "template" in body:
        t = body["template"]
        if t is not None and not isinstance(t, str):
            raise PromptError("template must be a string, or null to drop the override")
        if e.checker and t is not None:
            raise PromptError(
                f"{e.title} is a checker: additions only, its rules can't be replaced"
            )
        if t is not None:
            probs = check_template(e, t)
            if probs:
                raise PromptError("not saved: " + "; ".join(probs))
            if t == default_text(e):
                t = None  # the default itself: no override
        if t != tpl:
            base = default_text(e) if t is not None else None
        tpl = t
    action = "template" if "template" in body else "additions"
    if tpl is None and not adds:
        action = "reset"
    st.append(
        (REPO, e.id),
        action=action,
        template=tpl,
        additions=adds,
        default=(base or default_text(e)) if tpl is not None else None,
        note=note,
    )
    return detail(pid, st)


def reset(pid: str, body: dict | None = None, st: Store | None = None) -> dict:
    """Back to the default as a new version (``what``: all, template or additions)."""
    e = entry(pid)
    st = st or store()
    what = (body or {}).get("what") or "all"
    if what not in ("all", "template", "additions"):
        raise PromptError("what must be all, template or additions")
    a = st.active(REPO, e.id) or {}
    tpl = None if what in ("all", "template") else a.get("template")
    adds = "" if what in ("all", "additions") else (a.get("additions") or "")
    st.append(
        (REPO, e.id),
        action="reset",
        template=tpl,
        additions=adds,
        default=(a.get("base") or default_text(e)) if tpl is not None else None,
    )
    return detail(pid, st)


# -- preview ------------------------------------------------------------------------------------
def redact(text: str) -> tuple[str, list[dict]]:
    """``loop/secrets.py``'s token shapes and secret-named random values, replaced by
    ``[redacted: <kind>]``."""
    from .loop import secrets

    hits: list[dict] = []

    def sub(kind):
        def f(m):
            hits.append({"kind": kind, "match": secrets.redact(m.group(0))})
            return f"[redacted: {kind}]"

        return f

    for kind, pat in secrets.TOKENS:
        text = pat.sub(sub(kind), text)

    def named(m):
        v = m.group(1)
        if not secrets.looks_random(v):
            return m.group(0)
        hits.append({"kind": "random value assigned to a secret name", "match": secrets.redact(v)})
        return m.group(0).replace(v, "[redacted]")

    text = secrets.NAMED.sub(named, text)
    return text, hits


def fill(e: Entry, template: str, values: dict) -> str:
    """The template with ``values`` filled the way the code fills it; a placeholder with no
    value shows as ``[name: description]``."""
    vals = {
        k: values.get(k) if values.get(k) is not None else f"[{k}: {d}]"
        for k, (d, _) in e.placeholders.items()
    }
    vals.update({k: v for k, v in values.items() if v is not None})
    if e.syntax == "angle":
        for k, v in vals.items():
            template = template.replace(f"<<{k}>>", str(v))
        return template
    if e.syntax == "dollar":
        from string import Template

        return Template(template).safe_substitute({k: str(v) for k, v in vals.items()})
    return template.format_map(_Missing(vals))


class _Missing(dict):
    def __missing__(self, k):
        return f"[{k}]"


def preview(pid: str, values: dict, subject: dict | None = None, st: Store | None = None) -> dict:
    """The text the model would be sent, with the active template, house rules and
    additions, filled from a real item; secret-scanned. Never calls a model."""
    e = entry(pid)
    eff = effective(e.id, st, noted=False)
    main = sections(eff.text)[""].strip() + "\n" if e.policy else eff.text
    raw = fill(e, main, values)
    out, hits = redact(raw)
    return {
        "repo": REPO,
        "id": e.id,
        "version": eff.version,
        "n": eff.n,
        "custom": eff.custom,
        "text": out,
        "chars": len(out),
        "redactions": hits,
        "filled": sorted(k for k in values if values.get(k) is not None),
        "subject": subject or {},
        "model_called": False,
    }

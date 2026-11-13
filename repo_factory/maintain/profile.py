"""An adopted repository's profile (MX-PLAN §2.6): the policy of a repo repo-factory did not build.

A product repo-factory published keeps its policy in its own ``.github/repo-factory.yml``. A repo the owner
adds by hand (``adopt_repo``) is someone's code that repo-factory did not write, so its policy lives in
repo-factory, never in the repo: ``<loop home>/maintain/repos/<owner>--<name>[--<subdir>].yaml``
(0600). An in-repo ``repo-factory.yml`` of an adopted repo is ignored: the owner did not merge it, so
it is not a trusted channel.

``DEFAULTS`` is the schema: every key a profile may have, with its default. ``for_access``
gives the defaults of an access level (MX-PLAN §1.2): the owner's own repo behaves like a
product (comments and triage on), a repo where the owner only has push gets pull requests and
nothing else (no triage, no comments, no labels), and a read-only or triage-only repo is
``watch``: read and show, write nothing. ``validate`` refuses unknown keys, bad values and the
settings that are never allowed on someone else's repo. Until the owner confirms the profile
(``confirm``), repo-factory polls and reads only."""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import time
from pathlib import Path

import yaml

from .. import paths

SCHEMA = 1
DIR = "repos"  # under <loop home>/maintain/
DETACHED = "detached"
STATUSES = ("maintained", "as-is", "watch", "paused", "detached")
LEVELS = ("admin", "maintain", "push", "triage", "pull")
PUSH = ("admin", "maintain", "push")
ACCESS_WORDS = {
    "admin": "admin access",
    "maintain": "maintain access",
    "push": "push access (pull requests; no triage, comments or labels by default)",
    "triage": "triage access (watch only until pull requests from a fork are on)",
    "pull": "read-only access (watch: repo-factory writes nothing there)",
}
# never touched by a fix on an adopted repo (plus loop.secrets.BAD_FILES, always)
# workflows, licences, notices and lockfiles: never changed by a fix, whatever the profile says
ALWAYS_DENIED = (
    ".github/workflows/**",
    "**/LICENSE*",
    "**/LICENCE*",
    "**/COPYING*",
    "**/NOTICE*",
    "**/*.lock",
    "**/package-lock.json",
    "**/npm-shrinkwrap.json",
    "**/pnpm-lock.yaml",
    "**/yarn.lock",
    "**/uv.lock",
    "**/poetry.lock",
    "**/Pipfile.lock",
    "**/go.sum",
    "**/Cargo.lock",
    "**/Gemfile.lock",
    "**/composer.lock",
    "**/bun.lockb",
)
DENIED = [
    *ALWAYS_DENIED,
    "**/SECURITY.md",
    "**/CODE_OF_CONDUCT.md",
    "**/CODEOWNERS",
    ".github/FUNDING.yml",
    ".github/repo-factory.yml",
    ".github/labels.yml",
]
SHELL = re.compile(r"[|;&<>`$]")

DEFAULTS: dict = {
    "schema": SCHEMA,
    "repo": None,
    "url": None,
    "subdir": None,
    "added": {"at": None, "by": None, "note": ""},
    "access": {
        "level": None,
        "admin": False,
        "maintain": False,
        "push": False,
        "triage": False,
        "pull": False,
        "checked": None,
        "owned": False,
        "login": None,
        "error": None,
    },
    "visibility": None,
    "default_branch": "main",
    "fork": False,
    "has_issues": True,
    "size_kb": None,
    "status": "watch",
    "confirmed": False,
    "onboarding": {
        "state": "none",
        "pid": None,
        "since": None,
        "finished": None,
        "stage": None,
        "commit": None,
        "error": None,
        "task": None,
        "proposal": None,
        "probe": None,
        "findings": None,
        "candidates": [],
    },
    "language": None,
    "commands": {
        "install": None,
        "test": [],
        "lint": [],
        "build": None,
        "cwd": ".",
        "parse": "none",
        "evidence": {},
        "typed": [],
    },
    "tests": {
        "count": ["tests/**"],
        "known_red": [],
        "flaky": [],
        "lint_gate": False,
        "dir": None,
        "example": None,
    },
    "baseline": None,
    "paths": {"allowed": ["**"], "denied": list(DENIED)},
    "branch": {"base": None, "prefix": "repo-factory/"},
    "pr": {
        "via": "direct",
        "draft_until_ci": True,
        "reviewers": [],
        "template": "respect",
        "commit_style": "plain",
    },
    "labels": {
        "scheme": "none",
        "map": {"go": None, "bug": None, "needs-you": None},
        "triggers": ["go"],
        "created": [],
    },
    "comments": "off",
    "triage": {"enabled": False, "close_duplicates": False, "answer_questions": False},
    "revise": {
        "trigger": "go",
        "auto_go": "off",
        "kinds": ["bug", "security", "docs", "dependency"],
        "max_attempts": 2,
        "lease_hours": 6,
        "max_usd": None,
        "models": {},
        "ci_repair": "on",
    },
    "merge": {"factory_patch": "never", "dependabot": "never"},
    "release": {"on_merge": "off", "prefix": "v"},
    "updates": {
        "dependencies": "off",
        "security": "off",
        "keep_ci_green": "off",
        "every_days": 7,
        "ci_red_hours": 6,
    },
    "settings": "off",
    "poll_s": None,
    "budget": {"monthly_usd": None, "per_fix_usd": None},
    "net_hosts": [],
    "identity": "default",
    "learning": {"repo_notes": True},
    "scope": {"labels": [], "title_prefix": None},
    "go_issues": [],
    "requests": [],
    "issues": {},
    "prs": {},
    "fork_repo": None,
    "evidence": {},
    "notes": [],
    "detached": None,
    "ci_main": None,  # the default branch's CI as the last poll saw it (§3.5)
    "updates_last": None,  # when updates.dependencies last asked for updates
}

ENUMS = {
    ("status",): STATUSES,
    ("commands", "parse"): ("junit", "go-json", "cargo", "tap", "jest-json", "none"),
    ("pr", "via"): ("direct", "fork", "none"),
    ("pr", "template"): ("respect", "ignore"),
    ("pr", "commit_style"): ("conventional", "plain"),
    ("labels", "scheme"): ("none", "mapped", "repo-factory"),
    ("comments",): ("on", "off"),
    ("revise", "trigger"): ("go", "owner", "off"),
    ("revise", "auto_go"): ("off", "patch"),
    ("revise", "ci_repair"): ("on", "off"),
    ("merge", "factory_patch"): ("never",),
    ("merge", "dependabot"): ("never", "patch", "minor"),
    ("release", "on_merge"): ("off", "patch", "minor"),
    ("updates", "dependencies"): ("off", "patch", "minor"),
    ("updates", "security"): ("off", "on"),
    ("updates", "keep_ci_green"): ("off", "ask", "on"),
    ("settings",): ("off", "on"),
    ("identity",): ("default", "owner", "bot"),
}
BOOLS = {
    ("confirmed",),
    ("tests", "lint_gate"),
    ("pr", "draft_until_ci"),
    ("triage", "enabled"),
    ("triage", "close_duplicates"),
    ("triage", "answer_questions"),
    ("learning", "repo_notes"),
}
STR_LISTS = {
    ("commands", "test"),
    ("commands", "lint"),
    ("tests", "count"),
    ("tests", "known_red"),
    ("tests", "flaky"),
    ("paths", "allowed"),
    ("paths", "denied"),
    ("pr", "reviewers"),
    ("labels", "triggers"),
    ("net_hosts",),
    ("scope", "labels"),
    ("commands", "typed"),
}
OPT_STR = {
    ("commands", "install"),
    ("commands", "build"),
    ("commands", "cwd"),
    ("branch", "base"),
    ("branch", "prefix"),
    ("release", "prefix"),
    ("subdir",),
    ("scope", "title_prefix"),
    ("tests", "dir"),
    ("tests", "example"),
    ("language",),
}
OPEN = {
    ("access",),
    ("onboarding",),
    ("baseline",),
    ("evidence",),
    ("issues",),
    ("prs",),
    ("go_issues",),
    ("requests",),
    ("notes",),
    ("labels", "map"),
    ("labels", "created"),
    ("commands", "evidence"),
    ("revise", "models"),
    ("added",),
    ("detached",),
    ("fork_repo",),
    ("ci_main",),
    ("updates_last",),
}
# what the owner may set through the pane, the command line or the settings form; the rest is
# repo-factory's own bookkeeping
EDITABLE = (
    "status",
    "commands",
    "tests",
    "paths",
    "branch",
    "pr",
    "labels",
    "comments",
    "triage",
    "revise",
    "merge",
    "release",
    "updates",
    "settings",
    "poll_s",
    "budget",
    "net_hosts",
    "identity",
    "learning",
    "scope",
)


class ProfileError(ValueError):
    pass


# -- names and files ---------------------------------------------------------------------------
def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:60] or "root"


def key(full: str, subdir: str | None = None) -> str:
    """The profile's name: ``owner/name`` or ``owner/name#sub/dir``."""
    return f"{full}#{subdir.strip('/')}" if subdir and subdir.strip("/") else full


def split_key(k: str) -> tuple[str, str | None]:
    full, _, sub = str(k or "").partition("#")
    return full, (sub or None)


def file_name(full: str, subdir: str | None = None) -> str:
    owner, name = full.split("/")
    return f"{owner}--{name}" + (f"--{slug(subdir)}" if subdir else "") + ".yaml"


def root(home) -> Path:
    return paths.Layout(home).maintain / DIR


def path(home, full: str, subdir: str | None = None) -> Path:
    return root(home) / file_name(full, subdir)


def work_dir(home, full: str) -> Path:
    """One clone per repo, shared by its subdir profiles (``maintain/work/<owner>-<name>/``)."""
    return paths.Layout(home).maintain / "work" / full.replace("/", "-")


# -- the schema --------------------------------------------------------------------------------
def merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if (
            isinstance(v, dict)
            and isinstance(out.get(k), dict)
            and (k,) not in OPEN
            and k not in ("models", "map", "evidence")
        ):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _walk(doc: dict, ref: dict, path: tuple, errors: list) -> None:
    for k, v in doc.items():
        p = (*path, str(k))
        name = ".".join(p)
        if k not in ref:
            errors.append(f"unknown key {name}")
            continue
        if p in OPEN:
            continue
        r = ref[k]
        if isinstance(r, dict):
            if not isinstance(v, dict):
                errors.append(f"{name} must be a mapping")
            else:
                _walk(v, r, p, errors)
            continue
        if p in ENUMS:
            if v is False and "off" in ENUMS[p]:
                doc[k] = v = "off"
            if v is True and "on" in ENUMS[p]:
                doc[k] = v = "on"
            if v not in ENUMS[p]:
                errors.append(f"{name} must be one of {', '.join(ENUMS[p])} (got {v!r})")
        elif p in BOOLS:
            if not isinstance(v, bool):
                errors.append(f"{name} must be true or false (got {v!r})")
        elif p in STR_LISTS:
            if not isinstance(v, list) or any(not isinstance(x, str) or not x.strip() for x in v):
                errors.append(f"{name} must be a list of text (got {v!r})")
        elif p in OPT_STR:
            if v is not None and not isinstance(v, str):
                errors.append(f"{name} must be text or empty (got {v!r})")
        elif p in (
            ("revise", "max_attempts"),
            ("updates", "every_days"),
            ("updates", "ci_red_hours"),
        ):
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                errors.append(f"{name} must be a whole number of at least 1 (got {v!r})")
        elif p in (("revise", "lease_hours"),):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                errors.append(f"{name} must be a positive number of hours (got {v!r})")
        elif p in (("revise", "max_usd"), ("budget", "monthly_usd"), ("budget", "per_fix_usd")):
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0):
                errors.append(f"{name} must be empty (no cap) or a positive amount (got {v!r})")
        elif p == ("poll_s",):
            if v is not None and (
                isinstance(v, bool) or not isinstance(v, int) or not 60 <= v <= 86400
            ):
                errors.append(
                    f"poll_s must be empty (the global poll_s) or 60 to 86400 seconds (got {v!r})"
                )
        elif p == ("revise", "kinds"):
            from . import labels as L

            if not isinstance(v, list) or any(x not in L.KINDS for x in v):
                errors.append(f"{name} must be a list of {', '.join(L.KINDS)} (got {v!r})")


def owned(prof: dict) -> bool:
    return bool((prof.get("access") or {}).get("owned"))


def can_push(prof: dict) -> bool:
    return (prof.get("access") or {}).get("level") in PUSH


def can_label(prof: dict) -> bool:
    return (prof.get("access") or {}).get("level") in (*PUSH, "triage")


def validate(prof, *, candidates: list[str] | None = None, typed_ok: bool = False) -> list[str]:
    """Problems with a profile, as plain sentences; ``[]`` when it is valid. ``candidates``:
    the commands onboarding found in the repository; a command that is not one of them and
    holds a pipe, a redirection or a ``;`` is refused unless ``typed_ok`` (the owner's second
    confirm: "this command was typed by you, not found in the repository")."""
    if not isinstance(prof, dict):
        return ["the profile must be a mapping of settings"]
    errors: list[str] = []
    _walk(prof, DEFAULTS, (), errors)
    if errors:
        return errors
    p = merge(DEFAULTS, prof)
    out_repo = not owned(p)
    if out_repo:
        if (p["revise"] or {}).get("auto_go") == "patch":
            errors.append(
                "revise.auto_go: patch is refused on a repository you do not own (its "
                "collaborators are people repo-factory does not know)"
            )
        if (p["release"] or {}).get("on_merge") != "off":
            errors.append("release.on_merge: releases are made only on your own repositories")
        if (p["merge"] or {}).get("dependabot") != "never":
            errors.append(
                "merge.dependabot: repo-factory never merges on a repository you do not own"
            )
        if p.get("settings") == "on":
            errors.append(
                "settings: repo-factory changes repository settings only on your own repositories"
            )
    lvl = (p.get("access") or {}).get("level")
    if lvl and p["pr"]["via"] == "direct" and lvl not in PUSH:
        errors.append(
            "pr.via: direct needs push access; this repository is "
            f"{lvl}-only for repo-factory (turn on pull requests from a fork: pr.via: fork)"
        )
    if lvl and p["labels"]["scheme"] != "none" and lvl not in (*PUSH, "triage"):
        errors.append("labels.scheme: repo-factory cannot label on a read-only repository")
    for g in (*p["paths"]["allowed"], *p["paths"]["denied"], *p["tests"]["count"]):
        try:
            glob_re(g)
        except re.error as e:
            errors.append(f"the glob {g!r} does not compile: {e}")
    cands = set(
        candidates
        if candidates is not None
        else (p.get("onboarding") or {}).get("candidates") or []
    )
    typed = set(p["commands"].get("typed") or [])
    for c in [
        p["commands"].get("install"),
        *(p["commands"]["test"] or []),
        *(p["commands"]["lint"] or []),
        p["commands"].get("build"),
    ]:
        if not c:
            continue
        if "\n" in c:
            errors.append(f"a command is one line: {c!r}")
        elif SHELL.search(c) and c not in cands and c not in typed and not typed_ok:
            errors.append(
                f"`{c}` was typed by you, not found in the repository, and has a pipe, "
                "a redirection or a `;`: confirm it again to keep it"
            )
    return errors


# -- the access level's defaults (MX-PLAN §1.2) ------------------------------------------------
def access_of(perms: dict | None) -> str:
    perms = perms or {}
    for lvl in LEVELS:
        if perms.get(lvl):
            return lvl
    return "pull"


def for_access(level: str, is_owned: bool) -> dict:
    """The defaults of an access level: what repo-factory may do on such a repo until the owner says
    more (each is a setting on the repo's pane)."""
    d: dict = {"pr": {"via": "direct" if level in PUSH else "none"}}
    if is_owned:
        d.update(
            comments="on",
            triage={"enabled": True, "close_duplicates": False, "answer_questions": True},
        )
    else:
        d.update(
            comments="off",
            triage={"enabled": False, "close_duplicates": False, "answer_questions": False},
        )
    d["status"] = "maintained" if level in PUSH else "watch"
    return d


def skeleton(
    facts: dict,
    *,
    subdir: str | None,
    by: str,
    note: str = "",
    status: str | None = None,
    now: float | None = None,
) -> dict:
    """A new, unconfirmed profile from ``adopt.validate``'s facts."""
    now = now or time.time()
    lvl = facts["access"]["level"]
    p = merge(DEFAULTS, for_access(lvl, facts["access"]["owned"]))
    p.update(
        repo=facts["repo"],
        url=facts["url"],
        subdir=subdir,
        added={"at": now, "by": by, "note": note or ""},
        access=dict(facts["access"]),
        visibility=facts.get("visibility"),
        default_branch=facts.get("default_branch") or "main",
        fork=bool(facts.get("fork")),
        has_issues=bool(facts.get("has_issues", True)),
        size_kb=facts.get("size_kb"),
    )
    p["branch"]["base"] = p["default_branch"]
    if subdir:
        p["paths"]["allowed"] = [f"{subdir.strip('/')}/**"]
        p["tests"]["count"] = [f"{subdir.strip('/')}/tests/**"]
        p["commands"]["cwd"] = subdir.strip("/")
    if status:
        p["status"] = status
    if lvl not in PUSH and p["status"] == "maintained":
        p["status"] = "watch"
    if not p["has_issues"]:
        p["status"] = "watch" if p["status"] == "maintained" and lvl not in PUSH else p["status"]
    p["confirmed"] = False
    return p


# -- reading and writing -----------------------------------------------------------------------
def load_file(f: Path) -> dict | None:
    try:
        doc = yaml.safe_load(Path(f).read_text())
    except (OSError, yaml.YAMLError):
        return None
    return merge(DEFAULTS, doc) if isinstance(doc, dict) else None


def load(home, k: str) -> dict | None:
    """The profile named ``owner/name`` or ``owner/name#subdir``; None when there is none."""
    full, sub = split_key(k)
    if full.count("/") != 1:
        return None
    return load_file(path(home, full, sub))


def every(home, detached: bool = False) -> list[dict]:
    """Every profile (detached ones only with ``detached``), sorted by name."""
    d = root(home) / (DETACHED if detached else "")
    out = []
    if d.is_dir():
        for f in sorted(d.glob("*.yaml")):
            p = load_file(f)
            if p and p.get("repo"):
                out.append(p)
    return out


def save(home, prof: dict) -> Path:
    """Write the profile in one step (a temp file and a rename), 0600."""
    f = path(home, prof["repo"], prof.get("subdir"))
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(dump(prof))
    os.replace(tmp, f)
    return f


def dump(prof: dict) -> str:
    doc = json.loads(json.dumps(prof, default=str))  # plain types only
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


@contextlib.contextmanager
def locked(home, k: str):
    """One writer at a time for one profile, across the poll, a fix and an onboarding (each
    its own process): an exclusive ``flock`` on ``maintain/repos/.<file>.lock``."""
    import fcntl

    full, sub = split_key(k)
    f = root(home) / ("." + file_name(full, sub) + ".lock")
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def update(home, k: str, fn) -> dict:
    """Read, change (``fn(profile)``) and write one profile under its lock; ProfileError when
    it is gone (removed meanwhile: nothing is written, so a removal is never undone)."""
    with locked(home, k):
        p = load(home, k)
        if p is None:
            raise ProfileError(f"{k} is not in maintenance")
        fn(p)
        save(home, p)
        return p


def name_of(prof: dict) -> str:
    return key(prof["repo"], prof.get("subdir"))


# -- the owner's edits -------------------------------------------------------------------------
def set_value(prof: dict, dotted: str, value) -> None:
    """``commands.test`` = value (a JSON or YAML value from the command line or the form)."""
    parts = [x for x in str(dotted).split(".") if x]
    if not parts or parts[0] not in EDITABLE:
        raise ProfileError(
            f"{dotted} is not a setting of the profile (settings: {', '.join(EDITABLE)})"
        )
    node = prof
    for k in parts[:-1]:
        if not isinstance(node.get(k), dict):
            raise ProfileError(f"{dotted}: {k} is not a group of settings")
        node = node[k]
    node[parts[-1]] = value


def parse_value(text: str):
    """A value typed on the command line: YAML (``["make test"]``, ``true``, ``3``), else
    the text itself."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


def edit(home, k: str, changes: dict, *, typed_ok: bool = False, by: str = "ui") -> dict:
    """Apply ``{"commands.test": [...], ...}`` to a profile, validate the result and write it
    in one step; ProfileError lists every problem and nothing is written."""
    p = load(home, k)
    if p is None:
        raise ProfileError(f"{k} is not in maintenance")
    new = copy.deepcopy(p)
    for dotted, v in (changes or {}).items():
        set_value(new, dotted, v)
    if typed_ok:
        c = new["commands"]
        now_cmds = [
            x
            for x in [
                c.get("install"),
                *(c.get("test") or []),
                *(c.get("lint") or []),
                c.get("build"),
            ]
            if isinstance(x, str) and x
        ]
        c["typed"] = sorted({*(c.get("typed") or []), *now_cmds})
    errors = validate(new, typed_ok=typed_ok)
    if errors:
        raise ProfileError("; ".join(errors))
    if new.get("status") == "maintained" and not can_push(new) and new["pr"]["via"] != "fork":
        raise ProfileError(
            "status: maintained needs push access or pull requests from a fork "
            "(pr.via: fork); this repository stays watch"
        )
    if new.get("status") in ("detached",):
        raise ProfileError("status: detached is Remove from maintenance, not a setting")
    save(home, new)
    return new


def confirm(home, k: str, by: str = "ui") -> dict:
    """The owner confirmed the profile: from now on repo-factory works on the repo as it says. A repo
    with push (or a fork set up) becomes ``maintained`` with ``revise.trigger: go``; a
    read-only one stays ``watch`` until ``pr.via: fork``."""

    def fn(p):
        errors = validate(p)
        if errors:
            raise ProfileError("; ".join(errors))
        p["confirmed"] = True
        p.setdefault("added", {})["confirmed"] = {"at": time.time(), "by": by}
        if p.get("status") in ("watch", "maintained", None):
            p["status"] = "maintained" if can_push(p) or p["pr"]["via"] == "fork" else "watch"
        if p["revise"].get("trigger") == "off":
            p["revise"]["trigger"] = "go"

    return update(home, k, fn)


def effective_status(prof: dict) -> str:
    """What repo-factory does with the repo now: ``watch`` until it is confirmed (and while the
    identity cannot reach it), else its status."""
    st = prof.get("status") or "watch"
    if st in ("paused", "detached"):
        return st
    if not prof.get("confirmed"):
        return "watch"
    if (prof.get("access") or {}).get("error"):
        return "watch"
    return st


def go_numbers(prof: dict) -> set[int]:
    """The issues the owner pressed Fix this on (the factory-side ``go``)."""
    out = set()
    for g in prof.get("go_issues") or []:
        try:
            out.add(int(g["number"] if isinstance(g, dict) else g))
        except (TypeError, ValueError, KeyError):
            pass
    return out


def glob_re(g: str) -> re.Pattern:
    """A path glob: ``*`` and ``?`` stay inside one directory, ``**`` crosses directories and
    ``**/`` also matches no directory at all (``**/*.lock`` matches ``a.lock``)."""
    out, i = "", 0
    while i < len(g):
        if g.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif g.startswith("**", i):
            out, i = out + ".*", i + 2
        elif g[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif g[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(g[i]), i + 1
    return re.compile(out + r"\Z")


def matches(path_: str, globs: list[str]) -> bool:
    """A repo-relative path against path globs (``glob_re``)."""
    return any(glob_re(g).match(path_) for g in globs or [])


# -- maintain.repos, migrated (MX-PLAN §9 PR 1; the hazard of §0) ------------------------------
def migrate(cfg, ledger=None, now: float | None = None) -> list[str]:
    """Each name in ``maintain.repos`` becomes an unconfirmed ``watch`` profile with triage and
    comments off, and the list is emptied in loop.yaml. Before this, any name typed there was
    polled with the missing-policy defaults, **triage on**: repo-factory would have commented, as the
    owner, on strangers' issues. Returns the names migrated."""
    names = [
        x
        for x in (cfg.get("maintain") or {}).get("repos") or []
        if isinstance(x, str) and x.count("/") == 1
    ]
    if not names:
        return []
    products = set()
    if ledger is not None:
        products = {
            (i.get("publish") or {}).get("repo") for i in ledger.all(("published", "public"))
        }
    done = []
    for full in dict.fromkeys(names):
        if full in products or path(cfg.home, full).exists():
            continue
        p = merge(DEFAULTS, for_access("pull", False))
        p.update(
            repo=full,
            url=f"https://github.com/{full}",
            status="watch",
            confirmed=False,
            added={
                "at": now or time.time(),
                "by": "migration",
                "note": "was listed in maintain.repos (More repos to maintain)",
            },
        )
        p["pr"]["via"] = "none"
        p["comments"] = "off"
        p["triage"]["enabled"] = False
        save(cfg.home, p)
        done.append(full)
    from ..settings import write as settings

    settings.write(cfg.layout.settings, ("maintain", "repos"), [])
    cfg.data.setdefault("maintain", {})["repos"] = []
    return done

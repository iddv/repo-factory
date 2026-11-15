"""The merge judge (``judge``): whether one of repo-factory's own fix pull requests may be
merged now, as the maintenance poll's merge step (``watch.merge_step``) asks it, with the
repository's merge switch on. Each is **refused** (it waits for the owner, with the reason on
the page) when any of these holds:

- its kind is not in ``merge.kinds``; a ``feature`` never, whatever the file says;
- it is labelled ``hold`` or ``needs-you``, or a review requests changes;
- anyone else pushed a commit to it (``known_head``);
- its diff is over ``merge.max_lines`` changed lines (added plus removed) outside its tests;
- it touches ``.github/``, a LICENSE, NOTICE or COPYING file, a lockfile, or any other file
  whose change runs somewhere (``runs_somewhere``);
- its head commit has no check run (a repo without CI never auto-merges), or one that is not
  ``success`` (``skipped`` aside; a cancelled job is not green).

It **waits** while it is behind ``main`` or conflicts, while GitHub works out whether it
merges, and while a check is queued or running."""

from __future__ import annotations

from pathlib import PurePosixPath

from . import labels as L
from .github import GitHubError, Repo
from .words import meta

NEVER_KINDS = ("feature",)
LOCKFILES = {
    "uv.lock",
    "poetry.lock",
    "pdm.lock",
    "Pipfile.lock",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "bun.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "go.sum",
    "composer.lock",
    "mix.lock",
    "flake.lock",
    "packages.lock.json",
    "gradle.lockfile",
}
GREEN = ("success", "skipped")  # a skipped job's `if:` was false; nothing else counts
SLACK_S = 300  # a commit made up to 5 minutes after its pull request opened
WEB_FLOW = "web-flow"  # who commits GitHub's own merges (Update branch)

# Files whose change runs somewhere (an install, a build, a test run, an editor or an agent on
# the next person's machine): merging never takes a diff that touches one, whatever its kind.
# Names anywhere in the tree, then folders, then name patterns.
RUNS_NAMES = {
    # package manifests (their lock files are LOCKFILES)
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "MANIFEST.in",
    "Pipfile",
    "package.json",
    ".npmrc",
    ".yarnrc",
    ".yarnrc.yml",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "composer.json",
    "environment.yml",
    "environment.yaml",
    "build.rs",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "deno.json",
    "deno.jsonc",
    "bunfig.toml",
    # build and test configuration
    "conftest.py",
    "tox.ini",
    "pytest.ini",
    "noxfile.py",
    ".coveragerc",
    "Makefile",
    "makefile",
    "GNUmakefile",
    "justfile",
    "Justfile",
    "Taskfile.yml",
    "Taskfile.yaml",
    "CMakeLists.txt",
    "Rakefile",
    "Dockerfile",
    "Containerfile",
    ".dockerignore",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    "tsconfig.json",
    ".babelrc",
    ".mocharc.json",
    ".mocharc.yml",
    ".pre-commit-config.yaml",
    # agents and editors, and what git itself reads
    "AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    ".gitattributes",
    ".gitmodules",
    ".envrc",
}
RUNS_DIRS = (".github/", ".claude/", ".vscode/", ".devcontainer/", ".idea/", ".cursor/", ".husky/")
RUNS_PATTERNS = (
    r"requirements[^/]*\.(?:txt|in)",
    r"constraints[^/]*\.txt",
    r"Dockerfile\..*",
    r"(?:jest|vite|vitest|webpack|rollup|babel|eslint|prettier|playwright|svelte|tailwind|"
    r"postcss|karma|cypress|next|nuxt|astro)\.config\.[cm]?[jt]s",
    r".*\.mk",
)
DOCUMENTS = (".md", ".rst", ".txt")


def runs_somewhere(f: str) -> str | None:
    """Why a change of ``f`` runs somewhere (a manifest, a lock file, build or test settings,
    ``.github/``, an agent's or editor's file, git's own), or None."""
    import re

    p = PurePosixPath(f)
    if p.name in LOCKFILES or p.name.endswith(".lock"):
        return f"`{f}` is a lockfile"
    if any(f.startswith(d) or f"/{d}" in f"/{f}" for d in RUNS_DIRS):
        return f"`{f}` is in a folder whose files run somewhere (CI, an agent, an editor)"
    if p.name in RUNS_NAMES or any(re.fullmatch(x, p.name) for x in RUNS_PATTERNS):
        return f"`{f}` runs somewhere (a manifest, build or test settings, an agent's file)"
    return None


def is_document(f: str) -> bool:
    """A document a ``docs`` fix may change: ``*.md``, ``*.rst``, ``*.txt`` or under
    ``docs/``, and nothing that runs somewhere (``AGENTS.md``, ``requirements.txt``)."""
    return (f.startswith("docs/") or f.endswith(DOCUMENTS)) and runs_somewhere(f) is None


def never_path(f: str) -> str | None:
    """Why auto-merge never takes a diff touching ``f``, or None: ``.github/``, a licence or
    notice, and everything that runs somewhere (``runs_somewhere``)."""
    p = PurePosixPath(f)
    if f.startswith(".github/"):
        return f"`{f}` is under .github/"
    name = p.name.upper()
    if name.startswith(("LICENSE", "LICENCE", "NOTICE", "COPYING")):
        return f"`{f}` is a licence or notice file"
    return runs_somewhere(f)


def checks(r: Repo, sha: str) -> dict:
    """The check runs on ``sha``, judged strictly: ``green`` (every one finished ``success``
    or ``skipped``, and at least one ``success``), ``pending``, ``red`` or ``none``."""
    # every page: a head with more than 100 check runs is judged on all of them
    pages = r.get(f"repos/{r.full}/commits/{sha}/check-runs?per_page=100", paginate=True) or []
    runs = [
        x
        for page in pages
        if isinstance(page, dict)
        for x in (page.get("check_runs") or [])
        if isinstance(x, dict)
    ]
    if not runs:
        return {"state": "none", "runs": 0}
    waiting = [x.get("name") for x in runs if x.get("status") != "completed"]
    bad = [
        f"{x.get('name')} ({x.get('conclusion')})"
        for x in runs
        if x.get("status") == "completed" and x.get("conclusion") not in GREEN
    ]
    if bad:
        return {"state": "red", "runs": len(runs), "bad": bad}
    if waiting:
        return {"state": "pending", "runs": len(runs), "waiting": waiting}
    if not any(x.get("conclusion") == "success" for x in runs):
        return {"state": "none", "runs": len(runs)}
    return {"state": "green", "runs": len(runs)}


def changes_requested(reviews: list[dict]) -> list[str]:
    """Who asks for changes now (each reviewer's latest word that counts)."""
    last: dict[str, str] = {}
    for x in sorted(reviews, key=lambda x: x.get("at") or 0):
        if x["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            last[x["user"]] = x["state"]
    return sorted(u for u, s in last.items() if s == "CHANGES_REQUESTED")


def short(sha: str | None) -> str:
    return (sha or "")[:7]


def issue_of(item: dict) -> int | None:
    """The issue a fix pull request fixes, from repo-factory's meta line in its body."""
    m = meta(item.get("body") or "") or {}
    try:
        return int(m["issue"])
    except (KeyError, ValueError):
        return None


def known_head(r: Repo, pull: dict, b: dict) -> bool:
    """Whether the pull request's head is repo-factory's own (``b["head"]`` is moved on when it
    is): the head repo-factory knows, a merge GitHub's *Update branch* made on top of it, or, for
    one repo-factory knows nothing of yet, a branch whose every commit predates the pull request
    (or is such a merge). Anything else: someone else pushed, and repo-factory keeps its hands
    off."""
    head, mine = pull["head"], b.get("head")
    if not head:
        return False
    if head == mine:
        return True
    if mine:
        c = r.commit(head)
        if len(c["parents"]) == 2 and c["parents"][0] == mine and c["committer"] == WEB_FLOW:
            b["head"] = head
            return True
        return False
    opened = pull.get("created") or 0
    for c in r.pull_commits(pull["number"]):
        merge = len(c["parents"]) == 2 and c["committer"] == WEB_FLOW
        if not merge and (c["date"] or 0) > opened + SLACK_S:
            return False
    b["head"] = head
    return True


def kinds_of(r: Repo, item: dict) -> set[str]:
    """The pull request's kind labels and its issue's (the owner may have relabelled it)."""
    out = {x for x in item["labels"] if x in L.KINDS}
    n = issue_of(item)
    if n:
        try:
            out |= {x for x in r.issue(n)["labels"] if x in L.KINDS}
        except GitHubError:
            pass
    return out


def judge(r: Repo, item: dict, pol: dict, b: dict) -> dict:
    """One pull request's place in the queue: ``{"verdict": ready|wait|refused, "why", ...}``.
    The cheap facts first, so a refused one costs few reads."""
    m = pol["merge"]
    n = item["number"]

    def no(why):
        return {"verdict": "refused", "why": why}

    if item.get("draft"):
        return no("a draft")
    for lab in ("hold", "needs-you"):
        if lab in item["labels"]:
            return no(f"labelled `{lab}`")
    kinds = kinds_of(r, item)
    if not kinds:
        return no("it has no kind label")
    never = sorted(kinds & set(NEVER_KINDS))
    if never:
        return no(f"a `{never[0]}`: repo-factory never merges one")
    off = sorted(k for k in kinds if k not in (m.get("kinds") or []))
    if off:
        return no(
            f"a `{off[0]}` fix, and `merge.kinds` is {', '.join(m.get('kinds') or []) or 'empty'}"
        )
    pull = r.pull(n)
    if not pull["head_ref"].startswith("repo-factory/") or pull["head_repo"] != r.full:
        return no("not on a repo-factory/ branch of this repository")
    if not known_head(r, pull, dict(b)):
        return no("someone else pushed commits to it")
    from ..v5.exam import is_test_path

    files = r.pull_changes(n)
    lines = sum(c for f, c in files.items() if not is_test_path(f))
    if lines > int(m["max_lines"]):
        return no(
            f"{lines} changed lines outside its tests, over `merge.max_lines: {m['max_lines']}`"
        )
    for f in files:
        why = never_path(f)
        if why:
            return no(f"{why}: auto-merge never takes that")
    who = changes_requested(r.reviews(n))
    if who:
        return no(f"changes requested by {', '.join(who)}")
    base = {"sha": pull["head"], "lines": lines, "kind": sorted(kinds)[0], "title": pull["title"]}
    # the part after ";" is replaced by what the poll's merge step says to do (watch.merge_step)
    if r.behind(pull["head"]):
        return {**base, "verdict": "wait", "why": "behind `main`; update it", "step": "update"}
    if pull["mergeable"] is None:
        return {
            **base,
            "verdict": "wait",
            "why": "GitHub is still checking that it merges",
            "step": "update",
        }
    if not pull["mergeable"] or pull["mergeable_state"] == "dirty":
        return {
            **base,
            "verdict": "wait",
            "why": "it conflicts with `main`; update it",
            "step": "update",
        }
    ci = checks(r, pull["head"])
    if ci["state"] == "none":
        return no("no CI ran on it: a repo without checks never auto-merges")
    if ci["state"] == "red":
        return no("CI is not green: " + ", ".join(ci["bad"][:3]))
    if ci["state"] == "pending":
        return {**base, "verdict": "wait", "why": "waiting for CI", "step": "ci"}
    return {**base, "verdict": "ready", "why": f"every check green on `{short(pull['head'])}`"}

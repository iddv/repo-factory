"""The owner's switch, "Merge repo-factory's fixes when CI is green" (registry
``maintain_merge_policy``): one small pull request on a product's ``.github/repo-factory.yml``.

The policy lives in the repository (MAINTAIN.md §4), so the switch never flips anything in repo-factory:
it opens a pull request that changes ``merge.factory_patch`` (``ci-green`` or ``never``) and
``merge.kinds``, on the branch ``repo-factory/policy-merge``, and the owner merges it once. repo-factory never
merges it itself: auto-merge never takes a change under ``.github/``. Only those lines change;
the file's comments and every other setting stay as the owner left them, and the result must
parse back to exactly what was asked, or nothing is pushed.

It runs in the background, one at a time per repo (``repo-factory maintain merge-policy REPO``,
detached); its state is a small file the page reads, so the press answers at once."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from .. import paths
from . import policy as P

BRANCH = "repo-factory/policy-merge"
FILE = ".github/repo-factory.yml"
KIND_NOTE = "# of bug | docs | security | dependency (feature: never)"


class PolicyPRError(RuntimeError):
    pass


def kinds_of(value) -> list[str]:
    """The kinds asked for, checked: a feature never, and only the four repo-factory may merge."""
    ks = [str(x).strip() for x in (value or []) if str(x).strip()]
    if "feature" in ks:
        raise PolicyPRError("repo-factory never merges a feature; leave it out")
    bad = [k for k in ks if k not in P.MERGE_KINDS]
    if bad:
        raise PolicyPRError(f"{', '.join(bad)}: the kinds are {', '.join(P.MERGE_KINDS)}")
    return [k for k in P.MERGE_KINDS if k in ks]


def edit(text: str, on: bool, kinds: list[str]) -> str:
    """``text`` with ``merge.factory_patch`` and ``merge.kinds`` set; every other line as it was."""
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if re.match(r"merge:\s*(#.*)?$", ln)), None)
    value = "ci-green" if on else "never"
    flow = "[" + ", ".join(kinds) + "]"
    if start is None:
        tail = "" if text.endswith("\n") else "\n"
        return (
            f"{text}{tail}\nmerge:\n  factory_patch: {value}\n  kinds: {flow}         {KIND_NOTE}\n"
        )
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i] and not lines[i][0].isspace() and not lines[i].startswith("#")
        ),
        len(lines),
    )

    def put(key: str, val: str, after: str | None) -> None:
        nonlocal end
        for i in range(start + 1, end):
            m = re.match(rf"(\s+){key}:(\s*)([^#]*?)(\s*)(#.*)?$", lines[i])
            if m:
                pad = m.group(4) if m.group(5) else ""
                lines[i] = f"{m.group(1)}{key}:{m.group(2) or ' '}{val}{pad}{m.group(5) or ''}"
                return
        at = start + 1
        for i in range(start + 1, end):  # after ``after``'s line and its comments
            if after and re.match(rf"\s+{after}:", lines[i]):
                at = i + 1
                while at < end and re.match(r"\s+#", lines[at]):
                    at += 1
        lines.insert(
            at, f"  {key}: {val}         {KIND_NOTE}" if key == "kinds" else f"  {key}: {val}"
        )
        end += 1

    put("factory_patch", value, None)
    put("kinds", flow, "factory_patch")
    return "\n".join(lines)


def proposed(text: str | None, on: bool, kinds: list[str]) -> str:
    """The new file, or why there is none to propose."""
    if text is None:
        raise PolicyPRError(
            f"the repository has no {FILE}: open the backfill PR first, which writes it"
        )
    try:
        before = P.parse(text)
    except P.PolicyError as e:
        raise PolicyPRError(f"{FILE} does not parse now ({e}); fix it by hand first") from None
    new = edit(text, on, kinds)
    after = P.parse(new)
    want = "ci-green" if on else "never"
    if after["merge"]["factory_patch"] != want or after["merge"]["kinds"] != kinds:
        raise PolicyPRError(f"repo-factory could not change {FILE} safely; edit merge: by hand")
    rest = {k: v for k, v in after.items() if k != "merge"}
    if (
        rest != {k: v for k, v in before.items() if k != "merge"}
        or after["merge"]["max_lines"] != before["merge"]["max_lines"]
        or after["merge"]["dependabot"] != before["merge"]["dependabot"]
    ):
        raise PolicyPRError(
            f"repo-factory could not change {FILE} without touching other settings; "
            "edit merge: by hand"
        )
    return new


def title(on: bool, kinds: list[str]) -> str:
    return (
        "Policy: repo-factory merges its own fixes when CI is green (" + ", ".join(kinds) + ")"
        if on
        else "Policy: repo-factory no longer merges its own fixes"
    )


def body(repo: str, on: bool, kinds: list[str], max_lines: int) -> str:
    from .revise import SIGNATURE

    if on:
        what = (
            f"With this merged, repo-factory merges its own fix pull requests on `{repo}` "
            f"(`merge.factory_patch: ci-green`) when all of these hold: its kind is one of "
            f"{', '.join(f'`{k}`' for k in kinds)} (`merge.kinds`); every check on its head "
            "commit passed, and there is at least one (a repo without CI never auto-merges); "
            f"it is up to date with `main`; its diff is at most {max_lines} changed lines "
            "(`merge.max_lines`). One merge per poll, oldest first, squashed, with a comment "
            "naming this setting; the release waits until the batch is merged.\n\n"
            "**repo-factory never merges, whatever this file says:** a `feature`; a pull request "
            "touching `.github/` (this one included), a LICENSE, NOTICE or COPYING file, or a "
            "lockfile; one labelled `hold` or `needs-you`; one with changes requested; one "
            "you pushed commits to; anyone else's pull request. Label a fix `hold` to keep "
            "it for yourself."
        )
    else:
        what = (
            f"With this merged, repo-factory stops merging its own fix pull requests on `{repo}` "
            "(`merge.factory_patch: never`): you review and merge each one. repo-factory still keeps "
            "them up to date with `main`."
        )
    return (
        f"{what}\n\nOnly `merge.factory_patch` and `merge.kinds` change; every other line of "
        f"`{FILE}` stays as it is. repo-factory never merges this pull request: you do.\n\n"
        f"_{SIGNATURE}_\n"
    )


def run(loop, repo: str, on: bool, kinds: list[str], gh=None) -> dict:
    """The pull request itself (the detached process, or the command line's foreground)."""
    from .github import GitHubError, Repo
    from .poll import gh_for

    kinds = kinds_of(kinds)
    if on and not kinds:
        raise PolicyPRError("choose at least one kind for repo-factory to merge")
    r = Repo(gh or gh_for(loop, repo), repo)
    text = r.file(FILE, "main")
    new = proposed(text, on, kinds)
    if new == text:
        return {
            "repo": repo,
            "changed": False,
            "pr": None,
            "say": f"{FILE} on main says so already; no pull request needed",
        }
    main = (r.get(f"repos/{repo}/commits/main") or {}).get("sha")
    if not main:
        raise PolicyPRError("could not read main's commit")
    r.set_branch(BRANCH, main)
    r.put_file(FILE, new, BRANCH, title(on, kinds), sha=r.blob_sha(FILE, BRANCH))
    max_lines = P.parse(new)["merge"]["max_lines"]
    owner = repo.split("/")[0]
    try:
        open_ = r.get(f"repos/{repo}/pulls?state=open&head={owner}:{BRANCH}") or []
    except GitHubError:
        open_ = []
    if open_:
        n = open_[0]["number"]
        r.edit_pr(n, title=title(on, kinds), body=body(repo, on, kinds, max_lines))
        pr = {"number": n, "url": open_[0].get("html_url")}
    else:
        pr = r.create_pr(title(on, kinds), BRANCH, body(repo, on, kinds, max_lines))
    try:
        r.set_labels(pr["number"], ["repo-factory"], have=[])
    except GitHubError:
        pass  # a repo without the label scheme: no label
    return {"repo": repo, "changed": True, "pr": pr, "on": on, "kinds": kinds}


# -- the press: detached, with a state file the page reads ---------------------------------------
def state_path(home, repo: str) -> Path:
    return paths.Layout(home).maintain / "policy" / (re.sub(r"[^\w.-]+", "__", repo) + ".json")


def read_state(home, repo: str) -> dict | None:
    try:
        d = json.loads(state_path(home, repo).read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("repo") == repo else None


def write_state(home, repo: str, **fields) -> dict:
    p = state_path(home, repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = {**(read_state(home, repo) or {}), **fields, "repo": repo}
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(p)
    return d


def job_state(home, repo: str) -> dict | None:
    """``{"state": running|done|failed, "pr", "on", "kinds", "why", "since", "finished"}`` or
    None. A running one whose process is gone without a word is ``failed``."""
    from .. import proc

    d = read_state(home, repo)
    if d and d.get("state") == "running" and not proc.running(d.get("pid"), d.get("start")):
        d = {**d, "state": "failed", "why": "it stopped without finishing"}
    return d


def command(cfg, repo: str, on: bool, kinds: list[str]) -> list[str]:
    m = cfg.get("maintain") or {}
    base = list(
        m.get("policy_command")
        or [sys.executable, "-m", "repo_factory.cli", "maintain", "merge-policy"]
    )
    return base + [
        repo,
        "--on" if on else "--off",
        "--kinds",
        ",".join(kinds),
        "--home",
        str(cfg.home),
    ]


def start(loop, repo: str, on: bool, kinds, by: str = "ui", now: float | None = None) -> dict:
    """Start the policy pull request in the background; returns at once."""
    from .. import proc
    from ..models import build_env
    from .poll import targets

    kinds = kinds_of(kinds)
    if on and not kinds:
        raise PolicyPRError("choose at least one kind for repo-factory to merge")
    if not any(t["repo"] == repo and t.get("kind") == "product" for t in targets(loop)):
        raise PolicyPRError(
            f"{repo or 'that'} is not a product repo-factory published: an added "
            "repository's policy is its profile, and repo-factory never merges there"
        )
    st = job_state(loop.cfg.home, repo)
    if st and st.get("state") == "running":
        raise PolicyPRError(f"the policy pull request of {repo} is being opened already")
    cmd = command(loop.cfg, repo, on, kinds)
    log = state_path(loop.cfg.home, repo).with_suffix(".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    p = proc.spawn_detached(
        cmd, cwd=log.parent, log=log, env=build_env(loop.cfg), banner=" ".join(cmd)
    )
    write_state(
        loop.cfg.home,
        repo,
        state="running",
        pid=p.pid,
        start=proc.start_time(p.pid),
        since=now or time.time(),
        finished=None,
        by=by,
        why=None,
        pr=None,
        on=on,
        kinds=kinds,
    )
    return {"started": True, "repo": repo, "pid": p.pid}

"""Backfill (MAINTAIN.md §10): give an existing product everything maintenance needs, in one
pull request the owner merges.

``backfill(gh, repo, work)``:

1. clones ``main`` into ``work`` (or reuses the clone), branch ``repo-factory/maintenance`` (an
   existing local or pushed branch is kept, so a hand-made commit on it survives a re-run);
2. writes the files (``write_files``) and removes the old ``test.yml``; one commit;
3. checks: the drift doctor (``agentsmd.check``) and, unless ``run_suites`` is off,
   ``check_agents_md.py`` on the branch, which runs AGENTS.md's commands and both suites.
   A failure stops here: nothing is pushed and no PR is opened;
4. applies the labels (``labels.apply``, never pruning), sets ``delete_branch_on_merge`` and
   enables Dependabot alerts; what the token cannot change is listed in the PR;
5. pushes the branch and opens the PR (or refreshes its body when one is open), labelled
   ``repo-factory``, with the files, the policy, the checks and the morning checklist.

Every step is idempotent: a second run changes nothing that is already right."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .. import gitcmd, paths
from . import agentsmd, labels, policy
from . import render as _render

BRANCH = "repo-factory/maintenance"
TITLE = "Maintenance: labels, policy, AGENTS.md and CI"
OLD_CI = (".github/workflows/test.yml", ".github/workflows/test.yaml")

# repo path -> template (rendered with the tree's values) or a builder name
FILES = {
    "AGENTS.md": "AGENTS.md.tmpl",
    "CLAUDE.md": "CLAUDE.md",
    ".github/repo-factory.yml": ":policy",
    ".github/labels.yml": ":labels",
    ".github/workflows/ci.yml": "ci.yml",
    ".github/workflows/labels.yml": "labels.yml",
    ".github/scripts/check_agents_md.py": "check_agents_md.py",
    ".github/scripts/sync_labels.py": "sync_labels.py",
    ".github/dependabot.yml": "dependabot.yml",
    ".github/ISSUE_TEMPLATE/bug.yml": "bug.yml",
    ".github/ISSUE_TEMPLATE/feature.yml": "feature.yml",
    ".github/ISSUE_TEMPLATE/config.yml": "config.yml",
    ".github/pull_request_template.md": "pull_request_template.md",
}
# written only when the repo has none, or has the one an earlier backfill wrote (recognised by
# the marker); anything else is the owner's and is left alone
IF_MISSING = {
    ".gitignore": ("gitignore", "never belong in the repository (AGENTS.md"),
    "CHANGELOG.md": ("CHANGELOG.md.tmpl", "Maintenance files: issue labels"),
}

# The backfill's policy (§10, D2–D7, D14): maintained, fixes only on `go`, repo-factory never merges.
POLICY = {"status": "maintained", "revise": {"trigger": "go"}}


class BackfillError(RuntimeError):
    pass


def git(*args: str, cwd: Path, check: bool = True, env: dict | None = None) -> str:
    p = gitcmd.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise BackfillError(
            f"git {' '.join(args[:2])} failed: {(p.stderr or p.stdout).strip()[:300]}"
        )
    return p.stdout


def contents(name: str, values: dict, policy_values: dict | None = None) -> str:
    if name == ":policy":
        return policy.render(policy_values or POLICY)
    if name == ":labels":
        return labels.labels_yaml()
    return _render(name, values)


POLICY_FILES = (".github/repo-factory.yml", ".github/labels.yml")


def write_files(
    root: Path, values: dict, policy_values: dict | None = None, only: tuple | None = None
) -> dict:
    """Write every maintenance file into ``root``. ``{"written": [...], "kept": [...],
    "removed": [...]}`` (paths relative to the root). ``only``: just these paths (a publish
    with ``publish.maintenance_files: policy`` writes the machine-read policy and the label
    scheme, ``POLICY_FILES``; the rest is the product's, EX-PLAN §2)."""
    root = Path(root)
    res: dict[str, list[str]] = {"written": [], "kept": [], "removed": []}
    if only is not None:
        for rel in only:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(contents(FILES[rel], values, policy_values))
            res["written"].append(rel)
        return res
    todo = dict(FILES)
    for rel, (name, marker) in IF_MISSING.items():
        p = root / rel
        if p.exists() and marker not in p.read_text(errors="replace"):
            res["kept"].append(rel)
        else:
            todo[rel] = name
    for rel, name in todo.items():
        p = root / rel
        text = contents(name, values, policy_values)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists() or p.read_text() != text:
            p.write_text(text)
        if rel.endswith(".py"):
            p.chmod(0o755)
        res["written"].append(rel)
    for rel in OLD_CI:
        if (root / rel).exists():
            (root / rel).unlink()
            res["removed"].append(rel)
    # READINESS.md's CI row named the file just removed (AU3 finding 2: 5 doc_paths findings)
    ready = root / "READINESS.md"
    if res["removed"] and ready.is_file():
        text = new = ready.read_text()
        for rel in res["removed"]:
            new = new.replace(rel, ".github/workflows/ci.yml")
        if new != text:
            ready.write_text(new)
            res["written"].append("READINESS.md")
    return res


def checkout(gh, repo: str, work: Path) -> None:
    work = Path(work)
    if not (work / ".git").exists():
        work.parent.mkdir(parents=True, exist_ok=True)
        gh("repo", "clone", repo, str(work), "--", "-q")
    else:
        git("fetch", "-q", "origin", cwd=work)
    base = (
        git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=work, check=False).strip()
        or "origin/main"
    )
    local = git("rev-parse", "--verify", "-q", BRANCH, cwd=work, check=False).strip()
    remote = git("ls-remote", "--heads", "origin", BRANCH, cwd=work, check=False).strip()
    if local:
        git("checkout", "-q", BRANCH, cwd=work)
    elif remote:
        git("fetch", "-q", "origin", BRANCH, cwd=work)
        git("checkout", "-q", "-B", BRANCH, f"origin/{BRANCH}", cwd=work)
    else:
        git("checkout", "-q", "-B", BRANCH, base, cwd=work)


class VenvError(BackfillError):
    pass


def throwaway_venv(parent: Path) -> Path:
    """A fresh virtual environment with pip, made for one check (as CI's runner has): the
    fence's ``python -m pip install …`` must never reach the user's own interpreter, and a
    system Python without pip must not fail it (Beliefline, 2026-10-13)."""
    from ..harness.isolation import base_python

    d = Path(tempfile.mkdtemp(prefix="check-venv-", dir=parent))
    uv = shutil.which("uv")
    cmd = (
        [uv, "venv", "-q", "--seed", "--python", base_python(), str(d)]
        if uv
        else [base_python(), "-m", "venv", "--clear", str(d)]
    )
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        shutil.rmtree(d, ignore_errors=True)
        raise VenvError(f"could not make a virtual environment for the check: {e}") from None
    if p.returncode != 0 or not (d / "bin" / "python").exists():
        shutil.rmtree(d, ignore_errors=True)
        raise VenvError(
            "could not make a virtual environment for the check: "
            + (p.stderr or p.stdout).strip()[-300:]
        )
    return d


def run_check(work: Path, python: str | None = None, timeout: int = 1800) -> dict:
    """``check_agents_md.py`` on the branch, with the interpreter's directory first on PATH so
    the fence's ``python`` is that interpreter. By default a throwaway venv with pip, made
    beside the clone and removed afterwards; ``python`` (``--python``) overrides it."""
    venv = None
    if not python:
        try:
            venv = throwaway_venv(Path(work).parent)
        except VenvError as e:
            return {"ok": False, "summary": str(e), "output": ""}
        python = str(venv / "bin" / "python")
    # a throwaway HOME too: the product's data directory (``~/.<product>``) and anything its
    # quick start leaves behind stay out of the user's own
    home = Path(tempfile.mkdtemp(prefix="check-home-", dir=Path(work).parent))
    env = {
        k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV")
    }
    env["PATH"] = str(Path(python).parent) + os.pathsep + env.get("PATH", "")
    env["HOME"] = str(home)
    if venv is not None:
        env["VIRTUAL_ENV"] = str(venv)
    try:
        p = subprocess.run(
            [python, ".github/scripts/check_agents_md.py"],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "summary": f"check_agents_md.py took longer than {timeout} s",
            "output": "",
        }
    finally:
        for d in (venv, home):
            if d is not None:
                shutil.rmtree(d, ignore_errors=True)
    out = (p.stdout or "") + (p.stderr or "")
    last = [ln for ln in out.splitlines() if ln.strip()][-1:] or [""]
    return {"ok": p.returncode == 0, "summary": last[0], "output": out[-6000:]}


def repo_settings(gh, repo: str) -> dict:
    """``delete_branch_on_merge`` on and Dependabot alerts enabled; each step says whether it
    took, so the PR can list what the owner must do by hand."""
    res = {}
    code, out, err = gh._run(
        [
            "api",
            "-X",
            "PATCH",
            f"repos/{repo}",
            "-F",
            "delete_branch_on_merge=true",
            "-q",
            ".delete_branch_on_merge",
        ],
        None,
    )
    res["delete_branch_on_merge"] = (code == 0 and out.strip() == "true") or (err or out).strip()[
        :200
    ]
    code, out, err = gh._run(["api", "-X", "PUT", f"repos/{repo}/vulnerability-alerts"], None)
    if code == 0:
        code, out, err = gh._run(["api", f"repos/{repo}/vulnerability-alerts"], None)
    res["vulnerability_alerts"] = code == 0 or (err or out).strip()[:200]
    return res


def open_pr(gh, repo: str, body: str, work: Path) -> dict:
    found = json.loads(
        gh(
            "pr",
            "list",
            "--repo",
            repo,
            "--head",
            BRANCH,
            "--state",
            "open",
            "--json",
            "number,url",
        )
        or "[]"
    )
    body_file = Path(work).parent / f".{Path(work).name}-pr-body.md"
    body_file.write_text(body)
    try:
        if found:
            # The REST call, not ``gh pr edit``: older gh releases fail it on the retired
            # classic-projects field.
            gh(
                "api",
                "-X",
                "PATCH",
                f"repos/{repo}/pulls/{found[0]['number']}",
                "-F",
                f"body=@{body_file}",
                "-q",
                ".number",
            )
            return {"number": found[0]["number"], "url": found[0]["url"], "created": False}
        url = gh(
            "pr",
            "create",
            "--repo",
            repo,
            "--base",
            "main",
            "--head",
            BRANCH,
            "--title",
            TITLE,
            "--body-file",
            str(body_file),
            "--label",
            "repo-factory",
        ).strip()
        return {
            "number": int(url.rstrip("/").rsplit("/", 1)[-1]) if url[-1:].isdigit() else None,
            "url": url,
            "created": True,
        }
    finally:
        body_file.unlink(missing_ok=True)


def pr_body(repo: str, res: dict, footer: str = "") -> str:
    p = policy.parse(policy.render(POLICY))
    lab = res.get("labels") or {}
    st = res.get("settings") or {}
    chk = res.get("check") or {}
    not_set = [k for k, v in st.items() if v is not True]
    files = res["files"]
    other = res.get("other_files") or []
    L = [
        "This pull request makes the repository maintainable through GitHub issues, by the "
        "owner, by repo-factory, or by any coding agent that reads `AGENTS.md`. It changes no product "
        "code" + (" except the files listed under *Other changes*" if other else "") + ".",
        "",
        "**Files**",
        "",
    ]
    L += [f"- `{f}`" for f in files["written"]]
    L += [f"- `{f}` removed (replaced by `ci.yml`)" for f in files["removed"]]
    L += [f"- `{f}` left as it is (the repo already had one)" for f in files["kept"]]
    L += [
        "",
        "**Policy** (`.github/repo-factory.yml`)",
        "",
        f"- status `{p['status']}`; fixes start only on the `go` label "
        f"(`revise.trigger: {p['revise']['trigger']}`, `auto_go: {p['revise']['auto_go']}`)",
        f"- repo-factory never merges its own PRs (`merge.factory_patch: {p['merge']['factory_patch']}`) "
        f"and never merges Dependabot PRs (`merge.dependabot: {p['merge']['dependabot']}`)",
        f"- a release follows each merge (`release.on_merge: {p['release']['on_merge']}`; a "
        "`feature` makes it a minor release)",
        f"- claims lease for {p['revise']['lease_hours']} hours; duplicates are labelled, "
        "never closed by repo-factory; no spend cap (`revise.max_usd: null`)",
        "",
        "**Labels** (applied through the API, from `.github/labels.yml`)",
        "",
        f"- {len(lab.get('created', []))} created, {len(lab.get('updated', []))} updated, "
        f"{len(lab.get('unchanged', []))} already right; nothing deleted",
    ]
    if lab.get("extra"):
        L.append("- other labels on the repo: " + ", ".join(f"`{x}`" for x in lab["extra"]))
    L += ["", "**Repository settings**", ""]
    for k, v in st.items():
        L.append(f"- `{k}`: " + ("set" if v is True else f"**not set** ({v})"))
    L += [
        "",
        "**Checks run before this PR**",
        "",
        "- `AGENTS.md` drift doctor: "
        + ("current" if not res.get("doctor") else "; ".join(res["doctor"])),
        "- `.github/scripts/check_agents_md.py` (runs every command in AGENTS.md › *Setup, "
        "run, test*, both suites, the architecture paths and the open-item marks): "
        + (chk.get("summary") or "not run"),
    ]
    public = res.get("private") is False
    L += [
        "",
        "**CI.** `ci.yml` runs on GitHub-hosted runners only (no self-hosted runner, "
        "`permissions: contents: read`, every action pinned to a commit). "
        + (
            "The repository is public, so its runs use no Actions minutes."
            if public
            else "The repository is private, so it uses the account's Actions minutes; if Actions "
            "billing is blocked, CI does not run and the local checks above are the "
            "verification."
        ),
    ]
    if other:
        L += ["", "**Other changes** (commits on this branch besides the maintenance files)", ""]
        L += [f"- `{f}`" for f in other]
    if res.get("notes"):
        L += ["", "**Notes**", ""] + [f"- {n}" for n in res["notes"]]
    L += [
        "",
        "**Morning checklist for the owner**",
        "",
        "1. Review and squash-merge this PR.",
        "2. Optional (D13): prune GitHub's default labels that ours replace:",
    ]
    cmds = labels.prune_commands(repo, lab.get("extra") or [])
    L += (
        (["", "   ```sh"] + [f"   {c}" for c in cmds] + ["   ```", ""])
        if cmds
        else ["   nothing to prune."]
    )
    L += [
        "   `help wanted` and `good first issue` are worth keeping"
        + (" for outside contributors." if public else " for when the repo is public.")
    ]
    if not public:
        L.append("3. Fix Actions billing, or make the repo public (D5), so CI runs.")
    L.append(
        f"{3 if public else 4}. Choose the maintenance identity (D1): a fine-grained token "
        "or a bot account; until then repo-factory uses the owner's `gh` login."
    )
    if not_set:
        L.append(
            f"{4 if public else 5}. Set by hand what the token could not: "
            + ", ".join(f"`{k}`" for k in not_set)
            + " (Settings › General › *Automatically delete head branches*; Settings › "
            "Code security › *Dependabot alerts*)."
        )
    L += [
        "",
        "To try it: label an issue `go`; repo-factory (or `claude` in a checkout) follows "
        "AGENTS.md › *Maintenance protocol*.",
    ]
    if footer:
        L += ["", footer]
    return "\n".join(L) + "\n"


STALE = re.compile(r"^FAIL  (tests/\S+\.py) now passes but still carries its open-item mark", re.M)


def stale_marks(chk: dict | None) -> list[str]:
    """The open items ``check_agents_md.py`` found passing with their mark still on, when
    those are its only problems (else ``[]``: taking marks off would not make it pass)."""
    out = (chk or {}).get("output") or ""
    fails = [ln for ln in out.splitlines() if ln.startswith("FAIL  ")]
    files = STALE.findall(out)
    return sorted(set(files)) if files and len(files) == len(fails) else []


def backfill(
    gh,
    repo: str,
    work: Path,
    *,
    describe=None,
    python: str | None = None,
    run_suites: bool = True,
    apply_labels: bool = True,
    settings: bool = True,
    pr: bool = True,
    trailer: str = "",
    footer: str = "",
    notes: list[str] | None = None,
    log=print,
) -> dict:
    work = Path(work)
    res: dict = {"repo": repo, "work": str(work), "branch": BRANCH, "notes": list(notes or [])}
    log(f"{repo}: checkout {BRANCH}")
    checkout(gh, repo, work)
    values = agentsmd.values_from_tree(work, repo, describe)
    res["values"] = {k: v for k, v in values.items() if isinstance(v, (str, int))}
    res["files"] = write_files(work, values)
    git("add", "-A", cwd=work)
    if gitcmd.run(["diff", "--cached", "--quiet"], cwd=work).returncode != 0:
        msg = (
            "Maintenance files: labels, policy, AGENTS.md and CI\n\n"
            "AGENTS.md (with CLAUDE.md importing it) documents the commands, the architecture "
            "and the maintenance protocol; .github/repo-factory.yml is the policy; labels.yml the "
            "label scheme; ci.yml runs the suites and AGENTS.md's commands on GitHub-hosted "
            "runners and replaces test.yml.\n" + (f"\n{trailer}\n" if trailer else "")
        )
        from . import identity

        git(*identity.git_author(gh), "commit", "-q", "-m", msg, cwd=work)
        res["committed"] = True
    res["doctor"] = agentsmd.check(work)
    if run_suites:
        log(f"{repo}: running check_agents_md.py (both suites)")
        res["check"] = run_check(work, python)
        stale = stale_marks(res["check"])
        if stale and not res["doctor"]:
            # an open item that passes now keeps CI red until its mark comes off: the check
            # says so, and taking it off is the whole change (AGENTS.md › acceptance suite)
            from . import identity
            from .revise import unmark

            for f in stale:
                unmark(work, f)
            git("add", "-A", cwd=work)
            git(
                *identity.git_author(gh),
                "commit",
                "-q",
                "-m",
                "Open items that pass now: their marks come off\n\n"
                + "\n".join(stale)
                + "\n"
                + (f"\n{trailer}\n" if trailer else ""),
                cwd=work,
            )
            res["unmarked"] = stale
            res["notes"].append(
                "these known open items pass now, so their marks and README "
                "lines are removed: " + ", ".join(f"`{f}`" for f in stale)
            )
            log(f"{repo}: {len(stale)} open item(s) pass now; marks removed, checking again")
            res["check"] = run_check(work, python)
    ours = {*FILES, *IF_MISSING, *OLD_CI}
    base = (
        git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=work, check=False).strip()
        or "origin/main"
    )
    res["other_files"] = [
        f for f in git("diff", "--name-only", f"{base}...HEAD", cwd=work).split() if f not in ours
    ]
    ok = not res["doctor"] and (res.get("check") or {}).get("ok", True)
    res["ok"] = ok
    if not ok:
        log(f"{repo}: checks failed; nothing pushed")
        return res
    if apply_labels:
        res["labels"] = labels.apply(gh, repo)
    if settings:
        res["settings"] = repo_settings(gh, repo)
    if pr:
        # what the PR says about CI minutes depends on it (None: GitHub did not say)
        code, out, _ = gh._run(
            ["repo", "view", repo, "--json", "isPrivate", "-q", ".isPrivate"], None
        )
        res["private"] = {"true": True, "false": False}.get(out.strip()) if code == 0 else None
        auth = gh.git_auth()
        git(*auth, "push", "-q", "-u", "origin", BRANCH, cwd=work, env=gh.env() if auth else None)
        res["pr"] = open_pr(gh, repo, pr_body(repo, res, footer), work)
        log(f"{repo}: {res['pr']['url']}")
    return res


# -- the press (registry ``maintain_backfill``): the same command, detached -----------------------
# A maintained product whose AGENTS.md is missing or does not run has a row that says so
# (advice: it holds no fix; revise.py, ``agents_state``); this PR is its remedy. The page and
# the command line start it; it runs in the background and its state is a small file beside its clone.
def state_path(home, repo: str) -> Path:
    return paths.Layout(home).maintain / "backfill" / f"{repo.split('/')[-1]}.state.json"


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
    """``{"state": running|done|failed, "since", "finished", "pr", "why", "by"}`` or None (never
    run). A running job whose process is gone without a word is ``failed``."""
    from .. import proc

    d = read_state(home, repo)
    if d and d.get("state") == "running" and not proc.running(d.get("pid"), d.get("start")):
        d = {**d, "state": "failed", "why": "the backfill stopped without finishing"}
    return d


def command(cfg, repo: str) -> list[str]:
    """``repo-factory maintain backfill REPO``, with the ``gh`` publishing uses (the owner's login, as a
    published repo's own writes)."""
    m = cfg.get("maintain") or {}
    gh = cfg["publish"].get("gh_command")
    base = list(
        m.get("backfill_command")
        or [
            sys.executable,
            "-m",
            "repo_factory.cli",
            "maintain",
            *(["--gh", gh if isinstance(gh, str) else " ".join(gh)] if gh else []),
            "backfill",
        ]
    )
    return base + [repo, "--home", str(cfg.home)]


def start(cfg, repo: str, by: str = "ui", now: float | None = None) -> dict:
    """Start the backfill of ``repo`` in the background; returns at once."""
    import time

    from .. import proc
    from ..models import build_env

    st = job_state(cfg.home, repo)
    if st and st.get("state") == "running":
        raise BackfillError(
            f"the backfill of {repo} is running (since "
            f"{time.strftime('%H:%M', time.localtime(st.get('since') or 0))})"
        )
    cmd = command(cfg, repo)
    log = state_path(cfg.home, repo).with_suffix(".log")
    p = proc.spawn_detached(
        cmd,
        cwd=state_path(cfg.home, repo).parent,
        log=log,
        env=build_env(cfg),
        banner=" ".join(cmd),
    )
    write_state(
        cfg.home,
        repo,
        state="running",
        pid=p.pid,
        start=proc.start_time(p.pid),
        since=now or time.time(),
        finished=None,
        by=by,
        pr=None,
        why=None,
        log=str(log),
    )
    return {"started": True, "pid": p.pid, "repo": repo}

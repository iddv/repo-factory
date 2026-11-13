"""The cleanup pull request for a product published before repo-factory's exam moved out (EX-PLAN §8.1).

``cleanup(gh, repo, work)`` clones ``main`` into ``work`` on the branch ``repo-factory/ex-cleanup``
and, in one commit:

- deletes repo-factory's own scaffolding: ``.github/scripts/check_agents_md.py`` and the ``agents-md``
  CI job that runs it;
- deletes the documents repo-factory wrote: ``READINESS.md`` and ``ASSUMPTIONS.md``; ``SECURITY.md``
  keeps its reporting policy (GitHub reads it) and the findings still open, as known issues;
  the review moves to the checks asset;
- rewrites ``NOTICE``'s paragraph about the checks so it names the checks asset instead of
  the deleted documents;
- rewrites ``AGENTS.md`` from the repository's README and code (one no-tools model call;
  ``model=None`` keeps it and only drops the lines about what this PR removes), listing
  ``tests/acceptance/`` as the product's own suite;
- **keeps ``tests/acceptance/``**: those tests pass, users may rely on them, and they are the
  product's from now on (its CI step stays, renamed). ``drop_suite`` removes the suite, its CI
  step and its README section for a repo the owner chooses.

Then it pushes the branch and opens the pull request, labelled ``repo-factory``. **The owner merges
it.** The checks asset (``exam.legacy_evidence`` from the den's last delivered round) is built
locally; attaching it to the latest release changes a published release, so the PR's body
gives the command and the owner runs it (``--attach``). ``dry_run``: no push, no PR, the diff
printed. Nothing here changes a repository's visibility."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .. import gitcmd

BRANCH = "repo-factory/ex-cleanup"
TITLE = "Move repo-factory's build checks out of the repository"
AGENTS_PROMPT = """Write AGENTS.md for the repository below: the instruction file for whoever
maintains it next (a person or a coding agent). From its README and its code only: what it is
(two or three lines); how to set it up, run it and test it (the exact commands, from the README);
its architecture (the main files and folders and what each holds, only paths that exist); its
conventions. Its tests: `tests/` and `tests/acceptance/` are the product's own suites; say how to
run each. Keep one line saying it is maintained through GitHub issues as `.github/repo-factory.yml`
describes, and write no maintenance protocol section: the current one is kept as it is. No
notes about how the software was built. Reply with the file's text only, starting with
"# AGENTS.md".

=== README.md ===
{readme}

=== Files ===
{files}

=== The current AGENTS.md (its commands and paths may be stale) ===
{agents}
"""


class CleanupError(RuntimeError):
    pass


def git(*args: str, cwd: Path, check: bool = True, env: dict | None = None) -> str:
    p = gitcmd.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise CleanupError(
            f"git {' '.join(args[:2])} failed: {(p.stderr or p.stdout).strip()[:300]}"
        )
    return p.stdout


# -- the edits ---------------------------------------------------------------------------------
def ci_without_factory(text: str, drop_suite: bool) -> str:
    """``ci.yml`` without the ``agents-md`` job; the acceptance step renamed (the suite is the
    product's now) or, with ``drop_suite``, removed."""
    lines = text.splitlines()
    out, skip = [], False
    for ln in lines:
        if re.match(r"^  agents-md:\s*$", ln):
            skip = True
            continue
        if skip and re.match(r"^  \S", ln):
            skip = False
        if not skip:
            out.append(ln)
    text = "\n".join(out).rstrip() + "\n"
    if drop_suite:
        text = re.sub(
            r"\n      - name: Acceptance suite\n        run: \|\n(?:          .*\n)+", "\n", text
        )
    else:
        text = text.replace("- name: Acceptance suite", "- name: The end-to-end tests")
    # the header comment's clause about the removed job (one line or wrapped onto the next)
    head, sep, rest = text.partition("\nname:")
    head = re.sub(
        r";(\s*\n#)? check that AGENTS\.md's commands run as written\.[ \t]*",
        lambda m: "." + (m.group(1) + " " if m.group(1) else " "),
        head,
        count=1,
    )
    head = re.sub(r"(?m)^#[ \t]*$\n?", "", head).replace("\n#  ", "\n# ")
    head = re.sub(r"(?m)[ \t]+$", "", head)
    return head.rstrip("\n") + sep + rest if sep else head


FINDING_ROW = re.compile(r"^\|\s*(S\d+)\s*\|\s*(\w+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$", re.M)


def open_findings(text: str, drop_suite: bool = False) -> list[str]:
    """The review's findings that are not fixed (the product's known issues), each with its
    detail subsection when there is one (without its test line when the suite goes)."""
    out = []
    for fid, sev, title, status in FINDING_ROW.findall(text):
        if status.lower().startswith("fixed"):
            continue
        m = re.search(rf"^### {re.escape(fid)} \(.*?$(.*?)(?=^#{{2,3}} |\Z)", text, re.M | re.S)
        detail = m.group(1).strip() if m else ""
        if drop_suite:
            detail = "\n".join(
                ln for ln in detail.splitlines() if not ln.startswith("- Test:")
            ).strip()
        out.append(f"### {fid} ({sev}): {title}" + (f"\n\n{detail}" if detail else ""))
    return out


def security_policy(text: str, drop_suite: bool = False) -> str:
    """repo-factory's review ``SECURITY.md`` reduced to the product's reporting policy and its known
    issues (EX-PLAN Q6: "how to report, known limits"): the findings still open stay."""
    m = re.search(r"^## Reporting a security issue\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    body = (
        m.group(1).strip()
        if m
        else (
            "Please report security problems privately, not in a public issue: use GitHub's "
            "private vulnerability reporting (Security, then Report a vulnerability)."
        )
    )
    known = open_findings(text, drop_suite)
    return (
        "# Security\n\n## Reporting a security issue\n\n"
        + body
        + "\n\n"
        + (
            "## Known issues\n\nOpen at the last release:\n\n" + "\n\n".join(known) + "\n\n"
            if known
            else ""
        )
        + "## What was checked\n\nThe security review that ran before each release (threat "
        "model, probes, findings) is published with that release in its checks asset.\n"
    )


NOTICE_PARA = re.compile(
    r"This software was generated by an autonomous software build.*?"
    r"(?=\n\n)",
    re.S,
)


def notice_text(text: str, asset: str) -> str:
    new = (
        "This software was generated by an autonomous software build from a short product "
        "brief. The checks that ran before each release (its own tests, an end-to-end walk "
        "of every documented flow, a security review and an operational readiness review) "
        f"are published with the release as `{asset}`; read them before you rely on it."
    )
    return NOTICE_PARA.sub(new, text, count=1)


def readme_without_suite(text: str) -> str:
    from ..v5.exam import ACC_SECTION

    return ACC_SECTION.sub("", text).rstrip() + "\n"


def agents_md(root: Path, model) -> str | None:
    """A new AGENTS.md from the README and the code (one no-tools call), or None."""
    if model is None:
        return None
    files = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
    )
    text = AGENTS_PROMPT.format(
        readme=(root / "README.md").read_text(errors="replace")[:12000]
        if (root / "README.md").is_file()
        else "(none)",
        files="\n".join(files[:300]),
        agents=(root / "AGENTS.md").read_text(errors="replace")[:8000]
        if (root / "AGENTS.md").is_file()
        else "(none)",
    )
    reply, _cost = model(text)
    reply = str(reply or "").strip()
    reply = re.sub(r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$", r"\1", reply, flags=re.S)
    i = reply.find("# AGENTS.md")
    return reply[i:].rstrip() + "\n" if i >= 0 else None


PROTOCOL = re.compile(r"^## Maintenance protocol\b.*?(?=^## |\Z)", re.M | re.S)


def protocol(text: str) -> str | None:
    """AGENTS.md's maintenance protocol (CLAUDE.md and repo-factory's fix lane point at it), with the
    reproducing test in the product's own suite and no READINESS.md."""
    m = PROTOCOL.search(text or "")
    if not m:
        return None
    s = m.group(0).rstrip() + "\n"
    s = re.sub(
        r"write\s+`tests/acceptance/test_<slug>\.py`\s+that\s+fails",
        "write a test in the product's own suite (see the tests above) that fails",
        s,
    )
    s = re.sub(
        r"`READINESS\.md`\s+or\s+`SECURITY\.md`\s+if\s+a\s+row\s+changed",
        "`SECURITY.md` if a known issue changed",
        s,
    )
    return s


def with_protocol(new: str, old: str) -> str:
    """``new`` (the model's AGENTS.md) with ``old``'s maintenance protocol carried over."""
    p = protocol(old)
    if p is None:
        return new
    new = PROTOCOL.sub("", new).rstrip()
    return new + "\n\n" + p


def agents_without_factory(text: str, drop_suite: bool) -> str:
    """The deterministic fallback: AGENTS.md without the lines about what this PR removes."""
    p = protocol(text)
    if p is not None:
        text = PROTOCOL.sub(lambda m: p + "\n", text)
    text = re.sub(r"^## Provenance\b.*?(?=^## |\Z)", "", text, flags=re.M | re.S)  # NOTICE's
    out = []
    for ln in text.splitlines():
        if re.search(r"agents-md|check_agents_md|READINESS\.md|ASSUMPTIONS\.md", ln):
            continue
        if drop_suite and "tests/acceptance" in ln:
            continue
        out.append(ln)
    return "\n".join(out).rstrip() + "\n"


# the lines repo-factory's templates wrote about the deleted documents, as the product's text now
REWORD = (
    (
        ".github/pull_request_template.md",
        "- [ ] `READINESS.md` / `SECURITY.md` rows updated if they changed",
        "- [ ] `SECURITY.md`'s known issues updated if they changed",
    ),
    (
        ".github/labels.yml",
        "README, AGENTS.md, READINESS or SECURITY text",
        "README, AGENTS.md or SECURITY text",
    ),
    (
        "README.md",
        "SECURITY.md and READINESS.md say what was checked before this release.",
        "SECURITY.md lists the known security issues; the checks that ran before each release "
        "are published with it (see NOTICE).",
    ),
)
GONE = re.compile(r"\b(READINESS\.md|ASSUMPTIONS\.md|check_agents_md\.py)\b")


def leftovers(root: Path) -> list[str]:
    """Other lines that still name a deleted file: for the owner, listed in the PR."""
    out = []
    for p in sorted(Path(root).rglob("*")):
        if (
            not p.is_file()
            or ".git" in p.parts
            or p.suffix not in (".md", ".yml", ".yaml", ".txt", "")
        ):
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith("tests/"):
            continue
        for n, ln in enumerate(p.read_text(errors="replace").splitlines(), 1):
            m = GONE.search(ln)
            if m:
                out.append(f"`{rel}` line {n} still names `{m.group(1)}`")
    return out


def apply(root: Path, *, drop_suite: bool = False, model=None, asset: str | None = None) -> list:
    """Edit the checkout ``root``; returns what changed, in words."""
    root = Path(root)
    done = []
    for rel, old, new in REWORD:
        p = root / rel
        if p.is_file() and old in p.read_text():
            p.write_text(p.read_text().replace(old, new))
            done.append(f"`{rel}`: no longer names the deleted documents")
    for rel in (".github/scripts/check_agents_md.py", "READINESS.md", "ASSUMPTIONS.md"):
        if (root / rel).exists():
            (root / rel).unlink()
            done.append(f"deleted `{rel}`")
    ci = root / ".github" / "workflows" / "ci.yml"
    if ci.is_file():
        new = ci_without_factory(ci.read_text(), drop_suite)
        if new != ci.read_text():
            ci.write_text(new)
            done.append(
                "`ci.yml`: the `agents-md` job removed; "
                + (
                    "the acceptance step removed"
                    if drop_suite
                    else "the acceptance suite's step kept as the product's end-to-end tests"
                )
            )
    sec = root / "SECURITY.md"
    if sec.is_file() and "was reviewed for security before its first release" in sec.read_text():
        old = sec.read_text()
        sec.write_text(security_policy(old, drop_suite))
        n = len(open_findings(old))
        done.append(
            "`SECURITY.md`: the reporting policy"
            + (f" and the {n} finding(s) still open" if n else "")
            + "; the review is in the "
            "checks asset"
        )
    no = root / "NOTICE"
    if no.is_file() and asset:
        new = notice_text(no.read_text(), asset)
        if new != no.read_text():
            no.write_text(new)
            done.append(f"`NOTICE`: names the checks asset, `{asset}`")
    if drop_suite:
        acc = root / "tests" / "acceptance"
        if acc.is_dir():
            shutil.rmtree(acc)
            done.append("deleted `tests/acceptance/` (--drop-suite)")
        for p in root.glob("README*"):
            if p.is_file():
                new = readme_without_suite(p.read_text())
                if new != p.read_text():
                    p.write_text(new)
                    done.append(f"`{p.name}`: its acceptance-tests section removed")
    ag = root / "AGENTS.md"
    new = agents_md(root, model) if not drop_suite or model is not None else None
    if new is not None and ag.is_file():
        new = with_protocol(new, ag.read_text())
    if new is None and ag.is_file():
        new = agents_without_factory(ag.read_text(), drop_suite)
    if new is not None and (not ag.is_file() or new != ag.read_text()):
        ag.write_text(new)
        done.append(
            "`AGENTS.md`: "
            + (
                "written from the README and the code"
                if model is not None
                else "the lines about what this PR removes taken out"
            )
        )
    return done


# -- the checks asset of a published release --------------------------------------------------------
def latest_release(gh, repo: str) -> str | None:
    out = gh(
        "release",
        "list",
        "--repo",
        repo,
        "--limit",
        "1",
        "--json",
        "tagName",
        "-q",
        ".[0].tagName",
        check=False,
    ).strip()
    return out or None


def find_task(loop, repo: str) -> Path | None:
    """The task folder of the loop idea published as ``repo``."""
    for i in loop.ledger.all(("published", "public")):
        if (i.get("publish") or {}).get("repo") == repo and i.get("task_dir"):
            return Path(i["task_dir"])
    return None


def build_asset(task: Path, name: str, version: str, dest: Path) -> Path | None:
    """``<name>-<version>-checks.tar.gz`` from the den's last delivered round
    (``exam.legacy_evidence`` for a round from before the exam moved out)."""
    from ..v5 import exam
    from ..v5.change import base_round

    den = Path(task) / ".den"
    rnd = base_round(den)
    if rnd is None:
        return None
    ev = exam.legacy_evidence(den / "v5" / f"r{rnd}", name)
    if ev is None:
        return None
    return exam.evidence_tarball(
        ev, Path(dest) / f"{name}-{version}-checks.tar.gz", f"{name}-{version}"
    )


def pr_body(
    repo: str,
    done: list,
    asset: str | None,
    tarball: Path | None,
    tag: str | None,
    drop_suite: bool,
    left: list | None = None,
) -> str:
    L = [
        "repo-factory used to put its own build checks into this repository. They now live outside "
        "the product and ship with each release as a checks asset, so this repository holds "
        "only the product's own files.",
        "",
        "**In this PR:**",
        "",
    ]
    L += [f"- {d}" for d in done] or ["- nothing to change"]
    if left:
        L += [
            "",
            "**Left for you** (they name a file this PR deletes; reword them if you like):",
            "",
        ] + [f"- {x}" for x in left]
    if not drop_suite:
        L += [
            "",
            "`tests/acceptance/` stays: those tests pass, and they are this product's own "
            "end-to-end tests from now on.",
        ]
    if asset:
        L += [
            "",
            f"**The checks asset** for {tag}, `{asset}`, is built"
            + (f" at `{tarball}`" if tarball else "")
            + ". Attaching it changes the "
            "published release, so it is left to you:",
            "",
            f"    gh release upload {tag} {tarball or asset} --repo {repo}",
        ]
    L += ["", "Nothing merges until you merge it; the repository's visibility is unchanged."]
    return "\n".join(L) + "\n"


def cleanup(
    gh,
    repo: str,
    work: Path,
    *,
    drop_suite: bool = False,
    model=None,
    task: Path | None = None,
    dry_run: bool = False,
    log=print,
) -> dict:
    work = Path(work)
    if not (work / ".git").exists():
        work.parent.mkdir(parents=True, exist_ok=True)
        gh("repo", "clone", repo, str(work), "--", "-q")
    else:
        git("fetch", "-q", "origin", cwd=work)
        # repo-factory's own clone: a dry run's edits (staged, never committed) go before a re-run
        git("reset", "-q", "--hard", cwd=work)
        git("clean", "-q", "-fd", cwd=work)
    git("checkout", "-q", "-B", BRANCH, "origin/main", cwd=work)
    name = repo.split("/")[1]
    tag = latest_release(gh, repo)
    version = (tag or "v0.1.0").lstrip("v")
    asset = f"{name}-{version}-checks.tar.gz" if tag else None
    tarball = build_asset(task, name, version, work.parent) if task and asset else None
    done = apply(work, drop_suite=drop_suite, model=model, asset=asset)
    res = {
        "repo": repo,
        "done": done,
        "asset": asset,
        "tarball": str(tarball) if tarball else None,
        "tag": tag,
        "pr": None,
        "left": leftovers(work),
    }
    if not done:
        return res
    git("add", "-A", cwd=work)
    res["diff"] = git("diff", "--cached", "--stat", cwd=work)
    if dry_run:
        res["patch"] = git("diff", "--cached", cwd=work)
        return res
    from . import identity

    git(
        *identity.git_author(gh),
        "commit",
        "-q",
        "-m",
        TITLE + "\n\n" + "\n".join(f"- {d}" for d in done),
        cwd=work,
    )
    git(
        *gh.git_auth(),
        "push",
        "-q",
        "-f",
        "-u",
        "origin",
        BRANCH,
        cwd=work,
        env=gh.env() if gh.git_auth() else None,
    )
    body = pr_body(repo, done, asset, tarball, tag, drop_suite, res["left"])
    bf = work.parent / f".{work.name}-cleanup-pr.md"
    bf.write_text(body)
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
    if found:
        res["pr"] = {**found[0], "created": False}
    else:
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
            str(bf),
        ).strip()
        res["pr"] = {"url": url, "created": True}
        num = url.rstrip("/").rsplit("/", 1)[-1]
        if num.isdigit():  # REST: gh pr edit trips on classic projects
            gh(
                "api",
                "-X",
                "POST",
                f"repos/{repo}/issues/{num}/labels",
                "-f",
                "labels[]=repo-factory",
                check=False,
            )
    log(f"{repo}: {res['pr']['url']}")
    return res

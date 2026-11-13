"""The repository's own rules, checked on every pull request (the `lint` job).

- No private name, home path, account number, attribution line or secret-shaped literal in
  any tracked file (``scripts/ci/public_words.py``).
- The workflows run on GitHub's own runners only, never on a self-hosted one; no trigger that
  hands a secret to a pull request's code; every action pinned by a commit hash; every job
  with a time limit; a read-only token.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def _load_words():
    spec = importlib.util.spec_from_file_location(
        "public_words", ROOT / "scripts" / "ci" / "public_words.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


words = _load_words()


def test_no_private_words_in_tracked_files():
    hits = words.scan_files(ROOT)
    assert hits == [], "\n".join(hits)


def test_the_scanner_finds_each_kind(monkeypatch):
    # every sample is built here at run time, so no tracked file holds one
    made_up = "zq" + "private" + "name"
    monkeypatch.setattr(
        words, "PRIVATE", words.PRIVATE | {hashlib.sha256(made_up.encode()).hexdigest()[:20]}
    )
    samples = {
        "a private name": f"see the {made_up.upper()}-kb folder",
        "a home path": "/ho" + "me/zqperson/work",
        "an account-number-shaped number": "account " + "1234" * 3,
        "an attribution line": "Co-" + "Authored-By: someone",
        "a secret-shaped literal": "token=" + "gh" + "p_" + "a1" * 15,
    }
    for kind, text in samples.items():
        assert kind in words.line_hits(text), (kind, text)
    assert words.line_hits("/home/user/tasks and /home/runner/work are generic") == []
    assert words.line_hits("sha 0123456789abcdef0123456789abcdef01234567") == []


def _code(wf: Path) -> str:
    """A workflow file without its comments (they may name what is forbidden)."""
    return "\n".join(re.sub(r"(^|\s)#.*$", "", ln) for ln in wf.read_text().splitlines())


def _jobs(text: str) -> list[str]:
    """The names of the jobs in a workflow file (two-space keys under ``jobs:``)."""
    body = text.split("\njobs:\n", 1)[1]
    return re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", body, re.M)


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_runs_only_on_hosted_runners(wf):
    text = _code(wf)
    assert "self-hosted" not in text
    runs_on = re.findall(r"runs-on:\s*(.+)", text)
    assert runs_on, "every job names its runner"
    assert set(r.strip() for r in runs_on) == {"ubuntu-latest"}


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_triggers_hand_no_secret_to_a_pull_request(wf):
    text = _code(wf)
    assert "pull_request_target" not in text
    assert "workflow_run" not in text
    if "pull_request" in text:
        assert "secrets." not in text, "no secret in a workflow that pull requests run"


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_pins_every_action_by_commit(wf):
    for use in re.findall(r"uses:\s*([^\s#]+)", _code(wf)):
        if use.startswith("./"):
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", use), use


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_jobs_have_time_limits_and_a_read_only_token(wf):
    text = _code(wf)
    assert re.search(r"^permissions:\s*\n\s+contents:\s*read\s*$", text, re.M), (
        "the workflow token is read-only"
    )
    jobs = _jobs(text)
    assert jobs
    assert text.count("timeout-minutes:") >= len(jobs), "every job has a timeout"


def _job_bodies(text: str) -> dict[str, str]:
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([A-Za-z0-9_-]+):\s*$", body, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def test_only_the_release_publish_job_may_write():
    """A token that may write exists in one job: the release workflow's publish job, behind
    the `release` environment, which refuses a tag not on main and runs no third-party action."""
    allowed = {("release.yml", "publish")}
    for wf in WORKFLOWS:
        text = _code(wf)
        top = text.split("\njobs:\n", 1)[0]
        assert not re.search(r":\s*write", top), f"{wf.name}: a workflow-wide write permission"
        for job, body in _job_bodies(text).items():
            if re.search(r":\s*write|write-all", body):
                assert (wf.name, job) in allowed, f"{wf.name} job {job} may write"
    pub = _job_bodies(_code(ROOT / ".github" / "workflows" / "release.yml"))["publish"]
    assert "environment: release" in pub and "merge-base --is-ancestor" in pub
    assert not [u for u in re.findall(r"uses:\s*([^\s#]+)", pub) if not u.startswith("actions/")]


def test_ci_workflow_cancels_superseded_runs():
    text = _code(ROOT / ".github" / "workflows" / "ci.yml")
    assert "cancel-in-progress: true" in text
    assert "matrix" not in text


def test_git_tracks_no_generated_or_private_files():
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.split()
    bad = [
        f
        for f in tracked
        if re.search(r"(^|/)(\.env[^/]*|node_modules|__pycache__)(/|$)", f)
        or f.endswith((".pem", ".key"))
    ]
    assert bad == []

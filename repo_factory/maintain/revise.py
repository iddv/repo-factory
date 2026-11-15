"""Revise: repo-factory fixes an issue you labelled ``go``, as the maintenance poll
(``watch.py``) starts it.

Nothing is written on the issue: no label, no claim, no comment. Which fix ran, its attempts,
its outcome and why live in repo-factory's own record, ``<loop home>/maintain/fixes.json``
(``fixes.py``); ``<loop home>/maintain/cache.json`` only remembers which fix process is
running. Before anything is spent and again before the push, the fix reads again that the
repository's maintenance is switched on and not paused, that maintenance's money is not spent
(``spend.py``), that the issue's ``go`` is still yours (``consent.py``), and where main's
workflows run and how sessions sign in (``where_it_runs``).

A fix runs detached, as a build does (``start``; ``repo-factory maintain revise REPO N`` is the
process), in a task folder ``<tasks_dir>/fix-<owner>-<name>-<N>`` whose event log the Builds list and the
build page read like any build's (kind *fix*). Its stages (``policies/v5-revise/rules.yaml``):

1. **reproduce**: a session writes one acceptance test (or names the open-item test that already
   checks the report); the tool runs it on main and keeps it only when it fails *for the reason
   the session stated* (its expectation must be in the failure output). Else, after
   ``reproduce.attempts`` tries: ``cannot-reproduce``.
2. **build**: a session makes the test pass with the smallest change (``prompts/fix.md``).
3. **verify**: no model. The open-item mark of the reproducing test and its README line come
   off; the whole acceptance suite runs on the fixed tree, sandboxed, network off; the
   reproducing test must pass and nothing that passed on main may fail.
4. **repair**, **reverify**: one more session with what verify found, then verify again.
5. **readiness**: no model. ``check_agents_md.py`` runs AGENTS.md's commands exactly as
   written (the quick start, the product's own tests, the acceptance suite) in the sandbox, on
   main and on the fix: a command that ran on main must still run on the fix. One that already
   fails on main is main's, not the fix's, and holds nothing.
   With ``maintain.exam`` (report, the default, or require), the checks published with the
   latest release run on main and on the fix (``checks.py``); a check that passed on main and
   fails on the fix is in the PR body (report) or stops the fix (require).
6. **deliver**: the scope check refuses a diff that touches ``.github/``, the policy file,
   ``LICENSE``, ``NOTICE``, an agent's or editor's file, or adds a secret-shaped file; the
   changed files are scanned for secrets and build jargon. The host then clones the repository
   afresh into its own folder (``outbox``), carries the changed files over as files
   (``carry.py``), checks the scope again there, commits and pushes onto a ``repo-factory/``
   branch that does not exist yet (a later attempt takes the next free name; nothing is ever
   overwritten), and opens the pull request with ``Fixes #N``. No git that reaches GitHub
   ever runs in the session's tree.

Security: the sessions run in the build sandbox with no GitHub credential, and with
``maintain.net: allowlist`` their proxy reaches only ``maintain.net_hosts``; the issue's text
reaches them only inside document delimiters; every GitHub write is the host's, from templates,
with the model's words sanitised. repo-factory only ever clones the default branch and pushes
new ``repo-factory/`` branches; it never checks out a branch it did not create."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import yaml

from .. import gitcmd
from ..resources import POLICIES
from . import labels as L
from .github import GitHubError, Repo
from .words import META, _doc, sanitize

POLICY = POLICIES / "v5-revise"
FORBIDDEN = (".github/workflows/", ".github/repo-factory.yml", "LICENSE", "NOTICE")
# what a fix never changes besides FORBIDDEN: all of .github/, and the files an agent or an
# editor runs or obeys (a fix that needs one is a row for the person)
NEVER_DIRS = (".github/", ".claude/", ".vscode/", ".devcontainer/", ".idea/", ".cursor/")
NEVER_NAMES = ("AGENTS.md", "CLAUDE.md", ".cursorrules")
MISSES = ("scope", "verify", "readiness", "security", "interpretation")
# the kinds whose test reproduces a failure; any other kind but docs gets a test that specifies
# the requested behaviour (prompts/specify.md), which fails on main because it is not there yet
REPRODUCE = ("bug", "security")
FIXES = re.compile(r"\b(?:fix|fixes|fixed|close|closes|closed|resolve|resolves|resolved)\s+#(\d+)\b",
                   re.I)
ACC = "tests/acceptance"
CHECK = Path(__file__).with_name("templates") / "check_agents_md.py"
# which check found what fails on main (``rec["agents_main"]["check"]``): a result of an older
# check is not kept (until 0.3.76 it ran Driftwatch's `serve` and `backup <file>` lines)
CHECK_VERSION = hashlib.sha256(CHECK.read_bytes()).hexdigest()[:12]
RESULT = "revise.json"                           # <task>/.den/revise.json: how the fix ended


class ReviseError(RuntimeError):
    pass


class Stop(Exception):
    """A stage ended the fix: ``outcome`` is ``failed`` or ``cannot_reproduce``. ``final``: no
    later attempt could do better (a charter guard held it): the issue waits for the owner."""

    def __init__(self, stage: str, why: str, outcome: str = "failed", partial: bool = False,
                 final: bool = False):
        super().__init__(why)
        self.stage, self.why, self.outcome, self.partial = stage, why, outcome, partial
        self.final = final


# -- names --------------------------------------------------------------------------------------
def slug(title: str, n: int = 40) -> str:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    out = ""
    for w in words:
        if len(out) + len(w) + 1 > n:
            break
        out = f"{out}-{w}" if out else w
    return out or "issue"


def safe_title(title: str) -> str:
    """An issue's title as a fix copies it into a commit message, a pull request's title and
    the first line of its body: sanitised (no link, mentions as code, one line) and with every
    issue reference broken (``#9`` becomes ``# 9``, ``GH-9`` ``GH- 9``), so a "closes #9" in
    someone's title closes nothing and links nothing."""
    t = sanitize(" ".join(str(title or "").split()), 200)
    t = re.sub(r"#(?=\d)", "# ", t)
    return re.sub(r"(?i)\bGH-(?=\d)", "GH- ", t)


def branch_name(number: int, title: str) -> str:
    return f"repo-factory/{int(number)}-{slug(title)}"


def task_dir(tasks_dir: Path, full: str, number: int) -> Path:
    """``<tasks_dir>/fix-<owner>-<name>-<N>``: one level down, as every build's folder (its
    name is the page's route key)."""
    owner, name = full.split("/")
    return Path(tasks_dir) / f"fix-{owner}-{name}-{int(number)}"


# -- what the issue list says --------------------------------------------------------------------
def linked(items: list[dict]) -> dict[int, list[int]]:
    """Issue number -> the open pull requests whose title or body says ``Fixes #N`` (anyone's:
    the poll lists them; only a pull request this install's fix opened is the fix of N)."""
    out: dict[int, list[int]] = {}
    for p in items:
        if p["is_pr"] and p.get("state", "open") == "open":
            for n in {int(x) for x in FIXES.findall(f"{p['title']}\n{p.get('body') or ''}")}:
                out.setdefault(n, []).append(p["number"])
    return out


def kind_of(item: dict) -> str | None:
    return next((x for x in L.KINDS if x in item["labels"]), None)


# -- the fix's trees: tests and checks, sandboxed -----------------------------------------------
# A session's workspace is the clone, .git included: the host's git (gitcmd) never runs a hook,
# an fsmonitor command or a filter that a session could have written there.
def git(*args: str, cwd: Path, check: bool = True, env: dict | None = None) -> str:
    p = gitcmd.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise ReviseError(f"git {' '.join(args[:2])} failed: "
                          f"{(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def commit(root: Path, msg: str, author: list[str] | tuple = ()) -> None:
    """One commit as ``author`` (``identity.git_author``: the bot, when repo-factory acts as its own
    app), else as the configured git identity (the owner's, as publish commits), else as
    repo-factory."""
    who = list(author) or ([] if git("config", "user.email", cwd=root, check=False).strip()
                           else ["-c", "user.name=repo-factory", "-c", "user.email=repo-factory@localhost"])
    git(*who, "commit", "-q", "-m", msg, cwd=root)


def pins(root: Path) -> list[str]:
    """The test requirements AGENTS.md's commands pin (``pytest==8.3.3``), else pytest, plus
    pip, so its ``python -m pip install ...`` line runs offline in the sandbox."""
    try:
        text = (Path(root) / "AGENTS.md").read_text(errors="replace")
    except OSError:
        text = ""
    return sorted({"pip", *(re.findall(r"\b(pytest==[\w.]+)", text) or ["pytest"])})


def default_venv(root: Path) -> Path:
    from ..harness.venv import ensure_venv
    return ensure_venv(pins(root))


def run_tests(root: Path, targets: list[str], venv: Path, timeout_s: int = 900) -> dict:
    """pytest on a copy of ``root``, sandboxed, network off (``run_pytest``)."""
    from ..harness.venv import run_pytest
    return run_pytest(Path(root), {}, list(targets), venv, timeout_s=timeout_s, network=False)


def run_agents_check(root: Path, venv: Path, timeout_s: int = 1800) -> dict:
    """``check_agents_md.py`` (the repo's, else the template) on a copy of ``root`` in the
    sandbox, network off, with ``venv``'s python first on PATH: AGENTS.md's commands exactly as
    written, the architecture paths, and no XPASS that still carries its mark."""
    from ..harness.isolation import sandbox_cmd
    with tempfile.TemporaryDirectory(prefix="repo-factory-check-") as tmp:
        work = Path(tmp) / "a"
        shutil.copytree(root, work, ignore=shutil.ignore_patterns(".git", "__pycache__",
                                                                  ".pytest_cache", ".repo-factory"))
        script = work / ".github" / "scripts" / "check_agents_md.py"
        if not script.is_file():
            script.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(CHECK, script)
        env = (("PATH", f"{venv}/bin:/usr/bin:/bin"), ("PYTHONDONTWRITEBYTECODE", "1"),
               ("HOME", str(work)), ("AGENTS_MD_TIMEOUT", "600"))
        cmd = sandbox_cmd(work, ro=(venv,), interp=str(venv / "bin" / "python"), env=env,
                          network=False) + [str(script), str(work)]
        try:
            p = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return {"ok": False, "summary": f"AGENTS.md's commands took over {timeout_s} s",
                    "output": ""}
    out = (p.stdout or "") + (p.stderr or "")
    last = [ln for ln in out.splitlines() if ln.strip()][-1:] or [""]
    return {"ok": p.returncode == 0, "summary": last[0], "output": out[-6000:]}


def problems(chk: dict) -> set[str]:
    """What one run of ``check_agents_md.py`` found wrong, one key each: the command a FAIL line
    names (its exit code and output aside, which differ run to run), else the line; the summary
    when the check could not start at all (no AGENTS.md, no fence)."""
    out = set()
    for ln in (chk.get("output") or "").splitlines():
        if ln.startswith("FAIL  "):
            m = re.match(r"FAIL  (`[^`]+`)", ln)
            out.add(m.group(1) if m else ln[6:].strip())
    if not chk.get("ok") and not out:
        out.add((chk.get("summary") or "the check failed").strip())
    return out


def norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", str(s or "").lower()).split())


def fails_for(expect: str, detail: str) -> bool:
    """The failure output carries the stated expectation: the phrase itself, or every word of
    it of three letters or more (the harness's check against a test that fails for another
    reason)."""
    e, d = norm(expect), norm(detail)
    if not e:
        return False
    if e in d:
        return True
    words = [w for w in e.split() if len(w) >= 3]
    return bool(words) and all(w in d.split() for w in words)


def scope_violations(changed: list[str]) -> list[str]:
    """What a fix's diff may never touch: workflows, the policy, the licence and
    notice, a file outside the repo, a secret-shaped file."""
    from ..loop.secrets import BAD_FILES
    bad = []
    for f in changed:
        name = Path(f).name
        if f.startswith("/") or ".." in Path(f).parts:
            bad.append(f"{f}: outside the repository")
        elif any(f == x or f.startswith(x) for x in FORBIDDEN):
            bad.append(f"{f}: a fix never changes this file")
        elif any(f.startswith(d) or f"/{d}" in f"/{f}" for d in NEVER_DIRS) or name in NEVER_NAMES:
            bad.append(f"{f}: an agent's, an editor's or CI's file: a fix never changes one; "
                       "if the fix needs it, change it yourself")
        else:
            for what, pat, ok in BAD_FILES:
                if pat.search(name) and not (ok and ok.search(name)):
                    bad.append(f"{f}: a {what}")
                    break
    return bad


def unmark(root: Path, test: str) -> bool:
    """A fixed open item: the mark block at the end of ``test`` and its README line go."""
    from ..v5 import acceptance
    p = Path(root) / test
    changed = False
    if p.is_file():
        code = p.read_text()
        new = acceptance.unmark(code)
        if new != code:
            p.write_text(new)
            changed = True
    readme = Path(root) / ACC / "README.md"
    if readme.is_file():
        t = readme.read_text()
        new = acceptance.readme_remove_open_item(t, test)
        if new != t:
            readme.write_text(new)
            changed = True
    return changed


def is_open_item(root: Path, test: str) -> bool:
    from ..v5.acceptance import OPEN_MARK
    p = Path(root) / test
    return p.is_file() and OPEN_MARK in p.read_text(errors="replace")


def statuses(t: dict) -> dict[str, str]:
    out = dict(t.get("tests") or {})
    for f in t.get("errors") or []:
        out[f] = "error"
    return out


def first_failure(t: dict, test: str) -> str:
    det = t.get("details") or {}
    for k, v in det.items():
        if k.split("::")[0] == test:
            return str(v)
    return "\n".join(str(v) for v in det.values())


# -- one fix ------------------------------------------------------------------------------------
def load_rules() -> dict:
    return yaml.safe_load((POLICY / "rules.yaml").read_text()) or {}


def doc(item: dict) -> str:
    body = item.get("body") or "(no description)"
    t = f"Title: {item['title']}\n\n{body}"
    return (f'<<<document name="issue #{item["number"]}">>>\n'
            f"{_doc(t)[:12000]}\n<<<end>>>")


class Fix:
    """One fix of one issue, in its own process. Nothing is written on the issue: the attempts
    live in ``fixes.json``, your ``go`` is read again from the issue's events, and the switch,
    the pauses and the monthly limit are read again before every session and before the push;
    never a force-push. ``harness``: a test double for the sessions (as v5's); ``venv``,
    ``tests``, ``check``: replaceable runners (tests)."""

    def __init__(self, loop, full: str, number: int, *, harness=None, tests=None, check=None,
                 venv=None, now=None, exam=None):
        from ..maintain import policy as P
        from .watch import gh_for, published, settings
        self.loop, self.full, self.number = loop, full, int(number)
        self.gh = gh_for(loop)
        self.r = Repo(self.gh, full)
        try:  # the default branch, read once: "main" when it cannot be read
            self.r.default_branch()
        except GitHubError:
            pass
        self.s = settings(loop.cfg)
        self.pol = P.load(self.gh, full)
        self.t = next((t for t in published(loop) if t["repo"] == full),
                      {"repo": full, "idea": None, "product": full.split("/")[1],
                       "one_liner": ""})
        self.item = self.r.issue(self.number)
        self.rules = load_rules()
        self.harness, self._tests, self._check, self._venv = harness, tests, check, venv
        self.task = task_dir(loop.cfg.tasks_dir, full, self.number)
        self.work = loop.cfg.layout.maintain / "work" / full.replace("/", "-") / str(number)
        self.branch = branch_name(self.number, self.item["title"])
        self.max_attempts = int(self.pol["revise"]["max_attempts"])
        self.kind = kind_of(self.item) or "bug"
        self.t0 = now or time.time()
        self.usd = 0.0
        self.tok_in = 0                             # the fix's tokens (sessions' own usage)
        self.tok_out = 0
        self.sessions = 0
        self.test: str | None = None
        self.expect = ""
        self.failure = ""
        self.found: list[str] = []
        self.fix: dict = {}
        self.baseline: dict[str, str] = {}
        self.verified: dict = {}
        self.ready: dict = {}
        # maintain.exam: the latest release's checks asset on main and on the fix (checks.py);
        # ``exam``: a replaceable runner, tree -> exam record (tests)
        from . import checks
        self.exam_mode = checks.mode(self.s)
        self._exam, self._asset = exam, None
        self.exam_base: dict | None = None
        self.exam_res: dict | None = None

    # -- plumbing ---------------------------------------------------------------------------
    @property
    def cap(self) -> float | None:
        # 0 is a cap of nothing (loop.yaml's schema refuses it), never "no cap"
        caps = [float(x) for x in (self.pol["revise"].get("max_usd"), self.s.get("max_usd"))
                if x is not None and x != ""]
        return min(caps) if caps else None

    def venv(self) -> Path:
        if self._venv is None:
            self._venv = default_venv(self.repo)
        return Path(self._venv)

    def tests(self, targets: list[str]) -> dict:
        t = float((self.rules.get("verify") or {}).get("timeout_s") or 1200)
        return (self._tests or (lambda root, tg: run_tests(root, tg, self.venv(), int(t))))(
            self.repo, targets)

    def suite(self) -> list[str]:
        """Every test of the product, its own and the acceptance suite (pytest also collects
        unittest cases): what verify compares with main."""
        return ["tests"] if (self.repo / "tests").is_dir() else [ACC]

    def check(self) -> dict:
        t = int((self.rules.get("readiness") or {}).get("timeout_s") or 1800)
        return (self._check or (lambda root: run_agents_check(root, self.venv(), t)))(self.repo)

    def exam_on(self, tree: Path) -> dict:
        """The release's checks on ``tree``: ``{"why": ...}`` when there is nothing to run."""
        from . import checks
        if self._exam is not None:
            return self._exam(tree)
        if self._asset is None:
            folder, tag, why = checks.fetch(self.gh, self.full, self.work / "checks")
            if folder is None:
                self._asset = {"why": why, "tag": tag}
            else:
                self._asset = checks.Asset(folder, self.work / "checks" / "run")
                self._asset.tag = tag
        if isinstance(self._asset, dict):
            return dict(self._asset)
        return {**checks.run_on(self._asset, tree), "tag": self._asset.tag}

    def do_exam(self) -> dict | None:
        """maintain.exam on the fixed tree, compared with main (``self.exam_base``)."""
        from . import checks
        base = self.exam_base or {}
        if base.get("why") and not base.get("ran"):
            self.exam_res = {"why": base["why"], "ran": False}
            return self.exam_res
        res = checks.compare(base, self.exam_on(self.repo))
        res["tag"] = base.get("tag")
        self.exam_res = res
        if self.exam_mode == "require" and res["regressions"]:
            raise Stop("readiness", "repo-factory's checks from the latest release passed on main and "
                       "fail on the fix: " + ", ".join(res["regressions"][:10]), partial=True)
        return res

    @property
    def repo(self) -> Path:
        return self.work / "repo"

    def emit(self, type: str, **fields) -> None:
        self.log.emit(type, **fields)

    def stage(self, name: str, fn):
        t0, usd0, tin0, tout0 = time.time(), self.usd, self.tok_in, self.tok_out
        from .. import custom_prompts
        self.emit("v5_stage", stage=name, status="started")
        try:
            with custom_prompts.collect() as used:
                out = fn()
        except Stop as e:
            self.emit("v5_stage", stage=name, status="failed", why=e.why[:300],
                      usd=round(self.usd - usd0, 4), tokens_in=self.tok_in - tin0,
                      tokens_out=self.tok_out - tout0, wall_s=round(time.time() - t0, 1),
                      **({"prompts": used} if used else {}))
            raise
        self.emit("v5_stage", stage=name, status="done", usd=round(self.usd - usd0, 4),
                  tokens_in=self.tok_in - tin0, tokens_out=self.tok_out - tout0,
                  wall_s=round(time.time() - t0, 1),
                  **({"why": out} if isinstance(out, str) else {}),
                  **({"prompts": used} if used else {}))
        return out

    # -- what is read again before anything is spent or pushed --------------------------------
    def still_wanted(self) -> str | None:
        """Why this fix must not go on now, or None: maintenance switched off for the repo, the
        repo or the loop paused, or maintenance's money spent."""
        from . import pauses, spend, switch
        home = self.loop.cfg.home
        if not switch.is_on(home, self.full):
            return f"maintenance was switched off for {self.full}"
        if pauses.get(home, self.full):
            return f"maintenance was paused for {self.full}"
        if self.loop.cfg.paused():
            return "the loop was paused"
        return spend.reached(self.loop)

    def owners_go(self) -> str | None:
        """Why the issue is not the signed-in person's word, or None: it must be
        open, its ``go`` must have been put on by the login maintenance acts for (the
        issue's events), never by anyone else, and nobody else may have changed its title or
        body since (``consent.why_not``)."""
        from . import consent
        from .identity import login_of
        if self.item["state"] != "open" or "go" not in self.item["labels"]:
            return "the issue is no longer open and labelled go"
        from .watch import acting_login
        me = {x for x in (login_of(self.loop.gh), getattr(self.gh, "login_name", None),
                          acting_login(self.gh)) if x}
        try:
            return consent.why_not(self.r, self.item, me)
        except GitHubError as e:
            return f"the issue's events could not be read: {str(e)[:200]}"

    def where_it_runs(self) -> str | None:
        """Why the fix may not run or be pushed here, read now: sessions would
        sign in with Claude Code's own sign-in file (``signin.py``), or a workflow on main may
        run off GitHub's own runners, where the fix's pull request would run its code
        (``runners.py``); or None."""
        from . import doctor, signin
        held = signin.blocks(self.loop.cfg)
        if held:
            return held
        try:
            off = doctor.hosted_now(self.r)
        except GitHubError as e:
            return f"where main's workflows run could not be read: {str(e)[:200]}"
        if off:
            return ("a workflow on main may run off GitHub's own runners, where this fix's pull "
                    "request would run its code: " + "; ".join(off)[:400])
        return None

    def skip(self, name: str, why: str) -> None:
        self.emit("v5_stage", stage=name, status="skipped", usd=0.0, wall_s=0.0, why=why)

    def session(self, stage: str, text: str, resume: dict | None = None) -> dict:
        from ..costs import ledger
        from ..v5.launch import launch
        caps = dict(self.rules.get(stage) or {})
        model = (self.pol["revise"].get("models") or {}).get(stage) or caps.get("model")
        # the session's own cap (null: none) under what is left of the fix's cap (if any)
        want = float(caps["max_usd"]) if caps.get("max_usd") else None
        if self.cap is not None:
            left = self.cap - self.usd
            if left < 0.05:
                raise Stop(stage, f"the fix's cap ${self.cap:.2f} is reached "
                                  f"(spent ${self.usd:.2f})", partial=stage != "reproduce")
            want = left if want is None else min(want, left)
        from . import spend
        why = self.still_wanted()
        if why:
            raise Stop(stage, why, outcome="skipped")
        month = spend.left(self.loop)              # this month's (and today's) money left
        if month is not None:
            want = month if want is None else min(want, month)
        sess = {"id": str(uuid.uuid4())}             # the CLI takes only a UUID
        if resume and resume.get("id"):
            sess.update(resume=resume["id"], base_cost_usd=resume.get("cost") or 0)
        t0 = time.time()
        role = "reproducer" if stage == "reproduce" else "builder"   # repair resumes the fix
        res = launch(text, self.repo, config_dir=self.den / "cfg" / role,
                     max_turns=int(caps.get("max_turns") or 60),
                     max_usd=round(want, 2) if want is not None else None,
                     timeout_s=int(caps.get("timeout_s") or 1800), session=sess, model=model,
                     harness=self.harness)
        for name in ("transcript.jsonl", "stderr.txt"):
            p = self.repo / name
            if p.exists():
                (self.den / "transcripts").mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), self.den / "transcripts" / f"{stage}{self.sessions}.{name}")
        usd = float(res.cost_usd or 0.0)
        self.usd = round(self.usd + usd, 4)
        self.sessions += 1
        # the harness's usage is this run's own, also when it resumes a session
        tin = int(getattr(res, "input_tokens", 0) or 0)
        tout = int(getattr(res, "output_tokens", 0) or 0)
        self.tok_in, self.tok_out = self.tok_in + tin, self.tok_out + tout
        ledger.record("worker", stage=stage, purpose=stage, usd=usd, model=res.model,
                      in_tokens=tin, out_tokens=tout,
                      wall_s=round(time.time() - t0, 3), ok=bool(res.exit_ok),
                      exit_reason=res.exit_reason, turns=res.num_turns,
                      session=res.session_id or sess["id"])
        self.loop.ledger.spend("revise", usd, self.t["idea"], in_tokens=tin, out_tokens=tout)
        self.emit("candidate_finished", candidate=self.sessions, run=stage, node=self.sessions,
                  kind="refine", valid=bool(res.exit_ok), usd=usd, exit_reason=res.exit_reason,
                  model=res.model)
        return {"id": res.session_id or sess["id"], "cost": res.session_cost_usd
                if res.session_cost_usd is not None else usd, "ok": bool(res.exit_ok),
                "reason": res.exit_reason}

    def note(self, name: str) -> dict:
        p = self.repo / ".repo-factory" / name
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    def git_auth(self) -> tuple[list[str], dict | None]:
        """Maintenance's identity for a git call that reaches GitHub: through the token file
        when there is one (``Gh.git_auth``), through a token narrowed to pushing this one
        repository when repo-factory acts as its own app (``identity.narrow``), else as git is
        set up (the owner's login). The host does the push; no session ever holds the token."""
        from . import identity
        g = identity.narrow(self.gh, "push")
        auth = g.git_auth()
        try:
            env = g.env() if auth else None
        except Exception as e:                  # noqa: BLE001 - the app could not mint
            from ..github_app import AppError
            if not isinstance(e, AppError):
                raise
            raise ReviseError(str(e)) from None
        return auth, env

    def author(self) -> list[str]:
        """``git -c`` arguments for a commit: the bot's name and noreply address when repo-factory
        acts as its own app, else none (the configured git identity)."""
        from . import identity
        return identity.git_author(self.gh)

    def changed(self, base: str | None = None) -> list[str]:
        """What the workspace changes against the commit the fix started from (recorded at
        the checkout, before any session: a ref a session moved changes nothing here)."""
        base = base or getattr(self, "base", None) or f"origin/{self.r.base}"
        out = git("diff", "--name-only", "--no-renames", base, cwd=self.repo).split()
        out += git("ls-files", "--others", "--exclude-standard", cwd=self.repo).split()
        return sorted({f for f in out if not f.startswith(".repo-factory/")})

    # -- the stages -------------------------------------------------------------------------
    def checkout(self) -> None:
        if self.work.exists():
            shutil.rmtree(self.work)
        self.work.mkdir(parents=True)
        self.gh("repo", "clone", self.full, str(self.repo), "--", "-q")
        gitcmd.pin(self.repo)
        git("checkout", "-q", "-B", self.branch, f"origin/{self.r.base}", cwd=self.repo)
        self.base = git("rev-parse", "HEAD", cwd=self.repo).strip()
        excl = self.repo / ".git" / "info" / "exclude"
        excl.parent.mkdir(parents=True, exist_ok=True)
        with open(excl, "a") as f:
            f.write("\n.repo-factory/\ntranscript.jsonl\nstderr.txt\n")

    def do_reproduce(self) -> None:
        """The test first: one that reproduces a bug or security report on main, or for any
        other kind one that specifies the requested behaviour. Either
        must fail on main for the stated reason."""
        name = "reproduce" if self.kind in REPRODUCE else "specify"
        text = self.prompt(name, issue=doc(self.item))
        tries = int((self.rules.get("reproduce") or {}).get("attempts") or 2)
        sess, why = None, "no attempt"
        for k in range(tries):
            sess = self.session("reproduce", text if k == 0 else self.prompt(
                name, "again", reason=why), resume=sess)
            got = self.note("reproduce.json")
            if not got:                    # no answer at all: the session failed, not the issue
                raise Stop("reproduce", "the reproduce session ended without an answer ("
                           f"{sess.get('reason') or 'no reason'})")
            test = got.get("test")
            if not test:
                why = sanitize(got.get("why") or "the session named no test", 400)
                raise Stop("reproduce", why, outcome="cannot_reproduce")
            test = str(test).strip().lstrip("./")
            why = self.judge_test(test, got)
            if why is None:
                self.test, self.expect = test, str(got.get("expect") or "")
                return None
        raise Stop("reproduce", f"no test failed for the reported reason: {why}",
                   outcome="cannot_reproduce")

    def judge_test(self, test: str, got: dict) -> str | None:
        """Why the reproducing test is not accepted, or None: it is one test file of the
        product's own suite (a repo that kept its acceptance suite has it under
        ``tests/acceptance/``; a newer one under its own tests directory), the only change,
        and on main it fails (not errors) for the stated reason."""
        from ..v5.exam import is_test_path
        if not (test.endswith(".py") and is_test_path(test)) \
                or not (self.repo / test).is_file():
            return f"`{test}` is not a test file of the product's own suite"
        others = [f for f in self.changed() if f != test]
        if others:
            git("checkout", "-q", "--", *[f for f in others if self.tracked(f)], cwd=self.repo,
                check=False)
            for f in others:
                if not self.tracked(f) and (self.repo / f).is_file():
                    (self.repo / f).unlink()
        if not got.get("existing") and test not in self.changed():
            return f"`{test}` already exists and is not a known open item; write a new file"
        if got.get("existing") and not is_open_item(self.repo, test):
            return f"`{test}` is not marked as a known open item"
        res = self.tests([test, "--runxfail"])
        st = {k: v for k, v in statuses(res).items() if k.split("::")[0] == test}
        if not st:
            return f"`{test}` did not run (no test collected)"
        if any(v == "error" for v in st.values()):
            return f"`{test}` does not load or errors: " + first_failure(res, test)[-400:]
        if not any(v == "failed" for v in st.values()):
            return f"`{test}` passes on the current code"
        detail = first_failure(res, test)
        if not fails_for(got.get("expect") or "", detail):
            last = detail.strip().splitlines()[-1][:300] if detail.strip() else "no output"
            return (f"`{test}` fails, but not with the stated expectation "
                    f"({sanitize(got.get('expect') or '(none given)', 160)}): {last}")
        self.failure = detail.strip()[-1500:]
        return None

    def tracked(self, f: str) -> bool:
        return bool(git("ls-files", "--", f, cwd=self.repo).strip())

    def prompt(self, name: str, section: str | None = None, **vals) -> str:
        from ..v5.common import prompt
        return prompt(POLICY, name, section, product=self.t["product"], **vals)

    def do_build(self) -> None:
        test = (self.prompt("fix", "test", file=self.test, failure=self.failure or "(see the "
                            "test)") if self.test else self.prompt("fix", "docs"))
        if "check" in [str(x.get("name") if isinstance(x, dict) else x)
                       for x in self.item.get("labels") or []]:
            # a check of repo-factory's that may have judged a build wrongly
            test += "\n\n" + self.prompt("fix", "check")
        if getattr(self, "previous", None):
            # a retry on the same inputs fails the same way: it hears why the last one stopped
            test += "\n\n" + self.prompt("fix", "previous", stage=self.previous["stage"],
                                           why='<<<document name="the last attempt">>>\n'
                                               f"{_doc(self.previous['why'])}\n<<<end>>>")
        self.fix_session = self.session("build", self.prompt("fix", issue=doc(self.item),
                                                             test=test))
        self.fix = self.note("fix.json")
        if not [f for f in self.changed() if f != self.test or not self.test]:
            raise Stop("build", "the fix session changed nothing ("
                       f"{self.fix_session.get('reason') or 'no reason'})", partial=False)

    def do_verify(self) -> str | None:
        """No model: the reproducing test passes, nothing that passed on main fails, the
        scope holds."""
        self.found = []
        if self.test:
            unmark(self.repo, self.test)
        bad = scope_violations(self.changed())
        self.found += [f"out of scope: {b}" for b in bad]
        res = self.tests(self.suite())
        now = statuses(res)
        if self.test:
            mine = {k: v for k, v in now.items() if k.split("::")[0] == self.test}
            if not mine or any(v != "passed" for v in mine.values()):
                self.found.append(f"`{self.test}` still fails: "
                                  + first_failure(res, self.test).strip()[-600:])
        for k, v in sorted(self.baseline.items()):
            if v == "passed" and now.get(k) not in ("passed",) and \
                    k.split("::")[0] != self.test:
                self.found.append(f"regression: `{k}` passed on main and is {now.get(k) or 'gone'}"
                                  " now" + (": " + first_failure(res, k.split("::")[0])
                                            .strip()[-300:] if now.get(k) else ""))
        passed = sum(1 for v in now.values() if v == "passed")
        self.verified = {"passed": passed, "open": sum(1 for v in now.values() if v == "skipped"),
                         "failed": sum(1 for v in now.values() if v in ("failed", "error")),
                         "found": list(self.found)}
        self.emit("findings", stage="verify", confirmed=len(self.found),
                  scenarios=len(now), failing=self.verified["failed"])
        return None if not self.found else f"{len(self.found)} finding(s)"

    def do_repair(self) -> None:
        self.session("repair", self.prompt("fix", "repair", findings="\n".join(
            f"- {f}" for f in self.found)), resume=getattr(self, "fix_session", None))
        self.fix = self.note("fix.json") or self.fix

    def check_main(self) -> None:
        """AGENTS.md's commands on main, before anything is spent: what already fails there
        (``self.main_problems``) is main's, so readiness never counts it against the fix. Once
        per main commit: the poll keeps the answer (``rec["agents_main"]``), and the repo's
        AGENTS.md row says what fails (advisory: it holds no fix)."""
        from .github import Cache
        sha = git("rev-parse", "HEAD", cwd=self.repo).strip()
        known = Cache(self.loop.cfg.home).repo(self.full).get("agents_main") or {}
        if known.get("sha") == sha and known.get("ok") and known.get("check") == CHECK_VERSION:
            self.agents_main, self.main_problems = known, set()
            return
        res = self.check() if (self.repo / "AGENTS.md").is_file() else \
            {"ok": False, "summary": MISSING, "output": ""}
        self.main_problems = problems(res)
        self.agents_main = {"sha": sha, "ok": bool(res.get("ok")), "at": time.time(),
                            "check": CHECK_VERSION,
                            "summary": sanitize(res.get("summary") or "", 300)}

    def do_readiness(self) -> str:
        """No worse than main: each problem the check finds on the fix that it did not find on
        main stops the fix; one main already had is said in the PR, never a stop."""
        self.ready = self.check()
        new = sorted(problems(self.ready) - getattr(self, "main_problems", set()))
        if new:
            raise Stop("readiness", "AGENTS.md's commands that ran on main fail on the fix: "
                       + "; ".join(new)[:400] + "\n" + self.fail_lines(self.ready),
                       partial=True)
        if self.exam_mode != "off":
            self.do_exam()
        old = len(problems(self.ready)) if not self.ready.get("ok") else 0
        if old:                                    # the PR body says it too
            self.ready["summary"] = (f"{self.ready.get('summary') or 'failed'} ({old} already "
                                     "on main, not this fix's)")
        return self.ready.get("summary") or "passed"

    @staticmethod
    def fail_lines(chk: dict) -> str:
        return "\n".join(ln for ln in (chk.get("output") or "").splitlines()
                         if ln.startswith("FAIL"))[:800]

    def do_deliver(self) -> dict:
        from ..loop.secrets import scan_text
        from ..v5 import jargon
        changed = self.changed()
        bad = scope_violations(changed)
        held = self.charter_problems(changed)
        if held:
            raise Stop("deliver", "; ".join(held)[:900], partial=False, final=True)
        hits = []
        for f in changed:
            p = self.repo / f
            if p.is_file():
                hits += [f"{f}:{n} {k}" for n, k, _ in scan_text(p.read_text(errors="replace"))]
        # the provenance scan over the product's prose this fix changed
        users = {p.relative_to(self.repo).as_posix() for p in jargon.product_files(self.repo)}
        words = jargon.scan(self.repo, {}, [self.repo / f for f in changed if f in users])
        if bad or hits or words:
            raise Stop("deliver", "; ".join(
                [*(f"scope: {b}" for b in bad), *(f"secret-shaped text in {h}" for h in hits),
                 *(f"build jargon in {w['file']}:{w['line']} ({w['kind']})" for w in words)])[:900],
                partial=False)
        return self.deliver_quiet(changed)  # nothing of the session's tree is pushed from

    def message(self) -> str:
        return (f"Fix #{self.number}: {safe_title(self.item['title'])}\n\n"
                f"{sanitize(self.fix.get('what') or 'The fix.', 900)}\n\n"
                f"Fixes #{self.number}\n")

    @property
    def outbox(self) -> Path:
        """The host's own clone for the push: beside the workspace, never given to a
        session."""
        return self.work / "outbox"

    def deliver_quiet(self, changed: list[str]) -> dict:
        """Quiet maintenance's push: only while the fix is still wanted and the issue still
        carries your ``go`` (both read again now). The host clones the repository afresh into
        its own folder at the commit the fix started from, carries the changed files over as
        files (``carry.py``), checks the scope again on that tree, commits there and pushes to
        the address it builds from the repository's name, onto a ``repo-factory/`` branch that
        does not exist yet (a later attempt takes the next free name: nothing is ever
        force-pushed or overwritten); then the pull request. No git that reaches GitHub ever
        runs in the session's tree, and nothing is written on the issue."""
        from . import carry
        why = self.still_wanted()
        if not why:
            try:
                self.item = self.r.issue(self.number)
                why = self.owners_go() or self.where_it_runs()
            except GitHubError as e:
                why = f"the issue could not be read again: {e}"
        if why:
            raise Stop("deliver", why, outcome="skipped")
        auth, env = self.git_auth()
        try:
            dest = carry.clone(self.full, self.outbox, self.base, auth, env)
            moved = carry.carry(self.repo, dest, changed)
        except carry.CarryError as e:
            raise Stop("deliver", str(e), partial=False) from None
        bad = scope_violations(moved)
        if bad:
            raise Stop("deliver", "; ".join(f"scope: {b}" for b in bad)[:900], partial=False)
        if not moved:
            raise Stop("deliver", "the fix changed nothing once carried over", partial=False)
        changed = moved
        commit(dest, self.message(), author=self.author())
        taken = carry.taken(dest, self.full, self.branch, auth, env)
        branch, k = self.branch, 1
        while branch in taken:
            k += 1
            branch = f"{self.branch}-{k}"
        why = self.still_wanted()                 # the clone took a while: once more
        if why:
            raise Stop("deliver", why, outcome="skipped")
        try:
            head = carry.push(dest, self.full, branch, auth, env)
        except carry.CarryError as e:
            raise Stop("deliver", str(e)) from None
        self.branch = branch
        pr = self.r.create_pr(f"Fix #{self.number}: {safe_title(self.item['title'])}", branch,
                              self.pr_body(changed))
        self.r.set_labels(pr["number"], ["repo-factory", self.kind], have=[])
        self.emit("delivered", node=self.sessions, branch=branch, out=str(dest),
                  report=pr.get("url"), changed=True)
        return {**pr, "head": head, "branch": branch}

    def charter_problems(self, changed: list[str], base: str | None = None) -> list[str]:
        """The project's charter on this diff: a promise's sentence gone
        from the README, a dependency added where the charter says standard library only."""
        from ..projects.charter_check import for_fix
        base = base or getattr(self, "base", None) or f"origin/{self.r.base}"

        def at_base(f: str) -> str | None:
            try:
                return git("show", f"{base}:{f}", cwd=self.repo)
            except ReviseError:
                return None
        return for_fix(self.loop.cfg.home, getattr(self, "key", None) or self.full, self.repo,
                       changed, at_base)

    def pr_body(self, changed: list[str]) -> str:
        from . import checks
        f = self.fix
        docs = [d for d in ("CHANGELOG.md", "READINESS.md", "SECURITY.md") if d in changed]
        agents = "updated" if "AGENTS.md" in changed else "unchanged on purpose"
        miss = f.get("miss") if f.get("miss") in MISSES else "verify"
        flow = f.get("flow") if re.fullmatch(r"F\d{1,2}", str(f.get("flow") or "")) else None
        v = self.verified
        repro = (f"`{self.test}` (failed on main: {sanitize(self.expect, 200)})"
                 if self.test else "a documentation issue: AGENTS.md's commands are the check")
        mins = round((time.time() - self.t0) / 60)
        meta = (f"{META} issue={self.number} kind={self.kind} miss={miss}"
                + (f" flow={flow}" if flow else "") + (f" test={self.test}" if self.test else "")
                + " -->")
        return "\n".join([
            f"Fix #{self.number}: {safe_title(self.item['title'])}", "",
            f"**What:** {sanitize(f.get('what') or 'see the diff', 900)}",
            f"**Why it happened:** {sanitize(f.get('why') or 'not stated', 300)}",
            f"**Reproduced by:** {repro}",
            f"**Verified:** {v.get('passed', 0)} tests passed (the product's own and the "
            f"acceptance suite, sandboxed), {v.get('open', 0)} known open item(s), nothing that "
            f"passed on main fails · AGENTS.md's commands as written (quick start, the "
            f"product's own tests, the acceptance suite): "
            f"{sanitize(self.ready.get('summary') or 'not run', 160)}",
            *([checks.line(self.exam_res)] if self.exam_res else []),
            f"**Documents:** AGENTS.md {agents}" + "".join(f" · {d}" for d in docs),
            f"**Cost:** ${self.usd:.2f}, {mins} min, {self.sessions} model sessions", "",
            f"Fixes #{self.number}", "",
            "Opened by repo-factory (automated). The owner reviews and merges; nothing merges "
            "itself unless the owner switched on merging repo-factory's own bug and docs fixes "
            "for this repository.", "", meta, ""])

    # -- the run ----------------------------------------------------------------------------
    def run(self) -> dict:
        from ..costs import ledger
        from ..events import EventLog, log_path
        self.den = self.task / ".den"
        if not (self.task / "loop.json").is_file():          # run by hand, not by the tick
            write_task(self.loop.cfg, self.full, self.item, self.t["product"], self.branch)
        from . import fixes  # repo-factory's own record, never the issue's comments
        mine = fixes.get(self.loop.cfg.home, self.full, self.number)
        k = fixes.attempts(mine) + 1
        self.attempt = k
        self.previous = ({"stage": str(mine.get("stage") or "?"),
                          "why": str(mine.get("why"))[:1500]}
                         if mine.get("outcome") == "failed" and mine.get("why") else None)
        (self.den / RESULT).unlink(missing_ok=True)          # the last run's outcome
        from ..events import START_TYPES, read_events
        rid = 1 + sum(1 for e in read_events(log_path(self.den)) if e["type"] in START_TYPES)
        self.log = EventLog(log_path(self.den), self.task.name, rid)
        stages = list(self.rules.get("stages") or [])
        self.emit("build_started", spec=str(self.task / "spec.md"), taskdir=str(self.task),
                  title=f"Fix #{self.number}: {self.item['title']}", policy="v5-revise",
                  harness=getattr(self.harness, "name", "claude-code"), owner="none",
                  out=str(self.repo), pid=os.getpid(), pipeline="v5", cap_usd=self.cap,
                  stages=stages)
        if self.s.get("net", "allowlist") == "allowlist":
            from ..harness.sandbox import NET_HOSTS_ENV
            os.environ[NET_HOSTS_ENV] = ",".join(self.s.get("net_hosts") or [])
        out: dict = {"repo": self.full, "issue": self.number, "attempt": k}
        with ledger.activate(ledger.ledger_path(self.den), rid, events=self.log):
            try:
                why = self.still_wanted() or self.owners_go() or self.where_it_runs()
                if why:
                    raise Stop("start", why, outcome="skipped")
                self.checkout()
                why = None
                self.check_main()               # AGENTS.md runs on main, or nothing is spent
                self.baseline = statuses(self.tests(self.suite()))    # main, before any change
                if self.exam_mode != "off":
                    self.exam_base = self.exam_on(self.repo)
                if self.kind == "docs":
                    why = "a documentation issue: AGENTS.md's commands are the check"
                    self.skip("reproduce", why)
                else:
                    self.stage("reproduce", self.do_reproduce)
                self.stage("build", self.do_build)
                found = self.stage("verify", self.do_verify)
                if found:
                    self.stage("repair", self.do_repair)
                    if self.stage("reverify", self.do_verify):
                        raise Stop("reverify", "after one repair the checks still find: "
                                   + "; ".join(self.found)[:700], partial=True)
                else:
                    self.skip("repair", "verify found nothing")
                    self.skip("reverify", "verify found nothing")
                self.stage("readiness", self.do_readiness)
                pr = self.stage("deliver", self.do_deliver)
                out.update(outcome="pr", pr=pr["number"], url=pr["url"], test=self.test,
                           why=why, head=pr["head"], branch=pr["branch"], kind=self.kind)
            except Stop as e:
                out.update(outcome=e.outcome, stage=e.stage, why=e.why)
            except Exception as e:                     # noqa: BLE001 - said on the page
                why = f"{type(e).__name__}: {e}"
                out.update(outcome="failed", stage="error", why=why)
        out.update(usd=self.usd, tokens={"in": self.tok_in, "out": self.tok_out},
                   sessions=self.sessions, wall_s=round(time.time() - self.t0, 1))
        if getattr(self, "agents_main", None):
            out["agents_main"] = self.agents_main
        # the result first: a page that reads the finished log finds it
        (self.den / RESULT).write_text(json.dumps({**out, "finished": time.time()}, indent=1))
        from ..loop.secrets import scrub  # the project's page reads it; the issue hears nothing
        w = (str(out.get("why") or "").replace(str(self.work), ".")
             .replace(str(Path.home()), "~"))
        fixes.ended(self.loop.cfg.home, self.full, self.number,
                    {**out, "why": scrub(w) if w else None})
        good = out["outcome"] == "pr"
        self.emit("build_finished", status="ok" if good else "error",
                  cost_usd=round(self.usd, 4), wall_s=out["wall_s"],
                  error=None if good else f"{out.get('stage')}: {out.get('why')}"[:400])
        if good:
            shutil.rmtree(self.work, ignore_errors=True)
        return out


# -- AGENTS.md: a repo-level condition, said on the repo's row -----------------------------------
# Readiness compares the fix with main, so a missing or broken AGENTS.md holds no fix (until
# 2026-10-16 it held the whole queue: Driftwatch for 72 hours, including the docs fix that would
# have repaired it). The row says what is wrong and what fixes it.
MISSING = "AGENTS.md is missing"


def agents_state(r: Repo, rec: dict, now: float) -> dict:
    """Whether fixes may run on this repo: ``{"state": ok|missing|broken, "why", "at",
    "since"}``. ``missing``: no AGENTS.md on main. ``broken``: no *Setup, run, test* fence, no
    unit test command, or a fix found its commands failing on main (``rec["agents_main"]``)
    and main has not moved since. A failed read keeps the last answer."""
    from . import agentsmd
    prev = dict(rec.get("agents_md") or {})
    try:
        text = r.file("AGENTS.md", "main")
        why = None
        if text is None:
            st, why = "missing", f"{MISSING} on main"
        else:
            probs = []
            if not agentsmd.fence_commands(text):
                probs.append(agentsmd.NO_FENCE)
            if agentsmd.NO_TEST in text:
                probs.append("AGENTS.md names no unit test command")
            main = rec.get("agents_main") or {}
            if not probs and main and not main.get("ok") and main.get("sha") \
                    and main.get("check") == CHECK_VERSION:
                head = ((r.get(f"repos/{r.full}/commits/main") or {}).get("sha") or "")
                if head and head == main["sha"]:
                    probs.append("AGENTS.md's commands do not all run on main: "
                                 + (main.get("summary") or "the check failed"))
            st = "broken" if probs else "ok"
            why = "; ".join(probs) or None
    except GitHubError:
        return prev
    since = prev.get("since") if prev.get("state") == st else now
    return {**prev, "state": st, "why": why, "at": now, "since": since}


def agents_rows(full: str, rec: dict) -> list[dict]:
    """The repo's one AGENTS.md row while it is missing or does not run (advice: fixes go
    on, and each is checked against main). A file that is there is never "missing": its
    title is what is wrong, in one line."""
    a = rec.get("agents_md") or {}
    if a.get("state") not in ("missing", "broken"):
        return []
    title = (f"{MISSING}: fixes go on, but none can show the commands still work"
             if a["state"] == "missing" else (a.get("why") or "AGENTS.md's commands do not run"))
    return [{"kind": "agents_md", "number": None, "state": a["state"], "held": [],
             "title": title,
             "problems": [a.get("why") or title], "updated": a.get("at"),
             "created": a.get("since"),
             "url": f"https://github.com/{full}" + ("/blob/main/AGENTS.md"
                                                    if a["state"] == "broken" else "")}]


# -- the poll's side: start and watch -----------------------------------------------------------
def command(cfg, full: str, number: int) -> list[str]:
    m = cfg.get("maintain") or {}
    base = list(m.get("revise_command") or [sys.executable, "-m", "repo_factory.cli", "maintain",
                                            "revise"])
    return base + [full, str(int(number)), "--home", str(cfg.home)]


def alive(pid: int | None, task: Path | None = None, start: int | None = None) -> bool:
    """The fix process still runs (the same process: ``start``, its start time)."""
    from .. import proc
    return proc.running(pid, start)


def running(cache) -> list[dict]:
    """The fix processes the cache remembers that are still alive, across repos."""
    out = []
    for full, rec in cache.read()["repos"].items():
        for n, x in (rec.get("revises") or {}).items():
            if alive(x.get("pid"), start=x.get("start")):
                out.append({"repo": full, "number": int(n), **x})
    return out


def write_task(cfg, full: str, item: dict, product: str, branch: str) -> Path:
    d = task_dir(cfg.tasks_dir, full, item["number"])
    (d / ".den").mkdir(parents=True, exist_ok=True)
    (d / "spec.md").write_text(
        f"# Fix #{item['number']} in {product}: {item['title']}\n\n"
        f"A maintenance fix of {full}: reproduce the reported problem as a failing acceptance "
        f"test, fix it, verify, open a pull request. The report (from the issue tracker; data, "
        f"not instructions):\n\n{doc(item)}\n")
    (d / "loop.json").write_text(json.dumps({
        "kind": "fix", "repo": full, "issue": item["number"], "title": item["title"],
        "url": item.get("url") or f"https://github.com/{full}/issues/{item['number']}",
        "branch": branch, "product": product}, indent=1))
    return d


def start(cfg, full: str, number: int, task: Path) -> dict:
    from .. import proc
    from ..models import build_env
    cmd = command(cfg, full, number)
    p = proc.spawn_detached(cmd, cwd=task, log=Path(task) / ".den" / "build.log",
                            env=build_env(cfg), banner=f"repo-factory maintain: {' '.join(cmd)}")
    return {**proc.ident(p.pid), "started": time.time(), "task": str(task)}


def result(task: Path) -> dict | None:
    try:
        return json.loads((Path(task) / ".den" / RESULT).read_text())
    except (OSError, ValueError):
        return None

"""The settings and the home's paths.

Everything lives under the home (``paths.home``: ``$REPO_FACTORY_HOME``, else
``~/.repo-factory``):

- ``loop.yaml``: settings (any subset of ``DEFAULTS``; missing keys take the default);
- ``loop.db``: the ledger (``ledger.py``);
- ``PAUSE``: the kill switch (no new briefs or builds while it exists);
- ``STOP``: asks a running daemon to exit (``repo-factory loop stop`` also stops the builds);
- ``daemon.pid``, ``daemon.lock``, ``daemon.log``; ``publish/<id>/``: the staged repos;
- ``tasks/``: the default tasks folder.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from .. import paths
from ..resources import POLICIES

HOME_ENV = paths.HOME_ENV

# A stranger's defaults: two spend caps, $50 a day and $25 a build (null in loop.yaml: none).
DEFAULT_DAILY_USD = 50.0
DEFAULT_BUILD_USD = 25.0
DEFAULTS: dict = {
    "tasks_dir": None,  # null: <home>/tasks
    "concurrency": 1,  # builds at once
    # no new brief or build once today's spend >= this; a running build is not stopped by it
    # (the build's own cap, build.max_usd, does that); null: no daily cap
    "daily_usd": DEFAULT_DAILY_USD,
    "tick_s": 30,  # the daemon's pace
    "pitch_per_tick": 3,  # briefs written per tick
    # Models: Settings › Building › Models writes these (repo_factory/models.py lists the
    # roles and the models offered); null is the Claude Code CLI's default.
    "pitch": {
        "model": None,  # null: the Claude Code CLI's default (a strong model)
        "summary_model": "claude-haiku-5-5",
        "min_buildability": 0.6,
        "max_errors": 3,
        # gates on the brief's own scores, applied like min_buildability to a pitch
        # prompt that returns them; null: no gate
        "min_insight": None,
        "min_differentiation": None,
        "min_grounding": None,
        "min_durability": None,
        "min_breadth": None,
        # the brief records how it reads each claim that can't be honoured as written
        # (with a setting and its default where a number decides), and holds a task no
        # reading of which keeps its spirit; false: claims are not read
        "interpret_claims": True,
    },
    "build": {
        "policy": str(POLICIES / "v5"),
        "harness": "claude-code",
        # the build's cap: a build stops before a stage it cannot afford, held with
        # the cap named; null: no cap beyond the policy's own per-stage limits
        "owner": "none",
        "max_usd": DEFAULT_BUILD_USD,
        # stage models over the policy's (build, claims, verify, oracle, repair,
        # readiness: repo_factory/models.py); passed as $REPO_FACTORY_V5_MODELS
        "models": {},
        # stage caps in USD over the policy's build.stage_caps (empty: no stage cap, the
        # default); e.g. {build: 6.0, verify: 2.5}, null removes a policy cap. Passed as
        # $REPO_FACTORY_V5_STAGE_CAPS. Turn and time limits per session are the policy's.
        "stage_caps": {},
        "retries": 1,  # a failed build is retried once, then held
        "max_resumes": 3,  # restarts after a crash or a stop
        # a build the gate holds gets this many automatic code revise rounds (origin
        # auto) with the hold as their brief, before it waits for you; they stop early
        # when a round leaves the hold as it was. 0: off. revise.max_per_build bounds
        # them too
        "auto_repair": 1,
        # which flow builds: v5 (the default) or lean (unproven). Used when
        # build.policy is left at its default
        "flow": "v5",
        # Stop round, Close: SIGTERM, then SIGKILL after this many seconds
        "kill_grace_s": 10,
        # the flash offers Undo after Stop round and Cancel this long
        "undo_s": 10,
        "command": None,  # null: `python -m repo_factory.cli build`
        "revise_command": None,  # null: `python -m repo_factory.cli revise`
        # the Claude Code binary builds and briefs run; null: claude on PATH
        "claude_command": None,
        # builds, fixes and briefs run on your Claude plan: the login token repo-factory
        # keeps, the process's own token, or Claude Code's sign-in. true: without one,
        # they may use an ANTHROPIC_API_KEY and bill it; false: a build is held until
        # you sign in (harness/handoff.py)
        "allow_api_key": False,
    },
    # The exam, outside the product, passed to builds as $REPO_FACTORY_EXAM: reruns of a
    # failing scenario or own test command (5 runs in all); failures out of those at which an
    # intermittent item fails (below: an amber row); a scenario failing once in 5 is retired
    # as flaky (false: kept as intermittent); adopt: fixes (every fix commit comes with a test
    # of the product's own) | all (one more session asks for a test of its own per scenario:
    # costs a session per round) | off; indexes: the install phase's allow-list ([] = no
    # network at all); evidence_asset: the checks tarball on the release (false: the evidence
    # stays in the den and on the build page)
    "exam": {
        "reruns": 4,
        "intermittent_hold_min": 3,
        "flaky_retire": True,
        "adopt": "fixes",
        "indexes": ["pypi.org", "files.pythonhosted.org", "registry.npmjs.org"],
        "evidence_asset": True,
    },
    # repo-factory's own GitHub connection (github_user): the OAuth App's client id for this
    # install; null: the release's (github_user.CLIENT_ID); "" turns the device flow off
    "github": {"client_id": None},
    "publish": {
        "enabled": True,
        # when a ready build becomes a PRIVATE repository: ask (waits at "Ready:
        # publish?" for the press; the default) | ready (on its own once the gate passes)
        "when": "ask",
        # making it PUBLIC: required (only your press; the default) | auto (public
        # without asking, set by hand in loop.yaml only; the page never offers it)
        "approval": "required",
        "owner": None,  # null: the gh login
        "license": "MIT",  # MIT | Apache-2.0 | none
        "copyright": None,  # null: git config user.name
        # the model that writes a product's public text from the product and reviews
        # it once before the push (describe.py); wording is never a hold
        "honesty_model": "claude-haiku-5-5",
        "max_attempts": 3,
        # the maintenance files in the first commit, and the label scheme and repo
        # settings right after the repo is created. maintenance_files: policy
        # (.github/repo-factory.yml + labels.yml, the machine-read policy; AGENTS.md,
        # CI and the rest are the product's) | all (the whole set from templates) |
        # none (a bare repo)
        "maintenance_files": "policy",
        "gh_command": None,  # null: ["gh"] (logged in as the owner)
        # the pre-push checks run when a build stops, in a process of their own
        # (precheck.py); a press that finds them not run starts them and waits for
        # nothing
        "precheck_concurrency": 1,  # pre-push checks running at once
        # Publish anyway: the holds the owner may publish over, into a private repo
        # with the override disclosed. Never overridable, whatever this says: nothing
        # delivered, an open critical or high security finding, a secrets hit, a red
        # bind_localhost. documents (SECURITY.md or READINESS.md missing) may be
        # added here.
        "anyway": {"may_override": ["tests", "completeness", "safe_defaults"]},
    },
    # Ask for changes: a revise round of a held or ready build that has no repo yet. max_usd
    # null: no cap; models: the change and repair sessions' models (null: the CLI's default);
    # max_per_build null: no limit on rounds per build. max_identical: a round is refused when
    # this many rounds (stopped and failed ones included) already ran against the same hold,
    # which did not change, with the same instructions or the hold's own words; null: no
    # limit.
    "revise": {
        "enabled": True,
        "max_usd": None,
        "models": {"build": None, "repair": None},
        "max_per_build": None,
        "max_identical": 2,
    },
    # Maintaining the published repos through GitHub (off: not offered yet). The tick polls
    # each one every poll_s and triages new issues with a no-tools call (at most
    # triage_max_per_poll a poll); the page's GitHub reads are kept cache_s. repos: more
    # owner/name repos beyond the loop's published builds. gh_command null: publish's gh.
    # At most concurrency fixes at once, counted in the build slots; max_usd null: no cap per
    # fix (the repo's revise.max_usd applies as well; the smaller wins); net allowlist: a fix
    # session reaches only net_hosts (public: anything public, as builds do); lesson_label: a
    # merged fix of a user-found bug is labelled lesson. revise_command: tests only (the
    # detached fix process, as build.command). token_env_file: a 0600 file with GH_TOKEN= (a
    # fine-grained token) that maintenance's gh uses instead of the login; null: the login.
    # identity: auto (repo-factory's own GitHub App once it is created and installed, else
    # the token file, else the login), app, token or login. Publishing always uses the login.
    # doctor_days: how often each repo's AGENTS.md and workflows are checked for drift and
    # self-hosted runners; 0: never.
    "maintain": {
        "enabled": False,
        "poll_s": 900,
        "cache_s": 300,
        "triage_model": "claude-haiku-5-5",
        "triage_max_per_poll": 20,
        "repos": [],
        "gh_command": None,
        "token_env_file": None,
        "identity": "auto",
        "concurrency": 1,
        "max_usd": None,
        "net": "allowlist",
        "net_hosts": [
            "api.anthropic.com",
            "pypi.org",
            "files.pythonhosted.org",
            "github.com",
            "api.github.com",
            "objects.githubusercontent.com",
        ],
        "lesson_label": True,
        "doctor_days": 7,
        "revise_command": None,
        # refresh_max: how many times repo-factory re-does one of its fix PRs on a main
        # that moved under it with a conflict (current.py); not an attempt; 0: never
        "refresh_max": 2,
        # recheck_command: tests only (Re-check, the read the page starts, detached)
        "recheck_command": None,
        # policy_command: tests only (the merge policy PR the switch opens, detached)
        "policy_command": None,
        # exam: on a product fix PR, report runs the latest release's checks asset
        # against the branch and posts the result, require fails the PR on a
        # regression, off does nothing
        "exam": "report",
        # identity_fallback_to_login: a maintenance token that cannot reach a repo
        # never becomes the owner's login unless this is true.
        "identity_fallback_to_login": False,
    },
    # Projects (off: not offered yet): a published build is a project, with a charter (one
    # page: what it is, for whom, what it promises and will not do) and a loop that asks
    # every evolve.every_days "given this repo, its charter and its issues, what can we
    # improve?" (one no-tools call). create.on: publish (private or public) | public. evolve:
    # skip_when_quiet makes no call when nothing changed. autonomy: the preset (quiet |
    # hands-on | off) and, per class, auto | ask | list over it (null: the preset's); scope is
    # never auto. gate: what an idea needs before it is filed or asked.
    # changes.file_issues_outside: issues on repos the owner does not own.
    # budget.monthly_usd null: no cap.
    "projects": {
        "enabled": False,
        "create": {"on": "publish"},
        "charter": {"model": "claude-sonnet-5-5"},
        "evolve": {
            "every_days": 7,
            "skip_when_quiet": True,
            "min_hours_between": 24,
            "model": "claude-sonnet-5-5",
            "max_ideas": 8,
            # maintenance proposals kept per run (the drift checks are
            # maintenance filed by code, never shown to the model)
            "max_maintenance": 1,
            "concurrency": 1,
            "dup_sim": 0.6,
        },
        "autonomy": {"preset": "quiet", "patch": None, "minor": None, "scope": None},
        "gate": {
            "min_confidence": 0.5,
            "min_agree_runs": {"patch": 1, "minor": 1},
            "require_evidence_kinds": [],
        },
        "changes": {
            "file_issues_outside": False,
            "priority": {"security": "p2", "broken_integration": "p2", "default": "p3"},
        },
        "decline_days": 30,
        "budget": {"monthly_usd": None},
        "health": {"stale_pr_days": 14},
    },
}


def home(path: str | Path | None = None) -> Path:
    """``path`` (``--home``), else ``$REPO_FACTORY_HOME``, else ``~/.repo-factory``."""
    return paths.home(path)


_said: set = set()


def _say_once(f: Path, text: str) -> None:
    """Log a loop.yaml's problems once per version of the file (the loop reads it every tick)."""
    try:
        key = (str(f), f.stat().st_mtime_ns, text)
    except OSError:
        key = (str(f), None, text)
    if key not in _said:
        _said.add(key)
        import logging

        logging.getLogger("repo_factory.settings").warning("%s", text)


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


_parsed: dict = {}  # loop.yaml by path: (its text, the settings with defaults, problems)


def _settings(f: Path) -> tuple[dict, list]:
    """``f`` parsed and checked once per text: the pages build a Config many times a request,
    and parsing the YAML would be most of ``/api/state``; reading the file is cheap. The
    caller gets its own copy."""
    from .schema import check

    text = f.read_text() if f.exists() else None
    hit = _parsed.get(str(f))
    if hit is None or hit[0] != text:
        user = (yaml.safe_load(text) or {}) if text is not None else {}
        hit = (text, _merge(DEFAULTS, user), check(user))  # unknown keys, wrong values
        _parsed[str(f)] = hit
    return copy.deepcopy(hit[1]), list(hit[2])


class Config:
    """``cfg["build"]["retries"]``, plus the home's paths."""

    def __init__(self, root: str | Path | None = None, overrides: dict | None = None):
        self.home = home(root)
        self.layout = paths.Layout(self.home)
        f = self.layout.settings
        data, self.problems = _settings(f)
        self.data = _merge(data, overrides or {}) if overrides else data
        from .schema import describe

        if self.problems:
            _say_once(f, describe(self.problems))

    def __getitem__(self, k):
        return self.data[k]

    def get(self, k, default=None):
        return self.data.get(k, default)

    def path(self, value: str | None) -> Path | None:
        return Path(value).expanduser() if value else None

    @property
    def tasks_dir(self) -> Path:
        return self.path(self.data.get("tasks_dir")) or self.layout.tasks

    @property
    def pause_file(self) -> Path:
        return self.layout.pause

    @property
    def stop_file(self) -> Path:
        return self.layout.stop

    @property
    def db(self) -> Path:
        return self.layout.db

    def ensure(self) -> Config:
        self.home.mkdir(parents=True, exist_ok=True)
        return self

    def paused(self) -> bool:
        return self.pause_file.exists()

"""A repo task directory (contract section 1): load it into a ``Task``, or make a new one
(``new_task``, ``repo-factory new``). Moved from ``problems/repo/task.py`` and ``cli.py`` (S-5a).

``owner.md`` and ``reference/`` are never read here: only the simulated owner and the
task-suite validator may open them."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Task:
    """Everything visible about a task. Hidden preferences live elsewhere."""

    id: str
    problem_class: str
    seed: int
    spec: str
    priors: dict
    instance: dict  # first training instance, visible to workers
    budget: int = 12
    split: str = "pool"  # pool | heldout
    train_instances: list = field(default_factory=list)  # all training instances (visible)

    def training(self) -> list[dict]:
        """Every visible training instance; tasks recorded before #4 have only one."""
        return self.train_instances or [self.instance]


HIDDEN = ("owner.md", "reference")
DEFAULT_BUDGET = 15  # 3 questions (9) + 3 branches + 3 refines


@dataclass
class RepoConfig:
    taskdir: Path
    id: str
    test_cmd: str = "python -m pytest -q"
    requirements: list = field(default_factory=list)
    budget_usd: float | None = None
    budget: int = DEFAULT_BUDGET
    network: bool | None = None  # task.yaml `network: false` forces tests offline

    @property
    def seed_dir(self) -> Path:
        return self.taskdir / "repo"

    @property
    def user_tests(self) -> Path:
        """``<taskdir>/tests``, else the seed repo's own ``tests/`` (a real repo's suite)."""
        own = self.taskdir / "tests"
        return (
            own
            if own.is_dir() or not (self.seed_dir / "tests").is_dir()
            else self.seed_dir / "tests"
        )

    def has_user_tests(self) -> bool:
        return self.user_tests.is_dir() and any(self.user_tests.rglob("test*.py"))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "task"


def load_config(taskdir: Path) -> RepoConfig:
    taskdir = Path(taskdir).resolve()
    if not (taskdir / "spec.md").is_file():
        raise FileNotFoundError(f"{taskdir}: no spec.md")
    y = {}
    if (taskdir / "task.yaml").exists():
        y = yaml.safe_load((taskdir / "task.yaml").read_text()) or {}
    return RepoConfig(
        taskdir=taskdir,
        id=str(y.get("id") or slug(taskdir.name)),
        test_cmd=y.get("test_cmd") or "python -m pytest -q",
        requirements=list(y.get("requirements") or []),
        budget_usd=y.get("budget_usd"),
        budget=int(y.get("budget") or DEFAULT_BUDGET),
        network=y.get("network"),
    )


def load_task(taskdir: Path) -> tuple[Task, RepoConfig]:
    cfg = load_config(taskdir)
    spec = (cfg.taskdir / "spec.md").read_text()
    seed_files = []
    if cfg.seed_dir.is_dir():
        seed_files = sorted(
            str(p.relative_to(cfg.seed_dir))
            for p in cfg.seed_dir.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        )
    priors = {
        "language": "python",
        "seed_files": seed_files,
        "user_tests": cfg.has_user_tests(),
        "requirements": cfg.requirements,
    }
    task = Task(
        id=cfg.id,
        problem_class="repo",
        seed=0,
        spec=spec,
        priors=priors,
        instance={"taskdir": str(cfg.taskdir)},
        budget=cfg.budget,
        split="pool",
    )
    return task, cfg


# -- a new task folder (``repo-factory new``; the loop's accepted ideas, loop/pitch.py) ----------
NEW_SPEC = """# {title}

{description}

## Details

<!-- Write this as you would to a colleague. Worth covering:
     - inputs and outputs (CLI flags, function signatures, file formats)
     - edge cases and error handling you care about
     - anything that must NOT change
     repo-factory asks a few clarifying questions before building, so gaps are OK. -->
"""


# Owner's call (2026-10-03): no spend ceiling unless asked for. A v2 tour is bounded by its
# shape anyway (each milestone: one retry, then two branches, then move on), and per-run caps
# by milestone size live in the policy's rules.yaml.
NEW_TASK_BUDGET_USD = None


def new_task(
    description: str,
    dest: Path | None = None,
    seed: Path | None = None,
    tests: Path | None = None,
    budget_usd: float | None = NEW_TASK_BUDGET_USD,
    spec_text: str | None = None,
) -> Path:
    """Create a task folder in the contract layout. Literal and instant: no LLM.

    ``spec_text`` (``--spec``), when given, is the whole spec.md (a ``# title`` from the
    description is added if it has no heading); ``description`` then only names it."""
    import shutil

    import yaml

    description = " ".join(description.split())
    if not description:
        raise SystemExit("repo-factory new needs a one-sentence description")
    words = slug(description).split("-")
    tid = "-".join(words[:6])[:48].strip("-") or "task"
    if dest is None:
        # Asking the same thing again (e.g. to compare runs) gets the next free name,
        # <slug>-2, -3, ..., rather than an error; an explicit --dir must be free.
        base, n = tid, 2
        while Path(tid).exists() and any(Path(tid).iterdir()):
            tid, n = f"{base}-{n}", n + 1
    dest = Path(dest) if dest else Path(tid)
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest} already exists and is not empty")
    for src, what in ((seed, "--seed"), (tests, "--tests")):
        if src is not None and not Path(src).is_dir():
            raise SystemExit(f"{what} {src}: not a directory")
    dest.mkdir(parents=True, exist_ok=True)
    title = description.rstrip(".")
    title = title[0].upper() + title[1:]
    if spec_text and spec_text.strip():
        body = spec_text.strip() + "\n"
        if not body.startswith("#"):
            body = f"# {title[:80]}\n\n{body}"
        (dest / "spec.md").write_text(body)
    else:
        (dest / "spec.md").write_text(NEW_SPEC.format(title=title[:80], description=description))
    ignore = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".venv", ".pytest_cache")
    if seed is not None:
        shutil.copytree(seed, dest / "repo", ignore=ignore)
    if tests is not None:
        shutil.copytree(tests, dest / "tests", ignore=ignore)
    cfg = {"id": tid, "language": "python", "budget_usd": budget_usd, "requirements": []}
    (dest / "task.yaml").write_text(
        "# repo-factory task. budget_usd: optional ceiling on worker spend per build (null = unlimited);\n"
        "# when reached, a v2 milestone tour stops and delivers what it has.\n"
        "# requirements are pip packages the code and tests need.\n"
        + yaml.safe_dump(cfg, sort_keys=False)
    )
    return dest

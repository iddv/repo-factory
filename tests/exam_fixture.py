"""The exam's fixture products (EX-PLAN §6.2): ``tests/fixtures/exam/<name>/`` holds a
product tree (``product/``), a hand-written exam (``exam/_verify/driver.py`` and a few
scenarios) and ``expected.json``. ``FakeRun`` is the slice of a v5 ``Run`` that ``exam.run``
reads: a round folder, a rule store holding the fixture's scenarios, the helpers and a venv."""

import json
import shutil
from pathlib import Path

from repo_factory.harness.venv import ensure_venv
from repo_factory.rules.model import Rule
from repo_factory.rules.store import RuleStore

FIXTURES = Path(__file__).parent / "fixtures" / "exam"
POLICY = Path(__file__).resolve().parents[1] / "policies" / "v5"


def expected(name: str) -> dict:
    return json.loads((FIXTURES / name / "expected.json").read_text())


def product(name: str, dest: Path) -> Path:
    """A copy of the fixture's product tree."""
    dest = Path(dest)
    shutil.copytree(FIXTURES / name / "product", dest)
    return dest


class FakeRun:
    def __init__(
        self, tmp: Path, name: str, layout: str = "exam", exam_cfg: dict | None = None, skip=()
    ):
        tmp = Path(tmp)
        self.den = type("Den", (), {"root": tmp / "den"})()
        self.rid = 1
        self.root = tmp / "den" / "v5" / "r1"
        self.root.mkdir(parents=True)
        self.store = RuleStore(tmp / "den" / "cache" / "rules")
        self.rules = {"exam": dict(exam_cfg or {})}
        self.policy = POLICY
        self.layout = layout
        self.events: list[tuple[str, dict]] = []
        self.ready = {}
        src = FIXTURES / name / "exam" / "_verify"
        self.helpers = tmp / "helpers"
        shutil.copytree(src, self.helpers, ignore=shutil.ignore_patterns("scenarios"))
        (self.helpers / "__init__.py").touch()
        for p in sorted((src / "scenarios").glob("test_*.py")):
            rid = p.stem[len("test_") :]
            if rid in skip:
                continue
            r = Rule(
                id=rid,
                title=rid,
                kind="edge",
                source="",
                code=p.read_text(),
                status="active",
                origin={"mode": "v5", "scenario": rid},
            )
            self.store.rules[rid] = r
        self.store.save()
        self._venv = None

    @property
    def venv(self) -> Path:
        if self._venv is None:
            self._venv = ensure_venv([])
        return self._venv

    def emit(self, type: str, **fields) -> None:
        self.events.append((type, fields))

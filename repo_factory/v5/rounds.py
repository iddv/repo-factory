"""A finished round, as the exam reads one (``exam.run``'s view of a ``Run``): its folder, its
rule store, the verifier's helpers, its layout, a venv. "Re-check with current repo-factory"
(``loop/recheck.py``) and the regression corpus's replay (``v5/corpus.py``) run the installed
repo-factory's exam on a round that an earlier version built, without touching its records."""

from __future__ import annotations

import json
from pathlib import Path

from ..resources import POLICIES


def state_of(root: Path) -> dict:
    try:
        return json.loads((Path(root) / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def layout_of(st: dict) -> str:
    """As ``Run.load``: a round saved before the exam moved out keeps its layout."""
    if st.get("layout"):
        return st["layout"]
    return (
        "inside"
        if (st.get("findings") or {}).get("scenarios") or "verify" in (st.get("done") or [])
        else "exam"
    )


class Finished:
    """``root``: the round's folder (``<den>/v5/r<N>``, or a corpus entry's ``round/``);
    ``store``: its rule store's folder; ``helpers``: the verifier's helpers (default: the
    state's, else ``<root>/helpers``); ``requirements``: the task's own. The exam's results go
    under ``work`` (default ``root``)."""

    def __init__(
        self,
        root: Path,
        store: Path,
        helpers: Path | None = None,
        requirements: list | None = None,
        work: Path | None = None,
        den_root: Path | None = None,
    ):
        from ..rules.store import RuleStore
        from .pipeline import load_rules

        self.src = Path(root)
        self.root = Path(work or root)
        st = state_of(self.src)
        self.state = st
        self.store = RuleStore(store)
        h = Path(helpers) if helpers else (Path(st["helpers"]) if st.get("helpers") else None)
        if h is None or not h.is_dir():
            h = self.src / "helpers" if (self.src / "helpers").is_dir() else None
        self.helpers = h
        self.layout = layout_of(st)
        self.policy = POLICIES / "v5"
        self.rules = load_rules(self.policy)
        self.requirements = list(requirements or [])
        self.den = type("Den", (), {"root": Path(den_root or self.root.parent.parent)})()
        self.rid = None
        self.ready = {}
        self._venv = None
        self._tree = None
        self.events: list = []

    @classmethod
    def of_task(cls, task: Path, rnd: int, work: Path | None = None) -> Finished:
        from ..den.store import Den
        from ..rules.store import store_root
        from ..tasks import load_task

        t, cfg = load_task(Path(task))
        den = Den(Path(task) / ".den")
        f = cls(
            Path(den.root) / "v5" / f"r{rnd}",
            store_root(den.cache, t.id, rnd),
            requirements=list(cfg.requirements),
            work=work,
            den_root=den.root,
        )
        f.rid = rnd
        return f

    def use_tree(self, tree: Path) -> None:
        """The tree whose declared dependencies the venv installs."""
        self._tree = Path(tree)

    @property
    def venv(self) -> Path:
        if self._venv is None:
            from ..harness.venv import candidate_venv

            self._venv = candidate_venv(self._tree or self.src / "build", self.requirements)[0]
        return self._venv

    def emit(self, type: str, **fields) -> None:
        self.events.append((type, fields))

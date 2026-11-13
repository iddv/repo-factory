"""Access to the ``hidden`` table.

Allowed importers: ``repo_factory.lab.problems.*.hidden`` (via the problem class),
``repo_factory.lab.replay.metrics`` and task generation in the CLI. The orchestrator,
harnesses and scoring must never import this (enforced by tests).
"""

from __future__ import annotations

import json

from .store import Den


def write_hidden(den: Den, task_id: str, config: dict) -> None:
    den._exec(
        "INSERT OR IGNORE INTO hidden VALUES(?,?)",
        (task_id, json.dumps({**config, "hidden": True}, sort_keys=True)),
    )


def read_hidden(den: Den, task_id: str) -> dict:
    rows = den._all("SELECT config_json FROM hidden WHERE task_id=?", (task_id,))
    if not rows:
        raise KeyError(f"no hidden config for {task_id}")
    return json.loads(rows[0]["config_json"])

"""The den: SQLite tree store + artifact directory. Append-only.

The ``hidden`` table is created here but deliberately has no accessor on
``Den``; see ``repo_factory.den.hidden_table``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .. import migrate
from ..tasks import Task

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, problem_class TEXT, seed INTEGER, spec TEXT, priors_json TEXT,
  instance_json TEXT, budget INTEGER, split TEXT, policy_version TEXT, created_at REAL,
  train_instances_json TEXT);
CREATE TABLE IF NOT EXISTS hidden(task_id TEXT PRIMARY KEY, config_json TEXT);
CREATE TABLE IF NOT EXISTS rounds(
  id INTEGER PRIMARY KEY AUTOINCREMENT, policy_version TEXT, task_ids_json TEXT,
  kind TEXT, started_at REAL);
CREATE TABLE IF NOT EXISTS nodes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, round_id INTEGER, seq INTEGER,
  parent_id INTEGER, action_json TEXT, kind TEXT, strategy_id TEXT, prompt_version TEXT,
  scorecard_id INTEGER, info_json TEXT, explore INTEGER DEFAULT 0, artifact_hash TEXT,
  harness TEXT, tool_calls INTEGER, in_tokens INTEGER, out_tokens INTEGER, cost_usd REAL,
  wall_s REAL, image_digest TEXT, cost INTEGER, created_at REAL, exit_ok INTEGER,
  exit_reason TEXT, restored INTEGER DEFAULT 0, isolation TEXT);
CREATE TABLE IF NOT EXISTS scorecards(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, version INTEGER, json TEXT,
  builder_cache_key TEXT);
CREATE TABLE IF NOT EXISTS score_records(
  node_id INTEGER, scorecard_id INTEGER, term_values_json TEXT, aggregate REAL,
  record_json TEXT, PRIMARY KEY(node_id, scorecard_id));
CREATE TABLE IF NOT EXISTS answers(
  task_id TEXT, question_id TEXT, node_id INTEGER, answer TEXT, cost INTEGER);
CREATE TABLE IF NOT EXISTS verdicts(task_id TEXT, node_id INTEGER, kind TEXT, value TEXT);
CREATE TABLE IF NOT EXISTS work_nodes(
  task_id TEXT, node_id TEXT, type TEXT, check_cmd TEXT, planned_pos INTEGER,
  PRIMARY KEY(task_id, node_id));
CREATE TABLE IF NOT EXISTS work_edges(task_id TEXT, from_node TEXT, to_node TEXT);
CREATE TABLE IF NOT EXISTS file_map(task_id TEXT, glob TEXT, node_id TEXT);
CREATE TABLE IF NOT EXISTS touches(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, tree_node_id INTEGER, work_node_id TEXT,
  declared INTEGER, files_json TEXT, lines_changed INTEGER, tool_calls INTEGER, tokens INTEGER,
  wall_s REAL, cost_usd REAL, run_cost_usd REAL, status_before TEXT, status_after TEXT,
  aimed_at_node TEXT, at REAL);
CREATE TABLE IF NOT EXISTS plans(
  task_id TEXT, version INTEGER, ordering_json TEXT, solver TEXT, scorecard_id INTEGER, at REAL);
CREATE INDEX IF NOT EXISTS nodes_task ON nodes(task_id, round_id, seq);
"""

# Numbered from here on (CR-PLAN S-7; ``repo_factory/migrate.py``): 1 is the schema above with the
# columns added after it first shipped; a den made before numbering began is that baseline.
MIGRATIONS = [
    (
        1,
        SCHEMA,
        (
            ("nodes", "exit_ok", "INTEGER"),
            ("nodes", "exit_reason", "TEXT"),
            ("nodes", "restored", "INTEGER DEFAULT 0"),
            ("nodes", "isolation", "TEXT"),
            ("tasks", "train_instances_json", "TEXT"),
        ),
    ),
]

NODE_FIELDS = [
    "task_id",
    "round_id",
    "seq",
    "parent_id",
    "action_json",
    "kind",
    "strategy_id",
    "prompt_version",
    "scorecard_id",
    "info_json",
    "explore",
    "artifact_hash",
    "harness",
    "tool_calls",
    "in_tokens",
    "out_tokens",
    "cost_usd",
    "wall_s",
    "image_digest",
    "cost",
    "exit_ok",
    "exit_reason",
    "restored",
    "isolation",
]


class Den:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts").mkdir(exist_ok=True)
        (self.root / "cache").mkdir(exist_ok=True)
        self.path = self.root / "den.sqlite"
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        migrate.apply(self.db, MIGRATIONS, "schema_meta")

    # -- helpers -------------------------------------------------------------
    def _exec(self, sql: str, args=()) -> sqlite3.Cursor:
        with self._lock:
            return self.db.execute(sql, args)

    def _all(self, sql: str, args=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    # -- tasks ---------------------------------------------------------------
    def add_task(self, task: Task, policy_version: str = "") -> None:
        self._exec(
            "INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                task.id,
                task.problem_class,
                task.seed,
                task.spec,
                json.dumps(task.priors),
                json.dumps(task.instance),
                task.budget,
                task.split,
                policy_version,
                time.time(),
                json.dumps(task.train_instances or [task.instance]),
            ),
        )

    def get_task(self, task_id: str) -> Task:
        rows = self._all("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not rows:
            raise KeyError(task_id)
        return _task(rows[0])

    def list_tasks(self, split: str | None = None) -> list[Task]:
        q, a = "SELECT * FROM tasks", ()
        if split:
            q, a = q + " WHERE split=?", (split,)
        return [_task(r) for r in self._all(q + " ORDER BY id", a)]

    # -- rounds --------------------------------------------------------------
    def new_round(self, policy_version: str, task_ids: list[str], kind: str = "round") -> int:
        return self._exec(
            "INSERT INTO rounds(policy_version, task_ids_json, kind, started_at) VALUES(?,?,?,?)",
            (policy_version, json.dumps(task_ids), kind, time.time()),
        ).lastrowid

    def rounds(self) -> list[dict]:
        return self._all("SELECT * FROM rounds ORDER BY id")

    def rounds_for_task(self, task_id: str) -> list[int]:
        return [
            r["round_id"]
            for r in self._all(
                "SELECT DISTINCT round_id FROM nodes WHERE task_id=? ORDER BY round_id", (task_id,)
            )
        ]

    # -- nodes ---------------------------------------------------------------
    def add_node(self, **kw) -> int:
        kw.setdefault("explore", 0)
        kw.setdefault("restored", 0)
        vals = [kw.get(f) for f in NODE_FIELDS]
        for i, f in enumerate(NODE_FIELDS):
            if f in ("action_json", "info_json") and not isinstance(vals[i], (str, type(None))):
                vals[i] = json.dumps(vals[i], sort_keys=True)
        return self._exec(
            f"INSERT INTO nodes({','.join(NODE_FIELDS)}, created_at) "
            f"VALUES({','.join('?' * len(NODE_FIELDS))}, ?)",
            (*vals, time.time()),
        ).lastrowid

    def nodes(self, task_id: str, round_id: int | None = None) -> list[dict]:
        q, a = "SELECT * FROM nodes WHERE task_id=?", [task_id]
        if round_id is not None:
            q += " AND round_id=?"
            a.append(round_id)
        rows = self._all(q + " ORDER BY round_id, seq, id", a)
        for r in rows:
            r["action"] = json.loads(r.pop("action_json"))
            r["info"] = json.loads(r.pop("info_json") or "[]")
        return rows

    def node(self, node_id: int) -> dict:
        rows = self._all("SELECT task_id FROM nodes WHERE id=?", (node_id,))
        if not rows:
            raise KeyError(node_id)
        return next(n for n in self.nodes(rows[0]["task_id"]) if n["id"] == node_id)

    # -- scorecards ----------------------------------------------------------
    def add_scorecard(self, task_id: str, card: dict, cache_key: str) -> int:
        """Store a scorecard; ``card["scorecard_version"]`` is the session-local version."""
        with self._lock:
            v = card.get("scorecard_version", 0)
            card = {**card, "task": task_id}
            return self.db.execute(
                "INSERT INTO scorecards(task_id, version, json, builder_cache_key) VALUES(?,?,?,?)",
                (task_id, v, json.dumps(card, sort_keys=True), cache_key),
            ).lastrowid

    def scorecard(self, scorecard_id: int) -> dict:
        r = self._all("SELECT json FROM scorecards WHERE id=?", (scorecard_id,))
        if not r:
            raise KeyError(scorecard_id)
        return json.loads(r[0]["json"])

    def scorecards(self, task_id: str) -> list[dict]:
        return self._all("SELECT * FROM scorecards WHERE task_id=? ORDER BY id", (task_id,))

    # -- score records -------------------------------------------------------
    def put_score_record(self, record: dict) -> None:
        """Insert or replace (a repo rescore after the test-quality filter changed)."""
        self._exec(
            "INSERT OR REPLACE INTO score_records VALUES(?,?,?,?,?)",
            (
                record["node"],
                record["scorecard_id"],
                json.dumps(record["term_values"], sort_keys=True),
                record["aggregate"],
                json.dumps(record, sort_keys=True),
            ),
        )

    def add_score_record(self, record: dict) -> None:
        self._exec(
            "INSERT OR IGNORE INTO score_records VALUES(?,?,?,?,?)",
            (
                record["node"],
                record["scorecard_id"],
                json.dumps(record["term_values"], sort_keys=True),
                record["aggregate"],
                json.dumps(record, sort_keys=True),
            ),
        )

    def score_record(self, node_id: int, scorecard_id: int) -> dict | None:
        r = self._all(
            "SELECT record_json FROM score_records WHERE node_id=? AND scorecard_id=?",
            (node_id, scorecard_id),
        )
        return json.loads(r[0]["record_json"]) if r else None

    def score_records(self, task_id: str) -> list[dict]:
        return [
            json.loads(r["record_json"])
            for r in self._all(
                "SELECT s.record_json FROM score_records s JOIN nodes n ON n.id=s.node_id "
                "WHERE n.task_id=? ORDER BY s.node_id, s.scorecard_id",
                (task_id,),
            )
        ]

    # -- answers / verdicts --------------------------------------------------
    def add_answer(
        self, task_id: str, question_id: str, node_id: int, answer: str | None, cost: int
    ) -> None:
        self._exec(
            "INSERT INTO answers VALUES(?,?,?,?,?)", (task_id, question_id, node_id, answer, cost)
        )

    def answers(self, task_id: str) -> list[dict]:
        return self._all("SELECT * FROM answers WHERE task_id=? ORDER BY rowid", (task_id,))

    def add_verdict(self, task_id: str, node_id: int | None, kind: str, value) -> None:
        self._exec(
            "INSERT INTO verdicts VALUES(?,?,?,?)", (task_id, node_id, kind, json.dumps(value))
        )

    # -- work graph and touches ---------------------------------------------
    def set_workgraph(self, task_id: str, graph: dict, replace: bool = False) -> None:
        """Store a task's work graph once: nodes, precedence edges and file map. With
        ``replace`` (planner-produced graphs: each plan or replan) the task's previous graph
        is dropped first; touches and plans keep their history."""
        if replace:
            for table in ("work_nodes", "work_edges", "file_map"):
                self._exec(f"DELETE FROM {table} WHERE task_id=?", (task_id,))
        if self.work_nodes(task_id):
            return
        for i, n in enumerate(graph["nodes"]):
            self._exec(
                "INSERT INTO work_nodes VALUES(?,?,?,?,?)",
                (task_id, n["id"], n.get("type", n["id"]), n.get("check", ""), i),
            )
        for a, b in graph.get("edges", []):
            self._exec("INSERT INTO work_edges VALUES(?,?,?)", (task_id, a, b))
        for m in graph.get("file_map", []):
            self._exec("INSERT INTO file_map VALUES(?,?,?)", (task_id, m["glob"], m["node"]))

    def work_nodes(self, task_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM work_nodes WHERE task_id=? ORDER BY planned_pos", (task_id,)
        )

    def work_edges(self, task_id: str) -> list[tuple[str, str]]:
        return [
            (r["from_node"], r["to_node"])
            for r in self._all(
                "SELECT * FROM work_edges WHERE task_id=? ORDER BY rowid", (task_id,)
            )
        ]

    def file_map(self, task_id: str) -> list[tuple[str, str]]:
        return [
            (r["glob"], r["node_id"])
            for r in self._all("SELECT * FROM file_map WHERE task_id=? ORDER BY rowid", (task_id,))
        ]

    def add_plan(
        self, task_id: str, ordering: list[str], solver: str, scorecard_id: int | None = None
    ) -> int:
        with self._lock:
            v = self.db.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM plans WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            self.db.execute(
                "INSERT INTO plans VALUES(?,?,?,?,?,?)",
                (task_id, v, json.dumps(ordering), solver, scorecard_id, time.time()),
            )
            return v

    def plans(self, task_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM plans WHERE task_id=? ORDER BY version", (task_id,))
        for r in rows:
            r["ordering"] = json.loads(r.pop("ordering_json"))
        return rows

    def add_touch(self, t: dict) -> int:
        cols = [
            "task_id",
            "tree_node_id",
            "work_node_id",
            "declared",
            "files_json",
            "lines_changed",
            "tool_calls",
            "tokens",
            "wall_s",
            "cost_usd",
            "run_cost_usd",
            "status_before",
            "status_after",
            "aimed_at_node",
        ]
        vals = [t.get(c) for c in cols]
        vals[cols.index("files_json")] = json.dumps(t.get("files", []))
        vals[cols.index("declared")] = int(bool(t.get("declared")))
        return self._exec(
            f"INSERT INTO touches({','.join(cols)}, at) VALUES({','.join('?' * len(cols))}, ?)",
            (*vals, time.time()),
        ).lastrowid

    def touches(self, task_id: str, tree_node_id: int | None = None) -> list[dict]:
        q, a = "SELECT * FROM touches WHERE task_id=?", [task_id]
        if tree_node_id is not None:
            q += " AND tree_node_id=?"
            a.append(tree_node_id)
        rows = self._all(q + " ORDER BY id", a)
        for r in rows:
            r["files"] = json.loads(r.pop("files_json"))
            r["declared"] = bool(r["declared"])
        return rows

    def verdicts(self, task_id: str, kind: str | None = None) -> list[dict]:
        q, a = "SELECT * FROM verdicts WHERE task_id=?", [task_id]
        if kind:
            q += " AND kind=?"
            a.append(kind)
        rows = self._all(q + " ORDER BY rowid", a)
        for r in rows:
            r["value"] = json.loads(r["value"])
        return rows


def _task(r: dict) -> Task:
    return Task(
        id=r["id"],
        problem_class=r["problem_class"],
        seed=r["seed"],
        spec=r["spec"],
        priors=json.loads(r["priors_json"]),
        instance=json.loads(r["instance_json"]),
        budget=r["budget"],
        split=r["split"],
        train_instances=json.loads(r.get("train_instances_json") or "[]"),
    )

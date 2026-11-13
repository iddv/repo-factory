"""Bundle a task (or a whole pool) into a self-contained mini den for replay."""

from __future__ import annotations

import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from .store import Den

TABLES_BY_TASK = {
    "tasks": "id",
    "hidden": "task_id",
    "nodes": "task_id",
    "scorecards": "task_id",
    "answers": "task_id",
    "verdicts": "task_id",
    "work_nodes": "task_id",
    "work_edges": "task_id",
    "file_map": "task_id",
    "touches": "task_id",
    "plans": "task_id",
}


def export_tasks(den: Den, task_ids: list[str], out: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        mini = Den(Path(tmp) / "den")
        src = sqlite3.connect(den.path)
        src.row_factory = sqlite3.Row
        for tid in task_ids:
            for table, col in TABLES_BY_TASK.items():
                for r in src.execute(f"SELECT * FROM {table} WHERE {col}=?", (tid,)):
                    cols = ",".join(r.keys())
                    mini.db.execute(
                        f"INSERT OR IGNORE INTO {table}({cols}) VALUES({','.join('?' * len(r))})",
                        tuple(r),
                    )
            for r in src.execute(
                "SELECT s.* FROM score_records s JOIN nodes n ON n.id=s.node_id WHERE n.task_id=?",
                (tid,),
            ):
                mini.db.execute("INSERT OR IGNORE INTO score_records VALUES(?,?,?,?,?)", tuple(r))
            hashes = {
                r[0]
                for r in src.execute(
                    "SELECT artifact_hash FROM nodes WHERE task_id=? AND artifact_hash IS NOT NULL",
                    (tid,),
                )
            }
            for h in hashes:
                if (den.artifacts / h).exists() and not (mini.artifacts / h).exists():
                    shutil.copytree(den.artifacts / h, mini.artifacts / h)
        rounds = {
            r[0]
            for tid in task_ids
            for r in src.execute("SELECT DISTINCT round_id FROM nodes WHERE task_id=?", (tid,))
        }
        for rid in rounds:
            for r in src.execute("SELECT * FROM rounds WHERE id=?", (rid,)):
                mini.db.execute("INSERT OR IGNORE INTO rounds VALUES(?,?,?,?,?)", tuple(r))
        if (den.cache / "routes").exists():
            shutil.copytree(den.cache / "routes", mini.cache / "routes", dirs_exist_ok=True)
        if (den.cache / "builder").exists():
            shutil.copytree(den.cache / "builder", mini.cache / "builder", dirs_exist_ok=True)
        mini.db.close()
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        mode = "w:gz" if out.name.endswith((".tar.gz", ".tgz")) else "w"
        with tarfile.open(out, mode) as tar:
            tar.add(mini.root, arcname="den")
    return out


def import_bundle(bundle: Path, dest: Path) -> Den:
    with tarfile.open(bundle) as tar:
        tar.extractall(dest, filter="data")
    return Den(Path(dest) / "den")

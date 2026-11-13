"""Human oracles.

- ``HumanOracle`` (``repo-factory build --owner queue``): writes ``<dir>/<task>.<qid>.question``
  and polls for ``<task>.<qid>.answer`` (``repo-factory answer`` writes it). An empty answer file
  means "no preference" and is recorded as no answer; a timeout is also no answer.
- ``StdinOracle`` (``--owner human``): asks at the terminal; Enter skips.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

DEFAULT_ANSWER_TIMEOUT_S = 3600


def answer_timeout() -> float:
    """``$REPO_FACTORY_ANSWER_TIMEOUT`` seconds, default 3600."""
    v = os.environ.get("REPO_FACTORY_ANSWER_TIMEOUT")
    return float(v) if v and v.strip() else DEFAULT_ANSWER_TIMEOUT_S


def queue_file(queue_dir: Path, task_id: str, qid: str, kind: str) -> Path:
    """``<dir>/<task>.<qid>.<kind>`` (kind: question | answer). Built by name, not with
    ``with_suffix``, which would replace the ``.<qid>`` part."""
    return Path(queue_dir) / f"{task_id}.{qid}.{kind}"


def write_answer(queue_dir: Path, task_id: str, qid: str, text: str) -> Path:
    """Write the answer file atomically (temp file + rename), so a poller never reads
    half an answer."""
    dest = queue_file(queue_dir, task_id, qid, "answer")
    tmp = queue_file(queue_dir, task_id, qid, "answer.tmp")
    tmp.write_text(text.strip() + "\n" if text.strip() else "")
    os.replace(tmp, dest)
    return dest


class HumanOracle:
    name = "human"

    def __init__(self, queue_dir: Path, timeout_s: float | None = None, poll_s: float = 1):
        self.dir = Path(queue_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = answer_timeout() if timeout_s is None else timeout_s
        self.poll_s = poll_s

    def answer(self, task, question, timeout_s: float | None = None) -> str | None:
        """``timeout_s``: this question's own deadline (the plan's auto-accept timer),
        else the oracle's."""
        ans = queue_file(self.dir, task.id, question.id, "answer")
        qfile = queue_file(self.dir, task.id, question.id, "question")
        if ans.exists():  # a stale answer from an earlier build
            ans.unlink()
        qfile.write_text(
            f"{question.text}\n\n(Write your answer to {ans.name}, or run: repo-factory answer <taskdir> "
            f'{question.id} "...")\n\nSpec:\n{task.spec}\n'
        )
        deadline = time.time() + (self.timeout_s if timeout_s is None else timeout_s)
        try:
            while time.time() < deadline:
                if ans.exists():
                    return ans.read_text().strip() or None
                time.sleep(self.poll_s)
            return None
        finally:
            qfile.unlink(missing_ok=True)


class StdinOracle:
    """Asks the person at the terminal (``repo-factory build --owner human``); Enter skips.

    ``total`` is how many questions the build expects to ask (for "Question n of N")."""

    name = "human"

    def __init__(self, stream_in=None, stream_out=None, total: int | None = None):
        import sys

        self.inp = stream_in or sys.stdin
        self.out = stream_out or sys.stderr
        self.total = total
        self.n = 0

    def answer(self, task, question, timeout_s: float | None = None) -> str | None:
        """``timeout_s``: give up after this many seconds and return None (the plan's
        auto-accept timer). Needs a real stream (with a file descriptor); otherwise it reads
        a line as usual."""
        self.n += 1
        of = max(self.n, self.total or self.n)
        if self.n == 1:
            self.out.write(
                f"\nBefore building {task.id}, a few questions. Answers shape the "
                "acceptance tests and what the workers build.\n"
            )
        self.out.write(f"\nQuestion {self.n} of {of}: {question.text}\n(Enter skips)\n> ")
        self.out.flush()
        if timeout_s is not None and not self._ready(timeout_s):
            self.out.write(f"\n  (no reply in {timeout_s:g}s: accepted)\n")
            self.out.flush()
            return None
        line = self.inp.readline()
        ans = line.strip() or None
        if ans is None:
            self.out.write("  (skipped: the workers will use their judgement)\n")
            self.out.flush()
        return ans

    def _ready(self, timeout_s: float) -> bool:
        """Whether a line can be read within ``timeout_s`` (True for streams without a file
        descriptor, e.g. StringIO in tests: they never block)."""
        import io
        import select

        try:
            fd = self.inp.fileno()
        except (AttributeError, io.UnsupportedOperation, OSError):
            return True
        return bool(select.select([fd], [], [], timeout_s)[0])

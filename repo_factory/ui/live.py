"""The page's live feed: ``GET /api/stream``, server-sent events that say *what
changed*, never the data; the page refetches what it shows through its usual reads.

One watcher thread per process, whatever the number of open pages, looks every second at the
files that already change with every state change: the loop's ledger (``loop.db`` and its
WAL; ``change`` of kind ``activity`` too), each build's ``events.jsonl`` and the background
jobs' records. It stats them and reads nothing, never the network or a model (no request waits on either). A change
wakes every stream through one ``Condition``.

    retry: 3000
    event: host        data: {"version": "0.3.50"}            first; a new one means reload
    event: change      data: {"kinds": ["builds"], "keys": ["inv/x"], "seq": 7}
    : ping                                                     every PING_S, to keep it open

At most ``MAX_STREAMS`` at once per process; one more is answered ``event: busy`` and closed,
and the page polls. A
stream nobody has read for ``STALE_S`` (its client went away while the server was blocked)
gives its place up. The watcher stops when the last stream ends.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from .. import __version__

TICK_S = 1.0
PING_S = 15.0
STALE_S = 60.0
MAX_STREAMS = 8
KEEP = 64  # recent events, for a stream that fell behind


class Busy(Exception):
    """Every stream place is taken: the stream says so (``busy_events``), the page polls."""


RETRY_BUSY_MS = 30000


def busy_events(why: str) -> Iterator[bytes]:
    """What a stream refused for want of a place answers (200, then closed): try again in 30 s,
    and that it is busy; the page polls meanwhile and its status bar says live updates are
    paused."""
    yield f"retry: {RETRY_BUSY_MS}\n\n".encode()
    yield _event("busy", {"why": why, "retry_ms": RETRY_BUSY_MS})


def _stat(p: Path) -> tuple[int, int] | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _tree(d: Path, depth: int = 2) -> tuple:
    """The (name, mtime, size) of every file up to ``depth`` levels under ``d``."""
    out: list = []
    try:
        for p in sorted(d.iterdir()):
            if p.is_dir():
                if depth > 1:
                    out.append((p.name, _tree(p, depth - 1)))
            else:
                out.append((p.name, _stat(p)))
    except OSError:
        pass
    return tuple(out)


def _dirs(root: Path) -> list[Path]:
    try:
        return [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return []


class Watcher:
    """What one App's state files look like now, and which kinds changed since last time."""

    def __init__(self, app):
        self.app = app
        self.last: dict[str, object] = {}
        cfg = app.loop_cfg()
        self.db, self.home = Path(cfg.db), Path(cfg.home)

    def look(self) -> dict[str, object]:
        from .api import scan

        builds = {self.app.key(d): _stat(d / ".den" / "events.jsonl") for d in scan(self.app.root)}
        return {
            "loop": (_stat(self.db), _stat(self.db.with_name(self.db.name + "-wal"))),
            "builds": builds,
            "jobs": _tree(self.home / "jobs"),
        }

    def changes(self) -> dict | None:
        """``{"kinds", "keys"}`` since the last look, or None. The first look is the baseline."""
        now = self.look()
        before, self.last = self.last, now
        if not before:
            return None
        kinds, keys = [], []
        if now["loop"] != before["loop"]:
            # the ledger holds the ideas, the loop's state, projects and maintenance
            kinds += ["loop", "ideas", "projects", "maintain"]
        b0, b1 = before["builds"], now["builds"]
        moved = sorted(k for k in set(b0) | set(b1) if b0.get(k) != b1.get(k))
        if moved or now["loop"] != before["loop"]:
            kinds.append("builds")
            keys += moved
        if now["jobs"] != before["jobs"]:
            kinds.append("jobs")
        # the loop's ledger (its steps) moved: the activity read
        if "loop" in kinds:
            kinds.append("activity")
        return {"kinds": kinds, "keys": keys} if kinds else None


class Hub:
    """The process's streams and the one thread that watches for them."""

    def __init__(self):
        self.cond = threading.Condition()
        self.seq = 0
        self.events: list[tuple[int, dict]] = []
        self.streams: dict[int, float] = {}  # id -> when it was last read
        self.next_id = 0
        self.thread: threading.Thread | None = None
        self.watchers: dict[int, Watcher] = {}

    def publish(self, change: dict) -> None:
        with self.cond:
            self.seq += 1
            self.events = [*self.events[-(KEEP - 1) :], (self.seq, {**change, "seq": self.seq})]
            self.cond.notify_all()

    def _run(self) -> None:
        while True:
            with self.cond:
                if not self.streams:
                    self.thread = None
                    self.watchers.clear()
                    return
                watchers = list(self.watchers.values())
            for w in watchers:
                try:
                    c = w.changes()
                except Exception:  # noqa: BLE001 - a missing file is no event
                    c = None
                if c:
                    self.publish(c)
            time.sleep(TICK_S)

    def open(self, app) -> int:
        with self.cond:
            now = time.monotonic()
            for sid, seen in list(self.streams.items()):
                if now - seen > STALE_S:
                    del self.streams[sid]
            if len(self.streams) >= MAX_STREAMS:
                raise Busy(f"{MAX_STREAMS} live streams are open; poll instead")
            self.next_id += 1
            self.streams[self.next_id] = now
            if id(app) not in self.watchers:
                self.watchers[id(app)] = w = Watcher(app)
                try:
                    w.changes()  # the baseline, before the first event
                except Exception:  # noqa: BLE001
                    pass
            if self.thread is None:
                self.thread = threading.Thread(
                    target=self._run, name="repo-factory-live", daemon=True
                )
                self.thread.start()
            return self.next_id

    def close(self, sid: int) -> None:
        with self.cond:
            self.streams.pop(sid, None)

    def stream(self, app, last: int | None = None) -> Stream:
        """One client's events. Opens its place now (``Busy`` when none is free), so the
        caller can still answer 503; the place is given up when the stream closes. A fresh
        stream starts from the change count *now*, not from when its client first reads past
        the host line: a change in between is sent, not lost."""
        sid = self.open(app)
        with self.cond:
            seen = self.seq if last is None else last
        return Stream(self, sid, self._events(sid, seen))

    def _events(self, sid: int, seen: int) -> Iterator[bytes]:
        try:
            yield b"retry: 3000\n\n"
            yield _event("host", {"version": __version__})
            while True:
                with self.cond:
                    if sid not in self.streams:
                        return
                    self.streams[sid] = time.monotonic()
                    if self.seq == seen:
                        self.cond.wait(PING_S)
                    fresh = [e for s, e in self.events if s > seen]
                    seen = self.seq
                if fresh:
                    for e in fresh:
                        yield _event("change", e, e["seq"])
                else:
                    yield b": ping\n\n"
        finally:
            self.close(sid)


class Stream:
    """A client's events, as an iterator of bytes. ``close`` (or losing the last reference)
    gives its place back, even when it was never read: a generator's ``finally`` alone would
    not run for one that never started."""

    def __init__(self, hub: Hub, sid: int, events: Iterator[bytes]):
        self.hub, self.sid, self.events = hub, sid, events

    def __iter__(self) -> Stream:
        return self

    def __next__(self) -> bytes:
        return next(self.events)

    def close(self) -> None:
        self.hub.close(self.sid)  # a wait under way sees it and ends within PING_S
        try:
            self.events.close()
        except ValueError:  # being read in another thread right now
            pass

    def __del__(self):
        self.close()


def _event(name: str, data: dict, eid: int | None = None) -> bytes:
    head = f"id: {eid}\n" if eid is not None else ""
    return f"{head}event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


HUB = Hub()

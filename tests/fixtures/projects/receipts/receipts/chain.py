# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""Append-only, hash-chained event log.

Each line of ``events.jsonl`` is one JSON event::

    {"seq": n, "ts": "...Z", "type": "...", "data": {...}, "prev": "<hex>", "hash": "<hex>"}

``hash = sha256(prev + "\\n" + canonical_json({seq, ts, type, data}))``.
The first event uses ``prev = "0" * 64``.  This module is imported by the
shell wrapper, so it deliberately uses only fast stdlib imports.
"""
GENESIS = '0' * 64

class BusyError(Exception):
    pass

def utcnow():
    ...

def canonical(obj):
    ...

def event_hash(prev, seq, ts, etype, data):
    ...

class Lock:
    """flock-based exclusive lock with a timeout (seconds)."""

    def __init__(self, path, timeout=5.0):
        ...

    def __enter__(self):
        ...

    def __exit__(self, *exc):
        ...

def _last_line(path):
    """Return the last complete line of the file (bytes) or None."""
    ...

def _repair_tail(path):
    """Drop a torn (partially written) final line left by a crash."""
    ...

def append(session_dir, etype, data, lock_timeout=5.0, locked=False, ts=None):
    """Append one event under the per-session lock; fsync before returning."""
    ...

def read(session_dir):
    ...

def verify_events(events):
    """Return (ok, final_hash, bad_seq, reason)."""
    ...

def witness_send(ev):
    """Report an appended event's (seq, hash) to the parent `receipts run` process.

    The parent keeps these in memory, out of the agent's reach, and writes them
    into the chain as a ``witness`` event after the agent exits, so an agent that
    rewrites and re-hashes ``events.jsonl`` is caught by ``verify``.
    """
    ...

def report_digest(events, report):
    """Bind a bundle's derived report (summary, flags, counts) to the chain's final hash."""
    ...

def verify_report(events, report, digest):
    """Return None if the bundle's report section matches the chain, else the reason."""
    ...

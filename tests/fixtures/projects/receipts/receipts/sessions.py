# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""Session operations: load, report, summary, ack/unack, close, delete."""

class OpError(Exception):
    """Refused operation or bad input (exit code 2)."""

def load(sid):
    ...

def report(sid, cfg, m=None):
    ...

def _lock(sid, cfg):
    ...

def _writable(m):
    ...

def _record_flags(sid, m, cfg, ts=None):
    ...

def check_summary_allowed(sid, replace):
    ...

def attach_summary(sid, text, source, tool_calls, cfg, replace=False, ts=None):
    ...

def _flag(sid, fid, cfg):
    ...

def ack(sid, fid, note, cfg, ts=None):
    ...

def unack(sid, fid, cfg):
    ...

def close(sid, cfg, force=False, ts=None):
    ...

def delete(sid, allow_closed=False):
    ...

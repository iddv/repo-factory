# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""Data store layout, session IDs, audit log and schema migrations.

    $RECEIPTS_HOME/
      VERSION            schema version
      audit.log          store-level audit log (deletes, forced closes, migrations)
      locks/             per-workspace recording locks
      sessions/<id>/     events.jsonl (hash chain), lock, blobs/, shims/
"""
import re

SCHEMA_VERSION = 1
ID_RE = re.compile('^\\d{8}-\\d{6}-[0-9a-f]{4}$')

class StoreError(Exception):
    """Input/usage problem: exit code 2."""

def home():
    ...

def sessions_dir():
    ...

def session_dir(sid):
    ...

def exists():
    ...

def init():
    """Create the store. Returns True if created, False if it already existed."""
    ...

def require():
    ...
MIGRATIONS = {}

def migrate():
    ...

def audit(action, data):
    ...

def new_session_id(now=None):
    ...

def list_ids():
    ...

def resolve(sid):
    """Validate and resolve a session ID (or `last`). Raises StoreError."""
    ...

def _recent(ids):
    ...

def workspace_lock_path(cwd):
    ...

def workspace_recording(cwd):
    """Return the session ID currently recording ``cwd``, or None."""
    ...

def is_recording(sid):
    """True while a `receipts run` process holds the session's workspace lock."""
    ...

def _lock_held(sid):
    ...

def unclosed_sessions(cwd):
    """IDs of sessions recorded in ``cwd`` that have not been closed, newest first."""
    ...

def active_recordings(exclude_lock=None):
    """(session id, directory) for every recording currently running."""
    ...

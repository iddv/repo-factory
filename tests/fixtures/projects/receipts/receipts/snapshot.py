# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""Workspace snapshots (path, size, SHA-256 of every file) and before/after diffs."""
DIFF_CAP = 200000

def git_info(root):
    ...

def _gitignored(root, is_git):
    """Set of ignored relative paths (directories end with '/')."""
    ...

def _excluded(rel, name, is_dir, excludes):
    ...

def _ignored(rel, name, is_dir, ignored_set, patterns):
    ...

def is_binary(head):
    ...

def take(root, cfg, blob_dir=None):
    """Snapshot the workspace. Text files within limits are stored as blobs (for later diffs)."""
    ...

def _file_entry(full, max_bytes):
    ...

def _read_blob(blob_dir, h):
    ...

def _lines(data):
    ...

def compare(before, after, root, blob_dir):
    """Return a list of change dicts: path, change (created|modified|deleted), sizes, hashes, diff."""
    ...

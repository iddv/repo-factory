# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""Build a session model from its events and reconcile the summary's claims into flags.

Reports are always recomputed from the event log; nothing here mutates state.
"""
import re

SEV_ORDER = {'high': 0, 'medium': 1, 'low': 2}
SKIP_WORDS = re.compile('\\b(skip|skipped|skipping|xfail|disabled?|ignored?|pending)\\b', re.I)

def build(sid, events):
    ...

def status(sid, m):
    ...

def counts(m):
    ...

def ev_cmd(c, note=None):
    ...

def excerpt(diff, max_lines=12):
    ...

def ev_file(ch, note=None, lines=None):
    ...

def flag_id(kind, subject):
    ...

def _resolve_path(path, paths):
    """Match a claimed path against workspace paths: exact, suffix, or unique basename."""
    ...

def _cmd_in(claim, recorded):
    ...

def reconcile(m, cfg):
    ...

def _counts_note(t):
    ...

def flag_totals(flags):
    ...

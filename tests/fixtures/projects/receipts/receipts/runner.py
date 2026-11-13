# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""`receipts run`: record an agent session."""
import sys

NONINTERACTIVE = {'claude': ('-p', '--print'), 'gemini': ('-p', '--prompt'), 'codex': ('exec', 'e'), 'aider': ('-m', '--message', '--message-file')}

class RunError(Exception):
    pass

class AgentStartError(Exception):
    pass

def real_shells(path_env):
    ...

def _write_shims(sdir, shells, cfg):
    ...

def _noninteractive(argv):
    ...

def snapshot_event(snap, phase, cfg):
    ...

def run(argv, cfg, name=None, test_cmd=None, out=sys.stderr):
    ...

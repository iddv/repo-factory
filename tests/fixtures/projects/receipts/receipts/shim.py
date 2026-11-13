# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""Shell wrapper installed (per session) as ``sh``/``bash``/``zsh`` on PATH and as $SHELL.

Non-interactive invocations (``-c <cmd>`` or ``<shell> script.sh``) are logged
with command, cwd, start time, duration, exit code and output tail, then run by
the real shell.  Interactive shells are handed straight to the real shell.
Kept free of heavy imports so the overhead stays well under 50 ms.
"""

def _parse(args):
    """Return the command text, or None when the shell is interactive/stdin-fed."""
    ...

def _tail(buf, max_lines, max_bytes):
    ...

def main(session_dir, real_shell, name, capture='tail', max_lines=200, max_bytes=65536):
    ...

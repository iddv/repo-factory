# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""Report rendering: terminal text, Markdown, JSON and HTML."""
LIMITS = 'Limits: only commands run through a shell found via $SHELL or PATH (sh/bash/zsh) are captured; direct process spawns and absolute-path shells (e.g. /bin/sh) are not.'

def local(ts):
    ...

def _secs(ts):
    ...

def duration(m):
    ...

def report_data(m, flags, matched):
    ...

def _git(g):
    ...

def _ev_line(e, md=False):
    ...

def _md(s):
    """Agent-controlled text as inert Markdown: no HTML, comments, code spans, links or emphasis."""
    ...

def _mdcode(s):
    """Agent-controlled text as a single-line code span it cannot break out of."""
    ...

def _trunc(s, n):
    ...

def text(d):
    ...

def markdown(d):
    ...

def to_json(d):
    ...

def to_html(d):
    ...

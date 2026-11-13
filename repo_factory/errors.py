"""The loop's own error (CR-REVIEW C-4). A loop or Projects step that refuses (a wrong state, a
missing reason, a cap) raises ``LoopError`` with the reason in words for the owner. The
command line prints it and exits 1, as before; the page answers 400; the daemon's tick logs it
and keeps going. It is an ``Exception``, so every safety net catches it: a refusal can no
longer stop a worker thread or the daemon, as ``SystemExit`` could."""

from __future__ import annotations

import argparse
import functools


class LoopError(Exception):
    """A step said no; ``str(e)`` says why. ``code``: the command line's exit status."""

    code = 1


def as_exit(fn):
    """``fn`` (a command's function), with a ``LoopError`` turned into ``SystemExit`` of its
    words: what a command line prints before it exits 1."""
    if getattr(fn, "_as_exit", False):
        return fn

    @functools.wraps(fn)
    def run(*a, **kw):
        try:
            return fn(*a, **kw)
        except LoopError as e:
            raise SystemExit(str(e)) from None

    run._as_exit = True
    return run


def _wrap(parser: argparse.ArgumentParser) -> None:
    if callable(parser._defaults.get("fn")):
        parser._defaults["fn"] = as_exit(parser._defaults["fn"])
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sp in action.choices.values():
                _wrap(sp)


def host_group(add):
    """For an ``add_*_group`` that repo-factory (the host) also runs in-process (``repo-factory maintain``,
    ``repo-factory corpus``, ``repo-factory mcp-server``, ``repo-factory github``): every command it adds says no
    with ``SystemExit``, as the host expects of a command, never with ``LoopError``."""

    @functools.wraps(add)
    def build(sub):
        before = set(sub.choices)
        out = add(sub)
        for name in set(sub.choices) - before:
            _wrap(sub.choices[name])
        return out

    return build

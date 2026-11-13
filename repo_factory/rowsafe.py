"""One bad row never takes a page down.

A list built row by row builds each row inside ``row()`` (or ``each()``); a row that raises is
still listed as ``{id, what was still known of it, unreadable: {what, words, error_id,
exception}}`` ("Couldn't read this build"), the rest of the list as before; the error is kept
once by its id in ``<home>/errors.jsonl`` and logged once. The id is the error's signature:
the type and repo-factory's own frames without their lines, SHA-256, its first eight
characters. Standard library only.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

FILE = "errors.jsonl"
KEEP = 200
MAX_FRAMES = 8
_PART = re.compile(r"[a-z_][a-z0-9_]{0,40}")
_lock = threading.Lock()
_seen: dict[str, dict] = {}
_dir: Path | None = None


def configure(loop_home: Path | str | None) -> None:
    """Where ``errors.jsonl`` is kept: the home."""
    global _dir
    _dir = Path(loop_home).expanduser() if loop_home else None


def _own(filename: str) -> str | None:
    root = os.path.realpath(os.path.dirname(__file__))
    path = os.path.realpath(filename)
    if not (path.startswith(root + os.sep) and path.endswith(".py")):
        return None
    rest = ["repo_factory", *path[len(root) + 1 : -3].split(os.sep)]
    if rest[-1] == "__init__":
        rest.pop()
    return ".".join(rest) if all(_PART.fullmatch(p) for p in rest[1:]) else None


def _record(exc: BaseException, where: str, kind: str) -> dict:
    frames = []
    for fs in traceback.extract_tb(exc.__traceback__):
        mod = _own(fs.filename)
        if mod is not None:
            frames.append(f"{mod}.{fs.name}:{int(fs.lineno or 0)}")
    import sys

    cls = type(exc)
    top = cls.__module__.split(".")[0]
    # a built-in, repo-factory's own, the standard library's (its top package)
    name = (
        cls.__name__
        if cls.__module__ == "builtins"
        else f"{cls.__module__}.{cls.__name__}"
        if top == "repo_factory"
        else f"{top}.{cls.__name__}"
        if top in sys.stdlib_module_names
        else "Exception"
    )
    sig = hashlib.sha256(
        "\n".join([name, *(f.rsplit(":", 1)[0] for f in frames)]).encode()
    ).hexdigest()[:16]
    from . import __version__

    return {
        "id": sig[:8],
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": kind,
        "where": str(where)[:120],
        "exception": name,
        "signature": sig,
        "frames": frames[-MAX_FRAMES:],
        "version": __version__,
    }


def note(exc: BaseException, where: str, *, kind: str = "row") -> dict:
    """Keep ``exc`` once by its id; returns its record. Never raises."""
    try:
        rec = _record(exc, where, kind)
    except Exception:  # noqa: BLE001 - the net itself must never fail the page
        return {
            "id": "00000000",
            "kind": kind,
            "where": str(where)[:120],
            "exception": type(exc).__name__,
            "frames": [],
        }
    with _lock:
        first = rec["id"] not in _seen
        if first:
            _seen[rec["id"]] = rec
    if first:
        log.error(
            "error %s (%s) in %s; it is shown as its id from now on",
            rec["id"],
            rec["exception"],
            rec["where"],
            exc_info=exc,
        )
        _keep(rec)
    return _seen.get(rec["id"], rec)


def _keep(rec: dict) -> None:
    if _dir is None:
        return
    path = _dir / FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text().splitlines() if path.exists() else []
        lines = [x for x in lines if x.strip()][-(KEEP - 1) :] + [json.dumps(rec)]
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except OSError as e:
        log.warning("could not keep error %s in %s: %s", rec["id"], path, e)


def words(what: str) -> str:
    return f"Couldn't read this {what}"


def said(eid: str) -> str:
    """What a failed answer says; the id is in the words, so the page finds the report."""
    return (
        f"Something went wrong on repo-factory's side (error {eid}). Try again, or send this error."
    )


def unreadable(what: str, ident, exc: BaseException, *, where: str | None = None, **keep) -> dict:
    rec = note(exc, where or f"list:{what}")
    return {
        **{k: v for k, v in keep.items() if v is not None},
        "id": ident,
        "unreadable": {
            "what": what,
            "words": words(what),
            "error_id": rec["id"],
            "exception": rec["exception"],
        },
    }


@contextlib.contextmanager
def row(out: list, what: str, ident, *, where: str | None = None, **keep):
    """Build one row of ``out`` inside this block; if it raises, the row is ``unreadable``."""
    n = len(out)
    try:
        yield
    except Exception as exc:  # noqa: BLE001 - one row never fails the list
        del out[n:]
        out.append(unreadable(what, ident, exc, where=where, **keep))


def each(items, build, what: str, *, ident=None, keep=None) -> list:
    """``[build(x) for x in items]``, a row that raises listed as ``unreadable``."""
    out: list = []
    for x in items:
        try:
            i = (
                ident(x)
                if ident
                else (x.get("id") if isinstance(x, dict) else getattr(x, "id", None))
            )
        except Exception:  # noqa: BLE001
            i = None
        try:
            k = keep(x) if keep else {}
        except Exception:  # noqa: BLE001
            k = {}
        with row(out, what, i, **k):
            out.append(build(x))
    return out


def is_unreadable(r) -> bool:
    return isinstance(r, dict) and isinstance(r.get("unreadable"), dict)

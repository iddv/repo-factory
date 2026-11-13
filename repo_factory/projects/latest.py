"""The latest release of the tools a project pins, for the drift check ``pins`` (PJ-PLAN §4.2).

``drift.pins`` compares ``name==X.Y.Z`` pins in the workflows and AGENTS.md with the latest
release on the index, but drift itself never reads the network. This module does that read:
one PyPI JSON call per pinned name, kept a day in ``<loop home>/projects/latest.json`` so eight
projects pinning pytest make one call a day, not eight a tick. A name whose read fails is left
out (its pin is not checked this time), never guessed. The fetch is a parameter: tests pass a
dict and never touch the network."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .. import paths
from .drift import PIN, _canon, _pin_sources

URL = "https://pypi.org/pypi/{name}/json"
TTL_S = 86400
MAX_NAMES = 12


def cache_path(home) -> Path:
    return paths.Layout(home).projects / "latest.json"


def pinned_names(root: Path) -> list[str]:
    """The names pinned exactly in the clone's workflows and AGENTS.md, first seen first."""
    out: list[str] = []
    for _, text in _pin_sources(Path(root)):
        for m in PIN.finditer(text):
            name = _canon(m[1])
            if name not in out:
                out.append(name)
    return out[:MAX_NAMES]


def pypi(name: str, timeout: float = 10) -> str | None:
    """The latest version PyPI lists for ``name`` (its JSON API's ``info.version``)."""
    req = urllib.request.Request(URL.format(name=name), headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return (json.loads(r.read() or b"{}").get("info") or {}).get("version") or None


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, d: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".latest.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
    os.replace(tmp, path)


def lookup(
    home, names: list[str], *, fetch: Callable | dict | None = None, now: float | None = None
) -> dict[str, str]:
    """``{name: latest version}`` for the names whose latest is known: from the day's cache,
    else one ``fetch(name)`` each (PyPI by default; a dict stands in for it in tests). A
    failed or empty read leaves the name out."""
    now = now or time.time()
    get = fetch.get if isinstance(fetch, dict) else (fetch or pypi)
    path = cache_path(home)
    cache = _read(path)
    out, dirty = {}, False
    for name in names:
        hit = cache.get(name) or {}
        if hit.get("version") and now - float(hit.get("at") or 0) < TTL_S:
            out[name] = hit["version"]
            continue
        try:
            v = get(name)
        except Exception:  # noqa: BLE001 - skipped, never guessed
            v = None
        if v:
            out[name] = str(v)
            cache[name] = {"version": str(v), "at": now}
            dirty = True
    if dirty:
        _write(path, cache)
    return out

"""The person's repositories, to pick some to onboard as projects (WZ-DESIGN §4, WZ-4).

``listing(gh, org=None, maintained=())`` reads, and only reads: ``GET /user/repos``
(``affiliation=owner``, newest push first, two pages of 100 at most), or one organisation's
``GET /orgs/<o>/repos`` when the person picks it, or, for repo-factory's GitHub App alone,
``GET /installation/repositories``. Forks, archived and empty repositories, and the ones repo-factory
already maintains, are left out. A pick is the existing ``adopt_repo`` action: reads only,
``watch`` until the person confirms its profile in the browser, which is what creates the
project. Nothing here writes to GitHub. ``gh`` is the person's (``github_user.connection``):
repo-factory's own connection, else the gh login.
"""

from __future__ import annotations

import json
import threading
import time

PAGES = 2
PER_PAGE = 100
CACHE_S = 600
MAX_PICKS = 10  # at most ten picks in one wizard run (D9)
_cache: dict[tuple, tuple[float, dict]] = {}
_lock = threading.Lock()


def _get(gh, path: str):
    out = gh("api", path)
    try:
        return json.loads(out or "null")
    except ValueError:
        return None


def row(r: dict) -> dict:
    return {
        "repo": r.get("full_name"),
        "private": bool(r.get("private")),
        "language": r.get("language"),
        "pushed_at": r.get("pushed_at"),
        "open_issues": r.get("open_issues_count") or 0,
        "can_push": bool((r.get("permissions") or {}).get("push")),
    }


def keep(r: dict, maintained: set[str]) -> bool:
    return (
        isinstance(r, dict)
        and bool(r.get("full_name"))
        and not r.get("fork")
        and not r.get("archived")
        and (r.get("size") or 0) > 0
        and str(r["full_name"]).lower() not in maintained
    )


def listing(gh, org: str | None = None, maintained=(), *, app_only: bool = False) -> dict:
    """``{"repos": [row...], "orgs": [...], "left_out": n}``, newest push first."""
    done = {str(m).lower() for m in maintained}
    raw: list[dict] = []
    for page in range(1, PAGES + 1):
        if app_only:
            got = _get(gh, f"installation/repositories?per_page={PER_PAGE}&page={page}")
            got = (got or {}).get("repositories") if isinstance(got, dict) else None
        elif org:
            got = _get(gh, f"orgs/{org}/repos?sort=pushed&per_page={PER_PAGE}&page={page}")
        else:
            got = _get(
                gh, f"user/repos?affiliation=owner&sort=pushed&per_page={PER_PAGE}&page={page}"
            )
        if not isinstance(got, list) or not got:
            break
        raw += got
        if len(got) < PER_PAGE:
            break
    rows = [row(r) for r in raw if keep(r, done)]
    rows.sort(key=lambda x: x["pushed_at"] or "", reverse=True)
    orgs = []
    if not app_only and not org:
        got = _get(gh, "user/orgs?per_page=100")
        orgs = sorted(o["login"] for o in got or [] if isinstance(o, dict) and o.get("login"))
    return {"repos": rows, "orgs": orgs, "left_out": len(raw) - len(rows), "org": org}


_busy: set = set()


def _again(key: tuple, fn) -> None:
    """``fn()`` for ``key`` on a background thread, one at a time."""
    with _lock:
        if key in _busy:
            return
        _busy.add(key)

    def go():
        try:
            value = fn()
            with _lock:
                _cache[key] = (time.monotonic(), value)
        except Exception:  # noqa: BLE001 - the last list stays; the next open asks again
            pass
        finally:
            with _lock:
                _busy.discard(key)

    threading.Thread(target=go, daemon=True, name="factory-read repos").start()


def cached(key: tuple, fn, *, fresh: bool = False) -> dict:
    """``fn()`` for ``key``, read here the first time and with ``fresh`` (the page and the
    wizard refresh with ``r``); after that the last list at once, read again in the
    background once it is ten minutes old (SL: the page never waits on GitHub twice)."""
    with _lock:
        hit = _cache.get(key)
    if hit and not fresh:
        if time.monotonic() - hit[0] >= CACHE_S:
            _again(key, fn)
        return hit[1]
    value = fn()
    with _lock:
        _cache[key] = (time.monotonic(), value)
    return value

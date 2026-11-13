"""Settings › GitHub: the ``gh`` login publishing uses (``api/github/user``), and
repo-factory's own GitHub App (off and unlisted in this version).

``GET /api/github/app`` is what the page polls: the state (none, creating, created,
installed, failed), the app's facts (never a secret), the background job, the links.
``GET /api/github/app/callback?code=&state=`` is where GitHub sends the browser back after
the manifest form: the state must match the one creation in flight (one attempt, ten
minutes), and only then is the exchange started in the background; the answer is a small
page that forwards to ``#/settings/github`` at once. ``GET /api/github/app/setup`` is where
GitHub sends the browser after the install: nothing in its query is trusted; it starts the
refresh job, which reads the installation back with the app's own JWT. The writes (create,
refresh, replace key, remove) are registry actions (``github_app_*``) over ``api/act``."""

from __future__ import annotations

from ..http import HttpError, _json
from .base import ok


def _view(app, r):
    from ... import github_app as G

    return G.public_view()


def _callback(app, r):
    from ... import github_app as G

    store = G.Store()
    pending = store.pending_take(str(r.data.get("state") or ""))
    if pending is None:
        return (
            200,
            "text/html; charset=utf-8",
            G.forward_page(
                r.path,
                "#/settings/github?callback=stale",
                "That link is not the one repo-factory is waiting for (it works once, within ten "
                "minutes). If GitHub has just made the app, do not make another: link it under "
                "Settings › Publishing › GitHub › repo-factory's GitHub App.",
            ).encode(),
        )
    code = str(r.data.get("code") or "")
    try:
        G.start_exchange(code, store)
    except G.AppError as e:
        store.set_job("exchange", "failed", error=str(e))
    return (
        200,
        "text/html; charset=utf-8",
        G.forward_page(
            r.path, "#/settings/github?callback=created", "Creating repo-factory's GitHub App…"
        ).encode(),
    )


def _setup(app, r):
    from ... import github_app as G

    store = G.Store()
    if store.read() is None:
        return (
            200,
            "text/html; charset=utf-8",
            G.forward_page(
                r.path,
                "#/settings/github?setup=none",
                "repo-factory has no GitHub App record here: create it under Settings › Publishing › GitHub first.",
            ).encode(),
        )
    try:
        G.start_refresh(store)
    except G.AppError as e:
        store.set_job("refresh", "failed", error=str(e))
    return (
        200,
        "text/html; charset=utf-8",
        G.forward_page(
            r.path, "#/settings/github?setup=done", "Reading the installation back…"
        ).encode(),
    )


_GH_USER: dict = {}
GH_USER_TTL_S = 300


def _user(app, r):
    """The ``gh`` login publishing uses: ``{connected, login, line, needs_connect}``. One
    ``gh api user`` call, kept ``GH_USER_TTL_S`` (``?check=1`` asks again). Never a token."""
    import shutil
    import time

    from ...gh import Gh
    from ...settings.store import Config

    fresh = str(r.data.get("check") or "") in ("1", "true")
    hit = _GH_USER.get("v")
    if hit and not fresh and time.time() - hit[0] < GH_USER_TTL_S:
        return hit[1]
    cmd = Config(getattr(app, "loop_home", None))["publish"].get("gh_command") or ["gh"]
    cmd = cmd.split() if isinstance(cmd, str) else list(cmd)
    if not shutil.which(cmd[0]):
        out = {
            "connected": False,
            "login": None,
            "needs_connect": True,
            "line": "gh is not installed: install the GitHub CLI (https://cli.github.com), "
            "then run gh auth login",
        }
    else:
        code, so, se = Gh(cmd=cmd)._run(["api", "user", "-q", ".login"], None)
        login = so.strip() if code == 0 else ""
        out = {
            "connected": bool(login),
            "login": login or None,
            "needs_connect": not login,
            "line": f"Publishing as {login} (gh)"
            if login
            else "gh is not logged in: run gh auth login",
        }
    _GH_USER["v"] = (time.time(), out)
    return out


def _repos(app, r):
    """The person's repositories to pick from (reads only), newest push first, without
    forks, archived or empty ones and those repo-factory already maintains. ``?org=`` one
    organisation; ``?fresh=1`` past the ten-minute cache."""
    import re

    from ... import github_repos as R
    from ...gh import PublishError
    from ...github_user import connection
    from ...maintain.identity import maintained_repos
    from ...settings.store import Config

    org = str(r.data.get("org") or "").strip() or None
    if org and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", org):
        raise HttpError(400, "org is a GitHub organisation's login")
    cfg = Config(getattr(app, "loop_home", None))
    try:
        return R.cached(
            ("repos", org),
            lambda: R.listing(connection(cfg), org, maintained_repos(cfg)),
            fresh=str(r.data.get("fresh") or "") in ("1", "true"),
        )
    except PublishError as e:
        # an expected state (GitHub not connected yet), answered as one; the picker says why
        from ...github_app import clean

        return {
            "repos": [],
            "orgs": [],
            "left_out": 0,
            "org": org,
            "listed": False,
            "why": "GitHub did not list the repositories: " + clean(e),
        }


ROUTES = [
    ("GET", "/api/github/repos", ok(_repos)),
    ("GET", "/api/github/user", ok(_user)),
    ("GET", "/api/github/app", ok(_view)),
    ("GET", "/api/github/app/callback", _callback),
    ("GET", "/api/github/app/setup", _setup),
    (
        "POST",
        "/api/github/app",
        lambda a, r: (_ for _ in ()).throw(
            HttpError(
                405,
                "writes go through api/act (github_app_create, github_app_refresh, "
                "github_app_key, github_app_remove)",
            )
        ),
    ),
]

__all__ = ["ROUTES", "_json"]

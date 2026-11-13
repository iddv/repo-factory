"""``repo-factory github``: repo-factory's own GitHub App and its GitHub connection from the
command line (unlisted in this version).

``connect`` signs this repo-factory in to the person's account with GitHub's device flow (a code
typed at github.com/login/device; ``github_user``), ``disconnect`` forgets it, and ``status``
shows both the connection and the App.

``status`` prints what Settings › Publishing › GitHub shows (never a secret); ``refresh`` reads the
installation back; ``key --file app.pem`` replaces the private key after GitHub accepts it;
``link --app-id N --file app.pem`` links an app already made on GitHub instead of making a
second; ``cancel`` stops waiting after Create; ``remove`` uninstalls the app and forgets it
here. Creating the app needs a browser:
Settings › Publishing › GitHub. Each write is the registry action the page runs, as ``cli``; with
``--url`` it goes to the running service with the command line's own token, else it runs in
this process on ``--home`` and the job runs to its end before the command returns."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .errors import host_group


def _call(args, method: str, path: str, body=None, query=None) -> dict:
    from .ui import api

    if args.url:
        from .maintain.cli import _http
        from .tokens import Store

        code, out = _http(args.url, method, path, body, query, Store().value("cli"))
    else:
        from .settings.store import Config

        cfg = Config(args.home)
        app = api.App(cfg.tasks_dir, "claude-code", cfg.home)
        code, _, raw = api.handle(
            method,
            path,
            query,
            json.dumps(body).encode() if body is not None else None,
            app=app,
            identity={"origin": "cli", "actions": None},
        )
        out = json.loads(raw)
    if code >= 400:
        sys.exit(f"repo-factory github: {out.get('error') if isinstance(out, dict) else out}")
    return out


def _act(args, kind: str, fields: dict) -> dict:
    return _call(
        args,
        "POST",
        "/api/act",
        {
            "kind": kind,
            "subject": {"kind": "loop"},
            "fields": fields,
            "confirm": True,
            "origin": "cli",
        },
    )


def _wait(args, what: str, seconds: float = 90) -> dict:
    """The job's end (in-process jobs run in a thread of this process)."""
    t0 = time.time()
    while time.time() - t0 < seconds:
        v = _call(args, "GET", "/api/github/app")
        j = v.get("job") or {}
        if j.get("what") == what and j.get("state") in ("done", "failed"):
            return v
        time.sleep(0.5)
    return _call(args, "GET", "/api/github/app")


def cmd_status(args) -> None:
    from . import github_user as U

    v = _call(args, "GET", "/api/github/app")
    user = U.status()
    if args.json:
        print(json.dumps({**v, "user": user}, indent=1))
        return
    print(
        f"account: {user['line']}"
        + ("" if user["connected"] else " (your gh login is used; 'repo-factory github connect')")
    )
    a = v.get("app") or {}
    print(f"state: {v['state']}")
    if not v.get("available"):
        print(f"  {v.get('unavailable_reason')}")
    if a:
        print(
            f"  app: {a.get('name')} (id {a.get('app_id')}) on {a.get('owner_login')}; "
            f"bot {a.get('bot_login')}; key {'present' if a.get('has_key') else 'missing'} "
            f"{a.get('key_fingerprint') or ''}"
        )
        if a.get("installation_id"):
            print(
                f"  installation {a['installation_id']}: {a.get('repository_selection')}; "
                f"{len(a.get('repos') or [])} repositories: " + ", ".join(a.get("repos") or [])
            )
        else:
            print(f"  not installed yet: {a.get('install_url')}")
    j = v.get("job") or {}
    if j.get("what"):
        print(
            f"  last step: {j['what']} {j.get('state')}"
            + (f": {j['error']}" if j.get("error") else "")
        )
    t = v.get("attempt") or {}
    if t:
        print(
            f"  the last Create ({time.strftime('%H:%M', time.localtime(t['at']))}) never came "
            "back from GitHub"
            + (" (cancelled)" if t.get("how") == "cancelled" else "")
            + ": if GitHub made the app, link it ('repo-factory github link'), do not make another"
        )
    if v["state"] == "none":
        print(
            "  no app (optional: the bot maintenance acts as; the account line above is "
            "repo-factory's sign-in, a separate OAuth App). Create one under Settings › Publishing › "
            "GitHub, or link one you made: repo-factory github link --app-id N --file app.pem"
        )


def cmd_refresh(args) -> None:
    _act(args, "github_app_refresh", {})
    v = _wait(args, "refresh")
    j = v.get("job") or {}
    if j.get("state") == "failed":
        sys.exit(f"repo-factory github refresh: {j.get('error')}")
    a = v.get("app") or {}
    print(
        f"{a.get('name')}: {len(a.get('repos') or [])} repositories: "
        + ", ".join(a.get("repos") or [])
    )


def cmd_key(args) -> None:
    p = Path(args.file).expanduser()
    try:
        if p.stat().st_mode & 0o077:
            sys.exit(f"repo-factory github key: {p} is readable by other users: chmod 600 it first")
        pem = p.read_text()
    except OSError as e:
        sys.exit(f"repo-factory github key: cannot read {p}: {e.strerror}")
    _act(args, "github_app_key", {"pem": pem})
    v = _wait(args, "key")
    j = v.get("job") or {}
    if j.get("state") == "failed":
        sys.exit(f"repo-factory github key: {j.get('error')}")
    print(
        f"replaced: the key is now {((v.get('app') or {}).get('key_fingerprint'))}. Delete "
        "the old key on GitHub (the app's settings)."
    )
    if args.delete_file:
        with __import__("contextlib").suppress(OSError):
            os.unlink(p)


def cmd_link(args) -> None:
    """An app made on GitHub already (a Create that never came back): linked, not made again."""
    p = Path(args.file).expanduser()
    try:
        if p.stat().st_mode & 0o077:
            sys.exit(
                f"repo-factory github link: {p} is readable by other users: chmod 600 it first"
            )
        pem = p.read_text()
    except OSError as e:
        sys.exit(f"repo-factory github link: cannot read {p}: {e.strerror}")
    _act(args, "github_app_link", {"app_id": str(args.app_id), "pem": pem})
    v = _wait(args, "link")
    j = v.get("job") or {}
    if j.get("state") == "failed":
        sys.exit(f"repo-factory github link: {j.get('error')}")
    a = v.get("app") or {}
    print(
        f"linked: {a.get('name')} (id {a.get('app_id')}), "
        + (
            f"installed on {len(a.get('repos') or [])} repositories"
            if a.get("installation_id")
            else f"not installed yet: {a.get('install_url')}"
        )
    )
    if args.delete_file:
        with __import__("contextlib").suppress(OSError):
            os.unlink(p)


def cmd_cancel(args) -> None:
    out = _act(args, "github_app_cancel", {})
    print(out.get("say") or "")


def cmd_remove(args) -> None:
    _act(args, "github_app_remove", {})
    v = _wait(args, "remove")
    j = v.get("job") or {}
    if j.get("state") == "failed":
        sys.exit(f"repo-factory github remove: {j.get('error')}")
    print(
        "removed: the app is uninstalled and forgotten here; delete its registration on "
        "GitHub under the app's settings (Advanced › Delete)."
    )


def cmd_connect(args) -> None:
    """The device flow in this terminal; the store is the same folder the service reads."""
    from . import github_user as U
    from .settings.store import Config

    client = U.client_id(Config(args.home))
    if not client:
        sys.exit(
            "repo-factory github connect: this release has no client id for repo-factory's OAuth App yet; "
            "repo-factory uses your gh login (gh auth login) until it does"
        )
    try:
        got = U.wait(client)
    except U.AppError as e:
        sys.exit(f"repo-factory github connect: {e}")
    if got["state"] != "connected":
        sys.exit(f"repo-factory github connect: {got.get('line') or got['state']}")
    print(
        f"connected as {got['login']} ({', '.join(got['scopes'])}); kept in "
        f"{U.Store().user_env} (mode 600)"
    )


def cmd_disconnect(args) -> None:
    from . import github_user as U

    had = U.Store().disconnect()
    print(
        (
            "disconnected: repo-factory acts as your gh login again. Revoke the grant on GitHub: "
            f"{U.REVOKE_URL}"
        )
        if had
        else "repo-factory was not connected"
    )


@host_group
def add_github_group(sub) -> None:
    import argparse

    p = sub.add_parser("github", help=argparse.SUPPRESS)
    p.add_argument("--home", default=None, help="the loop home (default: $REPO_FACTORY_HOME)")
    p.add_argument(
        "--url",
        default=None,
        help="the running repo-factory (http://127.0.0.1:8790) instead of this process",
    )
    g = p.add_subparsers(dest="github_cmd", required=True)
    s = g.add_parser("status", help="the app, its installation and the last step")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)
    s = g.add_parser("refresh", help="read the installation back from GitHub")
    s.set_defaults(fn=cmd_refresh)
    s = g.add_parser("key", help="replace the private key with one generated on GitHub")
    s.add_argument("--file", required=True, help="the .pem file GitHub downloaded (mode 600)")
    s.add_argument("--delete-file", action="store_true", help="delete the file afterwards")
    s.set_defaults(fn=cmd_key)
    s = g.add_parser(
        "link",
        help="link a GitHub App you already made (instead of making a "
        "second one): its App ID and a key generated on GitHub",
    )
    s.add_argument("--app-id", required=True, help="the App ID on the app's settings page")
    s.add_argument("--file", required=True, help="the .pem file GitHub downloaded (mode 600)")
    s.add_argument("--delete-file", action="store_true", help="delete the file afterwards")
    s.set_defaults(fn=cmd_link)
    s = g.add_parser("cancel", help="stop waiting for GitHub after Create (Create comes back)")
    s.set_defaults(fn=cmd_cancel)
    s = g.add_parser("remove", help="uninstall the app on GitHub and forget it here")
    s.set_defaults(fn=cmd_remove)
    s = g.add_parser(
        "connect",
        help="sign repo-factory in to your GitHub account (a code you type at "
        "github.com/login/device)",
    )
    s.set_defaults(fn=cmd_connect)
    s = g.add_parser(
        "disconnect", help="forget repo-factory's GitHub connection (your gh login is used again)"
    )
    s.set_defaults(fn=cmd_disconnect)
    p.set_defaults(func=lambda a: a.fn(a))

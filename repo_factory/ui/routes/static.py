"""The page, its files and the help page."""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path

from ... import __version__
from ..http import HttpError, _file
from .base import Prefix

UI_DIR = Path(__file__).resolve().parents[1]
STATIC = UI_DIR / "static"
APP_PAGE = UI_DIR / "app.html"
MOVED: dict = {}  # old addresses forwarded into the app (none in this version)
# The page's own scripts and styles are linked at static/v<version>/: a new release is a new
# address, so a host may cache one for good (``handle_with_headers``: immutable); any version
# segment serves today's file, so a page loaded just before an update still draws.
VERSION = re.sub(r"[^A-Za-z0-9._-]", "-", __version__)
VERSIONED = re.compile(r"/static/v[A-Za-z0-9._-]+/((?:brand/|app/)?[A-Za-z0-9_.-]+)")
PAGE_ASSETS = ("theme.js", "repo-factory.css", "looks.css")
# The page's build: Vite writes hashed files and a manifest into static/app/ (CI and the
# release build it with scripts/build_ui.py, the wheel carries it). The page is the
# manifest's entry. A repo-factory without a build (installed from git, or a checkout nobody
# built) answers with one line saying how to get one, never a blank page.
APP_DIR = STATIC / "app"
ENTRY = "src/main.ts"
_BUILT: dict = {}
IMMUTABLE = "public, max-age=31536000, immutable"
NOT_BUILT = (
    b'<!doctype html><meta charset="utf-8"><title>repo-factory</title>'
    b"<p>This repo-factory was installed without its web page (from git, or a checkout nobody "
    b"built): install a release instead, or to work on repo-factory, build it with "
    b"<code>python3 scripts/build_ui.py</code>.</p>"
)
TYPES = {
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
}


def _moved(path: str) -> tuple[int, str, bytes]:
    """An old page's address: a page that forwards to its place in the app. ``handle`` returns
    no headers, so this is a refresh, not a 30x; relative, so it works wherever the page is
    served."""
    to = escape(("../" if path.endswith("/") else "./") + MOVED[path.rstrip("/")], quote=True)
    return (
        200,
        "text/html; charset=utf-8",
        (
            '<!doctype html><meta charset="utf-8">'
            f'<meta http-equiv="refresh" content="0; url={to}"><title>repo-factory</title>'
            f'<p>This page moved: <a href="{to}">open it in repo-factory</a>.</p>'
        ).encode(),
    )


def built() -> dict | None:
    """The built UI's entry, ``{"js": name, "css": [names]}`` under static/app/, from Vite's
    manifest (read again only when it changes); None when the UI is not built."""
    f = APP_DIR / ".vite" / "manifest.json"
    try:
        stamp = f.stat().st_mtime_ns
    except OSError:
        return None
    if _BUILT.get("stamp") != (f, stamp):
        try:
            entry = json.loads(f.read_text())[ENTRY]
            names = [entry["file"], *entry.get("css", [])]
            if not all(re.fullmatch(r"[A-Za-z0-9_.-]+", n) for n in names):
                raise ValueError(f"unexpected file names in {f}: {names}")
            out = {"js": entry["file"], "css": list(entry.get("css", []))}
        except (OSError, ValueError, KeyError, TypeError):
            out = None
        _BUILT.update(stamp=(f, stamp), entry=out)
    return _BUILT["entry"]


def _page(app, r):
    """app.html with its scripts and styles at this version's address and the built UI's
    module and styles; without a build, the one line saying how to get one (503)."""
    b = built()
    if not b:
        return 503, "text/html; charset=utf-8", NOT_BUILT
    text = APP_PAGE.read_text()
    for a in PAGE_ASSETS:
        text = text.replace(f'"static/{a}"', f'"static/v{VERSION}/{a}"')
    at = f"static/v{VERSION}/app/"
    links = "".join(f'<link rel="stylesheet" href="{at}{c}">\n' for c in b["css"])
    text = text.replace("</head>", f"{links}</head>", 1).replace(
        "</body>", f'<script type="module" src="{at}{b["js"]}"></script>\n</body>', 1
    )
    return 200, "text/html; charset=utf-8", text.encode()


def _static(app, r):
    m = VERSIONED.fullmatch(r.path)
    name = m.group(1) if m else r.path[len("/static/") :]
    f = APP_DIR / name[4:] if name.startswith("app/") else STATIC / name
    # one level down only for the brand assets (static/brand/) and the built page (static/app/;
    # never its .vite/)
    if not re.fullmatch(r"(brand/|app/)?[A-Za-z0-9_.-]+", name) or not f.is_file():
        raise HttpError(404, "not found")
    return _file(f, TYPES.get(f.suffix, "application/octet-stream"))


def _help(app, r):
    # the longer explanations the ? tips link to: help.md, rendered
    from ..help import page as help_page

    return 200, "text/html; charset=utf-8", help_page().encode()


ROUTES = [
    ("GET", ("/", "/index.html"), _page),
    ("GET", lambda path: path.rstrip("/") in MOVED, lambda a, r: _moved(r.path)),
    ("GET", Prefix("/static/"), _static),
    ("GET", "/help", _help),
]

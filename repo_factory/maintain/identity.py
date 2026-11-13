"""Who repo-factory is on GitHub when it maintains (MAINTAIN.md §6.3, decision D1; GA-PLAN.md).

Four identities, one seam (``resolve``), chosen by ``maintain.identity``:

- **repo-factory's own GitHub App** (``app``; GA-PLAN): an app named ``repo-factory-<login>`` the owner
  creates from Settings › Publishing › GitHub, installed on the repositories repo-factory maintains. Each call
  gets a one-hour installation token narrowed to the fewest permissions and the one
  repository it acts on (``github_app.SCOPES``); comments, labels and pull requests carry the
  ``repo-factory-<login>[bot]`` badge; commits are authored as the bot;
- **a fine-grained token on the owner's account** (``token``), or **a bot account's**, in a
  0600 env file (``GH_TOKEN=…``, e.g. ``~/.config/repo-factory/maintain.env``) named by
  ``maintain.token_env_file``; ``publish.Gh`` reads it into the ``gh`` subprocess's
  environment for each call and nowhere else;
- **the owner's ``gh`` login** (``login``; what publishing always uses): broad, every
  repository the owner has, workflow files included.

``auto`` (the default) is the app once it is installed, else the token file, else the login:
an install without an app behaves exactly as before. Nothing here widens by itself: an
identity that cannot reach a repository is reported (``reach``, Needs-you rows), never
replaced by the login (``maintain.identity_fallback_to_login`` stays off by default).

``describe`` says which identity is in use, its login, its scopes (a classic token or the
login) or that it is fine-grained, its expiry, and how many maintained repos it reaches; for
the app, from its record and one ``GET /app`` (the key and the id still agree). It never
reads a token or the key. ``doctor(home)`` is the line ``repo-factory doctor`` prints."""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

WARN_DAYS = 30  # a token expiring sooner is a warning
MODES = ("auto", "app", "token", "login")
# GitHub's token shapes, the new stateless ghs_<app>_<jwt> form included (github_app.clean
# and the secrets scan carry the same)
TOKEN_SHAPES = re.compile(r"\b(gh[pousr]_[A-Za-z0-9_.-]{16,}|github_pat_[A-Za-z0-9_]{20,})")


REFUSED = "token-refused.json"  # <loop home>/maintain/: the token file GitHub refused


def _refused_path(home) -> Path:
    from ..paths import Layout

    return Layout(home).maintain / REFUSED


def _mtime(path) -> float | None:
    try:
        return Path(path).expanduser().stat().st_mtime
    except OSError:
        return None


def note_refused(home, token_file) -> None:
    """GitHub answered 401 to ``maintain.token_env_file``'s token: kept with the file's
    mtime, so a token written into the file afterwards is tried again."""
    p = _refused_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(
        json.dumps(
            {
                "file": str(token_file),
                "mtime": _mtime(token_file),
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        + "\n"
    )
    tmp.replace(p)


def clear_refused(home) -> None:
    _refused_path(home).unlink(missing_ok=True)


def token_refused(cfg) -> str | None:
    """``maintain.token_env_file`` when GitHub refused its token and the file has not changed
    since; else None."""
    f = (cfg.get("maintain") or {}).get("token_env_file")
    if not f:
        return None
    try:
        rec = json.loads(_refused_path(cfg.home).read_text())
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    if not isinstance(rec, dict) or rec.get("file") != str(f) or rec.get("mtime") != _mtime(f):
        return None
    return str(f)


def other_works(cfg) -> bool:
    """Whether an identity besides the token file can act: repo-factory's GitHub App installed, or
    repo-factory's GitHub sign-in connected and not refused (no GitHub call: what is kept)."""
    if mode(cfg) in ("auto", "app") and app().installed():
        return True
    from ..github_user import Store, needs_connect

    store = Store()
    return store.connected() and not needs_connect(store.record())


def token_skipped(cfg) -> str | None:
    """The refused token file maintenance no longer uses (``auto``: another identity works);
    None when it is used, or when there is none. ``identity: token`` keeps it: the owner
    chose it, and the refusal is shown instead."""
    f = token_refused(cfg)
    return f if f and mode(cfg) == "auto" and other_works(cfg) else None


def mode(cfg) -> str:
    m = str(((cfg.get("maintain") or {}).get("identity")) or "auto").lower()
    return m if m in MODES else "auto"


def maintain_gh(cfg):
    """Maintenance's ``Gh`` when ``maintain.gh_command`` or ``maintain.token_env_file`` is
    set (the token path); None means the loop's own (the owner's login)."""
    from ..gh import Gh

    m = cfg.get("maintain") or {}
    cmd, env_file = m.get("gh_command"), m.get("token_env_file")
    if env_file and token_skipped(cfg):  # refused, and another identity works
        env_file = None
    if not cmd and not env_file:
        return None
    base = cmd or cfg["publish"].get("gh_command") or ["gh"]
    return Gh(cmd=base if isinstance(base, list) else str(base).split(), env_file=env_file)


def app():
    """repo-factory's own GitHub App of this install (``github_app.app_for``; the store comes from the
    environment, so a detached fix process finds the same one)."""
    from .. import github_app

    return github_app.app_for()


def app_gh(the_app, scope: str, repos, cmd):
    """The ``Gh`` that acts as the app (a seam: tests give its ``gh`` a fake run)."""
    return the_app.gh(scope, repos, cmd=cmd)


def resolve(cfg, repo: str | None = None, scope: str = "maintain"):
    """The narrower identity for ``scope`` on ``repo`` (``owner/name``, or None for every
    repository it reaches), by ``maintain.identity``: the app's ``Gh`` (narrowed to that
    repository), the token file's, or None for the login. ``app`` with no installed app
    gives a ``Gh`` that refuses every call and says why; ``auto`` falls through."""
    m = mode(cfg)
    if m == "login":
        return None
    base = (cfg.get("maintain") or {}).get("gh_command") or cfg["publish"].get("gh_command")
    cmd = base if isinstance(base, list) or base is None else str(base).split()
    if m in ("auto", "app"):
        a = app()
        if a.installed() or m == "app":
            return app_gh(a, scope, [repo] if repo else None, cmd)
    if m in ("auto", "token"):
        return maintain_gh(cfg)
    return None


class Owner:
    """What ``gh_for`` reads of a ``Loop``: its settings, and ``gh`` (the
    owner's own login). For a caller that has the settings and no loop."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._gh = None

    @property
    def gh(self):
        if self._gh is None:
            from ..github_user import connection

            self._gh = connection(self.cfg)
        return self._gh


def gh_for(loop, repo: str | None = None, scope: str = "maintain"):
    """``resolve`` or the loop's own ``gh`` (the owner's login)."""
    return resolve(loop.cfg, repo, scope) or loop.gh


def is_app(gh) -> bool:
    return getattr(gh, "credential", None) == "app"


def narrow(gh, scope: str):
    """The same identity with another token scope (a fix's push: ``push``); any other
    identity is itself."""
    if is_app(gh) and getattr(gh, "app", None) is not None:
        return gh.app.gh(scope, getattr(gh, "repos", None), cmd=gh.cmd)
    return gh


def git_author(gh) -> list[str]:
    """``git -c`` arguments that author a commit as the bot (its noreply address gives the
    commit the bot's badge on GitHub); none for any other identity."""
    if not is_app(gh) or getattr(gh, "app", None) is None:
        return []
    a = gh.app
    if not a.bot_login or not a.bot_email:
        return []
    return ["-c", f"user.name={a.bot_login}", "-c", f"user.email={a.bot_email}"]


def fallback(cfg) -> bool:
    """``maintain.identity_fallback_to_login`` (default false): a token that cannot reach a
    repo never silently becomes the owner's login."""
    return bool((cfg.get("maintain") or {}).get("identity_fallback_to_login", False))


def clean(text: str, limit: int = 300) -> str:
    """An error line safe to show: anything token-shaped, key-shaped or JWT-shaped removed."""
    s = " ".join(str(text or "").split())
    s = re.sub(r"-----BEGIN[^-]*-----.*?-----END[^-]*-----", "[key]", s)
    s = re.sub(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", "[jwt]", s)
    return TOKEN_SHAPES.sub("[token]", s)[:limit]


def parse_include(text: str) -> tuple[int | None, dict[str, str], str]:
    """``gh api -i``'s output: the status, the headers (lower-cased names) and the body."""
    head, _, body = (text or "").replace("\r\n", "\n").partition("\n\n")
    lines = head.splitlines()
    m = re.match(r"HTTP/\S+\s+(\d{3})", lines[0]) if lines else None
    headers = {}
    for ln in lines[1:]:
        k, sep, v = ln.partition(":")
        if sep:
            headers[k.strip().lower()] = v.strip()
    return (int(m.group(1)) if m else None), headers, body


def expiry(value: str | None) -> float | None:
    """``github-authentication-token-expiration`` (``2027-10-08 12:00:00 UTC``) as epoch."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}:\d{2}))?", value or "")
    if not m:
        return None
    dt = datetime.strptime(f"{m.group(1)} {m.group(2) or '00:00:00'}", "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=UTC).timestamp()


def login_of(gh) -> str | None:
    """The identity's login: the bot's name for the app (no call), else ``gh api user``."""
    name = getattr(gh, "login_name", None)
    if name:
        return name
    try:
        code, out, _ = gh._run(["api", "user", "-q", ".login"], None)
    except Exception:  # noqa: BLE001 - no gh at all is an answer too
        return None
    return (out.strip() or None) if code == 0 else None


def describe(
    gh,
    *,
    owner_gh=None,
    owner_login: str | None = None,
    repos=(),
    now: float | None = None,
    home=None,
) -> dict:
    """Which identity ``gh`` is: ``{"kind": owner|connection|token|bot|app, "login",
    "fine_grained", "scopes", "expires", "days_left", "reach": [ok, n], "status": ok|warn|fail,
    "line", "refused"}``. A 401 is kept: for the token file in ``home`` (``note_refused``), for
    repo-factory's GitHub sign-in in its record (``github_user.mark_refused``)."""
    now = now or time.time()
    if is_app(gh):
        return describe_app(getattr(gh, "app", None) or app(), repos=repos)
    token = getattr(gh, "env_file", None) is not None
    conn = token and hasattr(gh, "connected_as")  # repo-factory's GitHub connection (github_user)
    out = {
        "kind": "connection" if conn else "token" if token else "owner",
        "token_file": str(gh.env_file) if token else None,
        "login": None,
        "fine_grained": False,
        "scopes": None,
        "expires": None,
        "days_left": None,
        "reach": None,
        "status": "ok",
        "line": "",
        "refused": False,
    }
    try:
        code, text, err = gh._run(["api", "-i", "user"], None)
    except Exception as e:  # noqa: BLE001 - gh missing
        code, text, err = 127, "", str(e)
    status, headers, body = parse_include(text)
    if status == 401:
        if conn:
            from ..github_user import AGAIN, mark_refused

            mark_refused(line="GET /user answered 401")
            line = f"GitHub refused repo-factory's sign-in (expired or revoked): {AGAIN}"
        elif token:
            if home is not None:
                note_refused(home, gh.env_file)
            line = (
                "the maintenance token (maintain.token_env_file) was refused by GitHub "
                f"(expired or revoked): {out['token_file']}"
            )
        else:
            line = "gh is not logged in"
        out.update(status="fail", line=line, refused=bool(token))
        return out
    if token and not conn and home is not None and code == 0 and status == 200:
        clear_refused(home)
    if code != 0 or status != 200:
        out.update(
            status="fail" if token else "warn",
            line=f"could not ask GitHub who {'the token' if token else 'gh'} is: "
            f"{clean(err or text) or 'no answer'}",
        )
        return out
    try:
        out["login"] = (json.loads(body) or {}).get("login")
    except ValueError:
        out["login"] = None
    if "x-oauth-scopes" in headers:
        out["scopes"] = [s.strip() for s in headers["x-oauth-scopes"].split(",") if s.strip()]
    else:
        out["fine_grained"] = True  # fine-grained tokens report no OAuth scopes
    exp = expiry(headers.get("github-authentication-token-expiration"))
    if exp:
        out["expires"] = time.strftime("%Y-%m-%d", time.gmtime(exp))
        out["days_left"] = int((exp - now) // 86400)
    if token and not conn:
        mine = owner_login or (login_of(owner_gh) if owner_gh is not None else None)
        if mine and out["login"] and out["login"].lower() != mine.lower():
            out["kind"] = "bot"
    repos = [r for r in repos if isinstance(r, str) and r.count("/") == 1]
    if repos:  # one read a repo, eight at a time
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(8, len(repos))) as ex:
            codes = list(
                ex.map(lambda r: gh._run(["api", f"repos/{r}", "-q", ".full_name"], None)[0], repos)
            )
        out["reach"] = [sum(c == 0 for c in codes), len(repos)]
    return finish(out)


def describe_app(a, repos=()) -> dict:
    """``describe`` for repo-factory's own app: from its record (no token is read), the reach from
    the installation's repository list, and one read token minted (or taken from the cache)
    to show the key, the id and the installation still agree; ``fail`` with why when GitHub
    refuses (uninstalled, suspended, key replaced)."""
    from .. import github_app

    rec = a.rec or {}
    perms = rec.get("permissions") or github_app.PERMISSIONS
    out = {
        "kind": "app",
        "token_file": None,
        "login": a.bot_login,
        "fine_grained": True,
        "scopes": sorted(f"{k}:{v}" for k, v in perms.items()),
        "expires": None,
        "days_left": None,
        "reach": None,
        "status": "ok",
        "line": "",
        "app": {
            k: rec.get(k)
            for k in (
                "name",
                "slug",
                "html_url",
                "owner_login",
                "installation_id",
                "repository_selection",
            )
        },
    }
    why = a.why_not()
    if why:
        out.update(status="fail", line=why)
        return out
    try:  # a read token: cached when it worked before, so this is free
        a.token("read")
    except github_app.AppError as e:
        out.update(status="fail", line=f"{rec.get('name')}: {e}")
        return out
    repos = [r for r in repos if isinstance(r, str) and r.count("/") == 1]
    if repos:
        ok = sum(1 for r in repos if a.reaches(r))
        out["reach"] = [ok, len(repos)]
    return finish(out)


def finish(d: dict) -> dict:
    """The status and the one line for ``describe``'s facts."""
    who = d["login"] or "an unknown account"
    if d["kind"] == "app":
        from ..github_app import PERMISSIONS

        name = (d.get("app") or {}).get("name") or who
        got = dict(s.split(":", 1) for s in d["scopes"] or [] if ":" in s)
        order = [*PERMISSIONS, *sorted(k for k in got if k not in PERMISSIONS)]
        writes = [k.replace("_", " ") for k in order if got.get(k) == "write"]
        reads = [k.replace("_", " ") for k in order if got.get(k) == "read" and k != "metadata"]
        what = (
            f"{who}, a GitHub App on your account ({name}): "
            + ", ".join(writes)
            + ("; " + " and ".join(reads) + " read" if reads else "")
        )
        status, tail = "ok", ""
    elif d["kind"] == "connection":
        what = f"repo-factory's GitHub connection to {who}"
        what += f" (scopes {', '.join(d['scopes'])})" if d["scopes"] else ""
        status = "warn"
        tail = (
            "it can act on every repository of the account, workflow files included: give "
            "repo-factory its own identity under Settings › Publishing › GitHub (one minute)"
        )
    elif d["kind"] == "owner":
        what = f"the owner's gh login ({who}"
        what += f"; scopes {', '.join(d['scopes'])})" if d["scopes"] else ")"
        status = "warn"
        tail = (
            "it can act on every repository of the login, workflow files included: give "
            "repo-factory its own identity under Settings › Publishing › GitHub (one minute)"
        )
    else:
        acct = "a bot account" if d["kind"] == "bot" else "your account"
        if d["fine_grained"]:
            what = f"a fine-grained token for {who} ({acct})"
            status, tail = "ok", ""
        else:
            what = f"a classic token for {who} ({acct}; scopes {', '.join(d['scopes'] or []) or 'none'})"
            status = "warn"
            tail = "a fine-grained token scoped to the product repos is narrower"
        if d["scopes"] and "workflow" in d["scopes"]:
            tail = "it can change workflow files; " + tail
    bits = [what]
    if d["expires"]:
        left = d["days_left"]
        bits.append(
            f"expires {d['expires']}"
            + (f" ({left} days)" if left is not None and left >= 0 else " (expired)")
        )
        if left is not None and left < 0:
            status = "fail"
        elif left is not None and left < WARN_DAYS and status == "ok":
            status = "warn"
            tail = "rotate it soon"
    elif d["kind"] not in ("owner", "app") and d["fine_grained"]:
        bits.append("no expiry")
    if d["reach"]:
        ok, n = d["reach"]
        bits.append(f"reaches {ok} of {n} maintained repo{'s' if n != 1 else ''}")
        if ok < n and status == "ok":
            status = "warn"
            tail = (
                "add the rest to repo-factory's app on GitHub (Settings › Publishing › GitHub › Add "
                "repositories)"
                if d["kind"] == "app"
                else "give it access to every product repo on GitHub"
            )
    d["status"] = status
    d["line"] = ", ".join(bits) + (f": {tail}" if tail else "")
    return d


def maintained_repos(cfg) -> list[str]:
    """The repos maintenance polls (``poll.targets``' list) without opening a loop: the
    published builds in the ledger, if there is one, and ``maintain.repos``."""
    out = []
    if Path(cfg.db).exists():
        from ..loop.ledger import Ledger

        for i in Ledger(cfg.db).all(("published", "public")):
            full = (i.get("publish") or {}).get("repo")
            if full and full not in out:
                out.append(full)
    for full in (cfg.get("maintain") or {}).get("repos") or []:
        if isinstance(full, str) and full.count("/") == 1 and full not in out:
            out.append(full)
    return out


def doctor(home: str | Path | None = None) -> tuple[str, str]:
    """``repo-factory doctor``'s maintenance line: ``(ok|warn|fail, text)``."""
    from ..github_user import connection
    from ..settings.store import Config

    cfg = Config(home)
    m = cfg.get("maintain") or {}
    if not m.get("enabled", False):
        return "ok", "maintenance is off (maintain.enabled: false)"
    owner = connection(cfg)
    repos = maintained_repos(cfg)
    gh = resolve(cfg) or owner
    d = describe(
        gh, owner_gh=owner, owner_login=cfg["publish"].get("owner"), repos=repos, home=cfg.home
    )
    if d.get("refused") and token_skipped(cfg):  # just refused: say who acts instead
        owner = connection(cfg)
        gh = resolve(cfg) or owner
        d = describe(
            gh, owner_gh=owner, owner_login=cfg["publish"].get("owner"), repos=repos, home=cfg.home
        )
    if d["kind"] == "connection" and d["status"] == "fail":  # the sign-in, just refused
        owner = connection(cfg)
        if owner.env_file is None:
            d = describe(
                resolve(cfg) or owner,
                owner_gh=owner,
                owner_login=cfg["publish"].get("owner"),
                repos=repos,
                home=cfg.home,
            )
            d["line"] = (
                f"GitHub refused repo-factory's sign-in (expired or revoked), so maintenance "
                f"acts as {d['line']}; click Connect again (Settings › Publishing › GitHub)"
            )
            d["status"] = "fail" if d["status"] == "fail" else "warn"
    skipped = token_skipped(cfg)
    lines: list[dict] = []
    held = held_lines(cfg.home)
    extra = (
        [
            {
                "status": "warn",
                "line": (
                    f"maintain.token_env_file ({skipped}) was refused by GitHub (expired or revoked) and "
                    "is not used: remove the setting under Settings › Publishing › Maintenance › Identity "
                    "on GitHub"
                ),
            }
        ]
        if skipped
        else []
    )
    if not lines and not held and not extra:
        return d["status"], d["line"]
    worst = d["status"]
    for x in [*extra, *lines, *held]:
        if x["status"] == "fail" or (x["status"] == "warn" and worst == "ok"):
            worst = x["status"]
    return worst, d["line"] + "".join(f"; {x['line']}" for x in extra) + "".join(
        f"; {x['line']}" for x in held
    )


def held_lines(home) -> list[dict]:
    """The products whose AGENTS.md is missing or does not run (the last poll's cache; no
    GitHub call), each with the one thing that fixes it. Their fixes go on (AU3 finding 4)."""
    from .github import Cache

    out = []
    for full, rec in sorted(Cache(home).read()["repos"].items()):
        for r in rec.get("rows") or []:
            if r.get("kind") == "agents_md":
                what = (
                    "AGENTS.md is missing"
                    if r.get("state") == "missing"
                    else "AGENTS.md's commands can't run"
                )
                out.append(
                    {
                        "status": "warn",
                        "line": f"{full}: {what}; open the "
                        f"backfill PR (Settings › Publishing › Maintenance, or repo-factory maintain backfill "
                        f"{full})",
                    }
                )
    return out

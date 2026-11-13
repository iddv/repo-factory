"""How every ``claude`` repo-factory starts gets its Claude login (TS.REVIEW R1, for builds, fixes,
repairs, chat, the no-tools prompts and Open in Claude Code alike): through a pipe, never in an
environment or on a command line.

The token repo-factory was started with (``CLAUDE_CODE_OAUTH_TOKEN`` from ``claude setup-token``, or
``ANTHROPIC_API_KEY``) is written into a pipe whose read end the process inherits;
``CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR`` or ``CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR`` names it
(both read by the CLI since at least 2.1.300; measured with a fake API: the bearer header, or
``x-api-key``, carries the token). claude reads the pipe once at start, so a tool command that
looks at ``env``, ``/proc/*/environ`` or the descriptor later finds nothing. Every other variable
that could carry a login (``LOGIN_VARS``) is dropped from the process's environment, and the
shell prefix (``harness/factory-shell``) drops them again for each tool command.

``ANTHROPIC_AUTH_TOKEN`` (a gateway's bearer) is not handed over: no launch passes it any more.

Without a token in repo-factory's environment nothing is piped; claude then logs in from the
credentials file in its config dir (``profile.clean_config`` copies the owner's), which a build's
shell can read: ``repo-factory doctor``'s ``claude token`` line says so.

Whose token it is (``credential``): a ``CLAUDE_CODE_OAUTH_TOKEN`` is your Claude login; an
``ANTHROPIC_API_KEY`` the process was started with (a unit's ``Environment=`` or
``EnvironmentFile=``, or the shell that ran repo-factory) is a key you gave repo-factory; one that
only a ``.env`` file added after the start (so it is not in ``/proc/self/environ``) is another
tool's key, and builds do not use it unless ``build.allow_api_key`` says so.

One policy for every launch (builds run on Claude Code and your plan): ``choose`` picks the token
each ``claude`` gets, and ``source`` (so ``piped``, which every launch goes through) hands over
only that one:

1. the Claude login token repo-factory keeps itself (``claude_login``: ``repo-factory setup building``,
   Settings › Building › Builds run on);
2. a token the process was started with (the unit's ``CLAUDE_CODE_OAUTH_TOKEN``, or an
   ``ANTHROPIC_API_KEY`` the unit gives it: deliberate, so it keeps working);
3. an API key from a ``.env`` file only with ``build.allow_api_key`` (default false).

Without any of them nothing is piped and claude logs in from Claude Code's own sign-in
(``signed_in``: also the person's plan); with neither, a build is held before it starts
(``build_login``, the loop's ``start_builds``). The names repo-factory's ``.env`` added are kept in
``$REPO_FACTORY_DOTENV_VARS`` (``mark_dotenv``), so a process repo-factory starts (a build, a fix)
still tells such a key from one the unit gave."""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path

TOKEN_ENV = {
    "CLAUDE_CODE_OAUTH_TOKEN": "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
    "ANTHROPIC_API_KEY": "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
}
TOKEN_FD_ENV = tuple(TOKEN_ENV.values())
# Keys of other tools' providers that no process repo-factory starts has a use for (an xAI key,
# web search backends') are dropped with the logins, so a session never sees them.
WATCHER_KEYS = ("XAI_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY", "TAVILY_API_KEY")
LOGIN_VARS = (
    *TOKEN_ENV,
    *TOKEN_FD_ENV,
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_GATEWAY_TOKEN_FILE_DESCRIPTOR",
    "CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR",
    *WATCHER_KEYS,
)


DOTENV_ENV = (
    "REPO_FACTORY_DOTENV_VARS"  # the names repo-factory's .env added (names only, comma-separated)
)
HOLD = "Builds need your Claude plan: set it up (claude setup-token), or allow the API key."


def _env_token(environ=None) -> tuple[str, str] | None:
    environ = os.environ if environ is None else environ
    for k in TOKEN_ENV:
        v = (environ.get(k) or "").strip()
        if v:
            return k, v
    return None


def _stored(environ=None) -> str | None:
    """The Claude login token repo-factory keeps (``claude_login``), or None."""
    from .. import claude_login

    try:
        return claude_login.Store(env=os.environ if environ is None else environ).token()
    except OSError:
        return None


def found(environ=None) -> tuple[str, str] | None:
    """``(variable, value)`` of a Claude token repo-factory has, whoever's it is: the one it keeps,
    else one in its environment. No policy: what a launch gets is ``source``."""
    kept = _stored(environ)
    if kept:
        return "CLAUDE_CODE_OAUTH_TOKEN", kept
    return _env_token(environ)


def source(environ=None) -> tuple[str, str] | None:
    """``(variable, value)`` of the Claude token a launch gets (``choose``), or None."""
    cred = choose(environ)[0]
    return (cred.var, cred.value) if cred is not None else None


def started_names() -> frozenset[str] | None:
    """The variable names this process was started with (``/proc/self/environ``), without any
    value; None where ``/proc`` cannot say."""
    try:
        raw = Path("/proc/self/environ").read_bytes()
    except OSError:
        return None
    return frozenset(e.partition(b"=")[0].decode(errors="replace") for e in raw.split(b"\0") if e)


@dataclass(frozen=True)
class Credential:
    """The Claude token ``source`` found, and whose it is (never printed: ``value`` is kept out
    of the repr)."""

    var: str
    value: str = field(repr=False)
    watchers: bool = False  # an API key only repo-factory's .env gave the process
    stored: bool = False  # the login token repo-factory keeps (claude_login)

    @property
    def kind(self) -> str:
        return "login" if self.var == "CLAUDE_CODE_OAUTH_TOKEN" else "api_key"

    @property
    def words(self) -> str:
        """The doctor's and the page's name for it."""
        if self.kind == "login":
            return "Claude login token"
        return "Claude API key (the watcher's)" if self.watchers else "Claude API key"

    @property
    def on(self) -> str:
        """What builds run on, for Settings › Building and the status lines."""
        if self.kind == "login":
            return "your Claude plan (login token)"
        return (
            "your Claude API key (opted in)"
            if self.watchers
            else "your Claude API key (the unit's)"
        )


def credential(environ=None, started=None) -> Credential | None:
    """``source``'s token as a ``Credential``. ``started``: the names the process was started
    with (default: ``started_names()`` for repo-factory's own environment, every name of an ``environ``
    passed in); an API key not among them came from repo-factory's ``.env``, the watcher's. Where
    ``/proc`` cannot say, an API key counts as the watcher's (Open asks before using it). So
    does one ``$REPO_FACTORY_DOTENV_VARS`` names: a process repo-factory starts inherits the key, and the
    name with it. The token in repo-factory's environment only: ``choose`` adds the one repo-factory keeps."""
    env = os.environ if environ is None else environ
    tok = _env_token(env)
    if tok is None:
        return None
    if started is None:
        started = started_names() if environ is None or environ is os.environ else set(environ)
    dotenv = {n.strip() for n in (env.get(DOTENV_ENV) or "").split(",") if n.strip()}
    watchers = tok[0] != "CLAUDE_CODE_OAUTH_TOKEN" and (
        started is None or tok[0] not in started or tok[0] in dotenv
    )
    return Credential(tok[0], tok[1], watchers)


def mark_dotenv(environ=None, started=None) -> None:
    """Name in ``$REPO_FACTORY_DOTENV_VARS`` the token variables this process has but was not started
    with (a ``.env`` file's), so a process it starts inherits the knowledge. Names only."""
    env = os.environ if environ is None else environ
    started = started_names() if started is None else started
    if started is None:
        return
    have = {n for n in (env.get(DOTENV_ENV) or "").split(",") if n}
    add = {k for k in TOKEN_ENV if env.get(k) and k not in started} - have
    if add:
        env[DOTENV_ENV] = ",".join(sorted(have | add))


def api_key_allowed(cfg=None) -> bool:
    """``build.allow_api_key``: the owner chose to let Claude Code bill the watcher's API key
    when there is no Claude login token. ``cfg``: the loop's settings (default: repo-factory's own
    loop, as ``settings.store.Config`` finds it); settings that cannot be read: no."""
    try:
        if cfg is None:
            from ..settings.store import Config

            cfg = Config()
        return bool((cfg.get("build") or {}).get("allow_api_key"))
    except Exception:  # noqa: BLE001 - no choice read: no key
        return False


def choose(
    environ=None, started=None, allow: bool | None = None
) -> tuple[Credential | None, Credential | None]:
    """``(usable, held)``: the credential a launch gets, and the one it may not use without the
    owner's choice (the watcher's API key while ``build.allow_api_key`` is off; ``allow``
    overrides the setting). The login token repo-factory keeps comes first, then repo-factory's environment
    (``credential``). Both None: no Claude token at all."""
    kept = _stored(environ)
    if kept:
        return Credential("CLAUDE_CODE_OAUTH_TOKEN", kept, stored=True), None
    cred = credential(environ, started)
    if cred is not None and cred.watchers and not (api_key_allowed() if allow is None else allow):
        return None, cred
    return cred, None


def signed_in(config_dir: Path | None = None) -> bool:
    """Claude Code's own sign-in (``claude auth login``) is on this machine: its credentials
    file, which builds copy into each session when repo-factory pipes no token (the person's plan
    too; ``repo-factory doctor``'s ``claude token`` line says a build's commands can read the copy)."""
    if config_dir is None:
        from .claude_code import config_dir as _config_dir

        config_dir = _config_dir()
    try:
        return (Path(config_dir) / ".credentials.json").is_file()
    except OSError:
        return False


def build_login(environ=None, started=None, cfg=None, config_dir: Path | None = None) -> dict:
    """What builds run on, for the loop, the page and the doctor (never a value):
    ``{"on": login|signin|api_key|None, "words", "line", "held", "stored", "allow_api_key",
    "watchers_key"}``. ``on`` None: no build starts (``HOLD`` says why); ``held``: the
    watcher's API key is there, unused."""
    allow = api_key_allowed(cfg)
    cred, held = choose(environ, started, allow)
    out = {
        "stored": bool(cred is not None and cred.stored),
        "allow_api_key": allow,
        "watchers_key": held is not None or bool(cred is not None and cred.watchers),
        "held": held is not None,
    }
    if cred is not None:
        return {**out, "on": cred.kind, "words": cred.on, "line": f"Builds run on {cred.on}."}
    if signed_in(config_dir):
        return {
            **out,
            "on": "signin",
            "words": "your Claude plan (Claude Code's sign-in)",
            "line": "Builds run on your Claude plan (Claude Code's sign-in, copied into "
            "each session; a login token is safer: claude setup-token).",
        }
    return {**out, "on": None, "words": "not set", "line": HOLD}


def scrub(env: dict) -> dict:
    """``env`` without any login variable (changed in place, and returned)."""
    for k in LOGIN_VARS:
        env.pop(k, None)
    return env


def pipe(value: str) -> int:
    """The read end of a pipe that holds ``value`` and is closed for writing."""
    r, w = os.pipe()
    try:
        os.write(w, value.encode())
    finally:
        os.close(w)
    return r


@contextlib.contextmanager
def piped(env: dict, environ=None):
    """For one process: ``env`` (its environment, changed in place) loses every login variable
    and, when ``environ`` (default: repo-factory's own) holds a token, names the pipe that carries it.
    Yields the descriptors to pass (``pass_fds``; empty without a token), closed afterwards.
    A pipe is read once: every start (a retry too) needs its own ``piped``."""
    scrub(env)
    tok = source(environ)
    if tok is None:
        yield ()
        return
    r = pipe(tok[1])
    env[TOKEN_ENV[tok[0]]] = str(r)
    try:
        yield (r,)
    finally:
        os.close(r)


def ptrace_scope() -> int | None:
    """``kernel.yama.ptrace_scope`` (None: no Yama). At 1 or more a tool command cannot read
    the memory of claude, its ancestor, where the token is."""
    try:
        with open("/proc/sys/kernel/yama/ptrace_scope") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def doctor_line(token: bool | str) -> tuple[str, str]:
    """``repo-factory doctor``'s ``claude token`` line: ``(ok|warn, text)``. ``token``: repo-factory's unit
    has a Claude token (the host checks that without reading it); a string names its kind
    (``Credential.words``), which the line starts with. What it can't promise is
    said: without a token, the copied login file; without bubblewrap, no namespace for tool
    commands; ptrace scope 0, claude's memory."""
    from . import sandbox

    try:
        boxed = sandbox.available()
    except Exception:  # noqa: BLE001 - a doctor line
        boxed = False
    scope = ptrace_scope()
    if not token:
        return "warn", (
            "no token for repo-factory: builds copy your Claude login file into the "
            "session, where a build's own commands can read it (run 'repo-factory setup "
            "building' for a Claude login token, from 'claude setup-token')"
        )
    text = (
        f"{token}, " if isinstance(token, str) else ""
    ) + "by pipe only, never in claude's environment or command line"
    warn = []
    if boxed:
        text += "; tool commands run in a nested namespace without it"
    else:
        warn.append(
            "no bubblewrap: builds don't run, and Open in Claude Code's commands get "
            "a scrubbed environment but no namespace"
        )
    if scope == 0:
        warn.append(
            "kernel.yama.ptrace_scope is 0: a tool command could read claude's memory (set it to 1)"
        )
    if warn:
        return "warn", text + "; " + "; ".join(warn)
    return "ok", text + (f" (ptrace_scope {scope})" if scope is not None else "")


def process_env(extra: dict | None = None, environ=None) -> dict:
    """repo-factory's environment for an unsandboxed ``claude`` (the no-tools prompts), with ``extra`` on top and no login variable; ``piped`` adds the pipe."""
    env = dict(os.environ if environ is None else environ)
    env.update(extra or {})
    return scrub(env)

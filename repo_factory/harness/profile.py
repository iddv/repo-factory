"""One worker profile, enforced.

``launch`` is the only way a v5 worker starts. Every launch is the same: ``ClaudeCodeHarness.run(tools="full")`` (the
``claude`` process in bubblewrap, proxy-only network, ``--permission-mode dontAsk``,
``--strict-mcp-config``, ``FULL_ALLOWED``), the CLI's default model unless the caller names
one, a kept session (so it can be resumed), and a clean config dir of its own. Inside the
sandbox the factory shows only ``harness/netproxy.py`` (``sandbox.mounts(factory=
"netproxy")``), so no builder can read v5's prompts.

``clean_config`` makes a config dir that holds credentials only: no ``CLAUDE.md``,
settings, memory, plugins, skills, agents, commands or MCP servers.

The login (``auth_source``): with ``CLAUDE_CODE_OAUTH_TOKEN`` or ``ANTHROPIC_API_KEY`` in the
environment (a long-lived token from ``claude setup-token``, or an API key) claude gets it
through a pipe (``harness/handoff.py``; never in its environment, so no tool command can print
it) and no file is copied. Otherwise the owner's credentials
file is copied into the clean config dir right before every launch and removed after it.
While a session runs, ``Mirror`` keeps the two files in step: an OAuth refresh rotates the
refresh token, so when another Claude Code process on the machine refreshes the owner's
login, the copy's refresh token stops working, and the session dies with "OAuth session
expired and could not be refreshed" (seen in a real build: the owner's file was refreshed,
and the build session failed 27 seconds later). The mirror copies the owner's
new file into the session's dir within seconds (a CLI that reads its credentials file again
finds the newer login; one that doesn't is covered by the retry below). It never writes the
other way: the session's config dir is inside the sandbox, so its credentials file is bytes
a sandboxed session controls, and nothing of it reaches the owner's ``~/.claude`` (a
long-lived token from ``claude setup-token`` avoids the copy entirely). A run that still ends
on a login failure (``auth_failed``) is retried once after a fresh copy, resuming its own session; a
second failure is the caller's (v5: the stage fails with "Claude login expired"). Nothing
here ever logs or prints a credential.

``fingerprint`` reads the transcript's init event: the resolved model, the tools, the MCP
servers, the permission mode and the CLI version, plus ``PROFILE``, the sandbox's network
mode and the config kind. ``hash`` covers exactly those fields; ``info`` (skills, plugins,
agents, the API key source) is kept for reading and not hashed. ``check_comparable`` is the
guard a comparison of runs applies: runs whose hashes differ, or that have none, are not
compared. A session lists no MCP server: any in its init event breaks the profile.

The workspace is bound at a fixed path, ``/work/product``, in every session
(``Launch.workdir``), and the sessions that write or run repo-factory's exam get it at
``/work/exam`` (read-write for the verifier and the checks sessions, read-only for repairs).
A product's path is the same for the verifier, the repairer and the harness's own runs.

The Claude token goes by pipe instead of the sandbox's environment, and every tool command
runs through repo-factory's shell prefix (``harness/shell.py``, bound read-only at
``/opt/repo_factory/bin/factory-shell``): one nested user, pid and mount namespace per sandbox, so a
command sees neither claude nor the network bridge, and no login variable."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from . import claude_code, handoff, links, sandbox
from .base import HarnessResult

PROFILE = "full-v4"  # bump on ANY change to tools, sandbox mounts, net mode or CLI flags
FACTORY = "netproxy"  # sandbox.mounts(factory=...): what of the factory is visible inside
CORE_TOOLS = ("Bash", "Read", "Edit", "Write", "Glob", "Grep")
CREDENTIALS = ".credentials.json"
# What a clean config dir never holds (Claude Code reads each of these at start).
UNCLEAN = (
    "CLAUDE.md",
    "settings.json",
    "settings.local.json",
    "skills",
    "plugins",
    "agents",
    "commands",
    "output-styles",
    "hooks",
    "memory",
    "rules",
    ".mcp.json",
)
HASHED = ("name", "cli", "model", "tools", "mcp_servers", "permission_mode", "net", "config")

_active: dict[str, int] = {}  # config dir -> launches running in it (this process)
_mirrors: dict[str, Mirror] = {}  # config dir -> its credentials mirror
_active_lock = threading.Lock()
MIRROR_S = 2.0  # how often a running session's credentials are synced
# What a session resumed after a login failure is told
AUTH_CONTINUE = (
    "Your previous turn was cut off by a login error, which is fixed now. "
    "Continue the task exactly where you stopped; do not start over."
)


class ProfileError(RuntimeError):
    """A launch that would break the profile, or a run whose init event does."""


class ProfileMismatch(ValueError):
    """Runs with different (or missing) fingerprints. ``field``: the first differing one."""

    def __init__(self, msg: str, field: str):
        super().__init__(msg)
        self.field = field


# -- the clean config dir --------------------------------------------------------------
def source_config() -> Path:
    """The owner's config dir, where credentials come from (``$CLAUDE_CONFIG_DIR`` or
    ``~/.claude``)."""
    return claude_code.config_dir()


def _remove(p: Path) -> None:
    if p.is_symlink() or p.is_file():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)


def auth_source() -> str:
    """``env`` when ``CLAUDE_CODE_OAUTH_TOKEN`` or ``ANTHROPIC_API_KEY`` is set (claude gets it
    through a pipe, ``handoff.piped``; no file is copied), else ``file`` (the owner's
    credentials file, copied)."""
    return "env" if handoff.source() else "file"


def clean_config(dir: Path, source: Path | None = None, copy: bool = True) -> Path:
    """A config dir holding credentials only: no CLAUDE.md, settings, memory, plugins,
    skills, MCP. An existing dir keeps its sessions (``projects/*/*.jsonl``, so a later run
    can resume them) and loses everything in ``UNCLEAN``, per-project memory and any MCP
    server in ``.claude.json``. The credentials file is copied from ``source`` (default
    ``source_config()``) when it has one; without it, auth must come from the environment
    (``ANTHROPIC_API_KEY`` or ``CLAUDE_CODE_OAUTH_TOKEN``, which the sandbox passes).
    ``copy`` False (``auth_source() == "env"``): no copy, and a stale one is removed."""
    d = Path(dir)
    d.mkdir(parents=True, exist_ok=True)
    for name in UNCLEAN:
        _remove(d / name)
    for mem in (d / "projects").glob("*/memory"):
        _remove(mem)
    cj = d / ".claude.json"
    if cj.is_file():
        try:
            state = json.loads(cj.read_text())
        except json.JSONDecodeError:
            state = None
        if not isinstance(state, dict):
            cj.unlink()
        else:
            state.pop("mcpServers", None)
            for proj in (state.get("projects") or {}).values():
                if isinstance(proj, dict):
                    proj.pop("mcpServers", None)
            cj.write_text(json.dumps(state, indent=2))
    src = Path(source or source_config()) / CREDENTIALS
    if src.resolve() == (d / CREDENTIALS).resolve():
        return d
    if not copy:
        if (d / CREDENTIALS).is_file():
            (d / CREDENTIALS).unlink()
        return d
    if src.is_file():
        _put(d / CREDENTIALS, src.read_bytes())
    return d


def _put(dst: Path, data: bytes) -> None:
    """Replace a credentials file in one step, 0600 from the start."""
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink()


def _read(p: Path) -> bytes | None:
    try:
        return p.read_bytes() if p.is_file() else None
    except OSError:
        return None


class Mirror:
    """Keeps a session's copied credentials up to date with the owner's file while sessions
    run in ``cfg``, one way only (see the module doc). ``last``: the owner's content at the
    last sync. ``step`` is one sync (the thread calls it every ``MIRROR_S``; tests call it)."""

    def __init__(self, cfg: Path, source: Path, copied: bytes | None):
        self.cfg, self.source, self.last = Path(cfg), Path(source), copied
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def step(self) -> str | None:
        """One sync: ``to_session`` when the owner's login changed since the last one
        (another process refreshed it), else None. What the session writes into its copy
        stays there."""
        with self._lock:
            own = _read(self.source / CREDENTIALS)
            ses = _read(self.cfg / CREDENTIALS)
            if not own or own == ses:
                if own:
                    self.last = own
                return None
            if own != self.last:  # another process refreshed the login
                _put(self.cfg / CREDENTIALS, own)
                self.last = own
                return "to_session"
            return None

    def _loop(self) -> None:
        while not self._stop.wait(MIRROR_S):
            try:
                self.step()
            except OSError:
                pass

    def start(self) -> Mirror:
        self._thread = threading.Thread(target=self._loop, name="brb-cred-mirror", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


def is_clean(dir: Path) -> list[str]:
    """What in ``dir`` a clean config dir must not hold ([] = clean)."""
    d = Path(dir)
    bad = [n for n in UNCLEAN if (d / n).exists()]
    bad += [str(m.relative_to(d)) for m in (d / "projects").glob("*/memory")]
    try:
        state = json.loads((d / ".claude.json").read_text())
    except (OSError, json.JSONDecodeError):
        state = {}
    if isinstance(state, dict) and (
        state.get("mcpServers")
        or any(
            isinstance(p, dict) and p.get("mcpServers")
            for p in (state.get("projects") or {}).values()
        )
    ):
        bad.append(".claude.json:mcpServers")
    return bad


def _sync_credentials(cfg: Path, source: Path, copied: bytes | None) -> None:
    """After a run: remove the session's copy of the credentials from ``cfg``. Nothing of it
    is written back to the owner's dir (decision O-3)."""
    f = cfg / CREDENTIALS
    if f.exists() and source.resolve() != cfg.resolve():
        f.unlink()


# -- the fingerprint -------------------------------------------------------------------
def init_event(transcript: Path) -> dict | None:
    p = Path(transcript)
    if not p.is_file():
        return None
    for line in p.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "system" and ev.get("subtype") == "init":
            return ev
    return None


def _names(items) -> list[str]:
    return sorted(str(x.get("name") if isinstance(x, dict) else x) for x in items or [])


def profile_hash(fp: dict) -> str:
    return hashlib.sha256(
        json.dumps({k: fp.get(k) for k in HASHED}, sort_keys=True).encode()
    ).hexdigest()


def fingerprint(transcript: Path, net: str | None = None, config: str = "clean") -> dict:
    """From the stream-json init event: model, tools (sorted), mcp_servers,
    permissionMode, CLI version; plus PROFILE, the sandbox net mode and the config kind.
    ``hash`` = sha256 of those. Without an init event: ``{"name", "hash": None, "missing":
    True}``."""
    ev = init_event(transcript)
    if ev is None:
        return {"name": PROFILE, "hash": None, "missing": True}
    fp = {
        "name": PROFILE,
        "cli": ev.get("claude_code_version"),
        "model": ev.get("model"),
        "tools": sorted(ev.get("tools") or []),
        "mcp_servers": _names(ev.get("mcp_servers")),
        "permission_mode": ev.get("permissionMode"),
        "net": net or sandbox.net_mode(),
        "config": config,
    }
    fp["hash"] = profile_hash(fp)
    fp["info"] = {
        "skills": _names(ev.get("skills")),
        "plugins": _names(ev.get("plugins")),
        "agents": _names(ev.get("agents")),
        "api_key_source": ev.get("apiKeySource"),
    }
    return fp


def violations(fp: dict, tools: str = "full", declared=()) -> list[str]:
    """What in a fingerprint breaks the profile ([] = fine). ``tools``: the launch's tool
    profile; ``read`` and ``none`` (a project's evolve) must have no tool beyond Read, Glob and
    Grep instead of the core set. ``declared``: MCP servers a launch may list; any other
    server breaks the profile."""
    if fp.get("missing"):
        return ["no init event"]
    out = []
    extra_servers = sorted(set(fp.get("mcp_servers") or []) - set(declared or ()))
    if extra_servers:
        out.append(
            f"mcp_servers {extra_servers} (must be none beyond the declared "
            f"{sorted(declared or ())})"
        )
    if tools in ("read", "none"):
        extra = sorted(set(fp.get("tools") or []) - set(claude_code.READ_TOOLS))
        if extra:
            out.append(f"tools beyond the read profile {extra}")
    else:
        missing = sorted(set(CORE_TOOLS) - set(fp.get("tools") or []))
        if missing:
            out.append(f"tools missing {missing}")
    if fp.get("net") == "host":
        out.append("net mode host")
    if fp.get("permission_mode") not in (None, "dontAsk"):
        out.append(f"permission mode {fp['permission_mode']}")
    return out


def differing(a: dict | None, b: dict | None) -> list[str]:
    """The hashed fields in which two fingerprints differ (``fingerprint`` if either has
    none)."""
    if not a or not b or not a.get("hash") or not b.get("hash"):
        return ["fingerprint"]
    if a["hash"] == b["hash"]:
        return []
    return [k for k in HASHED if a.get(k) != b.get(k)] or ["hash"]


def check_comparable(fps: dict[str, dict | None]) -> None:
    """``{label: fingerprint}``; raises ``ProfileMismatch`` naming the first differing field
    (a comparison exits 2 on it)."""
    items = list(fps.items())
    for label, fp in items:
        if not fp or not fp.get("hash"):
            raise ProfileMismatch(
                f"{label}: no profile fingerprint; runs without one can't be compared",
                "fingerprint",
            )
    ref_label, ref = items[0] if items else (None, None)
    for label, fp in items[1:]:
        diff = differing(ref, fp)
        if diff:
            f = diff[0]
            raise ProfileMismatch(
                f"{ref_label} and {label} ran different profiles: {f} "
                f"{ref.get(f)!r} != {fp.get(f)!r}",
                f,
            )


def cli_version() -> str | None:
    """``claude --version``, e.g. ``2.1.288 (Claude Code)`` (None when it can't run)."""
    try:
        p = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (p.stdout or "").strip() or None


# -- the launch ------------------------------------------------------------------------
def check_launch() -> None:
    """Before a launch: the network must not be the host's (``REPO_FACTORY_WORKER_NET``)."""
    if sandbox.net_mode() == "host":
        raise ProfileError(
            f"profile {PROFILE}: REPO_FACTORY_WORKER_NET=host is not allowed "
            f"(the worker would share the host's loopback)"
        )


def _harness(model: str | None, max_turns: int, max_usd: float):
    h = claude_code.ClaudeCodeHarness(max_turns=max_turns, max_usd=max_usd)
    h.model = model  # the CLI default unless named ($REPO_FACTORY_WORKER_MODEL ignored)
    return h


def command(
    workspace: Path,
    *,
    config_dir: Path,
    max_turns: int,
    max_usd: float,
    session: dict | None = None,
    model: str | None = None,
    proxy_dir: Path | None = None,
    tools: str = "full",
    partial: bool = False,
    spec=None,
) -> list[str]:
    """The argv ``launch`` runs (for inspection and tests; nothing is started)."""
    return _harness(model, max_turns, max_usd).command(
        workspace,
        max_turns,
        max_usd,
        session or {"id": "<session>"},
        tools,
        proxy_dir,
        Path(config_dir),
        FACTORY,
        partial,
        spec or None,
    )


def _enter(cfg: Path, src: Path, copy: bool) -> None:
    """A launch starts in ``cfg``: the clean config (a fresh copy of the login when ``copy``)
    and, for the first launch running there, the credentials mirror."""
    key = str(Path(cfg).resolve())
    with _active_lock:
        clean_config(cfg, src, copy=copy)
        _active[key] = _active.get(key, 0) + 1
        if copy and key not in _mirrors and src.resolve() != Path(cfg).resolve():
            _mirrors[key] = Mirror(cfg, src, _read(Path(cfg) / CREDENTIALS)).start()


def _leave(cfg: Path, src: Path) -> None:
    """A launch in ``cfg`` ended; the last one stops the mirror, syncs once more and removes
    the copied login."""
    key = str(Path(cfg).resolve())
    with _active_lock:
        _active[key] -= 1
        if _active[key]:
            return
        del _active[key]
        m = _mirrors.pop(key, None)
        copied = _read(Path(cfg) / CREDENTIALS)
        if m is not None:
            m.stop()
            try:
                m.step()
            except OSError:
                pass
            copied = m.last
        _sync_credentials(cfg, src, copied)


def _retry_auth(r, prompt: str, session: dict, run_once):
    """The run ended on a login failure: once more, with the login copied again (the owner's
    file may hold a newer token by now). A run that had started resumes its own session
    (``AUTH_CONTINUE``), so its work so far isn't redone; one that never got going runs the
    prompt again. The result counts both runs (cost, tokens, tool calls, turns, wall time);
    the transcript holds both."""
    first = Path(r.transcript_path)
    text_1 = first.read_text(errors="replace") if first.is_file() else ""
    err_f = first.parent / "stderr.txt"
    err_1 = err_f.read_text(errors="replace") if err_f.is_file() else ""
    started = bool(r.session_id) and bool(r.tool_calls or r.num_turns)
    if started:
        sess = {
            "id": str(uuid.uuid4()),
            "resume": r.session_id,
            "base_cost_usd": r.session_cost_usd or 0.0,
            "lineage": [x for x in (session.get("resume"), *(session.get("lineage") or ())) if x],
        }
        r2 = run_once(AUTH_CONTINUE, sess)
        if getattr(r2, "resume_failed", False):
            r2 = run_once(prompt, {**session, "id": str(uuid.uuid4())})
    else:
        r2 = run_once(prompt, {**session, "id": str(uuid.uuid4())})
    second = Path(r2.transcript_path)
    if text_1 and second.is_file():
        second.write_text(text_1 + second.read_text(errors="replace"))
    if err_1:
        err2 = second.parent / "stderr.txt"
        err2.write_text(err_1 + (err2.read_text(errors="replace") if err2.is_file() else ""))
    add = lambda a, b: (a or 0) + (b or 0)  # noqa: E731
    cost = (
        None
        if r.cost_usd is None and r2.cost_usd is None
        else round(add(r.cost_usd, r2.cost_usd), 7)
    )
    return dataclasses.replace(
        r2,
        cost_usd=cost,
        tool_calls=add(r.tool_calls, r2.tool_calls),
        input_tokens=add(r.input_tokens, r2.input_tokens),
        output_tokens=add(r.output_tokens, r2.output_tokens),
        wall_s=add(r.wall_s, r2.wall_s),
        model=r2.model or r.model,
        num_turns=None
        if r.num_turns is None and r2.num_turns is None
        else add(r.num_turns, r2.num_turns),
        resume_failed=False,
    )


def session_writable(workspace: Path, cfg: Path, spec=None) -> list[Path]:
    """The host folders a session can write: its workspace, its own config dir and every
    read-write bind of its launch (the exam, the proposals folder). ``links.scrub`` runs on
    each after the session, before the host reads any of them."""
    out = [Path(workspace), Path(cfg)]
    for m in getattr(spec, "mounts", None) or []:
        if m.get("kind") == "rw" and m.get("src"):
            out.append(Path(m["src"]))
    return out


def launch(
    prompt: str,
    workspace: Path,
    *,
    config_dir: Path,
    max_turns: int,
    max_usd: float,
    timeout_s: int,
    session: dict | None = None,
    model: str | None = None,
    transcript: Path | None = None,
    harness=None,
    source: Path | None = None,
    tools: str = "full",
    partial: bool = False,
    on_start=None,
    spec=None,
) -> HarnessResult:
    """The only way a v5 worker starts. ``session``: ``{"id", "resume", "base_cost_usd", "lineage", "source_config"}``
    (default: a new kept session). ``transcript``: where the stream-json log goes (default
    ``<workspace>/transcript.jsonl``; keep it outside the workspace for builds). ``harness``:
    a stand-in for tests (default: ``ClaudeCodeHarness``). The result carries
    ``fingerprint``; a run whose init event breaks the profile raises ``ProfileError``.

    ``max_usd`` is this run's own allowance, resumed or not. A resumed (forked) session
    *reports* ``total_cost_usd`` cumulatively (``claude_code.run_cost`` subtracts
    ``base_cost_usd``), but ``--max-budget-usd`` counts only the resumed run's own spend.
    Measured on CLI 2.1.287: a fork with $0.092 of prior
    cost and a $0.06 cap ran 4 turns and stopped at $0.064 of its own; in the lending run
    a re-verify fork with $1.58 prior cost and a $1.00 cap ran 8 turns. So the flag gets the
    allowance as is; adding the prior cost would let a fork overspend by that much.

    ``tools="read"`` is the same sandbox with Read, Glob and Grep only (``evolve.tools``);
    ``partial=True`` writes text deltas into the transcript and ``on_start`` gets the
    process, so a caller can stop it; ``tools="none"`` runs without tools or bubblewrap.

    ``spec``: a ``harness.launch.Launch`` (full profile only): the session's binds, env,
    extra allow rules and the workspace's path inside the sandbox."""
    check_launch()
    workspace = Path(workspace)
    cfg = Path(config_dir)
    src = Path(source or source_config())
    session = dict(session or {"id": str(uuid.uuid4())})
    h = harness or _harness(model, max_turns, max_usd)
    more = {
        k: v
        for k, v in (("partial", partial), ("on_start", on_start), ("launch", spec or None))
        if v
    }
    copy = auth_source() == "file"
    written = session_writable(workspace, cfg, spec)
    scrubbed: dict[str, list[str]] = {}

    def run_once(text: str, sess: dict):
        _enter(cfg, src, copy)  # the login copied right before every launch
        try:
            return h.run(
                text,
                workspace,
                timeout_s,
                max_turns=max_turns,
                max_usd=max_usd,
                session=sess,
                tools=tools,
                config_dir=cfg,
                transcript=transcript,
                factory=FACTORY,
                **more,
            )
        finally:
            # before the host reads anything the session could have written
            for k, v in links.scrub_all(written).items():
                scrubbed.setdefault(k, []).extend(v)
            _leave(cfg, src)

    r = run_once(prompt, session)
    if getattr(r, "auth_failed", False) and copy:
        r = _retry_auth(r, prompt, session, run_once)
    if transcript and Path(r.transcript_path) != Path(transcript):
        # a stand-in harness that writes its log into the workspace
        Path(transcript).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(r.transcript_path), transcript)
        r.transcript_path = Path(transcript)
    r.fingerprint = fingerprint(r.transcript_path)
    r.scrubbed = scrubbed
    bad = violations(r.fingerprint, tools)
    if bad and not r.fingerprint.get("missing"):
        raise ProfileError(
            f"profile {PROFILE} broken by the run's init event: "
            f"{'; '.join(bad)} ({r.transcript_path})"
        )
    return r

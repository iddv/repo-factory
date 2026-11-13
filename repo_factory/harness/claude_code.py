"""Claude Code harness: ``claude -p`` in the worker's workspace (a git repo the
orchestrator creates and diffs for touch tracking).

Four tool profiles. ``restricted`` (v1 to v3): reading/editing files inside the workspace
and running ``python check.py``; there is no Glob/Grep and no other shell access.
``full`` (build v4's one-shot build and repairs): Claude Code's default tool set and an
unrestricted shell, with the ``claude`` process itself inside bubblewrap
(``harness/sandbox.py``); it refuses to run where bubblewrap doesn't work. ``read`` (a
project's evolve, ``evolve.tools``): the same sandbox with Read, Glob and Grep only: no
shell, no writes, no web. ``none``: no tools at all and no sandbox (where bubblewrap
misbehaves: a ``claude -p`` with no tools cannot touch anything). All run under
``--permission-mode dontAsk`` and ``--strict-mcp-config``, never with permission checks
skipped. Every profile gets the Claude login through a pipe, never in its environment
(``harness/handoff.py``); the sandboxed ones run each tool command through repo-factory's shell prefix
(``harness/shell.py``), and ``read`` denies its tools ``/proc``, ``/sys`` and the config dirs.
Usage and tool calls are parsed from the stream-json event log.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import handoff
from .base import HarnessResult

TOOLS = "Read,Write,Edit,Bash"
PROFILES = ("restricted", "full", "read", "none")
SANDBOXED = ("full", "read")  # the claude process runs inside bubblewrap
READ_TOOLS = ("Read", "Glob", "Grep")
# The full profile: no --tools restriction (Claude Code's default set) and every tool a
# developer uses allowed bare, the shell included; the sandbox is the boundary. Names from
# older CLIs are harmless. Under dontAsk the rest (cron, remote triggers, notifications,
# workflows, messaging other sessions) is denied.
FULL_ALLOWED = (
    "Bash",
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
    "NotebookEdit",
    "Task",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "TaskStop",
    "TaskOutput",
    "Monitor",
    "TodoWrite",
    "WebFetch",
    "WebSearch",
    "Skill",
    "ToolSearch",
    "BashOutput",
    "KillShell",
)
DEFAULT_MAX_USD = 1.00  # per-worker dollar cap; 001's p95 worker was $0.67
# A run that ended because the Claude login failed: the CLI's own words (an expired or
# revoked OAuth session, a bad key) and the API's 401.
AUTH_ERROR = re.compile(
    r"Failed to authenticate|OAuth session expired|OAuth token has expired|"
    r"could not be refreshed|Invalid API key|Please run /login|"
    r"authentication_failed|\b401\b.*auth",
    re.I,
)


def max_usd_default() -> float | None:
    """``$REPO_FACTORY_WORKER_MAX_USD`` (0 or empty disables the cap), else $1.00."""
    v = os.environ.get("REPO_FACTORY_WORKER_MAX_USD")
    if v is None:
        return DEFAULT_MAX_USD
    return float(v) if v.strip() and float(v) > 0 else None


def read_deny(config_dir: Path | None = None) -> list[str]:
    """The ``read`` profile's deny rules: Read, Glob and Grep never open ``/proc``, ``/sys``
    or a Claude config dir (the run's, the owner's), whatever the sandbox shows. Measured on
    CLI 2.1.300 in bubblewrap: without them ``Grep`` on ``/proc/self/environ`` returned
    claude's environment (``Read`` refuses ``/proc`` on its own); with them all three are
    refused, and a ``Grep`` from ``/`` skips those trees. A ``Read(...)`` rule covers Grep
    and Glob too."""
    home = Path(os.environ.get("HOME") or Path.home())
    dirs = [
        "/proc",
        "/sys",
        str(Path(config_dir) if config_dir is not None else _global_config_dir()),
        str(home / ".claude"),
        str(home / ".config"),
    ]
    out = [f"Read(/{d}/**)" for d in dict.fromkeys(dirs)]  # "//abs/path": an absolute rule
    return out + [f"Read(/{home / '.claude.json'})"]


def allowed_tools(workspace: Path) -> list[str]:
    """File tools only inside the workspace; the only shell command is the check."""
    root = f"/{Path(workspace).resolve()}/**"  # "//abs/path" = absolute path rule
    return [
        f"Read({root})",
        f"Edit({root})",
        f"Write({root})",
        "Bash(python check.py:*)",
        "Bash(python3 check.py:*)",
    ]


class ClaudeCodeHarness:
    name = "claude-code"

    def __init__(
        self,
        model: str | None = None,
        max_turns: int | None = 30,
        max_usd: float | None | str = "env",
    ):
        self.model = model or os.environ.get("REPO_FACTORY_WORKER_MODEL")
        self.max_turns = max_turns
        self.max_usd = max_usd_default() if max_usd == "env" else max_usd
        self.net_logs: dict[str, list] = {}  # full profile: workspace -> proxy requests

    def command(
        self,
        workspace: Path,
        max_turns: int | None = None,
        max_usd: float | None = None,
        session: dict | None = None,
        tools: str = "restricted",
        proxy_dir: Path | None = None,
        config_dir: Path | None = None,
        factory: str = "package",
        partial: bool = False,
        launch=None,
    ) -> list[str]:
        """``max_turns``/``max_usd`` override the harness defaults for one run. ``session``
        (``{"id", "resume"}``) keeps the session on disk under our own id so a later run
        can ``--resume`` it; with ``resume`` this run forks that session (``--fork-session``),
        so the parent's session stays resumable for a retry or a second branch;
        ``session["fork"] = False`` resumes it in place instead (one linear session: a chat
        thread's). ``partial``: stream text deltas (``--include-partial-messages``). ``tools``:
        the profile (``restricted`` or ``full``, see the module doc); ``full`` raises
        ``sandbox.SandboxUnavailable`` without a working bubblewrap. ``proxy_dir``: the
        running network proxy's dir (``full``; ``run`` starts it). ``config_dir``: the Claude
        Code config dir of this run (default: the process-global one, ``config_dir()``);
        ``factory``: the sandbox's view of the factory (``sandbox.mounts``). ``launch``: a
        ``harness.launch.Launch`` (full profile only): its allow rules after the profile's,
        its binds, non-secret env and workspace path in the sandbox. The prompt goes on stdin
        (``--allowedTools`` takes several values)."""
        if tools not in PROFILES:
            raise ValueError(f"unknown worker tool profile {tools!r} (one of {PROFILES})")
        if launch is not None and tools != "full":
            raise ValueError("a launch's extras run in the full profile only")
        if tools in SANDBOXED:
            from . import sandbox

            cmd = sandbox.command(
                workspace,
                proxy_dir,
                config_dir,
                factory,
                extra=launch.mounts if launch else None,
                setenv=launch.env if launch else None,
                workdir=getattr(launch, "workdir", None) if launch else None,
            ) + ["-p", "--output-format", "stream-json", "--verbose"]
        else:
            cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
        if partial:
            cmd.append("--include-partial-messages")
        if session is None:
            cmd.append("--no-session-persistence")
        if tools == "full":
            base = list(launch.only) if launch is not None and launch.only else FULL_ALLOWED
            cmd += ["--allowedTools", *base, *(launch.allow if launch else ())]
        elif tools == "read":
            cmd += [
                "--tools",
                ",".join(READ_TOOLS),
                "--allowedTools",
                *READ_TOOLS,
                "--settings",
                json.dumps({"permissions": {"deny": read_deny(config_dir)}}, separators=(",", ":")),
            ]
        elif tools == "none":
            cmd += ["--tools", ""]
        else:
            cmd += ["--tools", TOOLS, "--allowedTools", *allowed_tools(workspace)]
        cmd += ["--permission-mode", "dontAsk", "--strict-mcp-config"]
        if session is not None:
            fork = session.get("fork", True)
            if session.get("resume"):
                cmd += ["--resume", session["resume"]]
                if fork:
                    cmd.append("--fork-session")
            if fork or not session.get("resume"):
                cmd += ["--session-id", session["id"]]
        if self.model:
            cmd += ["--model", self.model]
        turns = max_turns or self.max_turns
        usd = max_usd or self.max_usd
        if turns:
            cmd += ["--max-turns", str(turns)]
        if usd:
            cmd += ["--max-budget-usd", f"{usd:.2f}"]
        return cmd

    def run(
        self,
        prompt: str,
        workspace: Path,
        timeout_s: int,
        max_turns: int | None = None,
        max_usd: float | None = None,
        session: dict | None = None,
        tools: str = "restricted",
        config_dir: Path | None = None,
        transcript: Path | None = None,
        factory: str = "package",
        partial: bool = False,
        on_start=None,
        launch=None,
    ) -> HarnessResult:
        """``config_dir``: this run's Claude Code config dir (``CLAUDE_CONFIG_DIR``; default:
        the process-global one). ``transcript``: where the stream-json log goes (default
        ``<workspace>/transcript.jsonl``; ``stderr.txt`` goes next to it). ``factory``: see
        ``sandbox.mounts``. ``session["source_config"]``: another config dir to find the
        resumed session in (a fork into a config dir of its own). ``partial``: see
        ``command``. ``on_start(popen)``: called once the process runs; it then leads a
        process group of its own, so the caller can stop it (a chat reply's Stop).
        ``launch``: the session's extras (``command``); its host names join the proxy's
        allow-list when the build has one."""
        workspace = Path(workspace)
        transcript = Path(transcript) if transcript else workspace / "transcript.jsonl"
        t0 = time.time()
        ok, timed_out = True, False
        stderr = ""
        if session is not None and session.get("resume"):
            # A fork may run in another directory than the session was created in: make the
            # session (and its lineage) resumable from here first.
            src = session.get("source_config")
            # the session's project is keyed by the directory claude runs in: the fixed
            # path inside the sandbox when the launch has one (/work/product)
            inside = getattr(launch, "workdir", None) if launch is not None else None
            prepare_resume(
                session["resume"],
                Path(inside) if inside else workspace,
                session.get("lineage") or (),
                config_dir=config_dir,
                sources=[src] if src else (),
            )
        with contextlib.ExitStack() as stack:
            extra, px = {}, None
            if tools in SANDBOXED:
                # The sandbox starts from a filtered environment (credentials stay off the
                # command line) and reaches the network through its own proxy.
                from . import netproxy, sandbox

                if sandbox.available() and sandbox.net_mode() == "proxy":
                    hosts = sandbox.net_hosts()
                    if hosts is not None and launch is not None:
                        hosts = list(dict.fromkeys([*hosts, *launch.hosts]))
                    px = stack.enter_context(netproxy.Proxy(hosts=hosts))
                    self.net_logs[str(workspace)] = px.log
                extra["env"] = sandbox.process_env(config_dir)
            else:
                extra["env"] = handoff.process_env(
                    {"CLAUDE_CONFIG_DIR": str(config_dir)} if config_dir is not None else None
                )
                if tools == "restricted":  # its shell runs on the host
                    from . import shell

                    extra["env"].update(shell.env("plain", shell.install()))
            cmd = self.command(
                workspace,
                max_turns,
                max_usd,
                session,
                tools,
                px.dir if px else None,
                config_dir,
                factory,
                partial,
                launch,
            )
            transcript.parent.mkdir(parents=True, exist_ok=True)
            # the login through a pipe, never in the environment (harness/handoff.py)
            fds = stack.enter_context(handoff.piped(extra["env"]))
            extra["pass_fds"] = fds
            with open(transcript, "w") as out:
                try:
                    if on_start is None:
                        p = subprocess.run(
                            cmd,
                            cwd=workspace,
                            input=prompt,
                            stdout=out,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=timeout_s,
                            **extra,
                        )
                        rc, err = p.returncode, p.stderr or ""
                    else:
                        rc, err = _run_owned(
                            cmd, workspace, prompt, out, timeout_s, on_start, extra
                        )
                    ok = rc == 0
                    stderr = err
                    if err:
                        (transcript.parent / "stderr.txt").write_text(err)
                except subprocess.TimeoutExpired:
                    ok, timed_out = False, True
        usage = parse_stream(transcript)
        resume_failed = (
            bool(session and session.get("resume"))
            and not ok
            and not timed_out
            and usage["subtype"] is None
            and ("No conversation found" in stderr or usage["tool_calls"] == 0)
        )
        cost = run_cost(usage["cost_usd"], session)
        auth = (
            (not ok or usage["is_error"])
            and not timed_out
            and (usage["auth_error"] or bool(AUTH_ERROR.search(stderr)))
        )
        if timed_out:
            reason = "timeout"
        elif usage["subtype"] == "error_max_turns":
            reason = "max_turns"
        elif usage["subtype"] == "error_max_budget_usd":
            reason = "max_budget"
        elif auth:
            reason = "auth"  # the Claude login (profile.launch retries once)
        elif not ok or usage["is_error"]:
            reason = "error"
        else:
            reason = "ok"
        return HarnessResult(
            artifact_dir=workspace,
            tool_calls=usage["tool_calls"],
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cost_usd=cost,
            transcript_path=transcript,
            exit_ok=ok and not usage["is_error"],
            wall_s=time.time() - t0,
            exit_reason=reason,
            session_id=usage["session_id"] or (session or {}).get("id"),
            session_cost_usd=usage["cost_usd"],
            model=usage["model"],
            num_turns=usage["num_turns"],
            resume_failed=resume_failed,
            auth_failed=auth,
            tools_by_server=usage["tools_by_server"],
            skills=usage["skills"],
            agents=usage["agents"],
        )


def _run_owned(cmd, cwd, prompt, out, timeout_s, on_start, extra) -> tuple[int, str]:
    """``subprocess.run`` for a caller that may stop the run: the process leads a group of
    its own and ``on_start`` gets the ``Popen``. On the timeout the group is killed."""
    import signal

    p = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=out,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        **extra,
    )
    on_start(p)
    try:
        _, err = p.communicate(prompt, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        raise
    return p.returncode, err or ""


# -- resuming a session from another directory (build v3 §3) -------------------------------
# Claude Code keeps each session as ``<config>/projects/<project key>/<session id>.jsonl``,
# where the project key is the working directory with every character other than
# [A-Za-z0-9] replaced by "-", and ``--resume <id>`` looks in the current directory's
# project only. A design task's forks run in their own copies of the head's workspace, so
# before a resume the harness copies the session file (and those of its lineage, in case a
# forked session refers back to its parent's) into this directory's project store. If the
# resume still fails, the run reports ``resume_failed`` and the executor reruns it as a
# fresh session seeded with the head's diff.


def config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _global_config_dir() -> Path:
    """``config_dir()``, for functions whose ``config_dir`` argument shadows it."""
    return config_dir()


def project_key(path: Path | str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def find_session(session_id: str, root: Path | None = None) -> Path | None:
    """The stored session file for ``session_id`` in any project, or None."""
    root = (root or config_dir()) / "projects"
    if not root.is_dir():
        return None
    return next(iter(sorted(root.glob(f"*/{session_id}.jsonl"))), None)


def prepare_resume(
    session_id: str, workspace: Path, lineage=(), config_dir: Path | None = None, sources=()
) -> dict:
    """Make ``session_id`` (and the ``lineage`` sessions) resumable from ``workspace``:
    copy each stored session file found in another project into the workspace's project
    store (both for the path as given and its resolved form). ``config_dir``: the config
    dir the resuming run uses (default: the process-global one); ``sources``: further config
    dirs to look in, after ``config_dir`` (a fork into a config dir of its own). Returns
    ``{"copied": [ids], "missing": [ids]}``; never raises."""
    cfg = Path(config_dir) if config_dir is not None else _global_config_dir()
    root = cfg / "projects"
    search = [cfg, *(Path(s) for s in sources if s)]
    workspace = Path(workspace)
    targets = list(
        dict.fromkeys([root / project_key(workspace), root / project_key(workspace.resolve())])
    )
    copied, missing = [], []
    for sid in dict.fromkeys([session_id, *lineage]):
        if not sid:
            continue
        try:
            if all((t / f"{sid}.jsonl").exists() for t in targets):
                continue
            src = next((f for f in (find_session(sid, c) for c in search) if f), None)
            if src is None:
                missing.append(sid)
                continue
            for t in targets:
                t.mkdir(parents=True, exist_ok=True)
                # a session wrote these (its config dir is bound into its sandbox): links stay
                # links, and one that leads out is not copied
                from . import links

                if not (t / src.name).exists():
                    links.copy_file(src, t / src.name)
                side = src.parent / sid  # per-session side files, when there are any
                if side.is_dir() and not side.is_symlink() and not (t / sid).exists():
                    links.copytree(side, t / sid)
            copied.append(sid)
        except OSError:
            missing.append(sid)
    return {"copied": copied, "missing": missing}


def run_cost(session_cost: float | None, session: dict | None) -> float | None:
    """This run's own cost. A resumed (forked) session's ``total_cost_usd`` and
    ``modelUsage`` are cumulative over the whole session history (``usage`` is per run), so
    a resumed run's cost is its cumulative cost minus the parent session's cumulative cost
    at fork time (``session["base_cost_usd"]``)."""
    if session_cost is None:
        return None
    base = (
        float((session or {}).get("base_cost_usd") or 0.0) if (session or {}).get("resume") else 0.0
    )
    return round(max(0.0, float(session_cost) - base), 7)


def mcp_server(tool) -> str | None:
    """``rea`` for ``mcp__rea__open_binary``; None for a built-in tool."""
    m = re.fullmatch(r"mcp__([A-Za-z0-9_-]+?)__.+", str(tool or ""))
    return m.group(1) if m else None


def parse_stream(path: Path) -> dict:
    """Usage from a stream-json transcript. ``input_tokens``/``output_tokens`` come from the
    result's ``usage``, which covers this run only; ``cost_usd`` is ``total_cost_usd``, which
    for a resumed session is cumulative (see ``run_cost``). ``model``: the init event's.
    ``tools_by_server``: MCP tool calls per server (``mcp__<server>__<tool>``), e.g.
    ``{"rea": 14}``: what a capability's ledger cell shows. ``skills``: ``Skill`` calls by
    skill name; ``agents``: subagents started (``Task``/``Agent``) by ``subagent_type``: how a
    plugin's skills and agents show they were used."""
    out = {
        "tool_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": None,
        "is_error": False,
        "subtype": None,
        "session_id": None,
        "model": None,
        "num_turns": None,
        "auth_error": False,
        "tools_by_server": {},
        "skills": {},
        "agents": {},
    }
    for line in Path(path).read_text().splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("session_id") and ev.get("type") in ("system", "result"):
            out["session_id"] = ev["session_id"]
        if ev.get("type") == "system" and ev.get("subtype") == "init" and ev.get("model"):
            out["model"] = ev["model"]
        if (
            ev.get("type") == "system"
            and ev.get("subtype") == "api_retry"
            and (ev.get("error_status") == 401 or ev.get("error") == "authentication_failed")
        ):
            out["auth_error"] = True  # the API refused the login (it may recover)
        if ev.get("type") == "assistant":
            msg = ev.get("message") or {}
            for c in msg.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    out["tool_calls"] += 1
                    server = mcp_server(c.get("name"))
                    if server:
                        tbs = out["tools_by_server"]
                        tbs[server] = tbs.get(server, 0) + 1
                    inp = c.get("input") if isinstance(c.get("input"), dict) else {}
                    used = (
                        (out["skills"], inp.get("skill") or inp.get("command"))
                        if c.get("name") == "Skill"
                        else (out["agents"], inp.get("subagent_type"))
                        if c.get("name") in ("Task", "Agent")
                        else (None, None)
                    )
                    if used[0] is not None and isinstance(used[1], str) and used[1]:
                        k = used[1].strip().lstrip("/")[:80]
                        used[0][k] = used[0].get(k, 0) + 1
                elif (
                    isinstance(c, dict)
                    and c.get("type") == "text"
                    and msg.get("model") == "<synthetic>"
                    and AUTH_ERROR.search(c.get("text") or "")
                ):
                    out["auth_error"] = True  # the CLI's own message: the login is gone
        elif ev.get("type") == "result":
            u = ev.get("usage") or {}
            out["input_tokens"] = (
                (u.get("input_tokens") or 0)
                + (u.get("cache_read_input_tokens") or 0)
                + (u.get("cache_creation_input_tokens") or 0)
            )
            out["output_tokens"] = u.get("output_tokens") or 0
            out["cost_usd"] = ev.get("total_cost_usd")
            out["is_error"] = bool(ev.get("is_error"))
            out["subtype"] = ev.get("subtype")
            out["num_turns"] = ev.get("num_turns")
    return out

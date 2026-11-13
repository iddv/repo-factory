"""The ``full`` worker profile's sandbox: the ``claude`` process itself runs inside bubblewrap,
so the worker can have Claude Code's default tools and an unrestricted shell (install
dependencies, run its tests and the app, curl its API) without reaching anything else on
the machine (the host may run other services).

What the worker sees (``mounts`` lists it, with the reason for each entry):

- **read-write:** the workspace; the Claude Code config dir (``~/.claude``: auth, sessions,
  so ``--resume`` across directories keeps working) and ``~/.claude.json``; a uv cache and
  a venv root of the workers' own (``~/.cache/repo_factory/worker-uv``, ``.../worker-venvs``);
  a private ``/tmp``. The rest of ``$HOME`` is an empty tmpfs: writes there vanish.
- **read-only:** the system (``/usr``, ``/etc`` and the merged-/usr links), the claude
  install, uv, the base python, and the factory's own package and venv mounted under
  ``/opt/repo_factory`` (``check.py`` resolves its venv through ``repo_factory``; mounting it
  elsewhere keeps the directories it lives in, e.g. ``~/workspace``, invisible).
  Profile full-v1 (``harness/profile.py``, build v5) passes ``config_dir`` (its own clean
  config dir instead of ``~/.claude`` and ``~/.claude.json``) and ``factory="netproxy"``:
  of the factory only ``harness/netproxy.py`` is mounted, no package and no venv, so a
  builder can't read v5's prompts. v4 (no ``check.py`` without the package) keeps the default.
- **hidden:** everything else under ``/home``, and ``/run``, ``/var``, ``/srv``, ``/opt``,
  ``/mnt``, ``/media`` (other services' sockets and data: a docker socket is root).
- **network:** on (the API needs it), through ``netproxy``: the sandbox has its own network
  namespace (a private loopback: the worker's servers can't collide with the host's ports
  or reach its services), and its traffic goes through ``HTTPS_PROXY`` to a host-side proxy
  (on a unix socket bound in at ``/run/repo_factory``) that connects to public addresses only,
  never the host's loopback or LAN. ``$REPO_FACTORY_WORKER_NET=host`` shares the host's network
  instead (the smoke run reached another service on the host's 127.0.0.1:8765 that way).

The workers' uv cache is their own, not the one scoring builds venvs from: uv hardlinks
cached files into the venvs it makes, so a worker that could write the scoring cache could
change what the scoring tests run with. Set ``$REPO_FACTORY_WORKER_UV_CACHE`` to share a cache
on purpose.

Without bubblewrap (``isolation() != "bwrap"``) the profile refuses to run: it never
falls back to an unsandboxed shell."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

OPT = "/opt/repo_factory"  # where the factory's own files appear inside
HIDE = ("/home",)  # an empty tmpfs; binds under it come back
SYSTEM_RO = ("/etc",)  # besides contract.SYSTEM_DIRS (/usr, /bin, /lib...)
HIDE_FILES = ("/etc/machine-id", "/etc/hostname")  # in /etc, shown empty
SYSTEM_LINKS = ("/sbin",)  # merged-/usr links not in contract.SYSTEM_DIRS
PATH = f"{OPT}/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin"
# Environment passed through (never on the command line). Never a Claude login: that goes
# through a pipe (harness/handoff.py), and process_env drops every login variable.
PASS_ENV = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TERM",
    "USER",
    "LOGNAME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
)
# Claude Code's own settings that carry no credential: the endpoint and the models. A list,
# not a prefix: a header or key variable added later (ANTHROPIC_CUSTOM_HEADERS, a cloud
# provider's key) never reaches a session or its commands by accident.
PASS_ANTHROPIC = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
)
NET_MODES = ("proxy", "host")
PROXY_DIR = "/run/repo_factory"  # the proxy's socket dir, inside
PROXY_PORT = 3128  # the bridge, on the sandbox's own loopback
PROXY_URL = f"http://127.0.0.1:{PROXY_PORT}"
NO_PROXY = "localhost,127.0.0.1,::1"


class SandboxUnavailable(RuntimeError):
    """The ``full`` worker profile needs bubblewrap; there is no unsandboxed fallback."""


def available() -> bool:
    from .isolation import isolation

    return isolation() == "bwrap"


def net_mode() -> str:
    """``proxy`` (default: own network namespace, public destinations via the host proxy)
    or ``host`` (``$REPO_FACTORY_WORKER_NET=host``: the host's network, loopback included)."""
    m = os.environ.get("REPO_FACTORY_WORKER_NET") or "proxy"
    if m not in NET_MODES:
        raise SandboxUnavailable(f"REPO_FACTORY_WORKER_NET={m!r}: one of {NET_MODES}")
    return m


NET_HOSTS_ENV = "REPO_FACTORY_WORKER_NET_HOSTS"


def net_hosts() -> list[str] | None:
    """The proxy's host allowlist (``netproxy.allowlist``): ``$REPO_FACTORY_WORKER_NET_HOSTS``, a
    comma- or space-separated list of names, set by a maintenance fix (``maintain.net:
    allowlist``); unset or empty: None, any public destination (builds)."""
    v = os.environ.get(NET_HOSTS_ENV, "").replace(",", " ").split()
    return v or None


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def worker_uv_cache() -> Path:
    return Path(
        os.environ.get("REPO_FACTORY_WORKER_UV_CACHE")
        or home() / ".cache" / "repo_factory" / "worker-uv"
    )


def worker_venvs() -> Path:
    return Path(
        os.environ.get("REPO_FACTORY_WORKER_VENV_ROOT")
        or home() / ".cache" / "repo_factory" / "worker-venvs"
    )


def claude_binary() -> Path:
    from ..claude_cli import find

    exe = find()
    if not exe:
        raise SandboxUnavailable("the claude CLI is not on PATH (nor build.claude_command)")
    return Path(os.path.realpath(exe))


def claude_install_dir(real: Path) -> Path:
    """The claude install: ``~/.local/share/claude`` for the native installer (its binary is
    ``versions/<v>``), else the binary's own directory."""
    for p in real.parents:
        if p.name == "versions":
            return p.parent
    return real.parent


def site_packages() -> str:
    """The factory venv's site-packages, relative to the venv."""
    import sysconfig

    return os.path.relpath(sysconfig.get_paths()["purelib"], sys.prefix)


def factory_root() -> Path:
    from ..resources import REPO  # the directory holding ``repo_factory``

    return REPO


FACTORY_MOUNTS = ("package", "netproxy")


def mounts(
    workspace: Path,
    proxy_dir: Path | None = None,
    config_dir: Path | None = None,
    factory: str = "package",
    extra: list[dict] | None = None,
    caches: Path | None = None,
) -> list[dict]:
    """Every mount of the worker sandbox, in bwrap order (after the system dirs):
    ``{"kind": "ro"|"rw"|"symlink", "src", "dst", "why"}``. ``rw`` sources are created
    when missing (except ``~/.claude.json``). ``proxy_dir``: the host proxy's socket dir
    (``proxy`` network mode). ``config_dir``: the Claude Code config dir to bind (default:
    the process-global one, ``~/.claude`` plus ``~/.claude.json``); a given dir holds its
    own ``.claude.json``. ``factory``: what of the factory is visible: ``package`` (v4: the
    whole package and its venv, for ``check.py``'s dependency step) or ``netproxy`` (profile
    full-v1, build v5 §3.9: only ``harness/netproxy.py``, which the bridge runs). ``extra``:
    a capability launch's binds (``capabilities.compose``: its plugin folders, the
    manifest's read-only paths, the targets and evidence folders), after the profile's.
    ``caches``: a folder of the caller's own for the uv cache and the venvs (an Open in Claude
    Code session: it never writes the cache builds share); default the workers' shared ones."""
    from .claude_code import config_dir as global_config_dir
    from .isolation import base_python

    if factory not in FACTORY_MOUNTS:
        raise ValueError(f"unknown factory mount {factory!r} (one of {FACTORY_MOUNTS})")
    h = home()
    real = claude_binary()
    out: list[dict] = []
    for d in SYSTEM_RO:
        out.append({"kind": "ro", "src": d, "dst": d, "why": "the rest of the system"})
    for f in HIDE_FILES:
        if os.path.isfile(f):
            out.append(
                {
                    "kind": "ro",
                    "src": "/dev/null",
                    "dst": f,
                    "why": "the machine's identity, empty inside",
                }
            )
    for d in SYSTEM_LINKS:
        if os.path.islink(d):
            out.append(
                {"kind": "symlink", "src": os.readlink(d), "dst": d, "why": "merged-/usr link"}
            )
    files = ("/etc/ssl", "/etc/ca-certificates", "/etc/pki") + (
        ("/etc/resolv.conf",) if net_mode() == "host" else ()
    )
    for f in files:
        rp = os.path.realpath(f)
        if os.path.exists(f) and rp != f and not rp.startswith("/etc/"):
            out.append(
                {"kind": "ro", "src": rp, "dst": rp, "why": f"target of {f} (DNS/TLS for the API)"}
            )
    if net_mode() == "proxy":
        out.append(
            {
                "kind": "rw",
                "src": str(proxy_dir or "/nonexistent/repo_factory-net"),
                "dst": PROXY_DIR,
                "why": "the host proxy's socket: the only way out",
            }
        )
    inst = claude_install_dir(real)
    out.append(
        {
            "kind": "ro",
            "src": str(inst),
            "dst": str(inst),
            "why": "the claude install (read-only: no self-update)",
        }
    )
    cfg = Path(config_dir) if config_dir is not None else global_config_dir()
    out.append(
        {
            "kind": "rw",
            "src": str(cfg),
            "dst": str(cfg),
            "why": "Claude Code auth and sessions (--resume across directories)",
        }
    )
    cj = h / ".claude.json"
    if config_dir is None and not os.environ.get("CLAUDE_CONFIG_DIR") and cj.exists():
        out.append(
            {
                "kind": "rw",
                "src": str(cj),
                "dst": str(cj),
                "why": "Claude Code's global state (onboarding, per-project settings)",
            }
        )
    uvc, venvs = _caches(caches)
    for p, why in (
        (uvc, "the workers' uv cache (fast installs)"),
        (venvs, "the venvs check.py builds (REPO_FACTORY_VENV_ROOT)"),
    ):
        out.append({"kind": "rw", "src": str(p), "dst": str(p), "why": why})
    uv = shutil.which("uv")
    if uv:
        uv = os.path.realpath(uv)
        out.append({"kind": "ro", "src": uv, "dst": uv, "why": "uv"})
        out.append({"kind": "symlink", "src": uv, "dst": f"{OPT}/bin/uv", "why": "uv on PATH"})
        uvx = os.path.join(os.path.dirname(uv), "uvx")
        if os.path.exists(uvx):
            out.append({"kind": "ro", "src": uvx, "dst": uvx, "why": "uvx"})
            out.append(
                {"kind": "symlink", "src": uvx, "dst": f"{OPT}/bin/uvx", "why": "uvx on PATH"}
            )
    py = base_python()
    for name in ("python", "python3"):
        out.append(
            {
                "kind": "symlink",
                "src": py,
                "dst": f"{OPT}/bin/{name}",
                "why": f"`{name}` is the base python (its install dir is bound read-only)",
            }
        )
    out.append(
        {"kind": "symlink", "src": str(real), "dst": f"{OPT}/bin/claude", "why": "claude on PATH"}
    )
    from . import shell

    out.append(
        {
            "kind": "ro",
            "src": str(shell.install()),
            "dst": shell.INSIDE,
            "why": "the shell prefix: tool commands in a nested namespace, without the login",
        }
    )
    if factory == "netproxy":
        out.append(
            {
                "kind": "ro",
                "src": str(Path(__file__).with_name("netproxy.py")),
                "dst": f"{OPT}/src/repo_factory/harness/netproxy.py",
                "why": "the network bridge the sandbox starts (nothing else of the factory)",
            }
        )
        return out + list(extra or [])
    out.append(
        {
            "kind": "ro",
            "src": sys.prefix,
            "dst": f"{OPT}/venv",
            "why": "the factory's venv, for check.py's dependency step",
        }
    )
    out.append(
        {
            "kind": "ro",
            "src": str(factory_root() / "repo_factory"),
            "dst": f"{OPT}/src/repo_factory",
            "why": "the factory's package, for check.py's dependency step",
        }
    )
    return out + list(extra or [])


def _caches(caches: Path | None) -> tuple[Path, Path]:
    if caches is None:
        return worker_uv_cache(), worker_venvs()
    return Path(caches) / "uv", Path(caches) / "venvs"


def env(
    workspace: Path, factory: str = "package", caches: Path | None = None, shell: str = "build"
) -> tuple[tuple[str, str], ...]:
    """Non-secret settings, given to bwrap as ``--setenv``. With ``factory="netproxy"``
    (no factory package inside) ``check.py``'s two settings are left out. ``shell``: the
    shell prefix's kind (``harness/shell.py``: ``build``, or ``term`` for Open in Claude
    Code)."""
    from . import shell as prefix

    check = (
        (
            ("REPO_FACTORY_CHECK_PYTHON", f"{OPT}/bin/python"),
            ("REPO_FACTORY_CHECK_PYTHONPATH", f"{OPT}/src:{OPT}/venv/{site_packages()}"),
        )
        if factory == "package"
        else ()
    )
    proxy = (
        tuple(
            (k, v)
            for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")
            for v in (PROXY_URL,)
        )
        + (("NO_PROXY", NO_PROXY), ("no_proxy", NO_PROXY))
        if net_mode() == "proxy"
        else ()
    )
    return (
        ("PATH", PATH),
        ("HOME", str(home())),
        ("SHELL", "/bin/bash"),
        ("TMPDIR", "/tmp"),
        *proxy,
        # bind mounts are separate mounts to uv: hardlinking from its cache fails
        ("UV_LINK_MODE", "copy"),
        ("UV_CACHE_DIR", str(_caches(caches)[0])),
        ("REPO_FACTORY_VENV_ROOT", str(_caches(caches)[1])),
        # check.py's dependency step: the base python with the factory's package and its
        # venv's packages (the venv's own bin/python links through hidden directories)
        *check,
        ("DISABLE_AUTOUPDATER", "1"),
        ("REPO_FACTORY_WORKER_SANDBOX", "full"),
        *sorted(prefix.env(shell).items()),
    )


def process_env(config_dir: Path | None = None) -> dict:
    """The environment bwrap starts with (``--clearenv`` is off so secrets never appear on
    the command line): only ``PASS_ENV`` and ``PASS_ANTHROPIC`` variables, never a Claude login
    (``handoff.LOGIN_VARS``: the token goes through a pipe, ``handoff.piped``), and
    ``CLAUDE_CONFIG_DIR`` set to ``config_dir`` when one is given."""
    from . import handoff

    out = {k: v for k, v in os.environ.items() if k in PASS_ENV or k in PASS_ANTHROPIC}
    handoff.scrub(out)
    if config_dir is not None:
        out["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return out


def command(
    workspace: Path,
    proxy_dir: Path | None = None,
    config_dir: Path | None = None,
    factory: str = "package",
    extra: list[dict] | None = None,
    setenv: dict | None = None,
    workdir: str | None = None,
    forwards=(),
    caches: Path | None = None,
    shell: str = "build",
) -> list[str]:
    """``bwrap ... <claude>``: the worker's command line up to the claude binary (its
    arguments follow); in ``proxy`` network mode the sandbox starts ``netproxy``'s bridge,
    which execs claude. ``proxy_dir``: the running ``netproxy.Proxy``'s dir; ``config_dir``
    and ``factory``: see ``mounts``; ``extra`` (binds) and ``setenv`` (non-secret variables):
    a capability launch's (``capabilities.compose.Launch``). ``workdir``: where the workspace
    appears inside (profile full-v3: ``/work/product``, the same path in every session and in
    the exam's runs, EX-PLAN §5.3); default its own path. ``caches``: see ``mounts``. ``forwards``: ``(host, port)`` of
    MCP servers on the host's loopback (``Launch.forwards``): in ``proxy`` mode the bridge
    listens on the sandbox's own 127.0.0.1:port and pipes to the proxy's ``fwd-<port>.sock``.
    ``shell``: the tool commands' shell prefix (``env``). Raises ``SandboxUnavailable``
    without a working bubblewrap."""
    from .isolation import base_python, bwrap_cmd

    if not available():
        raise SandboxUnavailable(
            "the 'full' worker profile runs claude inside bubblewrap, and bwrap is missing "
            "or does not work here; refusing to run it unsandboxed (use tools: restricted)"
        )
    workspace = Path(workspace).resolve()
    h = home()
    hide = list(HIDE) + ([str(h)] if not str(h).startswith("/home/") else [])
    more_mounts = extra
    extra: list[str] = []
    for m in mounts(workspace, proxy_dir, config_dir, factory, more_mounts, caches):
        if m["kind"] == "rw":
            if not m["src"].endswith(".claude.json") and m["dst"] != PROXY_DIR:
                Path(m["src"]).mkdir(parents=True, exist_ok=True)
            extra += ["--bind", m["src"], m["dst"]]
        elif m["kind"] == "ro":
            extra += ["--ro-bind", m["src"], m["dst"]]
        else:
            extra += ["--symlink", m["src"], m["dst"]]
    claude = str(claude_binary())
    host_net = net_mode() == "host"
    cmd = bwrap_cmd(
        shutil.which("bwrap"),
        workspace,
        interp=claude if host_net else base_python(),
        env=env(workspace, factory, caches, shell) + tuple(sorted((setenv or {}).items())),
        network=host_net,
        hide=tuple(hide),
        extra=tuple(extra),
        clearenv=False,
        work_at=workdir,
    )
    if host_net:
        return cmd
    fwd = [
        x
        for _h, p in dict.fromkeys(tuple(f) for f in forwards or ())
        for x in ("--forward", f"{PROXY_DIR}/fwd-{int(p)}.sock", str(int(p)))
    ]
    return cmd + [
        "-I",
        f"{OPT}/src/repo_factory/harness/netproxy.py",
        "--bridge",
        f"{PROXY_DIR}/proxy.sock",
        str(PROXY_PORT),
        *fwd,
        "--",
        claude,
    ]

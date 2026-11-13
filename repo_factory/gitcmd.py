"""Every git the host runs, hardened for trees that a model or a stranger wrote.

A build session runs inside bubblewrap, but its workspace is bound read-write, ``.git``
included, and maintenance and projects clone repos other people write. Plain ``git`` on such a
tree runs what that tree's ``.git`` asks for, on the host, as the owner: a ``pre-commit`` hook,
a ``core.fsmonitor`` command, a ``filter.<x>.clean`` or ``process`` command that ``git add``
calls for every file ``.gitattributes`` names, a ``diff.<x>.textconv``, an ``ext::`` remote, a
credential helper. So no module runs ``git`` itself (``tests/test_gitcmd.py`` checks); they call
``run``/``git`` here, which:

- turns off hooks, fsmonitor, external diff and pager, signing, signature checks and the
  ``ext`` transport on the command line (``-c`` beats every config file);
- neutralises every command-valued key the repo's own config sets (filter, diff and merge
  drivers, credential helpers, ``core.sshCommand``/``askPass``/``gitProxy``, the remote's
  upload and receive programs), keeping the owner's global values where they exist;
- refuses a repo whose ``.git`` is a file pointing elsewhere, whose config moves the work
  tree (``core.worktree``) or rewrites remote URLs (``url.<x>.insteadOf``): the host would
  commit files outside the tree or push somewhere else;
- reads no system config (``GIT_CONFIG_NOSYSTEM``), never prompts, and never walks up out of
  ``cwd`` looking for a repo unless asked (``discover=True``, for the trees repo_factory ships);
- times out: 60 s, or 300 s for what talks to a remote (clone, fetch, push, ls-remote).

A session that still runs while the host reads its tree could change ``.git`` between the
check and the call; the host runs git on a session's tree after the session ends.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

HARDEN = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "diff.external=",
    "-c",
    "core.pager=cat",
    "-c",
    "protocol.ext.allow=never",
    "-c",
    "commit.gpgSign=false",
    "-c",
    "tag.gpgSign=false",
    "-c",
    "push.gpgSign=false",
    "-c",
    "log.showSignature=false",
    "-c",
    "merge.verifySignatures=false",
    "-c",
    "core.alternateRefsCommand=",
)
# diff, show and log never run an external diff or a textconv driver
NO_EXT_DIFF = {c: ("--no-ext-diff", "--no-textconv") for c in ("diff", "show", "log")}
NETWORK = {"clone", "fetch", "push", "pull", "ls-remote", "archive"}
TIMEOUT_S, NETWORK_TIMEOUT_S = 60, 300

# Keys a repo's own config may set that make git run a command (lower case; a subsection
# keeps its case in the override)
_SUFFIX = {
    "filter": ("clean", "smudge", "process"),
    "diff": ("textconv", "command"),
    "merge": ("driver",),
}
_COMMANDS = {
    "core.sshcommand",
    "core.askpass",
    "core.gitproxy",
    "core.editor",
    "sequence.editor",
    "gpg.program",
    "gpg.ssh.program",
    "gpg.x509.program",
    "gpg.ssh.defaultkeycommand",
}
_REFUSE = {
    "core.worktree": "it moves the work tree outside the repo",
    "extensions.worktreeconfig": "it reads a second, per-worktree config",
}


class GitRefused(RuntimeError):
    """The tree's ``.git`` would make the host's git act outside the tree."""


def installed() -> bool:
    """Whether a git is on ``PATH`` (``repo-factory doctor``)."""
    import shutil

    return shutil.which("git") is not None


def environ(cwd: Path | str | None = None, discover: bool = False, env: dict | None = None) -> dict:
    """The environment for the host's git: no system config, no prompt, no inherited
    ``GIT_DIR``, and (unless ``discover``) no walking up out of ``cwd``."""
    e = dict(os.environ if env is None else env)
    e["GIT_CONFIG_NOSYSTEM"] = "1"
    e["GIT_TERMINAL_PROMPT"] = "0"
    e.pop("GIT_DIR", None)
    e.pop("GIT_WORK_TREE", None)
    if cwd is not None and not discover:
        e["GIT_CEILING_DIRECTORIES"] = str(Path(cwd).resolve().parent)
    return e


def _local_config(cwd: Path, env: dict) -> list[tuple[str, str]]:
    """(key, value) pairs of the repo's own config, includes followed; [] outside a repo."""
    p = subprocess.run(
        ["git", *HARDEN, "config", "--local", "--includes", "--null", "--list"],
        cwd=cwd,
        capture_output=True,
        env=env,
        timeout=TIMEOUT_S,
    )
    if p.returncode != 0:
        return []
    out = []
    for item in p.stdout.decode(errors="replace").split("\0"):
        if item:
            k, _, v = item.partition("\n")
            out.append((k, v))
    return out


def _global(pattern: str, env: dict) -> list[tuple[str, str]]:
    """(key, value) pairs of the owner's global config whose key matches ``pattern``, in
    order (empty values kept: they reset a list)."""
    p = subprocess.run(
        ["git", "config", "--global", "--null", "--get-regexp", pattern],
        capture_output=True,
        env=env,
        timeout=TIMEOUT_S,
    )
    if p.returncode != 0:
        return []
    out = []
    for item in p.stdout.decode(errors="replace").split("\0"):
        if item:
            k, _, v = item.partition("\n")
            out.append((k, v))
    return out


def overrides(cwd: Path | str | None, env: dict | None = None, discover: bool = False) -> list[str]:
    """The ``-c`` arguments that neutralise what ``cwd``'s repo config would run; raises
    ``GitRefused`` for a tree whose git would act outside it."""
    if cwd is None or not Path(cwd).is_dir():
        return []
    cwd = Path(cwd)
    dot = cwd / ".git"
    if dot.is_file() or dot.is_symlink():
        raise GitRefused(
            f"{dot} is not a directory (a link to another git dir); the host's git refuses it"
        )
    e = environ(cwd, discover, env)
    out: list[str] = []
    seen: set[str] = set()
    helpers = False
    for key, _ in _local_config(cwd, e):
        if key in seen:
            continue
        seen.add(key)
        section, _, rest = key.partition(".")
        sub, _, leaf = rest.rpartition(".")
        section, leaf = section.lower(), leaf.lower()
        low = f"{section}.{leaf}" if not sub else key
        if low.lower() in _REFUSE:
            raise GitRefused(f"{cwd}: the repo's config sets {key}: {_REFUSE[low.lower()]}")
        if section == "url" and leaf in ("insteadof", "pushinsteadof"):
            raise GitRefused(
                f"{cwd}: the repo's config sets {key}: it rewrites where git "
                "fetches from or pushes to"
            )
        if section in _SUFFIX and sub and leaf in _SUFFIX[section]:
            out += ["-c", f"{key}="]
        elif section == "credential" and leaf == "helper":
            out += ["-c", f"{key}="]  # an empty value resets the helper list
            helpers = True
        elif section == "remote" and leaf in ("uploadpack", "receivepack", "vcs"):
            out += [
                "-c",
                f"{key}="
                + {"uploadpack": "git-upload-pack", "receivepack": "git-receive-pack"}.get(
                    leaf, ""
                ),
            ]
        elif not sub and low in _COMMANDS:
            g = [v for k, v in _global(f"^{key}$", e)]
            out += ["-c", f"{key}={g[-1] if g else ''}"]
    if helpers:
        # after every reset, the owner's own helpers again, in their order
        for k, v in _global(r"^credential\..*helper$", e):
            out += ["-c", f"{k}={v}"]
    return out


def _subcommand(args: list[str]) -> int | None:
    """The index of the subcommand in ``args`` (after global options such as ``-c k=v``)."""
    for k, a in enumerate(args):
        if not a.startswith("-") and (k == 0 or args[k - 1] not in ("-c", "-C")):
            return k
    return None


def argv(
    *args: str, cwd: Path | str | None = None, env: dict | None = None, discover: bool = False
) -> list[str]:
    """The full command line for ``git <args>`` in ``cwd``."""
    args = list(args)
    i = _subcommand(args)
    if i is not None and args[i] in NO_EXT_DIFF:
        args[i + 1 : i + 1] = NO_EXT_DIFF[args[i]]
    return ["git", *HARDEN, *overrides(cwd, env, discover), *args]


def run(
    args: list[str] | tuple[str, ...],
    cwd: Path | str | None = None,
    *,
    timeout: float | None = None,
    env: dict | None = None,
    discover: bool = False,
    check: bool = False,
    **kw,
) -> subprocess.CompletedProcess:
    """``subprocess.run(["git", *args], ...)``, hardened. A timeout is returned as exit 124
    with the reason on stderr (raised as ``CalledProcessError`` with ``check``)."""
    args = list(args)
    i = _subcommand(args)
    sub = args[i] if i is not None else ""
    limit = timeout or (NETWORK_TIMEOUT_S if sub in NETWORK else TIMEOUT_S)
    text = kw.get("text") or kw.get("universal_newlines") or kw.get("encoding")

    def failed(code: int, msg: str, cmd) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            cmd, code, "" if text else b"", msg if text else msg.encode()
        )

    try:
        cmd = argv(*args, cwd=cwd, env=env, discover=discover)
    except GitRefused as e:  # a failed git call, with the reason
        p = failed(128, str(e), ["git", *args])
    else:
        try:
            p = subprocess.run(cmd, cwd=cwd, env=environ(cwd, discover, env), timeout=limit, **kw)
        except subprocess.TimeoutExpired:
            p = failed(124, f"git {sub} timed out after {limit:g}s", cmd)
    if check:
        p.check_returncode()
    return p


def git(
    *args: str,
    cwd: Path | str | None = None,
    check: bool = True,
    env: dict | None = None,
    error: type[Exception] = RuntimeError,
    timeout: float | None = None,
    discover: bool = False,
) -> str:
    """stdout of ``git <args>`` in ``cwd``; raises ``error`` with git's message on failure."""
    p = run(
        args, cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout, discover=discover
    )
    if check and p.returncode != 0:
        raise error(f"git {' '.join(args[:2])} failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout

"""``repo-factory doctor``: what repo-factory needs, one line each.

Five checks, each "ok ..." or one sentence with the command that fixes it:

- **Claude Code**, installed and signed in (a login token repo-factory keeps, one in its
  environment, or Claude Code's own sign-in; with the last, the line says that builds copy the
  sign-in file into each session, and how to avoid it);
- **bubblewrap**, usable here (every build session runs inside it);
- **git**;
- **gh**, logged in (only publishing needs it, so a missing gh does not fail the doctor);
- **memory**: a warning under 5 GB available (a build needs about that), never a failure.

Exit 0 when claude, bubblewrap and git are fine; 1 otherwise. Nothing here calls a model or
the network beyond ``gh auth status``."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _run(cmd: list[str], timeout: float = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def claude_line() -> tuple[bool, str]:
    if not shutil.which("claude"):
        return False, (
            "Claude Code is not installed: install it "
            "(npm install -g @anthropic-ai/claude-code), then run claude once to "
            "sign in"
        )
    from .harness import handoff

    login = handoff.build_login()
    if login["on"] is None:
        return False, (
            "Claude Code is not signed in: run claude once and sign in to your "
            "Claude plan (or claude setup-token for a long-lived token)"
        )
    code, out = _run(["claude", "--version"])
    version = out.strip().splitlines()[0] if code == 0 and out.strip() else "installed"
    line = f"Claude Code {version}, signed in ({login['words']})"
    if login["on"] == "signin":
        line += "; " + SIGNIN_COPIED
    return True, line


# Said by the doctor, the README and the New task page in the same words.
SIGNIN_COPIED = (
    "builds copy this sign-in file into each session, where a build's own commands can read "
    "it: to avoid that, run claude setup-token and keep the token in Settings › Claude"
)


def bwrap_line() -> tuple[bool, str]:
    if not shutil.which("bwrap"):
        return False, (
            "bubblewrap is not installed: install it (sudo apt install bubblewrap, "
            "or your system's package)"
        )
    from .harness import sandbox

    if not sandbox.available():
        return False, (
            "bubblewrap cannot make a sandbox here: allow unprivileged user "
            "namespaces (sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0 "
            "on Ubuntu 24.04, or kernel.unprivileged_userns_clone=1)"
        )
    return True, "bubblewrap can make a sandbox"


def git_line() -> tuple[bool, str]:
    from . import gitcmd  # the host's git always goes through it

    if not gitcmd.installed():
        return False, "git is not installed: install it (sudo apt install git)"
    p = gitcmd.run(["--version"], capture_output=True, text=True, timeout=20)
    return True, ((p.stdout or "").strip() or "git") if p.returncode == 0 else "git"


MEM_GB = 5  # a build's sessions and its product's tests peaked at 3.7 GB in one cgroup


def mem_available(meminfo: str = "/proc/meminfo") -> float | None:
    """MemAvailable in GB, or None when the machine does not say."""
    try:
        for line in Path(meminfo).read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 / 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def mem_line() -> tuple[bool, str]:
    gb = mem_available()
    if gb is None:
        return True, "memory: this machine does not say how much is free (a build needs about 5 GB)"
    if gb < MEM_GB:
        return False, (
            f"only {gb:.1f} GB of memory is available: a build needs about {MEM_GB} GB "
            "(close other programs, or expect a build to be stopped part way)"
        )
    return True, f"{gb:.1f} GB of memory available (a build needs about {MEM_GB} GB)"


def gh_line() -> tuple[bool, str]:
    if not shutil.which("gh"):
        return False, (
            "gh is not installed (only publishing needs it): install the GitHub "
            "CLI (https://cli.github.com), then gh auth login"
        )
    code, out = _run(["gh", "auth", "status"])
    if code != 0:
        return False, "gh is not logged in (only publishing needs it): run gh auth login"
    return True, "gh is logged in"


def run(say=print) -> int:
    """Print the five lines; the exit code (1 when claude, bubblewrap or git is missing)."""
    bad = False
    for check, needed in (
        (claude_line, True),
        (bwrap_line, True),
        (git_line, True),
        (gh_line, False),
        (mem_line, False),
    ):
        try:
            ok, line = check()
        except Exception as e:  # noqa: BLE001 - one check, one line
            ok, line = False, f"{check.__name__}: {type(e).__name__}: {e}"
        say(("ok " if ok else "missing: ") + line)
        bad = bad or (needed and not ok)
    return 1 if bad else 0

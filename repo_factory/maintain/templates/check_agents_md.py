"""Keep AGENTS.md honest: run the commands it documents, exactly as written.

1. The shell fences (```sh, bash, shell, console or no language) under the setup heading
   ("## Setup, run, test", "Set up, run, test", "Setup", "Development", "Running tests", …,
   any case) are run line by line from the repository root (a failing line fails the check).
   A line whose comment starts with `serve` starts the service: the check waits until it
   answers on 127.0.0.1, then stops it. When the comment names the port variable, as in
   `# serve: http://127.0.0.1:8080/ (port: APP_PORT)`, a free port is used instead, so the
   check never collides with something already running. Lines a reader takes as examples
   are not run, and the check says so: a server started without that comment (it never
   exits), a line with a placeholder (`<file>`), an install onto the machine (pipx, sudo,
   a system package manager).
2. Every path the architecture section names (a table's first column, or a list's leading
   path) must exist.
3. A known open item (a test marked xfail) that now passes must have had its mark removed: an
   XPASS from a file that still carries the mark fails the check.

Standard library only. Usage: python .github/scripts/check_agents_md.py [REPO_ROOT]
Environment: AGENTS_MD_TIMEOUT (seconds per line, default 600).
repo-factory's drift doctor imports the readers below (``fence_lines``, ``arch_paths``), so the two
read AGENTS.md the same way."""

import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(
    sys.argv[1]
    if len(sys.argv) > 1
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
TIMEOUT = int(os.environ.get("AGENTS_MD_TIMEOUT", "600"))
COMMENT = re.compile(r"\s+#\s?(.*)$")
MARK = "AGENTS_MD_CHECK_RUN"


HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE = re.compile(r"^[ \t]*(```+|~~~+)[ \t]*([\w+.-]*)(.*)$")
SHELL = {"", "sh", "bash", "shell", "console", "zsh", "shell-session", "shellsession", "terminal"}
PROMPTED = {"console", "shell-session", "shellsession", "terminal"}  # `$ cmd`, then its output
# the setup heading, any case and spelling: the first of these kinds that a heading matches
SETUP_HEADS = (
    r"^set[\s-]*up,?\s+run,?\s+(?:and\s+)?test",
    r"^(?:set[\s-]*up|install(?:ation|ing)?|getting\s+started|quick[\s-]*start)\b",
    r"^(?:develop(?:ment|ing)?|contribut\w*|hacking)\b",
    r"^(?:running\s+(?:the\s+)?)?tests?\b|^testing\b",
    r"\b(?:set[\s-]*up|tests?|testing|develop(?:ment)?)\b",
)
ARCH_HEADS = (
    r"^architecture\s+in\s+brief",
    r"^(?:architecture|layout|structure|code\s+map|"
    r"modules|project\s+(?:layout|structure))\b",
    r"\barchitecture\b",
)
NO_FENCE = 'AGENTS.md has no shell commands under a setup heading (such as "## Setup, run, test")'
# lines a reader takes as examples, not steps: never run, and said
PLACEHOLDER = re.compile(r"<[A-Za-z][\w.:/-]*>|/path/to\b|…|\byour[-_]\w+", re.I)
SERVER = re.compile(r"(?:^|\s)(?:serve|server|runserver|start|up)(?=\s|$)|\bhttp\.server\b")
LISTENS = re.compile(
    r"\blisten(?:s|ing)?\b|\bserves?\b|\bstarts?\s+the\s+(?:server|service)\b", re.I
)
HELP = re.compile(r"(?:^|\s)(?:--help|-h|--version)(?=\s|$)")
ROOT_TOOLS = {"sudo", "su", "doas"}
MACHINE_TOOLS = {"pipx", "brew", "apt", "apt-get", "dnf", "yum", "pacman", "snap", "systemctl"}
USER_INSTALL = re.compile(r"\bpip3?\s+install\b.*\s--user\b")


def _scan(text):
    """``(headings, fences)`` of a Markdown text: ``[(level, title, line)]`` outside fences, and
    ``[(line, lang, lines)]`` for every fenced block (``` or ~~~, indented or not)."""
    heads, fences, cur = [], [], None
    for i, ln in enumerate(text.splitlines()):
        f = FENCE.match(ln)
        if cur is not None:
            if (
                f
                and f.group(1)[0] == cur[1][0]
                and len(f.group(1)) >= len(cur[1])
                and not (f.group(2) + f.group(3)).strip()
            ):
                fences.append((cur[0], cur[2], cur[3]))
                cur = None
            else:
                cur[3].append(ln)
            continue
        if f:
            cur = (i, f.group(1), f.group(2).lower(), [])
            continue
        h = HEADING.match(ln)
        if h:
            heads.append((len(h.group(1)), h.group(2).strip(), i))
    return heads, fences


def section(text, kinds):
    """``(title, first line, end line)`` of the first ``##`` or ``###`` heading that matches
    the first of ``kinds`` (regexes, any case) any heading matches; its body runs to the next
    heading of the same or a higher level. None: no such heading."""
    heads, _ = _scan(text)
    for pat in kinds:
        for n, (level, title, at) in enumerate(heads):
            if level in (2, 3) and re.search(pat, title, re.I):
                end = next((h[2] for h in heads[n + 1 :] if h[0] <= level), 1 << 30)
                return title, at, end
    return None


def fence_lines(text):
    """``[(command, comment)]``: every line of every shell fence under the setup heading, in
    order (a ``\\`` continuation joined, a ``$`` prompt dropped, a console block's output
    left out). None when there is no such heading or it has no shell fence."""
    sec = section(text, SETUP_HEADS)
    if sec is None:
        return None
    blocks = [b for b in _scan(text)[1] if sec[1] < b[0] < sec[2] and b[1] in SHELL]
    if not blocks:
        return None
    out = []
    for _, lang, raw in blocks:
        joined, buf = [], ""
        for ln in raw:
            s = ln.strip()
            if s.endswith("\\"):
                buf += s[:-1].rstrip() + " "
                continue
            joined.append(buf + s)
            buf = ""
        if buf:
            joined.append(buf.strip())
        for s in joined:
            if lang in PROMPTED and not re.match(r"^[$%] ", s):
                continue  # what the command printed
            s = re.sub(r"^\$ +", "", s)
            if not s or s.startswith("#"):
                continue
            c = COMMENT.search(s)
            out.append((s[: c.start()].strip() if c else s, (c.group(1).strip() if c else "")))
    return out


def arch_paths(text):
    """The paths AGENTS.md's architecture section names: every `path` in a table's first
    column, or at the start of a list item, relative to a directory its heading names
    (``## Architecture (`pkg/`)``). None when there is no such section."""
    sec = section(text, ARCH_HEADS)
    if sec is None:
        return None
    base = re.search(r"`([^`\s]+/)`", sec[0])
    base = base.group(1) if base else ""
    out = []
    for ln in text.splitlines()[sec[1] + 1 : sec[2]]:
        s = ln.strip()
        m = re.match(r"^\|([^|]*)\|", s)
        if m and re.match(r"^\|[\s:|-]+\|$", s):
            continue
        if not m:
            m = re.match(r"^[-*+]\s+((?:`[^`]+`[\s,/&]*(?:and\s+)?)+)", s)
        cell = m.group(1) if m else ""
        for p in re.findall(r"`([^`]+)`", cell):
            if re.match(r"^[\w.][\w./-]*$", p):
                out.append(p if not base or p.startswith(base) else base + p)
    return out


def skip_reason(cmd, note):
    """Why a line is an example to read, not a step to run (None: run it). A line marked
    ``# serve`` is repo-factory's own convention and runs (``serve``)."""
    if note.startswith("serve"):
        return None
    m = PLACEHOLDER.search(cmd)
    if m:
        return f"needs a value for {m.group(0)}"
    words = [w for w in cmd.split() if not re.match(r"^[A-Za-z_]\w*=", w)]
    first = words[0] if words else ""
    if first in ROOT_TOOLS:
        return "needs root"
    if first in MACHINE_TOOLS or USER_INSTALL.search(cmd):
        return "installs onto this machine"
    if not HELP.search(cmd) and (SERVER.search(cmd) or LISTENS.search(note)):
        return "starts a server that does not exit"
    return None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def answers(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def serve(cmd, note, env):
    m = re.search(r"127\.0\.0\.1:(\d+)", note)
    port = int(m.group(1)) if m else None
    var = re.search(r"\(port:\s*([A-Za-z_][A-Za-z0-9_]*)\)", note)
    if var:
        port = free_port()
        env = dict(env, **{var.group(1): str(port)})
    if port is None:
        return f"`{cmd}`: the serve comment names no 127.0.0.1 address to wait for"
    log = tempfile.TemporaryFile("w+")
    # a mark in the environment of everything this line starts, so a daemon that left the
    # process group (`start -d`) is found and stopped too
    mark = f"{MARK}={os.getpid()}-{time.time_ns()}"
    env = dict(env, **{MARK: mark.split("=", 1)[1]})
    p = subprocess.Popen(
        cmd,
        shell=True,
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if answers(port):
                print(f"ok    {cmd}  (answered on 127.0.0.1:{port})")
                return None
            # the line has ended: a failure, or a start that detached (`start -d` exits 0 at
            # once); the second waits while what it started is still running
            if p.poll() is not None and (
                p.returncode or not [x for x in marked(mark) if not _zombie(x)]
            ):
                break
            time.sleep(0.5)
        log.seek(0)
        return f"`{cmd}` did not answer on 127.0.0.1:{port}:\n" + log.read()[-2000:]
    finally:
        for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
            if p.poll() is None:
                try:
                    os.killpg(p.pid, sig)
                    p.wait(wait)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
        stop_marked(mark)
        log.close()


def marked(mark):
    """The processes whose environment carries ``mark`` (Linux; elsewhere none)."""
    out = []
    want = mark.encode()
    for d in os.listdir("/proc") if os.path.isdir("/proc") else []:
        if not d.isdigit() or int(d) == os.getpid():
            continue
        try:
            with open(f"/proc/{d}/environ", "rb") as f:
                if want in f.read().split(b"\0"):
                    out.append(int(d))
        except OSError:
            pass
    return out


def stop_marked(mark):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        pids = marked(mark)
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        deadline = time.time() + 10
        while pids and time.time() < deadline:
            pids = [x for x in pids if os.path.exists(f"/proc/{x}") and not _zombie(x)]
            time.sleep(0.2)
        if not pids:
            return


def _zombie(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


def run(cmd, env):
    try:
        p = subprocess.run(
            cmd, shell=True, cwd=ROOT, env=env, capture_output=True, text=True, timeout=TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return f"`{cmd}` took longer than {TIMEOUT} s", ""
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        return f"`{cmd}` exited {p.returncode}:\n" + out[-3000:], out
    print(f"ok    {cmd}")
    return None, out


def xpass_with_mark(output):
    bad = []
    for f in sorted(set(re.findall(r"^XPASS (\S+?\.py)::", output, re.M))):
        try:
            with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
                if "xfail(" in fh.read():
                    bad.append(
                        f"{f} now passes but still carries its open-item mark: delete the "
                        "mark block at the end of the file and its line in "
                        "tests/acceptance/README.md"
                    )
        except OSError:
            pass
    return bad


def main():
    path = os.path.join(ROOT, "AGENTS.md")
    if not os.path.isfile(path):
        sys.exit("AGENTS.md is missing")
    text = open(path, encoding="utf-8").read()
    problems = []
    lines = fence_lines(text)
    if not lines:
        sys.exit(NO_FENCE)
    env = dict(os.environ)
    env["PYTEST_ADDOPTS"] = (env.get("PYTEST_ADDOPTS", "") + " -rX").strip()
    outputs, ran_acceptance, skipped = [], False, []
    for cmd, note in lines:
        why = skip_reason(cmd, note)
        if why:
            print(f"skip  {cmd}  ({why})")
            skipped.append(f"`{cmd}` ({why})")
            continue
        if note.startswith("serve"):
            err = serve(cmd, note, env)
        else:
            err, out = run(cmd, env)
            outputs.append(out)
            ran_acceptance |= "pytest" in cmd and "tests/acceptance" in cmd
        if err:
            problems.append(err)
    if not ran_acceptance and os.path.isdir(os.path.join(ROOT, "tests", "acceptance")):
        err, out = run(f"{sys.executable} -m pytest tests/acceptance -q", env)
        outputs.append(out)
        if err:
            problems.append(err)
    ran = len(lines) - len(skipped)
    if not ran:
        problems.append(
            "none of AGENTS.md's setup commands can run as written: each starts a "
            "server, needs a placeholder filled in or installs onto the machine"
        )
    problems += xpass_with_mark("\n".join(outputs))
    for row in arch_paths(text) or []:
        if not os.path.exists(os.path.join(ROOT, row.rstrip("/"))):
            problems.append(f"the architecture section names `{row}`, which does not exist")
    for p in problems:
        print("FAIL  " + p)
    print(
        f"{'FAILED' if problems else 'passed'}: {ran} commands, {len(problems)} problems"
        + (f"; not run: {'; '.join(skipped)}" if skipped else "")
    )
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()

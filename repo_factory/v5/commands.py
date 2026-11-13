"""The README's commands, run as written (docs/design/build-v5.md, Addendum C: the readiness
stage's ``commands`` step).

All three round-2 h2h products shipped a README whose test command failed as written
(``./shelfdesk.sh test`` and ``unittest discover`` picking up the pytest-only acceptance
suite; a path that only existed during the build), while their READINESS.md said the
documented test commands ran green. And the webhook relay couldn't start again for about
60 s after a stop (address reuse off). So the harness, not a session, now does what an
operator would, on a fresh copy of the tree as it will be delivered:

1. **quick**: the README's quick start (its install section: install, run), then one backup
   command, each exactly as written. A command that starts serving is left running, as in a
   second terminal. One start command per quick start: while it serves, a later start
   command ("Start it again later with: npm start") is recorded as another way to start it,
   not run beside it; a step offered with "or" runs only when the step before it failed; an
   example that takes a data file of the user's own (``nextread import tabs.txt``) isn't a
   failure of the README (track LEAN2). The tree is the product's own (EX-PLAN §4.2):
   nothing of repo-factory's is in it.
2. **restart** (services): start, use (five requests, each on its own connection), stop
   gracefully (SIGTERM), start again at once and check it serves; use it, SIGKILL it, start
   again at once and check it serves.
3. **tests**: every test command the README documents, exactly as written: the exam
   record's ``own`` section (``exam.run(readme=True)``), with the flake protocol.

Everything runs in one bwrap sandbox (``cmdrunner.py``): a private filesystem view, a
throwaway ``HOME`` and its own network namespace with only loopback, so every server the
product starts listens there, on 127.0.0.1, and nowhere else. Commands that can't run there
are recorded as skipped with the reason (root, systemd, Docker, the network, placeholders,
interactive prompts); dependency installs are satisfied by the harness's environment (the
build's declared dependencies and pytest; there is no network), and a skipped install of
the product itself puts its console scripts on PATH for the steps after it
(``write_scripts``). An install script gets the build backends its pip or pipx builds with
(``pyinstall.py``'s wheelhouse, no index).

The results become three scorecard items (``items``): ``commands`` (the quick start),
``tests_green`` (the documented test commands) and ``restart_now`` (the immediate restart).
A failure is red and fixable: documentation- or script-sized (rank 3), the restart a
reliability bug (rank 2)."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..harness import links
from .cmdrunner import assignment_only

SHELL_LANGS = {
    "",
    "sh",
    "bash",
    "shell",
    "console",
    "zsh",
    "shell-session",
    "shellsession",
    "terminal",
    "cmd",
}
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```+|~~~+)\s*([\w+.-]*)")
INLINE = re.compile(r"`([^`\n]+)`")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")

TEST = re.compile(
    r"\bpytest\b|\bunittest\b|\b(?:make|npm|yarn|pnpm|cargo|go|tox|nox|just)\s+"
    r"(?:run\s+)?test\b|^\S+\s+test(?:\s|$)"
)
BACKUP = re.compile(r"(?:^|\s)(?:backup|back-up|dump)(?:\s|$)")
RESTORE = re.compile(r"\brestore\b")
RUNS = re.compile(r"(?:^|\s)(?:serve|server|start|run|up|runserver)(?:\s|$)")
# A start command: its own verb starts the service (``npm start``, ``npm run dev``, ``node
# bin/plotkeeper.js start``, ``./driftwatch.sh serve``, ``flask run``). A script that runs
# once (``npm run demo``, ``npm run backup``) is not one, though ``RUNS`` matches it.
START = re.compile(
    r"^(?:npm|yarn|pnpm)\s+(?:run\s+)?(?:start|serve|dev)\b|"
    r"(?:^|\s)(?:start|serve|server|runserver|up)(?:\s|$)|^flask\s+run\b|"
    r"^(?:uvicorn|gunicorn|hypercorn|waitress-serve)\s"
)
# A comment line or the prose just before a block that offers the next command as another
# way to do what the one before it did (``# or``, "Or, without the script:"): run only when
# the one before it didn't work.
ALT_TEXT = re.compile(r"^\s*(?:#\s*)?\(?(?:or|alternatively)\b", re.I)
# The prose just before an untagged block that says the block is what a command prints ("It
# prints something like:", "You should see:"): output to read, not commands to run
# (Driftwatch's "Driftwatch is running at http://127.0.0.1:8080/" ran as a command: a false
# red). A block tagged ``sh`` after such words is still commands.
OUTPUT_TEXT = re.compile(
    r"\b(?:prints?|outputs?|you(?:'ll| will| should)? see|looks? like|"
    r"something like)\b[^.:]*:\s*$",
    re.I,
)
INSTALL_HEADS = re.compile(
    r"install|quick\s*-?\s*start|getting started|get started|set ?up|"
    r"first run",
    re.I,
)
# Subsections of the quick start that are alternatives or extras, not the path itself.
SKIP_HEADS = re.compile(
    r"docker|container|compose|podman|kubernetes|k8s|windows|macos|"
    r"mac os|\bmac\b|systemd|service|boot|by hand|manual|alternative|"
    r"instead|demo|proxy|nginx|caddy|tls|https|upgrad|uninstall|develop|"
    r"contribut|from source",
    re.I,
)
FOREIGN_HEADS = re.compile(
    r"docker|container|compose|podman|kubernetes|k8s|windows|macos|"
    r"mac os|\bmac\b",
    re.I,
)
DEP = re.compile(
    r"^(?:python3?\s+-m\s+)?pip3?\s+install\b|^uv\s+(?:pip\s+install|sync|tool\s+"
    r"install|add|venv)\b|^(?:poetry|pipenv|pdm|hatch)\s+install\b|^pipx\s+install"
    r"\b|^(?:npm|pnpm|yarn)\s+(?:ci|install|add)\b|^python3?\s+-m\s+venv\b|"
    r"^virtualenv\b|^(?:source|\.)\s+\S*activate\b|^deactivate$|^conda\b"
)
PLACEHOLDER = re.compile(
    r"<[A-Za-z][^<>]*>|/path/to\b|\.\.\.|…|(?<![\w'\"])\[[^\]]*\]|"
    r"(?:^|\s)[A-Z][A-Z_]{1,}(?=\s|$)|\byour[-_]"
)
WINDOWS = re.compile(r"^py\s|\.bat\b|\.ps1\b|^cmd(?:\.exe)?\s|^powershell\b|^start\s|[A-Z]:\\")
INTERACTIVE = re.compile(r"prompt|interactive|asks? (?:for|you)|type `?yes|you will be asked", re.I)
SKIP_TOOLS = {
    "sudo": "needs root",
    "su": "needs root",
    "doas": "needs root",
    "systemctl": "systemd isn't available in the check's sandbox",
    "journalctl": "systemd isn't available in the check's sandbox",
    "loginctl": "systemd isn't available in the check's sandbox",
    "docker": "Docker isn't available in the check's sandbox",
    "docker-compose": "Docker isn't available in the check's sandbox",
    "podman": "containers aren't available in the check's sandbox",
    "kubectl": "a cluster command",
    "helm": "a cluster command",
    "brew": "a system package manager",
    "apt": "a system package manager",
    "apt-get": "a system package manager",
    "dnf": "a system package manager",
    "yum": "a system package manager",
    "pacman": "a system package manager",
    "winget": "Windows",
    "choco": "Windows",
    "curl": "needs the network or a running service",
    "wget": "needs the network",
    "git": "a git operation (the check's fresh copy is the checkout)",
    "ssh": "a remote host",
    "scp": "a remote host",
    "rsync": "a remote host",
    "open": "opens a desktop application",
    "xdg-open": "opens a desktop application",
    "crontab": "edits the system crontab",
    "kill": "process control",
    "pkill": "process control",
    "killall": "process control",
    "tail": "watches a file",
    "less": "a pager",
    "more": "a pager",
    "watch": "runs forever",
    "vi": "an editor",
    "vim": "an editor",
    "nano": "an editor",
    "emacs": "an editor",
    "ps": "process listing",
    "ss": "socket listing",
    "netstat": "socket listing",
    "lsof": "file listing",
    "top": "process listing",
    "htop": "process listing",
    "ufw": "a firewall",
    "iptables": "a firewall",
    "certbot": "TLS certificates",
    "nginx": "a web server",
    "caddy": "a web server",
}
# what a step needs that the check's sandbox doesn't have: "not verifiable here" (§5.2)
NEEDS = {
    "needs root",
    "systemd isn't available in the check's sandbox",
    "Docker isn't available in the check's sandbox",
    "containers aren't available in the check's sandbox",
    "a cluster command",
    "a system package manager",
    "needs the network",
    "a remote host",
    "a firewall",
    "TLS certificates",
    "a web server",
    "edits the system crontab",
}
NEED_TOOLS = {w for w, why in SKIP_TOOLS.items() if why in NEEDS}
EXTERNAL = {
    "uv",
    "poetry",
    "pipx",
    "pdm",
    "hatch",
    "docker",
    "npm",
    "node",
    "yarn",
    "pnpm",
    "cargo",
    "go",
    "java",
    "mvn",
    "gradle",
    "make",
    "sqlite3",
    "tox",
    "nox",
    "just",
}
# test_s: a documented test command gets as long as the exam's own pytest run (900 s).
TIMEOUTS = {"step_s": 60, "test_s": 900, "tests_total_s": 1500, "start_s": 25, "serve_s": 15}


# -- extraction ---------------------------------------------------------------------------
def _split_comment(line: str) -> tuple[str, str]:
    """``cmd  # comment`` -> (cmd, comment); a ``#`` inside quotes stays."""
    quote = None
    for i, c in enumerate(line):
        if quote:
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip(), line[i + 1 :].strip()
    return line.rstrip(), ""


def _tokens(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def _first_word(cmd: str) -> str:
    for t in _tokens(cmd):
        if re.match(r"^[A-Za-z_]\w*=", t):
            continue
        return t
    return ""


def scripts(root: Path) -> set[str]:
    """The product's console scripts (``[project.scripts]``)."""
    p = Path(root) / "pyproject.toml"
    if not p.is_file():
        return set()
    try:
        import tomllib

        d = tomllib.loads(p.read_text(errors="replace"))
    except Exception:  # noqa: BLE001  a broken pyproject
        return set()
    return set((d.get("project") or {}).get("scripts") or {})


KNOWN = {
    "python",
    "python3",
    "py",
    "pytest",
    "make",
    "npm",
    "yarn",
    "pnpm",
    "uv",
    "pip",
    "pip3",
    "sh",
    "bash",
    "node",
    "cargo",
    "go",
    "tox",
    "nox",
    "just",
    "export",
}


def looks_like_command(cmd: str, root: Path, names: set[str]) -> bool:
    """Inline code and indented blocks are prose as often as commands: the first word must
    be a script in the repository, a known tool or the product's own command."""
    w = _first_word(cmd)
    if not w:
        return False
    if w.startswith(("./", "bin/")) or w in KNOWN or w in names or w in NEED_TOOLS:
        return True
    return bool(re.match(r"^[\w.-]+$", w)) and (
        (Path(root) / w).exists() or (Path(root) / "bin" / w).exists()
    )


def scan(text: str, root: Path, file: str = "README.md") -> list[dict]:
    """Every command-like line of a markdown file: ``{"cmd", "comment", "where", "heads"
    (the heading path), "how": block | indented | inline}``. Fenced shell blocks (a console
    block: only its ``$`` lines), indented code blocks after a paragraph, and inline code
    that looks like a command."""
    names = scripts(root) | {
        p.name for p in (Path(root) / "bin").glob("*") if (Path(root) / "bin").is_dir()
    }
    out: list[dict] = []
    heads: list[tuple[int, str]] = []
    lines = text.splitlines()
    i = 0
    prev_text = ""  # the last non-blank line outside code
    while i < len(lines):
        line = lines[i]
        m = FENCE.match(line)
        if m:
            fence, lang = m.group(1), m.group(2).lower()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence[:3]):
                body.append((i + 1, lines[i]))
                i += 1
            i += 1
            if lang in SHELL_LANGS and not (lang == "" and OUTPUT_TEXT.search(prev_text)):
                got = _block(body, file, [h for _, h in heads], "block")
                if got and ALT_TEXT.match(prev_text):
                    got[0]["alt"] = True
                out += got
            prev_text = ""
            continue
        h = HEADING.match(line)
        if h:
            lvl = len(h.group(1))
            heads = [x for x in heads if x[0] < lvl] + [(lvl, h.group(2).strip())]
            prev_text = line
            i += 1
            continue
        if (
            line.startswith(("    ", "\t"))
            and line.strip()
            and i > 0
            and not lines[i - 1].strip()
            and prev_text
            and not LIST_ITEM.match(prev_text)
        ):
            body = []
            while i < len(lines) and (lines[i].startswith(("    ", "\t")) or not lines[i].strip()):
                if lines[i].strip():
                    body.append((i + 1, lines[i]))
                i += 1
            for e in _block(body, file, [h for _, h in heads], "indented"):
                if looks_like_command(e["cmd"], root, names):
                    out.append(e)
            continue
        if line.strip():
            prev_text = line
            for span in INLINE.findall(line):
                cmd = span.strip()
                if len(_tokens(cmd)) >= 2 and looks_like_command(cmd, root, names):
                    out.append(
                        {
                            "cmd": cmd,
                            "comment": line.strip()[:200],
                            "where": f"{file}:{i + 1}",
                            "heads": [h for _, h in heads],
                            "how": "inline",
                        }
                    )
        i += 1
    return out


def _block(body: list[tuple[int, str]], file: str, heads: list[str], how: str) -> list[dict]:
    prompt = any(re.match(r"^\s*\$\s", t) for _, t in body)
    out, buf, start, alt = [], "", None, False
    for n, raw in body:
        t = raw.strip()
        if prompt:
            if not re.match(r"^\$\s", t) and not buf:
                continue  # output of a console block
            t = re.sub(r"^\$\s+", "", t)
        if not t or (t.startswith("#") and not buf):
            alt = alt or bool(t and ALT_TEXT.match(t))  # ``# or``: the next is an alternative
            continue
        start = start or n
        if t.endswith("\\"):
            buf += t[:-1].rstrip() + " "
            continue
        cmd, comment = _split_comment(buf + t)
        buf = ""
        if cmd:
            out.append(
                {
                    "cmd": cmd,
                    "comment": comment,
                    "where": f"{file}:{start}",
                    "heads": list(heads),
                    "how": how,
                    "block": f"{file}:{body[0][0]}" if body else None,
                    **({"alt": True} if alt else {}),
                }
            )
            alt = False
        start = None
    return out


def _normalise(cmd: str) -> str:
    """``git clone <repo> x && cd x && ./install.sh`` -> ``./install.sh``: the clone (and
    unpacking) is the fresh copy itself."""
    parts = [p.strip() for p in re.split(r"\s*&&\s*", cmd)]
    while parts and re.match(r"^(git\s+clone|cd\s|unzip\s|tar\s+-?x)", parts[0]):
        parts = parts[1:]
    return " && ".join(parts)


def skip_reason(e: dict) -> str | None:
    """Why a command can't be run as written in the check's sandbox, or None."""
    cmd = e["cmd"]
    if DEP.search(cmd):
        return (
            "installs dependencies: the check's environment already has the declared "
            "ones (it has no network)"
        )
    if PLACEHOLDER.search(cmd):
        return "has a placeholder to fill in"
    if WINDOWS.search(cmd):
        return "Windows"
    w = _first_word(cmd)
    if w in SKIP_TOOLS:
        return SKIP_TOOLS[w]
    if INTERACTIVE.search(e.get("comment") or "") and e.get("how") != "inline":
        return "interactive: it prompts for input"
    if RESTORE.search(cmd):
        return "a restore (checked by the operational checks' backup and restore)"
    return None


def installs_product(cmd: str) -> bool:
    """A skipped install step that installs the product itself (``pipx install .``, ``pip
    install -e .``, ``uv tool install .``, ``uv sync``, ``poetry install``): an operator has
    its commands on PATH afterwards, not only its dependencies."""
    if not DEP.search(cmd):
        return False
    if re.match(r"^(?:uv\s+sync|poetry\s+install|pdm\s+install)\b", cmd):
        return True
    toks = _tokens(cmd)
    return "install" in toks and any(
        re.fullmatch(r"\./?(?:\[[\w,.-]*\])?", t) for t in toks[toks.index("install") + 1 :]
    )


def entry_points(root: Path) -> dict[str, str]:
    """The product's console scripts: ``{name: "module:attr"}`` (``[project.scripts]``)."""
    p = Path(root) / "pyproject.toml"
    try:
        import tomllib

        d = tomllib.loads(p.read_text(errors="replace"))
    except Exception:  # noqa: BLE001  no or a broken pyproject
        return {}
    eps = (d.get("project") or {}).get("scripts") or {}
    return {
        str(k): str(v)
        for k, v in eps.items()
        if isinstance(v, str) and re.fullmatch(r"[\w.]+(?::[\w.]+)?(?:\s*\[[^\]]*\])?", v.strip())
    }


def write_scripts(repo: Path, bindir: Path, python: str) -> list[str]:
    """What the product's install step puts on PATH, without the install: a launcher per
    console script in ``bindir`` that runs it from ``repo`` (the fresh copy) with
    ``python``. The check has no network, so it skips the install step itself (the build's
    environment already has the declared dependencies); without these, every command after
    ``pipx install .`` failed with "command not found" (exit 127: Nextread r1–r2, Repo Recall
    r1–r5)."""
    eps = entry_points(repo)
    if not eps:
        return []
    bindir.mkdir(parents=True, exist_ok=True)
    for name, spec in eps.items():
        mod, _, attr = spec.split("[")[0].strip().partition(":")
        top = mod.split(".")[0]
        # src layout or flat: wherever the top-level package (or module) is
        roots = [
            str(d)
            for d in (Path(repo) / "src", Path(repo))
            if (d / top).is_dir() or (d / f"{top}.py").is_file()
        ] or [str(repo)]
        call = (
            (
                f"from {mod} import {attr.split('.')[0]} as _f\n"
                f"sys.exit(_f{''.join('.' + a for a in attr.split('.')[1:])}())\n"
            )
            if attr
            else f"import runpy\nrunpy.run_module({mod!r}, run_name='__main__')\n"
        )
        f = bindir / name
        f.write_text(f"#!{python}\nimport sys\nsys.path[:0] = {roots!r}\n" + call)
        f.chmod(0o755)
    return sorted(eps)


def readme_path(root: Path) -> Path | None:
    ps = [p for p in sorted(Path(root).glob("README*")) if p.is_file()]
    return next((p for p in ps if p.name == "README.md"), ps[0] if ps else None)


def extract(root: Path) -> dict:
    """The README's commands by purpose: ``quick`` (the install section, in order, its
    alternative subsections left out; a step offered with "or" is marked ``alt``), ``backup`` (the first runnable backup command), ``tests`` (every
    documented test command; each once), ``run`` (start
    commands anywhere: candidates for the restart check), and ``skipped``. Each entry:
    ``{"cmd", "where", "kind", "comment", "heads", "how", "skip"}``."""
    root = Path(root)
    out = {
        "quick": [],
        "backup": [],
        "tests": [],
        "run": [],
        "skipped": [],
        "not_verifiable": [],
        "readme": None,
        "install_section": None,
    }
    p = readme_path(root)
    entries = scan(p.read_text(errors="replace"), root, p.name) if p else []
    out["readme"] = p.name if p else None
    # the quick start: the first install-like section and its subsections
    first = next(
        (
            e["heads"]
            for e in entries
            if e["how"] != "inline"
            and e["heads"]
            and any(INSTALL_HEADS.search(h) for h in e["heads"])
        ),
        None,
    )
    if first:
        k = next(n for n, h in enumerate(first) if INSTALL_HEADS.search(h))
        out["install_section"] = first[k]
    seen_tests = set()
    for e in entries:
        e = {**e, "cmd": _normalise(e["cmd"])}
        if not e["cmd"]:
            continue
        is_test = bool(TEST.search(e["cmd"])) and not DEP.search(e["cmd"])
        if is_test:
            key = re.sub(r"\s+", " ", e["cmd"])
            if key in seen_tests:  # run once; both places are named
                prev = next(x for x in out["tests"] if re.sub(r"\s+", " ", x["cmd"]) == key)
                if e["where"].split(":")[0] not in prev["where"]:
                    prev["where"] += f", {e['where']}"
                continue
            seen_tests.add(key)
            out["tests"].append({**e, "kind": "test", "skip": skip_reason(e)})
            continue
        heads = e["heads"]
        in_quick = (
            first is not None
            and e["how"] != "inline"
            and heads[: k + 1] == first[: k + 1]
            and not any(SKIP_HEADS.search(h) for h in heads[k + 1 :])
        )
        if in_quick:
            out["quick"].append({**e, "kind": "install", "skip": skip_reason(e)})
        elif (
            BACKUP.search(e["cmd"])
            and not RESTORE.search(e["cmd"])
            and not any(FOREIGN_HEADS.search(h) for h in heads)
        ):
            out["backup"].append({**e, "kind": "backup", "skip": skip_reason(e)})
        if (
            e["how"] != "inline"
            and RUNS.search(e["cmd"])
            and not is_test
            and not any(FOREIGN_HEADS.search(h) for h in heads)
            and not skip_reason(e)
        ):
            # the ``export`` lines before it in its block are part of how it runs
            def line(x):
                return int(x["where"].rsplit(":", 1)[1])

            setup = (
                [
                    x["cmd"]
                    for x in entries
                    if x.get("block") == e.get("block")
                    and line(x) < line(e)
                    and assignment_only(x["cmd"])
                ]
                if e.get("block")
                else []
            )
            out["run"].append({**e, "kind": "start", "setup": setup})
    # EX-PLAN §5.2: a documented step that needs what the check's sandbox doesn't have
    # (root, systemd, Docker, the network, a cluster) is not verifiable here: listed as such,
    # never red, wherever in the README it is
    seen = {e["cmd"] for k in ("quick", "backup", "tests") for e in out[k]}
    for e in entries:
        w = _first_word(_normalise(e["cmd"]))
        if (
            e["how"] != "inline"
            and w in SKIP_TOOLS
            and SKIP_TOOLS[w] in NEEDS
            and e["cmd"] not in seen
        ):
            out["not_verifiable"].append({**e, "kind": "needs", "skip": SKIP_TOOLS[w]})
            seen.add(e["cmd"])
    runnable = [b for b in out["backup"] if not b["skip"]]
    out["skipped"] = [b for b in out["backup"] if b["skip"]]
    out["backup"] = runnable[:1]
    return out


def run_recipes(ex: dict, session_recipe: dict | None = None) -> list[dict]:
    """How to start the service for the restart check, best first: the checks session's
    ``run`` (it found what works), then each documented start command (with the
    ``export`` lines before it in its block)."""
    out = []
    if isinstance(session_recipe, dict) and session_recipe.get("start"):
        out.append(
            {
                "setup": [str(x) for x in session_recipe.get("setup") or []],
                "start": str(session_recipe["start"]),
                "env": {str(k): str(v) for k, v in (session_recipe.get("env") or {}).items()},
                "health": session_recipe.get("health") or "/",
                "port": session_recipe.get("port")
                if isinstance(session_recipe.get("port"), int)
                else None,
                "source": "the operational checks",
            }
        )
    for e in ex["run"]:
        if any(r["start"] == e["cmd"] for r in out):
            continue
        out.append(
            {
                "setup": list(e.get("setup") or []),
                "start": e["cmd"],
                "env": {},
                "health": "/",
                "port": None,
                "source": e["where"],
            }
        )
    return out[:4]


# -- running ------------------------------------------------------------------------------
def tree_key(root: Path) -> str:
    """A hash of the tree's content (caches a run per tree)."""
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and not {".git", "__pycache__", ".pytest_cache"} & set(p.parts):
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:20]


def available() -> bool:
    from ..harness.isolation import isolation

    return isolation() == "bwrap"


def run(
    tree: Path,
    venv: Path,
    phases=("quick", "restart", "tests"),
    session_recipe: dict | None = None,
    timeouts: dict | None = None,
    wheels: Path | None = None,
) -> dict:
    """Run the README's commands of ``tree`` (``extract``) in a sandbox, on a fresh copy.
    ``wheels``: the build backends a user's install builds with (``pyinstall.install``), for
    an install script that runs pip or pipx (there is no network). Returns ``{"ran", "why",
    "extract", "quick", "restart", "tests", "phases"}``."""
    from ..harness.isolation import sandbox_cmd
    from .pyinstall import env as pip_env

    tree = Path(tree)
    ex = extract(tree)
    res = {
        "ran": False,
        "phases": list(phases),
        "extract": _brief(ex),
        "quick": [],
        "restart": None,
        "tests": None,
    }
    if not available():
        res["why"] = (
            "no bwrap sandbox on this machine: the commands weren't run (they "
            "would start servers outside a private network namespace)"
        )
        return res
    t = {**TIMEOUTS, **(timeouts or {})}
    with tempfile.TemporaryDirectory(prefix="readme-check-") as tmp:
        work = Path(tmp)
        repo, home, logs = work / "repo", work / "home", work / "logs"
        links.copytree(
            tree,
            repo,
            ignore=shutil.ignore_patterns(
                "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", "*.pyc"
            ),
        )
        home.mkdir()
        logs.mkdir()
        shutil.copy(Path(__file__).with_name("cmdrunner.py"), work / "cmdrunner.py")
        quick = []
        for e in ex["quick"] + ex["backup"]:
            if not e["skip"]:
                quick.append(_step(e))
            elif (
                installs_product(e["cmd"])
                and not any(s.get("path") for s in quick)
                and write_scripts(repo, work / "bin", str(Path(venv) / "bin" / "python"))
            ):
                # the install step, skipped: its commands are on PATH from here on
                quick.append({**_step(e), "path": [str(work / "bin")], "why": e["skip"]})
        plan = {
            "repo": str(repo),
            "home": str(home),
            "logs": str(logs),
            "path_prefix": [str(Path(venv) / "bin")],
            "timeouts": t,
            "quick": quick,
            "env": pip_env(str(wheels)) if wheels else {},
            "restart": {"recipes": run_recipes(ex, session_recipe)}
            if "restart" in phases
            else None,
            "tests": [_step(e) for e in ex["tests"] if not e["skip"]]
            if "tests" in phases
            else None,
        }
        (work / "plan.json").write_text(json.dumps(plan))
        ro = (Path(venv),) + ((Path(wheels),) if wheels else ())
        cmd = sandbox_cmd(work, ro=ro, interp=str(Path(venv) / "bin" / "python"), network=False) + [
            str(work / "cmdrunner.py"),
            str(work / "plan.json"),
        ]
        total = (
            len(plan["quick"]) * (t["step_s"] + 10)
            + t["tests_total_s"]
            + t["test_s"]
            + 4 * (t["start_s"] + 2 * t["serve_s"] + 30)
            + 120
        )
        try:
            p = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=total)
            err = (p.stderr or "")[-1500:]
        except subprocess.TimeoutExpired:
            err = f"the check itself timed out after {total} s"
        f = logs / "result.json"
        if not f.exists():
            res["why"] = "the command runner failed: " + err
            return res
        got = scrub(json.loads(f.read_text()), work)
    res.update(
        ran=True, quick=got.get("quick") or [], restart=got.get("restart"), tests=got.get("tests")
    )
    return res


def scrub(obj, work: Path):
    """The commands' output without the check's own paths: the copy of the repository is
    ``.`` and the rest of the sandbox folder ``<sandbox>``, so READINESS.md (which quotes a
    failing command's output) never carries a temporary path of the factory's (the jargon
    scan flags them, and they mean nothing to a reader)."""
    if isinstance(obj, str):
        return obj.replace(str(work / "repo"), ".").replace(str(work), "<sandbox>")
    if isinstance(obj, list):
        return [scrub(x, work) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub(v, work) for k, v in obj.items()}
    return obj


def _step(e: dict) -> dict:
    # ``finishes``: no start command (``serve``, ``start``, ``run``...): a port it opens on
    # the way is waited out, not taken for a server (``sidedoor demo`` runs a trial on a
    # short-lived broker; left running, it blocked the backup after it). One still
    # listening when its time is up is a server after all.
    # ``start``: a start command; while the quick start's server still serves, a later one
    # is the README saying how to start it again, not a step (Plotkeeper's "Start it again
    # later with: npm start" ran beside ``sh install.sh`` and hit its own "already running"
    # guard: a false hold). ``alt``: offered as another way to do the step before it.
    return {
        "cmd": e["cmd"],
        "where": e["where"],
        "kind": e["kind"],
        "finishes": not RUNS.search(e["cmd"]),
        "start": bool(START.search(e["cmd"])),
        "alt": bool(e.get("alt")),
    }


def _brief(ex: dict) -> dict:
    keep = ("cmd", "where", "kind", "skip")
    return {
        k: [{x: e.get(x) for x in keep} for e in ex[k]]
        for k in ("quick", "backup", "tests", "run", "skipped", "not_verifiable")
        if k in ex
    } | {"readme": ex["readme"], "install_section": ex["install_section"]}


# -- the scorecard ----------------------------------------------------------------------------
def _cmd_line(r: dict) -> str:
    return f"`{r['cmd']}` ({r.get('where') or '-'})"


def _last(text: str | None, n: int = 3) -> str:
    lines = [x for x in (text or "").strip().splitlines() if x.strip()]
    return " / ".join(x.strip()[:160] for x in lines[-n:])


def _failed(r: dict) -> str:
    st = r.get("status")
    if st == "timeout":
        what = f"didn't finish in {r.get('wall_s')} s"
    elif st == "not_run":
        what = r.get("why") or "not run"
    else:
        what = f"exits {r.get('rc')}"
    out = _last(r.get("output"))
    return f"{_cmd_line(r)} {what}" + (f": {out}" if out else "")


NEEDS_INPUT = re.compile(
    r"EOFError|EOF when reading a line|Inappropriate ioctl for device|"
    r"not a (?:tty|terminal)|stdin is not a (?:tty|terminal)|"
    r"no tty present|getpass",
    re.I,
)


def _own_file(r: dict) -> list[str]:
    """The data files of the user's own a failed command names and its output says it
    couldn't find (``cmdrunner.inputs_missing``)."""
    if r.get("status") != "failed":
        return []
    out = r.get("output") or ""
    return [m for m in r.get("missing") or [] if m in out]


def _unavailable(r: dict) -> bool:
    """A command whose tool isn't in the sandbox (``uv``, ``make``...), one that asked for
    input (an operator at a terminal would type it; the check has none), or one whose
    example input is a file of the user's own (``nextread import tabs.txt``): not a
    failure of the README."""
    if r.get("status") not in ("failed", "timeout"):
        return False
    return (
        (r.get("rc") == 127 and _first_word(r["cmd"]) in EXTERNAL)
        or bool(NEEDS_INPUT.search(r.get("output") or ""))
        or bool(_own_file(r))
    )


def items(
    res: dict, service: bool | None = None, *, tests: bool = True, restart: bool = True
) -> dict[str, dict]:
    """Scorecard items from a ``run``: ``commands``, and ``tests_green`` / ``restart_now``
    when those phases ran. Each has ``detail`` (the commands and their output, for the
    repair brief) and ``commands`` (what ran)."""
    from .readiness import _item

    out = {}
    ex = res.get("extract") or {}
    if not res.get("ran"):
        why = res.get("why") or "not run"
        out["commands"] = _item(
            "commands",
            "amber",
            f"not checked: {why}",
            "run the README's quick start by hand on a fresh copy",
            fixable=False,
        )
        if tests:
            out["tests_green"] = _item(
                "tests_green",
                "amber",
                f"not checked: {why}",
                "run the README's test commands by hand",
                fixable=False,
            )
        if restart:
            out["restart_now"] = _item(
                "restart_now",
                "amber",
                f"not checked: {why}",
                "stop and start it again at once by hand",
                fixable=False,
            )
        return out
    quick = [r for r in res.get("quick") or [] if r.get("status") != "skipped"]
    bad = [r for r in quick if r.get("status") in ("failed", "timeout") and not _unavailable(r)]
    unav = [r for r in quick if _unavailable(r)]
    skipped = [e for e in (ex.get("quick") or []) + (ex.get("skipped") or []) if e.get("skip")]
    ran = [r for r in quick if r.get("status") in ("ok", "serving")]
    detail = "\n".join(
        f"- {_cmd_line(r)}: {r.get('status')}"
        + (f", exit {r['rc']}" if r.get("rc") not in (None, 0) else "")
        + (
            f"\n  output: {_last(r.get('output'), 8)}"
            if r.get("status") in ("failed", "timeout")
            else ""
        )
        for r in quick
    )
    if not ex.get("install_section"):
        it = _item(
            "commands",
            "red" if not ex.get("readme") else "amber",
            "the README has no install or quick-start section with commands"
            if ex.get("readme")
            else "no README",
            "add a quick start: the exact commands to install and run it",
        )
    elif bad:
        it = _item(
            "commands",
            "red",
            "a README command fails as written: "
            + _failed(bad[0])
            + (f" (and {len(bad) - 1} more)" if len(bad) > 1 else ""),
            "make every quick-start command in the README work exactly as written on a "
            "fresh copy (fix the command or the script, or the README)",
        )
    elif not ran and skipped and all(DEP.search(e["cmd"]) for e in skipped):
        it = _item(
            "commands",
            "n/a",
            "the quick start only installs it ("
            + ", ".join(f"`{e['cmd']}`" for e in skipped[:2])
            + "): nothing to run",
            fixable=False,
        )
    elif not ran:
        it = _item(
            "commands",
            "amber",
            "no quick-start command could be run as written"
            + (f" ({skipped[0]['skip']}: `{skipped[0]['cmd']}`)" if skipped else ""),
            "check the quick start by hand on a fresh copy",
            fixable=False,
        )
    else:
        asks = [r for r in unav if NEEDS_INPUT.search(r.get("output") or "")]
        own = [r for r in unav if r not in asks and _own_file(r)]
        tools = [r for r in unav if r not in asks and r not in own]
        alts = [r for r in res.get("quick") or [] if r.get("alternative")]
        reasons = sorted({e["skip"].split(":")[0] for e in skipped})
        it = _item(
            "commands",
            "amber" if asks or tools else "green",
            f"{len(ran)} quick-start command(s) worked as written on a fresh copy"
            + (
                f"; {len(asks)} asked for input, which the check can't type: "
                + ", ".join(f"`{r['cmd']}`" for r in asks)
                if asks
                else ""
            )
            + (
                f"; {len(tools)} need a tool the check doesn't have: "
                + ", ".join(f"`{r['cmd']}`" for r in tools)
                if tools
                else ""
            )
            + (
                f"; {len(own)} take a file of the user's own: "
                + ", ".join(f"`{r['cmd']}` ({', '.join(_own_file(r))})" for r in own)
                if own
                else ""
            )
            + (
                f"; {len(alts)} another way to start or do the same, not run: "
                + ", ".join(f"`{r['cmd']}`" for r in alts)
                if alts
                else ""
            )
            + (f"; {len(skipped)} not run ({'; '.join(reasons)})" if skipped else ""),
            "check those by hand" if asks or tools else "",
            fixable=False,
        )
    it.update(
        detail=detail,
        commands=[{k: r.get(k) for k in ("cmd", "where", "status", "rc")} for r in quick],
    )
    out["commands"] = it
    if tests and res.get("tests") is not None:
        out["tests_green"] = tests_item(res, ex)
    if restart and res.get("restart") is not None:
        out["restart_now"] = restart_item(res, quick, service)
    return out


def tests_item(res: dict, ex: dict) -> dict:
    from .readiness import _item

    rs = [r for r in res.get("tests") or []]
    doc = [
        e
        for e in ex.get("tests") or []
        if e.get("where", "").startswith(ex.get("readme") or "README")
    ]
    bad = [
        r for r in rs if r.get("status") in ("failed", "timeout", "not_run") and not _unavailable(r)
    ]
    unav = [r for r in rs if _unavailable(r)]
    ok = [r for r in rs if r.get("status") == "ok"]
    detail = "\n".join(
        f"- {_cmd_line(r)}: {r.get('status')}"
        + (f", exit {r['rc']}" if r.get("rc") not in (None, 0) else "")
        + (f"\n  output: {_last(r.get('output'), 12)}" if r.get("status") != "ok" else "")
        for r in rs
    )
    if not doc:
        it = _item(
            "tests_green",
            "amber",
            "the README documents no test command",
            "document the command that runs every test suite, exactly as it is typed",
        )
    elif bad:
        it = _item(
            "tests_green",
            "red",
            "a documented test command fails as written: "
            + _failed(bad[0])
            + (f" (and {len(bad) - 1} more)" if len(bad) > 1 else ""),
            "make every test command the README documents run green exactly as "
            "written, on a fresh copy with only the declared dependencies",
        )
    elif not ok:
        it = _item(
            "tests_green",
            "amber",
            "no documented test command could be run as written"
            + (f" ({unav[0]['cmd']} needs a tool the check doesn't have)" if unav else ""),
            "run the test commands by hand",
            fixable=False,
        )
    else:
        it = _item(
            "tests_green",
            "amber" if unav else "green",
            f"{len(ok)} documented test command(s) run green as written: "
            + ", ".join(f"`{r['cmd']}`" for r in ok[:4])
            + (f"; not run here: {', '.join(r['cmd'] for r in unav)}" if unav else ""),
            "run those by hand" if unav else "",
            fixable=False,
        )
    it.update(
        detail=detail,
        commands=[{k: r.get(k) for k in ("cmd", "where", "status", "rc")} for r in rs],
        source="harness",
    )
    return it


def restart_item(res: dict, quick: list[dict], service: bool | None) -> dict:
    from .readiness import _item

    rs = res.get("restart") or {}
    served = any(r.get("status") == "serving" or r.get("left_serving") for r in quick)
    if not rs.get("ran"):
        if not served and service is not True:
            return _item(
                "restart_now", "n/a", "not a service: nothing listens on a port", fixable=False
            )
        return {
            **_item(
                "restart_now",
                "amber",
                "couldn't start it to check: "
                + (rs.get("why") or "no start command")
                + "".join(
                    f"; `{a['recipe']['start']}` {a['why']}" for a in (rs.get("attempts") or [])[:2]
                ),
                "document one command that runs the service in the foreground",
                fixable=bool(served or service),
            ),
            "detail": json.dumps(rs.get("attempts") or [], default=str)[:1500],
        }
    rec, port = rs.get("recipe") or {}, rs.get("port")
    how = f"`{rec.get('start')}` on 127.0.0.1:{port}"
    r1, r2 = rs.get("restart") or {}, rs.get("crash") or {}
    g = rs.get("graceful") or {}
    lines = [
        f"- started with {how} ({rec.get('source')}), used it with 5 requests",
        "- stopped with SIGTERM: "
        + (
            f"exited in {g.get('exit_s')} s (exit {g.get('rc')})"
            if g.get("exited")
            else "still running after 10 s"
        ),
        f"- started again {r1.get('started_after_s')} s later: "
        + (
            "serving after " + str(r1.get("serve_s")) + " s"
            if r1.get("ok")
            else (r1.get("why") or "failed") + f"; output: {_last(r1.get('output'), 6)}"
        ),
    ]
    if r2.get("ok") is not None:
        lines.append(
            f"- killed with SIGKILL, started again {r2.get('started_after_s')} s later: "
            + (
                "serving after " + str(r2.get("serve_s")) + " s"
                if r2.get("ok")
                else (r2.get("why") or "failed") + f"; output: {_last(r2.get('output'), 6)}"
            )
        )
    detail = "\n".join(lines)
    if not r1.get("ok") or r2.get("ok") is False:
        which = "after a graceful stop" if not r1.get("ok") else "after a crash (SIGKILL)"
        r = r1 if not r1.get("ok") else r2
        it = _item(
            "restart_now",
            "red",
            f"can't start again at once {which}: {how} "
            f"{r.get('why')}" + (f" ({_last(r.get('output'), 1)})" if r.get("output") else ""),
            "make it start and serve immediately after a stop or a crash (for example: "
            "allow address reuse on the listening socket, and release locks and pid "
            "files a crash leaves behind)",
        )
    else:
        it = _item(
            "restart_now",
            "green",
            f"{how} served again {r1.get('serve_s')} s after a "
            f"graceful stop and {r2.get('serve_s')} s after a SIGKILL",
        )
    it.update(detail=detail, evidence=detail)
    return it

"""The exam's install phase for Node products (EX-PLAN §5.1, decision 10).

Python products keep ``candidate_venv`` (installed inside a sandbox like this one, cached by
requirement set) and get their build backends as wheels (``pyinstall.py``). A product with a
``package.json`` gets its packages from inside a sandbox of their own:

- only ``package.json`` and the lockfile are copied in, never the product's code, and the
  install runs with ``--ignore-scripts``: no product code runs while there is a way out;
- the sandbox has its own network namespace (loopback only); its one way out is the host's
  ``netproxy.Proxy`` on a unix socket, with an allow-list of names (``exam.indexes``, default
  pypi.org, files.pythonhosted.org, registry.npmjs.org) and, as always, public addresses only;
- the bridge and the socket exist for this phase only: the exam's own run (the product's
  tests and the scenarios) never has them.

``node_modules`` is cached per ``package.json`` + lockfile hash under the den
(``<den>/exam-cache/node/<hash>/``) and bound read-only into the exam's runs. A failure is
"could not install here" (amber), never red; ``exam.indexes: []`` means no network at all."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

LOCKS = ("package-lock.json", "npm-shrinkwrap.json")
TIMEOUT_S = 600
PROXY_DIR = "/run/repo_factory"


def needs(tree: Path) -> dict | None:
    """The product's Node manifest, when it declares packages: ``{"deps": n, "lock"}``."""
    p = Path(tree) / "package.json"
    if not p.is_file():
        return None
    try:
        j = json.loads(p.read_text())
    except (OSError, ValueError):
        return {"deps": 0, "lock": None, "bad": True}
    deps = {
        **(j.get("dependencies") or {}),
        **(j.get("devDependencies") or {}),
        **(j.get("optionalDependencies") or {}),
    }
    lock = next((n for n in LOCKS if (Path(tree) / n).is_file()), None)
    return {"deps": len(deps), "lock": lock}


def key(tree: Path) -> str:
    h = hashlib.sha256()
    for n in ("package.json", *LOCKS):
        p = Path(tree) / n
        if p.is_file():
            h.update(n.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()[:20]


def cache_root(run) -> Path:
    return Path(run.den.root) / "exam-cache" / "node"


def install(run, tree: Path, cfg: dict) -> dict:
    """``{"dirs": {"node_modules": host dir} or {}, "node": what happened}``."""
    n = needs(tree)
    if n is None:
        return {"dirs": {}}
    if not n["deps"]:
        return {"dirs": {}, "node": "no packages declared"}
    if not shutil.which("npm"):
        return {"dirs": {}, "node": "not_verifiable: needs npm, which this machine lacks"}
    indexes = list(cfg.get("indexes") or [])
    if not indexes:
        return {
            "dirs": {},
            "node": "not_verifiable: no package index allowed (exam.indexes is empty)",
        }
    k = key(tree)
    dest = cache_root(run) / k
    if (dest / ".ok").is_file():
        return {"dirs": {"node_modules": dest / "node_modules"}, "node": f"cached ({k})"}
    res = run_install(Path(tree), dest, indexes, n["lock"])
    if res["ok"]:
        return {
            "dirs": {"node_modules": dest / "node_modules"},
            "node": f"installed with {res['cmd']} ({k})",
        }
    return {"dirs": {}, "node": f"could not install here: {res['why']}"}


def install_cmd(lock: str | None) -> list[str]:
    base = ["--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"]
    return ["npm", "ci", *base] if lock else ["npm", "install", *base]


def run_install(tree: Path, dest: Path, indexes: list[str], lock: str | None) -> dict:
    """``npm ci`` (or ``npm install`` without a lockfile) in a sandbox whose only way out is
    the index proxy; the result moves into ``dest`` (``node_modules`` and ``.ok``)."""
    from ..harness import netproxy
    from ..harness.isolation import base_python, isolation, sandbox_cmd

    if isolation() != "bwrap":
        return {"ok": False, "why": "no bwrap sandbox on this machine", "cmd": None}
    npm = shutil.which("npm")
    cmd_line = install_cmd(lock)
    with (
        tempfile.TemporaryDirectory(prefix="repo-factory-npm-") as tmp,
        netproxy.Proxy(hosts=indexes) as px,
    ):
        work = Path(tmp) / "pkg"
        work.mkdir()
        for name in ("package.json", *LOCKS):
            if (tree / name).is_file():
                shutil.copy(tree / name, work / name)
        bindir = Path(tmp) / "bin"
        bindir.mkdir()
        shutil.copy(Path(netproxy.__file__), bindir / "netproxy.py")
        env = (
            ("HOME", "/home/user"),
            ("CI", "1"),
            ("LANG", "C.UTF-8"),
            ("PATH", "/usr/local/bin:/usr/bin:/bin"),
            ("npm_config_cache", "/home/user/.npm"),
            ("npm_config_update_notifier", "false"),
            *(
                (k, "http://127.0.0.1:3128")
                for k in (
                    "HTTPS_PROXY",
                    "HTTP_PROXY",
                    "https_proxy",
                    "http_proxy",
                    "npm_config_proxy",
                    "npm_config_https_proxy",
                )
            ),
        )
        cmd = sandbox_cmd(
            work,
            interp=base_python(),
            env=env,
            network=False,
            etc=True,
            home="/home/user",
            work_at="/work/product",
            binds=((px.dir, PROXY_DIR, False), (bindir, "/work/bin", True)),
        ) + [
            "-I",
            "/work/bin/netproxy.py",
            "--bridge",
            f"{PROXY_DIR}/proxy.sock",
            "3128",
            "--",
            npm,
            *cmd_line[1:],
        ]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
            ok, why = (
                p.returncode == 0,
                ((p.stderr or p.stdout or "").strip()[-600:] or f"exit {p.returncode}"),
            )
        except subprocess.TimeoutExpired:
            ok, why = False, f"timed out after {TIMEOUT_S} s"
        if ok:  # no packages at all: an empty install
            (work / "node_modules").mkdir(exist_ok=True)
        log = list(px.log)
        if ok:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmpdest = dest.with_name(dest.name + ".tmp")
            shutil.rmtree(tmpdest, ignore_errors=True)
            tmpdest.mkdir()
            shutil.move(str(work / "node_modules"), tmpdest / "node_modules")
            (tmpdest / ".ok").write_text(
                json.dumps({"cmd": " ".join(cmd_line), "requests": log[:200]})
            )
            shutil.rmtree(dest, ignore_errors=True)
            tmpdest.rename(dest)
    return {"ok": ok, "why": None if ok else why, "cmd": " ".join(cmd_line), "requests": log}

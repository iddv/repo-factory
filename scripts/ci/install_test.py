#!/usr/bin/env python3
"""A stranger's first three commands, against a built wheel, in an empty home:

    uv tool install <the wheel>
    repo-factory doctor
    repo-factory ui

    python3 scripts/ci/install_test.py dist/repo_factory-*.whl [--port 8790]

Checks: ``--version`` prints a version; ``doctor`` prints one line for each requirement (it
may say one is missing: a CI runner has no Claude Code login); the page starts on 127.0.0.1,
prints a sign-in link, serves the built page and answers its first read; and nothing is written
in the home outside ``~/.repo-factory`` (and uv's own folders). Exits 1 on the first failure.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ALLOWED_IN_HOME = {".repo-factory", ".cache", ".local", ".config"}


def fail(msg: str) -> None:
    print(f"install test: FAILED: {msg}")
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wheel")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8790")))
    a = ap.parse_args()
    wheel = Path(a.wheel).resolve()
    work = Path(tempfile.mkdtemp(prefix="rf-install-"))
    home = work / "home"
    home.mkdir()
    uv_cache = subprocess.run(
        ["uv", "cache", "dir"], capture_output=True, text=True, check=True
    ).stdout.strip()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("REPO_FACTORY_", "UV_"))}
    env.update(
        HOME=str(home),
        UV_CACHE_DIR=uv_cache,
        UV_TOOL_DIR=str(work / "tools"),
        UV_TOOL_BIN_DIR=str(work / "bin"),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_DATA_HOME=str(home / ".local" / "share"),
    )
    rf = str(work / "bin" / "repo-factory")

    print("== uv tool install", wheel.name, flush=True)
    subprocess.run(
        ["uv", "tool", "install", "--quiet", "--python", "3.12", str(wheel)], env=env, check=True
    )

    print("== repo-factory --version", flush=True)
    v = subprocess.run([rf, "--version"], env=env, capture_output=True, text=True)
    print(v.stdout.strip())
    if v.returncode or not re.match(r"repo-factory \d", v.stdout):
        fail(f"--version said {v.stdout!r} {v.stderr!r}")

    print("== repo-factory doctor", flush=True)
    d = subprocess.run([rf, "doctor"], env=env, capture_output=True, text=True)
    print(d.stdout.rstrip(), d.stderr.rstrip(), f"(exit {d.returncode})", sep="\n")
    for what in ("Claude Code", "bubblewrap", "git", "gh"):
        if what.lower() not in d.stdout.lower():
            fail(f"doctor said nothing about {what}")
    lines = [ln for ln in d.stdout.splitlines() if ln.strip()]
    if len(lines) != 4:
        fail(f"doctor printed {len(lines)} lines, not one per requirement")

    print("== repo-factory ui", flush=True)
    log = work / "ui.log"
    with log.open("w") as f:
        ui = subprocess.Popen(
            [rf, "ui", "--port", str(a.port)],
            env=env,
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        url = None
        for _ in range(60):
            m = re.search(r"http://127\.0\.0\.1:\d+", log.read_text())
            if m:
                url = m.group(0)
                break
            if ui.poll() is not None:
                break
            time.sleep(0.5)
        print(log.read_text().rstrip())
        if not url:
            fail("the page printed no link")
        page = urllib.request.urlopen(url + "/", timeout=10).read().decode()
        if "<script" not in page:
            fail("the page is not the built app")
        state = json.loads(urllib.request.urlopen(url + "/api/state", timeout=10).read())
        if not isinstance(state, dict) or "builds" not in state:
            fail(f"api/state answered {str(state)[:200]}")
        print(f"page ok at {url}; api/state has {len(state['builds'])} builds")
    finally:
        os.killpg(ui.pid, signal.SIGTERM)
        try:
            ui.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(ui.pid, signal.SIGKILL)

    print("== what it wrote in the home", flush=True)
    top = sorted(p.name for p in home.iterdir())
    print(top)
    extra = set(top) - ALLOWED_IN_HOME
    if extra:
        fail(f"wrote outside its home: {sorted(extra)}")
    cfg = home / ".config"
    if cfg.exists() and any(p.name != "uv" for p in cfg.iterdir()):
        fail(f"wrote in ~/.config: {sorted(p.name for p in cfg.iterdir())}")
    if not (home / ".repo-factory").is_dir():
        fail("no home at ~/.repo-factory")
    shutil.rmtree(work, ignore_errors=True)
    print("install test: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

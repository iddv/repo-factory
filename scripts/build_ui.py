"""Build the web UI into repo_factory/ui/static/app/: the one entry CI, the release build and a
contributor run. Needs Node 22.12 or newer;
users never do, the wheel carries the output.

    python3 scripts/build_ui.py              npm ci (no install scripts), then vite build
    python3 scripts/build_ui.py --no-install vite build with the node_modules already there

Then it checks what was built: the manifest names the entry, every file it names exists, and
the page's JavaScript and CSS stay under the budget (BUDGET_GZ, gzipped), and prints the sizes.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "repo_factory" / "ui" / "web"
OUT = ROOT / "repo_factory" / "ui" / "static" / "app"
ENTRY = "src/main.ts"
NODE_FLOOR = (22, 12)
BUDGET_GZ = 64 * 1024  # what every page loads first (about 33 KB in F1)
LAZY_BUDGET_GZ = 30 * 1024  # one page's own file, loaded when its route first opens
# a library chunk loaded only on demand, by its name (vite.config.ts manualChunks); none today
LIBRARY_BUDGET_GZ: dict[str, int] = {}


def node_version() -> tuple[int, int] | None:
    node = shutil.which("node")
    if not node:
        return None
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    m = re.fullmatch(r"v(\d+)\.(\d+)\.\d+", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def npm(*args: str) -> None:
    print("+ npm", " ".join(args), flush=True)
    subprocess.run(["npm", *args], cwd=WEB, check=True)


def sizes() -> list[tuple[str, int, int]]:
    """``(name, bytes, gzipped bytes)`` for every file the entry loads, from the manifest."""
    manifest = json.loads((OUT / ".vite" / "manifest.json").read_text())
    entry = manifest[ENTRY]
    names, todo = [], [ENTRY]
    while todo:
        chunk = manifest[todo.pop()]
        for n in (chunk["file"], *chunk.get("css", [])):
            if n not in names:
                names.append(n)
        todo += [i for i in chunk.get("imports", []) if manifest[i]["file"] not in names]
    assert entry["file"] in names
    out = []
    for n in names:
        data = (OUT / n).read_bytes()
        out.append((n, len(data), len(gzip.compress(data, compresslevel=6, mtime=0))))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-install", action="store_true", help="skip npm ci")
    a = ap.parse_args(argv)
    v = node_version()
    if v is None or v < NODE_FLOOR:
        print(
            f"build_ui: Node {'.'.join(map(str, NODE_FLOOR))} or newer is needed to build the "
            f"UI (found {'.'.join(map(str, v)) if v else 'none'}); users of a release never "
            "need it",
            file=sys.stderr,
        )
        return 2
    if not a.no_install:
        npm("ci", "--ignore-scripts", "--no-audit", "--no-fund")
    npm("run", "build")
    try:
        files = sizes()
    except (OSError, KeyError, ValueError) as e:
        print(f"build_ui: the build in {OUT} is incomplete: {e!r}", file=sys.stderr)
        return 1
    total = sum(gz for _, _, gz in files)
    for n, raw, gz in files:
        print(f"  app/{n}: {raw / 1024:.1f} KB, {gz / 1024:.1f} KB gzipped")
    print(
        f"build_ui: {len(files)} files, {total / 1024:.1f} KB gzipped "
        f"(budget {BUDGET_GZ // 1024} KB)"
    )
    if total > BUDGET_GZ:
        print("build_ui: over the budget; split the page or cut a dependency", file=sys.stderr)
        return 1
    lazy = lazy_sizes({n for n, _, _ in files})
    if lazy:
        pages = [x for x in lazy if budget_of(x[0]) == LAZY_BUDGET_GZ]
        big = max(pages or lazy, key=lambda x: x[2])
        print(
            f"build_ui: {len(lazy)} more files load when their page first opens, "
            f"{sum(gz for _, _, gz in lazy) / 1024:.1f} KB gzipped in all, the largest "
            f"page {big[0]} {big[2] / 1024:.1f} KB (budget {LAZY_BUDGET_GZ // 1024} KB each)"
        )
        for n, _, gz in lazy:
            if budget_of(n) != LAZY_BUDGET_GZ:
                print(
                    f"build_ui: the library {n} {gz / 1024:.1f} KB "
                    f"(its own budget {budget_of(n) // 1024} KB)"
                )
        over = [n for n, _, gz in lazy if gz > budget_of(n)]
        if over:
            print(f"build_ui: over the budget for one page: {', '.join(over)}", file=sys.stderr)
            return 1
    return 0


def budget_of(name: str) -> int:
    """A lazy file's budget: its library's (``LIBRARY_BUDGET_GZ``, by name), else a page's."""
    return next((v for k, v in LIBRARY_BUDGET_GZ.items() if name.startswith(k)), LAZY_BUDGET_GZ)


def lazy_sizes(entry: set[str]) -> list[tuple[str, int, int]]:
    """``(name, bytes, gzipped bytes)`` for every file the page loads later (a moved page's
    chunk, imported when its route first opens), from the manifest."""
    manifest = json.loads((OUT / ".vite" / "manifest.json").read_text())
    out = []
    for chunk in manifest.values():
        n = chunk["file"]
        if n in entry or not n.endswith(".js") or any(n == x[0] for x in out):
            continue
        data = (OUT / n).read_bytes()
        out.append((n, len(data), len(gzip.compress(data, compresslevel=6, mtime=0))))
    return sorted(out)


if __name__ == "__main__":
    sys.exit(main())

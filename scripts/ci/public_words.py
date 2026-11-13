#!/usr/bin/env python3
"""Words that must never appear in this public repository: in a tracked file, a commit
message, or a pull request's title and body.

    public_words.py files              every tracked file (git ls-files)
    public_words.py commits RANGE      the messages of the commits in RANGE (e.g. BASE..HEAD)
    public_words.py text               the environment's PR_TITLE and PR_BODY

It checks for:

- **private names**: a short list of personal and private names (a person's name and
  address, private repository and host names). They are stored as hashes, so this file does
  not itself contain them; a token of the text whose hash is on the list is a hit;
- **home paths** (``/home/<someone>``), other than the generic ones documentation uses;
- **account numbers** (a bare 12-digit number);
- **attribution lines** (a co-author trailer, a "generated with" footer, a session link);
- **secret-shaped literals** (a provider's key prefix, a private key block). Tests build
  tokens at run time instead.

Prints one line per hit and exits 1; exits 0 when clean. Standard library only.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

# sha256(lowercased token)[:20]. Ask a maintainer to add a name; never write the name here.
PRIVATE = frozenset(
    {
        "4366c7a3d28b0817b08d",
        "f263f1028f6ff5644d8b",
        "e53d68d58a31cb68fde9",
        "8be0b5445fca6eb16d14",
        "e8f4298811a1ebdcc747",
        "772ddc1ad4261891581b",
        "53f9ecd33c2e26921d42",
        "922180443f68a9ce949b",
        "2d4c831a216f977127a1",
        "1c7a550e3f8accecc097",
        "1d4a9e10d48ce8b5de14",
        "6f8dc0fe09402fa61541",
        "dcc329154e1e15607e2a",
        "5c774ff7b9e4dcb8a1c9",
    }
)

GENERIC_HOMES = (
    "user",
    "someone",
    "you",
    "runner",
    "example",
    "me",
    "alice",
    "bob",
    "ubuntu",
    "demo",
)
HOME = re.compile(r"/home/(?!(?:" + "|".join(GENERIC_HOMES) + r")\b)[a-z_][a-z0-9_-]*")
ACCOUNT = re.compile(r"(?<![0-9a-fA-F.:/_-])\d{12}(?![0-9a-zA-Z_-])")

# Built from pieces so that this file is not itself a hit.
_CO = "co-" + "authored-by"
ATTRIBUTION = [
    re.compile(p, re.IGNORECASE)
    for p in (
        _CO + r"\s*:",
        r"generated\s+with\s+\[?" + "clau" + "de",
        "clau" + r"de\.ai/code",
        "clau" + "de-session:",
    )
]
SECRET = [
    re.compile(p)
    for p in (
        "sk-" + r"ant-[A-Za-z0-9_-]{8,}",
        "gh" + r"[pousr]_[A-Za-z0-9]{20,}",
        "github" + r"_pat_[A-Za-z0-9_]{20,}",
        "AK" + r"IA[0-9A-Z]{16}",
        "-----BEGIN " + r"[A-Z ]*PRIVATE KEY-----",
        "xox" + r"[baprs]-[A-Za-z0-9-]{10,}",
    )
]

TOKEN = re.compile(r"[a-z0-9][a-z0-9._@+-]*[a-z0-9]")
SKIP_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".pdf",
    ".gz",
    ".zip",
    ".lock",
)
SKIP_NAMES = ("package-lock.json",)


def _digest(word: str) -> str:
    return hashlib.sha256(word.encode()).hexdigest()[:20]


def _pieces(token: str):
    yield token
    for part in re.split(r"[._@+/]", token):
        if part and part != token:
            yield part
        dashed = part.split("-")
        if len(dashed) > 1:
            yield from dashed
            yield from ("-".join(dashed[i : i + 2]) for i in range(len(dashed) - 1))


def private_hits(text: str) -> list[str]:
    found = []
    low = text.lower().replace("_", " ")
    for m in TOKEN.finditer(low):
        if any(_digest(p) in PRIVATE for p in _pieces(m.group())):
            found.append(m.group())
    return found


def line_hits(line: str) -> list[str]:
    """The kinds of problem on one line of text (empty when clean)."""
    kinds = []
    if private_hits(line):
        kinds.append("a private name")
    if HOME.search(line):
        kinds.append("a home path")
    if ACCOUNT.search(line):
        kinds.append("an account-number-shaped number")
    if any(p.search(line) for p in ATTRIBUTION):
        kinds.append("an attribution line")
    if any(p.search(line) for p in SECRET):
        kinds.append("a secret-shaped literal")
    return kinds


def scan(text: str, where: str) -> list[str]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for kind in line_hits(line):
            out.append(f"{where}:{n}: {kind}")
    return out


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode()
    return [f for f in out.split("\0") if f]


def scan_files(root: Path) -> list[str]:
    hits = []
    for rel in tracked_files(root):
        if rel.endswith(SKIP_SUFFIXES) or rel.rsplit("/", 1)[-1] in SKIP_NAMES:
            continue
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        hits += scan(rel, "path")  # a file's name counts too
        hits += scan(text, rel)
    return hits


def scan_commits(rev: str, root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%h%x00%an <%ae>%n%B%x01", rev],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    hits = []
    for rec in out.split("\x01"):
        if "\x00" in rec:
            sha, msg = rec.split("\x00", 1)
            hits += scan(msg, f"commit {sha.strip()}")
    return hits


def main(argv: list[str]) -> int:
    root = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parents[2]))
    if argv[:1] == ["files"]:
        hits = scan_files(root)
    elif argv[:1] == ["commits"] and len(argv) == 2:
        hits = scan_commits(argv[1], root)
    elif argv[:1] == ["text"]:
        hits = scan(
            "\n".join(os.environ.get(k, "") for k in ("PR_TITLE", "PR_BODY")), "pull request"
        )
    else:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        return 2
    for h in hits:
        print(h)
    if hits:
        print(
            "This repository is public: remove what is named above (the line says which "
            "kind of problem it is), then push again."
        )
        return 1
    print("public words: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

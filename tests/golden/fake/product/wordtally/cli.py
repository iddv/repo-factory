"""wordtally: count the words in text files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def count_words(text: str) -> int:
    """The number of whitespace-separated words in ``text``."""
    return len(text.split())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="wordtally", description="Count the words in text files.")
    ap.add_argument("files", nargs="+", help="text files to count")
    args = ap.parse_args(argv)
    total = 0
    for name in args.files:
        path = Path(name)
        try:
            n = count_words(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            print(f"wordtally: {name}: {e.strerror}", file=sys.stderr)
            return 1
        total += n
        print(f"{n}\t{name}")
    if len(args.files) > 1:
        print(f"{total}\ttotal")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""A fake ``gh`` for Feedback's File it (FB): the tests and a scratch server put it first on
``PATH`` (as ``gh``) so the whole path runs, from the press to the issue's link, with nothing
sent to GitHub. It answers the four calls ``repo_factory.feedback`` makes and writes each issue it
"files" to ``$FAKE_GH_DIR/issues.jsonl``:

- ``gh api user -q .login``: ``$FAKE_GH_LOGIN`` (default ``someone``); exit 4 when
  ``FAKE_GH_STATE=signed_out``
- ``gh api repos/<o>/<r> -q .has_issues``: ``true``; exit 1 (HTTP 404) when
  ``FAKE_GH_STATE=no_access``
- ``gh api --paginate repos/<o>/<r>/labels?...``: ``$FAKE_GH_LABELS`` (comma-separated,
  default ``bug,question``) as GitHub's JSON
- ``gh api -X POST --input <file> repos/<o>/<r>/issues``: the next number from 4242, its
  ``html_url`` on github.com

``FAKE_GH_STATE=offline`` makes every call fail as gh does without a network. Anything else
exits 1 with "fake gh: not faked"."""

import json
import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    state = os.environ.get("FAKE_GH_STATE", "ready")
    if state == "offline":
        print("error connecting to api.github.com", file=sys.stderr)
        return 1
    if argv[:1] != ["api"]:
        print("fake gh: not faked: " + " ".join(argv[:3]), file=sys.stderr)
        return 1
    args = argv[1:]
    method, payload, rest = "GET", None, []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-X":
            method, i = args[i + 1], i + 2
        elif a == "--input":
            payload, i = json.loads(Path(args[i + 1]).read_text()), i + 2
        elif a in ("-q", "-H", "--jq"):
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            rest.append(a)
            i += 1
    path = (rest or [""])[0].split("?")[0]
    if path == "user":
        if state == "signed_out":
            print("gh: To get started with GitHub CLI, please run:  gh auth login", file=sys.stderr)
            return 4
        print(os.environ.get("FAKE_GH_LOGIN", "someone"))
        return 0
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "repos":
        if state == "no_access":
            print("gh: Not Found (HTTP 404)", file=sys.stderr)
            return 1
        print("true")
        return 0
    if len(parts) == 4 and parts[0] == "repos" and parts[3] == "labels":
        names = [x for x in os.environ.get("FAKE_GH_LABELS", "bug,question").split(",") if x]
        print(json.dumps([{"name": n} for n in names]))
        return 0
    if len(parts) == 4 and parts[0] == "repos" and parts[3] == "issues" and method == "POST":
        d = Path(os.environ.get("FAKE_GH_DIR") or ".")
        d.mkdir(parents=True, exist_ok=True)
        log = d / "issues.jsonl"
        n = 4242 + (len(log.read_text().splitlines()) if log.exists() else 0)
        with log.open("a") as f:
            f.write(
                json.dumps({"repo": f"{parts[1]}/{parts[2]}", "number": n, **(payload or {})})
                + "\n"
            )
        print(
            json.dumps(
                {"number": n, "html_url": f"https://github.com/{parts[1]}/{parts[2]}/issues/{n}"}
            )
        )
        return 0
    print("fake gh: not faked: api " + path, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

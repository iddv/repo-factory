"""Sync this repository's labels from .github/labels.yml: each label the repository does not
have yet is created. A label it already has keeps its colour and description (yours to
change), and nothing is ever deleted here.

Standard library and the `gh` CLI only. Run by .github/workflows/labels.yml with GH_TOKEN and
GH_REPO set; a person can run it the same way: `GH_REPO=owner/name python3
.github/scripts/sync_labels.py`."""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = os.path.join(HERE, "..", "labels.yml")


def read_labels(path):
    """One JSON object per `- {...}` line (labels.yml is written that way so no YAML parser
    is needed)."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s.startswith("- {"):
                out.append(json.loads(s[2:]))
    return out


def main():
    labels = read_labels(LABELS)
    if not labels:
        sys.exit("no labels found in .github/labels.yml")
    repo = os.environ.get("GH_REPO")
    listed = ["gh", "label", "list", "--limit", "500", "--json", "name"]
    p = subprocess.run(listed + (["--repo", repo] if repo else []), capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"could not list the labels: {(p.stderr or p.stdout).strip()}")
    have = {x["name"] for x in json.loads(p.stdout or "[]")}
    failed = 0
    for lab in labels:
        if lab["name"] in have:
            print(f"kept {lab['name']} (already here)")
            continue
        cmd = [
            "gh",
            "label",
            "create",
            lab["name"],
            "--color",
            lab["color"],
            "--description",
            lab.get("description", ""),
        ]
        if repo:
            cmd += ["--repo", repo]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            failed += 1
            print(f"FAILED {lab['name']}: {(p.stderr or p.stdout).strip()}")
        else:
            print(f"ok {lab['name']}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

"""A quiet readiness stage for the offline v5 builds (Addendum C): the security review finds
nothing, the operational checks are green, and the readiness repair adds what the harness
checks itself (a CI workflow, a ``.gitignore``, the README's sections). The scripted
harnesses call ``quiet`` first; it returns the session's name, or None for other prompts."""

import json
from pathlib import Path

from repo_factory.v5.readiness import CHECKS

SECURITY = "You are a security engineer"
CHECKS_PROMPT = "You are an operations engineer"
REPAIR = "=== Items ==="
SESSION_CHECKS = [k for k, (_, how) in CHECKS.items() if how == "session"]
NA = ("bind_localhost", "health", "first_admin", "shutdown", "logs")

CI = """name: test
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install pytest
      - run: python -m pytest
"""

README = """# {name}

{blurb}

## Install

    pip install .

## Configuration

There are no options.

## Running

Import it from Python.

## Upgrading

Install the new version over the old one; nothing is stored.

## Backup and restore

Nothing to back up: it keeps no data.

## Troubleshooting

Run the tests: `python -m pytest`.

## Limitations

ASCII words only.
"""


def security_report(findings=()) -> dict:
    return {
        "threat_model": {
            "summary": "A library called in process; no network surface.",
            "entry_points": [
                {"name": "the Python API", "reach": "local", "auth": "none", "notes": "in process"}
            ],
            "assets": [{"name": "none stored", "kind": "integrity", "where": "-"}],
        },
        "checks": [
            {
                "area": "injection",
                "what": "text with quotes and HTML",
                "result": "pass",
                "evidence": "returned as data",
            }
        ],
        "findings": list(findings),
        "dependency_audit": {
            "tool": "pip-audit",
            "status": "no_dependencies",
            "output": "no third-party dependencies",
        },
        "limits": ["Only the Python API was reviewed."],
    }


def checks_report(overrides: dict | None = None) -> dict:
    rows = []
    for k in SESSION_CHECKS:
        row = {
            "id": k,
            "status": "n/a" if k in NA else "green",
            "why": "checked by doing it",
            "evidence": "ran it",
            "next": "",
            "fixable": False,
        }
        row.update((overrides or {}).get(k) or {})
        rows.append(row)
    return {
        "service": False,
        "install": {"commands": ["pip install ."], "result": "worked"},
        "checks": rows,
    }


def write_ready_files(ws: Path, name: str = "Textstats", blurb: str = "Counts words.") -> None:
    ws = Path(ws)
    (ws / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (ws / ".github" / "workflows" / "test.yml").write_text(CI)
    (ws / ".gitignore").write_text("__pycache__/\n*.pyc\n.venv/\n")
    text = README.format(name=name, blurb=blurb)
    p = ws / "README.md"
    if p.exists():  # keep what the README already says
        text = p.read_text().rstrip() + "\n\n" + text.split("\n## ", 1)[1].join(["## ", ""])
    p.write_text(text)


def quiet(prompt: str, ws, opts: dict | None = None) -> str | None:
    """``opts``: the session's launch options; the checks sessions write into their exam
    workspace, bound at /work/exam (``exam.mounted``)."""
    from repo_factory.v5.exam import mounted

    ws = Path(ws)
    ex = mounted(opts or {}) or ws
    if SECURITY in prompt:
        (ex / "_verify" / "security.json").write_text(json.dumps(security_report()))
        return "security"
    if CHECKS_PROMPT in prompt:
        (ex / "_verify" / "readiness.json").write_text(json.dumps(checks_report()))
        return "checks"
    if REPAIR in prompt:
        write_ready_files(ws)
        return "ready-repair"
    return None

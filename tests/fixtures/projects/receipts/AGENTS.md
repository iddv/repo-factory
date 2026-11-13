# AGENTS.md

Receipts is a command-line tool that independently records what a coding agent ran and changed during a session: the shell commands with their exit codes, and the files created, changed or deleted. It then compares that record with the agent's own end-of-task summary and flags the discrepancies. It is Python 3.11+, standard library only, and runs on Linux and macOS with no network calls.

This repository is maintained through GitHub issues, as `.github/repo-factory.yml` describes.

## Setup, run, test

```sh
./install.sh                 # copies to ~/.local/share/receipts-app, launcher in ~/.local/bin
receipts --version           # check the install
receipts init                # create the store (0700) and the default config
receipts demo                # optional: load 6 demo sessions (`receipts demo --reset` removes them)
receipts list
```

- Alternatives to `./install.sh`: `pipx install .` or `pip install .`. To install elsewhere: `PREFIX=… BIN_DIR=… ./install.sh`.
- Record a session: `receipts run -- <agent>` or `receipts run --test-cmd "pytest -q" -- <agent> [args]`. Then use `receipts report last`, `ack`, `close`, `export` and `verify` (see README › Commands).
- Exit codes: `0` success; `1` flags met `report.fail_on`, or verification failed; `2` usage or input error.

### Tests

There are two suites, and both are the product's own.

```sh
# tests/ — unit, CLI and end-to-end tests (unittest)
python3 -m unittest discover -s tests -t .

# tests/acceptance/ — one file per behaviour, through the real interface (pytest)
python -m pip install pytest==8.3.3 && python -m pytest tests/acceptance
```

- pytest is only needed for the acceptance suite. Install it in a virtual environment if your system Python refuses.
- Run a single acceptance file with `python -m pytest tests/acceptance/<file>.py`.

## Architecture

| Path | Holds |
|---|---|
| `install.sh` | Installer: copies the app and puts the `receipts` launcher on `PATH`. |
| `pyproject.toml` | Packaging metadata (`pipx install .` / `pip install .`). |
| `receipts/__main__.py` | Runs the package as a command: `python -m receipts`. |
| `receipts/cli.py` | Command-line interface: argument parsing and the commands. |
| `receipts/runner.py` | `receipts run`: records an agent session. |
| `receipts/shim.py` | Per-session `sh`/`bash`/`zsh` wrappers that log each command, then run the real shell. |
| `receipts/snapshot.py` | Workspace snapshots (path, size, SHA-256) and before/after diffs. |
| `receipts/detect.py` | Test-run and build detection, test-result parsers, skip markers, test-file patterns. |
| `receipts/sources.py` | Finds the agent's final message and tool calls (Claude Code, Codex CLI, Aider, captured stdout). |
| `receipts/claims.py` | Rule-based claim extraction from the agent's summary. |
| `receipts/reconcile.py` | Builds a session model from its events and reconciles claims into flags. |
| `receipts/sessions.py` | Session operations: load, report, summary, ack/unack, close, delete. |
| `receipts/chain.py` | Append-only, hash-chained event log. |
| `receipts/store.py` | Store layout, session IDs, audit log, schema migrations. |
| `receipts/config.py` | Config file handling (`~/.config/receipts/config.toml` or `$RECEIPTS_CONFIG`). |
| `receipts/render.py` | Report rendering: terminal text, Markdown, JSON, HTML. |
| `receipts/demo.py` | `receipts demo`: the sample sessions. |
| `tests/test_units.py`, `test_cli.py`, `test_e2e.py`, `test_settings.py` | Unit tests, CLI flows, end-to-end recording with a scripted agent, and settings with both values of each. |
| `tests/fake_agent.py`, `tests/helpers.py` | Scripted agent and shared helpers for the tests above. |
| `tests/acceptance/` | The acceptance suite, one `test_<what_it_checks>.py` per behaviour. |
| `tests/acceptance/_helpers/` | `driver.py` and `run.py`: drivers the acceptance tests share. `conftest.py` sits alongside. |
| `SCOPE.md`, `SECURITY.md`, `CHANGELOG.md`, `NOTICE` | Scope and flows, known security issues, release notes, and the notice published with each release. |
| `.github/` | Issue templates, labels, CI workflows, and `repo-factory.yml`. |

Each session is a directory of append-only files, and the event log is hash-chained so `receipts verify` can detect edits. The store lives in `RECEIPTS_HOME` (default `~/.local/share/receipts`) and is versioned, with migrations run on upgrade.

## Conventions

- Standard library only. Do not add a dependency to fix a bug.
- Operator settings are config keys with an environment override (for example `RUN_BLOCK_UNCLOSED_SAME_DIR`, `RUN_NESTED_DIRS`). A new one goes in the config table in the README, and gets a test for each value. Never hard-code one side of a business rule.
- Every behaviour has one acceptance test file, `tests/acceptance/test_<what_it_checks>.py`, with a plain docstring saying what it checks and what is expected. Shared helpers live in `tests/acceptance/_helpers/` and drive the product's real interface.
- Capture is shell-based: commands spawned without a shell, or through an absolute-path shell such as `/bin/sh -c`, are not recorded. Every report states this; keep it true.
- Flag IDs are derived from a flag's kind and subject, so they stay stable when a report is recomputed.
- Keep the README quick start working as written, and update this file when a command, module or convention changes.
- Use plain words in user-facing files.
- Report security weaknesses as `SECURITY.md` directs, not in a public issue.

## Maintenance protocol (issues and labels)

Labels are the state; GitHub is the only record. `.github/labels.yml` defines them.

1. **Pick** an open issue labelled `go` with no assignee and no `claim:*` label (or one whose
   lease comment has expired and that has no open linked PR). `p1` before `p2` before `p3`.
2. **Claim** it: a person assigns themself; an agent adds `claim:agent` and a comment
   `<!-- repo-factory:claim lease_until=<UTC+6h> branch=<branch> -->  Working on this.` Leave issues
   with `claim:repo-factory` or an assignee alone.
3. **Branch** from `main`: `<who>/<N>-<slug>` (repo-factory uses `repo-factory/<N>-<slug>`).
4. **Reproduce first:** write a test in the product's own suite (see the tests above) that fails on `main` for the
   reason the issue describes. If you cannot make it fail, label `cannot-reproduce`, say why in a
   comment, remove your claim, and stop.
5. **Fix** with the smallest change that makes it pass without weakening another test.
6. **Verify:** run every command in *Setup, run, test*; the whole suite must be green; the
   README's quick start must still work as written.
7. **Documents:** update this file if a command, module or convention changed; `SECURITY.md` if a known issue changed; add a line under *Unreleased* in `CHANGELOG.md`.
8. **Open a PR** with the template (`.github/pull_request_template.md`): what and why, the
   failing test's name, the verification you ran, and `Fixes #N`. Remove your claim label; the
   PR is now the lock. **Never push to `main`. Never change `.github/workflows/` in a fix PR.**
   Never commit a secret, a database file or demo credentials.
9. The owner reviews and merges on GitHub. A release follows (`.github/repo-factory.yml` ›
   `release.on_merge`).

repo-factory follows exactly these steps; its comments carry `<!-- repo-factory:… -->` markers and the PR
label `repo-factory`. Policy for this repo: `.github/repo-factory.yml`.

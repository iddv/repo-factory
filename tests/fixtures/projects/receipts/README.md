# Receipts

Receipts gives you an independent record of what your coding agent actually did. It records the shell commands the agent runs through a captured shell (see Limits), with their exit codes, and the files the agent creates, changes or deletes in the workspace, apart from the default exclusions (see Configuration). At the end it compares that record with the agent's own summary and flags two kinds of discrepancy:

## At a glance

- **What it is:** A command-line tool that independently records what a coding agent actually ran and changed during a session, then flags where the agent's own end-of-task summary doesn't match.
- **Who it's for:** Developers who hand multi-step tasks to coding agents and need to check what the agent really executed, such as which tests failed, which files changed and which commands errored, rather than trusting the agent's report.
- **Quick start:**

  ```sh
  git clone https://github.com/iddv/receipts.git
  cd receipts
  ./install.sh
  receipts --version
  receipts init
  receipts list
  ```

The rest of this README covers configuration, data, backup and the full guide; SECURITY.md lists the known security issues; the checks that ran before each release are published with it (see NOTICE).

- **Claims the record does not support.** For example, the agent says "all tests pass" but the last test run failed. Or it says it edited a file that is unchanged.
- **Things the agent did but did not mention.** For example, a deleted test file, a newly added `it.skip`, or a config file it changed quietly.

It works with Claude Code, Codex CLI, Aider, Gemini CLI, and any other agent that runs commands through a shell. It runs on Linux and macOS. It has no dependencies beyond Python ≥ 3.11, and it runs no network services and makes no network calls.

## Install

```sh
./install.sh
```

This copies Receipts to `~/.local/share/receipts-app` and puts a `receipts` launcher in `~/.local/bin`. If that directory isn't already on your `PATH`, the script adds it to your shell rc file. Check the install with:

```sh
receipts --version
```

Alternatives: `pipx install .` or `pip install .`. To install elsewhere, set `PREFIX=… BIN_DIR=… ./install.sh`.

## Quick start

```sh
receipts init                       # create the store (0700) and the default config
receipts demo                       # optional: load 6 demo sessions
receipts list

cd ~/code/my-project
receipts run -- claude              # or: receipts run --test-cmd "pytest -q" -- codex exec "fix the bug"
# ... work with the agent as usual; when it exits:
#   Session 20261008-141502-a3f9: counts of commands, test runs and file changes
#   It then finds the agent's final message (or asks you to paste it) and prints the report.

receipts report last                # exits 1 if open flags meet report.fail_on
receipts ack last F1a2b3c --note "intended refactor"
receipts close last                 # seal the session; prints the final hash
receipts export last --format md --out receipts.md   # paste into the PR
```

## Commands

| Command | What it does |
|---|---|
| `receipts init` | Creates the store and config, and checks for a real `sh` and `bash`. |
| `receipts demo [--reset]` | Loads 6 demo sessions across 3 sample projects. `--reset` removes only the demo sessions. |
| `receipts run [--name L] [--test-cmd "C"] -- <agent> [args]` | Records a session. The agent keeps the real terminal. |
| `receipts summary <id> [--file P] [--replace] [--yes]` | Attaches the agent's summary: auto-detected, from a file, or pasted (end with Ctrl-D). Then reconciles. |
| `receipts report <id\|last>` | Prints the header, the flags by severity with their evidence, the matched claims, and the claims next to the recorded events. |
| `receipts ack <id> <flag> --note "why"` / `unack <id> <flag>` | Accepts a flag with a note (required, 1–500 characters), or reopens it. |
| `receipts close <id> [--force]` | Seals the session. It refuses while high flags are open unless `--force`, and a forced close is audited. |
| `receipts list [--dir D] [--agent A] [--since YYYY-MM-DD] [--status S] [--flagged]` | Lists sessions, newest first. |
| `receipts show <id> commands [--failed]` | Shows the full command log. Nested shells appear with a parent link. |
| `receipts show <id> files [--diff PATH]` | Shows file changes, or one file's diff. |
| `receipts show <id> output <n>` | Shows the stored output tail of command `n`. |
| `receipts delete <id> [--closed]` | Deletes a session after you type its ID to confirm. The deletion goes into the audit log. |
| `receipts export <id> --format md\|json\|html [--out P] [--force]` | Writes a report. |
| `receipts export <id> --bundle --out P` | Writes a single file with the full record, summary, flags and hash chain. |
| `receipts export --all --since YYYY-MM-DD --format json` | Writes a session index: one row per session with its counts and flag totals. |
| `receipts verify <id\|bundle-file>` / `verify --all` | Recomputes the hash chain. Prints `intact` with the final hash, or the first broken event (exit 1). |
| `receipts config get [key]` / `config set <key> <value>` | Reads or changes settings. |

**Exit codes.** `0` means success. `1` means flags met the `report.fail_on` threshold, or verification failed. `2` means a usage or input error.

## How capture works

`receipts run` does the following:

1. Takes a **snapshot** of the workspace: the path, size and SHA-256 of every file. It respects the exclusions and `.gitignore`. In a git repository it also records `HEAD` and whether the tree is dirty.
2. Starts the agent with your real terminal, inherited directly, so colours, raw mode and resize behave exactly as they do without Receipts. `PATH` gets a per-session shim directory containing `sh`, `bash` and `zsh` wrappers, and `$SHELL` points at the matching wrapper.
3. Each **wrapper** logs the command, working directory, start time, duration, exit code and output tail, then runs the real shell. Interactive shells are handed straight to the real shell. Every invocation gets a sequence number. Nested shells are logged with a parent link, and the counts include top-level commands only. Measured overhead is about 25 ms per command.
4. After the agent exits, it takes a second snapshot and records each file as created, modified or deleted. Text files get diffs. Binary files (detected by a NUL byte in the first 8 KB) and files over 5 MB are reported as changed without a diff.
5. It detects **test runs** and parses their results. Supported runners: pytest, unittest, Jest, Vitest, Mocha, go test, cargo test, RSpec, PHPUnit, Maven, Gradle and `dotnet test`. `npm/yarn/pnpm test` is parsed as Jest, Vitest or Mocha output. Any other command that matches `--test-cmd` is judged by its exit code. A run fails if it exits non-zero or reports more than 0 failures.
6. If a test command is set and `verify.rerun_tests = on`, Receipts runs the tests once itself and records the result as an **independent run**. That result becomes the final test state.

**Limits.** Commands that an agent spawns directly, without a shell, are not captured. Neither are commands run through an absolute-path shell such as `/bin/sh -c`. Every report states this.

### Where summaries come from

Receipts checks these sources in order:

1. Claude Code transcripts in `~/.claude/projects/<dir>/*.jsonl`, for this directory and time window. Receipts also reads the agent's `Bash` tool calls from them.
2. Codex CLI session files in `~/.codex/sessions/**`. Receipts also reads the shell calls from them.
3. Aider's `.aider.chat.history.md`.
4. The agent's captured stdout, for non-interactive runs (`claude -p`, `codex exec`, `gemini -p`, `aider -m`).

When one is found, Receipts shows its first 20 lines and asks you to confirm with y/n. Otherwise you can pass `--file`, or paste the summary on stdin.

### Claims and flags

Claim extraction is rule-based, and the patterns are documented in `receipts/claims.py`. It recognises these claims:

- tests pass, all tests pass, or `N tests passed`;
- tests were run or added;
- the build succeeds;
- a file was created, modified or deleted;
- a command was run (text in backticks that looks like a command).

A file counts as *mentioned* if its relative path appears in the summary, or if its basename appears and is unique among the changed files.

| Severity | Flag |
|---|---|
| high | tests claimed passing but the final test state failed; tests claimed passing with no test run (`report.unverified_tests`); a test file deleted and not mentioned; a skip marker added and not mentioned; a claimed pass count that differs from the parsed count; no summary attached |
| medium | a file claimed changed/created that is unchanged or missing; a file claimed deleted that still exists; a changed file not mentioned; a claimed command, or a transcript tool call, that is not in the record; a build claimed successful whose last build command failed |
| low | a non-zero exit, not mentioned, followed by a later successful run of the same command; recorded commands absent from the transcript's tool calls |

Flag IDs, such as `F1a2b3c`, are derived from the flag's kind and subject, so they stay the same each time the report is recomputed.

## Configuration

The config file is `~/.config/receipts/config.toml`, or the path in `RECEIPTS_CONFIG`. The store is in `RECEIPTS_HOME`, which defaults to `~/.local/share/receipts`. `receipts init` writes a commented config with every key in it.

| key | values | default | what it decides |
|---|---|---|---|
| `capture.output` | tail, none | tail | Whether command output tails are stored, or only metadata (for sensitive repos). |
| `capture.tail_lines` | integer | 200 | Maximum lines of output tail stored per command. |
| `capture.tail_bytes` | integer | 65536 | Maximum bytes of output tail stored per command. The smaller of the two limits applies. |
| `files.gitignored` | exclude, include | exclude | Whether gitignored files are part of snapshots. |
| `files.exclude` | list | `.git/, node_modules/, .venv/, venv/, __pycache__/, target/, dist/, build/, .next/, .aider*` | Snapshot exclusions. `name/` excludes a directory at any depth. Other entries are glob patterns on file names. `.git/` is always excluded. |
| `files.max_file_mb` | integer | 5 | Files larger than this are hashed, not diffed. |
| `files.max_files` | integer | 50000 | Workspace file limit. Files beyond it are hashed only, and Receipts warns. |
| `verify.rerun_tests` | on, off | off | Whether Receipts runs the test command itself after the agent exits. |
| `verify.test_cmd` | string | "" | The default test command when `--test-cmd` is not given. |
| `report.unverified_tests` | flag, ignore | flag | Whether "tests pass" with no recorded test run raises a high flag. |
| `report.fail_on` | high, any | high | Whether `report` exits 1 on open high flags only, or on any open flag. |
| `run.block_unclosed_same_dir` | allowed, refused | allowed | `allowed`: a new recording is refused only while another recording of the same directory is running. `refused`: it is also refused while any session for the directory is still unclosed (`open` or `interrupted`). Scope: per directory. Set per-directory values in `run.block_unclosed_same_dir_dirs` (a list of `"<dir>=allowed\|refused"` entries); the global value applies elsewhere. Environment override: `RUN_BLOCK_UNCLOSED_SAME_DIR` (applies everywhere). |
| `run.block_unclosed_same_dir_dirs` | list | [] | Per-directory values for `run.block_unclosed_same_dir`. |
| `run.nested_dirs` | refused, allowed | refused | `refused`: a recording is refused while an active recording covers a parent or child of its directory (their snapshots would overlap). `allowed`: only the exact same directory is locked; parent and child directories may record in parallel. Scope: the whole system. Environment override: `RUN_NESTED_DIRS`. |
| `list.since_timezone` | local, utc | local | The time zone that defines the calendar day in `--since <date>` filters (`list` and `export --all`). `local`: sessions from 00:00 local time on that date. `utc`: from 00:00 UTC. Scope: the whole system. Environment override: `LIST_SINCE_TIMEZONE`. |
| `lock.timeout_s` | integer | 5 | How long `ack`, `summary` and `close` wait for a busy session before failing with "session busy". |
| `notes.max_chars` | integer | 500 | The maximum length of an ack note. |

## Data, integrity and backup

- Each session is a directory at `$RECEIPTS_HOME/sessions/<YYYYMMDD-HHMMSS-xxxx>/`. Its `events.jsonl` is append-only, and every event is fsynced as soon as it is written. If the agent or Receipts crashes, everything captured up to that point is kept, and the session shows as `interrupted`.
- Each event stores `sha256(previous hash + its own content)`. Summaries, replaced summaries, reconciliations, acks, unacks and closes are all new events, so nothing is edited in place. Reports are always recomputed from the events.
- Locks:
  - a per-directory lock allows only one recording per workspace;
  - a per-session lock serialises writes from parallel wrappers and from `ack`, `summary` and `close`.
- **Witness.** While the agent runs, each wrapper also reports the hash of every event it writes to the `receipts run` process, which keeps them in the receipts run process's memory, so a later rewrite of `events.jsonl` is detectable. This is tamper-evidence, not protection: an agent running as the same user could still interfere with the process.. When the agent exits, Receipts writes these hashes into the log as a `witness` event. If the agent rewrites `events.jsonl` (for example, drops its failed commands and recomputes the hashes), `receipts verify` reports the session as BROKEN at the first rewritten event.
- The store is outside the workspace and has mode 0700. Deletes, forced closes and migrations go into `$RECEIPTS_HOME/audit.log`.
- **Backup:** copy `$RECEIPTS_HOME`, or export bundles. **Restore:** copy the directory back, then run `receipts verify --all`.
- **Upgrades:** the `VERSION` file holds the schema version. On a version mismatch, Receipts copies the store to `<store>.backup-v<N>-<time>`, then runs numbered migrations. Migrations never rewrite hashed event content.

## Upgrading

1. Back up the store first: copy the whole `$RECEIPTS_HOME` directory (default `~/.local/share/receipts`) somewhere safe.
2. Get the new version of Receipts (download it, or pull it into your copy), then run `./install.sh` again from that directory. It replaces the installed copy and keeps your store and config.
3. Run `receipts --version` to confirm, then `receipts verify --all`.

The first command after an upgrade migrates the store if its schema version changed, after writing a backup copy next to it (see Data, integrity and backup).

Receipts has no third-party runtime dependencies: it uses only the Python standard library (Python 3.11 or newer), so there is nothing else to pin or lock. The test suite under `tests/acceptance/` needs pytest.

## Troubleshooting

- **`receipts: command not found` after install:** `~/.local/bin` is not on your `PATH` yet. Open a new terminal, or add `export PATH="$HOME/.local/bin:$PATH"` to your shell rc file.
- **No commands recorded:** the agent ran commands without a shell, or through an absolute path such as `/bin/sh`. Those are not captured (see Known limitations). `receipts init` also says if it cannot find a real `sh` or `bash`.
- **`receipts run` refuses because another session is recording the directory:** a recording of the same directory is still running. Finish it, or wait for it to exit. If Receipts was killed, the lock is released automatically when its process is gone, so you can start again straight away.
- **"session busy":** another `ack`, `summary` or `close` holds the session lock. Wait and retry, or raise `lock.timeout_s`.
- **`receipts verify` says BROKEN:** the session log was changed after it was written, or a backup copy is incomplete. Restore the session directory from a backup and run `receipts verify --all` again.
- **The summary was not found automatically:** pass it with `receipts summary last --file <path>`, or paste it on stdin and end with Ctrl-D.

## Known limitations

- Only commands run through a shell found via `$SHELL` or `PATH` (`sh`, `bash`, `zsh`) are captured. Processes the agent starts directly, and absolute-path shells such as `/bin/sh -c`, are not.
- The store belongs to the same OS user as the agent. Receipts detects an agent that rewrites the log while it is being recorded (see the Witness note under Data, integrity and backup), but a process that keeps running after the agent exits, or anything else running as your user, can still change or delete the store afterwards. For a record the agent can never touch, run the agent as a different OS user.
- Claim extraction is rule-based. Claims phrased in unusual ways may not be recognised.
- Linux and macOS only; there is no native Windows support.
- Single local user: there is no shared store and no key signing.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

If the `tests/acceptance/` directory is present, run it too (it needs pytest, for example `python3 -m pip install pytest`):

```sh
python -m pytest tests/acceptance
```

The suite covers:

- the hash chain, including tampering, a torn tail and parallel writers;
- the test-result parsers for every supported runner;
- runner, build and skip-marker detection;
- claim extraction and file mentions;
- snapshots and diffs;
- wrapper argument parsing;
- Claude Code and Codex transcript import;
- the CLI flows against the demo data.

It also includes an **end-to-end test** (`tests/test_e2e.py`). The test records a scripted fake agent (`tests/fake_agent.py`) that runs failing tests, edits files, deletes a test file and adds a skip marker. It then checks that the expected flags appear, and walks through ack, unack, close, export and verify, including a hand-edited bundle.

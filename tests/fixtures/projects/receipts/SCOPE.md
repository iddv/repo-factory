# Scope: Receipts

Receipts is a command-line tool that a developer installs on their own machine and runs their coding-agent session through. While the agent works, Receipts independently records every shell command the agent runs, with its exit code, and every file the agent creates, changes or deletes. At the end it takes the agent's own summary, compares it with the record, and produces a report that flags two things: claims the record does not support, such as "all tests pass" when the last test run failed, and things the agent did but never mentioned, such as unmentioned file changes, deleted tests or newly skipped tests. In v1 the developer can record sessions, reconcile them, resolve the flags, keep a tamper-evident history, and export reports for code review or CI.

## Users
- **Operator (developer):** a single person at a terminal who runs agents such as Claude Code, Codex CLI, Aider or Gemini CLI on their own repositories. They need a trustworthy record and a short, clear list of discrepancies. Access is the local OS account. There are no logins or other accounts. The OS user who runs `receipts init` owns the data store, and the store is readable only by that user (directory mode 0700).
- **Reviewer (read-only, indirect):** a teammate or CI job that receives an exported report, or that runs `receipts verify` on an exported bundle. They need no installation beyond Receipts itself and no account.

## Core flows

### F1: Install and first run
- Who: Operator
- Steps:
  1. The operator follows the README and runs the single install command. The `receipts` command is now on their PATH.
  2. They run `receipts init`. The system creates the data store (see Install and run), writes a default config file, and checks that it can find a real `sh` and `bash`.
  3. The system prints the store location, the config path, "0 sessions", and the next commands: `receipts run -- <agent>` and `receipts demo`.
  4. Optionally the operator runs `receipts demo`, which loads the demo sessions and prints their IDs.
- When it goes wrong:
  - The store already exists: it is left untouched and the system prints "already initialised" with the session count.
  - The store path is not writable: the system prints the path and the OS error, and exits non-zero.
  - No real shell is found: the system says which shell is missing and that command capture will not work.
  - `receipts demo` is run on a store that already holds demo data: the system refuses and points to `receipts demo --reset`.

### F2: Record an agent session
- Who: Operator
- Steps:
  1. In a project directory, the operator runs `receipts run [--name <label>] [--test-cmd "<cmd>"] -- <agent command and args>`, for example `receipts run -- claude` or `receipts run -- codex exec "fix the bug"`.
  2. The system checks that the directory is readable and that no other open session is recording the same directory.
  3. The system snapshots the workspace: path, size and content hash of every file, using the exclusions in the Domain rules. In a git repository it also records the HEAD commit and whether the tree was dirty.
  4. The system starts the agent with the terminal fully interactive. Shell capture is in place: `SHELL`, and a PATH shim directory for `sh`, `bash` and `zsh`, point to Receipts wrappers. Each wrapper logs the command, the working directory, the start time, the duration, the exit code and an output tail, then hands off to the real shell.
  5. When the agent exits, the system takes a second snapshot and records each file as created, modified or deleted, with diffs for text files. It detects test runs and their results among the logged commands.
  6. If a test command is configured and `verify.rerun_tests` is on, the system runs it once itself and records the result as an independent run.
  7. The system prints the session ID and the counts: commands, failed commands, test runs (pass/fail/skip), and files created/modified/deleted. It then asks for the summary, as in F3.
- When it goes wrong:
  - No agent command is given: the system prints usage and exits 2.
  - The agent binary is not found: no session is created and the system prints the name it searched for.
  - Another open session is recording the same directory: the system refuses and shows that session's ID.
  - The agent crashes or is interrupted with Ctrl-C: the session is still finalised with whatever was captured and is marked `interrupted`.
  - The workspace exceeds the file-count limit: the system warns and records hashes only for files over the limit. The session is still recorded.

### F3: Attach the agent's summary
- Who: Operator
- Steps:
  1. Right after F2, or later with `receipts summary <session-id>`, the system looks for the agent's final message automatically. It checks, in order:
     - the Claude Code transcript (`~/.claude/projects/...` JSONL) for this directory and time window;
     - the Codex CLI session file (`~/.codex/sessions`);
     - Aider's `.aider.chat.history.md`;
     - the agent's final stdout block, for non-interactive runs such as `claude -p`, `codex exec` or `gemini -p`.
  2. If one is found, the system shows its first 20 lines and the operator confirms with y/n. If not, or if the operator declines, they give `--file <path>`, or paste into stdin and end with Ctrl-D.
  3. When the summary comes from a transcript, the system also reads the commands the agent reported running (its tool calls).
  4. The system stores the summary text and its source, extracts claims (see Domain rules), and runs the reconciliation.
  5. The system prints the report as in F4.
- When it goes wrong:
  - The session ID is not found: the system says so and lists the 5 most recent sessions.
  - The summary is empty: it is rejected and nothing changes.
  - The session already has a summary: the system refuses unless `--replace` is given. On replace, the old summary is kept in history and the report is regenerated.
  - The session is closed: the system refuses and says "closed sessions are read-only".

### F4: Review the reconciliation report
- Who: Operator
- Steps:
  1. The operator runs `receipts report <session-id>`, or `last`.
  2. The system shows a header with the agent, directory, duration and git HEAD before and after.
  3. Next come the flags, grouped by severity (high, medium, low). Each flag gives:
     - its kind;
     - the claim text quoted from the summary, if there is one;
     - the evidence: the command with its exit code and timestamp, or the file with a diff excerpt;
     - a flag ID.
  4. Below the flags are the "matched" claims (supported by the record), then the full side-by-side lists: claims on one side, recorded events on the other.
  5. The exit code is 0 when no flags are open, or 1 when the `report.fail_on` threshold is met. This lets CI or a script gate on it.
- When it goes wrong:
  - The session has no summary: the system shows the record only, adds one high flag ("no summary attached"), and suggests F3.
  - The session is unknown: an error is printed and the exit code is 2.

### F5: Resolve flags and close the session
- Who: Operator
- Steps:
  1. The operator runs `receipts ack <session-id> <flag-id> --note "<why>"` to accept a flag, for example "intended refactor". The system records the note and the time, and the flag shows as acknowledged.
  2. `receipts unack <session-id> <flag-id>` reopens an acknowledged flag. Both actions go into the session's event log.
  3. `receipts close <session-id>` seals the session: the hash chain is finalised and the session becomes read-only. The system prints the final hash and the counts of flags: open, acknowledged and total.
- When it goes wrong:
  - The flag ID is not in the session: an error is printed.
  - `ack` is run without a note: it is rejected, because a note is required.
  - `close` is run while high-severity flags are open: it is refused unless `--force`, and the forced close is recorded.
  - Any change is attempted on a closed session: it is refused.

### F6: Find and inspect sessions
- Who: Operator
- Steps:
  1. `receipts list` shows sessions newest first, with ID, name, agent, directory, start time, status (`open`/`interrupted`/`closed`) and open flag count. The filters are `--dir`, `--agent`, `--since <date>`, `--status` and `--flagged`.
  2. `receipts show <id> commands` shows the full command log, with `--failed` to show only failed commands.
  3. `receipts show <id> files` shows file changes, with `--diff <path>` for one file's diff.
  4. `receipts show <id> output <command-no>` shows one command's stored output tail.
  5. `receipts delete <id>` removes an open or interrupted session after the operator types its ID to confirm. Closed sessions can only be deleted with `--closed`, and the deletion is logged in the store's audit log.
- When it goes wrong:
  - No sessions match the filters: the system prints "0 sessions" and the active filters.
  - The date in a filter is invalid: an error is printed showing the expected format, `YYYY-MM-DD`.
  - The confirmation text does not match: nothing is deleted.

### F7: Export a report and verify a record
- Who: Operator, Reviewer
- Steps:
  1. `receipts export <id> --format md|json|html [--out <path>]` writes the report. The Markdown output is ready to paste into a PR. `--bundle` writes a single file containing the full record, the summary, the flags and the hash chain.
  2. `receipts export --all --since <date> --format json` writes a session-index export of the period: one row per session with its counts and flag totals.
  3. `receipts verify <id | bundle-file>` recomputes the hash chain. It prints "intact" with the final hash, or the first event whose hash does not match.
- When it goes wrong:
  - The output path already exists: the system refuses unless `--force`.
  - The bundle is malformed: the system says it is "not a Receipts bundle".
  - The chain is broken: the system prints the event number and exits 1.

## Features
### Must have (v1)
- M1: One-command install, `init`, `demo`, and a documented config file (F1)
- M2: Session runner that keeps the agent's terminal fully interactive and captures shell commands through `SHELL` and PATH shims, recording command, directory, exit code, duration and output tail (F2)
- M3: Workspace snapshots before and after, with created/modified/deleted lists and text diffs, git-aware but also working without git (F2, F6)
- M4: Test-run detection and result parsing for pytest, unittest, Jest, Vitest, Mocha, `go test`, `cargo test`, RSpec, PHPUnit, JUnit through Maven and Gradle, and `dotnet test`. Any other command matching the configured test command counts by its exit code only. (F2, F4)
- M5: Optional independent re-run of the test command at the end of a session (F2)
- M6: Summary import that auto-detects Claude Code, Codex CLI and Aider transcripts and non-interactive stdout, with file and paste fallback (F3)
- M7: Rule-based claim extraction and reconciliation into severity-ranked flags with evidence (F3, F4)
- M8: Report view with CI-friendly exit codes (F4)
- M9: Flag acknowledge and unacknowledge with notes, and session close/seal (F5)
- M10: Session list, filters, command/file/output inspection, and confirmed delete (F6)
- M11: Hash-chained append-only session log, `verify`, and exports in Markdown, JSON, HTML and bundle form (F7)
- M12: Automated test suite covering capture, diffing, parsers, claim matching and the hash chain (all)

### Later (not in v1)
- AI/LLM-based claim extraction: rule-based extraction is predictable and testable. An LLM can be added once the rule-based extractor is proven.
- Capturing commands an agent runs without a shell (direct process spawns), using ptrace or eBPF: this is platform-specific and heavy. The v1 report states this limit.
- A local web viewer: the CLI and HTML export cover review.
- Posting to GitHub PRs or CI integrations: the Markdown export and exit codes are enough for v1.
- Native Windows support: v1 targets Linux and macOS.
- Team/shared stores and signing with keys: v1 has a single local operator.
- Automatic retention and pruning: manual delete is enough for v1.

## Domain rules and defaults
- **Session ID:** `YYYYMMDD-HHMMSS-<4 random hex>`. `last` refers to the newest session.
- **Times:** stored in UTC (ISO 8601) and shown in the machine's local time zone.
- **Snapshot exclusions (default):**
  - always `.git/`;
  - `node_modules/`, `.venv/`, `venv/`, `__pycache__/`, `target/`, `dist/`, `build/`, `.next/`;
  - paths ignored by `.gitignore`, controlled by `files.gitignored`.
  
  The list is editable in the config.
- **Size and output limits:**
  - Files over 5 MB (default) are hashed, not diffed.
  - Binary files are detected by a NUL byte in the first 8 KB and are reported as changed without a diff.
  - The workspace limit is 50,000 files (default).
  - The stored output tail is the last 200 lines or 64 KB per command, whichever is smaller (default).
- **Test run:** a command whose program or subcommand matches a supported runner (M4) or the configured `--test-cmd`.
  - A test run fails if its exit code is non-zero or its parsed failure count is above 0.
  - Skipped or xfail counts are recorded when the runner reports them.
  - The "final test state" is the last test run of the session, or the independent re-run if there is one.
- **Skip markers detected in diffs:** `@pytest.mark.skip`/`skipif`/`xfail`, `@unittest.skip`, `it.skip`/`describe.skip`/`test.skip`/`xit`/`xdescribe`, `t.Skip(`, `#[ignore]`, `@Disabled`/`@Ignore`, `[Fact(Skip=`/`[Ignore]`, `markTestSkipped`, `pending`/`skip` in RSpec.
- **Test files:** paths matching `test_*`, `*_test.*`, `*.test.*`, `*.spec.*`, `tests/`, `test/`, `__tests__/`, `spec/`.
- **Claims extracted (case-insensitive patterns, documented in the code):**
  - tests pass, all tests pass, or `N tests passed`;
  - tests were run or added;
  - build succeeds;
  - a file created, modified or updated, or deleted or removed;
  - a command run (text in backticks that looks like a command).
  
  Any file path in the summary counts as a mention of that file. A file counts as mentioned if its relative path appears, or its basename appears and is unique among the changed files.
- **Flags and severity:**
  - **High:**
    - tests claimed passing while the final test state failed;
    - tests claimed passing with no test run recorded (see `report.unverified_tests`);
    - a test file deleted and not mentioned;
    - a skip marker added and not mentioned;
    - a pass count claimed that differs from the parsed count;
    - no summary attached.
  - **Medium:**
    - a file claimed changed or created that is unchanged or missing;
    - a file claimed deleted that still exists;
    - a file changed that is not mentioned;
    - a command claimed run, or listed in the transcript's tool calls, that is not in the record;
    - a build claimed successful whose last build command failed.
  - **Low:**
    - a non-zero exit that is not mentioned, when a later run of the same command succeeded;
    - commands recorded that are absent from the transcript's tool calls.
- **Session states:** `open` → `closed`, or `interrupted` → `closed`. Only `open` and `interrupted` sessions accept summary, ack and unack.
- **One open recording per directory** at a time. Recordings in different directories may run in parallel.

## Operator settings

| key | values | default | what it decides |
|---|---|---|---|
| `capture.output` | tail, none | tail | Whether command output tails are stored, or only metadata (for sensitive repos). |
| `files.gitignored` | exclude, include | exclude | Whether gitignored files are part of snapshots. |
| `verify.rerun_tests` | on, off | off | Whether Receipts runs the test command itself after the agent exits. |
| `report.unverified_tests` | flag, ignore | flag | Whether "tests pass" with no recorded test run raises a high flag. |
| `report.fail_on` | high, any | high | Whether `report` exits 1 on open high flags only, or on any open flag. |

## Money and data integrity
Receipts handles no money.
- **Never lost:**
  - Every captured command and file change is appended to the session's event log as soon as it happens. The log is flushed per event, so a crash or a killed agent keeps everything captured up to that point.
- **Append-only:**
  - Summaries, replaced summaries, flags, acks, unacks and closes are new events. Nothing is edited in place.
  - Reports are always recomputed from the events, so the counts always equal the sum of the logged events.
- **Tamper evidence:**
  - Each event stores a SHA-256 hash of its own content plus the previous event's hash.
  - The store is outside the workspace, so an agent editing the project does not touch it.
- **No double counting:**
  - Each wrapper invocation gets a unique sequence number. Nested shells (a script calling `bash`) are logged once each, with a parent link, and the report counts top-level commands only.
- **Concurrency:**
  - A per-directory lock prevents two recordings of one workspace.
  - Writes to one session from parallel wrappers are serialised by a per-session lock.
  - `ack`, `summary` and `close` take the same lock. If it is held, the second caller waits up to 5 s (default), then fails with "session busy".
- **Validation before save:**
  - Session IDs, flag IDs, dates and paths are validated.
  - A summary must not be empty.
  - Notes are 1–500 chars (default).
  - Config values must be in their allowed set.

## Install and run
- **Install:** one documented command must install Receipts on a fresh Linux or macOS machine and put `receipts` on the PATH, with no other services required. `receipts --version` confirms it.
- **Safe defaults:**
  - Receipts runs no network service and makes no network calls.
  - A fresh store is empty.
  - There are no accounts and no passwords. Ownership is the OS user, and the store has 0700 permissions.
- **Demo data:** `receipts demo` (explicit only) loads 6 realistic sessions across 3 sample projects. Together they include:
  - a clean run with no flags;
  - "all tests pass" claimed while pytest failed;
  - a deleted test file;
  - added `it.skip`;
  - unmentioned config changes;
  - an interrupted session;
  - one closed session with acknowledged flags.
  
  `receipts demo --reset` removes only the demo sessions.
- **Configuration:**
  - The config file is `~/.config/receipts/config.toml`, or the path in `RECEIPTS_CONFIG`.
  - The store location is set by `RECEIPTS_HOME`, defaulting to `~/.local/share/receipts`.
  - `receipts config get|set <key> [value]` reads and changes settings.
  - Every key is listed with its default in the README.
- **Data and backup:**
  - Each session is a directory of append-only files, and the store has a version file.
  - To back up, copy `RECEIPTS_HOME`, or export bundles.
  - To restore, copy the directory back, then run `receipts verify --all`.
- **Upgrades:** the store has a schema version. On a version mismatch, `receipts` runs numbered migrations after writing a backup copy, and it never rewrites the hashed event content.

## Non-functional basics
- **Access:** a single local user, enforced by file permissions. There are no roles.
- **Errors:** every error names the bad input and the expected form, and exit codes are consistent:
  - 0 for success;
  - 1 for flags present or verification failed;
  - 2 for usage or input errors.
- **Low overhead:** the wrapper adds under 50 ms per command (default target), and the agent's interactive terminal (colours, raw mode, resize) behaves exactly as it does without Receipts.
- **Persistence and audit:** all data survives restarts and crashes, as described under Money and data integrity. Deletes and forced closes are written to a store-level audit log.
- **Tests:** a test suite runs with one documented command. It includes:
  - an end-to-end test that records a scripted fake agent, which runs failing tests, edits files and deletes a test;
  - a check that the expected flags appear.

## Definition of done
- [ ] F1: Install and first run — after the install command and `receipts init`, `receipts list` shows "0 sessions", and `receipts demo` then lists 6 sessions.
- [ ] F2: Record an agent session — `receipts run -- <agent>` leaves the agent fully interactive, and on exit prints a session ID with correct counts of commands, test results and file changes.
- [ ] F3: Attach the agent's summary — a Claude Code or Codex summary is picked up automatically (or pasted), and claims are extracted and stored.
- [ ] F4: Review the reconciliation report — `receipts report` shows high flags for false "tests pass" claims, deleted tests and added skips, each with evidence, and exits 1.
- [ ] F5: Resolve flags and close the session — ack with a note shows the flag acknowledged, unack reopens it, and close prints a final hash after which changes are refused.
- [ ] F6: Find and inspect sessions — the list filters work, `show` displays commands, diffs and output tails, and delete requires typed confirmation.
- [ ] F7: Export a report and verify a record — Markdown, JSON, HTML and bundle exports are written, `verify` reports "intact", and a hand-edited bundle reports the broken event and exits 1.

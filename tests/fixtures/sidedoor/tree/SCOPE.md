# Scope: Sidedoor

Sidedoor is a command-line tool that a developer, platform engineer or security engineer runs on their own machine (or in CI) to test whether a coding agent fetches secrets from the intended broker or quietly takes them from somewhere else. For each trial it builds a throwaway sandbox, plants unique decoy credentials in the usual hiding spots, and starts a local mock broker and a local target API. It then gives the operator's chosen agent a task that needs a credential, and records every credential that reaches the target. At the end it states plainly either "went through the broker" or "snuck around it via <door>", with evidence. v1 covers repeat runs, comparison across agents, and exportable reports.

## Users

- **Operator**: the person at the terminal who installs Sidedoor, defines agent profiles, runs trials and reads verdicts. There are no accounts and no login. The operator is the OS user who ran `sidedoor init`, which is the first-run owner. The data directory is created readable only by that user (mode 0700).
- **CI pipeline**: a non-interactive run of the same CLI by the same OS user. It needs machine-readable output and meaningful exit codes. It does not need prompts.
- **The agent under test** is not a user. It is a subprocess that Sidedoor launches and observes.

## Core flows

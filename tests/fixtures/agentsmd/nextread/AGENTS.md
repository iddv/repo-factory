# AGENTS.md

## What this is

Nextread: a command-line reading-list manager. The list is one plain-text file the user also edits by hand; Nextread is designed to avoid losing or silently rewriting data: commands change only the lines they need to, and `rm` deletes an item only after confirmation. Python 3.9+, standard library only, no network access, no runtime dependencies. Product scope: `SCOPE.md`.

## Set up, run, test

```
pipx install .                          # or: python3 -m pip install --user .   (installs `nextread`)
python3 -m nextread --help              # run from the checkout without installing
python3 -m unittest discover -s tests   # the whole test suite (~15 s)
python3 -m unittest tests.test_format   # one module
```

Tests run the real CLI in subprocesses against a temp HOME with `NEXTREAD_FILE`, `NEXTREAD_CONFIG` and `NEXTREAD_TODAY=2026-10-11` set (see `tests/helpers.py`). Never point tests at the real home directory.

## Architecture (`nextread/`)

- `cli.py` - argparse parser (`build_parser`), one `cmd_*` function per command, output formatting, `main()` mapping `NextreadError` to exit codes. Parser errors are raised as `UsageError` (exit 1).
- `model.py` - the file format. `Document` parses text into `Line`s (kind item/comment/blank/bad) keeping raw text and per-line EOL; `Item` holds status, title and ordered `[key, value]` pairs; only items marked `dirty` are re-serialised, everything else is emitted verbatim. Also field validation (`check_value`, `item_problems`), normalisation for duplicates, header text.
- `store.py` - I/O. `write_atomic` (temp file + fsync + rename), `Lock` (O_EXCL lock file, stale-pid detection), `Library` (list + archive documents as one collection), `Transaction` (lock, read, mutate, conflict re-read, history snapshot, atomic write), `undo`, history files in `<list>.history/`.
- `config.py` - setting definitions (`SETTINGS` is the single source for defaults, validation, the commented config file and the README table), path resolution (flag > env > config > default), `config set`.
- `rank.py` - `next` scoring and reason text. Pure functions of items, config and `today`.
- `reports.py` - `stats` and the csv/json/md exporters.
- `demo.py` - the demo list, dates relative to today.
- `errors.py` - exception types carrying exit codes (1 usage/validation, 2 conflict/busy, 3 I/O).

## Conventions

- Every write goes through `store.transaction(cfg, command)`; commands validate everything first and raise before the transaction commits, so a failed command writes nothing. Do not write list files any other way (init uses `write_atomic` under the lock).
- Mutate items through `Item.set/remove/set_status/set_title` so `dirty` is tracked. Do not rebuild lines you did not change.
- Errors: raise `UsageError` (etc.) with a one-line message that names the field and the allowed values. No tracebacks for expected failures.
- Warnings go to stderr, results to stdout. No network imports (a test checks).
- New setting: add it to `SETTINGS` in `config.py` and to the README table; add a test.
- Keep Python 3.9 compatible (no `match`, no `X | Y` types). Standard library only.
- Each flow in SCOPE.md's definition of done has an end-to-end test in `tests/test_flows.py`; keep it that way when changing behaviour. Update `CHANGELOG.md` for user-visible changes.
- `NEXTREAD_TODAY` is a test aid that fixes "today"; use `cli.today()` rather than `date.today()`.

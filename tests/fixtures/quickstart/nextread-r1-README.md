# Nextread
A command-line reading-list manager that keeps your books, papers and articles in one plain-text file and tells you what to read next.
For people who read a lot and want a list they can edit by hand and script, with no account and no network.

## Quick start

```sh
pipx install .          # from a clone of this repository; puts `nextread` on your PATH
nextread                # first run: creates your list and shows where it is
nextread add "Dune" --author "Frank Herbert" --len 612p --prio 1
nextread import tabs.txt        # bulk capture: one title or URL per line (or pipe text in)
nextread next --time 30m -n 3   # what should I read next, and why
nextread done dune --rating 5
nextread demo && nextread --demo list   # try it on a sample list first
```

## Install

Needs Python 3.9 or newer on Linux or macOS. Nextread has no dependencies.

- Recommended: `pipx install .` (from the repository directory).
- Without pipx: `python3 -m pip install --user .`
- Uninstall: `pipx uninstall nextread` (or `python3 -m pip uninstall nextread`). Your list is never deleted.

Windows is not supported in v1 (the file lock uses `fcntl`).

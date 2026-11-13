Nextread is a command-line reading list that lives in one plain-text file you can also edit by hand.
It is for people who read a lot and keep losing track of books, papers and articles; it adds and finishes items quickly and tells you what to read next, and why.

## Quick start

```
pipx install .          # from a checkout of this repository
nextread                # creates your list and config, shows where they are
nextread demo           # optional: load about 40 example items
nextread add "Designing Data-Intensive Applications" --by "Martin Kleppmann" --type book --prio 1 --len 600p
nextread next --time 30m
nextread start k3f9     # use the id that `add` or `next` printed
nextread done k3f9 --rating 4
```

## Install

Requires Python 3.9 or newer and nothing else (no third-party packages, no network).

```
pipx install .
```

Run it from the repository folder. This puts `nextread` on your PATH (run `pipx ensurepath` once if your shell does not find it).
Without pipx: `python3 -m pip install --user .`.

**Uninstall:** `pipx uninstall nextread` (or `python3 -m pip uninstall nextread`).
Uninstalling does **not** delete your data: the list, archive, history and config files stay where they are.

## Using it

Items are referred to by their 4-character id, by their exact title, or by any part of the title that matches only one item (`nextread done kleppmann`). If several match, Nextread lists them and stops.

| command | what it does |
|---|---|
| `nextread` / `nextread init` | create the list and config if missing; show paths and item count |
| `nextread demo [--file P]` | load ~40 demo items into an empty or new list |
| `add "<title>" [--by --type --tags --prio --len --url --note]` | add an item. A lone URL as the title becomes the url, with type article. |
| `list [--status --type --tag --prio --search --sort]` | list items (default: to-read and reading, by priority then age) |
| `show <ref>` | every field of one item, including unknown ones, and its line number |
| `edit <ref> [flags] [--title T] [--rating N] [--clear FIELD]` | change one item; prints the line before and after |
| `start / done / drop / reopen <ref>` | change status (`done` takes `--rating 1-5 --note`, `drop` takes `--note`) |
| `rm <ref> [--yes]` | delete an item after a y/N question |
| `undo [--force]` | undo the last change; repeat to go further back |
| `next [--time 30m] [--tag] [--type] [--count N] [--why]` | what to read next, with a reason |
| `check [--fix]` | validate the file; `--fix` makes only safe repairs |
| `open` | edit the list in `$EDITOR` (default `vi`), then check it |
| `stats [--month YYYY-MM \| --year YYYY]` | monthly or yearly summary |
| `export --format csv\|json\|md [--status --from --to --out --force]` | export items |
| `config` / `config set <key> <value>` | show or change settings |
| `path` | print every location Nextread uses |

Every command has `--help` with examples. Every command accepts `--file <path>` to use another list.

Exit codes: 0 success; 1 usage or validation error; 2 conflict or busy file; 3 I/O error. Errors are one line on stderr.

### The file format

```
# comments and blank lines are kept
[ ] Designing Data-Intensive Applications | by: Martin Kleppmann | type: book | tags: systems, db | prio: 1 | len: 600p | url: https://... | added: 2026-10-01 | id: k3f9
```

- Status marker first: `[ ]` to-read, `[>]` reading, `[x]` done, `[-]` dropped; then the title.
- Fields follow, separated by ` | `, each `key: value`. A literal `|` in a value is written `\|`.
- Keys (case-insensitive): `by type tags prio len url note added started done dropped rating id`. Unknown keys are kept as they are.
- `type`: book, paper, article (default), video, other. `prio`: 1 (high), 2 (default), 3. `len`: `300p` pages or `25m` minutes, 1 to 5000. `rating`: 1 to 5, done items only. Dates: `YYYY-MM-DD` in your local time zone. Tags: lowercase letters, digits and hyphens.
- Ids are 4 random lowercase letters/digits, unique in the file.
- Lines Nextread cannot understand are left exactly as they are. Commands print a one-line warning (`2 lines not understood; run nextread check`).
- Commands rewrite only the lines they change. Comments, blank lines, unknown fields, unparseable lines, line endings (LF or CRLF) and file permissions are preserved.

### What to read next

`next` scores every to-read item (and reading items if `next.in_progress = prefer`): priority 1/2/3 = 30/15/0; 1 point per full week since added (max 20); +25 if you are already reading it; with `--time`, +20 if it fits (items that do not fit are left out); +10 if its type differs from the item you finished most recently. Ties go to the oldest `added`, then line order. The same file, date and flags always give the same answer. The reason names the two largest factors, plus the time fit. A page counts as 2 minutes; items without `len` are estimated by type (article 15m, paper 45m, video 30m, book 600m, other 30m). If nothing fits `--time`, the shortest items are shown instead. `--why` prints the score breakdown.

## Configuration

Settings live in the config file, which `nextread` creates with every option written out and commented. Edit it, or use `nextread config set <key> <value>`; `nextread config` shows effective values and where each came from.

| | Linux | macOS | Windows |
|---|---|---|---|
| list (default) | `~/.local/share/nextread/reading.txt` (`$XDG_DATA_HOME`) | `~/Library/Application Support/nextread/reading.txt` | `%APPDATA%\nextread\reading.txt` |
| config | `~/.config/nextread/config` (`$XDG_CONFIG_HOME`) | `~/Library/Application Support/nextread/config` | `%APPDATA%\nextread\config` |

(Windows and macOS paths are untested; see Known limitations.)

List path, highest priority first: `--file`, the `NEXTREAD_FILE` environment variable, `file` in the config. Config path: `NEXTREAD_CONFIG`, else the default above. Set `NEXTREAD_TODAY=YYYY-MM-DD` to pretend it is another day (used by the tests).

| key | values | default | meaning |
|---|---|---|---|
| `file` | path | platform default | the list file |
| `add.duplicates` | reject, warn | reject | refuse a duplicate (same URL, or same title ignoring case and punctuation), or save it with a warning |
| `file.done_items` | keep, archive | keep | keep done/dropped items in the list, or move them to `<name>.archive.txt` next to it. `list`, `stats` and `export` read both files; `reopen` moves an item back. |
| `next.in_progress` | prefer, ignore | prefer | favour items you are reading, or leave them out of `next` |
| `write.order` | preserve, sort | preserve | append new items and keep your order, or sort item lines by status, priority, added (comments and unparseable lines move to the top) on each write |
| `reopen.clear_rating` | true, false | true | `true`: `reopen` removes the rating along with the done date; `false`: the rating stays on the reopened item. One value for the whole system. Environment override: `REOPEN_CLEAR_RATING`. |
| `reopen.keep_started` | true, false | true | `true`: an item that goes back to to-read (from done, dropped or reading) keeps its `started` date; `false`: the `started` date is removed. One value for the whole system. Environment override: `REOPEN_KEEP_STARTED`. |
| `next.variety_without_history` | none, all | none | when nothing has been finished yet: `none`: no item gets the variety bonus; `all`: every item gets it (reason says "a fresh start"). One value for the whole system. Environment override: `NEXT_VARIETY_WITHOUT_HISTORY`. |
| `add.duplicates_check_archive` | true, false | true | `true`: `add` looks for duplicates in the list and the archive file; `false`: only in the list file (items moved to the archive can be added again). One value for the whole system. Environment override: `ADD_DUPLICATES_CHECK_ARCHIVE`. |
| `limits.title` | 1-10000 | 300 | longest title |
| `limits.note` | 1-10000 | 500 | longest note |
| `limits.tags` | 1-100 | 10 | most tags per item |
| `len.page_minutes` | 1-60 | 2 | minutes per page |
| `len.default.article` `.paper` `.video` `.book` `.other` | minutes | 15 / 45 / 30 / 600 / 30 | estimate when `len` is missing |
| `next.weight.prio1` `.prio2` `.prio3` | 0-1000 | 30 / 15 / 0 | priority score |
| `next.weight.week` | 0-1000 | 1 | points per full week waiting |
| `next.weight.week_cap` | 0-1000 | 20 | most points from waiting |
| `next.weight.in_progress` | 0-1000 | 25 | bonus for items being read |
| `next.weight.time_fit` | 0-1000 | 20 | bonus for fitting `--time` |
| `next.weight.variety` | 0-1000 | 10 | bonus for a different type than the last finished item |
| `history.size` | 1-100000 | 50 | undo snapshots kept |
| `lock.timeout` | seconds | 5 | how long to wait for another Nextread before "list is busy" |

## Upgrading

Install the new version the same way (`pipx install --force .`). The first line of the list is a comment containing `format: 1`. Newer versions read older formats and update that header on the next write. A list with a newer format than your installed version understands is opened read-only with a warning, so nothing is damaged.

## Backup and restore

Everything is in plain files: the list, the optional archive (`reading.archive.txt`), and the undo history directory (`reading.txt.history/`). `nextread path` prints all locations.
Back up by copying the list file (and the archive file). Restore by copying them back. `nextread undo` also restores earlier states.

## Troubleshooting

- **`nextread: command not found`** - run `pipx ensurepath` and open a new terminal.
- **`N lines not understood; run nextread check`** - `nextread check` shows each line with its number and the reason. Those lines are never changed by other commands.
- **`list is busy`** - another Nextread is running. If none is, delete the `reading.txt.lock` file shown by `nextread path` (locks left by crashed processes are normally cleared on their own).
- **`file changed while working; nothing written, run again`** - something (usually an editor) saved the list while the command ran. Run it again.
- **`undo` says `file edited since; use --force`** - you edited the file by hand after Nextread's last change. `undo --force` restores anyway and first saves your current file under `reading.txt.history/saved/`.
- **`does not look like a Nextread list`** - the configured file is mostly text Nextread cannot read, so it is never touched. Point `--file` somewhere else.
- **Duplicate refused but you want it** - `nextread config set add.duplicates warn`.

## Known limitations

- No title/metadata fetching from URLs, DOIs or ISBNs; no network access at all.
- One list per file (use `--file` for several); no sync (put the file in a synced folder if you like); no reading progress or reminders.
- The lock is a lock file, not an operating-system lock; see `SECURITY.md`.
- The history keeps copies of old file contents, unencrypted.
- Tested on Linux. macOS and Windows use the paths above but have not been exercised by hand.
- A value cannot end in a backslash, and a title or value cannot contain a line break.

## Running the tests

```
python3 -m unittest discover -s tests
```

No dependencies beyond Python. Add `-v` for names. See `AGENTS.md` for maintainers.

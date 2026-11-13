# NoteHatch

NoteHatch is an independent implementation, not affiliated with or endorsed by NoteRepo's maker (Flavio Copes), and it was built against NoteRepo 1.2.0.

It is a command-line tool that reads NoteRepo's local database **read-only** and exports every day that has content to its own plain Markdown file (`YYYY-MM-DD.md`), keeping the note text as written (image links are rewritten by default), the date and the last-modified time, and saving images as ordinary files. It opens no network ports and sends nothing anywhere.

## Install

Needs Python 3.11+ (standard library only). From this folder, on macOS, Linux or Windows:

```
python -m pip install .
```

(`pipx install .` also works.) `notehatch` is then on your PATH; check with `notehatch --version`.

## Try it

```
notehatch demo ./demo-notes
notehatch locate --source ./demo-notes
notehatch list --source ./demo-notes
notehatch export ~/NotesExport --source ./demo-notes
notehatch verify ~/NotesExport --source ./demo-notes
```

## Commands

Every command accepts `--help`. Options `--source`, `--config` and `--set KEY=VALUE` apply where they make sense.

| command | what it does |
|---|---|
| `locate` | Finds the database, checks the schema, prints path, days with content, empty rows, images, date range, notes without `updated_at`. |
| `demo DIR` | Writes a demo NoteRepo-format `DIR/notes.sqlite3` (70 content days, empty rows, NULL timestamps, 6 images, a dangling image reference, a malformed date key). Refuses if the file exists. |
| `list [--from D] [--to D] [--json]` | One line per day with content, newest first: date, title, last update (local time), line count, image count. |
| `show DATE [--json]` | Prints exactly the file an export would write (current settings). The referenced images are listed on stderr (or in the JSON). |
| `export DIR [--from D] [--to D] [--dry-run] [--force] [--rebuild-manifest]` | Writes the notes. Re-running only rewrites new/changed days. |
| `verify DIR [--from D] [--to D] [--json]` | Compares an export folder with the source (existence, bytes, front matter, image SHA-256). |
| `config` / `config set KEY VALUE` | Lists settings with their source / saves one to the config file. |

### Where the database is looked for

1. `--source` (a folder, a `notes.sqlite3` file, or a NoteRepo backup folder)
2. `NOTEHATCH_SOURCE`
3. `NOTEREPO_DATA_DIR`
4. macOS `~/Library/Application Support/NoteRepo`; Linux `$XDG_CONFIG_HOME/NoteRepo` then `~/.config/NoteRepo` (unverified); Windows `%APPDATA%\NoteRepo` (unverified)

An explicit `--source`/`NOTEHATCH_SOURCE` that does not exist is an error; it is never replaced by another database. The database is opened through SQLite in read-only mode (so the WAL is included), waits up to 5 seconds on a lock, and everything is read in one snapshot transaction. SQLite may create its `-shm` helper file next to a WAL database; NoteHatch does not modify the NoteRepo database.

### What an export contains

* `YYYY-MM-DD.md` per day with content (flat), mode 0600, folders 0700. A day has content if some line, trimmed, is not empty and not just `-` or `N.`.
* YAML front matter (`date`, quoted `title` such as `Wed, October 7th, 2026`, `updated` when known, `source: NoteRepo 1.2.0`), a blank line, then the note verbatim. Only image links are rewritten (under `extract`).
* File modification time = `updated_at`; for notes without it, 12:00 local time on the note's date.
* Images used by exported notes go to `assets/<first 16 hex of id>.<ext>` (the full id if two ids share the prefix), written once.
* `.notehatch/manifest.json` (tool version, source, per-file hashes, images) and `.notehatch/lock`.
* Rows with a malformed date key are skipped with a warning; dangling image references stay as they are with a warning. NoteHatch never deletes exported notes or images; it removes only its own lock and temporary files.
* Summary invariant: written + unchanged + conflicts + skipped-invalid = days with content in scope.

### Re-exporting

Days are classified as new, changed (source content or `updated_at` differs, or a setting changes the output), unchanged, edited-in-export (file differs from the manifest) or gone-from-source. Unchanged files are not touched (modification times stay). Edited files follow `export.edited_files`; gone days are kept and listed. A folder with no manifest that already contains a file we would write needs `--force`. A manifest from a different source needs `--force`. A corrupt manifest: use `--rebuild-manifest`. A lock whose process is gone is removed with a notice (so a killed run restarts at once).

Ctrl-C (or SIGTERM) stops cleanly: finished files stay, the manifest records exactly them, no temp file or lock is left; run the export again to finish.

### Settings

Precedence: flags (`--set key=value`) > environment (`NOTEHATCH_<KEY>`, dots as underscores, e.g. `NOTEHATCH_EXPORT_IMAGES`) > config file > defaults. The config file is TOML (`NOTEHATCH_CONFIG` or `--config` to choose it; default `~/.config/notehatch/config.toml`, `~/Library/Application Support/NoteHatch/config.toml`, or `%APPDATA%\NoteHatch\config.toml`):

```toml
[export]
images = "keep"
```

| key | values | default | meaning |
|---|---|---|---|
| `export.front_matter` | yaml, none | yaml | start each file with front matter, or body only |
| `export.images` | extract, keep | extract | save images under `assets/` and rewrite links, or leave `noterepo:image:` references |
| `export.edited_files` | keep, overwrite | keep | on re-export, keep (and report) files you edited, or replace them |
| `export.mtime` | updated, export_time | updated | file times follow the note's update time or the export moment |
| `export.empty_days` | skip, include | skip | skip rows without content, or write them with an empty body |

## Configuration

Besides the five settings above, these decide questions the scope leaves open. Each is one value for the whole system, can be set with `--set`, the config file or `NOTEHATCH_<KEY>`, and is also overridden by the plain environment variable named below, which wins over everything when set.

| key | values (default first) | env override |
|---|---|---|
| `export.unknown_folder` | `refuse_any_files`: an output folder with no manifest that holds any file is refused without `--force`; `refuse_on_overlap`: refused only when a file would be overwritten | `EXPORT_UNKNOWN_FOLDER` |
| `export.unchanged_exit` | `success`: a re-export that writes nothing exits 0; `warn`: it exits 1 | `EXPORT_UNCHANGED_EXIT` |
| `verify.scope` | `all_source_days`: every source day with content must have a matching file; `manifest_days`: only days recorded in the manifest are checked (handy after a partial export) | `VERIFY_SCOPE` |
| `export.missing_files` | `rewrite`: an exported file you deleted is written again; `conflict`: it is reported as a conflict and not recreated | `EXPORT_MISSING_FILES` |
| `lock.stale_requires_age` | `false`: a lock whose process is gone is removed at once; `true`: removed only when it is also over 1 hour old | `LOCK_STALE_REQUIRES_AGE` |
| `source.env_precedence` | `notehatch_first`: `NOTEHATCH_SOURCE` is used before `NOTEREPO_DATA_DIR`; `noterepo_first`: the other way round | `SOURCE_ENV_PRECEDENCE` |
| `export.mtime_change_rewrites` | `true`: changing `export.mtime` marks existing files as changed so their modification times are updated; `false`: the change only affects files written for other reasons | `EXPORT_MTIME_CHANGE_REWRITES` |

### Exit codes

0 success · 1 completed with warnings, conflicts, or nothing to export · 2 usage/config error (or `verify` on a non-export folder) · 3 source not found, unreadable or busy · 4 unsupported schema · 5 output error · 130 interrupted.

`verify` exits 1 when a note is missing, out of date or edited, or an image is missing/altered. Orphaned files (for example days since removed from the source) are listed but are not a failure. A re-export that finds everything unchanged exits 0.

## Running

After installing, run `notehatch` from any terminal (see "Try it" above for a first session). The usual routine is `notehatch locate`, then `notehatch export ~/NotesExport`; repeat the same `export` command whenever you want to update the backup. The tool is not a service: each command starts, does its job and exits, so there is nothing to keep running. A run that is stopped or killed can simply be started again at once.

NoteHatch listens on no network port and starts with no data of its own; demo data exists only after you run `notehatch demo DIR`. There are no accounts or passwords: your operating-system user's file permissions are the only access control.

## Upgrading

Install the new version over the old one from the new project folder:

```
python -m pip install --upgrade .
```

Your export folders and config file are kept. Later versions have not been tested against this manifest format, so run `notehatch verify DIR` after upgrading to confirm the export is intact.

## Backing up and restoring

An export is just a folder: copy it to back it up (for example `cp -r ~/NotesExport ~/NotesExport-backup`), and copy it back to restore. Then run `notehatch verify ~/NotesExport --source <your NoteRepo folder>` to confirm it matches. The manifest has a format version. NoteHatch's only other state is its config file.

## Troubleshooting

* `notehatch: command not found`: the Python scripts folder is not on your PATH; use `python -m notehatch` instead, or reinstall with `pipx install .`.
* "no NoteRepo database found" (exit 3): the message lists every path tried; pass `--source` with the folder or the `notes.sqlite3` file.
* "NoteRepo is busy" (exit 3): the database stayed locked for over 5 seconds; try again.
* "unsupported schema" (exit 4): only NoteRepo 1.2.0 was studied.
* "another NoteHatch run holds the lock" (exit 5): another run is active. If none is, delete the named `.notehatch/lock` file. A lock left by a dead process is removed automatically.
* Output folder refused (exit 5): it holds files NoteHatch did not write; choose another folder or use `--force`.
* Manifest corrupt (exit 5): rerun with `--rebuild-manifest`.
* A warning about an invalid date key or a dangling image reference: that row or link is skipped or left as it is; the rest is exported and the exit code is 1.

## Known limitations

* Only NoteRepo 1.2.0's database format is supported; the legacy `notes.json` and the `metadata` table are ignored.
* The Linux and Windows default locations are unverified; use `--source` if the database is not found.
* Output is flat Markdown only (no HTML, PDF, year/month folders, watch mode or import back into NoteRepo).
* NoteHatch never deletes exported notes or images, so days removed from NoteRepo stay in the export folder.
* Dependencies: none beyond Python 3.11+ and its standard library, so there is nothing to pin. The tests need `pytest` (`python -m pip install pytest`).

## Tests

Run from the project folder, after installing:

```
python -m unittest tests.test_notehatch
python -m pip install pytest
```

The first command runs the unit suite, which builds its own fixture databases. The last two run the end-to-end acceptance tests in `tests/acceptance` (present in the full source tree).

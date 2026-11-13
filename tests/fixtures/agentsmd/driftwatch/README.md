# Driftwatch

Self-hosted tracker for vendor changelog drift. Register the vendors you depend on, point Driftwatch at their
public changelogs, feeds and docs pages, and it keeps a searchable history of the changes it finds in the public sources you register, lets
reviewers classify them, compares vendors by churn, and exports dated, hash-verified evidence packs.

## At a glance

- **What it is:** A self-hosted tracker that follows the public changelogs and release notes of the vendors you depend on, flags changes to documented behavior, defaults and deprecations, and keeps a per-vendor history of how often things shifted under you.
- **Who it's for:** Small engineering, ops or platform teams, and serious self-hosters, who rely on a handful of hosted services (databases, identity providers, secrets managers, agent runtimes) and keep getting surprised by silent default changes and deprecations at renewal or upgrade time.
- **Quick start:**

  ```sh
  git clone https://github.com/iddv/driftwatch.git
  cd driftwatch
  ./bin/driftwatch serve
  ```

The rest of this README covers configuration, data, backup and the full guide; SECURITY.md lists the known security issues; the checks that ran before each release are published with it (see NOTICE).

Pure Python standard library: **there are no dependencies to install.**

## Prerequisites

- Python 3.11 or newer (`python3 --version`)
- Linux or macOS shell. SQLite (bundled with Python) is the only datastore.

## Install and run (one command)

```sh
./bin/driftwatch serve
```

That creates the data directory (`./data` by default) and schema, then listens on <http://127.0.0.1:8080>.
Open it: because no admin exists, you get the one-time **setup page** (admin username + password entered twice,
minimum 12 characters). You land on an empty Vendors page.

Optional sample data (8 vendors, 16 sources of every type, ~160 entries over 18 months, plus `demo-analyst` and
`demo-viewer` users whose one-time passwords are printed):

```sh
./bin/driftwatch demo load          # refuses if vendors already exist; --force-into-existing overrides
```

Demo sources point at fixture files that the running service serves to itself from `<data>/fixtures/`
(a secret header keeps them private), so every flow works offline. Edit a fixture file and click "Fetch now" to
watch a new entry appear. The demo expects the service to run on the address it was loaded with.

`./bin/driftwatch` is the command used throughout this document. (`pip install .` also provides a plain
`driftwatch` command with the same subcommands.)

## Commands

| command | what it does |
|---|---|
| `./bin/driftwatch serve` | run the web service and scheduler (default) |
| `./bin/driftwatch demo load [--force-into-existing]` | load demo data |
| `./bin/driftwatch backup <file>` | consistent `.tar.gz` of database, fixtures, exports and secret; safe while running |
| `./bin/driftwatch restore <file> [--force]` | restore into a **stopped** instance (refuses while the service runs; `--force` replaces an existing DB, keeping the old one as `.pre-restore-*`) |
| `./bin/driftwatch test` | run the unit tests |

## Tests

```sh
./bin/driftwatch test                              # unit tests (same as: python3 -m unittest discover -s tests -t .)
python -m pytest tests/acceptance                  # acceptance tests, if the directory exists (needs: pip install pytest)
python -m pytest tests/acceptance               # scenario checks, if the directory exists
```

They start a real server on a random port with a throw-away data directory and local fixtures; no network needed.
The health check for monitoring is `GET /health` (no sign-in; 200 when the database opens, 503 otherwise).

## Configuration

Environment variables (or a TOML file named by `DRIFTWATCH_CONFIG`, default `./driftwatch.toml`, using the
lowercase keys shown in parentheses; environment wins):

| variable (toml key) | default | meaning |
|---|---|---|
| `DRIFTWATCH_LISTEN` (`listen`) | `127.0.0.1:8080` | address and port |
| `DRIFTWATCH_DATA_DIR` (`data_dir`) | `./data` | everything lives here |
| `DRIFTWATCH_TIMEZONE` (`timezone`) | `UTC` | display time zone and month boundaries (IANA name) |
| `DRIFTWATCH_SESSION_SECRET` (`session_secret`) | random, saved to `<data>/secret.key` | signs CSRF tokens and fixture access |
| `DRIFTWATCH_GITHUB_TOKEN` (`github_token`) | none | use the GitHub API instead of the public releases Atom feed |
| `DRIFTWATCH_FETCH_CONCURRENCY` (`fetch_concurrency`) | 4 | concurrent fetches (max 1 per host) |
| `DRIFTWATCH_FETCH_TIMEOUT` (`fetch_timeout`) | 30 | seconds |
| `DRIFTWATCH_FETCH_MAX_BYTES` (`fetch_max_bytes`) | 5242880 | max response size |
| `DRIFTWATCH_FETCH_MAX_REDIRECTS` (`fetch_max_redirects`) | 5 | redirects followed |
| `DRIFTWATCH_HTTP_PROXY` (`http_proxy`) | none | outbound proxy (loopback is never proxied) |
| `DRIFTWATCH_FETCH_ALLOW_HOSTS` (`fetch_allow_hosts`) | none | comma-separated host names, IPs or CIDR ranges that sources may reach even though they are loopback or private (see below) |

Sources are refused loopback, link-local and private-network addresses (also after redirects) unless listed in
`DRIFTWATCH_FETCH_ALLOW_HOSTS`, except that already-saved sources may still reach loopback (see Known limitations); the Test result says
"refused". To watch an internal server on purpose, list its host or network in `DRIFTWATCH_FETCH_ALLOW_HOSTS`.

Startup exits with a clear message if the port is in use or the data directory is not writable.

Operator settings and defaults (the five `triage.suggestions`, `fetch.robots_txt`, `churn.count_basis`,
`export.include_dismissed`, `vendors.allow_purge` keys, plus default interval, failure threshold, diff noise
threshold, churn weights and multipliers, evidence size limit, password/lockout/session rules) are edited by an
admin on the **Settings** page; keyword lists are edited by analysts on the **Keywords** page. Changes are audited.

### Policy settings

Editable on the Settings page (admin), one value for the whole system. Setting the environment variable forces the
value everywhere and takes precedence over the stored one.

| key | values (default first) | environment variable | what it decides |
|---|---|---|---|
| `vendors.restore_resumes_fetching` | `true`, `false` | `VENDORS_RESTORE_RESUMES_FETCHING` | `true`: restoring an archived vendor resumes fetching of its non-paused sources. `false`: restoring pauses all its sources until resumed manually. |
| `export.range_basis` | `published`, `detected` | `EXPORT_RANGE_BASIS` | Which date places an entry inside an evidence pack's range. `published`: published date, falling back to detection date. `detected`: when Driftwatch detected it. |
| `fetch.robots_block_fails_immediately` | `true`, `false` | `FETCH_ROBOTS_BLOCK_FAILS_IMMEDIATELY` | `true`: one robots.txt block marks the source failing. `false`: blocks count toward the consecutive-failure threshold like other errors. |
| `churn.count_edited` | `true`, `false` | `CHURN_COUNT_EDITED` | `true`: edited entries (same item, changed text) count in churn like any other entry. `false`: they are shown but excluded from churn scores and counts. |
| `churn.trend_includes_current_month` | `true`, `false` | `CHURN_TREND_INCLUDES_CURRENT_MONTH` | `true`: trend compares the current (partial) month and the 2 before with the 3 months prior. `false`: compares the last 3 complete months with the 3 before them. |
| `vendors.name_case_insensitive` | `true`, `false` | `VENDORS_NAME_CASE_INSENSITIVE` | `true`: names differing only in case are duplicates and refused. `false`: they are distinct vendors. |

## Upgrading

1. Stop the service and take a backup (see below).
2. Replace the program files with the new version (for example `git pull`); your data directory is separate and
   is not touched.
3. Start it again with `./bin/driftwatch serve`. The schema is versioned: migrations run automatically at startup,
   after an automatic backup in `<data>/backups/`.

To go back, stop the service, install the older version and run `./bin/driftwatch restore <file> --force` with a
backup made before the upgrade.

## Backup and restore

```sh
./bin/driftwatch backup backup.tar.gz     # safe while the service runs; written readable by its owner only
./bin/driftwatch restore backup.tar.gz    # into a stopped instance; add --force to replace an existing database
```

The archive holds the database, fixtures, exports and the session secret (and password hashes): store it as
carefully as the data directory. Both commands use `DRIFTWATCH_DATA_DIR`, so give them the same environment as
`serve`.

## Troubleshooting

- **"Port 8080 is already in use"**: set `DRIFTWATCH_LISTEN`, for example `DRIFTWATCH_LISTEN=127.0.0.1:8081`.
- **"data directory ... is not writable"**: fix the permissions of `DRIFTWATCH_DATA_DIR` or choose another one.
- **Cannot reach it from another machine**: it listens on 127.0.0.1 only. Set `DRIFTWATCH_LISTEN=0.0.0.0:8080`
  deliberately, and put a TLS reverse proxy in front of it.
- **A source says "refused: the address is loopback, link-local or on a private network"**: add the host to
  `DRIFTWATCH_FETCH_ALLOW_HOSTS`.
- **A source is "failing"**: open the source page and read its fetch log for the exact reason.
- **Locked out**: an account is unlocked after 15 minutes; an admin can reset any password.
- **Restore says the service is running**: stop it first.
- A crash or stop needs no clean-up: start the service again with the same command.

## Known limitations

- Single process, SQLite, one team; no TLS (use a reverse proxy), no SSO, no alerts, no public API.
- JavaScript-only pages are not rendered; only the HTML the server returns is tracked.
- Fetches of already-saved sources may reach this machine's own loopback address (private networks, link-local
  and cloud-metadata addresses are refused unless listed in `DRIFTWATCH_FETCH_ALLOW_HOSTS`); the Test button refuses loopback too.
- At most 200 simultaneous connections; idle connections are closed after 15 seconds.
- Snapshots and entries are not deleted automatically; they are removed only when an admin purges a vendor (requires `vendors.allow_purge` to be on).

## Using it

1. **Vendors** → *Add vendor*, then *Add source* (RSS, Atom, JSON Feed, GitHub releases as `owner/repo`, or an HTML
   page with an optional CSS selector; or *Auto-detect*). *Test* fetches once and saves nothing; *Save* runs the
   baseline fetch. If a test fails you must tick "save anyway".
2. **Triage** (analyst/admin): classify, set impact (high needs a note), flag "affects us", dismiss, reopen, or
   apply in bulk. Every change is kept in the entry history; concurrent edits are rejected, never overwritten.
3. **Timeline**: filter and full-text search all entries (50 per page); the URL is shareable. Open an entry for
   the original text/diff, triage history and the raw stored snapshot with its SHA-256.
4. **Churn**: monthly score per vendor (3/6/12/24 months), per-category counts, trend, drill-down, CSV export.
5. **Evidence pack** (vendor page): zip with `report.html`, `entries.csv`, raw `snapshots/` and `manifest.json`
   (SHA-256 of every file; `VERIFY.txt` shows how to check). The manifest hash is written to the audit log; past
   packs are listed on the vendor page and can be re-downloaded.
6. **Users / Audit log / Settings** (admin). New users get a one-time password shown once and must change it at first
   sign-in. 5 failed sign-ins lock an account for 15 minutes. Sessions expire after 12 idle hours.

Roles are enforced on every request (viewer: read + download; analyst: + vendors, sources, fetches, triage;
admin: + users, settings, audit log, purge; keyword rules are analyst-editable on the Keywords page). There is no anonymous access.

## Behavior notes and interpretations

- Churn score = category weight × impact multiplier (unreviewed entries: suggested category at ×1; dismissed never
  count), bucketed by the month of the published date, else detection date, in the display time zone. Totals are
  computed from entries at query time.
- Feeds: first fetch imports existing items as `backfill`; HTML: first snapshot is a baseline and creates no entries
  (a changed selector also starts a new baseline). Re-fetched items with changed text under the same key create an
  `edited` entry linked to the original.
- `triage.suggestions = auto_apply` only auto-reviews entries where a keyword matched (low impact); "other" stays
  unreviewed. Keywords of 4 characters or fewer (`EOL`, `CVE`) match whole words only.
- A feed that cannot be parsed counts toward the failure threshold; the raw body is stored as a snapshot.
- CSV exports prefix cells that start with `= + - @` with a `'` to defuse spreadsheet formulas; the original text
  is untouched in the report and the snapshots.
- Fetched content is never rendered as HTML: it is shown escaped, raw snapshots download as opaque bytes, and a
  strict Content-Security-Policy is sent. Feed link targets are only linked when they are http(s).
- Evidence-pack size limit is checked against the snapshots to be included before building.
- Fetches run in the scheduler (every 15 s it starts sources whose interval has elapsed); "Fetch now" runs
  synchronously. Per-source locks live in the database and recover when older than 10 minutes.
- Pre-migration backups are written to `<data>/backups/` before any schema upgrade.

## Layout

```
bin/driftwatch        launcher
driftwatch/           application package (parsing, fetcher, ingest, scheduler, scoring, evidence, web + v_*.py views)
tests/                unit tests
```

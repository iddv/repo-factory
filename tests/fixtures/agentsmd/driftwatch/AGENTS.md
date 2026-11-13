# AGENTS.md

Driftwatch is a self-hosted tracker for vendor changelog drift. You register the vendors you depend on and point it at their public changelogs, feeds and docs pages. It keeps a searchable history of the changes it finds, lets reviewers classify them, compares vendors by churn, and exports dated, hash-verified evidence packs.

It is maintained through GitHub issues, as `.github/repo-factory.yml` describes.

## Setup, run, test

Python 3.11 or newer on Linux or macOS. It uses only the standard library, so there is nothing to install. SQLite is the only datastore.

```sh
./bin/driftwatch serve                 # creates ./data and the schema, listens on http://127.0.0.1:8080
./bin/driftwatch demo load             # optional sample data; refuses if vendors exist (--force-into-existing overrides)
./bin/driftwatch backup <file>         # consistent .tar.gz, safe while running
./bin/driftwatch restore <file>        # into a stopped instance; --force replaces an existing DB
```

On a fresh data directory the first visit shows a one-time setup page for the admin account (password of at least 12 characters). `pip install .` also provides a plain `driftwatch` command with the same subcommands.

### Tests

There are two suites. Both start a real server on a random port with a throw-away data directory and local fixtures, so no network is needed.

- **`tests/`: the unit tests.**

  ```sh
  ./bin/driftwatch test    # same as: python3 -m unittest discover -s tests -t .
  ```

- **`tests/acceptance/`: the acceptance suite.** It uses pytest, one file per behaviour.

  ```sh
  pip install pytest
  python -m pytest tests/acceptance
  ```

  Install pytest in a virtual environment if your system Python refuses.

The health check for monitoring is `GET /health` (no sign-in; 200 when the database opens, 503 otherwise).

### Configuration

Configuration comes from environment variables, or from a TOML file named by `DRIFTWATCH_CONFIG` (default `./driftwatch.toml`). The environment wins. The main variables are:

- `DRIFTWATCH_LISTEN`
- `DRIFTWATCH_DATA_DIR`
- `DRIFTWATCH_TIMEZONE`
- `DRIFTWATCH_SESSION_SECRET`
- `DRIFTWATCH_GITHUB_TOKEN`
- `DRIFTWATCH_FETCH_*`
- `DRIFTWATCH_HTTP_PROXY`
- `DRIFTWATCH_FETCH_ALLOW_HOSTS`

`README.md` has the full table with defaults.

## Architecture

| Path | Holds |
|---|---|
| `bin/driftwatch` | Launcher script for the command line. |
| `driftwatch/__main__.py` | Runs the package as `python -m driftwatch`. |
| `driftwatch/cli.py` | Subcommands: serve, demo load, backup, restore, test. |
| `driftwatch/config.py` | Configuration from environment variables and the optional TOML file. |
| `driftwatch/db.py` | SQLite storage: schema, versioned migrations, settings, audit log. |
| `driftwatch/auth.py` | Users, passwords, lockout and sessions. |
| `driftwatch/web.py` | HTTP server, dispatch, security headers, static and fixture routes. |
| `driftwatch/router.py` | Request and response objects and the route table shared by the views. |
| `driftwatch/ui.py` | HTML building blocks: layout, forms, formatting. |
| `driftwatch/util.py` | Small shared helpers: time, escaping, hashing. |
| `driftwatch/fetcher.py` | Outbound HTTP: limits, redirects, proxy, robots.txt, address restrictions. |
| `driftwatch/parsing.py` | Parsers for RSS, Atom, JSON Feed, GitHub releases and HTML pages (selectors, block diff). |
| `driftwatch/ingest.py` | Fetches a source, stores the snapshot and records changes. |
| `driftwatch/scheduler.py` | Background scheduler that picks due sources and fetches them with bounded concurrency. |
| `driftwatch/scoring.py` | Churn scoring. |
| `driftwatch/evidence.py` | Evidence pack: report, CSV, raw snapshots and hashed manifest in one zip. |
| `driftwatch/demo.py` | Demo data, served from fixture files under `<data>/fixtures/`. |
| `driftwatch/v_auth.py` | Setup, sign-in, sign-out and own-password views. |
| `driftwatch/v_vendors.py` | Vendor and source management views. |
| `driftwatch/v_triage.py` | Triage queue, entry detail and actions, and the entry filter shared with the timeline. |
| `driftwatch/v_reports.py` | Timeline, snapshots, churn report and evidence packs. |
| `driftwatch/v_admin.py` | Users, audit log and settings views. |
| `tests/` | Unit tests (`test_*.py`) and `helpers.py`. |
| `tests/acceptance/` | Acceptance suite, with `conftest.py`, `_helpers/driver.py` and its own `README.md`. |
| `.github/` | CI workflow, issue templates, labels and `repo-factory.yml`. |
| `SCOPE.md`, `SECURITY.md`, `NOTICE`, `CHANGELOG.md` | Scope, known security issues, release notice and change history. |

Data lives in `DRIFTWATCH_DATA_DIR` (default `./data`). It holds the database, fixtures, exports, backups and `secret.key`. The schema is versioned, and migrations run at startup after an automatic backup in `<data>/backups/`.

## Conventions

- Use only the Python standard library. Do not add a dependency.
- Roles are enforced on every request: viewer, analyst and admin. There is no anonymous access, and state-changing requests are CSRF-protected.
- Business-rule choices are admin settings on the Settings page, each with an environment override that wins when set (for example `EXPORT_RANGE_BASIS`). Do not hard-code one side of a rule. When you add or change a setting, update the README tables as well.
- Snapshots and entries are never deleted automatically. Only an admin purge of a vendor removes them, and only when `vendors.allow_purge` is on.
- Acceptance tests are one file per behaviour, `tests/acceptance/test_<what_it_checks>.py`, and drive the real interface through `tests/acceptance/_helpers/`.
- Update `README.md` when a command or setting changes. Update `SECURITY.md` when a security finding changes status.

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

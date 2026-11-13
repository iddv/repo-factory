# Readiness

Where Plotkeeper stands on the way to production: each item was checked by doing it (installing from the README, starting it, backing it up and restoring it, ...) or by reading the repository. Green is ready, amber needs attention before production, red is a gap.

**18 green, 2 amber, 0 red**.

| item | status | why | next step to production |
|---|---|---|---|
| Clean install from the README | green | the single documented command installed and started the service in a fresh copy | - |
| The README's quick-start commands work as written (install, run, backup) | green | 2 quick-start command(s) worked as written on a fresh copy; 1 not run (has a placeholder to fill in) | - |
| Listens on localhost unless configured | green | listens on 127.0.0.1:8080 by default; PLOTKEEPER_HOST changes it | - |
| Starts empty; demo data only on an explicit flag or command | green | a fresh install has no data; demo data is loaded only by './plotkeeper.sh demo' | - |
| First-run admin setup, no default password | green | the first Admin is created on the setup page with a one-time code printed at start; there is no default password | - |
| Configuration by environment or file, every option documented | green | settings come from environment variables or plotkeeper.conf, and every variable in __main__.py is in the README table with its default and meaning | - |
| Data location documented | green | the README says all data is in PLOTKEEPER_DATA_DIR (default ./data) and lists what is inside it | - |
| Backup and restore work end to end | green | backup, then a change, then restore brought the data back exactly | - |
| Data survives a restart | green | it started and served at once after both SIGTERM and SIGKILL, and the data was still there | - |
| Starts again at once after a stop or a crash (services) | green | `./plotkeeper.sh` on 127.0.0.1:8080 served again 0.11 s after a graceful stop and 0.11 s after a SIGKILL | - |
| Graceful shutdown | amber | SIGTERM ends it at once with no SIGTERM handler: requests in flight are cut off and nothing is logged. SQLite transactions keep the data safe. | handle SIGTERM: stop accepting, finish requests in flight, log the shutdown |
| Health endpoint (services) | amber | /health answers without a login but always says ok without checking that the database can be opened | make /health run a trivial query (and check the schema version), returning 503 when that fails |
| Readable logs, no secrets or personal data | green | timestamped access lines with method, path and status; no passwords or tokens seen | - |
| Schema changes and upgrades keep the data | green | the schema is versioned (PRAGMA user_version) with an ordered MIGRATIONS list, and a pre-migration backup is taken automatically | - |
| CI workflow installs the project and runs the tests | green | .github/workflows/test.yml installs and runs the tests | - |
| Dependencies locked or pinned | green | no third-party runtime dependencies (standard library only) | - |
| Sensible .gitignore, no build artefacts in the repository | green | .gitignore covers caches and environments | - |
| README covers install, configure, run, upgrade, backup and restore, troubleshooting and limitations | green | every section is there | - |
| The documented test commands run green, as written | green | 47 own test(s) pass; the acceptance suite is green (85 pass, 2 known open item(s) marked); the README's test commands run green as written | - |
| User-facing docs in plain words | green | no build-process jargon in README, ASSUMPTIONS, SECURITY, READINESS or the tests | - |

## Security

See `SECURITY.md`: 6 finding(s), 3 not fixed by a tested change (low 3).

## Known open items in the tests

Marked as expected failures in `tests/acceptance/` (the suite stays green):

- `test_database_backups_password_hashes_gardeners_contact_details.py`: Known security issue (low): The database and backups (password hashes, gardeners' contact details) are readable only by the service's user
- `test_huge_numeric_id_url_gets_404_not_server_error.py`: Known security issue (low): A huge numeric id in a URL gets a 404, not a server error

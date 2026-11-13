# Security

How Plotkeeper was reviewed for security before its first release, what was found, and what is still open.

## Threat model

A self-hosted web app (Python standard library only, SQLite) for one community garden. Only /health, /login and /setup (until the first Admin exists, protected by a one-time setup code) are public; everything else needs a signed session cookie, and Admin-only pages are checked on the server. It listens on 127.0.0.1 by default, but the README tells operators to set PLOTKEEPER_HOST=0.0.0.0 for use on a local network, so the HTTP parser is reachable by anyone on that network.

### Entry points

| entry point | who can reach it | authentication | notes |
|---|---|---|---|
| Raw HTTP parsing (plotkeeper/web.py Handler._go) | public | none | Content-Length is parsed and the body read before any route or auth check; no socket timeout |
| GET /health | public | none | returns {"ok": true} |
| GET/POST /setup | public | one-time setup code printed to the terminal (48 bits) | only works while no user exists |
| GET/POST /login | public | username + password, pre-session CSRF cookie | lockout after 5 failures for 15 minutes |
| POST /logout, GET/POST /account/password | staff | session cookie + CSRF token |  |
| Plots, gardeners, waiting list, offers, payments/refunds/voids, dues, CSV exports | staff | session cookie + CSRF token on POST | Coordinator and Admin |
| Direct assign, adjustments, seasons new/close, users, settings, audit log | admin | session cookie + server-side role check |  |
| CLI: python -m plotkeeper start\|demo\|backup\|restore\|test, ./plotkeeper.sh install-service | local | shell access to the machine | demo prints random passwords once; restore asks for confirmation |
| Data directory (plotkeeper.db, session_secret, backups/) | local | file permissions | session_secret is 0600; the database and backups are 0644 |
| plotkeeper.conf / environment variables | local | file permissions | may hold PLOTKEEPER_SESSION_SECRET |

### Assets

| asset | kind | where |
|---|---|---|
| staff password hashes and active sessions | secrets | data/plotkeeper.db (users, sessions tables) |
| session signing secret | secrets | data/session_secret or PLOTKEEPER_SESSION_SECRET |
| gardeners' names, emails, phones, addresses | personal data | data/plotkeeper.db and backups, CSV exports |
| fee ledger (payments, refunds, adjustments) | money / record integrity | data/plotkeeper.db |
| waiting-list order and plot assignments | record integrity | data/plotkeeper.db |
| service availability | availability | the single Python process |

## What was checked, and how

The product was started as its README says and probed while running.

| area | check | result | evidence |
|---|---|---|---|
| AuthN/authZ | anonymous GET of every staff page (/, /plots, /plots/1, /gardeners, /waitlist, /dues, /seasons, /settings, /users, /audit, /export/dues.csv) | pass | all answer 303 to /login |
| AuthN/authZ | Coordinator GET of Admin pages /settings, /users, /audit | pass | 403 'Only Admins can do this.' |
| AuthN/authZ | Coordinator POST of adjustment (/gardeners/1/money kind=adjustment), direct assign, settings, create user, open season | pass | all 403, nothing changed |
| AuthN/authZ | every route in ROUTES has a role except /setup and /login | pass | route table reviewed; /setup refuses once a user exists |
| hostile HTTP | negative Content-Length (-1) on POST /login, then stream 100 MiB | fail | rfile.read(-1) reads until EOF; RSS 25 MiB -> 128 MiB, unauthenticated |
| hostile HTTP | huge Content-Length (99999999999) | pass | 413 at once, body not read |
| hostile HTTP | non-numeric Content-Length ('abc') | fail | ValueError in handler, connection closed with no response, traceback in server log |
| hostile HTTP | half-sent request headers left open (slowloris) | fail | no socket timeout; 50 idle connections held 50 threads; a single connection stayed open >20 s |
| hostile HTTP | body over the documented limit (>1,000,000 bytes) | pass | 413 |
| hostile HTTP | huge numeric id in URL /plots/9999...(30 digits) | fail | 500 'Something went wrong' (generic page, no traceback shown; SQLite OverflowError in log) |
| hostile HTTP | malformed / deeply nested JSON | n/a | no route accepts JSON; bodies are parsed as urlencoded forms |
| hostile HTTP | huge, negative, NaN, Infinity amounts | pass | amounts are parsed by a strict 2-decimal pattern in core.py; reviewed, field error |
| injection | SQL: ' OR 1=1 -- in gardener search | pass | 200, no extra rows; all queries use ? parameters |
| injection | stored XSS: <script>, "'><img onerror> in gardener name/address/notes and plot code/notes, viewed on lists, detail pages and dashboard | pass | rendered escaped everywhere |
| injection | reflected XSS in ?q= search and ?kind= on the money form | pass | escaped; kind is checked against an allow-list |
| injection | CSV formula injection in exports | pass | cells starting with = + - @ are prefixed with ' |
| injection | CSRF: POST without _csrf token, and with foreign Origin | pass | 403 'CSRF check failed', nothing saved (regression probe test_sec_ok_1.py) |
| injection | path traversal | n/a | no file name or path parameter over HTTP; backup/restore paths are local CLI arguments |
| injection | open redirect | pass | no next=/return_to=; error redirects use only the Referer path and need a valid CSRF token |
| authentication | 5 wrong passwords then the right one | pass | 'Too many failed attempts. Try again later.'; correct password refused while locked |
| authentication | session cookie flags | pass | pk_session: HttpOnly; SameSite=Lax; no Secure (served over plain HTTP, TLS left to a proxy) |
| authentication | session ends on logout | pass | after POST /logout, /plots redirects to /login (row deleted server-side) |
| authentication | session ends on password change | fail | a second session of the same user kept working (200) after the password change |
| authentication | password storage | pass | scrypt n=2^14 r=8 p=1 with 16-byte random salt, constant-time compare |
| authentication | session expiry after inactivity, deactivation and admin reset | pass | load_session drops sessions older than auth.session_hours; deactivate/reset delete sessions |
| secrets and defaults | default credentials / hard-coded secrets | pass | grep found none; setup code and demo passwords are random; session secret generated at first run with mode 0600 |
| secrets and defaults | secrets in logs | pass | server log has request lines only; setup code is printed to the terminal by design |
| secrets and defaults | file permissions of database and backups | fail | plotkeeper.db and backups/*.db are -rw-r--r--; data dir is drwxrwxr-x |
| error pages | bad ids, bad season, bad date range | pass | friendly pages/field errors, no traceback, path or SQL in responses |
| dependencies | third-party dependencies | n/a | standard library only (README; no requirements file) |

## Findings

| | severity | finding | status |
|---|---|---|---|
| S1 | high | A negative Content-Length makes the server read the request body with no size limit | fixed (a test checks it) |
| S2 | medium | No socket timeout: idle or half-sent connections hold a server thread forever | fixed (a test checks it) |
| S3 | medium | Changing a password does not sign out the account's other sessions | fixed (a test checks it) |
| S4 | low | Database and backup files are readable by every local user | open |
| S5 | low | A huge numeric id in a URL causes a 500 error | open |
| S6 | low | A non-numeric Content-Length crashes the request handler instead of returning 400 | its test passes after other fixes; confirm by hand |

### S4 (low): Database and backup files are readable by every local user

- Where: plotkeeper/store.py (sqlite file creation), plotkeeper/__main__.py backup
- What happens: plotkeeper.db and backups/plotkeeper-backup-*.db are -rw-r--r--; they contain password hashes, session tokens and gardeners' contact details
- What should happen: files created 0600 and the data directory 0700 (session_secret already is 0600)
- Reproduce: `python3 -m plotkeeper demo && python3 -m plotkeeper backup && ls -l data data/backups`
- Test: `tests/acceptance/test_database_backups_password_hashes_gardeners_contact_details.py` (marked as a known open item)

### S5 (low): A huge numeric id in a URL causes a 500 error

- Where: plotkeeper/web.py:379 (int(req.params[0]) passed to SQLite)
- What happens: 500 'Something went wrong' (no traceback in the page; OverflowError traceback in the server log)
- What should happen: 404 Not found
- Reproduce: `curl -b <session> http://127.0.0.1:8080/plots/999999999999999999999999999999`
- Test: `tests/acceptance/test_huge_numeric_id_url_gets_404_not_server_error.py` (marked as a known open item)

### S6 (low): A non-numeric Content-Length crashes the request handler instead of returning 400

- Where: plotkeeper/web.py:1208
- What happens: no response; connection dropped; ValueError traceback written to the server log on every such request (log noise)
- What should happen: HTTP 400
- Reproduce: `printf 'POST /login HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\n\r\n' \| nc 127.0.0.1 8080`
- Test: `tests/acceptance/test_non_numeric_content_length_gets_400_response_instead_dropped.py` passes since other fixes went in, but this finding wasn't repaired on its own: a changed default can also void the test's premise. Reproduce it by hand before closing it.

## Dependency audit

- Tool: pip-audit; result: no_dependencies.
- Plotkeeper uses only the Python standard library; there is no requirements file or third-party package to audit (pip-audit 2.10.1 was available).

## Known limits

- This was a time-boxed review of v1 by one reviewer with the code and a running copy: scripted probes and manual checks, not a penetration test or an external audit.
- TLS is left to a reverse proxy and was not tested; the session cookie has no Secure flag, which matters only if served over HTTPS without the proxy setting it.
- Brute force of the one-time setup code was not timed; the code has 48 random bits and only matters before the first Admin exists.
- Login lockout is per username; there is no per-IP rate limit, so an attacker can lock out known staff accounts for 15 minutes (by design per the spec).
- plotkeeper.sh install-service (systemd, sudo) was not run.
- Restore from a crafted backup file was not tested (local-only, requires shell access).
- Concurrency of offers and payments was not load-tested as a security issue.

## Reporting a security issue

Please report security problems privately, not in a public issue: use the repository host's private vulnerability reporting (on GitHub: Security, then Report a vulnerability), or write to the maintainers directly. Include the version, the steps to reproduce and what an attacker could do. You will get an answer within a few working days.

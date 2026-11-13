# Plotkeeper

Plotkeeper keeps a community garden's plot register, waiting list and yearly fees in one place. It
replaces the spreadsheets. You install it on one machine, and your coordinators use it in a web browser.

## At a glance

- **What it is:** A self-hosted web app that lets a community garden's volunteer coordinator manage plots, members, the waiting list and yearly fees in one place instead of spreadsheets.
- **Who it's for:** Volunteer coordinators of small community gardens and allotment associations who currently track plot assignments, waiting lists and dues in shared spreadsheets.
- **Quick start:**

  ```sh
  git clone https://github.com/iddv/plotkeeper.git
  cd plotkeeper
  ./plotkeeper.sh
  ```

The rest of this README covers configuration, data, backup and the full guide; SECURITY.md and READINESS.md say what was checked before this release.

It needs **only Python 3.9 or newer**. There are no other dependencies, no database server and nothing to compile.

## Install and start (one command)

```sh
git clone <this repository> plotkeeper && cd plotkeeper && ./plotkeeper.sh
```

(If you got Plotkeeper as an archive, unpack it, `cd` into the folder and run `./plotkeeper.sh`.)

The launcher installs Python if it is missing (using apt, dnf, apk or Homebrew). It then creates the
data store, applies the schema and starts the service on **http://127.0.0.1:8080/**. On first run, it
prints a **one-time setup code**. The terminal shows the address and a line such as:

> First run: open http://127.0.0.1:8080/setup and enter this one-time setup code: 4F2A-91C3-0B7E

Open the URL and fill in the setup page:

- the setup code;
- the garden name;
- a currency code (e.g. EUR or GBP);
- a time zone (e.g. Europe/London);
- an Admin username and password (at least 12 characters, entered twice).

Setup creates the Admin, signs you in and invalidates the code. If you restart before finishing setup,
a new code is printed and the old one stops working.

No default password and no built-in secret are shipped. A session secret is generated in the data
directory at first run, unless you configure one.

### Run as a background service (restarts on reboot and after a crash or stop)

On Linux with systemd:

```sh
./plotkeeper.sh install-service
sudo journalctl -u plotkeeper | grep "setup code"    # first run only
```

This writes `/etc/systemd/system/plotkeeper.service`, which runs as your user from this folder. It
reads `plotkeeper.conf` if that file exists. Manage it with
`sudo systemctl status|stop|restart plotkeeper`.

### Reaching it from other computers

By default Plotkeeper listens only on the local machine. To let coordinators use it from other
computers, set `PLOTKEEPER_HOST=0.0.0.0`. Put it behind a reverse proxy with HTTPS (Caddy, nginx)
if it is reachable beyond a trusted local network.

## Demo data

To try it out, load demo data into an **empty** install (before running setup):

```sh
./plotkeeper.sh demo
```

The demo contains:

- 40 plots in every size and status;
- 45 gardeners;
- 12 waiting-list entries;
- one open offer and one expired offer;
- a closed previous season, with some fees still owed;
- a current season with fees charged and payments that cover paid, partly paid, unpaid and in-credit
  cases (the previous season shows overdue);
- one void, one refund and one adjustment.

The `admin` and `coordinator` accounts get random passwords, which are printed once. On an install
that already has data, the command refuses with "Demo data can only be loaded into an empty install".

## Commands

| Command | What it does |
|---|---|
| `./plotkeeper.sh` or `./plotkeeper.sh start` | Start the web service |
| `./plotkeeper.sh demo` | Load demo data (empty install only) |
| `./plotkeeper.sh backup [-o FILE]` | Write a timestamped single-file backup to `DATA_DIR/backups/` |
| `./plotkeeper.sh restore FILE [--yes]` | Replace all data with a backup, after you confirm. Stop the service first. The current data is backed up before it is replaced. |
| `./plotkeeper.sh test` | Run the automated test suite |
| `./plotkeeper.sh install-service` | Install and start the systemd service |

`python3 -m plotkeeper <command>` does the same as `./plotkeeper.sh <command>`.

## Configuration

Settings come from environment variables or a `plotkeeper.conf` file in the folder you start from
(`KEY=value` lines; see `plotkeeper.conf.example`). Set `PLOTKEEPER_CONFIG` to use another file.
Environment variables take precedence over the file.

| Variable | Default | Meaning |
|---|---|---|
| `PLOTKEEPER_HOST` | `127.0.0.1` | Listen address |
| `PLOTKEEPER_PORT` | `8080` | Listen port. If the port is already in use, start exits with a message naming the port and this variable. |
| `PLOTKEEPER_DATA_DIR` | `./data` | Folder holding all data |
| `PLOTKEEPER_SESSION_SECRET` | generated into `DATA_DIR/session_secret` | Key used to sign session cookies |
| `PLOTKEEPER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `PLOTKEEPER_TIMEZONE` | the garden time zone chosen at setup | Overrides the time zone used to show dates |

Admins can change the garden's rules on the in-app **Settings** page. Every change is written to the audit log.

| Setting | Values | Default |
|---|---|---|
| `waitlist.decline_policy` | keep_place, move_to_end | keep_place |
| `fees.new_holder_charge` | full, prorated | full |
| `payments.overpayment` | reject, credit | reject |
| `plots.release_with_balance` | allow, block | allow |
| Default fees for small / standard / large plots | amounts | 40.00 / 60.00 / 80.00 |
| Offer expiry | days | 14 |
| Default due date | MM-DD | 03-31 |
| Declines before an entry is removed | number | 2 |
| Session inactivity timeout | hours | 8 |
| Failed sign-ins before lockout / lockout length | number / minutes | 5 / 15 |

#### Operator settings with environment overrides

Each of these settings has one value for the whole system. Admins set it on the Settings page. When its
environment variable is set to a valid value, that value wins everywhere, and the stored setting is ignored.

| Setting | Values (**default**) | What each value does | Environment variable |
|---|---|---|---|
| `fees.prorate_from` | recorded_date, **start_date** | `start_date`: the remaining months for a prorated charge count from the month of the assignment start date. `recorded_date`: they count from the month the acceptance (or direct assignment) is recorded. Only used when `fees.new_holder_charge` = prorated. | `FEES_PRORATE_FROM` |
| `seasons.current_rule` | most_recently_opened, **latest_year** | `latest_year`: the season with the highest year is the current season (opening 2025 after 2026 leaves 2026 current). `most_recently_opened`: whichever season was opened last becomes current. The current season is the default on the Dues page and payment forms, and it receives automatic charges for new holders. | `SEASONS_CURRENT_RULE` |
| `assign.direct_with_active_entry` | **close_entry**, refused | `close_entry`: an Admin may direct-assign a plot to a gardener who is on the waiting list, and their active entry closes as "placed". `refused`: direct assign is refused while the gardener has an active waiting-list entry; remove the entry first. | `ASSIGN_DIRECT_WITH_ACTIVE_ENTRY` |
| `offers.valid_on_expiry_date` | **true**, false | `true`: an offer is still open on its expiry date and shows as Expired from the day after. `false`: an offer shows as Expired on the expiry date itself. | `OFFERS_VALID_ON_EXPIRY_DATE` |

## Data, backups and upgrades

- **All data lives in one folder**, `PLOTKEEPER_DATA_DIR` (default `./data`):
  - `plotkeeper.db` is the SQLite database;
  - `session_secret` holds the session key;
  - `backups/` holds the backups.
- **Back up regularly.** `./plotkeeper.sh backup` makes a consistent copy even while the service runs.
  For a daily backup, add a cron line:
  `0 2 * * * cd /path/to/plotkeeper && ./plotkeeper.sh backup`.
- **Upgrades.** The schema is versioned. When a newer version starts, it first writes an automatic
  `pre-migration` backup and then applies the pending migrations.
- **Nothing is deleted in the app.**
  - Assignments and waiting-list entries are closed, not deleted.
  - The money ledger is append-only, and the database refuses to update or delete ledger rows.
  - Corrections are new, linked entries (voids, refunds, adjustments).

## Who can do what

- **Admin:** everything, including:
  - users and settings;
  - opening and closing seasons;
  - adjustments;
  - direct assignment of a plot (with a reason).
- **Coordinator:**
  - plots, gardeners and the waiting list;
  - offers and releases;
  - payments, voids and refunds;
  - dues and exports.

The server checks roles on every request, not just the menus. New users get a temporary password and
must change it at their first sign-in.

## How the rules work (summary)

- **Waiting list order:** join time, with ties broken by creation order. Positions are calculated
  each time, never stored. "Offer to next in line" picks the earliest active entry whose preference
  matches the plot's size or is "any".
- **Offers** expire after 14 days. Expired offers are flagged on the dashboard, but a person must
  record the outcome:
  - Recording "expired" counts as a decline.
  - After 2 declines, the entry is removed with the reason "declined twice".
  - Withdrawing an offer restores the person's place.
- **Fees:**
  - Opening a season charges every current holder once, at the fee for their plot's size. A unique
    database index makes a second charge for the same assignment and season impossible.
  - Holders who accept later are charged when they accept, in full or prorated. Prorated means
    fee × remaining months ÷ 12, counting the acceptance month, rounded half-up to the cent.
- **Balance** = charges + adjustments − payments + voids + refunds.
- **Status:**
  - Paid: balance 0;
  - In credit: balance below 0;
  - Partly paid: balance between 0 and the amount charged;
  - Unpaid: balance equals the amount charged, on or before the due date;
  - Overdue: anything still owed after the due date.
- **Money** is stored as integer cents in one currency. Every total is calculated from the ledger
  entries.
- **Concurrency:**
  - Offers, accepts, releases and status changes check the plot's current status and version inside a
    write transaction. Of two simultaneous actions, the second gets "changed since you opened it" (or
    "This plot was just offered to …") and changes nothing.
  - Payment forms carry a one-time token, so a double submission is saved only once.

## Tests

Run the unit tests (no extra software needed):

```sh
./plotkeeper.sh test
```

If the `tests/acceptance` folder is present, also run the end-to-end tests. They need pytest
(`python -m pip install pytest`, inside a virtual environment if your system Python refuses):

```sh
python -m pytest tests/acceptance
```

The suite covers:

- waiting-list ordering and size matching;
- both decline policies;
- offer and accept concurrency (real threads on separate connections);
- idempotent and concurrent season charging;
- prorating and rounding;
- balance and status calculations;
- overpayment and refund limits;
- double-submit protection;
- role and CSRF enforcement through the HTTP layer;
- the setup code and lockout;
- demo loading and rendering every page;
- the backup and restore round-trip.

## Troubleshooting

- **"Port 8080 is already in use".** Another program (or another Plotkeeper) is using the port. Stop it,
  or set `PLOTKEEPER_PORT=8081` in `plotkeeper.conf` or the environment and start again.
- **I lost the setup code.** Stop Plotkeeper and start it again. While no Admin exists, every start
  prints a new code and the old one stops working.
- **The page does not open from another computer.** Plotkeeper listens on `127.0.0.1` by default, so only
  the machine it runs on can reach it. See "Reaching it from other computers".
- **An account is locked.** After 5 failed sign-ins the account is locked for 15 minutes. Wait, or ask an
  Admin to reset the password under **Users**.
- **Everyone was signed out.** Sessions end after 8 hours without activity, when the session secret
  changes, and (for that user's other browsers) when a user changes their password.
- **The service stopped.** Start it again with `./plotkeeper.sh`. The systemd service restarts it
  automatically after a crash. For details, check the terminal output, or
  `sudo journalctl -u plotkeeper` for the service; set `PLOTKEEPER_LOG_LEVEL=DEBUG` for more detail.
- **"Python 3.9+ not found".** The launcher tries to install Python with apt, dnf, apk or Homebrew. If
  that is not possible, install Python 3.9 or newer yourself and run the command again.
- **Restore refuses a file.** The file is not a Plotkeeper backup or is damaged. Pick another file from
  `DATA_DIR/backups/`.

## Known limitations (not in v1)

- One server serves at most 64 connections at a time, and a connection that sends nothing for 10
  seconds is closed.
- Not included in v1:
  - a gardener portal;
  - email or SMS;
  - online payments;
  - discount tiers;
  - shared plots or more than one plot per gardener;
  - an import wizard;
  - a plot map;
  - more than one garden;
  - PDF receipts.

To rebuild a waiting list from a spreadsheet, add each person with a backdated join date.

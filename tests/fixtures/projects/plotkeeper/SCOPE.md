# Scope: Plotkeeper

Plotkeeper is a self-hosted web application that a community garden's volunteer coordinators install on one machine. It replaces their spreadsheets. It keeps a register of every plot and who holds it. It runs a first-come waiting list that offers freed plots to the next person in line. It charges each holder a yearly fee and shows, at a glance and as exports, who has paid this season and who still owes. v1 serves one garden, run by a handful of coordinators.

## Users

- **Admin.** The garden's lead coordinator. They do everything a Coordinator does. They also manage staff accounts, operator settings, fee amounts and seasons. The first Admin account is created during first run (F1). Admins sign in with a username and password.
- **Coordinator.** A volunteer who maintains plots, gardeners, the waiting list, offers, payments and reports. Coordinators cannot manage accounts or settings, and cannot open a season. They sign in with a username and password created by an Admin.
- **Gardeners** (plot holders and people on the waiting list) are records, not users. They have no login in v1.

## Core flows

### F1: Install and first run
- Who: Admin (operator)
- Steps:
  1. The operator follows the README and runs the single install-and-start command on a fresh machine.
  2. The system creates an empty data store and applies the schema. It starts listening on 127.0.0.1:8080 (default) and prints the URL plus a one-time setup code to the terminal.
  3. The operator opens the URL. Because no accounts exist, they see the setup page. They enter the setup code, the garden name, a currency code, a time zone, an admin username and a password (min 12 characters), entered twice.
  4. The system validates the input, creates the Admin, invalidates the setup code and signs the operator in.
  5. The operator sees the dashboard with zero plots, an empty waiting list and no season. Each section has a hint to add the first record.
  6. Separately, the documented demo command loads the demo data (see Install and run) into an empty install.
- When it goes wrong:
  - Wrong or used setup code: "Invalid setup code", and nothing is created.
  - Password too short or the two entries differ: field error, other fields kept.
  - Port already in use: the start command exits with a message naming the port and the config variable to change it.
  - Demo command run on a non-empty install: refused with "Demo data can only be loaded into an empty install".

### F2: Manage plots
- Who: Coordinator, Admin
- Steps:
  1. The user opens Plots. They see a list with filters by status and size, a search by plot code or holder name, and counts per status.
  2. Add plot: the user enters a code (e.g. "B-07"), a size (small, standard or large), optional area in m² and optional notes. The system checks the code is unique, then saves the plot as Available.
  3. Edit plot: the user changes the size, area or notes. A code change is rechecked for uniqueness. Changes are written to the plot's history.
  4. Mark out of service, with a reason, from Available: the plot is skipped by offers. "Return to service" sets it back to Available.
  5. Retire plot, from Available or Out of service, with a reason: the plot leaves the default list. Its history stays viewable.
  6. The plot detail page shows the current holder, any open offer, the fee status this season and a dated history of holders and changes.
- When it goes wrong:
  - Duplicate code: "Plot code B-07 already exists".
  - Retire or put out of service while Occupied or Offered: blocked with "Release the holder or withdraw the offer first".
  - Record edited by someone else since it was opened: "This plot changed since you opened it", and the user reloads.

### F3: Manage gardeners and the waiting list
- Who: Coordinator, Admin
- Steps:
  1. Add gardener: the user enters a name (required), email and/or phone (at least one required), an optional address and optional notes. The system warns about a likely duplicate (same email or phone) and lets the user open the existing record instead.
  2. Search gardeners by name, email or phone, then open a gardener to see their plot, waiting-list entry, balances and history.
  3. Edit gardener details. The change is recorded in the history.
  4. Add to waiting list: the user picks a gardener and a size preference (any, small, standard or large). The join date defaults to now and can be backdated when migrating from a spreadsheet. The system assigns a position.
  5. The Waiting list page shows active entries in order, with position, name, join date, preference and days waiting.
  6. Change the preference, which keeps the position. Remove from the list, with a reason (withdrew, unreachable, other); the entry closes and later entries move up.
  7. Archive a gardener who has no plot, no active entry and a zero balance. They leave the default list but stay searchable.
- When it goes wrong:
  - No contact details: field error.
  - Gardener already holds a plot or already has an active entry: "Already holds plot X / already on the list at position N".
  - Archiving with a plot, an entry or a non-zero balance: blocked with the reason.

### F4: Offer a freed plot and assign it
- Who: Coordinator, Admin
- Steps:
  1. Release: on an Occupied plot, the user clicks "Release". They enter an end date and a reason (gave up, moved, removed by committee). The assignment closes and the plot becomes Available. Charges and payments stay on the gardener.
  2. On an Available plot, the user clicks "Offer to next in line". The system picks the first active entry, by waiting-list order, whose preference matches the plot's size or is "any". It shows the person and their contact details for confirmation.
  3. The user confirms. The plot becomes Offered with an expiry date (see Domain rules), and the entry is marked "offered". The user contacts the person outside the system.
  4. The user then records one of three outcomes:
     - **Accepted:** an assignment starts on the chosen date, the entry closes as "placed", the plot becomes Occupied, and a fee charge is created if a season is open.
     - **Declined:** the entry is handled per `waitlist.decline_policy`.
     - **Withdraw offer:** the entry is restored to its place.
  5. Offers past their expiry show as "Expired" on the dashboard. Recording "expired" is treated like a decline. After a decline or expiry, the plot returns to Available and the user can offer it to the next person.
  6. Direct assign is Admin only, with a required reason such as an accessibility need. It assigns an Available plot to any gardener without a plot, bypassing the list. This is recorded in the audit trail.
- When it goes wrong:
  - No matching entry: "No one on the waiting list matches a small plot". Direct assign stays available to Admins.
  - Plot already offered or assigned by another user: "This plot was just offered to N", and nothing changes.
  - Release with an outstanding balance when `plots.release_with_balance` = block: "Gardener owes 30.00 for 2026; record payment or adjustment first".
  - Start date before the plot's last release date: field error.

### F5: Open a season and charge fees
- Who: Admin
- Steps:
  1. In Seasons, the Admin reviews the fee per size, which is pre-filled from the last season or the defaults. They enter the year and a due date.
  2. The system shows a preview: how many current assignments will be charged, and the total by size.
  3. The Admin confirms. One fee charge per active assignment is created for that season, and the season becomes the current season.
  4. Assignments accepted later in the season are charged automatically at acceptance, per `fees.new_holder_charge`.
  5. The Admin can close a past season. Closing stops new charges for it, but payments and adjustments can still be recorded against it.
- When it goes wrong:
  - Season year already exists: "Season 2026 already exists".
  - Running it twice or concurrently never creates a second charge for the same assignment and season.
  - Fee not a positive amount: field error.
  - Due date outside the season year: field error.

### F6: Record payments, refunds and adjustments
- Who: Coordinator, Admin, except adjustments, which are Admin only
- Steps:
  1. The user finds the gardener (search, or from the dues list) and clicks "Record payment". They enter the amount, date (default today), method (cash, bank transfer, cheque, card, other), an optional reference and the season (default current).
  2. The system validates the amount and saves an immutable payment entry. The user sees the updated balance and status for that season.
  3. Void payment, for entry mistakes: requires a reason. It creates a reversing entry that is linked to the original. The original stays visible and is marked voided.
  4. Refund, for money actually returned: the user enters amount, date, method and reason. It is saved as a negative entry.
  5. Adjustment (Admin): the user enters an amount (+ to add a charge, − to waive or discount) and a required reason. It is saved as a new entry.
  6. The gardener's ledger lists every entry with date, type, amount, user and running balance.
- When it goes wrong:
  - Amount zero, negative or more than 2 decimals: field error.
  - Overpayment when `payments.overpayment` = reject: "Balance is 40.00; amount exceeds it".
  - Refund greater than net paid for the season: "Refund cannot exceed 60.00 paid".
  - Voiding an already-voided payment: "Already voided".
  - Double submission of the same form: saved once.

### F7: Dues overview, totals and exports
- Who: Coordinator, Admin
- Steps:
  1. The Dues page for the chosen season (default current) lists every charged gardener with plot, charged, paid, balance and status (Paid, Partly paid, Unpaid, Overdue, In credit). It can be filtered by status and sorted by balance or name.
  2. The headline shows the season totals (charged, paid, outstanding, count per status) and, for the selected date range, payments by method. The date range defaults to today and "this month" is one click away.
  3. The dashboard summarises the same: plots by status, waiting-list length, open or expired offers, and money owed.
  4. The user can export CSV files:
     - season dues;
     - payments ledger for a date range, with a totals row;
     - plot register (plot, size, status, holder, since);
     - waiting list in order.
- When it goes wrong:
  - Date range end before start: field error.
  - No season exists: "No season yet". An Admin sees a link to F5.
  - Empty result: a CSV with headers only.

### F8: Staff accounts and settings
- Who: Admin
- Steps:
  1. Under Users, the Admin adds a user (username and role) and the system shows a one-time temporary password. The new user must change it at first sign-in.
  2. The Admin can change a user's role, reset a password (new temporary password) or deactivate a user. Deactivated users can no longer sign in and their history keeps their name.
  3. Any user can change their own password.
  4. Under Settings, the Admin edits the garden name, fees per size, offer expiry days, the due-date default and the operator settings. Each change is audited.
  5. The Audit log page lists every recorded change, filterable by user, record and date.
- When it goes wrong:
  - Duplicate username: field error.
  - Deactivating or demoting the last active Admin: blocked.
  - Wrong current password: error.
  - Sign-in after 5 failed attempts: locked for 15 minutes (default).

## Features

### Must have (v1)
- M1: Single-command install, first-run Admin setup with a one-time code, demo-data command (F1)
- M2: Plot register with sizes, statuses, search, filters, history and optimistic edit checks (F2, F4)
- M3: Gardener records with duplicate warning, archive and per-person history (F3)
- M4: Ordered waiting list with size preference, backdated join dates, removal reasons and live positions (F3, F4)
- M5: Offer workflow: next-eligible selection, expiry, accept, decline, withdraw, release, Admin direct assign (F4)
- M6: Seasons with fees per plot size, one-click charging, automatic charges for new holders (F5)
- M7: Append-only money ledger: payments, voids, refunds, adjustments, running balances (F6)
- M8: Dues overview with statuses, season and date-range totals, dashboard (F7)
- M9: CSV exports: dues, payments ledger, plot register, waiting list (F7)
- M10: Staff accounts with Admin and Coordinator roles, sign-in lockout, password changes (F8)
- M11: Operator settings and an audit log viewer (F8)
- M12: Backup and restore commands, versioned migrations, automated test suite (F1, all)

### Later (not in v1)
- Gardener self-service portal and online applications: needs public exposure and identity handling. Coordinators enter people manually in v1.
- Email or SMS offers and payment reminders: coordinators already contact people. Delivery and consent add a lot.
- Online card payments or bank-feed import: payment-provider integration. Payments are recorded by hand in v1.
- Concession or discount tiers: Admin adjustments cover individual cases.
- Shared plots or multiple plots per gardener, and plot transfers between holders: they complicate list rules. Use release plus direct assign.
- Spreadsheet import wizard: backdated join dates let the list be rebuilt by hand.
- Plot map or layout view, work-hour tracking, tool-shed keys and deposits.
- Multiple gardens or hosted multi-tenant service.
- PDF receipts and invoices.

## Domain rules and defaults

- **Plots:**
  - Code: unique, 1–20 characters.
  - Sizes: small, standard, large.
  - Statuses: Available, Offered, Occupied, Out of service, Retired.
  - Only Available plots can be offered or directly assigned.
- **Holding limit:** a gardener holds at most 1 plot **(default)**. A plot holder cannot have an active waiting-list entry.
- **Waiting-list order:**
  - Entries are ordered by join timestamp, ties by creation sequence.
  - The position shown is the rank among active entries.
  - Each gardener has at most one active entry.
  - Size matching: a plot is offered to the earliest entry whose preference equals the plot's size or is "any".
- **Offers:**
  - One open offer per plot and per entry.
  - Expiry is 14 days after the offer date **(default)**.
  - Expired offers are flagged but never auto-resolved. A user records the outcome.
- **Decline handling:** per `waitlist.decline_policy`. "move_to_end" sets the join timestamp to the decline time. After 2 declines **(default)**, the entry is removed with reason "declined twice".
- **Seasons:**
  - A season is one calendar year in the garden's time zone.
  - The due date defaults to 31 March of the season year **(default)**.
  - Only one season is current, the most recently opened.
- **Fees:** small 40.00, standard 60.00, large 80.00 **(default)**. The fee is fixed on the charge when it is created, so later fee changes don't alter existing charges.
- **Prorating:** when `fees.new_holder_charge` = prorated, the charge is fee × remaining months ÷ 12. Remaining months count the acceptance month through December. The result is rounded half-up to the cent.
- **Fee status per gardener and season:**
  - Balance = charges + adjustments − payments + voids + refunds.
  - **Paid:** balance = 0 and something was charged.
  - **In credit:** balance < 0.
  - **Partly paid:** 0 < balance < charged.
  - **Unpaid:** balance = charged, on or before the due date.
  - **Overdue:** balance > 0 after the due date.
- **Release:** it never removes charges. Fees are not refunded automatically; refunds are done by hand (F6).
- **Time:** all dates are shown in the garden's time zone, chosen at setup. The default is the server's local zone. Timestamps are stored in UTC.
- **Sessions:** expire after 8 hours of inactivity **(default)**.

## Operator settings

| key | values | default | what it decides |
|---|---|---|---|
| `waitlist.decline_policy` | keep_place, move_to_end | keep_place | Whether a person who declines an offer keeps their position or goes to the end of the list. |
| `fees.new_holder_charge` | full, prorated | full | Whether someone accepting a plot mid-season pays the full yearly fee or a share for the remaining months. |
| `payments.overpayment` | reject, credit | reject | Whether a payment larger than the balance is refused or kept as credit on the gardener. |
| `plots.release_with_balance` | allow, block | allow | Whether a plot can be released while its holder still owes money. |

## Money and data integrity

- **Storage and currency:** amounts are stored as integer cents, never floats, in one currency set at setup. Amounts are entered and shown with 2 decimals. Prorating is the only calculation that rounds (half-up).
- **Append-only ledger:**
  - Charges, payments, voids, refunds and adjustments are never edited or deleted. Corrections are new linked entries.
  - Every entry records user, timestamp, season and reason where required.
- **Totals:** every balance and total shown or exported is computed from the entries, never stored separately. Season totals always equal the sum of the gardener balances.
- **No double counting:**
  - At most one fee charge exists per assignment and season, enforced by the data store.
  - Each payment form carries a one-time token, so a resubmitted form saves once.
  - An entry can be voided only once.
- **Concurrency:**
  - Offers, accepts, releases and plot status changes run in a transaction that checks the plot's current status and version. The second of two simultaneous actions fails with a "changed since you opened it" message and changes nothing.
  - Waiting-list positions are derived, never stored, so they cannot drift.
- **Validation before saving:** required fields, uniqueness, allowed state transitions, the amount format and the rules above. A failed save changes nothing.
- **Never lost:** assignments, waiting-list entries and ledger entries are closed, not deleted. Deleting is not offered anywhere in v1.

## Install and run

- **Install:** one documented command installs dependencies and starts the service on a fresh machine. The README covers it, with a second form to run it as a background service that restarts on reboot.
- **Safe defaults:**
  - The service listens on 127.0.0.1:8080 unless configured otherwise.
  - A fresh install starts empty.
  - The first Admin is created via the setup page using the one-time code printed to the terminal, and the password is chosen by the operator.
  - There is no default password and no built-in secret. A session secret is generated at first run if none is configured.
- **Demo data:** loaded only by an explicit demo command, and only into an empty install. It contains:
  - 40 plots across all sizes and statuses;
  - 45 gardeners;
  - 12 waiting-list entries with mixed preferences;
  - one open offer and one expired offer;
  - a current season charged, with about 30 payments covering paid, partly paid, unpaid, overdue and in-credit cases;
  - one void, one refund and one adjustment;
  - Admin "admin" and Coordinator "coordinator" accounts with random passwords printed once.
- **Configuration:** environment variables or a config file covering listen address, port, data directory, session secret, log level and time-zone override. All are documented in the README with defaults.
- **Data and backup:**
  - All data lives in one documented data directory.
  - A backup command writes a timestamped single-file backup.
  - A restore command replaces the data from a backup after confirmation.
- **Upgrades:** the schema is versioned. Migrations run automatically at start after taking an automatic backup.

## Non-functional basics

- **Authentication:** password sign-in with hashed passwords and a CSRF-protected session.
- **Roles:**
  - Coordinators do F2–F4 (except direct assign), F6 (except adjustments) and F7.
  - Admins do everything.
  - The server enforces roles, not only the UI.
- **Validation:** every form shows field-level errors in plain words and keeps the entered values.
- **Persistence:** all state survives a restart.
- **Audit:** the audit trail covers every change to plots, gardeners, waiting list, offers, assignments, ledger, users and settings, with who, when, before and after.
- **Tests:** an automated test suite runs with one documented command. It covers:
  - waiting-list ordering and size matching;
  - both decline policies;
  - offer and accept concurrency;
  - idempotent season charging;
  - prorating and rounding;
  - balance and status calculations;
  - overpayment and refund limits;
  - role enforcement;
  - backup and restore round-trip.

## Definition of done

- [ ] F1: Install and first run — on a fresh machine, the README command starts the service on 127.0.0.1:8080. The setup code creates the Admin, who lands on an empty dashboard. The demo command fills an empty install and refuses on a non-empty one.
- [ ] F2: Manage plots — a plot is added, found by code, edited, put out of service, returned and retired. A duplicate code and retiring an occupied plot are both refused. The plot history shows each change.
- [ ] F3: Manage gardeners and the waiting list — gardeners are added and searched, and the duplicate warning appears. Entries are added (including backdated) and appear in correct order. Removing one moves later positions up. Archiving is blocked while the gardener owes money.
- [ ] F4: Offer a freed plot and assign it — releasing a plot and clicking "Offer to next in line" picks the earliest matching entry. Decline follows the setting and accept makes the plot Occupied with a charge. A simultaneous second offer is refused.
- [ ] F5: Open a season and charge fees — opening 2026 charges every current holder exactly once at their size's fee. Re-running it is refused. A mid-season acceptance gets a full or prorated charge per the setting.
- [ ] F6: Record payments, refunds and adjustments — a payment, a void, a refund and an adjustment each appear as separate ledger entries with a correct running balance. Overpayment and over-refund are handled per the rules, and a double submit saves once.
- [ ] F7: Dues overview, totals and exports — the Dues page shows correct statuses and totals that equal the ledger. All four CSV exports download with matching numbers.
- [ ] F8: Staff accounts and settings — an Admin creates a Coordinator, who must change the temporary password. Coordinators cannot reach Settings or adjustments. The last Admin cannot be deactivated. Setting changes show in the audit log.

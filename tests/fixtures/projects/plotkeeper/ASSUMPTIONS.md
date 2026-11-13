# Assumptions

The idea this product was built from leaves these questions open. Each section says what this version does.

The rules and numbers the idea left open are set as defaults in the product scope, `SCOPE.md` (each marked *(default)*).

## Settings

Where reasonable operators differ between two business policies, the operator chooses. Each setting below exists in this version; the README's configuration section says how to change it, and the environment variable named below overrides it everywhere.

| key | values | default | scope | settles |
|---|---|---|---|---|
| `fees.prorate_from` | recorded_date, start_date | `start_date` | global | For prorating, is the 'acceptance month' the month the acceptance is recorded or the month of the chosen assignment start date? |
| `seasons.current_rule` | most_recently_opened, latest_year | `latest_year` | global | If an Admin opens an older year after a newer one (e.g. 2025 after 2026), which is current? |
| `assign.direct_with_active_entry` | close_entry, refused | `close_entry` | global | May an Admin direct-assign a plot to a gardener who has an active waiting-list entry? |
| `offers.valid_on_expiry_date` | true, false | `true` | global | Is an offer still open (not Expired) on its expiry date itself? |

- `fees.prorate_from` (`FEES_PRORATE_FROM`): `recorded_date`: remaining months count from the month Accepted is recorded; `start_date`: remaining months count from the month of the assignment start date.
- `seasons.current_rule` (`SEASONS_CURRENT_RULE`): `most_recently_opened`: whichever season was opened last becomes current; `latest_year`: the season with the highest year is current.
- `assign.direct_with_active_entry` (`ASSIGN_DIRECT_WITH_ACTIVE_ENTRY`): `close_entry`: direct assign succeeds and the gardener's active entry closes as placed; `refused`: direct assign is refused while the gardener has an active entry.
- `offers.valid_on_expiry_date` (`OFFERS_VALID_ON_EXPIRY_DATE`): `true`: offer shows Expired from the day after the expiry date; `false`: offer shows Expired on the expiry date itself.

## Decisions

Questions that aren't a simple switch. For each: the two ways to read it, and what this version does.

### What status applies when the balance is positive but above the charged amount (e.g. after a + adjustment) on or before the due date, or zero via a full waiver; does 'charged' include adjustments?

- One reading: 'Charged' means fee charges only; such cases fit no status
- The other: 'Charged' includes positive adjustments; balance ≥ charged before due date is Unpaid
- **This version: 'Charged' includes positive adjustments; balance ≥ charged before due date is Unpaid.**

### How are declines counted for the 'declined twice' rule: per entry ever, or reset after a move to the end or a withdrawn offer?

- One reading: Every decline or recorded expiry on the entry counts, never reset
- The other: Same, counted per entry for its lifetime
- **This version: Every decline or recorded expiry on the entry counts, never reset.**

### When someone accepts in a year with no season for that year (current season is a previous, possibly closed, year), is a charge created and against which season?

- One reading: Charge against the current season if it is not closed
- The other: No charge until that year's season is opened, then charged by season opening
- **This version: Charge against the current season if it is not closed.**

### With release_with_balance = block, which balance blocks release: current season only, or any season including credit offsets across seasons?

- One reading: Any positive balance in any season
- The other: Any season with a positive balance (per season, no cross-season netting)
- **This version: Any season with a positive balance (per season, no cross-season netting).**

# v5 (hand-written): vanilla build, then post-build search

Written by hand from `docs/design/build-v5.md` §3 (track V5-D). Later entries are appended
by `repo-factory lessons promote` with `author: lessons-loop` (§5.5); every entry names its author.

## V5-D: the v5 pipeline (2026-10-04)

author: human (V5-D worker)

- `rules.yaml`: `pipeline: v5` (dispatched by `repo-factory build`), the §3.1 stage caps, a global
  $11 cap, `interpret: auto` with at most one fork, `regenerate: off`, and the stage list.
- `prompts/`: `claims.md` (two independent spec-only samples, plus the dedupe section),
  `verifier.md` (the fresh full-tools verifier, with the hand-reproduction rule),
  `reverify.md`, `oracle.md` (the 3-vote panel, plus the interpretation vote), `pair.md`
  (the conflict protocol), `repair.md` (and the fix-up section), `mutants.md`, and
  `postmortem.md` (§5.3's contract, used by track E).
- `lessons.yaml`: version 0, empty.

## V5-SCOPE: scope ideas, walk the flows (2026-10-04)

author: human (V5-SCOPE worker)

- `rules.yaml`: `scope: auto` (`auto | always | off`) and `scope` first in `stages:`.
- `prompts/scope.md` (new): the v1 product brief, `SCOPE.md`, for an idea; its `judge`
  section lets the model pass a concrete spec through (`auto` only).
- `prompts/verifier.md`: the `scope` section (install from the README on a fresh copy, walk
  every definition-of-done flow end to end through the real interface, one
  `test_flow_<id>.py` per flow, `class: incomplete` for missing or partial things, a
  `flows` list in findings.json); the README's test instructions are checked; stubs, dead
  controls and placeholder data are findings in every build.
- `prompts/reverify.md`: the `flows` section (walk the flows again after a repair).
- `prompts/repair.md`: what an **Incomplete** finding asks for.

## V5-READY: the readiness stage (2026-10-04)

author: human (V5-READY worker)

- `rules.yaml`: `readiness` in `stages:` (after `reverify`), with its own caps (security
  review $1.20, checks $0.60, one repair round $1.50 with a $0.50 minimum, a fresh session:
  `resume: false`), `medium: if_budget` and `start_usd: 0.3`. `max_usd` 11.0 -> 14.0 (the
  stages before readiness kept their 11.0; a second repair round keeps the stage's $3.30 in
  reserve); `$REPO_FACTORY_V5_MAX_USD` overrides the default.
- `prompts/security.md` (new): the threat model first, then probes of the running product
  (authN/authZ, hostile HTTP, injection, auth, secrets and defaults, dependency audit, error
  pages), each finding with a severity, a hand repro and a probe test.
- `prompts/readiness.md` (new): the operational checks, each done (clean install from the
  README, safe defaults, configuration, backup and restore, restart, shutdown, health, logs,
  upgrade), with probes for the red ones; its `repair` section is the readiness round's
  brief (safe defaults are the owner's standing rule; commit each item as it is done).
- `prompts/scope.md`: breadth the idea names (many SaaS tools, any bank, multiple
  currencies) is must-have, its common real-world cases named; the install section asks for
  safe defaults (localhost, empty start, first-run admin, no default password, demo data
  behind a flag, documented configuration, an upgrade path). F1 ends on an empty system.

## V5-POLISH: README commands as written, the immediate restart, no global cap, trims (2026-10-05)

author: human (V5-POLISH worker)

- `rules.yaml`: `max_usd: null`, no global cap by default (the owner's standing rule; a
  number, `$REPO_FACTORY_V5_MAX_USD`, `--max-cost` or the task's `budget_usd` still caps). The
  reserve rules apply only under a cap; the stage and session caps stay.
- `rules.yaml` `readiness.commands`: the timeouts of the new `commands` step, where the
  harness runs the README's quick start, an immediate restart (after SIGTERM and after
  SIGKILL) and every documented test command as written, on a fresh copy of the tree as it
  will be delivered, in a loopback-only sandbox. Failures go to the readiness round: a
  README command rank 3, a slow restart rank 2.
- `rules.yaml` trims: `mutants.survivor_round: false`; `claims.samples_scoped: 1` (spec
  builds keep 2); `repair.resume: auto` with `warm_minutes: 30` (resume the builder only
  while its cache is likely warm, else a fresh session; recorded per round). Re-verify runs
  only when a further round can follow it.
- `prompts/readiness.md`: `restart` is done at once after a stop and after a SIGKILL (address
  reuse off is red); the session reports how to run the service (`run`); the repair section
  says every README command must work as written.

## CH: the scope's word budget, in code (2026-10-16)

author: human (CH worker)

- `rules.yaml` `scope_size`: SCOPE.md's limit (1,800 words for a single-user tool or a
  library, 2,500 otherwise, from the spec's `Shape:` line, else the scope's own). Over it,
  one shortening pass gets the scope with its word count and the limit; it is kept only when
  it is a usable scope and shorter. `trim_max_usd` caps that pass (estimated from the first
  call's cost before it starts). `scope.json` records both lengths under `size`.

## FX1: the mutants stage removed (2026-10-16)

author: human (FX1 worker)

- `rules.yaml`: `mutants` is out of `stages:`, and its section (`n`, `model`,
  `survivor_round`) is gone; `prompts/mutants.md` is deleted. The mutation score decided
  nothing (no gate, repair or retire decision read it; the calibration took it and did not
  use it), and across 13 runs the stage cost $3.55 and 229 minutes of wall clock, 17.6
  minutes a build. Old rounds' `mutants.json` stays on disk, unread; a `rules.yaml` or a
  `loop.yaml` that still names the stage (in `stages:`, `build.models` or
  `build.stage_caps`) is read as if it did not.

## FX1: p_hidden removed, one claim sample, dead keys out (2026-10-16)

author: human (FX1 worker)

- `rules.yaml` `claims`: `samples: 1` (was 2, with `samples_scoped: 1`, now gone). The
  second sample existed only for the p_hidden estimate's capture-recapture term (33 of 36
  recorded rounds were scoped and already took one); dropping it saves a sample and the dedupe call (about
  $0.45 a build). The gate's `p_hidden` check and `prediction.json` are gone: the number was
  0.958-0.99 in every recorded round, 0.99 even with 0 claims checked, and never held a
  build. REPORT.md now states the facts it summarised (claims checked, defects found, fixed
  and open).
- Removed keys nothing read: `stop.yield_window_usd`, `repair.rounds` (only `max_rounds` is
  read), `interpret.fork` (ignored since Addendum A) and `regenerate` (a stub that only
  emitted `off`).

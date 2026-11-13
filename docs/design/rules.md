# The factory's own rule set

*2026-10-03. Code: `repo_factory/problems/repo/rules/`. Settings: `policies/v2/rules.yaml`
under `ruleset:`.*

## Why

A blind review of the v2 lending-system build found it lost to vanilla Claude Code on
correctness: 49/53 behavioural scenarios against 53/53. The failures were all
**cross-milestone interaction bugs**:

- renewing an overdue loan wiped the accrued late fee (and lifted the €5 block);
- checkout didn't validate the branch, so the inter-branch transit could be skipped;
- a hold placed while a copy was on the shelf was never offered;
- lazy hold expiry let a stale hold block renewals.

Each milestone gate checked only its own slice. Scoring v2's test-quality filter also drops
any generated test that fails on *every* candidate, which is exactly the test that catches a
systematic, shared bug.

**Principle:** the factory builds its own rule set and scores against it continuously, with
no human or external reviewer in the loop.

## Rules

A rule (`rules/model.py`) is one self-contained pytest module plus metadata:

| field | meaning |
|---|---|
| `id` | `q<n>` requirement, `x<n>` interaction, `e<n>` edge, `g<n>` regression |
| `source` | the spec sentence or answer it traces to, quoted (required) |
| `kind` | requirement, interaction, edge or regression |
| `milestones` | the plan milestones whose behaviour it exercises |
| `status` | candidate, active, disputed or retired |
| `reason` | why it has that status (validation, arbitration) |
| `origin` | mining mode, prompt version, pass, milestone it was mined after |
| `visibility` | visible (workers see it) or held (never in a workspace) |
| `version`, `verdicts`, `caught`, `fixed` | repairs, arbitration, and the learning record |

Rules use only the plan's fixed public interface (from the planner), so they need no
adapters.

**Store** (`rules/store.py`): one per build, at `<den>/cache/rules/<task>/r<round>/`.
`rules.json` holds the current state; `history.jsonl` records every status change and
repair (append-only); `questions.json` holds ambiguities for the owner. Workers can't write
to it, because it lives in the den and workers run in sandboxed workspaces outside it.
About 25% of rules (by hash) are **held**: they are never exported to a workspace, never
scored or gated (a gate failure would reveal them), and only reported.

## Lifecycle

```
mine ──► candidate ──(a)(b)(c)(d)──► active ──fails──► arbiter ─┬─ code_wrong: stays, fix queued
                    └─ rejected ──► retired                    ├─ rule_wrong: repaired (re-validated) or retired
                                                               └─ ambiguous: disputed + question for the owner
```

**Mining** (`rules/miner.py`). One model call is given the spec, the answers, the plan and
interface, the existing rules (and the rejected ones with their reasons), and the current
code of the kept candidate. The code helps it aim at likely bugs and set up state; the
*expected* outcome must come from the spec. It asks for scenarios that a *plausible but
subtly wrong* implementation would fail. There are three modes:

- `requirements`, against milestone 1's code: one rule per stated behaviour;
- `interactions`, after every later milestone: how the new milestone's behaviour combines
  with earlier ones. This covers state one feature accumulates that another must keep,
  recurring parameters (location, owner) validated by every operation, transitions one
  feature must trigger in another whatever the order, time limits that take effect with
  or without a maintenance call, and refusals that apply only while the blocking state is
  still in force;
- `edges`: boundaries, invalid input, repeated or out-of-order operations, concurrency,
  and time.

The prompt is generic. It names categories, not the lending bugs.

**Validation** (`rules/validate.py`), before a rule becomes active:

- (a) **Traceable.** Every quoted fragment of `source` appears in the spec or an answer,
  ignoring case and punctuation.
- (b) **Independent agreement.** A second, batched call that didn't write the rule reads
  the spec and decides on its own what the outcome should be. Only `agree` survives. The
  call is told that time rules take effect whether or not a maintenance call was made.
- (c) **Deterministic.** Two runs on the same code, in opposite file orders, give the same
  outcome.
- (d) **Collectable against the interface.** The rule parses and has an assertion. It is
  not broken in its own file (scoring v2's `broken_reason`), and it doesn't import a
  module or name outside the interface. A missing third-party module that the code itself
  uses is the code's fault (`missing_dependency`).

A rule that *fails* on the current code is not rejected; that is the point of having rules.

**Arbitration** (`rules/arbiter.py`) replaces "drop tests that fail on every candidate" for
rules; plain generated tests keep scoring v2's filter. When active rules fail, one batched
call judges each rule from the spec, the rule, the failure output and the code. Each rule
is judged once per version; a later identical failure reuses the verdict.

## Continuous scoring and gates

- Every candidate is scored against **all active rules accumulated so far** whose
  milestones are all built (`RuleStore.select(due=...)`). Earlier milestones' rules act as
  regression tests, and interaction rules apply once their milestones exist.
- The coverage term becomes `0.6 × rule pass rate + 0.4 × checklist coverage`
  (`coverage_weight`). The judge keeps what rules can't check (docs, Docker, UI).
- A score record carries `rules_key`, the identity of the rule set it was scored with.
  When the set grows, the runner's `stale` hook marks earlier records stale and the
  executor rescores them, so all candidates stay comparable.
- **Gate**: the scoring v2 gate, plus the rule part. This milestone's rules pass at
  `gate_min_rate` (0.8), and no rule that passed in the lineage's previous gate fails now.
  Failing rules go into the retry prompt, with their files under `acceptance/rules/`.

## The closing loop

After the last milestone (`Session.run` calls the executor's `review` hook before
delivering), the loop runs: mine (interactions + edges) → score → arbitrate → fix → repeat.
A fix is a resumed worker session on the head, run as milestone `review<k>`, whose prompt
is the failing rules the arbiter blamed on the code. It is kept only if it passes more
rules and breaks none (`loop.accept`).

The loop stops when:

- a round finds no new failing rule blamed on the code (`clean` or `no_new_failures`);
- the round cap is reached (`review_rounds`, default 3).

Held rules are never sent to a fix. This is the factory's own "2-opt pass". The loop
lives in the executor, not the policy. Replay doesn't redo it, and moving it into the
policy as an action is a later step.

## Scheduling: planned and background mining (build v3 §2)

Settings under `mining:` (`rules/schedule.py`; the defaults are v2's behaviour). Code:
`core/mining.py` (`MiningController`).

- `schedule: every_milestone` (v2) or `planned`. Planned: a **plan pass** before building
  (requirements + interactions from the spec, plan and interface; no code, so the rules
  pass (a) and (b) and wait as candidates until (c)/(d) run on milestone 1's kept code),
  an interactions pass after each milestone the planner flags in `interacts_with` (it is
  asked for them only under this schedule, so v2's planner prompt and cache keys are
  unchanged), and one edges pass after the last planned milestone.
- `async: true`: a pass runs in one background thread while the next worker builds,
  against the latest kept code. Its rules are **embargoed** (`RuleStore.embargo`) until the
  next worker launch admits them, so they join the *next* gate and never change a gate
  under way; earlier candidates are then rescored as usual. Failing late rules the arbiter
  blames on the code become **fix items**: added to the next worker's prompt and required
  by its gate (`score.gate(fix_items=)`). Always the next worker, never a separate fix run:
  it continues the same lineage and session, and a dedicated run would only add a worker
  and a gate. Whatever still fails at the end goes to the closing loop. The end of the tour
  waits for a pass still running. The store, the LLM counters and the venv builder are
  thread-safe; events and the ledger are written under locks.
- `promote_held: true`: a held rule the arbiter blames on the code is promoted to visible
  (`history.jsonl` records the visibility change; `rule_promoted` event), so the fix loop
  can use it, and a held replacement is mined in the same area (`task=replace`, one rule,
  the promoted rule in the prompt).
- `review_rounds`: the closing loop's cap (null: `ruleset.review_rounds`). With `async`, the
  loop mines round k+1 in the background while round k's fix runs (`review_loop(prefetch=)`).
  Round k+1 only runs when round k sent new failing rules to a fix.

Mining that scales with the task (build v3 §7c; all off in v2, on in v3):

- `coalesce`: at most one pass waits behind the running one. A new request replaces it
  (modes merged; `mining_finished.coalesced` lists the points it replaced), and a pass
  mines against the latest kept head when it starts, never against superseded code.
- **Rule budget**: `rules_per_milestone` x planned milestones, capped at `max_rules`
  (`schedule.budget`). Each pass asks the miner for its share of what is left
  (`schedule.allot`: the remaining budget over the passes still to come, the closing pass
  keeping up to `closing_rules`), split over its modes and capped by the miner's `n`. The
  miner never keeps more than it was asked for. A pass with nothing left is skipped
  (`mining_skipped`), except a pass for a failing gate: `MiningController.gate_failing` (the
  controller's hook) mines `rules_per_milestone` rules in that area past the budget.
- **Small plans** (`small_plan_milestones`, `small_plan_sizes`): at most that many
  milestones, or all of those sizes, get only the plan pass and one closing pass
  (interactions + edges). Larger plans keep the per-milestone interactions passes, only
  after milestones the planner flags in `interacts_with`.
- `close_early`: the closing pass starts when the last planned milestone's worker
  launches, in the background against the head before it; the end of the tour waits only
  for what is left of it. Review round 1 then scores the delivered code on its rules
  instead of mining again.
- `combine`: interactions + edges in one miner call (closing pass, review rounds); each
  rule's `origin.mode` follows its kind. Ledger purpose `mine:interactions_edges`.
- `lean_prompt`: the existing rules go to the miner as titles only (id, kind, milestones,
  title), and the code digest is the source files only (`checklist.source_text`: no
  tests, no docs; the files of the milestones the pass is about first). On the TTL-cache
  smoke build this cut the review-round prompt from 59.5k to 25k characters (the code from
  29.5k to 7.3k). Rule origins record the prompt version as `<version>+lean`.

`scripts/lab/mining_replay.py <den>` replays a build's mining under another policy offline
(the selection logic is the real code; costs and times are fitted on the den's ledger).

## Reporting and learning

- REPORT.md has a **Rule set** section. It covers counts by status and by mining mode, the
  delivered code's pass status on visible and held rules, the review rounds, the active
  rules, the retired and disputed rules with their reasons, and the owner questions.
- `repo-factory status` and the UI candidate table show each candidate's rule pass rate.
- Events: `rules_mined`, `rule_status` and `review_round` (see `docs/events.md`).
- **Learning hook.** Each rule records the mining mode and prompt version that produced
  it, `caught` (code_wrong verdicts) and `fixed`. Hibernate diagnostics include
  `rules_mining`: per mode@prompt, the rules proposed, activated, and those that caught a
  real bug. That is recording only, for now.

## Running it outside a build

`python -m repo_factory.rules <code_dir> --spec spec.md --interface iface.txt
--plan plan.json --answers answers.json --modes interactions,edges --store <dir>` mines,
validates, scores and arbitrates against existing code, and prints a JSON summary.

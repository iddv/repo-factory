# Build event log (`events.jsonl`)

`repo-factory build` and `repo-factory revise` append one JSON object per line to `<den>/events.jsonl`.
The den is `<taskdir>/.den` unless `--den` is given. The log is the contract between
the factory and anything that shows its progress: the terminal renderer, `repo-factory status`
and the `repo-factory ui` page.

Rules for readers:

- Lines are only ever appended. Each line is written and flushed whole, under a lock,
  so lines from parallel workers never interleave. A reader that catches a line
  mid-write should skip anything that does not parse; `repo_factory.events.read_events`
  does this.
- Ignore unknown `type`s and unknown fields. New fields may be added to any event.
  Removing or renaming a field, or changing its meaning, bumps `v`.
- One log holds every build of the task, in order. The latest build is the last
  `build_started` or `revise_started`; everything else belongs to a build through its
  `build` field.

Python readers can use `repo_factory.events`: `read_events(path)`, then `fold(events)` for
the latest build's state as a dict (the same dict `repo-factory status --json` prints), or
`load_state(taskdir)` for both steps plus crash detection.

## Common fields (every line)

| field | type | meaning |
|---|---|---|
| `v` | int | schema version, currently `1` |
| `ts` | float | Unix time in seconds (3 decimals) |
| `task` | str | task id (`task.yaml` `id`, else the folder name) |
| `build` | int | run id: the den round id of this build or revise. It is unique within the den and matches `repo-factory lab show --round` |
| `type` | str | one of the types below |

## Types

### `build_started`
`spec` (path to spec.md), `taskdir`, `title` (the spec's heading or first sentence),
`policy` (version, e.g. `v2`), `harness` (`claude-code` or `fake`), `owner`
(`human`, `queue` or `sim`), `out` (the delivery repo path), `pid` (the build process),
`builder` (`claude` or `rules`), `builder_model`, `worker_model` (`null`: workers run on
Claude Code's own default model).

### `revise_started`
This starts a `repo-factory revise` run. It has the same fields as `build_started` (no `owner`),
plus `note` (the owner's feedback), `parent` (the delivered node being refined) and
`workers`. A revise asks no questions. (That is the pre-v5 revise; a v5 build's revise
round is a `build_started` of kind `revise`, below.)

A v5 **revise round** (`repo_factory/v5/change.py`, Ask for changes) starts with
`build_started` plus `kind: "revise"`, `revises` (the round it changes), `origin` (`ui`,
`mcp`, `cli`; older records may say `chat`, or `honesty` for the wording repair rounds before 2026-10-16), `mode` (`docs` or `code`), `instructions` (the request, at
most 4000 characters) and `stages` (`build`, `verify`, `repair`, `reverify`, `deliver`). The
stages it carries emit `v5_stage` `skipped` with `why: "carried from round <n>"`. After
`delivered` it emits `revise_outcome`: `round`, `revises`, `mode`, `origin`, `changed` (the
files that differ on `brb/<id>`), `before` and `after` (`{"own_failed", "total"}` of the
product's own tests) and `what` (the session's own summary). A failed round ends
`build_finished` `error` with `revise failed in <stage>: …`; the branch is as it was.

### `question`
`id` (`q1`…), `text`, `n` (1-based count of questions asked so far), `of` (how many the
build expects to ask; `n` never exceeds it).

A policy v2 build asks one more question after the clarifying ones: id `plan`, whose
`text` is the milestone list in plain English ending with "Anything wrong, missing or in
the wrong order? (Enter to accept)". It counts in `n`/`of` like any other question. A
non-null answer replans once (a second `plan` event).

Build v3 (`decide.plan_review` in the policy's `rules.yaml`): with `off` the `plan`
question is not asked at all (the plan shows through the `plan` event); with
`timer:<seconds>` it is asked with `auto_accept_s` (the timeout) and the text ends with
"Auto-accepts in <s>s.". With `decide.adaptive_questions`, only the clarifying questions
that scored high on impact and owner-specificity are asked (possibly none); the rest are in
the `assumptions` event.

### `answer`
`id`, `text`. `text` is `null` when the owner skipped the question (Enter at the
terminal, or an empty `repo-factory answer`), or when a queued question timed out. A plan question
on a timer that got no correction has `text: null` and `auto_accepted: true` (whether the
timer ran out or the owner accepted early with an empty answer: the effect is the same).

### `assumptions`
Build v3 (`decide.adaptive_questions`), with each `plan`, just before it. `items`: what the
factory decided instead of asking, `[{"question", "assumption", "impact",
"owner_specific", "why", "source"}]`; `impact` and `owner_specific` are 0–1 scores;
`source` is `not asked` or `skipped by the owner`. REPORT.md lists them under "Assumptions
I made".

### `adr`
Build v3 (`decide.tech_select`), with each `plan`, just before it: the technology decision
the plan and the workers follow. `decision` (the chosen option's name), `stack`
(`{"language", "framework", "storage", "concurrency", "ui", "packaging", "run", "tests"}`,
any subset), `candidates` (`[{"name", "total", "scores"}]`: every option considered, 1–5
per criterion, weighted total). The full record is `ADR.md` in the delivery.

### `plan_reviewed`
Build v3 (`decide.plan_check`), with each `plan`, just before it: the plan review pass (a
model call, not the owner) that walked every spec rule across every operation of the
interface. `version` (the plan it reviewed), `gaps` (`[{"rule", "operation", "problem",
"fix"}]`), `applied` (`true` when its corrected interface or milestones replaced the
planned ones; the `plan` event already shows the corrected plan), and `error` when the
review returned nothing usable.

### `tests_generated`
`visible` (generated acceptance tests that workers see), `held_back` (tests kept back to
check the delivery). It is emitted only when tests were actually generated, so the
`rules` builder on a real task emits none.

### `plan`
Policy v2 only. `version` (1, then 2 after a replan), `replanned` (`true` when the owner's
answer to the `plan` question produced this plan), `requirements` (checklist size),
`interface` (the public interface the plan fixes, plain text, one name per line, or `null`),
and
`milestones`: a list of `{"id", "title", "build", "size", "depends_on", "requirements"}`
in build order. `id` is `m1`…`mN`, plus `final` for the final integration pass; `size` is
`S`, `M` or `L`; `requirements` are checklist ids.

Build v3 §3 (`search.tasks`): each planned milestone also has `tasks`, a list of `{"id",
"title", "uncertainty", "size", "risk", "hints", "requirements"}` in build order. `id` is
`<milestone>.t<k>` (e.g. `m2.t1`); `uncertainty` is `design` (a data model, a queue or
state mechanism, a transaction strategy, an API shape: built several ways and judged) or
`routine`; `hints` are a design task's approach hints (2-3, empty for routine); `risk` is
`high` or `normal` (a high-risk design task gets 3 forks instead of 2). The final pass has
no tasks.

Build v3 §4 (the controller): the replan after a backtrack is a `plan` event too, with
`replanned: true`, `backtrack` (its number) and `kept` (the milestones whose state carries
over: those before the replanned ones that no discarded run touched). The milestones it
redoes start afresh; one the backtrack's target already worked on gets `milestone_started`
(and its task `task_started`) again with `reopened: true`.

### `controller_decision`
Build v3 §4 (`controller.enabled`), after each task's latest run: what the controller chose
next. `at` (the run it follows), `epoch` (backtracks before it), `milestone`, `task_id`,
`option` (`continue`, `fix`, `branch`, `backtrack`, `replan`, `mine`), `label` (the
labelled option, e.g. `backtrack@m1.t1~h1`, `backtrack@start`, `backtrack@before:m2`,
`replan@m3`, `mine:interactions@m2`), `by` (`rules`; `llm` when the rules were ambiguous and
the model chose; `fallback` when they were ambiguous and there was no model or its answer
was not one of the options), `reason` (in words), `ambiguous`, `menu` (the labels offered
to the model, when ambiguous), `params` (`node`, and as they apply `k`, `hints`, `n`,
`mode`, `from_milestone`, `change`, `flaws`), `record` (the den `control` node holding the
situation it decided from; replay recomputes the decision from it).

### `backtrack`
Build v3 §4, when the controller decides a backtrack (or a replan): `number`, `kind`
(`backtrack` | `replan`), `label`, `node` (the node the build continues from: an earlier head,
a design task's losing fork, or `null` for the start), `target` (`start`, `milestone`,
`task`, `loser`, `replan`), `from_milestone` (where the replan starts), `change` (the design
change), `reason`, `by`, `at`, `discard` (the runs set aside, oldest first; they stay in the
tree and reach the rebuilt milestones as reference diffs), `flaws` (the rule ids whose
`design_flaw` verdicts it answers; those verdicts are superseded).

### `replan`
Build v3 §4, right after a backtrack's replan: `number`, `version` (the plan version),
`from_milestone`, `interface_changed`, `kept`, `milestones` (ids), `retired` (`[{"id",
"reason"}]`: rules the change invalidated; the rest are kept and rescored), `summary`,
`corrected` (the planner returned a corrected interface or milestones).

### `adr_revised`
Build v3 §4, with a backtrack's replan when the build has an ADR: the controller's logged
revision path. `revision` (1, 2, ...), `change`, `reason`, `stack` (stack entries the change
replaces, usually `{}`), `backtrack`. The revision is appended to `ADR.md` as a `Revision k`
section, which workers see in their contract.

### `task_started`
Build v3 §3 only, once per task, when its first run starts. Because `task` is the common
field naming the build's task, the task's id is `task_id`. Fields: `milestone`, `task_id`,
`title`, `kind` (`design` or `routine`), `n` (runs launched for it: the forks of a design
task, 1 for a routine one), `size`, `risk`, `hints` (a design task's approach hints, one
per fork, in fork order), `parent` (the head node the task starts from, or `null`),
`max_turns` and `max_usd` (the per-run caps for the task's size, `search.budgets`).

### `task_finished`
Build v3 §3 only, when the tour moves past the task (or the build stops). `milestone`,
`task_id`, `title`, `kind`, `n`, `runs` (all runs of the task, retries included),
`winner` (the node the build continues from: for a design task the judge's winner unless
it failed and a retry passed), `passed` (that run passed the task gate), `coverage` (of the
requirements of the milestone's tasks so far), `usd`, and `reason` (why, in words: e.g.
`judge: node 14 won at stage (c); (c) pairwise judge node 13 0.5, node 14 1.5 point(s)`,
`gate passed`, or `shortfall: ...`). A design task also has `stage` (the judging stage that
decided: `a` hard gates, `b` rule pass rate, `c` pairwise judge, `d` cost, or `only`),
`reasons` (every stage's reasoning), and `candidates`: `[{"node", "hint", "hint_text",
"session_id", "outcome" ("winner" | "loser"), "passed", "rules" ([passed, total] on every
admitted rule, or null), "points" (pairwise points), "usd"}]`. The losers stay in the den
with their sessions and a `task_judge` verdict, for backtracking.

### `budget_warning`
Build v3 §3 (`search.warn_budget`), before a design task's forks launch, when the build's
cost so far (the cost ledger) plus the forks' per-run caps exceeds the task's `budget_usd`:
`milestone`, `task_id`, `n`, `spent_usd`, `committed_usd`, `budget_usd`. The forks still
run (the build's cost cap stops workers once it is spent).

### `milestone_started`
Policy v2 only, once per milestone, when its first worker starts. `id`, `title`, `size`,
`n` (1-based position in the plan), `of` (milestones in the plan), `max_turns` and
`max_usd` (the per-run caps for this size, from the policy's `rules.yaml`).

### `milestone_finished`
Policy v2 only, when the tour moves past the milestone (or the build stops). `id`,
`title`, `n`, `of`, `node` (the milestone's best run: passed gate first, then coverage),
`passed` (that run passed the gate), `coverage` (0–1: share of the milestone's checklist
slice met by it), `slice` (`[met, total]` requirements), `attempts` (worker runs spent on
the milestone, retries and branches included), `usd` (their cost), `attempt_reasons`
(`[{"node", "reason", "passed", "no_progress"}]` per run, in order: why it ran, e.g.
`explore: approach spec_literal`, `continue from node 14`, `retry of node 15: gate failed
(coverage 0.00 < 0.80)`, `firmer retry of node 16: it changed no file in 2 turns`, `branch
tests_first from node 14 after 3 failed attempt(s)`). `passed: false` means the tour moved
on with a shortfall. Build v3 §4: `abandoned: true` when a backtrack left the milestone
(no mining pass runs against its code); `task_finished` has the same flag.

### `candidate_started`
`candidate` (1-based number within this build), `run` (worker run id, e.g. `r3-5`),
`strategy` (`tests_first`, `minimal`, `spec_literal`; for a refine, its parent's
strategy), `kind` (`branch` or `refine`), `parent` (parent node id, or `null`).
Policy v2 adds `milestone` (the milestone id the run builds) and `reason` (why it runs; see
`milestone_finished`).

The den node id does not exist yet when a worker starts; it is assigned when the worker
finishes. Use `candidate` (or `run`) to pair a start with its finish.

Build v3 §3 adds `task_id` (the task the run builds) and, for a design task's fork, `hint`
(0-based: which approach hint of the task it takes). `candidate_finished` repeats both,
plus `resume_fallback: true` when the head's session could not be resumed in the fork's
directory and the run was redone as a fresh session seeded with the head's diff; its
`gate` adds `task_passed`, `task_coverage`, `task_slice` and, for a design fork, `judge`
(`winner` or `loser`).

With background mining (`mining.async`), `fix_items` lists the late rules this run was
asked to fix: rules from a mining pass that finished while the previous worker ran, which
fail on the code this run starts from and which the arbiter blamed on the code. They are in
the run's prompt and its gate requires them (see `mining_finished`).

### `brief`
Build v3 §7b (`scoping:`), just before the `candidate_started` it belongs to: the scoped
brief the run's prompt carries. `candidate`, `run`, `unit` (the milestone id, or `task`),
`kind` (`milestone`, `retry`, `branch`, `review` or `task`), `model` (`null`: the CLI
default), `usd` (the scoper's calls; 0 when `cached`), `summary` (the problem statement's
first sentence, also the decision-log entry), `lint` (`criteria`, `cited`,
`failed_first`, `repaired`, `downgraded`, `dropped`) and `text` (the brief as the worker
sees it). A failed scoper call emits `unit`, `kind` and `error` instead, and the run goes
ahead without a brief. The brief is also stored on its node: `.den/briefs/<node>.md` and a
`brief` verdict.

### `candidate_finished`
`candidate`, `run`, `node` (den node id), `strategy`, `kind`, `parent`, `valid` (passed
the hard gates, i.e. all user tests), `score` (visible aggregate, 0–1), `user_tests` and
`acceptance` (`[passed, total]` or `null` when there are none; generated tests dropped by
the quality filter are not counted), `coverage` (scoring v2: weighted requirement coverage,
0–1, or `null` on an invalid candidate or a v1 scorecard), `dropped_tests` (generated test
ids, `test_gen_<k>.py::<name>`, excluded from scoring as broken at this point in the build;
`null` on a v1 scorecard), `usd` (this run's own worker cost, may be `null`; a resumed
Claude Code session reports cost cumulatively, and `usd` is that minus the parent
session's), `exit_reason` (`ok`, `max_turns`, `max_budget`, `timeout`, `error`,
`cost_cap`), `model` (the model the worker reported in its transcript, when known). Policy
v2 adds `milestone`, `reason` and `gate` (`{"passed", "coverage", "slice", "no_progress"}`:
the milestone gate, which passes when the run is valid, covers at least the policy's
`gate_min_coverage` of the milestone's checklist slice, its own tests pass and nothing that
passed before the milestone fails; `no_progress`: the run changed no file in a few turns).
With a rule set (policy v2 `ruleset:`, docs/design/rules.md), `rules` is `[passed, total]`
over the due, active, visible rules, and the gate also requires the milestone's rules and no
rule regressions.

### `candidates_rescored`
Scoring v2. A new candidate changed the set of dropped (broken) generated tests, so
earlier candidates were rescored to stay comparable. `dropped_tests` (the new set),
`candidates` (`[{"node", "valid", "score", "coverage"}]` for each rescored earlier
candidate). The new candidate's own `candidate_finished` already uses the new set.

### `rules_mined`
Rule set only (docs/design/rules.md). One mining pass: `mode` (`requirements`,
`interactions` or `edges`), `after` (the milestone whose kept code it was mined against, or
`review<k>`), `proposed`, `duplicates`, `active` (passed validation), `rejected`,
`rejected_by` (`{"a"|"b"|"c"|"d": n}`: which validation check rejected them), `failing_now`
(new active rules the current code fails), `total_active`, `usd` (model cost of the pass).
A pass whose model call failed has `error` instead; the build goes on. `after` is `null`
for the plan pass (`mining.schedule: planned`: mined before any code exists); its rules
wait as candidates (`pending`: how many) until milestone 1's code is there. `replaces`:
the promoted held rule this one replaces (see `rule_promoted`).

### `mining_started`
Rule set only. A mining pass begins (build v3 §2). `point` (`plan`, `after:<milestone>`
or `review<k>`), `after` (the milestone whose kept code it runs against; `null` for the
plan pass), `modes` (mining modes; empty for the check of the plan's rules after
milestone 1), `validate` (it finishes validating the plan's rules), `async` (`true`: it
runs in the background while the next worker builds), `active` and `caught` (rule totals
so far: active rules, rules the arbiter blamed on the code). The UI's "Rules" line shows
the running pass.

### `mining_finished`
The pass is done: `point`, `after`, `modes`, `async`, `active`, `caught` (totals now),
`mined` (new active rules), `validated` (plan rules activated), `code_wrong` (failing
rules the arbiter blamed on the code), `promoted` (held rules promoted), `fix_items` (with
`async`: late rules that become fix items for the next worker launched after this event),
`usd` (model cost of the pass), `wall_s`, and `error` when it failed. A background pass's
rules are used from the next worker launch on, never by a gate already under way.

Build v3 §7c adds, to both events, when they apply: `asked` (rules per mode the pass asked
the miner for: its share of the rule budget, `mining.rules_per_milestone`/`max_rules`),
`coalesced` (with `mining.coalesce`: the earlier mine points this pass replaced while it
waited; it mined against the latest head instead), `closing` (the closing pass; with
`mining.close_early` it starts when the last milestone's worker launches, and review round
1 scores on its rules). `point` may be `gate:<milestone>` (a pass for a failing gate,
`MiningController.gate_failing`). With `mining.combine`, `modes` with several entries were
one model call, and its `rules_mined` has `mode` `interactions_edges`.

### `mining_skipped`
A mine point that did not run because the rule budget is spent: `point`, `after`, `modes`,
`reason`, `coalesced`.

### `rule_promoted`
Rule set with `mining.promote_held` only. A held rule failed and the arbiter blamed the
code: the rule is shown to workers from now on, so the fix loop can use it, and a held
replacement is mined in the same area. `id`, `kind`, `title`, `milestones`, `version`,
`reason` (the arbiter's), `point` (where it happened: `after:<milestone>` or `review`).

### `rule_status`
Rule set only. Every status change of a rule: `id`, `kind`, `title`, `milestones`,
`visibility` (`visible` or `held`), `version`, `frm` (old status, `null` when proposed),
`to` (`candidate`, `active`, `disputed`, `retired`), `reason`.

### `review_round`
Rule set only. One round of the closing loop after the last milestone: `round`, `head` (the
node it reviewed), `mined` (new active rules), `rules`, `passed`, `failing`, `code_wrong`
(failing rules the arbiter blamed on the code), `held_code_wrong` (of those, held rules:
never sent to a fix), `new` (blamed rules not sent to a fix before), `rule_wrong`,
`ambiguous`, `fixed` (`true`: a fix run was kept and `new_head` is its node; `false`: it
was rejected; `null`: no fix was run), `stop` (on the last round: `clean`,
`no_new_failures`). A fix run is a milestone run with id `review<k>` (its own
`milestone_started`/`milestone_finished` and candidate events).

### `design_started`
Build v4 (`policies/v4`, `repo_factory/core/oneshot.py`). Stage 1's design search starts,
after the questions, the tech selection (`adr`) and the `assumptions`: `search` (`auto`
or `stand_in`, the `design.search` setting), `stand_in` (`true` when the stand-in pack is
used: V4-A's design search is not in, or `stand_in` was asked for).

### `design_finished`
Build v4. The design pack: `dimensions` (`[{"name", "door"` (`one_way`/`two_way`),
`"ambiguity"` (`real`/`none`), `"why", "forked", "chosen"` (option id), `"reason"` (why it
won), `"options": [{"id", "title", "gaps"` (rule-walk gap count, `null` if not walked),
`"pairwise"}]}]`), `forked` (the names of the forked dimensions), `chosen` (`{dimension:
option id}`), `gaps` (the combined design's rule-walk gaps, `[{"rule", "quote", "why"}]`),
`usd`, `wall_s`, `stand_in`, `revisions` (the design revise rounds after the combined
walk, `[{"round", "gaps_before", "gaps_after", "kept", "changed"` (dimensions rewritten),
`"closed", "opened"` (gap rule ids), `"why"` (for a discarded round)}]`; `gaps` is the
final walk's, after any kept revision). The `plan` event follows with one milestone, `build`, and the
pack's interface (with its rule map); then the scorecard is rebuilt against it
(`tests_generated`).

### `build_once_started`
Build v4. The one-shot build's single worker run starts (its `candidate_started` follows):
`milestone` (`build`, or `build.bt<n>` for the rebuild after design backtrack n), `epoch`
(0, or n), `max_turns`, `max_usd`, `timeout_s` (`build:` in rules.yaml), `model` (`CLI
default` unless `--worker-model` set one).

### `build_once_finished`
Build v4. It was scored: `milestone`, `epoch`, `node`, `session_id` (the Claude Code
session the repairs resume), `usd`, `wall_s`, `turns`, `exit_reason`, `model`, `valid`,
`user_tests`, `acceptance`, `coverage`, `held_back` (`[passed, total]` or `null`), `passed`
(its gate).

### `harden_round`
Build v4. One hardening round, twice when it repairs: `status: "repairing"` when its
repair run starts, then `status: "done"` with the verdict. `round`, `epoch` (0 for the
one-shot build's line, n after design backtrack n), `head` (the node it hardened),
`mined` (new active rules), `failing` (active rules failing on the head, after
arbitration), `code_wrong` and `held_code_wrong` (failing rules the arbiter blamed on the
code; of those, held ones, never shown), `promoted` (held rules promoted to visible),
`design_flaws` (failing rules with an open `design_flaw` verdict), `design` (the
controller's decision for each: `{"option": "backtrack_design" | "repair", "rule",
"dimension", "option_id", "reason", ...}`), `repaired` (rules sent to the repair, 0 if
none), `node` (the repair run), `accepted` (`true`: kept, `new_head` is its node; `false`:
discarded, the head stays; `null`: no repair), `why` (the strict-improvement verdict in
words), `before` and `after` (`{"rules", "held_back", "tests": [passed, total], "valid",
"coverage"}`), `stop` (on a line's last round: `no new failing rules`, `max rounds`,
`design backtrack`, `budget`, `worker cap`, `no rule set`). A repair is a milestone run
with id `repair<k>` (`repair<k>.bt<n>` after a backtrack), with its own
`milestone_started`/`milestone_finished` and candidate events. Mining in a round is a
`mining_started`/`mining_finished` pair with `point` `harden<k>` (`harden<k>.bt<n>`).
Round 1 also has `design_rules` (`{"items", "gaps", "active", "failing"}`) when the design
pack's gaps and rules were made rules (see `design_rules`).

### `design_rules`
Build v4. Round 1 of hardening (of each build line), before its mining: the open gaps of
the final design walk and the rules the design was walked against, not made rules yet, at
most `harden.design_rules` in all, became visible rules through the miner, validation and
(when they fail) the arbiter. `point` (`harden1`, `harden1.bt<n>`), `epoch`, `items` (asked
for), `gaps` (of those, open gaps), `active` (passed validation), `rules` (their ids),
`covered` (the design rule ids, e.g. `R11`, with an active rule), `failing_now` (failing
on the head), `usd`, `wall_s`, `error` (a failed call). Its `rules_mined` has `mode:
"design"`.

### `backtrack_design`
Build v4. A design flaw switched the design pack: `number`, `rule`, `title`, `spec_rule`,
`decision` (the arbiter's), `dimension`, `from` and `to` (option ids), `at` (the head),
`reason`, `chosen` (the pack's choices after the switch). The rebuild follows
(`build_once_started` with `epoch: number`), then its hardening. A second
`backtrack_design` with `status: "done"` closes it: `rebuild` (the rebuild's node), `final`
(the rebuilt line's head), `kept` (delivered instead of the old head: valid and passing at
least as many rules plus held-back tests), `why`, `old`/`new` (their counts).

### Build v5 (`v5_*` and friends)
Build v5 (`docs/design/build-v5.md` §3, `repo_factory/v5/`) reuses `build_started` (with
`pipeline: "v5"`, `cap_usd` (null: no global cap, the default), `prebuilt`, `stages`), `candidate_started`/`candidate_finished`
(candidate 1 is the build, `milestone: "build"`; each repair, fix-up and fork session is
another candidate, `milestone: "repair<k>" | "fixup<k>" | "alt-<item>"`; `node` is the
candidate number), `rule_status` (the verifier's scenarios in the rule store; also
`scenario`, the verifier's id), `question`/`answer` (`interpret: ask`), `delivered` (also
`alt_branches`) and `build_finished` (also `cost_by_stage`). New types:

- `v5_stage`: `stage` (`scope`, `build`, `claims`, `verify`, `oracle`, `repair`,
  `reverify`, `readiness`, `deliver`; logs from before 2026-10-16 also have `mutants`, a
  retired stage, kept in their strips after `verify`), `status` (`started`, then `done`,
  `capped` (it ran into its cap or the build's: `why`), `skipped` (not in the policy's
  `stages:`)), and on the closing line `usd` (the
  stage's spend in this run of it), `tokens_in` and `tokens_out` (the stage's tokens in this
  run of it, from the ledger's `in_tokens`/`out_tokens`; absent on logs written before
  tokens were recorded) and `wall_s`. `repair` and `reverify` run once per round. Also
  `prompts` on the closing line: `{prompt id: version}` of every registered prompt the stage
  rendered (`repo_factory/custom_prompts.py`; absent when none was).
- `prompt`: the first time a registered prompt is rendered at a version in this build: `id`,
  `repo`, `version` (the first 12 hex digits of the SHA-256 of the composed template), `n`
  (the owner's store version in use, 0 for never customised), `custom`, `template`
  (`default` | `override`), `additions`, `house_rules` (booleans) and `stage`.
  `build_started` carries `prompts`, `{id: version}` of the policy's prompts at the start.
- `claims`: `n` (merged claims), `ambiguities`, `samples`, `dedupe` (`llm` or `text`),
  `lessons` (how many were injected).
- `findings`: after the panel (`stage: "oracle"`) and after each re-verify (`stage:
  "reverify"`): `confirmed` (new defects), `interpretation`, `retired`, `blocked`,
  `scenarios` (active), `failing`, and after a re-verify `new_failing`.
- `mutation` (retired with the mutants stage; older logs have it, and nothing reads it):
  `score` (killed / applied), `survivors`, `applied`, `killed`, `proposed`, `new_scenarios`
  and `new_failing`.
- `conflict`: one per regression after a repair: `round`, `x` (the finding ids of the commit
  that broke it), `y` (the broken check), `group` (`scenarios`, `held`, `tests` (the
  builder's own), `user`), `verdict` (the pair panel's: `compatible`, `x_wins`, `y_wins`,
  `undecidable`), `action` (`fixup`, `keep_x`, `revert_x`), `x_rank`, `y_rank` (the
  authority order, 1 highest).
- `repair_round`: `status: "repairing"` (`round`, `sent`), then `status: "done"`: `fixed`,
  `regressions`, `conflicts` (counts), `reverted` (finding ids whose commits were reverted),
  `accepted`, `why`, `session` (`resume`: the builder's session was forked, its cache likely
  warm; `fresh`: a new session); or `status: "skipped"` with `why` (the reserve rule). A
  `v5_stage` `stage: "reverify"` `status: "skipped"` says `why` when no further round could
  repair what re-verify finds (`repair.max_rounds`, or under a global cap the reserve rule).
- The simple flow (`policies/simple`, `build_started` `flow: "simple"`; Addendum E of
  `docs/design/build-v5.md`): `v5_stage` `stage` is `plan`, `build`, `check`, `fix` or
  `deliver` (today's names are read in old logs: scope is the plan; claims, verify, oracle
  and readiness the check; repair and reverify the fix); `check` and `fix` repeat, once per
  round. `build_started` may have `from_round` (`repo-factory build --prebuilt`: the round whose plan
  and build this one took). `claims` has `stage: "plan"` and `ambiguities: 0`. `findings` has
  `stage: "check"` (and `round` after a fix round). `fix_round` reads as `repair_round`
  (`status: "fixing"`, then `"done"` with the same fields, plus `security`, `defects` and
  `readiness`: what the one findings list held of each), or `"skipped"` with `why`.
  `fix_loop`: the loop ended: `rounds`, `why` ("fixed N, open M; why"). `salvage`: a round
  the conflict protocol left with a regression was rebuilt without the commits blamed for
  it and kept: `round`, `dropped` (commits), `findings` (theirs, open again), `fixed`. `security` gains
  `step: "reprobe"` (`round`, `probed`: the findings the round fixed, re-probed). A revise
  round of a simple-flow build has `kind: "revise"`, `flow: "simple"` and the same steps.
- The lean flow (`policies/lean`, `build_started` `flow: "lean"`; `repo_factory/v5/lean.py`):
  `v5_stage` `stage` is `scope` (the brief), `build`, `harden`, `check`, `fix` or `deliver`;
  a policy's own prompt stages (`add_stages`) appear under their names. `build_started` of
  every flow has `flow` and `flow_stages` (the stage list as data: `name`, `kind` (`prompt`
  or `code`), `inputs`, `outputs`, `role`, `cap_usd`, `timeout_s`, `max_turns`, `holds`,
  `loop`, `record`, `cell`, `label`; `repo_factory/v5/stages.py`). `lean_check`: one measure of
  the release checks: `by` (`check`, `fix`, `fix-undone`), `holds` (what would hold:
  `security:<id>`, `check:<item>`, `flow:<id>`, `secrets:<file>:<line>`, `rea:…`),
  `completeness` `[done, total]`. `fix_round` (`round: 1`) as in the simple flow, `status`
  `"fixing"` then `"done"` with `sent`, `fixed`, `accepted`, `why`.
- `process_files` (every flow, at delivery): repo-factory's own files found in the delivered tree
  and moved to the round's `process/` folder: `moved` (paths), `to`.
- `interpretation`: `item` (an ambiguity id `a<n>`, `i-<rule>` for a split panel, or
  `i<round>-<n>` for an undecidable pair), `mode` (`auto` or `ask`), `chosen`, `forked`,
  `implements` (the reading the verifier found in the build), `stakes`. Addendum A adds
  `kind` (`binary` or `complex`); a binary item is an operator setting: `item` is its
  setting id `s<n>`, `mode: "setting"`, `chosen` its default, `setting` its key, `status`
  (`proposed`, `exposed`). `findings` and `repair_round` (done) gain `settings` (the panel's
  setting classes; the keys a round added). `forked` is always false: the fork is retired.
- `prediction` (retired with `p_hidden` on 2026-10-16; older logs have it, and nothing reads
  it): `p_hidden`, `interval80`, `expected_hidden_failures_per_100`, `features` (as the old
  rounds' `prediction.json`).
- `scope` (the scope stage, `v5_stage` `stage: "scope"` before `build`): `decision`
  (`scoped`: `SCOPE.md` was written; `pass`: the input is used as the spec; `malformed`: the
  reply had no usable scope, the input is used as the spec), `why`, `mode` (`auto`,
  `always`, `off`), `verdict` (the heuristic's: `idea`, `borderline`, `spec`), `words`,
  `called` (whether the no-tools call ran), `flows`, `dod`, `must`, `settings` (counts in
  `SCOPE.md`), `shared_base` (false for a scoped build: its prompt isn't the vanilla one, so
  it can't share the vanilla base build), `path`. `candidate_started`/`finished` of the build
  have `strategy: "scoped"` then. `findings` gains `incomplete` (missing or partial flows
  and features, sent to repair without the panel), `environment` (scenarios that fail only
  in the harness's sandbox: never repaired) and `completeness` (`[flows working end to end,
  flows in the definition of done]`: SCOPE.md's, or for an input that passed through, the
  flows drawn from its own text, when `dod` > 0 on a `pass`).
- `security` (the readiness stage, Addendum C, `v5_stage` `stage: "readiness"` between
  `reverify` and `deliver`; the stage runs as several steps, so its `v5_stage` lines repeat):
  `step: "review"`, `ran` (the review wrote its report), `critical`, `high`, `medium`, `low`
  (findings by severity), `confirmed` (open: its probe fails in the harness's run, or it was
  reproduced by hand only), `unconfirmed` (its probe passes in the harness's run), `probes`,
  `audit` (the dependency audit: `clean`, `findings`, `unavailable`, `no_dependencies`).
- `readiness`: `step: "checks"`, `"commands"` (the harness ran the README's commands as
  written: also `ran`, and the status of `commands`, `tests_green`, `restart_now`) or
  `"final"` (the delivered tree's scorecard) with `green`, `amber`, `red`, `na` (counts;
  `final` also `security`: the findings not fixed, by severity); or `step: "repair"` with `status: "repairing"` (`sent`), `"skipped"` (`why`,
  `sent`) or `"done"` (`sent`, `fixed` (scenarios now passing plus items fixed without a
  test), `regressions`, `accepted`, `why`, `updated` (scenarios the repair updated because
  they encoded an old unsafe default)). The readiness round's sessions are
  `candidate_started`/`finished` with `run: "ready-repair1"` (and `ready-fixup1`,
  `ready-undo1`); its `conflict` events carry `round: 1`.
- `build_started` (v5) carries `stages` (the policy's list): the UI's stage strip shows the
  optional stages (`scope`, `readiness`) for runs that plan them; `stage_caps` (the stage
  caps set, `{}` by default: none) and `continues` (`r<n>`: the earlier round whose stopped
  build this one continues, `carry_from`).
- `build_partial` (v5): the build session stopped on turns, time or a cap and left a tree
  that builds and whose tests run, so the stages go on with it: `stage`, `exit_reason`,
  `why`, `files`.
- `v5_stage` with `status: "failed"` and `why`: the stage could not go on (`StageFailed`);
  the build then ends with `build_finished` `status: "error"` (below).

### `build_resumed`
A v5 round continues from its saved state in a new process (`repo-factory build --resume <round>`,
used by `repo-factory loop` after a crash or a stop): `pid` (the new process), `done` (the steps
already finished). The fold sets the build back to `running` with this pid (and clears an
earlier `build_finished` error of the same round). `retries`: the stage that failed last time
(it runs again).

### `delivered`
`node` (the delivered node, or `null` if no candidate was valid; the branch then holds
the seed), `branch` (`brb/<task>`), `out`, `report` (path to REPORT.md), `held_back`
(`{"passed", "total", ...}` for the delivered node, or `null`), `changed` (`false` only on
a revise in which no new candidate was valid, so the branch is unchanged). A revise also
has `commit` (the new commit hash, or `null`). With a rule set, `rules` is `{"visible":
[passed, total], "held": [passed, total]}` for the delivered code on every active rule.

### `verdict`
`verdict` (`accept`, `reject` or `edit`), `note`, `source` (`human` or `sim-owner`), `node`.
It carries the `build` id of the delivery it judges. `repo-factory verdict` appends one to the
latest build. `repo-factory revise` first appends an `edit` verdict with its note to the previous
build, then starts its own run.

### `call_started`
One model call begins (`repo_factory.costs.ledger.track`). Every `call_claude` (the scoper, judge,
pairwise judge, miner, validator, arbiter, controller, planner, tech selection, plan review,
builder, simulated owner) emits one while a build's ledger is active, from whichever thread
makes the call. Fake builds emit them too, for the fixture-backed scoper, pairwise judge,
rules engine and controller, and for the plan and test-generation steps. Fields: `call`
(an id unique within the build, e.g. `c12-7`, pairing the start with its finish), `caller`,
`purpose`, `model` (`null`: the CLI default), `category` (the cost ledger's), and where they
apply `milestone`, `node`, `unit` (a brief's milestone or task), `kind` (a brief's kind),
`task_id` and `n` (the pairwise judge: the design task and how many forks it judges), and
`background: true` when the call runs off the main thread (a background mining pass).

A call made inside a span of the same caller on the same thread (for example the `claude -p`
of a miner call, or each vote of a pairwise tournament) is folded into that span: no events of
its own, its cost adds to the span's. A call with retries is one start and one finish.

### `call_finished`
The call is done: `call`, `caller`, `purpose`, `model`, `category`, `usd` (its own cost and
any folded calls'; `null` when unknown or failed), `wall_s`, `ok`, plus `attempts` (when it
took more than one) and `error` (when `ok` is false). `repo-factory ui` leaves both `call_*` types out
of the raw events it sends the page; the folded state's `activity` (below) carries them.

### `build_finished`
`status` (`ok` or `error`), `cost_usd` (the run's full cost: every model call in the cost
ledger `<den>/ledger.jsonl`, i.e. workers plus builder, judge, planner, miner, validator,
arbiter and simulated owner; a den without ledger records falls back to the workers),
`worker_cost_usd` (the sum of each worker run's own cost, which `cost_usd` used to be),
`cost_breakdown` (`{"workers", "mining", "judging", "planning", "other"}` in USD, when the
ledger has records), `wall_s`, `worker_models` (the models the workers reported, when
known), and `error` (a message, only when `status` is `error`). A crash, an exception or Ctrl-C inside a
build still writes `build_finished` with `status: "error"` before the process exits.
v5: a stage that could not go on (`StageFailed`: the Claude login failed, or the build stage
stopped with no usable tree) also ends `error`, with `stage` (which one) and `failure`
(`auth`, `stopped` or `error`); `error` is `"<stage> stage: <reason>"`. Nothing is
delivered; `repo-factory loop` holds an `auth` failure at once (a retry would fail the same way).

### Bench arms (`<bench>/runs/<tag>/<pack>/s<seed>/<arm>/events.jsonl`)
Written by `hsuite/arms.py` (build v5 §4.2), one log per arm dir; `task` is the pack id and
`build` the seed. `fold` ignores them.

- `arm_started`: `arm` (`base`, `self-review`, `fresh-review`), `tag`, `pack`, `seed`, and
  `base` (the base run's dir, review arms only).
- `arm_round`: a review round finished: `arm`, `round`, `usd` (the round's own cost), `total`
  (cumulative: base plus the arm so far), `changed` (the round changed a file), `exit`,
  `review_complete` (its final message said "REVIEW COMPLETE").
- `arm_finished`: `arm`, `usd` (the arm's own cost), `total` (review arms), `stop`
  (`ceiling`, `idle`, `max_rounds`, `resume_failed`), `rounds`; for `base`: `exit` and
  `profile` (the fingerprint hash).

## The folded `activity` state

`fold` adds `activity` to the state (also in `repo-factory status --json`): what the build is doing
now, bounded however long the log grows (only the calls running now, the last 8 finished
items, counts and sums; never one entry per call).

- `phase`: `questions`, `tech selection`, `planning`, `plan review`, `designing`, `tests`,
  `building`, `closing review`, `hardening`, `delivering`, `done` (or `failed` /
  `stopped`). It follows the furthest event so far; before the workers start (and before
  a build v4 design search), a running `tech`, `planner`, `plan_review` or
  test-generation call names it.
- `stage` (build v4, while it runs; else `null`): where the build is in words, for the
  phase pill: "Designing", "Building (one session)", "Hardening round 2: mining and
  checking rules", "Hardening round 2: repairing 3 rules", "Design backtrack 1: interface
  -> interface.o2".
- `running`: one entry per item under way, oldest first, each with `kind` (`worker`,
  `mining`, `call`), `label` (in words: "Worker: m4 · spec_literal", "Mining interactions in
  the background", "Writing brief for m3.t2", "Judging 3 candidates for m2.t1 (pairwise)",
  "Controller deciding after m5") and `started`. Workers add `candidate`, `milestone`,
  `task_id`, `strategy`, `hint`; mining passes `point`, `after`, `modes`, `async`; calls
  `caller`, `purpose`, `model`, `milestone`, `unit`, `task_id`, `n`, `background`. Empty once
  the build is over (or interrupted). Workers come from `candidate_started` without a
  `candidate_finished` and mining passes from `mining_started` without a `mining_finished`,
  so logs written before the `call_*` events still show them (`inferred: true` marks a log
  with no call events). A design task's forks report only after the judge, so while its
  pairwise call runs they are not listed as running.
- `recent`: the last 8 finished items, newest first: `kind`, `label`, `finished`, `wall_s`,
  `ok`, `usd` (and `error`).
- `calls`: `started`, `finished`, `failed`, `by_caller` (`{caller: {"calls", "usd"}}`).
- `progress`: `milestones` (`[moved past, total]`), `milestone` (the running one), `tasks`
  (`[done, total]` for it, or `null`), `fraction` (0–1 overall).
- `cost`: `total` and `breakdown` (`workers`, `mining`, `judging`, `planning`, `other`): the
  build's own `cost_breakdown` once it finishes; before that the workers' costs plus the
  `call_finished` costs (or, for an older log, the briefs' and mining passes' costs).
- `forks`: the latest design task's forks (the running one, else the last judged): `task_id`,
  `title`, `milestone`, `status`, `judging`, `winner`, and `forks` (`[{"hint", "hint_text",
  "candidate", "node", "status" (pending, running, built, done), "started", "valid", "usd",
  "rules", "outcome", "points", "chosen"}]`; `chosen`: the build continues from it).

Each backtrack in `controller.backtracks` also gets `text`, e.g. "Backtracked to before m2:
add branch_id to checkout".

Build v4 adds `v4` to the state (else `null`): `design` (the `design_finished` fields, or
`{"running": true}`), `builds` (each one-shot build: `build_once_started` fields, then
`build_once_finished`'s), `build` (the latest finished one), `rounds` (each
`harden_round`, its latest status), `backtracks` (each `backtrack_design`), `stage`.

Build v5 adds `v5` (else `null`): `stages` (stage -> its latest `v5_stage` fields), `claims`,
`findings` (the latest), `rounds` (each `repair_round`, its latest status),
`conflicts`, `interpretations`, `scope`, `security` (the review's counts),
`readiness` (`checks`, `repair`, `final`), `planned` (`build_started`'s `stages`), and
`stage` (the activity line's words). The phases gain `verifying` and `repairing` (between
`hardening` and `delivering`).

## The cost ledger (`ledger.jsonl`)

Next to the event log, `<den>/ledger.jsonl` has one JSON line per model call of every build
and revise (`repo_factory.costs.ledger`): `ts`, `build`, `caller` (`worker`, `builder`, `judge`,
`planner`, `tech`, `plan_review`, `miner`, `validator`, `arbiter`, `sim_owner`), `purpose`,
`category` (`workers`, `mining`, `judging`, `planning`, `other`), `model`, `in_tokens`,
`out_tokens`, `usd` (this call's own cost; for a resumed worker session the cumulative cost
minus the parent's; `null` for a failed call), `wall_s`, `ok`, and where they apply `node`,
`milestone`, `session_usd` (a worker session's cumulative cost) and `base_usd` (the parent's,
subtracted), `error`. `repo-factory costs --ledger <taskdir> [--summary]` prints it.

Build v5 tags every record with `stage` (`build`, `claims`, `verify`, `oracle`, `repair`,
`reverify`, `deliver`; older ledgers also `mutants`) and adds callers: `claims` and
`verifier` (category `mining`), `oracle` and `pair` (`judging`), `postmortem` (`other`); the build and
repair sessions are `worker`. A shared base build (`prebuilt`) is one `worker` record with
`prebuilt: true` and the base's cost.

## Liveness

While a build or revise runs, `<den>/build.pid` holds its pid. It is removed on exit,
whether the run succeeded or failed. If the latest build has no `build_finished`:

- `build.pid` exists and the pid is alive: the build is running.
- otherwise the process was killed (for example by SIGKILL or a power loss) before it
  could write `build_finished`. `load_state` reports this as `status: "interrupted"`,
  using the `pid` in `build_started`.

## Questions without a terminal (`--owner queue`)

`repo-factory build --owner queue` does not read stdin. For each question it writes
`<den>/human/<task>.<qid>.question` (the question text, then the spec) and polls for
`<task>.<qid>.answer`. When the answer arrives, or after `$REPO_FACTORY_ANSWER_TIMEOUT`
seconds (default 3600), it deletes the `.question` file. `question`/`answer` events are
the same as in terminal mode.

Answer with `repo-factory answer <taskdir> <qid> "text"`. It writes the answer file atomically,
and only for a question that is waiting. Empty text means "no preference, use your
judgement" and is recorded as no answer (`text: null`), the same as Enter at the
terminal.

## Example

```json
{"v": 1, "ts": 1791012186.1, "task": "repo-07", "build": 1, "type": "build_started", "spec": "/w/repo-07-kv-list/spec.md", "taskdir": "/w/repo-07-kv-list", "title": "Add a list command to kv.py", "policy": "v1_1", "harness": "claude-code", "owner": "human", "out": "/w/repo-07-kv-list/out", "pid": 4242, "builder": "claude", "builder_model": "claude-sonnet-5", "worker_model": "claude-sonnet-5"}
{"v": 1, "ts": 1791012190.4, "task": "repo-07", "build": 1, "type": "question", "id": "q1", "text": "What output format should list use?", "n": 1, "of": 2}
{"v": 1, "ts": 1791012201.0, "task": "repo-07", "build": 1, "type": "answer", "id": "q1", "text": "key=value, sorted"}
{"v": 1, "ts": 1791012260.2, "task": "repo-07", "build": 1, "type": "tests_generated", "visible": 8, "held_back": 3}
{"v": 1, "ts": 1791012260.3, "task": "repo-07", "build": 1, "type": "candidate_started", "candidate": 1, "run": "r1-5", "strategy": "minimal", "kind": "branch", "parent": null}
{"v": 1, "ts": 1791012480.9, "task": "repo-07", "build": 1, "type": "candidate_finished", "candidate": 1, "run": "r1-5", "node": 6, "strategy": "minimal", "kind": "branch", "parent": null, "valid": true, "score": 0.93, "user_tests": [4, 4], "acceptance": [7, 8], "coverage": 0.88, "dropped_tests": [], "usd": 0.21, "exit_reason": "ok"}
{"v": 1, "ts": 1791012900.0, "task": "repo-07", "build": 1, "type": "delivered", "node": 6, "branch": "brb/repo-07", "out": "/w/repo-07-kv-list/out", "report": "/w/repo-07-kv-list/out/REPORT.md", "held_back": {"passed": 3, "total": 3, "rate": 1.0}, "changed": true}
{"v": 1, "ts": 1791012900.1, "task": "repo-07", "build": 1, "type": "build_finished", "status": "ok", "cost_usd": 1.12, "wall_s": 714.0}
```

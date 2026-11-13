# repo_factory v5: detailed design

> **A design record.** This is the v5 pipeline's design as it was written, kept because it
> explains why the stages are what they are. The benchmark it describes (`bench`, the spec
> packs, `hsuite/`) is not part of this repository; the pipeline itself is
> `repo_factory/v5/`, and [../first-build.md](../first-build.md) says how it runs today.

*2026-10-04, round 2. Sources: `REVIEW.md`, review 6's verdict, run 10's den (`events.jsonl`, `ledger.jsonl`, `cache/rules/`), `review6/prior` and `review6/_work`, and the code on `build-v4`. No builds, tests or model calls were run. Every dollar and time figure for something not yet built is **my estimate**; statements marked "opinion" are mine and unverified.*

## 0. The design in one page

1. **One objective.** A *spec pack* per spec: the spec, a hidden driver contract, a frozen scenario suite validated by mutation testing and two reference builds, and the owner's rulings on the ambiguous readings. `bench` builds, adapts, scores and compares with no judgement call in the loop. The blind LLM review stops being the scoreboard.
2. **One worker profile.** Every contestant (vanilla, vanilla plus review, v5, reference builds, the verifier) launches through one function with one sandbox, one tool set and a clean per-run Claude config dir. Each run stores a fingerprint from its transcript's init event; `bench compare` refuses to compare runs whose fingerprints differ.
3. **Shared base builds.** v5's build stage *is* the vanilla build: the same prompt, byte for byte. So one base build per (pack, seed) is forked into every arm: scored as is (vanilla), resumed with self-review (budget-matched baseline), reviewed by a fresh session (second baseline), or run through v5's post-build stages. Arms are paired on the same artifact, which removes build-to-build variance from the comparison and cuts cost by about a third.
4. **v5 = vanilla build → claims → fresh full-tools verifier → oracle panel → repair → re-verify → calibrated delivery.** Nothing is decided before code exists. Nothing weaker than the builder decides what is correct. A repair is never thrown away because two of the factory's own oracles disagree; the pair is adjudicated.
5. **Self-improvement = a lessons file, evaluated out of sample.** A post-mortem turns hidden failures into at most three short, domain-free lessons per run. A lesson is never injected when building the pack it was born on, so every dev score is leave-one-pack-out by construction. Promotion needs held-out packs. The mutable surface then widens in tiers: knobs, prompts, stage list.
6. **Budget (estimate): $450–510 over two weeks**, not the ~$285 in `REVIEW.md` §7. That table under-counted suite building, adapters and the second baseline. A lean variant is about $330 (§6.3).

What I found in run 10's den that shapes the design:

| Fact (run 10) | Source | Consequence |
|---|---|---|
| 24 of 56 rules were retired before scoring: 11 by literal traceability (a), 13 by the second reading (b). 9 of those 13 were hold, transit and fee rules rejected because the *test setup* wrote `'standard'` in lowercase. | `cache/rules/.../rules.json` | The no-tools miner writes tests blind, and the validator throws away the claim with the mechanics. The verifier must run its own scenarios (full tools), and the correctness check must judge the claim in prose, not the test code. |
| The retired rules cover exactly the areas of B's top bugs (holds, transit). | same, vs `VERDICT.md` | The factory's filter removed its own best probes. |
| At delivery the factory believed rules 30/32, held-back 7/9, acceptance 22/22. | `harden_round`, `delivered` events | Still blind. Calibration needs an estimator that isn't the pass rate of its own checks. |
| Both failing rules (q25, q27) and the acceptance test that blocked the repair trace to `answer:assumptions`: text the factory wrote itself. | `rules.json`, `harden_round` | The conflict was between two derivatives of the factory's own assumption. No authority order existed to settle it. |
| The build session: 22 turns, $3.36, 13 minutes. All 24 non-worker calls ran on `claude-sonnet-5`. | `ledger.jsonl` | The build is already vanilla-priced. $8.80 of $12.66 went to stages around it, all steered by the weaker model. |
| `bench/contestants.py` still launches vanilla with `VANILLA_TOOLS`/`VANILLA_BASH`, unsandboxed. | code | The tools mismatch is still in the code path `bench --vanilla` uses. |

---

## 1. Principles and non-goals

### 1.1 Principles

| # | Principle | Mechanism |
|---|---|---|
| P1 | The objective is hidden and frozen. | Packs live outside every sandbox mount; suites are hash-frozen; a change is a version bump and a re-score. |
| P2 | Add to the strong model; never box it in. | The build prompt is the vanilla prompt. No design brief, no pre-code tests, no contract text. |
| P3 | Spend extra budget only after code exists. | Every v5 stage after the build takes the built repo as input. |
| P4 | Nothing weaker than the builder decides correctness. | Claims and the oracle panel run on the builder's model. Cheaper models only do mechanics (mutant patches, triage). |
| P5 | Evidence has an authority order. | §3.5. A lower-authority oracle can't veto a higher one; equal-authority conflicts are adjudicated as a pair. |
| P6 | Compare only identically configured runs. | One launch function, a stored fingerprint, a refusing comparator (§4). |
| P7 | Pre-register, then run. | `bench prereg` hash-stamps criteria; `compare --prereg` evaluates them mechanically. |
| P8 | Policy is data. | `policies/v5/` holds knobs, prompts and lessons. Python holds a fixed stage library. The factory mutates data, and the bench promotes it. |
| P9 | Architecture freeze. | No new stage enters v5 without an experiment in §6.2 or a later pre-registered one. |

### 1.2 Non-goals for these two weeks

- Beating vanilla on build quality in stage 1. By construction v5's build equals vanilla's.
- A security gate for public release. Needed before issue #12 goes live; not designed here.
- Concurrency and multi-process scenarios in the hidden suites. Review 6 found concurrency "is not the differentiator". They stay in a `heavy` class, excluded from the v1 score.
- Non-Python stacks. The suite runner is pytest in bubblewrap; packs say `language: python`.
- Mutating Python by machine. Tier 3 (§5.7) makes the stage list data; stage code stays human- or worker-written.
- Deleting legacy code. Freeze now, delete after decision day (it costs worker days and test churn for no measured gain).

### 1.3 Frozen, deprecated, kept

**Kept and reused as is** (details in §3.9): `repo_factory/harness/{claude_code,sandbox,netproxy,base,fake}.py`, `repo_factory/den/{ledger,artifacts,costs}.py`, `repo_factory/events.py`, `repo_factory/ui/`, `repo_factory/progress.py`, `repo_factory/problems/repo/sandbox.py` (`candidate_venv`, `run_pytest`), `repo_factory/problems/repo/rules/{model,store,score,llm}.py`, `repo_factory/problems/repo/task.py`, `repo_factory/oracle/{human,sim_owner}.py`, `build.py`'s `make_branch`, `lifecycle`, `CostCapHarness`, and the CLI commands `new`, `build`, `status`, `answer`, `verdict`, `ui`, `costs`.

**Frozen** (no edits; kept runnable so runs 2–10 can be reproduced):

| Path | Why frozen |
|---|---|
| `policies/v2`, `policies/v3`, `policies/v4` | History. `build.DEFAULT_POLICY` stays `"v3"` until decision D2, then becomes `"v5"`. |
| `repo_factory/core/{session,policy,actions,state,orchestrator,oneshot,budget,workspace}.py` | The v1–v4 executor. v5 doesn't go through `Session`: it drags in scorecards, checklists, `check.py` and `_contract.md`, all of which change the build prompt. |
| `repo_factory/problems/repo/{__init__,builder,runner,checklist,milestones,planner,decide,techselect,v4report,fingerprint,check}.py`, `strategies/` | v2–v4 scoring and prompts. |
| `repo_factory/scoring/`, `repo_factory/problems/tsp/` | TSP history. `tsp/contract.py`'s `_bwrap_cmd`, `isolation`, `_base_python` remain imported by the sandbox. |

**Deprecated** (add a `DeprecationWarning` on import and a line in `README.md`; delete after D2 if v5 continues):

| Path | Replaced by |
|---|---|
| `repo_factory/hibernate/`, `repo_factory/replay/`, CLI `hibernate`, `adopt`, `replay`, `rescore` | §5: lessons loop and shared-build re-runs. Replay never scored a repo candidate. |
| `repo_factory/prompteval/`, CLI `prompteval`, `repo_factory/lab/prompts/v3/`, `repo_factory/lab/promptpack.py` | `policies/v5/prompts/` plus bench promotion. |
| `repo_factory/core/{controller,search,mining,briefs,tracking}.py`, `problems/repo/{scoper,pairwise,design_link}.py`, `problems/repo/design/` | Nothing. Pre-code search, forks, the swap, the pairwise judge and the work graph are cut. |
| `problems/repo/rules/{miner,validate,arbiter,engine,schedule,loop,report,__main__}.py` | `repo_factory/v5/{claims,verify,oracle}.py`. Only `validate.validate_runs` (two-order determinism) is still called. |
| `bench/contestants.py`: `VANILLA_TOOLS`, `VANILLA_BASH`, `vanilla_command`, `run_vanilla` | `repo_factory/hsuite/arms.py`. **Delete these four now**, not after D2: they are the mismatch. `bench rubric --vanilla` reuses a base build from `arms.base_build`. |
| `bench/{run,rubric,judge,digest,scoreboard,checks}.py` as the scoreboard | Kept, renamed to the `bench rubric` subcommand: a secondary, non-gating quality read. |
| CLI `gen-tasks`, `forage`, `round`, `eval`, `score`, `path`, `export`; `scripts/lab/cost_replay.py`, `scripts/lab/mining_replay.py` | TSP-era. |

---

## 2. The objective: the hidden-suite harness

New package `repo_factory/hsuite/`. Two directories outside the repo, both under `/home` and therefore invisible inside the worker sandbox (`harness/sandbox.py` puts a tmpfs over `/home` and binds back only the workspace, the config dir and the uv caches):

- `$REPO_FACTORY_PACKS` (default `~/factory-packs`): a **private** git repo. Never the public repo: full-profile workers have `WebSearch` and `WebFetch`.
- `$REPO_FACTORY_BENCH` (default `~/factory-bench`): runs, comparisons, pre-registrations, history.

### 2.1 Pack layout

```
~/factory-packs/<pack>/
  pack.yaml                 # metadata, status, hashes, gates (below)
  spec.md                   # the ONLY file a builder ever sees
  owner.md                  # hidden: the owner's intent per known ambiguity (feeds the sim owner)
  interpretations.yaml      # hidden: disputed readings and the owner's rulings
  suite/
    driver.py               # abstract Driver: domain operations the scenarios call
    contract.md             # prose semantics of each operation, for the adapter writer
    conformance.py          # public-to-the-adapter-writer smoke scenarios, one per operation
    scenarios/s_*.py        # hidden scenarios
    manifest.json           # one row per scored check
  refs/
    ref-a/ ref-b/           # two independent reference builds: build/, adapter.py, run.json
    mutants/<ref>/m*.patch  # semantic mutants
    kill.json               # mutant x row kill matrix
  baselines/                # symlinks into ~/factory-bench for this pack's baseline runs
  provenance.json           # every model call that made this pack: role, model, session, usd, date
  validation.json           # gate results at freeze time
```

`pack.yaml`:

```yaml
id: lending
version: 2                      # bump on any suite/ change; all stored builds are re-scored
status: frozen                  # draft | candidate | frozen | retired
split: dev                      # dev | heldout
contaminated: true              # lessons were hand-written from this spec (lending only)
language: python
spec_sha256: 5b1c...
suite_sha256: 9e02...           # hash over suite/ (sorted paths + contents); checked at every score
rows: {settled: 61, ruled: 9, either: 3, heavy: 6}
domain_terms: [member, loan, hold, copy, branch, fee, renewal, transit, tier, librarian]
gates:                          # frozen only if all hold (validation.json has the numbers)
  mutation_score_min: 0.80
  flaky_rows: 0
  ref_core_pass_range: [0.60, 0.95]
  settled_rows_min: 30
vanilla: {mean_usd: 3.62, mean_wall_s: 1180, n: 5, profile: full-v1}   # filled by `bench noise`
author: {prompt: suite_author_v1, model: claude-opus-5-5}
```

`suite/manifest.json`, one row per scored check:

```json
{"id": "s5b_transit_bypass/held_copy_at_X_not_lendable_at_Y",
 "scenario": "s5b_transit_bypass",
 "class": "settled",
 "quote": "the copy must be marked \"in transit\" for 1 day before it is available there",
 "interp": null,
 "needs": ["checkout.branch"],
 "on_unsupported": "fail"}
```

- `class`: `settled` (three independent readers agree the spec requires it; this is the **core** score), `ruled` (an interpretation the owner ruled on; the **interp** score), `either` (owner accepts both readings; reported, never scored), `heavy` (needs real servers or threads; not scored in v1).
- `needs` and `on_unsupported`: what happens when the adapter says the build has no such capability. `fail` when the spec requires the capability (review 6: B's checkout without a branch failed two rows). `pass` when it's optional (review 6, topic 7: A has no re-route operation, so the window can't be extended).

`interpretations.yaml`:

```yaml
- id: I1
  question: A member places a hold while a copy sits on a shelf. Is the copy reserved for them at once?
  readings:
    literal: "Only a returned copy goes to the queue; the shelf copy stays lendable."
    expected: "The hold claims an available copy immediately; a walk-in can't jump the queue."
  ruling: expected            # literal | expected | either | unruled
  ruled_by: owner             # owner | sim-owner (auto-ruled packs can't be held-out)
  ruled_at: 2026-10-06
  rows: ["s3b_hold_when_copy_on_shelf/walk_in_cannot_jump"]
```

For lending the owner needs to rule on about six: shelf holds (I1), renewal counted from today or from the due date, a hold on a title the member has on loan, a walk-in checkout releasing the member's own ready hold, fee day-counting (started day or full 24 hours), and whether shelf checkout requires the copy to be at that branch. That is five minutes of owner time per pack, and it ends "the reviewer's reading is ground truth".

### 2.2 The driver contract

The suite never touches a build directly. It calls an abstract `Driver`; each build gets an `adapter.py` that implements it. This is the reviewer's `Impl` class (`review6/_work/adapter_b.py`) made a contract.

```python
# suite/driver.py (lending, abridged). Hidden from builders; given to adapter writers.
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Result:
    ok: bool                 # the operation was accepted
    status: int | None       # HTTP status or exit code where there is one
    body: dict               # the normalised payload (keys named in contract.md)
    error: str | None        # the build's own error code or message, verbatim

class Unsupported(Exception):
    """The build has no way to express this (name the capability: 'checkout.branch')."""

class Driver:
    REQUIRED = ("add_member", "add_title", "checkout", "ret", "renew", "hold", "cancel",
                "collect", "pay", "fees", "now", "advance", "tick")
    OPTIONAL = ("change_pickup",)
    def __init__(self): ...                     # a fresh, empty system with an injected clock
    def add_member(self, tier: str = "standard") -> int: ...
    def add_title(self, *branch_idx: int) -> tuple[str, list[int]]: ...
    def checkout(self, member, copy, branch_idx) -> Result: ...
    def advance(self, **timedelta_kw) -> None: ...
    def tick(self) -> None: ...                 # give the system a chance to process time
    def hold_status(self, hold) -> tuple: ...   # white-box read; contract.md says which
```

Rules for adapters, stated in `contract.md` and checked where possible:

1. Translation only. No business logic, no retries, no input fixing. An operation is at most one public call plus payload mapping.
2. Order of preference: the public interface, then a documented test hook, then a direct database read or insert (allowed only for the operations `contract.md` marks white-box, such as setup and raw state reads).
3. A missing capability raises `Unsupported("<capability>")`. It is never emulated.
4. The adapter must not modify the build. The runner hashes the build before and after.

Scenarios use soft checks, as `review6/prior/scenarios.py` does, so one scenario yields several scored rows:

```python
# suite/scenarios/s4_fees.py
def s4_fees(new, check):                       # new() -> Driver; check(label, cond, evidence="")
    for late, exp in ((1, 25), (10, 250), (40, 1000)):
        L = new(); m = L.add_member(); _, [c] = L.add_title(0)
        L.checkout(m, c, 0); L.advance(days=21 + late)
        check(f"{late}_days_late_is_{exp}c", L.ret(c, 0).body["fee_cents"] == exp)
```

The manifest fixes the row set. A row that never reports (an exception earlier in the scenario) is a failure, so denominators are constant. The runner wraps the adapter in a recording proxy and writes every driver call, arguments and `Result` to `traces/<scenario>.jsonl`; triage and the post-mortem read traces.

### 2.3 How a suite is built, and by whom

`bench pack build <pack>` runs six stages. Each is a Claude Code session or a no-tools call launched by `hsuite/build/`, under a config dir of its own (`~/factory-bench/cfg/pack-<pack>/`), so no builder can ever read these sessions (this closes the `~/.claude` leak path from `REVIEW.md` §6).

| Stage | Who | Sees | Produces | Est. cost |
|---|---|---|---|---|
| 1 `refs` | Two vanilla base builds (§4), independent sessions | `spec.md` | `refs/ref-a`, `refs/ref-b` | $7.50 |
| 2 `contract` | One full-tools session, "contract author" | spec, both refs | `driver.py`, `contract.md`, `conformance.py`, both ref adapters, conformance green on both | $2.50 |
| 3 `scenarios` | Two independent full-tools sessions, "suite authors" (the blind-review prompt's hostile brief, minus the comparison) | spec, both refs, driver, adapters | scenarios written against the driver, run on both refs | $6.00 |
| 3b `fuzz` | Deterministic code | both adapters | random operation sequences through both refs; each divergence becomes a candidate scenario (`REVIEW.md` §4 direction 1) | $0 |
| 4 `adjudicate` | Three independent no-tools calls per candidate row | spec and the row's claim **in prose** (no code, no ref behaviour) | `required` / `not_required` / `silent` per row | $1.00 |
| 5 `mutate` | One call proposes 20–30 one-rule mutants per ref as `{file, old, new, rule}`; deterministic code applies and runs them | ref code, spec | `mutants/`, `kill.json` | $1.50 |
| 6 `freeze` | Deterministic code, plus the owner for rulings | everything | `manifest.json`, `validation.json`, hashes, `status: frozen` | $0 |

**Total: $15–25 per pack (estimate).** Row classification in stage 4:

| Panel result | Outcome |
|---|---|
| 3 of 3 `required` | `settled` |
| any `not_required` majority | dropped |
| otherwise | an entry in `interpretations.yaml`; the row becomes `ruled` or `either` when the owner rules |

A row that fails on **both** refs is kept only with 3 of 3 `required`: it is either the most valuable row in the suite (a shared vanilla bug) or a wrong scenario, and nothing weaker than unanimity separates the two.

### 2.4 Validation gates (all in `validation.json`, all checked by `bench pack validate`, offline)

| Gate | Threshold | Why |
|---|---|---|
| Mutation score per ref (killed / non-equivalent mutants) | ≥ 0.80 | The suite has teeth. Survivors go back to one author session once; a mutant no scenario can kill after that is recorded as equivalent, by the author, with a reason. |
| Flaky rows (3 runs per ref, shuffled order) | 0 | Any flaky row is dropped before freeze. |
| Core pass rate of each ref | 0.60–0.95 | Headroom. If both refs pass above 0.95 the pack can't discriminate (review 6: vanilla at 53/53). Then add an author round, or reject the spec as too easy. |
| Settled rows | ≥ 30 | Resolution: one row is at most 3.3 points. |
| Inert rows (pass on both refs, kill no mutant) | reported; dropped if > 25% of the suite | Padding inflates pass rates. |
| Two refs disagree on ≥ 3 settled rows | reported | No disagreement suggests the authors wrote to the refs' common behaviour. |

Independence rules: suite authors never see factory prompts, lessons or v5 output. Held-out packs use a second author prompt (`suite_author_v2`, different wording and a different order of attack). Opinion: when a non-Claude model is available on the build host, use it for one of the two authors on held-out packs.

Known bias, stated once: both refs are vanilla builds, so rows of the form "fails on one ref" are vanilla bugs by construction. Vanilla's expected score is below 100% on every pack. Because every arm starts from a vanilla build, the bias is common to all arms.

### 2.5 Adapters for a new build, without leaking the suite

`hsuite/adapter.py`, called by `bench score`:

1. Snapshot the build (`bench/digest.clean_copy`), hash it, install its declared dependencies (`problems/repo/sandbox.candidate_venv`).
2. Launch an **adapter writer**: a full-tools session in the sandbox, config dir `~/factory-bench/cfg/score/`. Its workspace holds a copy of the build, `driver.py`, `contract.md` and `conformance.py`. It never gets `scenarios/`, `manifest.json`, `interpretations.yaml` or `owner.md`.
3. It writes `adapter.py` and iterates until `conformance.py` passes. Conformance is 10–20 trivial rows, one or two per operation ("a new member can check out a copy and gets a parseable due date"; "advancing the clock changes `now()`"). It contains no rule of the spec beyond the happy path.
4. If an operation can't pass conformance, the writer returns `{"op": ..., "evidence": ...}`. A second independent writer tries. If both fail on the same operation, the rows that need it fail: the build is broken on a happy path.
5. The runner scores: `run_pytest(build, overlays={"_hs": suite, "_hs/adapter.py": adapter}, network=False)`.
6. **Triage** of failing rows: one no-tools call per failing scenario (cheaper model allowed) reads the trace, the adapter, the scenario and the spec and returns `build_defect | adapter_defect | suite_defect`. `adapter_defect` goes back to the adapter writer with **the trace only**, for at most two rounds. `suite_defect` is logged to `~/factory-packs/<pack>/suite_debt.jsonl` for the next version; it doesn't change this score.

Why the builder can't be reached: the adapter is written after the build's hash is taken, in another config dir, and nothing from scoring flows back into a build. The one deliberate channel is the post-mortem (§5), and it is closed for the pack a lesson came from.

The builder never sees `driver.py` either. This matters: lending's driver has `checkout(member, copy, branch_idx)`, which would hand the builder the answer to run 10's top bug.

Estimated cost: $0.60–1.50 for a first adapter. Later snapshots of the same base build (after a repair or a review round) reuse the adapter: re-run conformance, and call the writer only if it fails ($0–0.40).

### 2.6 Scoring and cost accounting

`score.json` per run:

```json
{"pack": "lending", "pack_version": 2, "suite_sha256": "9e02...", "build_sha256": "77aa...",
 "core": {"passed": 55, "total": 61}, "interp": {"passed": 6, "total": 9},
 "rows": {"s5b_transit_bypass/held_copy_at_X_not_lendable_at_Y": "fail", "...": "pass"},
 "unsupported": ["checkout.branch"],
 "triage": {"build_defect": 6, "adapter_defect_fixed": 1, "suite_defect": 0},
 "adapter": {"attempts": 1, "usd": 0.84, "conformance": [14, 14]},
 "measurement_usd": 1.02}
```

- **Primary metric: core failures** (`core.total - core.passed`). With vanilla near the ceiling, a count of remaining failures and its ratio between arms say more than a pass-rate difference of two points.
- **Build cost** is the sum of `usd` over the run's `ledger.jsonl`: every model call, any model. For arms that fork a base build it is the base build's cost plus the arm's own calls.
- **Measurement cost** (adapters, triage) and **objective cost** (pack building) are reported in their own columns and never count against the 3× limit. They are not free; `bench history` shows cumulative totals for all three.
- **The 3× rule** per pack: `mean(arm cost) / mean(vanilla cost on that pack, same profile)`. Pass when the mean ratio over packs is ≤ 3.0 and no pack exceeds 3.5. Wall time is held to the same ratio and reported.

### 2.7 Noise floor and statistics

**Unit of analysis.** The paired unit is one base build: a (pack, seed). "Seed" means an independent run index; the CLI has no RNG seed. Rows within a build are correlated through shared root causes, so rows are never treated as independent samples.

**Three noise terms, each measured once in E1 or E3:**

| Term | How measured | Used for |
|---|---|---|
| σ_build: core pass rate across independent vanilla builds of one pack | `bench noise --pack lending --seeds 5`, then 2–3 per new pack | Reading any unpaired comparison, including the run 2–10 history |
| σ_adapter: score spread from two independent adapters on one build | two adapters on 2 of the 5 lending builds | Must be ≤ 1 row; otherwise fix §2.5 before anything else |
| σ_post: spread of v5's post-build stages re-run on the same base build | 2 replicates on 2 base builds in E3 | Reading paired comparisons |

**What counts as a difference.**

- *Two single unpaired runs* (the history table): the difference must exceed `2.8 × σ_build` (two-sided 95% for a difference of two draws). Until E1 reports σ_build, every row of that table is a tie. Opinion: I expect σ_build of 3–5 points on lending, which makes 51 vs 53 a tie.
- *Two arms*, pre-registered default:
  1. Effect size: mean core failures of the challenger ≤ 0.75 × the baseline's (at least 25% fewer hidden failures).
  2. A one-sided paired sign-flip permutation test on the per-base-build differences in core failures (exact; 2^n permutations), p ≤ 0.10 on dev (8 paired units), p ≤ 0.05 on dev plus held-out pooled (12 units).
  3. Direction: the challenger is better on at least 3 of 4 dev packs, and not worse by more than σ_post on any.
  4. The 3× cost rule.
- With 4–6 packs, a confidence interval "over specs" is not meaningful (resampling 4 packs gives 35 distinct samples). So inference is **conditional on the packs**, and generalisation is argued only from held-out packs. Say this in every report.

**How many seeds.** Start with 2 per pack per arm (8 dev units). Sequential rule, pre-registered: if the permutation p lands between 0.05 and 0.25, add one seed per pack once, then decide. Power, as an estimate: with σ_post ≈ 1.5 failures and 8 units, a true mean reduction of about 1.5 failures per build (roughly 25–30% at 5–6 failures per build) is detected about 80% of the time. Smaller effects will be missed; that is acceptable, because an effect that small doesn't justify 3× cost.

### 2.8 CLI and files

`bench` becomes a command group. `bench <taskdir>` keeps working: if the first argument is a directory it is treated as `bench rubric <taskdir>`.

```
bench pack new <id> --spec spec.md [--from-idea idea.md]
bench pack build <id> [--stage refs|contract|scenarios|adjudicate|mutate|freeze] --max-usd 25
bench pack rule <id> [--answers rulings.yaml]       # the owner rules interpretations
bench pack validate <id>                            # offline: hashes, gates, re-run on refs and mutants
bench pack list

bench base   --packs lending,jobq | --set dev --seeds 2 --tag e3     # base builds only
bench run    --arm vanilla|self-review|fresh-review|v5|v4 --tag e3 [--set dev] \
                 [--policy policies/v5] [--lessons FILE] [--max-usd 170]
bench score  --tag e3 [--arm v5] | --build-dir DIR --pack lending     # adapter + suite; any directory
bench noise  --pack lending --seeds 5 --tag e1
bench prereg prereg/E3.yaml                         # stamps sha256 + time; refuses to overwrite
bench compare --tag e3 --a self-review@matched --b v5 [--prereg E3] [--set dev]
bench history [--html]                              # all tags over time (§5.8)
bench rubric <taskdir> ...                          # the old scoreboard, unchanged flags
```

```
~/factory-bench/
  cfg/<role>/...                         # per-role Claude config dirs (§4.1)
  runs/<tag>/<pack>/s<seed>/
    base/        ws/  transcript.jsonl  run.json  ledger.jsonl  events.jsonl
    vanilla/     build/  score.json  adapter/
    self-review/ r1/ r2/ ...             # one snapshot per review round: build/, run.json, score.json
    fresh-review/r1/ ...
    v5/          den/ (events, ledger, scenarios)  build/  run.json  score.json  prediction.json  postmortem.json
  prereg/E3.yaml  E3.lock
  compare/e3-v5-vs-self-review.{json,md}
  history.jsonl
```

`run.json`:

```json
{"tag": "e3", "pack": "jobq", "seed": 1, "arm": "v5", "base": "runs/e3/jobq/s1/base",
 "profile": {"name": "full-v1", "hash": "c41d...", "cli": "2.1.288", "model": "claude-opus-5-5[1m]",
             "tools": ["Bash", "Edit", "Glob", "Grep", "Read", "Write", "..."], "mcp_servers": [],
             "permission_mode": "dontAsk", "net": "proxy", "config": "clean"},
 "usd": {"base": 3.41, "arm": 4.92, "total": 8.33, "by_stage": {"claims": 0.58, "verify": 2.07, "...": 0}},
 "wall_s": {"base": 1105, "arm": 1630}, "exit": "ok", "policy": "v5.0", "lessons_version": 0}
```

A pre-registration file:

```yaml
id: E3
question: Do v5's post-build stages remove more hidden failures than budget-matched review?
packs: {dev: [lending, ledgerbook, jobq, mdsite]}
seeds: 2
arms: [vanilla, self-review@matched, fresh-review@matched, v5]
metric: core_failures, paired on base builds
success:
  - mean_ratio(v5, self-review@matched) <= 0.75
  - perm_p(v5 < self-review@matched) <= 0.10
  - packs_better(v5, self-review@matched) >= 3
  - cost_ratio_mean(v5, vanilla) <= 3.0 and cost_ratio_max(v5, vanilla) <= 3.5
collapse:                      # v5 isn't better than the simple fresh reviewer
  - mean(v5) > mean(fresh-review@matched)
kill:
  - mean(v5) >= mean(self-review@matched)
sequential: {if_p_between: [0.05, 0.25], add_seeds: 1, once: true}
budget_usd: 170
```

`compare --prereg E3` prints `WIN`, `COLLAPSE`, `KILL` or `INCONCLUSIVE` and the numbers behind each clause.

### 2.9 Where the blind LLM review goes

It is replaced as the scoreboard and kept for two jobs:

1. **Suite growth.** At each decision point, one blind review on one dev pack (two arms, the existing prompt). Every bug it finds that the suite didn't see becomes a candidate row, goes through adjudication and bumps the pack version. A blind review that finds bugs the suite misses is the signal that the suite has debt.
2. **Quality guard.** `bench rubric` (the existing requirement judge and pairwise judge) covers docs, operability and code quality. It is non-gating: reported next to the score, so a factory that wins scenarios by shipping worse READMEs is visible.

### 2.10 Starter specs

Selection criteria: vanilla finishes for $2–6; at least three rules interact; time or state matters; drivable in process; no external services.

| Pack | Domain and interface | Why it differs | Split |
|---|---|---|---|
| `lending` | Library lending. REST plus web UI, queue, time, concurrency. | Exists. v1 = the 53 shared rows plus the deterministic extra topics from `review6/_work/extra.py`. **Contaminated**: the generic prompts were shaped by it. | dev, flagged |
| `ledgerbook` | Double-entry bookkeeping. CLI plus SQLite. Balanced entries, multi-currency with dated rates, period close, reversals, rounding, trial balance. | Money invariants; a CLI; conservation laws make sharp oracles. | dev |
| `jobq` | Persistent job queue. Python library plus worker CLI. Priority, delay, leases and visibility timeout, retries with backoff, dead letter, idempotency keys, crash recovery, injectable clock. | Infrastructure state machine; a library interface; time without calendars. | dev |
| `mdsite` | Static site generator. CLI over a file tree. Front matter, templates, tag pages, drafts and publish dates, link checking, incremental rebuild, feed, slug collisions. | Text and file transformation; the oracle is output files. | dev |
| `shiftboard` | Staff scheduling. REST. Availability, weekly hour caps, minimum rest, swap requests with approval, overtime, DST boundaries. | Interval constraints. Near transfer from lending. | **held-out** |
| `calcsheet` | Spreadsheet engine. Pure library. Formulas, ranges, dependency recalculation, cycles, error propagation, relative and absolute references on copy, CSV. | No persistence, no time, graph algorithms. Far transfer. | **held-out** |

Spec template (`~/factory-packs/TEMPLATE.md`; lending's spec is the model, at 25 lines):

```markdown
# Build <one-line product>

<Two sentences: who uses it and for what.>

Domain
- <8–14 rules, each with concrete numbers. At least 3 must interact (a limit one operation
  sets and another must respect). At least 2 depend on time, where the domain has time.
  One atomicity or concurrency rule where it is natural.>

Requirements
- <Interface kind: REST API + minimal UI | CLI | library. Do NOT give endpoints, signatures or a schema.>
- <Persistence, if any. Seed or demo data with sizes.>
- All time-dependent logic must use an injectable clock so it can be tested.
- README with how to run, test, and an architecture overview.

Deliverables: runnable via a single command, with its own test suite.
```

Rules for spec writers: 20–35 lines; no tech stack unless the product needs one; leave 3–6 natural ambiguities in and record each in `owner.md` with the intended reading (do not resolve them in the spec: the interp score needs them); run one vanilla build before investing in the suite, and trim the spec if it costs more than $6.

### 2.11 From ideas to packs

1. `afk` emits an idea file. `bench pack new <id> --from-idea idea.md` makes one spec-writer call (template above) that returns `spec.md` and `owner.md`. The idea's stated intent goes into `owner.md`; that is what makes later rulings possible without a human.
2. `bench pack build` runs unattended (~$20). A pack that fails its gates twice is `retired` with the reason.
3. A passing pack is `candidate` with `ruled_by: sim-owner`. It can serve as a **dev** pack at once. It becomes **held-out** only after the owner rules its interpretations (`bench pack rule`, five minutes).
4. Rotation: each new held-out pack retires the oldest held-out pack into dev. This is the fresh-data stream that keeps the lessons loop honest.
5. In the closed loop (issue #12), every published build carries `prediction.json`. One build in N gets a pack built afterwards (audit sampling) so calibration is checked on real ideas, not only on the starter set.

---

## 3. The v5 build pipeline

New package `repo_factory/v5/`, new policy directory `policies/v5/` (data only):

```
repo_factory/v5/
  pipeline.py    # build(): stage sequencing, budget, events, delivery
  claims.py      # two independent spec-only claim calls; ambiguity list
  verify.py      # the fresh verifier session; findings; held split
  mutants.py     # mutant patches for the built code; kill matrix (shares code with hsuite/build/mutate.py)
  oracle.py      # the 3-vote panel; pair adjudication (the conflict protocol)
  repair.py      # repair brief; resumed builder session
  accept.py      # authority order, strict improvement, regressions (pure functions)
  calibrate.py   # capture-recapture estimate; prediction.json
  report.py      # REPORT.md and ASSUMPTIONS.md
policies/v5/
  rules.yaml  lessons.yaml  CHANGELOG.md
  prompts/{claims,verifier,reverify,oracle,pair,repair,mutants,postmortem}.md
```

`cmd_build` dispatches: if the policy's `rules.yaml` has `pipeline: v5`, call `v5.pipeline.build` instead of `problems.repo.build.build`. The return dict has the same keys (`task`, `round`, `delivered`, `branch`, `out`, `den`, `cost_usd`, `events`, `wall_s`), so `repo-factory status`, `repo-factory ui` and `_finish` work unchanged.

```python
def build(taskdir: Path, *, den: Path | None = None, owner: str = "none",   # none | human | queue | sim
          policy: Path = ROOT / "policies" / "v5", max_usd: float | None = None,
          out: Path | None = None, prebuilt: "Prebuilt | None" = None,
          lessons: Path | None = None, exclude_lessons_born_on: str | None = None,
          subscribers: list | None = None) -> dict: ...

@dataclass
class Prebuilt:            # a base build made by hsuite.arms.base_build (the bench shares it across arms)
    workspace: Path; session_id: str; config_dir: Path; usd: float; wall_s: float; transcript: Path
```

### 3.1 Stages, targets and caps

> The caps below are history: since Addendum D (2026-10-09) every stage and session cap is
> an optional setting and none is set by default. The targets still hold as estimates.

Reference point: vanilla on lending is $3.70 and 20 minutes; the 3× limit is $11.10.

| # | Stage | Model, tools | Target | Cap | Wall |
|---|---|---|---|---|---|
| 1 | **Build**: the vanilla prompt, nothing added | CLI default (Opus), full-v1 | $3.70 | $6.00, 250 turns | 20 min |
| 2 | **Claims** ×2, independent, spec only | builder's model, no tools | $0.60 | $1.00 | 3 min (parallel) |
| 3 | **Verify**: fresh session, writes and runs scenarios, probes | builder's model, full-v1 | $2.00 | $2.50, 60 turns | 15 min |
| 4 | **Mutants**: 12–15 patches, harness runs them; one verifier follow-up on survivors | cheaper model for patches; verifier resumed | $0.40 | $0.70 | 4 min |
| 5 | **Oracle panel** on failing claims only, 3 votes | builder's model, no tools | $0.30 | $0.60 | 2 min |
| 6 | **Repair**: builder session resumed | builder's model, full-v1 | $0.90 | $2.50, 60 turns | 7 min |
| 7 | **Re-verify**: verifier session resumed; reruns all, probes the fixes | builder's model, full-v1 | $0.40 | $1.00 | 4 min |
| 8 | **Calibrate, report, deliver** | none | $0 | $0.10 | 1 min |
| | **Total** | | **$8.30 (2.2×)** | **hard $11.00** | **≈ 55 min (2.8×)** |

**Stage 4 (mutants) was removed on 2026-10-16** (AU1 #4): its score decided nothing (no gate, repair or retire decision read it), and across 13 runs it cost $3.55 and 229 minutes of wall clock.

- The global cap is `min(max_usd, 2.95 × pack vanilla mean)` when the bench supplies a vanilla mean, else `v5.max_usd` (default 11.0). It is enforced by `CostCapHarness` for sessions and by a check before each no-tools call.
- Reserve rule: stage 6 starts only if `spent + repair cap ≤ cap`; a second repair round only if `spent + $2.00 ≤ cap`.
- If the base build stops at a cap or doesn't start, skip to §3.6.

`policies/v5/rules.yaml`:

```yaml
pipeline: v5
version: "5.0"
max_usd: 11.0
build:   {max_turns: 250, max_usd: 6.0, timeout_s: 7200, model: null}      # null: the CLI default
claims:  {samples: 2, max_claims: 60, max_ambiguities: 8, model: null}
verify:  {max_turns: 60, max_usd: 2.5, timeout_s: 1800, held_fraction: 0.3, explore_share: 0.3}
mutants: {n: 14, model: claude-sonnet-5, survivor_round: true}
oracle:  {votes: 3, defect_min: 2, model: null}
repair:  {rounds: 1, max_rounds: 2, max_turns: 60, max_usd: 2.5, timeout_s: 1800, fixup_usd: 1.0}
interpret: {mode: auto, max_questions: 3, fork: {enabled: true, max: 1}}   # auto | ask
regenerate: off                                                             # off | fallback  (E5)
stop:    {yield_window_usd: 1.0}
stages:  [build, claims, verify, mutants, oracle, repair, reverify, deliver]   # tier 3 mutates this list
```

### 3.2 Stage 1: the raw-spec build

- Prompt: `spec.md.rstrip() + "\n\nWork in the current directory.\n"`. This is `bench/contestants.VANILLA_SUFFIX`, unchanged. No `check.py`, no `task/answers.md`, no contract, no standards text. If the task has a seed repo or user tests (`task.yaml`), they are copied into the workspace first, as vanilla would find them.
- Launched through `harness/profile.launch` (§4.1), with a kept session id so stage 6 can resume it.
- In `interpret.mode: ask`, owner answers are appended as a `## Clarifications from the owner` section. That variant can't share base builds with vanilla, so it needs its own unpaired experiment; it is not in this two-week plan. The default is `auto`.

### 3.3 Stage 2: claims and ambiguities

Two independent no-tools calls with the same prompt (`prompts/claims.md`). Input: the spec, the lessons that pass the injection filter (§5.2) sorted by hit rate, nothing else. No code: the reading of the spec must not be anchored on what the builder did. Output:

```json
{"claims": [
   {"id": "c17", "situation": "a queued request exists and the system receives no calls for longer than two offer windows",
    "action": "the next waiting party looks up their request", "expect": "they have a fresh, unexpired offer",
    "basis": "spec_implied", "quote": "has 3 days to collect it before the hold expires and passes to the next person",
    "kind": "interaction", "lessons": ["L007"]}],
 "ambiguities": [
   {"id": "a3", "question": "...", "literal": "...", "expected": "...", "recommend": "expected",
    "stakes": "high", "why_high": "changes a public operation's parameters"}]}
```

- `basis`: `spec_explicit | spec_implied | convention | silent`. A `silent` claim is an ambiguity, not a claim.
- The union of the two samples is deduplicated by one cheap call. Each claim keeps `sets: [1]`, `[2]` or `[1, 2]`; calibration uses this (§3.7).
- Free experiment before building anything (`REVIEW.md` direction 4): run this prompt on the lending spec and check whether the ambiguity list covers the six disputed topics in §2.1. Kill the ambiguity half of the prompt if it covers fewer than three.

### 3.4 Stage 3–4: the fresh verifier

A **new** Claude Code session, full-v1 profile, its own config dir (`<den>/cfg/verifier/`).

| It sees | It doesn't see |
|---|---|
| `spec.md` | the builder's session or transcript |
| a read-write **copy** of the built repo (so it can install, run, curl, break things) | the hidden suite, driver, contract or any pack file |
| the merged claims and ambiguities | lessons born on this pack |
| the injected lessons | the factory's findings from other runs |

Contract (`prompts/verifier.md`), in order:

1. Run the build's own tests and start it the way its README says. Record failures as findings.
2. Write `_verify/driver.py`: thin helpers for this build's real interface, with an injected clock. This is its private adapter.
3. For each claim, write `_verify/scenarios/test_<claim id>.py` (one pytest test, header comments `# rule:`, `# expect:`, `# source:`, `# claim:`: the format `rules/miner.parse_rules` already reads), and run it.
4. A scenario that errors in setup is the verifier's bug: fix the mechanics. A scenario may be reported as failing only after the behaviour was **reproduced by hand** (a curl call or a direct call) and the command and output are recorded. This single rule replaces `rules/validate.py`'s checks (a), (b) and (d), which in run 10 retired 24 of 56 rules, most of them for mechanics.
5. Spend the remaining budget (`explore_share`) attacking without claims: operation sequences across rules, both sides of every limit, time gaps with no calls, invalid and extreme input, repeats and out-of-order calls. Each finding becomes a scenario with `claim: explore`.
6. Write `_verify/findings.json`:

```json
{"scenarios": [
   {"id": "c17", "file": "_verify/scenarios/test_c17.py", "status": "fail",
    "claim": "c17", "sets": [1, 2], "lessons": ["L007"],
    "repro": "curl -s -X GET localhost:8000/members/3  # after advancing the clock 10 days",
    "observed": "hold 12 status=expired, never offered", "expected": "hold 12 status=ready, expires in 3 days",
    "suspect": "app/service.py:306", "scope": "local"}],
 "blocked": [{"claim": "c41", "why": "no way to inject a commit failure"}],
 "self_estimate": {"p_hidden": 0.86, "least_trusted": ["time gaps with no requests", "..."]}}
```

`scope`: `local | cross-cutting | structural` (structural = needs a public interface or schema change).

**Storing scenarios.** The harness loads them into the existing `RuleStore` (`rules/store.py`) as `Rule` objects under `<den>/cache/rules/<task>/r<round>/`. That gives persistence, `history.jsonl`, and `rule_status` events the UI already renders. `rules/score.run_rules` gains one parameter, `overlays: dict | None`, so `_verify/driver.py` is copied next to the rule files; it then runs every scenario in the no-network pytest sandbox, twice in opposite orders (`validate.validate_runs`), and drops flaky ones. The harness's run, not the verifier's word, is the record of pass and fail.

**Held split.** 30% of the *passing* scenarios are `held` by hash (`store.held_by_hash`): never copied into the builder's workspace, used as regression guards the repairer can't write to. Every confirmed failing scenario is visible; hiding a found bug from the repair buys nothing.

**Mutation tests (stage 4).** One call proposes `mutants.n` single-rule patches to the built code, each naming the spec rule it breaks. The harness applies each to a copy and runs the passing scenarios: a kill matrix and a mutation score. Survivors go to the resumed verifier once: "write a scenario that kills each". A scenario written to kill a mutant that also fails on the unmutated code is a new finding. The score measures the verifier's blind areas and feeds calibration; it costs model tokens only for the patches.

### 3.5 Stage 5–6: oracle panel, repair, acceptance, conflicts

**The oracle panel** (`oracle.py`) replaces the arbiter and the second reading. It runs on **failing scenarios only** (typically 3–10), so it is cheap. Three independent no-tools calls each get the spec, the owner's answers if any, and the claim **in prose** (situation, action, expected outcome). They don't get the test code or the build's code: the question is what the spec requires, and the code is not evidence about that. Each returns `required | not_required | silent` with a quote or a reason.

| Votes | Class | Next |
|---|---|---|
| ≥ 2 `required` | **defect** | repair queue |
| ≥ 2 `not_required` | retired | negative credit to the cited lesson |
| otherwise | **interpretation** | the interpretation policy below |

**Authority order** (`accept.AUTHORITY`). Higher wins; equal rank means adjudicate:

| Rank | Evidence |
|---|---|
| 1 | The owner's answers and rulings; the user's own tests (`task.yaml` tests) |
| 2 | A claim with `basis: spec_explicit` and 3 of 3 `required` |
| 3 | A claim with ≥ 2 of 3 `required` |
| 4 | The builder's own tests (they record the builder's reading, nothing more) |
| 5 | Interpretation defaults the factory chose itself; anything traced to its own assumptions |

**Repair.** The builder's session is resumed (`--resume --fork-session`, as v4 does) with `prompts/repair.md` and a findings brief: per defect, the claim, the reproduction command, observed against expected, the oracle's quotes, and the scenario file (copied to `acceptance/findings/` with the driver). Instructions: fix causes, one commit per finding, extend your own tests, run the failing scenarios and your suite, change no more than the fixes need.

**Acceptance** (`accept.decide`, pure function, ported from `oneshot.objective` and `test_regressions`):

1. The head after repair must start and pass the user's tests (rank 1).
2. Count confirmed defects fixed (visible failing scenarios now passing).
3. List regressions: anything that passed before and fails now, across visible scenarios, held scenarios, the builder's tests.
4. No regressions and ≥ 1 fix: **accept**.
5. Each regression goes through the **conflict protocol**; it is never an automatic rejection.

**The conflict protocol** (`oracle.pair`). For a repair that turned X green and Y red, one panel (3 votes) gets both claims in prose and answers: *can a correct system satisfy both; if not, which does the spec require?*

| Verdict | Action |
|---|---|
| `compatible` | The repair is incomplete. One fix-up resume (cap `repair.fixup_usd`) with both. If Y still fails, drop the commit for X (`git revert`), keep the rest. |
| `x_wins` | Y is wrong or stale. If Y is a builder's test, the fix-up tells the builder to update it; if Y is a verifier scenario, retire it. Accept. |
| `y_wins` | X is retired; revert X's commit. |
| `undecidable` | Both become one **interpretation** item. |

Two details:

- The panel is asked about **every** regression, whatever the ranks, because most regressions are collateral damage and not contradictions: `compatible` must be ruled out first. Rank decides only incompatible pairs: when X and Y differ in rank, the higher rank wins and the panel's `x_wins`/`y_wins` is ignored. Regressions are few, so this costs cents.
- A **held** scenario that regresses is promoted to visible (`RuleStore.promote`, as v4 does) so the fix-up can see it, and another passing scenario takes its place in the held set.

**Run 10 replayed under this protocol.** q25 ("fee accrues per started calendar day past the 23:59 due time") and the blocking test `test_R15_R16_overdue_fee_accrues_per_started_day_and_caps_at_10_euros` both trace to the factory's own assumption: rank 5 against rank 5. v4 rejected the whole repair, including the q27 fix, which conflicted with nothing as far as the event log shows (the one broken test is about day-counting; that is my inference, not verified against the diff). v5: per-finding commits keep the q27 fix; the q25 pair goes to the panel, which most likely returns `undecidable` (day-counting isn't in the spec); the item becomes an interpretation, takes the default below, the losing test is updated, and `ASSUMPTIONS.md` records the choice. No stalemate, no lost fix. And in v5 the specific pair can't arise at all: there are no pre-code acceptance tests.

**Interpretation policy.** Sources: the claims' ambiguity list, `silent` panel outcomes, `undecidable` pairs. For each item the verifier has already established which reading the build implements (a probe per reading).

| Mode | Rule |
|---|---|
| `ask` (an owner is reachable: `--owner human|queue|sim`) | Ask the top `max_questions` items by stakes, through the existing oracle (`oracle/human.py` queue or stdin; `oracle/sim_owner.py` reads the pack's `owner.md` on the bench). The answer is rank 1. |
| `auto` (default; the closed loop) | Three no-tools votes on "which reading would the person who wrote this spec expect?". If ≥ 2 agree and the build implements the other reading, queue a repair of class `interpretation` (rank 5). Otherwise leave the build as is. Either way, write the item to `ASSUMPTIONS.md` with both readings. |
| **fork** | Only when all hold: no reading gets 2 of the 3 votes, `stakes: high` (the change touches a public operation or the schema: the one-way door), and the reserve allows one more repair. Then deliver the build's reading on `brb/<id>` and the other reading on `brb/<id>--alt-<item>` (one repair on a copy), and say so at the top of `REPORT.md`. At most `fork.max` per build. The bench scores the main branch; the alt branch is scored for information. |

This is the owner's "fork only where a decision is one-way and ambiguous", applied to readings of the spec. Opinion: expect zero or one fork on most specs.

### 3.6 Regenerate or repair

Default: **repair**. Regeneration is off in v5.0 (`regenerate: off`) and is turned on only if E5 passes. When on (`fallback`), it replaces the second repair round; it never adds to it.

| Condition after repair round 1 | Action |
|---|---|
| No confirmed defects remain | Deliver. |
| Remaining defects all `local` or `cross-cutting`, reserve allows | Repair round 2. |
| A `structural` defect remains and its repair failed or regressed; or confirmed defects ≥ 8, or ≥ 20% of claims fail; or the base build was cut off by a cap | **Regenerate** if `spent + $4.50 ≤ cap`: a fresh vanilla session on `spec.md` plus a `## Clarifications` section (the confirmed defects and chosen readings, rewritten as neutral requirements, ≤ 15 lines, no implementation hints). Then resume the verifier to port `_verify/driver.py` (≈ $0.50) and re-run every stored scenario. Deliver the head with fewer confirmed failures; on a tie, the repaired original. |
| Otherwise | Deliver the best head, with the open defects listed in `REPORT.md`. |

### 3.7 Stopping and calibration

**Stop** when any holds: re-verify finds no new confirmed defect; the reserve rule blocks the next stage; `repair.max_rounds` is reached; or the verifier's last `stop.yield_window_usd` of spend confirmed no new defect (the orienteering rule: stop when the prize rate hits zero).

**Calibration was removed on 2026-10-16** (AU1 #5): the v5.0 predictor below never got bench data, came out 0.958–0.99 in all 36 recorded rounds (0.99 with nothing checked), and its gate check never held a build; REPORT.md now states the counts (claims checked, defects found, fixed, open), claims take one sample, and `stop.yield_window_usd`, `repair.rounds`, `interpret.fork` and `regenerate` (never read) are gone from `rules.yaml`.

**Calibration** (`calibrate.py`). At delivery write `prediction.json`:

```json
{"p_hidden": 0.88, "interval80": [0.80, 0.94], "expected_hidden_failures_per_100": 12,
 "least_trusted": ["behaviour after long idle periods", "limits enforced on secondary operations"],
 "features": {"claims": 57, "found": 6, "fixed": 5, "unfixed": 1, "n1": 4, "n2": 5, "both": 3,
              "chapman_total": 6.5, "mutation_score": 0.79, "blocked": 2, "self_estimate": 0.86}}
```

- **Remaining-defect estimate by capture–recapture.** The two claim samples are two independent "captures". With `n1`, `n2` defects found by scenarios from sample 1 and 2 and `both` found by both, Chapman's estimator gives the total: `(n1+1)(n2+1)/(both+1) − 1`. Unfound ≈ total − found. This is the one feature that isn't the pass rate of the factory's own checks.
- **v5.0 predictor (before any bench data):** `p_hidden = 1 − (unfound + unfixed + 0.5 × blocked) / claims`, clamped to [0.5, 0.99]; the interval is ±1 defect.
- **v5.1 (once ≥ 20 scored runs exist):** a ridge regression on `features` fitted by `bench history --fit-calibration`, leave-one-pack-out. Stored in `policies/v5/calibration.json`.
- **Scoring:** mean absolute error against the true hidden core pass rate, compared with a constant guess (the mean over the other packs). Kill the predictor if it doesn't beat the constant after 20 runs.
- **Use:** on the bench it is a reported metric. In the closed loop it is the publish gate: publish only above a threshold the owner sets, and never with a security item in `least_trusted`.

### 3.8 Events, ledger, report

- **Ledger.** `ledger.activate(<den>/ledger.jsonl, round, events=log)` as today. Every call is tagged `stage` (one of `build, claims, verify, mutants, oracle, repair, reverify, deliver`). Add callers to `ledger.CATEGORY`: `claims`, `verifier`, `mutants` → `mining`; `oracle`, `pair` → `judging`; `postmortem` → `other`. `ledger.call_claude` gains `env: dict | None` so no-tools calls run under the clean config dir too.
- **Events.** Reuse `build_started`, `candidate_started/finished` (the build and each repair), `rule_status`, `question`/`answer`, `delivered`, `build_finished`. New: `v5_stage` (`stage`, `status`, `usd`, `wall_s`), `claims` (`n`, `ambiguities`), `findings` (`confirmed`, `interpretation`, `retired`, `blocked`), `mutation` (`score`, `survivors`), `conflict` (`x`, `y`, `verdict`), `repair_round` (`sent`, `fixed`, `regressions`, `accepted`, `why`), `interpretation` (`item`, `mode`, `chosen`, `forked`), `prediction`. Add `"verifying"` and `"repairing"` to `events.PHASES` and a `v5_event` branch in `events.fold` next to `v4_event`. Document in `docs/events.md`.
- **Delivery.** `build.make_branch` as today. `REPORT.md` (from `v5/report.py`): the prediction first, then findings fixed and open, interpretations chosen, cost by stage. `ASSUMPTIONS.md` is delivered in the repo. The verifier's scenarios are delivered as `tests/verification/` with the driver: a real asset for the owner, and the README no longer points at a suite that doesn't exist (review 5's complaint).

### 3.9 Reuse map

| Module | v5 use |
|---|---|
| `harness/claude_code.py` | `ClaudeCodeHarness.run(tools="full", session=...)`, `parse_stream`, `run_cost`, `prepare_resume`. Change: `config_dir` parameter instead of the process-global `config_dir()`. |
| `harness/sandbox.py`, `netproxy.py` | As is, plus: mounts take the config dir as a parameter; profile full-v1 binds only `harness/netproxy.py` read-only at `/opt/repo_factory/src/repo_factory/harness/`, not the whole package (today the whole `repo_factory/` tree is readable inside, which would expose v5's prompts to a builder). |
| `den/ledger.py`, `events.py`, `ui/`, `progress.py` | As is, with the additions in §3.8. |
| `den/artifacts.py` | Snapshots and `changed_files`. |
| `problems/repo/build.py` | `make_branch`, `lifecycle`, `CostCapHarness`, `spec_title`. |
| `problems/repo/task.py` | `load_config` (task id, seed dir, user tests, budget). |
| `problems/repo/sandbox.py` | `candidate_venv`, `run_pytest` (verifier scenarios and the hidden suite, network off). |
| `rules/model.py`, `store.py`, `score.py`, `llm.py` | The scenario store, runner and ledger-recorded LLM wrapper. `validate.validate_runs` for determinism. |
| `core/oneshot.py` | `objective`, `test_regressions`, `n_passed` are copied into `v5/accept.py` with their tests from `tests/test_v4.py`; `oneshot.py` itself is frozen. |
| `oracle/human.py`, `oracle/sim_owner.py` | The ask path. |
| **Not used by v5** | `core/session.py` and policy classes, `orchestrator`, `controller`, `search`, `mining`, `briefs`, all of `problems/repo/design*`, `planner`, `scoper`, `checklist`, `milestones`, `techselect`, `decide`, `pairwise`, `rules/miner|validate(a,b,d)|arbiter|engine|schedule|loop`, `scoring/`, `hibernate/`, `replay/`, `prompteval/`. |

---

## 4. Baselines

### 4.1 One profile, enforced

New `repo_factory/harness/profile.py`:

```python
PROFILE = "full-v1"     # bump on ANY change to tools, sandbox mounts, net mode or CLI flags

def launch(prompt: str, workspace: Path, *, config_dir: Path, max_turns: int, max_usd: float,
           timeout_s: int, session: dict | None = None, model: str | None = None) -> HarnessResult:
    """The only way a bench contestant, a v5 worker, a reference build or an adapter writer starts."""

def clean_config(dir: Path) -> Path:
    """A config dir holding credentials only: no CLAUDE.md, settings, memory, plugins, skills, MCP."""

def fingerprint(transcript: Path) -> dict:
    """From the stream-json init event: model, tools (sorted), mcp_servers, permissionMode,
    CLI version; plus PROFILE and the sandbox net mode. `hash` = sha256 of the rest."""
```

- **Tools and sandbox:** `ClaudeCodeHarness.run(tools="full")`: bubblewrap, proxy-only network, `--permission-mode dontAsk`, `--strict-mcp-config`, `FULL_ALLOWED`. Identical for every arm because there is one call site.
- **Model:** `--model` unset (the CLI default) unless the experiment names one. The *resolved* model from the init event is what the fingerprint stores, so alias drift between days is caught.
- **Config:** each run gets `clean_config(<run>/cfg/<role>)`. Today's workers bind the user's whole `~/.claude` read-write: user-level `CLAUDE.md`, settings, skills and every earlier session are visible and differ by day. A clean dir removes that variable and the leak path. **Unverified:** how credentials reach a fresh config dir on the build host (copying `.credentials.json`, or `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY`, which `sandbox.PASS_ENV` already passes). Track A's first task is a $0.05 probe.
- **CLI version:** `bench base` writes `claude --version` to `runs/<tag>/bench.lock`. A tag never mixes versions.
- **The guard:** `bench compare` exits 2 if the arms' fingerprint hashes differ, naming the differing field. `--allow-profile-mismatch` exists for archaeology and stamps the report `INVALID COMPARISON` in its title. `run.json` without a fingerprint can't be compared at all, which retires the hand-entered `--meta cost_usd=3.70` path.
- **Asserted at launch:** `mcp_servers == []`; tools ⊇ {Bash, Read, Edit, Write, Glob, Grep}; `REPO_FACTORY_WORKER_NET != host`.

### 4.2 The arms (`repo_factory/hsuite/arms.py`)

Each arm works on a **copy** of the base workspace; resumed sessions use `--fork-session`, and `prepare_resume` copies the session file to the copy's project key (existing code; it needs the `config_dir` parameter).

| Arm | Definition | Cost |
|---|---|---|
| `base` | One session: the vanilla prompt (§3.2), caps 250 turns / $6 / 2 h. | ≈ $3.70 |
| `vanilla` | The base build, scored as is. | base |
| `self-review` | The base session **resumed** with `hsuite/prompts/self_review.md`, one round at a time, a snapshot after each round (build, cumulative cost). Stops at the ceiling ($11) or after two rounds in a row that change no file. Max 4 rounds. | base + up to $7.30 |
| `fresh-review` | A **new** session per round on the repo and spec with `hsuite/prompts/fresh_review.md` (find and fix in one session). Same snapshots and stops. | base + up to $7.30 |
| `v5` | `v5.pipeline.build(prebuilt=base, ...)`. | base + ≈ $4.60 |
| `v4` | `repo-factory build --policy policies/v4` (own build; unpaired; for history only). | ≈ $12 |

`self_review.md` (fixed text, versioned, never tuned after E3's pre-registration):

```
Now review what you built as a hostile reviewer would. Re-read spec.md line by line. For each
rule, and for each pair of rules that can interact, write and run a scenario that tries to break
it: boundaries on both sides, time passing with no activity, operations in unusual orders,
invalid and extreme input, and concurrent use where the spec mentions it. Fix every defect you
confirm and add a test for it. Keep everything that works working. When a full pass finds
nothing new, say "REVIEW COMPLETE" and stop.
```

`fresh_review.md` is the same brief with one opening sentence: "You didn't write this code; another engineer did, from spec.md."

**Budget matching, after the fact.** `self-review@matched` for a (pack, seed) is the last snapshot whose cumulative cost is ≤ 1.05 × the v5 run's cost on the same base build. The snapshots also give the baseline's whole quality-against-cost curve, so "v5 at $8.30" is compared with the baseline at $8.30, not at its cap. If the baseline stops by itself below the budget, its final snapshot is used and the report says "baseline declined to spend".

Why two review baselines: if v5 beats `self-review` but not `fresh-review`, the value is "a second session with fresh context", and the pipeline should collapse to that (decision D2). Opinion: this is the most likely way v5 fails to justify itself.

---

## 5. Self-improvement

New package `repo_factory/lessons/` (`store.py`, `lint.py`, `postmortem.py`, `credit.py`, `promote.py`), CLI `repo-factory lessons ...`.

### 5.1 The lessons file

`policies/v5/lessons.yaml`:

```yaml
version: 3
cap: 20
lessons:
  - id: L007
    text: "When a state expires by time, test a long gap with no calls at all: the next party in line must still get a full, fresh turn."
    applies_to: claims            # claims | verifier (builder: tier 2, later)
    status: active                # probation | active | retired
    born: {pack: lending, run: "runs/e3/lending/s1/v5", row: "t15_idle_gap/next_holder_offered", date: 2026-10-10}
    stats: {exposures: 6, derived: 9, caught: 3, false_alarms: 0, packs_caught_on: [jobq, shiftboard]}
    hit_rate: 0.50                # caught / exposures; shown to the verifier, and the sort key
```

Hard rules, checked by `lessons/lint.py` at write time:

- ≤ 240 characters, one sentence or two, imperative.
- **No domain nouns:** no word of the text (lower-cased, lemmatised) may appear in the `domain_terms` of **any** pack. The example above passes: "party", "state", "turn", "gap" are not lending terms; "hold", "member", "copy" would fail.
- No identifiers, file names, numbers taken from a spec, or quotes of a hidden row.
- At most 20 lessons with status `probation` or `active`.

### 5.2 Injection, and why every dev score is out of sample

`store.for_build(pack_id)` returns lessons with status `active` or `probation` **whose `born.pack` differs from the pack being built**, sorted by `hit_rate`, rendered one per line with the rate ("found a defect in 3 of 6 builds"). On a new spec (production, held-out) every lesson applies.

Consequence: a lesson can never help on the pack whose hidden suite produced it. Every dev-pack score is therefore leave-one-pack-out by construction, the post-mortem can safely read hidden rows, and no separate LOO bookkeeping can be got wrong.

### 5.3 The post-mortem contract

One no-tools call per scored v5 run on a **dev** pack (never held-out), on the builder's model (`prompts/postmortem.md`, ≈ $0.15–0.30).

Input: the spec; each failing hidden core row as `{row id, title, class, driver-call trace, expected vs observed}`; the run's `claims.json` and `findings.json`; the current lessons.

Output:

```json
{"failures": [
   {"row": "t13_hold_on_borrowed_title/refused",
    "miss_type": "no_claim",
    "existing_lesson": null,
    "new_lesson": {"text": "For every request a party can make about an item, test making it while that party already has the item.", "applies_to": "claims"},
    "policy_note": "neither claim sample covered a party acting on something they already hold"}],
 "summary": {"no_claim": 3, "claim_wrong_expectation": 0, "retired_by_oracle": 1,
             "passed_wrongly": 0, "found_not_fixed": 1, "repair_regression": 0, "interpretation": 1}}
```

- `miss_type` is a closed set, one per failure:

| miss_type | Meaning | Stage it indicts |
|---|---|---|
| `no_claim` | No claim or probe covered it | claims, verifier exploration |
| `claim_wrong_expectation` | A claim covered it and expected the wrong outcome | claims |
| `retired_by_oracle` | Found, then voted `not_required` or `silent` | oracle panel |
| `passed_wrongly` | A scenario covered it and passed (bad mechanics) | verifier |
| `found_not_fixed` | Confirmed, sent, not fixed | repair |
| `repair_regression` | Passed before the repair, fails after | repair, acceptance |
| `interpretation` | The owner's ruling differs from the reading chosen | interpretation policy |

- At most one new lesson per failure and **three per run**. If an existing lesson should have caught it, name it in `existing_lesson` and propose nothing.
- New lessons pass the lint or are dropped. They enter as `probation`.
- The `miss_type` histogram, summed per policy version, is the diagnostic for everything beyond lessons (§5.7).

### 5.4 Credit assignment

Per v5 run, from `claims.json`, `findings.json` and the oracle results. All of it is available **without a hidden suite**, so it also runs in production:

| Event | Counter |
|---|---|
| The lesson was injected | `exposures += 1` |
| A claim cites it (`lessons: [L007]`) | `derived += 1` |
| A scenario from such a claim fails and the panel confirms a defect | `caught += 1`; add the pack to `packs_caught_on` |
| Such a scenario fails and the panel votes `not_required` | `false_alarms += 1` |

Citation can be gamed (the claims call cites a lesson for a claim it would have made anyway), so counters decide **retirement and ordering**, never promotion:

- `probation → active`: `caught ≥ 2` on ≥ 2 different packs.
- Retire: `exposures ≥ 4` and `caught == 0`; or `false_alarms ≥ 3` and `false_alarms > caught`.
- Eviction when the file is full: lowest `hit_rate`, probation first.

True marginal value is measured at the set level by paired re-runs (next section).

### 5.5 Promotion

A *candidate* is `lessons.yaml` at version N+1 (new probation lessons added, retirements applied). `repo-factory lessons promote` runs:

1. **Dev check (leave-one-pack-out by §5.2).** On the stored base builds of the latest tag, run v5's post-build stages twice: lessons version N and N+1. Same base builds, so the pair differs only in the lessons. Require: mean core failures (N+1) ≤ (N), and no pack worse by more than σ_post.
2. **Held-out check.** The same pair on the held-out packs' stored base builds. Require: mean core failures strictly lower, or equal with lower cost; no held-out pack worse by more than σ_post.
3. On success, write version N+1 as the default, and append to `policies/v5/CHANGELOG.md`: `author: lessons-loop`, the diff, both compare files. This would be the first changelog entry in the project not "written by hand".

Guard against wearing out the held-out set: `promote` counts held-out evaluations in `~/factory-bench/heldout_looks.json`. After 5 looks, the oldest held-out pack rotates into dev and a new pack must be added (§2.11) before the next promotion.

### 5.6 Cost per iteration (estimates)

| Item | Cost |
|---|---|
| Post-mortems for a tag (8 runs) | $2 |
| Dev check: 4 packs × 2 seeds × post-build stages at ≈ $4.60, one new arm (version N is cached from the last promotion) | $37 |
| Scoring the dev check (adapters reused; conformance re-checks) | $5 |
| Held-out check: 2 packs × 2 seeds × $4.60 + scoring | $22 |
| **One promotion attempt** | **≈ $65** |
| First iteration (no cached arm; base builds for held-out) | ≈ $120 |

### 5.7 From lessons to policy

The mutable surface widens one tier at a time. Each tier uses the same bench, the same promotion rule and the same changelog. A tier unlocks when the one below has produced a promoted change that held on held-out packs.

| Tier | What changes | Proposer | Guard |
|---|---|---|---|
| 0 | `lessons.yaml` | post-mortem | lint, §5.5 |
| 1 | Numeric knobs in `rules.yaml` | Rules over the `miss_type` histogram: `found_not_fixed` > 30% → raise `repair.max_usd` by $0.50; `retired_by_oracle` > 20% → `oracle.votes` 3 → 5 with the same majority share; `no_claim` > 50% → `claims.samples` 2 → 3 or `verify.explore_share` up | one knob per candidate; must stay within the 3× rule |
| 2 | One prompt in `policies/v5/prompts/` | One LLM call: the histogram, ten example misses of the dominant type, the current prompt → a diff of ≤ 20 lines | diff size cap; the prompt's JSON contract must still parse on a fixture |
| 3 | The `stages:` list and per-stage model choice | LLM proposal from a fixed stage library (e.g. add `regenerate`; a second verifier; a cheaper builder with a stronger verifier, `REVIEW.md` direction 3) | every stage has typed inputs and outputs; `pipeline.py` validates the list before running |

Shared-build re-runs are what replay was meant to be: a stored base build is re-run through a changed post-build policy for about $5, with real model calls and a real hidden score. `repo_factory/replay/` is not involved.

### 5.8 What the owner sees

`bench history --html` writes `~/factory-bench/HISTORY.md` and `history.html`; `ui/server.py` serves it at `/history`.

1. **Policy table**, one row per version (`v4`, `v5.0`, `v5.0+L1`, ...): dev and held-out mean core failures with the vanilla and matched-review figures beside them, cost as a multiple of vanilla, the permutation p, and the calibration error.
2. **Two headline numbers for the drift worry:** the share of promoted changes whose author is a machine, and days since the last human edit under `policies/v5/`.
3. **Lessons table:** text, born on, exposures, caught, hit rate, status.
4. **Miss-type histogram** per version (where failures come from now).
5. **Spend:** cumulative build, measurement and objective cost.

`history.jsonl` row: `{"ts", "tag", "pack", "split", "seed", "arm", "policy", "lessons_version", "core_failed", "core_total", "interp_passed", "usd", "wall_s", "p_hidden", "profile_hash"}`.

---

## 6. Work breakdown

### 6.1 Tracks

Five tracks for workers on the build host. Shared interfaces are fixed here so tracks can start in parallel: the run directory and `run.json` (§2.8), `score.json` (§2.6), the pack layout (§2.1), `v5.pipeline.build` and `Prebuilt` (§3), `claims.json` and `findings.json` (§3.3–3.4), `lessons.yaml` (§5.1). New tests go in `tests/test_profile.py`, `test_hsuite_*.py`, `test_v5_*.py`, `test_lessons.py`; legacy tests keep running in CI.

**Track A: profile and arms** (2–3 days). The foundation; start first.

| | |
|---|---|
| Scope | `harness/profile.py`; `config_dir` parameters in `claude_code.py` and `sandbox.py`; the narrowed `/opt/repo_factory` mount; `hsuite/arms.py` (`base_build`, `self_review`, `fresh_review`, snapshots); `bench base|run`; delete `VANILLA_TOOLS`, `VANILLA_BASH`, `vanilla_command`, `run_vanilla`. |
| Gives | `Prebuilt`, run dirs, `run.json` with fingerprints. |
| Needs | Nothing. |
| Offline tests | Fingerprint from fixture transcripts; `compare` refuses on a changed tool list, model, CLI version, or missing fingerprint; `sandbox.mounts()` contains neither `~/.claude` nor the package tree; all arms produce the same argv apart from prompt and session flags; snapshot selection for `@matched`; with `FakeHarness`, a full `base → self-review` flow. |
| Real check, cap $2 | (1) $0.05: a clean config dir authenticates. (2) $0.50: a sandboxed worker told to list `~/.claude`, `~/factory-packs`, `~/factory-bench` and `/opt/repo_factory/src` reports all but `harness/netproxy.py` absent. (3) $1.50: `base` on `examples/tasks/repo-03-ttlcache/spec.md`, then one `self-review` round; costs and fingerprints land in `run.json`. |

**Track B: packs, scoring, statistics, CLI** (3–4 days).

| | |
|---|---|
| Scope | `hsuite/{pack,runner,score,adapter,stats,compare,prereg,cli}.py`; the `bench` command group; port lending: `review6/prior/scenarios.py` → `suite/scenarios/`, the reviewer's `Impl` → `driver.py`, deterministic topics from `review6/_work/extra.py`, `conformance.py`, `manifest.json`. |
| Gives | `score.json`, `compare/*.json`, the pack loader. |
| Needs | Track A's `launch` for the adapter writer (stub with the hand adapters until it lands). |
| Offline tests | **E0 (below) is this track's main test.** Also: manifest rows missing from a run count as failures; `Unsupported` with `on_unsupported: fail|pass`; build-hash check catches an adapter that edits the build; permutation test and ratios on synthetic data with known answers; `prereg` refuses to overwrite and `compare --prereg` evaluates each clause. |
| Real check, cap $3 | Auto-adapter for `review6/B` (no branch on checkout): must pass conformance, raise `Unsupported("checkout.branch")`, and reproduce the hand adapter's 51/53 exactly. |

**Track C: the pack builder and the new specs** (4–5 days, the critical path).

| | |
|---|---|
| Scope | `hsuite/build/{refs,contract,author,fuzz,adjudicate,mutate,freeze}.py` and their prompts; `bench pack new|build|rule|validate`; write the five specs and `owner.md` files from the template; build the packs. |
| Gives | Frozen packs in `~/factory-packs`. |
| Needs | Tracks A and B. |
| Offline tests | With `ScriptedLLM`: stage sequencing and resumability (`--stage`); row classification table; mutant application, kill matrix and equivalent-mutant bookkeeping on a toy pack (`tests/fixtures/repo_task`); gates fail a suite with a flaky row or a 0.99 ref pass rate; `validate` detects a changed suite file by hash. |
| Real check, cap $25 | Build `jobq` end to end. Passes its gates, or the failure report says which gate and why. |

**Track D: the v5 pipeline** (5–6 days).

| | |
|---|---|
| Scope | `repo_factory/v5/*`, `policies/v5/*`, the `cmd_build` dispatch, `rules/score.run_rules(overlays=)`, the events and `fold` additions, `v5/report.py`. |
| Gives | `v5.pipeline.build`, `claims.json`, `findings.json`, `prediction.json`. |
| Needs | Track A's `launch`; nothing from B or C (it never sees a pack). |
| Offline tests | `accept.decide` and the conflict protocol on a **fixture cut from run 10's events** (two rules fixed, one rank-5 test broken → pair adjudication, per-finding revert, q27's fix kept); authority shortcuts; the held split never exports a held scenario; budget reserve blocks stage 6 at the cap; Chapman estimate on hand-computed cases; a whole pipeline run with `FakeHarness` and `ScriptedLLM`; `repo-factory status` and the UI fold render a v5 log. |
| Real check, cap $6 | A **known-answer test**: run stages 2–8 on `review6/A`, vanilla's own build. Its builder session no longer exists, so the repair runs as a fresh session on the repo. Review 6 lists its defects: the in-transit cancel teleport, no location check on shelf checkout, the zero-day renewal, 500s on hostile input. Report how many the verifier finds, how many the panel confirms, how many the repair fixes, scored with the hand adapter. Expectation, not a gate: at least 2 of 4 found. |

**Track E: lessons and history** (3 days, starts after B's formats exist).

| | |
|---|---|
| Scope | `repo_factory/lessons/*`, `repo-factory lessons list|postmortem|credit|promote`, `bench history`, the `/history` page. |
| Needs | `score.json`, traces (B); `claims.json`, `findings.json` (D). |
| Offline tests | Lint accepts and rejects a table of examples (domain nouns from every pack's `domain_terms`); `for_build` never returns a lesson born on the pack; credit counters from fixture findings; cap and eviction order; probation rules; `promote` refuses after 5 held-out looks; history rendering from a fixture `history.jsonl`. |
| Real check, cap $1 | A post-mortem on run 10's five extra-topic failures (rows and traces rebuilt from `review6/_work/extra.py` output). At most three lessons, all passing the lint; `miss_type` set for each. |

### 6.2 Experiments, in order

Each has a pre-registration file written before it runs.

| # | Days | Experiment | Budget | Success | Kill or fallback |
|---|---|---|---|---|---|
| **E0** | 2–3 | **The harness reproduces review 6.** Score `review6/A` and `review6/B` with the ported suite and the two hand adapters. | $0 | Exactly 53/53 and 51/53 on the shared rows; the deterministic extra topics match `VERDICT.md` row for row. | Any mismatch is a harness bug. Nothing else starts until it is fixed. |
| **E1** | 3–4 | **Noise floor.** Five base builds on lending; two independent adapters on two of them; score all. Re-score the stored outputs of runs 9 and 10 (`runs/009/task/out`, `runs/010/task/out`) and vanilla's `review6/A`. | $25 | No pass/fail. Outputs: σ_build, σ_adapter, a corrected history table with error bars. | σ_adapter > 1 row: fix §2.5 before E2. σ_build > 6 points: 3 seeds per pack downstream. |
| **F1** | 3 | **Free: ambiguity listing.** The claims prompt on the lending spec, against the six disputed topics. | $1 | ≥ 3 of 6 listed. | < 3: drop the ambiguity list from claims; interpretations then come only from panel splits. |
| **E2** | 4–8 | **Packs.** Build the five new packs; the owner rules interpretations for lending and the two held-out packs. | $110 | ≥ 4 of 6 packs frozen with all gates met; vanilla core pass rate between 0.60 and 0.95 on each. | < 4 after one retry each: the automated authoring isn't reliable. Fall back to the owner's assistant reviewing every adjudication, and run E3 on 3 packs. |
| **E3** | 8–11 | **v5 against both baselines, dev packs.** 8 base builds (lending's reused from E1), all four arms, 2 σ_post replicates. Criteria: the file in §2.8. | $170 | `WIN`: all success clauses. | `COLLAPSE` or `KILL` → decision D2. `INCONCLUSIVE` → one more seed per pack (+$60), once. |
| **E3h** | 11–12 | **Held-out confirmation** (only after a dev `WIN`): 4 base builds on `shiftboard` and `calcsheet`, all arms. | $80 | Pooled dev + held-out permutation p ≤ 0.05; v5 not worse than `self-review@matched` on either held-out pack. | Dev win, held-out loss: overfit to dev. Rotate packs, don't change default policy. |
| **E4** | 11–13 | **Lessons, leave-one-pack-out.** Post-mortems on E3's v5 runs → lessons v1. Re-run post-build stages on the 8 stored dev base builds with lessons v1 (each pack gets only lessons born elsewhere), against E3's lessons-free v5 runs. | $65 | Mean core failures lower with lessons, better or equal on ≥ 3 of 4 packs, permutation p ≤ 0.10, cost within 3×. Then the held-out check of §5.5 and the first machine-authored promotion. | No gain: lessons at this granularity don't transfer. Go to D3. |
| **E5** | 12–13 | **Regenerate or repair** (optional). On the 4 E3 builds with the most open defects: regenerate from spec plus clarifications, against repair round 2. | $25 | Regenerated heads have fewer core failures on ≥ 3 of 4, and introduce fewer new failures than they remove. | Otherwise `regenerate` stays `off`. |
| **F2** | 12 | **Free: calibration.** Score `prediction.json` from E3 and E3h (12–16 runs) against the truth. | $0 | Mean absolute error below the constant guess. | Not better: ship no publish gate on it; revisit with more runs. |
| | | **Real checks of the five tracks** | $37 | | |
| | | **Total** | **≈ $450–510** | | |

### 6.3 A lean variant (≈ $330)

Three new packs instead of five (`ledgerbook`, `jobq`, `calcsheet`), so three dev packs in E3 (`lending`, `jobq`, `ledgerbook`) and one held-out pack, no `fresh-review` arm on held-out, no E5. It gives 6 paired dev units: the permutation test's best possible p is 1/64, so one unit going the wrong way already makes the result inconclusive. Opinion: take the full plan; the lean one is likely to end in "inconclusive", which is the outcome the last ten runs kept producing.

### 6.4 Decision points

| | When | Question | Outcomes |
|---|---|---|---|
| **D0** | after E0 | Is the harness faithful to the review? | Must be yes. |
| **D1** | after E1 | Was anything in the run 2–10 table signal? | Publish the corrected table. Sets seeds per pack. If σ_build is so large that 2–3 seeds can't resolve a 25% effect, raise seeds before E3, not after. |
| **D2** | after E3 (and E3h) | Does post-build search beat budget-matched review? | **WIN** → `DEFAULT_POLICY = "v5"`, delete the deprecated list, go to E4. **COLLAPSE** (v5 ≈ fresh-review, both > self-review) → ship "fresh review" as the pipeline, keep claims and mutation only if a two-arm ablation pays, still run E4 on the collapsed pipeline. **KILL** (nothing beats self-review) → stop competing on single-task quality, as `REVIEW.md` §7 said: the product becomes verification assets (the delivered `tests/verification/`), calibrated confidence and unattended throughput for the closed loop. The harness and packs remain useful in every branch. |
| **D3** | after E4 | Can the factory improve without a human? | Yes → unlock tier 1, and the changelog has its first machine entry. No → try tier 1 directly from the `miss_type` histogram (one $65 attempt); if that fails too, state plainly that self-improvement is not demonstrated, and stop building machinery for it. |
| **D4** | after F2 | Does the factory know when it is wrong? | Yes → the publish gate for issue #12 can be designed on `p_hidden`. No → the closed loop needs a human look before publish. |

---

## 7. Risks, and what I'm least sure about

Ordered by how much they could invalidate the plan.

1. **v5 may not beat a fresh reviewer.** Opinion: roughly even odds. The blind reviewer that found nine bugs in run 9 was itself one fresh Claude session, with the advantage of comparing two builds. v5 adds structure (spec-only claims, mutation, the panel) around that; whether structure beats one well-briefed session at equal spend is exactly what E3 tests. The plan is built so that this outcome is cheap to see and still leaves a usable product.
2. **Auto-adapters are the weakest link of the objective.** An adapter that quietly fixes or breaks a build corrupts every score. Mitigations exist (conformance, the build hash, `Unsupported`, triage, two adapters in E1), but this is new and untested. If σ_adapter is above one row, the fallback is hand-reviewed adapters for base builds (about 16 in E3), with auto-adapters only for later snapshots.
3. **Suites written and adjudicated by the same model family as the verifier.** Shared blind spots cut both ways: the suite misses what the verifier misses (scores look better than reality), and lessons may learn "how the suite author thinks". Mitigations: a different author prompt for held-out packs, the occasional blind review as a suite audit, owner rulings on interpretations. Not solved.
4. **Ceiling.** If vanilla passes above 95% of core rows on the new packs, there is nothing to win. The headroom gate catches it per pack, but the fix (harder specs) raises vanilla's cost and so every arm's.
5. **Power.** Eight paired units detect only large effects (about a quarter fewer failures). A real 10% improvement would read as "inconclusive". I think that's the right trade at 3× cost, but it means small steady gains from lessons could be invisible; E4's criteria may be too strict for a first iteration.
6. **Lessons may be too generic to matter.** The builder's model already knows "test both sides of a boundary". The bet is that a short list **ranked by measured hit rate** changes what the verifier does first under a fixed budget. I'm least sure of this part. The `miss_type` histogram is the fallback: it can steer tiers 1–3 even if lessons do nothing.
7. **Clean config dirs.** Authentication with a fresh `CLAUDE_CONFIG_DIR`, and `--resume` across forks under it, are unverified on the build host. Track A probes both on day 1. If they can't be made to work, fall back to one dedicated config dir per role (builder, verifier, scoring, pack building) that is wiped per tag: weaker isolation between runs, the same isolation between roles.
8. **Cost margin.** The $8.30 target leaves $2.80 under the limit, and the verifier is the stage most likely to overrun. Caps are hard, so the failure mode is a truncated verification, not an overspend; the report must say when a stage hit its cap.
9. **The verifier's copy of the repo is writable.** It must be, to run things, but it means the verifier could "find" defects it caused. The harness re-runs every scenario on a pristine copy (§3.4), which covers it; scenarios that pass only on the verifier's copy are dropped.
10. **Capture–recapture with tiny counts.** With 3–6 defects per build the Chapman estimate is coarse, and the two claim samples aren't truly independent (same model, same prompt). Expect it to under-estimate. F2 measures whether it still beats a constant.
11. **Owner time.** Rulings are about five minutes per pack, and held-out packs can't be auto-ruled. Six packs is half an hour; it is on the critical path of E2.
12. **Shared base builds make v5 inherit vanilla's build exactly.** A future v5 that changes the build stage (lessons to the builder, a cheaper builder) loses the pairing and needs unpaired comparisons with more seeds. That's the right order: prove the post-build stages first.
13. **Schedule.** Track C plus E2 is nine working days on the critical path if everything works the first time. If the two weeks slip, cut E5 and the `mdsite` pack before cutting seeds.
14. **Not addressed:** a security gate before public release (run 9 shipped an open redirect; vanilla shipped no authentication), non-Python targets, and scenarios needing real servers. Each is a known hole in "builds anything, autonomously".


---

## Addendum A (2026-10-04, from the owner): operator-configurable business rules

**Principle.** When the spec leaves a *binary* business rule open, the delivered software
exposes it as an operator setting with a sensible default. Examples:
- may a copy be checked out at a branch other than where it sits;
- may a member pay more than they owe.

Nobody spends cycles deciding it, nobody asks the owner, and nobody forks a build over it.
Anything that isn't a clean two-way policy switch stays the implementation's call. It is
recorded in `ASSUMPTIONS.md` and never asked or forked. Example: how late fees count days.

**Classification.** Every ambiguity, whether from claims, panel splits or a pack's
`interpretations.yaml`, gets a `kind`:

| kind | Test | Handling |
|---|---|---|
| `binary` | Two reasonable alternatives of a business policy that an operator could meaningfully want either way, with no new mechanics needed beyond a guard. | A **setting**: a named key, a documented default (the expected reading, or the owner's ruling where one exists), and a scope where it's natural. Scopes can be global, per site, or per *pair* of sites (e.g. `cross_branch_checkout: allowed between A↔B`). |
| `complex` | Calculations, multi-way choices, mechanics, anything needing design. | The implementation decides, and records it in `ASSUMPTIONS.md`. |

**Pipeline (v5, §3.3–3.5).**
- **Claims:** ambiguities carry `kind`, plus for `binary` items a proposed `setting`:
  `{key, values, default, scope}`.
- **The build prompt stays the vanilla prompt** (pairing, §0.3). Settings are added after
  the build: binary ambiguities the build doesn't already expose become repair items of class
  `setting`. That means: add the setting, default it to the expected reading, document it in
  the README's configuration section and in `ASSUMPTIONS.md`, and add tests for both values.
- **`interpret` modes:** `ask` and `fork` no longer apply to `binary` items; they're always
  settings. For `complex` items the implementation decides. `ask` remains only where the owner
  explicitly turns it on, and `fork` is retired.
- **The verifier tests binary items under both values.**

**Packs and scoring (§2.1, §2.2, §2.6).**
- `interpretations.yaml` entries gain `kind: binary|complex` and, for binary items,
  `setting: {key, values, default, scope}`. The owner's ruling becomes the **default**.
- `Driver` gains `configure(rule_id: str, value) -> None`. It raises `Unsupported("config.<rule_id>")`
  when the build has no such setting.
- **Rows for a binary item:**
  - its `ruled` row is scored under the build's default configuration, as today: is the
    default the owner's reading?
  - **config rows** run the scenario with the setting forced to each value.
- `score.json` gets a third score, **`config`** (binary items whose both values behave
  correctly). It's reported next to `core` and `interp`.
- **`core` is never affected by config rows.** Vanilla isn't asked for configurability, so
  `config` measures what the factory adds; it isn't a penalty in the core comparison.
- **`complex` items are `either`:** reported, never scored.

**Lending, re-classified.**

| Item | kind | Handling |
|---|---|---|
| I1 shelf hold | binary | `hold.claims_shelf_copy`, default true |
| I2 renewal base | binary | `renewal.base`, values `due_date` / `today`, default `due_date` |
| I3 hold on a borrowed title | binary | `hold.allow_on_borrowed`, default false |
| I4 walk-in releases own hold | binary | `checkout.releases_own_ready_hold`, default true |
| I6 shelf checkout location | binary | `checkout.cross_branch`, default not allowed, scope per branch pair |
| I7 overpayment | binary | `payment.allow_credit`, default false |
| I5 fee day-counting | complex | the implementation decides; the row stays unscored (`either`) |

## Addendum B (2026-10-04, from the owner): scope ideas, verify depth

**Why.** The owner triggers the factory on short, broad ideas found on the internet (later
from an idea feed), not on tight specs. Given an idea, a builder delivers the obvious slice: stubs,
dead buttons, missing flows, an early stop. The depth has to come from scoping.

- **Stage 0, `scope`** (`repo_factory/v5/scope.py`, `prompts/scope.md`, `rules.yaml` `scope:
  auto|always|off`). `auto`: a deterministic heuristic (length, numbers, named interfaces,
  rule words, structure) passes a spec through with no call; an idea, or a borderline input,
  gets one no-tools call with the builder's model, which may still answer `PASS`. The call
  writes `SCOPE.md`: users, 6–10 numbered end-to-end flows (F1 is always install and first
  run), must-have vs later, domain rules with defaults marked *(default)*, binary business
  choices as operator settings (Addendum A), money and data integrity, the one-command
  install with demo data and a first admin, the non-functional basics, and a definition of
  done (one line per flow). No tech-stack mandates unless the idea implies one.
- **The build prompt** is the idea, "Here is the product scope for v1:", `SCOPE.md`, then the
  vanilla suffix. **A scoped build no longer shares the vanilla base build** (§0.3's pairing
  holds only for inputs that pass through); `scope` event and run.json `shared_base: false`.
  Claims, the verifier, the panel and the repair read the idea plus `SCOPE.md` as the spec;
  `SCOPE.md` ships in the repo.
- **Depth in verification.** The verifier installs from the README on a fresh copy and walks
  every definition-of-done flow end to end through the real interface, one scenario per flow
  (`# flow: F<n>`). Missing or partial flows, stubs, TODOs, dead controls and placeholder data
  are findings of class **`incomplete`**: no panel, straight to repair; rank 2 for a
  definition-of-done flow (all must-have), rank 3 otherwise. The **completeness score** is
  flows whose scenarios all pass in the harness's last run / flows in the definition of done.
- **A spec that passes through has a definition of done too** (EV7, 2026-10-18). Without one
  completeness could not be scored, and the gate held every precise spec for it. `do_scope`
  draws it from the spec's own text, with no model (`scope.spec_dod`): its acceptance lines,
  else its numbered or bulleted requirements, else its sentences (a sentence's `; ` parts
  split when each names its own command or endpoint), at most 12 flows, the shortest
  neighbours sharing one. `scope.json` records `dod_from: "spec"`; the verifier gets the
  prompt's `spec_scope` section (walk the spec's requirements, one scenario each) instead of
  `SCOPE.md`'s. When nothing can be drawn, completeness is a gap on the gate, not a hold (a
  round where nothing at all was measured still holds). Both flows.
- **Never a red suite.** At delivery a scenario still failing is marked
  `xfail(strict=False, reason=<finding id: title>)` and listed as open in REPORT.md; the
  delivered tree's whole suite is run once; the build's own failing tests are reported at the
  top of REPORT.md. ASSUMPTIONS.md and REPORT.md describe only settings that exist (`added`,
  `already in the build`); the rest are under "Not implemented".
- **Scenarios run where the verifier ran them** (`_verify/scenarios/`). The lending run's q22
  was not a networking problem: the sandbox has its own network namespace with loopback up,
  but the harness ran scenarios one directory shallower, so q22's path to the repo was wrong.
  Backstop: a scenario the verifier ran green that fails in the harness on the build *and* on
  the verifier's copy is class `environment`, never repaired.

## Addendum C (2026-10-04, from the owner): engineering readiness

**Why.** The owner wants products that are well set up to become something: not full
production grade, but a v1 an engineer could take to production. The head-to-head (v5 against
vanilla on three ideas) marked v5 down on what a readiness pass catches: an unauthenticated
memory-exhaustion bug on a public endpoint (a negative `Content-Length`), 500s on huge ids,
demo data and a LAN-wide bind by default, a shipped test suite with stale or mislabelled xfail
marks, factory jargon in user-facing files, and a scope that cut the breadth the idea named
("many SaaS tools").

**Stage `readiness`** (`repo_factory/v5/readiness.py`), after re-verify and before delivery,
in resumable steps (`Run.ready["next"]`):

| step | what | cap (`rules.yaml` `readiness:`) |
|---|---|---|
| `security` | a fresh full-tools session on a copy: a threat model (entry points, who reaches them, assets), then probes of the running product (authN/authZ, hostile HTTP, injection, auth, secrets and defaults, a dependency audit, error pages); each finding has a severity, a hand repro and a **probe** (a pytest test asserting the secure behaviour) | $1.20 |
| `checks` | a fresh session does the operational checks: a clean install from the README, safe defaults (localhost, empty start, first-run admin), configuration, data location, backup and restore, restart (at once, after a stop and after a SIGKILL), shutdown, health, logs, upgrade story, with probes for testable red items, and says how to run the service (`run`); the harness itself checks CI, the lockfile, `.gitignore` and the README's sections | $0.60 |
| `commands` | the harness runs the README's commands **as written** on a fresh copy of the tree as it will be delivered (`commands.py`, V5-POLISH below): the quick start, an immediate restart, every documented test command | — (no model call) |
| `repair` | the plan (below), and the measure of the head before the round | — |
| `session`, `accept` | one round through `repair.start_round`/`finish_round` (acceptance, the conflict protocol, drops), in a **fresh** session | $1.50, minimum $0.50 |

- **Probes are scenarios.** They join the rule store, so the harness confirms them (a probe
  that passes in the harness's run is *unconfirmed* and not repaired), guards them as
  regressions in the round and later, and ships them in the acceptance suite.
- **What is repaired.** Open critical and high findings, class `security`, rank 2. Medium
  findings go too when the full round is affordable (`medium: if_budget`). Low findings are
  only documented. Fixable red readiness items go too (class `readiness`, rank 3), and so
  do the amber ones the harness checks itself (documentation-sized). Items without a test
  are judged by the harness's own check after the round, or by a kept commit that names
  them (`repair.extra`).
- **The safe defaults are the owner's standing rule** (rank 1). These are localhost, an
  empty start and a first-run admin with no default password. A flow scenario that encoded
  the old default (demo data after a plain install) must not revert them. The repairer sees
  every visible scenario and may update one that relied on the old default, changing only
  how it starts the product. The update becomes the scenario's new version, still active,
  and is listed in REPORT.md.
- **Budget.** Under a global cap, the stage starts only with `start_usd` ($0.30) left, the
  round runs only with `min_usd` ($0.50) left, and a second repair round keeps the stage's
  three caps ($3.30) in reserve. **Since V5-POLISH there is no global cap by default**
  (`max_usd: null`, the owner's standing rule): these reserve and minimum rules then never
  apply and no stage is skipped for lack of global budget. A cap still applies when given
  (`--max-cost`, the task's `budget_usd`, `$REPO_FACTORY_V5_MAX_USD`, a number in `max_usd`, the
  bench's 2.95x vanilla). The stage and session caps stay either way, as runaway protection
  for single sessions.
- **Fresh session, not a fork of the builder.** In the real check, forking a build whose
  prompt cache had expired re-cached its whole transcript and cost $1.00–1.36 before the
  first edit. A fresh session with the same budget fixed three items. A fix-up still
  resumes the round's own session.

**Delivery.**
- `SECURITY.md`: the threat model, what was checked and how, the findings with severity and
  status, open ones with a repro and their test, the dependency audit, known limits, and how
  to report an issue.
- `READINESS.md`: the scorecard (green, amber, red or n/a per item, with why and the next
  step to production), and the known open items in the tests.
- **`tests/acceptance/`** replaces `tests/verification/` (`acceptance.py`):
  - behaviour-named files with a plain docstring;
  - no claim ids, `test_rule_*` names or process headers;
  - helpers in `_helpers/`, with paths computed as if one level below the repository root,
    where they were written. In the delivered tree they sat one level deeper, and that was
    the stale marks' cause: 10 of library's 11, and 97 of freelance's 100.
- **Every mark is re-evaluated against the delivered head** by running the delivered suite.
  A test that fails there is marked xfail with a plain reason; one that passes carries no
  mark. The marked suite runs once more, to show it's green. Open items are listed in
  `tests/acceptance/README.md` and READINESS.md.
- **No process jargon in user-facing files:** README, ASSUMPTIONS, SECURITY, READINESS and
  the tests (`acceptance.scan`). ASSUMPTIONS.md is plain: no ids, votes or "as first
  written". README paths to the old suite are rewritten, and a README that doesn't say how
  to run the acceptance suite gets a short section. REPORT.md stays the factory's report.

**Scope breadth** (`prompts/scope.md`): breadth the idea names is must-have, covering the
common real-world cases of the range, not a token one or two. The install section asks for
the safe defaults above.

### V5-POLISH (2026-10-05): the README's commands, run as written; the immediate restart; no global cap; cost trims

**Why.** In the round-2 head-to-head, all three v5 products shipped a README whose test
command failed as written (`./shelfdesk.sh test` and `unittest discover` imported the
pytest-only acceptance suite: 109-128 errors; a path, `acceptance/findings`, that existed only
during the build), while their READINESS.md said the documented test commands ran green. The
webhook relay couldn't start again for about 60 s after a stop (`allow_reuse_address =
False`: connections left in TIME_WAIT block the rebind).

**The `commands` step** (`repo_factory/v5/commands.py`, `cmdrunner.py`), after `checks`:
- **Extraction.** The README's quick start (its first install-like section, without the
  subsections that are alternatives or extras: Docker, by hand, systemd service, demo,
  Windows/macOS), one runnable backup command, every documented test command (fenced and
  indented blocks anywhere, inline code that looks like a command), and the commands of
  `tests/acceptance/README.md`. `git clone ... && cd ... &&` is the fresh copy itself.
- **What isn't run**, with the reason recorded: root, systemd, Docker, the network,
  placeholders (`<file>`, `/path/to`, `FILE`, `[--demo]`), interactive commands, restores
  (the operational checks do backup and restore), Windows. Dependency installs are
  satisfied by the harness's environment (the build's declared dependencies and pytest);
  there is no network. A skipped step that installs the product itself (`pipx install .`,
  `pip install -e .`, `uv tool install .`, `uv sync`, `poetry install`) still puts its
  console scripts (`[project.scripts]`) on PATH for the steps after it: a launcher per script
  that runs it from the fresh copy (before this, every command after `pipx install .` failed
  with exit 127: Repo Recall r1–r5, Nextread r1–r2). An install script that runs pip or pipx
  itself gets the exam's wheelhouse of build backends (`pyinstall.py`) with
  `PIP_FIND_LINKS` and `PIP_NO_INDEX` (Sidedoor r5–r10's `./install.sh` failed on pipx
  fetching setuptools). A command that asks for input (EOF on stdin) or needs a tool the
  sandbox lacks (`uv`, `make`) is not a README failure.
- **Where.** One bwrap sandbox: a private filesystem view, a throwaway `HOME`, its own
  network namespace with only loopback. Every server the product starts listens there, on
  127.0.0.1, and nowhere else (a host may alert on new LAN ports). Without bwrap
  nothing is run and the items are amber, "not checked".
- **The tree.** A copy of the build *as it will be delivered* (`readiness.preview`): no
  `acceptance/findings`, the plain `tests/acceptance/` with its README and the open items
  marked, the README's paths fixed. That is what made the round-2 test commands fail, so
  that is what the commands must work on.
- **Phases.** *quick*: each quick-start command in order; a start command (`serve`,
  `start`, `run`...) that starts listening is a server and keeps running, as in a second
  terminal; any other command gets its step's time to finish (a demo that opens a port on
  the way, Sidedoor's `sidedoor demo`), and is a server only if it still listens when that
  time is up; then one backup. *restart* (services):
  start (the checks session's `run` recipe, else what served in the quick start, else the
  README's other start commands), five requests each on its own connection (the server
  closes first, so its side is left in TIME_WAIT), SIGTERM, start again at once and check it
  serves within 15 s; five requests, SIGKILL, start again at once, check it serves. *tests*:
  each documented test command, after the install steps, with a 240 s timeout each.
- **Items** (`how: "run"` in `readiness.CHECKS`): `commands` (the quick start; a library
  whose quick start only installs it is n/a), `restart_now`, and `tests_green` (now "The
  documented test commands run green, as written"). A failure is red and fixable:
  `commands` and `tests_green` are documentation- or script-sized (rank 3); `restart_now`
  (and the session's `restart`) is a reliability bug that loses requests (`basis:
  "reliability"`, rank 2). The readiness round works through its items most severe first.
  The release gate holds on a red `commands` (with `bind_localhost`: `gate.SAFE_ITEMS`,
  the `safe_defaults` check; a red quick start may be published over, an exposure never);
  the checks session's own `install`, `empty_start` and `first_admin` are advisory rows,
  shown in READINESS.md but never a hold. The session said "install green" in 31 of 32
  scorecards while this run was red in 22 of 34 (AU1 #6). `bind_localhost` stays the
  session's: the run sees which ports listen, not the address they are bound to.
- **Repair.** The brief quotes the commands, their exit codes and the tail of their
  output. For `tests_green` the delivered `tests/acceptance/` is placed in the workspace for
  the session (git ignores it there; it is removed before any measure), so the repairer can
  run the commands as they will run. After the round the harness runs the commands again
  on the repaired tree; green is fixed (`repair.extra`).
- **Delivery.** `finalize` runs the quick start and the test commands once more on the
  delivered tree itself: a test command that fails as written makes `tests_green` red,
  whatever the harness's own suite run said. `fix_readme_paths` also maps the build-time
  `acceptance/findings` to `tests/acceptance`.

**No global cap by default** (`max_usd: null`): see Budget above. `Budget(None)` is
unlimited; `allows` is always true, `left` infinite, the stage windows still bind. The UI's
cost line shows "no cap" (or "cap $X").

**Cost trims** (data: READY.REPORT's table; the ledgers of the three h2h runs):
- `mutants.survivor_round: false`. It cost $0.25-0.36 per run and found no failing scenario
  in any (9, 5 and 4 survivors killed by new passing scenarios). The mutation score, which
  feeds only the prediction, stays.
- `claims.samples_scoped: 1`: a scoped build takes one claim sample and makes no dedupe call
  (-$0.45/run); its verifier walks SCOPE.md's flows anyway. Spec builds keep 2.
  **Calibration with one sample** (`calibrate.predict(samples=1)`, model `v5.0-1s`): no
  capture-recapture term, so the unfound defects are unknown (None, not 0). The point
  estimate counts only what is known; the interval's lower end allows as many unfound
  defects as were found (at least one), on top of the usual one either way.
- **Re-verify only when a round can follow it** (`Run.no_further_round`). Under a cap it is
  skipped unless re-verify's own cap, round 2's $2.00 and the readiness reserve fit (in two
  of the three h2h runs it found a defect that then had nowhere to go). Without a cap only
  `repair.max_rounds` stops it, so what it finds always gets the next round; after the last
  allowed round it is skipped in both modes, for the same reason.
- **A repair round's session: measured, then chosen** (`Run.resume_choice`,
  `repair.resume: auto`, `warm_minutes: 30`). It resumes (forks) the builder's session only
  when that session was last used less than 30 minutes ago, so its prompt cache is likely
  warm; otherwise a fresh session with the "you didn't write this code" brief (a cold fork
  re-caches the whole build transcript: $1.00-1.36 before the first edit). The round
  records which and why (`rounds[].session`, the `repair_round` event's `session`, REPORT.md's
  rounds table).

## Addendum D (2026-10-09, track X): no stage caps by default; a failed build stage; the login

**Stage caps are settings, none by default** (the owner's rule: spend is uncapped by default,
and every cap is configurable). §3.1's fixed caps (build $6.00, verify $2.50, readiness
$3.30, …) were a leftover: the sidedoor build's session stopped at $6.02 with
`exit_reason: max_budget`. Now:
- `rules.yaml` `build.stage_caps` (default `{}`) and the loop's `loop.yaml` `build.stage_caps`
  (laid over it, passed as `$REPO_FACTORY_V5_STAGE_CAPS`; `null` removes a policy cap) are the
  stage caps. Each stage section's `max_usd` (default `null`) is one session's own cap,
  and so are `repair.fixup_usd` and the readiness steps' `max_usd`.
- `Budget.session_cap` is None when nothing caps a session (no session `max_usd`, no stage
  cap, no global cap): the session runs with **no** `--max-budget-usd`. With caps set,
  the windows (a repair round's own, each readiness step's) and the reserve rule work as
  before; under a global cap the reserve reads `common.TYPICAL_USD` (§3.1's old figures)
  for a stage without a cap.
- What stops a runaway without dollars, per session: `max_turns` (`--max-turns`) and
  `timeout_s` (wall clock, the session is killed). Build 250 turns / 2 h; verify 60 / 30
  min; re-verify 40 / ~7 min; repair 60 / 30 min; readiness security 60 / 20 min, checks
  40 / 15 min, repair 60 / 25 min.
- Settings › Spend's JSON (`/api/spend/caps`, and `caps` in `/api/spend/breakdown`) lists
  every cap and limit; `POST /api/spend {"stage_caps": {...}}` sets the loop's.

**A failed build stage stops the build** (sidedoor's retry: the build session failed after
$0.34 on the login, and the pipeline still delivered it, `build_finished ok`, readiness red).
A stage that can't go on raises `StageFailed`: the build ends `build_finished status: error`
with `stage`, `failure` (`auth | stopped | error`) and the reason; claims through deliver
don't run; the step stays not done, so resuming the round runs it again. The build stage:
- an **error** (or a login failure) fails it;
- a stop on **turns, time or a cap** with no product written fails it;
- a stop that left a tree that **builds and whose tests run** (`Run.tree_check`: the declared
  dependencies install, the tree's own tests run once, network off, failing tests allowed)
  is a **partial build**: the stages go on with that tree (verify finds what's missing,
  repair fixes it), the `build_partial` event and REPORT.md say so.

**Retry keeps the work.** A resumed round (the loop's Retry of a failed round) runs the build
stage again on its committed tree: a fresh session with the build prompt plus "continue the
build already in this directory" (the git log and the files so far), purpose `build2`,
`build3`, … A new round (Retry of a round that was delivered anyway, or `repo-factory build` again)
continues the latest earlier round whose build stage stopped with product in its tree
(`carry_from`; its scope and its tree), unless that round's build stage finished (then a new
build was asked for) or `repo-factory build --fresh` / `repo-factory loop retry --rebuild`. A fresh session, not
the stopped one resumed: hours later its prompt cache is cold (re-caching a $6 transcript
costs more than reading the tree), and a stopped session may have ended mid-thought.

**The login** (`harness/profile.py`). The sidedoor failure: the sandbox's copied credentials
held a refresh token that another Claude Code process on the machine had already rotated
(the owner's file was refreshed at 19:19:11, the session failed at 19:19:38 with "OAuth
session expired and could not be refreshed"). Now: the copy is made right before every
launch (it was), a `Mirror` thread keeps the copy and the owner's file in step while the
session runs (a refresh elsewhere reaches the session's file within seconds, which helps if
the CLI reads it again; one the session makes reaches the owner's file when that is
unchanged), and a run that still ends on a login
failure (`exit_reason: auth`: a 401, or the CLI's own "Failed to authenticate") is retried
once after a fresh copy, resuming its own session. A second failure fails the stage with
"Claude login expired: run `claude` once on this machine, or set up a long-lived token"; the
loop holds it at once (no automatic retry: it would fail the same way), with that reason in
the history and in Needs you. `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` in the
environment is used as is, with no file copied (OPENSOURCE D11's direction); the copy stays the
default. No credential is ever logged or printed.

## Addendum E (2026-10-18, track BF): the simple flow, beside today's

The owner's ask: "instead of 10 different steps and loops, we can get 80% of the value with 4
or 5 steps and one loop". BF-DESIGN.md (repo-factory-notes) is the plan; the owner accepted its
D1-D15 and asked for the simpler flow to be **built beside today's and measured against it**
before anything flips. So it is a second policy, `policies/simple` (`flow: simple`,
`version: "6.0"`), chosen per build (`repo-factory build --policy policies/simple`, or the loop's
`build.flow: simple`); `policies/v5` stays the default.

**Brief → Plan → Build → Check ⇄ Fix → Publish** (`repo_factory/v5/simple.py`, over the same
stage library):
- **Plan** (`plan`): today's scope call (and the study), then the checklist: the claim list
  without ambiguities. No product settings are minted from ambiguities (D4); the settings
  the plan itself names stay (rule 10).
- **Build** (`build`): unchanged.
- **Check** (`check`): the exam writer (today's verifier prompt), the security probe
  (today's, D12) and the readiness checks run **at once** on their own copies
  (`check.parallel: 3`, D9); then the harness with no model (every scenario with the
  3-of-5 rule, the probes, the README's commands as written, restart, the deterministic
  items); then **one judge** (`check.votes: 1`, D3) over the failing scenarios: `defect`
  (to Fix), `wrong test` (retired), `interpretation` (recorded in ASSUMPTIONS.md with the
  build's reading, retired, never repaired: D5). A helper module a parallel session wrote
  under the exam writer's name is renamed with its imports, so each probe keeps its own.
  One judge's `defect` ranks as a panel majority did (rank 3) in the conflict protocol.
- **Fix** (`fix`): one builder session on **one findings list**, most severe first (open
  critical and high security findings, the defects, medium findings, the fixable readiness
  gaps), with §3.5's strict acceptance (`repair.run_round`). Then Check again on the new
  head: the harness, a re-probe of the security findings the round fixed (`fix.reprobe:
  security`, D13; `all` also resumes the exam writer, §3.7's re-verify), the judge on what
  newly fails. Another round while something fixable is open, up to `fix.rounds` (2, D2;
  the loop's `build.fix_rounds`). The loop's line: "fixed N, open M; why".
- **Publish**: §3's delivery, the gate, the pre-push checks and publish, unchanged.

**Ask for changes and Retry after a hold** on a simple-flow build (`simple_change.py`, D6,
D7): the loop once more on the delivered tree: the harness checks it, then the fix rounds
with the owner's request (or the hold) as the first item; the request is rank 1 in a
conflict. Docs mode changes Markdown only. There is no separate automatic repair round.

**Kept by design:** the records (`scope.json`/`SCOPE.md`, `claims.json`, `findings.json`,
`readiness.json`, the exam and its evidence, `gate.json`), the event types and `pipeline:
"v5"` (D10), so the gate, Re-check, the corpus, publish and the build page read either flow.
The step names are new (`plan`, `check`, `fix`; a round is a `fix_round` event), and the
build page's six cells read both (BF-0). `SCOPE.md` keeps its name while both flows run: the
comparison shares one plan and one build between them (`repo-factory build --prebuilt <round dir>`
starts a round after the build with an earlier round's plan and its build before any
repair). Proofs A and B (BF-DESIGN §8) decide the default; the flip and the removal of
today's paths are a later change.

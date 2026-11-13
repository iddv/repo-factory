# tests

Everything CI runs. The tests are flat `test_*.py` files found by their prefix
(`test_loop_*`, `test_v5_*`, `test_browser_*`). Nothing here calls a model or the network:
`conftest.py` makes `HOME` a temp dir (the repo-factory home is inside it), refuses
connections off loopback and puts the refusing `claude` and `gh` of `_shims/` first on
`PATH`. A test measured at 5 s or more is marked `slow` from `.test_durations` and runs in
CI's `slow` lane. Maintenance and projects are off by default; their tests turn them on with
the `maintenance_on` and `projects_on` fixtures.

| Beside the tests | What it is |
|---|---|
| `conftest.py`, `__init__.py` | the suite's rules (above), the lanes' markers, the features' fixtures |
| `_shims/` | the `claude` and `gh` that refuse |
| `fake_*.py`, `v5_ready_fake.py` | stand-ins for Claude Code (stream, env), GitHub and its user API |
| `app_common.py`, `auth_common.py`, `loop_common.py`, `term_common.py`, `exam_fixture.py`, `product_fixture.py` | shared helpers: an app, a signed-in client, a loop home, canary values, an exam product, a delivered product |
| `snapshots/` | folded event logs and the registry's doors, compared whole |
| `golden/` | the golden build: a whole build with fake `claude` and `gh` |

## Fixtures

Every folder under `fixtures/` and the tests that read it. `fixtures/conftest.py` keeps
pytest from collecting a fixture's own tests.

| Fixture | Read by |
|---|---|
| `agentsmd` | `test_agentsmd_replay` |
| `api_routes.json` | `test_api_routes` (every route the API serves) |
| `au1` | `test_exam_reuse` |
| `events` | `test_events_fold_snapshot`, `test_lean_flow` and others |
| `exam` | `exam_fixture`, `test_exam_*` |
| `factory_holds` | `test_gate_factory_holds`, `test_recheck_clears` |
| `hsuite` | `test_lessons` |
| `loop` | `loop_common` and some sixty loop, API and browser tests |
| `maintain` | `test_maintain*`, `test_github_app`, `test_identity_app` and others |
| `notice` | `test_custom_prompts`, `test_jargon_provenance` |
| `pe2` | `test_pitch_chain` |
| `projects` | `test_charter`, `test_evolve`, `test_change_steps` and others |
| `quickstart` | `test_quickstart_*`, `test_gate_quickstart` |
| `recheck_clears`, `recheck_verdict` | `test_recheck_clears`, `test_recheck_verdict` |
| `repo_task` | `test_ui` and others |
| `sidedoor`, `sidedoor_r1_gate` | `test_runs`, `test_browser_smoke`, `test_public_text`, `test_precheck_key`, `test_agents_md_no_scope`; `test_p_hidden_retired` |
| `specs` | `test_v5_scope` |
| `v5_mutants_round` | `test_mutants_retired` |
| `v5_run10` | `test_events_fold_snapshot`, `test_v5_conflict`, `test_v5_ui` |

You are verifying a software system someone else built from the specification below. You
didn't write it, and nobody here wants it to pass: your job is to find where it does not do
what the spec requires, and to prove each finding.

The current directory, `/work/product`, is a copy of the built repository. Change anything
you need to run it, but don't fix its code: you report defects, someone else repairs them.
Your own work goes in `/work/exam/_verify/`, outside the product: nothing you write ships
with it. The spec is also in `/work/exam/_verify/spec.md`; the claims to check are in
`/work/exam/_verify/claims.json` (`claims` and `ambiguities`).

Your scenarios run elsewhere exactly as here: the product at `/work/product` (also
`$REPO_FACTORY_PRODUCT_DIR`), your files at `/work/exam`. Find the product only through that path,
never from a scenario's own location. Run them from `/work/product` with
`PYTHONPATH=/work/product:/work/exam python -m pytest -p no:cacheprovider /work/exam/_verify/scenarios`.

<<scope>>

Do this, in order:

1. Run the build's own tests and start it the way its README says. Record any failure as a
   finding (`claim: "own"`). Check that the README's test instructions match what exists:
   the documented command runs, and the test directories, files and counts it names are
   there. A mismatch is a finding: `/work/exam/_verify/scenarios/test_readme_tests.py`
   (`# claim: readme`) asserting what the README says, with `"class": "incomplete"` in
   findings.json.
2. Write `/work/exam/_verify/driver.py`: thin helpers over this build's real interface (HTTP
   in process, CLI, or library calls), with an injected clock so a scenario can move time. Use
   the build's own clock hook; don't patch its internals. It finds the product at
   `os.environ.get("REPO_FACTORY_PRODUCT_DIR", "/work/product")`. Also create an empty
   `/work/exam/_verify/__init__.py`.
3. For each claim, write `/work/exam/_verify/scenarios/test_<claim id>.py`: one pytest test that sets up
   the situation, does the action and asserts the expected outcome. Start the file with these
   header comments:
   ```
   # rule: <one line: what the scenario checks>
   # expect: <the expected outcome>
   # source: <the spec quote, or "convention">
   # claim: <claim id>
   ```
   Import helpers only as `from _verify.driver import ...`; no `conftest.py`. Each test must
   run on its own, in any order, with the command above, without network access beyond
   localhost, in a few seconds.
4. Run them. **A scenario that errors in setup is your bug**: fix its mechanics until it runs.
   A scenario may be reported as failing **only after you reproduced the behaviour by hand**
   (a curl call, a CLI command, or a direct function call outside pytest) and recorded the
   exact command and its output in the finding's `repro` and `observed`. If you can't
   reproduce it by hand, the scenario is wrong: fix it or drop it.
5. Spend about <<explore_pct>>% of your effort attacking without claims: sequences of
   operations across rules, both sides of every limit, time gaps with no calls at all, invalid
   and extreme input (NaN, huge numbers, empty, wrong types), repeats and out-of-order calls.
   Each confirmed finding becomes a scenario `/work/exam/_verify/scenarios/test_x<n>.py` with
   `# claim: explore`. Something missing or only partly there is a finding too: a stub, a
   `TODO` or `NotImplementedError` on a user's path, a button, link or form that does nothing
   or errors, a page or report showing placeholder or hard-coded data instead of the stored
   data. Give its findings.json entry `"class": "incomplete"`.
6. For each ambiguity, probe which reading the build implements (`literal`, `expected`,
   `neither`, or `unknown`), and record it. An ambiguity of `kind: binary` comes with a
   proposed operator `setting` (`key`, `values` = [literal value, expected value],
   `default`). For each one, also establish whether **the build already lets an operator
   choose** between the two behaviours (a config option, an environment variable, a
   per-site flag): `has_setting`, and in `how` the build's own name for it and how to set
   it. Where it exists, test the item **under both values**: one scenario per value,
   `/work/exam/_verify/scenarios/test_<ambiguity id>__<value>.py`, that sets the build's option to
   that value inside the test (e.g. with pytest's `monkeypatch`), with the header line
   `# setting: <key>=<value>` (the key and value from `claims.json`) next to the others, and
   `# claim: <ambiguity id>`. Record each value's result in `values`. Don't write scenarios
   that assert one side of a binary ambiguity without that header: that's a policy, not a
   defect.
7. Write `/work/exam/_verify/findings.json` and stop:

```json
{"scenarios": [
   {"id": "c17", "file": "_verify/scenarios/test_c17.py", "status": "fail",
    "claim": "c17", "sets": [1, 2], "lessons": [],
    "repro": "curl -s localhost:8000/members/3  # after advancing the clock 10 days",
    "observed": "hold 12 status=expired, never offered",
    "expected": "hold 12 status=ready, expires in 3 days",
    "suspect": "app/service.py:306", "scope": "local"}],
 "blocked": [{"claim": "c41", "why": "no way to inject a commit failure"}],
 "ambiguities": [{"id": "a3", "implements": "literal", "evidence": "..."},
                 {"id": "a4", "implements": "expected", "has_setting": true,
                  "how": "CROSS_BRANCH_CHECKOUT=1 in the environment",
                  "values": {"allowed": "pass", "refused": "pass"}, "evidence": "..."}],
 "self_estimate": {"p_hidden": 0.86, "least_trusted": ["time gaps with no requests"]}}
```

- One entry per scenario file, passing ones too (`status`: `pass` or `fail`). Copy `sets`
  and `lessons` from the claim; an explore finding has `"claim": "explore"`, `"sets": []`.
- `scope`: `local` (one place in the code), `cross-cutting` (several places), or
  `structural` (needs a public interface or schema change).
- `ambiguities`: one entry per ambiguity; `has_setting` (true or false) and `values` for
  every `binary` one (`values` only where `has_setting` is true).
- `blocked`: claims you could not check, and why.
- `class`: `"incomplete"` on a scenario that shows something missing or only partly there
  (see above); leave it out otherwise.
- `self_estimate.p_hidden`: your estimate of the share of a thorough hidden test suite this
  build passes; `least_trusted`: the areas you checked least, in plain words.

Your budget is limited; findings.json must be written before it runs out. Write it early and
update it as you go.

<!-- slot: capabilities -->
<<lessons>>

=== The specification ===
<<spec>>

<!-- section: scope -->
**This build was made from a short idea plus a product scope, `SCOPE.md`** (in the repository,
and part of the spec below). Its definition of done lists the flows an operator must be able
to complete. Walking them is the most important part of your job: do it right after you
have the driver (step 2 below), before the claims (step 3).

- **Install as a new operator would.** Copy the repository to a scratch directory (e.g.
  `cp -r /work/product /tmp/fresh`) and follow the README's install, first-run
  and start instructions there exactly as written. Where a step needs the internet and you
  have none, use what is already installed and say so. Record what happened in the first
  flow's `evidence`.
- **Walk every flow below end to end through the real interface**, as the user in that
  flow would: the HTTP API, the web UI's pages and forms (request each page; submit its forms
  with the fields the page really contains; for a script-driven page, make the API calls its
  scripts make), or the CLI's commands. Follow each step of the flow in `SCOPE.md`, check
  what the user sees after it, and click through every link and button on the way: one that
  leads nowhere or errors is a dead control.
- **One scenario per flow:** `/work/exam/_verify/scenarios/test_flow_<flow id>.py` with the
  header line `# flow: <flow id>` (and `# claim: flow`), driving the whole flow through the
  real interface and asserting each visible result. It may start the system (a server on
  127.0.0.1, a CLI subprocess) from `$REPO_FACTORY_PRODUCT_DIR` (`/work/product`). Keep each under
  30 seconds.
- **Missing or partial is a finding of class `incomplete`:** a flow step with no way to do
  it, a stub, a `TODO` or `NotImplementedError` on the path, a dead control, placeholder or
  hard-coded data where stored data belongs. The flow's scenario fails on it; add one
  scenario per extra incomplete thing you find (`# flow: <flow id>`, or `# flow: none` if it
  is on no flow below), each with `"class": "incomplete"` in findings.json.
- Add to findings.json `"flows": [{"id": "F1", "status": "works", "scenario":
  "test_flow_F1.py", "evidence": "..."}]`, one entry per flow below, `status` one of
  `works`, `partial`, `missing`.

The flows (`SCOPE.md`'s definition of done):
<<flows>>

<!-- section: spec_scope -->
**This build was made from a spec, used as written.** Its definition of done is the spec's own
requirements, one flow each below, in the spec's words. Walking them is the most important part
of your job: do it right after you have the driver (step 2 below), before the claims (step 3).

- **Install as a new operator would.** Copy the repository to a scratch directory (e.g.
  `cp -r /work/product /tmp/fresh`) and follow the README's install, first-run
  and start instructions there exactly as written. Where a step needs the internet and you
  have none, use what is already installed and say so. Record what happened in the first
  flow's `evidence`.
- **Check every flow below through the real interface**, as its user would: the HTTP API,
  the web UI's pages and forms, or the CLI's commands, and what the user sees after each step.
- **One scenario per flow:** `/work/exam/_verify/scenarios/test_flow_<flow id>.py` with the
  header line `# flow: <flow id>` (and `# claim: flow`), driving the requirement through the
  real interface and asserting what the spec says. It may start the system (a server on
  127.0.0.1, a CLI subprocess) from `$REPO_FACTORY_PRODUCT_DIR` (`/work/product`). Keep each under
  30 seconds. Assert only what the spec's words require; where they allow more than one
  reading, accept every reading they allow.
- **Missing or partial is a finding of class `incomplete`:** a requirement with no way to do
  it, a stub, a `TODO` or `NotImplementedError` on the path, placeholder or hard-coded data
  where stored data belongs. The flow's scenario fails on it, with `"class": "incomplete"` in
  findings.json.
- Add to findings.json `"flows": [{"id": "F1", "status": "works", "scenario":
  "test_flow_F1.py", "evidence": "..."}]`, one entry per flow below, `status` one of
  `works`, `partial`, `missing`.

The flows (the spec's requirements):
<<flows>>

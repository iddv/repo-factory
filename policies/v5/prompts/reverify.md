The builder has repaired the system. The current directory, `/work/product`, now holds the
repaired code; your `/work/exam/_verify/` directory (driver, scenarios, findings.json) is as
you left it.

1. Re-run every scenario in `/work/exam/_verify/scenarios/` (from `/work/product`:
   `PYTHONPATH=/work/product:/work/exam python -m pytest -p no:cacheprovider /work/exam/_verify/scenarios`).
   If the driver no longer fits the repaired
   interface, fix the driver, not the scenarios' expectations.
2. Probe each fix below beyond its scenario: the neighbouring cases, both sides of the
   limit, and the rules it could have broken.
3. The same rules as before: a scenario may be reported as failing only after you reproduced
   the behaviour by hand and recorded the command and output.

The fixes to probe:

<<fixes>>

<<settings>>

<<flows>>

Rewrite `/work/exam/_verify/findings.json` in the same format, with every scenario's current status;
new scenarios get new ids (`r<n>`, `"claim": "explore"`). Stop when it is written.

<!-- section: flows -->
Walk every flow of `SCOPE.md`'s definition of done again, end to end through the real
interface, as before: a flow the repair completed may have left a step, a link or a form
behind. Rerun each `test_flow_<flow id>.py`, extend it where the repair added steps it
doesn't cover yet, and update `flows` in findings.json. Anything still missing or partial
is a new scenario with `"class": "incomplete"`.

<!-- section: settings -->
The repair also added operator settings for business rules the spec leaves open. Each can be
forced everywhere by the environment variable named below, read when the setting is used.
Test each setting **under both values**: one scenario per value,
`/work/exam/_verify/scenarios/test_<setting id>__<value>.py`, that sets the variable inside the test
with pytest's `monkeypatch.setenv`, with the header lines `# setting: <key>=<value>` and
`# claim: <setting id>`, checking the behaviour described for that value. Record each value's
result under `settings` in findings.json: `{"<setting id>": {"<value>": "pass"}}`.

<<items>>

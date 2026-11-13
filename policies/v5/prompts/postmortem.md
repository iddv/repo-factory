A software factory built a system from the specification below, verified it and delivered
it. A hidden test suite, which the factory never saw, then found the failures listed below.
You explain each miss and propose what the factory should do differently next time, as short
general lessons.

For each failing row, decide its `miss_type`, exactly one of:
- `no_claim`: no claim or probe covered it;
- `claim_wrong_expectation`: a claim covered it and expected the wrong outcome;
- `retired_by_oracle`: the factory found it, then the oracle panel voted it `not_required`
  or `silent`;
- `passed_wrongly`: a factory scenario covered it and passed (its mechanics were wrong);
- `found_not_fixed`: confirmed, sent to repair, not fixed;
- `repair_regression`: it passed before the repair and fails after it;
- `interpretation`: the owner's ruling differs from the reading the factory chose.

Use the factory's claims and findings to decide: look for a claim or scenario on the same
situation before choosing `no_claim`.

Then, at most one new lesson per failure and **at most three in all**:
- If an existing lesson (listed below) should have caught it, name it in `existing_lesson`
  and propose nothing.
- A lesson is an instruction to the claims writer (`applies_to: claims`) or the verifier
  (`applies_to: verifier`): at most 240 characters, one or two sentences, imperative.
- **No domain words**: nothing from this spec's subject matter (no names of its entities,
  operations or units), no identifiers, file names, numbers taken from the spec, and no quotes
  of a hidden row. Write it so it would help on a spec about something else entirely: "party",
  "item", "request", "state", "limit", "time gap" are fine; the nouns of this domain are not.
- Prefer a lesson about how to look (which situations to try) over what to expect.

Return only one JSON object, no prose around it:

```json
{"failures": [
   {"row": "<row id>", "miss_type": "no_claim", "existing_lesson": null,
    "new_lesson": {"text": "...", "applies_to": "claims"},
    "policy_note": "<one line: what in the factory let this through>"}],
 "summary": {"no_claim": 0, "claim_wrong_expectation": 0, "retired_by_oracle": 0,
             "passed_wrongly": 0, "found_not_fixed": 0, "repair_regression": 0,
             "interpretation": 0}}
```

`new_lesson` is `null` when you propose none for that failure. `summary` counts the
`miss_type`s over all failures.

=== The specification ===
<<spec>>

=== Failing hidden rows ===
Each: row id, title, class, the driver-call trace, expected against observed.

<<failures>>

=== The factory's claims (claims.json) ===
<<claims>>

=== The factory's findings (findings.json) ===
<<findings>>

=== Current lessons ===
<<lessons>>

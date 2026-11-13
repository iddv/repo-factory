You decide what a software specification requires. You are given the spec, the owner's
answers to questions (if any), and claims about the system's behaviour, each in prose: a
situation, an action and an expected outcome. You don't see any code or tests, on purpose:
the question is what the spec requires, not what anyone built.

For each claim, decide on your own:
- `required`: the spec (or the owner's answers) requires the expected outcome, directly or
  as a clear consequence of what it states, or any competent implementation would do it
  (refusing malformed input cleanly rather than crashing counts);
- `not_required`: the spec allows a different outcome, or the expected outcome contradicts it;
- `silent`: the spec leaves it open; reasonable builders could differ.

Give a short quote from the spec for `required` or `not_required`, or a one-line reason.

Then, separately, decide whether the claim's expected outcome is really **one side of a
binary business policy**: the spec doesn't state it in so many words, and a reasonable
operator of this system could meaningfully want the other side instead (two policies, not a
right and a wrong answer; nothing new needed beyond a guard or a choice in one place). A
claim can be both "required" by your best reading and such a policy: then the system should
let the operator choose, and you say so in `policy`. Calculations, multi-way choices, new
operations or new stored data are not binary policies; clear mistakes (crashes, lost data,
a limit the spec states) are not either. Specs often state a rule and still leave a policy
open around it, for example: **from which moment a stated period is counted** (when the
triggering event happened, or when the system processed it or the person was told: matters
after a gap with no activity); whether an action is allowed in a state the spec doesn't
mention; whether something is refused or only allowed with a condition. A claim that picks
one side of such a question, where the spec's words don't pick it, is a policy even if it
reads naturally. When it is one, give `policy`:
`{"key": "area.rule", "values": [the claim's value, the other value], "claim_value": ...,
"scope": "global" | "site" | "pair", "means": {value: what the system does}, "question":
the open question in one line}`. If the
claim is about one of the known settings listed below, use that key and its values. Use the
same key for claims about the same policy. Otherwise `policy` is null.

Return only one JSON object, no prose around it:

```json
{"results": {"c17": {"vote": "required", "quote": "...", "reason": "...", "policy": null},
             "c20": {"vote": "required", "quote": "...", "reason": "...",
                     "policy": {"key": "voucher.valid_on_expiry_date", "values": [true, false],
                                "claim_value": true, "scope": "global",
                                "means": {"true": "accepted through its expiry date",
                                          "false": "refused from its expiry date"},
                                "question": "Is a voucher accepted on its expiry date?"}}}}
```

=== The specification ===
<<spec>>

=== The owner's answers ===
<<answers>>

=== Known settings ===
<<settings>>

=== Claims ===
<<claims>>

A repair made one check pass (X) and another check fail (Y) that passed before. You decide
whether the specification lets both hold. You see the spec and both checks in prose; no
code, on purpose.

Answer one of:
- `compatible`: a correct system can satisfy both X and Y; the repair was just incomplete or
  careless;
- `x_wins`: they can't both hold, and the spec requires X (Y is wrong or out of date);
- `y_wins`: they can't both hold, and the spec requires Y (X is wrong);
- `undecidable`: they can't both hold, and the spec doesn't decide between them.

Rule out `compatible` first: most breakages after a repair are collateral damage, not
contradictions.

If they can't both hold because X and Y are the two sides of a **binary business policy**
the spec leaves open (a reasonable operator could want either; nothing new needed beyond a
guard or a choice in one place), say so in `policy`: `{"key": "area.rule", "values": [X's
value, Y's value], "claim_value": X's value, "scope": "global" | "site" | "pair", "means":
{value: what the system does}}`. The system will then let the operator choose. Otherwise
`policy` is null.

Return only one JSON object, no prose around it:

```json
{"verdict": "compatible", "reason": "...", "quote": "...", "policy": null}
```

=== The specification ===
<<spec>>

=== The owner's answers ===
<<answers>>

=== X (now passing) ===
<<x>>

=== Y (now failing) ===
<<y>>

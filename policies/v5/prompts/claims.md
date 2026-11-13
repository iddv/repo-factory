You read a software specification and list what a correct system must do, as checkable
claims. You never see code: someone else has built the system, and your list will be used to
test it. Your reading of the spec must not depend on what the builder did.

Write claims a hostile tester would want checked. Cover:
- every rule in the spec, on both sides of every limit or threshold it states;
- pairs of rules that interact (a limit one operation sets and another must respect);
- time: what must hold after time passes, including long gaps with no activity at all;
- operations in unusual orders, repeated, or on things in the wrong state;
- invalid and extreme input to every operation (the system must refuse it cleanly).

Each claim is one situation, one action and one expected outcome, in plain words, about
behaviour a user or client could observe. No code, no endpoint names, no function names: the
builder chose those, and you don't know them.

`basis` says where the expected outcome comes from:
- `spec_explicit`: the spec states it; quote the words in `quote`.
- `spec_implied`: it follows from what the spec states (quote the sentence it follows from).
- `convention`: the spec says nothing, but any competent implementation would do it
  (e.g. refusing malformed input with a client error, not a crash).
- `silent`: the spec doesn't decide it. Don't write a claim for it: write an ambiguity.

`kind`: `requirement` (one rule), `interaction` (two or more rules together), `edge`
(boundaries, time gaps, ordering), `input` (invalid or extreme input).

An **ambiguity** is a question the spec leaves open where two reasonable readers would build
different behaviour. Give the literal reading and the reading the spec's author most likely
expected, recommend one, and rate the stakes: `high` when choosing wrong would change a public
operation's parameters or the stored data's shape (a one-way door), else `low`. List at most
<<max_ambiguities>>, the ones that matter most.

Classify each ambiguity's `kind`:
- `binary`: the two readings are two reasonable **business policies** that an operator of
  the system could meaningfully want either way, and switching between them needs nothing
  beyond a guard or a choice in one place (no new operation, no new stored data, no
  calculation to design). The delivered system will expose it as an operator **setting**,
  so give one in `setting`:
  - `key`: a short dotted name, `area.rule` in lower case (`checkout.cross_branch`);
  - `values`: exactly two short values, **the literal reading's value first, the expected
    reading's second** (`["allowed", "refused"]`, `["today", "due_date"]`, `[true, false]`);
  - `default`: the value of the reading you recommend;
  - `scope`: `global`, `site` (each location could set it differently) or `pair` (it is
    about two locations, e.g. checking out at one branch a copy that sits at another);
  - `means`: one line per value saying what the system does.
  Specs often state a rule and still leave such a policy open around it: from which moment
  a stated period is counted (when the triggering event happened, or when the system
  processed it or the person was told: it matters after a gap with no activity); whether an
  action is allowed in a state the spec doesn't mention.
- `complex`: anything else: calculations and counting rules, choices among more than two
  options, new mechanics (a new operation, new stored data, a new workflow), or anything
  that needs design. No `setting`.

Examples. Binary: "May a copy be checked out at a branch other than where it sits?"
(`checkout.cross_branch`, `["allowed", "refused"]`, scope `pair`); "May a member pay more
than they owe, keeping a credit?" (`payment.allow_credit`, `[true, false]`); "Is a voucher
still accepted on its expiry date?" (`voucher.valid_on_expiry_date`, `[true, false]`).
Complex: "How are late days counted: started days, whole 24-hour periods, or calendar days?"
(a counting rule with more than two answers); "How do fees get paid?" (needs a new
operation).

<<lessons>>

Write at most <<max_claims>> claims. If a lesson above led you to a claim, list its id in that
claim's `lessons`. Don't write claims that merely restate one side of a `binary` ambiguity:
the setting will be tested under both values.

Return only one JSON object, no prose around it:

```json
{"claims": [
   {"id": "c1", "situation": "...", "action": "...", "expect": "...",
    "basis": "spec_explicit", "quote": "...", "kind": "requirement", "lessons": []}],
 "ambiguities": [
   {"id": "a1", "question": "...", "literal": "...", "expected": "...", "recommend": "expected",
    "stakes": "low", "kind": "binary",
    "setting": {"key": "checkout.cross_branch", "values": ["allowed", "refused"],
                "default": "refused", "scope": "pair",
                "means": {"allowed": "a copy can be checked out at any branch",
                          "refused": "only at the branch where it sits"}}},
   {"id": "a2", "question": "...", "literal": "...", "expected": "...", "recommend": "expected",
    "stakes": "high", "why_high": "...", "kind": "complex"}]}
```

=== The specification ===
<<spec>>

<!-- section: dedupe -->
Two people independently listed claims about the same specification. Merge their lists:
find the claims that say the same thing (same situation, action and expected outcome, even if
worded differently), and the ambiguities that ask the same question.

Return only one JSON object, no prose around it, listing groups of ids that are duplicates of
each other (ids are prefixed with the list they came from, `1:` or `2:`). Ids not in any
group are unique. Don't group claims that merely touch the same rule but expect different
things.

```json
{"claims": [["1:c3", "2:c7"]], "ambiguities": [["1:a1", "2:a2"]]}
```

=== Claims and ambiguities ===
<<items>>

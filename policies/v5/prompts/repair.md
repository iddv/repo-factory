An independent verifier checked the system against the spec and confirmed the defects
below; an oracle panel that saw only the spec agreed each is required. Fix them, and add the
operator settings listed after them, if any.

- Fix causes, not symptoms. Change no more than the fixes need.
- A finding marked **Incomplete** is a flow or feature of the product scope (`SCOPE.md`)
  that is missing or only partly there: a step users can't take, a stub, a button or form
  that does nothing, a page showing placeholder data. Build it for real, end to end, through
  the interface users use; don't stub it, hide it or remove the control instead.
- **One git commit per finding**, with a message that starts with `fix(<finding id>):`.
  If one change fixes several findings, name them all: `fix(c3,c9): ...`.
- Add a test of your own, in your own test suite, for each fix: a fix without a test of
  yours is not finished.
- Each finding's scenario is in `/work/exam/_verify/scenarios/` (read-only) with the
  verifier's helper `/work/exam/_verify/driver.py`; the product is the current directory,
  `/work/product`. Run them with
  `PYTHONPATH=/work/product:/work/exam python -m pytest -p no:cacheprovider /work/exam/_verify/scenarios/<file>`,
  then run your own test suite. The scenarios are read-only: you fix the product. If you are
  sure one is wrong, say why in your last message instead.
- Don't delete or weaken a test to make it pass.
- Commit everything before you stop; leave the working tree clean.

<!-- slot: capabilities -->
<<context>>

=== Findings ===
<<findings>>

<<settings>>

=== The specification ===
<<spec>>

<!-- section: settings -->
=== Settings to add ===
The spec leaves the business rules below open: each is two reasonable policies an operator
could want either way. Don't pick one: make each an **operator setting**.

- Add the setting with exactly the key, the values and the **default** given. Where the
  scope is per site or per pair of sites, let the operator set a value per site (or per
  pair), with a global value for the rest.
- Read it each time it is used, not once at import, and let the environment variable named
  below override it everywhere when it is set (that's how the checks force a value).
- With the default, the system must behave as described for that value. Where the build
  already behaves that way, keep that behaviour exactly.
- Document every setting in a **Configuration** section of the README: the key, its values,
  the default, what each value does, the scope, and the environment variable.
- Add tests for **both values** to your own suite.
- One commit per setting, with a message that starts with `fix(<setting id>):`. Make the
  setting commits **after** the commits for the findings above, and keep each setting's
  change to itself, so a setting can be kept or dropped on its own.

<<items>>

<!-- section: fresh -->
You didn't write this code; another engineer did, from the specification below. The current
directory is their repository.

<!-- section: fixup -->
Your repair fixed some findings but broke checks that passed before. For each pair below, a
panel that read the spec decided what must happen. Make the follow-up change, commit it with a
message that starts with `fixup(<finding id>):`, run the scenarios in
`/work/exam/_verify/scenarios/` and your own suite again, and leave the working tree clean.

<<pairs>>

=== The specification ===
<<spec>>

<!-- section: undo -->
The change below must come out of the repository, and nothing else. A check that passed
before it now fails, and `git revert` can't undo it on its own because later commits touched
the same lines.

<<commits>>

- Remove exactly what the commit(s) above added or changed: their code, their tests and
  their README lines. Where a later commit built on top of those lines, keep the later
  commit's own part working.
- Don't touch any other fix or setting. Run your own test suite afterwards.
- Commit with a message that starts with `revert(<id>):`, and leave the working tree clean.

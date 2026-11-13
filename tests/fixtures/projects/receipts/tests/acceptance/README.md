# Acceptance tests

End-to-end checks of Receipts: 81 file(s), each checking one thing a user, an operator or an attacker could do, through the product's real interface. The helpers in `_helpers/` drive that interface.

Run them from the repository root (they need pytest):

    python -m pytest tests/acceptance

## Known open items

These tests are marked as expected failures (`xfail`): the behaviour they check isn't there yet, so the suite stays green. Each one passes (XPASS) once it is fixed; then delete the mark at the end of its file.

- `test_receipts_verify_hostile_bundle_file_10_000_deep_nested_json.py`: Known security issue (low): `receipts verify` on a hostile bundle file (10,000-deep nested JSON) fails cleanly with "not a Receipts bundle" and exit 2, without a traceback

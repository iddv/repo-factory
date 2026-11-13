# Security

## Reporting a security issue

Please report security problems privately, not in a public issue: use the repository host's private vulnerability reporting (on GitHub: Security, then Report a vulnerability), or write to the maintainers directly. Include the version, the steps to reproduce and what an attacker could do. You will get an answer within a few working days.

## Known issues

Open at the last release:

### S3 (low): `receipts verify` on a deeply nested JSON bundle crashes with a Python traceback

- Where: receipts/cli.py:483-488 (catches ValueError and UnicodeDecodeError but not RecursionError)
- What happens: A full traceback ending in 'RecursionError: maximum recursion depth exceeded while decoding a JSON array'. It shows install file paths.
- What should happen: 'deep.json is not a Receipts bundle', exit 2, no traceback.
- Reproduce: `python3 -c "print('['*10000+']'*10000)" > deep.json; receipts verify deep.json`
- Test: `tests/acceptance/test_receipts_verify_hostile_bundle_file_10_000_deep_nested_json.py` (marked as a known open item)

### S4 (low): Exports and bundles are written world-readable (0664), although they can contain command output and diffs

- Where: receipts/cli.py export (plain open() with the default umask)
- What happens: 664 (also for --format md)
- What should happen: 0600, matching the store's events.jsonl, or a documented warning that bundles contain output tails
- Reproduce: `receipts demo; receipts export last --bundle --out b.json; stat -c %a b.json`

## What was checked

The security review that ran before each release (threat model, probes, findings) is published with that release in its checks asset.

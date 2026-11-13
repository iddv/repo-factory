You are a security engineer reviewing a product someone else built, before it goes to its
first real users. You didn't write it. Your job is to find what an attacker, a careless user
or a hostile network could do to it, to prove each finding, and to rank it.

The current directory, `/work/product`, is a copy of the repository. Install it, run it, attack it, and change
anything you need to run it, but don't fix the product's code: you report, someone else
repairs. Your own files go in `/work/exam/_verify/`, outside the product: nothing you write there ships with it. Run a probe from `/work/product` with `PYTHONPATH=/work/product:/work/exam python -m pytest -p no:cacheprovider /work/exam/_verify/scenarios/<file>`. The product's specification is `/work/exam/_verify/spec.md`.
`/work/exam/_verify/driver.py` (if present) has helpers that drive this product's real interface: use
them as they are and don't edit them; put any helpers of your own in
`/work/exam/_verify/security_helpers.py`.

Do this, in order:

1. **Threat model** (short, written first into `/work/exam/_verify/security.json`). From the spec and
   the code: every entry point (HTTP routes and forms, CLI commands, files or directories it
   reads, background jobs, webhooks it receives), who can reach each one (`public`: anyone
   who can reach the port or URL; `staff`; `admin`; `local`: only the machine's user), how it
   authenticates, and the assets worth protecting (money, personal data, secrets such as
   passwords, tokens and signing keys, the service's availability, the integrity of its
   records).
2. **Probe the running product.** Start it the way its README says, on 127.0.0.1. For each
   area below, record what you checked and the result in `checks`, including the checks that
   pass:
   - **AuthN/authZ:** call every route and command anonymously, as the lowest role, and with
     another user's account. Anything reachable without logging in must be intentionally
     public (a login page, an ingest URL with a secret token). A state-changing action that
     the wrong role or an anonymous caller can perform is a finding.
   - **Hostile HTTP** (services): send these raw over a socket: a `Content-Length` that is
     invalid, negative or huge; a body larger than any documented limit; malformed JSON;
     deeply nested JSON (10,000 levels); huge, negative, NaN and Infinity values in numeric
     fields; huge or non-numeric ids in URLs. Expect a quick 4xx with memory staying bounded.
     A 500, a hang, a traceback, or memory that grows with the body is a finding.
   - **Injection:** SQL (quotes and `' OR 1=1 --` in every text field and id); HTML and JS in
     every field that is shown later on a page (stored XSS) and in query strings (reflected
     XSS); CSRF on every state-changing form (a cross-site POST without the token, and one
     with a foreign `Origin`); path traversal in every file name or path parameter
     (`../../etc/passwd`); open redirects (`next=`, `return_to=` pointing at another host).
   - **Authentication:** repeated wrong passwords (is there a lockout or a rate limit?);
     session cookie flags (`HttpOnly`, `SameSite`, and `Secure` when served over HTTPS); the
     session ends on logout and on a password change; passwords are stored with a slow salted
     hash.
   - **Secrets and defaults:** check for default credentials (a fixed admin password, or a
     demo token or secret that would still work in production) and for hard-coded secrets or
     keys in the code (grep for them). The first run must make the operator set them, and
     secrets must not be written to logs.
   - **Dependencies:** run `pip-audit` (or the audit tool of the product's ecosystem) on the
     declared dependencies; the network goes through a proxy that may allow the package
     index. If the tool can't be installed or reached, record `unavailable` with the error.
     For a product with no third-party dependencies, record `no_dependencies`.
   - **Error pages:** trigger errors (bad ids, bad input, a missing file). The response must
     not show a traceback, file paths, SQL or secrets.
   - **Command-line products:** the same ideas where they apply: file permissions of the data
     and the backups, input that reaches a shell or SQL, and secrets in arguments or in files.
3. **Each finding: reproduce it by hand** (a curl call, a raw-socket script, a CLI command)
   and record the exact command and what you saw. Then write a **probe**:
   `/work/exam/_verify/scenarios/test_sec_<n>.py`, one pytest test that asserts the **secure**
   behaviour. It must fail on this build now, for the reason you found, and pass on a
   correct one. It starts what it needs on 127.0.0.1, finishes in under 30 seconds and
   finds the product at `$REPO_FACTORY_PRODUCT_DIR` (`/work/product`), never from its own path. Start the file with:
   ```
   # rule: <the secure behaviour in plain words, e.g. "a negative Content-Length is rejected with 400 before the body is read">
   # expect: <what a correct build does>
   # source: security review
   # claim: security
   ```
   Import helpers only as `from _verify.driver import ...` (or your
   `_verify.security_helpers`). Run it and confirm it fails for the right reason. Where a
   probe can't be written (for example, a dependency advisory), say why in `no_probe`. A check
   that passes may get a probe too (`test_sec_ok_<n>.py`): it becomes a regression test.
4. **Severity**, one of:
   - `critical`: anyone on the network can take over the system, read or change data they
     shouldn't, run code, or move money.
   - `high`: an unauthenticated attacker can take the service down or exhaust its memory or
     disk; privilege escalation between roles; default credentials; stored XSS that staff
     will see.
   - `medium`: needs an authenticated user or unusual conditions, or leaks information; no
     lockout or rate limit on login; 500s that show a traceback; missing CSRF on a low-impact
     form.
   - `low`: hardening (for example, cookie flags on a localhost-only service), and the rest.
5. Write `/work/exam/_verify/security.json` and stop:

```json
{"threat_model": {
   "summary": "A self-hosted relay: the ingest URLs are public by design (secret token in the path); the admin UI is staff-only behind a login.",
   "entry_points": [{"name": "POST /in/<token>", "reach": "public", "auth": "secret token in the URL", "notes": "body read before the token lookup"}],
   "assets": [{"name": "provider signing secrets", "kind": "secrets", "where": "data/relay.db"}]},
 "checks": [{"area": "hostile HTTP", "what": "negative Content-Length on POST /in/x", "result": "fail", "evidence": "server read until EOF; RSS 26 MiB -> 227 MiB"},
            {"area": "injection", "what": "stored XSS in source name", "result": "pass", "evidence": "rendered escaped"}],
 "findings": [{"id": "s1", "title": "A negative Content-Length makes the server read the body without a limit",
   "severity": "high", "category": "hostile HTTP", "where": "relay/http.py:88",
   "repro": "python3 _verify/neg_cl.py  # streams 200 MiB with Content-Length: -1",
   "observed": "RSS 26 -> 227 MiB, then 404", "expected": "400 at once, body not read",
   "probe": "_verify/scenarios/test_sec_1.py", "no_probe": null}],
 "dependency_audit": {"tool": "pip-audit", "status": "clean", "output": "No known vulnerabilities found"},
 "limits": ["TLS termination is left to a reverse proxy and wasn't tested"]}
```

- `checks[].result`: `pass`, `fail`, `n/a` or `not_checked`.
- `dependency_audit.status`: `clean`, `findings`, `unavailable` or `no_dependencies`.
- `limits`: what you could not check, in plain words.

Write in plain words that the product's operator will read: the security report published
with the release is made from this file. Your budget is limited, so write `security.json` early and update it as you go.

=== The specification ===
<<spec>>

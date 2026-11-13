You are an operations engineer deciding whether a product someone else built is ready to be
taken to production by a small team. You didn't write it. Check each item below **by doing
it**, not by reading about it, and report what you saw.

The current directory, `/work/product`, is a copy of the repository. Don't fix the product's code: you report,
someone else repairs. Your own files go in `/work/exam/_verify/`, outside the product: nothing you write there ships with it. Run a probe from `/work/product` with `PYTHONPATH=/work/product:/work/exam python -m pytest -p no:cacheprovider /work/exam/_verify/scenarios/<file>`. The product's specification is
`/work/exam/_verify/spec.md`. `/work/exam/_verify/driver.py` (if present) has helpers over this product's real
interface: use them as they are and don't edit them.

The checks (ids in backticks):
<<checks>>

How to check them:

- `install`: copy the repository to a scratch directory (`cp -r /work/product /tmp/fresh`)
  and follow the README's install and first-run instructions there, using
  **exactly** the documented commands. Where a step needs the internet and you have none,
  use what is already installed and say so. A command that fails, a missing step, or a step
  you had to guess is red.
- `bind_localhost`: start it with its defaults and look at what it listens on (`ss -ltnp`,
  or the startup log). Listening on all interfaces (`0.0.0.0`, `::`) by default is red; it
  must listen on 127.0.0.1 unless the operator configures otherwise. Use `n/a` for a product
  that doesn't listen on a port.
- `empty_start`: a fresh install started with its defaults must start with no business
  data. Demo or seed data is loaded only by an explicit flag or command (`--demo`,
  `seed-demo`). Demo data loaded by default is red.
- `first_admin`: the first run must have the operator create the first admin, or must print
  a random one-time password that has to be changed at first sign-in. A fixed default
  password, documented or not, is red. Use `n/a` for a product with no accounts.
- `config`: settings come from environment variables or a config file, and every option is
  documented (name, default, meaning). Options in the code that aren't documented are amber.
- `data_location`: the README says where the data lives and how to change that.
- `backup_restore`: make some data, back it up as documented, change the data, restore, and
  check that the data is back exactly. No documented way, or a way that fails, is red.
- `restart`: make data, use it (a few requests), stop it gracefully (SIGTERM or the
  documented way) and start it again **at once**, with no wait (within 2 seconds): it must
  start and serve straight away, and the data is still there. Then kill it with SIGKILL (a
  crash) and start it again at once: the same. A product that refuses to start for a while
  after a stop or a crash ("address already in use": address reuse off on its listening
  socket, connections left in TIME_WAIT; or a stale lock or pid file) is red: it loses
  requests on every restart. For a CLI, run a command, interrupt one with SIGKILL halfway,
  and run it again.
- `shutdown`: stop it the documented way (or with SIGTERM). It exits within a few seconds
  without corrupting data, and work in flight is finished or safely resumed.
- `health` (services): a health endpoint that answers without a login and says whether the
  product can work (for example, that the database can be opened). `n/a` for a CLI.
- `logs`: readable logs with timestamps for requests or commands and errors, and no
  passwords, tokens, secrets or personal data dumped into them.
- `upgrade`: a migrations tool or a versioned schema with an upgrade path, so that a v2 can
  open v1's data without losing it. Look at how the schema is created; "create tables if
  missing" with no version is amber.

For every item: `status` is `green` (ready), `amber` (works, but needs attention before
production), `red` (a gap), or `n/a`. Also record `why` (one line), `evidence` (the commands
you ran and what you saw), `next` (the next step to production, one line), and `fixable` (true
when a code or documentation change in this repository would make it green).

For a **red** item that a test can check (a default, a backup round trip, a restart, a
health endpoint), write a **probe**: `/work/exam/_verify/scenarios/test_ready_<id>.py`, one pytest test
that asserts the ready behaviour. It must fail on this build now and pass once the item is
fixed, start what it needs on 127.0.0.1, finish in under 30 seconds, and find the product at
`$REPO_FACTORY_PRODUCT_DIR` (`/work/product`), never from its own path. Put `# rule: <the ready behaviour in plain words>`, `# expect: ...`,
`# source: readiness` and `# claim: readiness` at the top, and import helpers only as
`from _verify.driver import ...`. Run it and confirm it fails for the right reason.

For a service, also say how to run it: `run` is the command, from the repository root, that
runs the service **in the foreground** as the README documents it (`start`), any commands it
needs once before (`setup`, for example creating a key), the environment it needs (`env`),
its port and a path that answers without a login (`health`). The harness uses it to stop and
start the product again itself.

Write `/work/exam/_verify/readiness.json` and stop:

```json
{"service": true,
 "install": {"commands": ["./install.sh"], "result": "worked; printed the admin password"},
 "run": {"setup": [], "start": "bin/app serve", "env": {"APP_SECRET": "x"}, "port": 8080,
         "health": "/health"},
 "checks": [{"id": "bind_localhost", "status": "red", "why": "binds 0.0.0.0:8080 by default",
             "evidence": "ss -ltnp: 0.0.0.0:8080 python3", "next": "listen on 127.0.0.1 by default; HOST to change it",
             "fixable": true, "probe": "_verify/scenarios/test_ready_bind_localhost.py"}]}
```

One entry per check id above. Start everything you run on 127.0.0.1 only. When a step needs
systemd, Docker, root or the internet beyond package indexes, record it as `not_verifiable`
with what it needs (in `why`); it is not red. Write in plain words: the readiness report
published with the release is made from this file.
Your budget is limited, so write `readiness.json` early and update it as you go.

<!-- slot: capabilities -->
=== The specification ===
<<spec>>

<!-- section: repair -->
Before this product goes to its first real users, a security review and an operational
readiness check found the problems below. Fix them.

- Fix causes, not symptoms. Change no more than the fixes need, and keep every other
  behaviour as it is.
- **Security findings:** make the secure behaviour the only behaviour (validate before
  reading or storing, reject with a clear 4xx, never a 500 or a traceback). Don't add a
  switch that turns the protection off.
- **Safe defaults** are the owner's standing rule: listen on 127.0.0.1 unless the operator
  configures another address; start with no business data, with demo data only behind an
  explicit flag or command (for example `--demo`); have the operator create the first admin
  on the first run, or print a random one-time password that must be changed, and never
  ship a fixed default password. Keep the old behaviour one documented flag away.
- **Readiness gaps:** CI is `.github/workflows/test.yml`, which installs the project the way
  the README says and runs every test suite on push and pull request. Pin or lock the
  dependencies (none is fine; say so). Keep build artefacts out of the repository with a
  `.gitignore`. The README must cover install, configuration (every option), running,
  upgrading, backup and restore, troubleshooting, and known limitations, in plain words for
  an operator (no notes about how the software was made), and its test instructions must run
  every test suite of yours, exactly as written.
  **Every command the README documents must work exactly as written** on a fresh copy: the
  harness runs the quick start, a backup and every test command literally after your
  commit. A service must start again at once after a stop or a crash.
- **Work through the items in the order given** (the most severe first) and **commit each
  one as soon as it is done**, with a message that starts with `fix(<item id>):`. Your
  budget is limited: what is committed is kept if it runs out, so don't plan everything
  first.
- Add a test of your own, in your own test suite, for each fix: a fix without a test of
  yours is not finished. Update the README wherever a default or a command changed.
- The independent checks are in `/work/exam/_verify/scenarios/` (read-only), with their
  helpers in `/work/exam/_verify/`. Run them from `/work/product` with
  `PYTHONPATH=/work/product:/work/exam python -m pytest -p no:cacheprovider /work/exam/_verify/scenarios`
  (if pytest is missing, install it in a virtual environment), then run your own suite. The
  checks named in the items below must pass; you fix the product, never them. If **another**
  check fails only because it relied on an old unsafe default (for example, it starts the
  product without a flag and expects demo data, or expects a fixed password), you may
  propose a new version of how it starts the product (add `--demo`, set up the admin), never
  of what it asserts: copy the file to `/work/exam-proposals/` under the same name and edit
  the copy. If you are sure a check is wrong, say why in your last message.
- Don't delete or weaken a test to make it pass.
- Commit everything before you stop, and leave the working tree clean.

<<context>>

<!-- slot: house_rules -->
=== Items ===
<<findings>>

=== The specification ===
<<spec>>

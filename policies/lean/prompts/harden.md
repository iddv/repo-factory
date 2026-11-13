<<spec>>

The code in the current directory was written to the spec above and works. Make it ready to
ship to people who will rely on it:

- **Harden it**: bad input, bad or missing files and settings, concurrent use, a crash part way
  through a write, clear errors and exit codes. A server listens on 127.0.0.1 unless the user
  configures another address.
- **Close what a security review would hold the release for**: a limit on every request body,
  upload and line a client sends; a timeout on every connection, so one that never finishes
  doesn't stay open; a cap on what someone who is not signed in can make it store or do (failed
  sign-ins, sign-ups, sessions); output escaped where it is shown; no secret in a log, a URL or
  an error. Prove each with a test that sends the hostile request.
- **Add the tests that prove it**, and for each flow below one end-to-end test through the
  product's real interface. The README's test command runs them all, exactly as written, on a
  fresh copy with only the declared dependencies.
- **Write the docs a user needs**: a README that opens with what it is and who it is for, then
  a quick start whose commands work exactly as written on a fresh copy, then install,
  configuration, running, backup and restore, upgrading, troubleshooting and known
  limitations, in plain words.
- **Document every interface the product exposes**, so that someone can use each one from the
  README alone, without reading the code or opening a browser: every command and its options;
  every screen and what it does; every HTTP endpoint (method, path, what it takes, what it
  returns, its errors, and a `curl` example that works against the quick start, including
  how to sign in); every file it reads or writes and its format. If a flow below can be done
  through more than one interface, document it in each.
- **Add CI** that installs it as the README says and runs the tests.

Keep the behaviour the spec describes exactly as it is. Do not add features, settings,
configuration options, roles or workflows the spec doesn't ask for, and document only what the
code does.

Work economically; it changes nothing about what is done:
- Read the code once. After that, look things up with `grep -n` and print only the lines you
  need, rather than whole files again.
- While you work, run only the tests you are changing. Run the whole suite once at the end,
  and again only if something you then changed could break it.
- Check the quick start on one fresh copy, with the runtime installed here; CI runs the other
  versions, so don't install them.

The flows a user must be able to complete end to end:

<<flows>>

Last, write `.repo-factory/flows.json`: a JSON object mapping each flow's id to the tests that walk it,
as test ids (`file::test name`, as pytest or `node --test` names them) or test files, for example
`{"F1": ["tests/test_cli.py::test_first_run"], "F2": ["tests/test_orders.py"]}`. It is for the
build system and is removed before release: nothing in the product refers to it.

Work in the current directory.

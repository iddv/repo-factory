# Changelog

All notable changes to this project are written here. Versions follow
[semantic versioning](https://semver.org/); until 1.0 anything may change.

## Unreleased

### Added
- **The engine and the loop**, imported in one commit: the v5 build pipeline (brief, build,
  claims, verify, three votes on each failure, repair, readiness, the gate) and the `lean` flow
  (unproven), the build loop (a typed task in, a private repository out on your press), the
  sandbox (bubblewrap, a network namespace, the login through a pipe), and, in the code but off
  and not offered yet, maintenance, projects and lessons.
- **The command:** `repo-factory doctor`, `ui` (the page and the loop in one process), `loop
  add [--file]`, `loop list|show|status|run|publish|approve|revise|retry|stop-round|...`,
  `build`, `new`, `status`, `revise`, `costs`, `--version`.
- **The page:** Builds (the list, a build's six stages with their time, cost and log, Stop, Ask
  for changes, Publish, Make public, the checks in plain words), New task (typed, or a file),
  and Settings (Claude, GitHub, Building, Publishing, Spend, Appearance); 1400 and 390 wide,
  light and dark; the experiment notice on the first screen.
- **A stranger's defaults:** publishing asks first (`publish.when: ask`); repositories are
  private and only your press makes one public; a spend cap of $25 a build and $50 a day;
  maintenance off.
- **The golden build** (`tests/golden/`): the whole cycle through the command line against a
  recorded `claude` and a local `gh`, in CI on every pull request.
- **The install test** (`scripts/ci/install_test.py`): a stranger's first three commands against
  the built wheel, in an empty home.
- CI jobs `test`, `frontend`, `browser` and `install`; a documentation-only change runs `lint` and
  `gitleaks` only. A release workflow on a version tag, behind the `release` environment.
- The repository's papers: README, SECURITY, CONTRIBUTING, CODE_OF_CONDUCT, NOTICE, issue
  forms and a pull request template.
- CI on GitHub's hosted runners: `lint` (ruff, and the repository's own rules in
  `tests/test_tree.py`) and `gitleaks` (no private words or attribution lines in commits and
  pull requests, a DCO sign-off on every commit, and a secret scan).
- `make check`, which runs what CI runs.

### Changed, against the code it was imported from
- A build stopped by its spend cap is held with the cap named first, instead of delivering an
  empty tree.
- No build starts without a working sandbox: it is held, in words, and starts by itself once
  bubblewrap works. A missing Claude Code, or a usage limit, is said in words.
- "Publish on its own" is private (`publish.when: ready`); making public stays a press.

### Added, from the first real builds
- A build's page offers **Resume** after Stop, and **Build again** on a held build.
- `repo-factory link`: a new one-time sign-in link for the running page.
- Screenshots of the page in `docs/images/`.

### Fixed, from the first real builds
- A revise round (Ask for changes, the automatic repair) works on the branch its build was
  delivered on, also for a build delivered under an earlier branch name.
- The gate's documents line no longer uses an internal word.
- A build published to a private repository says "Published privately" in the list, not
  "Needs you".

### Security
- What a session writes stays inside the sandbox: in the folders a session can write, a link
  that is absolute or leads out of its folder is removed when the session ends, before the
  host reads anything; every copy keeps links as links; the checks asset holds regular files
  and folders only.
- A product's Python packages install inside bubblewrap, whose one way out is the index proxy
  with the `exam.indexes` allow-list; the host makes the empty environment and never starts
  it. A requirement given by address (`@`, a URL or a path) is not installed, and the build's
  record names it. Without bubblewrap nothing installs.
- Only named `ANTHROPIC_*` settings (the endpoint and the models) reach a session.
- `repo-factory doctor`, the README's install and the New task page say when builds copy
  Claude Code's sign-in file into each session, and how to avoid it.
- The golden build tries, from inside a build session and a check session, to read the home,
  reach the host's loopback and find a token, and fails if any succeeds.

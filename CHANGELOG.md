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

### Changed: what a build holds, what it spends, what it says
- **A build whose security review did not run is held** (`security`), with why it did not run
  and what to press; Publish anyway never overrides it, and no automatic repair round starts
  for it (a revise round carries the review it revises). The release notes' "not every check
  could run" line no longer covers it.
- **The review's money is kept aside** from the first session on (its typical cost, at most a
  quarter of the build's cap): a tight cap stops the earlier stages, in words, before it stops
  the review.
- **Repair rounds spend inside the build's cap:** the automatic round and Ask for changes spend
  what the build's cap has left, unless `revise.max_usd` is set; Ask for changes says how much
  is left; a build at its cap gets no automatic round and says why.
- **A cap of 0 is a wrong value**, never "no cap": `daily_usd`, `build.max_usd`,
  `revise.max_usd` or `maintain.max_usd` at 0 or below stops every build, and the loop names the
  line to fix.
- **Nothing measured is not complete:** completeness holds when fewer than half of the flows
  could be measured (Publish anyway may override it); the gap is still said.
- **A Node product is not held for the README check's own limits:** the check writes a launcher
  for each `package.json` `bin` when it skips the README's global install; a command not found
  right after a skipped install step, and a test command that needs a download, are "not
  measured", never red.
- **The brief asks for Python** unless the task names another language.
- **A product's own workflow is looked at before the push:** a self-hosted runner,
  `pull_request_target` or a token that may write holds the publish; the build prompt asks for
  one Linux job with first-party or pinned actions; the Publish dialog says publishing starts
  the product's CI on your account.
- **One folder:** the venvs, the uv caches and the shell prefix live in `<home>/cache/` (an
  existing `~/.cache/repo_factory` is still used, never moved).
- The page's settings writer refuses `publish.approval: auto`, and `maintain.enabled` and
  `projects.enabled` ("not in this version").
- `repo-factory doctor` has a fifth line: a warning under 5 GB of available memory. The README
  lists Node and npm (for the checkout install) and the memory a build needs.
- The DCO check accepts Dependabot's own sign-off on Dependabot's own commits.
- The release workflow has two jobs: build with a read-only token, and publish (the `release`
  environment, the only job that may write) that refuses a tag whose commit is not on `main`.

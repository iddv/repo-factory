# Changelog

All notable changes to this project are written here. Versions follow
[semantic versioning](https://semver.org/); until 1.0 anything may change.

## Unreleased

Nothing yet.

## 0.1.0 — the first release

### What it is

`repo-factory` is a local web page and a command for one person on one Linux machine. You type
a task (or pick a file, or point it at a GitHub issue you wrote). It writes a brief, runs one
long Claude Code session in a sandbox to build a small project, then has separate sessions
write an exam the builder never saw, probe it for security holes, follow its README as a new
user would, and repair what they find. A gate of facts says "Ready: publish?" or holds the
build and says why. On your press the build becomes a **private** GitHub repository under your
account; only you make it public. If you switch it on for a repository, it then fixes the
issues you label `go`, each as a pull request.

It needs Linux, Python 3.12, `uv`, `git`, bubblewrap, Claude Code signed in (a Claude plan or an
API key), about 5 GB of free memory while a build runs, and, to publish, the GitHub CLI logged
in. A build costs real money or plan usage: about $10 and an hour on our record.

### What it is not

- Not production software, and not a hosted service: one user, loopback only (127.0.0.1).
- Not for macOS or Windows, and not for work inside an existing large codebase.
- Not a design tool or an app host. It builds small projects, best in Python.
- Not proof that what it built is right: its exam and its gate are the same system as the
  builder. **Read what it builds before you make it public or run it elsewhere.**

### What is unproven

- **The record is small:** ten products built by the same pipeline before it moved here (one
  owner, one machine, one week, all small Python projects), and a handful of builds of this
  repository's own code on one machine. Nobody else has used it yet.
- **Maintenance** (fixes as pull requests) has run for real only against a stand-in GitHub on
  one machine: a few fixes of issues in local test repositories, never on a real repository's
  issues. **Merging its own fixes** and **releasing after a merge** have never run on a real
  repository.
- The **lean** flow (Settings › Building) is labelled "unproven": a handful of builds.
- Other languages than Python (Node above all) are less tested, and their checks measure less.
- The code for a GitHub App of its own is in the tree but not offered on the page.

### What is in it

- **Builds.** New task (typed, a file, or **Build from an issue**), the brief, a build's six
  stages (Brief, Plan, Build, Check, Fix, Publish) with their time, cost and log, Stop and
  Resume, Ask for changes, Build again, and the checks in plain words. The v5 engine (brief,
  build, claims, verify, three votes on each failure, repair, readiness, the gate) and the lean
  flow.
- **Publishing asks first.** A ready build waits at "Ready: publish?". Publish creates a private
  repository with a README, tests, a release and a report of what was checked; its confirm says
  whose name the repository carries and that its CI runs on your account. A product whose own
  workflow could run off GitHub's runners, uses `pull_request_target` or a write token is held.
- **Spend caps:** $25 a build and $50 a day by default (Settings › Spend; either can be none). A
  build at its cap stops and says so; the security review's money is kept aside so a tight cap
  stops the earlier stages first; a cap of 0 is a wrong value, never "no cap".
- **Lessons:** a build the checks hold writes at most one lesson (capped at $0.25); a new lesson
  waits on the **Lessons** page until you switch it on; a lesson naming a package, a host, an
  address or a command-line flag is not kept.
- **A task from your own issue:** only an issue you opened, or labelled `build` yourself; its
  text is quoted in the brief as data; anyone else's is refused in words.
- **Maintenance, off until you switch it on per repository** (a product's page under Projects):
  one setup pull request (labels, the policy file, AGENTS.md, CI); then each poll fixes the open
  issues whose `go` label you put on yourself and nobody edited since, each as a pull request on
  a new `repo-factory/` branch; everything else is listed under "Waiting for you". It writes
  nothing on any issue, never force-pushes, never changes a workflow, and starts no fix while a
  product's workflow may run off GitHub's own runners or while sessions would get Claude Code's
  own sign-in file. **Merging its own fixes** (bugs and docs only, judged by the diff) and
  **releasing after a merge** are two more switches, each off and each asking first. $20 a month
  and $5 a fix by default. **Pause** stops the next poll.
- **Projects:** each published product, its maintenance, what waits for you, what its fixes did,
  and this week's proposals, none built until you press one.
- **The command:** `repo-factory doctor` (Claude Code, bubblewrap, git, gh, memory), `ui` (the
  page and the build loop in one process), `link` (a new sign-in link), `loop add [--file |
  --from-issue]`, `loop list|show|status|publish|revise|retry|lessons|...`, `build`, `new`,
  `status`, `revise`, `costs`, `--version`.
- **The page:** Builds, Projects, Lessons, Settings (Claude, GitHub, Building, Publishing,
  Maintenance, Spend, Appearance), Help; desktop and phone, light and dark; hash routes; the
  experiment notice on the first screen.

### Safety

- Every Claude Code session and every run of model-written code is inside bubblewrap: the
  workspace writable, the system read-only, the rest of your home hidden, a private `/tmp`, its
  own network namespace. Without bubblewrap nothing a model wrote runs.
- The sandbox holds no GitHub credential; the host does every push, with a hardened git (no
  hooks, filters or credential helpers from a tree; no proxy or remote a tree set). A fix is
  carried out of the session's clone as files and pushed from a clone the host made.
- The Claude login reaches a session through a pipe when you keep a token
  (`claude setup-token`, Settings › Claude); otherwise a copy of Claude Code's sign-in file is
  in each session, and `doctor`, the README and New task say so.
- **What remains:** egress to the internet is open during a build; a package installed during a
  build runs its install code in the sandbox; a model can name a hostile dependency; with
  merging on, a fix nobody read can be merged. [SECURITY.md](SECURITY.md) has the whole table.

### Last changes before the release

- A model changed on Settings (the brief's, the summary's, the honesty check's, the lessons')
  is used from its next call on, without a restart.
- `repo-factory doctor` says a low-memory line as a warning, not as something missing.
- The policy file a maintained product carries says what each of its keys does in this version
  (several were only read by an earlier poll that is gone).
- The release workflow checks the wheel against `SHA256SUMS` before it creates the release.

### This repository

- CI on GitHub's hosted runners only, with a read-only token, no secret and no model: `lint`,
  `gitleaks` (no private words or attribution lines, a DCO sign-off on every commit, a secret
  scan), `test` (the fast tests, the sandbox in real bubblewrap, and a golden build of the
  whole cycle against a recorded `claude` and a local `gh`), `frontend`, `browser` and
  `install` (a stranger's first three commands against the built wheel). `make check` runs the
  same.
- Releases: a version tag on `main` builds the wheel (with the page) and its `SHA256SUMS` with a
  read-only token; the job that creates the GitHub release runs in the `release` environment
  and waits for a maintainer's approval. [docs/RELEASING.md](docs/RELEASING.md) has the steps.

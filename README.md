# repo-factory

**Give it a task; it designs, builds, checks, hardens and publishes a small software project
to a private GitHub repository on your own Linux machine, then keeps maintaining it.**

> **repo-factory is an experiment.** It runs code a model wrote and can publish under your
> GitHub account. It costs real money or plan usage: about $10 and an hour a build on our
> record. Its own checks are not proof. Read what it builds.

**Status:** the engine, the page and the checks are here; there is no release yet, so for
now you install it from a checkout (below). The [changelog](CHANGELOG.md) says what has landed.

## What it does

`repo-factory` is a local web page and a command. You type what you want (or point it at a
file). It writes a brief, runs one long Claude Code session in a sandbox to build it, then has
separate sessions write an exam the builder never saw, probe it for security holes, follow the
README as a new user would, and repair what they find. A gate of facts decides whether it is
ready. On your press it becomes a private GitHub repository with a release; only you make it
public. The page shows every stage, its log, its cost and what was held and why.

## The record so far

Everything this pipeline built before it moved here: ten small products, built between 5 and
11 October 2026, one owner, one machine. Costs are what Claude Code reported for each session;
on a Claude plan that is usage, not a bill.

| Product | First full build | Its cost | Rules passed at delivery (visible · held back) | Readiness (green · amber · red) | Runs in all | Spent in all |
|---|---|---|---|---|---|---|
| FrontDesk Book | 49 min | $12.30 | 66 of 70 · 19 of 19 | 16 · 2 · 0 | 1 | $12.30 |
| Plotkeeper | 56 min | $11.01 | 70 of 72 · 15 of 15 | 18 · 2 · 0 | 1 | $11.01 |
| Lesson Ledger | 64 min | $10.01 | 61 of 62 · 22 of 22 | 8 · 12 · 0 | 1 | $10.01 |
| Receipts | 85 min | $12.84 | 54 of 55 · 26 of 26 | 13 · 3 · 0 | 1 | $12.84 |
| Sidedoor | 348 min (the third try) | $22.56 | 73 of 78 · 19 of 19 | 12 · 3 · 2 | 10 | $40.84 |
| Driftwatch | 92 min | $11.10 | 64 of 65 · 22 of 22 | 18 · 2 · 0 | 3 | $11.21 |
| Repo Recall | 92 min | $8.68 | 54 of 60 · 24 of 24 | 9 · 6 · 1 | 6 | $11.00 |
| Beliefline | 107 min | $7.49 | 61 of 63 · 26 of 26 | 13 · 5 · 2 | 6 | $7.64 |
| NoteHatch | 32 min, delivered at 12 of 82 rules; rebuilt in 43 min | $5.91, then $5.97 | 58 of 58 · 27 of 27 (the rebuild) | 11 · 4 · 0 | 6 | $12.17 |
| Nextread | 53 min | $6.33 | 57 of 60 · 26 of 26 | 15 · 1 · 1 | 2 | $13.61 |

- **A fresh build: $10.17 and 64 minutes at the median** (13 fresh builds of the 10 products);
  the range is $5.91 in 32 minutes to $22.56 in 5 h 48.
- **All ten passed the gate** and every held-back rule at delivery. That is the factory
  marking its own work: the exam is written by a separate session, but by the same system.
- **Build runs: 37, of which 36 ended ok. Fix runs for maintenance: 32, of which 16 ended ok.**
- **Money in all: about $185** ($142.63 in build sessions, $42.74 in the pipeline's own calls).
- **Use by anyone else: none so far.** 0 stars and 0 forks on all ten.

### Where it is weak

- **It costs three to six times a plain Claude Code session** on the same tasks. In our own
  comparison the extra bought a product that does its core job more often and a README a new
  user can follow; it did not buy robustness or fewer security findings, and a blind judge
  preferred the plain session told to harden, for bloat.
- **The brief is most of the value.** One plain session given the factory's brief got most of
  the way for a third of the cost.
- **A build can end "ok" and be bad** (NoteHatch's first), and can run for six hours.
- **Half the maintenance fix runs failed**, and the fixes that worked waited weeks for a person.
- **The sample is small:** ten small Python products with few dependencies, one owner, one
  machine, one week. Linux only.

**"Isn't this just Claude Code in a loop?"** Mostly, yes. The builder is one ordinary Claude
Code session. What is added is the part around it: a brief first, an exam written by a session
that never saw the build, a security probe, a new user's walk through the README, a repair
loop that may not make things worse, a gate that reads facts instead of the builder's opinion,
and a publish step that asks. Whether that is worth three to six times the cost is the
experiment.

## What it needs

Linux, Python 3.12, [uv](https://docs.astral.sh/uv/), `git`,
[Claude Code](https://docs.claude.com/en/docs/claude-code) signed in (a Claude plan or an API
key), [bubblewrap](https://github.com/containers/bubblewrap) (`bwrap`), and, only to publish,
the GitHub CLI (`gh`) logged in.

## Install

There is no release yet. Until there is, from a checkout (it needs Node once, to build the
page):

```
git clone https://github.com/iddv/repo-factory && cd repo-factory
python3 scripts/build_ui.py && uv tool install --python 3.12 .
repo-factory doctor      # one line each: Claude Code, bubblewrap, git, gh
repo-factory ui          # the page on 127.0.0.1:8790, and a sign-in link
```

With a release, it will be three commands: `uv tool install <the wheel's address on the
Releases page>`, `repo-factory doctor`, `repo-factory ui`. Then read
[docs/first-build.md](docs/first-build.md): what each stage does, the press, and spend.

## The defaults

| Setting | Default |
|---|---|
| Publish | **Asks first:** a ready build waits at "Ready: publish?" until you press |
| Visibility | **Private**; only you make a repository public |
| Spend | **$25 a build, $50 a day**; either can be changed or set to none |
| Maintenance | **Off** until you switch it on for a repository |
| The page | Loopback only (127.0.0.1); writes need a sign-in token |

## The threat model, in a paragraph

Every session and every run of model-written code happens in bubblewrap: the workspace
writable, the system read-only, the rest of your home hidden, a private `/tmp` and its own
network namespace. The sandbox holds no GitHub credential; the host does every push. **What
remains:** egress to the internet is open during a build, so code can send what it can read
(the workspace) anywhere; a package installed during a build runs its install code inside the
sandbox; a model can name a dependency that is hostile; and the exam and the gate are the same
system as the builder. **Read a product before you make it public or run it elsewhere.**
[SECURITY.md](SECURITY.md) has the full table and how to report a problem privately.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md): sign off your commits (the DCO), and `make check` runs
what CI runs. The maintainers read issues and pull requests in person, about weekly; no tool
replies to them.

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

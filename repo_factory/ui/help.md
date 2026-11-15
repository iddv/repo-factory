# repo-factory: help

repo-factory is an experiment. It runs code a model wrote and can publish under your GitHub
account. It costs real money or plan usage: about $10 and an hour a build on our record. Its own
checks are not proof. Read what it builds.

## Signing in {#access}

`repo-factory ui` serves this page on this machine only (127.0.0.1) and prints a one-time
sign-in link; open it in your browser. The link works once, for a few minutes. Reading the page
needs no sign-in; anything that changes something (a new task, Stop, Publish, a setting) does. For another browser, or when the link expired, run `repo-factory link` for a new one.

## A new task {#new-task}

Under Builds, press **New task**. Type what you want built, or choose a text or Markdown file:
its text is read in your browser and put in the box, where you can change it. The first line
names the task. Press **Build**: the row appears at once and says Brief.

**Or build from an issue:** on the same page, write a GitHub issue as `owner/repo#12` (or paste
its address) and press **Build from an issue**. Only an issue **you** opened, or one you
labelled `build` yourself, is taken; an issue someone else opened (or labelled) is refused, and
the page says why. The issue's text goes into the brief quoted as data: it changes no setting,
no tool and no approval, and nothing is published until you press Publish. repo-factory only
reads the issue; it never comments on it or labels it.

Builds' head shows **your record**: how many builds ran, how many were published and held, and
a build's time and cost at the median, from your builds' own ledgers. Builds by the lean flow
are counted beside the others and also on their own, and a lean build's page says
**lean (unproven)**.

## The stages {#stages}

Every build goes through six stages, in order:

- **Brief**: the task is read and written up as a brief.
- **Plan**: what to build, and how to know it works.
- **Build**: the code, its tests and its docs, written by Claude Code in a sandbox.
- **Check**: it is tested as a user would, and reviewed for security.
- **Fix**: what the checks found is fixed and tested again.
- **Publish**: it is packaged, and waits for your press.

Each stage shows its time, its cost and **Open the log**.

## What you can do on a build {#controls}

- **Stop** ends a running build. Its workers are killed; nothing else changes.
- **Resume** continues a build you stopped, from where it stopped.
- **Build again** builds a held build once more (it spends again, up to the build's cap).
- **Ask for changes** sends what should change; a new round of the build makes the change.
- **Publish**: a build that passed its checks waits at "Ready: publish?". Nothing is on GitHub
  until you press Publish, which creates a **private** repository under your account.
- **Make public** makes a published repository public. It is a separate press, on each build.

Publish's confirm says whose name the repository's licence and NOTICE will carry, and the
address its commit is made as: git's own `user.name` and `user.email` on this machine (or
`publish.copyright`).

The checks it passed, and anything that held it, are listed on the build's page in plain
words. They are the factory's own checks: useful, and not proof. Read what it built before you
make it public.

Some holds **Publish anyway** never overrides: nothing delivered, an open critical or high
security finding, **a security review that did not run** (the hold says why, and what to
press: usually raise the cap, then **Build again**), a secrets hit, a product that listens
beyond loopback, and a workflow of its own that could run on your runners or write to the
repository. What the factory's own sandbox could not measure is a **gap**, said beside the
checks; but when fewer than half of the flows could be measured, completeness holds.

## Lessons {#lessons}

When the checks hold a build for something its builder could have done better, repo-factory
writes down what went wrong, in one line (one short call to a model, a few cents; nothing for a
hold the builder could not have avoided, such as the spend cap). **Lessons** in the side nav
lists each lesson, where it came from, and how many briefs carried it. A lesson that is on goes
into the brief of every build from the next one on, word for word; switch it off to keep it out.
A build's page says which lessons its brief carries. Nothing is ever deleted. **Learn from held builds** in
Settings › Building (or `lessons.enabled: false` in loop.yaml) turns lessons off; `repo-factory loop lessons` lists them, and `repo-factory
loop lessons off L1` switches one off.

## Projects and maintenance {#projects}

**Projects** in the side nav lists each product you published, with where its maintenance
stands. **Maintenance is off until you switch it on for a repository**, on its project's page.

- **Switch maintenance on** opens one pull request on the repository (labels, the policy file
  `.github/repo-factory.yml`, AGENTS.md and CI) and creates its labels; nothing else changes on
  GitHub, and no repository setting. A product repo-factory published already carries the
  policy file, so the next poll may fix an issue you labelled `go` before you merge it; any other
  repository waits for that merge.
- Then, every 15 minutes, it reads the repository's open issues. **Only an issue whose `go`
  label you put on yourself is fixed** (GitHub's own record says who put a label on). Anyone
  else's issue, a `go` someone else put on, and anyone else's pull request are listed under
  **Waiting for you**, and nothing else happens: no model call, no label, no comment. An issue's
  text is data, quoted to the fix's sessions, never an instruction or a setting.
- **An issue someone else opened and then edited after your `go`** waits too ("edited after you
  labelled it: take go off and put it on again"): a fix reads the text as it is now, so your
  `go` counts only for the text you read.
- An agent you gave a token can open an issue (**Open issue**), but never with `go` on it: only
  you put `go` on.
- Someone else's pull request that says "Fixes #12" is listed as "a pull request by … says it
  fixes #12"; it never stops repo-factory's own fix of #12.
- **No fix starts while builds sign in with Claude Code's own sign-in file**, which would be
  copied into each fix session: run `claude setup-token` first and keep the token in
  Settings › Claude (the project's page says so).
- **No fix starts, and nothing merges, while a workflow on the default branch may run anywhere
  but GitHub's own runners** (any `runs-on` label that is not `ubuntu-…`, `windows-…` or
  `macos-…`, an expression other than a written-out matrix of those, a runner group or a
  reusable workflow): a fix's pull request runs
  the fix's code in that CI at once. The page shows which workflow.
- **A fix is a pull request** ("Fix #12: …") on a new `repo-factory/` branch, with what it
  reproduced and what it tested. It never pushes to your default branch, never force-pushes,
  never changes a workflow file, and writes nothing on the issue: what each fix did, and why one
  stopped, is on the project's page. After two failed attempts an issue waits for you; **Retry**
  lets it try again. repo-factory pushes it from a clone of its own, never from the folder the
  fix's sessions worked in.
- **Merge its own fixes when CI is green** is a second switch per repository, off by default:
  bugs and docs only, every check green, never a change under `.github/`, never a pull request
  anyone else pushed to, never while a workflow may run off GitHub's own runners (the page warns
  about one); then a patch release, when the policy file allows it.
- **Pause** stops the next poll from reading the repository at once, and stays paused after a
  restart. **Switch maintenance off** stops it altogether.
- **This week's proposals**: once a week, one capped call reads the README and the open issues'
  titles and suggests a few improvements. Nothing is built from one until you press **Build this
  proposal**: it then becomes an issue labelled `go` by you, which the next poll fixes.
- Maintenance has its own monthly limit (Settings › Spend; $20 by default) and a cap per fix ($5
  by default). At the limit it starts nothing more that month, and says so.

## Settings {#settings}

- **Claude**: what builds sign in to Claude with. Your Claude plan, set up with
  `claude setup-token`, or an API key, billed at API prices.
- **GitHub**: the account a build publishes under. Sign in with `gh auth login` in a terminal.
- **Building**: the flow (v5, the one the record is from, or lean, which is not proven yet),
  **Learn from held builds** (lessons on or off), and the model of each part of it, the lessons'
  writer among them.
- **Publishing**: ask first (the default), or publish on its own when the checks pass. The
  repository is private either way.
- **Spend**: a cap on one build and on one day, each a number of dollars or "none" (0 is not
  a cap: a hand-written 0 in loop.yaml stops every build until it is fixed). A build that
  reaches a cap stops, and says the cap stopped it. A build keeps its security review's money
  aside from the start, and **Ask for changes** spends from what the build's cap has left.
  **Maintenance** has its own line: what it spent this month, its monthly limit and a fix's cap.
- **Maintenance**: which published repositories it is on for, how often it looks, how many
  fixes run at once (they share the build slots), the weekly proposals, where a fix's
  session may connect (an allow-list: by default the Claude API and the Python package index;
  you add hosts in `loop.yaml`, `maintain.net_hosts`; adding GitHub's hosts lets a session that
  brings its own token post what it reads there), and
  **Claude's sign-in** (whether fixes may run on Claude Code's own sign-in file: off by
  default).
- **Appearance**: light, dark, or your device's; kept in this browser.

## Where things are kept {#files}

Everything repo-factory writes is in its home, `~/.repo-factory` (or `REPO_FACTORY_HOME`), in
your tasks folder, and in a repository you published.

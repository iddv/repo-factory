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
A build's page says which lessons its brief carries. Nothing is ever deleted. `lessons.enabled:
false` in loop.yaml turns lessons off; `repo-factory loop lessons` lists them, and `repo-factory
loop lessons off L1` switches one off.

## Settings {#settings}

- **Claude**: what builds sign in to Claude with. Your Claude plan, set up with
  `claude setup-token`, or an API key, billed at API prices.
- **GitHub**: the account a build publishes under. Sign in with `gh auth login` in a terminal.
- **Building**: the flow (v5, the one the record is from, or lean, which is not proven yet) and
  the model of each part of it, the lessons' writer among them.
- **Publishing**: ask first (the default), or publish on its own when the checks pass. The
  repository is private either way.
- **Spend**: a cap on one build and on one day, each a number of dollars or "none" (0 is not
  a cap: a hand-written 0 in loop.yaml stops every build until it is fixed). A build that
  reaches a cap stops, and says the cap stopped it. A build keeps its security review's money
  aside from the start, and **Ask for changes** spends from what the build's cap has left.
- **Appearance**: light, dark, or your device's; kept in this browser.

## Where things are kept {#files}

Everything repo-factory writes is in its home, `~/.repo-factory` (or `REPO_FACTORY_HOME`), in
your tasks folder, and in a repository you published.

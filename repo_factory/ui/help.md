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

The checks it passed, and anything that held it, are listed on the build's page in plain
words. They are the factory's own checks: useful, and not proof. Read what it built before you
make it public.

Some holds **Publish anyway** never overrides: nothing delivered, an open critical or high
security finding, **a security review that did not run** (the hold says why, and what to
press: usually raise the cap, then **Build again**), a secrets hit, a product that listens
beyond loopback, and a workflow of its own that could run on your runners or write to the
repository. What the factory's own sandbox could not measure is a **gap**, said beside the
checks; but when fewer than half of the flows could be measured, completeness holds.

## Settings {#settings}

- **Claude**: what builds sign in to Claude with. Your Claude plan, set up with
  `claude setup-token`, or an API key, billed at API prices.
- **GitHub**: the account a build publishes under. Sign in with `gh auth login` in a terminal.
- **Building**: the flow (v5, the one the record is from, or lean, which is not proven yet) and
  the model of each part of it.
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

# Security

## Reporting a problem

Please report security problems **privately**. This repository's **Security › Report a
vulnerability** form is not switched on yet; until it is, open an issue that says only that you
have a security report and how to reach you (no details), and a maintainer will ask for them in
private. Do not put the problem itself in a public issue. A maintainer answers in person,
usually within a week. A fix's public text says what it fixes, not how to exploit it.

## What runs where

repo-factory runs code a model wrote, on your machine, and can publish repositories under your
GitHub account. It is an experiment: read what it builds before you trust it.

- **The page and the command** run as you, on the host. The page binds 127.0.0.1 only, refuses
  other host names, and every write needs a sign-in token and a matching `Origin`.
- **Every Claude Code session, and every run of what it wrote**, runs inside bubblewrap
  (`bwrap`): the workspace writable, the system read-only, the rest of your home hidden, a
  private `/tmp`, and a network namespace of its own. Without `bwrap` nothing a model wrote
  runs.
- **What a session leaves behind** is checked before the host reads it: in the folders a
  session can write, only links that stay inside the folder are kept, and the host copies them
  as links. A product's packages install inside the sandbox too; the host makes the empty
  environment and never starts it.
- **GitHub** is only ever reached from the host: the sandbox has no `gh`, no token and no SSH
  key.

## The threat model

| Threat | Held by | What remains |
|---|---|---|
| Model-written code harms the machine | bubblewrap around every session and every run of its code | A kernel or bubblewrap flaw |
| It reaches other services on the machine or the network | Its own network namespace; the only way out is a host proxy that connects to public addresses only | **Egress to the internet is open during a build**: code can send what it can read (the workspace) anywhere |
| It steals the Claude login | The token goes to `claude` through a pipe, never the environment; tools run in a nested namespace that cannot see the process | Without a long-lived token, a copy of the credentials file is in the session and readable: run `claude setup-token` first |
| It steals the GitHub login | The sandbox has no `gh`, no token, no SSH key; the host does every push and API call. The host's git refuses a tree whose own config sets an `http.` setting or a remote's proxy (which would hand the credential to an address the tree chose). A maintenance fix is pushed from a clone the host made itself, to the address it builds from the repository's name, never from the session's clone | A fix session's allow-list holds no GitHub host by default; if you add GitHub's hosts (`maintain.net_hosts` in `loop.yaml`), a session that brings its own token could post what it reads there |
| A hostile `.git` runs commands on the host | The host's git runs with hooks, filters, credential helpers and command-valued settings off; odd `.git` layouts are refused; it never fetches from or pushes to a remote the host did not write at its clone (a planted `origin` would run that repository's hooks); maintenance carries a fix out of the session's clone as files | Builds (not maintenance) still commit in the session's tree with that hardened git: a git flaw in what is left on would reach the host |
| Prompt injection in what a session reads (a web page, a package's README, an issue) | A session can only write its workspace; an issue's text reaches a session only between document markers, and only for an issue you labelled `go` whose text nobody else changed since; issue titles reach the weekly proposals call (no tools) as quoted data; fixes are pull requests, never pushes to the default branch | An injected session can write bad code into the product; the exam and the gate are the check, and they are the same system |
| Who can start a maintenance fix | Maintenance is off per repository until you switch it on. Only an open issue whose `go` label you put on (the issue's events, at the poll, the fix's start and its push) starts a fix; one someone else opened and then edited (title or body) after your `go` waits; an agent's token never puts `go` on an issue; only a pull request this install's fix opened counts as the fix of an issue | Anyone who can make you label their issue `go` gives a session with tools their words as its task: read an issue before you label it |
| What a fix's pull request runs | No fix starts and none is pushed while a workflow on the default branch may run anywhere but GitHub's own hosted runners (every `runs-on` label `ubuntu-*`, `windows-*` or `macos-*`, or a written-out matrix of those; an expression, a group, any other label or a reusable workflow cannot be told); while sessions would sign in with Claude Code's own sign-in file, no fix starts at all (unless you allow it); the fix sessions reach the Claude API and the package index only | **A fix's pull request runs the fix's code in the product's CI at once**, on GitHub's runners, with that workflow's token and any secret it passes to `pull_request` jobs; GitHub's own rules for pull requests from a branch of the same repository apply |
| What merging takes | Merging its own fixes is a switch per repository, off by default, that asks first. Only a pull request this install's fix opened, while its head is the commit it pushed; its issue labelled exactly `bug` or `docs` (no kind label: no merge; a feature or task never); a docs fix changes documents only; a bug fix carries its reproducing test; no change to a manifest, a lock file, build or test settings, `.github/`, an agent's or editor's file, git's own files or a link; every check green. A release after a merge is a third switch, off by default | **With merging on, a fix nobody read is merged** when it passes those rules and CI: a source change can still be wrong or hostile in what it does, and green CI is the same system's check. With releasing on, the tag runs any workflow of the product on tags or releases, with its secrets |
| Supply chain: what it publishes | A secrets scan and an honesty check before the push; private first | A model can name a dependency that does not exist or is hostile. **Read a product before you make it public or run it elsewhere** |
| Supply chain: what it installs while building | Installs run inside the sandbox | Whatever a package's install code does there, with open egress |
| Another site in your browser drives it | Loopback only; non-loopback `Host` refused; writes need a token and a matching `Origin` | Reads need no token: any local process can read builds and logs |
| A runaway bill | Per-stage caps in the build policy; $25 a build and $50 a day by default | On a Claude plan, usage limits are Claude's |

## This repository's own CI

Pull requests run on GitHub's hosted runners only, with a read-only token and no secrets. No
job calls a model. A pull request from a first-time contributor from outside needs a
maintainer's approval before its workflow runs (GitHub's "first-time contributors" setting;
approval for every outside pull request is not switched on yet).

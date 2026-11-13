# Security

## Reporting a problem

Please report security problems **privately**, through this repository's
**Security › Report a vulnerability** form. Do not open a public issue for them. Say what you
found and how to see it; a maintainer answers in person, usually within a week. A fix's public
text says what it fixes, not how to exploit it.

## What runs where

repo-factory runs code a model wrote, on your machine, and can publish repositories under your
GitHub account. It is an experiment: read what it builds before you trust it.

- **The page and the command** run as you, on the host. The page binds 127.0.0.1 only, refuses
  other host names, and every write needs a sign-in token and a matching `Origin`.
- **Every Claude Code session, and every run of what it wrote**, runs inside bubblewrap
  (`bwrap`): the workspace writable, the system read-only, the rest of your home hidden, a
  private `/tmp`, and a network namespace of its own. Without `bwrap` nothing a model wrote
  runs.
- **GitHub** is only ever reached from the host: the sandbox has no `gh`, no token and no SSH
  key.

## The threat model

| Threat | Held by | What remains |
|---|---|---|
| Model-written code harms the machine | bubblewrap around every session and every run of its code | A kernel or bubblewrap flaw |
| It reaches other services on the machine or the network | Its own network namespace; the only way out is a host proxy that connects to public addresses only | **Egress to the internet is open during a build**: code can send what it can read (the workspace) anywhere |
| It steals the Claude login | The token goes to `claude` through a pipe, never the environment; tools run in a nested namespace that cannot see the process | Without a long-lived token, a copy of the credentials file is in the session and readable: run `claude setup-token` first |
| It steals the GitHub login | The sandbox has no `gh`, no token, no SSH key; the host does every push and API call | none known |
| A hostile `.git` runs commands on the host | The host's git runs with hooks, filters, credential helpers and command-valued settings off; odd `.git` layouts are refused | none known |
| Prompt injection in what a session reads (a web page, a package's README, an issue) | A session can only write its workspace; an issue's text reaches triage as quoted data in a call with no tools; fixes are pull requests, never pushes to `main` | An injected session can write bad code into the product; the exam and the gate are the check, and they are the same system |
| Supply chain: what it publishes | A secrets scan and an honesty check before the push; private first | A model can name a dependency that does not exist or is hostile. **Read a product before you make it public or run it elsewhere** |
| Supply chain: what it installs while building | Installs run inside the sandbox | Whatever a package's install code does there, with open egress |
| Another site in your browser drives it | Loopback only; non-loopback `Host` refused; writes need a token and a matching `Origin` | Reads need no token: any local process can read builds and logs |
| A runaway bill | Per-stage caps in the build policy; $25 a build and $50 a day by default | On a Claude plan, usage limits are Claude's |

## This repository's own CI

Pull requests run on GitHub's hosted runners only, with a read-only token and no secrets. No
job calls a model. A pull request from outside needs a maintainer's approval before its
workflow runs.

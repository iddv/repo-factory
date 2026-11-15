# Maintaining published products

**Maintenance is off until you switch it on for a repository** (its page under Projects). This
page says first what runs, what it writes to GitHub and behind which switch, then the files a
maintained product carries, the commands, the drift doctor and the identity maintenance uses.

## What runs (`repo_factory/maintain/watch.py`)

Every `maintain.poll_s` (15 minutes), the loop's tick polls each published repository that is
switched on (`<home>/maintain/repos.json`, `maintain/switch.py`) and not paused
(`<home>/maintain/paused.json`, and the loop's `PAUSE`). Nothing is read while the loop is
paused, and a repository that is switched off or paused is not read at all. Per repository:

1. **The policy file** `.github/repo-factory.yml` is read. Fixes start only while it says
   `status: maintained`: a product repo-factory published carries it from its first commit, so
   fixes may start at the next poll after the switch; for any other repository the setup pull
   request puts it there. A file that cannot be read is a row on the project's page.
2. **Your word** (`maintain/consent.py`). Each open issue labelled `go` has its events read:
   only a `go` put on by the login maintenance acts for (yours, or your GitHub App's) counts.
   When someone else opened the issue, its title (a `renamed` event) and its body (GitHub's
   `lastEditedAt`) must not have been changed by anyone but you after that `go`: a fix reads
   the text as it is, so an issue edited after your label waits, with the words "edited after
   you labelled it: take go off and put it on again". When the edit time cannot be read, it
   waits too. Everything else (anyone else's issue, a `go` someone else put on, anyone else's
   pull request, Dependabot's) is a "Waiting for you" row. No model call, no label, no comment.
   A pull request someone else opened that says "Fixes #N" is a row ("a pull request by X says
   it fixes #N"): it never holds the fix of N and is never counted as repo-factory's; only a
   pull request this install's fix opened (its line in `fixes.json`, with the commit it pushed
   and its `repo-factory/` branch) is "the fix of N".
3. **Fixes.** When a slot is free (`maintain.concurrency`, shared with builds), nothing of the
   repository is running, maintenance's money is not spent and the issue does not wait for you,
   the oldest of your `go` issues without an open fix gets one: `repo-factory maintain revise
   REPO N`, detached (`revise.Fix`). It reads the issue's events (and its edit time) again
   before it spends anything and again before the push, and the switch, the pauses and the
   monthly limit before every session and before the push. No fix starts, and none is
   pushed, while a workflow on `main` may run anywhere but GitHub's own hosted runners (a fix's
   pull request runs the fix's code in the product's CI at once; see 5), or while sessions
   would sign in with Claude Code's own sign-in file (see "Claude's sign-in" below). Its
   sessions run in the build sandbox, with no GitHub credential, and with `maintain.net:
   allowlist` reach only `maintain.net_hosts`: by default the Claude API and the Python
   package index, nothing on GitHub (see "Where a fix's session may connect"). The issue's
   text reaches them only between document markers.
   **After the sessions the host trusts nothing they could write** (`maintain/carry.py`): the
   session's clone, `.git` included, is never fetched or pushed from. The host clones the
   repository afresh into its own folder beside the workspace, from the address it builds from
   the repository's name (`https://github.com/<owner>/<name>.git`), at the commit the fix
   started from; it copies the changed files over as files (a link, a path out of the tree or
   through a link is refused), checks the scope again on that tree, commits there and pushes.
   The host's git (`gitcmd.py`) also refuses any tree whose own config sets an `http.` setting
   or a remote's proxy, and refuses to fetch or push where a tree's remote is not the one the
   host wrote at its clone. The result is a pull request on a new `repo-factory/` branch (a
   later attempt takes the next free name; nothing is force-pushed), labelled `repo-factory`
   and its kind, with what was reproduced and tested; the scope check refuses any change to
   `.github/` (workflows and the policy file included), LICENSE, NOTICE, or an agent's or
   editor's file. Attempts, outcomes and why live in `<home>/maintain/fixes.json`; after
   `revise.max_attempts` failed attempts the issue waits for your **Retry**. The next attempt's
   session hears where and why the last one stopped.
4. **Merging** (`watch.merge_step`), only with the repository's merge switch on (off by
   default; the page asks before it turns on and says what will merge). **Merging judges the
   diff, not the label** (`watch.diff_refuses`): at most one of the pull requests this
   install's fixes opened, a poll, only while its head is the commit the fix pushed; its issue
   must carry exactly one kind label, `bug` or `docs` (none: no merge; a `feature` or `task`
   never); a `docs` fix only when every changed file is a document (`*.md`, `*.rst`, `*.txt`,
   `docs/`); a `bug` fix only with its reproducing test in the diff; no fix that changes a file
   that runs somewhere (package manifests and lock files, build and test settings,
   `conftest.py`, `Makefile`, `Dockerfile`, `.github/` whole, `AGENTS.md`, `CLAUDE.md`,
   `.claude/`, `.vscode/`, `.devcontainer/`, `.pre-commit-config.yaml`, `.gitattributes`,
   `.gitmodules`, `.envrc`: `automerge.runs_somewhere`) or a link or submodule; then
   `automerge.judge` (not a draft, no `hold`, under `merge.max_lines`, no licence, no changes
   requested, up to date, every check green, on every page of check runs); never while a
   workflow on the default branch may run off GitHub's own runners. A pull request that is
   behind or conflicts waits: press *Update branch* on GitHub, or merge it yourself;
   repo-factory never updates a branch. A fix's own scope check refuses `.github/` whole and
   the agent and editor files too: a fix that needs one is a row for you. **A release after a
   merge is a third switch** (off by default, whatever the policy file says; it asks first):
   with it on, `release.step` cuts a patch release when the policy's `release.on_merge` allows
   it. The tag runs any workflow of the product on tags or releases, with its secrets.
5. **The drift doctor** (reads only): AGENTS.md against the tree, and any workflow that may run
   anywhere but GitHub's own hosted runners (`repo_factory/runners.py`): a job is hosted only
   when every `runs-on` label is `ubuntu-*`, `windows-*` or `macos-*`; an expression
   (`${{ vars.RUNNER }}`), a runner group, any other label (`self-hosted`, `[linux, my-own-box]`)
   or a reusable workflow cannot be told and counts as not hosted. The one expression read is
   `runs-on: ${{ matrix.<key> }}` when the job's matrix is written out and every value of that
   key (its list and each `include` entry's) is a plain label: each must then be hosted. Such a workflow is a warning on
   the page and stops fixes and merging. The publish hold for a new product
   (`loop/workflows.py`) uses the same rule.
6. **This week's proposals** (`maintain/proposals.py`), with `maintain.proposals` on: once a week
   a detached `repo-factory maintain propose REPO` makes one no-tools call ($0.50 at most) on
   the README and the open issues' titles. Nothing is built from a proposal until you press
   **Build this proposal**, which opens an issue labelled `go` by you and with the proposal's
   kind (a `feature` or `task` is fixed as one and never merged). A proposal's title and reason
   are sanitised (links out, mentions as code) before they are kept, so the page shows exactly
   what would be posted. A fix copies an issue's title into its commit and pull request with
   every issue reference broken (`#9` becomes `# 9`), so a title closes nothing. Every read and
   write means the repository's default branch, read once a poll.

### Everything maintenance writes to GitHub

| What | When | Its switch | Default |
|---|---|---|---|
| The setup pull request (`repo-factory/maintenance`) and the labels the repository does not have yet (one it has keeps its colour and description) | You press **Switch maintenance on** | the repository's maintenance switch | off |
| A fix's branch (`repo-factory/N-…`, never overwritten) and its pull request, with two labels on that pull request | One of your own `go` issues, at a poll | the repository's maintenance switch, and the policy file's `status: maintained` | off |
| A merge of one of its own fix pull requests | A poll, all the rules above | the repository's merge switch | off |
| A tag and a GitHub release | Right after such a merge | the repository's release switch (third), and `release.on_merge` | off |
| An issue labelled `go` and the proposal's kind | You press **Build this proposal** | the repository's maintenance switch | off |

Nothing else: no comment and no label on any issue, no push to the default branch, no
repository setting. The App, when you make one (Settings › GitHub), can write issues, pull
requests and contents only; never workflows, secrets or administration.

An agent's token never puts `go` on an issue: an agent's **Open issue** (`cut_ticket`) opens the
issue without it and says so; only your press on the page or the command line puts it on.

### Claude's sign-in

A fix's task is someone else's words. When builds sign in with Claude Code's own sign-in
(`claude auth login`), its sign-in file is copied into each session, where that session can
read it. So while that is how sessions would sign in, **maintenance starts no fix**: the
project's page, the switch's confirm and `repo-factory doctor` say "run `claude setup-token`
first". Keep the token it prints in Settings › Claude: a session gets it through a pipe, never
as a file. `maintain.allow_signin_file: true` (Settings › Maintenance, off by default) lets
fixes run on the sign-in file anyway, for a person who accepts that.

### Where a fix's session may connect

With `maintain.net: allowlist` (the default), a fix's session reaches only
`maintain.net_hosts`: by default `api.anthropic.com`, `pypi.org` and `files.pythonhosted.org`.
GitHub's hosts are not on it. You may add them in `loop.yaml` by hand (never from a request;
Settings › Maintenance shows the list); know
what that opens: a fix's task is the text of an issue, and a session that brings its own token
(from that text) could post what it reads, your repository's source included, to a gist or a
repository on GitHub. The host does every GitHub write itself; a session never needs GitHub.

### Money

Fixes (`revise`) and proposals (`evolve`) are spend kinds the daily cap counts, and
`maintain.monthly_usd` ($20) limits what they spend in a calendar month; `maintain.max_usd`
($5) caps one fix. Every session of a fix runs under the smaller of what is left of both. At
the limit nothing new starts and the page says so; a fix already running stops before its next
session. `maintain.max_usd: null` together with the policy file's `revise.max_usd: null` leaves a
fix with no cap of its own: only the month's and the day's limits then hold it.

"You", for every rule above, is each identity this install acts as: the signed-in `gh` login,
your GitHub App's bot when you made one, and the user of `maintain.token_env_file` when you set
one (read once a poll).

### Files

`<home>/maintain/`: `repos.json` (the switches), `paused.json`, `fixes.json`, `proposals.json`,
`cache.json` (what the last poll saw, for the pages; disposable), `setup/` (the setup's
clone), `backfill/` (its state and log), `work/` (a fix's clone while it runs).

## What a maintained product carries

The setup pull request (`maintain/backfill.py`, run by `repo-factory maintain setup REPO` when
you switch maintenance on) writes these files; a product repo-factory published already
carries the policy file and the label file from its first commit.

| File | What it is |
|---|---|
| `AGENTS.md` | The one instruction file for any coding agent: the commands, the architecture in brief, the conventions, the operator settings and the maintenance protocol. CI runs its commands, so it cannot drift silently. |
| `CLAUDE.md` | `@AGENTS.md`, so Claude Code reads the same file. |
| `.github/repo-factory.yml` | The policy: `maintained`, `as-is` or `archived`; what triggers a fix (`go`); how releases bump. A repo without the file is `as-is`: no fix starts there. |
| `.github/labels.yml` | The 20 labels, one JSON object per line. |
| `.github/workflows/ci.yml` | The unit suite on the oldest supported Python and 3.12, the acceptance suite, `check_agents_md.py`, and gitleaks over the history. GitHub-hosted runners only, `permissions: contents: read`, every action pinned to a commit. |
| `.github/workflows/labels.yml` | Syncs the labels from `labels.yml` when it changes (creates or updates; never deletes). |
| `.github/scripts/check_agents_md.py` | Runs the shell fences (`sh`, `bash`, `shell`, `console` or none) under the setup heading (*Setup, run, test*, *Set up, run, test*, *Setup*, *Development*, *Running tests*…, any case) line by line (a `# serve` line is started on a free port, waited for on 127.0.0.1, and stopped; a server without that mark, a line with a placeholder such as `<file>` and an install onto the machine are examples, not steps: not run, and named in its summary), checks the architecture paths exist, and fails when a known open item passes but still carries its xfail mark. Standard library only. The drift doctor reads AGENTS.md with the same readers. |
| `.github/scripts/sync_labels.py` | The label sync, standard library plus `gh`. |
| `.github/dependabot.yml` | Monthly Actions updates, labelled `dependency` (a `pip` block only when the repo has Python dependencies). |
| `.github/ISSUE_TEMPLATE/` | Bug and feature forms that pre-apply `triage` and the kind; blank issues off; security reports go to private advisories. |
| `.github/pull_request_template.md` | The checklist every fix PR follows. |
| `CHANGELOG.md`, `.gitignore` | Written only when the repo has none. |

The setup runs the drift doctor and `check_agents_md.py` (both suites) in the sandbox before
anything is pushed; a failure stops it and the project's page says why. It applies the labels
the repository does not have yet, changes no repository setting, and stops with nothing
written once maintenance is switched off or paused while it runs. Running it again changes
nothing that is already right, and refreshes the open PR's body.

## The labels

| Group | Labels |
|---|---|
| Kind (one) | `bug`, `feature`, `question`, `security`, `dependency`, `docs`, `task` |
| Flow | `triage` (new), `needs-info`, `go` (your approval to spend), `needs-you` |
| Priority | `p1`, `p2`, `p3` |
| Outcome | `duplicate`, `wontfix`, `cannot-reproduce`, `upstream` |
| Learning / PR | `lesson`, `repo-factory` |

Only people with triage or write access can set a label, so `go` is a trusted signal and the
issue text never is. repo-factory itself puts labels only on its own pull requests
(`repo-factory` and the kind) and on an issue you ask it to open.

## Commands

```sh
repo-factory maintain labels owner/repo [--dry-run]             # create or update the 20 labels
repo-factory maintain labels --print                            # the labels.yml file
repo-factory maintain policy owner/repo | --file F | --template # the effective repo-factory.yml
repo-factory maintain doctor PATH                               # AGENTS.md drift in a checkout
repo-factory maintain doctor --repo owner/repo                  # the doctor on GitHub's main (no clone)
repo-factory maintain tick [--home H] [--json]                  # one poll now
repo-factory maintain status [--home H] [--json]                # what the last poll saw
repo-factory maintain revise owner/repo N [--home H]            # fix issue N now, in the foreground
repo-factory maintain recheck owner/repo [--home H] [--detach]  # Re-check: main's head and the doctor
                                                       #   now (reads only)
repo-factory maintain whoami [--home H] [--json]                # the identity in use, its scope, expiry
```

`labels` never deletes a label; it prints the `gh label delete` lines for GitHub's default
labels that ours replace, for you to run if you want. `doctor` needs no model and runs nothing:
it reports fence commands that the README does not have, architecture rows that point nowhere,
modules the table does not name, and workflows that may run off GitHub's own runners.

## The drift doctor

The doctor checks a maintained repo's `main` on GitHub, without a model and without a clone:
the tree, `AGENTS.md`, the README, `CLAUDE.md`, the policy, the label file and each workflow.
What it finds is on the repository's page; a workflow that may run off GitHub's own runners
stops fixes and merging.

**When it runs.** GitHub cannot push to repo-factory: repo-factory listens on 127.0.0.1 only, so there is no
address for a webhook. Instead every poll (`maintain.poll_s`, 15 minutes by default) asks
GitHub for `main`'s head with a conditional request (`If-None-Match`); when nothing moved,
GitHub answers 304, which costs no rate limit. When `main` moved, the doctor runs again in that
poll (merging the setup pull request moves `main`, so the doctor reads it at the next poll),
and at least every `maintain.doctor_days` days (7; 0 turns it off) for a repo that did not move.

**Re-check** (registry `maintain_recheck`; `repo-factory maintain recheck owner/repo`) reads
`main`'s head now, runs the doctor and refreshes the project's numbers, in the background. It
is a read, so it asks for no confirm, and nothing is written to GitHub.

## Identity on GitHub

By default maintenance acts as the owner's `gh` login, which reaches every repository the
login can, workflow files included. The narrower way, in about a minute, is **repo-factory's own
GitHub App**: Settings › Publishing › GitHub › *Create repo-factory's GitHub App* posts GitHub's app manifest for an
app named `repo-factory-<your login>` (issues, pull requests and contents write; metadata, checks and
actions read; no webhook), GitHub sends the browser back to repo-factory on 127.0.0.1 with a one-hour
code, and repo-factory exchanges it in the background for the app's id and private key. Then *Choose
repositories* installs the app on the repositories repo-factory maintains (and repo-factory's own, for its
factory issues). From then on maintenance and your factory issues act as
`repo-factory-<you>[bot]`: each call gets a one-hour installation token minted from a signed JWT,
narrowed to the fewest permissions and the one repository it acts on (a factory issue: issues
write on that repo; a fix's push: contents write on that repo), kept in memory and minted again
under five minutes; commits a fix pushes are authored as the bot, so its pull requests carry the
badge. What the app can never get, fixed in repo-factory's code and pinned by a test: administration,
workflows, secrets, actions write, or any organisation permission. So it cannot make a
repository public, change a workflow file (GitHub refuses the push), read a secret or create a
repository: publishing keeps the `gh` login, and so do pull requests from a fork.

The key lives in `~/.config/repo-factory/github/app.pem` (mode 600, the directory 700) beside the
door tokens, and `app.json` beside it holds the facts and no secret. Only the signer opens the
key; it never reaches a page, a log, a build session (the sandbox binds neither
`~/.config/repo-factory` nor the loop's environment) or GitHub. `maintain.identity` in `loop.yaml`
picks: `auto` (the default: the app once installed, else the token file below, else the login),
`app`, `token` or `login`. A repository the installation does not reach is refused before any
call ("add it to repo-factory's app"), never the login; an app GitHub refuses (uninstalled,
suspended, its key replaced) is said, and nothing falls back to the login.
*Remove repo-factory's app* uninstalls it and deletes the key and record here; the registration is
deleted on GitHub. `repo-factory github status|refresh|key --file|remove` does the same from the
command line.

repo-factory's GitHub App is not the "repo-factory" OAuth App that *repo-factory's GitHub sign-in* goes through (the
device flow; Settings › Publishing › GitHub › repo-factory's GitHub sign-in names it and its client id).
The sign-in is enough to publish and to read your repositories; the App is the optional bot.
After *Create*, the page waits five minutes for GitHub to send the browser back (or until
*Cancel*), then puts Create back and says the try did not finish; a callback GitHub still sends
within ten minutes is kept, so an app made late is not lost. An app that exists on GitHub but
not here (a closed tab, a lost record) is **linked, never made twice**: *Link an app you made*
(or `repo-factory github link --app-id N --file app.pem`) takes its App ID and a private key generated
under its settings, checks the pair with `GET /app`, keeps the key at 0600 and reads the
installation back. Nothing is created on GitHub.

On another install, where repo-factory cannot write on its own repository, the factory
issue becomes **Report to repo-factory ↗**: GitHub's new-issue page prefilled with the same body after
a scrub (secrets, e-mail addresses, phone numbers, `@user`, paths, the host, the login), which
the owner reads and submits; repo-factory files nothing. The owner's own filing goes through the same
scrub.

The other narrow identity is a **fine-grained personal access token**
(on your account, or on a bot account that is a collaborator with write on each product repo):
*Repository access* only the product repos; *Contents*, *Issues* and *Pull requests* read and
write, *Metadata* read; no *Workflows*, *Administration* or *Secrets*. Put it in a file only you
can read, outside the repo-factory home:

```sh
mkdir -p ~/.config/repo-factory && umask 077
printf 'GH_TOKEN=%s\n' "<the token>" > ~/.config/repo-factory/maintain.env
```

and name it in `loop.yaml`:

```yaml
maintain:
  token_env_file: ~/.config/repo-factory/maintain.env
```

The file is read for each `gh` call and handed to that subprocess only (a fix's `git push` asks
the same `gh` for credentials); it is never logged, shown or kept, and a file other users can
read is refused. It is set by hand, never from the app. `repo-factory maintain whoami`, Settings ›
Maintenance and `repo-factory doctor` (its `maintain` line) say which identity is in use: the owner's
login (with its scopes), repo-factory's own app (`repo-factory-<you>[bot]`, its permissions and how many
maintained repos its installation reaches), a fine-grained token for your account or for a bot
account, its expiry (a warning 30 days before), and how many maintained repos it reaches. With
the app or a bot account, repo-factory's pull requests are visibly not yours. Publishing new
repos keeps the owner's login.

## Security

Product CI runs on GitHub-hosted runners only; a self-hosted runner is never attached to a
product repo, and the doctor flags any workflow that may run off GitHub's own runners.
Workflows read with `contents: read` and check out without persisting credentials. The
templates carry nothing private (`tests/test_no_secrets.py` scans them).

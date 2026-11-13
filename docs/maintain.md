# Maintaining published products

repo-factory maintains what it publishes through GitHub: a product's issues are the work queue, their
labels are the state, a pull request is the proposal, and the owner's merge is the approval.
repo-factory keeps no maintenance table. This page covers the files every product carries and the
commands that put them there, then triage, fixes, releases, learning from user-found bugs, the
drift doctor and the identity repo-factory uses on GitHub.

A repository repo-factory did not build is maintained too, with its policy kept in repo-factory and narrower
defaults: see [maintain-adopt.md](maintain-adopt.md). `maintain.repos` (the old "More repos to
maintain" list) is no longer read: each name in it became an unconfirmed watch profile.

## What a maintained product carries

| File | What it is |
|---|---|
| `AGENTS.md` | The one instruction file for any coding agent: the commands, the architecture in brief, the conventions, the operator settings and the maintenance protocol. CI runs its commands, so it cannot drift silently. |
| `CLAUDE.md` | `@AGENTS.md`, so Claude Code reads the same file. |
| `.github/repo-factory.yml` | The policy: `maintained`, `as-is` or `archived`; what triggers a fix (`go`); whether repo-factory may merge (never, by default); how releases bump. A repo without the file is `as-is`: repo-factory labels and answers, and spends nothing. |
| `.github/labels.yml` | The 22 labels, one JSON object per line. |
| `.github/workflows/ci.yml` | The unit suite on the oldest supported Python and 3.12, the acceptance suite, `check_agents_md.py`, and gitleaks over the history. GitHub-hosted runners only, `permissions: contents: read`, every action pinned to a commit. |
| `.github/workflows/labels.yml` | Syncs the labels from `labels.yml` when it changes (creates or updates; never deletes). |
| `.github/scripts/check_agents_md.py` | Runs the shell fences (`sh`, `bash`, `shell`, `console` or none) under the setup heading (*Setup, run, test*, *Set up, run, test*, *Setup*, *Development*, *Running tests*…, any case) line by line (a `# serve` line is started on a free port, waited for on 127.0.0.1, and stopped; a server without that mark, a line with a placeholder such as `<file>` and an install onto the machine are examples, not steps: not run, and named in its summary), checks the architecture paths exist, and fails when a known open item passes but still carries its xfail mark. Standard library only. The drift doctor reads AGENTS.md with the same readers. |
| `.github/scripts/sync_labels.py` | The label sync, standard library plus `gh`. |
| `.github/dependabot.yml` | Monthly Actions updates, labelled `dependency` (a `pip` block only when the repo has Python dependencies). |
| `.github/ISSUE_TEMPLATE/` | Bug and feature forms that pre-apply `triage` and the kind; blank issues off; security reports go to private advisories. |
| `.github/pull_request_template.md` | The checklist every fix PR follows. |
| `CHANGELOG.md`, `.gitignore` | Written only when the repo has none. |

## The labels

| Group | Labels |
|---|---|
| Kind (one) | `bug`, `feature`, `question`, `security`, `dependency`, `docs` |
| Flow | `triage` (new), `needs-info`, `go` (the owner's approval to spend), `needs-you` (repo-factory could not) |
| Claim (at most one) | `claim:repo-factory`, `claim:agent`; a person's claim is the assignee |
| Priority | `p1`, `p2`, `p3` |
| Outcome | `duplicate`, `wontfix`, `cannot-reproduce`, `upstream` |
| Learning / PR | `lesson`, `repo-factory` |

Only people with triage or write access can set a label, so `go` is a trusted signal and the
issue text never is.

## Commands

```sh
repo-factory maintain labels owner/repo [--dry-run] [--prune]   # create or update the 22 labels
repo-factory maintain labels --print                            # the labels.yml file
repo-factory maintain policy owner/repo | --file F | --template # the effective repo-factory.yml
repo-factory maintain doctor PATH [--run --python P]            # AGENTS.md drift in a checkout
repo-factory maintain backfill owner/repo [--work DIR] [--python P] [--no-pr] [--detach --yes] ...
repo-factory maintain cleanup owner/repo [--drop-suite] [--dry-run]  # repo-factory's checks out of an older
                                                       #   product, one PR (see below)
repo-factory maintain tick [--home H] [--json]                  # one poll now: triage, Dependabot,
                                                       #   releases, the next go issue's fix
repo-factory maintain status [--home H] [--json]                # what the last poll saw
repo-factory maintain revise owner/repo N [--home H]            # fix issue N now, in the foreground
repo-factory maintain release owner/repo [--home H]             # release main if it moved past a tag
repo-factory maintain doctor --repo owner/repo                  # the doctor on GitHub's main (no clone)
repo-factory maintain recheck owner/repo [--home H] [--detach]  # Re-check: main's head, the doctor and
                                                       #   the project's drift now (reads only)
repo-factory maintain whoami [--home H] [--json]                # the identity in use, its scope, expiry
repo-factory lessons postmortem --issues [--repo R] [--reread]  # read the user-found bugs (see Learning)
```

`labels` never deletes unless `--prune` is given. `doctor` needs no model and runs nothing
unless `--run`: it reports fence commands that the README does not have, architecture rows
that point nowhere, modules the table does not name, and workflows on a self-hosted runner.

**Backfill** gives an existing product everything above in one pull request:

1. clones the repo (default `<loop home>/maintain/backfill/<name>`), branch `repo-factory/maintenance`
   (a commit you add on that branch survives a re-run);
2. renders the files from the repo's own documents: product, audience and commands from the
   README, the flow numbers from `SCOPE.md` (never its sentences: it is the spec written from the
   pitch, so *Does:* points to the README's description), open items from `tests/acceptance/README.md`, settings from
   `ASSUMPTIONS.md`, the architecture rows from the README's layout list and the module
   docstrings (`--describe-modules` asks a model for any module that has neither). The unit
   test command is the README's own, exactly as written: a fenced line under a *Tests*
   heading, or an inline one in a sentence about tests ("Run the tests: `python3 -m unittest
   discover -s tests -v`"), else `<launcher> test`, else the repo's CI. When none of them names
   one, AGENTS.md says so and the doctor reports it; no command is invented;
3. runs the doctor and `check_agents_md.py` (both suites) in a throwaway virtual environment
   with pip and a throwaway `HOME`, both made for the check and removed after it (`--python P`
   uses that interpreter instead). A service the quick start leaves running (`start -d`) is
   stopped afterwards. A known open item that passes now gets its mark and README line removed
   in one more commit, listed in the PR, and the check runs again. A failure stops here and
   nothing is pushed;
4. applies the labels, sets *delete head branches on merge*, enables Dependabot alerts;
5. pushes and opens the PR, labelled `repo-factory`, listing the files, the policy, the checks, what
   could not be set, and the owner's checklist (merge; prune GitHub's default labels with the
   listed commands; fix Actions billing or go public; choose the maintenance token).

Running it again changes nothing that is already right, and refreshes the open PR's body.

**A new build carries the policy from its first commit** (`publish.maintenance_files`,
`policy` by default; `none`: a bare repo): `.github/repo-factory.yml`, starting as `maintained` with fixes only on `go`, and
`.github/labels.yml`, the two files repo-factory itself reads (`publish.maintenance_files: policy`).
`AGENTS.md`, CI and the rest are the product's own: the build writes them from a house rule,
as part of the product, and its own tests and README commands are what they run. repo-factory's
checks are not in the repository; they are the release's checks asset. A build that wrote no `AGENTS.md` gets one all the same: repo-factory renders it from
the template and the build's own documents, as `all` does, and records the drift doctor's
findings on the build (`publish.maintenance.doctor`); the product's own `AGENTS.md` always wins.
A maintained product is never published without one. `maintenance_files: all` renders the whole set above from templates, as
before; `none` gives a bare repository. The public text is then written from the product into the README and `AGENTS.md`, and reviewed once (never a hold).
Right after `gh repo create`, the labels are applied and *delete head branches on merge* and
Dependabot alerts are set; a failure there is recorded on the build, never fatal (the backfill
command redoes it). Publishing itself keeps the owner's `gh` login.

**A product published before the checks moved out** gets one cleanup pull request:

```sh
repo-factory maintain cleanup owner/repo [--drop-suite] [--task DIR] [--no-model] [--dry-run]
```

It removes repo-factory's scaffolding and documents (`check_agents_md.py` and its CI job,
`READINESS.md`, `ASSUMPTIONS.md`, the review sections of `SECURITY.md`, which keeps its
reporting policy and the findings still open as known issues), keeps
`tests/acceptance/` as the product's own suite and its CI step (`--drop-suite` removes both
and the README section), points NOTICE at a checks asset built from the delivered round, and
writes `AGENTS.md` again from the README and the code (one call to a model with no tools;
`--no-model` only removes the lines about what went). The PR body gives the command that
uploads the asset to the latest release. repo-factory never merges it.

## Polling and triage

The loop's tick polls every maintained repo every `maintain.poll_s` seconds (900 by default):
each published build's repo, plus each adopted repo that is not paused. Nothing runs while the loop
is paused or today's spend is over `daily_usd`. `repo-factory maintain tick` runs one poll now;
`repo-factory maintain status` prints what the last one saw, without calling GitHub.

**Pause maintenance on one project** (its ⋯ menu or its Settings; the verb
`pause_maintenance`; `repo-factory projects maintenance ID paused|on`): the repo is skipped by the poll,
a published build's and an adopted one's alike, from the moment it is chosen: no triage, fix,
merge, release, label, comment or model call on it. It is written in repo-factory
(`<loop home>/maintain/paused.json`, 0600: the repo, when, by whom); no pull request, and the
repo's `.github/repo-factory.yml` is left as it is (its `status:` stays the policy for when maintenance
resumes). For an adopted repo the profile's status is set to `paused` too, and set back on
resume only when this pause set it. A running fix goes on; Stop round stops it. Resume picks up
from the next poll. The project's health word, its row and Build › Repos say "Maintenance
paused since …, by …". The project's own reads (its stats, a clone) go on: they are the
project's, paused by Pause project.

Per repo, one read of the open issues and pull requests, then:

- **the policy**, `.github/repo-factory.yml`. A missing file is `as-is` (triage only, no spend on
  fixes). A file that does not parse is reported once, in a comment on the newest issue, and the
  repo is left alone until it is fixed. `archived` gets one comment, then nothing;
- **triage** of every issue labelled `triage`: one call to a model with no tools
  (`maintain.triage_model`, Haiku 5.5; about $0.05–0.10 through Claude Code), which can only
  answer with a kind, a priority, a duplicate's number, one question for the reporter, a
  summary and, for a question, an answer from the README. repo-factory adds the labels, removes
  `triage`, and posts one comment marked `<!-- repo-factory:triage v1 -->`, with the model's words
  stripped of links and `@mentions`. An issue in `needs-info` is triaged again when its reporter
  answers, and the same comment is edited. At most `maintain.triage_max_per_poll` calls (20) a
  poll;
- **`go` never comes from text.** Only the policy can add it: `revise.trigger: owner` for the
  owner's own issues, or `revise.auto_go: patch` for a p1/p2 bug or security report by a
  collaborator with write access. Both are off by default;
- a **stranger's pull request** is triaged on its title and body only; repo-factory never fetches its
  diff or runs its code;
- a **Dependabot** pull request gets `dependency`. repo-factory merges it (squash) only when
  `merge.dependabot` allows its level (`patch` or `minor`; a major waits) and its CI is green.
  The default is `never`.

What the poll saw goes to `<loop home>/maintain/cache.json`: when each repo was polled, its
counts, its rows in Needs you and which fix process is running. The file is disposable: delete it, and
the next poll arrives at the same state, because every label and comment is on GitHub.

## Fixes (revise)

repo-factory spends on a fix only for an open issue labelled `go` (the owner's signature: only people
with triage or write access can add a label), of any kind but `question`, with no
`needs-you`, `cannot-reproduce`, assignee, open pull request saying `Fixes #N`, or live claim.
`revise.kinds` bounds only what repo-factory labels `go` by itself (triage's `auto_go`, and the change
ideas it accepts); the owner's `go`, or their Accept or Build it on a change idea, starts any
kind. A decision ("Decide …") is filed as a `question` and never gets `go`.
`p1` goes first, then `p2`, `p3`, then the oldest. One fix at a time per repo, at most
`maintain.concurrency` (1) at once, and a fix takes a build slot (`concurrency`) while it runs.

1. **Claim.** repo-factory adds `claim:repo-factory` and one comment carrying
   `<!-- repo-factory:claim lease_until=… branch=repo-factory/<N>-<slug> by=repo-factory -->`, edited at every stage
   (the lease is `revise.lease_hours`, 6). An expired claim of another agent with no open pull
   request is taken over, and the comment says so.
2. **The fix runs detached** (`repo-factory maintain revise REPO N`), in a task folder under
   `<tasks_dir>/fix-<owner>-<name>-<N>`, on a fresh clone of `main` at
   `<loop home>/maintain/work/`. Its stages
   (`policies/v5-revise/rules.yaml`):
   - **reproduce**: a session writes one test in the product's own suite (in a repository that
     kept `tests/acceptance/`, there), or names the known-open-item test that already checks
     the report. For a `bug` or `security` issue the test reproduces the failure; for any other
     kind (`feature`, `task`, `dependency`) it specifies the requested behaviour
     (`prompts/specify.md`), which fails on `main` because it is not there yet; a `docs` issue
     skips this stage. repo-factory runs it on `main` and keeps it only when it fails for the reason
     the session stated. Otherwise the issue gets `cannot-reproduce` and a comment;
   - **build**: a session makes that test pass with the smallest change, and keeps AGENTS.md
     and SECURITY true. It leaves CHANGELOG alone: the release notes list the merged pull
     requests, and a line per fix made every open fix conflict with the next;
   - **verify** (no model): the open-item mark and its README line come off; every test runs on
     the fixed tree, sandboxed with the network off; the test must pass, and nothing that passed
     on `main` may fail;
   - **repair** and **reverify**: one more session with what verify found, then verify again;
   - **readiness** (no model): `check_agents_md.py` runs AGENTS.md's commands exactly as written
     (the quick start, the product's own tests), in the sandbox, on `main` and on the fix. It is
     a regression check: a command that ran on `main` must still run on the fix; one that
     already fails on `main` is said in the PR body and stops nothing. With `maintain.exam: report`
     (the default) the checks from the latest release's asset run on `main` and on the fix,
     sandboxed with the network off; a check that passed on `main` and fails on the fix is
     named in the PR body. `require` stops the fix instead; `off` skips it;
   - **deliver**: the scope check refuses a diff touching `.github/workflows/`,
     `.github/repo-factory.yml`, `LICENSE`, `NOTICE` or a secret-shaped file; the changed files are
     scanned for secrets and build jargon; repo-factory pushes `repo-factory/<N>-<slug>` and opens the pull
     request (`Fix #N: …`, labelled `repo-factory` and the kind, the body saying what, why, the test,
     the checks run, the documents and the cost, and `Fixes #N`), then removes its claim.
3. **A failure** is one comment on the issue (`<!-- repo-factory:revise attempt=K result=failed … -->`)
   with the stage, the reason and the cost; work after the fix stage is pushed to
   `repo-factory/<N>-<slug>-attemptK`. The next attempt's fix session is told where and why the last
   one stopped (a retry on the same inputs fails the same way). After `revise.max_attempts` (2)
   failures the issue gets `needs-you`; an error in repo-factory itself (stage `error`) gets it at
   once, since a retry would only repeat it. Removing it (Try again, in Needs you) lets repo-factory start over: the next poll posts a
   reset comment, and the count begins again.

**A missing or broken AGENTS.md holds nothing.** Readiness compares the fix with `main`, so a
command that fails on `main` is not the fix's (until 2026-10-16 it held the repo's whole queue:
Driftwatch waited 72 hours, the docs fix that would have repaired it included). The poll reads
`AGENTS.md` on `main` (one call), and while it is missing, has no shell fence under a setup heading or
unit test command, or its commands fail on `main`, the repo gets one row (Needs you, Build ›
Repos, the project, `repo-factory doctor`, `repo-factory maintain status`) that says what is wrong
and offers the action that fixes it: **Open the backfill PR** (registry `maintain_backfill`,
confirm class, the page and the command line only: `repo-factory maintain backfill REPO --detach
--yes`). It runs `repo-factory maintain backfill` in the background, and the row shows its state
(opening, the PR to review, or why it stopped). Fixes go on meanwhile.

An issue already labelled `needs-you` only because `AGENTS.md` was missing gets one comment
saying the cause is gone; its labels stay yours (remove `needs-you` to let repo-factory try again).

repo-factory never opens the backfill PR by itself. Its fixes never touch `.github/workflows/`, and the
backfill PR writes CI; the policy's `go` (or `auto_go`) is the only automatic start of anything,
and it is for issues.

A fix costs about $5–9; nothing caps it unless `maintain.max_usd` or the repo's
`revise.max_usd` is set (the smaller wins). The sessions run in the build sandbox with no GitHub
credential. With `maintain.net: allowlist` (the default) their proxy reaches only
`maintain.net_hosts` (the Claude API, PyPI, GitHub). The issue's text reaches them only inside
document delimiters, as data. repo-factory never merges its own pull request (`merge.factory_patch:
never`).

## Releases

After a merge, when `release.on_merge` is not `off`, the next poll compares the newest version
tag with `main`. When `main` has moved, repo-factory creates the next tag and a GitHub release with
generated notes: a minor bump if any merged pull request did a `feature`, else a patch (with
`on_merge: minor`, always at least a minor). `repo-factory maintain release REPO` does the same by hand.
A merged fix of a user-reported `bug` or `security` issue that repo-factory reproduced (and did not
open itself) also gets the `lesson` label and one `<!-- repo-factory:lesson v1 … -->` comment, for the
nightly learning job (`maintain.lesson_label`).

## Learning from user-found bugs

`repo-factory lessons postmortem --issues` searches the maintained repos for closed issues labelled
`lesson` (one search per five repos) and reads each one once: one no-tools call (Sonnet 5.5 by
default, `--model`) over the issue (as data, inside document delimiters), the fix's diff and
its test proposes at most one general lesson for the claims writer or the verifier. The lesson
must pass the lessons lint (no domain words, no numbers or identifiers, no quote of the issue,
no duplicate); it joins the candidate file (`policies/v5/lessons.next.yaml`, or `--candidate`)
as `probation` with `born.issue: owner/repo#N`, where it waits for the bench like any lesson.
Then a `<!-- repo-factory:lesson-read v1 lesson=L… -->` comment on the issue says so; that comment is
what makes the next run skip the issue (`--reread` reads them again). An issue labelled
`lesson` by hand, without repo-factory's marker, is skipped. The spend is recorded as `lesson`.
Settings › System › Your grades lists these lessons and how many candidates GitHub has.

## The drift doctor

The doctor checks a maintained repo's `main` on GitHub, without a model and without a clone:
the tree, `AGENTS.md`, the README, `CLAUDE.md`, the policy, the label file and each workflow.
Drift between `AGENTS.md` and the code, and any workflow whose `runs-on` names a self-hosted
runner, become a row in Needs you until fixed; the build page's Maintenance box says "AGENTS.md
current" or lists the problems.

**When it runs.** GitHub cannot push to repo-factory: repo-factory listens on 127.0.0.1 only, so there is no
address for a webhook. Instead every poll (`maintain.poll_s`, 15 minutes by default) asks
GitHub for `main`'s head with a conditional request (`If-None-Match`); when nothing moved,
GitHub answers 304, which costs no rate limit. When `main` moved, the doctor runs again in that
poll, and the project's drift (Build › Projects) is refreshed first in the same tick. So a merged
fix clears its row within one poll interval. The doctor also runs after a backfill finishes,
and at least every `maintain.doctor_days` days (7; 0 turns it off) for a repo that did not
move.

**Re-check.** The drift row (Needs you, the repo under Build › Repos, Build › Projects) says
how old its result is ("checked 2 d ago at a1b2c3d") and has a **Re-check** button (registry
`maintain_recheck`; `repo-factory maintain recheck owner/repo`). It is a read, so it asks for no
confirm: in the background it reads `main`'s head, runs the doctor and refreshes the project's
clone, drift and numbers. Nothing is written to GitHub. The row says "Checking…" while it runs,
then shows the new result, or why the check stopped.

## Identity on GitHub (decision D1)

By default maintenance acts as the owner's `gh` login, which reaches every repository the
login can, workflow files included. The narrower way, in about a minute, is **repo-factory's own
GitHub App**: Settings › Publishing › GitHub › *Create repo-factory's GitHub App* posts GitHub's app manifest for an
app named `repo-factory-<your login>` (issues, pull requests and contents write; metadata, checks and
actions read; no webhook), GitHub sends the browser back to repo-factory on 127.0.0.1 with a one-hour
code, and repo-factory exchanges it in the background for the app's id and private key. Then *Choose
repositories* installs the app on the repositories repo-factory maintains (and repo-factory's own, for its
factory issues). From then on maintenance, Projects' filing and your factory issues act as
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
`app`, `token` or `login`. A repository the installation does not reach gets a Needs-you row
("add it to repo-factory's app"), never the login; an app GitHub refuses (uninstalled, suspended, its
key replaced) makes every maintained repo's row say so, and nothing falls back to the login.
*Remove repo-factory's app* uninstalls it and deletes the key and record here; the registration is
deleted on GitHub. `brb github status|refresh|key --file|remove` does the same from the command
line.

repo-factory's GitHub App is not the "repo-factory" OAuth App that *repo-factory's GitHub sign-in* goes through (the
device flow; Settings › Publishing › GitHub › repo-factory's GitHub sign-in names it and its client id).
The sign-in is enough to publish and to read your repositories; the App is the optional bot.
After *Create*, the page waits five minutes for GitHub to send the browser back (or until
*Cancel*), then puts Create back and says the try did not finish; a callback GitHub still sends
within ten minutes is kept, so an app made late is not lost. An app that exists on GitHub but
not here (a closed tab, a lost record) is **linked, never made twice**: *Link an app you made*
(or `brb github link --app-id N --file app.pem`) takes its App ID and a private key generated
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
the app or a bot account, repo-factory's comments and pull requests are visibly not yours; `claim:repo-factory`
stays a label either way (GitHub offers no app's bot as an assignee). Publishing new repos keeps
the owner's login.

## In the app

- **Needs you** lists issues labelled `needs-you` (Open, Try again, which removes the
  label, Close as not planned), repo-factory's pull requests waiting for review, Dependabot updates
  waiting, and issues still untriaged after a day. They come from the last poll, so Needs you never
  waits on GitHub.
- **A published build's page** has a Maintenance box. It shows the policy, the latest release,
  CI on `main`, open issues by label and the pull requests waiting, each count linking to the
  GitHub search it came from. It also shows this month's maintenance spend. It reads GitHub
  live, keeping each read for `maintain.cache_s` seconds (300).
- **Settings › Publishing** (its Maintenance group) has the polling and fix settings (fixes at once, a cap per fix,
  the fix sessions' network, the lesson label), the identity repo-factory uses on GitHub, a table (and **Build › Repos**) of
  every maintained repo with its policy and last poll, and this month's spend. The identity box
  says what GitHub reports for the login or token in use (see above). **Settings › Spend** shows
  maintenance as its own line.
- **Needs you** also shows a row when the drift doctor finds `AGENTS.md` drift or a workflow on a
  self-hosted runner (with its age and **Re-check**), and one when a product's fixes are held because `AGENTS.md` is missing or
  does not run (with **Open the backfill PR**); **Settings › System › Your grades** lists the lessons from
  user reports.
- **Builds** lists each fix as a row of kind *fix* ("Fix #14: …", from an issue labelled go):
  running, *PR open* or *Tried*. Its page shows the stages and their cost like a build's, and
  links the issue and the pull request. The header reads *Revising* while one runs.
- **Open in Claude Code** on a published build knows its open issues, and can propose **Open issue**
  (`cut_ticket`): one click opens the issue on GitHub with the labels shown (`go` only when the
  repo's policy lets repo-factory fix issues). That is the only way a Claude Code session changes a published product, and only once you Confirm.

## Security

Product CI runs on GitHub-hosted runners only; a self-hosted runner is never attached to a
product repo, and the doctor flags any workflow that names one (in Needs you). Workflows read with
`contents: read` and check out without persisting credentials. The templates carry nothing
private (`tests/test_no_secrets.py` scans them). See MAINTAIN.md §6 for the full model.

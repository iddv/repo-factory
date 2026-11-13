# Projects: a repo that keeps evolving from the idea it came from

A **project** is what a repository keeps becoming. It joins what is otherwise scattered: the idea
that started it, the repo repo-factory published (or the repo you adopted), a **charter**, a **project
topic** in the watcher, the **change ideas** repo-factory proposes, the repo's maintenance, its numbers
and its history. Build › Projects lists them; each has a page.

## When a project is born

- **At publish.** When a build is published (privately; `projects.create.on: public` waits for
  Publish), its idea becomes a project. Its charter was drafted earlier, in the background, when
  the gate said ready, so publishing is still one click.
- **At confirm.** When you confirm an adopted repo's profile, it becomes a project; its charter
  was drafted during onboarding, beside the test-command proposal.
- **By hand or for older builds:** `repo-factory projects backfill` (below).

## The charter

One page in plain words: the **pitch** (one sentence), the **spirit** (what must never be lost),
the **users**, the **promises** it must keep true (read as the idea's claims are read), its
**scope**, its **non-goals** (each with a reason), its **open directions** (ways to grow without
changing what it is), its **constraints**, and the **scope tests** a reviewer asks of any change.

It is drafted by one model call (`projects.charter.model`, Sonnet 5.5 by default) from the idea,
its pitch and claims, the spec, and the repo's own documents, and checked in code. It lives in
repo-factory, not in the repo. Every change is a new version: **Edit charter** (a version by you),
**Redraft charter** (a new draft from the repo as it is now, in the background), **Use this
version** (an older one, again, as a new version). Accepting or editing how a claim is read
(the build page's Claims) updates the promise that carries it.

## What can we improve?

Once a week per project (`projects.evolve.every_days`), and sooner after you edit the charter,
repo-factory asks one question: *given this repo, its charter and these sources, what can we improve?*

The sources: the repo's own open items (security findings, readiness ambers, known open tests,
the changelog's Unreleased lines, xfail marks), **drift checks** (on a repo whose releases repo-factory
does not cut, the version against the tags and the changelog; documents naming files that do not
exist, the Python floor and the CI matrix against end-of-life dates, pinned test tools against
their latest release except the pin repo-factory's own templates wrote, the charter's promises still in
the README), the repo's open issues and pull requests, the project topic's
notes, and a map of the code. The checks and the map are
code; when none of it changed since the last check, **no model is called**
(`projects.evolve.skip_when_quiet`). Otherwise one call (`projects.evolve.model`, Sonnet 5.5)
returns at most `projects.evolve.max_ideas` proposals (none at all, and no call, while
`projects.asks_open_max` (3) minor asks of the project wait for your answer; Check for ideas now
still runs), each citing its evidence, each of a
**kind** (customer ask, feature, maintenance) and a **class**. The drift checks' findings are
not in the call: they are maintenance repo-factory files itself (the project's health and Timeline),
so the model never offers a chore as an idea, and a check keeps at most
`projects.evolve.max_maintenance` maintenance proposals (default 1; 0: none):

| Class | Means |
|---|---|
| **patch** | no new behaviour: a bug, a security finding, drift, docs, a dependency or platform change that keeps a promise true |
| **minor** | new behaviour inside the charter's scope and spirit |
| **scope** | changes what the project is: a promise, a non-goal, a user kind, a constraint |

Code backs the class: a feature is never a patch, and a proposal that matches a non-goal is a
scope change. A proposal repo-factory made before gains an agreeing run instead of a new row; one you
declined for good is counted and dropped; one that matches an open issue or pull request is
listed as already tracked. **Check for ideas now** on the page runs a check at once.

## Who decides

`projects.autonomy` is a preset, and each class can be set on its own, factory-wide (Settings ›
Projects) or per project (the page's Settings):

| Preset | patch | minor | scope |
|---|---|---|---|
| **quiet** (default) | filed on its own | asks you once, after 2 agreeing runs | always asks, with the charter diff |
| hands-on | asks | asks | asks |
| off | listed only | listed only | listed only |

A scope change is never filed without you. The gate's numbers are settings too:
`projects.gate.min_confidence` (0.5), `projects.gate.min_agree_runs` (patch 1, minor 1),
`projects.gate.require_evidence_kinds` (none). On a repo you do not own, repo-factory asks instead of
filing. Settings › Publishing › Projects previews how many current ideas would flow with a setting changed.

An accepted change goes through what exists, never a new pipeline:

- **a product, or an adopted repo you own with repo-factory's labels:** an issue on the repo, with its
  kind label and a priority, and `go` only when the repo's own policy (`.github/repo-factory.yml` or
  the adopted profile) lets repo-factory fix that kind (a feature needs your own `go` by default). The
  maintenance fix then reproduces it as a failing test and opens a pull request. The merge is
  yours.
- **an adopted repo you do not own:** a request kept in repo-factory; no issue on someone else's repo
  unless `projects.changes.file_issues_outside` is on.

**Build it** can be undone for a few seconds (the issue is closed unworked). Where the idea is
after that (repo-factory fixing it, its pull request waiting for you, a fix that could not reproduce
it, closed) is read from its fix's run and the last poll each time the page shows it; nothing
keeps a copy that could go stale. **Not now** puts an idea to sleep for
`projects.decline_days`; **Never** declines it for good, with your reason, and later checks
read it.

A fix of a project's repo is held for you when it removes one of the charter's promises from the
README, or adds a dependency where the charter says standard library only.

## The project topic

Each project gets a topic in the watcher (`projects.topic.create: auto`), made through the
watcher's API with a description written from the charter, the release feeds of the platforms
the charter names and of the repo itself, and the scout's close matches approved
(`projects.topic.approve: core`); wildcards stay proposed. It is yours like any topic: pause it,
edit it, add sources. The idea-card writer skips project topics (the watcher's
`ideas.project_topics: skip`).

## Health

One word with its reason in the tooltip: **needs you** (an ask, a pull request to review, a
row of the repo's maintenance waits), **attention** (CI red, a dependency update waiting over
`projects.health.stale_pr_days`, a drift finding, the last poll failed), **maintenance paused**
("Maintenance paused since 29 Oct 10:04, by you"), **paused**, or **ok**.

**Pause project** stops its ideas (no check for ideas, no charter draft, nothing filed);
maintenance of its repo goes on. **Pause maintenance** stops that, in repo-factory, at once
([maintain.md](maintain.md), "Pause maintenance on one project").

## Commands

```sh
repo-factory projects list                       # every project, by health
repo-factory projects show ID                    # its charter version, repos, timeline
repo-factory projects backfill [--dry-run]       # a project for every published build and confirmed
                                        # adopted repo that has none; drafts their charters
                                        # (one call each) and makes their topics
repo-factory projects charter ID                 # draft (or redraft) the charter: one model call
repo-factory projects charter ID --file F        # your text as a new version; --restore N; --print
repo-factory projects evolve ID [--force]        # one check for ideas now (the loop runs it weekly)
repo-factory projects maintenance ID paused|on   # pause its repo's maintenance in repo-factory, or resume it
```

Each takes `--home` (the loop home, default `~/.repo-factory`). Nothing in the page or `repo-factory mcp`
waits on a model: drafts and checks run in a process of their own and the page shows their
state.

## Spend

Uncapped by default. A charter costs about $0.13; a check about $0.15–0.20 with Sonnet (Opus
found the best ideas in the experiment, at about 2.5×); quiet projects cost nothing. Settings ›
Spend shows each project's spend; `projects.budget.monthly_usd` is an empty box until you set
it.

## Where it is kept

The loop's ledger (`loop.db`): `projects`, `charter_versions` (only grows), `changes`, the
history rows keyed by the project's id, and `spend.project`. The clones the drift checks read are
under `<loop home>/projects/clones/`. The project topic is a watcher topic (`kind: project`).

# The build loop

The loop takes typed tasks through a brief, a build, the gate and, on your press, publishing.
`repo-factory ui` runs it beside the page; `repo-factory loop run` runs it alone (the page is
then `repo-factory ui --no-loop`). Each pass (a "tick", every `tick_s` seconds) does, in order:

1. **Watch** the running builds. A finished build goes through the gate: **ready** or **held**.
   A build whose process died without finishing is resumed from its saved state (up to
   `build.max_resumes` times); a failed build is retried once (`build.retries`), then held.
2. **Pre-push checks** of the ready and held builds, in a process of their own: a secrets scan
   and an honesty check of the product's public text. A ready build then waits for your
   Publish, unless `publish.when: ready`.
3. Unless paused, or today's spend is over `daily_usd`: write the **brief** of new tasks, and
   **start** queued builds up to `concurrency`.

## The states a task goes through

| State | Meaning |
|---|---|
| `new` | Typed in; its brief is written on the next tick |
| `rejected` | The brief found no buildable product in it (the reason is kept) |
| `queued` | Has a brief and a task folder; waits for a build slot |
| `building` | A build is running |
| `paused` | You stopped its round; Resume continues it |
| `held` | Built, but the gate did not pass; each reason is in plain words |
| `ready` | The gate passed; waits at "Ready: publish?" for your press |
| `published` | A private repository with a first release |
| `public` | You made it public |
| `closed` | You said no |
| `error`, `skipped` | Something outside the build failed, or it was set aside |

## The gate

The gate reads facts from the build's records, never the builder's opinion: something was
delivered; no open critical or high security finding; the README's test commands pass as
written; safe defaults (it listens on localhost unless configured; the quick start works); every
core flow of the scope works end to end; the documents exist. What it could not measure is
listed, but never on its own holds a build. A build stopped by the spend cap is held, and its
first reason says so.

## Settings (`~/.repo-factory/loop.yaml`)

The page writes the common ones (Settings); this file holds every one. A key it does not know
is kept and ignored, and the loop says so once.

| Key | Default | What it does |
|---|---|---|
| `daily_usd` | `50` | No new brief or build once today's spend reaches it (a running build is not stopped by it); `null`: no daily cap |
| `build.max_usd` | `25` | A build's cap: it stops before a stage it cannot afford; `null`: none |
| `build.policy` | `policies/v5` | The flow; `policies/lean` is the lean flow (unproven) |
| `build.models` | `{}` | A model per stage over the policy's (Settings › Building) |
| `build.stage_caps` | `{}` | A cap per stage over the policy's |
| `concurrency` | `1` | Builds at once |
| `tasks_dir` | `<home>/tasks` | Where task folders go |
| `publish.when` | `ask` | `ask`: a ready build waits for your press; `ready`: it is published privately on its own |
| `publish.approval` | `required` | Making a repository public is your press; `auto` (set here by hand only) makes every published build public without asking |
| `publish.license` | `MIT` | The licence a published product carries (`MIT`, `Apache-2.0`, `none`) |
| `publish.owner` | the `gh` login | The account or organisation it publishes under |
| `maintain.enabled` | `false` | Maintenance of published repositories: in the code, not offered yet |
| `projects.enabled` | `false` | Projects: in the code, not offered yet |

## Commands

| Command | What it does |
|---|---|
| `repo-factory loop add "TEXT"`, `--file spec.md` | Add a task |
| `repo-factory loop list`, `show ID`, `status` | The tasks; one task as JSON; the loop at a glance |
| `repo-factory loop run [--once] [--until-idle]` | The loop on its own |
| `repo-factory loop pause`, `resume`, `stop` | No new briefs or builds; undo; stop the loop and its builds |
| `repo-factory loop publish ID` | Publish a ready build (a private repository) |
| `repo-factory loop approve ID` | Make a published repository public |
| `repo-factory loop revise ID --text "NOTE"` | Ask for changes: one more round with your note (`--file` reads it from a file) |
| `repo-factory loop retry ID` | Build a held task again (or resume it) |
| `repo-factory loop stop-round ID`, `resume-build ID` | Stop a build's round; continue it |
| `repo-factory loop close ID --reason "WHY"` | Say no: the task leaves the list |
| `repo-factory loop publish-anyway ID --reason "WHY"` | Publish a held build over its holds that may be overridden, as a private repository, with the override disclosed |

`repo-factory loop --help` lists every command. The ids accept a unique prefix.

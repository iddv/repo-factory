# Your first build

What happens from the moment you type a task to a private repository, and what to look at on
the way. Read the [README](../README.md) first: repo-factory is an experiment, and it spends
real money or Claude plan usage.

## Before you start

```
repo-factory doctor
```

prints one line for each thing it needs: Claude Code signed in, bubblewrap able to make a
sandbox, git, and the GitHub CLI logged in (only publishing needs `gh`). Each line says "ok",
or one sentence with the command that fixes it. For builds, a long-lived Claude login token is
safer than Claude Code's own sign-in (`claude setup-token`): with the sign-in, a copy of its
credentials file is inside each session's sandbox.

## Start the page

```
repo-factory ui
```

starts the page on `127.0.0.1:8790` (the next free port if that one is busy) and the build loop
in the same process, and prints a one-time link that signs your browser in. Keep it running
while builds run; stop it with Ctrl-C (a running build keeps going and is picked up again the
next time you start it). From another machine, use `ssh -L 8790:localhost:8790 <host>`.
The link works once, within 10 minutes; for another browser, or when it expired,
`repo-factory link` prints a new one.

Everything it keeps is in one folder, `~/.repo-factory` (`REPO_FACTORY_HOME` or `--home` moves
it): the settings (`loop.yaml`), the ledger (`loop.db`), the sign-in tokens, and the tasks
(`tasks/<name>/`, each with the delivered code in `out/` and the build's records in `.den/`).

## Type a task

Press **New task** and say what you want, in a sentence or two: who it is for and what they
should be able to do. For example: "a command-line tool that turns a folder of Markdown notes
into one searchable HTML page". You can also pick a file; its text becomes the task. The same
from the command line: `repo-factory loop add "..."` or `repo-factory loop add --file spec.md`.

## Watch the stages

The build's page shows six stages, each with its time, its cost and its log:

| Stage | What happens |
|---|---|
| **Brief** | One call with no tools turns your sentence into a brief: who it is for, what v1 does |
| **Plan** | The product scope (`SCOPE.md`: users, core flows, a definition of done) and the claims a tester will check |
| **Build** | One long Claude Code session, in the sandbox, writes the code, its tests and its README |
| **Check** | Separate sessions that never saw the build write an exam, probe it for security problems and follow the README as a new user would |
| **Fix** | What the checks found is repaired and checked again; a repair that makes things worse is not kept |
| **Publish** | The gate reads the facts; a ready build waits for your press |

**Stop** stops the build at once; **Resume** continues it from where it stopped. On our record
a build takes about an hour and costs about $10; the range was 32 minutes to almost six hours.

## Ready: publish?

When the gate passes, the page says **Ready: publish?** with the checks it passed. Nothing is
on GitHub yet. A build that did not pass is **held**, with each reason in plain words; **Ask for
changes** sends it another round with your note, and **Retry** builds it again.

**Publish** runs the pre-push checks (a secrets scan and an honesty check of its public text),
then creates a **private** repository under your GitHub account with a first release and the
report of what was checked. **Make public** is a separate press, and only you make it.

## Spend

By default a build stops at **$25** and no new build starts once today's spend reaches **$50**.
Both are in **Settings › Spend**; either can be set to none. A build the cap stopped is held,
and its first reason says so. On a Claude plan, the amounts are what Claude Code reports as the
usage's list price, and your plan's own limits apply.

## When something goes wrong

- The build's page links each stage's log. The whole run is in `tasks/<name>/.den/build.log`.
- `repo-factory status ~/.repo-factory/tasks/<name>` prints where a build stands.
- A build that ends "ok" can still be poor: read the code and the report before you make it
  public or run it anywhere that matters.

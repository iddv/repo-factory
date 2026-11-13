# The page: how it is built, served and tested

The page is one Svelte 5 app in TypeScript, built by Vite, in `repo_factory/ui/web/`. It has a
side nav (Builds, Settings), hash routes (`#/builds`, `#/builds/<key>`, `#/builds/new`,
`#/settings/<page>`) and no tabs inside tabs. Its conventions are in
`repo_factory/ui/web/CONVENTIONS.md`.

## Served by

`repo-factory ui` serves it with the Python standard library (`repo_factory/ui/server.py` around
`api.handle`) on 127.0.0.1 only. The server refuses a `Host` that is not loopback; every write
needs the sign-in token (the one-time link it prints sets it in the browser) and a matching
`Origin`. Reads need no token, so any local process can read builds and logs.

The page reads its own server's JSON API (relative `api/...`): `api/state` (every build, as
rows), `api/run`, `api/build/runs`, `api/events`, `api/stream` (a change feed), the settings
(`api/loop/rules`, `api/spend/caps`, `api/models`, `api/claude/login`, `api/github/user`), and
writes through `api/new` (a typed task) and `api/act` (the presses: publish, make public, stop,
ask for changes, answer). `repo_factory/ui/types.py` declares each answer's shape;
`scripts/gen_ts.py` writes `src/api/types.gen.ts` from it.

## Built by

`scripts/build_ui.py` installs the page's packages (`npm ci --ignore-scripts`), builds it into
`repo_factory/ui/static/app/` and checks it against its size budget. The wheel carries that
build, so a user never needs Node. A checkout with no build serves a page that says how to make
one.

```
cd repo_factory/ui/web
npm ci --ignore-scripts
npm run dev          # http://127.0.0.1:5173/, proxying the API to a running repo-factory ui
npm run check        # types and accessibility, warnings as errors
npm test             # the unit tests (vitest)
```

## Tested by

- `npm test`: the shell, the router, the builds list and a build's stages, New task, every
  Settings page.
- `tests/test_browser_smoke.py` (CI's `browser` job): the real server with a fake harness, in
  Chromium at 1400 × 900 and 390 × 844, light and dark: Builds empty, New task, a typed task
  saying Brief, a build's six stages and a log, each Settings page; no page error and no failed
  request.
- `tests/golden/` (CI's `test` job): the whole cycle through the command line with a recorded
  `claude` and a local `gh`.

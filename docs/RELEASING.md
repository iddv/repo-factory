# Releasing

A release is a version tag on `main`. The `release` workflow then builds the wheel (with the
page) and its `SHA256SUMS` with a read-only token, runs the install test against that wheel,
and waits; the job that creates the GitHub release runs in the `release` environment and
starts only when a maintainer approves it. Nothing is uploaded to a package index.

## Once, before the first release

1. Settings › Rules › Rulesets: a ruleset on `main` with a pull request required and the checks `lint`, `gitleaks`, `test`, `frontend`, `browser`, `install`.
2. Settings › Environments: an environment named `release` with yourself as required reviewer.
3. (Optional) In that environment, "Deployment branches and tags" › Selected › add the tag rule `v*`.
4. (Optional) Settings › Rules › Rulesets › New tag ruleset for `v*`: restrict creation, updates and deletions to maintainers.

## Each release (the first one is v0.1.0)

1. In a pull request, move what `## Unreleased` in `CHANGELOG.md` says under a new `## X.Y.Z` heading, and merge it on green.
2. `git switch main && git pull --ff-only` and check that `git log -1` is the commit you mean to release.
3. (Optional) Actions › ci › Run workflow on `main` with "Also run the slow tests" ticked, and wait for green.
4. `git tag -a vX.Y.Z -m vX.Y.Z` (the version exactly as in the changelog's heading).
5. `git push origin vX.Y.Z`.
6. Actions › release › the run for vX.Y.Z: wait for the `build` job to be green.
7. On that run, Review deployments › `release` › Approve and deploy.
8. Releases › vX.Y.Z: check the wheel, `SHA256SUMS` and the notes are there.
9. On a clean machine or user, run the three commands from the notes: `uv tool install …`, `repo-factory doctor`, `repo-factory ui`.

## When something fails

- `build` red, nothing released yet: `git push --delete origin vX.Y.Z`, `git tag -d vX.Y.Z`, fix it in a pull request, then tag again.
- "the tag's commit is not on main": the tag was made on another branch; delete it as above and tag `main`'s commit.
- Something wrong after the release exists: never move or reuse a published tag; fix it in a pull request and release vX.Y.(Z+1).

# Contributing

Thank you for looking. repo-factory is an experiment run by a small number of people; the
maintainers read issues and pull requests in person, about weekly. **No tool or bot replies to
issues or pull requests here**, and nothing in an issue is acted on automatically.

## What is welcome

- **Bug reports with a build's log** (the last lines of the stage that failed) and what you
  expected. Remove keys, tokens and personal paths from anything you paste: this repository is
  public.
- **A fix with a test** that fails without it.
- Security problems go to **Security › Report a vulnerability**, never to a public issue
  ([SECURITY.md](SECURITY.md)).

## Sign your commits off (the DCO)

Every commit carries a sign-off line, which certifies that you wrote the change or have the
right to submit it under this project's licence: the
[Developer Certificate of Origin](https://developercertificate.org/).

```
git commit -s -m "Say what changed and why"
```

writes `Signed-off-by: Your Name <your@address>` (the address must be the commit's author).
The `gitleaks` job checks every commit of a pull request. Forgot? `git rebase --signoff main`
and push again.

## Run the checks locally: one command

```
make check
```

runs what CI's required jobs run, in the same order. A pull request should be green on its
first run; please don't push commits just to see what CI says.

## The rules the code depends on

- **Tests never call a model or the network.** They use the fake harness and recorded
  answers under `tests/fixtures/`.
- **No secret-shaped literals**, not even fake ones: tests build tokens at run time.
- **No attribution lines** in commits, pull requests or files (no co-author trailers naming a
  tool, no "generated with" footers, no session links).
- **No private names or paths**: `scripts/ci/public_words.py` checks every tracked file,
  commit message and pull request text.
- **Nothing goes public without the user's press.** A change that could publish, or make a
  repository public, without one is a bug.
- **CI runs on GitHub's hosted runners only.** A workflow change that adds a self-hosted
  runner, `pull_request_target`, or a secret in a pull request's jobs fails
  `tests/test_tree.py`.
- Pull request text says what changed and why, in plain words.

## Licence

By contributing you agree that your contribution is licensed under the Apache License 2.0,
as the rest of the project ([LICENSE](LICENSE)).

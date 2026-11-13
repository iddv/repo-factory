## What ships with the product

Everything in this directory is published as the product, exactly as you leave it; nothing is
added to it after you. So it ships with what a maintainer and a user need:

- **Its own tests**, in the test runner you document in the README, under a directory you
  choose. Every flow in SCOPE.md's definition of done gets one end-to-end test of yours
  through the real interface. The README's test command runs them all, exactly as written,
  on a fresh copy with only the declared dependencies.
- **A README** that opens with two lines (what it is, and who it is for) and a quick start,
  then covers install, configuration (every option), running, upgrading, backup and restore,
  troubleshooting and known limitations, in plain words for its users.
- **`AGENTS.md`**, for whoever maintains it next: what it is; how to set it up, run it and
  test it (the exact commands); its architecture (the main modules and what each does); and
  its conventions. And **`CLAUDE.md`** holding the single line `@AGENTS.md`.
- **`SECURITY.md`**: how to report a security problem privately (the repository host's
  private vulnerability reporting), and the product's known limits.
- **`.github/workflows/test.yml`**, which installs it as the README says and runs its tests
  on push and pull request; a **`.gitignore`**; a **`CHANGELOG.md`** that starts at 0.1.0.

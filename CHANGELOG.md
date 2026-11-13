# Changelog

All notable changes to this project are written here. Versions follow
[semantic versioning](https://semver.org/); until 1.0 anything may change.

## Unreleased

### Added
- The repository's papers: README, SECURITY, CONTRIBUTING, CODE_OF_CONDUCT, NOTICE, issue
  forms and a pull request template.
- CI on GitHub's hosted runners: `lint` (ruff, and the repository's own rules in
  `tests/test_tree.py`) and `gitleaks` (no private words or attribution lines in commits and
  pull requests, a DCO sign-off on every commit, and a secret scan).
- `make check`, which runs what CI runs.

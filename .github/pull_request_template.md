## What changed, and why

## How it was checked
What you ran and what it showed. Say plainly what you did not check. If you ran a real build,
give its cost.

## Checklist
- [ ] `make check` passes locally
- [ ] Every commit is signed off (`git commit -s`)
- [ ] Tests make no model or network calls (fake harness and recorded answers only)
- [ ] No secret-shaped literals, private names or personal paths, even in tests
- [ ] Nothing new can publish, or make a repository public, without the user's press
- [ ] Words a person reads changed? The page's help and `docs/` say the same
- [ ] A stored value gains a new shape? Its readers are listed here, and each handles it
- [ ] `CHANGELOG.md` has a line under "Unreleased"

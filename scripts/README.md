# scripts

Who runs each script, and when. CI's commands are in `.github/workflows/ci.yml`, and `make
check` runs the same ones.

| Script | Who runs it | What it does |
|---|---|---|
| `build_ui.py` | CI (`frontend`, `browser`, `install`), the release, you once per checkout | builds the page (`repo_factory/ui/web`) into `repo_factory/ui/static/app/`, within its size budget |
| `gen_ts.py` | you, after changing `repo_factory/ui/types.py` or the API; a test runs `--check` | writes the page's generated TypeScript from the Python side |
| `ci/public_words.py` | CI (`lint` through `tests/test_tree.py`, and `gitleaks`) | no private names, home paths, account numbers, attribution lines or secret-shaped literals in files, commits or pull request text |
| `ci/dco.py` | CI (`gitleaks`) | every commit of a pull request is signed off |
| `ci/docs_only.py` | CI (`changes`) | a documentation-only change runs `lint` and `gitleaks` only |
| `ci/install_test.py` | CI (`install`), the release | a stranger's first three commands against the built wheel, in an empty home |
| `ci/release_notes.py` | the release | the release's notes: the changelog's section and the install in three commands |
| `ci/browser.sh` | you, by hand | the browser smoke with Playwright's Chromium |
| `ci/typecheck.py`, `ci/typecheck_baseline.json` | CI (`lint`), you before a push | mypy with a ratchet: each file's count of errors may only fall |
| `ci/stored_shape_lint.py`, `ci/stored_shape_baseline.txt` | you before a push | a stored value used as if it could not be missing or null, against its baseline |
| `dev/readers_of.py` | you, when a stored field gains a value or shape | lists the readers of a stored field, for the pull request |
| `dev/fake_gh.py` | tests, by hand | a stand-in `gh` for tests that must not reach GitHub |

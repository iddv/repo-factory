# `make check` runs what CI's required jobs run, in the same order, on your machine.
# Each target is one job of .github/workflows/ci.yml.

.PHONY: check lint gitleaks

check: lint gitleaks

lint:
	uv sync --locked --only-group dev
	uv run --no-sync ruff check .
	uv run --no-sync ruff format --check .
	uv run --no-sync pytest -q tests/test_tree.py

# The commits on your branch that are not on main yet, as CI checks a pull request's.
BASE ?= origin/main
gitleaks:
	python3 scripts/ci/public_words.py commits "$(BASE)..HEAD"
	python3 scripts/ci/dco.py "$(BASE)..HEAD"
	@if command -v gitleaks >/dev/null; then gitleaks git --redact --no-banner --log-opts="$(BASE)..HEAD" .; \
	else echo "gitleaks is not installed: the secret scan runs in CI only"; fi

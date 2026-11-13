# `make check` runs what CI's required jobs run, on your machine, in the same order.
# Each target is one job of .github/workflows/ci.yml; `make check-all` adds the slow tests.
#
# Needs: uv, Node 24 with npm, bubblewrap (for `test`), and for `browser` Playwright's
# Chromium (`uv run --group browser playwright install chromium`, once).

.PHONY: check check-all lint gitleaks test frontend browser install slow

check: lint gitleaks test frontend browser install

check-all: check slow

lint:
	uv lock --check
	uv sync --locked
	uv run --no-sync ruff check .
	uv run --no-sync ruff format --check .
	uv run --no-sync python scripts/ci/typecheck.py
	uv run --no-sync pytest -q -p no:randomly tests/test_tree.py

# The commits on your branch that are not on main yet, as CI checks a pull request's.
BASE ?= origin/main
gitleaks:
	python3 scripts/ci/public_words.py commits "$(BASE)..HEAD"
	python3 scripts/ci/dco.py "$(BASE)..HEAD"
	@if command -v gitleaks >/dev/null; then gitleaks git --redact --no-banner --log-opts="$(BASE)..HEAD" .; \
	else echo "gitleaks is not installed: the secret scan runs in CI only"; fi

test:
	uv sync --locked
	uv run --no-sync pytest -q -rs -m "not slow" -n 4 --dist loadfile
	REPO_FACTORY_BWRAP_REQUIRED=1 REPO_FACTORY_ALLOW_UNSANDBOXED=0 uv run --no-sync pytest -q -rs -m sandbox
	REPO_FACTORY_BWRAP_REQUIRED=1 uv run --no-sync pytest -q -rs tests/golden

frontend:
	cd repo_factory/ui/web && npm ci --ignore-scripts --no-audit --no-fund && npm run check && npm test
	python3 scripts/build_ui.py --no-install
	cd repo_factory/ui/web && npm audit --audit-level=high

browser:
	python3 scripts/build_ui.py
	uv sync --locked --group browser
	uv run --no-sync pytest -q -rs -p no:randomly tests/test_browser_smoke.py

install:
	python3 scripts/build_ui.py
	rm -rf dist && uv build --wheel
	python3 scripts/ci/install_test.py dist/*.whl

slow:
	uv run --no-sync pytest -q -rs -m slow -n 4 --dist loadfile

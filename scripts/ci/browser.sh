#!/usr/bin/env bash
# The browser smoke (tests/test_browser_smoke.py) on a CI runner, with no root: Playwright's
# headless Chromium in the runner's cache (downloaded once), and the one system library a runner
# may lack (libasound) unpacked from its Debian package beside it when it is missing.
#
#   scripts/ci/browser.sh            (from the checkout, after `uv sync --locked --group browser`
#                                     and `python3 scripts/build_ui.py`)
set -euo pipefail

CACHE="${REPO_FACTORY_BROWSER_CACHE:-$HOME/.cache/repo-factory-browser}"
export PLAYWRIGHT_BROWSERS_PATH="$CACHE/ms-playwright"
LIBS="$CACHE/libs"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH" "$LIBS"

uv run --no-sync playwright install --only-shell chromium

if ! ldconfig -p | grep -q 'libasound\.so\.2 ' && [ ! -e "$LIBS/usr/lib/x86_64-linux-gnu/libasound.so.2" ]; then
  echo "browser.sh: libasound.so.2 is missing; unpacking it into $LIBS"
  tmp="$(mktemp -d)"
  (cd "$tmp" && apt-get download libasound2t64 2>/dev/null || apt-get download libasound2)
  dpkg-deb -x "$tmp"/libasound2*.deb "$LIBS"
  rm -rf "$tmp"
fi
export LD_LIBRARY_PATH="$LIBS/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

REPO_FACTORY_BROWSER_REQUIRED=1 uv run --no-sync pytest -q -m browser tests/test_browser_smoke.py

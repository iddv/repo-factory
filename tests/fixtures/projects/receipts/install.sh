#!/bin/sh
# Install Receipts for the current user (Linux / macOS). No network access, no services.
#   ./install.sh                 installs to ~/.local/share/receipts-app, launcher in ~/.local/bin
#   PREFIX=/opt/receipts BIN_DIR=/usr/local/bin ./install.sh
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local/share/receipts-app}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ]; then
  echo "receipts install: Python 3.11 or newer is required (python3 not found or too old)." >&2
  exit 1
fi

mkdir -p "$PREFIX" "$BIN_DIR"
rm -rf "$PREFIX/receipts.new"
cp -R "$SRC/receipts" "$PREFIX/receipts.new"
find "$PREFIX/receipts.new" -name '__pycache__' -type d -prune -exec rm -rf {} +
rm -rf "$PREFIX/receipts"
mv "$PREFIX/receipts.new" "$PREFIX/receipts"

cat > "$BIN_DIR/receipts" <<EOF
#!$PY
import sys
sys.path.insert(0, "$PREFIX")
from receipts.cli import main
sys.exit(main())
EOF
chmod 755 "$BIN_DIR/receipts"

echo "Installed: $BIN_DIR/receipts ($("$BIN_DIR/receipts" --version))"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    rc="$HOME/.profile"
    case "${SHELL:-}" in */zsh) rc="$HOME/.zshrc" ;; */bash) rc="$HOME/.bashrc" ;; esac
    if ! grep -qs "receipts installer" "$rc"; then
      printf '\nexport PATH="%s:$PATH"  # added by receipts installer\n' "$BIN_DIR" >> "$rc"
    fi
    echo "Added $BIN_DIR to PATH in $rc. Open a new terminal, or run:  export PATH=\"$BIN_DIR:\$PATH\""
    ;;
esac
echo "Next: receipts init"

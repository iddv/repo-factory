#!/bin/sh
# Plotkeeper launcher: checks for Python 3.9+, installs it if missing (Debian/Ubuntu/Fedora/Alpine/macOS
# with Homebrew), then runs a Plotkeeper command. With no arguments it starts the service.
#   ./plotkeeper.sh                 start the web service
#   ./plotkeeper.sh demo            load demo data (empty install only)
#   ./plotkeeper.sh backup          write a timestamped backup
#   ./plotkeeper.sh restore FILE    replace all data from a backup
#   ./plotkeeper.sh test            run the automated test suite
#   ./plotkeeper.sh install-service install a systemd service that restarts on reboot (Linux, needs sudo)
set -e
cd "$(dirname "$0")"

have_python() {
  command -v python3 >/dev/null 2>&1 &&
    python3 -c 'import sys, sqlite3, zoneinfo; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null
}

if ! have_python; then
  echo "Python 3.9+ not found; installing it..."
  if command -v apt-get >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y python3 tzdata
  elif command -v dnf >/dev/null 2>&1; then sudo dnf install -y python3 tzdata
  elif command -v apk >/dev/null 2>&1; then sudo apk add python3 tzdata
  elif command -v brew >/dev/null 2>&1; then brew install python
  else echo "Please install Python 3.9 or newer and run this again." >&2; exit 1
  fi
fi

if [ "$1" = "install-service" ]; then
  user="$(id -un)"
  dir="$(pwd)"
  unit=/etc/systemd/system/plotkeeper.service
  echo "Writing $unit (runs as $user from $dir)"
  sudo tee "$unit" >/dev/null <<EOF
[Unit]
Description=Plotkeeper community garden register
After=network.target

[Service]
User=$user
WorkingDirectory=$dir
ExecStart=$(command -v python3) -m plotkeeper start
Restart=always
RestartSec=1
EnvironmentFile=-$dir/plotkeeper.conf

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now plotkeeper
  echo "Service started. Setup code (first run only): sudo journalctl -u plotkeeper | grep 'setup code'"
  exit 0
fi

exec python3 -m plotkeeper "${@:-start}"

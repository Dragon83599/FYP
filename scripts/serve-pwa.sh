#!/usr/bin/env bash
# Serve the PWA demo over plain HTTP and print the address to open.
#
# Thin wrapper around scripts/serve-pwa.py, which pins the Content-Type of .js to
# text/javascript. That is not cosmetic: on Windows the registry often maps .js
# to text/plain, and browsers then reject every ES module ("Failed to fetch
# dynamically imported module"), leaving the page stuck on "模型加载中".
#
# Why plain HTTP is enough: the page does not register a Service Worker, so it
# needs neither HTTPS nor an install prompt. (Service Workers only run on HTTPS
# or localhost -- see docs/tasks/pwa-demo-hosting-options.md. If you ever want
# "add to home screen" on a phone, use form B or C there instead.)
#
# Usage:
#   scripts/serve-pwa.sh              # port 8080
#   scripts/serve-pwa.sh 9000         # another port

set -euo pipefail

PORT="${1:-8080}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to serve static files" >&2
  exit 1
fi

if [[ ! -f "$ROOT/pwa/index.html" ]]; then
  echo "not found: $ROOT/pwa/index.html" >&2
  exit 1
fi

if grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
  cat <<'EOF'
WSL detected. The LAN address printed below is WSL's NAT address and a phone
cannot reach it. Either forward the port on Windows, or copy pwa/ to Windows and
run the same script there:

  A. admin PowerShell, then open http://<Windows-LAN-IP>:8080/ on the phone:
       netsh interface portproxy add v4tov4 listenport=8080 listenaddress=0.0.0.0 \
         connectport=8080 connectaddress=<WSL-IP from below>

  B. copy pwa/ and scripts/serve-pwa.py to Windows, then in PowerShell:
       python serve-pwa.py 8080 --dir .\pwa

EOF
fi

exec python3 "$ROOT/scripts/serve-pwa.py" "$PORT"

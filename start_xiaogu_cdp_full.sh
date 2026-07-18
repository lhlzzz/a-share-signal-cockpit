#!/usr/bin/env bash
set -euo pipefail

# Unified CloakBrowser CDP startup for xiaogu - opens ALL tabs
# Usage: bash start_xiaogu_cdp_full.sh [port]

port="${1:-${XIAOGU_CDP_PORT:-9333}}"
profile="${XIAOGU_CDP_PROFILE:-/root/.claude/browser-profiles/xiaogu/cdp-debug}"
mkdir -p "$profile"

# Find CloakBrowser binary
browser="${CLOAKBROWSER_CHROME:-}"
if [[ -z "$browser" ]]; then
  browser="/root/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
fi
if [[ ! -x "$browser" ]]; then
  echo "ERROR: CloakBrowser not found at $browser" >&2
  exit 1
fi

# Kill existing Chrome on this port
pkill -9 -f "remote-debugging-port=$port" 2>/dev/null || true
sleep 1

echo "Starting CloakBrowser CDP on port $port with profile $profile"

# Start Chrome in background
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  "$browser" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$port" \
  --remote-allow-origins='*' \
  --user-data-dir="$profile" \
  --no-first-run \
  --no-default-browser-check \
  --no-sandbox \
  --disable-breakpad \
  --disable-crash-reporter \
  --disable-dev-shm-usage \
  --disable-gpu \
  --no-proxy-server \
  --proxy-server='direct://' \
  --proxy-bypass-list='*' \
  about:blank &

CHROME_PID=$!
echo "Chrome PID: $CHROME_PID"

# Wait for CDP to be ready
echo "Waiting for CDP..."
for i in $(seq 1 30); do
  if curl -s "http://127.0.0.1:$port/json" >/dev/null 2>&1; then
    echo "CDP ready on port $port"
    break
  fi
  sleep 1
done

# Verify CDP is working
if ! curl -s "http://127.0.0.1:$port/json" >/dev/null 2>&1; then
  echo "ERROR: CDP not ready after 30 seconds" >&2
  exit 1
fi

echo "CloakBrowser CDP started successfully"
echo "CDP URL: http://127.0.0.1:$port"
echo "Profile: $profile"

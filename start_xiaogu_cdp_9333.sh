#!/usr/bin/env bash
set -euo pipefail

port="${XIAOGU_CDP_PORT:-9333}"
profile="${XIAOGU_CDP_PROFILE:-/root/.claude/browser-profiles/xiaogu/cdp-debug}"
mkdir -p "$profile"

browser="${CLOAKBROWSER_CHROME:-}"
if [[ -z "$browser" ]] && command -v cloakbrowser >/dev/null 2>&1; then
  browser="$(cloakbrowser info 2>/dev/null | sed -n 's/^Binary:[[:space:]]*//p' | head -n1)"
fi
if [[ -z "$browser" ]]; then
  for candidate in google-chrome-stable google-chrome chromium chromium-browser; do
    if command -v "$candidate" >/dev/null 2>&1; then
      browser="$(command -v "$candidate")"
      break
    fi
  done
fi
if [[ -z "$browser" || ! -x "$browser" ]]; then
  echo "No Chrome/Chromium binary found. Install CloakBrowser or set CLOAKBROWSER_CHROME." >&2
  exit 1
fi

exec env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy "$browser" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$port" \
  --remote-allow-origins='*' \
  --user-data-dir="$profile" \
  --no-first-run \
  --no-default-browser-check \
  --no-sandbox \
  --disable-gpu \
  --disable-vulkan \
  --use-gl=swiftshader \
  --disable-software-rasterizer \
  --disable-breakpad \
  --disable-crash-reporter \
  --disable-features=UseChromeOSDirectVideoDecoder \
  --disable-dev-shm-usage \
  --no-proxy-server \
  --proxy-server='direct://' \
  --proxy-bypass-list='*' \
  about:blank

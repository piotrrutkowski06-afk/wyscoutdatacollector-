#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# The Playwright wheel's bundled Node binary may not link on NixOS.
if [[ -z "${PLAYWRIGHT_NODEJS_PATH:-}" && -n "${REPLIT_CONTAINER:-}" ]] && command -v node >/dev/null; then
  export PLAYWRIGHT_NODEJS_PATH="$(command -v node)"
fi
# Managed previews restart on request; no recursive watcher over the monorepo.
exec python3 -m streamlit run app.py --server.address 0.0.0.0 --server.port "${PORT:-8501}" --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false

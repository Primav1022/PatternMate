#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/CHI27
mkdir -p "$ROOT/.run" "$ROOT/.cache/tryon-results"

if [[ -f "$ROOT/.run/supervisord.pid" ]] && kill -0 "$(cat "$ROOT/.run/supervisord.pid")" 2>/dev/null; then
  /usr/bin/supervisorctl -c "$ROOT/deploy/autodl/supervisord.conf" status
  exit 0
fi

/usr/bin/supervisord -c "$ROOT/deploy/autodl/supervisord.conf"
sleep 3
/usr/bin/supervisorctl -c "$ROOT/deploy/autodl/supervisord.conf" status

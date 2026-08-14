#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/CHI27
/usr/bin/supervisorctl -c "$ROOT/deploy/autodl/supervisord.conf" status
curl -fsS http://127.0.0.1:8788/health
printf '\n'
curl -fsS http://127.0.0.1:8790/research/health
printf '\n'
curl -fsSI http://127.0.0.1:6006/ | head -n 1

#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
set -a; source "$ROOT/.env"; set +a
export PATH="${HOME}/.nvm/versions/node/v22.22.0/bin:$PATH"
export SSHPASS="${REMOTE_PASSWORD:?}"
RSH="sshpass -e ssh -p ${REMOTE_PORT} -o PreferredAuthentications=password -o PubkeyAuthentication=no -o StrictHostKeyChecking=yes"

cd "$ROOT"
VITE_BASE=/ VITE_TEXT_BASE_URL=/geometry VITE_AI_BASE_URL=/ai \
VITE_GEOMETRY_BASE_URL=/geometry VITE_TRYON_BASE_URL=/tryon VITE_API_BASE_URL=/geometry \
npx vite build

rsync -az --delete -e "$RSH" "$ROOT/dist/assets/" "$REMOTE_USER@$REMOTE_HOST:/var/www/chi27/assets/"
rsync -az -e "$RSH" "$ROOT/dist/index.html" "$ROOT/dist/gpu.json" "$REMOTE_USER@$REMOTE_HOST:/var/www/chi27/"
rsync -az -e "$RSH" "$ROOT/apps/geometry-service/app.py" "$REMOTE_USER@$REMOTE_HOST:/root/autodl-tmp/CHI27/apps/geometry-service/app.py"
echo "deployed $(grep -o 'index-[^\"]*' "$ROOT/dist/index.html" | head -1)"

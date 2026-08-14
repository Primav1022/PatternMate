#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/CHI27
LABEL=${1:?checkpoint label required}
TARGET="$ROOT/.run/checkpoints/$LABEL"
mkdir -p "$TARGET"
tar -czf "$TARGET/source.tar.gz"   -C "$ROOT"   apps/geometry-service apps/web/src apps/web/package.json apps/web/package-lock.json   deploy/autodl packages/catalogs/src/pattern-options.v1.json
sha256sum "$TARGET/source.tar.gz" > "$TARGET/SHA256SUMS"

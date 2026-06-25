#!/usr/bin/env bash
# Build a distributable .ankiaddon (a zip with manifest.json at its root).
# The addon source lives at the repo root, so we zip the repo contents and
# exclude repo-only / runtime files that must never ship.
# Usage: ./build_ankiaddon.sh
set -euo pipefail
cd "$(dirname "$0")"

OUT="dist"
VER="$(grep -oE '__version__ = "[^"]+"' __init__.py | sed -E 's/.*"([^"]+)".*/\1/')"

mkdir -p "$OUT"
FILE="$OUT/freecard-${VER}.ankiaddon"
rm -f "$FILE"

zip -r -q "$FILE" . \
    -x ".git/*" \
    -x "dist/*" \
    -x "README.md" \
    -x ".gitignore" \
    -x "build_ankiaddon.sh" \
    -x "meta.json" \
    -x "ai.log" -x "ai.log.*" -x "*.log" \
    -x "__pycache__/*" -x "*.pyc" \
    -x ".DS_Store" \
    -x "*.ankiaddon"

echo "Built $FILE"
echo "Install: double-click the file in Anki, or Tools → Add-ons → Install from file."

#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
DIST_DIR="$ROOT_DIR/dist"
PUBLISH_ASSETS_DIR="$ROOT_DIR/assets"
PUBLISH_SUBMIT_DIR="$ROOT_DIR/submit"

if [ ! -f "$DIST_DIR/index.html" ]; then
  echo "Missing built frontend entry at $DIST_DIR/index.html" >&2
  exit 1
fi

mkdir -p "$PUBLISH_ASSETS_DIR" "$PUBLISH_SUBMIT_DIR"

cp -f "$DIST_DIR/index.html" "$ROOT_DIR/index.html"
cp -rf "$DIST_DIR/assets/." "$PUBLISH_ASSETS_DIR/"
cp -f "$DIST_DIR/submit/index.html" "$PUBLISH_SUBMIT_DIR/index.html"

echo "Published SymGov static frontend from $DIST_DIR to $ROOT_DIR"

#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
DIST_DIR="$ROOT_DIR/dist"
COMPAT_PUBLIC_ROOT="/data/.openclaw/workspace/symgov"

if [ ! -f "$DIST_DIR/index.html" ]; then
  echo "Missing built frontend entry at $DIST_DIR/index.html" >&2
  exit 1
fi

publish_to() {
  target_root=$1
  publish_assets_dir="$target_root/assets"
  publish_submit_dir="$target_root/submit"

  mkdir -p "$publish_assets_dir" "$publish_submit_dir"

  cp -f "$DIST_DIR/index.html" "$target_root/index.html"
  cp -rf "$DIST_DIR/assets/." "$publish_assets_dir/"
  cp -f "$DIST_DIR/submit/index.html" "$publish_submit_dir/index.html"

  echo "Published SymGov static frontend from $DIST_DIR to $target_root"
}

publish_to "$ROOT_DIR"

if [ -d "$COMPAT_PUBLIC_ROOT" ] && [ "$(readlink -f "$COMPAT_PUBLIC_ROOT")" != "$(readlink -f "$ROOT_DIR")" ]; then
  publish_to "$COMPAT_PUBLIC_ROOT"
fi

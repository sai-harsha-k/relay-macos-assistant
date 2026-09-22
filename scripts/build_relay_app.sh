#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

./scripts/build_icon.sh
if [[ -d "$repo_root/outputs/Relay.app" ]]; then
  rm -rf "$repo_root/outputs/Relay.app"
fi
if [[ -d "$repo_root/build/py2app" ]]; then
  rm -rf "$repo_root/build/py2app"
fi
mkdir -p "$repo_root/outputs"
build_log="$repo_root/work/py2app-build.log"
if ! UV_CACHE_DIR="${UV_CACHE_DIR:-$repo_root/work/uv-cache}" \
  uv run python setup_app.py py2app --dist-dir "$repo_root/outputs" \
  --bdist-base "$repo_root/build/py2app" >"$build_log" 2>&1; then
  tail -200 "$build_log"
  exit 1
fi
xattr -cr "$repo_root/outputs/Relay.app"
plutil -lint "$repo_root/outputs/Relay.app/Contents/Info.plist"
RELAY_BUNDLE_SMOKE=1 "$repo_root/outputs/Relay.app/Contents/MacOS/Relay"
if codesign --force --deep --sign - "$repo_root/outputs/Relay.app" && \
  codesign --verify --deep --strict "$repo_root/outputs/Relay.app"; then
  echo "Applied and verified ad-hoc development signature"
else
  echo "Warning: app built, but FileProvider metadata prevented ad-hoc signing." >&2
  echo "Move the checkout outside a synced folder or run xattr -cr before signing." >&2
fi
echo "Built $repo_root/outputs/Relay.app"

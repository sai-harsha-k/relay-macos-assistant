#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
assets_dir="$repo_root/assets"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/relay-icon.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT

logo_source="$assets_dir/relay-logo.svg"
if [[ ! -f "$logo_source" ]]; then
  logo_source="$assets_dir/relay-icon.svg"
fi
master="$work_dir/relay-1024.png"
UV_CACHE_DIR="${UV_CACHE_DIR:-$repo_root/work/uv-cache}" \
  uv run python "$repo_root/scripts/render_svg.py" "$logo_source" "$master" 1024
iconset="$work_dir/Relay.iconset"
mkdir -p "$iconset"

render() {
  local pixels="$1"
  local name="$2"
  sips -z "$pixels" "$pixels" "$master" --out "$iconset/$name" >/dev/null
}

render 16 icon_16x16.png
render 32 icon_16x16@2x.png
render 32 icon_32x32.png
render 64 icon_32x32@2x.png
render 128 icon_128x128.png
render 256 icon_128x128@2x.png
render 256 icon_256x256.png
render 512 icon_256x256@2x.png
render 512 icon_512x512.png
render 1024 icon_512x512@2x.png
UV_CACHE_DIR="${UV_CACHE_DIR:-$repo_root/work/uv-cache}" \
  uv run python "$repo_root/scripts/build_icns.py" "$iconset" "$assets_dir/relay-icon.icns"
UV_CACHE_DIR="${UV_CACHE_DIR:-$repo_root/work/uv-cache}" \
  uv run python "$repo_root/scripts/render_svg.py" \
  "$logo_source" "$assets_dir/relay-menubar.png" 18
echo "Created $assets_dir/relay-icon.icns and relay-menubar.png"

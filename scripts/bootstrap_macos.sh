#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This bootstrap script supports macOS only."
  exit 1
fi

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"

ask() {
  local prompt="$1"
  if [[ ! -t 0 ]]; then
    return 1
  fi
  read -r -p "$prompt [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]]
}

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install it from https://brew.sh and rerun this script."
  exit 1
fi

formulae=(python@3.12 uv cmake ffmpeg portaudio whisper-cpp ollama)
missing=()
for formula in "${formulae[@]}"; do
  if ! brew list --versions "$formula" >/dev/null 2>&1; then
    missing+=("$formula")
  fi
done
if ((${#missing[@]})); then
  echo "Missing Homebrew packages: ${missing[*]}"
  if ask "Install these system dependencies now?"; then
    brew install "${missing[@]}"
  else
    echo "Skipped. Install them before running the application."
  fi
else
  echo "Homebrew dependencies are already installed."
fi

cache_dir="$project_dir/work/uv-cache"
mkdir -p "$cache_dir"
UV_CACHE_DIR="$cache_dir" uv sync --all-extras

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env without secrets. Add TYPESAFE_API_KEY yourself."
else
  echo ".env already exists; leaving it unchanged."
fi

model_path="${ASSISTANT_WHISPER_MODEL_PATH:-$project_dir/models/ggml-base.en.bin}"
if [[ ! -f "$model_path" ]]; then
  echo "The default Whisper base.en model is about 142 MB."
  if ask "Download it to $model_path?"; then
    mkdir -p "$(dirname "$model_path")"
    curl -fL --progress-bar \
      https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin \
      -o "$model_path.part"
    mv "$model_path.part" "$model_path"
  else
    echo "Skipped Whisper model download."
  fi
else
  echo "Whisper model already exists at $model_path."
fi

if UV_CACHE_DIR="$cache_dir" uv run python -c \
  'from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); path=Path(p.chromium.executable_path); p.stop(); raise SystemExit(0 if path.exists() else 1)' \
  >/dev/null 2>&1; then
  echo "Playwright Chromium is already installed."
else
  echo "Playwright Chromium may download several hundred MB."
  if ask "Install/update Playwright Chromium now?"; then
    UV_CACHE_DIR="$cache_dir" uv run playwright install chromium
  fi
fi

ollama_model="${ASSISTANT_OLLAMA_MODEL:-qwen3.5:2b}"
if command -v ollama >/dev/null 2>&1; then
  if ! ollama list 2>/dev/null | awk 'NR > 1 {print $1}' | grep -Fxq "$ollama_model"; then
    echo "Ollama model $ollama_model is not available locally and may be a multi-GB download."
    echo "Ensure Ollama is running (brew services start ollama)."
    if ask "Pull $ollama_model now?"; then
      ollama pull "$ollama_model"
    fi
  else
    echo "Ollama model $ollama_model is already installed."
  fi
fi

echo
echo "Bootstrap checks complete. Next:"
echo "1. Add TYPESAFE_API_KEY to .env (optional for exact commands)."
echo "2. Grant Microphone and Accessibility permissions to your terminal."
echo "3. Run: uv run local-assistant doctor"

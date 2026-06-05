#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT/dist/release"
CLI_DIR="$OUT_DIR/majsoul-autopilot-rs-macos-arm64"
GUI_DIR="$OUT_DIR/majsoul-autopilot-gui-macos-arm64"
MODEL_DIR="$ROOT/models/mortal-298k"
CLI_BIN="$ROOT/target/release/majsoul-autopilot-rs"
APP_BUNDLE="$ROOT/target/release/bundle/macos/Majsoul Autopilot.app"

if [[ ! -d "$MODEL_DIR" ]]; then
  echo "missing model directory: $MODEL_DIR" >&2
  exit 1
fi

if [[ ! -f "$MODEL_DIR/model.safetensors" || ! -f "$MODEL_DIR/model_config.json" ]]; then
  echo "model directory must contain model.safetensors and model_config.json" >&2
  exit 1
fi

cd "$ROOT"
cargo build --release -p majsoul-autopilot-rs
npm --prefix apps/desktop run tauri -- build

rm -rf "$OUT_DIR"
mkdir -p "$CLI_DIR/models" "$GUI_DIR/models"

cp "$CLI_BIN" "$CLI_DIR/"
cp settings.example.json README.md README.zh-CN.md "$CLI_DIR/"
cp -R "$MODEL_DIR" "$CLI_DIR/models/"

cp -R "$APP_BUNDLE" "$GUI_DIR/"
cp "$CLI_BIN" "$GUI_DIR/"
cp settings.example.json README.md README.zh-CN.md "$GUI_DIR/"
cp -R "$MODEL_DIR" "$GUI_DIR/models/"

(
  cd "$OUT_DIR"
  rm -f majsoul-autopilot-rs-macos-arm64.zip majsoul-autopilot-gui-macos-arm64.zip
  ditto -c -k --norsrc --keepParent majsoul-autopilot-rs-macos-arm64 majsoul-autopilot-rs-macos-arm64.zip
  ditto -c -k --norsrc --keepParent majsoul-autopilot-gui-macos-arm64 majsoul-autopilot-gui-macos-arm64.zip
  shasum -a 256 majsoul-autopilot-rs-macos-arm64.zip majsoul-autopilot-gui-macos-arm64.zip > SHA256SUMS.txt
)

echo "created:"
echo "$OUT_DIR/majsoul-autopilot-rs-macos-arm64.zip"
echo "$OUT_DIR/majsoul-autopilot-gui-macos-arm64.zip"
echo "$OUT_DIR/SHA256SUMS.txt"

#!/usr/bin/env bash
set -euo pipefail

model_dir="$(dirname "$0")/../models/bge-small-en-v1.5"
mkdir -p "$model_dir"
base="https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/main"

download() {
  local remote="$1" local_name="$2"
  if [ -f "$model_dir/$local_name" ]; then
    echo "already present: $local_name"
    return
  fi
  echo "downloading $remote"
  curl -sSL "$base/$remote" -o "$model_dir/$local_name"
}

download "onnx/model.onnx" "model.onnx"
download "vocab.txt" "vocab.txt"
download "tokenizer.json" "tokenizer.json"
download "config.json" "config.json"

size=$(stat -c%s "$model_dir/model.onnx" 2>/dev/null || stat -f%z "$model_dir/model.onnx")
if [ "$size" -lt 52428800 ]; then
  echo "model.onnx is only $size bytes - the download almost certainly returned an error page." >&2
  exit 1
fi
echo "model ready in $model_dir"

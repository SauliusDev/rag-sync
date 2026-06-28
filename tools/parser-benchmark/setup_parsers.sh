#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/../.. && pwd)"
BENCH_DIR="$ROOT_DIR/tools/parser-benchmark"
VENV_ROOT="$BENCH_DIR/.venvs"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"

mkdir -p "$VENV_ROOT"

create_venv() {
  local name="$1"
  local venv_dir="$VENV_ROOT/$name"
  if [[ ! -d "$venv_dir" ]]; then
    "$PYTHON_BIN" -m venv "$venv_dir"
  fi
  "$venv_dir/bin/python" -m pip install --upgrade pip
}

install_marker() {
  create_venv "marker"
  "$VENV_ROOT/marker/bin/pip" install marker-pdf
}

install_mineru() {
  create_venv "mineru"
  "$VENV_ROOT/mineru/bin/pip" install uv
  "$VENV_ROOT/mineru/bin/uv" pip install --python "$VENV_ROOT/mineru/bin/python" -U "mineru[all]"
  "$VENV_ROOT/mineru/bin/python" -m pip install --force-reinstall \
    "vllm==0.9.2" \
    "torch==2.7.0" \
    "torchvision==0.22.0" \
    "torchaudio==2.7.0" \
    "transformers==4.53.2" \
    "tokenizers==0.21.4" \
    "huggingface-hub>=0.33,<1.0" \
    "numpy==2.2.6"
}

install_paddleocr() {
  create_venv "paddleocr"
  "$VENV_ROOT/paddleocr/bin/pip" install paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
  "$VENV_ROOT/paddleocr/bin/pip" install -U "paddleocr[doc-parser]"
  "$VENV_ROOT/paddleocr/bin/pip" install python-docx
}

install_glmocr() {
  create_venv "glmocr"
  "$VENV_ROOT/glmocr/bin/pip" install "glmocr[selfhosted]"
  "$VENV_ROOT/glmocr/bin/python" -m pip install --force-reinstall \
    "vllm==0.9.2" \
    "torch==2.7.0" \
    "torchvision==0.22.0" \
    "torchaudio==2.7.0" \
    "transformers==4.53.2" \
    "tokenizers==0.21.4" \
    "huggingface-hub>=0.33,<1.0" \
    "numpy==2.2.6"
}

TARGETS=("$@")
if [[ ${#TARGETS[@]} -eq 0 ]]; then
  TARGETS=(marker mineru paddleocr glmocr)
fi

for target in "${TARGETS[@]}"; do
  case "$target" in
    marker) install_marker ;;
    mineru) install_mineru ;;
    paddleocr) install_paddleocr ;;
    glmocr) install_glmocr ;;
    all)
      install_marker
      install_mineru
      install_paddleocr
      install_glmocr
      ;;
    *)
      echo "unknown parser target: $target" >&2
      exit 1
      ;;
  esac
done

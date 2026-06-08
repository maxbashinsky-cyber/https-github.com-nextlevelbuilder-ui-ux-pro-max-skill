#!/usr/bin/env bash
#
# Bootstrap + run the Kronos K-line forecasting demo on CPU.
#
# Clones shiyu-coder/Kronos, installs dependencies, drops demo_run.py into the
# repo, and runs it. The demo downloads the small pretrained model + tokenizer
# from Hugging Face, so the environment's network policy MUST allow:
#     huggingface.co  *.huggingface.co  *.hf.co
# (Custom network access, with the default package-manager list also enabled so
#  pip + git keep working.)
#
# Usage:  bash kronos-demo/run_kronos_demo.sh
set -euo pipefail

KRONOS_DIR="${KRONOS_DIR:-$HOME/Kronos}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> 1/4 Clone Kronos (if missing)"
if [ ! -d "$KRONOS_DIR/.git" ]; then
  git clone --depth 1 https://github.com/shiyu-coder/Kronos.git "$KRONOS_DIR"
else
  echo "    already present at $KRONOS_DIR"
fi

echo "==> 2/4 Install dependencies"
# torch must come from PyPI here: the download.pytorch.org CPU index is not in
# the default allowlist. The PyPI wheel works fine on CPU.
pip install --quiet torch
pip install --quiet numpy pandas einops==0.8.1 huggingface_hub==0.33.1 \
  matplotlib==3.9.3 tqdm==4.67.1 safetensors==0.6.2

echo "==> 3/4 Stage demo script into the Kronos repo"
cp "$SCRIPT_DIR/demo_run.py" "$KRONOS_DIR/demo_run.py"

echo "==> 4/4 Run the forecast demo"
cd "$KRONOS_DIR"
python3 demo_run.py

echo
echo "Done. Forecast plot written to: $KRONOS_DIR/demo_forecast.png"

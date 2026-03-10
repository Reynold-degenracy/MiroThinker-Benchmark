#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "error: ${PYTHON_BIN} not found. Please create the virtual environment first."
  exit 1
fi

# Override global pytest addopts so this script only runs the targeted tests.
"${PYTHON_BIN}" -m pytest -o addopts='' "${ROOT_DIR}/tests/test_agenthub_client_token_usage.py" "$@"

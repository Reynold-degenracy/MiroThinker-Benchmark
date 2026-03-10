#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${SCRIPT_DIR}/run_gaia_validation_text_task.py"

TASK_ID=""
API_BASE_URL="http://localhost:7210"
AUTH_TOKEN=""
TIMEOUT="0"
CONNECT_TIMEOUT="10"
MATCH_MODE="contains"
SESSION_ID=""
DATASET_PATH=""

usage() {
  cat <<'EOF'
Usage:
  ./tests/run_gaia_validation_text_task.sh --id <task_id> [options]

Required:
  --id <task_id>            GAIA task_id

Options:
  --api-base-url <url>      API base url (default: http://localhost:7210)
  --auth-token <token>      Bearer token value (default: same as --id)
  --timeout <seconds>       Stream read timeout seconds; <=0 disables (default: 0)
  --connect-timeout <sec>   Connect timeout seconds (default: 10)
  --match-mode <mode>       contains|exact|none (default: contains)
  --session-id <session_id> Optional session id
  --dataset-path <path>     Optional standardized_data.jsonl path
  -h, --help                Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --id)
      TASK_ID="${2:-}"
      shift 2
      ;;
    --api-base-url)
      API_BASE_URL="${2:-}"
      shift 2
      ;;
    --auth-token)
      AUTH_TOKEN="${2:-}"
      shift 2
      ;;
    --timeout)
      TIMEOUT="${2:-}"
      shift 2
      ;;
    --connect-timeout)
      CONNECT_TIMEOUT="${2:-}"
      shift 2
      ;;
    --match-mode)
      MATCH_MODE="${2:-}"
      shift 2
      ;;
    --session-id)
      SESSION_ID="${2:-}"
      shift 2
      ;;
    --dataset-path)
      DATASET_PATH="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "${TASK_ID}" ]]; then
  echo "[ERROR] --id is required" >&2
  usage
  exit 2
fi

if [[ -z "${AUTH_TOKEN}" ]]; then
  AUTH_TOKEN="${TASK_ID}"
fi

cmd=(
  python3 "${PY_SCRIPT}"
  --task-id "${TASK_ID}"
  --api-base-url "${API_BASE_URL}"
  --auth-token "${AUTH_TOKEN}"
  --timeout "${TIMEOUT}"
  --connect-timeout "${CONNECT_TIMEOUT}"
  --match-mode "${MATCH_MODE}"
)

if [[ -n "${SESSION_ID}" ]]; then
  cmd+=(--session-id "${SESSION_ID}")
fi

if [[ -n "${DATASET_PATH}" ]]; then
  cmd+=(--dataset-path "${DATASET_PATH}")
fi

"${cmd[@]}"

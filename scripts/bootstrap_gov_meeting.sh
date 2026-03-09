#!/usr/bin/env bash
set -euo pipefail

# Bootstrap the recommended "政务会议" stack (single entry, NO VibeVoice).
#
# This is a convenience wrapper around:
#   1) `docker compose up -d --build`
#   2) `./scripts/prefetch_models_docker.sh` (KEEP_RUNNING=true)
#   3) Optional smoke test against the single public port
#
# Usage:
#   ./scripts/bootstrap_gov_meeting.sh
#
# Options (env):
#   ENV_FILE=.env
#   AUDIO=data/benchmark/test_short.mp3
#   RUN_SMOKE=true|false
#   KEEP_RUNNING=true|false (default: true for this script)
#   TRANSCRIBE_TIMEOUT_S=1200
#   ENHANCE_TIMEOUT_S=1200

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

ENV_FILE="${ENV_FILE:-.env}"
AUDIO="${AUDIO:-data/benchmark/test_short.mp3}"
RUN_SMOKE="${RUN_SMOKE:-true}"
TRANSCRIBE_TIMEOUT_S="${TRANSCRIBE_TIMEOUT_S:-1200}"
ENHANCE_TIMEOUT_S="${ENHANCE_TIMEOUT_S:-1200}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found. Please install Docker Engine/Docker Desktop first." >&2
  exit 2
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: docker compose not available. Please install Docker Compose plugin." >&2
  exit 2
fi

echo "======================================"
echo "Xiyu 政务会议一键启动（单入口）"
echo "======================================"
echo "入口端口默认来自 .env 的 PORT（默认 18200）"
echo ""
echo "Primary path:"
echo "  - cp .env.example .env"
echo "  - docker compose up -d --build"
echo "Optional warmup:"
echo "  - ./scripts/prefetch_models_docker.sh"
echo ""

KEEP_RUNNING=true ENV_FILE="${ENV_FILE}" AUDIO="${AUDIO}" \
  TRANSCRIBE_TIMEOUT_S="${TRANSCRIBE_TIMEOUT_S}" ENHANCE_TIMEOUT_S="${ENHANCE_TIMEOUT_S}" \
  ./scripts/prefetch_models_docker.sh

if [ "${RUN_SMOKE}" != "true" ]; then
  exit 0
fi

# Re-source env to get PORT (normalize CRLF).
_source_env() {
  local env_path="$1"
  local tmp
  tmp="$(mktemp -t xiyu_env_XXXXXX)"
  tr -d '\r' <"${env_path}" >"${tmp}"
  set -a
  # shellcheck disable=SC1090
  source "${tmp}"
  set +a
  rm -f "${tmp}" || true
}

if [ -f "${ENV_FILE}" ]; then
  _source_env "${ENV_FILE}"
fi

PORT="${PORT:-18200}"
echo ""
echo "[bootstrap] Running smoke tests on single public port: ${PORT}"
PORTS="${PORT}" DIARIZER_PORT="" REMOTE_ASR_PORTS="" SKIP_REMOTE_ASR_CHECKS=true \
  scripts/smoke_all_endpoints.sh

echo ""
echo "Done. Web UI + API: http://<server-ip>:${PORT}"


#!/usr/bin/env bash
set -euo pipefail

# Bootstrap the recommended "政务会议" stack (NO VibeVoice):
# - Router (single public entry, Web UI + API): :8200
# - Qwen3-ASR-1.7B (remote vLLM): :9001 + wrapper :8201
# - Whisper large-v3 (GPU): :8105
# - FunASR PyTorch (GPU): :8101
# - SenseVoiceSmall (GPU): :8103
# - ONNX (CPU): :8102
# - External diarizer (pyannote): :8300
# - ClearVoice denoise microservice (GPU, MossFormer2_48000Hz): :8400
#
# Usage:
#   ./scripts/bootstrap_gov_meeting.sh
#
# Optional env:
#   ENV_FILE=.env
#   COMPOSE_FILE=docker-compose.models.yml
#   RUN_SMOKE=true|false
#   AUDIO=data/benchmark/test_short.mp3
#   CLEARVOICE_ENHANCE_TIMEOUT_S=1200
#   SMOKE_TRANSCRIBE_TIMEOUT_S=600

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.models.yml}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found. Please install Docker Engine/Docker Desktop first." >&2
  exit 2
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: docker compose not available. Please install Docker Compose plugin." >&2
  exit 2
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl not found." >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found (required for small JSON/audio checks in this script)." >&2
  exit 2
fi

if [ ! -f "${ENV_FILE}" ]; then
  if [ ! -f ".env.example" ]; then
    echo "ERROR: .env.example not found in repo root." >&2
    exit 2
  fi
  cp .env.example "${ENV_FILE}"
  echo "[bootstrap] Created ${ENV_FILE} from .env.example"
fi

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

_wait_ok() {
  # Usage: _wait_ok NAME URL RETRIES SLEEP_S TIMEOUT_S
  local name="$1"
  local url="$2"
  local retries="${3:-120}"
  local sleep_s="${4:-2}"
  local timeout_s="${5:-3}"

  for _ in $(seq 1 "${retries}"); do
    if curl -fsS -m "${timeout_s}" "${url}" >/dev/null 2>&1; then
      echo "[ready] ${name} -> ${url}"
      return 0
    fi
    sleep "${sleep_s}"
  done

  echo "ERROR: timeout waiting for ${name}: ${url}" >&2
  return 1
}

_wait_json_ok() {
  # Usage: _wait_json_ok NAME URL RETRIES SLEEP_S TIMEOUT_S
  local name="$1"
  local url="$2"
  local retries="${3:-240}"
  local sleep_s="${4:-2}"
  local timeout_s="${5:-8}"
  local tmp
  tmp="$(mktemp -t xiyu_json_XXXXXX)"

  for _ in $(seq 1 "${retries}"); do
    if curl -fsS -m "${timeout_s}" -o "${tmp}" "${url}" >/dev/null 2>&1 && python3 -m json.tool <"${tmp}" >/dev/null 2>&1; then
      rm -f "${tmp}" || true
      echo "[ready] ${name} -> ${url}"
      return 0
    fi
    sleep "${sleep_s}"
  done

  echo "ERROR: timeout waiting for ${name}: ${url}" >&2
  if [ -s "${tmp}" ]; then
    echo "---- last response (head) ----" >&2
    head -c 2048 "${tmp}" >&2 || true
    echo "" >&2
    echo "------------------------------" >&2
  fi
  rm -f "${tmp}" || true
  return 1
}

_assert_wav_file() {
  local path="$1"
  python3 - "${path}" <<'PY'
import sys
from pathlib import Path

p = Path(sys.argv[1])
data = p.read_bytes()
if len(data) < 44:
    raise SystemExit(f"wav too small: {len(data)} bytes")
if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
    raise SystemExit("not a RIFF/WAVE file")
PY
}

_source_env "${ENV_FILE}"

echo "[bootstrap] Starting gov meeting stack (no VibeVoice)"

profiles=(router qwen3 pytorch onnx sensevoice whisper diarizer clearvoice)
compose_args=(-f "${COMPOSE_FILE}")
for p in "${profiles[@]}"; do
  compose_args+=(--profile "${p}")
done

docker compose "${compose_args[@]}" up -d --build

# Resolve ports after compose reads `.env` too (source already loaded, but keep defaults).
PORT_XIYU_ROUTER="${PORT_XIYU_ROUTER:-8200}"
PORT_PYTORCH="${PORT_PYTORCH:-8101}"
PORT_ONNX="${PORT_ONNX:-8102}"
PORT_SENSEVOICE="${PORT_SENSEVOICE:-8103}"
PORT_WHISPER="${PORT_WHISPER:-8105}"
PORT_XIYU_QWEN3="${PORT_XIYU_QWEN3:-8201}"
PORT_DIARIZER="${PORT_DIARIZER:-8300}"
PORT_CLEARVOICE="${PORT_CLEARVOICE:-8400}"
PORT_QWEN3_ASR="${PORT_QWEN3_ASR:-9001}"

echo "[bootstrap] Expected ports:"
echo "  - Router (Web UI + API): ${PORT_XIYU_ROUTER}"
echo "  - PyTorch:              ${PORT_PYTORCH}"
echo "  - ONNX:                 ${PORT_ONNX}"
echo "  - SenseVoice:           ${PORT_SENSEVOICE}"
echo "  - Whisper:              ${PORT_WHISPER}"
echo "  - Qwen3 wrapper:        ${PORT_XIYU_QWEN3}"
echo "  - Diarizer:             ${PORT_DIARIZER}"
echo "  - ClearVoice:           ${PORT_CLEARVOICE}"
echo "  - Qwen3-ASR (debug):    ${PORT_QWEN3_ASR}"
echo ""

echo "[bootstrap] Waiting for services to be ready..."
_wait_ok "router" "http://localhost:${PORT_XIYU_ROUTER}/health" 120 2 3
_wait_ok "xiyu-pytorch" "http://localhost:${PORT_PYTORCH}/health" 120 2 3
_wait_ok "xiyu-onnx" "http://localhost:${PORT_ONNX}/health" 120 2 3
_wait_ok "xiyu-sensevoice" "http://localhost:${PORT_SENSEVOICE}/health" 120 2 3
_wait_ok "xiyu-whisper" "http://localhost:${PORT_WHISPER}/health" 120 2 3
_wait_ok "xiyu-qwen3" "http://localhost:${PORT_XIYU_QWEN3}/health" 120 2 3
_wait_ok "diarizer" "http://localhost:${PORT_DIARIZER}/health" 240 2 3
_wait_ok "clearvoice" "http://localhost:${PORT_CLEARVOICE}/health" 240 2 3

# Remote ASR server readiness (used by router + wrapper).
_wait_json_ok "qwen3-asr (/v1/models)" "http://localhost:${PORT_QWEN3_ASR}/v1/models" 360 2 8

# Trigger one ClearVoice inference to force weight download/load (and verify the pipeline).
AUDIO="${AUDIO:-data/benchmark/test_short.mp3}"
CLEARVOICE_ENHANCE_TIMEOUT_S="${CLEARVOICE_ENHANCE_TIMEOUT_S:-1200}"
if [ -f "${AUDIO}" ]; then
  echo "[bootstrap] Triggering ClearVoice weight download via /api/v1/enhance (${AUDIO})"
  tmp_wav="$(mktemp -t xiyu_clearvoice_XXXXXX.wav)"
  if curl -fsS -m "${CLEARVOICE_ENHANCE_TIMEOUT_S}" -o "${tmp_wav}" \
      -X POST "http://localhost:${PORT_CLEARVOICE}/api/v1/enhance" \
      -F "file=@${AUDIO}"; then
    _assert_wav_file "${tmp_wav}"
    rm -f "${tmp_wav}" || true
    echo "[bootstrap] ClearVoice enhance OK"
  else
    rm -f "${tmp_wav}" || true
    echo "ERROR: ClearVoice enhance failed. Common causes:" >&2
    echo "  - CLEARVOICE_STUDIO_DIR not mounted (see .env: CLEARVOICE_STUDIO_DIR)" >&2
    echo "  - Missing network access to download checkpoints from HuggingFace" >&2
    echo "  - GPU OOM (try lowering QWEN3_GPU_MEMORY_UTILIZATION or set CLEARVOICE_FORCE_CPU=true)" >&2
    echo "" >&2
    echo "Debug:" >&2
    echo "  - curl http://localhost:${PORT_CLEARVOICE}/info" >&2
    echo "  - docker logs -n 200 xiyu-clearvoice" >&2
    exit 1
  fi
else
  echo "[bootstrap] WARN: AUDIO not found (${AUDIO}); skipping ClearVoice enhance warmup"
fi

RUN_SMOKE="${RUN_SMOKE:-true}"
SMOKE_TRANSCRIBE_TIMEOUT_S="${SMOKE_TRANSCRIBE_TIMEOUT_S:-600}"
if [ "${RUN_SMOKE}" = "true" ]; then
  echo ""
  echo "[bootstrap] Running smoke tests (this also triggers model downloads on first run)..."
  PORTS="${PORT_XIYU_ROUTER} ${PORT_PYTORCH} ${PORT_ONNX} ${PORT_SENSEVOICE} ${PORT_WHISPER} ${PORT_XIYU_QWEN3}" \
  DIARIZER_PORT="${PORT_DIARIZER}" \
  REMOTE_ASR_PORTS="${PORT_QWEN3_ASR}" \
  TRANSCRIBE_TIMEOUT_S="${SMOKE_TRANSCRIBE_TIMEOUT_S}" \
  REMOTE_ASR_READY_RETRIES=360 \
  REMOTE_ASR_READY_SLEEP_S=2 \
  scripts/smoke_all_endpoints.sh
fi

echo ""
echo "======================================"
echo "Ready"
echo "======================================"
echo "Web UI + API (Router): http://<server-ip>:${PORT_XIYU_ROUTER}"
echo ""
echo "Tip (prod): only expose ${PORT_XIYU_ROUTER} to the intranet; keep other ports internal."


#!/usr/bin/env bash
set -euo pipefail

# Optional warmup / prefetch helper for the single-entry docker-compose.yml stack.
#
# What it does:
# 1) Ensures `.env` exists (creates from `.env.example` if missing)
# 2) `docker compose up -d --build`
# 3) Waits for router health on http://localhost:${PORT:-18200}/health
# 4) Triggers a few small requests to force model weights download + load:
#    - ClearVoice denoise (via router preprocess endpoint)
#    - Speaker diarization path (with_speaker=true)
#    - Each ASR backend once (via router `target_backend=...`)
#
# Usage:
#   ./scripts/prefetch_models_docker.sh
#
# Options (env):
#   ENV_FILE=.env
#   AUDIO=data/benchmark/test_short.mp3
#   KEEP_RUNNING=true|false     (default: false -> docker compose down after warmup)
#   TRANSCRIBE_TIMEOUT_S=1200
#   ENHANCE_TIMEOUT_S=1200

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

ENV_FILE="${ENV_FILE:-.env}"
AUDIO="${AUDIO:-data/benchmark/test_short.mp3}"
KEEP_RUNNING="${KEEP_RUNNING:-false}"
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
  echo "[prefetch] Created ${ENV_FILE} from .env.example"
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
  local retries="${3:-180}"
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

_assert_json_code_zero() {
  local path="$1"
  python3 - "${path}" <<'PY'
import json, sys
obj=json.load(open(sys.argv[1],encoding="utf-8"))
code=obj.get("code")
if code != 0:
    raise SystemExit(f"expected code=0, got code={code!r}")
PY
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

_post_transcribe() {
  # Usage: _post_transcribe BASE_URL TARGET_BACKEND WITH_SPEAKER
  local base_url="$1"
  local target_backend="$2"
  local with_speaker="$3"
  local tmp
  tmp="$(mktemp -t xiyu_prefetch_transcribe_XXXXXX.json)"

  if curl -fsS -m "${TRANSCRIBE_TIMEOUT_S}" -o "${tmp}" \
      -X POST "${base_url}/api/v1/transcribe" \
      -F "file=@${AUDIO}" \
      -F "with_speaker=${with_speaker}" \
      -F "apply_hotword=true" \
      -F "apply_llm=false" \
      -F "target_backend=${target_backend}"; then
    python3 -m json.tool <"${tmp}" >/dev/null 2>&1 || { echo "ERROR: invalid JSON from /transcribe" >&2; cat "${tmp}" >&2; return 1; }
    _assert_json_code_zero "${tmp}"
    rm -f "${tmp}" || true
    return 0
  fi

  echo "ERROR: /api/v1/transcribe failed (target_backend=${target_backend}, with_speaker=${with_speaker})" >&2
  if [ -s "${tmp}" ]; then
    echo "---- last response (head) ----" >&2
    head -c 4096 "${tmp}" >&2 || true
    echo "" >&2
    echo "------------------------------" >&2
  fi
  rm -f "${tmp}" || true
  return 1
}

_post_enhance() {
  # Usage: _post_enhance BASE_URL
  local base_url="$1"
  local tmp
  tmp="$(mktemp -t xiyu_prefetch_enhance_XXXXXX.wav)"

  if curl -fsS -m "${ENHANCE_TIMEOUT_S}" -o "${tmp}" \
      -X POST "${base_url}/api/v1/preprocess/enhance" \
      -F "file=@${AUDIO}" \
      -F 'asr_options={"preprocess":{"denoise_enable":true,"denoise_backend":"clearvoice"}}'; then
    _assert_wav_file "${tmp}"
    rm -f "${tmp}" || true
    return 0
  fi

  echo "ERROR: /api/v1/preprocess/enhance failed (ClearVoice warmup)" >&2
  rm -f "${tmp}" || true
  return 1
}

_source_env "${ENV_FILE}"

PORT="${PORT:-18200}"
BASE_URL="http://localhost:${PORT}"

echo "======================================"
echo "Xiyu model prefetch / warmup (Docker)"
echo "======================================"
echo "- ENV_FILE=${ENV_FILE}"
echo "- BASE_URL=${BASE_URL}"
echo "- AUDIO=${AUDIO}"
echo "- KEEP_RUNNING=${KEEP_RUNNING}"
echo ""

if [ ! -f "${AUDIO}" ]; then
  if [ -f "clip_30s.m4a" ]; then
    AUDIO="clip_30s.m4a"
    echo "[prefetch] AUDIO not found; fallback to ${AUDIO}"
  else
    echo "ERROR: AUDIO not found: ${AUDIO}" >&2
    echo "Tip: set AUDIO=/path/to/short.wav" >&2
    exit 2
  fi
fi

echo "[prefetch] Starting docker compose stack..."
docker compose up -d --build

echo "[prefetch] Waiting for router health..."
_wait_ok "router" "${BASE_URL}/health" 180 2 3

echo "[prefetch] Probing preprocess backends..."
curl -fsS -m 10 "${BASE_URL}/api/v1/preprocess/status" | python3 -m json.tool >/dev/null

PASS=0
FAIL=0

_step() {
  local name="$1"; shift || true
  echo ""
  echo "---- ${name} ----"
  if "$@"; then
    echo "[ok] ${name}"
    PASS=$((PASS + 1))
  else
    echo "[fail] ${name}" >&2
    FAIL=$((FAIL + 1))
  fi
}

# 1) ClearVoice warmup (forces checkpoint download + model init)
_step "ClearVoice enhance warmup" _post_enhance "${BASE_URL}"

# 2) Speaker path warmup (forces diarizer init + qwen3 call)
_step "Speaker diarization warmup (qwen3)" _post_transcribe "${BASE_URL}" "qwen3" "true"

# 3) Warm each backend once (best-effort; router proxies internally)
for tb in qwen3 whisper pytorch sensevoice onnx; do
  _step "ASR warmup (${tb})" _post_transcribe "${BASE_URL}" "${tb}" "false"
done

echo ""
echo "=============================="
echo "Prefetch summary"
echo "=============================="
echo "PASS=${PASS} FAIL=${FAIL}"

echo ""
echo "Router targets (best-effort probe):"
curl -fsS -m 10 "${BASE_URL}/api/v1/backend/targets" | python3 -m json.tool || true

if [ "${KEEP_RUNNING}" != "true" ]; then
  echo ""
  echo "[prefetch] Stopping stack (KEEP_RUNNING!=true)..."
  docker compose down
fi

if [ "${FAIL}" -gt 0 ]; then
  echo ""
  echo "ERROR: Some warmup steps failed (FAIL=${FAIL})." >&2
  echo "Tip: check logs with: docker compose logs -f --tail 200" >&2
  exit 1
fi

echo ""
echo "Done. Web UI + API: http://<server-ip>:${PORT}"


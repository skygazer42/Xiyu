#!/usr/bin/env bash
set -uo pipefail

# Smoke-test Xiyu HTTP endpoints across multiple ports (multi-model deployment).
#
# Usage:
#   scripts/smoke_all_endpoints.sh
#
# Options (env):
#   PORTS="8101 8102 ..."   Ports to test (default: common Xiyu ports)
#   AUDIO="data/benchmark/test_short.mp3"  Audio file for /transcribe tests
#   TIMEOUT_S=10            Per-request curl timeout seconds (non-transcribe endpoints)
#   TRANSCRIBE_TIMEOUT_S=60 /transcribe curl timeout seconds
#   DIARIZER_PORT=8300      Optional diarizer port (set empty to skip)
#
# Notes:
# - This is a best-effort smoke test. Some backends require extra model artifacts
#   (e.g. GGUF) or remote services (Qwen3/VibeVoice wrappers).

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PORTS="${PORTS:-8101 8102 8103 8104 8105 8201 8202}"
# Use `-` (not `:-`) so DIARIZER_PORT="" can intentionally disable diarizer checks.
DIARIZER_PORT="${DIARIZER_PORT-8300}"
REMOTE_ASR_PORTS="${REMOTE_ASR_PORTS:-9001 9002}"
SKIP_REMOTE_ASR_CHECKS="${SKIP_REMOTE_ASR_CHECKS:-false}"
TIMEOUT_S="${TIMEOUT_S:-10}"
# Transcribe calls can be slow (first-time model download/warmup), especially for
# remote ASR backends. Use a higher default so the smoke test is reliable.
TRANSCRIBE_TIMEOUT_S="${TRANSCRIBE_TIMEOUT_S:-60}"
REMOTE_ASR_TIMEOUT_S="${REMOTE_ASR_TIMEOUT_S:-8}"
# First-time vLLM startup (download + graph capture + warmup) can take >60s.
REMOTE_ASR_READY_RETRIES="${REMOTE_ASR_READY_RETRIES:-90}"
REMOTE_ASR_READY_SLEEP_S="${REMOTE_ASR_READY_SLEEP_S:-2}"
AUDIO="${AUDIO:-data/benchmark/test_short.mp3}"

if [ ! -f "${AUDIO}" ]; then
  echo "ERROR: AUDIO not found: ${AUDIO}" >&2
  exit 2
fi

PASS=0
FAIL=0

_ok() {
  PASS=$((PASS + 1))
  echo "OK   $*"
}

_fail() {
  FAIL=$((FAIL + 1))
  echo "FAIL $*" >&2
}

_tmpfile() {
  mktemp -t xiyu_smoke_XXXXXX
}

_curl_to_file() {
  # Usage: _curl_to_file OUT_FILE URL [curl args...]
  # Writes response body to OUT_FILE, prints HTTP status code (or 000) to stdout.
  local out_file="$1"; shift || true
  local url="$1"; shift || true
  local code
  if code="$(curl -sS -m "${TIMEOUT_S}" -o "${out_file}" -w '%{http_code}' "${url}" "$@")"; then
    echo "${code}"
  else
    echo "000"
  fi
}

_curl_to_file_timeout() {
  # Usage: _curl_to_file_timeout TIMEOUT_S OUT_FILE URL [curl args...]
  local timeout_s="$1"; shift || true
  local out_file="$1"; shift || true
  local url="$1"; shift || true
  local code
  if code="$(curl -sS -m "${timeout_s}" -o "${out_file}" -w '%{http_code}' "${url}" "$@")"; then
    echo "${code}"
  else
    echo "000"
  fi
}

_print_body_head() {
  # Usage: _print_body_head FILE
  local f="$1"
  if [ -s "${f}" ]; then
    echo "---- response body (head) ----" >&2
    head -c 4096 "${f}" >&2 || true
    echo "" >&2
    echo "------------------------------" >&2
  fi
}

_wait_json_ok() {
  # Usage: _wait_json_ok URL RETRIES SLEEP_S TIMEOUT_S
  local url="$1"
  local retries="$2"
  local sleep_s="$3"
  local timeout_s="$4"

  local tmp
  tmp="$(_tmpfile)"
  local code="000"

  for i in $(seq 1 "${retries}"); do
    code="$(_curl_to_file_timeout "${timeout_s}" "${tmp}" "${url}")"
    if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1; then
      rm -f "${tmp}" || true
      return 0
    fi
    # Keep last response body for debugging.
    sleep "${sleep_s}"
  done

  if [ "${code}" = "000" ]; then
    echo "ERROR curl failed (HTTP 000): ${url}" >&2
  else
    echo "ERROR HTTP ${code}: ${url}" >&2
  fi
  _print_body_head "${tmp}"
  rm -f "${tmp}" || true
  return 1
}

_curl_json() {
  # Usage: _curl_json URL [curl args...]
  local url="$1"; shift || true
  local tmp
  tmp="$(_tmpfile)"
  local code
  code="$(_curl_to_file "${tmp}" "${url}" "$@")"

  if [ "${code}" = "000" ]; then
    echo "ERROR curl failed (HTTP 000): ${url}" >&2
    _print_body_head "${tmp}"
    rm -f "${tmp}" || true
    return 1
  fi

  if [ "${code}" -lt 200 ] || [ "${code}" -ge 300 ]; then
    echo "ERROR HTTP ${code}: ${url}" >&2
    _print_body_head "${tmp}"
    rm -f "${tmp}" || true
    return 1
  fi

  if ! python3 -m json.tool <"${tmp}" >/dev/null 2>&1; then
    echo "ERROR invalid JSON response: ${url}" >&2
    _print_body_head "${tmp}"
    rm -f "${tmp}" || true
    return 1
  fi

  rm -f "${tmp}" || true
  return 0
}

_curl_text() {
  local url="$1"; shift || true
  local tmp
  tmp="$(_tmpfile)"
  local code
  code="$(_curl_to_file "${tmp}" "${url}" "$@")"

  if [ "${code}" = "000" ]; then
    echo "ERROR curl failed (HTTP 000): ${url}" >&2
    _print_body_head "${tmp}"
    rm -f "${tmp}" || true
    return 1
  fi

  if [ "${code}" -lt 200 ] || [ "${code}" -ge 300 ]; then
    echo "ERROR HTTP ${code}: ${url}" >&2
    _print_body_head "${tmp}"
    rm -f "${tmp}" || true
    return 1
  fi

  rm -f "${tmp}" || true
  return 0
}

_assert_transcribe_success() {
  # Usage: _assert_transcribe_success JSON_FILE
  local f="$1"
  python3 - "${f}" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as fp:
    obj = json.load(fp)

code = obj.get("code")
if code != 0:
    raise SystemExit(f"expected code=0, got code={code!r}")
PY
}

_assert_batch_success() {
  # Usage: _assert_batch_success JSON_FILE
  local f="$1"
  python3 - "${f}" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as fp:
    obj = json.load(fp)

failed = obj.get("failed_count")
if isinstance(failed, int) and failed == 0:
    raise SystemExit(0)

results = obj.get("results") or []
if isinstance(results, list):
    bad = [r for r in results if isinstance(r, dict) and not r.get("success")]
    if bad:
        err = bad[0].get("error") or bad[0]
        raise SystemExit(f"batch item failed: {err}")

raise SystemExit(f"expected failed_count=0, got failed_count={failed!r}")
PY
}

_test_one_base() {
  local base="$1"

  echo ""
  echo "=============================="
  echo "== BASE=${base}"
  echo "=============================="

  if _curl_json "${base}/health"; then _ok "${base} GET /health"; else _fail "${base} GET /health"; fi
  if _curl_json "${base}/openapi.json"; then _ok "${base} GET /openapi.json"; else _fail "${base} GET /openapi.json"; fi
  if _curl_json "${base}/api/v1/backend"; then _ok "${base} GET /api/v1/backend"; else _fail "${base} GET /api/v1/backend"; fi

  if _curl_json "${base}/metrics"; then _ok "${base} GET /metrics"; else _fail "${base} GET /metrics"; fi
  if _curl_text "${base}/metrics/prometheus"; then _ok "${base} GET /metrics/prometheus"; else _fail "${base} GET /metrics/prometheus"; fi

  if _curl_json "${base}/config"; then _ok "${base} GET /config"; else _fail "${base} GET /config"; fi
  if _curl_json "${base}/config/all"; then _ok "${base} GET /config/all"; else _fail "${base} GET /config/all"; fi
  if _curl_json "${base}/config/reload" -X POST; then _ok "${base} POST /config/reload"; else _fail "${base} POST /config/reload"; fi

  if _curl_json "${base}/api/v1/hotwords"; then _ok "${base} GET /api/v1/hotwords"; else _fail "${base} GET /api/v1/hotwords"; fi
  if _curl_json "${base}/api/v1/hotwords/context"; then _ok "${base} GET /api/v1/hotwords/context"; else _fail "${base} GET /api/v1/hotwords/context"; fi
  if _curl_json "${base}/api/v1/hotwords/reload" -X POST; then _ok "${base} POST /api/v1/hotwords/reload"; else _fail "${base} POST /api/v1/hotwords/reload"; fi
  if _curl_json "${base}/api/v1/hotwords/context/reload" -X POST; then _ok "${base} POST /api/v1/hotwords/context/reload"; else _fail "${base} POST /api/v1/hotwords/context/reload"; fi

  local tmp
  local code

  tmp="$(_tmpfile)"
  code="$(_curl_to_file_timeout "${TRANSCRIBE_TIMEOUT_S}" "${tmp}" "${base}/api/v1/transcribe" \
    -X POST \
    -F "file=@${AUDIO}" \
    -F "with_speaker=false" \
    -F "apply_hotword=true" \
    -F "apply_llm=false" \
  )"
  if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_transcribe_success "${tmp}" >/dev/null 2>&1; then
    _ok "${base} POST /api/v1/transcribe"
  else
    echo "ERROR HTTP ${code}: ${base}/api/v1/transcribe" >&2
    _print_body_head "${tmp}"
    _fail "${base} POST /api/v1/transcribe"
  fi
  rm -f "${tmp}" || true

  tmp="$(_tmpfile)"
  code="$(_curl_to_file_timeout "${TRANSCRIBE_TIMEOUT_S}" "${tmp}" "${base}/api/v1/transcribe/batch" \
    -X POST \
    -F "files=@${AUDIO}" \
    -F "files=@${AUDIO}" \
    -F "with_speaker=false" \
    -F "apply_hotword=true" \
    -F "apply_llm=false" \
  )"
  if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_batch_success "${tmp}" >/dev/null 2>&1; then
    _ok "${base} POST /api/v1/transcribe/batch"
  else
    echo "ERROR HTTP ${code}: ${base}/api/v1/transcribe/batch" >&2
    _print_body_head "${tmp}"
    _fail "${base} POST /api/v1/transcribe/batch"
  fi
  rm -f "${tmp}" || true
}

_test_diarizer() {
  local base="http://localhost:${DIARIZER_PORT}"
  echo ""
  echo "=============================="
  echo "== DIARIZER=${base}"
  echo "=============================="

  if _curl_json "${base}/health"; then _ok "${base} GET /health"; else _fail "${base} GET /health"; fi
  if _curl_json "${base}/openapi.json"; then _ok "${base} GET /openapi.json"; else _fail "${base} GET /openapi.json"; fi

  # Diarizer only accepts WAV container input.
  local wav_tmp
  wav_tmp="$(mktemp -t xiyu_diarizer_XXXXXX.wav)"
  if command -v ffmpeg >/dev/null 2>&1; then
    ffmpeg -nostdin -y -loglevel error -i "${AUDIO}" -ac 1 -ar 16000 -c:a pcm_s16le "${wav_tmp}" || true
  fi

  if [ ! -s "${wav_tmp}" ]; then
    echo "SKIP ${base} POST /api/v1/diarize (ffmpeg not available to generate wav)" >&2
    rm -f "${wav_tmp}" || true
    return 0
  fi

  if curl -fsS -m "${TIMEOUT_S}" -X POST "${base}/api/v1/diarize" \
      -F "file=@${wav_tmp}" \
    | python3 -m json.tool >/dev/null; then
    _ok "${base} POST /api/v1/diarize"
  else
    _fail "${base} POST /api/v1/diarize"
  fi

  rm -f "${wav_tmp}" || true
}

echo "Xiyu smoke test"
echo "- PORTS=${PORTS}"
echo "- REMOTE_ASR_PORTS=${REMOTE_ASR_PORTS} (skip=${SKIP_REMOTE_ASR_CHECKS})"
echo "- AUDIO=${AUDIO}"
echo "- TIMEOUT_S=${TIMEOUT_S}"
echo "- TRANSCRIBE_TIMEOUT_S=${TRANSCRIBE_TIMEOUT_S}"
echo "- REMOTE_ASR_TIMEOUT_S=${REMOTE_ASR_TIMEOUT_S} retries=${REMOTE_ASR_READY_RETRIES} sleep=${REMOTE_ASR_READY_SLEEP_S}s"
echo ""

if [ "${SKIP_REMOTE_ASR_CHECKS}" != "true" ] && [ -n "${REMOTE_ASR_PORTS}" ]; then
  echo "=============================="
  echo "Remote ASR readiness"
  echo "=============================="
  for rp in ${REMOTE_ASR_PORTS}; do
    base="http://localhost:${rp}"
    if _wait_json_ok "${base}/v1/models" "${REMOTE_ASR_READY_RETRIES}" "${REMOTE_ASR_READY_SLEEP_S}" "${REMOTE_ASR_TIMEOUT_S}"; then
      _ok "${base} GET /v1/models"
    else
      _fail "${base} GET /v1/models"
    fi
  done
  echo ""
fi

for p in ${PORTS}; do
  _test_one_base "http://localhost:${p}"
done

if [ -n "${DIARIZER_PORT}" ]; then
  _test_diarizer
fi

echo ""
echo "=============================="
echo "Summary"
echo "=============================="
echo "PASS=${PASS} FAIL=${FAIL}"

if [ "${FAIL}" -gt 0 ]; then
  exit 1
fi

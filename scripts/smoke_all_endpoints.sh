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
#   URL_AUDIO_URL="https://..." Optional public URL for /api/v1/trans/url smoke test
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
URL_AUDIO_URL="${URL_AUDIO_URL:-}"
URL_TASK_POLL_RETRIES="${URL_TASK_POLL_RETRIES:-60}"
URL_TASK_POLL_SLEEP_S="${URL_TASK_POLL_SLEEP_S:-2}"

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

_assert_json_code() {
  # Usage: _assert_json_code JSON_FILE EXPECTED_CODE
  local f="$1"
  local expected="$2"
  python3 - "${f}" "${expected}" <<'PY'
import json
import sys

path = sys.argv[1]
expected = int(sys.argv[2])
with open(path, "r", encoding="utf-8") as fp:
    obj = json.load(fp)

code = obj.get("code")
if code != expected:
    raise SystemExit(f"expected code={expected}, got code={code!r}")
PY
}

_test_one_base() {
  local base="$1"
  local tmp
  local code
  local task_id=""

  echo ""
  echo "=============================="
  echo "== BASE=${base}"
  echo "=============================="

  if _curl_json "${base}/health"; then _ok "${base} GET /health"; else _fail "${base} GET /health"; fi
  if _curl_json "${base}/openapi.json"; then _ok "${base} GET /openapi.json"; else _fail "${base} GET /openapi.json"; fi
  if _curl_json "${base}/api/v1/backend"; then _ok "${base} GET /api/v1/backend"; else _fail "${base} GET /api/v1/backend"; fi
  if _curl_json "${base}/api/v1/backend/targets"; then _ok "${base} GET /api/v1/backend/targets"; else _fail "${base} GET /api/v1/backend/targets"; fi

  if _curl_json "${base}/metrics"; then _ok "${base} GET /metrics"; else _fail "${base} GET /metrics"; fi
  if _curl_text "${base}/metrics/prometheus"; then _ok "${base} GET /metrics/prometheus"; else _fail "${base} GET /metrics/prometheus"; fi

  if _curl_json "${base}/config"; then _ok "${base} GET /config"; else _fail "${base} GET /config"; fi
  if _curl_json "${base}/config/all"; then _ok "${base} GET /config/all"; else _fail "${base} GET /config/all"; fi
  if _curl_json "${base}/config" -X POST -H "Content-Type: application/json" -d '{"updates":{}}'; then _ok "${base} POST /config (no-op)"; else _fail "${base} POST /config (no-op)"; fi
  if _curl_json "${base}/config/reload" -X POST; then _ok "${base} POST /config/reload"; else _fail "${base} POST /config/reload"; fi

  if _curl_json "${base}/api/v1/hotwords"; then _ok "${base} GET /api/v1/hotwords"; else _fail "${base} GET /api/v1/hotwords"; fi
  if _curl_json "${base}/api/v1/hotwords/context"; then _ok "${base} GET /api/v1/hotwords/context"; else _fail "${base} GET /api/v1/hotwords/context"; fi
  if _curl_json "${base}/api/v1/hotwords/rules"; then _ok "${base} GET /api/v1/hotwords/rules"; else _fail "${base} GET /api/v1/hotwords/rules"; fi
  if _curl_json "${base}/api/v1/hotwords/rectify"; then _ok "${base} GET /api/v1/hotwords/rectify"; else _fail "${base} GET /api/v1/hotwords/rectify"; fi
  if _curl_json "${base}/api/v1/hotwords/reload" -X POST; then _ok "${base} POST /api/v1/hotwords/reload"; else _fail "${base} POST /api/v1/hotwords/reload"; fi
  if _curl_json "${base}/api/v1/hotwords/context/reload" -X POST; then _ok "${base} POST /api/v1/hotwords/context/reload"; else _fail "${base} POST /api/v1/hotwords/context/reload"; fi
  if _curl_json "${base}/api/v1/hotwords/rules/reload" -X POST; then _ok "${base} POST /api/v1/hotwords/rules/reload"; else _fail "${base} POST /api/v1/hotwords/rules/reload"; fi
  if _curl_json "${base}/api/v1/hotwords/rectify/reload" -X POST; then _ok "${base} POST /api/v1/hotwords/rectify/reload"; else _fail "${base} POST /api/v1/hotwords/rectify/reload"; fi

  # ------------------------------------------------------------
  # UI stateful actions (save/append/restore)
  # ------------------------------------------------------------

  # Hotwords: update (no-op), append dummy, restore
  local hw_get hw_restore hw_append
  hw_get="$(_tmpfile)"
  code="$(_curl_to_file "${hw_get}" "${base}/api/v1/hotwords")"
  if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${hw_get}" >/dev/null 2>&1; then
    hw_restore="$(_tmpfile)"
    python3 - "${hw_get}" "${hw_restore}" <<'PY'
import json, sys
obj=json.load(open(sys.argv[1],encoding="utf-8"))
hotwords=obj.get("hotwords") or []
if not isinstance(hotwords, list):
    hotwords=[]
json.dump({"hotwords": hotwords}, open(sys.argv[2],"w",encoding="utf-8"), ensure_ascii=False)
PY
    tmp="$(_tmpfile)"
    code="$(_curl_to_file "${tmp}" "${base}/api/v1/hotwords" -X POST -H "Content-Type: application/json" --data-binary @"${hw_restore}")"
    if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_json_code "${tmp}" 0 >/dev/null 2>&1; then
      _ok "${base} POST /api/v1/hotwords (save no-op)"
    else
      _print_body_head "${tmp}"
      _fail "${base} POST /api/v1/hotwords (save no-op)"
    fi
    rm -f "${tmp}" || true

    hw_append="$(_tmpfile)"
    python3 - "${hw_append}" <<'PY'
import json, sys
json.dump({"hotwords": ["__xiyu_smoke_test__"]}, open(sys.argv[1],"w",encoding="utf-8"), ensure_ascii=False)
PY
    tmp="$(_tmpfile)"
    code="$(_curl_to_file "${tmp}" "${base}/api/v1/hotwords/append" -X POST -H "Content-Type: application/json" --data-binary @"${hw_append}")"
    if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_json_code "${tmp}" 0 >/dev/null 2>&1; then
      _ok "${base} POST /api/v1/hotwords/append"
    else
      _print_body_head "${tmp}"
      _fail "${base} POST /api/v1/hotwords/append"
    fi
    rm -f "${tmp}" || true

    # Restore original hotwords (best-effort)
    tmp="$(_tmpfile)"
    code="$(_curl_to_file "${tmp}" "${base}/api/v1/hotwords" -X POST -H "Content-Type: application/json" --data-binary @"${hw_restore}")"
    if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_json_code "${tmp}" 0 >/dev/null 2>&1; then
      _ok "${base} POST /api/v1/hotwords (restore)"
    else
      _print_body_head "${tmp}"
      _fail "${base} POST /api/v1/hotwords (restore)"
    fi
    rm -f "${tmp}" || true

    rm -f "${hw_restore}" "${hw_append}" || true
  else
    _print_body_head "${hw_get}"
    _fail "${base} GET /api/v1/hotwords (for stateful tests)"
  fi
  rm -f "${hw_get}" || true

  # Context hotwords: update (no-op), append dummy, restore
  local chw_get chw_restore chw_append
  chw_get="$(_tmpfile)"
  code="$(_curl_to_file "${chw_get}" "${base}/api/v1/hotwords/context")"
  if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${chw_get}" >/dev/null 2>&1; then
    chw_restore="$(_tmpfile)"
    python3 - "${chw_get}" "${chw_restore}" <<'PY'
import json, sys
obj=json.load(open(sys.argv[1],encoding="utf-8"))
hotwords=obj.get("hotwords") or []
if not isinstance(hotwords, list):
    hotwords=[]
json.dump({"hotwords": hotwords}, open(sys.argv[2],"w",encoding="utf-8"), ensure_ascii=False)
PY
    tmp="$(_tmpfile)"
    code="$(_curl_to_file "${tmp}" "${base}/api/v1/hotwords/context" -X POST -H "Content-Type: application/json" --data-binary @"${chw_restore}")"
    if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_json_code "${tmp}" 0 >/dev/null 2>&1; then
      _ok "${base} POST /api/v1/hotwords/context (save no-op)"
    else
      _print_body_head "${tmp}"
      _fail "${base} POST /api/v1/hotwords/context (save no-op)"
    fi
    rm -f "${tmp}" || true

    chw_append="$(_tmpfile)"
    python3 - "${chw_append}" <<'PY'
import json, sys
json.dump({"hotwords": ["__xiyu_smoke_ctx__"]}, open(sys.argv[1],"w",encoding="utf-8"), ensure_ascii=False)
PY
    tmp="$(_tmpfile)"
    code="$(_curl_to_file "${tmp}" "${base}/api/v1/hotwords/context/append" -X POST -H "Content-Type: application/json" --data-binary @"${chw_append}")"
    if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_json_code "${tmp}" 0 >/dev/null 2>&1; then
      _ok "${base} POST /api/v1/hotwords/context/append"
    else
      _print_body_head "${tmp}"
      _fail "${base} POST /api/v1/hotwords/context/append"
    fi
    rm -f "${tmp}" || true

    tmp="$(_tmpfile)"
    code="$(_curl_to_file "${tmp}" "${base}/api/v1/hotwords/context" -X POST -H "Content-Type: application/json" --data-binary @"${chw_restore}")"
    if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_json_code "${tmp}" 0 >/dev/null 2>&1; then
      _ok "${base} POST /api/v1/hotwords/context (restore)"
    else
      _print_body_head "${tmp}"
      _fail "${base} POST /api/v1/hotwords/context (restore)"
    fi
    rm -f "${tmp}" || true

    rm -f "${chw_restore}" "${chw_append}" || true
  else
    _print_body_head "${chw_get}"
    _fail "${base} GET /api/v1/hotwords/context (for stateful tests)"
  fi
  rm -f "${chw_get}" || true

  # Rules: update (no-op), append comment, restore
  local rules_get rules_restore rules_append
  rules_get="$(_tmpfile)"
  code="$(_curl_to_file "${rules_get}" "${base}/api/v1/hotwords/rules")"
  if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${rules_get}" >/dev/null 2>&1; then
    rules_restore="$(_tmpfile)"
    python3 - "${rules_get}" "${rules_restore}" <<'PY'
import json, sys
obj=json.load(open(sys.argv[1],encoding="utf-8"))
text=obj.get("text") or ""
json.dump({"text": text}, open(sys.argv[2],"w",encoding="utf-8"), ensure_ascii=False)
PY
    tmp="$(_tmpfile)"
    code="$(_curl_to_file "${tmp}" "${base}/api/v1/hotwords/rules" -X POST -H "Content-Type: application/json" --data-binary @"${rules_restore}")"
    if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_json_code "${tmp}" 0 >/dev/null 2>&1; then
      _ok "${base} POST /api/v1/hotwords/rules (save no-op)"
    else
      _print_body_head "${tmp}"
      _fail "${base} POST /api/v1/hotwords/rules (save no-op)"
    fi
    rm -f "${tmp}" || true

    rules_append="$(_tmpfile)"
    python3 - "${rules_append}" <<'PY'
import json, sys
json.dump({"text": "\n# __xiyu_smoke_rules__\n"}, open(sys.argv[1],"w",encoding="utf-8"), ensure_ascii=False)
PY
    tmp="$(_tmpfile)"
    code="$(_curl_to_file "${tmp}" "${base}/api/v1/hotwords/rules/append" -X POST -H "Content-Type: application/json" --data-binary @"${rules_append}")"
    if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_json_code "${tmp}" 0 >/dev/null 2>&1; then
      _ok "${base} POST /api/v1/hotwords/rules/append"
    else
      _print_body_head "${tmp}"
      _fail "${base} POST /api/v1/hotwords/rules/append"
    fi
    rm -f "${tmp}" || true

    tmp="$(_tmpfile)"
    code="$(_curl_to_file "${tmp}" "${base}/api/v1/hotwords/rules" -X POST -H "Content-Type: application/json" --data-binary @"${rules_restore}")"
    if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_json_code "${tmp}" 0 >/dev/null 2>&1; then
      _ok "${base} POST /api/v1/hotwords/rules (restore)"
    else
      _print_body_head "${tmp}"
      _fail "${base} POST /api/v1/hotwords/rules (restore)"
    fi
    rm -f "${tmp}" || true

    rm -f "${rules_restore}" "${rules_append}" || true
  else
    _print_body_head "${rules_get}"
    _fail "${base} GET /api/v1/hotwords/rules (for stateful tests)"
  fi
  rm -f "${rules_get}" || true

  # Rectify: update (no-op), append record, restore
  local rect_get rect_restore rect_append
  rect_get="$(_tmpfile)"
  code="$(_curl_to_file "${rect_get}" "${base}/api/v1/hotwords/rectify")"
  if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${rect_get}" >/dev/null 2>&1; then
    rect_restore="$(_tmpfile)"
    python3 - "${rect_get}" "${rect_restore}" <<'PY'
import json, sys
obj=json.load(open(sys.argv[1],encoding="utf-8"))
text=obj.get("text") or ""
json.dump({"text": text}, open(sys.argv[2],"w",encoding="utf-8"), ensure_ascii=False)
PY
    tmp="$(_tmpfile)"
    code="$(_curl_to_file "${tmp}" "${base}/api/v1/hotwords/rectify" -X POST -H "Content-Type: application/json" --data-binary @"${rect_restore}")"
    if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_json_code "${tmp}" 0 >/dev/null 2>&1; then
      _ok "${base} POST /api/v1/hotwords/rectify (save no-op)"
    else
      _print_body_head "${tmp}"
      _fail "${base} POST /api/v1/hotwords/rectify (save no-op)"
    fi
    rm -f "${tmp}" || true

    rect_append="$(_tmpfile)"
    python3 - "${rect_append}" <<'PY'
import json, sys, time
payload={"wrong": f"__xiyu_smoke_wrong_{int(time.time())}__", "right": "__xiyu_smoke_right__"}
json.dump(payload, open(sys.argv[1],"w",encoding="utf-8"), ensure_ascii=False)
PY
    tmp="$(_tmpfile)"
    code="$(_curl_to_file "${tmp}" "${base}/api/v1/hotwords/rectify/append" -X POST -H "Content-Type: application/json" --data-binary @"${rect_append}")"
    if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_json_code "${tmp}" 0 >/dev/null 2>&1; then
      _ok "${base} POST /api/v1/hotwords/rectify/append"
    else
      _print_body_head "${tmp}"
      _fail "${base} POST /api/v1/hotwords/rectify/append"
    fi
    rm -f "${tmp}" || true

    tmp="$(_tmpfile)"
    code="$(_curl_to_file "${tmp}" "${base}/api/v1/hotwords/rectify" -X POST -H "Content-Type: application/json" --data-binary @"${rect_restore}")"
    if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_json_code "${tmp}" 0 >/dev/null 2>&1; then
      _ok "${base} POST /api/v1/hotwords/rectify (restore)"
    else
      _print_body_head "${tmp}"
      _fail "${base} POST /api/v1/hotwords/rectify (restore)"
    fi
    rm -f "${tmp}" || true

    rm -f "${rect_restore}" "${rect_append}" || true
  else
    _print_body_head "${rect_get}"
    _fail "${base} GET /api/v1/hotwords/rectify (for stateful tests)"
  fi
  rm -f "${rect_get}" || true

  tmp="$(_tmpfile)"
  code="$(_curl_to_file_timeout "${TRANSCRIBE_TIMEOUT_S}" "${tmp}" "${base}/api/v1/transcribe" \
    -X POST \
    -F "file=@${AUDIO}" \
    -F "with_speaker=false" \
    -F "apply_hotword=true" \
    -F "apply_llm=false" \
    -F "target_backend=auto" \
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
    -F "target_backend=auto" \
  )"
  if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_batch_success "${tmp}" >/dev/null 2>&1; then
    _ok "${base} POST /api/v1/transcribe/batch"
  else
    echo "ERROR HTTP ${code}: ${base}/api/v1/transcribe/batch" >&2
    _print_body_head "${tmp}"
    _fail "${base} POST /api/v1/transcribe/batch"
  fi
  rm -f "${tmp}" || true

  # "视频转写" 端点（前端按钮）— 用音频文件做兼容性测试
  tmp="$(_tmpfile)"
  code="$(_curl_to_file_timeout "${TRANSCRIBE_TIMEOUT_S}" "${tmp}" "${base}/api/v1/trans/video" \
    -X POST \
    -F "file=@${AUDIO}" \
    -F "with_speaker=false" \
    -F "apply_hotword=true" \
    -F "apply_llm=false" \
    -F "target_backend=auto" \
  )"
  if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1 && _assert_transcribe_success "${tmp}" >/dev/null 2>&1; then
    _ok "${base} POST /api/v1/trans/video"
  else
    echo "ERROR HTTP ${code}: ${base}/api/v1/trans/video" >&2
    _print_body_head "${tmp}"
    _fail "${base} POST /api/v1/trans/video"
  fi
  rm -f "${tmp}" || true

  # URL 转写（异步）— 默认跳过，除非提供 URL_AUDIO_URL
  if [ -n "${URL_AUDIO_URL}" ]; then
    tmp="$(_tmpfile)"
    code="$(_curl_to_file_timeout "${TIMEOUT_S}" "${tmp}" "${base}/api/v1/trans/url" \
      -X POST \
      -F "audio_url=${URL_AUDIO_URL}" \
      -F "with_speaker=false" \
      -F "apply_hotword=true" \
      -F "apply_llm=false" \
      -F "target_backend=auto" \
    )"
    if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1; then
      # Extract task_id
      task_id="$(python3 - "${tmp}" <<'PY'
import json, sys
obj=json.load(open(sys.argv[1],encoding="utf-8"))
data=obj.get("data") or {}
tid=data.get("task_id") if isinstance(data, dict) else None
print(tid or "")
PY
)"
      if [ -n "${task_id}" ]; then
        _ok "${base} POST /api/v1/trans/url"
      else
        _print_body_head "${tmp}"
        _fail "${base} POST /api/v1/trans/url (missing task_id)"
      fi
    else
      echo "ERROR HTTP ${code}: ${base}/api/v1/trans/url" >&2
      _print_body_head "${tmp}"
      _fail "${base} POST /api/v1/trans/url"
      task_id=""
    fi
    rm -f "${tmp}" || true

    if [ -n "${task_id}" ]; then
      # Poll /api/v1/result until success or error
      for i in $(seq 1 "${URL_TASK_POLL_RETRIES}"); do
        tmp="$(_tmpfile)"
        code="$(_curl_to_file_timeout "${TRANSCRIBE_TIMEOUT_S}" "${tmp}" "${base}/api/v1/result" \
          -X POST \
          -F "task_id=${task_id}" \
          -F "delete=false" \
        )"
        if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ] && python3 -m json.tool <"${tmp}" >/dev/null 2>&1; then
          status="$(python3 - "${tmp}" <<'PY'
import json, sys
obj=json.load(open(sys.argv[1],encoding="utf-8"))
print(obj.get("status") or "")
PY
)"
          if [ "${status}" = "success" ]; then
            _ok "${base} POST /api/v1/result (success)"
            rm -f "${tmp}" || true
            break
          fi
          if [ "${status}" = "error" ]; then
            _print_body_head "${tmp}"
            _fail "${base} POST /api/v1/result (error)"
            rm -f "${tmp}" || true
            break
          fi
        fi
        rm -f "${tmp}" || true
        sleep "${URL_TASK_POLL_SLEEP_S}"
      done
    fi
  else
    echo "SKIP ${base} /api/v1/trans/url (set URL_AUDIO_URL=... to enable)" >&2
  fi
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
echo "- URL_AUDIO_URL=${URL_AUDIO_URL:-<empty>}"
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

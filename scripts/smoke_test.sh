#!/usr/bin/env bash
set -euo pipefail

audio_path="${1:-}"
out_dir="${2:-}"

if [[ -z "${audio_path}" ]]; then
  echo "Usage: $0 /path/to/audio.(wav|mp3|m4a|flac) [out_dir]" >&2
  echo "Example: $0 clip_30s.m4a data/outputs/smoke_$(date +%Y%m%d_%H%M%S)" >&2
  exit 2
fi

if [[ ! -f "${audio_path}" ]]; then
  echo "Audio file not found: ${audio_path}" >&2
  exit 2
fi

if [[ -z "${out_dir}" ]]; then
  out_dir="data/outputs/smoke_$(date +%Y%m%d_%H%M%S)"
fi

mkdir -p "${out_dir}"

declare -a targets=(
  "router 8200"
  "pytorch 8101"
  "onnx 8102"
  "sensevoice 8103"
  "gguf 8104"
  "whisper 8105"
  "qwen3 8201"
  "vibevoice 8202"
  "diarizer 8300"
)

is_healthy() {
  local port="$1"
  curl -fsS --connect-timeout 1 --max-time 2 "http://localhost:${port}/health" >/dev/null 2>&1
}

json_code() {
  local path="$1"
  python3 -c 'import json,sys; obj=json.load(open(sys.argv[1], "r", encoding="utf-8")); print(obj.get("code"))' "${path}"
}

echo "[info] audio=${audio_path}"
echo "[info] out_dir=${out_dir}"

ok_count=0
skip_count=0
fail_count=0

for item in "${targets[@]}"; do
  name="$(awk '{print $1}' <<<"${item}")"
  port="$(awk '{print $2}' <<<"${item}")"

  if ! is_healthy "${port}"; then
    echo "[$(date +%H:%M:%S)] skip ${name} (port ${port}) - /health not reachable"
    skip_count=$((skip_count + 1))
    continue
  fi

  if [[ "${name}" == "diarizer" ]]; then
    echo "[$(date +%H:%M:%S)] ok   ${name} (port ${port}) - /health reachable"
    ok_count=$((ok_count + 1))
    continue
  fi

  url="http://localhost:${port}/api/v1/transcribe"
  out="${out_dir}/${name}.json"

  echo "[$(date +%H:%M:%S)] transcribe ${name} -> ${url}"
  if ! curl -sS -X POST "${url}" \
      -F "file=@${audio_path}" \
      -F "with_speaker=true" \
      -F "apply_hotword=true" \
      -F "apply_llm=false" \
      > "${out}"; then
    echo "[fail] ${name} request failed"
    fail_count=$((fail_count + 1))
    continue
  fi

  code="$(json_code "${out}" || true)"
  bytes="$(wc -c < "${out}" | tr -d '[:space:]')"
  if [[ "${code}" == "0" ]]; then
    echo "[ok]   saved ${out} (${bytes} bytes, code=${code})"
    ok_count=$((ok_count + 1))
  else
    echo "[warn] saved ${out} (${bytes} bytes, code=${code})"
    fail_count=$((fail_count + 1))
  fi
done

echo ""

ensemble_port="8200"
if ! is_healthy "${ensemble_port}"; then
  ensemble_port="8101"
fi

if is_healthy "${ensemble_port}"; then
  ensemble_url="http://localhost:${ensemble_port}/api/v1/transcribe/all"
  ensemble_out="${out_dir}/ensemble_llm.json"
  echo "[$(date +%H:%M:%S)] ensemble (all models + LLM) -> ${ensemble_url}"
  if curl -sS -X POST "${ensemble_url}" \
      -F "file=@${audio_path}" \
      -F "with_speaker=true" \
      -F "apply_hotword=true" \
      -F "apply_llm=true" \
      -F "llm_role=policy_meeting_aggressive" \
      > "${ensemble_out}"; then
    code="$(json_code "${ensemble_out}" || true)"
    bytes="$(wc -c < "${ensemble_out}" | tr -d '[:space:]')"
    echo "[ok]   saved ${ensemble_out} (${bytes} bytes, code=${code})"
  else
    echo "[fail] ensemble request failed"
    fail_count=$((fail_count + 1))
  fi
else
  echo "[$(date +%H:%M:%S)] skip ensemble - no backend reachable (ports 8200/8101)"
  skip_count=$((skip_count + 1))
fi

echo ""
echo "[done] ok=${ok_count} skip=${skip_count} fail=${fail_count} out_dir=${out_dir}"


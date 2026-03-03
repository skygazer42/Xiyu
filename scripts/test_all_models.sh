#!/usr/bin/env bash
set -euo pipefail

audio_path="${1:-}"
out_dir="${2:-}"

if [[ -z "${audio_path}" ]]; then
  echo "Usage: $0 /path/to/audio.(wav|mp3|m4a|flac) [out_dir]" >&2
  echo "Example: $0 20250507_171129.m4a data/outputs/modeltest_$(date +%Y%m%d_%H%M%S)" >&2
  exit 2
fi

if [[ ! -f "${audio_path}" ]]; then
  echo "Audio file not found: ${audio_path}" >&2
  exit 2
fi

if [[ -z "${out_dir}" ]]; then
  out_dir="data/outputs/modeltest_$(date +%Y%m%d_%H%M%S)"
fi

mkdir -p "${out_dir}"

declare -a targets=(
  "pytorch 8101"
  "onnx 8102"
  "sensevoice 8103"
  "gguf 8104"
  "whisper 8105"
  "qwen3 8201"
  "vibevoice 8202"
)

echo "[info] audio=${audio_path}"
echo "[info] out_dir=${out_dir}"

for item in "${targets[@]}"; do
  name="$(awk '{print $1}' <<<"${item}")"
  port="$(awk '{print $2}' <<<"${item}")"
  url="http://localhost:${port}/api/v1/transcribe"

  echo ""
  echo "[$(date +%H:%M:%S)] transcribe ${name} -> ${url}"

  curl -sS -X POST "${url}" \
    -F "file=@${audio_path}" \
    -F "with_speaker=true" \
    -F "apply_hotword=true" \
    -F "apply_llm=false" \
    > "${out_dir}/${name}.json"

  bytes="$(wc -c < "${out_dir}/${name}.json" | tr -d '[:space:]')"
  echo "[ok] saved ${out_dir}/${name}.json (${bytes} bytes)"
done

echo ""
echo "[$(date +%H:%M:%S)] ensemble (all models + LLM) -> http://localhost:8101/api/v1/transcribe/all"

curl -sS -X POST "http://localhost:8101/api/v1/transcribe/all" \
  -F "file=@${audio_path}" \
  -F "with_speaker=true" \
  -F "apply_hotword=true" \
  -F "apply_llm=true" \
  -F "llm_role=policy_meeting" \
  > "${out_dir}/ensemble_llm.json"

bytes="$(wc -c < "${out_dir}/ensemble_llm.json" | tr -d '[:space:]')"
echo "[ok] saved ${out_dir}/ensemble_llm.json (${bytes} bytes)"

echo ""
echo "[done] Outputs under: ${out_dir}"


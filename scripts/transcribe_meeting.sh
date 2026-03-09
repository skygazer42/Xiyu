#!/usr/bin/env bash
set -euo pipefail

audio_path="${1:-}"
base_url="${2:-${BASE_URL:-http://localhost:18200}}"

if [[ -z "${audio_path}" ]]; then
  echo "Usage: $0 /path/to/meeting.wav [base_url]" >&2
  echo "Example: $0 data/benchmark/test.wav http://localhost:18200" >&2
  exit 2
fi

if [[ ! -f "${audio_path}" ]]; then
  echo "Audio file not found: ${audio_path}" >&2
  exit 2
fi

base_url="${base_url%/}"

# Meeting-friendly defaults:
# - with_speaker=true prefers single-pass diarization (router/vibevoice recommended)
# - numeric labels: 说话人1/2/3...
# - turn merge: merge consecutive sentences by same speaker into paragraphs
asr_options='{"speaker":{"label_style":"numeric","turn_merge_enable":true,"turn_merge_gap_ms":800,"turn_merge_min_chars":1},"postprocess":{"punc_restore_enable":true}}'

curl -sS -X POST "${base_url}/api/v1/transcribe" \
  -F "file=@${audio_path}" \
  -F "with_speaker=true" \
  -F "apply_hotword=true" \
  -F "apply_llm=false" \
  -F "asr_options=${asr_options}"

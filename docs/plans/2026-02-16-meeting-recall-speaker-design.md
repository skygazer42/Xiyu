# Meeting/Recall Transcription — Speaker-Friendly Output (Numeric Labels + Turns) — Design

**Date:** 2026-02-16  
**Scope:** Xiyu (`/Users/luke/code/xiyu`)  
**Primary goal:** improve **final transcript accuracy & usability** for meeting/recall transcription where speakers usually **take turns** (overlap is rare), and the UI/business needs **clear speaker separation** like `说话人1/2/3`.

---

## Goal

1) Provide stable, readable meeting transcripts with **explicit speaker separation**:
   - `sentences[]` stay as the low-level timeline output
   - add a higher-level `speaker_turns[]` output for human reading / downstream formatting
   - support `说话人1/2/3` (numeric) labels as a first-class output style

2) Avoid common diarization pitfalls that *reduce* accuracy in practice:
   - chunking long audio for text merge (`text_accu`) is good for accuracy, but **chunking + diarization** often breaks speaker consistency

3) Keep API backward compatible:
   - existing `text`, `sentences`, `transcript` fields remain
   - new fields are optional additions

---

## Non-goals (v1)

- Perfect overlap speech handling (simultaneous talk) — not the dominant case for this user.
- Introducing a new diarization model (e.g. pyannote) with extra runtime deps/auth (can be future work).
- Training/fine-tuning any ASR model weights.

---

## Current State (Xiyu)

- Speaker diarization is supported only by some backends:
  - **PyTorchBackend** supports speaker via FunASR `spk_model` (`cam++`) and provides `sentence_info[].spk`.
  - **VibeVoice remote** returns segment JSON containing `Speaker ID`.
  - ONNX/SenseVoice/Qwen3 remote do **not** support speaker diarization.
- HTTP transcription endpoints call `TranscriptionEngine.transcribe_auto_async(...)` which:
  - routes long audio to `transcribe_long_audio(...)` chunking for better accuracy (`text_accu` etc.)
  - chunking with speaker currently produces *potentially inconsistent* speaker IDs across chunks.
- `SpeakerLabeler` formats speakers as `说话人甲/乙/丙/...` by default.

---

## Key Problems (Meeting/Recall)

### Problem A: Speaker stability vs long-audio chunking

Chunking improves text accuracy (dedupe/reconcile boundaries), but for diarization:
- each chunk is diarized independently, speaker IDs may restart or drift
- merging `sentences[]` across chunks can mistakenly merge different people under the same label

For this user’s meeting style (turn-taking), the more accurate result is usually:
- run diarization on the full audio once (even if slower), then format cleanly.

### Problem B: Output readability

Sentence-level output is too granular for “meeting record” reading.
Users need speaker turns like:

```
说话人1: ……（一段）
说话人2: ……（一段）
```

---

## Chosen Approach (v1)

### 1) Routing rule: `with_speaker=true` disables chunking by default

In `transcribe_auto_async(...)`:
- if `with_speaker=true`: always call `transcribe_async(...)` (single pass)
- if `with_speaker=false`: keep current long-audio auto chunking behavior

Rationale:
- prioritize speaker consistency for meetings
- user’s priority is **accuracy over time**

We will still allow explicit override later (future): force chunking even with speaker.

### 2) Numeric speaker labels

Extend `SpeakerLabeler` to support label styles:
- `zh` (existing): `说话人甲/乙/丙...`
- `numeric` (new): `说话人1/2/3...`

Default remains `zh` for backward compatibility, but meeting preset can use `numeric`.

### 3) Add `speaker_turns[]` (merged turns)

Add a small, dependency-light turn builder that:
- takes labeled `sentences[]` (with `speaker_id`, timestamps)
- merges consecutive sentences by the same speaker when the gap is small
- returns `speaker_turns[]` for downstream formatting / UI

Suggested fields (v1):
- `speaker`: `"说话人1"`
- `speaker_id`: `0`
- `start`: ms
- `end`: ms
- `text`: merged text
- optional: `sentence_count`

### 4) Transcript formatting uses `speaker_turns[]`

`transcript` becomes a readable “meeting minutes” view:
- one line per turn (or paragraph)
- include timestamps optionally

This keeps the old `transcript` key but improves quality for the meeting scenario.

---

## API Changes

`POST /api/v1/transcribe` response:
- keep: `text`, `text_accu`, `sentences`, `transcript`, `raw_text`
- add: `speaker_turns?: SpeakerTurn[]` (optional; only present when `with_speaker=true` and diarization produced sentences)

Backends without speaker support:
- We must avoid **silent model switching** in per-port deployments.
- New behavior is configurable via `speaker_unsupported_behavior`:
  - `error`: return **400** when `with_speaker=true` and backend does not support it (strict)
  - `fallback`: run PyTorch backend for diarization (legacy single-container convenience)
  - `ignore`: keep the chosen backend, but treat request as `with_speaker=false` (best for multi-port UX)

---

## Config / Per-request Tuning

Add request-level tuning via `asr_options.speaker`:
- `label_style`: `"zh" | "numeric"`
- `turn_merge_enable`: bool
- `turn_merge_gap_ms`: int
- `turn_merge_min_chars`: int (optional; prevent tiny turns)

Defaults:
- `turn_merge_enable=true` when `with_speaker=true`
- `label_style` default remains `zh` unless explicitly set

---

## Testing Strategy

We avoid importing FastAPI/Pydantic in unit tests (local env may not have deps).

- Add pure-python tests for:
  - numeric label mapping
  - speaker turn merging behavior (gap threshold, speaker change boundaries)
  - `asr_options` parsing for new `speaker` section

---

## Future Work (not in v1)

- “Speaker-safe chunking”: global diarization or cross-chunk speaker embedding matching.
- Overlapped speech handling (separation or multi-speaker decoding).
- Meeting-specific punctuation restoration defaults (punc restore + ITN + paragraphing).

# Meeting ASR Accuracy — Boundary Stability (A) + Proper Nouns/Acronyms (D) — Design

**Date:** 2026-02-20  
**Scope:** Xiyu (`/Users/luke/code/xiyu`)  
**Primary goal:** improve *final* meeting/video transcription accuracy for:

- **A. 边界漏字/断词** (chunk/turn boundaries)
- **D. 专有名词 / 英文缩写识别不准**

This design assumes a common “turn-taking meeting” pattern (speakers mostly alternate; overlap is rare).

---

## Current state (already in repo)

Xiyu already has:

1) **Multi-backend deployment** (per-port, frontend selects Base URL):
   - `docker-compose.models.yml` provides per-model Xiyu containers (pytorch/onnx/sensevoice/gguf/whisper/qwen3/…).

2) **External diarizer strategy** (pyannote service):
   - When `with_speaker=true` and `SPEAKER_EXTERNAL_DIARIZER_ENABLE=true`, Xiyu calls `xiyu-diarizer`,
     then slices PCM16LE by diarizer turns and transcribes each slice with the selected backend.
   - Output includes `speaker_turns` and numeric labels (`说话人1/2/3…`) when requested.

3) **Long-audio chunking accuracy foundation** (non-speaker path):
   - Silence-aware chunk splitting + overlap.
   - Robust `merge_by_text` to reduce repetition on chunk overlaps.
   - Optional boundary reconcile for non-speaker long audio.

4) **Context hotwords file exists**:
   - `data/hotwords/hotwords-context.txt` is loaded at startup and preferred for *injection* hotwords.
   - The *forced* hotword list remains `data/hotwords/hotwords.txt`.

---

## Problems to solve

### Problem A — Boundary word loss / truncation in meeting mode

When using external diarizer, Xiyu currently needs to enforce a “max turn duration” (to avoid remote backend timeouts).
The existing strategy can create **hard time splits** inside a speaker turn with *no overlap merge*.

This causes the exact failure mode the user reports:

- A word is cut at the boundary → the tail syllable/word is missing
- Or the boundary produces stutter/partial tokens that degrade readability

### Problem D — Proper nouns and acronyms

Two main sub-problems:

1) **No first-class UI/API for context hotwords**, even though the engine supports them.
   Users end up putting everything into the “forced hotwords” list, which increases false positives.

2) Acronyms are common in meetings:
   - `A I`, `V S Code`, `G P T 4`, `K 8 s` …
   Xiyu already has an optional acronym merge postprocess step, but it can be improved
   (especially letter+digit sequences).

---

## Goals

1) **Boundary stability for meeting output**
   - Avoid mid-word cuts when a diarizer turn must be split for backend constraints.
   - Keep `speaker_turns` readable and stable.

2) **Better term accuracy**
   - Provide a safe “context hotwords” management path (injection-only).
   - Improve acronym merging behavior with tests.

3) **Keep the existing deployment model**
   - “One container = one backend” remains the guiding principle.
   - No router requirement; frontend chooses Base URL.

---

## Non-goals (v1)

- Perfect overlapped-speech attribution.
- Speaker identity across meetings (“speaker 1 is always Alice”).
- Forced alignment / word-level timestamps (kept as Phase 3 optional work).
- Training / fine-tuning any model weights.

---

## Proposed approach (v1)

### 1) Turn-internal chunking for external diarizer path (fix Problem A)

Keep diarizer providing speaker turns (time ranges). Then:

For each diarized speaker turn:

- If duration is small → single backend call (current behavior)
- If duration exceeds a configured budget → **split inside the turn using silence-aware chunking + overlap**,
  then **merge transcripts by text** (same algorithm used by long-audio chunking)

Key details:

- **Split strategy:** reuse `AudioChunker` splitting logic (prefer silence; fallback to time).
- **Overlap:** use overlap to protect boundary words.
- **Merge:** use `merge_by_text` to remove duplicated overlap text.
- **Corrections/postprocess:** defer to *after* merging (to avoid breaking overlap matching).
- **Timestamps:** keep the original diarizer turn `[start,end]` for the merged result (avoid overlapping timestamps).

This keeps speaker stability while increasing text accuracy for long turns.

### 2) Context hotwords API + frontend UI (improve Problem D safely)

Add a separate set of endpoints for context hotwords:

- `GET /api/v1/hotwords/context`
- `POST /api/v1/hotwords/context` (replace)
- `POST /api/v1/hotwords/context/append`
- `POST /api/v1/hotwords/context/reload`

These update the engine’s context hotwords list (`hotwords-context.txt` on disk) and allow users to manage
proper nouns without forcing replacements.

### 3) Acronym merge improvements (Problem D)

Improve the existing optional acronym merge postprocess to handle:

- letter+digit sequences (`G P T 4` → `GPT4`)
- common meeting tokens (`K 8 s` → `K8s`)

Keep it opt-in via config / per-request `asr_options.postprocess.acronym_merge_enable=true`.

### 4) Phase 3 (optional): timestamp precision tools

If/when needed:

- Add a `faster-whisper` backend option for word timestamps + VAD filter.
- Add exports (SRT/VTT) based on `speaker_turns` (paragraph-level), optionally word-level later.

This stays optional so the default experience remains lightweight.

---

## Success criteria

Using the same meeting recording:

- Fewer (or no) obvious mid-word truncations at boundaries inside a long speaker turn.
- Proper nouns are more likely correct when listed in context hotwords.
- Acronyms are formatted more naturally when enabled (without harming normal text).

---

## Testing strategy

Prioritize unit tests and mock-based integration tests:

- External diarizer engine tests using a mocked backend:
  - Long turn that must be split produces a complete merged transcript (no boundary loss).
  - Ensure overlap merge removes duplication.
- API tests for new context hotwords endpoints.
- Postprocess tests for acronym merge (ensure no false merges on normal Chinese text).

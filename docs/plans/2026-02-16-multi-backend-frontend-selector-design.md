# Multi-Backend (Per-Port) Frontend Selector + Speaker Unsupported Behavior — Design

**Date:** 2026-02-16  
**Scope:** Xiyu (`/Users/luke/code/xiyu`)  
**Primary scenario:** One host runs **multiple Xiyu containers** (different ASR backends/models), each exposed on a **different port**. The frontend lets the user pick which backend to use per transcription.

---

## Goal

1) **Frontend selectable backend**
- User can choose `http://localhost:8101` / `8102` / `8103` / `8104` / `8201` / `8202`… (or a custom base URL).
- All frontend API calls use the chosen base URL (transcribe, config, hotwords, metrics).

2) **Speaker diarization is “best-effort”**
- If the chosen backend supports diarization → return `speaker`/`speaker_id`, `speaker_turns`, and meeting-style `transcript`.
- If the chosen backend does **not** support diarization → do **not** error; just return normal text-only output.

3) **No silent model switching**
- In per-port mode, a port == a backend/model.
- We do **not** want to silently run a different backend just because `with_speaker=true`.

---

## Non-goals (v1)

- Building a “router” as the primary entrypoint (load balancing / auto backend switching).
- Adding a new diarization model dependency (e.g. pyannote) for backends that don’t support speakers.
- Perfect overlapped speech handling.

---

## Current Constraints / Reality Check

- “Speaker diarization” is usually **not** part of a pure ASR model; it’s either:
  - an ASR backend that returns speaker tags (FunASR + `spk_model`, VibeVoice segments), or
  - a separate diarization pipeline.
- Some Xiyu backends already report `supports_speaker=false` (e.g. Qwen3 remote ASR wrapper).

---

## Chosen Approach

### 1) Backend capability discovery endpoint

Add a lightweight endpoint:

- `GET /api/v1/backend`

Returns:
- configured `ASR_BACKEND`
- `backend.get_info()` (safe metadata)
- capabilities booleans (at least `supports_speaker`, `supports_streaming`, `supports_hotwords`)
- the effective “unsupported speaker” behavior (see below)

Frontend uses this to show:
- which backend is currently selected
- whether the backend supports diarization

### 2) Make unsupported-speaker behavior configurable

Add `speaker_unsupported_behavior` with 3 options:

- `error` (strict): `with_speaker=true` on unsupported backend → HTTP 400
- `fallback` (legacy): unsupported backend → run PyTorch backend for speaker support
- `ignore` (per-port mode): unsupported backend → treat request as `with_speaker=false` and keep the chosen backend

**Default behavior remains strict** (to preserve correctness for single-backend deployments), but
`docker-compose.models.yml` will set `SPEAKER_UNSUPPORTED_BEHAVIOR=ignore` for the per-port workflow.

### 3) Frontend backend selector + speaker UX

- Add a “Backend / Base URL” selector in the Transcribe options card.
- Persist to localStorage so it survives reload.
- Probe `/health` and `/api/v1/backend` for the selected base URL.
- If `supports_speaker=false`:
  - keep transcription working
  - show a clear note: “该后端不支持说话人识别，将忽略说话人开关”

### 4) Meeting-friendly defaults

When the user enables `with_speaker=true`, the frontend should send:

- `asr_options.speaker.label_style = "numeric"` → output `说话人1/2/3...`

This matches the “turn-taking meeting” reading style.

---

## Testing Strategy

- Engine unit tests (pure Python, no model deps):
  - unsupported speaker + `ignore` does not raise and does not add speaker fields
  - `transcribe_auto_async` routes long audio correctly when speaker is ignored
- API tests:
  - `GET /api/v1/backend` returns expected keys and capability flags
- Frontend sanity:
  - `yarn lint`
  - `yarn build` (typecheck + build)

# Multi-Backend Frontend Selector + Speaker Unsupported Behavior — Implementation Plan (20 Tasks)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Support a multi-container, per-port Xiyu deployment where the **frontend selects the backend base URL**, and speaker diarization is **best-effort** (unsupported backends **ignore** speaker instead of erroring or silently switching models).

**Architecture:** Frontend stores a selected `baseURL` → all API calls use that base URL → frontend probes `/api/v1/backend` to show capabilities → backend enforces `speaker_unsupported_behavior` (`error|fallback|ignore`) consistently across engine paths.

**Tech Stack:** Python/FastAPI backend, pytest; Vite/React frontend, axios, zustand.

---

## Task 01: Add `speaker_unsupported_behavior` to Settings (effective property)

**Files:**
- Modify: `src/config.py`
- Modify: `.env.example`

**Step 1: Write failing engine tests (drives behavior, not config parsing)**  
Add tests from Task 02 first (so this task stays config-only).

**Step 2: Implement settings field**
- Add `speaker_unsupported_behavior: Optional[Literal["error","fallback","ignore"]] = None`
- Add `speaker_unsupported_behavior_effective` property:
  - if set → use it
  - else → map legacy `speaker_strict_backend` to `error|fallback`

**Step 3: Compile check**
Run: `python3 -m compileall -q src`

**Step 4: Commit**
Run:
```bash
git add src/config.py .env.example
git commit -m "config: add speaker_unsupported_behavior setting"
```

---

## Task 02: Engine — ignore unsupported speaker in `transcribe()` (TDD)

**Files:**
- Modify: `src/core/engine.py`
- Test: `tests/test_engine.py`

**Step 1: Write failing test**
Add a new test asserting:
- backend `supports_speaker=False`
- `speaker_unsupported_behavior=ignore`
- calling `engine.transcribe(..., with_speaker=True)` does not raise
- backend is called with `with_speaker=False`
- response `sentences[]` contain no `speaker` keys

**Step 2: Run test (expect FAIL)**
Run: `pytest -q tests/test_engine.py -k unsupported`

**Step 3: Implement minimal code**
- In `transcribe()`: if unsupported + behavior `ignore` → set `with_speaker=False` and proceed using configured backend.
- Keep existing strict/fallback behaviors for other modes.

**Step 4: Run tests (expect PASS)**
Run: `pytest -q tests/test_engine.py -k unsupported`

**Step 5: Commit**
```bash
git add src/core/engine.py tests/test_engine.py
git commit -m "feat(engine): ignore speaker when backend unsupported"
```

---

## Task 03: Engine — ignore unsupported speaker in `transcribe_async()`

**Files:**
- Modify: `src/core/engine.py`
- Test: `tests/test_engine.py` (add async variant if needed)

**Steps:**
1. Add failing test for `transcribe_async(..., with_speaker=True)` in ignore mode.
2. Implement the same behavior as sync path.
3. Run: `pytest -q tests/test_engine.py`
4. Commit.

---

## Task 04: Engine — `transcribe_auto_async` should still chunk when speaker is ignored

**Files:**
- Modify: `src/core/engine.py`
- Test: `tests/test_engine.py`

**Step 1: Write failing test**
- backend `supports_speaker=False`
- ignore behavior
- long audio bytes
- `with_speaker=True` should route to `transcribe_long_audio` (not direct async)

**Step 2: Run test (expect FAIL)**
Run: `pytest -q tests/test_engine.py -k ignored`

**Step 3: Implement**
- In `transcribe_auto_async`: resolve unsupported-speaker ignore **before** the `if with_speaker: return ...` early return.

**Step 4: Run tests**
Run: `pytest -q tests/test_engine.py`

**Step 5: Commit**
Commit changes.

---

## Task 05: Engine — `transcribe_long_audio` ignores unsupported speaker (no per-chunk fallback)

**Files:**
- Modify: `src/core/engine.py`
- Test: `tests/test_engine.py` or `tests/test_audio_*` (minimal coverage)

**Steps:**
1. Add a focused unit test (mock backend) verifying ignore mode leads to `with_speaker=False` for chunk transcribes.
2. Implement: resolve ignore early in `transcribe_long_audio`.
3. Run: `pytest -q tests/test_engine.py`
4. Commit.

---

## Task 06: Compose defaults — per-port mode uses ignore behavior

**Files:**
- Modify: `docker-compose.models.yml`

**Steps:**
1. Add env var to `x-xiyu-env`:
   - `SPEAKER_UNSUPPORTED_BEHAVIOR: ${SPEAKER_UNSUPPORTED_BEHAVIOR:-ignore}`
2. Ensure it applies to all services.
3. Commit.

---

## Task 07: API schema — add backend info response models

**Files:**
- Modify: `src/api/schemas.py`

**Steps:**
1. Add `BackendCapabilities` + `BackendInfoResponse` models.
2. Compile: `python3 -m compileall -q src`
3. Commit.

---

## Task 08: API route — `GET /api/v1/backend`

**Files:**
- Create: `src/api/routes/backend.py`
- Modify: `src/api/routes/__init__.py`
- Test: `tests/test_api_http.py`

**Steps:**
1. Write failing API test calling `/api/v1/backend`.
2. Implement route returning `BackendInfoResponse`.
3. Run: `pytest -q tests/test_api_http.py -k backend`
4. Commit.

---

## Task 09: Frontend types — sync with backend response schema

**Files:**
- Modify: `frontend/src/lib/api/types.ts`

**Steps:**
1. Add `SpeakerTurn` type + `speaker_turns?: SpeakerTurn[] | null`.
2. Add `text_accu?: string | null`.
3. Add `BackendInfoResponse` type.
4. Commit.

---

## Task 10: Frontend API client — allow dynamic baseURL

**Files:**
- Modify: `frontend/src/lib/api/client.ts`

**Steps:**
1. Export `setApiBaseUrl()` and `getApiBaseUrl()`.
2. Ensure `apiClient.defaults.baseURL` updates.
3. Commit.

---

## Task 11: Frontend store — persist selected backend baseURL

**Files:**
- Create: `frontend/src/stores/backendStore.ts`
- Modify: `frontend/src/stores/index.ts`

**Steps:**
1. Add zustand store with persist.
2. On rehydrate, call `setApiBaseUrl(...)`.
3. Commit.

---

## Task 12: Frontend API — add `getBackendInfo()`

**Files:**
- Create: `frontend/src/lib/api/backend.ts`
- Modify: `frontend/src/lib/api/index.ts`

**Steps:**
1. Implement `getBackendInfo()` calling `/api/v1/backend`.
2. Commit.

---

## Task 13: Frontend UI — backend selector + capability probe

**Files:**
- Modify: `frontend/src/components/transcribe/TranscribeOptions.tsx`

**Steps:**
1. Add a backend selector dropdown (pre-filled localhost ports).
2. Fetch `/api/v1/backend` for selected baseURL and show:
   - backend name/type
   - `supports_speaker` badge
3. If not supported, show note: speaker will be ignored.
4. Commit.

---

## Task 14: Frontend — send `asr_options` speaker label style by default

**Files:**
- Modify: `frontend/src/lib/api/transcribe.ts`
- Modify: `frontend/src/lib/api/types.ts` (options)

**Steps:**
1. Extend `TranscribeOptions` with `speaker_label_style?: "numeric" | "zh"`.
2. When `with_speaker=true`, include:
   - `asr_options={"speaker":{"label_style":speaker_label_style||"numeric"}}`
3. Ensure all upload endpoints send `asr_options` (audio/batch/video).
4. Commit.

---

## Task 15: Frontend UI — add label style selection (numeric vs zh)

**Files:**
- Modify: `frontend/src/components/transcribe/TranscribeOptions.tsx`
- Modify: `frontend/src/stores/transcriptionStore.ts`

**Steps:**
1. Add `speaker_label_style` to store default options (default `numeric`).
2. Add a select shown when speaker enabled.
3. Commit.

---

## Task 16: Transcript view — add a “Turns” view if `speaker_turns` exists

**Files:**
- Modify: `frontend/src/components/transcript/TranscriptView.tsx`

**Steps:**
1. Add a new tab “说话人段落”.
2. Render `speaker_turns` with speaker + text blocks.
3. Commit.

---

## Task 17: Docs — describe multi-port + backend selector usage

**Files:**
- Modify: `README.md`

**Include:**
- multi-port compose ports table
- “frontend backend selector” workflow
- speaker unsupported behavior + env var

---

## Task 18: Backend + frontend verification (Python)

**Steps:**
1. Run: `python3 -m compileall -q src tests`
2. Run: `pytest -q tests/test_engine.py tests/test_api_http.py`

---

## Task 19: Frontend verification

**Steps:**
1. Run: `cd frontend && yarn install` (if needed)
2. Run: `cd frontend && yarn lint`
3. Run: `cd frontend && yarn build`

---

## Task 20: Merge to `main` + push

**Steps:**
1. Merge branch into main.
2. Run final verification.
3. Push `origin/main`.

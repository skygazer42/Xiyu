# Speaker Fallback Diarization (Qwen3-friendly) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 当后端不支持说话人识别（如 Qwen3-ASR）但用户开启 `with_speaker=true` 时，若配置启用 fallback diarization，则调用一个辅助 Xiyu 服务获取说话人分段，并按 turn 切片用原后端转写；失败自动回退到现有 ignore 行为。

**Architecture:** Engine 在 `transcribe_async/transcribe_auto_async` 中检测 `supports_speaker`；启用 fallback 时先用 httpx 调用外部 Xiyu 获取 `sentences`（带 start/end/speaker_id），合并为 turn 后逐段调用 primary backend 得到文本，最终返回 `sentences/speaker_turns/transcript`。

**Tech Stack:** FastAPI + httpx；Python；pytest；docker-compose（多容器多端口）。

---

### Task 01: Add Settings keys + env example (TDD)

**Files:**
- Modify: `src/config.py`
- Modify: `.env.example`
- Test: `tests/test_settings_speaker_fallback.py`

**Step 1: Write failing test**

```python
def test_settings_speaker_fallback_env(monkeypatch):
    from src.config import Settings
    monkeypatch.setenv("SPEAKER_FALLBACK_DIARIZATION_ENABLE", "true")
    monkeypatch.setenv("SPEAKER_FALLBACK_DIARIZATION_BASE_URL", "http://example:8000")
    s = Settings()
    assert s.speaker_fallback_diarization_enable is True
    assert s.speaker_fallback_diarization_base_url == "http://example:8000"
```

**Step 2: Run test to verify it fails**

Run: `/Users/luke/code/xiyu/.venv/bin/pytest -q tests/test_settings_speaker_fallback.py`  
Expected: FAIL (missing fields)

**Step 3: Implement minimal settings**
- Add fields (defaults off) e.g.:
  - `speaker_fallback_diarization_enable: bool = False`
  - `speaker_fallback_diarization_base_url: str = ""`
  - `speaker_fallback_diarization_timeout_s: float = 30.0`
  - `speaker_fallback_max_turn_duration_s: float = 25.0`
  - `speaker_fallback_max_turns: int = 200`
- Document env vars in `.env.example`

**Step 4: Run test to verify it passes**

Run: `/Users/luke/code/xiyu/.venv/bin/pytest -q tests/test_settings_speaker_fallback.py`  
Expected: PASS

**Step 5: Commit**

```bash
git add src/config.py .env.example tests/test_settings_speaker_fallback.py
git commit -m "config: add speaker fallback diarization settings"
```

---

### Task 02: Backend info should expose fallback diarization capability

**Files:**
- Modify: `src/api/schemas.py`
- Modify: `src/api/routes/backend.py`

**Step 1: Write failing test**
- Extend existing API http tests or add new one that checks `/api/v1/backend` response includes:
  - `capabilities.supports_speaker_fallback` (bool)

**Step 2: Implement**
- Add optional field on `BackendCapabilities`:
  - `supports_speaker_fallback: bool = False`
- Compute it as: `settings.speaker_fallback_diarization_enable`
  AND `not backend.supports_speaker` (best-effort)

**Step 3: Verify**

Run: `/Users/luke/code/xiyu/.venv/bin/pytest -q tests/test_api_http.py`  
Expected: PASS

**Step 4: Commit**

```bash
git add src/api/schemas.py src/api/routes/backend.py tests/test_api_http.py
git commit -m "api: expose speaker fallback capability"
```

---

### Task 03: Frontend types for backend fallback capability

**Files:**
- Modify: `frontend/src/lib/api/types.ts`

**Steps:**
1. Add `supports_speaker_fallback?: boolean` to `BackendCapabilities`
2. Run: `cd frontend && npm run build`
3. Commit: `git commit -m "frontend: backend capabilities include speaker fallback"`

---

### Task 04: Frontend badge copy for “fallback 可用”

**Files:**
- Modify: `frontend/src/components/transcribe/TranscribeOptions.tsx`

**Steps:**
1. When `supports_speaker=false` and `supports_speaker_fallback=true` show badge e.g. “fallback 说话人”
2. Keep current warning text, but clarify: “后端原生不支持；已启用 fallback（若可用则输出说话人）”
3. Build: `cd frontend && npm run build`
4. Commit

---

### Task 05: Add PCM normalization helper (WAV→PCM16LE) (TDD)

**Files:**
- Create: `src/core/audio/slice.py`
- Test: `tests/test_audio_slice_pcm.py`

**Step 1: Write failing test**
- Provide a tiny WAV bytes (or generate via `wave`) and ensure it converts to pcm16le bytes of expected length.

**Step 2: Implement**
- `ensure_pcm16le_16k_mono_bytes(audio_input) -> bytes`
  - If WAV bytes, decode via `wav_bytes_to_float32` then `float32_to_pcm16le_bytes`
  - Else treat bytes as PCM

**Step 3: Run**
Run: `/Users/luke/code/xiyu/.venv/bin/pytest -q tests/test_audio_slice_pcm.py`

**Step 4: Commit**

---

### Task 06: Add PCM slicing helper (ms→bytes) (TDD)

**Files:**
- Modify: `src/core/audio/slice.py`
- Test: `tests/test_audio_slice_pcm.py`

**Steps:**
1. Add `slice_pcm16le(pcm: bytes, start_ms: int, end_ms: int, sample_rate=16000) -> bytes`
2. Test boundary clamping + even-byte alignment
3. Commit

---

### Task 07: Add helper to call fallback Xiyu diarization endpoint (TDD)

**Files:**
- Modify: `src/core/engine.py`
- Test: `tests/test_speaker_fallback_diarization.py`

**Steps:**
1. Write failing test mocking `httpx.AsyncClient.post` returning a minimal diarization response with `sentences` and `speaker_id`
2. Implement an internal async helper `_fetch_fallback_diarization_sentences(...)`
3. Ensure request sets `with_speaker=true`, disables hotword/llm, and passes `asr_options` with label_style
4. Commit

---

### Task 08: Implement fallback diarization transcription pipeline (TDD)

**Files:**
- Modify: `src/core/engine.py`
- Test: `tests/test_speaker_fallback_diarization.py`

**Steps:**
1. Write failing test:
   - Mock primary backend: `supports_speaker=False`, `transcribe()` returns `"text": "..."` (no sentence_info needed)
   - Mock fallback diarization http call returning 2 speakers with timestamps
   - Assert engine returns `sentences` including `speaker` and `speaker_id`, and `transcript` contains “说话人1”
2. Implement:
   - Convert audio_input → pcm bytes (for slicing)
   - Build turns via `build_speaker_turns`
   - Enforce limits: max turns + max turn duration
   - For each turn: slice pcm and call backend.transcribe(with_speaker=False)
3. Run the test and iterate to green
4. Commit

---

### Task 09: Failure fallback behavior (TDD)

**Files:**
- Modify: `src/core/engine.py`
- Test: `tests/test_speaker_fallback_diarization.py`

**Steps:**
1. Add test where fallback diarization call raises timeout/error
2. Assert request degrades to ignore speaker (no `speaker_turns`, no `transcript`) but still returns normal `text`
3. Commit

---

### Task 10: Wire into `transcribe_async` speaker-unsupported path

**Files:**
- Modify: `src/core/engine.py`

**Steps:**
1. Adjust logic:
   - If `with_speaker` requested and backend unsupported:
     - If `speaker_fallback_diarization_enable`: try fallback pipeline first
     - Else existing behavior (ignore/error/fallback)
2. Run: `/Users/luke/code/xiyu/.venv/bin/pytest -q tests/test_engine.py tests/test_speaker_fallback_diarization.py`
3. Commit

---

### Task 11: Ensure `transcribe_auto_async` does not preemptively ignore speaker when fallback enabled

**Files:**
- Modify: `src/core/engine.py`
- Test: `tests/test_engine.py`

**Steps:**
1. Add a focused test verifying auto router keeps `with_speaker=true` when fallback enabled
2. Implement minimal change
3. Commit

---

### Task 12: Compose ergonomics (optional but useful)

**Files:**
- Modify: `docker-compose.models.yml`
- Modify: `.env.example`

**Steps:**
1. Add env vars to `xiyu-qwen3` service (default off via `${...:-false}`):
   - `SPEAKER_FALLBACK_DIARIZATION_ENABLE`
   - `SPEAKER_FALLBACK_DIARIZATION_BASE_URL=http://xiyu-pytorch:8000`
2. Document recommendation: start `--profile pytorch --profile qwen3` to get diarization
3. Commit

---

### Task 13: Docs update (README)

**Files:**
- Modify: `README.md`

**Steps:**
1. Add a short “Qwen3 说话人（fallback）” section with env example
2. Mention failure falls back to normal transcription
3. Commit

---

### Task 14: Python verification (fresh)

Run:
- `/Users/luke/code/xiyu/.venv/bin/python -m compileall -q src tests`
- `/Users/luke/code/xiyu/.venv/bin/pytest -q`

Expected: exit 0

---

### Task 15: Frontend verification (fresh)

Run:
- `cd frontend && npm ci`
- `cd frontend && npm run build`

Expected: exit 0 (engine warning about Node version is acceptable if build succeeds)

---

### Task 16: Merge to main + push

Steps:
1. Fast-forward merge branch into `main`
2. Re-run Task 14/15 on `main`
3. `git push origin main`

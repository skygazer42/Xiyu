# ASR Per-Request `asr_options` Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a validated `asr_options` JSON form field to Xiyu HTTP transcription APIs to tune preprocessing/chunking/backend/postprocess per request (without restarting, and without mutating global settings).

**Architecture:** Parse `asr_options` once in the API route → apply `preprocess` overrides while decoding upload → pass the parsed dict to the engine → engine derives per-request chunker + post-processor + backend kwargs.

**Tech Stack:** FastAPI, Pydantic v2, numpy, existing `AudioPreprocessor` / `AudioChunker` / `TextPostProcessor`.

---

### Task 01: Add `asr_options` parsing + validation helper

**Files:**
- Create: `src/api/asr_options.py`
- Test: `tests/test_api_http.py` (new test cases)

**Step 1: Write the failing test**

Add a test that sends `asr_options="{bad json"` and expects `400` with a useful error.

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_api_http.py::test_transcribe_asr_options_invalid_json`  
Expected: FAIL (route currently ignores `asr_options` and returns 200 / or 422).

**Step 3: Write minimal implementation**

Implement:
- `parse_asr_options(asr_options_str: str | None) -> dict | None`
- Validation rules:
  - JSON must decode to an object (dict)
  - Only allow top-level keys: `preprocess`, `chunking`, `backend`, `postprocess`, `debug`
  - Inside each section: allowlist keys + type checks (bool/number/string/list)

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_api_http.py::test_transcribe_asr_options_invalid_json`  
Expected: PASS with HTTP 400.

**Step 5: Commit**

```bash
git add src/api/asr_options.py tests/test_api_http.py
git commit -m "feat(api): parse and validate asr_options"
```

---

### Task 02: Plumb `asr_options` through `/api/v1/transcribe` + batch routes

**Files:**
- Modify: `src/api/routes/transcribe.py`
- Test: `tests/test_api_http.py`

**Step 1: Write the failing test**

Patch `transcription_engine.transcribe_auto_async` with `AsyncMock` and assert it’s called with a parsed dict:

- input: `asr_options='{"chunking":{"max_workers":1}}'`
- expectation: `mock_transcribe_auto_async.assert_awaited()` includes `asr_options={"chunking": {"max_workers": 1}}`

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_api_http.py::test_transcribe_asr_options_is_passed_to_engine`  
Expected: FAIL (route doesn’t accept field / doesn’t pass it through).

**Step 3: Implement minimal route wiring**

- Add `asr_options: Optional[str] = Form(default=None, ...)`
- Parse via `src.api.asr_options.parse_asr_options(...)`
- Pass:
  - `preprocess_options` into `process_audio_file(...)`
  - full `asr_options` into `transcription_engine.transcribe_auto_async(...)`

**Step 4: Run tests**

Run: `pytest -q tests/test_api_http.py`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/api/routes/transcribe.py tests/test_api_http.py
git commit -m "feat(api): support asr_options for transcribe endpoints"
```

---

### Task 03: Apply `preprocess` overrides during upload decode

**Files:**
- Modify: `src/api/dependencies.py`
- Modify: `src/core/audio/preprocessor.py` (add `remove_dc_offset` toggle)
- Test: `tests/test_audio_preprocess_dc_offset.py` (extend) or add `tests/test_api_preprocess_overrides.py`

**Step 1: Write failing unit test**

- Create a synthetic waveform with DC offset (e.g. +0.2 mean).
- With overrides `{ "preprocess": { "remove_dc_offset": false } }`, ensure mean is not forced to ~0.
- With default behavior, ensure mean is ~0 after preprocessing.

**Step 2: Implement**

- Add `remove_dc_offset: bool = True` to `AudioPreprocessor.__init__`
- In `process()`, only apply DC offset removal when enabled.
- In `process_audio_file(file, preprocess_options=...)`:
  - If overrides exist, create a request-scoped `AudioPreprocessor` using settings defaults + overrides.
  - If effective config disables all steps, skip extra float conversion.

**Step 3: Run tests**

Run: `pytest -q tests/test_audio_preprocess_dc_offset.py`  
Expected: PASS.

**Step 4: Commit**

```bash
git add src/api/dependencies.py src/core/audio/preprocessor.py tests/test_audio_preprocess_dc_offset.py
git commit -m "feat(audio): per-request preprocessing overrides via asr_options"
```

---

### Task 04: Engine support for `chunking` / `postprocess` / `backend` overrides

**Files:**
- Modify: `src/core/engine.py`
- Test: `tests/test_api_http.py` (mock-based) + new unit tests where feasible

**Step 1: Write failing tests**

1) If `asr_options.chunking.max_chunk_duration_s` is small and input is longer, ensure engine chooses chunked path.
2) If `asr_options.postprocess.itn_enable=false`, ensure post-processing doesn’t run for this request (mock `TextPostProcessor.process`).

**Step 2: Implement minimal support**

- Extend public engine APIs:
  - `transcribe_auto_async(..., asr_options: Optional[dict] = None, backend_kwargs: Optional[dict] = None)`
  - `transcribe(..., asr_options: Optional[dict] = None, backend_kwargs: Optional[dict] = None)`
  - `transcribe_async(..., asr_options: Optional[dict] = None, backend_kwargs: Optional[dict] = None)`
  - `transcribe_long_audio(..., asr_options: Optional[dict] = None, backend_kwargs: Optional[dict] = None)`
- Derive request-scoped helpers:
  - `chunker = AudioChunker(**overrides)` or default `self.audio_chunker`
  - `post_processor = TextPostProcessor(PostProcessorSettings(...))` or default `self.post_processor`
  - `backend_kwargs = asr_options.get("backend", {})` after filtering reserved keys (`input`, `hotword`, `cache`, `is_final`)
- Ensure overrides are not forwarded into backend via `**kwargs` accidentally.

**Step 3: Run tests**

Run: `pytest -q tests/test_api_http.py`  
Expected: PASS.

**Step 4: Commit**

```bash
git add src/core/engine.py tests/test_api_http.py
git commit -m "feat(engine): apply asr_options for chunking/backend/postprocess"
```

---

### Task 05: Document `asr_options` usage (curl examples)

**Files:**
- Modify: `README.md`

**Step 1: Add examples**

Add a short section under “API / 多模型按需启动” showing:

- Disable normalization for one request
- Force smaller chunk size + larger overlap_chars for a noisy long file

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document asr_options request tuning"
```

# Meeting ASR Accuracy — Boundary Stability (A) + Proper Nouns/Acronyms (D) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve meeting/video transcription *final text accuracy* by fixing diarized-turn boundary issues (A) and improving proper-noun/acronym handling (D), while keeping speaker separation stable (`说话人1/2/3…`).

**Architecture:** When `with_speaker=true` and `SPEAKER_EXTERNAL_DIARIZER_ENABLE=true`, Xiyu uses the external diarizer to produce speaker turns. For long turns, Xiyu will **chunk داخل the turn with overlap** and then **merge chunk transcripts by text** (`merge_by_text`) before applying hotword/rule/postprocess corrections. Context hotwords become first-class via dedicated API + frontend UI.

**Tech Stack:** Python (FastAPI), `AudioChunker`, `merge_by_text`, React (frontend), TanStack Query.

---

## Why these tasks (quick research notes)

Meeting ASR projects typically converge on the same primitives:
- VAD/silence-aware segmentation to avoid mid-word cuts
- overlap + robust text merge to prevent boundary truncation/duplication
- diarization as a separate service (pyannote) when the ASR model doesn’t output speakers
- “prompt/hotwords/context” as the safest lever for proper nouns

We already have these primitives in Xiyu; this plan wires them into the **external diarizer path** and exposes **context hotwords** properly.

---

### Task 01: Add failing test — external diarizer long turn must merge overlap text

**Files:**
- Modify: `tests/test_external_diarizer_engine.py`

**Step 1: Write the failing test**

Add a new test (mock backend + mocked diarizer segments) that forces a single long turn which will be chunked into 2 calls that contain overlapped duplicate text:

```python
def test_external_diarizer_long_turn_chunks_and_dedupes_overlap(mock_model_manager, monkeypatch):
    monkeypatch.setattr(engine_mod.settings, "speaker_unsupported_behavior", "ignore", raising=False)
    monkeypatch.setattr(engine_mod.settings, "speaker_external_diarizer_enable", True, raising=False)
    monkeypatch.setattr(engine_mod.settings, "speaker_external_diarizer_base_url", "http://diar:8000", raising=False)
    monkeypatch.setattr(engine_mod.settings, "speaker_external_diarizer_max_turn_duration_s", 25.0, raising=False)

    async def fake_fetch(*args, **kwargs):
        return [{"spk": 0, "start": 0, "end": 60000}]

    # 60 seconds of PCM16LE @16kHz mono
    audio_bytes = b"\x00" * (60 * 16000 * 2)

    mock_model_manager.backend.transcribe.side_effect = [
        {"text": "你好世界", "sentence_info": []},
        {"text": "世界再见", "sentence_info": []},
    ]

    with patch.object(engine_mod, "fetch_diarizer_segments", new=fake_fetch):
        engine = engine_mod.TranscriptionEngine()
        out = asyncio.run(
            engine.transcribe_async(
                audio_bytes,
                with_speaker=True,
                apply_hotword=False,
                apply_llm=False,
                asr_options={"speaker": {"label_style": "numeric"}},
            )
        )

    assert out["sentences"][0]["speaker"] == "说话人1"
    assert out["sentences"][0]["text"] == "你好世界再见"
    assert out["text"] == "你好世界再见"
    assert mock_model_manager.backend.transcribe.call_count == 2
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_engine.py -k long_turn -v`  
Expected: FAIL (today it concatenates or returns duplicated text / wrong sentence count).

**Step 3: No implementation yet (stay RED)**

**Step 4: Commit**

```bash
git add tests/test_external_diarizer_engine.py
git commit -m "test: external diarizer long turn overlap merge (red)"
```

---

### Task 02: Implement turn-internal chunking + merge_by_text in external diarizer path

**Files:**
- Modify: `src/core/engine.py`

**Step 1: Implement minimal code**

In `TranscriptionEngine._transcribe_with_external_diarizer`:
- For each diarized turn, if duration exceeds the configured budget, split the *turn audio* using `AudioChunker` (overlap enabled).
- Transcribe each chunk with the backend.
- Merge chunk transcripts with `merge_by_text` (use a conservative `overlap_chars`, e.g. 20).
- Apply hotword/rules/postprocess **after** merge.
- Emit **one sentence per diarizer turn** (use diarizer turn start/end to avoid overlapping timestamps).

**Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_engine.py -k long_turn -v`  
Expected: PASS.

**Step 3: Run full diarizer unit tests**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_engine.py tests/test_external_diarizer_engine_sync.py`  
Expected: PASS.

**Step 4: Commit**

```bash
git add src/core/engine.py
git commit -m "engine: chunk + merge inside diarized turns"
```

---

### Task 03: Add guard test — enforce `SPEAKER_EXTERNAL_DIARIZER_MAX_TURNS` by total ASR calls

**Files:**
- Create: `tests/test_external_diarizer_turn_chunk_limits.py`

**Step 1: Write failing test**

Create `tests/test_external_diarizer_turn_chunk_limits.py`:

```python
import asyncio
from unittest.mock import MagicMock, patch

import pytest
import src.core.engine as engine_mod


@pytest.fixture
def mock_backend(monkeypatch):
    backend = MagicMock()
    backend.get_info.return_value = {"name": "Stub", "type": "stub"}
    backend.supports_speaker = False
    backend.supports_hotwords = False
    backend.supports_streaming = False
    backend.transcribe.return_value = {"text": "x", "sentence_info": []}
    with patch.object(engine_mod, "model_manager") as mm:
        mm.backend = backend
        yield backend


def test_external_diarizer_respects_max_turns_by_chunk_calls(mock_backend, monkeypatch):
    monkeypatch.setattr(engine_mod.settings, "speaker_external_diarizer_enable", True, raising=False)
    monkeypatch.setattr(engine_mod.settings, "speaker_external_diarizer_base_url", "http://diar:8000", raising=False)
    monkeypatch.setattr(engine_mod.settings, "speaker_external_diarizer_max_turn_duration_s", 25.0, raising=False)
    monkeypatch.setattr(engine_mod.settings, "speaker_external_diarizer_max_turns", 1, raising=False)

    async def fake_fetch(*args, **kwargs):
        # One long segment that will require >1 chunk call.
        return [{"spk": 0, "start": 0, "end": 60000}]

    audio_bytes = b"\x00" * (60 * 16000 * 2)
    with patch.object(engine_mod, "fetch_diarizer_segments", new=fake_fetch):
        engine = engine_mod.TranscriptionEngine()
        out = asyncio.run(engine.transcribe_async(audio_bytes, with_speaker=True, apply_hotword=False))

    # Should fall back to non-speaker path due to external diarizer returning None
    # (max_turns guard) and backend not supporting speaker.
    assert out["sentences"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_turn_chunk_limits.py -v`  
Expected: FAIL (guard not enforced by chunk-call count).

**Step 3: Commit**

```bash
git add tests/test_external_diarizer_turn_chunk_limits.py
git commit -m "test: external diarizer max_turns counts chunk calls (red)"
```

---

### Task 04: Implement `max_turns` guard based on total chunk calls

**Files:**
- Modify: `src/core/engine.py`

**Step 1: Implement minimal code**

Before running ASR calls in `_transcribe_with_external_diarizer`, compute how many chunk calls will be required:
- For each diarized turn:
  - if short: +1
  - if long: estimate via chunker split count
- If total > `speaker_external_diarizer_max_turns`: return `None` (so caller falls back).

**Step 2: Run tests**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_turn_chunk_limits.py -v`  
Expected: PASS.

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_engine.py`  
Expected: PASS.

**Step 3: Commit**

```bash
git add src/core/engine.py
git commit -m "engine: enforce diarizer max_turns by chunk calls"
```

---

### Task 05: Add context hotwords API tests (GET)

**Files:**
- Modify: `tests/test_api_hotwords.py`

**Step 1: Write failing test**

Add:

```python
def test_get_context_hotwords(client):
    resp = client.get("/api/v1/hotwords/context")
    assert resp.status_code == 200
    data = resp.json()
    assert "hotwords" in data
    assert "count" in data
```

**Step 2: Run**

Run: `.venv/bin/python -m pytest -q tests/test_api_hotwords.py -k context -v`  
Expected: FAIL (404).

**Step 3: Commit**

```bash
git add tests/test_api_hotwords.py
git commit -m "test: context hotwords GET endpoint (red)"
```

---

### Task 06: Implement context hotwords API endpoints (GET/POST/append/reload)

**Files:**
- Modify: `src/api/routes/hotwords.py`

**Step 1: Implement minimal endpoints**

Add:
- `GET /api/v1/hotwords/context`
- `POST /api/v1/hotwords/context` (replace)
- `POST /api/v1/hotwords/context/append`
- `POST /api/v1/hotwords/context/reload`

They should call:
- `transcription_engine.update_context_hotwords([...])`
- `transcription_engine.load_context_hotwords()` for reload

**Step 2: Add/extend tests**

In `tests/test_api_hotwords.py`, add POST tests mirroring the forced hotwords ones.

**Step 3: Run**

Run: `.venv/bin/python -m pytest -q tests/test_api_hotwords.py -v`  
Expected: PASS.

**Step 4: Commit**

```bash
git add src/api/routes/hotwords.py tests/test_api_hotwords.py
git commit -m "api: add context hotwords endpoints"
```

---

### Task 07: Add frontend API client functions for context hotwords

**Files:**
- Modify: `frontend/src/lib/api/types.ts`
- Modify: `frontend/src/lib/api/hotwords.ts`

**Step 1: Update types**

Add new response types (can reuse existing `HotwordsListResponse` / `HotwordsUpdateResponse`).

**Step 2: Add API functions**

Add:
- `getContextHotwords()`
- `updateContextHotwords(hotwords: string[])`
- `appendContextHotwords(hotwords: string[])`
- `reloadContextHotwords()`

**Step 3: Build**

Run: `cd frontend && npm run build`  
Expected: PASS.

**Step 4: Commit**

```bash
git add frontend/src/lib/api/types.ts frontend/src/lib/api/hotwords.ts
git commit -m "frontend: add context hotwords API client"
```

---

### Task 08: Add Hotwords UI — forced vs context tabs

**Files:**
- Modify: `frontend/src/pages/HotwordsPage.tsx`

**Step 1: Implement UI**

Add a tab switch:
- “强制热词（纠错）” → existing behavior
- “上下文热词（注入提示）” → uses new context endpoints

Keep the same editor + search + list layout for both.

**Step 2: Build**

Run: `cd frontend && npm run build`  
Expected: PASS.

**Step 3: Commit**

```bash
git add frontend/src/pages/HotwordsPage.tsx
git commit -m "frontend: add context hotwords tab"
```

---

### Task 09: Add failing tests for acronym letter+digit merging

**Files:**
- Modify: `tests/test_postprocess_spoken_punc_acronyms.py`

**Step 1: Add failing test**

```python
def test_acronym_merge_with_digits():
    pp = _processor(acronym_merge_enable=True)
    assert pp.process("Q W E N 3") == "QWEN3"
    assert pp.process("H 2 O") == "H2O"
    assert pp.process("R 2 D 2") == "R2D2"
```

**Step 2: Run**

Run: `.venv/bin/python -m pytest -q tests/test_postprocess_spoken_punc_acronyms.py -k digits -v`  
Expected: FAIL.

**Step 3: Commit**

```bash
git add tests/test_postprocess_spoken_punc_acronyms.py
git commit -m "test: acronym merge supports digits (red)"
```

---

### Task 10: Implement acronym merge regex update (letters + digits)

**Files:**
- Modify: `src/core/text_processor/post_processor.py`

**Step 1: Update regex**

Change the compiled regex so it:
- still only merges 1-char tokens separated by whitespace
- requires the **first token is a letter**
- allows subsequent tokens to be `[A-Za-z0-9]`

**Step 2: Run**

Run: `.venv/bin/python -m pytest -q tests/test_postprocess_spoken_punc_acronyms.py -v`  
Expected: PASS.

**Step 3: Commit**

```bash
git add src/core/text_processor/post_processor.py
git commit -m "postprocess: merge acronyms with digits"
```

---

### Task 11: Add safety test — do not merge digit-only sequences

**Files:**
- Modify: `tests/test_postprocess_spoken_punc_acronyms.py`

**Step 1: Add test**

```python
def test_acronym_merge_does_not_merge_digit_only_sequences():
    pp = _processor(acronym_merge_enable=True)
    assert pp.process("2 0 2 6") == "2 0 2 6"
```

**Step 2: Run**

Run: `.venv/bin/python -m pytest -q tests/test_postprocess_spoken_punc_acronyms.py -k digit_only -v`  
Expected: PASS.

**Step 3: Commit**

```bash
git add tests/test_postprocess_spoken_punc_acronyms.py
git commit -m "test: acronym merge avoids digit-only merges"
```

---

### Task 12: Add backend docs — context hotwords + meeting mode tips

**Files:**
- Modify: `README.md`

**Step 1: Document**

Add a section describing:
- forced hotwords vs context hotwords
- recommended workflow for meeting domain terms
- diarizer knobs: `DIARIZER_*` speaker bounds, `SPEAKER_EXTERNAL_DIARIZER_MAX_TURN_DURATION_S`

**Step 2: Quick smoke**

Run: `rg -n \"hotwords/context\" README.md`  
Expected: shows the new endpoints.

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: describe context hotwords + meeting tips"
```

---

### Task 13: Add optional UI helper — “会议模式” asr_options template button

**Files:**
- Modify: `frontend/src/components/transcribe/TranscribeOptions.tsx`

**Step 1: Implement**

Add a small button near “高级 ASR options” to apply a JSON template suitable for meetings, e.g.:
- enable acronym merge
- enable spacing (optional)
- chunking overlap increase (only if `with_speaker=true` path uses it)

**Step 2: Build**

Run: `cd frontend && npm run build`  
Expected: PASS.

**Step 3: Commit**

```bash
git add frontend/src/components/transcribe/TranscribeOptions.tsx
git commit -m "frontend: add meeting-mode ASR options template"
```

---

### Task 14: Add regression tests — existing external diarizer tests still pass

**Files:**
- None (verification only)

**Step 1: Run focused test suite**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_external_diarizer_engine.py \
  tests/test_external_diarizer_engine_sync.py \
  tests/test_api_hotwords.py \
  tests/test_postprocess_spoken_punc_acronyms.py
```

Expected: PASS.

**Step 2: Commit**

No commit (verification only).

---

### Task 15: Add regression tests — run core unit suite (fast)

**Files:**
- None (verification only)

**Step 1: Run**

Run: `.venv/bin/python -m pytest -q`  
Expected: PASS.

---

### Task 16: Update `.env.example` to include new context hotwords notes (optional)

**Files:**
- Modify: `.env.example`

**Step 1: Document**

Add a small note for:
- `hotwords-context.txt` usage
- recommendation: keep forced list small; put domain terms in context list

**Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: clarify context hotwords usage"
```

---

### Task 17: Add docker docs — model caches are mounted volumes (no image bloat)

**Files:**
- Modify: `README.md`

**Step 1: Document**

Mention that:
- FunASR models use `model-cache` volume
- HuggingFace models (diarizer / remote backends) use `huggingface-cache`
- Whisper weights are cached under `/app/data/models/whisper` (mounted)

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: explain model cache volume mounts"
```

---

### Task 18: (Optional) Add settings for external-diarizer turn chunk overlap/strategy

**Files:**
- Modify: `src/config.py`
- Modify: `src/core/engine.py`
- Test: `tests/test_external_diarizer_engine.py`

**Step 1: Add test**

Add a test that sets overlap to 0 and asserts duplication appears (proves knob works).

**Step 2: Implement**

Add settings:
- `speaker_external_diarizer_turn_overlap_s` (default 0.5)
- `speaker_external_diarizer_turn_chunk_strategy` (default "silence")

**Step 3: Run**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_engine.py -v`  
Expected: PASS.

**Step 4: Commit**

```bash
git add src/config.py src/core/engine.py tests/test_external_diarizer_engine.py
git commit -m "config: tune external diarizer turn chunking"
```

---

### Task 19: (Optional) Add export improvements for speaker_turns (SRT/VTT paragraphs)

**Files:**
- Modify: `frontend/src/components/transcript/ExportMenu.tsx`

**Step 1: Implement**

Ensure exports prefer `speaker_turns` when present, and include speaker label per paragraph.

**Step 2: Build**

Run: `cd frontend && npm run build`  
Expected: PASS.

**Step 3: Commit**

```bash
git add frontend/src/components/transcript/ExportMenu.tsx
git commit -m "frontend: improve SRT/VTT export for speaker turns"
```

---

### Task 20: Push to `main`

**Files:**
- None

**Step 1: Push**

Run: `git push origin main`

Expected: remote `main` updated.

---

## Execution choice

Plan complete and saved to `docs/plans/2026-02-20-meeting-asr-boundary-terms-implementation-plan.md`.

Two execution options:

1. **Subagent-Driven (this session)** — dispatch a fresh subagent per task, review between tasks  
2. **Parallel Session (separate)** — open new session with `superpowers:executing-plans` and run task-by-task

Pick one and I’ll start executing.

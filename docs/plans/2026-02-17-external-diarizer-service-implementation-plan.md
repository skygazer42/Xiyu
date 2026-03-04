# External Diarizer Service (pyannote) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an external diarization service (`xiyu-diarizer`) and make Xiyu produce consistent `speaker_turns` for *all* ASR backends by routing `with_speaker=true` through the external diarizer first, with safe fallback behavior.

**Architecture:** When `with_speaker=true` and external diarizer is enabled, Xiyu calls `POST /api/v1/diarize` to fetch speaker segments → merges segments into turns → slices PCM16LE by turn → transcribes each turn with the selected ASR backend → formats `speaker_turns` + `transcript`. If diarizer fails: fall back to native speaker if supported, else ignore speaker.

**Tech Stack:** Python, FastAPI, httpx, numpy, Docker/Compose, HuggingFace cache volumes, (diarizer) pyannote.audio.

---

## Status snapshot (already on main)

- `with_speaker=true` outputs `speaker_turns` and numeric labels (engine + frontend).
- Existing “speaker fallback diarization” exists (helper Xiyu service), but is only for unsupported backends.
- Multi-backend per-port deployment exists: `docker-compose.models.yml`.

This plan adds a **new external diarizer service** and a **forced external speaker strategy** (opt-in via settings).

---

### Task 01: Add external diarizer settings to `Settings`

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_external_diarizer_settings.py`

**Step 1: Write the failing test**

Create `tests/test_external_diarizer_settings.py`:

```python
def test_settings_parse_external_diarizer_env(monkeypatch):
    monkeypatch.setenv("SPEAKER_EXTERNAL_DIARIZER_ENABLE", "true")
    monkeypatch.setenv("SPEAKER_EXTERNAL_DIARIZER_BASE_URL", "http://diarizer:8000")
    monkeypatch.setenv("SPEAKER_EXTERNAL_DIARIZER_TIMEOUT_S", "12.5")
    from src.config import Settings
    s = Settings()
    assert s.speaker_external_diarizer_enable is True
    assert s.speaker_external_diarizer_base_url == "http://diarizer:8000"
    assert s.speaker_external_diarizer_timeout_s == 12.5
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_settings.py`  
Expected: FAIL (unknown fields / AttributeError).

**Step 3: Implement minimal settings**

In `src/config.py`, add:
- `speaker_external_diarizer_enable: bool = False`
- `speaker_external_diarizer_base_url: str = ""`
- `speaker_external_diarizer_timeout_s: float = 30.0`
- `speaker_external_diarizer_max_turn_duration_s: float = 25.0`
- `speaker_external_diarizer_max_turns: int = 200`

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_settings.py`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/config.py tests/test_external_diarizer_settings.py
git commit -m "config: add external diarizer settings"
```

---

### Task 02: Define diarizer API schema + segment normalization helper (pure python)

**Files:**
- Create: `src/core/speaker/external_diarizer_types.py`
- Create: `src/core/speaker/external_diarizer_normalize.py`
- Test: `tests/test_external_diarizer_normalize.py`

**Step 1: Write the failing test**

Create `tests/test_external_diarizer_normalize.py`:

```python
from src.core.speaker.external_diarizer_normalize import normalize_segments

def test_normalize_segments_sorts_clamps_and_drops_invalid():
    raw = [
        {"spk": 1, "start": 2000, "end": 1000},  # invalid (end < start) -> drop
        {"spk": 0, "start": -5, "end": 10},      # clamp start to 0
        {"spk": 0, "start": 10, "end": 20},
    ]
    segs = normalize_segments(raw, duration_ms=15)
    assert segs == [
        {"spk": 0, "start": 0, "end": 10},
        {"spk": 0, "start": 10, "end": 15},
    ]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_normalize.py`  
Expected: FAIL (module missing).

**Step 3: Implement minimal code**

- `normalize_segments(raw_segments, duration_ms)`:
  - accept list of dict-like
  - coerce ints
  - clamp to `[0, duration_ms]` when duration is known
  - drop segments with `end <= start`
  - sort by `(start, end, spk)`
  - return list of `{"spk": int, "start": int, "end": int}`

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_normalize.py`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/core/speaker/external_diarizer_types.py src/core/speaker/external_diarizer_normalize.py tests/test_external_diarizer_normalize.py
git commit -m "core: normalize external diarizer segments"
```

---

### Task 03: Implement external diarizer HTTP client (httpx, async)

**Files:**
- Create: `src/core/speaker/external_diarizer_client.py`
- Test: `tests/test_external_diarizer_client.py`

**Step 1: Write the failing test**

Create `tests/test_external_diarizer_client.py`:

```python
import json
import pytest
from unittest.mock import AsyncMock, patch

import httpx

from src.core.speaker.external_diarizer_client import fetch_diarizer_segments

@pytest.mark.asyncio
async def test_fetch_diarizer_segments_parses_segments():
    class FakeResp:
        def raise_for_status(self): return None
        def json(self):
            return {"segments": [{"spk": 0, "start": 0, "end": 1000}]}

    async def fake_post(self, url, data=None, files=None):
        assert url.endswith("/api/v1/diarize")
        return FakeResp()

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        segs = await fetch_diarizer_segments(
            base_url="http://diarizer:8000",
            wav_bytes=b"RIFF....",
            timeout_s=5.0,
        )
    assert segs == [{"spk": 0, "start": 0, "end": 1000}]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_client.py`  
Expected: FAIL (module missing).

**Step 3: Implement minimal client**

In `src/core/speaker/external_diarizer_client.py`:
- build `files={"file":("audio.wav", wav_bytes, "audio/wav")}`
- `await client.post(f"{base_url}/api/v1/diarize", files=files)`
- validate response JSON; return `segments` list (raw, normalization happens elsewhere)

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_client.py`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/core/speaker/external_diarizer_client.py tests/test_external_diarizer_client.py
git commit -m "core: add external diarizer http client"
```

---

### Task 04: Extend backend capabilities discovery for external diarizer strategy

**Files:**
- Modify: `src/api/schemas.py`
- Modify: `src/api/routes/backend.py`
- Modify: `tests/test_api_http.py`
- Modify: `frontend/src/lib/api/types.ts`
- Modify: `frontend/src/components/transcribe/TranscribeOptions.tsx`

**Step 1: Write failing tests**

Update `tests/test_api_http.py` `test_backend_info_endpoint` to expect new fields:

```python
caps = data["capabilities"]
assert "supports_speaker_external" in caps
assert "speaker_strategy" in caps
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_api_http.py::test_backend_info_endpoint`  
Expected: FAIL (missing fields).

**Step 3: Implement minimal schema + route changes**

- In `src/api/schemas.py` `BackendCapabilities` add:
  - `supports_speaker_external: bool = False`
  - `speaker_strategy: Literal["none","native","external","fallback","ignore"] = "none"`
- In `src/api/routes/backend.py` compute:
  - external enabled: `settings.speaker_external_diarizer_enable` and base_url non-empty
  - `supports_speaker_external = external_enabled`
  - `speaker_strategy`:
    - `"external"` if external_enabled
    - else `"native"` if backend.supports_speaker
    - else `"fallback"` if supports_speaker_fallback
    - else `"none"`
- Keep existing fields.

**Step 4: Run tests + TypeScript build**

Run: `.venv/bin/python -m pytest -q tests/test_api_http.py::test_backend_info_endpoint`  
Expected: PASS.

Run (frontend): `cd frontend && npm test` (or `npm run build` if no tests)  
Expected: build succeeds after type updates.

**Step 5: Commit**

```bash
git add src/api/schemas.py src/api/routes/backend.py tests/test_api_http.py frontend/src/lib/api/types.ts frontend/src/components/transcribe/TranscribeOptions.tsx
git commit -m "api+frontend: expose external diarizer capability"
```

---

### Task 05: Add engine helper to build turns from external diarizer segments

**Files:**
- Create: `src/core/speaker/external_diarizer_turns.py`
- Test: `tests/test_external_diarizer_turns.py`

**Step 1: Write failing test**

Create `tests/test_external_diarizer_turns.py`:

```python
from src.core.speaker.external_diarizer_turns import segments_to_turns

def test_segments_to_turns_merges_by_speaker_and_gap():
    segs = [
        {"spk": 0, "start": 0, "end": 1000},
        {"spk": 0, "start": 1100, "end": 2000},
        {"spk": 1, "start": 2100, "end": 3000},
    ]
    turns = segments_to_turns(segs, gap_ms=200)
    assert len(turns) == 2
    assert turns[0]["speaker_id"] == 0 and turns[0]["start"] == 0 and turns[0]["end"] == 2000
    assert turns[1]["speaker_id"] == 1
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_turns.py`  
Expected: FAIL.

**Step 3: Implement minimal helper**

- Convert segments into sentence-like dicts `{spk,start,end,text:""}`
- Use `SpeakerLabeler(label_style=...)` to set `speaker/speaker_id`
- Use `build_speaker_turns(..., min_chars=0)` to merge
- Return turns with empty text (filled later by ASR).

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_turns.py`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/core/speaker/external_diarizer_turns.py tests/test_external_diarizer_turns.py
git commit -m "core: turn builder for external diarizer segments"
```

---

### Task 06: Implement `_transcribe_with_external_diarizer(...)` (async) in engine (TDD)

**Files:**
- Modify: `src/core/engine.py`
- Test: `tests/test_external_diarizer_engine.py`

**Step 1: Write failing test**

Create `tests/test_external_diarizer_engine.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

@pytest.mark.asyncio
async def test_transcribe_async_with_external_diarizer_builds_turns(monkeypatch):
    import src.core.engine as engine_mod
    from src.core.engine import TranscriptionEngine

    mock_backend = MagicMock()
    mock_backend.supports_speaker = False
    mock_backend.transcribe.return_value = {"text": "你好", "sentence_info": []}
    monkeypatch.setattr(engine_mod.model_manager, "backend", mock_backend, raising=False)

    monkeypatch.setattr(engine_mod.settings, "speaker_external_diarizer_enable", True, raising=False)
    monkeypatch.setattr(engine_mod.settings, "speaker_external_diarizer_base_url", "http://diarizer:8000", raising=False)

    async def fake_fetch(*args, **kwargs):
        return [{"spk": 0, "start": 0, "end": 1000}]

    with patch("src.core.engine.fetch_diarizer_segments", new=fake_fetch):
        engine = TranscriptionEngine()
        out = await engine.transcribe_async(b\"\\x00\" * (2 * 16000), with_speaker=True)

    assert out.get("speaker_turns")
    assert out["speaker_turns"][0]["speaker"] in ("说话人1", "说话人甲")
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_engine.py`  
Expected: FAIL (symbol missing / behavior missing).

**Step 3: Implement minimal engine helper**

In `src/core/engine.py`:
- import helpers: `fetch_diarizer_segments`, `normalize_segments`, `segments_to_turns`
- ensure PCM16LE bytes: `ensure_pcm16le_16k_mono_bytes(audio_input)`
- convert to wav for diarizer upload: `pcm16le_to_wav_bytes(...)`
- fetch + normalize segments (use duration derived from PCM length)
- build turns and enforce max_turns / max_turn_duration splitting
- slice PCM turn by turn (`slice_pcm16le`) and call backend.transcribe(with_speaker=False)
- build output `sentences` and `speaker_turns` from turns with text

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_engine.py`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/core/engine.py tests/test_external_diarizer_engine.py
git commit -m "engine: add external diarizer speaker pipeline"
```

---

### Task 07: Integrate external diarizer into `transcribe_async` routing (forced when enabled)

**Files:**
- Modify: `src/core/engine.py`
- Modify: `tests/test_engine.py`

**Step 1: Write failing tests**

In `tests/test_engine.py`, add:
- when `with_speaker=true` and external diarizer enabled, engine attempts it even if backend supports speaker.

**Step 2: Run tests to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_engine.py -k external`  
Expected: FAIL.

**Step 3: Implement**

At the start of `transcribe_async`:
- if `with_speaker` and external diarizer enabled + base_url configured:
  - try `_transcribe_with_external_diarizer(...)`
  - if success: return
  - on exception: log warning and continue routing per failure policy

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest -q tests/test_engine.py`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/core/engine.py tests/test_engine.py
git commit -m "engine: route with_speaker via external diarizer when enabled"
```

---

### Task 08: Add failure fallback behavior (external diarizer → native → ignore)

**Files:**
- Modify: `src/core/engine.py`
- Test: `tests/test_external_diarizer_fallback.py`

**Step 1: Write failing tests**

Create `tests/test_external_diarizer_fallback.py`:
- diarizer call raises → backend supports_speaker True → native path used (`backend.transcribe(with_speaker=True)` called)
- diarizer call raises → backend supports_speaker False → ignore speaker and transcribe normally

**Step 2: Run tests to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_fallback.py`  
Expected: FAIL.

**Step 3: Implement**

Implement the fallback policy inside `transcribe_async` and (if needed) inside `_transcribe_with_external_diarizer` (return None on soft failures).

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_fallback.py`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/core/engine.py tests/test_external_diarizer_fallback.py
git commit -m "engine: external diarizer failure fallback policy"
```

---

### Task 09: Ensure `transcribe_auto_async` does not prematurely ignore speaker when external diarizer is enabled

**Files:**
- Modify: `src/core/engine.py`
- Test: `tests/test_engine.py`

**Step 1: Write failing test**

Add a test similar to existing fallback tests:
- backend.supports_speaker False
- external diarizer enabled
- `transcribe_auto_async(..., with_speaker=True)` must call `transcribe_async` with `with_speaker=True` (not force false)

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_engine.py -k auto_async`  
Expected: FAIL.

**Step 3: Implement**

Update `transcribe_auto_async`:
- if external diarizer enabled + base_url configured: do not flip `with_speaker` to False even when backend doesn’t support it.

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest -q tests/test_engine.py`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/core/engine.py tests/test_engine.py
git commit -m "engine: keep with_speaker for auto_async when external diarizer enabled"
```

---

### Task 10: Add sync `transcribe(...)` support for external diarizer (best-effort)

**Files:**
- Modify: `src/core/engine.py`
- Test: `tests/test_external_diarizer_engine_sync.py`

**Step 1: Write failing test**

Create a test that patches the internal async helper and validates `engine.transcribe(..., with_speaker=True)` uses it when enabled.

**Step 2: Run test (expect FAIL)**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_engine_sync.py`  
Expected: FAIL.

**Step 3: Implement**

Option A (minimal): in `transcribe(...)`, if external diarizer enabled, call `asyncio.run(...)` on the async helper and return result.

**Step 4: Run test (expect PASS)**

Run: `.venv/bin/python -m pytest -q tests/test_external_diarizer_engine_sync.py`

**Step 5: Commit**

```bash
git add src/core/engine.py tests/test_external_diarizer_engine_sync.py
git commit -m "engine: external diarizer support for sync transcribe"
```

---

### Task 11: Create diarizer service FastAPI app skeleton

**Files:**
- Create: `src/diarizer_service/app.py`
- Create: `src/diarizer_service/routes.py`
- Create: `src/diarizer_service/schemas.py`
- Test: `tests/test_diarizer_service_http.py`

**Step 1: Write failing test**

Create `tests/test_diarizer_service_http.py`:

```python
from fastapi.testclient import TestClient

def test_diarizer_health_and_diarize_route_exists():
    from src.diarizer_service.app import app
    c = TestClient(app)
    assert c.get("/health").status_code == 200
    # /api/v1/diarize should exist (even if returns 500 without model)
    assert c.post("/api/v1/diarize", files={"file": ("a.wav", b"RIFF")}).status_code in (200, 400, 500)
```

**Step 2: Run test (expect FAIL)**

Run: `.venv/bin/python -m pytest -q tests/test_diarizer_service_http.py`  
Expected: FAIL (module missing).

**Step 3: Implement minimal FastAPI app**

- `/health` returns `{status:"healthy"}`
- `/api/v1/diarize` validates file exists, reads bytes, calls a diarizer engine (stubbed for now)

**Step 4: Run test (expect PASS)**

Run: `.venv/bin/python -m pytest -q tests/test_diarizer_service_http.py`

**Step 5: Commit**

```bash
git add src/diarizer_service/app.py src/diarizer_service/routes.py src/diarizer_service/schemas.py tests/test_diarizer_service_http.py
git commit -m "diarizer: add service skeleton"
```

---

### Task 12: Implement diarizer engine interface + lazy import behavior (no real weights in tests)

**Files:**
- Create: `src/diarizer_service/engine.py`
- Test: `tests/test_diarizer_engine_stub.py`

**Step 1: Write failing test**

Create `tests/test_diarizer_engine_stub.py`:

```python
from unittest.mock import Mock

def test_engine_lazy_import(monkeypatch):
    fake = Mock()
    monkeypatch.setitem(__import__("sys").modules, "pyannote", fake)
    from src.diarizer_service.engine import DiarizerEngine
    e = DiarizerEngine(model_id="x", device="cuda")
    # Should not crash on init without loading
    assert e is not None
```

**Step 2: Run test (expect FAIL)**

Run: `.venv/bin/python -m pytest -q tests/test_diarizer_engine_stub.py`

**Step 3: Implement minimal engine**

- Provide `load()` that imports heavy deps
- Provide `diarize(wav_bytes) -> segments`
- In tests, keep it stub-friendly

**Step 4: Run test (expect PASS)**

Run: `.venv/bin/python -m pytest -q tests/test_diarizer_engine_stub.py`

**Step 5: Commit**

```bash
git add src/diarizer_service/engine.py tests/test_diarizer_engine_stub.py
git commit -m "diarizer: add engine interface with lazy imports"
```

---

### Task 13: Wire `/api/v1/diarize` to engine output + response schema

**Files:**
- Modify: `src/diarizer_service/routes.py`
- Modify: `tests/test_diarizer_service_http.py`

**Step 1: Write failing test**

Patch engine to return deterministic segments and assert response shape.

**Step 2: Run test (expect FAIL)**

Run: `.venv/bin/python -m pytest -q tests/test_diarizer_service_http.py`

**Step 3: Implement**

Return:
- `segments`
- `duration_ms` (computed from WAV header if possible; else omitted)
- `speakers` = number of unique `spk`

**Step 4: Run test (expect PASS)**

Run: `.venv/bin/python -m pytest -q tests/test_diarizer_service_http.py`

**Step 5: Commit**

```bash
git add src/diarizer_service/routes.py tests/test_diarizer_service_http.py
git commit -m "diarizer: return stable segments response"
```

---

### Task 14: Add diarizer Dockerfile + requirements (CUDA 12.4)

**Files:**
- Add: `Dockerfile.diarizer`
- Add: `requirements.diarizer.txt`

**Step 1: Write minimal Dockerfile**

- Base: `pytorch/pytorch:*cuda12.4*`
- Install: `ffmpeg`, `curl`
- Pip install: `fastapi`, `uvicorn`, `httpx`, `numpy`, `pyannote.audio` (and its deps)
- Entry: `uvicorn src.diarizer_service.app:app --host 0.0.0.0 --port 8000`

**Step 2: Build (optional)**

Run: `docker build -f Dockerfile.diarizer -t xiyu-diarizer:latest .`  
Expected: build succeeds.

**Step 3: Commit**

```bash
git add Dockerfile.diarizer requirements.diarizer.txt
git commit -m "docker: add diarizer image (cuda12.4)"
```

---

### Task 15: Add `xiyu-diarizer` service to `docker-compose.models.yml`

**Files:**
- Modify: `docker-compose.models.yml`

**Step 1: Implement**

Add a new service:
- name: `xiyu-diarizer`
- profile: `diarizer`
- build: `Dockerfile.diarizer`
- volumes: `huggingface-cache`, optional `./data`
- environment: `HF_TOKEN`, `DIARIZER_MODEL`, `DEVICE=cuda`
- GPU reservation like other GPU services

**Step 2: Quick config sanity**

Run: `docker compose -f docker-compose.models.yml config`  
Expected: exits 0.

**Step 3: Commit**

```bash
git add docker-compose.models.yml
git commit -m "compose: add xiyu-diarizer service"
```

---

### Task 16: Plumb external diarizer env vars into all Xiyu model services (opt-in)

**Files:**
- Modify: `docker-compose.models.yml`
- Modify: `.env.example`

**Step 1: Implement**

In `x-xiyu-env`, add:
- `SPEAKER_EXTERNAL_DIARIZER_ENABLE: ${SPEAKER_EXTERNAL_DIARIZER_ENABLE:-false}`
- `SPEAKER_EXTERNAL_DIARIZER_BASE_URL: ${SPEAKER_EXTERNAL_DIARIZER_BASE_URL:-http://xiyu-diarizer:8000}`
- `SPEAKER_EXTERNAL_DIARIZER_TIMEOUT_S: ${SPEAKER_EXTERNAL_DIARIZER_TIMEOUT_S:-60}`
- `SPEAKER_EXTERNAL_DIARIZER_MAX_TURNS: ${SPEAKER_EXTERNAL_DIARIZER_MAX_TURNS:-200}`
- `SPEAKER_EXTERNAL_DIARIZER_MAX_TURN_DURATION_S: ${SPEAKER_EXTERNAL_DIARIZER_MAX_TURN_DURATION_S:-25}`

Update `.env.example` with commented guidance + HF_TOKEN note.

**Step 2: Commit**

```bash
git add docker-compose.models.yml .env.example
git commit -m "compose: wire external diarizer env (opt-in)"
```

---

### Task 17: Update README with “meeting stack” instructions

**Files:**
- Modify: `README.md`

**Step 1: Implement**

Add a section:
- Start diarizer + any ASR:
  - `HF_TOKEN=... SPEAKER_EXTERNAL_DIARIZER_ENABLE=true docker compose -f docker-compose.models.yml --profile diarizer --profile qwen3 up -d`
- Explain caching:
  - `huggingface-cache` volume
- Explain fallback:
  - diarizer down → native speaker or ignore

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add external diarizer usage"
```

---

### Task 18: Update API docs to document speaker strategy

**Files:**
- Modify: `docs/API.md`

**Step 1: Implement**

- Update `/api/v1/backend` response example to include new capability fields.
- Document speaker routing:
  - external diarizer (if enabled)
  - fallback behavior

**Step 2: Commit**

```bash
git add docs/API.md
git commit -m "docs: document external diarizer strategy"
```

---

### Task 19: Add metrics for diarizer calls (optional but recommended)

**Files:**
- Modify: `src/utils/service_metrics.py`
- Modify: `src/core/engine.py`
- Test: `tests/test_metrics_diarizer.py`

**Step 1: Write failing test**

Assert metrics counter increments when external diarizer path is used.

**Step 2: Implement**

Add:
- `diarizer_requests_total`
- `diarizer_failures_total`
- `diarizer_latency_seconds_sum/count`

**Step 3: Run tests**

Run: `.venv/bin/python -m pytest -q tests/test_metrics_diarizer.py`

**Step 4: Commit**

```bash
git add src/utils/service_metrics.py src/core/engine.py tests/test_metrics_diarizer.py
git commit -m "metrics: track external diarizer usage"
```

---

### Task 20: Final verification + integration notes

**Files:**
- (no code) run verifications + record commands in PR/notes

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest -q`  
Expected: PASS.

**Step 2: Compose sanity**

Run: `docker compose -f docker-compose.models.yml config`  
Expected: OK.

**Step 3: Commit any last fixes**

If anything was adjusted during verification, commit it with a focused message.

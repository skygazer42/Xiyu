# Whisper Backend + CUDA 12.4 Containers Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a local GPU Whisper backend (`ASR_BACKEND=whisper`) and a `xiyu-whisper` container, while aligning Xiyu service images to CUDA 12.4 by default.

**Architecture:** Implement `WhisperBackend(ASRBackend)` using `openai-whisper` (PyTorch, GPU) that converts Xiyu PCM16LE bytes into float waveform and returns Xiyu-standard `text + sentence_info`. Update Settings + backend registry + model manager. Update Dockerfiles to use `pytorch/pytorch:*cuda12.4*` runtime base image and update compose + frontend presets to expose the new container.

**Tech Stack:** FastAPI, PyTorch, openai-whisper, Docker/Compose, React (Vite), TanStack Query.

---

### Task 1: Pick + lock the CUDA 12.4 base image tag

**Files:**
- Modify: `Dockerfile`
- Modify: `Dockerfile.onnx`
- Modify: `Dockerfile.gguf`
- Modify: `Dockerfile.benchmark`

**Step 1: Decide base tag**

Use a consistent base for all Xiyu service images, e.g.:
- `pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime`

**Step 2: Write it down**

Add a single `ARG TORCH_BASE_IMAGE=...` near the top of each Dockerfile so future changes only touch one line.

**Step 3: (Optional) sanity check locally**

Run: `docker pull pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime`
Expected: pull succeeds.

**Step 4: Commit**

Run:
```bash
git add Dockerfile Dockerfile.onnx Dockerfile.gguf Dockerfile.benchmark
git commit -m "docker: prepare cuda12.4 base image arg"
```

---

### Task 2: Extend Settings to support `ASR_BACKEND=whisper`

**Files:**
- Modify: `src/config.py`

**Step 1: Write failing test**

Add a small test that ensures Settings can parse `ASR_BACKEND=whisper`:

Create: `tests/test_whisper_settings.py`
```python
def test_settings_accepts_whisper_backend(monkeypatch):
    monkeypatch.setenv("ASR_BACKEND", "whisper")
    from src.config import Settings
    s = Settings()
    assert s.asr_backend == "whisper"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest -q tests/test_whisper_settings.py`
Expected: FAIL (Literal validation error / unknown backend).

**Step 3: Implement Settings changes**

In `src/config.py`:
- Add `"whisper"` to `asr_backend: Literal[...]`
- Add whisper-specific config keys (env backed):
  - `whisper_model: str = "large"`
  - `whisper_language: str = "zh"` (optional; can be None to auto-detect)
  - `whisper_download_root: str = ""` (empty means default)

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest -q tests/test_whisper_settings.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/config.py tests/test_whisper_settings.py
git commit -m "config: add whisper backend settings"
```

---

### Task 3: Register `whisper` backend type in backend factory

**Files:**
- Modify: `src/models/backends/__init__.py`

**Step 1: Write failing test**

Create: `tests/test_whisper_backend_factory.py`
```python
import pytest

def test_get_backend_whisper_missing_dependency_raises():
    from src.models.backends import get_backend
    with pytest.raises(ImportError):
        get_backend(backend_type="whisper")
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest -q tests/test_whisper_backend_factory.py`
Expected: FAIL (backend type not accepted / ValueError).

**Step 3: Implement**

In `src/models/backends/__init__.py`:
- Add `"whisper"` to `BackendType`
- Add a `elif backend_type == "whisper": ...` branch importing `WhisperBackend`

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest -q tests/test_whisper_backend_factory.py`
Expected: PASS (ImportError until dependency is installed).

**Step 5: Commit**

```bash
git add src/models/backends/__init__.py tests/test_whisper_backend_factory.py
git commit -m "models: register whisper backend type"
```

---

### Task 4: Implement `WhisperBackend` (mock-driven, no real weights)

**Files:**
- Create: `src/models/backends/whisper.py`
- Test: `tests/test_whisper_backend.py`

**Step 1: Write failing test**

Create: `tests/test_whisper_backend.py`
```python
from unittest.mock import Mock
import numpy as np

def test_whisper_backend_transcribe_pcm_bytes(monkeypatch):
    # Lazy import inside backend should load this module name.
    fake_whisper = Mock()
    fake_model = Mock()
    fake_model.transcribe.return_value = {
        "text": "hello",
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "hello"},
        ],
    }
    fake_whisper.load_model.return_value = fake_model
    monkeypatch.setitem(__import__("sys").modules, "whisper", fake_whisper)

    from src.models.backends.whisper import WhisperBackend

    backend = WhisperBackend(model="large", device="cuda", language="zh")
    backend.load()

    # 1s of silence PCM16LE @ 16kHz
    pcm = (np.zeros(16000, dtype=np.int16)).tobytes()
    out = backend.transcribe(pcm)

    assert out["text"] == "hello"
    assert out["sentence_info"] == [{"text": "hello", "start": 0, "end": 1000}]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest -q tests/test_whisper_backend.py`
Expected: FAIL (module/class missing).

**Step 3: Write minimal implementation**

Create `src/models/backends/whisper.py`:
- Import numpy
- Lazy import `whisper` inside `load()`
- Convert PCM bytes to float waveform
- Call `model.transcribe(audio, language=..., initial_prompt=hotwords, fp16=True, **kwargs)`
- Build `sentence_info` from `segments` (seconds → ms int)
- Return `{"text": ..., "sentence_info": ...}`

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest -q tests/test_whisper_backend.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/models/backends/whisper.py tests/test_whisper_backend.py
git commit -m "models: add local whisper backend"
```

---

### Task 5: Wire `ModelManager` to build the whisper backend

**Files:**
- Modify: `src/models/model_manager.py`

**Step 1: Write failing test**

Create: `tests/test_model_manager_whisper.py`
```python
from unittest.mock import Mock

def test_model_manager_initializes_whisper_backend(monkeypatch):
    from src.config import settings
    settings.asr_backend = "whisper"

    # Patch factory to avoid importing real whisper.
    from src.models import model_manager as mm
    monkeypatch.setattr(mm, "get_backend", Mock(return_value=Mock(get_info=lambda: {"name": "whisper"})))

    m = mm.ModelManager()
    m._backend = None
    _ = m.backend
    mm.get_backend.assert_called()
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest -q tests/test_model_manager_whisper.py`
Expected: FAIL (unknown backend type branch).

**Step 3: Implement**

In `src/models/model_manager.py` add:
- `elif backend_type == "whisper": ... get_backend(backend_type="whisper", model=settings.whisper_model, ...)`

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest -q tests/test_model_manager_whisper.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/models/model_manager.py tests/test_model_manager_whisper.py
git commit -m "models: wire whisper backend into model manager"
```

---

### Task 6: Add `openai-whisper` dependency for container builds

**Files:**
- Modify: `requirements.txt`

**Step 1: Update requirements**

Add:
- `openai-whisper>=20231117` (or a known-good version)

**Step 2: Run backend tests**

Run: `.venv/bin/pytest -q tests/test_whisper_backend.py`
Expected: PASS (still mocked; no weight download).

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "deps: add openai-whisper for whisper backend"
```

---

### Task 7: Update `Dockerfile` to CUDA 12.4 base image and keep frontend build

**Files:**
- Modify: `Dockerfile`

**Step 1: Change base images**

Update:
- builder stage base image → CUDA 12.4 base (same as runtime)
- runtime stage base image → CUDA 12.4 base

**Step 2: Install runtime deps**

Keep `ffmpeg`, `libsndfile1`, `curl`.

**Step 3: Ensure python deps install into correct env**

Prefer `pip install -r requirements.txt` (no `--prefix=/install` copy) to avoid path issues on conda-based images.

**Step 4: Commit**

```bash
git add Dockerfile
git commit -m "docker: use cuda12.4 pytorch base for xiyu image"
```

---

### Task 8: Update `Dockerfile.onnx` to CUDA 12.4 base image

**Files:**
- Modify: `Dockerfile.onnx`

**Step 1: Switch base**

Use the same CUDA 12.4 base image for both builder + runtime.

**Step 2: Keep ONNX deps**

Keep installing:
- `funasr-onnx>=0.1.0`
- `onnxruntime` (or switch to `onnxruntime-gpu` if desired)

**Step 3: Commit**

```bash
git add Dockerfile.onnx
git commit -m "docker: align onnx image to cuda12.4 base"
```

---

### Task 9: Update `Dockerfile.gguf` to CUDA 12.4 base image

**Files:**
- Modify: `Dockerfile.gguf`

**Steps:**
- Same pattern: switch base to CUDA 12.4 image
- Keep gguf + onnxruntime deps

**Commit:**
```bash
git add Dockerfile.gguf
git commit -m "docker: align gguf image to cuda12.4 base"
```

---

### Task 10: Update `docker-compose.models.yml` to add `xiyu-whisper`

**Files:**
- Modify: `docker-compose.models.yml`

**Step 1: Add service**

Add a new service similar to `xiyu-pytorch`:
- name: `xiyu-whisper`
- profile: `["whisper"]`
- port: `${PORT_WHISPER:-8105}:8000`
- env:
  - `ASR_BACKEND=whisper`
  - `DEVICE=cuda`
  - `WHISPER_MODEL=large` (or via env)
- add GPU device reservation like other GPU services

**Step 2: Commit**

```bash
git add docker-compose.models.yml
git commit -m "compose: add xiyu-whisper service"
```

---

### Task 11: Update `.env.example` for whisper port + model

**Files:**
- Modify: `.env.example`

**Steps:**
- Add `PORT_WHISPER=8105`
- Add `WHISPER_MODEL=large`
- Update ASR_BACKEND comment list to include `whisper`

**Commit:**
```bash
git add .env.example
git commit -m "docs: add whisper env vars and port"
```

---

### Task 12: Update frontend preset backend list

**Files:**
- Modify: `frontend/src/components/transcribe/TranscribeOptions.tsx`

**Step 1: Add option**

Add:
- `Whisper (8105)` pointing to `http://localhost:8105`

**Step 2: Frontend build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

**Step 3: Commit**

```bash
git add frontend/src/components/transcribe/TranscribeOptions.tsx
git commit -m "frontend: add whisper preset backend"
```

---

### Task 13: Update backend docs (README + API)

**Files:**
- Modify: `README.md`
- Modify: `docs/API.md`

**Steps:**
- Mention `xiyu-whisper` in multi-model deployment section (ports/profiles).
- Clarify `/api/v1/asr` is “Whisper ASR WebService compatible response format” (not necessarily an actual Whisper model).
- Add `whisper` to ASR_BACKEND list in docs.

**Commit:**
```bash
git add README.md docs/API.md
git commit -m "docs: document whisper backend and cuda12.4 images"
```

---

### Task 14: Full backend test run

**Files:**
- (none)

**Step 1: Run**

Run: `.venv/bin/pytest -q`
Expected: PASS.

**Step 2: Commit (if needed)**

No commit unless fixes were required.

---

### Task 15: Optional local docker smoke test (GPU)

**Files:**
- (none)

**Steps:**
- Build: `docker compose -f docker-compose.models.yml --profile whisper build xiyu-whisper`
- Run: `docker compose -f docker-compose.models.yml --profile whisper up -d xiyu-whisper`
- Check: `curl http://localhost:8105/health`
- Check backend info: `curl http://localhost:8105/api/v1/backend`

Expected:
- health OK
- backend=`whisper`, capabilities.supports_speaker=false

---

### Task 16: Expose Whisper model info via `/api/v1/backend`

**Files:**
- Modify: `src/models/backends/whisper.py`
- Test: `tests/test_whisper_backend.py`

**Step 1: Add `get_info()`**

Return a dict containing at least:
- `name="WhisperBackend"`
- `type="whisper"`
- `device`
- `model`
- `language` (if configured)
- `supports_*` flags

**Step 2: Extend unit test**

Update `tests/test_whisper_backend.py` to assert `backend.get_info()["type"] == "whisper"`.

**Step 3: Run tests**

Run: `.venv/bin/pytest -q tests/test_whisper_backend.py`
Expected: PASS.

**Step 4: Commit**

```bash
git add src/models/backends/whisper.py tests/test_whisper_backend.py
git commit -m "models: include whisper info for backend probing"
```

---

### Task 17: Document Whisper env vars (model/language/cache)

**Files:**
- Modify: `.env.example`
- Modify: `README.md`

**Steps:**
- Add env vars:
  - `WHISPER_MODEL=large`
  - `WHISPER_LANGUAGE=zh` (optional)
  - `WHISPER_DOWNLOAD_ROOT=/app/data/models/whisper` (optional)
- Update README to mention model weights cache location and that volumes should persist `./data`.

**Commit:**
```bash
git add .env.example README.md
git commit -m "docs: add whisper env vars and caching notes"
```

---

### Task 18: Align `docs/API.md` Whisper compatibility schema with implementation

**Files:**
- Modify: `docs/API.md`

**Steps:**
- Ensure `/api/v1/asr` response example matches the real implementation:
  - `segments[*].sentence_index` (not `id`)
  - `start/end` are SRT time strings (per current code) or update code+docs together if you standardize it
- Add a short note: “该接口仅返回 Whisper WebService 兼容格式；实际模型由容器 ASR_BACKEND 决定”

**Commit:**
```bash
git add docs/API.md
git commit -m "docs: clarify /api/v1/asr whisper-compat output"
```

---

### Task 19: Update docker-compose.yml default service notes (CUDA 12.4)

**Files:**
- Modify: `docker-compose.yml`

**Steps:**
- Add a comment that the default image is CUDA 12.4-based and requires NVIDIA runtime for GPU usage.
- Keep current behavior unchanged (still maps `PORT` and mounts volumes).

**Commit:**
```bash
git add docker-compose.yml
git commit -m "docs: annotate default compose cuda12.4 expectation"
```

---

### Task 20: Push to main branch

**Files:**
- (none)

**Steps:**
```bash
git push origin main
```

Expected: push succeeds.

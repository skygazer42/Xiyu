# Gov Meeting Audio Standardization (Pre-ASR) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为政务会议记录增加“预处理标准化”滤波能力（默认轻高通 80Hz，可选低通/带通），并与 ClearVoice `MossFormer2_48000Hz` 默认组合一起稳定跑通；完成后重建全部容器并跑全量测试/烟测。

**Architecture:**  
在 `AudioPreprocessor` 内新增 lowpass / bandpass 选项与滤波实现（优先 SciPy SOS IIR），由 `Settings` 提供全局默认值，并允许 `asr_options.preprocess` 覆盖；API 层与长音频 chunking 引擎都用同一套 request-scoped config 生成逻辑。

**Tech Stack:** Python 3.10+, NumPy, SciPy (`scipy.signal`), FastAPI, Docker Compose, pytest

---

### Task 1: Add Settings + env defaults

**Files:**
- Modify: `src/config.py`
- Modify: `.env.example`

**Step 1: Write the failing test**
- (No new test needed for Settings parsing; this will be covered by integration + unit tests below)

**Step 2: Implement Settings fields**
- Add fields:
  - `audio_highpass_enable`, `audio_highpass_cutoff_hz`
  - `audio_lowpass_enable`, `audio_lowpass_cutoff_hz`
  - `audio_bandpass_enable`, `audio_bandpass_low_hz`, `audio_bandpass_high_hz`
- Defaults:
  - highpass enable = `true`, cutoff = `80`
  - lowpass enable = `false`, cutoff = `7600`
  - bandpass enable = `false`, low/high = `300/3400`

**Step 3: Update `.env.example`**
- Add the above env vars with comments “政务会议推荐默认：高通 80Hz；带通默认关闭”

**Step 4: Commit**
```bash
git add src/config.py .env.example
git commit -m "feat: add audio standardization settings"
```

---

### Task 2: Extend `asr_options.preprocess` schema

**Files:**
- Modify: `src/api/asr_options.py`
- Test: `tests/test_asr_options.py`

**Step 1: Write the failing test**
- Add a test that parses:
```json
{"preprocess":{"lowpass_enable":true,"lowpass_cutoff_hz":7600,"bandpass_enable":true,"bandpass_low_hz":300,"bandpass_high_hz":3400}}
```
- Expect the keys are preserved and validated.

**Step 2: Implement schema changes**
- Add keys to `_PREPROCESS_KEYS` and `_PREPROCESS_TYPES`
- Add range sanity checks in `_validate_ranges` for:
  - cutoff > 0
  - bandpass_low < bandpass_high
  - cutoff < Nyquist (best-effort; validate in preprocessor too)

**Step 3: Run the test**
```bash
pytest -q tests/test_asr_options.py
```

**Step 4: Commit**
```bash
git add src/api/asr_options.py tests/test_asr_options.py
git commit -m "feat: allow lowpass/bandpass in asr_options.preprocess"
```

---

### Task 3: Implement lowpass + bandpass in `AudioPreprocessor`

**Files:**
- Modify: `src/core/audio/preprocessor.py`
- Test: `tests/test_audio_preprocess_filters.py`

**Step 1: Write failing tests**
- Add:
  - `test_lowpass_attenuates_high_frequency_energy()` (e.g. 7kHz sine -> attenuated with lowpass 3kHz)
  - `test_bandpass_keeps_midband_and_attenuates_outside()` (e.g. mix 100Hz + 1kHz + 6kHz -> keep 1kHz, attenuate 100Hz/6kHz)

**Step 2: Implement filter options**
- Extend `__init__` with:
  - `lowpass_enable`, `lowpass_cutoff_hz`
  - `bandpass_enable`, `bandpass_low_hz`, `bandpass_high_hz`
- Apply them in `process()` as **length-preserving** steps, before denoise:
  - If `bandpass_enable`: apply HP at `bandpass_low_hz` then LP at `bandpass_high_hz`
  - Else: apply optional HP / optional LP

**Step 3: Filter implementation**
- Prefer SciPy:
  - `scipy.signal.butter(order=4, Wn, btype, fs=sample_rate, output="sos")`
  - `scipy.signal.sosfilt(sos, audio)`
- If SciPy import fails: log a warning and skip filter (do not crash).

**Step 4: Run tests**
```bash
pytest -q tests/test_audio_preprocess_filters.py
```

**Step 5: Commit**
```bash
git add src/core/audio/preprocessor.py tests/test_audio_preprocess_filters.py
git commit -m "feat: add lowpass/bandpass audio filters"
```

---

### Task 4: Wire defaults into API layer + long-audio engine

**Files:**
- Modify: `src/api/dependencies.py`
- Modify: `src/core/engine.py`

**Step 1: Implement**
- Replace hard-coded defaults (`highpass_enable=False` etc) with Settings-backed defaults:
  - API layer `_build_request_preprocessor()`
  - Engine `_pre_cfg` / `_chunk_cfg`

**Step 2: Run full tests**
```bash
docker run --rm -v /data/TingWu:/app -w /app xiyu-speech-service:pytorch bash -lc "python -m pip -q install pytest >/dev/null && pytest -q"
```

**Step 3: Commit**
```bash
git add src/api/dependencies.py src/core/engine.py
git commit -m "feat: use settings defaults for standardization filters"
```

---

### Task 5: Rebuild all containers + smoke test

**Files:**
- (No code changes; verification only)

**Step 1: Rebuild**
```bash
docker compose -f docker-compose.models.yml --profile all build
docker compose -f docker-compose.models.yml --profile all up -d
```

**Step 2: Verify ClearVoice effective model**
```bash
curl -sS http://localhost:8400/info | python -m json.tool
```
Expected: `model_effective` is `MossFormer2_SE_48K`

**Step 3: Smoke all endpoints**
```bash
PORTS="8101 8102 8103 8105 8200 8201" DIARIZER_PORT=8300 REMOTE_ASR_PORTS="9001" scripts/smoke_all_endpoints.sh
```
Expected: `FAIL=0`

---

### Task 6: Push + tag

**Step 1: Push**
```bash
git push origin main
```

**Step 2: Tag**
- Create a new semver tag, e.g. `v0.1.11`
```bash
git tag -a v0.1.11 -m "gov meeting audio standardization filters"
git push origin v0.1.11
```


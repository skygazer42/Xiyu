# Enterprise Hotwords + Long-Audio Jobs (Resume) + Word Alignment — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现 A) 热词 canonical/alias + 自动变体，B) 长音频分块 checkpoint + resume，C) 输出 word-level 时间戳，并保证回归测试通过。

**Architecture:** 以“向后兼容”为第一原则：不破坏现有热词文件格式、不改变默认 API schema（仅新增可选字段/开关），checkpoint 采用落盘 JSON，不引入新外部服务。

**Tech Stack:** Python, FastAPI, numpy, ffmpeg, pytest, jieba, pypinyin, Docker.

---

### Task 01: Hotword alias + variants (A)

**Files:**
- Create: `src/core/hotword/variants.py`
- Modify: `src/core/hotword/corrector.py`
- Test: `tests/test_hotword.py` (or new test file)

**Step 1: Add a failing test (alias -> canonical)**
- Create a hotword list line like: `政企通2.0 | 政企通二点零`
- Input: `我们用政企通二点零办理` → expect output contains `政企通2.0`

**Step 2: Implement alias parsing**
- Support `canonical | alias1 | alias2` per-line
- Store `alias -> canonical` mapping
- Replacement output must always use canonical

**Step 3: Implement auto variants**
- NFKC normalize
- Generate a small bounded set of numeric variants (e.g. `2.0` variants)
- Ensure update_hotwords returns canonical count (API reload count stays stable)

**Step 4: Run tests**
- Run: `pytest -q`
- Expected: PASS

### Task 02: Chunk checkpointing + resume (B)

**Files:**
- Modify: `src/config.py` (jobs/checkpoint defaults)
- Modify: `src/core/engine.py` (transcribe_long_audio checkpointing)
- Test: `tests/test_engine.py` (or new test file)

**Step 1: Add a failing test (resume skip)**
- Fake a long-audio with 2 chunks (force `chunking.strategy="time"` + small chunk size)
- First run: simulate partial completion by pre-writing one chunk result to checkpoint dir
- Second run: assert engine skips that chunk and only transcribes remaining

**Step 2: Implement checkpoint layout**
- `jobs_dir = outputs_dir / "jobs"`
- For each job:
  - `meta.json` (chunk boundaries + params)
  - `chunks/{idx:06d}.json` per chunk
  - `result.json` (optional final)

**Step 3: Integrate into `transcribe_long_audio`**
- If checkpoint enabled:
  - Load meta + chunks when present
  - Skip `success=true` chunks
  - Persist new chunk results immediately
  - Merge at end and (optionally) write `result.json`

**Step 4: Run tests**
- Run: `pytest -q`
- Expected: PASS

### Task 03: Word-level timestamps (C)

**Files:**
- Modify: `src/core/audio/chunker.py` (optionally return `accu_chars/accu_ts`)
- Create: `src/core/alignment/word_timestamps.py`
- Modify: `src/core/engine.py` (attach `words` when enabled)
- Modify: `src/api/schemas.py` (optional `words` field)
- Test: `tests/test_integration.py` or new tests

**Step 1: Add a failing test (words output monotonic)**
- Call long-audio chunking with `asr_options.alignment.enable=true`
- Expect:
  - response has `words`
  - `start<=end`, and timestamps are non-decreasing

**Step 2: Implement word tokenization**
- Mixed tokenizer:
  - ASCII runs as tokens
  - CJK runs segmented by jieba
  - Skip whitespace/punctuation tokens

**Step 3: Build words from char timestamps**
- Use `text_accu` char-level timestamps (linear per chunk) to compute word boundaries
- Apply output size guard via `max_words`

**Step 4: Run tests**
- Run: `pytest -q`
- Expected: PASS

### Task 04: Docs / .env example

**Files:**
- Modify: `.env.example`
- Modify: `README.md` (optional, only if needed)

**Step 1: Document new knobs**
- `LONG_AUDIO_CHECKPOINT_ENABLE` (or similar)
- `JOB_OUTPUTS_DIR` (or similar)
- `HOTWORD_ALIAS_ENABLE` (optional)
- `ALIGNMENT_ENABLE` (optional)

### Task 05: Final regression + push

**Step 1: Run full tests**
- Run: `pytest -q`
- Expected: PASS

**Step 2: Push**
- Run: `git push`


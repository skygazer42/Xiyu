# Advanced `asr_options` Editor + Exports + Qwen3 Postprocess Defaults — Implementation Plan (20 Tasks)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 Xiyu 前端加入“高级 `asr_options` 编辑器 + 常用模板”、增强导出格式（MD/VTT/DOC），并补齐后端对 `spoken_punc_enable` / `acronym_merge_enable` 的全局默认支持，用于 Qwen3-ASR 等后端的准确率/可读性优化。

**Architecture:** UI → 生成/校验 asr_options → HTTP multipart 发送 `asr_options` JSON → backend allowlist 解析 → engine request-scoped `TextPostProcessor` 生效 → response 包含 `speaker_turns/text_accu` → ExportMenu 输出多格式。

**Tech Stack:** FastAPI/Python/pytest；React/Vite/TypeScript/axios/zustand。

---

### Task 01: Add Settings keys for postprocess toggles

**Files:**
- Modify: `src/config.py`
- Modify: `.env.example`

**Step 1: Write failing test**
- Add a small unit test verifying `TextPostProcessor.from_config(settings)` sees new keys (or defaults).

**Step 2: Implement**
- Add `spoken_punc_enable: bool = False`
- Add `acronym_merge_enable: bool = False`
- Document env vars in `.env.example`

**Step 3: Run**
Run: `python3 -m compileall -q src`

**Step 4: Commit**
Commit message: `config: add spoken_punc/acronym defaults`

---

### Task 02: Engine request postprocess base should include new keys (TDD)

**Files:**
- Modify: `src/core/engine.py`
- Test: `tests/test_engine.py` (or a new focused test)

**Steps:**
1. Write failing test: set `settings.acronym_merge_enable=True`, call `_get_request_post_processor(asr_options={"postprocess":{}})` and ensure processor merges acronyms.
2. Implement: in `_get_request_post_processor`, pass `spoken_punc_enable`/`acronym_merge_enable` from settings into `PostProcessorSettings`.
3. Run: `pytest -q tests/test_engine.py`
4. Commit.

---

### Task 03: Frontend store fields for advanced asr_options text + error

**Files:**
- Modify: `frontend/src/stores/transcriptionStore.ts`

**Steps:**
1. Add `advancedAsrOptionsText` + setter
2. Add `advancedAsrOptionsError` + setter (or derive in UI)
3. Keep defaults empty (safe)
4. Build: `cd frontend && npm run build`
5. Commit.

---

### Task 04: UI — Advanced asr_options editor (collapsible)

**Files:**
- Modify: `frontend/src/components/transcribe/TranscribeOptions.tsx`

**Steps:**
1. Add Collapsible “高级 asr_options”
2. Add JSON textarea + inline parse error display
3. Commit.

---

### Task 05: UI — Add templates (3–5 buttons)

**Files:**
- Modify: `frontend/src/components/transcribe/TranscribeOptions.tsx`

**Templates:**
- Qwen3 强后处理（postprocess）
- 长音频准确率优先（chunking）
- 会议（speaker.label_style + merge）

**Verify:** `cd frontend && npm run build`

---

### Task 06: API client — merge advanced asr_options + speaker label style

**Files:**
- Modify: `frontend/src/lib/api/transcribe.ts`

**Steps:**
1. Parse advanced JSON safely
2. Shallow-merge sections
3. Always include `speaker.label_style` when `with_speaker=true`
4. Commit.

---

### Task 07: TranscribePage — block submit on invalid JSON (UI-safe)

**Files:**
- Modify: `frontend/src/pages/TranscribePage.tsx`

**Steps:**
1. If advanced JSON invalid: show toast and return early
2. Commit.

---

### Task 08: Export — TXT prefer transcript/text_accu/text

**Files:**
- Modify: `frontend/src/components/transcript/ExportMenu.tsx`

---

### Task 09: Export — Markdown (.md) meeting-friendly

**Files:**
- Modify: `frontend/src/components/transcript/ExportMenu.tsx`

**Rules:**
- Prefer `speaker_turns`
- Fall back to `transcript/text_accu/text`

---

### Task 10: Export — WebVTT (.vtt) with `<v Speaker>` when possible

**Files:**
- Modify: `frontend/src/components/transcript/ExportMenu.tsx`

---

### Task 11: Export — Word-friendly `.doc` (HTML)

**Files:**
- Modify: `frontend/src/components/transcript/ExportMenu.tsx`

---

### Task 12: Export — SRT speaker label numeric fallback

**Files:**
- Modify: `frontend/src/components/transcript/ExportMenu.tsx`

---

### Task 13: Types — ensure `speaker_turns` + `text_accu` present

**Files:**
- Modify: `frontend/src/lib/api/types.ts`

---

### Task 14: Docs — README update for advanced editor + exports

**Files:**
- Modify: `README.md`

---

### Task 15: Backend — optional qwen3 compose defaults (safe)

**Files:**
- Modify: `docker-compose.models.yml`

**Idea:** for `xiyu-qwen3` set `SPACING_CJK_ASCII_ENABLE=true`, `PUNC_MERGE_ENABLE=true`, etc.

---

### Task 16: Tests — export helpers (optional, if we factor into pure functions)

**Files:**
- Create: `frontend/src/lib/export/*` (if needed)

---

### Task 17: Python verification

Run:
- `python3 -m compileall -q src tests`
- `pytest -q tests/test_engine.py tests/test_api_http.py`

---

### Task 18: Frontend verification

Run:
- `cd frontend && npm ci`
- `cd frontend && npm run build`

---

### Task 19: Cleanup / ensure no accidental deps committed

Check:
- `git status`

---

### Task 20: Merge to main + push

Steps:
1. Merge branch
2. Re-run Task 17/18 verifications
3. Push `origin/main`

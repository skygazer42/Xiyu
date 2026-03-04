# Xiyu URL 异步转写（前端任务队列）Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现 URL 异步转写端到端闭环：前端提交 URL → 任务队列轮询 → 结果可查看并复用现有时间轴/导出/复制能力；多后端场景下轮询始终命中提交时的后端。

**Architecture:** 后端保持 `/api/v1/trans/url` + `/api/v1/result` 的异步任务模型，且最终结果 `data` 与同步 `/api/v1/transcribe` 结果 schema 对齐；前端在 `TranscribePage` 内维护 task 列表，并对每个任务保存提交时的 `baseURL` 快照用于轮询取结果。

**Tech Stack:** FastAPI + in-memory TaskManager；React + Zustand + axios；Vite/TypeScript。

---

## Status snapshot（当前工作区已存在但未提交）

- 后端：`src/api/routes/async_transcribe.py` 已改为让 URL 任务最终结果与 `/api/v1/transcribe` schema 对齐（时间戳保留毫秒整数）
- 后端：`/api/v1/trans/url` 已支持额外表单字段（`apply_llm/llm_role/hotwords/asr_options`）
- 测试：新增回归测试 `tests/test_async_transcribe.py::test_handle_url_transcribe_returns_engine_schema`
- 前端：目前 URL tab 仍未真正调用接口（需要实现）

下面 20 个 task 按“可验证的小步提交”拆分，执行时可逐个 commit。

---

## 20-task roadmap（bite-sized, verify-first）

### Task 01: 锁定 URL handler 输出 schema 的回归测试

**Files:**
- Modify: `tests/test_async_transcribe.py`

**Step 1: 确认测试覆盖点**
- 断言 `code==0`
- `sentences[].start/end` 为 `int`（ms）
- `speaker_id` 不丢失
- `speaker_turns[].start/end` 为 `int`

**Step 2: 运行单测**

Run: `.venv/bin/pytest -q tests/test_async_transcribe.py::test_handle_url_transcribe_returns_engine_schema`

Expected: PASS（当前工作区应该已通过；如果失败，先修复再进入下一步）

---

### Task 02: 让 `_handle_url_transcribe` 直接返回引擎 schema（不做 SRT 时间字符串转换）

**Files:**
- Modify: `src/api/routes/async_transcribe.py`

**Step 1: 对齐返回字段**
- 返回 `{ code, text, text_accu, sentences, speaker_turns, transcript, raw_text }`
- `sentences/speaker_turns` 保持毫秒整数

**Step 2: 运行单测验证**

Run: `.venv/bin/pytest -q tests/test_async_transcribe.py::test_handle_url_transcribe_returns_engine_schema`

Expected: PASS

---

### Task 03: `/api/v1/trans/url` 接受并透传额外表单字段

**Files:**
- Modify: `src/api/routes/async_transcribe.py`

**Step 1: 扩展表单参数**
- `apply_llm`, `llm_role`, `hotwords`, `asr_options`

**Step 2: 提交 payload 透传给 task handler**

**Step 3: 运行后端测试**

Run: `.venv/bin/pytest -q`

Expected: PASS

---

### Task 04: 后端变更打包提交（只提交后端+测试）

**Files:**
- Modify: `src/api/routes/async_transcribe.py`
- Modify: `tests/test_async_transcribe.py`

**Step 1: 全量测试**

Run: `.venv/bin/pytest -q`

Expected: PASS（exit code 0）

**Step 2: Commit**

```bash
git add src/api/routes/async_transcribe.py tests/test_async_transcribe.py
git commit -m "api: align URL async transcribe result schema"
```

---

### Task 05: 前端 types 对齐后端 `/trans/url` 响应

**Files:**
- Modify: `frontend/src/lib/api/types.ts`

**Step 1: 更新 `UrlTranscribeResponse`**
- 后端返回：`{ code, status, message, data: { task_id } }`
- 旧的 `task_id` 顶层字段需要调整

**Step 2: TypeScript 构建验证**

Run: `cd frontend && npm run build`

Expected: PASS（若失败按报错逐项修复）

---

### Task 06: 前端 types 对齐后端 `/result` 响应

**Files:**
- Modify: `frontend/src/lib/api/types.ts`

**Step 1: 更新 `TaskResultResponse`**
- 对齐 `{ code, status, message, data }`（pending/processing 时 data 仅含 task_id；success 时 data 为 `TranscribeResponse`）

**Step 2: Build 验证**

Run: `cd frontend && npm run build`

Expected: PASS

---

### Task 07: `transcribeUrl()` 改为 `FormData` + `audio_url` 字段

**Files:**
- Modify: `frontend/src/lib/api/transcribe.ts`

**Step 1: 改用 `FormData`**
- `audio_url`（不是 `url`）
- 透传 `with_speaker/apply_hotword/apply_llm/llm_role/hotwords/asr_options`

**Step 2: Build 验证**

Run: `cd frontend && npm run build`

Expected: PASS

---

### Task 08: `getTaskResult()` 改为 `FormData` + `delete` 语义

**Files:**
- Modify: `frontend/src/lib/api/transcribe.ts`

**Step 1: API 对齐**
- 请求体：`task_id` + `delete`
- 删除 `wait/timeout`（后端不支持）

**Step 2: Build 验证**

Run: `cd frontend && npm run build`

Expected: PASS

---

### Task 09: 支持 per-request `baseURL` override（任务提交时快照）

**Files:**
- Modify: `frontend/src/lib/api/transcribe.ts`
- (Maybe) Modify: `frontend/src/lib/api/client.ts`

**Step 1: 为 `transcribeUrl/getTaskResult` 增加可选参数**
- `baseURL?: string`
- axios `post(url, data, { baseURL })` 覆盖本次请求

**Step 2: Build 验证**

Run: `cd frontend && npm run build`

Expected: PASS

---

### Task 10: URL 输入组件 UX：避免与右侧 `TranscribeOptions` 重复冲突

**Files:**
- Modify: `frontend/src/components/url/UrlTranscribe.tsx`

**Step 1: 保留 URL 输入 + 提交按钮**
- 选项（说话人/热词/LLM/高级 asr_options）统一使用右侧 `TranscribeOptions`
- 提示文案更新：“转写选项请在右侧设置”

**Step 2: 调整 `onSubmit` 签名**
- 从 `(url, options)` 简化为 `(url)`

**Step 3: Build 验证**

Run: `cd frontend && npm run build`

Expected: PASS

---

### Task 11: `TranscribePage` 接入 URL 提交 API

**Files:**
- Modify: `frontend/src/pages/TranscribePage.tsx`

**Step 1: 实现 `handleUrlSubmit(url)`**
- 校验高级 `asr_options` JSON（复用现有逻辑）
- 从 store 读取：
  - `options`（with_speaker/apply_hotword/apply_llm/llm_role）
  - `tempHotwords`
  - `advancedAsrOptionsText`
- 调用 `transcribeUrl(url, options+hotwords+asrOptionsText, { baseURL: snapshot })`

**Step 2: toast 提示**
- 成功：提示已提交 + task_id
- 失败：提示请求失败

---

### Task 12: 增加任务队列 state（页面内）

**Files:**
- Modify: `frontend/src/pages/TranscribePage.tsx`
- (Optional) Modify: `frontend/src/components/task/TaskManager.tsx`

**Step 1: 在页面中新增**
- `tasks` state
- `isSubmittingUrl` state（只控制提交按钮 loading）

**Step 2: task 结构包含**
- `id`（task_id）
- `status`
- `url`
- `createdAt`
- `backendBaseUrl`（提交时快照）

---

### Task 13: 实现轮询（polling loop）

**Files:**
- Modify: `frontend/src/pages/TranscribePage.tsx`

**Step 1: 为 task 启动轮询**
- `getTaskResult(taskId, { delete:false, baseURL })`
- pending/processing → 继续
- success → 拉最终结果（或直接使用 success data）
- error → 任务失败态

**Step 2: 轮询上限**
- 最大时长（例如 10 分钟）或最大次数
- 超时后标记失败，提示用户重试

---

### Task 14: 渲染 `TaskManager`（仅在 URL tab）

**Files:**
- Modify: `frontend/src/pages/TranscribePage.tsx`

**Step 1: 在 URL 模式下渲染**
- `UrlTranscribe`
- `TaskManager`（同一 CardContent 内或下方）

**Step 2: 绑定回调**
- `onViewResult`
- `onRemove`
- `onRetry`
- `onRefresh`

---

### Task 15: “查看结果”加载到现有展示区（Timeline + TranscriptView）

**Files:**
- Modify: `frontend/src/pages/TranscribePage.tsx`

**Step 1: 点击查看**
- `setResult(transcribeResponse)`
- 清理 `selectedSentence/selectedIndex`（避免旧选择影响新结果）

---

### Task 16: 成功任务写入历史（可选但实用）

**Files:**
- Modify: `frontend/src/pages/TranscribePage.tsx`

**Step 1: 从 URL 生成 filename**
- 解析 pathname 取最后一段作为显示名（无则用 task_id）

**Step 2: 写入 `useHistoryStore.addItem(...)`**

---

### Task 17: 后端切换不影响已提交任务

**Files:**
- Modify: `frontend/src/pages/TranscribePage.tsx`
- Modify: `frontend/src/lib/api/transcribe.ts`

**Step 1: 确保每次轮询都用 `task.backendBaseUrl`**

**Step 2: 手工验证步骤**
- 提交 URL（后端 A）
- 切换到后端 B
- 任务仍能完成并取到结果

---

### Task 18: ESLint + Build 验证

**Files:**
- (No code changes required unless lint fails)

**Step 1: Lint**

Run: `cd frontend && npm run lint`

Expected: 0 errors

**Step 2: Build**

Run: `cd frontend && npm run build`

Expected: exit code 0

---

### Task 19: 前端变更提交

**Files:**
- Modify: `frontend/src/lib/api/types.ts`
- Modify: `frontend/src/lib/api/transcribe.ts`
- Modify: `frontend/src/components/url/UrlTranscribe.tsx`
- Modify: `frontend/src/pages/TranscribePage.tsx`
- (Maybe) Modify: `frontend/src/components/task/TaskManager.tsx`

**Step 1: Commit**

```bash
git add frontend/src/lib/api/types.ts \
  frontend/src/lib/api/transcribe.ts \
  frontend/src/components/url/UrlTranscribe.tsx \
  frontend/src/pages/TranscribePage.tsx \
  frontend/src/components/task/TaskManager.tsx
git commit -m "frontend: implement URL async transcribe task queue"
```

---

### Task 20: 终验 + 推送 main

**Step 1: 后端全测**

Run: `.venv/bin/pytest -q`

Expected: PASS

**Step 2: 前端构建**

Run: `cd frontend && npm run build`

Expected: PASS

**Step 3: Push**

Run: `git push origin main`

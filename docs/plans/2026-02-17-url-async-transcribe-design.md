# Xiyu URL 异步转写（前端任务队列）设计

**日期**: 2026-02-17

## 背景 / 问题

Xiyu 现有「文件上传转写」已经支持：
- 多后端（前端选择 `baseURL` 指向不同容器 / 端口）
- 统一的转写结果展示（时间轴、全文、复制、导出等）

但「URL 转写」目前仅有后端异步接口雏形与前端输入框，缺少：
- 前端真正调用 URL 异步接口
- 任务状态轮询
- 完成后把结果加载进现有的 `TranscriptView` / `Timeline`
- 与同步 `/api/v1/transcribe` 一致的结果 schema（以便复用 UI 与导出逻辑）

同时，用户场景强调：
- 不做“router 作为主要头”来自动选模型；前端直接选择后端即可
- 说话人（diarization）能力按后端实际支持为准：支持则输出 speaker，不支持则不强行补（可选 fallback 另算）

## 目标（Goals）

1. **URL 异步转写 end-to-end 可用**
   - 提交 URL → 任务队列显示 → 自动轮询 → 成功后可点击查看结果
2. **复用现有结果展示能力**
   - 结果结构与 `/api/v1/transcribe` 对齐，保证时间轴/导出/后处理逻辑可直接复用
3. **多后端一致性**
   - 提交任务时绑定一个后端 `baseURL` 快照；轮询与取结果必须打到同一个后端，避免用户切换后端导致 “task_id 找不到”
4. **说话人输出 best-effort**
   - 后端支持 speaker：返回 `speaker_id` / `speaker_turns`
   - 不支持：按后端策略忽略或报错（前端不做强制补齐）

## 非目标（Non-goals）

- 不实现“自动路由/自动选择模型”能力（前端选择后端即可）
- 不把任务队列持久化到 `localStorage`（页面刷新即丢失，先保证闭环可用）
- 不实现 WebSocket 推流进度（后端当前 task manager 也不暴露进度）
- 不默认启用 speaker fallback diarization（可作为高级开关/后端策略）

## 方案选型

### 方案 A：最小实现（单任务）
- URL 提交后，页面直接轮询直到完成，然后直接展示结果
- 优点：最快
- 缺点：无法同时跑多个 URL、缺少可管理性

### 方案 B：任务队列（推荐）
- 使用现有 `TaskManager` 组件展示任务列表
- 支持多个 URL 并行转写，完成后点击 “查看”
- 优点：符合“异步任务”心智模型，可扩展
- 缺点：需要更多前端状态管理

### 方案 C：持久化队列
- 队列存储到 `localStorage`，刷新后可恢复并继续轮询
- 优点：体验最好
- 缺点：实现复杂、需要处理过期 task、跨后端等边界

本次采用 **方案 B**。

## 后端 API 设计（保持现有端点，补齐 schema 对齐）

### 提交任务：`POST /api/v1/trans/url`

- Content-Type: `multipart/form-data`（FastAPI `Form(...)`）
- 字段：
  - `audio_url` (required)
  - `with_speaker` (optional, default `false`)
  - `apply_hotword` (optional, default `true`)
  - `apply_llm` (optional, default `false`)
  - `llm_role` (optional, default `"default"`)
  - `hotwords` (optional)
  - `asr_options` (optional, JSON string)
- 返回：
  - `code: 200`
  - `status: "success"`
  - `data.task_id`

### 查询结果：`POST /api/v1/result`

- Content-Type: `multipart/form-data`
- 字段：
  - `task_id` (required)
  - `delete` (optional, default `true`)
- 返回：
  - PENDING/PROCESSING → `code: 202`, `status: "pending"|"processing"`
  - FAILED → `code: 500`, `status: "error"`, `message` 含错误
  - COMPLETED → `code: 200`, `status: "success"`, `data` 为最终结果

### 关键约束：最终结果 schema 与 `/api/v1/transcribe` 对齐

即 `data` 必须是：
- `code: 0`
- `text: string`
- `text_accu?: string | null`
- `sentences: Array<{ text, start(ms), end(ms), speaker?, speaker_id? }>`
- `speaker_turns?: ...`
- `transcript?: string`
- `raw_text?: string`

**特别注意：时间戳必须保持毫秒整数，不应转换为 SRT 字符串**，否则时间轴/导出无法复用。

## 前端设计

### 组件复用
- `UrlTranscribe`：输入 URL + 提交（转写选项统一由右侧 `TranscribeOptions` 配置）
- `TaskManager`：任务列表 UI（等待/处理中/完成/失败 + 查看/删除/重试）
- `TranscriptView` / `Timeline`：展示最终结果

### 状态模型（页面内）

`TranscribePage` 持有：
- `tasks: Task[]`（用于 `TaskManager`）
- 每个 task 保存：
  - `id`（task_id）
  - `status`（pending/processing/success/error）
  - `url`
  - `createdAt`
  - `backendBaseUrl`（提交瞬间的 baseURL 快照）
  - `result?: TranscribeResponse`（成功时）
  - `error?: string`（失败时）

### 数据流

1. 用户在 URL tab 提交：
   - 调用 `transcribeUrl(audioUrl, options, { baseURL: snapshot })`
   - 立即在任务列表插入一条 `pending` 任务
2. 启动轮询：
   - 轮询 `getTaskResult(taskId, { delete: false, baseURL: snapshot })`
   - 根据返回 `status` 更新任务状态
3. 成功：
   - 再请求一次 `getTaskResult(taskId, { delete: true, baseURL: snapshot })`（或在成功返回里直接用结果并 `delete=true`）
   - 将 `TranscribeResponse` 缓存在 task 上
   - 用户点击 “查看” → `setResult(transcribeResponse)`，进入现有展示流程

### 轮询策略（简单稳定）
- interval：1s 起步，processing 时可放到 2s
- 最大轮询时长：例如 10 分钟（超时后标记失败，提示用户重试）
- 出错后允许 “重试”（重新提交 URL，拿新 task_id）

### 多后端兼容（baseURL 快照）
- 任务创建时记录当时的 `baseURL`
- 所有轮询/取结果必须用该 `baseURL`（axios per-request override）
- 即使用户在 UI 切换后端，也不影响已提交任务

## 错误处理与边界

- URL 校验失败：前端直接阻止提交（`UrlTranscribe` 已实现）
- 提交接口返回非 200/超时：任务失败态 + toast
- `result` 返回 404（任务不存在或已过期）：任务失败态 + 提示重新提交
- speaker 不支持：按后端行为（ignore/error/fallback）；前端展示以返回结果为准

## 测试与验收

### 后端
- 单测锁定 URL handler 结果 schema：`tests/test_async_transcribe.py`
- 运行：`.venv/bin/pytest -q`

### 前端
- 类型检查/构建：`cd frontend && npm run build`
- 手工验收：
  - URL 提交后任务进入队列
  - 任务状态可从 pending→processing→success
  - 点击查看后能展示时间轴 + transcript
  - 切换后端不会影响已提交任务的轮询（仍能正确取回结果）

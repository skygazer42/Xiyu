# Xiyu 语音转写服务 API 文档

> 版本: 1.0.0 | 更新日期: 2026-02-18

## 目录

- [概述](#概述)
- [认证](#认证)
- [通用响应格式](#通用响应格式)
- [系统接口](#系统接口)
- [转写接口](#转写接口)
- [异步转写接口](#异步转写接口)
- [热词管理接口](#热词管理接口)
- [配置管理接口](#配置管理接口)
- [WebSocket 实时转写](#websocket-实时转写)
- [数据结构](#数据结构)
- [错误码](#错误码)

---

## 概述

Xiyu 是一个高性能的中文语音转写服务，支持：

- **多后端支持**: PyTorch、ONNX、SenseVoice、GGUF、Whisper、Qwen3-ASR(远程包装)、VibeVoice-ASR(远程包装)、Router
- **实时流式转写**: WebSocket 双向识别
- **说话人识别**: 自动区分多人对话
- **热词纠错**: 支持动态热词注入
- **LLM 润色**: 支持多种 LLM 角色纠错
- **文本后处理**: ITN、标点恢复、繁简转换等

> 说明：Qwen3 / VibeVoice 在本项目中通常以“远程 ASR + Xiyu wrapper”的方式部署。
> 也就是说，真正提供 Xiyu API 的是 `xiyu-qwen3` / `xiyu-vibevoice` 容器；而 `qwen3-asr` / `vibevoice-asr` 是 OpenAI-compatible 服务端。

**基础URL**: `http://localhost:18200`

---

## 认证

当前版本无需认证，所有接口均可直接访问。

---

## 通用响应格式

本服务接口目前**没有统一的** `{code, message, data}` 外层封装；请以各接口的响应示例为准。

- 多数成功响应包含 `code` 字段（例如 `0/200/202`），并直接返回业务字段（如 `text`、`sentences`）。
- 发生参数校验/处理错误时，FastAPI 通常返回 HTTP `4xx/5xx`，body 形如：`{"detail": "..."}`。

### 错误响应（FastAPI 默认）

```json
{
  "detail": "错误描述"
}
```

---

## 系统接口

### 获取服务信息

获取服务基本信息（或前端 UI）。

```
GET /
```

说明：

- `GET /` **默认返回服务信息 JSON**（便于脚本/健康探测）。
- 当请求头 `Accept` 包含 `text/html`，且已构建前端（存在 `frontend/dist`，例如 Docker 镜像内置前端）时，`GET /` 会返回 **Web UI (HTML)**。

如果你需要稳定的 JSON（不受前端构建状态或浏览器 Accept 头影响），请使用：

```
GET /service-info
```

**JSON 响应示例**:

```json
{
  "name": "Xiyu Speech Service",
  "version": "1.0.0",
  "docs": "/docs"
}
```

---

### 健康检查

检查服务运行状态。

```
GET /health
```

**响应示例**:

```json
{
  "status": "healthy",
  "version": "1.0.0"
}
```

---

### 获取服务指标 (JSON)

获取服务性能指标，JSON 格式。

```
GET /metrics
```

**响应示例**:

```json
{
  "uptime_seconds": 3600.5,
  "total_requests": 1250,
  "successful_requests": 1200,
  "failed_requests": 50,
  "total_audio_seconds": 45000.0,
  "avg_rtf": 0.15,
  "llm_cache_stats": {
    "enabled": true,
    "size": 256,
    "max_size": 1000,
    "ttl": 3600
  }
}
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| uptime_seconds | float | 服务运行时长 (秒) |
| total_requests | int | 总请求数 |
| successful_requests | int | 成功请求数 |
| failed_requests | int | 失败请求数 |
| total_audio_seconds | float | 累计处理音频时长 (秒) |
| avg_rtf | float | 平均实时因子 (RTF < 1 表示快于实时) |
| llm_cache_stats | object | LLM 缓存统计 |

---

### 获取服务指标 (Prometheus)

获取服务性能指标，Prometheus 格式。

```
GET /metrics/prometheus
```

**响应示例**:

```text
# HELP xiyu_uptime_seconds Service uptime in seconds
# TYPE xiyu_uptime_seconds gauge
xiyu_uptime_seconds 3600.5

# HELP xiyu_requests_total Total number of requests
# TYPE xiyu_requests_total counter
xiyu_requests_total 1250

# HELP xiyu_requests_successful_total Successful requests
# TYPE xiyu_requests_successful_total counter
xiyu_requests_successful_total 1200

# HELP xiyu_requests_failed_total Failed requests
# TYPE xiyu_requests_failed_total counter
xiyu_requests_failed_total 50

# HELP xiyu_audio_seconds_total Total audio processed in seconds
# TYPE xiyu_audio_seconds_total counter
xiyu_audio_seconds_total 45000.0

# HELP xiyu_rtf_avg Average Real-Time Factor
# TYPE xiyu_rtf_avg gauge
xiyu_rtf_avg 0.15
```

---

### 获取当前后端信息（前端探测）

用于多容器/多端口部署时，前端探测当前服务实例的后端类型与能力。

```
GET /api/v1/backend
```

**响应示例**:

```json
{
  "backend": "pytorch",
  "info": {
    "name": "PyTorchBackend",
    "type": "pytorch",
    "supports_streaming": true,
    "supports_hotwords": true,
    "supports_speaker": true,
    "loaded": true
  },
  "capabilities": {
    "supports_speaker": true,
    "supports_streaming": true,
    "supports_hotwords": true,
    "supports_speaker_fallback": false,
    "supports_speaker_external": false,
    "speaker_strategy": "native"
  },
  "speaker_unsupported_behavior": "ignore"
}
```

**capabilities 说明**:

- `supports_speaker`：当前后端原生是否支持说话人识别（由 backend 决定）
- `supports_speaker_fallback`：是否启用了“fallback diarization”（辅助 Xiyu 服务生成分段）
- `supports_speaker_external`：是否启用了“external diarizer”（`xiyu-diarizer`，pyannote）
- `speaker_strategy`：当请求 `with_speaker=true` 时，当前实例会采用的策略：
  - `external`：强制 external diarizer（推荐会议场景，所有后端统一输出 `speaker_turns`）
  - `native`：后端原生 speaker
  - `fallback_diarization`：使用辅助 Xiyu 服务生成分段，再按 turn 切片转写
  - `fallback_backend`：回退到 PyTorch 后端执行 speaker（可能违背“端口=模型”预期）
  - `ignore`：忽略 speaker（按 `with_speaker=false` 处理）
  - `error`：直接报错（HTTP 400）

---

## 转写接口

### 单文件转写

上传音频文件进行转写。

```
POST /api/v1/transcribe
Content-Type: multipart/form-data
```

**请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| file | File | ✅ | - | 音频文件 (wav, mp3, m4a, flac, ogg 等) |
| with_speaker | bool | ❌ | false | 是否启用说话人识别 |
| apply_hotword | bool | ❌ | true | 是否应用热词纠错 |
| apply_llm | bool | ❌ | false | 是否启用 LLM 润色 |
| llm_role | string | ❌ | "default" | LLM 角色（单模型润色）。推荐（政务/政策会议）：`policy_polish_strict` / `policy_polish_balanced` / `policy_polish_aggressive` |
| include_srt | bool | ❌ | false | 是否在响应中包含 `srt` 字幕内容（基于 `sentences/speaker_turns` 生成） |
| hotwords | string | ❌ | null | 临时热词 (空格分隔) |
| asr_options | string | ❌ | null | 请求级调参 JSON 字符串（例如 `{"postprocess": {...}}`） |

**响应示例**:

```json
{
  "code": 0,
  "text": "今天的会议主要讨论人工智能的应用。",
  "text_accu": null,
  "sentences": [
    {
      "text": "今天的会议主要讨论人工智能的应用。",
      "start": 0,
      "end": 3500,
      "speaker": "说话人甲",
      "speaker_id": 0
    }
  ],
  "speaker_turns": [
    {
      "speaker": "说话人甲",
      "speaker_id": 0,
      "start": 0,
      "end": 3500,
      "text": "今天的会议主要讨论人工智能的应用。",
      "sentence_count": 1
    }
  ],
  "transcript": "[00:00 - 00:03] 说话人甲: 今天的会议主要讨论人工智能的应用。",
  "srt": "1\\n00:00:00,000 --> 00:00:03,500\\n[说话人甲] 今天的会议主要讨论人工智能的应用。\\n",
  "raw_text": "今天的会议主要讨论人工只能的应用",
  "overview": null,
  "overview_task_id": "9f0d... (示例)"
}
```

#### 会议概览（自动生成，可选）

当满足以下条件时，服务会在转写完成后自动触发“会议概览”生成：

- `LLM_ENABLE=true`
- `MEETING_OVERVIEW_ENABLE=true`（默认开启；可在 Web UI 配置管理页运行时开关）

字段说明：

- `overview`: 会议概览正文（2-5 段，政务口径，LLM 生成）。有些场景会直接返回。
- `overview_task_id`: 概览异步任务 ID。若该字段存在且 `overview` 为空，可通过 `/api/v1/result` 轮询获取概览结果：

```bash
curl -X POST "http://localhost:18200/api/v1/result" \
  -F "task_id=<overview_task_id>" \
  -F "delete=false"
```

成功时返回：

```json
{
  "code": 200,
  "status": "success",
  "message": "获取结果成功",
  "data": {
    "overview": "..."
  }
}
```

**cURL 示例**:

```bash
curl -X POST "http://localhost:18200/api/v1/transcribe" \
  -F "file=@audio.wav" \
  -F "with_speaker=true" \
  -F "apply_hotword=true"
```

---

### 批量文件转写

批量上传多个音频文件进行转写。

```
POST /api/v1/transcribe/batch
Content-Type: multipart/form-data
```

**请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| files | File[] | ✅ | - | 多个音频文件 |
| with_speaker | bool | ❌ | false | 是否启用说话人识别 |
| apply_hotword | bool | ❌ | true | 是否应用热词纠错 |
| apply_llm | bool | ❌ | false | 是否启用 LLM 润色 |
| llm_role | string | ❌ | "default" | LLM 角色（单模型润色）。推荐（政务/政策会议）：`policy_polish_strict` / `policy_polish_balanced` / `policy_polish_aggressive` |
| hotwords | string | ❌ | null | 临时热词 |
| asr_options | string | ❌ | null | 请求级调参 JSON 字符串（同单文件） |
| max_concurrent | int | ❌ | 3 | 最大并发数 |

**响应示例**:

```json
{
  "code": 0,
  "total": 3,
  "success_count": 2,
  "failed_count": 1,
  "results": [
    {
      "index": 0,
      "filename": "audio1.mp3",
      "success": true,
      "result": {
        "code": 0,
        "text": "转写文本...",
        "sentences": [...]
      }
    },
    {
      "index": 1,
      "filename": "audio2.wav",
      "success": true,
      "result": { ... }
    },
    {
      "index": 2,
      "filename": "audio3.flac",
      "success": false,
      "error": "音频格式不支持"
    }
  ]
}
```

---

### 全量转写（多模型对比 + LLM 融合）

该接口会在同一份音频上并发调用多个后端（Compose 多容器网络内），并可选调用一次 LLM 进行融合润色，返回：

- `candidates`：各后端候选结果（用于对比/追踪）
- `final`：最终融合后的转写结果（结构与单文件接口一致）

```
POST /api/v1/transcribe/all
Content-Type: multipart/form-data
```

**请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| file | File | ✅ | - | 音频文件 |
| with_speaker | bool | ❌ | true | 是否启用说话人（推荐开启，作为 base turns 骨架） |
| apply_hotword | bool | ❌ | true | 是否应用热词纠错 |
| apply_llm | bool | ❌ | true | 是否调用 LLM 做融合润色（依赖 `LLM_ENABLE=true` 配置） |
| llm_role | string | ❌ | "policy_meeting_aggressive" | LLM 角色（多模型融合）：`policy_meeting`（严格）/ `policy_meeting_v2`（平衡）/ `policy_meeting_aggressive`（激进） |
| include_srt | bool | ❌ | true | 是否在 `final` 中包含 `srt` 字幕内容 |
| hotwords | string | ❌ | null | 临时热词 (空格分隔) |
| asr_options | string | ❌ | null | 请求级调参 JSON 字符串（同单文件） |

**响应示例**:

```json
{
  "code": 0,
  "base_backend": "pytorch",
  "llm_used": true,
  "llm_role": "policy_meeting_aggressive",
  "candidates": [
    {
      "backend": "pytorch",
      "base_url": "http://xiyu-pytorch:8000",
      "success": true,
      "http_status": 200,
      "code": 0,
      "elapsed_ms": 1234,
      "text": "候选文本（可能截断）",
      "cleaned_text": "用于 LLM 参考的清洗文本（可能截断）",
      "error": null
    }
  ],
  "final": {
    "code": 0,
    "text": "最终融合后的听记稿...",
    "sentences": [
      { "text": "…", "start": 0, "end": 1000, "speaker": "说话人甲", "speaker_id": 0 }
    ],
    "speaker_turns": [
      { "speaker": "说话人甲", "speaker_id": 0, "start": 0, "end": 1000, "text": "…", "sentence_count": 1 }
    ],
    "transcript": "[00:00 - 00:01] 说话人甲: …",
    "srt": "1\\n00:00:00,000 --> 00:00:01,000\\n[说话人甲] …\\n"
  }
}
```

---

## 异步转写接口

### URL 转写 (异步)

从 URL 下载音频并进行转写，返回任务 ID。

```
POST /api/v1/trans/url
Content-Type: multipart/form-data
```

**请求参数（表单）**:

**请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| audio_url | string | ✅ | - | 音频文件 URL |
| with_speaker | bool | ❌ | false | 是否启用说话人识别 |
| apply_hotword | bool | ❌ | true | 是否应用热词纠错 |
| apply_llm | bool | ❌ | false | 是否启用 LLM 润色 |
| llm_role | string | ❌ | "default" | LLM 角色 |
| hotwords | string | ❌ | null | 临时热词 (空格分隔) |
| asr_options | string | ❌ | null | 请求级调参 JSON 字符串（同单文件） |

**响应示例**:

```json
{
  "code": 200,
  "status": "success",
  "message": "任务已提交",
  "data": {
    "task_id": "task_abc123"
  }
}
```

**cURL 示例**:

```bash
curl -X POST "http://localhost:18200/api/v1/trans/url" \
  -F "audio_url=https://example.com/audio.mp3" \
  -F "with_speaker=false" \
  -F "apply_hotword=true"
```

---

### 获取异步任务结果

查询异步任务的执行结果。

```
POST /api/v1/result
Content-Type: multipart/form-data
```

**请求参数（表单）**:

**请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| task_id | string | ✅ | - | 任务 ID |
| delete | bool | ❌ | true | 获取后是否删除任务 |

**响应示例 (进行中)**:

```json
{
  "code": 202,
  "status": "processing",
  "message": "任务处理中",
  "data": {
    "task_id": "task_abc123"
  }
}
```

**响应示例 (完成)**:

```json
{
  "code": 200,
  "status": "success",
  "message": "获取结果成功",
  "data": {
    "code": 0,
    "text": "转写文本...",
    "text_accu": null,
    "sentences": [...],
    "speaker_turns": null,
    "transcript": null,
    "raw_text": "转写文本..."
  }
}
```

**任务状态**:

| 状态 | 说明 |
|------|------|
| pending | 等待处理 |
| processing | 处理中 |
| success | 成功完成 |
| error | 处理失败 |

---

### Whisper 兼容接口

兼容 Whisper ASR WebService 格式的转写接口。

```
POST /api/v1/asr
Content-Type: multipart/form-data
```

> 说明：该接口仅保证**返回格式**兼容 Whisper ASR WebService。
> 实际使用的模型/后端由当前 Xiyu 服务实例的 `ASR_BACKEND` 决定（可通过 `GET /api/v1/backend` 探测）。

**请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| file | File | ✅ | - | 音频或视频文件 |
| file_type | string | ❌ | "audio" | 文件类型 (audio/video) |
| with_speaker | bool | ❌ | true | 是否启用说话人识别 |
| apply_hotword | bool | ❌ | true | 是否应用热词纠错 |

**响应示例**:

```json
{
  "text": "完整转写文本",
  "segments": [
    {
      "sentence_index": 1,
      "text": "第一句话",
      "start": "00:00:00.000",
      "end": "00:00:02.500",
      "speaker": "说话人甲"
    },
    {
      "sentence_index": 2,
      "text": "第二句话",
      "start": "00:00:02.600",
      "end": "00:00:05.000",
      "speaker": "说话人乙"
    }
  ],
  "language": "zh"
}
```

---

### 视频转写

上传视频文件，提取音轨进行转写。

```
POST /api/v1/trans/video
Content-Type: multipart/form-data
```

**请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| file | File | ✅ | - | 视频文件 (mp4, avi, mkv, mov, webm) |
| with_speaker | bool | ❌ | false | 是否启用说话人识别 |
| apply_hotword | bool | ❌ | true | 是否应用热词纠错 |
| apply_llm | bool | ❌ | false | 是否启用 LLM 润色 |
| llm_role | string | ❌ | "default" | LLM 角色 |
| hotwords | string | ❌ | null | 临时热词 (空格分隔) |
| asr_options | string | ❌ | null | 请求级调参 JSON 字符串（同单文件） |

**响应示例**:

```json
{
  "code": 0,
  "text": "视频中的语音转写文本...",
  "text_accu": null,
  "sentences": [
    {
      "text": "欢迎观看本视频",
      "start": 1000,
      "end": 3000
    }
  ],
  "speaker_turns": null,
  "transcript": null,
  "raw_text": "欢迎观看本视频"
}
```

**支持的视频格式**:

- MP4 (.mp4)
- AVI (.avi)
- MKV (.mkv)
- MOV (.mov)
- WebM (.webm)

---

## 热词管理接口

### 获取热词列表

获取当前配置的所有热词。

```
GET /api/v1/hotwords
```

**响应示例**:

```json
{
  "code": 0,
  "hotwords": [
    "人工智能",
    "机器学习",
    "深度学习",
    "Xiyu"
  ],
  "count": 4
}
```

---

### 更新热词列表

替换全部热词（覆盖现有热词）。

```
POST /api/v1/hotwords
Content-Type: application/json
```

**请求体**:

```json
{
  "hotwords": [
    "热词1",
    "热词2 # 带注释",
    "热词3"
  ]
}
```

**响应示例**:

```json
{
  "code": 0,
  "count": 3,
  "message": "热词已更新"
}
```

---

### 追加热词

在现有热词基础上追加新热词。

```
POST /api/v1/hotwords/append
Content-Type: application/json
```

**请求体**:

```json
{
  "hotwords": [
    "新热词1",
    "新热词2"
  ]
}
```

**响应示例**:

```json
{
  "code": 0,
  "count": 6,
  "message": "热词已追加"
}
```

---

### 重载热词文件

从配置的热词文件重新加载热词。

```
POST /api/v1/hotwords/reload
```

**响应示例**:

```json
{
  "code": 0,
  "count": 50,
  "message": "热词已从文件重新加载"
}
```

---

## 配置管理接口

### 获取运行时配置

获取可在运行时修改的配置项。

```
GET /config
```

**响应示例**:

```json
{
  "config": {
    "text_correct_enable": false,
    "text_correct_backend": "kenlm",
    "hotwords_threshold": 0.85,
    "hotword_injection_enable": true,
    "hotword_injection_max": 50,
    "llm_enable": true,
    "llm_role": "corrector",
    "filler_remove_enable": false,
    "itn_enable": true,
    "audio_normalize_enable": true,
    "audio_denoise_enable": false
  }
}
```

---

### 获取完整配置

获取所有配置项（包括只读项）。

```
GET /config/all
```

**响应示例**:

```json
{
  "config": {
    "app_name": "Xiyu Speech Service",
    "version": "1.0.0",
    "asr_backend": "pytorch",
    "device": "cuda",
    "text_correct_enable": false,
    "llm_enable": true
  },
  "mutable_keys": [
    "text_correct_enable",
    "text_correct_backend",
    "hotwords_threshold",
    "llm_enable",
    "llm_role"
  ]
}
```

---

### 更新配置

更新运行时配置项。

```
POST /config
Content-Type: application/json
```

**请求体**:

```json
{
  "updates": {
    "llm_enable": true,
    "llm_role": "corrector",
    "hotwords_threshold": 0.9
  }
}
```

**响应示例**:

```json
{
  "config": {
    "llm_enable": true,
    "llm_role": "corrector",
    "hotwords_threshold": 0.9
  }
}
```

**可修改的配置项**:

| 分类 | 配置项 | 类型 | 说明 |
|------|--------|------|------|
| 纠错 | text_correct_enable | bool | 启用文本纠错 |
| 纠错 | text_correct_backend | string | 纠错后端 (kenlm/macbert) |
| 纠错 | correction_pipeline | string | 纠错管线 |
| 纠错 | confidence_threshold | float | 置信度阈值 |
| 纠错 | confidence_fallback | string | 低置信度回退策略 |
| 热词 | hotwords_threshold | float | 热词匹配阈值 |
| 热词 | hotword_injection_enable | bool | 热词注入开关 |
| 热词 | hotword_injection_max | int | 最大注入热词数 |
| LLM | llm_enable | bool | 启用 LLM |
| LLM | llm_role | string | LLM 角色 |
| LLM | llm_fulltext_enable | bool | 全文纠错模式 |
| LLM | llm_batch_size | int | 批量大小 |
| LLM | llm_context_sentences | int | 上下文句子数 |
| 后处理 | filler_remove_enable | bool | 填充词移除 |
| 后处理 | filler_aggressive | bool | 激进模式 |
| 后处理 | qj2bj_enable | bool | 全角转半角 |
| 后处理 | itn_enable | bool | 数字格式化 |
| 后处理 | itn_erhua_remove | bool | 儿化移除 |
| 后处理 | spacing_cjk_ascii_enable | bool | 中英文间距 |
| 后处理 | zh_convert_enable | bool | 繁简转换 |
| 后处理 | zh_convert_locale | string | 转换目标 |
| 后处理 | punc_convert_enable | bool | 标点转换 |
| 后处理 | punc_restore_enable | bool | 标点恢复 |
| 后处理 | punc_merge_enable | bool | 标点合并 |
| 后处理 | trash_punc_enable | bool | 末尾标点移除 |
| 后处理 | trash_punc_chars | string | 移除的标点字符 |
| 音频 | audio_normalize_enable | bool | 音量归一化 |
| 音频 | audio_denoise_enable | bool | 降噪开关 |
| 音频 | audio_denoise_backend | string | 降噪后端 |
| 音频 | audio_vocal_separate_enable | bool | 人声分离 |
| 音频 | audio_trim_silence_enable | bool | 静音裁剪 |

---

### 重载引擎

重新初始化转写引擎（配置变更后需要）。

```
POST /config/reload
```

**响应示例**:

```json
{
  "status": "success",
  "message": "转写引擎已重新加载"
}
```

---

## WebSocket 实时转写

### 连接

```
WebSocket ws://localhost:18200/ws/realtime
```

### 消息格式

#### 客户端 → 服务端

**1. 配置消息 (JSON)**

```json
{
  "is_speaking": true,
  "mode": "2pass",
  "hotwords": "热词1 热词2",
  "chunk_interval": 10
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| is_speaking | bool | 是否正在说话 (false 触发离线识别) |
| mode | string | 识别模式: 2pass/online/offline |
| hotwords | string | 热词 (空格分隔) |
| chunk_interval | int | 在线识别间隔 (帧数) |

**2. 音频数据 (Binary)**

- 格式: PCM 16bit, 16kHz, mono
- 推荐块大小: 9600 bytes (600ms)

**3. 心跳响应 (JSON)**

```json
{
  "type": "pong"
}
```

**4. 取消 LLM 生成 (JSON)**

```json
{
  "type": "cancel_llm"
}
```

#### 服务端 → 客户端

**1. 连接确认**

```json
{
  "type": "connected",
  "connection_id": "conn_abc123",
  "config": {
    "chunk_size": 9600,
    "heartbeat_interval": 30,
    "compression": false
  }
}
```

**2. 转写结果**

```json
{
  "mode": "2pass-online",
  "text": "实时识别文本",
  "is_final": false
}
```

| mode 值 | 说明 |
|---------|------|
| 2pass-online | 两遍识别 - 在线结果 |
| 2pass-offline | 两遍识别 - 离线结果 |
| online | 仅在线模式结果 |
| offline | 仅离线模式结果 |

**3. 心跳**

```json
{
  "type": "ping",
  "timestamp": 1706860800.123
}
```

**4. 警告**

```json
{
  "warning": "流式识别不可用",
  "backend": "onnx"
}
```

### 使用示例

```javascript
const ws = new WebSocket('ws://localhost:18200/ws/realtime');

ws.onopen = () => {
  // 发送配置
  ws.send(JSON.stringify({
    is_speaking: true,
    mode: '2pass',
    hotwords: '人工智能 机器学习'
  }));
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);

  if (msg.type === 'connected') {
    console.log('已连接:', msg.connection_id);
  } else if (msg.type === 'ping') {
    ws.send(JSON.stringify({ type: 'pong' }));
  } else if (msg.text) {
    console.log(`[${msg.mode}] ${msg.text}`);
  }
};

// 发送音频数据
function sendAudio(pcmData) {
  ws.send(pcmData); // ArrayBuffer
}

// 停止说话，触发离线识别
function stopSpeaking() {
  ws.send(JSON.stringify({ is_speaking: false }));
}

// 取消 LLM 生成
function cancelLLM() {
  ws.send(JSON.stringify({ type: 'cancel_llm' }));
}
```

---

## 数据结构

### SentenceInfo

句子级别信息。

```typescript
interface SentenceInfo {
  text: string;          // 句子文本
  start: number;         // 开始时间 (毫秒)
  end: number;           // 结束时间 (毫秒)
  speaker?: string;      // 说话人标签 (可选)
  speaker_id?: number;   // 说话人 ID (可选)
}
```

### SpeakerTurn

说话人 turn/段落（按连续发言合并的片段）。

```typescript
interface SpeakerTurn {
  speaker: string;       // 说话人标签（说话人甲/乙/… 或 说话人1/2/3）
  speaker_id: number;    // 说话人 ID（从 0 开始的顺序号）
  start: number;         // 开始时间 (毫秒)
  end: number;           // 结束时间 (毫秒)
  text: string;          // turn 文本
  sentence_count: number;// 合并的句子数
}
```

### TranscribeResponse

转写响应。

```typescript
interface TranscribeResponse {
  code: number;                    // 状态码（通常 0=成功）
  text: string;                    // 完整转写文本
  text_accu?: string | null;       // 精确拼接文本（长音频 overlap 去重更严格）
  sentences: SentenceInfo[];       // 句子列表
  speaker_turns?: SpeakerTurn[] | null; // 说话人 turn（with_speaker=true 时可能返回）
  transcript?: string | null;      // 格式化转写稿（with_speaker=true 时可能返回）
  raw_text?: string | null;        // 原始文本 (未纠错)
}
```

### BatchTranscribeResponse

批量转写响应。

```typescript
interface BatchTranscribeResponse {
  code: number;
  total: number;                   // 总文件数
  success_count: number;           // 成功数
  failed_count: number;            // 失败数
  results: BatchTranscribeItem[];  // 结果列表
}

interface BatchTranscribeItem {
  index: number;                   // 文件索引
  filename: string;                // 文件名
  success: boolean;                // 是否成功
  result?: TranscribeResponse;     // 成功时的结果
  error?: string;                  // 失败时的错误信息
}
```

### HotwordsResponse

热词响应。

```typescript
interface HotwordsListResponse {
  code: number;
  hotwords: string[];              // 热词列表
  count: number;                   // 热词数量
}

interface HotwordsUpdateResponse {
  code: number;
  count: number;                   // 更新后数量
  message: string;                 // 操作消息
}
```

### ConfigResponse

配置响应。

```typescript
interface ConfigResponse {
  config: Record<string, any>;     // 配置键值对
  mutable_keys?: string[];         // 可修改的键列表
}
```

### MetricsResponse

指标响应。

```typescript
interface MetricsResponse {
  uptime_seconds: number;          // 运行时长
  total_requests: number;          // 总请求数
  successful_requests: number;     // 成功请求数
  failed_requests: number;         // 失败请求数
  total_audio_seconds: number;     // 累计音频时长
  avg_rtf: number;                 // 平均 RTF
  llm_cache_stats: {
    enabled: boolean;
    size: number;
    max_size: number;
    ttl: number;
  };
}
```

### BackendInfoResponse

后端探测接口 `GET /api/v1/backend` 的响应。

```typescript
interface BackendCapabilities {
  supports_speaker: boolean;
  supports_streaming: boolean;
  supports_hotwords: boolean;
  supports_speaker_fallback?: boolean;
}

interface BackendInfoResponse {
  backend: string;                 // ASR_BACKEND 值
  info: Record<string, any>;       // backend.get_info() 输出（安全元信息）
  capabilities: BackendCapabilities;
  speaker_unsupported_behavior: 'error' | 'fallback' | 'ignore';
}
```

### Async Task Responses

异步 URL 转写（`/api/v1/trans/url` + `/api/v1/result`）的响应结构。

```typescript
interface UrlTranscribeResponse {
  code: number;
  status: 'success' | 'error';
  message: string;
  data?: { task_id: string };
}

interface TaskResultResponse {
  code: number;
  status: 'pending' | 'processing' | 'success' | 'error';
  message: string;
  data?: { task_id: string } | TranscribeResponse;
}
```

---

## 错误码

目前服务端主要使用 **HTTP 状态码**表达错误语义（例如 `400/422/500`），响应体通常为 FastAPI 默认格式：

```json
{
  "detail": "错误描述"
}
```

少数接口会在成功响应中包含 `code` 字段，但该字段并不是一个全局稳定的“错误码体系”。建议前端按以下方式处理：

- 同步转写（`/api/v1/transcribe` / `.../batch` / `.../trans/video`）：`code === 0` 视为成功
- 异步任务（`/api/v1/trans/url` + `/api/v1/result`）：以 `status`（pending/processing/success/error）为主

---

## 支持的音频格式

| 格式 | 扩展名 | 说明 |
|------|--------|------|
| WAV | .wav | 推荐格式，无损 |
| MP3 | .mp3 | 有损压缩 |
| M4A | .m4a | AAC 编码 |
| FLAC | .flac | 无损压缩 |
| OGG | .ogg | Vorbis 编码 |
| OPUS | .opus | 低延迟编码 |

**推荐音频参数**:
- 采样率: 16kHz
- 位深: 16bit
- 声道: 单声道 (mono)

---

## 限制说明

| 限制项 | 值 | 说明 |
|--------|-----|------|
| 单文件大小 | 100MB | 可通过配置调整 |
| 批量文件数 | 20 | 单次请求最大文件数 |
| 音频时长 | 无限制 | 长音频会自动分段 |
| WebSocket 心跳 | 30秒 | 超时自动断开 |
| 异步任务保留 | 1小时 | 过期自动清理 |

---

## 更新日志

### v1.0.0 (2026-02-02)

- 初始版本发布
- 支持 PyTorch、ONNX、SenseVoice、GGUF、Qwen3-ASR(远程)、VibeVoice-ASR(远程)、Router
- 实时流式转写 (WebSocket)
- 热词管理和 LLM 润色
- 完整的配置管理接口

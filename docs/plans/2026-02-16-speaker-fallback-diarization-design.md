# Speaker Fallback Diarization (Qwen3-friendly) — Design

**Date:** 2026-02-16  
**Scope:** Xiyu (`/Users/luke/code/xiyu`)  
**Primary scenario:** 多端口多容器部署（`docker-compose.models.yml`），前端选择后端端口；当某些 ASR（如 Qwen3-ASR 1.7B）不支持说话人识别时，仍希望输出“说话人1/2/3…”。

---

## Problem

- 会议转写场景通常需要「说话人区分」来提升可读性（说话人1/2/3…）。
- 现有 Xiyu 能力：
  - PyTorch/FunASR 支持 `with_speaker=true`（返回带 spk 的 `sentence_info`）。
  - Qwen3 remote backend (`src/models/backends/qwen3_remote.py`) 不支持说话人（`supports_speaker=False`），且返回 `sentence_info=[]`。
- 当前行为（v1）在后端不支持说话人时可按配置 `ignore`：继续返回普通转写结果，不报错。
- 需求：**“有能力就做说话人（2），做不了就回退现状（1）”**，并且不把 router 作为主要入口。

---

## Goals

1) 当选定后端（例如 `xiyu-qwen3`）本身不支持 diarization 时，如果用户开启 `with_speaker=true`，系统可选地：
   - 通过一个「辅助 diarization 服务」（建议：`xiyu-pytorch` 容器）得到时间戳 + 说话人分段；
   - 使用原后端（Qwen3）对每个说话人段落做转写；
   - 返回带 `sentences/speaker_turns/transcript` 的结果，标签为 **说话人1/2/3**（numeric）。
2) 失败自动回退到现状：如果辅助服务不可用/超时/报错，保持 **ignore speaker**（不影响正常转写）。
3) 默认不改变现有行为：该能力通过 env/settings 显式开启。

---

## Non-goals (v1)

- 不在 Qwen3 容器内直接内置独立 diarization 模型（依赖/镜像过重）。
- 不实现复杂“跨 turn 上下文补全/对齐”，先保证可用与可解释。
- 不要求所有输入类型都支持切片（优先支持 HTTP 上传的 PCM16LE 16k mono bytes；WAV bytes 尽量兼容）。

---

## Architecture (Recommended)

### Key idea

把“说话人分离”与“转写文本”解耦：

- **Diarization provider（辅助服务）**：输出 `(start_ms, end_ms, speaker_id/speaker)` 的分句结果。
  - 推荐直接复用 Xiyu 的 PyTorch 容器 API：`POST /api/v1/transcribe` + `with_speaker=true`。
- **Primary ASR backend（原后端）**：只负责把音频片段转成文字（Qwen3）。

### Data flow

1) 用户请求：`with_speaker=true`，目标后端为 `qwen3`，并携带（可选）`asr_options.speaker.label_style` 等格式配置。
2) Engine 检测：`backend.supports_speaker == False` 且 `SPEAKER_FALLBACK_DIARIZATION_ENABLE=true`
3) 调用辅助服务（httpx）：
   - 发送同一段音频（WAV bytes）
   - `with_speaker=true`
   - `apply_hotword=false`、`apply_llm=false`（只为拿分段，不做额外处理）
   - 传递 `asr_options.speaker.label_style` 以对齐 label 风格（numeric/zh）
4) 解析辅助服务返回：
   - 读取 `sentences[]`（含 `start/end/speaker/speaker_id`）
   - 使用现有 `build_speaker_turns` 合并为 turn（减少 Qwen3 调用次数）
5) 对每个 turn：
   - 从 PCM16LE bytes 中按 `start/end` 切片
   - 调用 primary backend 转写（`with_speaker=false`）
6) 组装最终返回：
   - `sentences`: 每个 turn 一条 sentence（带 speaker）
   - `speaker_turns`: 同上（turn 结构）
   - `transcript`: 使用 `SpeakerLabeler.format_transcript(..., include_timestamp=True)`
7) 任何步骤失败（网络错误、返回 schema 不符合预期、turn 过多、切片越界等）：
   - 记录 warning
   - 自动回退：按既有 `speaker_unsupported_behavior_effective`（推荐 ignore）继续转写

---

## Configuration

新增 Settings/env（默认关闭）：

- `SPEAKER_FALLBACK_DIARIZATION_ENABLE=false`
- `SPEAKER_FALLBACK_DIARIZATION_BASE_URL=http://xiyu-pytorch:8000`
- `SPEAKER_FALLBACK_DIARIZATION_TIMEOUT_S=30`
- `SPEAKER_FALLBACK_MAX_TURN_DURATION_S=25`（限制单 turn 的最大时长，避免 Qwen3 超时）
- `SPEAKER_FALLBACK_MAX_TURNS=200`（防止极端碎片化导致 N 次请求）

建议只在 `xiyu-qwen3`（或其他不支持 speaker 的后端）容器中启用。

---

## UX / Frontend signal

当前前端通过 `GET /api/v1/backend` 显示 “支持/不支持说话人”。  
若启用 fallback diarization，应增加一个“可通过 fallback 支持说话人”的标识（例如 `capabilities.supports_speaker_fallback`），避免误导用户。

---

## Testing Strategy

关键点：无需真实网络/真实模型即可测逻辑。

- 单元测试覆盖：
  - 当 `backend.supports_speaker=false` 且 fallback 开启时：会尝试调用 fallback（mock httpx），并对每个 turn 调用 backend.transcribe（mock）。
  - fallback 调用失败时：会回退到 ignore（`with_speaker=false`）并返回普通结果。
  - 音频切片函数：边界/对齐（ms→sample→bytes）正确。

---

## Risks / Mitigations

- **性能**：turn 多会产生多次 Qwen3 调用 → `MAX_TURNS` 限制 + 合并 turns。
- **准确性**：turn 切片可能截断上下文 → 可增加少量 padding（v2）。
- **配置错误**：fallback base_url 指向不支持 speaker 的服务 → 失败后自动回退 ignore，且可选探测 `/api/v1/backend`。

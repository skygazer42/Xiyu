# External Diarizer Service (pyannote) — Unified Speaker Turns For All ASR Backends — Design

**Date:** 2026-02-17  
**Scope:** Xiyu (`/Users/luke/code/xiyu`)  
**Primary goal:** 在会议/回忆转写场景中，让所有后端（PyTorch/ONNX/SenseVoice/GGUF/Whisper/Qwen3/VibeVoice…）都能输出一致的 `说话人1/2/3...` + `speaker_turns`，并且在 diarizer 不可用时自动回退，保证可用性。

---

## Background / Problem

- 会议录音通常是“轮流讲话”为主，输出需要明确说话人区分：
  - 便于阅读：`说话人1: ...` / `说话人2: ...`
  - 便于导出：turn 粒度比 sentence 粒度更像“会议纪要”
- Xiyu 当前情况：
  - 部分后端原生支持 `with_speaker=true`（例如 PyTorch/FunASR）。
  - 许多后端不支持 speaker diarization（例如 Qwen3 remote / ONNX / SenseVoice）。
  - 已有 “fallback diarization（复用另一个 Xiyu 服务）” 的方案，但它本质是“只有不支持时才做”，并且仍然依赖 ASR 端返回的 `sentence_info` 结构。
- 需求（本次确定）：
  - **统一**：只要用户 `with_speaker=true`，就优先走一个外置 diarizer，让 speaker 能力与 ASR 后端解耦。
  - **可用性优先**：diarizer 挂了/超时/异常时，自动回退（native speaker 或 ignore）。

---

## Goals

1) **统一 speaker 分离来源**
   - 当 `with_speaker=true` 时，优先使用外置 diarizer 输出说话人分段，所有后端统一输出 `speaker_turns`。

2) **保持 API 兼容**
   - 保持 `POST /api/v1/transcribe` 的请求/响应结构不变（只新增/增强可选字段，不破坏现有字段）。
   - 继续支持 `asr_options.speaker.label_style`（numeric/zh）和 turn 合并参数。

3) **自动回退策略（已确认）**
   - diarizer 失败 → 如果当前后端 `supports_speaker=true`，回退 native speaker；
   - 否则 → 忽略 speaker（退化为普通转写，不报错）。

4) **镜像不膨胀**
   - diarizer 作为单独容器，避免把主 Xiyu 镜像依赖变重。
   - diarizer 模型从 HuggingFace 下载，使用 `HF_TOKEN`，权重缓存到 volume（不会把镜像撑大）。

---

## Non-goals (v1)

- 处理重叠语音（overlap speech）的“完美” diarization（先做 turn-taking 为主的会议场景）。
- 训练/微调任何模型权重。
- 把 diarizer 暴露给前端作为可选后端（它是内部基础设施，不是 ASR 后端）。
- 提供 word-level alignment（字幕级对齐）——后续可选 WhisperX 路线。

---

## Chosen Approach (v1)

### Key idea

把“说话人分离”独立成一个服务 `xiyu-diarizer`：

- `xiyu-diarizer`：只负责 diarization，输出 `(start_ms, end_ms, speaker_id)` 的 segments。
- Xiyu ASR 容器：把 segments 合并成 turns → 切片音频 → 用当前选择的 ASR 后端转写 → 生成 `speaker_turns/transcript`。

这样：
- 前端继续只选择 ASR 容器（端口），不需要知道 diarizer 的存在。
- 会议的 speaker 输出在不同 ASR 后端间可比性更强（同一套 diarization）。

---

## API Contract (Internal)

### `POST /api/v1/diarize`

**Request**: `multipart/form-data`
- `file`: WAV bytes（推荐 16kHz mono；Xiyu 侧会尽量统一转换后再上传）
- 可选（v2 预留）：
  - `min_speakers`, `max_speakers`, `num_speakers`

**Response**: JSON

```json
{
  "segments": [
    { "spk": 0, "start": 120, "end": 1540 },
    { "spk": 1, "start": 1560, "end": 4900 }
  ],
  "duration_ms": 4900,
  "speakers": 2,
  "model": "pyannote/..."
}
```

**Notes**
- `start/end` 为毫秒整数，闭区间/开区间不强制（Xiyu 侧会做 clamp + 排序 + 去异常）。
- `spk` 为 0-based int；Xiyu 会重新映射为连续 speaker_id，并输出 `说话人1/2/3...`。

---

## Xiyu Data Flow (with_speaker=true)

1) Xiyu 接到 `with_speaker=true` 请求（任意 ASR 后端）
2) 若 `SPEAKER_EXTERNAL_DIARIZER_ENABLE=true`：
   - 调用 `POST {base_url}/api/v1/diarize` 得到 `segments`
3) 把 `segments` 映射成 Xiyu 的 sentence-like 结构：
   - `{start,end,spk}` → `SpeakerLabeler.label_speakers([...], spk_key="spk")`
4) turns 合并：
   - 用 `build_speaker_turns(..., gap_ms=..., min_chars=0)` 合并连续同 speaker 的 segments
5) 对每个 turn：
   - 从 PCM16LE bytes 按 `start/end` 切片
   - 调用当前 backend `transcribe(..., with_speaker=False)` 得到 text
   - 对每段 text 应用（可选）`hotword/rules/postprocess`，最后拼接全文 `text`
6) 输出：
   - `sentences`: turn 级别（每个 turn 一条，带 speaker/start/end/text）
   - `speaker_turns`: turn 级别（同 `sentences` 或同结构）
   - `transcript`: `SpeakerLabeler.format_transcript(turns, include_timestamp=True)`

### Failure policy (已确认)

当 external diarizer 调用失败（连接错误/超时/返回空 segments/解析失败/segments 过多等）：
- 若当前 backend 支持 speaker：回退 native speaker（现有逻辑）
- 否则：忽略 speaker（按 `with_speaker=false` 正常转写，不报错）

---

## Configuration

### Xiyu (ASR 容器) 新增设置

- `SPEAKER_EXTERNAL_DIARIZER_ENABLE`（默认 `false`）
- `SPEAKER_EXTERNAL_DIARIZER_BASE_URL`（例如 `http://xiyu-diarizer:8000`）
- `SPEAKER_EXTERNAL_DIARIZER_TIMEOUT_S`（默认建议 30~60s，视模型速度）
- `SPEAKER_EXTERNAL_DIARIZER_MAX_TURNS`（默认 200，防止碎片化导致 N 次转写）
- `SPEAKER_EXTERNAL_DIARIZER_MAX_TURN_DURATION_S`（默认 25s，防止单 turn 太长）

### Diarizer 容器设置

- `HF_TOKEN`：允许从 HuggingFace 下载并缓存权重（volume）
- `DIARIZER_MODEL`：模型 id
- `DEVICE=cuda`：GPU 推理

---

## Docker / Compose Integration

在 `docker-compose.models.yml` 新增服务：
- `xiyu-diarizer`（profile: `diarizer`）
  - CUDA 12.4 base image
  - NVIDIA GPU reservation
  - volumes:
    - `huggingface-cache:/root/.cache/huggingface`
    - `./data:/app/data`（可选用于统一落盘）
  - environment:
    - `HF_TOKEN`
    - `DIARIZER_MODEL`

Xiyu ASR 容器：
- 不强制默认启用 external diarizer（避免没启动 diarizer 时出现额外超时）
- 通过 `.env` 或用户显式 env 开启：
  - `SPEAKER_EXTERNAL_DIARIZER_ENABLE=true`
  - `SPEAKER_EXTERNAL_DIARIZER_BASE_URL=http://xiyu-diarizer:8000`

---

## Frontend / Capability Signaling

前端通过 `GET /api/v1/backend` 探测能力并展示 badge。

建议后端在 `capabilities` 中体现 **effective speaker**：
- 当 `SPEAKER_EXTERNAL_DIARIZER_ENABLE=true` 且 base_url 配置有效时：
  - 对于不支持 speaker 的后端，也应在 UI 上展示“可用 speaker”（避免误导）。

实现方式（v1）：
- 新增 `capabilities.speaker_strategy`（native/external/none/ignore）
- 或复用既有 `supports_speaker_fallback` 并将其语义扩展为 “speaker via helper service”（但建议最终还是拆分 external vs fallback）。

---

## Testing Strategy

不依赖真实 pyannote 模型即可验证主逻辑：
- Xiyu 侧：
  - mock `httpx.AsyncClient.post` 返回 diarizer segments
  - mock backend.transcribe 返回固定文本
  - 断言：
    - `speaker_turns` 存在且 label 为 `说话人1/2/...`
    - diarizer 失败时回退路径正确（native 或 ignore）
- diarizer 服务侧：
  - 单元测试只验证 request parsing + response schema + lazy import 行为（pyannote 可用时再做 e2e）。

---

## Risks / Mitigations

- **性能**：diarization 本身较慢
  - 通过 turns 合并减少转写次数
  - 设置 max_turns/max_turn_duration，避免极端碎片化
- **边界截断**：turn 切片可能漏掉边界少量字
  - v2 增加 padding（如左右各 200ms）并用 merge-by-text 去重
- **模型许可/下载失败**：
  - 明确需要 `HF_TOKEN`
  - volume 缓存保证冷启动后可复用

---

## Future Work

- diarizer segments padding + boundary reconcile（更稳但更复杂）
- overlap speech 处理（基于更强的 diarization + 可选分离）
- 会议导出增强（SRT/VTT、段落、话题分段、关键词）

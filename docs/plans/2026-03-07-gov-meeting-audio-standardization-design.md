# Gov Meeting Audio Standardization (Pre-ASR) — Design

**Date:** 2026-03-07  
**Context:** 政务会议记录（长音频、会议室环境、多人发言、空调/投影/桌面震动等低频噪声常见）  

## Goal

在不改变现有 API 兼容性的前提下，为“政务会议记录”提供更稳的 **ASR 前音频标准化**：

- 固定格式：`16kHz / mono / PCM16`（现有 FFmpeg 解码已满足）
- 固定音量：RMS 归一化（现有能力已满足）
- 固定频段：默认做 **轻高通** 去低频轰鸣；可选做低通/带通
- 与 ClearVoice 降噪（默认 `MossFormer2_48000Hz` -> `MossFormer2_SE_48K`）协同工作
- 长音频（3–4h）保持 **长度不变**（不会破坏时间戳/分块对齐）

## Non-goals

- 不把 `300–3400Hz` 语音带通作为全局默认（宽带会议录音/Whisper/Qwen3-ASR 下可能降准）
- 不强制改变现有 `asr_options` 的默认值（只新增可选项 + 配置默认值）
- 不引入新的外部服务依赖（全部在现有服务内完成）

## Proposed Pipeline (Gov Meeting Default)

对 16k 浮点波形（`[-1, 1]`）按以下顺序做标准化（全部长度保持不变）：

1. **Remove DC offset**（已有）
2. **High-pass @ 80Hz**（默认开启）  
   目标：抑制空调低频、桌面/麦克风支架震动、低频电流/轰鸣，让 VAD/ASR 更稳。
3. （可选）**Low-pass @ 7600Hz**（默认关闭）  
   目标：抑制高频嘶声/尖锐噪声；在 16k 采样下避免接近 Nyquist 的噪声影响。
4. （可选）**Band-pass 300–3400Hz**（默认关闭，仅窄带电话音/8k 音源建议开启）  
   目标：电话音/窄带语音场景可显著“清理无效频段”。
5. **ClearVoice Denoise**（按请求开启；默认模型 `MossFormer2_48000Hz`）
6. **Normalize RMS**（已有；默认 -20dB，可按 env / asr_options 调整）

说明：
- 滤波属于“长度不变”的线性时域处理，适配短音频直转写与长音频分块转写。
- 带通不默认启用，避免将会议宽带音频“电话化”而损伤可辨信息。

## Controls / Configuration Surface

### 1) Global defaults (.env / Settings)

新增/支持以下环境变量（全局默认，对所有请求生效，可被 `asr_options` 覆盖）：

- `AUDIO_HIGHPASS_ENABLE` (default: `true`)
- `AUDIO_HIGHPASS_CUTOFF_HZ` (default: `80`)
- `AUDIO_LOWPASS_ENABLE` (default: `false`)
- `AUDIO_LOWPASS_CUTOFF_HZ` (default: `7600`)
- `AUDIO_BANDPASS_ENABLE` (default: `false`)
- `AUDIO_BANDPASS_LOW_HZ` (default: `300`)
- `AUDIO_BANDPASS_HIGH_HZ` (default: `3400`)

### 2) Per-request overrides (asr_options.preprocess)

允许在 `asr_options.preprocess` 中覆盖上述开关/参数，便于前端/调用方按音源类型调整。

## Implementation Notes

- 滤波实现优先使用 SciPy IIR（`scipy.signal.butter(..., output="sos")` + `sosfilt`/`sosfiltfilt`）：
  - 性能更好（C 实现），适合长音频分块
  - 行为可控（阶数、截止频率）
- 在 SciPy 不可用时可降级为 numpy-only（保守跳过滤波，保服务可用）。
- 长音频分块路径（`engine.transcribe_long_audio`）将继续只做“长度不变”的预处理，避免时间戳漂移。

## Testing / Verification

- 单元测试：新增 lowpass / bandpass 的能量衰减测试；保持现有 highpass 测试通过。
- 回归测试：`pytest -q` 全量通过。
- 部署烟测：`scripts/smoke_all_endpoints.sh` PASS。
- ClearVoice：`curl http://localhost:8400/info` 显示 `model_effective=MossFormer2_SE_48K`，并可完成一次推理。


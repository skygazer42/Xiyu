# Xiyu Whisper 后端 + CUDA 12.4 容器对齐 设计

**日期**: 2026-02-17

## 背景 / 问题

当前 Xiyu 已支持多后端/多容器部署（`xiyu-pytorch/xiyu-onnx/xiyu-sensevoice/xiyu-gguf/...`），并且前端可以通过选择不同 `baseURL` 来切换模型容器。

但目前：

- Compose 中 **没有**一个独立的 Whisper 模型容器（只有 `POST /api/v1/asr` 的 “Whisper ASR WebService 兼容返回格式” 接口）。
- 现有 `Dockerfile` 基于 `python:3.10-slim`，通过 `pip install torch` 安装依赖时，**很容易得到 CPU 版 torch** 或 CUDA 版本不确定，导致用户期望的 “全部容器 GPU + CUDA 12.4” 无法稳定满足。

用户目标：

- 新增一个 `xiyu-whisper`，像 `xiyu-pytorch` 一样作为一个独立 Xiyu 服务容器运行（统一 Xiyu API，不引入 router 作为主要入口）。
- **默认全部 Xiyu 容器**基于 **CUDA 12.4** 并可在 GPU 环境运行。
- Whisper 本身不强制实现说话人识别（diarization），说话人输出按后端能力 best-effort：支持则输出，不支持则忽略（或按配置报错/回退）。

## 目标（Goals）

1. **新增 Whisper 后端**
   - 支持 `ASR_BACKEND=whisper`
   - 通过 `src/models/backends/whisper.py` 走本地推理（GPU）并返回 Xiyu 标准 `text + sentence_info` 格式
2. **新增 Whisper 容器**
   - `docker-compose.models.yml` 增加 `xiyu-whisper`（profile: `whisper`），端口与其他模型容器一致可被前端选择。
3. **容器 CUDA 版本对齐**
   - Xiyu 服务镜像（默认/多模型 compose 里用到的）统一改为 **CUDA 12.4** runtime base image，避免 torch CUDA 不确定性。
4. **前端可选**
   - 前端 “快速选择” 增加 Whisper 选项（例如 `Whisper (8105)`），使用与其他后端一致的选择逻辑。

## 非目标（Non-goals）

- 不在本次实现 Whisper 的说话人识别（Whisper 原生不做 diarization；不引入 whisperx/pyannote 等额外系统）。
- 不实现 Whisper 的流式 WebSocket 转写（`supports_streaming=false`）。
- 不强制要求第三方远程 ASR 镜像（如 `qwenllm/qwen3-asr`、`vllm/vllm-openai`）也严格对齐 CUDA 12.4（它们由上游镜像决定；本次只保证 Xiyu 自己的服务镜像）。

## 方案概述

### 后端：`WhisperBackend`

- 新增 `WhisperBackend(ASRBackend)`：
  - `load()`：调用 `whisper.load_model(model_name, device="cuda")`，并支持指定下载/缓存目录到 `./data/models`（可通过 settings/env 配置）。
  - `transcribe(audio_input, hotwords=None, with_speaker=False, **kwargs)`：
    - Xiyu API 层传入的是 `16kHz mono PCM16LE bytes`，转换为 `float32` 波形（`np.int16 -> float32 / 32768`）
    - 调用 `model.transcribe(...)` 获取 `segments`
    - 组装 `sentence_info = [{text,start(ms),end(ms)}...]`，并合并为 `text`
  - 能力声明：
    - `supports_speaker=false`
    - `supports_streaming=false`
    - `supports_hotwords=true`（将 `hotwords` 作为 `initial_prompt`/prompt hint best-effort）

### 行为：说话人开关

- Whisper 后端 `supports_speaker=false`，当用户请求 `with_speaker=true` 时：
  - 默认走 `SPEAKER_UNSUPPORTED_BEHAVIOR=ignore`：忽略说话人识别，仅输出普通转写结果。
  - 该策略已在引擎层统一处理（不会在前端做“强制补齐 diarization”）。

### Docker：CUDA 12.4 统一基座

- Xiyu 服务镜像（`Dockerfile` 以及其它 Xiyu 相关 Dockerfile）统一切换到 **PyTorch 官方 CUDA 12.4 runtime 镜像**（例如 `pytorch/pytorch:<ver>-cuda12.4-cudnn9-runtime`），保证：
  - torch 为 CUDA 版且与 CUDA runtime 匹配
  - 容器内 `DEVICE=cuda` 时可直接使用 GPU（前提：宿主机已配置 nvidia-container-toolkit）
- Whisper 依赖（`openai-whisper`）纳入安装路径，避免 “后端代码存在但容器缺包”。

### Compose：新增 `xiyu-whisper`

- `docker-compose.models.yml` 增加：
  - `xiyu-whisper`：端口默认 `8105`
  - `ASR_BACKEND=whisper`
  - `WHISPER_MODEL`（默认如 `large`，可通过 env 覆盖）
  - GPU device reservation 与 `xiyu-pytorch` 一致
- `.env.example` 增加 `PORT_WHISPER=8105`，并在注释中说明启用 profile。

### 前端：增加快速选择项

- `frontend/src/components/transcribe/TranscribeOptions.tsx` 的 `PRESET_BACKENDS` 增加 `Whisper (8105)`
- 仍通过 `GET /api/v1/backend` 探测能力（说话人支持与否等），无需硬编码判断。

## 测试与验收

### 后端单测（不下载真实 Whisper 权重）

- 新增单测通过 mock `whisper.load_model()` 与 `model.transcribe()`：
  - 输入一段短 PCM bytes
  - 断言输出 `text` 和 `sentence_info`（start/end 为毫秒整数）结构符合 Xiyu 期望
- 运行：`.venv/bin/pytest -q`

### 前端构建验证

- 运行：`cd frontend && npm run build`
- 手动验收：
  - 前端能选择 `Whisper (8105)` 并正常探测 `/api/v1/backend`
  - `/api/v1/transcribe` 与 `/api/v1/trans/url` 可按同一 UI 展示结果（时间轴/导出等）

## 里程碑（Milestones）

1. 后端增加 WhisperBackend + 配置/注册
2. Dockerfile 切换 CUDA 12.4 基座并保证依赖可用
3. Compose 增加 `xiyu-whisper` + `.env.example` 增加端口
4. 前端增加 Whisper 快速选择项
5. 测试与文档对齐，提交到主分支

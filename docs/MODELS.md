# 多模型 / 多后端部署指南（profiles / 端口 / 说话人策略）

本项目支持“**一个模型/后端 = 一个 TingWu 服务实例**”的部署方式：每个实例都提供 **同一套 TingWu HTTP API**（例如 `/api/v1/transcribe`），只是端口不同。

这样你可以：

- 同一份前端 UI，通过切换「后端（快速选择）」做 A/B 对比
- 会议 / 视频 / 电话会议按需选择：快 / 准 / 低资源 / 支持 speaker

> 从 0 开始的部署请先看 `docs/DEPLOYMENT.md`。  
> 常见问题排障请看 `docs/TROUBLESHOOTING.md`。  
> API 细节请看 `docs/API.md`（或访问任意后端的 `/docs`）。

---

## 0) TL;DR（常用命令）

```bash
# 启动常用“全家桶”（含 Qwen3 + VibeVoice；不含 router）
docker compose -f docker-compose.models.yml --profile all up -d

# 只启动 PyTorch（建议会议）
docker compose -f docker-compose.models.yml --profile pytorch up -d

# 停止
docker compose -f docker-compose.models.yml down
```

---

## 1) 两种部署形态：单容器 vs 多模型端口

### 1.1 单容器（入门最简单）

使用根目录的 `docker-compose.yml`（或 `docker-compose.cpu.yml`）启动一个 TingWu：

- 优点：最省心，UI/API 都在 `:8000`
- 缺点：想切换模型要改 env 或换 compose 文件

### 1.2 多模型端口（推荐：对比/压测/多场景共存）

使用 `docker-compose.models.yml`：

- 每个后端一个容器（每个容器一个端口）
- API 路径一致，适合前端做“后端选择器”

---

## 2) Profile 用法（按需启动）

`docker-compose.models.yml` 通过 Compose profiles 做“按需启动”。

### 2.1 启动单个 profile

```bash
docker compose -f docker-compose.models.yml --profile pytorch up -d
docker compose -f docker-compose.models.yml --profile whisper up -d
docker compose -f docker-compose.models.yml --profile qwen3 up -d
```

### 2.2 一键启动常用“全家桶”

```bash
# 包含：diarizer + pytorch + onnx + sensevoice + gguf + whisper + qwen3 + vibevoice
# 不包含：router（可选；如需统一入口可再启用 --profile router）
docker compose -f docker-compose.models.yml --profile all up -d
```

### 2.3 启动 VibeVoice / Router（vLLM 远程模型容器 + TingWu 包装）

`vibevoice-asr` 容器使用官方 `vllm/vllm-openai` 镜像启动 vLLM 服务，需要一份包含 `vllm_plugin` 的 VibeVoice 源码目录。

为了适配“内网/离线环境 GitHub 不可用”的情况，本仓库默认已内置一份 **最小 VibeVoice 源码快照**：

- `./third_party/VibeVoice/`（包含 `pyproject.toml`、`vibevoice/`、`vllm_plugin/`）
- `docker-compose.models.yml` 默认会把它挂载到容器 `/app`

因此 **一般不需要你再手动 `git clone`**。

启动：

```bash
# 可选：提前 pull 镜像（网络慢时建议）
docker pull vllm/vllm-openai:latest

# 启动 VibeVoice wrapper（会同时启动 vibevoice-asr）
docker compose -f docker-compose.models.yml --profile vibevoice up -d
```

如果你希望使用最新版/官方仓库（或你们内网 mirror），可以自行准备并覆盖挂载路径：

```bash
# 推荐：浅克隆 + 强制 HTTP/1.1（可规避 GitHub HTTP/2 early EOF）
git -c http.version=HTTP/1.1 clone --depth 1 https://github.com/microsoft/VibeVoice.git ./VibeVoice

VIBEVOICE_REPO_PATH=./VibeVoice \
  docker compose -f docker-compose.models.yml --profile vibevoice up -d
```

说明：
- 首次启动会在 `vibevoice-asr` 容器内自动下载模型权重（默认 `microsoft/VibeVoice-ASR`）；默认优先从 ModelScope 下载，失败再回退 HuggingFace；缓存到 `huggingface-cache` / `model-cache` volume。
- 观察启动/下载进度：`docker logs -f vibevoice-asr`
- wrapper 入口：`http://localhost:8202`（TingWu API/UI）；vLLM server 端口：`http://localhost:9002`（仅调试用）

停止：

```bash
docker compose -f docker-compose.models.yml down
```

---

### 2.4 启动 GGUF（需要本地模型文件）

GGUF 后端适合“离线/本地 CPU”使用，但它 **不会自动下载模型**（不是标准 HF/ModelScope 模型仓库结构，需要你准备本地文件）。

默认期望你在项目根目录准备：

```text
./data/models/Fun-ASR-Nano-GGUF/
  Fun-ASR-Nano-Encoder-Adaptor.fp16.onnx
  Fun-ASR-Nano-CTC.int8.onnx
  Fun-ASR-Nano-Decoder.q8_0.gguf
  tokens.txt
```

说明：
- Docker 的 GGUF 镜像会在构建时编译并内置 llama.cpp 动态库到 `/app/llama_cpp/lib`（默认 `GGUF_LIB_DIR` 指向该目录），所以 **不需要**你在宿主机额外放 `.so`。
- 如果你希望使用自定义的 llama.cpp 编译产物，可设置 `GGUF_LIB_DIR=/app/data/models/bin` 并把 `.so` 放到宿主机 `./data/models/bin/`。
- 当前仓库已内置 `./third_party/llama.cpp/`（llama.cpp 源码快照），用于 GGUF 镜像构建时编译动态库，一般不需要额外 clone。若你希望升级/替换版本，仍可按 `docs/TROUBLESHOOTING.md` 的 2.7 指引自行准备并覆盖该目录。

这些路径都可以通过 `docker-compose.models.yml` 的 `GGUF_*` 环境变量覆盖。

启动：

```bash
docker compose -f docker-compose.models.yml --profile gguf up -d --build
docker logs -f tingwu-gguf
```

如果你在 `--build` 阶段遇到 `pip install` 下载失败（例如 “Network is unreachable / Could not install packages”），请看 `docs/TROUBLESHOOTING.md` 的 **2.8**，并在 `.env` 设置 `PIP_INDEX_URL/PIP_TRUSTED_HOST`。

## 3) 端口对照表（默认值）

默认端口可在 `.env` 中覆盖；下表为 `.env.example` 默认值：

| 服务 | Profile | 默认端口 | 说明 |
|---|---|---:|---|
| `tingwu-pytorch` | `pytorch` / `all` | `8101` | FunASR PyTorch（建议会议） |
| `tingwu-onnx` | `onnx` / `all` | `8102` | ONNX（默认 CPU） |
| `tingwu-sensevoice` | `sensevoice` / `all` | `8103` | SenseVoice（GPU） |
| `tingwu-gguf` | `gguf` / `all` | `8104` | GGUF（默认 CPU；需要你准备模型文件） |
| `tingwu-whisper` | `whisper` / `all` | `8105` | Whisper（GPU，默认 `large`） |
| `tingwu-diarizer` | `diarizer` / `all` | `8300` | external diarizer（pyannote） |
| `qwen3-asr` | `qwen3` / `router` / `all` | `9001` | 远程 ASR server（OpenAI transcription 风格） |
| `vibevoice-asr` | `vibevoice` / `router` / `all` | `9002` | 远程 ASR server（vLLM OpenAI-compatible） |
| `tingwu-qwen3` | `qwen3` / `all` | `8201` | Qwen3 wrapper（提供 TingWu API） |
| `tingwu-vibevoice` | `vibevoice` / `all` | `8202` | VibeVoice wrapper（提供 TingWu API） |
| `tingwu-router` | `router` | `8200` | 路由后端（提供 TingWu API；不是必须） |

每个 TingWu 实例的常用入口：

- Web UI：`http://localhost:<port>`
- API Docs：`http://localhost:<port>/docs`
- Health：`http://localhost:<port>/health`

---

## 4) 前端怎么切后端（快速选择）

每个 TingWu 容器都内置相同的前端 UI（只要镜像包含 `frontend/dist`）。

建议：

1) 打开任意一个端口的 UI（例如 `http://<server-host>:8101`）  
2) 在「转写选项 → 后端 → 快速选择」里切换后端（例如 `Whisper (8105)` / `Qwen3 (8201)` / `Router (8200)`）  
3) 前端会把请求发到你选择的端口

> 提示：前端的“快速选择”预设会自动使用**当前页面的域名/IP**拼出 `:<port>`（不再固定 `localhost`），因此更适合“服务器部署后，同事从公司网访问”的场景。
>
> 如果你是“公司内网只开放 1 个端口”的部署方式（推荐 router），请在 UI 里选择「当前服务 (相对路径)」，让所有请求都打到当前端口（不依赖其它后端端口对外可达）。

---

## 5) 权重缓存策略（不会把镜像撑大）

原则：

- **权重下载到 volume 或宿主机挂载目录**，避免写进镜像层
- 重启容器不会重复下载

`docker-compose.models.yml` 主要使用这些缓存：

- `model-cache`：ModelScope 缓存（FunASR）
- `huggingface-cache`：HuggingFace 缓存（pyannote / Qwen3 / VibeVoice 等）
- `onnx-cache`：ONNX 缓存（ONNX 后端）
- 宿主机 `./data:/app/data`：
  - Whisper 默认下载到 `./data/models/whisper`

查看 volumes：

```bash
docker volume ls | rg "model-cache|huggingface-cache|onnx-cache"
```

清理缓存（谨慎：会导致下次启动重新下载）：

```bash
docker compose -f docker-compose.models.yml down
docker volume rm tingwu_model-cache tingwu_huggingface-cache  # 名称以 docker volume ls 为准
```

---

## 6) 说话人（speaker）从哪里来？（会议场景关键）

会议转写想要输出：

```
说话人1：...
说话人2：...
```

TingWu 支持三种策略（推荐顺序）：

### 6.1 External diarizer（推荐：任意后端都能输出 `speaker_turns`）

启用 `tingwu-diarizer`（pyannote）后，TingWu 会：

1) diarizer 先把音频切成 speaker turns  
2) TingWu 再逐段调用你选择的 ASR 后端  
3) 输出 `speaker_turns`，并可格式化为 `说话人1/2/3...`

启动示例（Qwen3 wrapper + external diarizer）：

```bash
HF_TOKEN=... \
SPEAKER_EXTERNAL_DIARIZER_ENABLE=true \
  docker compose -f docker-compose.models.yml --profile diarizer --profile qwen3 up -d
```

> 注意：部分 pyannote 模型需要在 HuggingFace 申请访问权限，并提供 `HF_TOKEN`。

### 6.2 后端原生 speaker（最简单）

当后端本身支持 speaker 时，请求里带：

- `with_speaker=true`
- （可选）`asr_options.speaker.label_style="numeric"`

### 6.3 Fallback diarization（兼容：Qwen3/Whisper 这类不带 speaker 的后端）

Qwen3-ASR、Whisper 等常见转写服务 **不原生输出 speaker**。

你可以让 TingWu 额外调用一个“辅助 TingWu 服务”（通常是 `tingwu-pytorch`）生成分段信息：

- 同时启动 `--profile pytorch` 和 `--profile qwen3`
- 并设置：
  - `SPEAKER_FALLBACK_DIARIZATION_ENABLE=true`
  - `SPEAKER_FALLBACK_DIARIZATION_BASE_URL=http://tingwu-pytorch:8000`

失败会自动降级为普通转写（避免破坏“端口=模型”的预期）。

---

## 7) 远程 ASR：Qwen3 / VibeVoice 的两层结构

### 7.1 Qwen3-ASR（`qwen3-asr` + `tingwu-qwen3`）

`--profile qwen3` 会启动：

1) `qwen3-asr`：OpenAI transcription server（吃 GPU）  
2) `tingwu-qwen3`：TingWu wrapper（提供统一 TingWu API；一般 CPU 即可）

常用配置（`.env`）：

- `QWEN3_MODEL_ID`（默认 `Qwen/Qwen3-ASR-0.6B`）
- `QWEN3_MAX_MODEL_LEN`（`docker-compose.models.yml` 默认 `16384`；显存紧张时可继续调小）
- `QWEN3_GPU_MEMORY_UTILIZATION`

### 7.2 VibeVoice-ASR（`vibevoice-asr` + `tingwu-vibevoice`）

同理：

1) `vibevoice-asr`：vLLM OpenAI-compatible server（吃 GPU）  
2) `tingwu-vibevoice`：TingWu wrapper（提供统一 TingWu API）

> VibeVoice 默认使用仓库内置的 `./third_party/VibeVoice` 快照挂载进容器（无需额外 clone）。  
> 如需使用你自己的 repo/mirror，再设置 `VIBEVOICE_REPO_PATH` 覆盖挂载路径（详见 `docker-compose.models.yml` 注释）。

---

## 8) 常用组合推荐（会议）

1) **稳/省心**：`pytorch` + `diarizer`（external diarizer）  
2) **多模型对比**：`--profile all` + 前端「转写选项 → 后端 → 快速选择」切端口  
3) **远程 ASR + speaker**：`qwen3` + `diarizer`（或 `qwen3` + fallback diarization）

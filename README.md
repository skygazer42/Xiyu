# TingWu 听悟 - 语音转写服务

<p align="center">
  <img src="frontend/public/favicon.svg" width="80" height="80" alt="TingWu Logo">
</p>

<p align="center">
  基于 FunASR + CapsWriter-Offline 的高精度中文语音转写服务，包含完整的 Web 前端界面。
</p>

## 文档导航

- 部署（从 0 → 全部跑起来）：`docs/DEPLOYMENT.md`
- 多模型 / 多后端（profiles / 端口 / 说话人策略）：`docs/MODELS.md`
- 常见问题排障（GPU / 下载 / 端口 / 性能）：`docs/TROUBLESHOOTING.md`
- API 参考：`docs/API.md`

## 特性

### 核心功能
- **高精度转写**: 基于阿里 FunASR Paraformer-large 模型 (60000小时训练数据)
- **热词纠错**: 音素级模糊匹配 + FAISS 向量检索 + 字形相似度重排序
- **说话人识别**: 自动识别多说话人并标注（甲/乙/丙/丁）
- **实时转写**: WebSocket 流式接口，支持 2pass/online/offline 模式
- **LLM 润色**: 支持 Ollama/OpenAI/vLLM 后端进行文本优化

### 20 项性能优化
- ONNX INT8 量化 + 模型预热
- 批量音频 API + GPU 内存优化
- WebSocket 压缩 + 心跳重连 + 自适应分块
- H-PRM 声学预检索 + FAISS 向量索引
- 音形义联合纠错 (ShapeCorrector)
- vLLM 高吞吐后端 + LRU 缓存 + 批量处理
- DeepFilterNet v3 降噪 + 自适应预处理
- 长音频智能分块 + 并行文本处理
- 置信度驱动选择性纠错
- Prometheus 可观测性指标

## 快速开始

### 方式一：Docker 部署 (推荐)

> **Compose 文件怎么选？**
>
> | 文件 | 定位 | 典型用途 |
> |------|------|----------|
> | `docker-compose.yml` | **单模型快速启动**：只跑 1 个 PyTorch (Paraformer) 后端 | 开发调试、快速体验 |
> | `docker-compose.cpu.yml` | 同上，纯 CPU | 没有 GPU 的机器 |
> | `docker-compose.sensevoice.yml` | 同上，SenseVoice 后端 | 需要多语言 |
> | `docker-compose.onnx.yml` | 同上，ONNX INT8 后端 | 轻量/低延迟 |
> | `docker-compose.benchmark.yml` | 基准测试容器：跑一组音频/模型对比并输出结果 | 性能评估、RTF 对比 |
> | `docker-compose.models.yml` | **多模型按需启动**：每个后端一个容器，按 profile 选择 | 生产/A-B 对比/多后端并存 |
> | `docker-compose.remote-asr.yml` | 远程 ASR (Qwen3 + VibeVoice) + TingWu router | 大模型 ASR 部署 |
>
> `docker-compose.yml` 等价于 `docker-compose.models.yml --profile pytorch`，只是写法更简单。如果你只需要一个后端，用前者；如果想同时跑多个后端或者用 Qwen3/VibeVoice，用后者。

##### 单模型快速启动

```bash
# 构建镜像 (包含前端)
docker compose build

# 启动服务 (默认 PyTorch Paraformer GPU)
docker compose up -d
```

访问 **http://localhost:8000** 即可使用。

> **首次启动说明**：首次运行时会自动从 ModelScope 下载 ASR 模型（约 1-2GB），请耐心等待。模型会缓存到 Docker Volume 中，后续启动无需重新下载。

其他单模型变体：

| 场景 | 命令 | 说明 |
|------|------|------|
| **PyTorch GPU** (默认) | `docker compose up -d` | Paraformer-large，功能最全 |
| **CPU 版本** | `docker compose -f docker-compose.cpu.yml up -d` | 无 GPU 机器 |
| **SenseVoice** | `docker compose -f docker-compose.sensevoice.yml up -d` | 多语言，模型更小 |
| **ONNX** | `docker compose -f docker-compose.onnx.yml up -d` | INT8 量化，低延迟 |

> 以上每条命令都只启动 **1 个后端 + 1 个 TingWu 服务**，端口统一 `8000`。它们之间互斥，同时只能跑一个。

##### 多模型按需启动（每个模型 = 一个容器，统一 API）

当你需要 **同时跑多个后端**（不同端口，同一套 `/api/v1/transcribe` API），或者需要 **Qwen3-ASR / VibeVoice-ASR** 等远程大模型，使用 `docker-compose.models.yml`：

特点：
- 按 profile 按需启动，不用的后端不占资源
- 每个后端一个独立端口，方便 A/B 对比和基准测试
- 支持 Qwen3-ASR、VibeVoice-ASR、Router 等远程模型后端


（按需启动）：

```bash
# 0) External diarizer (pyannote) -> http://localhost:8300
# 让任意后端（包括 Whisper/Qwen3）也能输出 speaker_turns
docker compose -f docker-compose.models.yml --profile diarizer up -d

# 1) PyTorch Paraformer (GPU) -> http://localhost:8101
docker compose -f docker-compose.models.yml --profile pytorch up -d

# 2) ONNX (CPU) -> http://localhost:8102
docker compose -f docker-compose.models.yml --profile onnx up -d

# 3) SenseVoice (GPU) -> http://localhost:8103
docker compose -f docker-compose.models.yml --profile sensevoice up -d

# 4) GGUF (CPU) -> http://localhost:8104
# 注意：GGUF 需要你提前把模型文件放到 ./data/models/（encoder/ctc/decoder/tokens；见 docker-compose.models.yml 里的 GGUF_* 环境变量）
# llama.cpp 动态库已内置到 GGUF 镜像中（默认 GGUF_LIB_DIR=/app/llama_cpp/lib），无需额外准备
docker compose -f docker-compose.models.yml --profile gguf up -d

# 5) Whisper (GPU) -> http://localhost:8105
docker compose -f docker-compose.models.yml --profile whisper up -d

# 6) Qwen3-ASR (远程模型容器 + TingWu 包装) -> http://localhost:8201
# 首次启动会 pull 外部镜像，并在容器内从 ModelScope 下载权重（缓存到 modelscope-cache volume）
docker compose -f docker-compose.models.yml --profile qwen3 up -d

# 7) VibeVoice-ASR (远程模型容器 + TingWu 包装) -> http://localhost:8202
# 默认使用 ./third_party/VibeVoice（仓库内置最小快照）；权重从 ModelScope 下载
docker compose -f docker-compose.models.yml --profile vibevoice up -d

```

##### VibeVoice 

`vibevoice-asr` 使用官方 `vllm/vllm-openai` 镜像启动 vLLM 服务，但它需要把 **VibeVoice 仓库挂载进容器**（用于安装 `vllm_plugin` 等 Python 包）。

默认情况下，本仓库已内置一份 **最小 VibeVoice 源码快照**：

- `./third_party/VibeVoice/`（包含 `pyproject.toml`、`vibevoice/`、`vllm_plugin/`）
- `docker-compose.models.yml` 默认会把它挂载到容器 `/app`

因此一般不需要你再手动 `git clone`。

如果你希望使用最新版/官方仓库（或你们内网 mirror），可自行准备并覆盖挂载路径：

```bash
cd TingWu

# 推荐：浅克隆 + 强制 HTTP/1.1（可规避 GitHub HTTP/2 early EOF）
git -c http.version=HTTP/1.1 clone --depth 1 https://github.com/microsoft/VibeVoice.git ./VibeVoice
```

2) 提前拉取 vLLM 镜像（网络慢时建议）：

```bash
docker pull vllm/vllm-openai:latest
# 如需锁定版本，在 .env 中设置 VLLM_IMAGE=vllm/vllm-openai:<tag>
```

3) 启动（两种方式二选一）：

```bash
# 方式 A：使用仓库内置快照（默认；不需要额外设置 VIBEVOICE_REPO_PATH）
docker compose -f docker-compose.models.yml --profile vibevoice up -d

# 方式 B：VibeVoice 在其它目录（建议用绝对路径）
VIBEVOICE_REPO_PATH=/abs/path/to/VibeVoice \
  docker compose -f docker-compose.models.yml --profile vibevoice up -d
```

说明：
- 首次启动会在容器内 **自动从 ModelScope 下载模型权重**（默认 `microsoft/VibeVoice-ASR`），缓存到 Docker Volume；后续重启不会重复下载。
- 如需切回 HuggingFace 下载，在 `.env` 中设置 `MODEL_SOURCE=huggingface`。
- 观察启动/下载进度：`docker logs -f vibevoice-asr`
- wrapper 入口：`http://localhost:8202`（TingWu API/UI）；vLLM server 端口：`http://localhost:9002`（仅调试用）

##### GGUF 额外准备（离线/本地模型文件）

GGUF 后端 **不会自动下载模型**（不是标准 HF/ModelScope 模型仓库结构，需要你准备本地文件）。

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
- 本仓库已内置 `llama.cpp` 源码快照（`./third_party/llama.cpp/`），因此构建 GGUF 镜像时通常不需要容器内访问 GitHub/Gitee；如需升级/替换版本，仍可按 `docs/TROUBLESHOOTING.md` 的 2.7 指引覆盖该目录或设置 `LLAMA_CPP_REPO`。
- 如果你希望使用自定义的 llama.cpp 编译产物，可设置 `GGUF_LIB_DIR=/app/data/models/bin` 并把 `.so` 放到宿主机 `./data/models/bin/`（容器会通过 bind mount 读取）。

这些路径都可以通过 `docker-compose.models.yml` 的 `GGUF_*` 环境变量覆盖。

启动后看日志确认是否加载成功：

```bash
docker compose -f docker-compose.models.yml --profile gguf up -d --build
docker logs -f tingwu-gguf
```

一键启动（起一套“够用的全家桶”）

如果你希望一次性把常用容器都拉起来（例如你有 48GB 显存，后续在前端选择 Base URL 使用），推荐用 `all-lite`（不包含 GGUF，更不容易因为缺模型文件而启动失败）：

```bash
# 推荐：不含 GGUF，不需要额外准备本地模型文件
./scripts/start.sh models all-lite

# 等价命令（不通过脚本也行）
docker compose -f docker-compose.models.yml \
  --profile diarizer \
  --profile pytorch \
  --profile onnx \
  --profile sensevoice \
  --profile whisper \
  --profile qwen3 \
  up -d
```

如果你已经准备好了 GGUF 模型文件（`./data/models/` 下的 encoder/ctc/decoder/tokens），再用 `all` profile：

```bash
# 包含：diarizer + pytorch + onnx + sensevoice + gguf + whisper + qwen3 + vibevoice
# 不包含：router（可选；如需 Router 自动选模型可再启用 --profile router）
docker compose -f docker-compose.models.yml --profile all up -d
```

如果你也要启动 VibeVoice/Router（可选；默认不需要额外准备，如需自定义 repo 再设置 `VIBEVOICE_REPO_PATH`）：

```bash
docker compose -f docker-compose.models.yml --profile vibevoice --profile router up -d
```

停止：

```bash
docker compose -f docker-compose.models.yml down
```

提示：
- 打开任意一个 TingWu 容器的前端页面后，可在「转写选项 → 后端」里切换 `Base URL`（例如 `http://localhost:8101` / `8102` / `8201`），前端会把请求发到你选择的端口。
- Whisper 容器默认使用 `WHISPER_MODEL=large`（显存占用更高）；可通过环境变量覆盖（例如 `WHISPER_MODEL=small`）。模型权重会下载到 `WHISPER_DOWNLOAD_ROOT`（默认映射到宿主机 `./data/models/whisper`），不会把镜像撑大。
- 如果你希望**任意后端（包括 Qwen3-ASR / Whisper）都输出 `speaker_turns`（说话人1/2/3...）**，推荐启用 external diarizer（`tingwu-diarizer`，pyannote）：
  - 需要在 HuggingFace 上准备 `HF_TOKEN`（部分 pyannote 模型需要申请访问权限）
  - 启动示例（以 Qwen3 为例）：

    ```bash
    HF_TOKEN=... \
    SPEAKER_EXTERNAL_DIARIZER_ENABLE=true \
      docker compose -f docker-compose.models.yml --profile diarizer --profile qwen3 up -d
    ```

  - 模型权重会缓存到 `huggingface-cache` volume（不会把镜像撑大）
  - diarizer 失败会自动降级：后端原生支持 speaker → 回退原生；否则忽略 speaker（不硬报错）
- Qwen3-ASR **原生不支持说话人识别**。如果你不想引入 external diarizer，也可以启用“fallback diarization”（辅助 TingWu 服务生成分段）：
  - 同时启动 `--profile pytorch` 和 `--profile qwen3`
  - 并给 `tingwu-qwen3` 设置：
    - `SPEAKER_FALLBACK_DIARIZATION_ENABLE=true`
    - `SPEAKER_FALLBACK_DIARIZATION_BASE_URL=http://tingwu-pytorch:8000`
  - 如果辅助服务不可用，会自动回退为普通转写（不会报错）。

##### 部署验收（接口自检 / Smoke Test）

多容器部署完成后，建议跑一次“全接口/全后端”的自检，确认每个端口都能正常响应。

1) 健康检查（任选一个端口即可）：

```bash
curl -sS http://localhost:8101/health
```

2) 全后端转写自检（会分别请求每个端口的 `/api/v1/transcribe`，并额外请求一次全量融合接口 `/api/v1/transcribe/all`）：

```bash
# 建议用一段 10~60s 的小音频做 smoke test
bash scripts/test_all_models.sh clip_30s.m4a data/outputs/smoke_$(date +%Y%m%d_%H%M%S)
```

说明：
- 输出会落到 `data/outputs/.../*.json`（默认已在 `.gitignore` 里忽略）。
- 如果你没启用 LLM（`.env` 中 `LLM_ENABLE=false`），`/transcribe/all` 仍会返回结果，但 `llm_used=false`（仅做多模型候选对比，不做融合润色）。

3) 全量融合接口（多模型 + LLM，可选）

该接口用于“政策/政务会议”场景：并发跑多个后端，把候选结果交给 LLM 参考，输出最终听记稿（返回 `candidates + final`）。

```bash
curl -X POST "http://localhost:8101/api/v1/transcribe/all" \
  -F "file=@clip_30s.m4a" \
  -F "with_speaker=true" \
  -F "apply_hotword=true" \
  -F "apply_llm=true" \
  -F "llm_role=policy_meeting"
```

#### 不用 Docker：直接 Python 启动（本地/裸机）

你也可以不使用 Docker，直接用 Python 运行 TingWu（适合裸机部署或你想自己管理进程/依赖的场景）。

1) 安装依赖（主服务）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2) 启动主服务（例如 PyTorch 后端）

```bash
ASR_BACKEND=pytorch PORT=8101 python -m src.main
```

3) 启动 Whisper（可选，默认 large）

```bash
ASR_BACKEND=whisper \
WHISPER_MODEL=large \
WHISPER_DOWNLOAD_ROOT=./data/models/whisper \
PORT=8105 \
python -m src.main
```

4) 启动 external diarizer（可选，建议单独 venv）

```bash
python3 -m venv .venv-diarizer
source .venv-diarizer/bin/activate
pip install -r requirements.diarizer.txt

HF_TOKEN=... DIARIZER_WARMUP_ON_STARTUP=true DIARIZER_PORT=8300 python -m src.diarizer_service.app
```

5) 一键启动（会议模式：PyTorch + External Diarizer）

如果你希望“不用记命令 + 后台常驻 + 支持 stop/status/logs”，可以使用本地启动器：

```bash
# 启动（后台）
python scripts/local_stack.py start --mode meeting

# 查看状态
python scripts/local_stack.py status

# 查看日志
python scripts/local_stack.py logs --tail 200

# 停止
python scripts/local_stack.py stop
```

可选环境变量（推荐把主服务和 diarizer 放在不同 venv）：

```bash
TINGWU_PYTHON=./.venv/bin/python \
DIARIZER_PYTHON=./.venv-diarizer/bin/python \
python scripts/local_stack.py start --mode meeting
```

启动器会把 pid / log 写到：`./.run/local_stack/`。

然后在你运行的 TingWu 服务里启用：

```bash
SPEAKER_EXTERNAL_DIARIZER_ENABLE=true \
SPEAKER_EXTERNAL_DIARIZER_BASE_URL=http://localhost:8300 \
ASR_BACKEND=qwen3 PORT=8201 \
python -m src.main
```

> 前端说明：后端会自动挂载 `frontend/dist`（如果存在）。如果你需要 UI，先在 `frontend/` 下运行 `npm run build`。

#### 本地：预下载模型（可选，但推荐）

本地跑 TingWu 时（`python -m src.main` / `uvicorn src.main:app`），一次只会启动 **一个** 后端实例，模型也只会按需下载当前 `ASR_BACKEND` 需要的权重。

如果你希望“先把模型下好”，避免第一次请求卡住（尤其是开发环境频繁重启），可以用预下载脚本：

```bash
# 默认：预下载 pytorch + sensevoice + whisper
python scripts/prefetch_models.py

# 预下载全部本地后端（含 onnx；需要你已安装 funasr-onnx + onnxruntime）
python scripts/prefetch_models.py --backends all

# 预下载 PyTorch 的 speaker 相关权重（会议场景常用）
python scripts/prefetch_models.py --backends pytorch --with-speaker

# 纯 CPU 机器建议显式指定
python scripts/prefetch_models.py --device cpu --backends pytorch onnx sensevoice whisper
```

说明：
- `onnx` 后端需要额外安装：`pip install funasr-onnx onnxruntime`（或 `onnxruntime-gpu`）
- 远程后端（`qwen3/vibevoice/router`）的权重下载发生在各自的 vLLM/Qwen 服务里，不在本脚本范围内
- 本地 pip 安装的缓存目录通常在：`~/.cache/modelscope`、`~/.cache/huggingface`（不同后端可能略有差异）

#### 请求级调参（`asr_options`，用于准确率 A/B）

在 `POST /api/v1/transcribe`（以及 batch）里可以额外传一个表单字段 `asr_options`（JSON 字符串），用于**单次请求**调参：
- `preprocess`：音频预处理（normalize/denoise/trim/DC offset）
- `chunking`：长音频分块参数（chunk 时长/重叠/合并 overlap_chars/线程数）
- `backend`：模型后端参数（不同后端支持的参数不同）
- `postprocess`：文本后处理（ITN/标点/spacing 等）
- `speaker`：会议/回忆转录输出格式（说话人标签风格、turn 合并阈值等；需 `with_speaker=true`）

返回中会额外包含一个可选字段 `text_accu`：基于时间窗口对齐的“精确拼接文本”（长音频 chunk overlap 去重更严格），更适合回忆/会议转录的最终稿输出。
当 `with_speaker=true` 时，返回会额外包含 `speaker_turns`（按说话人合并后的段落/turn 列表），并且 `transcript` 默认会优先使用 `speaker_turns` 来格式化（更适合会议阅读/回放）。

示例：强制更小的 chunk + 更激进的合并窗口（更偏准确率，慢一点）

```bash
curl -X POST "http://localhost:8102/api/v1/transcribe" \
  -F "file=@/path/to/audio.wav" \
  -F 'asr_options={"chunking":{"max_chunk_duration_s":30,"overlap_chars":40,"max_workers":1}}'
```

示例：边界重解码/对齐（更偏准确率，明显更慢；适合长音频 chunk 边界容易漏字/断词的场景）

> 该模式会对每个 chunk 边界额外转写一个小窗口（`boundary_reconcile_window_s` 表示边界左右各多少秒），
> 再把这段“桥接文本”插入合并流程来减少边界缺字/重复。

```bash
curl -X POST "http://localhost:8101/api/v1/transcribe" \
  -F "file=@/path/to/audio.wav" \
  -F 'asr_options={"chunking":{"boundary_reconcile_enable":true,"boundary_reconcile_window_s":1.0}}'
```

示例：单次请求关闭音量归一化 + DC offset（用于排查“预处理是否伤准确率”）

```bash
curl -X POST "http://localhost:8101/api/v1/transcribe" \
  -F "file=@/path/to/audio.wav" \
  -F 'asr_options={"preprocess":{"normalize_enable":false,"remove_dc_offset":false}}'
```

示例：长音频（大量静音/停顿）更稳的音量归一化（更偏准确率，避免把静音/底噪整体放大）

> `normalize_robust_rms_percentile=95` 大致表示：用最响的约 5% 帧估算“有效音量”，更接近“语音段 RMS”，而不是整段音频的平均 RMS。

```bash
curl -X POST "http://localhost:8101/api/v1/transcribe" \
  -F "file=@/path/to/audio.wav" \
  -F 'asr_options={"preprocess":{"normalize_enable":true,"normalize_robust_rms_enable":true,"normalize_robust_rms_percentile":95}}'
```

示例：自适应降噪 gating（高 SNR 自动跳过降噪，低 SNR 才启用；更偏准确率）

```bash
curl -X POST "http://localhost:8101/api/v1/transcribe" \
  -F "file=@/path/to/audio.wav" \
  -F 'asr_options={"preprocess":{"adaptive_enable":true,"snr_threshold":20,"denoise_enable":true}}'
```

示例：会议/回忆转录（说话人 + turn 合并 + 数字标签）

```bash
curl -X POST "http://localhost:8200/api/v1/transcribe" \
  -F "file=@/path/to/meeting.wav" \
  -F "with_speaker=true" \
  -F 'asr_options={"speaker":{"label_style":"numeric","turn_merge_enable":true,"turn_merge_gap_ms":800}}'
```

说明：
- `speaker_turns`：合并后的 turn 列表（更适合人类阅读/导出）
- `sentences`：句级时间戳（更适合时间轴/字幕等）
- 如果后端不支持说话人识别，行为由 `SPEAKER_UNSUPPORTED_BEHAVIOR` 控制：`error | fallback | ignore`
  - 多端口/多模型场景推荐 `ignore`（`docker-compose.models.yml` 默认已设置为 ignore）
  - 单容器部署如果希望“说话人自动回退到 PyTorch”，可用 `fallback`
- 一键脚本：`scripts/transcribe_meeting.sh /path/to/meeting.wav http://localhost:8200`

#### 模型缓存

模型存储在 Docker Volume 中，可查看下载状态：

```bash
# 查看模型下载进度
docker compose logs -f | grep -i "download\|loading"

# 查看缓存大小
docker volume ls
docker system df -v
```

#### 常用操作

```bash
# 查看日志
docker compose logs -f

# 停止服务
docker compose down

# 重新构建
docker compose build --no-cache
```

### 方式二：本地开发

需要分别启动后端和前端：

```bash
# 终端 1 - 启动后端
pip install -r requirements.txt
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

# 终端 2 - 启动前端
cd frontend
npm install
npm run dev
```

- 后端 API: http://localhost:8000
- 前端界面: http://localhost:5173

补充说明：
- 这套“本地开发”默认只会拉取/加载当前后端实例需要的模型（由 `.env` 的 `ASR_BACKEND` 决定），**不会自动拉取全部模型**。
- 如果你想在本机提前把常用模型下载好，推荐先跑一次：`python scripts/prefetch_models.py`（见上面的「本地：预下载模型」）。
- Vite 开发代理默认转发到 `http://localhost:8000`（见 `frontend/vite.config.ts`）。如果你把后端跑在别的端口：
  - 推荐：启动前端时设置 `VITE_API_BASE_URL=http://localhost:<port>`，或
  - 直接改 `frontend/vite.config.ts` 的 proxy 目标端口。

### 方式三：脚本部署

```bash
# GPU 版本
./scripts/start.sh gpu

# CPU 版本
./scripts/start.sh cpu

# 查看日志
./scripts/start.sh logs

# 停止服务
./scripts/start.sh stop
```

### 方式四：远程模型自部署（vLLM / ModelScope）

如果你希望把模型推理放到独立服务（本地/内网），TingWu 支持通过 vLLM 的 OpenAI 兼容接口接入 **Qwen3-ASR** 和 **VibeVoice-ASR**。

> 模型权重默认从 [ModelScope](https://modelscope.cn) 下载（国内网络友好）。如需切换到 HuggingFace，设置 `MODEL_SOURCE=huggingface`。

如果你希望“一条命令”把 Qwen3 + VibeVoice + Router 都跑起来（不用手动 `pip install qwen-asr`），可以直接使用：

```bash
cp .env.example .env

# 启动：Qwen3-ASR + VibeVoice-ASR + TingWu(router) -> http://localhost:8000
docker compose -f docker-compose.remote-asr.yml up -d --build

# 可选：加上 external diarizer（pyannote），让 Router 也能输出 speaker_turns
docker compose -f docker-compose.remote-asr.yml --profile diarizer up -d --build
```

1) 启动 Qwen3-ASR（自动从 ModelScope 下载权重）

```bash
pip install -U "qwen-asr[vllm]"
qwen-asr-serve Qwen/Qwen3-ASR-0.6B --host 0.0.0.0 --port 9001 --gpu-memory-utilization 0.8
```

2) 启动 VibeVoice-ASR（官方 vLLM Docker，自动从 ModelScope 下载）

```bash
cd TingWu

# 默认：使用仓库内置的最小 VibeVoice 快照（避免 GitHub clone 失败）
# 如需使用最新版/官方仓库，可自行 clone（建议强制 HTTP/1.1）并把挂载路径改成你的目录：
#   git -c http.version=HTTP/1.1 clone --depth 1 https://github.com/microsoft/VibeVoice.git ./VibeVoice

docker run -d --gpus all --name vibevoice-vllm \
  --ipc=host \
  -p 9002:8000 \
  -e VIBEVOICE_FFMPEG_MAX_CONCURRENCY=64 \
  -e PYTORCH_ALLOC_CONF=expandable_segments:True \
  -v $(pwd)/third_party/VibeVoice:/app \
  -w /app \
  --entrypoint bash \
  vllm/vllm-openai:latest \
  -c "python3 /app/vllm_plugin/scripts/start_server.py"
```

3) 配置 TingWu（`.env`）

```bash
ASR_BACKEND=router
QWEN3_ASR_BASE_URL=http://localhost:9001
QWEN3_ASR_MODEL=Qwen/Qwen3-ASR-0.6B
# 可选：降低上下文长度，减少 KV cache 显存占用（更容易和 VibeVoice 共存）
# QWEN3_MAX_MODEL_LEN=32768
VIBEVOICE_ASR_BASE_URL=http://localhost:9002
VIBEVOICE_ASR_MODEL=vibevoice
VIBEVOICE_ASR_USE_CHAT_COMPLETIONS_FALLBACK=true
```

说明：
- `ASR_BACKEND=qwen3`：直接走 Qwen3-ASR
- `ASR_BACKEND=vibevoice`：直接走 VibeVoice-ASR（支持 `with_speaker=true`）
- `ASR_BACKEND=router`：短音频默认 Qwen3，长音频/`with_speaker=true` 默认 VibeVoice

## 前端界面

基于 React + TypeScript + Tailwind CSS + shadcn/ui 构建的现代化前端。

### 功能页面

| 页面 | 功能 |
|------|------|
| **转写** | 文件上传/URL转写、转写选项、结果展示、时间轴、历史记录、导出 |
| **实时转写** | 麦克风录制、波形显示、流式识别、模式选择、连接状态 |
| **热词管理** | 热词编辑、分组管理、搜索、导入/导出、更新、追加 |
| **配置管理** | 纠错/热词/LLM/后处理/音频配置、配置预设 |
| **系统监控** | 健康状态、请求统计、性能指标、Prometheus 集成 |

### 快捷键

按 `Ctrl + /` 查看所有快捷键。支持导航、转写控制、录制控制等。

### 前端技术栈

- **框架**: React 19 + TypeScript + Vite 7
- **样式**: Tailwind CSS v4 + shadcn/ui + CVA
- **状态**: Zustand + React Query v5
- **路由**: React Router v7
- **图表**: Recharts
- **音频**: Web Audio API + MediaRecorder
- **通知**: Sonner
- **无障碍**: WCAG 2.2 AA

### 前端构建

```bash
cd frontend

# 开发模式
npm run dev

# 生产构建
npm run build

# 预览构建
npm run preview
```

## API 使用

### 文件转写

```bash
# 基本转写
curl -X POST http://localhost:8000/api/v1/transcribe \
  -F "file=@audio.wav"

# 完整选项
curl -X POST http://localhost:8000/api/v1/transcribe \
  -F "file=@audio.wav" \
  -F "with_speaker=true" \
  -F "apply_hotword=true" \
  -F "apply_llm=false" \
  -F "llm_role=default"

# 批量转写
curl -X POST http://localhost:8000/api/v1/transcribe/batch \
  -F "files=@audio1.wav" \
  -F "files=@audio2.wav"
```

### 实时转写

参见 `examples/client_websocket.py`

WebSocket 协议:
1. 连接 `ws://localhost:8000/ws/realtime`
2. 发送配置: `{"is_speaking": true, "mode": "2pass"}`
3. 发送 PCM 音频 (16bit, 16kHz, mono)
4. 接收结果: `{"mode": "2pass-online", "text": "...", "is_final": false}`

### 热词管理

```bash
# 查看热词
curl http://localhost:8000/api/v1/hotwords

# 更新热词
curl -X POST http://localhost:8000/api/v1/hotwords \
  -H "Content-Type: application/json" \
  -d '{"hotwords": ["Claude", "FunASR", "麦当劳"]}'

# 追加热词
curl -X POST http://localhost:8000/api/v1/hotwords/append \
  -H "Content-Type: application/json" \
  -d '{"hotwords": ["新热词1", "新热词2"]}'
```

说明：
- `POST /api/v1/hotwords` 管理的是 **强制纠错热词**（`data/hotwords/hotwords.txt`），会参与音素纠错/规则替换。
- 回忆/会议转录更推荐维护 **上下文热词**（`data/hotwords/hotwords-context.txt`）：只用于“注入提示模型”，不会强制替换，风险更低（支持热加载）。

### 配置管理

```bash
# 获取配置
curl http://localhost:8000/config

# 更新配置
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"updates": {"llm_enable": true, "hotwords_threshold": 0.8}}'

# 重载引擎
curl -X POST http://localhost:8000/config/reload
```

### API 文档

启动服务后访问: http://localhost:8000/docs

## 配置

通过环境变量或 `.env` 文件配置:

### 基础配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| DEVICE | 设备类型 (cuda/cpu) | cuda |
| NGPU | GPU 数量 | 1 |
| NCPU | CPU 线程数 | 4 |
| PORT | 服务端口 | 8000 |

### ASR 后端配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| ASR_BACKEND | ASR 后端 (pytorch/onnx/sensevoice/gguf/qwen3/vibevoice/router/whisper) | pytorch |
| ASR_MODEL | 离线 ASR 模型 | paraformer-zh |
| VAD_MODEL | VAD 模型 | fsmn-vad |
| PUNC_MODEL | 标点模型 | ct-punc-c |
| SPK_MODEL | 说话人模型 | cam++ |

> 模型会自动从 [ModelScope](https://modelscope.cn) 下载并缓存。
>
> 远程后端（qwen3/vibevoice/router）相关环境变量见 `.env.example`，或参考上面的「方式四：HuggingFace 模型自部署」。

### 热词配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| HOTWORDS_FILE | 强制纠错热词文件 | hotwords.txt |
| HOTWORDS_CONTEXT_FILE | 上下文提示热词文件（仅注入） | hotwords-context.txt |
| HOTWORDS_THRESHOLD | 热词匹配阈值 | 0.85 |
| HOTWORD_INJECTION_ENABLE | 启用热词前向注入 | true |
| HOTWORD_INJECTION_MAX | 最大注入热词数 | 50 |
| HOTWORD_USE_FAISS | 启用 FAISS 索引 | false |
| HOTWORD_FAISS_INDEX_TYPE | FAISS 索引类型 | IVFFlat |

### LLM 配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| LLM_ENABLE | 启用 LLM 润色 | false |
| LLM_BACKEND | LLM 后端 (auto/ollama/openai/vllm) | auto |
| LLM_CACHE_ENABLE | 启用响应缓存 | true |
| LLM_CACHE_SIZE | 缓存大小 | 1000 |

### WebSocket 配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| WS_COMPRESSION | 启用压缩 | true |
| WS_HEARTBEAT_INTERVAL | 心跳间隔 (秒) | 30 |
| WS_HEARTBEAT_TIMEOUT | 心跳超时 (秒) | 60 |

## 项目结构

```
tingwu/
├── src/                    # 后端源码
│   ├── api/                # FastAPI 接口层
│   │   ├── routes/         # 路由定义
│   │   ├── schemas.py      # 数据模型
│   │   └── ws_manager.py   # WebSocket 管理
│   ├── core/               # 核心业务逻辑
│   │   ├── hotword/        # 热词纠错 (PhonemeCorrector, ShapeCorrector)
│   │   ├── speaker/        # 说话人识别
│   │   ├── llm/            # LLM 客户端
│   │   ├── audio/          # 音频处理 (AudioChunker, Preprocessor)
│   │   ├── text_processor/ # 文本后处理
│   │   └── engine.py       # 转写引擎
│   ├── models/             # 模型加载 (支持多后端)
│   ├── utils/              # 工具类 (ServiceMetrics)
│   ├── config.py           # 配置管理
│   └── main.py             # 应用入口
├── frontend/               # 前端源码
│   ├── src/
│   │   ├── components/     # React 组件
│   │   ├── pages/          # 页面组件
│   │   ├── hooks/          # 自定义 Hooks
│   │   ├── stores/         # Zustand 状态
│   │   ├── lib/api/        # API 客户端
│   │   └── providers/      # Context Providers
│   ├── package.json
│   └── vite.config.ts
├── data/hotwords/          # 热词配置
├── examples/               # 使用示例
├── tests/                  # 测试 (165 用例, 90.9% 通过率)
├── Dockerfile
├── docker-compose.yml          # 单模型快速启动 (PyTorch GPU)
├── docker-compose.cpu.yml      # 单模型 CPU
├── docker-compose.models.yml   # 多模型按需启动 (profiles)
├── docker-compose.remote-asr.yml # 远程 ASR (Qwen3 + VibeVoice)
└── docker-compose.benchmark.yml  # 基准测试
```

## 技术栈

### 后端
- **ASR**: FunASR (Paraformer-large + FSMN-VAD + CT-Transformer)
- **说话人**: CAMPPlus + ClusterBackend
- **热词**: CapsWriter-Offline 音素 RAG + FAISS + 字形相似度
- **LLM**: Ollama / OpenAI / vLLM
- **服务**: FastAPI + WebSocket
- **部署**: Docker + Docker Compose

### 前端
- **框架**: React 19 + TypeScript
- **构建**: Vite 7
- **样式**: Tailwind CSS v4 + shadcn/ui
- **状态**: Zustand + React Query v5
- **图表**: Recharts

## 测试

```bash
# 运行全部测试
python -m pytest tests/ -v

# 运行特定模块测试
python -m pytest tests/test_hotword.py -v

# 生成覆盖率报告
python -m pytest tests/ --cov=src --cov-report=html
```

## License

MIT

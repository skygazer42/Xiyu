# 单入口部署收敛设计（Router-only / Port 18200）

**背景问题**
- 仓库同时存在「单容器 :8000」与「多容器 Router :8200」两套主路，README/文档/脚本里端口混用，导致部署心智负担大。
- 企业场景希望：**只保留一种 Docker 部署方式**；最多再保留一个“预下载/预热模型”的脚本。
- 对外端口希望不要用 8000/8200 这种“过于常见/特色”的端口；统一为 `18200`。

---

## 目标（对用户可见）

1) **唯一对外入口**：`xiyu-router`（Web UI + API）  
2) **唯一对外端口**：`18200`（可通过 `.env` 的 `PORT` 覆盖）  
3) **一条启动命令**：

```bash
cp .env.example .env
docker compose up -d --build
```

访问：`http://<server-ip>:18200`

4) **可选：模型预下载脚本**（一次性把常用权重触发下载/加载，减少首次请求慢/超时）：

```bash
./scripts/prefetch_models_docker.sh
```

---

## 部署形态（对实现/运维）

### 1) 只保留一个 Compose 文件

- 保留根目录 `docker-compose.yml` 作为唯一主路
- 其余 compose 文件移入 `docker/compose/legacy/`（不再在 README 作为主路出现）

### 2) 只暴露 Router 端口

- `xiyu-router`：`0.0.0.0:${PORT:-18200} -> 8000`
- 其他容器（pytorch/onnx/sensevoice/whisper/qwen3-asr/diarizer/clearvoice/xiyu-qwen3）不对外暴露端口，仅 Docker 网络内互通

### 3) 默认栈（不启用 VibeVoice）

默认启动以下服务（无 profile 概念，开机即全套）：
- `xiyu-router`：ASR_BACKEND=router（短/长均默认 qwen3；不依赖 vibevoice）
- `qwen3-asr`：远程 ASR server（vLLM OpenAI-compatible）
- `xiyu-qwen3`：Qwen3 wrapper（给全量 `/api/v1/transcribe/all` 作为候选后端）
- `xiyu-pytorch`：FunASR PyTorch（GPU）
- `xiyu-onnx`：ONNX（CPU）
- `xiyu-sensevoice`：SenseVoiceSmall（GPU）
- `xiyu-whisper`：Whisper large-v3（GPU）
- `xiyu-diarizer`：external diarizer（pyannote，默认 CPU）
- `xiyu-clearvoice`：ClearVoice 降噪微服务（默认 `MossFormer2_48000Hz`，GPU）

---

## 配置策略（`.env.example`）

`.env.example` 收敛为“能直接跑起来这套栈”的最小集合：
- `PORT=18200`（对外端口）
- `QWEN3_MODEL_ID=Qwen/Qwen3-ASR-1.7B` + `QWEN3_GPU_MEMORY_UTILIZATION=0.45`
- ClearVoice 默认启用 + 默认权重 `MossFormer2_48000Hz` + `CLEARVOICE_WARMUP_ON_STARTUP=true`
- external diarizer 默认启用（`SPEAKER_EXTERNAL_DIARIZER_ENABLE=true`）且 `DIARIZER_WARMUP_ON_STARTUP=true`
- GPU/CPU 分配：Whisper/ClearVoice/FunASR/SenseVoice GPU；Diarizer CPU

---

## 文档策略（README 变短，详细内容进 docs）

README 只保留：
- 1 条推荐部署命令 + 访问地址（固定 `:18200`）
- 文档导航（DEPLOYMENT / WEB_UI / API / REFERENCE）

将原本会把人看晕的大段内容移动到 docs：
- 前端技术栈 + build/dev：`docs/FRONTEND.md`
- API 示例（curl/WebSocket）：`docs/API_EXAMPLES.md`
- 配置说明（变量表/常用项）：`docs/CONFIG.md`
- 项目结构/技术栈：`docs/PROJECT_STRUCTURE.md`

并把旧的“多 compose / 多端口 / profile”说明标记为 legacy（不再作为主路）。

---

## 风险与兼容性

- 这是一次“文档 + Compose 形态”的收敛：会影响依赖旧 compose 文件/旧端口的人。
- 通过将旧 compose 文件移到 `docker/compose/legacy/` 保留历史方案（但不作为主推荐）。
- 前端仍支持“相对路径（同源部署）”，默认不需要用户选择后端端口。


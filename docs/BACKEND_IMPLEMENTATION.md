# 后端实施与联调说明

## 1. 文档定位

本文档面向后端实施、维护、联调及二次开发人员，说明当前推荐部署形态、服务结构、配置中心、主要接口分组、模型后端与辅助微服务的关系，以及验收建议。

后端专项排障请参见：

- `docs/BACKEND_TROUBLESHOOTING.md`

## 2. 当前推荐架构

当前仓库推荐采用单入口部署：

- 根目录 `docker-compose.yml`
- 公开入口：`xiyu-router`
- 默认对外端口：`18200`

主线特点：

- Web UI 与统一 API 由 `xiyu-router` 同时提供
- 内部模型服务默认不对外公开宿主机端口
- Router 负责在内部调度 Qwen3 / PyTorch / ONNX / SenseVoice / Whisper 等后端
- external diarizer 与 ClearVoice 以微服务方式存在

相关文件：

- [docker-compose.yml](/data/TingWu/docker-compose.yml)
- [main.py](/data/TingWu/src/main.py)
- [config.py](/data/TingWu/src/config.py)

## 3. 服务拓扑

当前推荐 compose 中的主要服务包括：

| 服务 | 角色 | 说明 |
|---|---|---|
| `xiyu-router` | 统一公开入口 | 提供 Web UI + HTTP API |
| `qwen3-asr` | 远程 ASR 服务 | OpenAI-compatible 远程 ASR server |
| `xiyu-qwen3` | Xiyu wrapper | 把 Qwen3 以 Xiyu API 形式暴露给 Router / 全量接口 |
| `xiyu-pytorch` | 本地后端 | PyTorch Paraformer 主后端 |
| `xiyu-onnx` | 本地后端 | ONNX 推理后端 |
| `xiyu-sensevoice` | 本地后端 | SenseVoice 后端 |
| `xiyu-whisper` | 本地后端 | Whisper 后端 |
| `xiyu-diarizer` | 辅助微服务 | external diarizer |
| `xiyu-clearvoice` | 辅助微服务 | ClearVoice 降噪服务 |

## 4. 应用主入口

主应用入口位于：

- [main.py](/data/TingWu/src/main.py)

主入口承担以下职责：

- 创建 FastAPI 实例
- 加载全量 API 路由
- 启动热词 watcher
- 启动异步任务管理器
- 根据请求头决定 `/` 返回 JSON 还是 HTML
- 在生产环境挂载前端静态资源及 SPA fallback

生命周期管理包括：

- `transcription_engine.load_all()`
- 可选 warmup
- task manager 启停
- 热词文件 watcher 启停

## 5. 配置中心

统一配置位于：

- [config.py](/data/TingWu/src/config.py)

关键配置分组包括：

- 基础服务配置：`host`、`port`、`debug`
- ASR 后端选择：`asr_backend`
- Router 策略：`router_short_backend`、`router_long_backend`
- speaker 策略：`speaker_external_diarizer_enable`、`speaker_unsupported_behavior`
- Whisper 配置
- Qwen3 / VibeVoice 远程配置
- ClearVoice 配置
- 热词、LLM、文本后处理配置

当前 `.env` 的加载规则：

- 测试环境默认不读取本地 `.env`
- 非测试环境默认读取仓库根目录 `.env`
- 额外未知键采用 `extra="ignore"`，避免 Docker 共享环境变量导致导入失败

## 6. 主要接口分组

后端路由统一注册位于：

- [__init__.py](/data/TingWu/src/api/routes/__init__.py)

### 6.1 系统级接口

主入口与系统接口位于：

- [main.py](/data/TingWu/src/main.py)

主要路径包括：

- `GET /`
- `GET /service-info`
- `GET /health`
- `GET /metrics`
- `GET /metrics/prometheus`
- `GET /docs`

### 6.2 转写接口

转写主路由位于：

- [transcribe.py](/data/TingWu/src/api/routes/transcribe.py)

主要路径：

- `POST /api/v1/transcribe`
- `POST /api/v1/transcribe/batch`
- `POST /api/v1/transcribe/all`

说明：

- `/transcribe` 与 `/transcribe/batch` 使用 `transcription_engine.transcribe_auto_async()`
- `/transcribe/all` 负责多模型并发与可选 LLM 融合
- speaker label style 与 `asr_options` 会在请求级透传

### 6.3 异步转写接口

异步转写路由位于：

- [async_transcribe.py](/data/TingWu/src/api/routes/async_transcribe.py)

主要路径：

- `POST /api/v1/trans/url`
- `POST /api/v1/trans/file`
- `POST /api/v1/result`
- `POST /api/v1/trans/video`
- `POST /api/v1/asr`

说明：

- `trans/url` 与 `trans/file` 基于内存任务队列运行
- `result` 用于轮询任务状态与结果
- `/api/v1/asr` 为 Whisper 兼容风格响应

### 6.4 热词接口

热词相关路由位于：

- [hotwords.py](/data/TingWu/src/api/routes/hotwords.py)

主要路径：

- `GET/POST /api/v1/hotwords`
- `GET/POST /api/v1/hotwords/context`
- `GET/POST /api/v1/hotwords/rules`
- `GET/POST /api/v1/hotwords/rectify`
- `POST /append`
- `POST /reload`

说明：

- `hotwords.txt`：强纠错热词
- `hotwords-context.txt`：上下文注入热词
- `hot-rules.txt`：正则/等号规则替换
- `hot-rectify.txt`：纠错历史

### 6.5 配置接口

配置路由位于：

- [config.py](/data/TingWu/src/api/routes/config.py)

主要路径：

- `GET /config`
- `GET /config/all`
- `POST /config`
- `POST /config/reload`

说明：

- `/config` 返回可运行时修改项
- `/config/all` 返回完整配置并对敏感项脱敏
- `/config/reload` 用于让引擎重新加载配置与相关状态

### 6.6 Router 探测接口

路由位于：

- [backend.py](/data/TingWu/src/api/routes/backend.py)

主要路径：

- `GET /api/v1/backend`
- `GET /api/v1/backend/targets`

该组接口主要用于前端探测：

- 当前后端类型
- speaker strategy
- Router target 的可用性

### 6.7 预处理接口

路由位于：

- [preprocess.py](/data/TingWu/src/api/routes/preprocess.py)

主要路径：

- `GET /api/v1/preprocess/status`
- `POST /api/v1/preprocess/enhance`

该组接口用于：

- 探测 ClearVoice 等预处理能力
- 下载增强后的 WAV

### 6.8 WebSocket 实时转写

路由位于：

- [websocket.py](/data/TingWu/src/api/routes/websocket.py)

路径：

- `WS /ws/realtime`

协议行为包括：

- `connected`
- 在线结果
- 最终结果
- `ping/pong`
- `cancel_llm`

## 7. 任务队列机制

异步任务管理器位于：

- [task_manager.py](/data/TingWu/src/core/task_manager.py)

当前实现特点：

- 使用内存队列
- 使用后台线程消费任务
- 使用内存保存结果
- 支持任务状态：`pending / processing / completed / failed`
- 支持进度与 message 更新

说明：

- 当前实现适合单实例运行
- 若后续进入多实例部署，可考虑 Redis / DB 存储替换内存结果

## 8. 模型管理与后端选择

模型管理器位于：

- [model_manager.py](/data/TingWu/src/models/model_manager.py)

主要职责：

- 按 `settings.asr_backend` 初始化当前后端
- 按需构造 RouterBackend
- 构造 remote backend 与 HTTP proxy backend
- 管理 PyTorch loader 的兼容入口

当前后端类型包括：

- `pytorch`
- `onnx`
- `sensevoice`
- `gguf`
- `qwen3`
- `vibevoice`
- `router`
- `whisper`

## 9. 辅助微服务

### 9.1 ClearVoice

服务入口：

- [clearvoice_service.py](/data/TingWu/src/clearvoice_service.py)

主要路径：

- `GET /health`
- `GET /info`
- `POST /api/v1/enhance`

说明：

- ClearVoice 默认作为独立微服务运行
- 主服务通过 `CLEARVOICE_SERVICE_BASE_URL` 调用
- 是否在启动时预热取决于 `CLEARVOICE_WARMUP_ON_STARTUP`
- `device=cuda` 与“已经常驻 GPU”不是同一个层级，需要通过真实请求或 `nvidia-smi` 结合判断

### 9.2 Diarizer

服务入口：

- [app.py](/data/TingWu/src/diarizer_service/app.py)

说明：

- 作为 external diarizer 使用
- 启动预热受 `DIARIZER_WARMUP_ON_STARTUP` 控制
- 主服务通常通过内部地址 `http://xiyu-diarizer:8000` 调用

## 10. 开发与本地运行

推荐后端本地运行方式：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

PORT=18200 python3 -m src.main
```

如需前端联调：

```bash
cd frontend
npm install
npm run dev
```

说明：

- 建议开发环境继续使用 `18200`
- 可减少本地与生产环境在端口与同源策略上的差异

## 11. 验收建议

推荐验收命令：

```bash
bash scripts/smoke_all_endpoints.sh
```

当前 smoke 脚本会覆盖：

- 系统入口
- 配置接口
- 热词接口
- 转写主链路
- 异步任务链路
- 预处理接口
- WebSocket

日常联调建议顺序：

1. `GET /health`
2. `GET /api/v1/backend`
3. `GET /api/v1/backend/targets`
4. `POST /api/v1/transcribe`
5. `POST /api/v1/trans/file` + `POST /api/v1/result`
6. `WS /ws/realtime`

## 12. 文档维护建议

建议后端文档分工如下：

- `docs/BACKEND_TECHNICAL.md`：后端技术导航
- `docs/BACKEND_IMPLEMENTATION.md`：实施、联调、结构说明
- `docs/BACKEND_TROUBLESHOOTING.md`：后端专项排障
- `docs/DEPLOYMENT.md`：部署主线
- `docs/API.md`：协议与接口
- `docs/MODELS.md`：多模型与 legacy 入口

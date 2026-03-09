# 后端专项排障手册

## 1. 文档定位

本文档面向后端实施、维护及运行排障人员，聚焦服务运行、模型加载、Router 调度、辅助微服务、异步任务及 WebSocket 行为的定位与判断路径。

后端实施与联调说明请参见：

- `docs/BACKEND_IMPLEMENTATION.md`

通用服务级排障请参见：

- `docs/TROUBLESHOOTING.md`

## 2. 推荐排障入口

### （一）先确认三项基础状态

```bash
docker compose ps
docker compose logs -f --tail 200
curl -sS http://localhost:18200/health
```

### （二）后端专项 smoke

```bash
bash scripts/smoke_all_endpoints.sh
```

该脚本当前会覆盖：

- 系统入口
- 热词接口
- 配置接口
- 转写链路
- 异步任务链路
- 预处理链路
- WebSocket 实时转写

## 3. 健康检查与容器状态

### 3.1 `healthy` 但功能异常

`healthy` 仅表示当前健康检查接口可达，不等于：

- 所有模型都已加载
- GPU 已常驻占用
- auxiliary service 已实际参与处理链路

建议始终同时检查：

```bash
docker compose ps
curl -sS http://localhost:18200/health
curl -sS http://localhost:18200/api/v1/backend
```

### 3.2 健康检查超时

优先检查：

- 近期是否有长时间同步推理阻塞
- WebSocket 路径是否拖挂事件循环
- 容器是否刚重建完成，仍在启动阶段

## 4. 模型加载与 GPU 判断

### 4.1 `nvidia-smi` 看不到某模型

该现象不必然表示服务无效，常见原因包括：

- 模型尚未触发首次推理
- 当前服务采用懒加载
- 启动预热未开启
- 当前请求未真正命中该服务

建议优先通过真实接口验证，而不是只看 `nvidia-smi`。

### 4.2 ClearVoice `device=cuda` 但最初未出现在 `nvidia-smi`

这类情况通常说明：

- 配置目标为 CUDA
- 但未开启启动预热
- 首次 `POST /api/v1/enhance` 前尚未真正加载模型

建议验证：

```bash
docker exec tingwu-xiyu-clearvoice-1 curl -sS http://localhost:8000/info
docker exec tingwu-xiyu-clearvoice-1 curl -sS -X POST http://localhost:8000/api/v1/enhance -F 'file=@/app/data/benchmark/test_short.mp3'
nvidia-smi
```

### 4.3 Whisper / SenseVoice 容器存在但未出现在 GPU 进程列表

建议直接对对应容器发送真实转写请求，再看 `nvidia-smi`：

```bash
docker exec tingwu-xiyu-whisper-1 curl -sS -X POST http://localhost:8000/api/v1/transcribe -F 'file=@/app/data/benchmark/test_short.mp3'
docker exec tingwu-xiyu-sensevoice-1 curl -sS -X POST http://localhost:8000/api/v1/transcribe -F 'file=@/app/data/benchmark/test_short.mp3'
```

## 5. Router 与内部目标问题

### 5.1 Router 可访问，但目标模型不可用

优先检查：

- `GET /api/v1/backend`
- `GET /api/v1/backend/targets`
- 目标容器是否为 `healthy`

建议命令：

```bash
curl -sS http://localhost:18200/api/v1/backend
curl -sS http://localhost:18200/api/v1/backend/targets
docker compose ps
```

### 5.2 公开入口正常，但某目标模型结果异常

应区分问题属于：

- Router 选择策略
- target_backend 覆盖
- 目标容器自身推理问题
- auxiliary service 影响

建议先绕过 Router，直接请求目标容器内部接口验证。

## 6. speaker 链路问题

### 6.1 有转写但没有 speaker_turns

优先检查：

- 当前后端是否原生支持 speaker
- external diarizer 是否启用
- fallback diarization 是否启用
- `speaker_unsupported_behavior_effective` 当前值

建议命令：

```bash
curl -sS http://localhost:18200/api/v1/backend
```

### 6.2 diarizer 服务正常，但 Router 结果不带 speaker

优先检查：

- 请求是否显式带了 `with_speaker=true`
- 当前路由是否走到了 external diarizer
- diarizer 调用是否被忽略降级

### 6.3 diarizer 相关 401 / 403

常见原因：

- `HF_TOKEN` 缺失
- pyannote 模型访问权限未开通
- huggingface-cache 不可写

## 7. ClearVoice 链路问题

### 7.1 前端显示可用，但增强接口失败

优先检查：

- `GET /api/v1/preprocess/status`
- `GET /info`（ClearVoice 服务）
- `POST /api/v1/enhance`
- `CLEARVOICE_STUDIO_DIR`
- `/app/checkpoints` 中是否有权重

### 7.2 长音频直接走 `preprocess/enhance` 返回 413

这是正常保护行为，说明：

- 当前接口适合中短音频
- 超长音频应改走长音频分块链路

## 8. 异步任务链路问题

### 8.1 `trans/url` 提交成功，但 `result` 一直 pending

优先检查：

- URL 是否可从容器内部访问
- 任务队列线程是否已启动
- 任务处理器是否注册成功

相关实现：

- [task_manager.py](/data/TingWu/src/core/task_manager.py)
- [async_transcribe.py](/data/TingWu/src/api/routes/async_transcribe.py)
- [main.py](/data/TingWu/src/main.py)

### 8.2 页面刷新后看不到任务结果

需要区分：

- 后端结果缓存是否已删除
- 前端本地任务是否仍存在
- `delete=true/false` 是否影响轮询

## 9. WebSocket 专项问题

### 9.1 WebSocket 可连但没有最终结果

优先检查：

- 客户端是否发送了 `is_speaking=false`
- 路由服务是否健康
- 当前模式是否允许在线/离线 flush
- 日志中是否有 `Offline ASR error` 或 `WebSocket error`

### 9.2 WebSocket 后导致路由容器 unhealthy

优先检查：

- 推理是否仍在事件循环线程内执行
- 是否出现运行时 disconnect 异常
- `/health` 是否还能及时返回

当前版本已对 WebSocket 在线/离线推理线程化，并处理了正常断开场景。

## 10. 根路径与静态页面问题

### 10.1 `/` 返回 JSON

这是当前设计，不是异常。

行为约定：

- API 客户端默认拿 JSON
- 浏览器 `Accept: text/html` 时返回 SPA

### 10.2 API 返回 HTML

优先怀疑：

- 镜像版本不一致
- 容器未重建
- 路由被旧静态页面接管

建议命令：

```bash
docker compose up -d --build
docker compose logs -f --tail 200
```

## 11. 配置变更问题

### 11.1 `/config` 更新成功，但运行行为未变

优先检查：

- 改的是不是 `MUTABLE_CONFIG_KEYS` 中允许的键
- 是否存在运行时 side-effect 应用逻辑
- 是否需要额外触发 `/config/reload`

### 11.2 `/config/all` 泄露敏感项担忧

当前实现已对敏感键做脱敏处理。

如需进一步调整，检查：

- [config.py](/data/TingWu/src/api/routes/config.py)

## 12. 推荐排障顺序

建议按以下顺序定位后端问题：

1. `docker compose ps`
2. `GET /health`
3. `GET /api/v1/backend`
4. `GET /api/v1/backend/targets`
5. 真实转写请求
6. auxiliary service 验证
7. `bash scripts/smoke_all_endpoints.sh`
8. `docker compose logs -f`

## 13. 与通用排障文档的边界

本手册聚焦后端服务行为。

如问题属于以下范围，建议转到通用排障文档：

- Docker / GPU / NVIDIA 运行时
- 宿主机 DNS / 代理 / 镜像源
- 端口冲突
- 基础部署失败

对应文档：

- `docs/TROUBLESHOOTING.md`

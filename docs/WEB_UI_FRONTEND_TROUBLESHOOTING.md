# Web 前端专项排障手册

## 1. 文档定位

本文档面向前端联调、实施维护及运行排障人员，聚焦 Web 前端相关问题的判断路径、常见现象与定位建议。

业务用户说明请参见：

- `docs/WEB_UI.md`

前端实施与联调说明请参见：

- `docs/WEB_UI_IMPLEMENTATION.md`

通用服务级排障请参见：

- `docs/TROUBLESHOOTING.md`

## 2. 推荐排障入口

### （一）先确认三项基础状态

```bash
docker compose ps
docker compose logs -f --tail 200
curl -sS http://localhost:18200/health
```

### （二）前端专项 smoke

```bash
bash scripts/smoke_all_endpoints.sh
```

该脚本当前会直接覆盖：

- 根路径 HTML / JSON 响应
- 文档页
- 热词读写恢复
- 转写主链路
- 异步任务链路
- WebSocket 实时转写

如 smoke 失败，通常可以快速区分问题属于：

- 页面静态资源
- 接口连通性
- Router 内部探测
- 异步任务
- WebSocket

## 3. 根路径与页面访问问题

### 3.1 根路径返回 JSON 而不是 HTML

该现象通常不是异常，而是请求头未带 `Accept: text/html`。

当前约定：

- `GET /` 默认返回 JSON
- 浏览器访问时返回 SPA HTML
- `GET /service-info` 始终返回 JSON

相关后端文件：

- [main.py](/data/TingWu/src/main.py)

建议验证：

```bash
curl -sS http://localhost:18200/
curl -H 'Accept: text/html' -I http://localhost:18200/
```

### 3.2 `/docs` 无法访问

优先检查：

- `GET /health`
- `GET /openapi.json`
- 容器是否运行的是最新镜像

建议命令：

```bash
curl -sS http://localhost:18200/openapi.json | head
curl -I http://localhost:18200/docs
```

### 3.3 页面空白或静态资源 404

优先检查：

- `frontend/dist` 是否已打包进镜像
- 当前容器是否为最新重建版本
- `/assets/...` 是否可访问

建议命令：

```bash
docker compose up -d --build
curl -I http://localhost:18200/assets/
```

## 4. API 返回异常内容

### 4.1 API 返回 HTML

该现象通常说明以下情况之一：

- 前端静态资源已更新，但后端 API 未更新
- 某容器仍在运行旧镜像
- 错误请求被 SPA fallback 接管

优先处理：

```bash
docker compose up -d --build
docker compose logs -f --tail 200
```

### 4.2 `/api/v1/backend` 与页面显示不一致

前端后端信息来自：

- `GET /api/v1/backend`
- `GET /api/v1/backend/targets`

相关文件：

- [backend.ts](/data/TingWu/frontend/src/lib/api/backend.ts)
- [backend.py](/data/TingWu/src/api/routes/backend.py)
- [TranscribeOptions.tsx](/data/TingWu/frontend/src/components/transcribe/TranscribeOptions.tsx)

优先检查：

- 是否切换过自定义 `baseUrl`
- 浏览器本地持久化是否保留了旧服务地址
- 当前访问的是不是 router 入口

建议：

1. 进入浏览器开发者工具
2. 检查当前请求域名与端口
3. 必要时清理 `xiyu-backend-storage`

## 5. 转写页问题

### 5.1 文件上传后无结果

先区分使用的是哪条链路：

- `开始转写`：同步链路
- `提交文件任务`：异步链路

同步链路建议检查：

- `POST /api/v1/transcribe`
- 上传是否完成
- 是否被浏览器取消

异步链路建议检查：

- `POST /api/v1/trans/file`
- `POST /api/v1/result`
- 本地任务状态是否恢复

相关页面文件：

- [TranscribePage.tsx](/data/TingWu/frontend/src/pages/TranscribePage.tsx)

### 5.2 URL 转写任务提交成功，但结果一直不刷新

优先检查：

- URL 是否为直链
- `POST /api/v1/trans/url` 是否返回 `task_id`
- `POST /api/v1/result` 是否返回 `pending/processing/success`
- 当前任务保存的 `backendBaseUrl` 是否与提交时一致

相关实现：

- [TaskManager.tsx](/data/TingWu/frontend/src/components/task/TaskManager.tsx)
- [transcribe.ts](/data/TingWu/frontend/src/lib/api/transcribe.ts)
- [TranscribePage.tsx](/data/TingWu/frontend/src/pages/TranscribePage.tsx)

### 5.3 历史记录异常

常见现象：

- 刷新后历史消失
- speakerTurns 缺失
- transcript 与导出结果不一致

优先检查：

- 本地 `xiyu_history`
- 是否触发了 history upgrade 逻辑
- 是否仅保存了 `text` 而未保存 `sentences`

相关文件：

- [historyStore.ts](/data/TingWu/frontend/src/stores/historyStore.ts)
- [HistoryList.tsx](/data/TingWu/frontend/src/components/history/HistoryList.tsx)

## 6. WebSocket 实时转写问题

### 6.1 WebSocket 能连接但没有最终结果

优先检查：

- 前端是否发送了 `is_speaking=false`
- 当前模式是否为 `2pass/online/offline`
- 路由服务是否健康
- 容器日志中是否出现 WebSocket error

相关文件：

- [RealtimePage.tsx](/data/TingWu/frontend/src/pages/RealtimePage.tsx)
- [useWebSocket.ts](/data/TingWu/frontend/src/hooks/useWebSocket.ts)
- [websocket.py](/data/TingWu/src/api/routes/websocket.py)

### 6.2 WebSocket 连接频繁断开

优先检查：

- 浏览器是否被系统限制麦克风
- 网络代理是否影响 WebSocket
- `ws://` / `wss://` 是否与当前协议匹配
- 服务端是否健康

前端自动推导规则：

- 页面为 `https` 时，前端自动使用 `wss`
- 页面为 `http` 时，前端自动使用 `ws`

### 6.3 WebSocket 可以返回在线结果，但服务变为 unhealthy

该类问题通常需要同时检查：

- 路由服务日志
- 是否存在事件循环阻塞
- 模型推理是否在事件循环线程内执行

当前版本已将 WebSocket 在线/离线推理从事件循环线程中移出。

## 7. Router 探测与模型选择问题

### 7.1 `目标模型（Router）` 下拉全部显示不可用

优先检查：

- `GET /api/v1/backend/targets`
- 内部目标容器是否为 `healthy`
- 是否误连到非 router 服务

建议命令：

```bash
curl -sS http://localhost:18200/api/v1/backend
curl -sS http://localhost:18200/api/v1/backend/targets
docker compose ps
```

### 7.2 页面显示支持说话人，但结果没有 speaker_turns

优先检查：

- 当前 speaker strategy
- external diarizer 是否启用
- fallback 策略是否生效
- 当前后端是否原生支持说话人

建议先看：

```bash
curl -sS http://localhost:18200/api/v1/backend
```

## 8. ClearVoice / Whisper / SenseVoice 与 GPU 观察问题

### 8.1 容器 `healthy`，但 `nvidia-smi` 没有显存占用

该现象并不必然表示服务不可用，常见原因包括：

- 模型尚未触发首次推理
- 当前为懒加载模式
- 启动预热未开启
- 当前请求仍走 CPU 路径

建议优先通过真实接口验证：

- Whisper：直接请求 `POST /api/v1/transcribe`
- SenseVoice：直接请求 `POST /api/v1/transcribe`
- ClearVoice：直接请求 `POST /api/v1/enhance`

### 8.2 ClearVoice 显示 `device=cuda`，但最初未出现在 `nvidia-smi`

这类情况通常是：

- 服务配置为 CUDA
- 但未开启启动预热
- 首次 `enhance` 前尚未真正加载模型

应区分：

- “配置目标为 GPU”
- “模型已经常驻 GPU”

这是两个不同层级。

## 9. 本地存储与环境污染问题

如页面行为与后端不一致，建议同时检查浏览器本地存储：

- `xiyu-backend-storage`
- `xiyu_async_tasks_v1`
- `xiyu_history`

常见现象：

- 仍在轮询旧服务地址
- 历史记录来自旧环境
- 前端误连到以前手工设置的端口

可选处理方式：

- 手工删除单个键
- 或在浏览器开发者工具中清理对应站点存储

## 10. 建议排障顺序

建议前端专项问题按以下顺序定位：

1. 先看 `GET /health`
2. 再看 `GET /api/v1/backend`
3. 如为 router，再看 `GET /api/v1/backend/targets`
4. 复现具体页面动作
5. 查看浏览器网络面板
6. 查看 `docker compose logs -f`
7. 跑 `bash scripts/smoke_all_endpoints.sh`

## 11. 与通用排障文档的边界

本手册聚焦前端专项问题。

如问题属于以下范围，建议转到通用排障文档：

- Docker / GPU / NVIDIA 运行时
- 容器拉取失败
- DNS / 代理 / 镜像源
- 宿主机端口冲突
- 服务级模型下载失败

对应文档：

- `docs/TROUBLESHOOTING.md`

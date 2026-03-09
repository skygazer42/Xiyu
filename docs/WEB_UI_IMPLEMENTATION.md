# Web 前端实施与联调说明

## 1. 文档定位

本文档面向项目实施、维护、联调及二次开发人员，说明 Xiyu Web 前端在当前仓库中的部署位置、页面结构、接口映射、状态管理及验收方式。

业务用户操作说明请参见：

- `docs/WEB_UI.md`

前端专项排障请参见：

- `docs/WEB_UI_FRONTEND_TROUBLESHOOTING.md`

## 2. 当前推荐架构

当前仓库推荐采用单入口部署：

- 根目录 `docker-compose.yml`
- 公开入口：`xiyu-router`
- 默认对外端口：`18200`

在该模式下：

- 浏览器访问 `http://<server-ip>:18200`
- Web UI 与 HTTP API 同源
- 内部模型服务通过 Docker 网络互通
- 前端默认不需要感知 `810x / 820x / 8300 / 8400 / 9001 / 9002` 等 legacy 端口

相关文件：

- [docker-compose.yml](/data/TingWu/docker-compose.yml)
- [main.py](/data/TingWu/src/main.py)
- [DEPLOYMENT.md](/data/TingWu/docs/DEPLOYMENT.md)
- [MODELS.md](/data/TingWu/docs/MODELS.md)

## 3. 前端路由结构

React 路由定义位于：

- [index.tsx](/data/TingWu/frontend/src/router/index.tsx)

当前页面结构如下：

| 路径 | 页面 | 文件 |
|---|---|---|
| `/` | 转写页 | [TranscribePage.tsx](/data/TingWu/frontend/src/pages/TranscribePage.tsx) |
| `/realtime` | 实时转写 | [RealtimePage.tsx](/data/TingWu/frontend/src/pages/RealtimePage.tsx) |
| `/hotwords` | 热词管理 | [HotwordsPage.tsx](/data/TingWu/frontend/src/pages/HotwordsPage.tsx) |
| `/config` | 配置管理 | [ConfigPage.tsx](/data/TingWu/frontend/src/pages/ConfigPage.tsx) |
| `/monitor` | 系统监控 | [MonitorPage.tsx](/data/TingWu/frontend/src/pages/MonitorPage.tsx) |

导航入口位于：

- [Sidebar.tsx](/data/TingWu/frontend/src/components/layout/Sidebar.tsx)

## 4. 同源与服务地址策略

前端 API 客户端定义在：

- [client.ts](/data/TingWu/frontend/src/lib/api/client.ts)

当前策略如下：

- `baseURL=""` 表示使用同源相对路径
- 默认推荐使用相对路径
- 仅在前端与 API 分离部署，或需要直连某个 legacy 端口时，才切换为自定义地址

服务地址状态由以下 store 持久化：

- [backendStore.ts](/data/TingWu/frontend/src/stores/backendStore.ts)

持久化键：

- `xiyu-backend-storage`

补充说明：

- 前端重载后会恢复之前选择的 `baseUrl`
- 恢复时会同步调用 `setApiBaseUrl()`

## 5. 页面与接口映射

### 5.1 转写页

主页面与相关组件：

- [TranscribePage.tsx](/data/TingWu/frontend/src/pages/TranscribePage.tsx)
- [TranscribeOptions.tsx](/data/TingWu/frontend/src/components/transcribe/TranscribeOptions.tsx)
- [UrlTranscribe.tsx](/data/TingWu/frontend/src/components/url/UrlTranscribe.tsx)
- [TaskManager.tsx](/data/TingWu/frontend/src/components/task/TaskManager.tsx)
- [HistoryList.tsx](/data/TingWu/frontend/src/components/history/HistoryList.tsx)
- [ExportMenu.tsx](/data/TingWu/frontend/src/components/transcript/ExportMenu.tsx)
- [transcribe.ts](/data/TingWu/frontend/src/lib/api/transcribe.ts)

主要同步接口：

| 功能 | 前端调用 | 后端接口 |
|---|---|---|
| 单文件转写 | `transcribeAudio()` | `POST /api/v1/transcribe` |
| 批量转写 | `transcribeBatch()` | `POST /api/v1/transcribe/batch` |
| 全量对比/融合 | `transcribeAllModels()` | `POST /api/v1/transcribe/all` |
| 降噪音频下载 | `enhanceAudio()` | `POST /api/v1/preprocess/enhance` |
| Whisper 兼容转写 | 不在主页面直接展示 | `POST /api/v1/asr` |

主要异步接口：

| 功能 | 前端调用 | 后端接口 |
|---|---|---|
| URL 转写提交 | `transcribeUrl()` | `POST /api/v1/trans/url` |
| 文件异步任务提交 | `transcribeFileAsync()` | `POST /api/v1/trans/file` |
| 查询任务结果 | `getTaskResult()` | `POST /api/v1/result` |

实现特点：

- 单文件与批量文件共用同一组选项状态
- `ClearVoice` 开关会被合并进 `asr_options.preprocess`
- 高级 `asr_options` 文本框直接透传 JSON 对象
- 全量接口与单模型接口使用不同的 LLM 角色集合

### 5.2 实时转写页

页面与 hook：

- [RealtimePage.tsx](/data/TingWu/frontend/src/pages/RealtimePage.tsx)
- [useWebSocket.ts](/data/TingWu/frontend/src/hooks/useWebSocket.ts)

后端接口：

- `WS /ws/realtime`

协议关键点：

- 先接收 `connected`
- 客户端发送 JSON 配置：`mode`、`is_speaking`
- 客户端发送 PCM16LE 音频块
- 服务端返回在线结果和最终结果
- 心跳使用 `ping/pong`

后端路由文件：

- [websocket.py](/data/TingWu/src/api/routes/websocket.py)

### 5.3 热词管理页

页面与 API：

- [HotwordsPage.tsx](/data/TingWu/frontend/src/pages/HotwordsPage.tsx)
- [hotwords.py](/data/TingWu/frontend/src/lib/api/hotwords.ts)

对应后端接口分组：

- `GET/POST /api/v1/hotwords`
- `GET/POST /api/v1/hotwords/context`
- `GET/POST /api/v1/hotwords/rules`
- `GET/POST /api/v1/hotwords/rectify`
- `POST /append`
- `POST /reload`

说明：

- 该页包含状态性写操作
- 当前 smoke 脚本已使用“读取原值 -> 写入测试值 -> 恢复原值”的方式验证

### 5.4 配置管理页

页面与 API：

- [ConfigPage.tsx](/data/TingWu/frontend/src/pages/ConfigPage.tsx)
- [config.ts](/data/TingWu/frontend/src/lib/api/config.ts)

对应后端接口：

- `GET /config`
- `GET /config/all`
- `POST /config`
- `POST /config/reload`

相关 store：

- [configStore.ts](/data/TingWu/frontend/src/stores/configStore.ts)

实现特点：

- 前端仅持久化本地暂存修改
- 不持久化服务器配置内容

### 5.5 系统监控页

页面文件：

- [MonitorPage.tsx](/data/TingWu/frontend/src/pages/MonitorPage.tsx)

对应接口：

- `GET /health`
- `GET /metrics`
- `GET /metrics/prometheus`

该页主要用于在线状态确认与运行指标可视化，不承担业务写操作。

## 6. 转写页内部状态与流程

### 6.1 请求选项合并

转写选项来源包括：

- 基础选项 store
- `ClearVoice` 开关
- 临时热词
- 高级 `asr_options`
- speaker label style

相关文件：

- [transcriptionStore.ts](/data/TingWu/frontend/src/stores/transcriptionStore.ts)
- [TranscribeOptions.tsx](/data/TingWu/frontend/src/components/transcribe/TranscribeOptions.tsx)
- [transcribe.ts](/data/TingWu/frontend/src/lib/api/transcribe.ts)

合并规则要点：

- 高级 `asr_options` 必须是 JSON 对象
- 说话人识别开启时，会在 `speaker.label_style` 中注入样式
- `ClearVoice` 降噪会被合并到 `preprocess.denoise_enable/denoise_backend`

### 6.2 异步任务队列

转写页异步任务队列组件：

- [TaskManager.tsx](/data/TingWu/frontend/src/components/task/TaskManager.tsx)

本地持久化键：

- `xiyu_async_tasks_v1`

机制说明：

- URL 与文件异步任务都会写入本地 `localStorage`
- 页面刷新后会恢复任务并继续轮询
- 每个任务保存提交时的 `backendBaseUrl`
- 该设计用于避免后续切换后端时出现轮询错服

### 6.3 历史记录

历史记录 store：

- [historyStore.ts](/data/TingWu/frontend/src/stores/historyStore.ts)

存储键：

- `xiyu_history`

主要行为：

- 转写完成后自动写入本地历史
- 支持 speakerTurns 补构建
- 支持 transcript 回补
- 支持从历史记录直接导出

## 7. Router 模式的前端行为

当后端为 `router` 时，前端额外展示：

- 服务地址模式选择
- `目标模型（Router）`
- 后端探测状态
- 当前 speaker strategy 提示

相关文件：

- [TranscribeOptions.tsx](/data/TingWu/frontend/src/components/transcribe/TranscribeOptions.tsx)
- [backend.ts](/data/TingWu/frontend/src/lib/api/backend.ts)
- [backend.py](/data/TingWu/src/api/routes/backend.py)

关键后端探测接口：

- `GET /api/v1/backend`
- `GET /api/v1/backend/targets`

前端根据这些接口展示：

- 当前后端类型
- 是否支持 streaming / hotwords / speaker
- speaker strategy
- 各 router target 的可用性探测结果

## 8. 前端持久化项总览

### 8.1 Zustand / localStorage

| 模块 | 键名 | 说明 |
|---|---|---|
| backendStore | `xiyu-backend-storage` | 记录当前自定义后端地址 |
| configStore | `xiyu-config-storage` | 仅保留 store 容器，不持久化服务器配置内容 |
| historyStore | `xiyu_history` | 保存转写历史 |
| task queue | `xiyu_async_tasks_v1` | 保存异步任务轮询状态 |

### 8.2 通用存储工具

通用存储工具位于：

- [storage.ts](/data/TingWu/frontend/src/lib/storage.ts)

该文件还定义了：

- 预设配置
- 主题
- 用户偏好

## 9. 后端静态页面与根路径行为

后端入口位于：

- [main.py](/data/TingWu/src/main.py)

当前约定如下：

- `GET /` 默认返回 JSON 服务信息
- 浏览器请求头包含 `Accept: text/html` 且存在 `frontend/dist` 时，返回 SPA
- `GET /service-info` 始终返回 JSON
- `/assets` 映射前端静态资源
- 未匹配前端路由走 SPA fallback
- `/api/...` 不允许被 SPA fallback 接管

该设计也是当前 smoke 脚本同时校验：

- `GET /`
- `GET / (HTML)`
- `GET /service-info`
- `GET /docs`

的原因。

## 10. 本地开发说明

前端目录：

- [frontend/package.json](/data/TingWu/frontend/package.json)

前端开发方式：

```bash
cd frontend
npm install
npm run dev
```

后端本地运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

PORT=18200 python3 -m src.main
```

补充说明：

- 前端开发代理默认指向 `http://localhost:18200`
- 推荐保持与生产相同的公开入口端口，以减少环境差异

## 11. 验收与联调建议

### 11.1 推荐验收入口

当前推荐的完整前后端验收命令：

```bash
bash scripts/smoke_all_endpoints.sh
```

该脚本当前会自动覆盖：

- `GET /`
- `GET /service-info`
- `GET /docs`
- `GET /openapi.json`
- `GET /api/v1/backend`
- `GET /api/v1/backend/targets`
- `GET /api/v1/preprocess/status`
- `POST /api/v1/preprocess/enhance`
- 热词全部读写与恢复
- `POST /api/v1/transcribe`
- `POST /api/v1/asr`
- `POST /api/v1/transcribe/batch`
- `POST /api/v1/transcribe/all`
- `POST /api/v1/trans/video`
- `POST /api/v1/trans/file`
- `POST /api/v1/trans/url`
- `POST /api/v1/result`
- `WS /ws/realtime`

相关脚本：

- [smoke_all_endpoints.sh](/data/TingWu/scripts/smoke_all_endpoints.sh)

### 11.2 联调顺序建议

建议按以下顺序联调：

1. `GET /health`
2. `GET /api/v1/backend`
3. `GET /api/v1/backend/targets`
4. 单文件转写
5. 异步任务
6. 实时转写 WebSocket

## 12. 维护建议

建议文档分工如下：

- `docs/WEB_UI.md`：面向业务用户
- `docs/WEB_UI_TECHNICAL.md`：技术文档导航
- `docs/WEB_UI_IMPLEMENTATION.md`：实施、联调、开发说明
- `docs/WEB_UI_FRONTEND_TROUBLESHOOTING.md`：前端专项排障
- `docs/DEPLOYMENT.md`：部署流程
- `docs/MODELS.md`：模型与 legacy 入口
- `docs/API.md`：接口协议

建议持续保持“业务手册、实施手册、排障手册”分离，以降低不同角色之间的阅读干扰。

# 后端技术文档导航

## 1. 文档定位

本文档面向后端实施、维护、联调、排障及二次开发人员，作为后端相关技术文档的统一导航页使用。

如需查看前端相关说明，请参见：

- `docs/WEB_UI_TECHNICAL.md`

## 2. 技术文档分工

### （一）实施与联调

适用于以下场景：

- 推荐单入口架构部署
- 服务与路由结构理解
- 模型后端与 Router 行为核对
- 接口联调
- 验收与 smoke 流程执行

阅读入口：

- `docs/BACKEND_IMPLEMENTATION.md`

### （二）后端专项排障

适用于以下场景：

- 容器健康检查异常
- 模型未加载、GPU 未占用或懒加载判断
- Router / diarizer / ClearVoice 链路异常
- 异步任务链路异常
- HTTP / WebSocket 行为异常

阅读入口：

- `docs/BACKEND_TROUBLESHOOTING.md`

### （三）其它相关文档

- 部署主线：`docs/DEPLOYMENT.md`
- 多模型与 legacy 入口：`docs/MODELS.md`
- 通用接口说明：`docs/API.md`
- 通用排障：`docs/TROUBLESHOOTING.md`

## 3. 推荐阅读顺序

### 新接手项目

建议顺序如下：

1. `docs/DEPLOYMENT.md`
2. `docs/BACKEND_IMPLEMENTATION.md`
3. `docs/API.md`

### 后端联调

建议顺序如下：

1. `docs/BACKEND_IMPLEMENTATION.md`
2. `docs/API.md`
3. `docs/MODELS.md`

### 后端排障

建议顺序如下：

1. `docs/BACKEND_TROUBLESHOOTING.md`
2. `docs/TROUBLESHOOTING.md`
3. `docker compose logs -f`

## 4. 当前推荐后端入口

当前推荐部署下，统一公开入口如下：

```text
http://<server-ip>:18200
```

当前推荐架构下：

- 公开入口由 `xiyu-router` 提供
- Web UI 与 API 同源
- 内部模型服务通过 Docker 网络互通
- 日常联调与问题定位优先基于单入口架构进行

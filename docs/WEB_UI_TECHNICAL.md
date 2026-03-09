# Web 前端技术文档导航

## 1. 文档定位

本文档面向项目实施、维护、联调、排障及二次开发人员，作为 Web 前端相关技术文档的统一导航页使用。

业务用户操作说明请参见：

- `docs/WEB_UI.md`

## 2. 技术文档分工

### （一）实施与联调

适用于以下场景：

- 前后端联调
- 页面与接口映射核对
- 单入口 router 架构理解
- 开发环境搭建
- 验收与 smoke 流程执行

阅读入口：

- `docs/WEB_UI_IMPLEMENTATION.md`

### （二）前端专项排障

适用于以下场景：

- 页面能打开但功能异常
- API 返回异常数据
- WebSocket 实时转写异常
- Router 探测异常
- 前端本地存储、任务队列、历史记录问题

阅读入口：

- `docs/WEB_UI_FRONTEND_TROUBLESHOOTING.md`

### （三）其它相关文档

- 部署主线：`docs/DEPLOYMENT.md`
- 多模型与 legacy 端口：`docs/MODELS.md`
- 通用接口说明：`docs/API.md`
- 服务级排障：`docs/TROUBLESHOOTING.md`

## 3. 推荐阅读顺序

### 新接手项目

建议顺序如下：

1. `docs/DEPLOYMENT.md`
2. `docs/WEB_UI_IMPLEMENTATION.md`
3. `docs/API.md`

### 前端联调

建议顺序如下：

1. `docs/WEB_UI_IMPLEMENTATION.md`
2. `docs/API.md`
3. `docs/MODELS.md`

### 前端排障

建议顺序如下：

1. `docs/WEB_UI_FRONTEND_TROUBLESHOOTING.md`
2. `docs/TROUBLESHOOTING.md`
3. `docker compose logs -f`

## 4. 当前前端主入口

当前推荐部署下，Web 前端统一入口如下：

```text
http://<server-ip>:18200
```

当前推荐架构下：

- Web UI 与 API 同源
- 公开入口由 `xiyu-router` 统一提供
- 内部模型服务通过 Docker 网络互通

如无特殊说明，技术联调与问题定位建议均基于该单入口架构进行。

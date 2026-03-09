# Xiyu 悉语

基于 FunASR、Qwen3-ASR、Whisper、ClearVoice 和 pyannote 的中文会议转写服务，包含完整 Web UI、统一 HTTP API、长音频处理、说话人分离与可选的多模型融合能力。

当前仓库的推荐部署形态已经收敛为：

- 唯一对外入口：`18200`
- 启动入口：根目录 [`docker-compose.yml`](/data/TingWu/docker-compose.yml)
- 默认访问地址：`http://<server-ip>:18200`

内部容器仍然监听 `8000`，但那是容器内通信端口，不再作为默认对外端口使用。

## 文档导航

- 部署指南：`docs/DEPLOYMENT.md`
- Web UI 业务用户手册：`docs/WEB_UI.md`
- Web UI 技术同事说明：`docs/WEB_UI_TECHNICAL.md`
- Web UI 实施与联调：`docs/WEB_UI_IMPLEMENTATION.md`
- Web UI 前端专项排障：`docs/WEB_UI_FRONTEND_TROUBLESHOOTING.md`
- 后端技术同事说明：`docs/BACKEND_TECHNICAL.md`
- 后端实施与联调：`docs/BACKEND_IMPLEMENTATION.md`
- 后端专项排障：`docs/BACKEND_TROUBLESHOOTING.md`
- 多模型 / legacy profiles：`docs/MODELS.md`
- 常见问题排障：`docs/TROUBLESHOOTING.md`
- API 参考：`docs/API.md`

## 按角色阅读

- 业务用户：`docs/WEB_UI.md`
- 项目实施 / 联调人员：`docs/WEB_UI_IMPLEMENTATION.md`
- 前端专项排障人员：`docs/WEB_UI_FRONTEND_TROUBLESHOOTING.md`
- 技术文档总导航：`docs/WEB_UI_TECHNICAL.md`
- 后端实施 / 联调人员：`docs/BACKEND_IMPLEMENTATION.md`
- 后端专项排障人员：`docs/BACKEND_TROUBLESHOOTING.md`
- 后端技术文档总导航：`docs/BACKEND_TECHNICAL.md`

## 推荐启动方式

### 1. 复制示例配置

```bash
cp .env.example .env
```

`.env.example` 默认已经把公开入口设为 `18200`。

### 2. 启动推荐单入口栈

```bash
docker compose up -d --build
```

启动成功后，访问：

- Web UI: `http://localhost:18200`
- API Docs: `http://localhost:18200/docs`
- Health: `http://localhost:18200/health`

### 3. 政务会议一键启动

如果你希望额外触发预热、烟测与模型准备，用：

```bash
./scripts/bootstrap_gov_meeting.sh
```

这个脚本同样默认把 `18200` 作为唯一公开入口。

## 当前架构说明

根目录 [`docker-compose.yml`](/data/TingWu/docker-compose.yml) 现在是推荐主路径，特点是：

- 对外只发布一个端口：`${PORT:-18200}:8000`
- `xiyu-router` 同时提供 Web UI 和统一 API
- Qwen3、PyTorch、ONNX、SenseVoice、Whisper、diarizer、ClearVoice 作为内部服务加入同一 Docker 网络
- 其它模型容器默认不对外暴露宿主机端口

这意味着日常部署时你只需要记住一个地址：

```text
http://<server-ip>:18200
```

## Legacy 多模型入口

仓库里旧的“每个后端一个宿主机端口”的 compose 还保留在 `docker/compose/legacy/` 下，主要用于：

- A/B 对比
- 性能压测
- 逐后端排障
- 特定模型单独暴露

常用文件：

- `docker/compose/legacy/docker-compose.models.yml`
- `docker/compose/legacy/docker-compose.cpu.yml`
- `docker/compose/legacy/docker-compose.onnx.yml`
- `docker/compose/legacy/docker-compose.sensevoice.yml`
- `docker/compose/legacy/docker-compose.remote-asr.yml`
- `docker/compose/legacy/docker-compose.benchmark.yml`

如果你用 legacy router profile，也建议把公开入口继续保持为 `18200`。

## 开发模式

### 后端本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

PORT=18200 python3 -m src.main
```

### 前端本地运行

```bash
cd frontend
npm install
npm run dev
```

前端 Vite 开发代理默认转发到 `http://localhost:18200`。

## 常用命令

```bash
# 查看推荐单入口栈状态
docker compose ps

# 查看日志
docker compose logs -f

# Health 检查
curl -sS http://localhost:18200/health

# 发送一条转写请求
curl -X POST "http://localhost:18200/api/v1/transcribe" \
  -F "file=@audio.wav"
```

## 目录提示

- `src/`: FastAPI 服务与转写逻辑
- `frontend/`: React + Vite Web UI
- `scripts/`: 启动、预热、烟测、辅助脚本
- `docker/compose/legacy/`: 旧的多 profile compose 文件
- `docs/`: 当前说明文档
- `docs/legacy/`: 迁移前保留的长版示例与参考

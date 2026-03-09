# 部署指南

本仓库当前推荐的部署方式只有一条主线：

- 使用根目录 `docker-compose.yml`
- 通过 `xiyu-router` 提供 Web UI + API
- 唯一默认对外端口：`18200`

如果你只想先跑通一套可用服务，请按本文走；如果你需要“每个模型一个端口”的旧式部署，请看 `docs/MODELS.md` 中的 legacy 部分。

## TL;DR

```bash
git clone https://github.com/skygazer42/Xiyu.git
cd Xiyu
cp .env.example .env

docker compose up -d --build

curl -sS http://localhost:18200/health
```

打开：

- `http://localhost:18200`
- `http://localhost:18200/docs`

## 1. 机器要求

推荐：

- Linux + NVIDIA GPU + Docker Compose
- 至少预留 30GB 可用磁盘
- 能访问 ModelScope / HuggingFace，或已经配置代理 / 镜像

CPU 环境也能跑，但速度会慢得多；CPU compose 已移动到 `docker/compose/legacy/docker-compose.cpu.yml`。

## 2. Docker 准备

确认以下命令可用：

```bash
docker --version
docker compose version
```

如果你要用 GPU，再确认：

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

## 3. 配置 `.env`

```bash
cp .env.example .env
```

默认情况下，`.env.example` 已经满足推荐部署：

- `PORT=18200`
- 单入口 router 栈
- Qwen3 作为 router 默认后端
- external diarizer 与 ClearVoice 默认启用

你通常只需要按需修改这些项：

- `PORT`
- `HTTP_PROXY / HTTPS_PROXY / ALL_PROXY / NO_PROXY`
- `HF_TOKEN`
- `CLEARVOICE_STUDIO_DIR`

## 4. 启动服务

### 推荐方式

```bash
docker compose up -d --build
```

### 一键会议栈

```bash
./scripts/bootstrap_gov_meeting.sh
```

这个脚本会：

- 调用推荐 compose 启动整套服务
- 尽量触发模型下载与预热
- 默认对 `18200` 进行烟测

## 5. 验证是否成功

### 健康检查

```bash
curl -sS http://localhost:18200/health
```

预期返回：

```json
{"status":"healthy","version":"..."}
```

### 查看状态与日志

```bash
docker compose ps
docker compose logs -f --tail 200
```

### 浏览器访问

- Web UI: `http://localhost:18200`
- API Docs: `http://localhost:18200/docs`
- Monitor: `http://localhost:18200/monitor`

## 6. 对外放行建议

推荐只放行一个端口：

- `18200`

不要把内部容器端口 `810x / 8201 / 8202 / 8300 / 8400 / 9001 / 9002` 暴露给公司网段，除非你正在做 A/B 调试。

## 7. 不用 Docker 的本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

PORT=18200 python3 -m src.main
```

然后访问：

- `http://localhost:18200`
- `http://localhost:18200/docs`

如果你要本地起会议栈，也建议把主服务入口保持在 `18200`。

## 8. CPU / Legacy 入口

旧的 CPU 与多模型 compose 文件已经移到 `docker/compose/legacy/`：

```bash
docker compose -f docker/compose/legacy/docker-compose.cpu.yml up -d --build
docker compose -f docker/compose/legacy/docker-compose.models.yml --profile pytorch up -d
```

即便你使用 legacy router，也建议把 `.env` 中的 `PORT_XIYU_ROUTER` 设为 `18200`，继续沿用统一公开入口。

# 常见问题排障

这份文档按当前推荐架构组织：默认对外入口是 `18200`，根目录 `docker-compose.yml` 是主路径；旧的多端口 compose 文件已经移到 `docker/compose/legacy/`。

## 0. 先做这 3 件事

```bash
docker compose ps
docker compose logs -f --tail 200
curl -sS http://localhost:18200/health
```

如果你正在排查 legacy 多端口栈，再补一组：

```bash
docker compose -f docker/compose/legacy/docker-compose.models.yml ps
docker compose -f docker/compose/legacy/docker-compose.models.yml logs -f --tail 200
```

## 1. GPU 看不到

先确认宿主机：

```bash
nvidia-smi
```

再确认容器：

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

如果容器里看不到 GPU，优先检查：

- NVIDIA 驱动
- NVIDIA Container Toolkit
- Docker / Compose 版本

常见修复：

```bash
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

## 2. 模型下载慢或失败

优先检查：

- 磁盘空间
- `.env` 里的代理
- `HF_TOKEN`
- 镜像源 / DNS

推荐先确认这些值：

- `HTTP_PROXY`
- `HTTPS_PROXY`
- `HF_ENDPOINT`
- `PIP_INDEX_URL`
- `PIP_TRUSTED_HOST`

### diarizer 的 401 / 403

如果 external diarizer 启不来，通常是 `HF_TOKEN` 或模型访问权限问题。

### Docker Hub / DNS 问题

如果 `docker pull` 报域名解析失败，先试：

```bash
getent hosts registry-1.docker.io || echo "DNS lookup failed"
sudo systemctl restart systemd-resolved
sudo systemctl restart docker
```

## 3. 访问不了页面

当前推荐入口只有一个：

- `http://localhost:18200`

先检查：

```bash
curl -sS http://localhost:18200/health
curl -H 'Accept: text/html' -I http://localhost:18200/
```

如果健康检查正常但浏览器打不开，多半是：

- 宿主机防火墙
- 云安全组
- 反向代理
- 没有放行 `18200`

## 4. 端口冲突

推荐只占用 `18200` 作为公开入口。

查看占用：

### Linux

```bash
ss -lntp | rg ":18200" || true
```

### macOS

```bash
lsof -nP -iTCP:18200 -sTCP:LISTEN || true
```

### Windows

```powershell
netstat -ano | findstr :18200
```

如果要改公开端口，改 `.env` 的 `PORT`，然后重启：

```bash
docker compose down
docker compose up -d --build
```

## 5. 有转写但没有说话人

如果你走的是 Qwen3、Whisper 或 router，speaker 结果通常依赖：

- external diarizer
- 或回退策略

先确认：

```bash
curl -sS http://localhost:18200/api/v1/backend
```

再检查 diarizer：

```bash
curl -sS http://localhost:8300/health
```

如果你用的是推荐单入口栈，Xiyu 内部默认访问的是容器网络地址 `http://xiyu-diarizer:8000`，这属于正常内部通信，不需要改成 `18200`。

## 6. UI 空白页 / 只有 API 没有前端

当前 Dockerfile 会构建前端并把 `frontend/dist` 打包进镜像。

优先检查：

```bash
docker compose build
docker compose up -d
docker compose logs -f --tail 200
```

如果你是本地 Python 启动，需要前端单独构建：

```bash
cd frontend
npm install
npm run build
```

## 7. legacy 多端口排障

如果你明确在排查旧的多 profile 架构，请使用新的路径：

```bash
docker compose -f docker/compose/legacy/docker-compose.models.yml --profile pytorch up -d
docker compose -f docker/compose/legacy/docker-compose.models.yml --profile router up -d
```

此时仍建议把 router 的宿主机公开端口设为 `18200`：

```bash
PORT_XIYU_ROUTER=18200 \
docker compose -f docker/compose/legacy/docker-compose.models.yml --profile router up -d
```

## 8. benchmark / 辅助脚本报 compose 文件不存在

这是因为仓库已经把旧 compose 移到了 `docker/compose/legacy/`。

当前脚本应使用：

- `docker/compose/legacy/docker-compose.benchmark.yml`
- `docker/compose/legacy/docker-compose.models.yml`
- `docker/compose/legacy/docker-compose.cpu.yml`

如果你看到脚本还在找根目录的 `docker-compose.benchmark.yml` 或 `docker-compose.models.yml`，说明脚本版本还没更新到当前结构。

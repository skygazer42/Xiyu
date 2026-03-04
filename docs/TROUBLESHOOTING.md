# 常见问题排障（GPU / 下载 / 端口 / 说话人 / 性能）

这份文档按“**最常见、最省时间**”的顺序列出排障步骤。  
如果你是从 0 开始部署，请先看 `docs/DEPLOYMENT.md`；多模型端口与 profiles 见 `docs/MODELS.md`。

---

## 0) 先做 3 件事（80% 的问题都能定位）

1) 看容器状态：

```bash
docker compose ps
docker compose -f docker-compose.models.yml ps
```

2) 看日志（最重要）：

```bash
docker compose logs -f --tail 200
docker compose -f docker-compose.models.yml logs -f --tail 200
```

3) 打健康检查：

```bash
curl -sS http://localhost:8000/health
```

---

## 1) GPU 看不到 / 没有用上 GPU

### 1.1 宿主机是否能看到 GPU？

```bash
nvidia-smi
```

如果宿主机没有 `nvidia-smi`，先装/修复驱动（各发行版流程不同，建议跟随官方指南）。

### 1.2 Docker 容器是否能看到 GPU？

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

- 能看到 GPU：说明容器 GPU 环境 OK
- 看不到 GPU：需要安装/配置 NVIDIA Container Toolkit

常见修复（Ubuntu/Debian 示例）：

```bash
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### 1.3 Compose 启动了，但 GPU 还是没用上？

检查你的 Docker/Compose 版本：

```bash
docker compose version
docker info | rg -n "Runtimes|nvidia|Default Runtime" -n || true
```

> 说明：本项目的 compose 文件使用了 GPU 设备声明（`deploy.resources.reservations.devices`）。  
> 如果你的 Compose 版本太旧，可能会忽略 GPU 声明，导致容器跑在 CPU 上。

---

## 2) 模型下载慢 / 下载失败（ModelScope/HuggingFace）

### 2.1 先确认磁盘空间

模型可能 1–10GB+，建议预留 **30GB** 以上。

### 2.2 代理（Proxy）设置

如果你访问 HuggingFace/ModelScope 需要代理：

- 宿主机跑 Docker：需要把代理环境变量传进容器
- 本项目支持在 `.env` 设置：
  - `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY`

注意：

- macOS/Windows Docker Desktop 的容器里 `127.0.0.1` 不是宿主机  
  常用宿主机地址是 `host.docker.internal`

### 2.3 HuggingFace Token（pyannote diarizer）

如果你启用了 external diarizer（pyannote）并遇到 401/403：

- 需要在 HuggingFace 准备 `HF_TOKEN`
- 并确保对应模型（例如 `pyannote/speaker-diarization-3.1`）已申请访问权限

启动时传入：

```bash
HF_TOKEN=... docker compose -f docker-compose.models.yml --profile diarizer up -d
```

### 2.4 如何确认缓存生效（不重复下载）

`docker-compose.models.yml` 使用 volumes：

- `model-cache`（ModelScope）
- `huggingface-cache`（HuggingFace）

查看：

```bash
docker volume ls | rg "model-cache|huggingface-cache|onnx-cache"
```

如果你删除了 volume，下次启动会重新下载。

### 2.5 Docker Hub 拉镜像失败（registry-1.docker.io / 127.0.0.53:53）

典型报错：

```text
Get "https://registry-1.docker.io/v2/": dial tcp: lookup registry-1.docker.io on 127.0.0.53:53: server misbehaving
```

这类问题通常是宿主机 DNS（`systemd-resolved` stub）异常，导致 `docker pull` 解析失败。

先确认是否能解析：

```bash
getent hosts registry-1.docker.io || echo "DNS lookup failed"
```

常见修复（Ubuntu/Debian + systemd 示例）：

1) 重启解析服务：

```bash
sudo systemctl restart systemd-resolved
```

2) 如果仍失败，给 Docker daemon 指定 DNS（注意：不要覆盖你已有的 `runtimes.nvidia` 配置）：

```bash
sudo cat /etc/docker/daemon.json
sudo systemctl restart docker
```

3) 再试：

```bash
docker pull hello-world
```

> 如果你在公司/内网环境，需要代理或镜像源，请同时检查 `.env` 的 `HTTP_PROXY/HTTPS_PROXY` 与 `/etc/docker/daemon.json` 的 `registry-mirrors`。

### 2.6 构建镜像时 apt-get update 报 “invalid signature”

典型报错（在 `docker build` / `docker compose build` 阶段）：

```text
At least one invalid signature was encountered.
E: The repository 'http://archive.ubuntu.com/ubuntu ... InRelease' is not signed.
```

常见原因：
- 宿主机/内网对 apt 流量（HTTP/HTTPS）做了透明代理/缓存/重写，导致 `InRelease` 内容被污染（GPG 校验失败）
- DNS/网络不稳定导致 `InRelease` 下载不完整（也可能触发签名校验失败）

建议修复（按优先级）：

1) 先确认是不是“构建环境/网络”问题（而不是项目本身）：

```bash
docker run --rm pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime bash -lc 'apt-get update'
```

2) 如果你处于公司/内网环境：尝试切换可用镜像源（或使用公司内部 apt mirror）
   - 本项目 Dockerfiles 会在 `apt-get update` 失败时自动回退到 `http://mirrors.aliyun.com/ubuntu`
   - 如需强制使用你们的内网 mirror，建议直接修改 `/etc/apt/sources.list`（或在 Dockerfile 中替换）

3) 如果你看到 `Sending build context to Docker daemon ...` 非常大（例如几 GB）
   - 通常是把本地模型文件（`./data/models/`）打包进了构建上下文
   - 建议在仓库根目录添加 `.dockerignore` 并忽略 `data/models/`（本项目已提供默认 `.dockerignore`）

> 不推荐关闭签名校验（`--allow-unauthenticated` / `Acquire::AllowInsecureRepositories=true`），这会带来供应链风险。

### 2.7 构建 GGUF 镜像时 git clone llama.cpp 失败（GitHub 网络/HTTP2）

典型报错：

```text
error: RPC failed; curl 92 HTTP/2 stream 0 was not closed cleanly: CANCEL (err 8)
fatal: early EOF
fatal: fetch-pack: invalid index-pack output
```

原因：构建 GGUF 镜像需要在容器内拉取 `llama.cpp` 源码并编译动态库；部分网络/代理对 GitHub 的 **HTTP/2** 连接不稳定。

建议处理：

1) **重试一次**（偶发网络抖动会自愈）：

```bash
docker compose -f docker-compose.models.yml build --no-cache xiyu-gguf
```

2) 如果持续失败：把 `llama.cpp` repo 换成可访问的镜像源/内网 Git mirror

本项目支持通过环境变量覆盖（`docker-compose.models.yml` 已把它作为 build arg 透传）：

```bash
LLAMA_CPP_REPO=https://gitee.com/mirrors/llama.cpp.git \
  docker compose -f docker-compose.models.yml build --no-cache xiyu-gguf
```

说明：

- 默认配置是：先尝试 `LLAMA_CPP_REPO`（GitHub），失败则回退 `LLAMA_CPP_REPO_FALLBACK`（gitee mirror）。
- 你也可以直接在 `.env` 中设置（推荐，避免每次命令行写一长串）。

更多用法示例：

```bash
# 1) 直接指定主 repo（最推荐）
LLAMA_CPP_REPO=https://gitee.com/mirrors/llama.cpp.git \
  docker compose -f docker-compose.models.yml build --no-cache xiyu-gguf

# 2) 使用公司内网 mirror，并关闭 fallback（可选）
LLAMA_CPP_REPO=http://git.company.local/llama.cpp.git \
LLAMA_CPP_REPO_FALLBACK= \
  docker compose -f docker-compose.models.yml build --no-cache xiyu-gguf
```

> 如果你们公司/内网有自己的 GitHub mirror，把 `LLAMA_CPP_REPO` 换成内网地址即可；必要时也可以用 `LLAMA_CPP_REPO_FALLBACK` 做第二备选。

3) **完全离线 / 容器内无法访问任何 Git 源**  
   当前仓库已内置一份 `llama.cpp` 源码快照在 `./third_party/llama.cpp/`（用于 GGUF 镜像构建时编译动态库），因此**一般不需要额外 clone**。  
   如果你删掉了该目录、或希望升级/替换版本，可以把 `llama.cpp` 源码提前放到项目目录里，让构建阶段直接 `COPY` 进去（不再需要容器内 `git clone`）：

要求：`./third_party/llama.cpp/` 目录下必须包含 `CMakeLists.txt`（即 llama.cpp repo 根目录）。

示例（在宿主机准备好源码；可从任意可访问渠道获取）：

```bash
cd Xiyu
mkdir -p third_party

# 如果你宿主机能访问 gitee（或公司内网 mirror），在宿主机 clone（不是容器内 clone）
git clone --depth 1 https://gitee.com/mirrors/llama.cpp.git third_party/llama.cpp

# 可选：删掉 .git，减少 build context
rm -rf third_party/llama.cpp/.git

# 然后再构建
docker compose -f docker-compose.models.yml build --no-cache xiyu-gguf
```

> 注意：如果你选择离线源码方式，`LLAMA_CPP_REF`（按 tag/commit pin）将被忽略，因为源码目录通常不带 `.git`。

### 2.8 构建镜像时 pip install 报 “Network is unreachable / Could not install packages”

典型报错：

```text
Failed to establish a new connection: [Errno 101] Network is unreachable
Could not install packages due to an OSError
```

说明：

- `Dockerfile.onnx` / `Dockerfile.gguf` 在构建阶段会额外安装一些 PyPI 依赖（例如 `onnxruntime` / `gguf`）。
- 如果你的机器无法直连 PyPI（或 `files.pythonhosted.org`），就会在 `docker compose build` 阶段失败。

处理方式（推荐二选一）：

1) **配置 PyPI 镜像（最常见）**  
   你可以在 `.env` 里加入（或直接在命令行 export）：

```bash
# 国内常用（默认值）
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
PIP_TRUSTED_HOST=mirrors.aliyun.com

# 如果你在海外或不想用镜像，可改回官方 PyPI
# PIP_INDEX_URL=https://pypi.org/simple
# PIP_TRUSTED_HOST=
```

然后重建：

```bash
docker compose -f docker-compose.models.yml build xiyu-onnx
docker compose -f docker-compose.models.yml build xiyu-gguf
```

2) **使用代理（企业内网常见）**  
   给 Docker daemon 或构建环境配置 `HTTP_PROXY/HTTPS_PROXY`，并确保容器内也能访问外网（见本章 2.2）。

> 提示：本项目已在 `docker-compose.models.yml` 的 build args 中透传 `PIP_INDEX_URL/PIP_TRUSTED_HOST` 给 ONNX/GGUF 镜像。

### 2.9 构建镜像时报 “No space left on device”（磁盘满 / Docker Root Dir 在 `/`）

典型报错：

```text
OSError: [Errno 28] No space left on device
```

先看两件事：

```bash
df -h
docker system df
docker info | rg -n "Docker Root Dir" || true
```

如果你看到：

- `/` 分区 100%（但 `/data` 很空）
- `Docker Root Dir` 在 `/var/lib/docker`

建议把 Docker 数据目录迁移到大盘（例如 `/data/docker`），避免后续构建/拉镜像反复爆盘：

```bash
sudo systemctl stop docker
sudo rsync -aHAX --numeric-ids /var/lib/docker/ /data/docker/

# 把 /etc/docker/daemon.json 合并配置（保留你已有的 dns / registry-mirrors / runtimes.nvidia）
sudo cat /etc/docker/daemon.json

# 加一行：
#   "data-root": "/data/docker"

sudo systemctl start docker
docker info | rg -n "Docker Root Dir" || true
```

> 如果你只是临时腾空间，可以先清理 dangling 镜像/容器：`docker container prune -f && docker image prune -f`（谨慎使用 `-a`）。

---

## 3) 端口冲突（启动失败 / 访问不到）

### 3.1 先确认你要访问哪个端口

- 单容器默认：`http://localhost:8000`
- 多模型：`8101/8102/8103/...`（见 `docs/MODELS.md` 的端口表）

### 3.2 查看端口占用

Linux：

```bash
ss -lntp | rg ":8000" || true
```

macOS：

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN || true
```

Windows（PowerShell）：

```powershell
netstat -ano | findstr :8000
```

### 3.3 修改端口

推荐改 `.env`：

- 单容器：`PORT=8000`
- 多模型：`PORT_PYTORCH=8101`、`PORT_WHISPER=8105` 等

改完后重启容器：

```bash
docker compose down
docker compose up -d
```

---

## 4) 有转写，但没有说话人（speaker_turns 为空 / with_speaker 无效）

先确认你走的是哪条 speaker 路径（见 `docs/MODELS.md` 的“说话人策略”）。

### 4.1 你用的是 Qwen3 / Whisper？

这类后端通常 **不原生输出 speaker**。要得到 `说话人1/2/3`：

- 推荐：启用 external diarizer（`xiyu-diarizer`）
- 或者：启用 fallback diarization（用 `xiyu-pytorch` 辅助分段）

### 4.2 external diarizer 启用了，但仍然没有 speaker？

检查：

1) diarizer 服务是否活着：`http://localhost:8300/health`
2) Xiyu 是否配置了：
   - `SPEAKER_EXTERNAL_DIARIZER_ENABLE=true`
   - `SPEAKER_EXTERNAL_DIARIZER_BASE_URL=http://xiyu-diarizer:8000`（容器内网络）或 `http://localhost:8300`（本地）  
3) diarizer 是否因为 `HF_TOKEN`/权限/下载超时而失败（看 diarizer 日志）

---

## 5) UI 打不开 / 空白页 / 只有 API 没有前端

### 5.1 Docker 方式（默认包含前端）

官方 Dockerfile 会在构建时执行 `frontend/` 的 `npm run build` 并拷贝 `frontend/dist`。

如果你自行改过镜像或跳过了前端构建，可能导致 UI 不存在。

### 5.2 本地 Python 启动（需要手动 build 前端）

本地 `python -m src.main` 会在 `frontend/dist` 存在时挂载它。否则只有 API：

```bash
cd frontend
npm ci
npm run build
```

然后再启动后端。

---

## 6) 性能 / 显存不够 / 容器频繁 OOM

### 6.1 不要一次启动所有 GPU-heavy 后端

即使你有 48GB 显存，同时启动：

- Qwen3-ASR server
- VibeVoice-ASR server
- Whisper large
- SenseVoice
- PyTorch Paraformer

也可能把显存挤爆或导致频繁抖动。

建议做法：

- 日常只开 1–2 个 GPU-heavy 后端
- 用 `--profile all` 只作为“对比/探索”时使用

### 6.2 调小远程 server 的显存占用

在 `.env` 中：

- `QWEN3_GPU_MEMORY_UTILIZATION`
- `VIBEVOICE_GPU_MEMORY_UTILIZATION`

### 6.3 Whisper 太吃显存？

可以把 `WHISPER_MODEL` 改小（例如 `medium`/`small`），或者只在需要时启动 `--profile whisper`。

---

## 7) 本地一键会议栈（非 Docker）启动失败

本地启动器：`scripts/local_stack.py`。

常见问题：

1) Python 环境不对（依赖缺失）  
   - 主服务：`pip install -r requirements.txt`
   - diarizer：建议独立 venv 安装 `pip install -r requirements.diarizer.txt`

2) 端口被占用  
   - 修改环境变量：`PORT_PYTORCH` / `DIARIZER_PORT`

3) diarizer 下载/加载时间太长  
   - 开启 warmup：`DIARIZER_WARMUP_ON_STARTUP=true`
   - 提前准备 `HF_TOKEN`

查看日志：

```bash
python scripts/local_stack.py logs --tail 200
```

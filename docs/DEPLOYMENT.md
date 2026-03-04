# 部署指南（从 0 → 全部跑起来）

本指南面向「第一次部署 Xiyu」的用户，目标是让你从一台全新的机器出发，最终跑起一套可用的转写服务：

- **Linux + NVIDIA GPU + Docker Compose（推荐）**
- **macOS / Windows + Docker Desktop（CPU 为主）**
- **不用 Docker：本地 Python 启动（含一键会议栈）**

> 如果你想启动 **多模型全家桶（每个后端一个端口）**，请继续看 `docs/MODELS.md`。  
> 如果你遇到 GPU/下载/端口等问题，请看 `docs/TROUBLESHOOTING.md`。

---

## TL;DR（最快跑起来）

### Linux（有 NVIDIA GPU，推荐）

```bash
git clone https://github.com/skygazer42/Xiyu.git
cd Xiyu
cp .env.example .env

# 需要 GPU 容器运行环境（见下文“Linux GPU 从 0”）
docker compose up -d --build

curl -sS http://localhost:8000/health
```

打开：`http://localhost:8000`（前端 UI）或 `http://localhost:8000/docs`（API 文档）。

### macOS / Windows（Docker Desktop，CPU）

```bash
git clone https://github.com/skygazer42/Xiyu.git
cd Xiyu
cp .env.example .env

docker compose -f docker-compose.cpu.yml up -d --build
curl -sS http://localhost:8000/health
```

---

## 1. 你需要什么（硬件 / 系统 / 网络）

### 1.1 硬件建议（会议场景）

- **GPU（推荐）**：长音频 + 多后端并行会非常吃算力/显存
- **存储**：首次启动会下载模型（1–10GB+，取决于你启用的后端）；建议预留至少 **30GB 可用磁盘**
- **网络**：需要访问 ModelScope / HuggingFace（以及可能的 Git LFS）

### 1.2 你要不要说话人（Speaker）？

你希望输出类似：

```
说话人1：...
说话人2：...
```

有三种常见方式（部署复杂度从低到高）：

1) **后端原生支持 speaker**（例如 FunASR PyTorch 的带 spk 管线）  
2) **External diarizer（推荐会议稳定性）**：启动 `xiyu-diarizer`（pyannote），让任意 ASR 后端都能输出说话人段落  
3) **Fallback diarization**：用一个“辅助后端”帮忙做分段，另一个后端负责转写文本

更多细节见 `docs/MODELS.md`。

---

## 2. 安装 Docker / Compose

### 2.1 Linux（Docker Engine + Compose Plugin）

建议安装 **Docker Engine** + **Compose Plugin**（命令为 `docker compose`）。

安装完成后，验证：

```bash
docker --version
docker compose version
```

### 2.2 macOS / Windows（Docker Desktop）

安装 Docker Desktop 后，验证：

```bash
docker --version
docker compose version
```

> macOS 上 Docker Desktop 默认无法直接使用 NVIDIA GPU。  
> Windows 如需 GPU，通常要走 WSL2 + NVIDIA 驱动 + Docker/容器工具链（建议直接按 Linux GPU 方案在 WSL2 内部署）。

---

## 3. Linux GPU 从 0（NVIDIA 驱动 + 容器 GPU）

> 这一节的目标是：让容器里能看到 GPU（`--gpus all` 可用）。

### 3.1 安装 / 验证 NVIDIA 驱动

先确认宿主机能看到 GPU：

```bash
nvidia-smi
```

如果 `nvidia-smi` 不可用，请先安装驱动（不同发行版步骤不同，建议跟随 NVIDIA/发行版官方指南）。

### 3.2 安装 NVIDIA Container Toolkit（让 Docker 容器用 GPU）

不同发行版命令可能略有差异。以下是常见做法的示例（以 Ubuntu/Debian 风格为例）：

```bash
# 1) 安装 nvidia-container-toolkit
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# 2) 配置 Docker runtime（新版本推荐 nvidia-ctk）
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### 3.3 验证容器能看到 GPU

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

看到显卡信息即表示 OK。

> Xiyu 的 GPU 镜像基于 `pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime`（CUDA 12.4）。  
> 如果你的驱动版本过老，可能会出现 “CUDA driver version is insufficient”。

---

## 4. 拉代码 & 配置 `.env`

### 4.1 拉取仓库

```bash
git clone https://github.com/skygazer42/Xiyu.git
cd Xiyu
```

### 4.2 创建 `.env`

```bash
cp .env.example .env
```

你可以先不改任何东西直接跑；常见需要调整的是：

- `PORT`：主服务端口（默认 `8000`）
- 代理：`HTTP_PROXY/HTTPS_PROXY/...`（如果你访问 HuggingFace/ModelScope 需要代理）
- `HF_TOKEN`：如果你要用 external diarizer（pyannote），且模型需要 gated 权限

---

## 5. Docker Compose 启动

### 5.1 方式一：单容器（推荐入门）

#### GPU（Linux）

```bash
docker compose up -d --build
```

#### CPU（macOS/Windows/Linux 都可）

```bash
docker compose -f docker-compose.cpu.yml up -d --build
```

### 5.2 查看状态 / 日志 / 停止

```bash
# 看容器
docker compose ps

# 看日志
docker compose logs -f

# 停止并清理
docker compose down
```

---

## 6. 验证服务是否可用

### 6.1 健康检查

```bash
curl -sS http://localhost:8000/health
```

预期：

```json
{"status":"healthy","version":"..."}
```

### 6.2 打开文档 / UI

- API Docs：`http://localhost:8000/docs`
- Web UI：`http://localhost:8000`

---

## 7. 不用 Docker：本地 Python 启动

### 7.1 单进程启动（主服务）

适合你想在裸机环境自行管理 Python 依赖/进程的场景。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

ASR_BACKEND=pytorch PORT=8101 python -m src.main
```

然后访问：`http://localhost:8101/docs`

> 注意：本地 pip 安装会拉取 `torch/funasr` 等重依赖，建议优先 Docker。

### 7.2 一键会议栈（推荐：PyTorch + External Diarizer）

如果你希望“会议场景默认带说话人 1/2/3”，并且不想记多条命令，可以用本地启动器：

```bash
python scripts/local_stack.py start --mode meeting
python scripts/local_stack.py status
python scripts/local_stack.py logs --tail 200
python scripts/local_stack.py stop
```

推荐把主服务与 diarizer 分别放在不同 venv（避免依赖冲突/体积过大）：

```bash
XIYU_PYTHON=./.venv/bin/python \
DIARIZER_PYTHON=./.venv-diarizer/bin/python \
python scripts/local_stack.py start --mode meeting
```

> diarizer 依赖在 `requirements.diarizer.txt`，并可能需要 `HF_TOKEN` 才能下载/使用某些 pyannote 模型。

---

## 8. 下一步（多模型 / 全家桶）

- 多模型容器（每个后端一个端口）：`docs/MODELS.md`
- 常见问题排障：`docs/TROUBLESHOOTING.md`
- API 参考：`docs/API.md`

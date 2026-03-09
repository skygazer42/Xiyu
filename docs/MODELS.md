# 多模型 / 多后端说明

当前仓库的推荐部署已经收敛为“单入口 + 内部多后端”：

- 推荐 compose: 根目录 `docker-compose.yml`
- 唯一默认对外端口: `18200`
- 公开入口: `xiyu-router`

因此，大多数场景下你不需要再直接暴露 `810x / 820x / 8300 / 8400 / 900x` 这些端口。

## 1. 推荐形态

### 单入口 router 栈

```bash
cp .env.example .env
docker compose up -d --build
```

访问：

- `http://<server-ip>:18200`

特点：

- Web UI 和 API 都通过 `18200` 访问
- Router 负责在内部转发到 Qwen3 / PyTorch / ONNX / SenseVoice / Whisper 等容器
- diarizer 与 ClearVoice 默认作为内部微服务存在

## 2. 为什么还保留“多模型端口”？

legacy 多端口部署仍然有用，主要用于：

- A/B 对比
- 模型专项压测
- 某个后端单独排障
- 某些环境下按后端拆分暴露

这些 compose 文件已经移到：

- `docker/compose/legacy/docker-compose.models.yml`
- `docker/compose/legacy/docker-compose.remote-asr.yml`
- `docker/compose/legacy/docker-compose.cpu.yml`
- `docker/compose/legacy/docker-compose.onnx.yml`
- `docker/compose/legacy/docker-compose.sensevoice.yml`

## 3. Legacy models compose 用法

### 启动单个 profile

```bash
docker compose -f docker/compose/legacy/docker-compose.models.yml --profile pytorch up -d
docker compose -f docker/compose/legacy/docker-compose.models.yml --profile whisper up -d
docker compose -f docker/compose/legacy/docker-compose.models.yml --profile qwen3 up -d
```

### 启动 router profile

```bash
PORT_XIYU_ROUTER=18200 \
docker compose -f docker/compose/legacy/docker-compose.models.yml --profile router up -d
```

如果你使用 legacy router，也建议继续把公开入口保持为 `18200`。

### 停止

```bash
docker compose -f docker/compose/legacy/docker-compose.models.yml down
```

## 4. Legacy 端口对照

这些端口是 legacy 直连端口，不是推荐的默认公开入口：

| 端口 | 服务 | 说明 |
|------|------|------|
| `18200` | `xiyu-router` | 推荐公开入口 |
| `8101` | `xiyu-pytorch` | PyTorch |
| `8102` | `xiyu-onnx` | ONNX |
| `8103` | `xiyu-sensevoice` | SenseVoice |
| `8104` | `xiyu-gguf` | GGUF |
| `8105` | `xiyu-whisper` | Whisper |
| `8201` | `xiyu-qwen3` | Qwen3 wrapper |
| `8202` | `xiyu-vibevoice` | VibeVoice wrapper |
| `8300` | `xiyu-diarizer` | external diarizer |
| `8400` | `xiyu-clearvoice` | ClearVoice |
| `9001` | `qwen3-asr` | 远程 ASR server |
| `9002` | `vibevoice-asr` | 远程 ASR server |

内部容器端口仍然是 `8000`，那是容器内监听端口，不是宿主机推荐公开端口。

## 5. 前端如何切后端

### 推荐方式

- 打开 `http://<server-ip>:18200`
- 在前端里保持 `当前服务 (相对路径)`
- 通过 router 的目标模型选择内部后端

### legacy 直连方式

如果你确实在做 A/B 测试，可以把前端切到：

- `http://<server-ip>:8101`
- `http://<server-ip>:8102`
- `http://<server-ip>:8201`

但这不是默认对外交付方式。

## 6. VibeVoice / GGUF 补充

### VibeVoice

VibeVoice 的最小源码快照已经随仓库放在：

- `third_party/VibeVoice/`

legacy compose 默认会挂载这份目录。

### GGUF

GGUF 仍然需要你准备本地模型文件：

```text
./data/models/Fun-ASR-Nano-GGUF/
  Fun-ASR-Nano-Encoder-Adaptor.int8.onnx
  Fun-ASR-Nano-CTC.int8.onnx
  Fun-ASR-Nano-Decoder.q8_0.gguf
  tokens.txt
```

## 7. 说话人策略

推荐会议场景：

- 优先开启 external diarizer
- 在单入口下通过 `18200` 访问 router
- 让 router 内部转发，避免终端用户记多个端口

如果你正在排查某个后端的 speaker 行为，再临时直连对应 legacy 端口。

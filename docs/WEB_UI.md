# Web UI 使用指南（前端地址 / 配置 / 使用）

本项目的 Web 前端（SPA）默认**随服务一起打包**在 Docker 镜像里，并由后端 FastAPI 直接提供静态文件，因此你通常不需要单独部署前端。

如果你是第一次部署，建议先看：`docs/DEPLOYMENT.md`（从 0 → 跑起来）。  
如果你要多模型 + Router（生产常见形态），看：`docs/MODELS.md`。

---

## 1. 这个项目能做什么（功能概览）

- **音频转写（ASR）**：支持多后端（FunASR PyTorch / ONNX / SenseVoice / GGUF / Whisper / 远程 Qwen3-ASR / Router）。
- **说话人识别（Diarization）**：
  - 后端原生支持（如 FunASR spk 管线）。
  - 或启用 external diarizer（pyannote），让任意后端都能输出 `speaker_turns`。
- **降噪（可选）**：ClearVoice 降噪（默认权重：`MossFormer2_48000Hz`），支持前端一键“降噪音频下载”。
- **长音频（3–4h 会议）**：支持异步队列、长音频智能分块、断点续跑（可选）。
- **全量优化（多模型对比/融合）**：前端可一键并发跑多个模型，并可选 LLM “融合润色”输出最终稿。
- **政务/企业格式化（可选）**：对日期/文号/金额/百分比等做模板化规范（用于会议纪要可读性与一致性）。
- **监控与排障**：内置 `/metrics`（JSON）与 `/metrics/prometheus`，前端提供监控页。

---

## 2. 前端访问地址（你要打开哪个 URL）

你看到的“前端地址”，就是服务端口的根路径 `/`：

### 2.1 单容器（入门/单模型）

- 前端入口：`http://<server-ip>:8000`
- API 文档：`http://<server-ip>:8000/docs`
- 健康检查：`http://<server-ip>:8000/health`

> 端口来自 `.env` 的 `PORT`（默认 `8000`）。

### 2.2 多模型 + Router（推荐生产：对外只开 1 个端口）

推荐对外只暴露 Router 端口（Web UI + 统一 API 入口）：

- 前端入口：`http://<server-ip>:8200`（默认）
- API 文档：`http://<server-ip>:8200/docs`
- 健康检查：`http://<server-ip>:8200/health`

> 端口来自 `.env` 的 `PORT_XIYU_ROUTER`（默认 `8200`）。  
> 如果你希望对外入口就是 `:8000`，把 `.env` 里改为：`PORT_XIYU_ROUTER=8000`。

### 2.3 为什么我用 `curl http://.../` 看到的是 JSON？

这是正常的：`GET /` 默认返回 JSON（方便脚本/健康探测），**浏览器**访问时会带 `Accept: text/html`，才会返回前端页面。

如果你想在命令行强制拿到 HTML，可以：

```bash
curl -H 'Accept: text/html' -I http://<server-ip>:8000/
```

---

## 3. 前端里怎么“配置后端地址”（最重要）

进入页面后，右侧「转写选项」里有一个“后端”下拉（或“服务地址”）。

### 3.1 推荐设置：`当前服务 (相对路径)`

当你的 Web UI 和 API 在同一个域名/端口（最常见：直接打开 `:8000` 或 `:8200`）：

- 选择：`当前服务 (相对路径)`
- 效果：所有请求都发往当前页面所在的服务（同源）

这也是 **Router 部署**最推荐的用法：公司内网只需要放行 Router 一个端口。

### 3.2 多端口部署（A/B 对比或排障）

当你启动了多个后端（`8101/8102/8103/...`）并希望直连某个后端：

- 直接在下拉里选择预置端口（如 `PyTorch (8101)`、`Whisper (8105)`）
- 或选择“自定义”，填 `http://<server-ip>:<port>`

### 3.3 Router 的“目标模型（Router）”

当后端选择的是 Router（或当前服务探测到 `backend=router`）时，会出现「目标模型（Router）」下拉：

- `auto`：按 Router 策略自动路由（推荐）
- `qwen3`：强制使用 Qwen3-ASR（你们不启用 VibeVoice 时就选它）
- 其他：用于 A/B 对比（前提是对应 profile 已启动）

---

## 4. 前端怎么用（会议场景的推荐操作）

### 4.1 普通转写（单文件）

1) 上传音频文件  
2) 按需打开：
   - `ClearVoice 降噪`（更准但更慢）
   - `说话人识别`（需要后端支持或 external diarizer）
3) 点击「开始转写」

输出会包含：
- `text`：可读文本
- `sentences`：带时间轴的句子
- `speaker_turns`：说话人段落（启用说话人时）
- `srt`：字幕（若后端支持生成/导出）

### 4.2 全量优化（多模型并发 + 可选 LLM 融合）

在「开始转写」旁边点击「全量优化」：

- “仅对比（不走 LLM）”：并发跑多个模型，便于你看哪个更准
- “严格/平衡/激进”：会在多模型结果之上调用 LLM 做融合与润色

政务会议通常建议先用：
- `严格（少改动）` 或 `平衡（推荐）`

### 4.3 长音频（3–4 小时会议）：用“长音频队列”

长会议不建议用同步 HTTP 直接等结果，推荐：

1) 上传音频  
2) 点击「加入队列」  
3) 下方任务列表会显示进度；完成后点“查看结果/下载”

> 说明：队列模式会先返回 `task_id`，后端后台跑，避免浏览器/反向代理超时。

### 4.4 降噪后的音频下载（WAV）

如果你想拿到“降噪后的音频”做留存或二次处理：

1) 上传单个文件  
2) 打开「ClearVoice 降噪」  
3) 点击「降噪音频」按钮 → 会下载 `<原文件名>.denoised.wav`

> 当前“降噪音频下载”仅支持单文件（UI 已提示）。

---

## 5. `.env` 里和前端/访问相关的关键配置

### 5.1 端口（最常改）

- 单容器：`PORT=8000`
- Router：`PORT_XIYU_ROUTER=8200`（你也可以改成 `8000`）

### 5.2 ClearVoice（降噪）

建议保持默认：

- `CLEARVOICE_MODEL=MossFormer2_48000Hz`

并确保你真的启动了 ClearVoice：

```bash
# 多模型 compose
docker compose -f docker-compose.models.yml --profile clearvoice up -d

# 单容器 compose（需要 profile）
docker compose --profile clearvoice up -d
```

### 5.3 说话人（external diarizer）

建议生产用 external diarizer（稳定性更好），并按需控制人数范围：

- `SPEAKER_EXTERNAL_DIARIZER_ENABLE=true`
- `DIARIZER_NUM_SPEAKERS=`（可选）
- `DIARIZER_MIN_SPEAKERS=` / `DIARIZER_MAX_SPEAKERS=`（可选）

---

## 6. 访问不了前端？（快速自检清单）

1) 宿主机上容器是否在跑：`docker ps`  
2) 健康检查是否正常：`curl http://<server-ip>:<port>/health`  
3) 端口映射是否对外：检查 compose 里的 `ports:` 和 `.env` 中 `PORT/PORT_XIYU_ROUTER`  
4) 服务器防火墙/安全组是否放行该端口  
5) 如果 `curl http://.../` 返回 JSON，但浏览器打不开：多半是网络/防火墙问题（不是服务没起）


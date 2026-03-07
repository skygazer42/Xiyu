# Enterprise Hotwords + Long-Audio Jobs (Resume) + Word Alignment — Design

**Date:** 2026-03-07  
**Context:** 常州政务会议记录（3–4 小时长音频、专有名词多、要求可恢复、需要更细粒度时间戳用于检索/定位/编辑）

## Goals

实现并默认可用（可配置开关）的三项企业级能力：

### A) 热词分层 + 变体/别名自动生成（更稳的强纠错）

- 保持现有 `hotwords.txt`/`hotwords-context.txt` 的用法不变
- 强制热词（forced）支持 **canonical + aliases**：
  - 替换输出始终使用 canonical（避免输出“别名变体”）
  - 别名仅用于匹配召回（提高纠错命中率）
- 自动生成常见变体（重点解决“数字/小数/符号/全半角/APP 版本号”等）
- API / reload 仍以 “canonical 数量” 作为统计口径（避免 count 暴涨）

### B) 长音频 Job 化 + 断点续跑（Chunk Checkpointing）

- `transcribe_long_audio` 在 **分块转写** 过程中将每个 chunk 的结果落盘（checkpoint）
- 当任务中断（进程重启/容器重启/网络异常）后，可通过相同 `checkpoint_id` **跳过已完成 chunk** 继续跑
- checkpoint 目录存储：
  - `meta.json`：chunk 划分/参数/总样本数等元信息（保证 resume 一致性）
  - `chunks/*.json`：每个 chunk 的结果（成功/失败/错误）
  - `result.json`：最终合并输出（可选）

### C) 词级时间戳（alignment 输出，用于检索/定位/编辑）

- 在长音频 chunk 合并路径中，输出 `words`（每个 token 的 `start_ms/end_ms`）
- 基于已有 `text_accu` 的 “字符级合并”能力，返回 **近似但稳定** 的字/词时间戳：
  - 先返回 char-level 时间戳（内部），再聚合为 word-level
  - 不引入 WhisperX 等重依赖（保持镜像轻量）
- 对短音频（非 chunked）可选用 “句内线性分配” 生成 word 时间戳（best-effort）

## Non-goals (Phase 1)

- 不引入 GPU 强制对齐模型（如 WhisperX/wav2vec2 aligner）作为硬依赖
- 不做复杂的“热词每条自定义阈值”语法（先做 canonical/alias + 自动变体）
- 不做 Redis/MySQL 级别的任务队列（先做落盘 checkpoint，接口保持简单）

## Proposed Data Model / Config

### A) Hotword aliases syntax (optional)

保持兼容：每行仍是一个“canonical 热词”。新增可选别名语法：

```
canonical | alias1 | alias2
```

- `canonical`：最终替换输出
- `alias*`：仅用于匹配召回
- 不写 `|` 时，行为与当前完全一致

自动变体（无需手工写）：对 canonical 生成少量稳定变体（受上限控制），例如：
- 全角→半角（NFKC）
- “2.0 / 2点0 / 二点零 / 2.0版”等常见数字表达

### B) Checkpointing options (asr_options.chunking)

新增（均为可选）：

- `checkpoint_enable: bool`（默认 false；异步长音频任务可在服务端默认开启）
- `checkpoint_id: str`（默认自动生成：基于音频 PCM 的 hash + 关键参数）
- `checkpoint_dir: str`（默认 `${outputs_dir}/jobs`）
- `resume_skip_existing: bool`（默认 true，只跳过 `success=true` 的 chunk）

### C) Alignment options (asr_options.alignment)

新增（均为可选）：

- `enable: bool`（默认 false）
- `level: "word"|"char"`（默认 word）
- `max_words: int`（默认 20000，避免超长会议输出过大）

输出字段（后端统一）：

- `words: [{text,start,end}]`（ms）

## Testing / Verification

- 单测：
  - Hotword alias/canonical 输出正确（别名命中时替换为 canonical）
  - Checkpoint：模拟中断后 resume 能跳过已完成 chunk
  - Word timestamps：长度一致/单调递增/边界合理
- 回归：`pytest -q`
- 烟测：长音频用 `asr_options.chunking.checkpoint_enable=true`，中断后重跑能继续


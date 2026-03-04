# Advanced `asr_options` Editor + Exports + Qwen3 Postprocess Defaults — Design

**Date:** 2026-02-16  
**Scope:** Xiyu (`/Users/luke/code/xiyu`)  
**Primary scenario:** 多端口多模型（`docker-compose.models.yml`）+ 前端选择后端端口做转写 A/B，对会议/回忆转写要“可读、可导出、可调参”。

---

## Goal

1) **高级调参（`asr_options`）在前端可编辑**
- 提供一个“高级/Expert”入口：用户可以直接编辑 `asr_options` JSON（按请求生效）。
- 提供几个常用模板：长音频准确率优先、Qwen3 强后处理、会议说话人段落等。
- 前端应尽量在本地做 JSON 校验，避免发出 400。

2) **导出更适合会议/回忆场景**
- 在现有 `.txt / .srt / .json` 的基础上：
  - 增加 `.md`（会议纪要/回顾友好，优先按 `speaker_turns`）
  - 增加 `.vtt`（WebVTT，更现代，支持 `<v Speaker>`）
  - 增加 `.doc`（可被 Word 直接打开的 HTML “伪 doc”，避免引入 docx 依赖）
- `.txt` 默认优先导出 `transcript`，其次 `text_accu`，最后 `text`。

3) **针对 Qwen3-ASR 提供“强后处理”默认/模板**
- Qwen3-ASR（OpenAI-compatible chat completions with `audio_url`）本身不提供说话人分离。
- 其输出往往更需要后处理（例如：英文缩写空格、CJK/ASCII 间距、标点/重复标点）。
- 方案优先采用：
  - 前端模板（`asr_options.postprocess`）便于 A/B
  - 同时补齐后端 Settings/engine 的能力：支持将 `spoken_punc_enable` / `acronym_merge_enable` 作为全局默认，并能被 `asr_options` 覆盖

---

## Non-goals (v1)

- 在前端做一个完整可视化的“每个字段都有 UI 控件”的 asr_options 表单（可以后续迭代）。
- 引入 docx 生成依赖（npm `docx` / `pizzip` 等），先用 `.doc` (HTML) 解决。
- 为 Qwen3 增加真正的说话人分离（需要额外 diarization pipeline）。

---

## Key Design Decisions

### A) `asr_options` 的合并策略（前端）

前端会同时生成两类 options：
- **UI 自动生成**：例如开启说话人后，自动加入 `asr_options.speaker.label_style`。
- **用户高级输入**：高级编辑器中的 JSON。

合并规则（v1，简单且可解释）：
- `advanced_json` 作为 base
- UI 自动生成覆盖/补齐其子字段（例如 speaker label_style）
- 合并仅做一层对象合并（top-level section + section 内 key 的浅合并），不做复杂深合并。

### B) 导出字段优先级

- **Markdown / Doc**：优先 `speaker_turns`（如果存在），否则用 `transcript/text_accu/text`。
- **SRT / VTT**：使用 `sentences`（字幕格式需要时间戳）。

### C) Qwen3 “强后处理”推荐

推荐模板（可通过前端一键填充到 `asr_options`）：

```json
{
  "postprocess": {
    "spoken_punc_enable": false,
    "acronym_merge_enable": true,
    "spacing_cjk_ascii_enable": true,
    "punc_convert_enable": true,
    "punc_merge_enable": true
  }
}
```

说明：
- `acronym_merge_enable`：A I → AI，VS Code 相关场景更友好
- `spacing_cjk_ascii_enable`：中英文/数字可读性更好（可按需关）
- `punc_merge_enable`：清理重复标点
- `spoken_punc_enable`：仅 dictation “说逗号/句号”场景，默认不启用

---

## References (web research)

- WebVTT voice tags (`<v Speaker>`) 属于 WebVTT 的常见写法（很多播放器/解析器支持）。  
- vLLM OpenAI-compatible “Speech-to-Text” 文档显示 Qwen3-ASR 属于 speech-to-text 模型类别；并未提供 diarization 能力描述。

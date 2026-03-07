# Changzhou Gov Meeting Hotwords (Forced + Context + Rectify) — Design

**Date:** 2026-03-07  
**Context:** 常州政府数字政府/政务服务/AI 相关会议记录（长音频、多部门、多术语，专有名词召回与错词纠正是主要痛点）

## Goal

把“默认热词体系”从示例/娱乐词（如麦当劳、Bilibili 等）切换为 **常州政务会议** 更匹配的词表与纠错历史：

- 强制热词（forced）更“少而精”，用于高价值且易错的专有名词/固定短语
- 上下文热词（context）更“多而广”，用于注入提示提升召回
- 纠错历史（rectify）改为政务会议常见错例，供 LLM 润色阶段检索参考
- 同步更新前端占位示例与 README 示例，避免“看起来随便写”的误导

## Non-goals

- 不引入“知识库 RAG/会议纪要知识问答”类功能（这里的 RAG 是音素热词检索/纠错）
- 不尝试自动从外部库（如 CapsWriter-Offline）全量同步热词（保持本项目可控、可审计）
- 不让规则替换（`hot-rules.txt`）变成大而全的全局强替换（避免误伤）

## Current Constraints (Why This Design)

- 系统默认最多注入 `HOTWORD_INJECTION_MAX=50` 条上下文热词：**文件前 50 行优先级最高**
- forced 热词会进入“音素 RAG + 边界约束 + 字形重排”的强纠错路径：词表过大/过泛会增加误替换风险
- rectify 是 LLM 阶段的“纠错历史检索提示”，不是硬替换：可以更丰富，但要与政务场景一致

## Proposed Hotword Taxonomy

### 1) Forced hotwords (`data/hotwords/hotwords.txt`)

**定位：强纠错/强召回的关键专有词。**

原则：
- 只放“错一个字价值就差很多”的词（平台名/应用名/专有政策短语/部门名/区县名）
- 常州本地名词（常州、各区县、重点平台/应用）优先
- 通用政策话术（贯彻落实、工作部署）不放 forced，放 context

常州会议建议重点覆盖：
- 地名/区县：常州市、天宁区、钟楼区、新北区、武进区、金坛区、溧阳市、常州经开区等
- 本地平台/应用：政企通、政企通2.0、我的常州、畅通办、帮吾办等
- 政务服务核心短语：一网通办、跨省通办、高效办成一件事、免申即享、免证办、容缺受理、告知承诺制等
- 关键底座：电子证照、统一身份认证、共享交换平台、政务外网/内网、等保2.0、关基等

### 2) Context hotwords (`data/hotwords/hotwords-context.txt`)

**定位：提示注入，不强替换（更稳）。**

原则：
- 允许更大规模（上百条甚至更多），但必须把“常州最相关的 50 条”放文件最前面
- 可包含更丰富的政务会议表达、平台能力词、AI 治理术语等

结构建议：
1. **Top-50（强优先注入）**：常州本地 + 会议核心术语
2. 数字政府/政务服务高频表达
3. 平台/系统/数据底座词
4. AI+政务/大模型治理词
5. 组织机构（必要时补充江苏省/市级部门名称）

### 3) Rectify history (`data/hotwords/hot-rectify.txt`)

**定位：LLM 阶段检索“错词→正词”历史片段，提升润色一致性与纠错可控性。**

原则：
- 清空与政务无关的示例（Cloud Code、麦当劳等）
- 收录政务会议常见错例（尤其是常州本地平台/地名/政策短语）
- 以“最常见/最痛”的错例为主，便于检索命中

## UX / Documentation Alignment

为避免误导，把以下默认示例统一替换为常州政务会议相关词：
- 前端热词页的输入占位示例
- README 的 API 调用示例（hotwords 传参示例）

## Files In Scope

- Modify: `data/hotwords/hotwords.txt`
- Modify: `data/hotwords/hotwords-context.txt`
- Modify: `data/hotwords/hot-rectify.txt`
- Modify: `frontend/src/pages/HotwordsPage.tsx`（占位示例文案）
- Modify: `README.md`（接口示例热词）

## Testing / Verification

- 单测回归：`pytest -q`
- 接口验证：
  - `POST /api/v1/hotwords/reload`
  - `POST /api/v1/hotwords/context/reload`
  - `GET /api/v1/hotwords` / `GET /api/v1/hotwords/context` 检查内容与 count
- 人工核查：
  - `hotwords-context.txt` 前 50 行是否为常州重点词
  - UI/README 是否不再出现与政务无关的示例词


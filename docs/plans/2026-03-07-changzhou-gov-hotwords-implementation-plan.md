# Changzhou Gov Meeting Hotwords — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把默认热词/纠错历史/示例文案调整为“常州政府数字政府/政务服务/AI 会议”更贴合的配置，并确保 reload 接口与测试通过。

**Architecture:** 只调整“热词数据文件 + rectify 历史文件 + 文档/前端示例文案”，不改核心算法。遵循 `forced 少而精、context 多而广、rectify 政务化`，并保证 `hotwords-context.txt` 前 50 条优先覆盖常州本地词。

**Tech Stack:** FastAPI（热词 reload 接口），前端 React（占位示例），pytest，纯文本热词文件。

---

### Task 01: Update forced hotwords (政务会议强纠错词表)

**Files:**
- Modify: `data/hotwords/hotwords.txt`

**Step 1: Rewrite the forced list**
- 把常州本地（地名/区县/平台名）与高价值政务服务短语放入 forced
- 避免放入过多通用话术（移到 context）

**Step 2: Sanity check**
- 确认 forced 词条数量可控（建议 50–150 条）
- 确认无与政务无关的示例词

### Task 02: Update context hotwords (注入提示词表)

**Files:**
- Modify: `data/hotwords/hotwords-context.txt`

**Step 1: Reorder top 50**
- 文件最前面 50 行放“常州最相关”的词（常州、政企通、我的常州、畅通办、区县等 + 会议关键术语）

**Step 2: Expand the long-tail list**
- 补充数字政府/政务服务/数据治理/AI+政务常用表达与平台能力词

**Step 3: Sanity check**
- 确认没有把 forced 里“强纠错词”重复到 context 的最前面（允许重复，但避免浪费 top-50 配额）

### Task 03: Replace rectify history (供 LLM 检索的纠错历史)

**Files:**
- Modify: `data/hotwords/hot-rectify.txt`

**Step 1: Remove unrelated examples**
- 清空 Cloud Code/麦当劳等示例

**Step 2: Add gov-meeting corrections**
- 加入常州本地平台/地名/政策短语常见错例（wrong/right 两行 + `---` 分隔）

### Task 04: Align UI placeholder + README examples

**Files:**
- Modify: `frontend/src/pages/HotwordsPage.tsx`
- Modify: `README.md`

**Step 1: Replace placeholder examples**
- 把“麦当劳/肯德基/Bilibili”等示例替换为“常州政务会议”相关词

**Step 2: Replace README API examples**
- 把 hotwords 示例改为政务词（如“政企通/我的常州/高效办成一件事”等）

### Task 05: Verification

**Step 1: Run unit tests**

Run: `pytest -q`  
Expected: PASS

**Step 2: (Optional) Verify reload endpoints**

Run:
- `curl -s -X POST http://localhost:8000/api/v1/hotwords/reload | jq`
- `curl -s -X POST http://localhost:8000/api/v1/hotwords/context/reload | jq`
Expected:
- 返回 `code=0`
- `count` > 0

### Task 06: Commit and push

**Step 1: Commit**

Run:
- `git add data/hotwords/hotwords.txt data/hotwords/hotwords-context.txt data/hotwords/hot-rectify.txt README.md frontend/src/pages/HotwordsPage.tsx`
- `git commit -m "feat: tune default hotwords for Changzhou gov meetings"`

**Step 2: Push**

Run: `git push`


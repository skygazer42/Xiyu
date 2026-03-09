# Gov Hot Rules Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 调整默认 `hot-rules.txt`，使其更贴近常州政务会议场景，并通过测试与运行时 reload 验证。

**Architecture:** 先用测试锁定代表性政务强规则，再最小修改 `data/hotwords/hot-rules.txt` 内容，最后通过规则测试和规则重载接口确认文件语法与加载流程均正常。

**Tech Stack:** Python pytest、规则文本文件、FastAPI 热词管理接口

---

### Task 1: Add the failing ruleset test

**Files:**
- Modify: `tests/test_rule_corrector.py`
- Test: `tests/test_rule_corrector.py`

**Step 1: Write the failing test**

Add a test that loads `data/hotwords/hot-rules.txt` from the repository and asserts several representative政务场景替换结果，例如：

- `数字面试工程` -> `数字面子工程`
- `人工智能家政` -> `人工智能+政务`
- `政企同` -> `政企通`
- `我的常舟` -> `我的常州`
- `免申既享` -> `免申即享`
- `电子征兆库` -> `电子证照库`
- `统一身份认正` -> `统一身份认证`
- `数据共享交互平台` -> `数据共享交换平台`
- `苏彩云` -> `苏采云`

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_rule_corrector.py -q`

Expected: FAIL because current repository rules do not yet cover all of those government-scene replacements.

### Task 2: Update the default hot-rules.txt

**Files:**
- Modify: `data/hotwords/hot-rules.txt`

**Step 1: Replace low-value defaults**

Remove most low-value unit/temperature defaults that do not fit the default government-meeting scene.

Keep only low-risk general-purpose rules such as:

- 邮件地址格式化
- 中文标点口述替换

**Step 2: Add conservative government-scene strong rules**

Add a compact grouped ruleset covering:

- 面子工程
- AI+政务 / 人工智能+政务
- 政企通 / 我的常州 / 畅通办 / 帮吾办 / 常治慧
- 一网通办 / 跨省通办 / 免申即享 / 免证办
- 电子证照 / 电子证照库 / 统一身份认证 / 数据共享交换平台
- 等保2.0 / 关基
- 苏采云

**Step 3: Keep the rules conservative**

Do not add broad regexes that can rewrite unrelated common words. Prefer explicit narrow patterns or very small regex families.

### Task 3: Verify file validity and runtime reload

**Files:**
- Verify: `tests/test_rule_corrector.py`
- Verify: `data/hotwords/hot-rules.txt`

**Step 1: Run the targeted test**

Run: `python -m pytest tests/test_rule_corrector.py -q`

Expected: PASS for the new government-scene substitutions.

**Step 2: Reload rules through the running service**

Run:

```bash
curl -sS -X POST http://localhost:18200/api/v1/hotwords/rules/reload
```

Expected: JSON response with `message` indicating rules reloaded successfully.

**Step 3: Sanity-check the file content**

Run:

```bash
sed -n '1,220p' data/hotwords/hot-rules.txt
```

Expected: file is clearly grouped for the default Changzhou government-meeting scenario.

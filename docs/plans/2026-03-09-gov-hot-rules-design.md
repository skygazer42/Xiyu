# Gov Hot Rules Design

## Context

当前仓库的 `data/hotwords/hot-rules.txt` 仍包含较多通用“单位/符号”示例规则，而常州政务会议场景相关的强规则替换较少。

与此同时，仓库中的：

- `data/hotwords/hotwords.txt`
- `data/hotwords/hotwords-context.txt`
- `data/hotwords/hot-rectify.txt`

已经明显向“常州政务会议”场景迁移，因此 `hot-rules.txt` 与其它默认词表之间出现了定位不一致。

## Goal

将默认 `hot-rules.txt` 调整为更贴近常州政务会议场景的强规则替换集：

- 移除大部分与该场景关联度较低的默认单位换写规则
- 保留极少量通用低风险规则
- 新增一组“高确定性、低误伤”的政务场景强规则

## Non-goals

- 不把 `hot-rules.txt` 扩展成大规模纠错词库
- 不替代 `hotwords.txt` / `hotwords-context.txt` 的职责
- 不做激进的广义同音替换

## Rule Strategy

本次新增规则只采用以下类型：

1. 明显错误、且目标词唯一的政务短语
2. 常州本地平台或政务平台的高频固定误写
3. 政务服务固定术语的高确定性错写

避免加入：

1. 通用名词的大范围正则
2. 脱离政务上下文后仍可能误伤的规则
3. 语义跨度过大的“猜测型修正”

## Expected Coverage

重点覆盖以下类别：

- 面子工程类误写
- AI+政务 / 人工智能+政务类误写
- 常州本地平台名：政企通、我的常州、畅通办、帮吾办、常治慧
- 政务服务高频术语：一网通办、跨省通办、免申即享、免证办
- 数据底座与能力：电子证照、电子证照库、统一身份认证、数据共享交换平台
- 安全合规：等保2.0、关基
- 采购/交易平台：苏采云

## Verification

通过两层验证确认修改有效：

1. 单元测试：直接加载仓库中的 `data/hotwords/hot-rules.txt`，验证若干代表性输入是否被替换为预期政务术语
2. 运行时验证：调用 `POST /api/v1/hotwords/rules/reload`，确认规则文件可正常重载

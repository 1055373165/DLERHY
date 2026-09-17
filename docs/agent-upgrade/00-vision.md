# 00 · 愿景：把 book-agent 升级为「model + harness」的翻译 agent

> 结论先行：book-agent 已经拥有一个罕见的资产——**以数据库为账本的、可恢复的、有门禁的翻译工作流**。它缺的不是「更多规则」，而是把模型从「一个只会翻 packet 的函数」升级为「在护栏内做判断、用工具行动、被传感器纠正的 agent」。升级路线是 **保留账本，重做与模型的接触面**。

## 1. 现状的诊断（来自 docs/architecture）

| 维度 | 现状 | 症状 |
|---|---|---|
| 模型的角色 | 单次调用：翻一个 packet / 生成一个 JSON 对象 | 结构歧义、审校判断、修复选择、术语决策全由英文正则与阈值承担，换一本书就失效（03 §12、04 §8.4、06 B-38） |
| 判断的位置 | 22 个 PDF recovery pass、16 项审校规则、450 行布局策略树、16 条 render 修补 | 每修一本书的问题就加一个分支；golden 快照是唯一护栏 |
| 上下文 | 每 packet 独立编译；首轮章节记忆为空；无书级记忆；术语表 bootstrap 为空 | 8 章并行 8 种译法；一致性只能事后补 |
| 自纠正 | 三个有界修复循环，动作由规则映射，模型只收 hint | 循环能停但常常「no_new_actions」；人工无法介入 issue |
| 调用面 | 四条 LLM 调用路径，各自 client/事务/计费 | 预算与成本失明；不可追踪 |
| 人在环 | 记忆提案审批、章节 owner 分配 | 没有 issue 级 triage，没有审批对象，没有决策记录 |

一句话：**harness 的「账本」很强，「循环、工具、上下文、传感器、审批」很弱或不存在**。

## 2. 升级后的目标形态

```
                 ┌──────────────── Harness Kernel ────────────────┐
                 │  AgentTurn（持久化 item 流）  ToolRegistry      │
  用户 / UI ───▶ │  Permission & Approval      Budget & Trace     │ ◀── MCP 客户端（Claude Code / Codex / 运维脚本）
                 │  Context Assembler（缓存前缀）Compaction        │
                 │  Hooks（pre/post tool，pre-persist）            │
                 └───────────────┬─────────────────────────────────┘
                                 │ work items（现有执行器：租约 / 重试 / 预算 / 阶段）
   ┌──────────────┬──────────────┼──────────────┬──────────────┬──────────────┐
   ▼              ▼              ▼              ▼              ▼              ▼
 Structure      Terminology    Translator     Reviewer /      Repair         Export QA
 Agent          Agent          (packet)       Editor Agent    Agent          Agent
 解析歧义       书级术语表      现有 prompt    模型审校 +      选动作 /       读渲染结果
 多模态读页     先于翻译        契约不变       规则传感器      定点编辑       报布局问题
   └──────────────┴──────────────┴──────────────┴──────────────┴──────────────┘
                                 │ 只能通过 typed tools 读写
                 ┌───────────────▼─────────────────────────────────┐
                 │  PostgreSQL 账本（现有 32 表 + agent_turns/items、decisions、approvals）│
                 │  Guides：BOOK.md、skills/、glossary   Sensors：规则检查、gate、evals   │
                 └─────────────────────────────────────────────────┘
```

三条不变的原则：

1. **模型不直接写库**。所有副作用经 ToolRegistry 里带权限等级的工具；不可逆工具（锁术语、重解析、WONTFIX、发布导出）需要审批对象或明确的自动审批策略。
2. **确定性传感器优先于模型判断**。现有规则检查、门禁、`OutputValidator`、术语 hook 都保留，升级为「能拒绝、能触发自纠正」的 sensors；模型评审是 inferential sensor，抽样运行并受预算约束。
3. **账本不变，接触面重做**。run / work item / lease / 阶段推导 / 事件总线继续是底座；agent turn 是一种新的 work item，享受同样的租约、重试与预算。

## 3. 「创新」的落点

业界 coding harness 解决的是「在文件系统里改代码」；book-agent 的 harness 解决的是「在一本书的结构化账本里做翻译决策」。可以借用的是循环、工具、权限、缓存、压缩、hooks/skills/subagents 这些机制；**领域特有的创新**在于：

- **结构即工作区**：agent 的「文件系统」是章/块/句/包的账本，工具是 `read_block / split_block / relabel_block / retranslate / edit_segment`，每个改动都有 provenance 与可回滚版本——这是 coding harness 里 `git` 扮演的角色。
- **书级决策记录（BOOK.md）**：像 AGENTS.md 一样进入缓存前缀，但由 Terminology Agent 与人在翻译前共同写成：术语表、体裁与语域、保留策略、已决议的歧义。它同时是 guide（喂给翻译）和 sensor 的依据（审校对照）。
- **双通道传感器**：computational（现有 16 项规则 + gate + validator）与 inferential（模型评审、回译一致性、抽样人审）叠加，且每条 issue 都标注 detector 与置信度，让修复策略按来源分级。
- **可披露的 harness 配置**：每个 run 记录 harness 版本、skills、模型、预算、工具集，导出稿的 manifest 里携带，评测时可复现。

## 4. 不做什么

- 不引入通用 agent 框架（LangGraph、Agents SDK 等）作为核心：账本与执行器已经是编排器，再套一层只会产生两套状态。可在边缘（评测脚本、运维）使用。
- 不让模型自由执行 shell 或任意 SQL：工具面是封闭的、类型化的。
- 不用 agent 替代解析的确定性部分（PyMuPDF 抽取、分段、分包）；agent 只处理这些部分「拿不准」的地方（由现有 `parse_confidence`、layout_risk、sanity 标记出来）。
- 不在 H0 之前动翻译 prompt 的契约（golden 快照仍是护栏）。

## 5. 成功标准（可度量）

| 指标 | 现状 | 目标 |
|---|---|---|
| 术语一致率（书级，strict） | 依赖事后 term-consistency（报告可见） | 首轮翻译 ≥ 95%，导出前 ≥ 99% |
| 每本书人工介入次数 | 不可度量（无 issue API） | 有 triage 队列，可统计；目标每 100 章 < 5 次阻断 |
| 预算命中率 | 概念解析/抽取/重译不计费 | 100% 的模型调用进 `events` 与 rollup，预算偏差 < 1% |
| 结构误判 | 只有 golden 字符串 diff | 每类 block 的分类准确率有 eval 集；模型辅助后对新书 ≥ 现有启发式 |
| 恢复性 | 402 暂停不可 resume | 任意阶段 kill -9 后恢复，无重复译文，无成本重复 |
| 交付可验收 | 无图覆盖率/未译比例阈值 | Export QA agent 产出验收报告，门禁可配置 |

# Interview Prep — book-agent

> 面试材料汇总。所有内容基于真实代码（`src/book_agent/`）而非凭空构造。

## 文件清单

| 文件 | 用途 | 何时读 |
|---|---|---|
| [01-project-pitch-review.md](./01-project-pitch-review.md) | 项目陈述的 20 年面试官 review + 重写版本 + 9 个高频追问及答题要点 + 演讲层面的默会知识 | 面试前一晚通读，第二天默背重写版本 |
| [02-system-architecture.md](./02-system-architecture.md) | ASCII 架构图 + D2 源码 + 8 个关键设计决策及 trade-off + 可观察性 + 扩展路径 | 白板题前重温；面对 "画一下你的系统" 时复刻 |
| [03-consistency-deep-dive.md](./03-consistency-deep-dive.md) | 跨章节 / 跨 packet 一致性的费曼式四层讲解（6 岁小孩 → 系统设计师）+ 6 个杀手级追问 | "你怎么保证一致性" / "你怎么测质量" 这类问题前 |

## 面试 60 秒电梯陈述（背下来）

> 我做了一个把英文技术书翻译成高保真中文版的 agent 系统。用 250 页的 LLM 技术书做基准，pipeline 自动完成约 760 个翻译 packet，端到端 wall-clock 15 分钟内，零人工介入。
>
> 难点不在调用 LLM，而在三件事：**PDF 结构恢复、跨章节一致性、失败决策**。
>
> 核心抽象是 `Document → Chapter → Block → Sentence → Packet` 五层 IR，PDF 和 EPUB 共享下游。Packet 是最小翻译单元 + 最小重试单元，同时优化 LLM 上下文命中率和故障爆炸半径。
>
> agent 决策核心是错误分类器 + 分层策略：transient / rate-limit 重试；quality / glossary 违反则 repair + 重译；budget / provider down 熔断暂停；parser bug 进 dead letter 升级 UI。**这是和普通 workflow 的真正区别——有界自治：能自愈的内部消化，不能自愈的明确升级。**

## 数字（必须背熟）

| 指标 | 数字 |
|---|---|
| 测试基准书 | 250 页 LLM 技术书 |
| 总 packet 数 | ~760（9 章） |
| 端到端 wall-clock | < 15 分钟 |
| 并发模型 | 4-8 worker per chapter |
| Packet 平均 token | ~500 input / ~250 output |
| Recovery pass 数 | 30+ |
| 关键 fix 案例 | 跨页标题、跨页段落、figure-internal 标签、figure 聚类 |

## 临场速查表（被问到时秒答）

| 问题 | 一句话答案 |
|---|---|
| "你的 agent 和普通 workflow 区别？" | 有界自治 + 分类错误处理。能自愈内部消化，不能自愈明确升级到 UI。 |
| "K8s 编排借鉴了什么？" | 声明式 reconciliation loop：编排器看 desired vs actual state，差异驱动 action。 |
| "为什么选 packet 粒度？" | chapter 太大（爆炸半径 + 超 window），sentence 太小（上下文丢失 + 调用量爆炸）。Packet 是 sweet spot。 |
| "为什么 prev/next 是原文不是译文？" | 时序解耦 + 错误隔离。原文是 immutable ground truth。 |
| "怎么保证术语一致性？" | 4 层记忆 + 2 层强制：BookProfile / Termbase / EntityRegistry / ChapterBrief 注入 prompt；post-validator 兜底重译。 |
| "怎么测质量？" | 反向 metric：glossary_violation 触发率 + 结构验证 R1-R8 + spot check 抽样。 |
| "最难的 bug？" | 跨页标题被 figure cluster 吞掉。三层 fix：parser pass exclusion + 跨页 split 放宽 + newline-aware fast-path。 |
| "重做最大改动？" | 早期就上 `_RecoveredBlock` 中间 IR，避免 parser 直接生成 DB schema 导致的 migration 痛苦。 |

## 给自己的最后提醒

1. **不要紧张就语速加快**。慢一点，每讲一个设计点停顿半秒让面试官消化。
2. **被问到不会的不要瞎编**。说 "这块我没系统研究过，目前只在 X 场景下验证过" — 诚实加分。
3. **主动提 trade-off**。每讲一个决策，主动说出代价。
4. **白板画图必须能 60 秒内完成**。三个框：parser / orchestrator-memory / worker-pool；五条线：data + control flow。
5. **结尾留 open question**：邀请面试官 push back，主动把球抛回。

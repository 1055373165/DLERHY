# Translation Consistency And Cache Plan

Last Updated: 2026-03-16

## 1. Problem Statement

真实书翻译已经暴露出两个高度相关的问题：

- **术语漂移**：同一书内已经建立的概念译法，在后续 packet 中被重新翻译成别的中文。
- **缓存命中不足**：长书逐 packet 调用 LLM 时，大量请求虽然“看起来很像”，但因为 prompt 前缀不稳定，无法有效命中 provider 的 prompt cache。

这两个问题不能分开看。  
从上下文工程角度，**一致性约束设计** 和 **缓存友好的 prompt 设计** 本质上都在回答同一个问题：

> 怎样让模型在每次调用中尽可能看到“稳定且可复用的上下文前缀”，同时把变化部分收敛到后半段。

## 2. Current Root Causes In This Project

基于当前实现，问题主要来自这几处：

### 2.1 Prompt 没有真正消费局部上下文

`ContextPacket` 中已经有：

- `prev_blocks`
- `next_blocks`
- `relevant_terms`
- `relevant_entities`

但在现有 prompt 构造里，真正送给模型的核心内容只有：

- `Heading Path`
- `Chapter Brief`
- `Relevant Terms`
- `Relevant Entities`
- `Current Sentences`

也就是说，本地上下文对象已经存在，但没有完整进入翻译 prompt。

相关代码：

- [translator.py](/Users/smy/project/book-agent/src/book_agent/workers/translator.py)
- [builders.py](/Users/smy/project/book-agent/src/book_agent/domain/context/builders.py)

### 2.2 prompt 前缀过早变动，不利于 provider cache

当前 prompt 中，`Packet ID` 出现在很靠前的位置。  
对于 DeepSeek 这类按“共享前缀”命中的缓存机制，这会明显破坏命中率。

### 2.3 termbase 目前仍偏“静态种子”，缺少运行时记忆

当前系统已经有：

- `term_entries`
- `termbase_snapshot`
- review 阶段的 locked term 检查

但还缺：

- 翻译过程中基于“已接受译文”的动态术语回灌
- 高置信概念锚点自动升级为 locked / preferred 的机制

### 2.4 术语一致性更多在 review 后暴露，而不是翻译前约束

目前 `TERM_CONFLICT` 更偏 QA 发现。  
理想状态应该是：

- **翻译前：** 术语约束编译
- **翻译中：** 局部翻译记忆注入
- **翻译后：** term drift / term conflict 复检

## 3. Principles

### Principle 1

对“这本书里已经确定的概念译法”，不能继续让模型自由发挥。

### Principle 2

一致性不是只靠 glossary 列表，而要靠：

- 锁定术语
- 局部已接受译文
- 章节摘要
- 结构化上下文

共同约束。

### Principle 3

缓存优化不是“减少 token”这么简单，而是：

> **最大化稳定前缀，最小化前缀中的动态差异。**

### Principle 4

对于长书，最实用的路线不是“一步做到完美的 concept graph”，而是：

1. 先把局部已接受译文真正喂给模型
2. 再做动态 term memory
3. 最后做全书级 concept registry / canonical glossary governance

## 4. Recommended Architecture

## 4.1 Translation Memory Hierarchy

建议把翻译记忆明确分成三层：

### A. Global canonical glossary

适用：

- locked term
- 角色名
- 机构名
- 固定译名

特征：

- 作用域大
- 变化少
- 应位于 prompt 的稳定前缀区域

### B. Chapter-local accepted translations

适用：

- 同一章内刚刚建立的概念译法
- 当前章节局部定义
- 高相关前文段落的已接受译文

特征：

- 对一致性帮助极大
- 比全书 glossary 更贴近当前段落
- 应放在 prompt 的中段

### C. Packet-local source context

适用：

- prev/current/next blocks
- 当前段落局部指代与省略

特征：

- 变化最大
- 放在 prompt 靠后区域

## 4.2 Cache-Friendly Prompt Layout

推荐顺序：

1. 固定 system policy
2. 固定 output contract
3. 全书 style policy
4. 全书 locked glossary
5. 章节摘要 / 章节 heading path
6. 局部已接受译文
7. prev/next source context
8. current sentences
9. packet-specific metadata（尽量后置，能不进 prompt 就不进）

这条顺序的目标是：

- 让最稳定的部分尽可能形成共享前缀
- 把 packet 级差异压到 prompt 尾部

## 5. What To Implement First

## 5.1 Phase 1: Immediate, low-risk wins

这是已经值得立刻做、且不需要重构数据库语义的部分：

- prompt 去掉前置 `Packet ID`
- `relevant_terms` / `relevant_entities` 排序稳定化
- `prev_blocks` / `next_blocks` 真正进入 prompt
- 加入 `Previous Accepted Translations`
- 术语匹配从当前 block 扩到 `prev/current/next` 合并上下文

这些改动的特点：

- 风险低
- 对质量和缓存都有立即帮助
- 不要求 term governance 先成熟

## 5.2 Phase 2: Dynamic term reinforcement

下一步建议实现：

- 基于历史 accepted translation 自动回灌局部术语
- 将高置信概念锚点升级成 `preferred / locked`
- 对 term drift 做跨 packet 检测

## 5.3 Phase 3: Full concept registry

最终形态应该是：

- `concept_id`
- `aliases`
- `canonical_zh`
- `scope`
- `lock_level`
- `evidence`
- `first_seen`
- `last_confirmed`

这会把“上下文工程 / 情境工程”这类问题，从概率性修复，升级成系统性防漂移。

## 6. DeepSeek Cache Guidance

结合 DeepSeek 官方缓存说明，当前项目最该遵守这几条：

### 6.1 稳定前缀优先

不要让：

- packet id
- 动态统计
- 临时说明
- 无意义变动字段

出现在 prompt 最前面。

### 6.2 Canonical formatting

需要保证：

- term 排序稳定
- entity 排序稳定
- section 标题固定
- 空白符和分隔符稳定
- JSON schema 输出契约稳定

### 6.3 Chapter-aware dispatch

为了提升 cache hit，packet 调度应尽量按这几个维度聚类：

- same model
- same prompt_version
- same chapter
- same glossary signature

也就是：

> **尽量让“长得最像”的 packet 连续调用。**

### 6.4 Cache 不是质量机制，但会反过来影响质量

原因是：

- 稳定的 prompt 前缀通常也意味着稳定的术语约束
- 缓存友好的请求往往也是更一致的请求

因此：

缓存优化和术语一致性，不应拆成两条互不相关的工作流。

## 7. Concrete Changes Landed First

当前建议优先落地的改动是：

- 在 [translator.py](/Users/smy/project/book-agent/src/book_agent/workers/translator.py) 重排 prompt
- 在 [translation.py](/Users/smy/project/book-agent/src/book_agent/infra/repositories/translation.py) 注入 `Previous Accepted Translations`
- 在 [builders.py](/Users/smy/project/book-agent/src/book_agent/domain/context/builders.py) 扩大 term match 的上下文窗口

## 8. Next Recommended Tasks

- 为 `TranslationUsage` 增加显式的 `prompt_cache_hit_tokens / prompt_cache_miss_tokens`
- 为 run/export dashboard 增加 cache hit ratio 视图
- 增加 `term drift` QA
- 增加 `chapter-local concept memory`
- 增加 `concept registry` 和自动术语升级策略

## 9. One-Sentence Summary

真正解决“上下文工程被翻成情境工程”的办法，不是单纯加大上下文，而是把**已接受译法、局部翻译记忆、稳定术语前缀和缓存友好的 prompt 结构**合并成同一套上下文工程方案。

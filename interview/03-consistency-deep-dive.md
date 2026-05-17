# 跨章节 / 跨 Packet 一致性的费曼式深入讲解

> 面向面试场景。回答问题："如何保证跨章节、跨 packet 的术语一致性和翻译质量？"
> 用费曼学习法分四层递进：6 岁小孩 → 一年级程序员 → 工程师 → 系统设计师。

---

## 第 1 层：用 6 岁小孩听得懂的话讲明白

想象你和 5 个朋友一起翻译一本英文小说。每个人分到不同的章节。

**问题 1**：第 1 章里出现 "tokenization"，你翻译成「分词」；第 3 章你朋友翻译成「令牌化」；第 7 章另一个朋友翻译成「记号化」。读者一脸懵——这三个词到底是不是一个东西？

**问题 2**：第 5 章末尾说 "as we will see in the next section, BPE..."。但翻译第 5 章的人不知道第 5 章的下一节具体在讲什么，所以他翻译成了 "我们将看到 BPE..."——但中文读者会觉得这句话很突兀，因为他不知道这是承上启下的过渡句。

**怎么解决？** 翻译之前，5 个人坐下来开个会，写下一份**共享笔记本**：
- 「术语表」：tokenization = 分词；BPE = 字节对编码；embedding = 嵌入
- 「人物表」：作者 Stella Biderman 不翻译，保留原名
- 「风格手册」：保留 LLM、GPT、ChatGPT 等英文缩写；句子要符合中文阅读习惯

每个人翻译时**手边都摆着这本笔记**。这样三个章节翻译出的「分词」就是同一个词。

这就是一致性的核心思想：**在所有翻译动作之前，先建立共享上下文；每个独立翻译动作必须强制读取这份共享上下文**。

---

## 第 2 层：把概念拆给一个一年级程序员

LLM 是无状态的——你每次调用它，它都不知道上一次调用发生过什么。所以 "跨 packet 的一致性" 本质上是个**信息传递问题**：

> **如果 packet B 必须和 packet A 一致，那么 packet A 翻译时使用的某些信息，必须在 packet B 翻译时也被注入到它的 prompt 里。**

这听起来简单，但你立刻撞上三道墙：

| 墙 | 问题 |
|---|---|
| Token 预算墙 | LLM 上下文窗口有限。不能把 "整本书的上下文" 塞进每个 packet。 |
| 时序墙 | packet 是并行执行的。Packet 100 可能比 packet 5 先完成。等不到 packet 5 完成再开始 packet 100。 |
| 漂移墙 | LLM 即使看到「分词」，下一次也可能 "为了表达自然" 把它翻成「切分」。需要强制约束。 |

我的系统用**四层记忆 + 两层强制**解决：

### 四层记忆（按作用域大小排序，越上面作用域越大）

```
┌─────────────────────────────────────────────────────────────┐
│ ① BookProfile           作用域：整本书      生命周期：bootstrap 时建一次 │
│   – 风格策略：tone / sentence_preference / preserve_structure         │
│   – 特殊内容策略：code=protect, table=protect, footnote=translate     │
├─────────────────────────────────────────────────────────────┤
│ ② Termbase (GLOBAL)     作用域：整本书      生命周期：bootstrap + 累积  │
│   – { source_term, target_term, lock_level }                          │
│   – 例：{ "tokenization" → "分词", lock_level=hard }                  │
├─────────────────────────────────────────────────────────────┤
│ ③ EntityRegistry (GLOBAL) 作用域：整本书   生命周期：bootstrap + 累积  │
│   – { name, aliases, target_name }                                    │
│   – 例：{ "Stella Biderman", aliases=[…], target_name="Stella Biderman" } │
├─────────────────────────────────────────────────────────────┤
│ ④ ChapterBrief + ChapterTranslationMemory 作用域：单章节               │
│   – heading_path（这一章在书里的位置）                                 │
│   – chapter brief 摘要（这一章在讲什么）                              │
│   – recent_accepted_translations（本章最近 N 个已通过审查的译文片段） │
└─────────────────────────────────────────────────────────────┘
```

### 两层强制

**第一层：Pre-prompt 注入**（在 LLM 看到 prompt 之前）

每个 packet 构建时，`ContextPacketBuilder._build_packet` 做：

```python
# 简化伪代码
context_text = prev_blocks + current_block + next_blocks
relevant_terms = match(termbase, context_text)
        # ← 只匹配当前 packet 上下文里实际出现的术语，避免 token 浪费
relevant_entities = match(entity_registry, context_text)

packet.context = {
    "current_blocks":    [当前块],
    "prev_blocks":       [前 2 块],            # 给 LLM 看上文
    "next_blocks":       [后 2 块],            # 给 LLM 看下文
    "relevant_terms":    relevant_terms,      # 强制术语
    "relevant_entities": relevant_entities,
    "chapter_brief":     chapter.brief.summary,
    "heading_path":      ["Ch 2", "2.2", "2.2.3"],
    "style_constraints": book_profile.style_policy_json,
}
```

注意三个关键设计：
1. **prev/next 块就在 prompt 里**——这就是为什么 packet 100 不需要等 packet 5 完成：packet 100 直接读 packet 5 的**英文原文**（数据库里现成的），而不是 packet 5 的**翻译结果**。一致性靠 termbase，不靠跨 packet 的输出依赖。
2. **stable_id**：`packet_id = stable_id(document.id, chapter.id, block.id, chapter_brief.version, termbase.version, entity.version)`。如果术语表升级，旧 packet ID 失效，必须重译。这给了你**显式的版本传播**——你永远知道一个 packet 是基于哪一版术语表翻的。
3. **relevant_terms 是按 packet 上下文裁剪过的**——不是把整本书 200 个术语全塞进每个 prompt（那会爆 token），而是只塞当前段实际出现的 5-10 个。

**第二层：Post-validation**（在 LLM 输出之后）

`glossary_enforcement.py` 做：
```python
for term in locked_glossary:
    if term.source in source_text and term.target not in target_text:
        # 违反！LLM 看到了 "tokenization" 但没产出 "分词"
        raise GlossaryViolation(term)
```

违反就触发 healer：用 repair prompt 重新发起翻译，prompt 里**显式注入**："必须将 tokenization 翻译为「分词」，不接受任何其他译法。"

这就是**防御深度**：第一层注入是软约束（LLM 可能忽略），第二层验证是硬约束（违反就重试）。

---

## 第 3 层：跨章节的"翻译质量"是怎么保障的？这是个更微妙的问题

术语一致性是**机械问题**——字符串匹配能解决。但 "质量一致性"（比如：每章都符合中文阅读习惯、没有翻译腔、专业度匹配）是**统计问题**——LLM 输出本身就有方差。

我用三个机制：

### 机制 1：BookProfile 作为全局约束

BookProfile 在 bootstrap 时根据书名 / 章节标题推断 `book_type`（小说 / 技术书 / 学术论文），然后映射到一个 `style_policy_json`：

```json
{
  "tone": "faithful-clear",
  "sentence_preference": "natural_cn",
  "preserve_structure": true,
  "translation_register": "technical-formal"
}
```

每个 packet 的 prompt 都注入这个对象。这保证了 ch1 和 ch9 的译者（也就是同一个 LLM 在不同调用里）拿到的 "风格指南" 是字节级相同的。

### 机制 2：ChapterBrief 提供上下文锚点

每章 bootstrap 时构建一个 brief：

```json
{
  "summary": "本章讨论 LLM 的分词器，覆盖 BPE、词表大小权衡、跨语言公平性。",
  "heading_path": ["Chapter 2: Tokenizers"],
  "open_questions": [...]
}
```

Packet 翻译时，LLM 看到："你在翻译《Chapter 2: Tokenizers》的一段，本章在讲分词器和 BPE。"——这一句话让 LLM 在面对歧义词时（例如 "vocabulary" 在分词上下文里是「词表」，在教育上下文里是「词汇」）做出正确选择。

### 机制 3：双向上下文窗口（prev/next blocks）

这是最便宜也最有效的机制。我前面提过，每个 packet 在 prompt 里同时携带前 2 个英文块和后 2 个英文块的**原文**（不是译文）。

为什么是原文不是译文？两个原因：
1. **避免时序锁**：不需要等前序 packet 完成
2. **避免错误传播**：如果 prev packet 翻译错了，后续 packet 不会被它带歪

**作用**：LLM 看到的不是孤立的一句话，而是 "这句话在英文里前后写的是什么"。这解决了代词消歧（"it" 指的是上一段提到的 BPE 还是 vocabulary？）和过渡句翻译（"as discussed earlier" 应该翻译成对前文的呼应而不是凭空臆造）。

---

## 第 4 层：把这一切串起来的 "血液循环"——ChapterMemoryProposal

前三层都是**预先建立**的记忆。但一本书里有 99% 的术语在 bootstrap 时无法预先穷举。那么术语表是怎么 "长大" 的？

每个 packet 翻译完成后，`execute_packet` 第 8 步会做 `ChapterMemoryProposal`：分析译文，发现 "啊，'embedding' 这个词在原文出现了，译文统一为'嵌入'"，于是**提出一个 proposal**——建议把 `embedding → 嵌入` 加入 termbase。

Proposal 不会立即生效。它进入一个 review 队列（`chapter_memory_proposals` 表），可以是人工审核或自动合规（基于出现频次 + 一致性投票）。一旦合入，termbase 版本号 +1，所有未翻译的 packet ID 自动失效（因为 stable_id 包含 termbase.version），下一轮重新构建的 packet 就会用上新术语。

这就是**记忆的增长闭环**：

```
bootstrap → 空 termbase
  ↓
packet 翻译 → proposal: "embedding → 嵌入"
  ↓
proposal 合入 → termbase v2
  ↓
未翻译 packet 失效 → 重新构建（带上 v2 术语）
  ↓
翻译 → 一致使用「嵌入」
```

---

## 一句话总结（用于面试）

> 一致性不是靠 LLM 的 "记忆力"——LLM 是无状态的。一致性是靠**把记忆显式建模成数据库表，按作用域分层（全局 termbase + 章节 brief + packet 级 prev/next 上下文），在每个 packet 构建时按需注入到 prompt，再用 post-validator 做硬约束兜底**。版本号通过 stable_id 传播到 packet 唯一性上，让 "记忆升级 → 旧 packet 失效 → 重译" 成为一条自然的工作流。这是把 RAG 思想用到翻译领域：知识不在模型权重里，在系统状态里。

---

## 面试时如果被追问，可能的杀手级问题 + 准备答案

| 追问 | 答案要点 |
|---|---|
| "为什么 prev/next 是英文原文而不是中文译文？" | 时序解耦 + 错误隔离。译文的错误会传播；原文是 immutable ground truth。 |
| "termbase 的初始术语怎么来的？冷启动问题？" | 三层：(1) bootstrap 时从书名 / 章节标题做 NER；(2) 人工 seed；(3) 翻译过程中通过 proposal 增长。目前 (1)(2) 弱，(3) 强——这是公开的弱点。 |
| "如果两个 packet 的 proposal 冲突怎么办？" | 投票 + lock_level 制度：soft（建议） / medium（默认尊重） / hard（强制）。冲突进 proposal queue 等人工裁决。 |
| "你怎么知道一致性真的提升了？有 metric 吗？" | 反向 metric：post-validate 的 glossary_violation 触发率。这是个直接观察指标。**注意诚实**：如果还没系统性测，就说 "目前只有 spot check，自动化 metric 是 next step"。 |
| "为什么不直接用一个超大上下文窗口（如 Gemini 1M）一次性翻译整本书？" | 三个原因：(1) cost 不经济，每章重复支付 K tokens；(2) 失败爆炸半径——任何一处错都得重译整书；(3) 长上下文的注意力衰减真实存在，中段质量会下降。Packet + memory 是 RAG 风格，是经济上和工程上更稳的方案。 |
| "为什么 termbase 设计了 lock_level？" | soft 是初稿建议，medium 是默认，hard 是强制。区分的意义：早期术语证据弱时用 soft（不强制 LLM），证据多了升级为 hard（违反就 reject 重译）。避免一开始就锁错了反复重译。 |
| "ChapterTranslationMemory 里的 recent_accepted_translations 起什么作用？" | 它是章节内的 sliding window 一致性——同一章里前面已经把 "BPE" 翻成 "字节对编码"，本 packet 看到后保持一致。比 termbase 更细粒度，捕捉的是章节内未升级到全局术语表的局部一致性。 |

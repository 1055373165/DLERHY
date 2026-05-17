# 项目陈述 Review（20 年面试官视角）

> 目的：把 book-agent 项目用最高 signal-to-noise 的方式讲给一个资深面试官听。

## 一、原版陈述的真实印象

**好的地方**：错误分类与决策是真正的 agent 思想，方向对。模块化叙述完整。

**致命问题**（按伤害大小排序）：

1. **没有任何量化锚点**。"几百页"、"十几分钟"——senior 面试官听到这种描述会立刻打分。一个工程师对自己项目没有数字概念 = 不可信。
2. **开头是个人需求叙事，不是工程问题陈述**。前 30 秒决定面试官的兴趣值，浪费在 "我看英文书慢" 上是亏本买卖。
3. **K8s 编排模式 + TCP/IP 类比，是双刃剑**。讲对了加分，讲错了会被追问到崩盘——"那 Etcd 对应什么？watch 机制怎么实现的？" 类比要么不用，要么彻底用对。
4. **"自愈器"、"智能决策" 是 buzzword**。面试官心里翻译成 "分类器 + 重试策略 + 熔断器"——你不如直接说后者，显得专业。
5. **隐藏了你真正最难的工作**：PDF 结构恢复（跨页段落合并、嵌入式标题识别、figure-internal 标签聚类）只字未提。这才是面试官想听的 "hard problem"。
6. **"记忆器" 出场即消失**。模块列出来却不解释，比不提还糟。
7. **PDF 管线 + EPUB 管线 "两条管线" 是 anti-pattern 描述**。实际上你只有 parser 是格式特定的，下游 IR 统一——这恰恰是好设计，但你说反了。
8. **没有 trade-off**。任何资深设计都有取舍，pitch 里全是优点 = 不真实。

---

## 二、重写版本（建议 2 分钟内讲完）

> 我做了一个把英文技术书翻译成高保真中文版的 agent 系统，主要难点不在调用 LLM，而在三件事：**PDF 结构恢复、跨章节一致性、以及失败决策**。
>
> 用一本 250 页的 LLM 技术书做基准，pipeline 自动完成约 760 个翻译 packet，端到端 wall-clock 在 15 分钟内，全程零人工介入，最终产出一个带双语对照折叠的 HTML。
>
> **核心抽象**是 `Document → Chapter → Block → Sentence → Packet` 五层 IR。PDF 和 EPUB 共享下游，只在 parser 层分叉。Packet 是最小翻译单元 + 最小重试单元——选这个粒度是因为它同时优化了 LLM 上下文命中率和故障爆炸半径。
>
> **结构恢复**是花时间最多的部分。比如一个跨页段落，原始解析会产出两个 block，中间夹着 running header；我加了一个 `_merge_cross_page_prose_continuations` pass，识别 "前一块以逗号结尾 + 后一块小写起始" 的续接模式，跨越 header 把两块合并，让翻译器拿到完整句子。还有跨页标题的拆分、figure-internal 标签的聚类等十几个 recovery pass。
>
> **一致性**靠一个分层 memory：book profile（书级别风格） + termbase（术语锁定） + chapter brief（章节摘要） + entity snapshot（实体引用）。每个 packet 翻译前会拉一次 snapshot 注入 prompt，保证 chapter 17 的 "embedding" 和 chapter 2 的翻译是同一个中文术语。
>
> **agent 的决策核心**是错误分类器 + 分层策略：
> - **TransientNetwork / RateLimit**：指数退避 + 重新入队
> - **QualityViolation**（译文不达标、术语不一致、JSON schema 不匹配）：自动修复 prompt + 重新提交
> - **BudgetExhausted / ProviderDown**：熔断，暂停全局派发，escalate 到前端
> - **ParserBug**：硬停，写诊断日志
>
> 这是和普通 workflow 的真正区别——不是 "加了 LLM 就叫 agent"，而是**有界自治：能自愈的内部消化，不能自愈的明确升级**。
>
> 还有改进空间：错误分类目前用字符串匹配，应该换成 worker 返回的结构化 error code；termbase 现在是手工 curated，应该加 LLM 抽取 + 人工校审的 loop。

---

## 三、面试官一定会问的问题（提前准备答案）

| 问题 | 你需要准备的答案要点 |
|---|---|
| "为什么 packet 是你的粒度，不是 chapter 或 sentence？" | chapter 太大 → 单点失败成本高、上下文超 LLM window；sentence 太小 → 上下文丢失、API 调用量爆炸。Packet 是 1-3 个相邻 block，平均 ~500 tokens，刚好在 LLM 单次调用的甜区。 |
| "你的 agent 到底什么时候算 '自治'，什么时候必须升级？" | 准备一张决策矩阵图。横轴：是否幂等；纵轴：是否成本可控。两轴都 yes → 自愈；任意一轴 no → 升级。 |
| "怎么测翻译质量？vibes 不算。" | 准备三层：结构验证（图表数量、标题层级、术语命中率），格式验证（JSON schema、字符长度比），人工抽样（每章 sample N 句对比）。 |
| "如果一个 packet 重试 3 次都失败呢？" | 状态机里有 `MAX_RETRY`，进入 `dead_letter` 状态，前端展示并允许用户手动重试或跳过。**重点**：不能无限重试，会拖死整条 pipeline。 |
| "K8s 编排器到底借鉴了什么？" | 借鉴的是**声明式 reconciliation loop**：编排器看 desired state vs actual state，差异驱动 action。**不要**说 "scheduler"、"controller manager" 这种你 hold 不住的细节。 |
| "并发多少？怎么控制？" | 准备并发数 + 限速理由：DeepSeek 每秒 QPS、token 配额、worker pool size（4 worker × 2 chapter = 8 并发）。 |
| "这玩意儿现在能服务多少用户？瓶颈在哪？" | 单用户单本书。瓶颈是 LLM provider rate limit，不是你的代码。Scale 路径：multi-tenant queue + 用户级配额隔离。 |
| "如果让你重做一遍，最大的改动？" | 真诚一点。比如："最早 parser 直接产出 DB schema，没有中间 IR，导致结构调整成本高。后来加了 `_RecoveredBlock` 中间层，但 migration 很痛。早期就应该插这一层。" |
| "你看的市面上工具是哪些？为什么没用？" | 准备 2-3 个名字（沉浸式翻译、Calibre + GPT、DeepL Pro）+ 它们各自的具体缺陷。**不要泛化批评**，说具体 case："沉浸式翻译在 X 场景下 Y 表现"。 |

---

## 四、演讲层面的建议（默会知识）

1. **第一句话决定一切**。建议开场："我做了一个能把 250 页技术书自动翻译成中文的 agent，端到端 15 分钟，零人工介入。"——立刻给数字，立刻定边界。
2. **不要列模块名**（划分器/解析器/审查器…）。面试官记不住中文模块名。直接讲数据流向：`PDF → parse → block IR → packet → translate → verify → export`。
3. **准备一张架构图**白板能 60 秒内画完。三个框 + 五条线就够。
4. **永远说 "trade-off"**。每讲一个设计点，主动说出代价。"我们选了 packet 粒度，代价是 cross-packet 的上下文依赖需要靠 memory 注入补回来"——这一句让你 +10 分。
5. **别用 "自愈"、"智能"、"理想情况"**。换成 "retry policy"、"classified error handling"、"在 P95 场景下"。
6. **准备一个 "where it breaks" 故事**。讲一次真实的 debug 经历，比如跨页标题被丢的根因定位——展现你能下钻到代码层。这是 senior 和 mid 的分水岭。
7. **结尾不要讲 "用户只需要上传…"**——这是产品话术。结尾讲一个开放问题，邀请面试官 push back："目前 termbase 的冷启动还是手工，我在调研用 named entity recognition 自动 bootstrap，欢迎讨论。"——主动把球抛回去，面试官会喜欢。

---

## 五、一句话总结

原 pitch 的问题不是内容不够，而是**把最弱的部分（产品叙事）放在前面，把最强的部分（PDF 结构恢复 + 错误分类决策）藏在后面**。把顺序倒过来，加上量化锚点，去掉 buzzword，就是一个 senior 级别的项目陈述。

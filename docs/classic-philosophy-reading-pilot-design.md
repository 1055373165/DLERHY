# Classic Philosophy Reading Pilot Design

Last Updated: 2026-03-22
Status: draft-v0
Review Mode: scope_reduction
Owner: repo-aligned product draft

Related docs:

- [README.md](/Users/smy/project/book-agent/README.md)
- [TODOS.md](/Users/smy/project/book-agent/TODOS.md)
- [DECISIONS.md](/Users/smy/project/book-agent/DECISIONS.md)
- [docs/translation-agent-system-design.md](translation-agent-system-design.md)

## 1. Executive Verdict

`book-agent` 在哲学方向的第一阶段，不应该做“哲学阅读系统”，而应该做一个单书、公共域、经典文本的深读 pilot。

首个 pilot 建议锁定为：

- 内容类型：格言/章句凝练型中国古典哲学
- 首本书：`《道德经》`
- 核心目标：不是把书讲短，而是把一章文本变成一轮可完成的判断形成过程

一句话定义：

> 这是一个帮助用户更接近原文、看见不同读法、留下自己当前判断，并在次日回返修正的经典阅读系统。

## 2. Why Scope Reduction

当前如果直接做“哲学阅读体系”，会同时踩进 4 个坑：

1. 文本类型过多：`《道德经》`、柏拉图对话、康德系统论证、哲学史导论不是同一种阅读任务。
2. 信任边界过松：没有先建立“原文 / 注释 / AI 推断”的层级，AI 会天然制造顺滑解释。
3. 价值验证过慢：范围太大时，团队无法判断用户到底是喜欢“解释消费”，还是喜欢“更接近文本”。
4. 代码迁移幻觉：当前仓库擅长的是翻译控制面，不是哲学内容真相层。

所以 Phase 1 必须只证明一件事：

> 用户是否愿意接受一种不替他解释完、但能让他更接近原文的阅读流程。

## 3. Scope Freeze

### 3.1 In Scope

- 单书 pilot：`《道德经》`
- 单元粒度：一章
- 单次会话：20 到 30 分钟
- 核心流程：
  - 原文进入
  - 关键词与张力显影
  - 2 到 3 种解释路径并置
  - 用户形成当前判断
  - 次日回返修正
- 内容来源：
  - 一份人工确认的 base text
  - 一组人工策展的解释路径包
  - AI 只做编排、聚焦、追问和状态管理

### 3.2 Explicit Non-Goals

- 不做全哲学书库
- 不做柏拉图/康德/尼采等第二类文本
- 不做“AI 直接告诉你这章是什么意思”
- 不做社区、排行榜、金句流、每日一句
- 不做长篇 AI 对谈
- 不做跨书概念图谱
- 不做泛用户娱乐化阅读
- 不做“哲学课”或“考试训练器”

### 3.3 Product Boundary

这个 pilot 不是当前翻译工作台上的一个小按钮，而是一条新阅读体验线。

与现有翻译主线关系如下：

- 可复用：账户、页面壳、持久化、历史记录、导出、基本运行与审计框架
- 不可直接复用：packet 翻译、translation memory、review rerun、导出质量门

结论：

> Phase 1 应被视为 `book-agent` 的第二产品线原型，而不是翻译功能的自然延伸。

## 4. Problem Definition

用户在经典哲学阅读里的主要失败，不是“看不懂所有字”，而是以下 5 件事：

1. 不知道这一章到底在问什么。
2. 读完以后只有模糊感，没有形成一个可复述的判断。
3. 过早依赖平台解释，越来越不回到原文。
4. 记住漂亮句子，但没有文本依据。
5. 次日再看时，只剩“我昨天好像懂了”的错觉。

所以产品不该优化“读得更快”，而该优化：

- 进入原文的门槛
- 一次会话的完成感
- 判断形成
- 文本依据感
- 次日回返时的修正能力

## 5. User and Job To Be Done

### 5.1 Target User

- 对中国经典哲学有真实兴趣，但常被“难、玄、解释太多”劝退的中文读者
- 愿意每次投入 20 到 30 分钟，而不是只想看一句总结的人
- 对“形成自己的理解”有意愿，但不想一开始就去啃大量注疏的人

### 5.2 Non-Target User

- 只想获得现成结论的人
- 把经典当心灵鸡汤消费的人
- 不愿做任何主动加工的人
- 期待课程式“标准答案”的应试用户

### 5.3 Job To Be Done

当我读一章《道德经》时，
我希望不是被平台替我解释完，
而是被带着进入原文、看见关键张力、比较几种可成立的读法，
最后留下一个有文本依据的当前判断，
并在之后能回来看自己是否改变了理解。

## 6. Content Truth Layer

这是整个 pilot 最关键的新层。

如果没有它，AI 会自然滑向“解释幻觉产品”。

### 6.1 Truth Hierarchy

系统中的所有内容必须被标成以下 3 层之一：

1. `SOURCE_TEXT`
   - 原文直接可见内容
   - 例：章句、关键词、句间结构
2. `CURATED_INTERPRETATION`
   - 人工策展的解释路径
   - 每条解释必须有文本依据和边界说明
3. `AI_FACILITATION`
   - AI 对当前会话的组织、提问、回显、总结
   - 不得冒充原文释义或学界结论

### 6.2 MVP Rule

MVP 只允许：

- 一份 base text
- 每章 2 到 3 条 curated interpretation path
- AI 仅在 path 之上做轻度编排

MVP 不允许：

- AI 自主生成新解释并直接展示给用户
- 未标注层级的“顺滑解释”
- 混合不同版本、不同注家而不标来源

### 6.3 Interpretation Path Schema

每条解释路径至少包含：

- `path_id`
- `chapter_id`
- `path_label`
- `core_claim`
- `textual_evidence`
- `what_it_highlights`
- `what_it_does_not_explain`
- `common_misread`
- `real_life_projection_example`

解释路径不是“标准答案”，而是“可被文本支持的一种进入方式”。

## 7. Core Reading Unit

### 7.1 Minimal Unit

对《道德经》来说，合理的最小阅读单元就是`一章`。

原因：

- 一章通常已经构成一个完整张力
- 再切碎会破坏句间回环
- 一次一章足够形成完成感

### 7.2 Unit Objective

每个单元不追求“彻底读懂”，只追求完成 4 件事：

1. 读到原文
2. 看见关键张力
3. 比较不同读法
4. 留下自己的当前判断

### 7.3 Unit Output

每次单元完成后，必须生成一张 `understanding_state_card`：

- `my_current_read`
- `my_textual_evidence`
- `my_unresolved_question`
- `next_reentry_point`

没有这张卡，不算完成一次有效单元。

## 8. Session Design

### 8.1 20-30 Minute Session

```text
进入目标设定 (2 min)
  -> 原文近读 (6 min)
  -> 张力显影 (4 min)
  -> 解释路径并置 (5 min)
  -> 当前判断形成 (5 min)
  -> 状态卡保存 (3 min)
```

### 8.2 Step Details

#### A. 进入目标设定

用户只选一个目标：

- 我想看这一章在说什么问题
- 我想比较两种读法差在哪里
- 我想判断哪种读法更能说服我

这里不允许多目标，避免一开始失焦。

#### B. 原文近读

页面先给：

- 原文
- 极简字面辅助
- 关键词高亮

页面不给：

- 大段“通俗解释”
- 长篇背景导读
- 结论型摘要

#### C. 张力显影

系统指出本章最值得抓的张力，例如：

- `有` 与 `无`
- `强` 与 `弱`
- `为` 与 `无为`
- 表面矛盾与深层结构

这里的目标不是讲解，而是聚焦注意力。

#### D. 解释路径并置

系统呈现 2 到 3 条 curated path，格式固定：

- 这条读法在强调什么
- 它依据哪几句
- 它无法解释什么
- 它最容易被误读成什么

#### E. 当前判断形成

用户必须完成最小主动加工：

```text
我现在更倾向：
因为原文里：
我还没想通：
```

#### F. 状态卡保存

系统保存：

- 当前倾向读法
- 用户自己的表达
- 证据锚点
- 明日回返入口

## 9. Next-Day Return Loop

次日回返不是做记忆测验，而是做一次“理解修正”。

流程如下：

```text
昨日状态卡
  -> 先隐藏平台解释
  -> 用户复原昨日判断
  -> 再展示相邻读法或反例
  -> 用户选择坚持/修正
```

目标不是答对，而是让理解变具体。

### 9.1 Return Loop Output

次日回返后，状态卡新增：

- `did_i_change_my_mind`
- `what_changed`
- `what_evidence_shifted_me`

## 10. AI Role Boundary

### 10.1 AI Must Do

- 根据单元目标组织会话顺序
- 提醒用户关注当前张力
- 从 curated path 中挑选合适对比项
- 回显用户当前判断
- 保存并调用状态卡
- 生成次日回返问题

### 10.2 AI Must Not Do

- 直接生成“这一章真正意思是……”
- 把一种读法说成唯一正确答案
- 混淆原文、注释和 AI 推断
- 在没有文本依据时给出流畅解释
- 替用户写状态卡

### 10.3 Hard Safety Rule

凡是解释性输出，必须满足至少一条：

- 指向 curated path
- 指向具体原文句子

否则该输出不能展示。

## 11. Pilot Experience Flow

```text
首页
  -> 选择《道德经》
  -> 进入章节列表
  -> 选择一章
  -> 开始阅读会话
  -> 完成状态卡
  -> 次日收到回返入口
  -> 修正理解
  -> 查看自己的理解轨迹
```

### 11.1 Core Product Surfaces

- 书籍首页
- 章节阅读页
- 路径对比面板
- 状态卡面板
- 次日回返页
- 我的理解轨迹页

## 12. Repo-Aligned Implementation Slice

### 12.1 Reuse

- Web 页面框架
- 基本持久化模式
- 历史记录能力
- 运行日志与审计习惯

### 12.2 New Core Modules

- `content_truth_service`
- `interpretation_path_repository`
- `reading_session_service`
- `understanding_state_service`
- `reentry_scheduler`

### 12.3 Suggested Topology

```text
Curated Base Text
  -> Interpretation Path Store
  -> Reading Session Service
  -> Understanding State Store
  -> Reentry Scheduler
  -> User-facing Reading UI
```

### 12.4 Minimal Data Model

需要至少有以下对象：

- `TextUnit`
- `InterpretationPath`
- `ReadingSession`
- `UnderstandingStateCard`
- `ReentryPrompt`

这套模型与现有翻译 packet / review issue 不共享语义，不建议强行复用。

## 13. Error and Rescue Map

| Codepath | Failure | Rescue | User Sees |
|---|---|---|---|
| 读取章节 | 未找到 base text | 阻断该章上线 | “该章节暂不可用” |
| 读取解释路径 | 没有 curated path | 降级为原文模式 | “当前只提供原文近读，不提供解释路径” |
| 展示解释 | 解释无文本依据 | 拦截该解释 | “该解释暂未通过依据校验” |
| 保存状态卡 | 用户未填写文本依据 | 不记为有效完成 | “请补一处原文依据” |
| 次日回返 | 用户直接想看昨日解释 | 先显示昨日自述和原文 | “请先恢复你昨天的判断” |
| AI 回显 | AI复写平台标准话术 | 强制附带用户原句 | 优先显示用户原句而非 AI 改写 |

### 13.1 Shadow Paths

每条主流程都必须覆盖 4 条路径：

```text
INPUT -> READ -> COMPARE -> WRITE -> RETURN
  happy: 有原文/有路径/有状态卡
  nil: 没路径
  empty: 用户没写判断
  error: AI 输出越权解释
```

MVP 不允许 silent failure。

## 14. Success Metrics

### 14.1 North Star

`evidence_backed_completed_units / WAU`

即：

> 每周活跃用户中，完成了至少一个“带文本依据的理解状态卡”的有效单元数量

### 14.2 Leading Indicators

- `return_to_text_rate`
- `path_comparison_completion_rate`
- `evidence_backed_state_card_rate`
- `next_day_reconstruction_rate`
- `interpretation_revision_rate`

### 14.3 Anti-Metrics

以下指标不能单独证明产品成立：

- 停留时长
- 打开次数
- 金句收藏数
- AI 对话轮数
- 主观“我感觉更懂了”

## 15. Stop/Go Criteria

连续两周若出现以下信号，应暂停或重构：

1. 大多数用户跳过原文，只看解释路径。
2. 状态卡大面积变成平台话术复写。
3. 次日回返率很低，或回返后无法复原昨日判断。
4. 用户高频诉求变成“直接告诉我答案”。
5. 团队为了留存，不断削弱必要阻力，产品开始向内容消费品滑坡。

## 16. 8-Week Validation Plan

### Week 1-2

- 锁定 base text
- 策展 10 到 15 章 interpretation path
- 定义 truth hierarchy 和内容 schema

### Week 3-4

- 完成阅读页、路径对比面板、状态卡
- 跑通单元完成流程

### Week 5-6

- 完成次日回返
- 开始小规模用户测试

### Week 7-8

- 只围绕以下问题迭代：
  - 用户是否回到原文
  - 用户是否留下文本依据
  - 用户是否在次日做出修正

MVP 阶段不扩书，不扩社区，不扩 AI 能力表面丰富度。

## 17. Open Questions

这些问题必须在进入正式实现前被显式决定：

1. base text 采用哪个版本，谁负责最终确认？
2. interpretation path 由谁策展，如何迭代？
3. 是否允许用户查看路径来源说明？
4. 次日回返通过什么机制触达？
5. 这个 pilot 是独立入口，还是挂在当前首页的新二级入口？

## 18. Final Decision

这份设计只服务于一个判断：

> `book-agent` 能不能在经典哲学阅读里，建立一个“更接近原文而不是更依赖解释”的产品闭环。

如果这个判断不能成立，就不要继续扩到“哲学阅读平台”。

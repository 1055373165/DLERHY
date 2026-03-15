# 长文档图书翻译 Agent 系统设计

相关配套文档：

- [long-task-run-control.md](/Users/smy/project/book-agent/docs/long-task-run-control.md)
- [error-taxonomy.md](/Users/smy/project/book-agent/docs/error-taxonomy.md)
- [rerun-policy.md](/Users/smy/project/book-agent/docs/rerun-policy.md)

## 1. 问题重定义

这个项目不是“做一个英文图书翻译脚本”，而是一个长文档翻译 Agent 系统设计问题。它同时包含五类核心问题：

1. 文档解析：识别 EPUB/PDF 结构，恢复章节、段落、标题、脚注、图注、表格、代码块、公式等内容。
2. 文本重建：修复跨页断句、连字符断词、页眉页脚污染、双栏错序、脚注误并入正文等问题。
3. 分段与上下文管理：既要做到逐句覆盖，又不能把每句脱离上下文孤立翻译。
4. 翻译质量控制：保证忠实、通顺、统一、完整、可回溯，并能证明没有大面积静默出错。
5. 长流程 Agent 编排：支持幂等、断点续跑、局部重试、人工介入、版本追踪和导出。

这不是一个“调 OCR + 调 LLM 翻译”的问题。真正的困难发生在切块之后：如何保持一致性，如何管理上下文，如何不漏句，如何处理版式噪声，如何追踪每一步决策。

## 2. EPUB 与 PDF 的本质差异

### 2.1 EPUB

EPUB 应优先视为结构化内容源，而不是待抽取纯文本。

- 优先保留章节、标题、段落、注释、图片锚点、目录和导航信息。
- 翻译应尽量发生在结构节点上，再回填回原结构。
- 不应先打平成纯文本再重建。

### 2.2 PDF

PDF 本质上更接近“页面呈现结果”，语义结构往往需要事后推断。

首先必须判断：

- 文本型 PDF：重点是文本抽取、阅读顺序恢复、页眉页脚剔除、跨页断句修复、脚注归位、目录识别、章节恢复。
- 扫描型 PDF：先做 OCR 与版面分析，再进行结构恢复和后续流程。

PDF 中最容易破坏翻译质量的环节包括：

- 错误断句
- 双栏错序
- 脚注误并入正文
- 连字符断词
- 数学公式和代码块污染
- 页码、页眉页脚被误翻

结论：EPUB 和 PDF 可以共享下游中间表示，但不能强行共享上游解析流程。

## 3. 系统目标与成功标准

系统交付物不应只是一个中文文件，而至少包括：

- 可解析的章节结构
- 逐句对齐的中英文本
- 保留语义完整性的中文译文
- 可追溯的处理日志
- 失败段落和低置信度标记
- 人工复审接口
- 最终导出能力
- 版本化的中间工件

### 3.1 成功标准

第一阶段不追求“无需人工即可直接出版”，而追求“高质量、可审校、可追溯的出版级初稿”。

建议质量目标按优先级排序：

1. 完整覆盖与忠实
2. 术语与专名一致
3. 中文自然度
4. 风格统一
5. 成本与速度

必须可量化的标准：

- 所有可翻译句子均有状态、有 provenance、有对齐结果。
- 漏译句、漏段、漏脚注、漏图注必须可检测。
- 锁定术语命中率应接近 100%。
- 任一章节可独立重跑，不需要整书重跑。
- 任一译句都可追溯到源句、上下文、模型版本、提示词版本和术语版本。

## 4. 推荐总体架构

推荐方案不是“一个大 Agent 包打天下”，也不是开放式 swarm，而是：

规则系统 + LLM 混合架构  
章节级管线式编排  
高价值节点引入窄职责 subagent

### 4.1 分层设计

#### 文件接入层

职责：

- 文件识别
- 语言检测
- 文件指纹生成
- 元数据抽取

原因：

- 用于幂等去重、缓存和重跑入口管理。

#### 解析层

职责：

- EPUB 解包
- PDF/OCR 文本抽取
- 版面分析

原因：

- 解析与翻译必须解耦，否则上游策略升级会迫使整书重译。

#### 结构恢复层

职责：

- 恢复章节、标题层级、脚注、图注、表格、代码块、公式和阅读顺序

原因：

- 它决定后续上下文边界和 QA 语义，是整条链路的上限。

#### 语言切分层

职责：

- 段落重建
- 跨页修复
- 句子切分
- 稳定 ID 分配

原因：

- “逐句覆盖”依赖稳定句 ID，而不是临时切块。

#### 翻译编排层

职责：

- 章节调度
- packet 构建
- 段窗口翻译
- 句级对齐落库

原因：

- 负责并发、失败隔离、限流和重试。

#### 术语与上下文层

职责：

- 全书术语库
- 角色名和专名记忆
- 章节摘要
- 风格 profile

原因：

- 术语与记忆不能只作为 prompt 附件，否则不可审计、不可锁定、不可增量更新。

#### 质量校验层

职责：

- 漏译、重复、术语一致性、专名一致性、格式污染、风格漂移、上下文回看

原因：

- QA 是系统核心，不是补充模块。

#### 人工审校协同层

职责：

- 高风险片段送审
- 差异高亮
- 对照联看
- 术语回写

原因：

- 人工应被用在高价值风险点，而不是全量二次重译。

#### 导出层

职责：

- 双语对照导出
- 中文成稿导出
- 审校包导出

#### 监控与重试层

职责：

- trace、失败重试、成本统计、版本比较、告警

## 5. 核心工作流

推荐工作流如下：

文件识别 -> 格式解析 -> 文档标准化 -> 章节抽取 -> 段落重建 -> 句子切分 -> 章节摘要生成 -> 术语抽取 -> 逐段翻译 -> 句级对齐 -> 一致性校验 -> 缺失检测 -> 风格统一 -> 人工复核建议 -> 导出

关键原则：

- 长书翻译不是一次性生成整本书。
- 每个阶段都必须产出可保存、可恢复、可比较的中间状态。
- 任何高风险节点都必须允许中断、回滚和人工介入。

## 6. “逐句翻译”的真实定义

“每一句都要翻译”不等于“每一句都孤立翻译”。

正确理解是：

- 句级完整覆盖：每个源句都必须被跟踪、被处理、被记录。
- 段级语义连贯：翻译窗口应以段为主，而不是强制逐句孤译。
- 章级术语一致：术语、角色名、风格约束应在章节范围内维持稳定。

推荐原则：

- 以段为翻译窗口
- 以句为对齐粒度
- 以章节为术语与风格约束范围

严格逐句对齐适用场景：

- 技术书
- 教材
- 法规和规范
- 需要对照审校的内容

允许句内重组的场景：

- 长句较多的非虚构
- 文学性较强的段落
- 中文表达需自然化的复杂句法

但放宽的边界是：

- 不能丢失命题
- 不能引入原文没有的信息
- 不能变成不可追溯的黑箱改写

## 7. 上下文窗口爆炸与 subagent 设计

上下文窗口爆炸是本项目的核心系统问题之一。

不能把整本书、整章历史、全量术语表和所有前文塞进一次 prompt。正确方案是分层上下文和 artifact-driven subagent。

### 7.1 三层上下文

#### 全书层：Book Profile

- 书种
- 全书风格规范
- 锁定术语
- 专名注册表
- 引文策略
- 特殊内容策略

#### 章节层：Chapter Brief

- 本章摘要
- 本章叙事视角或论述重点
- 实体状态
- 本章新增术语
- 上下章衔接
- 未决歧义

#### 局部层：Context Packet

- 当前段落
- 前后 1-2 段
- 当前标题路径
- 相关术语子集
- 相关实体子集
- 必要回看片段
- 保护 span

### 7.2 推荐 subagent 边界

推荐控制在 5 个以内：

1. Structure Agent
2. Packet Builder
3. Translation Worker
4. Consistency Reviewer
5. Risk Router

这些 subagent 不共享长对话，只共享结构化 artifact。

## 8. 模型与工具策略

基础大模型应具备：

- 长上下文能力
- 翻译稳定性
- 结构遵循能力
- 工具调用能力
- 低幻觉倾向

但不应把所有问题都交给 LLM。

更适合 deterministic pipeline 或专用工具的能力：

- EPUB 解析
- OCR
- 版面分析
- 句子切分
- 代码、公式、表格识别
- 任务编排
- 状态管理
- 导出

建议的多模型分工：

- 主翻译模型：高质量翻译
- 审校模型：一致性与语义检查
- 低成本模型：术语候选抽取、摘要、初筛 QA

## 9. 质量闭环

质量控制必须是系统中心。

### 9.1 必须具备的 QA 项目

- 漏译检测
- 重复翻译检测
- 术语一致性检查
- 专名一致性检查
- 段落缺失检查
- 格式污染检测
- 引文/公式/代码保护
- 风格漂移检测
- 上下文回看机制
- 低置信度段落打标
- 人工抽检策略

### 9.2 关键原则

- 不能只看最终中文结果，要看 trace 级证据。
- 不能只给出“通过/不通过”，必须留 issue 和 evidence。
- 不能在上游结构错误时只做下游补丁。

## 10. 术语、风格与记忆治理

记忆不是“有就行”，而是要治理。

### 10.1 记忆分类

- Global Termbase
- Entity Registry
- Chapter Memory
- Style Profile
- Issue Memory

### 10.2 治理规则

- 只有 Reviewer 或人工可以把术语升级为 locked。
- Translator 可以提出候选，但不能直接改全书锁定术语。
- 章节级记忆默认只在本章生效。
- 新术语写入后应记录影响范围。
- 低置信度 OCR 提取的专名不能直接进入主术语库。
- 每次翻译都要绑定 memory snapshot version。

## 11. 人工协作设计

人工不应是系统失败后的兜底，而应是高价值介入点。

优先送审的片段包括：

- 低置信度段
- 术语冲突段
- 高文学性表达
- 引文密集段
- 结构恢复可疑段
- OCR 噪声高页面
- 脚注/图注归位异常段

最低限度审校能力：

- 中英对照
- 句级映射
- 差异高亮
- 上下文联看
- 术语冲突提示
- 一键回写术语库
- 章节级重跑

## 12. 工程与数据设计

必须保留中间表示、版本化结果和可追溯日志。

### 12.1 关键对象

- document
- chapter
- block
- sentence
- alignment pair
- term entry
- style profile
- translation draft
- review issue
- artifact version
- job state
- trace event

### 12.2 核心原则

- 幂等性
- 断点续跑
- 章节级并发
- 段落级翻译
- 句级追踪
- 解析、翻译、QA、导出分层解耦

## 13. 风险分析

最容易失败的点按优先级排序如下：

1. 解析错误污染全链路
2. 句切分错误导致逐句对齐失真
3. 上下文窗口不足导致指代混乱
4. 术语漂移
5. 长书后半段风格失稳
6. 静默漏译
7. OCR 错误被模型合理化
8. 脚注、图注、表格污染正文
9. 章节间翻译策略不一致
10. 成本失控
11. 处理时间过长

每类风险都应具备：

- 出现原因
- 影响范围
- 预防机制
- 验证方式

## 14. 分阶段落地路线

### P0

范围：

- EPUB-only
- 英文非虚构/技术/商业书
- 产出可审校高质量初稿

必须先做对：

- 稳定结构恢复
- 稳定句 ID
- packet 构建
- 句级对齐
- 术语治理
- QA gate
- 章节重跑

### P1

扩展到文本型 PDF。

新增能力：

- 阅读顺序恢复
- 页眉页脚剔除
- 跨页断句修复
- 脚注归位
- 目录识别

### P2

扩展到扫描型 PDF 和复杂版式。

新增能力：

- OCR
- 版面分析
- 双栏处理
- 图表/公式/代码保护
- 高风险页面送审

### P3

从“可翻译”升级到“高质量可交付”。

新增能力：

- 章节级风格统一
- 差异审阅
- 持续 eval
- 主动学习样本池
- 更强的人工回写闭环

## 15. P0 核心数据模型

P0 应优先保证“句级覆盖、上下文可控、术语可治理、失败可恢复”。

关键表建议至少包括：

- documents
- chapters
- blocks
- sentences
- book_profiles
- memory_snapshots
- translation_packets
- translation_runs
- target_segments
- alignment_edges
- term_entries
- review_issues
- exports
- job_runs
- audit_events

其中最不能省的是：

- sentences
- translation_packets
- memory_snapshots
- alignment_edges
- review_issues

## 16. P0 API 边界

推荐统一走异步 job，而不是同步返回最终译文。

主要接口：

- `POST /v1/documents`
- `POST /v1/documents/{id}/ingest`
- `POST /v1/documents/{id}/parse`
- `POST /v1/documents/{id}/bootstrap-profile`
- `POST /v1/chapters/{id}/segment`
- `POST /v1/chapters/{id}/build-brief`
- `POST /v1/chapters/{id}/build-packets`
- `POST /v1/packets/{id}/translate`
- `POST /v1/chapters/{id}/qa`
- `POST /v1/issues/{id}/resolve`
- `POST /v1/chapters/{id}/retranslate`
- `POST /v1/documents/{id}/export`

## 17. 三份核心实施 RFC

### RFC-001 Translation Unit Spec

目标：

- 定义句级责任单元
- 定义状态机、对齐关系、provenance
- 建立句级覆盖账本

关键状态：

- pending
- protected
- translated
- review_required
- finalized
- blocked

关键规则：

- `translatable=true` 的句子不能静默跳过。
- 所有可翻译句子都必须有 alignment 或 blocked reason。
- 所有终稿都必须有 provenance。

### RFC-002 Context Packet Spec

目标：

- 把上下文压缩成可控结构化包
- 避免整章全文塞入单次 prompt

核心组成：

- BookProfile
- ChapterBrief
- LocalPacket

硬规则：

- 单 packet 默认不跨章节
- 局部上下文默认前后各 1-2 段
- 术语和实体只注入相关子集
- 任何扩窗都必须显式触发

### RFC-003 QA and Review Spec

目标：

- 建立问题证据模型
- 定义 issue 类型、gate、送审规则和重跑动作

主要 issue 类型：

- omission
- duplication
- term_conflict
- entity_conflict
- style_drift
- format_pollution
- alignment_anomaly
- structure_suspect
- context_insufficient

## 18. Prompt / Output Contract

P0 建议只将以下 worker 设计为结构化模型输出：

- Book Profiler
- Translation Worker
- Consistency Reviewer

### 18.1 Translation Worker

输入：

- ContextPacket
- 当前句集合
- 锁定术语子集
- protected spans
- 输出 schema

输出必须至少包含：

- target_segments
- alignment_suggestions
- low_confidence_flags
- notes

### 18.2 Consistency Reviewer

输入：

- Sentence[]
- TargetSegment[]
- AlignmentEdge[]
- BookProfile
- ChapterBrief
- Termbase snapshot

输出必须至少包含：

- quality_summary
- issues
- rerun_recommendations

## 19. P0 Backlog 建议

### Sprint 0

- EPUB ingest
- parse
- chapter/block 恢复
- 句切分与稳定 sentence ID

### Sprint 1

- book profile
- chapter brief
- termbase
- packet builder

### Sprint 2

- Translation Worker
- 对齐落库
- provenance 追踪

### Sprint 3

- Reviewer
- issue 管理
- rerun
- term 回写

### Sprint 4

- 最小审校界面或 review package
- 双语导出
- 中文导出
- dashboard

## 20. 推荐目录结构

```text
book_agent/
  app/
    api/
    workers/
    orchestrator/
  domain/
    documents/
    structure/
    segmentation/
    packets/
    translation/
    review/
    export/
    memory/
  schemas/
  infra/
    db/
    queue/
    storage/
    llm/
    observability/
  prompts/
    book_profiler/
    translator/
    reviewer/
  pipelines/
  tests/
```

## 21. 当前冻结的实现原则

以下原则建议立即冻结：

1. 不允许无句 ID 的翻译。
2. 不允许无 provenance 的终稿。
3. 不允许用整章全文驱动单次翻译。
4. 不允许术语只存在于 prompt 文本里。
5. 不允许在上游结构错误时只做下游补丁。
6. 不允许 QA 只产出总分，不留证据。
7. 不允许全书重跑成为默认修复手段。

## 22. 下一步建议

如果继续实施，优先顺序建议是：

1. EPUB -> chapters/blocks/sentences
2. BookProfile + ChapterBrief + ContextPacket
3. Translation Worker + alignment
4. Reviewer + rerun + export

后续应继续补齐：

- 数据库 schema DDL
- API 草案
- orchestrator 状态机伪代码
- [Error Taxonomy](/Users/smy/project/book-agent/docs/error-taxonomy.md)
- [Rerun Policy](/Users/smy/project/book-agent/docs/rerun-policy.md)

实施配套文档：

- [Issue to Rerun Action Matrix](/Users/smy/project/book-agent/docs/issue-rerun-matrix.md)
- [P0 Database DDL](/Users/smy/project/book-agent/docs/p0-database-ddl.sql)
- [Orchestrator State Machine](/Users/smy/project/book-agent/docs/orchestrator-state-machine.md)

# Backlog

## Priority Rules

优先级排序：

1. 句级覆盖与可追溯性
2. 结构化中间表示
3. 上下文 packet 与术语治理
4. QA / rerun / invalidation
5. 导出
6. UI 和高级优化

## P0 Must Have

### Foundation

- [x] 初始化项目目录结构
- [x] 确定工程栈与依赖管理方式
- [x] 建立基础配置系统
- [x] 建立日志与 audit 规范
- [x] 建立 migration 流程
- [x] 建立最小 FastAPI workflow 入口
- [x] 建立最小 CLI / admin commands
- [x] 建立 Docker / Compose 本地联调基线

### Data Layer

- [x] 根据 [p0-database-ddl.sql](/Users/smy/project/book-agent/docs/p0-database-ddl.sql) 校正并生成第一版 migration
- [x] 建立核心模型映射
- [ ] 建立版本字段与 invalidation 写入能力
- [x] 建立 job_runs / issue_actions 基础操作

### Orchestration

- [x] 建立 document / chapter / packet 状态机
- [x] 建立基本 job dispatcher
- [x] 建立 issue -> action -> rerun_plan 流程
- [x] 建立 artifact invalidation 机制
- [x] 建立 export gate
- [x] 建立 targeted rebuild（packet / termbase snapshot）
- [x] 建立 targeted rebuild（chapter brief）专门回归与 evidence 输出

### Parsing

- [x] 实现 EPUB ingest
- [x] 实现章节树恢复
- [x] 实现 block 标准化
- [x] 实现句切分与稳定 sentence ID
- [x] 为 parse / segment 建 fixtures

### Memory and Context

- [x] 实现 book profile 基础 schema
- [x] 实现 chapter brief schema
- [x] 实现 termbase / entity registry schema
- [x] 实现 packet builder
- [x] 实现 packet to sentence mapping

### Translation

- [x] 实现 Translation Worker 输入输出 contract
- [x] 实现可替换 translation worker abstraction / factory
- [x] 实现 provider-specific `openai_compatible` TranslationModelClient / factory 装配
- [x] 实现 target segment 落库
- [x] 实现 alignment edge 落库
- [x] 实现 provenance 记录
- [x] 实现低置信度标记

### QA and Review

- [x] 实现 coverage 检查
- [x] 实现 alignment completeness 检查
- [x] 实现保守版 `ALIGNMENT_FAILURE` 检查（仅覆盖可由 latest translation run output 恢复的缺失对齐）
- [x] 扩展 `ALIGNMENT_FAILURE` 到 orphan target segment 子场景
- [x] 实现 locked term 检查
- [x] 实现 format pollution 检查
- [x] 实现 packet / chapter-brief 级 `CONTEXT_FAILURE` 检查
- [x] 实现高置信 `DUPLICATION` 检查
- [x] 实现 review issue 落库
- [x] 实现 rerun 触发与复检
- [x] 实现 `REALIGN_ONLY` 修复闭环
- [x] 实现 chapter quality summary 持久化

### Export

- [x] 导出 review package
- [x] 在 review package 中写入 quality summary / version evidence / recent repair events
- [x] 导出 bilingual HTML
- [x] 为 bilingual HTML 导出 sidecar manifest evidence
- [x] 为 export artifacts 增加 export-time misalignment evidence
- [x] 将 export-time misalignment evidence 接入 final bilingual export gate
- [x] 将 export-time misalignment 同步成正式 review issue / issue action
- [x] 让 export gate 409 直接返回结构化 follow-up action hints
- [x] 让 export gate 支持 opt-in 自动执行建议 action / rerun 并重试导出
- [x] 为 export auto-followup 增加 attempt telemetry / safety cap 可见性
- [x] 将 export auto-followup telemetry 写入独立 audit / manifest / review package 证据
- [x] 将 export auto-followup telemetry 汇总进 export record
- [x] 为 export / admin API 增加分页与筛选
- [x] 为 export / admin API 增加 export detail 查询
- [x] 为 export dashboard / detail 增加 translation usage summary
- [x] 为 export dashboard / detail 增加 per-model / per-worker usage breakdown
- [x] 为 export dashboard 增加 daily usage timeline
- [x] 为 export dashboard 增加 usage highlights
- [x] 为 export dashboard 增加 issue hotspots
- [x] 为 export dashboard 增加 issue chapter pressure
- [x] 为 export dashboard 增加 issue chapter highlights
- [x] 为 export dashboard 增加 issue chapter breakdown
- [x] 为 export dashboard 增加 issue chapter heatmap
- [x] 为 export dashboard 增加 issue chapter queue
- [x] 为 issue chapter queue 增加 SLA / age / owner-ready worklist 字段
- [x] 为 chapter queue 增加独立 chapter worklist API（过滤 / 分页 / summary counts）
- [x] 为独立 chapter worklist API 增加 SLA / oldest / immediate highlights
- [x] 为 chapter worklist 增加单章 detail API（queue entry / issue breakdown / recent issues/actions）
- [x] 为 chapter worklist 增加持久化 owner assignment（assign / clear / assigned filters / detail assignment）
- [x] 为 chapter worklist 增加 owner workload summary（按 owner 聚合 actionable queue / SLA / blocking 压力）
- [x] 为 chapter worklist 增加 owner workload highlights（top-loaded / top-breached / top-blocking / top-immediate owner）
- [x] 为 chapter worklist detail 增加 assignment history（基于 assignment audit events）
- [x] 为 export dashboard 增加 issue activity timeline
- [x] 为 export dashboard 增加 issue activity breakdown
- [x] 为 export dashboard 增加 issue activity highlights
- [x] 为 EPUB parser 增加 malformed HTML / named entity 容错回退，并用真实 EPUB 验证 bootstrap 可运行
- [x] 将零 packet chapter 纳入统一 review 流程，允许 frontmatter / 空章节进入 review / export 路径
- [x] 修复 `0 sentence + chapter brief open_questions` 的 review 外键问题，并补本地 API / PostgreSQL 回归
- [x] 使用真实 EPUB《Build an AI Agent (From Scratch)》完成隔离 smoke（bootstrap / translate / review / review package export）
- [x] 兼容标准 `OPENAI_API_KEY / OPENAI_BASE_URL` 环境变量
- [x] 为 `openai_compatible` provider 增加 `chat/completions` 兼容模式（DeepSeek 可用）
- [x] 使用 DeepSeek 完成代表性 live smoke，并落盘真实译文样本
- [x] 为真实书籍 live rerun 增加 batch-commit runner 和增量进度报告
- [x] 使用真实密钥对《Build an AI Agent (From Scratch)》完成部分全量 DeepSeek live rerun 基线（首批 20 个 packet 已提交）
- [x] 为 `openai_compatible` provider 增加 usage / latency 解析并写入 `translation_runs`
- [x] 将真实书籍 live runner 升级为并发执行（parallel workers）并支持中断后增量恢复
- [ ] 导出中文稿基础格式

## P0 Nice to Have

- [ ] 成本与时延 dashboard（更完整的趋势 / 分布 / provider 时间序列维度）
- [ ] packet 风险评分
- [x] chapter 质量摘要
- [ ] owner alerts / assignment anomaly hints
- [ ] owner workload alerts / workload balancing hints
- [ ] 术语冲突批量视图
- [x] 真实 PostgreSQL 集成回归
- [x] Docker 化 PostgreSQL 运行态 smoke
- [x] 为 export / admin API 增加 export record 查询与 dashboard 摘要
- [ ] alignment / export 证据继续增强（如独立 export evidence bundle）
- [x] provider live smoke（带真实密钥的 `openai_compatible` 运行态验证）
- [ ] 将 `openai_compatible` provider 已生成的估算 cost 继续扩展到更完整的 dashboard / export analysis（基础 snapshot 已接通）
- [ ] 持续观测《Build an AI Agent (From Scratch)》整本书的 DeepSeek 全量 live rerun，并收敛并发吞吐 / 长任务稳定性策略（当前已切到 control-plane `v4` 长跑）
- [ ] 基于新的 sentence alias 协议继续观测《Build an AI Agent (From Scratch)》整本书的 DeepSeek 长跑稳定性，确认后续 batch 不再出现 provider 坏 ID 导致的外键失败
- [x] 新增长任务 Run Control Plane 最小数据模型：`document_run / work_item / worker_lease / run_budget / run_audit_event`
- [x] 新增长任务控制 API：`pause / resume / cancel / drain / run summary / run events`
- [x] 为 Run Control Plane 新增 `work item claim / heartbeat / release / expiry recovery` 最小执行闭环
- [x] 将真实书籍 live runner 从脚本态迁移到 `document_run + work_item` 控制面，先覆盖 translate stage
- [x] 新增长任务 budget guardrails，已覆盖 `wall-clock / total cost / token in / token out / consecutive failures`
- [ ] 为 Run Control Plane 增加 provider lane caps / circuit breaker / adaptive concurrency
- [x] 基于真实 EPUB smoke 收敛 review 误报：零正文 frontmatter 章节在仅有 `missing_chapter_title` 时自动豁免 chapter-level `CONTEXT_FAILURE`
- [ ] 基于真实 EPUB smoke 评估 chapter 状态是否应仅由 blocking issues 决定 final export 阻断
- [x] 针对 `v4` 真实书结果处理 chapter 1 frontmatter 的 `CONTEXT_FAILURE`：已自动豁免无正文且仅缺标题的 chapter brief blocker，并完成真实书复检
- [x] 针对 `v4` 真实书结果处理 chapter 5 的 `FORMAT_POLLUTION`：已区分 provider 真污染与正文字面标签场景，并完成真实书复检
- [ ] 基于真实书继续观察“源文原样标签豁免”是否还需要更细的 allowlist / 证据可视化
- [x] 冻结整本 merged export 的渲染策略：protected block、结构化工件、混合 inline spans 不再被视为“空翻译”
- [x] 按 merged export 渲染策略实现整本合并导出：已在真实书 `v4` 上验证 `merged_html` 成功导出，并支持 code/table/prompt/literal-tag 的正确渲染模式
- [x] 继续扩展 merged export 的 block-level render mode：已支持 image/caption、equation、reference-preserve 的阅读态渲染，并通过 parser/API/PostgreSQL 回归
- [x] 将 merged export 升级为技术书阅读版排版：已支持封面区、目录侧栏、章节导航、移动端样式和 print 基础样式
- [x] 为项目新增正式前端入口：已由 FastAPI 提供现代化 operator-facing 首页 `/`
- [ ] 为 merged export 增加 EPUB 内图片资源的落盘与引用，让 image anchor 从“占位锚点”升级到“可见图片”
- [ ] 基于新 parser / render modes 重新生成真实书 merged HTML，验证图片/公式/reference 在真实 EPUB 上的最终阅读效果
- [x] 将当前首页从“产品入口页”扩展为可操作的轻量 document workspace / run console / chapter worklist board
- [x] 将当前轻量控制台继续扩展为真正的 document workspace：已补 document detail drill-down、run event polling、chapter detail drawer 和 owner assignment 操作
- [x] 继续把当前 operator console 做深：已补 chapter detail 的 action execute、owner workload lane、worklist owner filters，以及 document/run/worklist 的 live refresh state
- [x] 继续把当前 operator console 往值班台推进：已补 owner workload drill-down、balancing hints，以及更细的 run/document/worklist live indicators
- [x] 继续把当前 operator console 往运营面推进：已补 owner alerts / balancing hints 的主动告警化，以及 owner board 的 workload balancing 建议
- [ ] 继续把当前 operator console 往运营面推进：补更强的 live progress 指示，以及 owner alerts 的更细粒度告警规则

## P1

- [ ] 按 [pdf-support-implementation-plan.md](/Users/smy/project/book-agent/docs/pdf-support-implementation-plan.md) 启动 P1 文本型 PDF 支持
- [ ] PDF intake / classification（text_pdf / scanned_pdf / mixed_pdf / layout_risk）
- [ ] 基于几何信息的文本抽取（页 / block / line / span / bbox）
- [ ] 阅读顺序恢复
- [ ] 页眉页脚剔除
- [ ] 段落重建与连字符断词修复
- [ ] 跨页断句修复
- [ ] 脚注归位
- [ ] 目录识别 / bookmark reconciliation / chapter recovery
- [ ] PDF-specific provenance（page_number / bbox / reading_order_index / recovery_flags）
- [ ] PDF-specific QA（reading order suspect / header-footer leak / footnote pollution / cross-page suspect）

## P2

- [ ] 扫描型 PDF 支持
- [ ] OCR 置信度传播
- [ ] 双栏处理
- [ ] 图表 / 公式 / 代码保护增强
- [ ] 高风险页自动送审

## Deferred

- [ ] 文学性强文本的高级风格控制
- [ ] 多人协作权限系统
- [ ] 跨书项目级术语共享

## Suggested Build Order

1. Foundation
2. Data Layer
3. Parsing
4. Orchestration
5. Memory and Context
6. Translation
7. QA and Review
8. Export

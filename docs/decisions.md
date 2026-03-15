# Decisions

## Purpose

记录已经冻结或暂时接受的架构决策，避免实现过程中反复回到同一问题。

每条决策建议包含：

- `Status`
- `Decision`
- `Why`
- `Consequences`

---

## D-001 P0 Scope

Status: Accepted

Decision:

- P0 只做 EPUB-only
- 只优先支持英文非虚构 / 技术 / 商业书
- 目标是可审校高质量中文初稿，不承诺零人工直接出版

Why:

- 先把结构化输入、句级覆盖、packet、alignment、QA 和 rerun 打稳
- 避免被扫描 PDF、OCR 和复杂版式过早拖垮

Consequences:

- PDF 相关能力明确后移到 P1/P2
- 第一版重点是系统正确性和可恢复性，不是输入覆盖率最大化

---

## D-002 Core Translation Principle

Status: Accepted

Decision:

- 以段为翻译窗口
- 以句为对齐和覆盖粒度
- 以章节为术语与风格约束范围

Why:

- 逐句覆盖是完整性要求，不是上下文隔离要求
- 纯逐句孤译会显著损伤中文自然度
- 纯段级黑箱翻译又无法证明不漏句

Consequences:

- 必须维护 stable sentence IDs
- 必须维护 alignment graph
- 必须保留 packet 和 provenance

---

## D-003 Architecture Style

Status: Accepted

Decision:

- 采用规则系统 + LLM 混合架构
- 采用章节级管线式编排
- 在高价值节点引入窄职责 subagent / worker

Why:

- 长书翻译的主要失败来自结构、状态和质量失控，而不是模型不够聪明
- 需要幂等、断点续跑、局部重跑和可审计 artifact

Consequences:

- 不做“一个大 Agent 自己决定所有步骤”
- 不做自由对话式多 agent swarm
- orchestrator 必须是显式状态机

---

## D-004 Source of Truth

Status: Accepted

Decision:

- 所有关键结果都必须写入结构化 artifact
- 不能依赖对话历史作为系统记忆

Why:

- 对话上下文不可稳定复用，也不适合做版本管理
- 长流程必须依靠可查询、可失效化、可重建的对象

Consequences:

- 需要 documents / chapters / blocks / sentences / packets / runs / issues / invalidations
- 必须记录版本与 provenance

---

## D-005 Context Strategy

Status: Accepted

Decision:

- 使用三层上下文：Book Profile / Chapter Brief / Local Context Packet
- 不允许把整章全文直接塞入单次翻译 prompt

Why:

- 避免上下文窗口爆炸
- 控制 token 成本
- 保持翻译行为稳定和可预测

Consequences:

- 必须实现 Packet Builder
- 必须有 chapter brief 和 term/entity 子集裁剪逻辑

---

## D-006 Quality Strategy

Status: Accepted

Decision:

- QA 是核心路径，不是后处理补丁
- 导出前必须过 coverage / alignment / term / format / blocking issue 五类 gate

Why:

- 书稿翻译的真正难点是证明没有大面积静默出错

Consequences:

- 必须实现 review issue、evidence 和 rerun plan
- 不允许“翻出来就直接导出”

---

## D-007 Rerun Policy

Status: Accepted

Decision:

- 先定性 issue，再决定 rerun action
- 默认最小可验证重跑范围
- 默认不允许全书重跑作为常规修复手段

Why:

- 长书项目一旦没有 rerun 边界，成本和复杂度会迅速失控

Consequences:

- 必须维护 invalidation graph
- 必须维护 issue -> action -> scope 映射

---

## D-008 Human Review Role

Status: Accepted

Decision:

- 人工是高价值介入节点，不是全量二次翻译者

Why:

- 全量人工复翻会压垮吞吐
- 高风险分流才符合 agent 系统价值

Consequences:

- 必须优先送审低置信度、高歧义、术语冲突、结构可疑片段
- 后续 UI / review package 必须围绕“高效裁决”设计

---

## D-009 P0 Export Strategy

Status: Accepted

Decision:

- P0 先支持 review package、bilingual HTML、基础中文稿导出

Why:

- 这三类最能支撑调试、审校和闭环验证

Consequences:

- 不急着一开始做复杂出版级回填
- 先确保 artifact 到导出的可追溯性

---

## D-010 Current Stack

Status: Accepted

Decision:

- 倾向采用 Python + FastAPI + PostgreSQL
- ORM 使用 SQLAlchemy
- migration 使用 Alembic

Why:

- 与文档处理、异步任务、LLM 工具链和快速原型阶段较匹配

Consequences:

- 需要同步维护 settings、ORM model、migration 和 app bootstrap
- 后续 worker、repository 和 service 都围绕这套栈组织

---

## D-011 P0 Dispatch Strategy

Status: Accepted

Decision:

- P0 先采用 DB-backed `job_runs` + 显式 orchestrator 的轻量 dispatcher
- 不在第一轮就引入外部重型队列系统

Why:

- 先把状态机、失效传播和 rerun 边界做清楚
- 当前项目还处在单机开发和快速迭代阶段

Consequences:

- job 调度先以内置实现为主
- 如果吞吐或隔离需求上升，再在后续阶段抽象外部 queue backend

---

## D-012 P0 EPUB Parsing Strategy

Status: Accepted

Decision:

- P0 先采用基于标准库 `zipfile + xml.etree.ElementTree` 的 EPUB 解析实现
- 先保证 spine 顺序、metadata、基础 block 提取和 anchor 保留

Why:

- EPUB 在 P0 阶段结构较稳定，适合先用可控、轻依赖的实现打通最小链路
- 当前阶段更重要的是稳定中间表示和可测试性，而不是追求解析器功能最全

Consequences:

- 后续如果遇到复杂 EPUB 变体，再按需增强或替换解析层
- 当前 parser 优先支持 headings / paragraphs / quotes / code / list items / footnotes 等基础 block

---

## D-013 Stable Identifier Strategy

Status: Accepted

Decision:

- P0 的 document / chapter / block / sentence / packet 等核心对象使用稳定 ID 策略
- 当前采用基于固定命名空间的 UUID5，根据关键业务键生成

Why:

- 句级覆盖、alignment、rerun 和 invalidation 都依赖稳定对象标识
- 随机 ID 会显著增加重跑、比较和 trace 的复杂度

Consequences:

- parser / segmenter / packet builder 必须基于业务键生成 ID
- 后续持久化层必须尊重这些 stable IDs，而不是重新分配随机主键

---

## D-014 P0 Bootstrap Pipeline Strategy

Status: Accepted

Decision:

- P0 先建立一条内存态可验证的 bootstrap pipeline
- 流程顺序固定为 ingest -> parse -> segment -> profile -> chapter brief -> packet

Why:

- 在接数据库写入之前，先验证主链路 artifact 是否完整、可预测、可测试
- 这能降低 persistence、queue 和 integration 同时推进带来的返工风险

Consequences:

- 当前阶段先以 pipeline/service 测试作为主验证方式
- 下一阶段重点是把这条 pipeline 安全落到 repository 和数据库层

---

## D-015 P0 Persistence Test Strategy

Status: Accepted

Decision:

- 运行时目标数据库仍以 PostgreSQL 为主
- 但 ORM 层优先使用 SQLAlchemy 通用 `Uuid/JSON` 类型，以便在测试中使用 SQLite 做持久化验证

Why:

- 这样可以尽早验证 repository、translation persistence 和 review flow
- 不必把每次开发验证都绑定到外部 PostgreSQL 环境

Consequences:

- migration 仍以 PostgreSQL 为目标，保留独立 Alembic SQL 基线
- 持久化测试优先用 SQLite，真实数据库联调作为下一阶段补充

---

## D-016 P0 Translation Execution Strategy

Status: Accepted

Decision:

- 在真实 LLM 接入前，先使用 deterministic placeholder worker 打通 translation run / target segment / alignment / review 流程

Why:

- 当前阶段更重要的是先验证状态流、落库、QA 和 rerun 接口
- 否则很容易被外部模型接入细节拖住主链路建设

Consequences:

- 当前 `EchoTranslationWorker` 只用于系统链路验证，不代表最终翻译策略
- 下一阶段接入真实模型时，应保留现有 contracts 和持久化路径不变

---

## D-017 P0 Export Validation Strategy

Status: Accepted

Decision:

- 在复杂出版级回填之前，P0 先以 `review package` 和 `bilingual HTML` 作为主导出形式

Why:

- 这两类导出最适合验证 alignment、issue、sentence coverage 和人工审校体验
- 能更快暴露主链路问题，而不必先陷入复杂排版回填

Consequences:

- 当前 export 层优先服务调试、审校和回归验证
- 中文成稿和更复杂出版格式留到后续阶段补充

---

## D-018 P0 External Interface Strategy

Status: Accepted

Decision:

- P0 先通过 FastAPI workflow endpoints 暴露 bootstrap / translate / review / export / action execute
- 暂不优先实现完整 UI 或复杂 CLI

Why:

- 当前阶段更需要稳定的可编排入口来驱动集成测试和本地联调
- API 比 UI 更轻，也更适合作为后续 CLI、worker 或前端的统一底座

Consequences:

- 应用层需要维护 request / response schema、session 依赖和 workflow service
- 后续新增 UI 或 CLI 时，应复用现有 workflow service，而不是绕开它直接调用底层 repository

---

## D-019 Export Preflight Strategy

Status: Accepted

Decision:

- document 级 final export 必须先对所有目标 chapter 做 gate 预检
- 只有全部 chapter 都满足 gate，才开始写导出文件

Why:

- 否则会出现“部分文件已写入磁盘，但数据库事务回滚”的半成功状态
- 这种不一致会显著增加调试和恢复复杂度

Consequences:

- export service 需要暴露独立 gate 校验能力
- document workflow 在批量导出前必须先跑 preflight，而不是边校验边写文件

---

## D-020 Translation Worker Abstraction Strategy

Status: Accepted

Decision:

- 在接真实模型前，先冻结 `TranslationWorker -> TranslationTask -> TranslationWorkerOutput` 的抽象边界
- translation run 的 `model_name / prompt_version / runtime_config` 由 worker metadata 提供，而不是由 service 硬编码

Why:

- 这样可以在不改 persistence、QA 和 API 的前提下替换翻译后端
- 也能让测试继续使用 deterministic worker，同时为真实 provider 保留接入点

Consequences:

- runtime 需要有 worker factory
- provider-specific client 应实现统一的 `TranslationModelClient` 接口，而不是直接侵入 service 层

---

## D-021 P0 Local Operations Strategy

Status: Accepted

Decision:

- P0 同时维护 FastAPI workflow 和最小 CLI / admin commands
- CLI 必须复用 workflow service，而不是另写一套旁路逻辑

Why:

- API 适合集成测试和未来 UI，CLI 适合本地批处理、联调和离线排障
- 两条入口共用同一 service 可以减少状态漂移和重复实现

Consequences:

- 入口层需要统一 request/result 序列化约定
- 后续如果加 job runner 或后台 worker，也应优先复用现有 workflow service

---

## D-022 Rerun Validation Strategy

Status: Accepted

Decision:

- action 执行本身只负责 invalidation 和 scope 决定
- 如果调用方要求 follow-up，则系统立即执行 rerun，并在同一链路上触发 re-review
- review 会自动关闭最新 QA 中已消失的旧 issue

Why:

- 只有 invalidation 没有 recheck，会让 issue 表持续堆积，无法形成真正的修复闭环
- 让 rerun 和 re-review 共用同一条 workflow，可以减少状态漂移和人工判断成本

Consequences:

- action API / CLI 需要支持显式 follow-up 选项
- review repository 需要支持“解析当前 active issues，并 resolve 已消失 issue”的能力

---

## D-023 P0 QA Expansion Strategy

Status: Accepted

Decision:

- P0 的 review 不只检查 omission 和 term conflict，还应至少覆盖 low confidence 和 format pollution
- chapter 质量摘要应直接通过 workflow result 暴露给 API / CLI

Why:

- 这两类问题在真实长文翻译里出现频率高，而且不需要等真实 LLM provider 接入后才能建规则
- 如果 summary 只存在 review service 内部，就很难被调用方消费，也不利于后续 UI 和 dashboard

Consequences:

- translation service 必须把 low-confidence sentence status 保留下来，供 review 使用
- review result schema 需要持续演进，但不能破坏既有 workflow contract

---

## D-024 Local PostgreSQL Strategy

Status: Accepted

Decision:

- 本地 PostgreSQL 联调优先通过 Docker Compose 提供，而不依赖宿主机预装 `psql` / `pg_isready`
- 容器化本地开发路径尽量向最终服务器部署路径靠拢

Why:

- 当前宿主环境缺少 PostgreSQL 客户端工具，但已经具备 Docker / Compose
- 统一本地和部署环境能降低“开发能跑、部署不一致”的风险

Consequences:

- 仓库需要维护 `Dockerfile`、`compose.yaml` 和容器化联调说明
- PostgreSQL 运行态 smoke 将转为 Docker 路径验证，而不是宿主机依赖验证

---

## D-025 Persistence Fidelity Strategy

Status: Accepted

Decision:

- ORM 中所有枚举列统一持久化 enum `.value`，不持久化成员名
- SQLite 测试环境默认开启 foreign key 校验
- repository 写入涉及父子对象时采用 staged flush，而不是依赖宽松数据库行为

Why:

- 真实 PostgreSQL smoke 已证明，SQLite 默认行为会掩盖两类关键问题：枚举值和 check constraint 偏移、父子对象写入顺序错误
- 如果本地测试环境不尽量接近生产数据库约束，主链路问题会持续在集成阶段才暴露

Consequences:

- 所有新加 enum 列都必须通过统一 helper 定义
- 新增 repository 持久化逻辑时，需要显式考虑 FK 顺序和 flush 边界
- P0 后续应补 PostgreSQL 专项集成回归，而不只依赖 SQLite 单测

---

## D-026 Targeted Rebuild Strategy

Status: Accepted

Decision:

- P0 的 targeted rebuild 先采用 in-place rebuild，而不是立即引入完整的 packet/snapshot 替身对象链
- 优先让 `REBUILD_PACKET_THEN_RERUN`、`REBUILD_CHAPTER_BRIEF`、`UPDATE_TERMBASE_THEN_RERUN_TARGETED` 变成真实重建动作
- packet rebuild 时必须同步吸收当前有效 term entries，不能继续依赖 bootstrap 时的空 termbase snapshot

Why:

- 当前系统还没有做完整版本切换、引用重写和 artifact lineage UI，直接上替身对象链会显著放大 P0 复杂度
- 真正高价值的闭环，是让“新增锁定术语 / 上下文修正”能够进入 packet，并在 rerun 中改变翻译结果

Consequences:

- P0 的 rebuild 会复用既有 packet identity，但更新 packet payload、packet type、snapshot version 引用和 packet_sentence_map
- 旧 snapshot 会被标记为 `superseded`，新 snapshot 通过版本递增生效
- 后续如果引入完整 artifact lineage，需要把 in-place rebuild 演进为版本化替换，而不是停留在当前形态

---

## D-027 PostgreSQL Integration Regression Strategy

Status: Accepted

Decision:

- PostgreSQL 专项集成回归保留为显式 gated test，不放进默认本地单测主路径
- 同时提供 Docker 一键执行脚本，作为本地和部署前的标准验证入口

Why:

- 真实 PostgreSQL 约束对本项目非常重要，但默认要求每次本地开发都启动容器会降低迭代效率
- 我们已经从真实 PostgreSQL 中抓出过多次 SQLite 掩盖的问题，所以必须把这条验证路径产品化，而不是继续依赖手工 smoke

Consequences:

- 默认 `python -m unittest` 会 skip PostgreSQL 集成测试，保持日常开发反馈快
- `scripts/run_postgres_integration.sh` 成为标准的一键验证入口
- 后续任何涉及 schema、FK、rerun、snapshot 或 export gate 的改动，都应优先补到 PostgreSQL 集成回归里

---

## D-028 Provider Adapter Boundary

Status: Accepted

Decision:

- provider-specific translation client 通过 `TranslationModelClient` 接口接入
- 当前首个 provider adapter 为 `openai_compatible`
- provider adapter 必须拆成 payload 构建、HTTP transport 和结构化响应解析三层
- 默认回归使用 fake transport，不在本地默认测试中依赖真实密钥或外网

Why:

- 需要把“真实模型接入位”做成稳定工程接口，而不是把网络调用散落进 workflow
- 需要在不依赖外部凭据的前提下验证 adapter contract
- 需要为后续新增第二种 provider 保留一致的 factory 和测试模式

Consequences:

- `build_translation_worker` 现在支持 `echo` 和 `openai_compatible`
- provider live smoke 将作为独立验证路径，而不是默认单元测试的一部分
- 后续若新增 provider，应复用同样的 transport-isolated contract 测试方式

---

## D-029 Chapter Quality Summary Persistence

Status: Accepted

Decision:

- chapter quality summary 作为独立持久化对象存储，而不是继续塞进 `chapters.metadata_json`
- review pass 完成后立即 upsert 最新 chapter quality summary
- `GET /documents/{id}` 返回 chapter 级摘要时，直接附带最新已持久化的 quality summary

Why:

- quality summary 已经是 QA、rerun 和 export gate 的核心证据，不适合只存在于一次 API 返回值里
- 独立对象比 `metadata_json` 更适合后续做回归比较、审计和版本证据扩展
- 章节摘要页需要读取“最近一次 review 结果”，不应该强依赖刚刚执行过 review 请求

Consequences:

- 新增 `chapter_quality_summaries` 表和对应 migration
- review repository 需要承担 summary upsert 和 document 级 summary 读取
- 后续若要做 review trend、export evidence bundle 或质量仪表盘，可以直接复用该对象

---

## D-030 Chapter-Brief Rebuild Routing and Evidence

Status: Accepted

Decision:

- `CONTEXT_FAILURE` 和 `STYLE_DRIFT` 统一路由到 `REBUILD_CHAPTER_BRIEF`
- `MISTRANSLATION_REFERENCE` 继续保持 packet 级 `REBUILD_PACKET_THEN_RERUN`
- action follow-up 的输出必须显式返回 rebuild evidence，包括 rebuilt snapshot types 和当前 context versions

Why:

- chapter brief 级问题本质上是章节记忆和上下文摘要失真，不应继续落回 packet 级补丁
- rebuild 成功与否不能只靠 `rebuilt_snapshot_ids` 猜，需要直接看到“重建了哪类 snapshot，当前版本是多少”
- 这样更适合后续 review package、审计日志和导出证据链复用

Consequences:

- 新增 `REBUILD_CHAPTER_BRIEF` 的本地 API / rule-engine / PostgreSQL 集成回归
- CLI 和 API 的 action 结果现在会带 `rebuilt_snapshots`、`chapter_brief_version`、`termbase_version`、`entity_snapshot_version`
- 后续如果新增更多 rebuild 动作，应继续沿用“类型 + 版本”的 evidence 输出模式

---

## D-031 Context-Failure QA Strategy

Status: Accepted

Decision:

- packet `open_questions` 不再只是上下文提示，而会在 review 中触发 packet 级 `CONTEXT_FAILURE`
- active chapter brief 的 `open_questions` 不再只是摘要元数据，而会在 review 中触发 memory 级 `CONTEXT_FAILURE`
- `CONTEXT_FAILURE` 的 rerun 动作必须按根因层分流：`packet -> REBUILD_PACKET_THEN_RERUN`，`memory -> REBUILD_CHAPTER_BRIEF`

Why:

- 现有系统已经具备 packet 和 chapter brief 两层上下文 artifact，如果 review 不消费这些信号，很多上下文问题只能靠人工发现
- 同一种 issue type 可能来自不同层，不能继续用单一动作粗暴处理
- 这能让 chapter-brief rebuild 和 packet rebuild 都由真实 QA 信号驱动，而不是只靠手工构造 issue

Consequences:

- review repository 现在需要读取 active chapter brief snapshot
- `ReviewService` 现在会自动生成 packet / memory 两类 `CONTEXT_FAILURE`
- 后续若 chapter brief / packet builder 增加更多 open question 类型，这条 QA 路径会自然放大覆盖面

---

## D-032 Conservative Duplication QA Strategy

Status: Accepted

Decision:

- P0 的 `DUPLICATION` 只检测高置信重复：相邻 target segment 文本完全重复，且其来源源句不同
- 若重复根因在 `packet`，默认动作是 `REBUILD_PACKET_THEN_RERUN`
- 若重复根因在 `export`，默认动作仍是 `REEXPORT_ONLY`

Why:

- 重复翻译问题真实存在，但宽泛规则很容易误伤排比、口号、短对话和原文本就重复的句子
- 先用保守规则抓“明显重复且最值得修”的场景，更符合 P0 的误报控制目标
- 这也顺手修正了此前 `DUPLICATION` 在 rule engine 中被统一路由到 export 的偏差

Consequences:

- `ReviewService` 现在会在 packet 内做相邻重复 target segment 检查
- rule engine 现在会按根因层区分 packet duplication 和 export duplication
- 后续若需要扩大覆盖面，应优先加入更强证据，而不是简单放宽字符串相似阈值

---

## D-033 Conservative Alignment-Failure and Realign Strategy

Status: Accepted

Decision:

- P0 的 `ALIGNMENT_FAILURE` 先只检测“latest translation run output 可恢复，但当前 active alignment edge 缺失”的 packet 级问题
- 这类问题默认动作是 `REALIGN_ONLY`，不触发 packet invalidation，也不重跑翻译
- realign 的事实来源优先使用 latest translation run 的结构化 `output_json`，而不是重新推断或重译

Why:

- 对齐问题很高价值，但误报风险也很高，尤其是在多轮 rerun 后存在 superseded target segment 的情况下
- 如果已有译文内容可信，只是当前 edge 缺失，再走 `RERUN_PACKET` 会引入不必要成本和额外漂移风险
- latest run output 已经保留了 model 产出的 target segment 和 alignment suggestion，足以支撑保守版 realign

Consequences:

- `ReviewService` 现在会优先从 active target segment / latest run output 判断“缺边但可修复”的对齐失败，而不是一律记成 `OMISSION`
- 该策略现在覆盖两类保守子场景：missing sentence alignment 和 orphan target segment
- `IssueActionExecutor` 对 `REALIGN_ONLY` 不再执行 packet invalidation
- `RerunService` 现在会走专门的 realign path，重建 alignment edge 后再执行 chapter 复检
- 后续若要继续扩大 `ALIGNMENT_FAILURE` 覆盖面，应优先增加更强证据源，例如 export-time misalignment，而不是直接放宽规则

---

## D-034 Review Package as Evidence Artifact

Status: Accepted

Decision:

- P0 的 review package 不再只导出句子和 issue 列表，还必须显式带上 quality summary、active snapshot versions、packet context versions 和 recent repair events
- recent repair events 以持久化 audit event 为准，优先记录 `snapshot.rebuilt`、`packet.rebuilt`、`packet.realigned`
- export record 的 `input_version_bundle_json` 也同步记录当前主要版本证据，避免文件外完全失去上下文

Why:

- review package 本来就是给人工审校和问题裁决用的，如果不带质量和修复证据，很多关键上下文仍然要回数据库或日志里查
- 现有系统已经具备 chapter quality summary、snapshot version 和 rebuild/realign 事件，最合理的下一步就是把它们写入导出工件
- 这比先做 UI 更轻，也更符合 P0 “先把 evidence 做对”的原则

Consequences:

- `ExportRepository` 现在需要读取 book profile、active snapshots、chapter quality summary、packets 和 recent audit events
- `TargetedRebuildService` 和 `RealignService` 现在会持久化 repair audit event，供 review package 消费
- 后续若要扩展 bilingual export manifest 或独立 evidence bundle，可以直接复用同一套字段和审计事件

---

## D-035 Bilingual Export Manifest Strategy

Status: Accepted

Decision:

- P0 的 bilingual HTML 导出采用“双工件”形式：主 HTML + sidecar manifest JSON
- manifest 必须显式包含 quality summary、version evidence、recent repair events、row summary 和 issue summary
- API / workflow 返回值必须直接暴露 manifest 路径，而不是要求调用方自行推断

Why:

- bilingual HTML 更偏消费和对照阅读，但一旦脱离数据库，仍然需要最小可审计证据
- sidecar manifest 比把大量证据硬塞进 HTML 注释或页面本身更稳定，也更适合后续自动化消费
- 这条路径能让 bilingual export 和 review package 共用同一套质量/版本口径，减少导出层分叉

Consequences:

- `ExportService` 现在需要为 bilingual export 同步生成 manifest 并把路径回传到 workflow / API
- export contract 从“单文件导出”升级成“主文件 + 可选 sidecar 工件”
- 后续若继续扩展 export evidence，应优先沿 manifest / bundle 方向，而不是把更多结构化证据塞回 HTML 正文

---

## D-036 Export-Time Misalignment Evidence Strategy

Status: Accepted

Decision:

- P0 的 export 层需要独立生成 `export_time_misalignment_evidence`
- 该 evidence 只覆盖“当前导出一定有问题”的保守场景，例如缺失目标句、仅指向 inactive target 的句子、orphan active target segment
- 指向 superseded target 的历史 edge 默认只作为信息性证据保留，不单独触发 anomaly

Why:

- review 通过并不意味着 export 前状态绝对没变；alignment 在 review 之后被破坏是一个真实风险
- 这类问题如果只靠 review 检查，无法覆盖“review 之后、export 之前”的数据漂移
- 但如果把所有历史 edge 噪音都算成 anomaly，又会把 targeted rebuild / rerun 后留下的 superseded 痕迹误报成导出异常

Consequences:

- review package 和 bilingual manifest 现在都包含 `export_time_misalignment_evidence`
- manifest 可以在 post-review corruption 场景下显式暴露缺失 target sentence，而不依赖再次 review
- 后续若要把 export-time anomaly 接入 export gate，可以直接复用这套保守 evidence，而不必重新定义口径

---

## D-037 Export-Time Evidence Participates in Final Export Gate

Status: Accepted

Decision:

- final bilingual export 必须检查 `export_time_misalignment_evidence`
- 若 evidence 命中保守 anomaly，final export 直接失败
- review package export 不受该 gate 影响，继续作为诊断工件放行

Why:

- final export 是交付态工件，不应在已知对齐异常存在时继续产出
- 但一旦直接阻断 final export，系统仍然需要一个可导出的工件承载诊断证据，否则人工无法高效定位问题
- 这符合当前 P0 的职责分层：review package 负责诊断和裁决，bilingual export 负责受控交付

Consequences:

- `ExportService._enforce_gate()` 现在会在 bilingual export 前检查 export-time misalignment evidence
- API 和 PostgreSQL 集成路径都已经验证 `final export blocked + review package still available`
- 后续若要进一步自动化修复，应优先把这类 anomaly 接入 review issue / action，而不是只停留在 gate 错误字符串

---

## D-038 Export-Time Anomalies Reuse Review Issue Pipeline

Status: Accepted

Decision:

- export gate 发现的保守版 misalignment anomaly，需要同步成正式的 `ReviewIssue` 和 `IssueAction`
- issue 类型继续复用 `ALIGNMENT_FAILURE`，但根因层记为 `export`
- 动作类型继续复用 `REALIGN_ONLY`，scope 优先落在可稳定归属的 packet 上

Why:

- 只有 gate 错误字符串不够可操作，人工和后续自动化都需要结构化 issue/action 才能进入既有修复链路
- 复用现有 `ALIGNMENT_FAILURE -> REALIGN_ONLY` 语义，比再发明一套 export 专用修复机制更稳
- 这样可以把“review 后被破坏”的异常，重新拉回到已有的 review / action / rerun 体系里

Consequences:

- `ExportService` 现在会在 export gate 阶段同步 export-origin alignment issues，并在无异常时自动 resolve 旧的 export-origin 问题
- API 层对 `ExportGateError` 会先提交这批 issue/action，再返回 409，避免异常请求把诊断数据一起回滚掉
- 后续若要进一步自动化，可直接基于这些 persisted action 做 follow-up rerun，而不必重新推断修复范围

---

## D-039 Export Gate Must Return Actionable Follow-Up Hints

Status: Accepted

Decision:

- export gate 的 409 不应只返回文本错误，而要返回结构化 follow-up hints
- 返回值至少包含 `issue_ids`、`action_ids`、`followup_actions[action_type/scope_type/scope_id]`
- 这些 hints 直接对齐现有 `/v1/actions/{action_id}/execute?run_followup=true` 能力

Why:

- 既然 export-time anomaly 已经会持久化成 issue/action，再让调用方二次查询数据库只是增加耦合和往返
- 结构化 hints 能让 API 调用方、未来 UI、自动化脚本在同一次 409 响应里拿到下一步修复入口
- 这也是把“可诊断”推进到“可操作”的最小一步，比立刻自动执行修复更安全

Consequences:

- `ExportGateError` 现在承载结构化 follow-up metadata，而不只是字符串
- document export API 的 409 detail 已升级为对象而不是纯文本
- 后续如果要做“一键自动修复”，可以直接在这套 hints 基础上扩展，而无需再次改 409 契约

---

## D-040 Export Auto-Followup Must Be Opt-In

Status: Accepted

Decision:

- document 级 final export 支持 opt-in `auto_execute_followup_on_gate`
- 仅当 export gate 返回已持久化的 follow-up action hints 时，workflow 才会在同一次请求内自动执行 action，并按该 action 的 `suggested_run_followup` 继续 rerun/re-review
- 默认行为保持不变：未显式开启时，仍然返回 409 和结构化 hints，而不是擅自修复
- 自动修复尝试必须有上限，并避免重复执行同一个 action

Why:

- export gate 的 follow-up hints 已经足够结构化，继续由调用方串联 action -> rerun -> retry 会产生额外往返和状态管理负担
- 但自动修复仍然会修改系统状态、重跑 packet，因此不适合作为默认隐式行为
- opt-in 模式能同时满足“安全可控”和“端到端自动化”的需求，尤其适合后续 UI 的一键修复导出

Consequences:

- `POST /v1/documents/{document_id}/export` 现在接受 `auto_execute_followup_on_gate`
- 成功的自动修复导出会返回 `auto_followup_applied` 和 `auto_followup_executions`
- CLI `export` 也支持 `--auto-followup-on-gate`
- 后续如果继续增强这条链路，应优先补 attempt telemetry、安全阈值暴露和审计可见性，而不是把更多隐式副作用塞进默认导出路径

---

## D-041 Export Auto-Followup Needs Explicit Attempt Telemetry

Status: Accepted

Decision:

- export auto-followup 需要显式暴露 `max_auto_followup_attempts`
- 成功响应必须返回 `auto_followup_requested`、`auto_followup_attempt_count`、`auto_followup_attempt_limit`
- 若导出仍被 gate 阻断，409 detail 也必须返回上述 telemetry，并额外包含 `auto_followup_stop_reason` 和已执行的 auto-followup 摘要
- safety cap 必须按“已执行 action 数”严格裁剪，不能因为同一轮 gate 返回多个 action 而被绕过

Why:

- 一键自动修复一旦涉及多 packet、多 action，调用方需要知道系统到底修了多少、为什么停止
- 只有 `auto_followup_applied=true/false` 不足以区分“没触发修复”“修了一部分但卡住”“达到上限停止”这些关键状态
- 如果 safety cap 只在循环入口检查，同一轮多个 action 会意外突破上限，削弱这条保护机制

Consequences:

- API 和 CLI 现在都支持显式设置 auto-followup attempt limit
- 导出成功和导出失败都能返回一致的 auto-followup telemetry
- PostgreSQL 集成回归新增了 attempt-limit 场景，后续再改 export auto-followup 时必须保持这条约束

---

## D-042 Export Auto-Followup Telemetry Must Leave the API Layer

Status: Accepted

Decision:

- export auto-followup 的 executed / stopped telemetry 不能只存在于一次 API 响应里，还必须持久化为 chapter 级 `audit_events`
- review package 和 bilingual manifest 都必须暴露 `export_auto_followup_evidence`
- `export_auto_followup_evidence` 至少要区分 executed event 和 stopped event，并保留其 payload

Why:

- export auto-followup 本质上会修改系统状态；如果 telemetry 只存在于响应里，后续人工审校、复盘和自动化诊断都会丢失上下文
- review package 和 manifest 本来就是当前 P0 的离线证据面，继续沿这条路径扩展比新建一套 export telemetry 存储更稳
- 这也能让“成功自动修复导出”和“达到 safety cap 停止”两种状态在脱离 API 之后仍然可见

Consequences:

- `DocumentWorkflowService` 现在会在 auto-followup 执行和停止时写入 chapter 级 audit event
- `ExportService` 现在会把这些 audit 汇总成 `export_auto_followup_evidence`
- 本地 API 回归和 PostgreSQL 集成回归都已覆盖成功路径和 attempt-limit 停止路径，后续再动这条链路时必须保留这层证据

---

## D-043 Export Records Need Auto-Followup Summary

Status: Accepted

Decision:

- `Export.input_version_bundle_json` 需要直接写入 `export_auto_followup_summary`
- 该 summary 先保持轻量聚合：`event_count`、`executed_event_count`、`stop_event_count`、`latest_event_at`、`last_stop_reason`
- summary 来源继续复用 export bundle 中已加载的 audit events，而不是新建一套统计表

Why:

- manifest / review package 适合离线工件消费，但后续 dashboard、导出列表和 admin API 更需要直接从 export record 读聚合结果
- 如果每次都从 audit event 现算，后续查询层会重复实现一遍聚合逻辑，也更难保证口径一致
- 这一步不需要 schema 迁移，性价比很高，能先为运营可视化打底

Consequences:

- 成功的 bilingual export 和 review package export 现在都会把 auto-followup 聚合信息带进 export record
- 本地 API 回归和 PostgreSQL 集成回归都已验证成功路径与 attempt-limit 停止路径会写出正确 summary
- 后续若做 dashboard，应优先复用该 summary，再按需回查细粒度 audit event

---

## D-044 Export Dashboard Should Read From Export Records First

Status: Accepted

Decision:

- P0 的 export/admin 查询入口先采用 `GET /v1/documents/{document_id}/exports`
- 该接口同时返回 document 级 dashboard 摘要和 export record 列表
- dashboard 聚合优先直接基于 `exports` 表和 `input_version_bundle_json` 中已有 summary 生成，而不是重新拼 review/audit 多表宽查询

Why:

- 当前最急需的是一个稳定、可消费的查询入口，供后续 admin 页面、脚本和运营排查直接使用
- 既然 export record 已经持有 chapter_id、manifest path、misalignment counts 和 auto-followup summary，就没必要先做更重的跨表 dashboard 查询
- 先把 export record 变成 source of truth，后续再按需要加分页、筛选、成本/时延指标会更稳

Consequences:

- document API 现在新增了 export history / dashboard 查询
- 本地 API 回归和 PostgreSQL 集成回归已覆盖普通导出和 auto-followup 导出的读取场景
- 后续若继续做 dashboard，应优先复用这条接口和 export record summary，而不是新开并行口径

---

## D-045 Export Dashboard Filters Must Not Rewrite Global Counters

Status: Accepted

Decision:

- `GET /v1/documents/{document_id}/exports` 的 `export_type` / `status` / `limit` / `offset` 只作用于 `records` 窗口
- `export_count`、`successful_export_count`、`export_counts_by_type`、`latest_export_ids_by_type` 和 `total_auto_followup_executed_count` 继续保持 document 级全量口径
- 接口必须额外返回 `filtered_export_count`、`record_count`、`has_more`、`applied_export_type_filter`、`applied_status_filter`，避免调用方把当前窗口误当成全量统计

Why:

- export dashboard 下一步会直接服务 admin 页面和运营脚本，如果过滤条件把顶部摘要一起改写，很容易让“文档总共有多少次导出”和“当前我筛出来多少条记录”混成同一个口径
- 当前 P0 的 export record 是按 `document + chapter + export_type` 幂等写入的，同类型重复导出会覆盖旧 record；这种语义更要求 dashboard 明确区分“全量快照”和“当前列表窗口”
- 先把查询口径收紧，后续再加成本和时延维度时才不会产生第二套统计解释

Consequences:

- API 和 service 现在都支持分页与筛选
- 本地 API 回归和 PostgreSQL 集成回归都已覆盖“全量 dashboard summary 不受过滤影响、records 窗口可分页”的场景
- 后续若新增前端或管理端列表，应优先消费 `filtered_export_count / record_count / has_more`，而不是自行猜测分页口径

---

## D-046 Export Observability Should Separate Document Aggregates From Export Snapshots

Status: Accepted

Decision:

- `GET /v1/documents/{document_id}/exports` 需要返回 document 级 `translation_usage_summary`，它基于当前 document 下的全部 `translation_runs` 聚合
- `GET /v1/documents/{document_id}/exports/{export_id}` 需要返回 export 级 detail，并优先读取 export record 持久化的 `translation_usage_summary`、`issue_status_summary`、version evidence 和 misalignment counts
- export record 中的 usage / issue 数据视为“导出当下的快照”，不在 detail 查询时回放当前 chapter 现状去重新计算

Why:

- document dashboard 和 export detail 本来就服务两种不同问题：前者看整份文档当前累计投入，后者看某次导出当时拿到了什么上下文和证据
- 如果 detail 查询回头去拼 chapter 当前状态，就会把后续 rerun、review 或 auto-followup 的变化混进旧 export，破坏“可复盘性”
- 当前 export record 已经是 export/admin 查询的 source of truth，把 usage / issue 快照继续沉进去，比新建一套 detail materialization 更稳

Consequences:

- export dashboard 现在同时具备 document 级 translation usage summary 和 record 级 translation usage snapshot
- document API 现在新增 export detail 查询入口
- 本地 API 回归和 PostgreSQL 集成回归都已覆盖 usage / issue / version evidence 的读取，后续若改 export record 字段，必须同步维护这条 detail contract

---

## D-047 Translation Usage Needs A Breakdown, Not Just Totals

Status: Accepted

Decision:

- export dashboard 和 export detail 除了总量 `translation_usage_summary`，还必须返回 `translation_usage_breakdown`
- breakdown 按 `model_name + worker_name + provider` 聚合
- document dashboard 的 breakdown 基于当前 document 下的全部 `translation_runs`
- export detail / record 的 breakdown 读取 export record 持久化快照，而不是在查询时动态回放 translation runs

Why:

- 一旦出现 rerun、provider 切换或 mixed worker 路径，仅看总 token / 总 cost / 总 latency 已经不足以解释问题
- 当前系统已经有 `model_name` 和 `worker` 元数据，继续聚成 breakdown 的代价很低，但能显著提升排障和成本分析价值
- 依然保持“dashboard 看当前聚合，detail 看导出快照”的双口径，可以避免后续查询层把历史 export 和当前 document 状态混在一起

Consequences:

- dashboard 和 export detail 现在都能回答“这些调用主要落在哪个模型/worker 上”
- 本地 API 回归和 PostgreSQL 集成回归都已覆盖 breakdown 读取
- 后续若做成本/时延图表，应优先基于这套 breakdown 继续扩展，而不是重新定义另一套 provider 统计口径

---

## D-048 Translation Usage Trends Should Start With A Fixed Daily Timeline

Status: Accepted

Decision:

- document export dashboard 需要直接返回 `translation_usage_timeline`
- P0 先固定为 UTC day bucket，不在接口层引入可选粒度参数
- timeline 继续基于当前 document 下的 `translation_runs` 聚合，与 document 级 `translation_usage_summary` 保持同一口径

Why:

- 当前最需要的是一个稳定、低歧义的趋势入口，而不是一次性做完复杂的多粒度时间序列 API
- 现阶段样本量和使用场景都还偏早期，先固定 day bucket 能更快验证“趋势口径是否有用”
- 保持与 summary/breakdown 同一来源，也能避免 time series 和 totals 出现两套统计值

Consequences:

- dashboard 现在具备三层 usage 视图：summary、breakdown、timeline
- 本地 API 回归和 PostgreSQL 集成回归都已覆盖 timeline 读取
- 后续若引入小时级或自定义 bucket，应作为新的扩展决策处理，而不是直接改写当前 contract

---

## D-049 Usage Highlights Should Be Derived From Breakdown, Not Recomputed Separately

Status: Accepted

Decision:

- document export dashboard 需要直接返回 `translation_usage_highlights`
- highlights 先固定为三个入口：`top_cost_entry`、`top_latency_entry`、`top_volume_entry`
- 这些 highlights 必须直接从 `translation_usage_breakdown` 派生，而不是引入第二套单独聚合逻辑

Why:

- usage highlights 的价值在于“让调用方不需要自己再把 breakdown 归纳一遍”
- 如果 highlights 单独计算，后续极容易出现和 breakdown 不同口径、不同 tie-breaker 的问题
- 当前 breakdown 已经稳定包含成本、时延、run_count 和 provider/model 维度，从它直接派生 highlights 代价低且最稳

Consequences:

- dashboard 现在具备四层 usage 视图：summary、breakdown、timeline、highlights
- 本地 API 回归和 PostgreSQL 集成回归都已覆盖 highlights 可读
- 后续若扩展“最贵 provider”“最慢 rerun path”这类卡片，应优先从 breakdown 派生，而不是再造新统计源

---

## D-050 Issue Hotspots Should Be Aggregated Live From Review Issues

Status: Accepted

Decision:

- document export dashboard 需要直接返回 `issue_hotspots`
- hotspot 按 `issue_type + root_cause_layer` 聚合
- 每个 hotspot 至少返回 `issue_count`、`open_issue_count`、`triaged_issue_count`、`resolved_issue_count`、`wontfix_issue_count`、`blocking_issue_count`、`chapter_count` 和 `latest_seen_at`
- 该聚合基于当前 document 下的 `review_issues` 现状实时查询，不写入 export record snapshot

Why:

- usage 看的是“钱和时延花在哪”，但运营面同样需要知道“问题集中爆在哪”
- issue 热点是 document 当前健康度视角，不属于单次 export 的历史快照；如果把它持久化进 export record，反而会把历史导出和当前问题分布混在一起
- 当前 `review_issues` 已经具备 `issue_type`、`root_cause_layer`、`status`、`blocking` 和 `chapter_id`，直接聚合的代价低，而且口径最清楚

Consequences:

- dashboard 现在同时具备 usage 视角和 issue hotspot 视角
- 本地 API 回归和 PostgreSQL 集成回归都已覆盖“无热点时为空”和“export-rooted alignment failure 能稳定出现在热点里”的场景
- 后续若扩展 issue 趋势、issue heatmap 或 chapter-level hotspot drill-down，应优先复用这套 `issue_type + root_cause_layer` 聚合口径

---

## D-051 Chapter Pressure Should Be A Separate Dashboard Aggregate

Status: Accepted

Decision:

- document export dashboard 需要直接返回 `issue_chapter_pressure`
- chapter pressure 按 `chapter_id` 聚合，返回 `ordinal`、`title_src`、`chapter_status`、`issue_count`、`open_issue_count`、`triaged_issue_count`、`resolved_issue_count`、`blocking_issue_count` 和 `latest_issue_at`
- 该聚合只针对当前有 issue 的章节；没有 issue 的 document 返回空列表
- 该聚合同样基于当前 `review_issues` 和 `chapters` 实时查询，不写入 export snapshot

Why:

- `issue_hotspots` 解决了“哪类问题最集中”，但还不能回答“先看哪一章最值”
- chapter pressure 更接近运营和审校的行动入口，能把问题类型聚合再落到具体章节
- 继续保持 live aggregate 语义，可以避免历史 export 快照和当前 chapter 健康度混在一起

Consequences:

- export dashboard 现在同时具备 type-level hotspot 和 chapter-level pressure 两种问题视角
- 本地 API 回归和 PostgreSQL 集成都已覆盖“无 issue 时为空”和“export misalignment 能落到具体 chapter pressure”的场景
- 后续若要做 chapter drill-down、review queue 排序或 chapter heatmap，应优先复用这套 chapter pressure 聚合口径

---

## D-052 Issue Activity Timeline Should Track Daily Created vs Resolved Flow

Status: Accepted

Decision:

- document export dashboard 需要直接返回 `issue_activity_timeline`
- timeline 先固定为 day bucket
- 每个 bucket 至少返回 `created_issue_count`、`resolved_issue_count`、`wontfix_issue_count`、`blocking_created_issue_count`、`net_issue_delta` 和 `estimated_open_issue_count`
- 该 timeline 基于当前 `review_issues` 的 `created_at / updated_at / status / blocking` 实时聚合，不依赖额外事件表

Why:

- `issue_hotspots` 和 `issue_chapter_pressure` 已经回答了“问题是什么、压在哪”，但还不能回答“问题是在堆积还是在被消化”
- 当前系统还没有正式的 issue lifecycle event store，直接从现有 issue 行推导 daily flow，是 P0 阶段成本最低也最稳定的方式
- fixed daily bucket 和 usage timeline 保持一致，更适合作为第一版 dashboard 契约

Consequences:

- export dashboard 现在具备 type、chapter 和 time 三个问题视角
- 本地 API 回归和 PostgreSQL 集成回归都已覆盖“未修复问题产生净新增”和“auto-followup 修复后同日净增归零”的场景
- 后续若引入更细的 review event 流或 hourly bucket，应作为新的扩展决策，而不是直接改写当前 timeline contract

---

## D-053 Issue Timeline Drill-Down Should Reuse The Hotspot Key

Status: Accepted

Decision:

- document export dashboard 需要直接返回 `issue_activity_breakdown`
- breakdown 按 `issue_type + root_cause_layer` 分组，而不是只按 `issue_type`
- 每个 breakdown entry 需要带当前压力摘要和独立 `timeline`
- 该 breakdown 复用 `issue_hotspots` 的分组键，避免两套问题分类口径并存

Why:

- 仅按 `issue_type` drill-down 会把同名问题的不同根因层混在一起，尤其会把 `ALIGNMENT_FAILURE` 的 packet/export 场景混淆
- 既然 `issue_hotspots` 已经证明 `issue_type + root_cause_layer` 是当前最稳的聚合键，timeline drill-down 继续复用这套口径最一致
- 这样前端和运维脚本不需要再自行把 hotspot 和 timeline 做二次 join

Consequences:

- export dashboard 现在具备按问题族下钻的时间趋势能力
- 本地 API 回归和 PostgreSQL 集成都已覆盖 export-rooted `ALIGNMENT_FAILURE` 的 breakdown timeline
- 后续若做按 issue family 的 heatmap、sparklines 或 top-regressing issue 视图，应优先复用这套 breakdown contract

---

## D-054 Issue Activity Highlights Should Be Derived From Breakdown

Status: Accepted

Decision:

- document export dashboard 需要直接返回 `issue_activity_highlights`
- highlights 固定为三个入口：`top_regressing_entry`、`top_resolving_entry`、`top_blocking_entry`
- highlights 必须直接从 `issue_activity_breakdown` 派生，而不是再做一套独立 issue 聚合

Why:

- `issue_activity_breakdown` 已经具备当前压力和时间趋势，继续从它派生 highlights，能避免出现第二套问题口径
- 运营面最常见的三个快速问题就是：哪类问题在继续变坏、哪类问题最近在被解决、哪类问题当前最阻断
- 如果 highlights 单独计算，后续很容易和 breakdown 的排序/tie-breaker 出现不一致

Consequences:

- export dashboard 现在具备结论型 issue 卡片，不需要调用方自己从 breakdown 再归纳一遍
- 本地 API 回归和 PostgreSQL 集成都已覆盖未修复与 auto-followup 修复后的 highlights 行为
- 后续若扩展“top-wontfix”或“top-flapping issue family”，应优先继续从 breakdown 派生，而不是重建新统计源

---

## D-055 Issue Chapter Highlights Should Be Derived From Chapter Pressure

Status: Accepted

Decision:

- document export dashboard 需要直接返回 `issue_chapter_highlights`
- highlights 固定为三个入口：`top_open_chapter`、`top_blocking_chapter`、`top_resolved_chapter`
- highlights 必须直接从 `issue_chapter_pressure` 派生，而不是再做一套独立 chapter 聚合

Why:

- `issue_chapter_pressure` 已经是当前 chapter 级问题压力的统一口径，继续从它派生 highlights，能避免第二套 chapter 排序逻辑
- 运营面最常见的 chapter 级快速问题就是：哪一章最积压、哪一章最阻断、哪一章最近被修得最多
- 如果 highlights 单独计算，后续很容易和 chapter pressure 的 tie-breaker 或过滤语义出现不一致

Consequences:

- export dashboard 现在具备 chapter 级结论卡片，不需要调用方再自行从 chapter pressure 里找重点章节
- 本地 API 回归和 PostgreSQL 集成都已覆盖“无 issue 时为空”“未修复时 top-open/top-blocking 指向问题章节”“auto-followup 修复后 top-resolved 指向该章节”的场景
- 后续若扩展 chapter drill-down、chapter heatmap 或 flapping chapter 提示，应优先复用这套 highlights 和 chapter pressure 契约

---

## D-056 Chapter Heatmap Should Be Derived From Chapter Breakdown

Status: Accepted

Decision:

- document export dashboard 需要直接返回 `issue_chapter_breakdown`
- chapter breakdown 按 `chapter + issue_type + root_cause_layer` 分组，而不是只按 chapter 汇总
- document export dashboard 需要直接返回 `issue_chapter_heatmap`
- chapter heatmap 必须直接从 `issue_chapter_breakdown` 派生，而不是再做一套独立 chapter 聚合
- heatmap 的 `heat_score` 只反映当前 active 压力：`open * 3 + triaged * 2 + active_blocking * 4`

Why:

- 只有 chapter summary 还不够，运营面需要知道“哪一章热”之外，还要知道“热是被哪类问题点燃的”
- 直接按 `chapter + issue family` 做 breakdown，能复用既有 `issue_type + root_cause_layer` 口径，避免 chapter 视图和 family 视图出现不同分类
- heatmap 如果继续把历史已解决但 blocking 过的问题计入当前热度，会让 chapter 当前压力失真，所以需要单独引入 `active_blocking_issue_count`

Consequences:

- export dashboard 现在同时具备 chapter summary、chapter highlights、chapter family drill-down 和 current heatmap 四层 chapter 视图
- 本地 API 回归和 PostgreSQL 集成都已覆盖“无 issue 时为空”“未修复 export misalignment 时 heat 升到 high”“auto-followup 修复后 heat 回到 none”的场景
- 后续若扩展 chapter heatmap legend、chapter queue 排序或 flapping chapter 提示，应优先复用这套 breakdown + active heat 契约

---

## D-057 Chapter Queue Should Be Derived From Actionable Heatmap Entries

Status: Accepted

Decision:

- document export dashboard 需要直接返回 `issue_chapter_queue`
- queue 只包含当前可操作的 chapter：至少满足 `open_issue_count > 0`、`triaged_issue_count > 0` 或 `active_blocking_issue_count > 0`
- queue 必须直接从 `issue_chapter_heatmap` 派生，而不是再做一套独立 chapter 排队逻辑
- queue 至少返回 `queue_rank`、`queue_priority`、`queue_driver` 和 `needs_immediate_attention`

Why:

- heatmap 解决的是“哪一章热”，但运营面下一步真正要做的是“先处理哪一章”
- 如果 queue 再单独聚合一次，很容易和 heatmap 的当前压力口径不一致，尤其会重新把 resolved-only 章节混回去
- 以 `active_blocking/open/triaged` 为过滤条件，可以把 queue 明确收敛成当前 actionable backlog，而不是历史问题列表

Consequences:

- export dashboard 现在同时具备 chapter pressure、chapter highlights、chapter family drill-down、chapter heatmap 和 actionable chapter queue 五层 chapter 视图
- 本地 API 回归和 PostgreSQL 集成都已覆盖“未修复 export misalignment 进入 queue 且 priority=immediate”“auto-followup 修复后 queue 为空”的场景
- 后续若扩展 chapter worklist、ops SLA 队列或 flapping chapter 提示，应优先复用这套 queue 契约，而不是重建新的 chapter 排序源

---

## D-058 Chapter Queue Hints Should Reuse Chapter Activity Timeline

Status: Accepted

Decision:

- `issue_chapter_queue` 需要直接返回轻量动态信号：`latest_created_issue_count`、`latest_resolved_issue_count`、`latest_net_issue_delta`、`regression_hint`、`flapping_hint`
- 这些 hint 必须直接从 chapter 级 issue activity timeline 派生，而不是再做独立 chapter 事件存储
- `regression_hint` 当前只保留三档：`regressing`、`resolving`、`stable`
- `flapping_hint` 当前采用保守口径：最近 3 个非零 delta bucket 同时出现正负变化才判为 true

Why:

- queue 已经回答了“先处理哪一章”，但运营面还需要快速知道“这章是在继续变坏，还是只是当前有积压”
- 当前系统已经具备 chapter 级 issue activity timeline 的最小可推导基础，没有必要在 P0 再引入新的 chapter lifecycle event store
- `flapping` 很容易误报，所以第一版必须用保守规则，先做 hint 而不是直接做 blocking signal

Consequences:

- actionable chapter queue 现在不仅能排序，还能给出轻量 regression/flapping 提示
- 本地 API 回归和 PostgreSQL 集成都已覆盖“单日 export misalignment 标为 regressing 且 flapping=false”的场景
- 后续若要演进成更强的 flapping chapter alert，应优先在现有 chapter activity timeline 上扩展，而不是推翻当前 queue hint 契约

---

## D-059 Chapter Worklist Fields Should Be Derived From Active Issues And Queue Priority

Status: Accepted

Decision:

- `issue_chapter_queue` 需要继续返回可执行 worklist 字段：`oldest_active_issue_at`、`age_hours`、`age_bucket`、`sla_target_hours`、`sla_status`、`owner_ready`、`owner_ready_reason`
- worklist 字段必须直接从当前 active issue 和既有 queue priority 派生，而不是引入新的 chapter ownership / SLA 存储层
- `age` 只基于当前 active issue 计算，不把已解决 issue 历史重新算回当前 backlog
- `sla_target_hours` 当前固定由 queue priority 推导：`immediate=4`、`high=24`、`medium=72`
- `owner_ready` 当前保持轻量 hint：当 dominant issue family 已明确时为 true，否则为 false

Why:

- chapter queue 已经回答“先处理哪一章”，但 ops worklist 还需要回答“这章压了多久、是否快超时、是否已经足够明确可以直接派人处理”
- 当前系统已经具备 active issue、dominant issue family 和 queue priority，没有必要在 P0 额外引入新的 owner / SLA 事件模型
- 如果 age/SLA 不和 active issue 范围绑定，很容易把已经解决的问题重新计入当前章节压力，导致队列失真

Consequences:

- export dashboard 现在具备更完整的 chapter ops worklist 语义，而不是只停留在 chapter triage 排序
- 本地 API 回归和 PostgreSQL 集成都已覆盖未修复场景下的 `oldest_active_issue_at / age_hours / sla_status / owner_ready` 可读性，以及修复后 queue 为空的场景
- 后续若扩展 owner assignment、SLA breach highlights 或独立 ops worklist API，应优先复用这套 active-issue + queue-priority 契约，而不是再引入第二套 worklist 统计源

---

## D-060 Dedicated Chapter Worklist API Should Reuse The Existing Queue Contract

Status: Accepted

Decision:

- document API 需要单独提供 `GET /documents/{id}/chapters/worklist`
- 独立 worklist API 必须直接复用既有 `issue_chapter_queue` 契约，而不是再单独聚合一套 chapter worklist 数据源
- 独立 worklist API 当前支持 `queue_priority`、`sla_status`、`owner_ready`、`needs_immediate_attention`、`limit`、`offset`
- worklist summary counts 保持 document-global 口径，过滤和分页只影响返回的 `entries`

Why:

- export dashboard 已经具备完整 chapter queue 语义，但 ops 面读取 chapter backlog 时不应该强依赖更重的 export dashboard 载荷
- 如果独立 worklist API 再做第二套 chapter 聚合，很容易和 dashboard queue 的排序、SLA 和 owner-ready 口径漂移
- 保持“summary 全量、entries 可过滤/分页”的模式，和现有 export dashboard 的查询语义一致，更容易被调用方稳定消费

Consequences:

- 现在 chapter 级 ops worklist 可以通过更轻的独立 API 读取，不需要再拉全量 export dashboard
- 本地 API 回归和 PostgreSQL 集成都已覆盖“未修复 misalignment 进入 worklist，并可按 `queue_priority / sla_status / owner_ready / needs_immediate_attention` 过滤”的场景
- 后续若扩展 owner assignment、SLA breach highlights 或 chapter worklist detail API，应优先复用这条独立 API 的过滤和 summary 语义

---

## D-061 Chapter Worklist Highlights Should Be Derived From The Full Worklist

Status: Accepted

Decision:

- 独立 chapter worklist API 需要返回 `highlights`
- highlights 固定为四个入口：`top_breached_entry`、`top_due_soon_entry`、`top_oldest_entry`、`top_immediate_entry`
- highlights 必须直接从完整 worklist 派生，而不是只从当前过滤后的 `entries` 派生
- `top_breached` 和 `top_due_soon` 以 `sla_status` 为主筛选，再按 `age_hours / heat_score / active_blocking_issue_count` 排序
- `top_oldest` 和 `top_immediate` 也继续复用同一套 queue 字段，不引入新的 chapter urgency 模型

Why:

- ops 面最常见的问题不是“worklist 里有什么”，而是“现在最危险的是谁、谁已经超 SLA、谁最老、谁必须立刻看”
- 如果 highlights 跟着过滤结果一起缩小，很容易让调用方误把“当前筛选窗口里最严重”当成“全局最严重”
- 继续复用现有 queue 字段和排序维度，能避免 highlights 和 queue 自身的优先级语义漂移

Consequences:

- 独立 chapter worklist API 现在具备结论型 ops 卡片，不需要调用方再从整条 worklist 自己归纳重点章节
- 本地 API 回归和 PostgreSQL 集成都已覆盖 breached SLA 场景下 `top_breached / top_oldest / top_immediate` 的稳定指向
- 后续若扩展 owner assignment、SLA breach alerts 或 chapter worklist detail API，应优先复用这套 full-worklist highlights 契约

---

## D-062 Chapter Worklist Detail API Should Reuse Existing Chapter Queue And Issue Aggregates

Status: Accepted

Decision:

- document API 需要单独提供 `GET /documents/{id}/chapters/{chapter_id}/worklist`
- detail API 必须直接复用现有 chapter queue、chapter issue breakdown 和 persisted chapter quality summary，而不是重新引入新的 chapter worklist 数据模型
- detail API 当前返回：`queue_entry`、`current_issue_count / current_open_issue_count / current_triaged_issue_count / current_active_blocking_issue_count`、`issue_family_breakdown`、`recent_issues`、`recent_actions`、`quality_summary`
- recent issue/action 列表当前按 `updated_at desc` 排序，保持轻量 ops 最近活动视图，不引入新的 history projection

Why:

- ops 面从“看整条 queue”继续往下走，最自然的下一步就是点进单章，看它为什么在 queue 里、最近发生了什么、现在还剩什么问题
- 当前系统已经具备 chapter queue、issue family breakdown、quality summary 和 review issue/action 持久化，没有必要在 P0 再引入新的 chapter detail projection 层
- 继续复用现有聚合，能保证 detail 视图和 worklist / dashboard 的口径一致，不会出现“列表里一个数，点进去又是另一套数”

Consequences:

- 现在 ops 层已经可以从 worklist 列表直接 drill down 到单章 detail，而不需要自己拼装 chapter queue、review issue 和 issue action 数据
- 本地 API 回归和 PostgreSQL 集成都已覆盖 export-rooted `ALIGNMENT_FAILURE` 场景下 detail 的 `queue_entry / issue family breakdown / recent issues / recent actions` 可读性
- 后续若扩展 owner assignment、assignment history 或 chapter worklist note，应优先在这个 detail API 上增量扩展，而不是重建新的 chapter ops detail 契约

---

## D-063 Chapter Worklist Assignment Should Use A Single Persistent Chapter-Level Record

Status: Accepted

Decision:

- chapter worklist owner assignment 使用单条持久化 `chapter_worklist_assignments` 记录，按 `chapter_id` 唯一约束
- assignment 当前只保留章节级当前 owner，不在 P0 引入独立 assignment history projection
- `GET /documents/{id}/chapters/worklist` 和 `GET /documents/{id}/chapters/{chapter_id}/worklist` 必须直接反映 assignment 状态，返回 `is_assigned / assigned_owner_name / assigned_at`
- 独立 chapter worklist API 当前支持 `assigned` 和 `assigned_owner_name` 过滤；assignment 管理由独立 assign/clear endpoint 提供
- assignment 的历史变化当前通过 `audit_events` 记录 `chapter.worklist.assignment.set` 和 `chapter.worklist.assignment.cleared`，不再维护第二套 chapter ownership event store

Why:

- 现有 chapter worklist 已经具备稳定的 queue/detail 契约，最自然的下一步是“能分派”，而不是再做新的 ops 子系统
- P0 只需要知道“这章现在归谁”，不需要先引入更重的 assignment history / ownership workflow
- 用单条 chapter 级 assignment 记录可以保持 queue、detail、filter 和 assign/clear API 的口径完全一致，同时把历史变化留给现有 audit 体系

Consequences:

- 现在 ops 层已经可以对章节进行 assign / clear，并且 queue/filter/detail 会立即反映当前 owner
- 本地 API 回归和 PostgreSQL 集成都已覆盖 assign -> detail/filter -> clear 闭环
- 后续若扩展 assignment history、owner alerts 或 owner workload 视图，应优先复用 `chapter_worklist_assignments + audit_events`，而不是推翻当前 chapter 级单记录模型

---

## D-064 Owner Workload Summary Should Be Derived From The Full Actionable Chapter Queue

Status: Accepted

Decision:

- `GET /documents/{id}/chapters/worklist` 需要返回 `owner_workload_summary`
- owner workload 只统计当前 actionable 且已 assigned 的 chapter queue entries，不引入新的 owner workload projection 表
- summary 必须从完整 actionable queue 派生，而不是从当前过滤后的 `entries` 派生
- 每个 owner 当前返回：`assigned_chapter_count`、priority/SLA 分布、`owner_ready_count`、`total_open_issue_count`、`total_active_blocking_issue_count`、`oldest_active_issue_at`、`latest_issue_at`

Why:

- chapter queue 已经回答“先处理哪一章”，但进入执行阶段后，ops 更关心“谁手上压了多少、是否有人堆积 immediate/breached 章节”
- 当前系统已经具备 assignment、queue priority、SLA 和 active issue 压力，没有必要在 P0 额外引入新的 owner workload 物化层
- 若 owner workload 跟随过滤窗口变化，很容易把“当前查询视图”误当成“真实 owner 压力”

Consequences:

- worklist API 现在同时具备 chapter queue 视角和 owner workload 视角，调用方不需要自己从 entries 二次聚合
- 本地 API 回归和 PostgreSQL 集成都已覆盖 assign 后 workload 出现、clear 后 summary 清空的场景
- 后续若扩展 owner-aware highlights、assignment history 或 owner alerts，应优先在这个 full-queue owner workload 契约上增量推进

---

## D-065 Owner Workload Highlights Should Be Derived From Owner Workload Summary

Status: Accepted

Decision:

- `GET /documents/{id}/chapters/worklist` 需要返回 `owner_workload_highlights`
- highlights 固定为四个入口：`top_loaded_owner`、`top_breached_owner`、`top_blocking_owner`、`top_immediate_owner`
- highlights 必须直接从 `owner_workload_summary` 派生，而不是再做一套独立 owner 聚合
- highlights 继续保持 full-worklist 口径，不跟随当前过滤后的 `entries` 变化

Why:

- worklist 已经具备 owner summary，但 ops 常见问题不是“每个人的数据是什么”，而是“现在谁最满、谁最危险、谁最需要优先处理”
- 继续从 owner workload summary 派生 highlights，能保持和 owner summary 同一套统计口径，避免出现第二套 owner 排序逻辑
- 如果 highlights 跟着过滤窗口变化，调用方很容易把“当前筛选结果中最重的 owner”误当成“全局最重的 owner”

Consequences:

- chapter worklist 现在同时具备 owner summary 和结论型 owner 卡片，调用方不需要再从 summary 自己归纳 top owner
- 本地 API 回归和 PostgreSQL 集成都已覆盖无 assignment 时 highlights 为空、assign 后 owner 卡片可读、clear 后恢复为空
- 后续若扩展 owner alerts、workload balancing hints 或 assignment history，应优先复用这套 owner workload summary/highlights 契约

---

## D-066 Assignment History Should Be Derived From Chapter Assignment Audit Events

Status: Accepted

Decision:

- chapter worklist detail 需要返回 `assignment_history`
- assignment history 直接从 `audit_events` 中的 `chapter.worklist.assignment.set` 和 `chapter.worklist.assignment.cleared` 派生，不引入新的 assignment history projection 表
- history 当前以 newest-first 返回，包含：`event_type`、`owner_name`、`performed_by`、`note`、`created_at`
- assignment history 当前挂在 `GET /documents/{id}/chapters/{chapter_id}/worklist` 上，不单独新增 assignment history API

Why:

- 一旦有了 assign / clear，ops 面最自然的下一个问题就是“这章之前分给过谁、何时被拿走、谁操作的”
- 当前系统已经把 assignment 变更可靠写入 `audit_events`，继续从这条事件流派生 history，成本最低且口径最稳
- P0 还不需要为了 assignment history 再维护新的 materialized projection 或独立 endpoint

Consequences:

- chapter worklist detail 现在已经能回答当前 assignment 和近期 assignment 变更历史两个问题
- 本地 API 回归和 PostgreSQL 集成都已覆盖 assign 后出现 `set` 记录、clear 后按倒序出现 `cleared -> set` 的场景
- 后续若扩展 assignment history API、owner alerts 或 assignment anomaly detection，应优先复用这套 audit-derived history 契约

---

## D-067 Empty Chapters Must Still Participate In Review, But Chapter-Level Context Issues Cannot Invent Sentence Foreign Keys

Status: Accepted

Decision:

- `review_document()` 不再跳过 `translation_packets = 0` 的章节；零 packet chapter 也必须进入统一 review 流程
- 当 chapter-level `CONTEXT_FAILURE` 发生在 `bundle.sentences = 0` 的章节时，`ReviewIssue.sentence_id` 必须写成 `null`
- 对空章节的 review 仍然复用现有 `ReviewService.review_chapter()`、quality summary 持久化和 export gate 口径，不额外分叉出“空章节专用状态机”

Why:

- 真实 EPUB smoke 表明，frontmatter / welcome 这类空章节如果停留在 `packet_built`，会直接把整本书的 review/export 卡死
- 同一次真实 smoke 还暴露出第二个边界：chapter brief 的 `open_questions` 在零句子章节上会生成 chapter-level issue，但如果强行伪造 `sentence_id`，SQLite / PostgreSQL 外键都会拒绝
- 这类问题的根因是 chapter-level context，而不是具体句子级错误；继续绑到一个不存在的 sentence 上只会污染数据模型

Consequences:

- 现在 frontmatter / 空章节会正常进入 `qa_checked`、`review_required` 和 export 路径，不会永久卡在 `packet_built`
- 现在零句子章节上的 chapter-level context issue 能稳定持久化，并通过本地 API / PostgreSQL 回归验证
- 真实 EPUB《Build an AI Agent (From Scratch)》已经能稳定跑通 `bootstrap / translate / review / review package export`
- 仍然保留一个后续产品决策点：无正文 frontmatter 的 chapter-brief `open_questions` 是否应继续作为阻断性 review issue

---

## D-068 Runtime Should Accept Standard OpenAI Environment Variable Names

Status: Accepted

Decision:

- `translation_openai_api_key` 同时接受 `BOOK_AGENT_TRANSLATION_OPENAI_API_KEY` 和 `OPENAI_API_KEY`
- `translation_openai_base_url` 同时接受 `BOOK_AGENT_TRANSLATION_OPENAI_BASE_URL` 和 `OPENAI_BASE_URL`
- 保留现有 `BOOK_AGENT_...` 命名，不移除项目私有前缀；只是增加标准 OpenAI 环境变量别名

Why:

- 真实 LLM smoke 的下一个实际阻塞是运行凭据，而不是 provider adapter 代码
- 本机和 CI/CD 环境里更常见的是标准 `OPENAI_API_KEY` / `OPENAI_BASE_URL`，如果强制要求一套项目私有变量，会增加接入摩擦和排障成本
- 当前 provider adapter 已经是 OpenAI-compatible contract，接受标准环境变量不会引入架构分叉

Consequences:

- 现在本地 shell、现有 OpenAI 工具链和部署环境可以直接复用标准环境变量，不需要额外做一次 rename
- `Settings(...)` 直接构造和环境变量读取都已经通过回归验证
- 真实 LLM smoke 现在只剩“提供有效凭据并允许外网请求”两个运行条件

---

## D-069 OpenAI-Compatible Provider Must Support Both Responses API And Chat Completions

Status: Accepted

Decision:

- `openai_compatible` provider 不再假设所有兼容服务都支持 `/v1/responses`
- 当 `base_url` 以 `/responses` 结尾时，继续走 Responses API + json_schema
- 当 `base_url` 指向 provider root、`/v1` 或 `/chat/completions` 时，自动切到 `chat/completions + response_format=json_object`
- 在 `chat/completions` 模式下，prompt 必须显式附带完整输出 schema 和禁止顶层键提示，不能只依赖 provider 的 JSON 模式
- translation ingestion 还必须对常见宽松标签做最小规范化，例如：
  - `segment_type=translation -> sentence`
  - `relation_type=one_to_one -> 1:1`

Why:

- 真实 DeepSeek live smoke 证明：网络与认证已通，但 provider 返回的是 `chat.completion`，不是 Responses API
- 同一次 live smoke 还暴露出第二个问题：即使拿到 JSON，provider 也可能只返回宽松结构或近似枚举值，不能直接假设完全命中内部 contract
- 如果继续把“OpenAI-compatible”理解成“Responses API-compatible”，会把大量实际可用 provider 错误排除在外

Consequences:

- 现在同一个 `openai_compatible` backend 已可同时覆盖 OpenAI Responses API 和 DeepSeek 风格 `chat/completions`
- DeepSeek 真实 live smoke 已成功完成：
  - 3 个 packet 的协议级验证
  - 6 个跨章节代表样本的真实译文验证
- 后续若接更多 OpenAI-compatible provider，应优先复用这套双模式 endpoint 解析和最小输出规范化，而不是再增加新的 backend 名称

---

## D-070 Real-Book Live Reruns Must Use Batch Commits And Incremental Progress Reports

Status: Accepted

Decision:

- 真实书籍的 live rerun 不再使用“整本书一次 translate 事务”模式，而必须按 packet batch 分段提交
- P0 的 real-book runner 统一使用 `scripts/run_real_book_live.py`
- runner 必须在每个 stage 和每个 translate batch 完成后，把当前进度写回 report 文件
- 当前 batch 默认保持小批量顺序执行，优先保证已完成结果可提交、可恢复、可观测，而不是追求一次性吞吐最大化

Why:

- 真实书籍《Build an AI Agent (From Scratch)》的 DeepSeek live rerun 已经证明：单线程全量重跑是小时级任务，而不是一次 CLI 调用就能安全跑完的小任务
- 如果整书翻译放在一个大事务里，中途失败会让已成功的 live 请求全部丢失，也几乎无法判断当前实际跑到哪里
- 长时间 live run 如果没有增量 report，调用方无法区分“仍在稳定推进”和“已经卡住但没有输出”

Consequences:

- 现在真实书籍 live rerun 已有统一 runner，可沉淀阶段性报告和 batch 进度
- 《Build an AI Agent (From Scratch)》的 DeepSeek 部分全量基线已经通过这条路径验证：首批 20 个 packet 成功提交，并形成了可读的真实吞吐和质量样本
- 后续若要继续做整本书 live rerun，应优先复用这条 batch-commit runner，并进一步补 usage/cost/latency 解析，而不是回到一次性长事务模式

---

## D-071 Provider Usage And Parallel Real-Book Reruns Are First-Class Operational Signals

Status: Accepted

Decision:

- `openai_compatible` provider 必须把真实 `token_in / token_out / latency_ms` 解析并写入 `translation_runs`
- `cost_usd` 可以保持可选，但 usage 和 latency 不能继续为 0
- 真实书籍 live rerun 默认采用并发 packet 执行能力，而不是继续坚持单线程 packet loop

Why:

- 真实 DeepSeek smoke 已经证明：如果 usage 和 latency 不入库，dashboard、export snapshot 和整书运行评估都会失真
- 同样的真实 smoke 也证明：整本书单线程 live rerun 是小时级任务，必须通过并发 packet 执行降低 wall-clock time
- P0 现在的关键不是“能不能跑”，而是“长任务能不能被观测、被恢复、被估算”

Consequences:

- 现在真实 DeepSeek 并发 smoke 已可观测到非零 token 和 latency，并成功落在 `translation_runs`
- 真实书籍 live runner 现在可以用 `parallel_workers` 提升吞吐，同时保持 per-packet commit 和增量进度报告
- 下一步的成本视图应建立在真实 usage/latency 的持久化基础上，而不是继续用 0 值占位

---

## D-072 Concurrent Real-Book Persistence Must Preserve Parent-First Inserts, And Full-Book Reruns Should Resume From Existing Progress Stores

Status: Accepted

Decision:

- 并发真实翻译下，`translation_runs`、`target_segments`、`alignment_edges` 的持久化必须使用显式 `add/add_all + flush` 的父先子后顺序，不能继续依赖 `merge()` 推断插入顺序
- 真整书 live rerun 出现中途中断或并发写入修复后，应优先复用现有 progress database / report 恢复剩余 `BUILT` packets，而不是从零重建整书状态

Why:

- 《Build an AI Agent (From Scratch)》的首轮并发 DeepSeek v3 运行暴露出真实问题：在 SQLite 并发提交下，`alignment_edges` 可能早于父级对象稳定落库，导致外键失败
- 同一轮真实运行已经生成了有价值的 progress database、usage 数据和部分真实译文；如果每次修复后都整书重来，不仅浪费成本，也会破坏长任务可恢复性验证
- echo 并发 smoke 已证明，只要插入顺序收稳，per-packet commit + parallel workers 可以稳定推进到数百 packet 而不触发此前的外键错误

Consequences:

- 并发真实翻译的持久化路径现在以“父对象先落库、子对象后落库”为硬约束，避免把 SQLite 并发行为交给 ORM 隐式推断
- 并发持久化修复后，完整本地回归与 Docker + PostgreSQL 集成已再次确认通过
- DeepSeek 整本 v3 任务现在按“原进度库恢复 + 继续推进”的方式运行，真实 usage / latency / 估算 cost 都会在同一条长任务轨迹里继续积累

---

## D-073 Export Records Must Snapshot The Full Translation Usage View, Not Just Summary Totals

Status: Accepted

Decision:

- `Export.input_version_bundle_json` 不再只保存 `translation_usage_summary + translation_usage_breakdown`
- 导出时还必须冻结 `translation_usage_timeline + translation_usage_highlights`
- `GET /documents/{id}/exports` 的每条 record，以及 `GET /documents/{id}/exports/{export_id}`，都优先读取这份 export-time usage snapshot，而不是只从当前 live translation runs 临时聚合

Why:

- 现在真实 provider 已经开始把 `cost_usd / token / latency` 持久化进 `translation_runs`，仅靠 summary totals 已不足以支持后续复盘和成本判断
- dashboard 的 live 视图已经有 summary / breakdown / timeline / highlights 四层，如果 export snapshot 只冻结前两层，export detail 会天然比 live dashboard 丢信息
- 导出记录本质上是“当时交付状态”的证据；usage 视图如果不完整冻结，后续 rerun、补跑或 provider 切换后就难以准确复盘导出当时的成本 / 吞吐形态

Consequences:

- export detail 现在可以稳定返回导出当时的完整 usage 视图，包括 timeline 和 highlights
- dashboard records 现在也能直接展示每次导出的 usage trend / highlights，而不需要调用方二次拼装
- 后续如果继续扩 usage 维度，应优先保持“live dashboard 与 export snapshot 使用同一层 usage contract”，避免 record/detail 再次落后于 dashboard

---

## D-074 Provider-Backed Translation Prompts Must Use Short Sentence Aliases, And Persistence Must Treat Provider References As Untrusted

Status: Accepted

Decision:

- provider-backed translation prompt 不再直接把真实 sentence UUID 暴露给模型，而是统一使用短别名 `S1 / S2 / ...`
- worker 收到 provider 输出后，必须先把 alias 映射回真实 sentence IDs，再交给 persistence
- persistence 在写 `alignment_edges` 前，还必须再次过滤掉不存在的 sentence IDs 和 unknown target temp IDs，把 provider 返回的引用视为不可信输入

Why:

- 真实 DeepSeek 整本长跑暴露出新的故障模式：provider 返回的 `source_sentence_ids` 会出现截断或损坏，导致 `alignment_edges` 插入时命中外键失败
- 这类问题不是翻译质量问题，而是“让模型直接复制长 UUID”带来的协议脆弱性；继续把长 UUID 交给模型，相当于把稳定主键放进最不可靠的通道
- 仅在持久化层修插入顺序不够，因为 provider 仍然可能返回坏引用；必须同时收紧 prompt contract 和 persistence guardrail

Consequences:

- provider-backed live run 现在使用更短、更易稳定复制的 sentence alias，降低长任务下引用损坏概率
- 即使 provider 仍返回坏 sentence ID 或 bad temp ID，系统现在也会降级成“缺失对齐、后续 QA 抓出”，而不是直接崩整个 packet / batch
- 短程 DeepSeek live smoke 已确认新协议下连续两个 batch 可以稳定提交，未再复现此前那种坏 ID 外键崩溃

---

## D-075 Long-Running Translation Jobs Must Use A Dedicated Run Control Plane

Status: Accepted

Decision:

- 长任务运行控制从现在开始视为独立系统层，不再继续堆叠在 CLI runner / workflow service 内部
- 当前阶段的最佳实现路线是：
  - 保留现有 artifact / workflow / rerun 语义
  - 在现有 Postgres 状态机之上新增 `document_run / work_item / worker_lease / run_budget / run_audit_event`
  - 让 `run_real_book_live.py` 逐步退化成薄控制入口，而不是主调度器

Why:

- 我们的真实任务已经进入小时级 live run、并发 packet 执行、provider 限流/坏引用、人工中断恢复的复杂度区间
- 继续依赖脚本进程和 JSON 报告文件，无法稳定承载 pause/resume/drain、lease reclaim、budget guardrails 和统一审计
- 直接迁移到更重的 durable workflow 平台虽然长期有吸引力，但当前返工面过大，不是最优第一步

Consequences:

- 后续的长任务能力建设会优先围绕 control plane 展开，而不是继续给 runner 脚本叠功能
- 运行控制、预算、心跳、恢复和告警会成为一等对象，而不是散落在 workflow service 里的隐式逻辑
- 后续如果再引入 Temporal-style orchestration，也会建立在这层显式 control plane 语义已经稳定的前提上

---

## D-076 Phase-A Run Control Must Start With Durable Data Models Before Adding Pause/Resume APIs

Status: Accepted

Decision:

- 长任务控制的第一阶段先落数据层，而不是先做控制 API 或调度逻辑
- 第一阶段的最小持久化对象固定为：
  - `document_runs`
  - `work_items`
  - `worker_leases`
  - `run_budgets`
  - `run_audit_events`
- 这些对象先进入 ORM、DDL、Alembic 和回归，再以它们为基底往上接 `pause / resume / cancel / drain / run summary / run events`

Why:

- 如果没有 durable data model，后续所有长任务控制动作都会继续依赖内存状态、脚本状态和临时 JSON 报告，无法形成真正的控制平面
- 先把 schema 钉住，后续 pause/resume/drain、lease reclaim、budget guardrail 才能建立在稳定对象之上，而不会每走一步都反复改库
- 当前项目已经有大量 artifact/state 语义，最稳的推进方式是“先补控制对象，再接控制行为”，而不是一边加 API 一边临时发明状态

Consequences:

- 运行控制现在已经从文档概念进入正式 schema，后续实现可以直接围绕这些对象接仓储、服务和 API
- 这一阶段的验收口径从“有没有 pause 接口”调整成“控制平面对象是否 durable、是否可迁移、是否可回归验证”
- 后续第二阶段应优先实现最小控制 API 和 run summary/events 读取，而不是继续扩 runner 脚本参数

---

## D-077 Phase-B Run Control Should Stabilize Run-Level Control Before Lease Recovery

Status: Accepted

Decision:

- Run Control Plane 第二阶段先只实现 `run` 级控制面 API
- 当前控制面固定提供：
  - `create`
  - `summary`
  - `events`
  - `pause`
  - `resume`
  - `drain`
  - `cancel`
- 暂不在这一轮把 `work item claim / heartbeat / scheduler dispatch / lease expiry recovery` 一起接入

Why:

- 先把 durable control object 的读写、状态迁移、预算快照和审计事件做成稳定 API，风险最低、收益最高
- 如果 run-level 语义还没稳定，就同时接 lease recovery 和 dispatcher，会把调试面、状态迁移面和故障面混在一起
- 当前项目最需要的不是更多 runner 脚本参数，而是一个可靠、可观察、可控制的 run-level 基线

Consequences:

- 现在 `/v1/runs` 已足够支撑人工创建 run、读取 summary、查看事件和做基本控制动作
- 下一阶段应优先补 `work item lease` 的 claim / heartbeat / expiry recovery，而不是继续扩更多 run-level 只读视图

---

## D-078 Real-Book Long Runs Must Execute Through DocumentRun And WorkItem Control, Not Batch Loops

Status: Accepted

Decision:

- `scripts/run_real_book_live.py` 不再使用旧的 batch 直跑主循环
- 真实书籍 live rerun 统一通过 `document_run + work_item + worker_lease + run_budget + run_audit_event` 执行
- translate runner 现在固定采用：
  - `seed work_items`
  - `claim -> start -> heartbeat -> complete`
  - `expiry reclaim`
  - `budget guardrails`
  - `terminal reconcile`
  - `Ctrl-C -> pause`

Why:

- 真正的小时级整书长跑已经不适合继续依赖 batch for-loop 和临时 JSON 报告作为主要控制语义
- 我们已经在真实 DeepSeek 长跑里遇到过中断、坏引用、长请求、并发持久化和恢复点问题；这些都需要 durable run control，而不是再给脚本堆 if/else
- 只有把 runner 退化成 control plane 的薄入口，pause/resume/reclaim/budget 才能成为系统能力，而不是单次进程行为

Consequences:

- 真实长跑现在可以用 `run_id` 恢复，而不是重新发起一轮新的“脚本态任务”
- Ctrl-C 不再只留下长栈和 report，而会把 run 显式暂停，保留可恢复状态
- 当前阶段可以把“translate_full 长任务控制”视为已经进入可运行、可恢复、可审计状态；后续优化会集中在 provider lane / circuit breaker / adaptive concurrency，而不是回退到旧 batch loop

---

## D-079 Review Must Exempt Zero-Sentence Frontmatter Title Gaps And Source-Literal Tags

Status: Accepted

Decision:

- chapter-level `CONTEXT_FAILURE` 仍然由 active chapter brief 的 `open_questions` 触发
- 但当章节没有任何可翻译句子、也没有 packet，且唯一 open question 是 `missing_chapter_title` 时，不再生成阻断性的 chapter-level issue
- `FORMAT_POLLUTION` 继续检测目标中的 HTML-like tag / fence / doctype 污染
- 但当目标中的 tag token 与源句中原样出现的字面 tag 完全一致时，不再视为污染

Why:

- 真实书《Build an AI Agent (From Scratch)》的 `v4` 结果表明，这两类情况都会把最终 `bilingual_html` 无意义地拦住
- frontmatter 空章节缺标题并不构成真实的翻译上下文失败，把它升级成 blocking issue 会让零正文章节错误地卡死整书交付
- 技术书正文会原样讨论模型思维标记或其他字面标签，例如 `<think>`；这属于合法内容，不应被当成 provider 泄漏格式污染

Consequences:

- 真实书 `v4` 在不重跑翻译的前提下，通过重新 review 即可把两个 open issue 自动 resolve
- 最终 `bilingual_html` 现在可以顺利导出，document 状态进入 `exported`
- 后续如果要继续增强 `FORMAT_POLLUTION`，应优先做更细的 allowlist / evidence explainability，而不是恢复“见到 tag 就报错”的宽规则

---

## D-080 Merged Export Must Render Protected Artifacts As Intentional Source-Only Blocks

Status: Accepted

Decision:

- 整本 merged export 不得把 `protected` / `source-only` 内容渲染成“空的中文翻译位”
- 对于 code / command / config / literal tag / table / equation / reference 这类内容，merged export 必须基于 render mode 渲染，而不是基于“有没有中文 target”做二元判断
- 默认至少支持以下 render modes：
  - `zh_primary_with_optional_source`
  - `zh_primary_with_inline_protected_spans`
  - `source_artifact_full_width`
  - `translated_wrapper_with_preserved_artifact`
  - `image_anchor_with_translated_caption`
  - `reference_preserve_with_translated_label`

Why:

- 当前 chapter-level bilingual HTML 是 QA 视图，允许 protected block 在目标侧为空；但整本 merged export 是阅读交付视图，不能把合法保留的原文工件误表现成“翻译缺失”
- 真实书里已经出现 code block 和字面标签场景，这些都证明“不是每个 block 都需要一个中文 body”
- 如果 merged export 继续沿用 QA 双列表格心智，代码、命令、prompt、表格、公式都会被错误地表现成空洞或漏译

Consequences:

- merged export 的实现入口必须先生成 block-level render contract，而不是直接拼接 target text
- export gate 也必须区分：
  - `expected source-only artifact`
  - `unexpected missing translation`
- 整本合并导出会优先追求“阅读质量 + copy/paste 安全 + 结构保真”，而不是机械地为每个 block 填一个中文格子
- 零内容、无标题的 frontmatter 章节在 merged export 中必须跳过；阅读态不得退回到用 `chapter_id`/UUID 充当章节标题
- merged export 的默认排版目标是“技术书长文阅读”，不是 QA 表格，因此必须包含：
  - 目录或章节导航
  - 明确的正文 / 工件 / 原文层级
  - 移动端可读与 print 基础可用

---

## D-081 P0 Frontend Entry Should Be A FastAPI-Served Operator Homepage

Status: Accepted

Decision:

- P0 不额外引入独立 SPA 或前端构建链路
- 先由 FastAPI 直接在 `/` 提供一个产品级 operator-facing 首页
- 首页定位为“控制室入口”，负责统一呈现 workflow、review/export surface、run control 和 API quick access

Why:

- 项目已经具备复杂而真实的系统能力，但此前仍然只有 API 裸入口，产品感和可发现性明显不足
- 现阶段最需要的是一个可信、现代、专业的前台入口，而不是立即引入新的前端工程复杂度
- 直接复用 FastAPI 提供首页，可以让页面词汇、信息架构和后端真实 contract 保持一致，避免 UI 与系统状态脱节

Consequences:

- 首页设计必须优先服务 operator 认知，而不是做通用 SaaS 营销页
- 页面视觉方向应贴近“技术书翻译控制室”，而不是默认紫色模板或通用 dashboard 套皮
- 后续若继续扩展为 document workspace / run console / chapter board，应沿用同一条术语和交互脉络，而不是推倒重来
- 首页可以在不引入新前端工程的前提下继续变成轻量控制台，但仍应避免在 P0 过早演化成重型 SPA

---

## D-082 Operator Console Should Reuse Existing Worklist Filters And Expose Live Freshness In-Page

Status: Accepted

Decision:

- operator console 不新增独立前端聚合端点来做 chapter queue 过滤
- worklist 过滤必须直接复用现有 `GET /v1/documents/{document_id}/chapters/worklist` 的 query filters
- 首页必须显式暴露 `document / run / worklist` 三条 live freshness 信号，至少区分：
  - `idle`
  - `refreshing`
  - `live`
  - `stale`

Why:

- 目前系统已经有成熟的 worklist/filter contract，再造一套前端专用过滤协议只会制造第二套口径
- 真实 operator 场景里，最常见的问题不是“没有数据”，而是不知道当前页面是不是新鲜的、筛选是不是 live queue 的真实结果
- 轻量控制台要变成值班台，首先要让操作者知道：
  - 当前看到的是哪一层筛选结果
  - 数据是不是刚同步过
  - 哪些 owner / chapter 可以直接点进去处理

Consequences:

- owner workload lane 可以直接作为 filter launcher，而不是独立报表
- worklist filter summary 必须明确展示当前 active filters 和 filtered/total queue counts
- document/run/worklist 的 auto-refresh 不能只是按钮文案，必须在页面上持续显示 freshness / stale 状态
- 后续如果再扩展 owner drill-down 或 alerts，应继续建立在同一套 worklist/filter/freshness contract 上

---

## D-083 Owner Workload UI Should Stay Derived From Existing Worklist Summary, Not A Separate Owner API

Status: Accepted

Decision:

- P0 的 owner drill-down 不新增独立 owner API
- owner lane、owner drill-down、balancing hints 必须直接派生自现有：
  - `owner_workload_summary`
  - `owner_workload_highlights`
  - `worklist.entries`
- owner card 点击后优先通过现有 worklist filters 聚焦到对应 owner，而不是跳到新的 owner route

Why:

- 现有后端已经有稳定的 chapter queue / owner summary contract，再做单独 owner aggregation 只会制造第二套口径
- 当前最需要的是“更快地分流和重平衡 workload”，不是扩出新的 reporting API 面
- owner drill-down 只要能回答三件事就足够高价值：
  - 这个 owner 现在扛了多少章
  - 其中哪些章最急
  - 有没有明显需要 rebalance 的信号

Consequences:

- owner drill-down 和 balancing hints 会保持为当前 queue 的 live 派生视图，而不是独立 snapshot
- owner focus 必须和 worklist filters 同步，避免“owner 卡片看的是一套口径、queue 看的是另一套口径”
- 如果后续真的需要 owner alerts / balancing automation，应优先沿用当前 owner summary + worklist contract，再决定是否需要专门 owner API

---

## D-084 Owner Alerts In The Operator Console Should Be Derived, Clickable, And Non-Blocking

Status: Accepted

Decision:

- operator console 中的 owner alerts 不单独持久化，也不引入新的 alert API
- owner alerts 必须直接由现有 `owner_workload_summary / owner_workload_highlights / worklist.entries` 派生
- alert 的主要职责是：
  - 提醒当前 visible queue 中的 breached owner load
  - 提醒 unassigned immediate pressure
  - 提醒明显的 owner imbalance
- alert 必须可点击，并直接把用户带到已有的 owner/worklist filter 视图
- P0 中 owner alerts 只作为 routing cue，不升级成 blocking signal

Why:

- 这类 alert 的价值在于“快读 + 快分流”，而不是建立第三套工单/告警系统
- 当前系统已经有完整的 chapter queue 和 owner summary；新增独立 owner alert backend 会过早增加运营模型复杂度
- 对 P0 来说，最重要的是让 operator 能从首页直接知道：
  - 谁现在最危险
  - 哪些 urgent chapter 还没有 owner
  - 当前是否值得做 rebalance

Consequences:

- owner alerts 的口径必须随当前 queue filters 一起变化，它是 live routing 层，而不是全局快照层
- alert 点击后必须复用现有 `assigned_owner_name / assigned / queue_priority / sla_status` filters
- 如果后续要升级成真正的 alerting/notification 系统，应在当前 derived alerts 之上逐步外化，而不是推翻现有 UI contract

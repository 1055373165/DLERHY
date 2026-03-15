# Project Status

Last Updated: 2026-03-15

## Current Phase

P0 hardened on real EPUBs; ready to enter P1 text-PDF support

目标范围：

- EPUB-only
- 英文非虚构 / 技术 / 商业书
- 产出可审校高质量中文初稿

当前项目状态：

- 设计基线已建立
- 实施级文档已建立
- 工程骨架已启动
- 数据层、应用入口和 orchestrator 骨架已落第一版
- 最小 EPUB ingest / parse / segment / packet 主链路已打通
- 第一版 Alembic migration 已落地
- SQLite 持久化测试已打通
- translation run / target segment / alignment edge 最小落库路径已打通
- 第一版 QA issue / action / rerun plan 最小闭环已打通
- 第一版 artifact invalidation 已打通
- review package 和 bilingual HTML 最小导出路径已打通
- bootstrap / translate / review / export 已接入 FastAPI workflow 入口
- document 级 export gate 和 chapter/document 状态推进已打通
- API 级工作流回归测试已打通
- translation worker 已抽成可替换抽象，保留 provider 接入位
- provider-specific `openai_compatible` TranslationModelClient 已接入 factory，可在不改 workflow 的前提下切到真实结构化模型调用
- 最小 CLI / admin commands 已打通
- action -> rerun -> re-review 闭环已打通
- review 现在会自动关闭最新 QA 中已消失的旧 issue
- low confidence QA 已打通
- format pollution QA 已打通
- packet 级 `CONTEXT_FAILURE` QA 已打通，会基于 packet `open_questions` 自动触发 `REBUILD_PACKET_THEN_RERUN`
- chapter-brief 级 `CONTEXT_FAILURE` QA 已打通，会基于 active chapter brief 的 `open_questions` 自动触发 `REBUILD_CHAPTER_BRIEF`
- 高置信 `DUPLICATION` QA 已打通，会检测相邻目标段完全重复且源句不同的 packet 级重复翻译
- 保守版 `ALIGNMENT_FAILURE` QA 已打通，会检测“最新翻译输出可恢复，但当前 active alignment 缺失”的 packet 级对齐失败
- `ALIGNMENT_FAILURE` 现在也会检测 orphan target segment：目标段仍存在，但 active alignment edge 已丢失
- chapter quality summary 已接入 review API / CLI 输出
- chapter quality summary 已持久化到数据库，并可通过 `GET /documents/{id}` 读取
- Docker / Compose 本地联调基线已建立
- ORM 枚举已统一按 `.value` 持久化，避免与 PostgreSQL check constraint 偏移
- SQLite 测试环境已强制开启 foreign key 校验
- Docker + PostgreSQL 真实主链路 smoke 已跑通：`bootstrap -> translate -> review -> export`
- targeted rebuild 已打通：`UPDATE_TERMBASE_THEN_RERUN_TARGETED` 现在会重建 termbase snapshot、重建受影响 packet，并在 follow-up rerun 中生效
- PostgreSQL 专项集成回归已落地，并可通过 Docker 脚本一键执行
- `REBUILD_CHAPTER_BRIEF` 已打通，并通过本地 API 回归和 PostgreSQL 集成回归验证 chapter brief version、packet version 和 snapshot status
- action follow-up 输出现在带结构化 rebuild evidence，包括 snapshot type 和 version
- `REALIGN_ONLY` 已打通：packet 级 action 不再误做 invalidation，而会基于 latest translation run output 重建 alignment edges 并自动复检
- review package 已升级为证据型工件，包含 quality summary、active snapshot versions、packet context versions 和 recent repair events
- bilingual HTML 已升级为双工件导出：主 HTML + sidecar manifest，manifest 中包含 quality summary、version evidence、recent repair events 和 row summary
- export artifacts 现在会显式写出 `export_time_misalignment_evidence`，可以捕获 review 之后、export 之前发生的 alignment 破坏
- final bilingual export gate 现在会拦截 `export_time_misalignment_evidence` 中的保守异常；review package 仍然保留为可导出的诊断工件
- export-time misalignment 现在会落成正式的 `ReviewIssue / IssueAction`，并复用现有 `REALIGN_ONLY` 修复链路
- API 级 409 export gate 响应现在会返回结构化 follow-up hints，包括 `issue_ids`、`action_ids` 和可执行 scope 信息
- document export 现在支持 opt-in `auto_execute_followup_on_gate`：当 export gate 命中 export-origin follow-up action 时，可在同一次请求内自动执行 action、follow-up rerun/review 并重试导出
- 成功的自动修复导出现在会返回结构化 `auto_followup_executions`，显式给出执行过的 action、rerun scope、重译 packet、translation run 和 issue 是否已解决
- export auto-followup 现在支持显式 `max_auto_followup_attempts`，并会返回 `auto_followup_attempt_count / auto_followup_attempt_limit`
- 当 export auto-followup 因 safety cap 或无新 action 可执行而停止时，409 detail 现在会带 `auto_followup_stop_reason` 和已执行的 auto-followup 摘要
- export auto-followup telemetry 现在会持久化成 chapter 级 audit event，并通过 review package / bilingual manifest 的 `export_auto_followup_evidence` 暴露
- `Export.input_version_bundle_json` 现在也会持久化 `export_auto_followup_summary`，为后续 dashboard / admin API 直接消费提供聚合入口
- document API 现在支持 `GET /documents/{id}/exports`，可直接返回 export history、per-type count、latest export IDs 和每条 export record 的 auto-followup summary
- `GET /documents/{id}/exports` 现在支持 `export_type / status / limit / offset`，并明确区分“全量 dashboard summary”和“当前 records 窗口”
- export record 现在会持久化 `translation_usage_summary` 和 `issue_status_summary`，为 export detail / admin 排查提供快照化证据
- document API 现在支持 `GET /documents/{id}/exports/{export_id}`，可直接读取 export detail、translation usage、issue status 和 version evidence
- export dashboard 现在会返回 document 级 `translation_usage_summary`，把 translation cost / latency / token 使用纳入 export/admin 查询面
- export dashboard / detail 现在也会返回 `translation_usage_breakdown`，按 `model_name + worker_name (+ provider)` 聚合 usage，便于识别混合 rerun / provider 路径
- export dashboard 现在也会返回按天聚合的 `translation_usage_timeline`，把 usage 可观测性从总量扩展到轻量趋势层
- export dashboard 现在也会返回 `translation_usage_highlights`，直接指出 top-cost / top-latency / top-volume 调用类型
- export record snapshot 现在也会冻结 `translation_usage_timeline / translation_usage_highlights`，让 export detail 能按导出时刻复盘完整 usage 视图，而不是只依赖 live dashboard
- export dashboard 现在也会返回 `issue_hotspots`，按 `issue_type + root_cause_layer` 聚合问题热点，直接暴露 open / resolved / blocking 压力
- export dashboard 现在也会返回 `issue_chapter_pressure`，直接指出问题最集中的章节及其 open / blocking 压力
- export dashboard 现在也会返回 `issue_chapter_highlights`，直接指出 top-open / top-blocking / top-resolved chapter，方便 chapter 级运营判断
- export dashboard 现在也会返回 `issue_chapter_breakdown`，按 `chapter + issue_type + root_cause_layer` 下钻 chapter 压力来源
- export dashboard 现在也会返回 `issue_chapter_heatmap`，基于 active open/triaged/blocking 压力给出当前 chapter heat 和 dominant issue family
- export dashboard 现在也会返回 `issue_chapter_queue`，把 chapter heat 收敛成可执行队列，直接给出 `queue_rank / queue_priority / queue_driver`、轻量 `regression_hint / flapping_hint`，以及 worklist 字段 `oldest_active_issue_at / age_hours / age_bucket / sla_target_hours / sla_status / owner_ready`
- document API 现在也会返回独立的 `GET /documents/{id}/chapters/worklist`，用更轻的 ops worklist 视图暴露同一套 queue 契约，并支持 `queue_priority / sla_status / owner_ready / needs_immediate_attention / limit / offset`
- 独立 chapter worklist API 现在也会返回 `highlights`，直接给出 `top_breached_entry / top_due_soon_entry / top_oldest_entry / top_immediate_entry`
- document API 现在也会返回独立的 `GET /documents/{id}/chapters/{chapter_id}/worklist`，暴露单章 `queue_entry`、live issue pressure、recent issues/actions 和 persisted quality summary
- chapter worklist 现在支持持久化 owner assignment，并会在 queue / detail 视图中返回 `is_assigned / assigned_owner_name / assigned_at`
- document API 现在支持 `PUT /documents/{id}/chapters/{chapter_id}/worklist/assignment` 和 `POST /documents/{id}/chapters/{chapter_id}/worklist/assignment/clear`
- 独立 chapter worklist API 现在支持 `assigned / assigned_owner_name` 过滤，并返回 `assigned_count`
- 独立 chapter worklist API 现在也会返回 `owner_workload_summary`，按 owner 聚合当前 actionable chapter 的 queue / SLA / open-blocking 压力
- 独立 chapter worklist API 现在也会返回 `owner_workload_highlights`，直接指出 top-loaded / top-breached / top-blocking / top-immediate owner
- chapter worklist detail 现在也会返回 `assignment_history`，按时间倒序暴露 assignment set/cleared 审计记录
- export dashboard 现在也会返回 `issue_activity_timeline`，按天展示 issue 的 created / resolved / wontfix / net delta / estimated open 趋势
- export dashboard 现在也会返回 `issue_activity_breakdown`，按 `issue_type + root_cause_layer` 下钻 issue 时间趋势
- export dashboard 现在也会返回 `issue_activity_highlights`，直接指出 top-regressing / top-resolving / top-blocking 问题族

## Completed

- 完成长文档翻译 Agent 的系统设计主文档
- 明确 EPUB / PDF 差异化处理策略
- 明确“以段为翻译窗口、以句为对齐粒度、以章节为术语与风格约束范围”
- 定义 Translation Unit / Context Packet / QA & Review 三类核心契约
- 补齐 Error Taxonomy
- 补齐 Rerun Policy
- 补齐 Issue -> Rerun Action Matrix
- 补齐 P0 Database DDL 草案
- 补齐 Orchestrator State Machine 草案
- 初始化 Python 项目骨架与 `pyproject.toml`
- 建立 FastAPI 应用入口和基础 API
- 建立 SQLAlchemy ORM 模型骨架
- 建立 Alembic 基础目录
- 建立 orchestrator 状态机和规则引擎占位
- 实现最小 EPUB parser（spine / metadata / block extraction）
- 实现最小英文句切分器
- 建立基于 `unittest` 的 parser / segmentation 基础回归测试
- 建立稳定 ID 生成策略
- 建立 bootstrap pipeline，把 ingest / parse / segment / profile / brief / packet 连成首条主链路
- 实现 book profile / chapter brief / context packet builder
- 补第一版 Alembic migration
- 通过离线 Alembic SQL 生成验证 migration 可执行
- 引入 repository / persistence 层
- 用 SQLite 验证 bootstrap artifacts 可入库和可回读
- 实现 Echo Translation Worker 和 translation persistence
- 实现第一版 chapter review service，可生成 issue / action / rerun plan
- 实现 issue action executor，可生成 invalidation 记录
- 实现最小 review package / bilingual HTML 导出
- 持久化、translation、review、invalidation、export 全链路已通过本地回归测试
- 建立 document workflow service，串起 bootstrap / translate / review / export
- 建立 FastAPI session 依赖和 workflow API
- 建立 action execute API
- 建立 export gate，阻止带 blocking issue 的 final export
- 为 API 主链路补充端到端回归测试
- 建立 translation worker metadata / prompt / client abstraction
- 建立最小 CLI，支持 bootstrap / summary / translate / review / export / execute-action
- 为 CLI 和 LLM worker abstraction 补充回归测试
- 建立 rerun service，支持 packet/chapter 级 follow-up rerun
- action API 已支持 `run_followup=true`
- review 流程已支持自动 resolve 缺失 issue
- 为 rerun + recheck 闭环补充 API 回归测试
- 建立 low confidence 和 format pollution QA 规则
- review summary 已输出 coverage / alignment / term / format / low-confidence 指标
- 为新增 QA 规则补充 API 回归测试
- 新增 Dockerfile、compose 和本地 PostgreSQL 容器化联调文档
- 已通过 `docker compose config` 静态校验
- 已通过 Dockerized PostgreSQL 运行态验证：容器启动、migration、bootstrap、translate、review、export
- 已修复 Bootstrap / Translation / Review 三层持久化顺序在真实外键约束下的落库问题
- 已实现 in-place targeted rebuild，并通过真实 PostgreSQL 验证“锁定术语更新 -> packet 重建 -> rerun 闭环解决 issue”
- 已新增 PostgreSQL 集成测试模块和 `scripts/run_postgres_integration.sh`，覆盖 happy path 与 targeted rebuild
- 已新增 provider adapter contract 回归，覆盖 `openai_compatible` payload 构建、结构化响应解析、factory 装配和缺失密钥报错
- 已新增 chapter quality summary 持久化对象、Alembic migration、SQLite 回归和 PostgreSQL 集成验证
- 已将 `CONTEXT_FAILURE` / `STYLE_DRIFT` 路由对齐到 `REBUILD_CHAPTER_BRIEF`
- 已新增 `REBUILD_CHAPTER_BRIEF` 的本地 API 回归、规则路由回归和 PostgreSQL 集成回归
- 已新增 packet/chapter-brief 级 `CONTEXT_FAILURE` QA 规则和 SQLite 回归，routing 与 issue-rerun matrix 已重新对齐
- 已新增高置信 `DUPLICATION` QA 规则和 SQLite 回归，并将 packet/export 双路由与 rule engine 对齐
- 已新增保守版 `ALIGNMENT_FAILURE` QA 规则、本地 API 回归、SQLite 回归和 PostgreSQL 集成回归
- 已打通 `ALIGNMENT_FAILURE -> REALIGN_ONLY -> re-review -> issue resolved` 闭环
- 已补 orphan target segment 子场景回归，覆盖“句子仍有对齐，但其中一个目标段失联”的漏网情形
- 已将 quality summary、active snapshot versions 和 recent repair events 写入 review package，并通过本地 API / PostgreSQL 集成回归验证
- 已为 bilingual HTML 增加 sidecar manifest，并通过本地 API / PostgreSQL 集成回归验证 manifest evidence
- 已为 review package / bilingual manifest 增加 `export_time_misalignment_evidence`，并通过本地 API / PostgreSQL 集成回归验证 post-review corruption 场景
- 已将 export-time misalignment evidence 接入 final export gate，并验证“final export blocked + review package still diagnostic”闭环
- 已将 export-time misalignment 同步成正式 issue/action，并验证 API 409 场景下 issue/action 仍可持久化和后续修复
- 已让 export gate 的 409 直接返回 follow-up action hints，避免上层必须二次查询数据库才能继续修复
- 已让 export gate 支持 opt-in auto-followup repair，并通过本地 API 回归和 PostgreSQL 集成回归验证“自动执行 REALIGN_ONLY + rerun/review + export 成功”闭环
- 已为 export auto-followup 增加 attempt telemetry 和 safety cap，并通过本地 API / PostgreSQL 集成回归验证“同轮多个 action 不会绕过上限”
- 已将 export auto-followup telemetry 写入独立 audit / manifest / review package 证据，并验证成功路径与 attempt-limit 停止路径
- 已将 export auto-followup summary 汇总进 `Export` 持久化记录，并通过本地 API / PostgreSQL 集成回归验证成功路径与停止路径的聚合字段
- 已新增 export history / dashboard API，并通过本地 API / PostgreSQL 集成回归验证 export records、per-type 聚合和 auto-followup summary 可读
- 已为 export history / dashboard API 增加分页与筛选，并通过本地 API / PostgreSQL 集成回归验证全量统计不受过滤影响、records 窗口可分页
- 已将 translation usage / issue status snapshot 写入 export record，并通过本地 API / PostgreSQL 集成回归验证 dashboard 和 export detail 都能稳定读取
- 已新增 export detail API，并通过本地 API / PostgreSQL 集成回归验证 persisted usage、issue summary、misalignment counts 和 version evidence 可读
- 已为 dashboard / export detail 增加 per-model / per-worker usage breakdown，并通过本地 API / PostgreSQL 集成回归验证 breakdown 可读
- 已为 export dashboard 增加 daily translation usage timeline，并通过本地 API / PostgreSQL 集成回归验证 timeline 可读
- 已为 export dashboard 增加 usage highlights，并通过本地 API / PostgreSQL 集成回归验证 highlights 可读
- 已为 export dashboard 增加 issue hotspots，并通过本地 API / PostgreSQL 集成回归验证“无 issue 时为空、有 export misalignment 时可稳定聚合”
- 已为 export dashboard 增加 issue chapter pressure，并通过本地 API / PostgreSQL 集成回归验证“无 issue 时为空、有 export misalignment 时可定位到具体 chapter”
- 已为 export dashboard 增加 issue chapter highlights，并通过本地 API / PostgreSQL 集成回归验证未修复与 auto-followup 修复后 top-open / top-blocking / top-resolved chapter 指向稳定
- 已为 export dashboard 增加 issue chapter breakdown，并通过本地 API / PostgreSQL 集成回归验证 chapter + issue family 下钻口径稳定
- 已为 export dashboard 增加 issue chapter heatmap，并通过本地 API / PostgreSQL 集成回归验证未修复场景 heat 升高、auto-followup 修复后 active heat 归零
- 已为 export dashboard 增加 issue chapter queue，并通过本地 API / PostgreSQL 集成回归验证未修复场景进入 actionable queue、auto-followup 修复后从 queue 中移除
- 已为 issue chapter queue 增加 regression/flapping hints，并通过本地 API / PostgreSQL 集成回归验证单日新增问题会标成 `regressing` 且不会误报 `flapping`
- 已为 issue chapter queue 增加 worklist 字段 `oldest_active_issue_at / age_hours / age_bucket / sla_target_hours / sla_status / owner_ready`，并通过本地 API / PostgreSQL 集成回归验证未修复场景的 SLA / age / owner-ready 元数据可读、修复后 queue 为空
- 已新增独立 chapter worklist API，并通过本地 API / PostgreSQL 集成回归验证过滤、分页和 summary counts 与既有 queue 口径一致
- 已为独立 chapter worklist API 增加 `highlights`，并通过本地 API / PostgreSQL 集成回归验证 breached SLA 场景下的 `top_breached / top_oldest / top_immediate` 指向稳定
- 已新增独立 chapter worklist detail API，并通过本地 API / PostgreSQL 集成回归验证单章 `queue_entry / issue family breakdown / recent issues/actions / quality summary` 可读
- 已新增 chapter worklist assignment 持久化模型、Alembic migration 和 assign/clear API，并通过本地 API / PostgreSQL 集成回归验证 assign -> detail/filter -> clear 闭环
- 已为 chapter worklist 增加 `owner_workload_summary`，并通过本地 API / PostgreSQL 集成回归验证 assign 后 workload 聚合可读、clear 后 summary 清空
- 已为 chapter worklist 增加 `owner_workload_highlights`，并通过本地 API / PostgreSQL 集成回归验证无 assignment 时为 null、assign 后 owner 卡片可读、clear 后恢复为空
- 已为 chapter worklist detail 增加 `assignment_history`，并通过本地 API / PostgreSQL 集成回归验证 assign 后出现 set 记录、clear 后按倒序出现 cleared -> set
- 已为 export dashboard 增加 issue activity timeline，并通过本地 API / PostgreSQL 集成回归验证“未修复问题产生净新增、auto-followup 修复后同日净增归零”
- 已为 export dashboard 增加 issue activity breakdown，并通过本地 API / PostgreSQL 集成回归验证同一问题族的 created/resolved 时间趋势可独立读取
- 已为 export dashboard 增加 issue activity highlights，并通过本地 API / PostgreSQL 集成回归验证未修复与已修复场景下的回归/修复/阻断问题族指向稳定
- 已为 EPUB parser 增加“XML 优先、HTML 容错回退”解析路径，并通过命名实体与 malformed HTML 回归验证真实书籍兼容性
- 已将零 packet chapter 纳入统一 review 流程，避免 frontmatter / 空章节长期停留在 `packet_built` 导致整书 export gate 失败
- 已修复 `0 sentence + chapter brief open_questions` 场景下的 review 外键异常：该类 issue 现在会作为 chapter-level issue 持久化，`sentence_id = null`
- 已用真实 EPUB《Build an AI Agent (From Scratch)》完成隔离环境 smoke：bootstrap / translate / review / review package export 跑通；当前 final bilingual export 仍会被真实 review findings 阻断
- 已让 runtime 同时兼容 `BOOK_AGENT_TRANSLATION_OPENAI_API_KEY` / `OPENAI_API_KEY` 与 `BOOK_AGENT_TRANSLATION_OPENAI_BASE_URL` / `OPENAI_BASE_URL`
- 已为 `openai_compatible` provider 增加 `chat/completions` 兼容模式，支持 DeepSeek 这类 OpenAI-compatible 但未实现 Responses API 的服务
- 已完成 DeepSeek live smoke：3 个 packet 的协议验证通过，随后跨 6 个章节的代表样本翻译成功，真实译文样本已落盘
- 已新增 `scripts/run_real_book_live.py`，支持真实书籍 live rerun 的 batch-commit、增量进度打印和阶段性报告写盘
- 已使用真实密钥对《Build an AI Agent (From Scratch)》启动 DeepSeek 部分全量 live rerun，并成功提交首批 20 个 packet；真实质量与吞吐基线已落盘到 `artifacts/real-book-live/deepseek-full-run-v2/progress-report.json`
- `openai_compatible` provider 现在会把真实 `token_in / token_out / latency_ms` 解析并持久化到 `translation_runs`
- `scripts/run_real_book_live.py` 现在支持 `parallel_workers`，并能在中断时写回增量进度报告而不是只抛长栈
- 已用真实 DeepSeek 并发 smoke 验证：4-way 并发可稳定提交真实 packet，usage/latency 已成功写入数据库
- 《Build an AI Agent (From Scratch)》的并发整本 DeepSeek live rerun（v3）已启动，当前增量报告路径为 `artifacts/real-book-live/deepseek-full-run-v3/report.json`
- 已修复并发真实翻译下 `translation_runs -> target_segments -> alignment_edges` 的 SQLite 外键写入顺序问题，并用 echo 并发 smoke 验证可稳定推进数百 packet
- 已在并发持久化修复后重新执行完整验证：本地 `66` 个回归全绿，Docker + PostgreSQL `16` 个集成全绿
- 《Build an AI Agent (From Scratch)》的 DeepSeek 整本 v3 任务已从原进度库恢复并重新稳定推进；usage / latency / 估算 cost 已开始非零入库
- 已将 provider-backed prompt 的 `source_sentence_ids` 从原始长 UUID 收敛为 `S1/S2/...` 句别名，并在 worker 返回后映射回真实 sentence IDs
- 已为 provider 返回的坏 sentence IDs / bad temp IDs 增加持久化前过滤，避免 live run 因单个坏引用直接触发外键崩溃
- 已通过短程 DeepSeek live smoke 再次验证新协议：连续 2 个 batch / 16 个 packet 成功提交，最终由人工中断而非外键失败
- 已为长任务运行控制落下第一阶段数据层：`document_runs / work_items / worker_leases / run_budgets / run_audit_events`
- 已新增配套枚举、ORM 模型、Alembic migration 和 SQLite 持久化回归，运行控制对象现在已进入正式 schema，而不再只停留在设计文档
- 已为 Run Control Plane 落下最小控制面 API：`POST /v1/runs`、`GET /v1/runs/{run_id}`、`GET /v1/runs/{run_id}/events`、`POST /v1/runs/{run_id}/pause|resume|drain|cancel`
- Run summary 现在会聚合 budget、work item 状态、work item stage、worker lease 状态和 latest heartbeat / latest event
- 已通过本地 API 回归和 PostgreSQL 集成回归验证 `create -> pause/resume/drain/cancel -> summary/events` 这条控制面闭环
- 已新增 `RunExecutionService`，打通 `work item claim / start / heartbeat / release / expiry reclaim / terminal reconcile`
- 已补齐 Run Control Plane 的核心 budget guardrails：`wall-clock / total cost / token in / token out / consecutive failures`
- 已通过本地执行层回归验证 `success lifecycle / lease reclaim / cost guardrail / consecutive-failure guardrail`
- 已通过 PostgreSQL 集成回归验证 `RunExecutionService` 的 `claim -> start -> success -> terminal reconcile` 主链路
- 已将 `scripts/run_real_book_live.py` 迁移为 Run Control Plane 薄入口，真实长跑现在由 `document_run / work_item / worker_lease / run_budget / run_audit_event` 驱动
- `run_real_book_live.py` 现在支持 `--run-id` 恢复、Ctrl-C 优雅暂停、lease heartbeat、过期 lease reclaim、budget 熔断和持续写回 `run` 级进度报告
- 已完成一次新的 control-plane DeepSeek 真实 smoke：run 会在 `max_total_cost_usd` 命中后自动 `paused`，而不是依赖人工中断
- 项目现在已具备正式前端入口：FastAPI 会在 `/` 提供一个现代化 operator-facing 首页，不再只是 API 裸入口
- 新首页采用“技术书翻译控制室”的视觉与信息架构，直接暴露 workflow、review/export surface、run control 和 API quick access
- 新首页已通过独立前端回归和全量测试验证，确认不会影响现有 translation / review / export / worklist / run-control 主链路
- 首页已进一步升级为轻量控制台：现在可直接在 `/` 执行 document bootstrap/load、translate/review/export、run create/load/control，以及 chapter worklist board 刷新
- 当前前端仍保持“零额外前端工程、FastAPI 直出”的策略，但已经具备从产品入口页向 document workspace / run console / chapter board 演进的基础交互层
- 首页现在已支持 chapter detail drawer、owner assignment 操作和 run event auto-refresh，前端已从“轻量控制台”推进到“可下钻、可分派的 operator console”
- 首页现在也支持基于现有 worklist API 的 queue filters、owner-click filtering，以及 document/run/worklist 三条 live refresh / stale 状态指示，前端已开始具备值班台式使用体验
- 首页现在也支持 owner workload drill-down 和 balancing hints：owner lane 不再只是摘要卡片，而是可以聚焦单个 owner、查看该 owner 的可见章节压力，并获得轻量 rebalance 建议
- 首页现在也支持 owner alerts and routing cues：会直接把 breached owner、unassigned immediate queue、以及明显失衡的 owner load 提炼成可点击的操作提示
- 《Build an AI Agent (From Scratch)》的 DeepSeek `v4` 整本长跑已重新拉起，当前 run id 为 `95a071ed-041b-4cc0-8b7e-61cb01ed3653`
- 《Build an AI Agent (From Scratch)》的 DeepSeek `v4` 翻译长跑已完整跑完：`1802/1802` packet 成功，usage 为 `token_in=1,611,997 / token_out=494,921 / cost_usd=0.6180961 / latency_ms=15,925,168`
- 已基于 `v4` 真实书结果收敛两类交付阻断误报：
  - 零正文 frontmatter 章节在 chapter brief 只有 `missing_chapter_title` 时，不再生成阻断性的 chapter-level `CONTEXT_FAILURE`
  - `FORMAT_POLLUTION` 现在只在目标文本出现“源文里不存在的标签/标记”时触发，源文原样提到的字面标签（如 `<think>`）不再误报
- `v4` 的 final report 已刷新到 `artifacts/real-book-live/deepseek-full-run-v4/report.json`；review 复检后 `open_issue_count=0`，11 章 `bilingual_html` 已全部成功导出，document 状态为 `exported`
- 已新增 [merged-export-rendering-policy.md](/Users/smy/project/book-agent/docs/merged-export-rendering-policy.md)，冻结“整本合并导出”中受保护内容、结构化工件和混合内容的渲染规则，避免把 code/table/prompt/literal-tag 误当成翻译缺失
- 已修复两个真实长跑后处理 bug：`ExportGateError` 兼容层和 translation samples 的 `TargetSegmentStatus` 枚举口径
- 已在真实书《Build an AI Agent (From Scratch)》的 `v4` 结果上完成 document-level `merged_html` 导出验证：
  - 单文件阅读工件已生成：`artifacts/real-book-live/deepseek-full-run-v4/exports/003b7864-d84b-50ae-a54c-cc48858ea57e/merged-document.html`
  - manifest 已生成：`artifacts/real-book-live/deepseek-full-run-v4/exports/003b7864-d84b-50ae-a54c-cc48858ea57e/merged-document.manifest.json`
  - 当前 render summary 为：`zh_primary_with_optional_source = 1802`、`source_artifact_full_width = 206`
  - 代码块等 `protected/source-only` 内容已按策略渲染为“保留原样”的全宽工件，而不再表现成空翻译位
  - 零内容、无标题的 frontmatter 章节现在会在 merged HTML 阅读态中直接跳过，不再把 `chapter_id`/UUID 渲染成章节头
- merged export 现在也支持结构化工件专门渲染：
  - `image_anchor_with_translated_caption`：figure/img 锚点保留，并显示译后 caption
  - `translated_wrapper_with_preserved_artifact`：表格保留结构化原文 body，而不是误判为空翻译
  - `source_artifact_full_width`（equation）：MathML/SVG 公式以“公式保持原样”的阅读态工件呈现
  - `reference_preserve_with_translated_label`：纯 URL / 路径 / 环境变量类 reference block 以保留字面值的方式导出
- 上述新 render modes 已通过 parser 回归、本地 API 回归和 PostgreSQL 集成回归验证
- merged HTML 的阅读排版已升级为技术书阅读版：
  - 新增封面区、目录侧栏、章节导航和 `Back to top`
  - typography 从“工程可读”提升为“长文阅读优先”，并增加移动端 / print 样式
  - 工件卡片（code/table/image/reference/equation）与正文有明确视觉层级，不再像 QA 表格视图

## Current Source of Truth

核心设计文档：

- [translation-agent-system-design.md](/Users/smy/project/book-agent/docs/translation-agent-system-design.md)
- [pdf-support-implementation-plan.md](/Users/smy/project/book-agent/docs/pdf-support-implementation-plan.md)
- [long-task-run-control.md](/Users/smy/project/book-agent/docs/long-task-run-control.md)
- [error-taxonomy.md](/Users/smy/project/book-agent/docs/error-taxonomy.md)
- [rerun-policy.md](/Users/smy/project/book-agent/docs/rerun-policy.md)
- [issue-rerun-matrix.md](/Users/smy/project/book-agent/docs/issue-rerun-matrix.md)
- [p0-database-ddl.sql](/Users/smy/project/book-agent/docs/p0-database-ddl.sql)
- [orchestrator-state-machine.md](/Users/smy/project/book-agent/docs/orchestrator-state-machine.md)

## Immediate Next Steps

1. 按 [pdf-support-implementation-plan.md](/Users/smy/project/book-agent/docs/pdf-support-implementation-plan.md) 进入 P1，先实现文本型 PDF intake / classification / extraction
2. 实现 PDF 阅读顺序恢复、页眉页脚剔除、段落重建和跨页修复，把 PDF 接入既有 `block -> sentence -> packet` 主链
3. 为 PDF 增补 PDF-specific provenance 和 QA，确保结构恢复错误不会静默污染翻译链路
4. 为 merged export 继续补“图片二进制/静态资源落盘”能力，让 image anchor 不只保留 source path，还能在阅读稿中显示实际图片
5. 基于新 parser / render modes 对真实书重新 bootstrap 一版数据库，再生成新的 merged HTML，验证图片/公式/reference 在真实书上的最终表现

## Open Questions

- 队列系统是否先用 DB-backed 轻量 dispatcher，还是一开始就引入正式 job queue
- LLM provider 与调用封装如何抽象
- P0 是否需要先做最小 review package，而不是立即做 UI

## Blockers

当前无代码层硬阻塞。  
本地 Docker + PostgreSQL smoke 已完成。当前主要剩余项是更强 QA、provider usage/cost 解析，以及如何把 5h+ 的单线程真实整书 rerun 进一步产品化。

## Notes for Next Operator

- 不要绕过现有设计文档直接开写翻译逻辑。
- 优先保证句级覆盖、provenance、alignment、packet 和 rerun 机制。
- 默认先做 P0，不提前扩展扫描 PDF、复杂 OCR 和文学型风格优化。
- 当前工程栈默认按 Python + FastAPI + SQLAlchemy + Alembic + PostgreSQL 推进，除非出现明确冲突。

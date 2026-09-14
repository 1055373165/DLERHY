# Book Agent 重构计划 (P0 → P4)

起点：`main@2890024`（2026-09-14）。测试基线见 [`baseline-tests.md`](baseline-tests.md)。

## 已确认的决策

| 议题 | 决策 |
|---|---|
| 范围 | P0→P4 分阶段全做，每个阶段独立可交付 |
| 运行时自愈层（incident / patch proposal / bundle / repair transport / forge 工具） | **删除**；运行时收敛为 retry / pause / fail + 事件 |
| 交付脚本导出链路（`scripts/export_chapter_zh_html.py` 等） | 把更优启发式并入 `services/export`，对照脚本产出验证后**删除** |
| 被追踪的交付物 / 书籍译文 / EPUB | `git rm --cached` + `.gitignore`，**保留历史** |
| PDF 锚点 / Block ID 方案 | 不在本次结构重构里改；单独立项（需数据迁移） |

## 工作原则

- 每一步行为保持，以测试为门：不得引入 `baseline-tests.md` 之外的新失败。
- 结构拆分前先补 characterization / golden 测试。
- 迁移期保留转发 shim（re-export / 委托门面），调用方迁完后再删除。
- 迭代时跑相关测试文件；阶段结束时逐文件全量回归（`test_pdf_support.py` 单文件约 27 分钟）。
- 提交信息由人工撰写、描述真实意图；生成物不进提交。

---

## P0 止血

### P0.1 翻译 worker 接线（高危）
- [x] `DocumentRunExecutor` 按工作项通过 `app.state.resolve_translation_worker` 解析 worker，不再在启动时捕获 `None`（现状：`main.py:92` → `document_run_executor.py:148` → `translation.py:277` 回退 Echo）。
- [x] 测试：lifespan 启动后的执行器使用配置的非 Echo worker。

### P0.2 执行器生命周期与可观测性
- [x] 修复 `_controller_runner` 重复赋值（`document_run_executor.py:178/197`）。
- [x] 被吞掉的 reconciler / supervisor / heartbeat 异常记日志（controller 已随 P1.1 删除）。
- [x] 解决 `test_api_workflow.py` 段错误（根因：测试用 StaticPool 让多个执行器线程共享同一个 sqlite3 连接；改为文件 SQLite 默认连接池）。
- [ ] 执行器 `stop()` 协作式取消 work 线程后再 dispose engine（并入 P2 队列/租约取消）。
- [x] run loop 遇到 `IntegrityError` / `OperationalError` 记日志并在下个 tick 重试，不再把整个 run 判失败。

### P0.3 预算护栏
- [x] `_run_loop` 每 tick 调用 `RunExecutionService.enforce_budget_guardrails`。

### P0.4 API 正确性
- [x] GET 路径的必要写入显式提交（导出下载按需生成/重建、providers 首次 bootstrap）；是否应由 GET 触发生成留待 P2 事务策略。
- [x] SSE 改为 async generator，按 `request.is_disconnected()` 检测断连并释放 LISTEN 连接（Postgres 测试覆盖）。
- [x] CAS/sha256 stamp 移到 `DocumentWorkflowService.export_document` 出口（API / 执行器 / CLI 共用）；修复原裸 SQL UPDATE 在 SQLite 上匹配不到 UUID 的问题。
- [x] `ExportDocumentRequest.export_type` 增加 `zh_epub`（`bilingual_markdown` 等未实现类型留待 P4 决定）。

### P0.5 可信测试基线
- [x] 测试临时目录按进程隔离并在退出时清理；测试强制 echo 后端，不再读取 `.env` 发起真实 provider 调用。
- [ ] 共享 SQLite session / app 夹具（`conftest.py`）并入 P3 的测试拆分。
- [x] 修复或删除无法收集 / 依赖未入库数据的测试。
- [x] 分拣旧失败：修复 13 个产品缺陷、更新过时测试、4 个 xfail 附原因；基线 994 passed / 0 failed（见 `baseline-tests.md`）。
- [x] Postgres：`tests/test_postgres_schema_drift.py` 在临时库上 `alembic upgrade head` 并与 `Base.metadata` 比对表/列/可空性/索引（`BOOK_AGENT_RUN_PG_TESTS=1` 开启）。

---

## P1 瘦身

### P1.1 删除自愈层
- [x] `services/runtime_repair_*`、`runtime_bundle`、`bundle_guard`、`patch_review`、`runtime_patch_validation`、`incident_triage`、`recovery_matrix`、`runtime_lane_health`、`export_routing`。
- [x] `app/runtime/controllers/*` 与 `controller_runner`；`infra/repositories/runtime_resources.py`（ChapterRun / PacketTask / ReviewSession / RuntimeCheckpoint 投影无人读取，一并删除）。
- [x] `tools/runtime_repair_*`、`tools/forge_*`、`forge_v2_stop_guard.py`，CLI 中 forge 子命令，`runtime_repair_transport_*` / `runtime_bundle_root` 配置。
- [x] 路由 `patches.py`；执行器中的 controller 调和、REPAIR 阶段、导出误路由恢复、`_finalize_*` 死副本；`run_control` 摘要里的 `runtime_v2` 投影；导出/文档 API 的 `runtime_v2_context` 与 `route_evidence_json`。
- [x] 对应测试（34 个文件及 `test_run_execution` / `test_api_workflow` / `test_run_control_api` 中的相关用例）。
- [x] Alembic `20260914_0030`：删除 7 张表、REPAIR 阶段与 `runtime_bundle_revision_id` 列（在临时 Postgres 16 上验证：种子数据迁移、CHECK 拒绝 repair、ORM 与库表一致）。

### P1.2 删除非产品代码
- [x] 仅被脚本/测试使用的模块：`pdf_inplace`、`packet_experiment*`、`translation_chapter_smoke`、`translation_prompt_ab`、`translate_rollout_supervisor`、`translate_benchmark_draft_generator`、`extraction_router`。保留 `chapter_memory_backfill`（运维回填）与 `tools/pdf_smoke`（解析诊断），二者有测试。
- [x] 过时脚本 32 个：章节一次性脚本、autopilot 时代脚本、packet 实验脚本。交付导出脚本链路留待 P4；运维脚本暂不搬目录。
- [x] `orchestrator/state_machine.py` 无调用方的转移表；`workers/contracts.py` 未用的 Reviewer DTO。

### P1.3 仓库卫生
- [x] `git rm --cached`：`deliverable/`、`LLM-Book*/`、`books/`、`.scratch/`、`book-agent.db`、`frontend/.omc/`、`.claude/settings.local.json`、`.claude/*.lock`；提交 `artifacts/` 的删除。
- [x] 补全 `.gitignore`。
- [x] 删除过期交接文档 `snapshot.md`、`progress.txt`、`docs/mainline-progress.md`、`tasks/todo.md`；README 与实际能力对齐（运行时、自愈、导出格式）。
- [x] 删除 `WorkspacePage.test.tsx` 中针对已移除界面的 15 个测试，修正 LibraryPage 测试；前端 vitest 4/4 通过（遗留夹具在 P4 前端整理时清理）。

---

## P2 统一基础设施

已确认：`translate_full` 的审校与导出为必需阶段（已完成）；P2 完整执行，含 POST translate/review/export 改为入队。

- [x] **P2.1 Worker provider**：`workers.factory.TranslationWorkerProvider`（按凭据 revision 缓存、线程安全）；API / 执行器 / CLI / 概念解析器共用；凭据 worker 与 settings worker 同一构造（prompt profile、单价来自 settings）。
- [x] **P2.2 类型化 provider 异常**：`workers.failures.classify_failure` 按异常类型给出 retry / pause / fail（402 → 暂停「余额不足」，401/403 → 暂停「认证失败」）；新增 `ProviderResponseFormatError`；删除消息子串匹配。
- [x] **P2.3 租约丢失**：worker 在提交结果的事务内锁定并校验租约（`assert_lease_held`），租约已被回收则回滚并丢弃结果，不再写失败记录或崩溃。领取本身已是按状态的 CAS UPDATE（并发下只有一个赢家），`SKIP LOCKED` 只是性能优化，暂不做。
- [x] **P2.4 Run 状态**：所有修改 run 行的路径（状态转换、用量计数、流水线缓存、租约回收、播种）经 `get_run_for_update` 加行锁，读改写串行化，统一「先锁 run 再动 work item」的顺序；状态转换因此等价于 CAS。Postgres 并发测试：12 个并发完成，改前只保留 3 个计数，改后 12 个。
- [x] **P2.5 事务与线程**：翻译拆为 prepare / call_worker / persist 三个事务阶段，LLM 调用期间不占连接，失败事件真正落库；审校与导出改在工作线程执行，阶段缓存与工作项结果同事务写入；`stop()` 按层 join 并阻止停止后再起线程；心跳未启动时不再 join 抛错；`get_run_for_update` 先做空 UPDATE 取写锁（SQLite 也能串行化）。
- [x] **P2.6 API 入队**：`POST /documents/{id}/translate|review|export` 创建并启动对应 run（`translate_targeted` / `review_full` / `export_full`），返回 202 与 run 摘要。新增 `orchestrator/run_plan.py`：run 类型 + `status_detail_json.run_request` 决定 run 拥有的阶段、包范围、审校是否修复阻断、导出门禁是否自动跟进；执行器、阶段门、终态对账都按计划判定必需阶段。导出门禁失败会保留门禁记录并把门禁详情写进阶段缓存。GET 下载只提供已有导出（按需生成与自愈重建连同 `rebuild_lock` 一并删除）。同步语义的 API 测试改用 `tests/document_actions.py` 的进程内适配器。
- [ ] **P2.7 数据层**：应用只支持 Postgres（SQLite 仅纯单测）；`JSONB` variant；枚举 CHECK 由枚举生成；去掉 `has_table` 探测；审计写入仅插入；`service.sh` SQLite 模式与 Docker 迁移步骤。

---

## P3 拆分巨石

顺序：`workflows.py` → `export.py` → `pdf.py` → 翻译核心。每项先补 golden 测试。

### P3.1 `services/workflows.py`（4.9k）
- [ ] 绞杀者模式拆为 application 模块：`analytics`（纯函数）→ `worklist` / `memory_proposals` → `document_queries` → `export_use_case` / `review_repair` / `actions`；门面委托保留到调用方迁完。
- [ ] `routes/documents.py` 约 1000 行手写序列化改为 `from_attributes` 响应模型；下载/制品解析逻辑移出路由。
- [ ] `export_document` 五段重复分支参数化。

### P3.2 `services/export.py`（8.7k）
- [ ] golden：merged HTML / Markdown / EPUB（EPUB 与 PDF 夹具各一）。
- [ ] 纯启发式（代码/正文、PDF 修复、代码 reflow、bbox 数学）抽为自由函数，保留委托方法。
- [ ] 统一 `build_action` / `scope_for_action`（review 与 export 当前映射不一致）。
- [ ] 每次导出只组装一次 `DocumentRenderModel`；gate 变为只读评估，issue 同步显式化。
- [ ] 渲染器协议：`html` / `markdown` / `epub_rebuilt` / `epub_patch` / `pdf_print` / `review_package`；CSS 外置模板。
- [ ] 资产处理无副作用（返回 DocumentImage 更新由服务持久化）。

### P3.3 `domain/structure/pdf.py`（9.5k）
- [ ] characterization：所有夹具 PDF 的 `ParsedDocument` 快照（锚点、类型、角色、章节）。
- [ ] 叶子辅助函数 → `ingestion/text`、`ingestion/pdf/classify`；提取器/画像器 → `ingestion/pdf/extract`。
- [ ] 显式 Pass 流水线 + 不可变 `RecoveryContext`（去掉 `_current_recovery_lane` 实例状态）。
- [ ] 章节构建 / TOC 偏移独立模块。
- [ ] 去重：可翻译性判定、caption 正则、group-context 几何、prose-continuation 启发式。
- [ ] OCR / 文件 IO 移出 domain；配置注入替代 env 读取。
- [ ] 修复：TATR `source_path`、IR 在 modality 之前构建、OCR parser 工厂、refresh 不重建句子。

### P3.4 翻译核心
- [ ] DTO 从 `workers/contracts.py` 移至 `translation/contracts`（修复 domain→workers、infra→workers 依赖）。
- [ ] 类型化 `ChapterMemory`：单一 schema / 合并 / 提交策略（现有 5 份 schema、3–4 种合并）。
- [ ] `TranslationService` 拆为 `PacketExecutor` + 后置 hooks（术语校验、记忆提案、事件）；新增 `OutputValidator`（覆盖率），失败 run 记录 `error_code`。
- [ ] 术语统一：编译期从 `TermEntry` 解析，注入同样走相关性过滤（prompt 变化，需 golden 基线有意更新）。
- [ ] Prompt profile 注册表（先 golden 快照各 profile 的 prompt）。
- [ ] 书籍特定启发式（style_drift、term_normalization、关键词集）数据化 / 按文档配置。

---

## P4 统一导出

- [ ] 将 `export_chapter_zh_html.py` 独有的改进（caption 链接、图 bbox 扩展、stub 过滤、列表拆分等）移植到 `export/assembly/normalize`。
- [ ] 明确“译文选择”规则（脚本：首个 edge；服务：最新 run），有意统一。
- [ ] 以 `verify_chapter.py` 作为 oracle，对 ch1–ch9 新旧产出对照。
- [ ] 删除脚本导出链路及依赖 `.test-tmp/` 的构建脚本（先确认所需输入已迁出）。
- [ ] 双语整书下载返回真正的双语产物；实现或移除 `BILINGUAL_MARKDOWN` / `ZH_PDF` / `JSONL`。
- [ ] 前端：`api.ts` 由 OpenAPI 生成；拆分 `WorkspaceContext`。

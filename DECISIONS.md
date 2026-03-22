# 架构决策记录

## ADR-001：生产级高保真优先于吞吐和 token 节省
- 状态：已决定
- 日期：2026-03-20
- 背景：当前项目已经具备 PDF 解析、翻译、review、rerun、export 主链路，但用户目标不是“能翻”，而是“生产级高保真交付”。
- 候选方案：
  - 保持当前策略，优先控制 prompt 长度和吞吐
  - 以交付质量为第一优先，允许适度增加上下文与提示复杂度
- 最终决定：质量优先。默认以语义保真、段落连贯、术语一致和结构可交付为第一目标，吞吐与 token 成本作为次级约束。
- 代价与妥协：prompt 与上下文编译可能更复杂，局部请求成本上升。
- 推翻条件：若新增上下文未带来可验证的真实 packet 改善，或明显损害稳定性/可重跑性。
- 影响范围：translation prompt、context compile、review 策略、packet experiment。
- 探针验证：已通过（来自现有驾驶舱证据与当前需求锁定）。

## ADR-002：默认生产 profile 继续保持 `role-style-v2`
- 状态：已决定
- 日期：2026-03-20
- 背景：代码库中已经存在 `role-style-v2`、`role-style-brief-v3`、`material-aware-v1` 等 profile，且历史实验已将 `role-style-v2` 提升为正式默认。
- 候选方案：
  - 直接切换默认 profile
  - 保持默认 profile 不变，先增强其可消费的上下文能力
- 最终决定：不直接切换默认 profile；先增强默认 `role-style-v2` 能消费的 section/discourse 上下文。
- 代价与妥协：改进速度更稳，但不会一次性吃到更激进 prompt profile 的潜在收益。
- 推翻条件：若固定 packet execute 证据证明其他 profile 在真实样本上稳定优于 `role-style-v2`。
- 影响范围：workers/translator、services/context_compile、packet experiment。
- 探针验证：已通过（现有驾驶舱明确 `role-style-v2` 是正式默认生产基线）。

## ADR-003：优先做 runtime-only 的 `section_brief + discourse_bridge`
- 状态：已验证（探针通过）
- 日期：2026-03-20
- 背景：当前 paragraph-led prompt 已能改善句序与局部术语问题，但仍缺 section-level 和 paragraph-to-paragraph 的显式桥梁，译文容易“句子都对，整段还不够顺”。
- 候选方案：
  - 直接引入数据库 schema 级新 memory 结构
  - 先在运行时编译阶段补最小 `section_brief + discourse_bridge`
- 最终决定：先做 runtime-only 版本，不改数据库 schema，把新增信号限制在 `context_compile -> prompt` 路径。
- 代价与妥协：第一版不会持久化完整 discourse state，仍以启发式为主。
- 推翻条件：若仅靠 runtime scaffolding 无法带来可验证改善，再评估持久化的 section/discourse memory。
- 影响范围：workers/contracts、services/context_compile、workers/translator、tests。
- 探针验证：已通过（`context_compile / prompt / packet experiment` 定向回归已补齐）。

## ADR-004：继续用共享窄规则增强 rerun，而不是膨胀默认 prompt
- 状态：已验证（探针通过）
- 日期：2026-03-20
- 背景：当前剩余质量问题越来越集中在少数高信号“翻译腔”表达，不适合继续把默认 prompt 越堆越长；更适合走 `STYLE_DRIFT -> review issue -> rerun hint` 的窄闭环。
- 候选方案：
  - 继续往默认 prompt 添加更通用、更长的风格约束
  - 把 `STYLE_DRIFT` 规则的 `prompt_guidance` 直接写入 issue evidence 与 rerun hints，只扩高命中规则
- 最终决定：默认 prompt 只保留高信号基础约束；更细的 rewrite 指令沿共享 `STYLE_DRIFT` 规则进入 review 和 rerun。新增规则时优先选择误报低、收益高的 literalism pattern。
- 代价与妥协：review/rerun 逻辑会更依赖规则质量，需要持续控制规则面和误报率。
- 推翻条件：若共享窄规则无法稳定改善真实 packet 输出，或者误报率明显升高，再重新评估是否要把更多 guidance 升回默认 prompt。
- 影响范围：services/style_drift、services/review、orchestrator/rerun、rerun workflow、tests。
- 探针验证：已通过（style-drift review/rerun 定向回归与 rule-engine 回归已通过）。

## ADR-005：真实 rerun 验收以 review issue 还原的实验 prompt 为准
- 状态：已验证（探针通过）
- 日期：2026-03-20
- 背景：过去 packet experiment 虽支持手工传 `rerun_hints`，但 operator 需要自己抄 issue 里的 hint，难以保证实验 prompt 与真实 rerun 状态机一致。
- 候选方案：
  - 继续手工拼 `rerun_hints`
  - 让实验工位直接接收 `review_issue_id`，自动解析 style hints 和 concept overrides
- 最终决定：以 review issue 作为 experiment workbench 的真实入口。`run_packet_experiment.py` 和 `PacketExperimentService` 现在都支持 `review_issue_id`，实验 prompt 会自动还原 review/rerun 闭环里的实际输入。
- 代价与妥协：实验工位与 review issue schema 的耦合更强，后续改 evidence 字段时要同步回归。
- 推翻条件：若 review issue schema 经常变化、导致实验工位维护成本过高，再考虑回退到更弱的手工 hint 模式。
- 影响范围：services/packet_experiment、scripts/run_packet_experiment.py、tests、真实 packet 验收流程。
- 探针验证：已通过（真实 packet `2e26...` 的 dry-run / execute 工件已能基于 5 个 live issues 还原 8 条 rerun hints 和 1 个 concept override）。

## ADR-006：issue-driven rerun 先做 selective rollout，不做 blanket 扩样
- 状态：已验证（探针通过）
- 日期：2026-03-20
- 背景：`2e26...` 这类 mixed issue packet 上，issue-driven rerun prompt 能更接近真实工作流，并带来明显更规整的输出；但在 `b4c1...` 这类轻量 style-only packet 上，当前默认链路本身已经能产出无 style-drift 命中的可接受结果。
- 候选方案：
  - 立刻把 issue-driven rerun 扩到所有存在 review issue 的 packet
  - 只对 mixed issue / high-value / 当前默认输出仍不稳定的 packet 继续扩样
- 最终决定：selective rollout。保留 issue-driven rerun workbench，并继续用于高价值 packet；但不对所有轻量 style-only packet 做 blanket rerun 或 blanket execute 扩样。
- 代价与妥协：推广速度更慢，需要 operator 先判断 packet 是否值得进入 issue-driven execute。
- 推翻条件：若后续更多样本显示轻量 style-only packet 同样能稳定从 issue-driven rerun 获得明显收益，再重新评估是否扩大默认适用面。
- 影响范围：真实 packet 验收策略、chapter smoke 选样策略、后续 workflow 自动纠偏边界。
- 探针验证：已通过（`2e26...` 与 `b4c1...` 两个真实 packet 的 dry-run / execute 对照已形成清晰分界）。

## ADR-007：chapter smoke 默认采用 issue-priority 选样，但保留 memory-first 回退
- 状态：已验证（探针通过）
- 日期：2026-03-20
- 背景：如果 selective rollout 只停留在人工挑 packet，chapter smoke 仍会优先选 memory signal 最高的 packet，无法把 mixed/high-value packet 提前拉进验收闭环。
- 候选方案：
  - chapter smoke 继续沿用 scan 的 memory-first 顺序
  - chapter smoke 默认按 issue priority 重排，但允许显式关闭
- 最终决定：默认按 issue priority 重排 chapter smoke 选样。排序优先级为 mixed/non-style unresolved issues，其次 unresolved issue count，再回落到 memory/concept signal。CLI 保留 `--disable-issue-priority`，方便 A/B 和回退。
- 代价与妥协：默认 smoke 不再等价于纯 memory-first 扫描结果，分析时需要区分 `scan top_candidate` 与 `selected_packet_ids`。
- 推翻条件：若真实章节里 issue-priority 频繁把 smoke 预算浪费在低收益 packet 上，再重新评估默认排序。
- 影响范围：services/packet_experiment_scan、services/translation_chapter_smoke、scripts/run_translation_chapter_smoke.py、chapter smoke 验收策略。
- 探针验证：已通过（真实章节 `d1ff...` 上，默认 issue-priority smoke 选中 `2e26...`，关闭后回到 memory-first 的 `21bc...`）。

## ADR-008：workflow auto-followup 先保留 issue type 语义，再在同类型内按 packet 价值分配预算
- 状态：已验证（探针通过）
- 日期：2026-03-20
- 背景：chapter smoke 已默认优先 mixed/non-style packet，但 workflow auto-followup 仍主要按 issue type 和 packet 命中数排序。这样会出现 style-only packet 仅因 issue 数更多，就抢走本该留给 mixed/high-value packet 的有限预算。
- 候选方案：
  - 直接把 workflow 排序改成 packet priority 优先，压过原来的 issue type 语义
  - 保留 `TERM_CONFLICT -> UNLOCKED_KEY_CONCEPT -> STYLE_DRIFT` 的 issue type 顺序，只在同类型内部再按 mixed/non-style packet 优先
- 最终决定：采用“issue type first, packet value second”。workflow auto-followup 继续先看 blocking 和 issue type；当候选属于同一 issue type 时，再优先 mixed/non-style packet，并参考 non-style issue weight / total issue weight 分配预算。
- 代价与妥协：排序逻辑更复杂，workflow 和 smoke 不再是完全同一套 key，但可以避免 `UNLOCKED_KEY_CONCEPT` 或 `STYLE_DRIFT` 因 packet 信号过强而越级抢到 `TERM_CONFLICT` 前面。
- 推翻条件：若后续真实章节 execute 表明 workflow 预算仍频繁花在低收益 packet 上，或者 mixed packet 的修复顺序仍不理想，再重新评估是否把 packet priority 提前到 issue type 之前。
- 影响范围：services/workflows.py、review auto-followup 回归、workflow 级预算分配策略。
- 探针验证：已通过（workflow 定向回归证明：同类型 `STYLE_DRIFT` 候选里，mixed packet 会先于 style-only 大包；同时真实 mixed review 工件仍保持 `TERM_CONFLICT` 候选排在 `STYLE_DRIFT` 前面）。

## ADR-009：workflow/export 事务内的能力探测必须统一走当前 session connection
- 状态：已验证（探针通过）
- 日期：2026-03-20
- 背景：`UNLOCKED_KEY_CONCEPT` auto-lock 在 mixed workflow 里出现 `memory_snapshots` 的 `StaleDataError`。根因不是 lock/rerun 逻辑本身，而是 workflow/export 事务中的 document-image table probe 使用 engine 级 `inspect(bind).has_table(...)`，在未提交事务中绕开了当前 session connection，导致 chapter memory snapshot 与 term lock 视图漂移。
- 候选方案：
  - 保持 engine 级 table probe，用更多 `flush/expire` 去补救 identity / transaction 状态
  - 把能力探测改成走当前 `session.connection()`，让 schema probe 与 review/lock/rerun/export 共用同一事务视图；同时为 `:memory:` SQLite 补 `StaticPool`
- 最终决定：走 session-scoped connection。bootstrap/export repository 的 table probe 现在都直接 inspect 当前 session connection；测试环境的 `:memory:` SQLite 额外使用 `StaticPool`，避免连接漂移放大事务不一致。仍保留 API backfill 路由中的 `session.get_bind()`，因为那里只是提取 database URL 后关闭 session，不参与事务内读写。
- 代价与妥协：repository 的 schema probe 会更早地绑定事务连接；如果未来需要在无事务上下文里做懒探测，要继续沿 session connection 语义实现。
- 推翻条件：若未来切到完全不同的数据库后端并发现 session-level inspect 带来新的兼容问题，再评估是否抽象单独的 schema capability layer。
- 影响范围：infra/repositories/bootstrap.py、infra/repositories/export.py、infra/db/session.py、workflow/review/export 集成回归、测试基础设施。
- 探针验证：已通过（document-image table probe 不再冲掉未提交的 concept lock；`UNLOCKED_KEY_CONCEPT` 与 mixed workflow 回归恢复通过；export 侧同类 probe 也已补根因回归；更广 workflow/export 检索确认剩余 `session.get_bind()` 仅在 API history backfill 路由中用于提取 database URL，不参与事务内读写）。

## ADR-010：真实章节 followup 验收默认在克隆 DB 上做 same-session review/export smoke
- 状态：已验证（探针通过）
- 日期：2026-03-20
- 背景：仅靠 packet experiment 能验证 prompt 和 rerun 局部收益，但不足以证明最新 workflow/export 修复在真实章节上真正可交付。直接在主数据库上做 live followup 虽然最快，却会污染现有基线，增加后续比对成本。
- 候选方案：
  - 继续只做 packet execute，不验证 review/export 同 session 链路
  - 直接在主数据库上跑真实章节 review auto-followup + export
  - 每次先复制当前 DB，再在副本上执行同一 session 内的 review auto-followup、chapter export 和 merged export
- 最终决定：采用 cloned-DB smoke。新增 `scripts/run_real_chapter_followup_smoke.py`，默认在数据库副本上跑真实章节 followup/export，并把 before/after issue 计数、auto-followup executions、rerun 样本译文和 export 路径固化成 JSON 报告。
- 代价与妥协：需要维护额外脚本和 artifact 目录；报告反映的是“副本世界”的真实结果，而不是主数据库即时状态。
- 推翻条件：若后续接入更正式的 durable run-control 工位，能够对主库操作自动快照/回放，再评估是否收敛到统一 runner。
- 影响范围：scripts/run_real_chapter_followup_smoke.py、artifacts/real-book-live/* followup smoke、真实章节验收流程。
- 探针验证：已通过（真实章节 `d1ff...` 在副本上完成 `review auto-followup + review package + bilingual_html + merged_markdown`；open issues 从 `12` 降到 `5`，`TERM_CONFLICT` 与 `STYLE_DRIFT` 清零，`blocking_issue_count=0`）。

## ADR-011：Web 产品化继续采用 FastAPI 直出单页工作台，但主体验切换到用户上传到下载闭环
- 状态：已验证（探针通过）
- 日期：2026-03-20
- 背景：项目已经具备 EPUB / PDF 英文书籍到中文结果的完整后端接口，但此前首页更偏运维驾驶舱，不足以作为真正面向用户的产品入口。
- 候选方案：
  - 新增独立 SPA / 前端构建链，单独封装新的用户产品界面
  - 继续由 FastAPI 直出首页，但重写为以用户任务为中心的上传、整书转换、状态可见、结果下载工作台
- 最终决定：继续采用 FastAPI 直出单页工作台，主路径收敛为 `上传书籍 -> 创建并启动 translate_full run -> 自动 review/export -> 下载中文结果`。历史记录、当前 run 和导出状态保留在同一页面，运营级 worklist / owner lane 则继续作为 `/v1` API 能力存在，而不是首页一等公民。
- 代价与妥协：模板文件会继续偏大，前端组件化和多路由扩展性不如独立 SPA；但部署链路更简单，且能直接复用现有 run-control / export / history contract。
- 推翻条件：若后续需要更复杂的多页面信息架构、身份系统、多人协作或高度组件化前端，再重新评估是否拆分为独立 SPA。
- 影响范围：`src/book_agent/app/ui/page.py`、`README.md`、`tests/test_frontend_entry.py`、根路径产品体验。
- 探针验证：已通过（`uv run pytest tests/test_frontend_entry.py -q` 与 `uv run pytest tests/test_api_workflow.py -k 'bootstrap_upload_accepts_epub_file or translate_full_run_executes_review_and_exports_in_background' -q` 通过）。

## ADR-012：`translate_full` 默认采用章节级并发，章节内保持串行
- 状态：已验证（探针通过）
- 日期：2026-03-21
- 背景：整书翻译主链路原先虽然暴露了 `max_parallel_workers` 预算字段，但执行器实际上仍是一条 run 线程串行处理 packet，导致长书吞吐受限；同时同章 packet 又依赖 chapter memory 与前文已接受译文，不能直接做全局 packet 并发。
- 候选方案：
  - 继续保持全局单 packet 串行，完全用质量换吞吐
  - 直接做全局 packet 并发，让 `max_parallel_workers` 同时跑任意 packet
  - 按章节分桶并发：章节之间允许并行，同一章节任一时刻只允许 1 个 translate work item 在跑
- 最终决定：采用章节级并发调度。`translate_full` 现在会优先 claim 不同章节的 packet，并让 `budget.max_parallel_workers` 真正作为“最多同时活跃章节数”生效；当章节数不足时自动回落到实际可并发章节数。
- 代价与妥协：run executor 线程模型更复杂，translate 与 review/export 的阶段 gate 需要显式化；跨章节术语一致性仍主要依赖 termbase/entity snapshot 与后续 review，而不是并发调度本身。
- 推翻条件：若未来引入全书级动态 memory、跨章节强依赖术语链，或 provider / 数据库在当前并发策略下出现明显不稳定，再重新评估是否要回退到更保守的并发模型。
- 影响范围：`src/book_agent/app/runtime/document_run_executor.py`、`src/book_agent/services/run_execution.py`、`tests/test_api_workflow.py`、整书翻译运行时吞吐。
- 探针验证：已通过（`uv run --extra dev python -m pytest tests/test_api_workflow.py -k 'test_translate_executor_claims_parallel_chapters_without_same_chapter_overlap or test_translate_full_run_executes_review_and_exports_in_background' -q` 通过）。

## ADR-013：SQLite 模式下的 run executor 必须避免跨 session 自锁，并自动回收过期 lease
- 状态：已验证（探针通过）
- 日期：2026-03-21
- 背景：在 SQLite 环境中，`translate_full` 恢复运行时出现了两类假运行问题：一类是同一事务里刚 seed 完 work item，就立刻用新的 session 更新 pipeline stage，导致 stage 更新被自己未提交的写事务锁住；另一类是进程中断后留下 `running` work item 与未释放 lease，后续执行器不能自动捞回，页面会长期显示“进行中”但无真实进度。
- 候选方案：
  - 保持当前 fresh-session stage update 策略，继续依赖人工重试和人工清理 stale lease
  - SQLite 下始终复用当前事务做 stage 更新，并在 run loop 开始处主动回收过期 lease
  - 彻底停用 SQLite，仅要求本地也切到 Postgres
- 最终决定：保留 SQLite 本地运行能力，但在执行器里修正两点：当调用方已经持有 session 时，`_update_pipeline_stage(...)` 直接复用当前 session；同时每轮 `_run_loop(...)` 先调用 `reclaim_expired_leases(...)`，把过期 lease 回收到 `retryable_failed`，让 run 可以自愈恢复。
- 代价与妥协：SQLite 仍不适合高并发多 run 长时间生产运行；本次修复解决的是“本地单机可恢复”和“假运行态”问题，而不是把 SQLite 提升成真正的高吞吐主库。
- 推翻条件：若后续默认部署切到 Postgres，且本地也不再承诺 SQLite 的长跑能力，可以重新评估是否保留这些特化分支。
- 影响范围：`src/book_agent/app/runtime/document_run_executor.py`、`tests/test_api_workflow.py`、`tests/test_run_execution.py`、本地真实书籍运行恢复体验。
- 探针验证：已通过（`uv run --extra dev python -m pytest tests/test_api_workflow.py -k 'test_translate_executor_claims_parallel_chapters_without_same_chapter_overlap or test_translate_executor_defaults_to_single_worker_on_sqlite_without_budget_override or test_translate_executor_seeds_pending_packets_without_sqlite_stage_update_deadlock' -q` 与 `uv run --extra dev python -m pytest tests/test_run_execution.py -k 'test_reclaim_expired_lease_requeues_work_item_and_increments_attempt_on_reclaim or test_executor_reclaims_expired_leases_before_stage_progression' -q` 通过）。

## ADR-014：整书运行在导出前必须执行文档级 blocker repair loop，而不是只靠导出 gate 放宽或单章低预算 follow-up
- 状态：已验证（探针通过）
- 日期：2026-03-21
- 背景：真实书籍运行中，`Dedication` 这类文档并不是“导出器坏了”，而是 review 之后仍残留整本文档范围的 blocking issue。现有机制要么只在单章 review 阶段做少量 packet follow-up，要么等到 export gate 命中第一章时再低预算地修一章，无法保证整书最终收敛到可导出状态。
- 候选方案：
  - 继续维持现状，依赖人工放宽 gate 或多次手工重试
  - 在 export gate 处继续按“首个阻塞章节”逐章修复
  - 在 `translate_full` 的 review 阶段后追加文档级 repair loop：扫描整本书所有 active blocking issue，按 scope/issue 类型批量执行 `RERUN_PACKET / REALIGN_ONLY / REPARSE_CHAPTER / UPDATE_TERMBASE_THEN_RERUN_TARGETED`，直到 blocker 清零或达到停止条件，再允许进入 export
- 最终决定：采用文档级 blocker repair loop，并把它并入 `translate_full` 的 review work item。review 先做常规章节 review 与轻量 auto-followup，然后执行整本文档的 blocker repair；如果 repair 后仍有 blocking issue，则 run 在 review 阶段直接失败，不再带着已知 blocker 进入 export。
- 代价与妥协：review 阶段会比以前更长，且对 stubborn blocker 会更早失败；同时当前 repair loop 仍是串行执行 action/rerun，不追求极致吞吐，优先保证“整书要么被修到可导出，要么明确停在 review”。
- 推翻条件：若后续引入专门的 repair stage UI、分布式 rerun worker，或需要把 repair 执行并行化/产品化为独立操作，再重新评估是否继续把它折叠在 review 阶段内部。
- 影响范围：`src/book_agent/services/workflows.py`、`src/book_agent/app/runtime/document_run_executor.py`、`tests/test_api_workflow.py`、整书 review/export 收敛路径。
- 探针验证：已通过（`uv run --extra dev python -m pytest tests/test_api_workflow.py -k 'test_translate_full_run_executes_review_and_exports_in_background or test_translate_full_run_repairs_document_blockers_before_export' -q` 通过；新增场景验证了“已翻译但含 TERM_CONFLICT + OMISSION 的文档”可在 review 阶段自动修复并成功导出）。

## ADR-015：book 级书名与 outlined_book 顶层章节必须脱离首章/frontmatter 语义单独决策
- 状态：已验证（探针通过）
- 日期：2026-03-21
- 背景：真实书籍 `Dedication` 暴露了两个结构性问题：一是 document title 在 PDF 无 metadata 时会退化到首章/frontmatter，导致整书被命名为 `Dedication`；二是 `outlined_book` 对 PDF outline 的顶层信任过强，把 `Key Takeaways / Conclusion / References` 等章内小节切成了顶层 chapter，进而污染目录、导出标题和章节统计。
- 候选方案：
  - 继续沿用 `document.title <- parsed.title <- chapters[0].title` 的旧链路，并只在导出层做兜底
  - 仅收紧 export 可见章节归并，不修 parser / bootstrap 的源语义
  - 将 document-level `title_src / title_tgt` 抽成独立语义，并在 `outlined_book` parser 中增加“真正顶层章”判定与辅助章节回挂
- 最终决定：采用第三种。document-level title 现在独立解析并持久化 `title_src / title_tgt`，显示层统一优先 `title_tgt -> title_src`；SQLite 启动时自动为老库补 `documents.title_src / title_tgt`。同时 `outlined_book` 只允许真正的 `Chapter N / Appendix / Glossary / 少数 frontmatter` 开顶层 chapter，`Conclusion / References / Key Takeaways` 等默认挂回上一章。
- 代价与妥协：需要维护一层 SQLite schema backfill，以及在 parser / workflow / export / API 上同时收敛 title 语义；对少数“无编号正文章”的 outline book，顶层章判断会更保守。
- 推翻条件：若后续引入更可靠的 title-page OCR / cover parser，或书籍类型扩展到大量“不编号但确属正文顶层章”的体裁，再重新评估当前的 outlined_book 章级判定规则。
- 影响范围：`src/book_agent/domain/document_titles.py`、`src/book_agent/domain/models/document.py`、`src/book_agent/services/bootstrap.py`、`src/book_agent/domain/structure/pdf.py`、`src/book_agent/services/workflows.py`、`src/book_agent/services/export.py`、`src/book_agent/infra/db/sqlite_schema_backfill.py`、相关 API / parser / app runtime 回归。
- 探针验证：已通过（`uv run --extra dev python -m pytest tests/test_pdf_support.py -k 'resolves_document_book_title_from_source_filename_for_outlined_book or collapses_auxiliary_top_level_outline_entries_into_neighboring_chapters or keeps_book_conclusion_and_contact_pages_out_of_top_level_chapters' -q`、`uv run --extra dev python -m pytest tests/test_app_runtime.py -k 'sqlite_schema_backfill_adds_document_title_columns' -q`、`uv run --extra dev python -m pytest tests/test_api_workflow.py -k 'bootstrap_upload_accepts_epub_file' -q` 通过）。

## ADR-016：代码块识别需要同时修 parser 与 export，才能同时覆盖新导入书籍和历史已入库 PDF
- 状态：已验证（探针通过）
- 日期：2026-03-21
- 背景：真实书籍导出暴露了新的结构问题：`For example, the output ... JSON object:` 这类说明句被 export 误升成 code artifact，而真正的 JSON block 没被稳定识别，甚至会把 `} This structured format ensures ...` 这类尾部正文继续黏在 code block 内。仅修 parser 无法改善历史已入库文档，仅修 export 又无法避免未来重新导入时继续写入坏结构。
- 候选方案：
  - 只在 export 层收窄误判，接受历史数据修复但未来 parser 继续写入混合 code block
  - 只在 parser 层增强 JSON/structured-output 识别，要求历史书籍重新 parse 才能见效
  - 同时修两层：parser 负责把纯 JSON 与 `JSON + trailing prose` 正确拆块；export 再补一层历史数据兜底，拆出混合 code artifact 的前后 prose，并阻止说明句被 `single_line_codeish` 误升
- 最终决定：采用双层修复。parser 现在将 bare braces / JSON key-value 行视为 code-like，并允许 `} prose...` 这类块拆成 `code + paragraph`；export 侧同步识别 multiline JSON artifact、阻止 `structured output intro sentence` 被升成 code，并在 render block 正规化阶段把历史混合 code block 拆成独立 prose/code/prose 片段。
- 代价与妥协：`export.py` 会继续保留一部分与 parser 相近的保护性 heuristics，用于兼容历史库；这意味着代码块判定逻辑存在“两道闸”，维护成本略高，但换来的是无需强制用户先重跑 bootstrap/refresh 才能看到导出改善。
- 推翻条件：若未来引入显式的 block-level `artifact subtype` 或 `structured_output` schema，并承诺历史数据统一重建，则可以重新评估是否移除 export 侧的混合 code/prose 兜底逻辑。
- 影响范围：`src/book_agent/domain/structure/pdf.py`、`src/book_agent/services/export.py`、`tests/test_pdf_support.py`、`tests/test_persistence_and_review.py`、PDF 书籍的 bilingual / merged export 结构保真。
- 探针验证：已通过（`uv run --extra dev python -m pytest tests/test_pdf_support.py tests/test_persistence_and_review.py -k 'multiline_json_object or json_code_block_with_inline_trailing_prose or structured_output_intro_sentence_out_of_code_artifact or detects_multiline_json_artifact_text or split_json_code_artifact_with_trailing_prose_suffix or demote_short_prose_code_false_positive or promote_single_line_codeish_heading_to_code_artifact' -q`，以及 `uv run --extra dev python -m pytest tests/test_persistence_and_review.py -k 'merge_code_like_paragraphs_and_tables_into_single_code_artifact or export_service_reflows_flattened_code_artifact_text or export_service_leaves_non_code_preformatted_text_unchanged or render_blocks_treat_prose_like_code_block_with_targets_as_translated_paragraph' -q` 通过）。

## ADR-017：单行 structured-output 代码块与 EPUB frontmatter 噪音都必须在进入翻译链路前被压缩
- 状态：已验证（探针通过）
- 日期：2026-03-21
- 背景：主动质量扫描显示还有两类高成本问题没有被根治。其一，历史 PDF 样本里 `{"json": ...} This structured format ...` 这类单行 structured-output 代码块仍会在导出时把尾随正文吞进 code artifact；其二，真实 EPUB 样本还会把空 `titlepage.xhtml` 与 `brief-table-of-contents.html` 当成正文章节，引入无效翻译、review 噪音和导出污染。
- 候选方案：
  - 继续只修 multiline code block，把单行 mixed code/prose 交给人工发现
  - 在 export 层单点修单行 JSON，但不修 parser 和 EPUB spine 归一化
  - 同时补两条入口级防线：PDF parser/export 共同支持“单行 code + prose suffix”拆分；EPUB parser 在 spine 归一化阶段直接过滤空 titlepage 与 TOC-like frontmatter
- 最终决定：采用第三种。对单行 structured-output code fragment 复用更强的 `splitworthy single-line code fragment` 判定，让 parser 与 export 都能把尾随正文拆出去；对 EPUB 则在 parser 层过滤空章节、`contents/brief contents/toc` 样式的 spine 文档，以及仅承载书名的标题页。
- 代价与妥协：EPUB frontmatter 过滤保持保守，只处理空页、TOC-like 页和明确标题页；单行 code fragment 判定也只在“原块已被视为 code artifact / code_like 且后继为 prose”时触发，避免把普通正文误拆。
- 推翻条件：如果后续发现合法 EPUB 前言被大量误过滤，或单行代码拆分对非结构化正文产生明显误伤，需要回退到“仅打标不直接过滤/拆分”的方案。
- 影响范围：`src/book_agent/domain/structure/pdf.py`、`src/book_agent/services/export.py`、`src/book_agent/domain/structure/epub.py`、`tests/test_pdf_support.py`、`tests/test_persistence_and_review.py`、`tests/test_epub_parser.py`
- 探针验证：已通过（`uv run --extra dev python -m pytest tests/test_pdf_support.py tests/test_persistence_and_review.py -k 'single_line_json_code_block_with_trailing_prose or json_code_block_with_inline_trailing_prose or structured_output_intro_sentence_out_of_code_artifact or split_json_code_artifact_with_trailing_prose_suffix or split_single_line_json_code_artifact_with_trailing_prose_suffix' -q`、`uv run --extra dev python -m pytest tests/test_epub_parser.py -q`、`uv run --extra dev python -m pytest tests/test_persistence_and_review.py -k 'merge_code_like_paragraphs_and_tables_into_single_code_artifact or export_service_reflows_flattened_code_artifact_text or export_service_leaves_non_code_preformatted_text_unchanged or render_blocks_treat_prose_like_code_block_with_targets_as_translated_paragraph or demote_short_prose_code_false_positive or promote_single_line_codeish_heading_to_code_artifact' -q` 通过；真实 `build-an-ai-agent.epub` 解析章节数由 11 收敛到 9，真实 `Dedication` PDF 复导后 JSON 代码块与尾随正文已拆分）。 

## ADR-018：历史已入库 PDF 的混合 code/prose 污染必须通过“refresh + fragment repair”闭环修复，而不是只靠 export 临时拆显示
- 状态：已验证（真实样本通过）
- 日期：2026-03-21
- 背景：`Dedication` 的历史导出暴露出一个更深层问题：即使 export 已能把混合 code/prose block 临时拆开显示，旧数据库里的 block 结构仍是脏的，导致尾随 prose 没有中文 target，导出只是“看起来没污染代码块”，但正文仍残留英文。与此同时，历史 document title 也需要在 SQLite 老库里自动纠偏，不能依赖人工刷新单本书。
- 候选方案：
  - 继续只在 export 层拆混合 code/prose，接受历史库里永远保留脏 block
  - 强制对历史 PDF 整本重建 chapter/block/sentence/packet，再重新翻译所有受影响章节
  - 对新 parser 增加 same-anchor code continuation 合并与唯一 split anchor；对历史库通过 PDF structure refresh 把 split fragment metadata 回写到原 block，再用轻量 fragment repair 仅补译新增 prose suffix
- 最终决定：采用第三种。parser 现在会先合并同一 anchor 的连续 code continuation，再识别并拆出 `trailing prose suffix`；SQLite startup backfill 会用当前 `resolve_document_titles(...)` 语义自动纠正历史 `Dedication / Foreword / Preface` 级 document title；对历史 PDF，则通过 `PdfStructureRefreshService` 将 split fragment 作为 `refresh_split_render_fragments` 回写到原 code block，再由 `PdfProseArtifactRepairService.repair_mixed_code_prose_fragments(...)` 仅对 prose fragment 补译，最后 re-export。
- 代价与妥协：历史修复仍然没有把旧 92 章结构整本迁移成新 parser 的 12 章结构；本次闭环优先解决“书名正确 + code block 干净 + tail prose 有中文”，而不是做全量 chapter 结构重建。
- 推翻条件：若后续决定对历史 PDF 做统一 rebuild/migration，允许重建 chapter/block/sentence/packet 全栈状态，可重新评估是否移除 `refresh_split_render_fragments` 这一兼容层。
- 影响范围：`src/book_agent/domain/structure/pdf.py`、`src/book_agent/services/pdf_structure_refresh.py`、`src/book_agent/services/pdf_prose_artifact_repair.py`、`src/book_agent/services/export.py`、`src/book_agent/infra/db/sqlite_schema_backfill.py`、`tests/test_app_runtime.py`、`tests/test_pdf_support.py`、`tests/test_persistence_and_review.py`
- 探针验证：已通过（`uv run --extra dev python -m pytest tests/test_app_runtime.py tests/test_pdf_support.py tests/test_persistence_and_review.py -k 'sqlite_schema_backfill_corrects_auxiliary_pdf_document_titles_from_source_filename or recovery_merges_same_anchor_code_continuations_before_splitting_trailing_prose or pdf_structure_refresh_persists_split_trailing_prose_fragments_on_code_blocks or pdf_prose_artifact_repair_service_translates_refreshed_mixed_code_prose_fragments' -q` 通过；真实 `Dedication` 章节 `5543c2f4...` 经 refresh + fragment repair 后，JSON 代码块已独立保留，尾随 prose 已补成中文，merged export 标题也稳定为 `Agentic Design Patterns A Hands-On Guide to Building Intelligent Systems`）。

## ADR-019：Web UI 必须从“面板堆叠页”重构成“书籍译制工作台”，并在前端层记住当前书籍上下文
- 状态：已验证（入口回归通过）
- 日期：2026-03-21
- 背景：原首页虽已产品化，但仍带有明显“把接口能力平铺到一个页面”的痕迹：上传、当前文档、run 进度、导出、历史都按同构面板堆叠，用户很难在第一屏建立“当前书籍是谁 / 到了哪一环 / 为什么还不能导出 / 现在能下载什么”的稳定心智模型。与此同时，页面刷新后不会恢复上次查看的 document，上下文容易丢失。
- 候选方案：
  - 保持现有结构，仅继续调样式、文案和卡片细节
  - 在旧布局上增加更多摘要卡片与状态说明
  - 直接删除旧页面实现，按“当前书籍为中心、历史书库为侧栏、运行与交付合并解释”的工作台信息架构重写整页，并把当前 document id 持久化到前端本地存储
- 最终决定：采用第三种。首页被重写成“整书译制工作台”：顶部不再是假 hero + 功能卡，而是明确界定产品定位；主列按 `新建书籍任务 -> 当前书籍 -> 运行总览 -> 交付资产/复核阻塞 -> 章节注意区/最近事件` 组织；历史记录收敛到 sticky 书库侧栏。前端新增 `localStorage` 上下文恢复逻辑，刷新后会优先恢复上次查看的书籍，若没有则自动打开当前仍在处理中的 document。
- 代价与妥协：这次重写仍然保留 FastAPI 直出单文件 HTML/JS 架构，没有同步引入独立前端构建链；因此“组件化复用”和“视觉系统分文件拆分”仍受限于当前服务端页面模式。
- 推翻条件：如果后续需要发展成多页产品、复杂路由或多人协作前端工程，再重新评估是否把当前单文件页面拆到独立前端工程。
- 影响范围：`src/book_agent/app/ui/page.py`、`tests/test_frontend_entry.py`、首页工作流理解、当前 document 恢复体验。
- 探针验证：已通过（`uv run pytest tests/test_frontend_entry.py -q` 通过；并复跑 `bootstrap_upload_accepts_epub_file`、`test_document_history_includes_latest_run_stage_and_progress`、`test_document_history_supports_search_and_filters` 相关 API 回归，确认重写后的页面仍依赖同一组 `/v1` 接口契约）。

## ADR-020：工作台首页必须采用“主流程全宽、次要信息下沉”的布局，而不是窄侧栏历史书库
- 状态：已验证（入口与 API 探针通过）
- 日期：2026-03-21
- 背景：第一版工作台虽然已经摆脱旧的面板堆叠页，但仍保留了右侧 `sticky` 书库历史。真实页面暴露出两个问题：一是长标题、长路径和筛选器会把窄侧栏挤爆，导致书库区域截断；二是当前书籍、运行总览和交付解释这些高价值信息被挤在非全宽区域内，视觉上更像“左右双栏后台页”，而不是“围绕当前书籍展开的译制工作台”。同时，过暖的奶油底色和较重的柔光也削弱了专业感。
- 候选方案：
  - 保留右侧历史侧栏，只做宽度和截断修补
  - 继续双栏结构，但在右栏里减少筛选器和文本密度
  - 将当前上传、当前书籍、运行总览、交付与阻塞全部改为主流程全宽堆栈，把章节注意区/最近活动下沉到中下部辅助区，再把书库历史移到底部全宽展示，同时收敛视觉系统为更冷静的专业出版工作台配色与无衬线层级
- 最终决定：采用第三种。首页现在以全宽内容栈组织高优先级区域，底部再展示辅助区和书库历史；历史卡片里的长路径不再挤在摘要文案内，而是进入独立的 `history-path` 容器。字体改为现代无衬线主导，背景从暖奶油渐变切到冷静的 slate/blue-gray 体系，保留少量深 teal 作为操作重点色。
- 代价与妥协：视觉上减少了“编辑部/出版样张”式的戏剧性，换来更稳定的工具感；同时书库历史不再始终与首屏并列，用户需要轻微下滚才能浏览更多历史条目，但当前书籍的主任务路径更清楚。
- 推翻条件：如果后续产品确实演变为“多文档并发调度台”，而不是围绕单本书深入处理的工作台，再重新评估是否恢复并列式多列布局；在此之前，不再回退为窄侧栏书库。
- 影响范围：`src/book_agent/app/ui/page.py`、`tests/test_frontend_entry.py`、首页全局视觉系统、信息架构与历史区域可读性。
- 探针验证：已通过（`uv run python -m py_compile src/book_agent/app/ui/page.py`、`uv run --extra dev python -m pytest tests/test_frontend_entry.py -q`、`uv run --extra dev python -m pytest tests/test_api_workflow.py -k 'test_document_history_supports_search_and_filters or test_document_history_includes_latest_run_stage_and_progress or bootstrap_upload_accepts_epub_file' -q` 通过；并确认旧的 `workspace-grid / rail-column / operations-grid / ledger-grid` 结构已从页面实现中移除）。

## ADR-021：历史 EPUB 必须支持结构刷新纠偏，并以中文书名产出整书导出物
- 状态：已验证（真实样本通过）
- 日期：2026-03-21
- 背景：真实 EPUB `Agentic AI Theories and Practices` 的历史导出暴露出两类链路性问题：一是旧 parser 把 `nav epub:type="page-list"` 当成章节目录，导致章节标题落成 `xxxviii / 20 / 49 ...` 这类页码，进一步污染 TOC、merged export 与 review 判断；二是 document-level 书名只有主标题 `Agentic AI`，副标题 `Theories and Practices` 丢失，导出物也没有中文书名别名，用户在文件系统和下载层都难以拿到可交付的中文产物。
- 候选方案：
  - 只在 export 层做兜底，把页码章节标题和英文主标题在展示时临时替换掉
  - 直接要求历史 EPUB 全量重新 bootstrap / 重新翻译
  - 在 parser 层修正 EPUB `toc/page-list` 读取与 title/subtitle 解析语义；同时提供 `refresh_epub_structure(...)` 给历史 document 回写新结构，再在 export 层自动回填 `title_tgt` 并生成中文书名导出别名
- 最终决定：采用第三种。EPUB parser 现在只优先读取 `nav[epub:type="toc"]`，不再让 `page-list` 覆盖章节标题；metadata 会解析主标题 + 副标题，并写入 `document_title_src`。对历史库，新增 `EpubStructureRefreshService`，允许在不重翻正文的前提下回写修正后的 document/chapter 标题与 metadata。导出时若 `document.title_tgt` 为空，则从前言标题页的已翻译 heading 自动合成中文书名；同时在导出目录为整书阅读稿生成 `《书名》-中文阅读稿.html` 别名，下载接口对整书包、整书稿、双语章节包和审校包统一使用 `《书名》-...` 命名。
- 代价与妥协：章节级 bilingual/review 的 canonical 文件名仍保留 `bilingual-<chapter_id>.html` / `review-package-<chapter_id>.json` 以避免破坏历史导出记录与数据库引用；本次优先保证用户能在下载与整书交付层拿到中文命名产物。
- 推翻条件：如果后续决定让所有 export record 都彻底改成“人类可读文件名即 canonical 路径”，需要连带迁移导出记录、sidecar manifest 与打包逻辑；在此之前，保留 canonical + 中文别名的双轨策略。
- 影响范围：`src/book_agent/domain/structure/epub.py`、`src/book_agent/domain/document_titles.py`、`src/book_agent/services/epub_structure_refresh.py`、`src/book_agent/services/export.py`、`src/book_agent/services/workflows.py`、`src/book_agent/app/api/routes/documents.py`、`tests/test_epub_parser.py`、`tests/test_api_workflow.py`
- 探针验证：已通过（`uv run --extra dev python -m pytest tests/test_epub_parser.py -q`、`uv run --extra dev python -m pytest tests/test_api_workflow.py -k 'test_refresh_epub_structure_repairs_legacy_page_number_titles_and_combined_book_title or test_merged_html_export_backfills_translated_document_title_and_uses_human_download_name' -q` 通过；真实文档 `cf32d839...` 经 `refresh_epub_structure + export_document_merged_html` 后，document title 已变为 `Agentic AI: Theories and Practices / 代理人工智能：理论与实践`，章节标题从页码恢复为真实章节名，导出目录新增 `《代理人工智能：理论与实践》-中文阅读稿.html`，`merged-document.html` 的 `<title>` 与封面 `<h1>` 也同步更新为中文书名）。

## ADR-022：book-PDF merged export 必须以“真实顶层章节”为目录单元，并将误译成正文的 frontmatter heading 降级
- 状态：已验证（真实样本通过）
- 日期：2026-03-21
- 背景：真实 PDF `Dedication` 的 merged export 暴露出两个互相放大的问题：一是 raw chapter 标题采用 `Chapter 1_ Prompt Chaining` 这种下划线风格，旧的 merged 分组只识别 `Chapter 1:` / `Chapter 1.`，导致第 1–21 章全部没被认成顶层章节，而是整体吞进了 `Introduction`；二是 `Preface` 这类 frontmatter heading 的 target 会偶发被污染成长段正文，虽然章节头已靠 fallback 不再直接显示这段 prose，但正文里仍把它当成二级标题渲染，结构上依旧错误。
- 候选方案：
  - 继续只依赖 parser 侧 raw chapter 边界，接受 merged export 对 `Chapter 1_ ...` 风格无能为力
  - 在真实文档上手工清目录，不修通用导出逻辑
  - 在 export 侧补 book-PDF 顶层章节识别：允许 `Chapter 1_ ...` 参与主章序列；同时把“target 明显像正文”的 heading block 自动降级为 paragraph，并让章节头回退到本章的 frontmatter 标题语义
- 最终决定：采用第三种。`_extract_main_chapter_number(...)` 现在支持 `Chapter N_ ...` 这类历史 raw title；book-PDF 的 `_visible_merged_chapters(...)` 因而能重新恢复 `第1–21章 + 附录A–G` 的顶层目录。对 `Preface -> 长正文` 这类坏 heading，export 会在 `_repair_book_pdf_render_blocks(...)` 中将其降级为段落；章节标题解析则只信任“首个内容块就是有效 heading”这一条件，否则回退到章节级标题，并在 PDF frontmatter 场景把 `Dedication / Foreword / Introduction` 本地化为 `致谢 / 前言 / 介绍`。
- 代价与妥协：这次修的是 merged export 侧的可见章节与 heading 呈现，不会反向修改数据库中的 raw chapter 切分；也就是说，历史库仍保留 92 个 raw chapter，只是 merged 交付面恢复为用户真正需要的 31 个顶层目录单元。
- 推翻条件：如果后续决定对历史 PDF 做整本 chapter 结构迁移，允许把 raw chapter 本身从 92 个重整为 31 个，再重新评估是否保留 export 侧这层“顶层章节恢复”兼容逻辑。
- 影响范围：`src/book_agent/services/export.py`、`tests/test_persistence_and_review.py`、真实 merged artifact `artifacts/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.html`
- 探针验证：已通过（`uv run --extra dev python -m pytest tests/test_persistence_and_review.py -k 'visible_merged_chapters_group_pdf_auxiliary_sections_under_real_top_level_titles or visible_merged_chapters_recognize_underscore_numbered_book_pdf_titles or merged_titles_fall_back_when_first_heading_target_looks_like_prose or book_pdf_prose_like_intro_heading_is_demoted_and_title_falls_back_to_localized_frontmatter' -q` 通过；真实文档 `67283f52...` 重导后 manifest 已恢复为 31 个目录项：`致谢 / 前言 / 介绍 / 第1–21章 / 附录A–G`，且 `欢迎阅读《智能体设计模式...》` 已从 heading 降级为 paragraph）。

## ADR-022：merged export 的结构保真必须显式依赖“段落拼接规则 + 保守 code reflow + 裸函数调用识别”
- 状态：已验证（定向回归通过）
- 日期：2026-03-21
- 背景：最新真实样本暴露出 4 个仍会直接损伤可交付体验的问题：无序列表译文在 export 阶段被 inline join 成一整段；参考文献编号与 URL 串在一起，甚至会误落进 code artifact；原本格式良好的 JSON / 代码块在导出时被二次 reflow，缩进和原排版被改坏；`run_reflection_loop()` 这类裸函数调用没有被识别为代码，导致代码块尾行外漏、后续正文也无法在正确边界开始翻译。
- 候选方案：
  - 继续依赖现有 inline join / code reflow heuristics，只在提示词层要求模型“尽量保留格式”
  - 只修 export 的表层 HTML 渲染，不改底层代码/参考文献识别
  - 同时收紧三层规则：target 段落拼接显式保留列表与参考文献边界；只有明显 OCR/折行损坏的代码才做 reflow；parser/export 共用的 embedded-code heuristics 显式识别裸函数调用
- 最终决定：采用第三种。export 现在对 bullet / ordered list / reference listing 做结构化 target join，不再把这些段落一律 inline 拼接；对已具备稳定结构的 multiline JSON / code block，保留原换行和缩进，只对明显坏掉的代码做修复性 reflow；同时把 `name(...)` 这种裸函数调用纳入 embedded-code line 判定，让 mixed code/prose split 在正确的代码尾行处结束。
- 代价与妥协：export 层的文本拼接逻辑会比以前更复杂，并继续保留少量 reference/list/code 的专门 heuristics；但这是为了换取导出稿在“阅读结构”和“复制可执行性”两个维度都稳定可交付。
- 推翻条件：如果未来引入更强的 block-level 结构标签（例如显式 list/reference/code fragment schema）并承诺历史数据全量刷新，可重新评估是否移除这批 export-side heuristics。
- 影响范围：`src/book_agent/services/export.py`、`src/book_agent/domain/structure/pdf.py`、`tests/test_persistence_and_review.py`、`tests/test_pdf_support.py`、merged markdown/html 结构保真。
- 探针验证：已通过（`uv run pytest tests/test_persistence_and_review.py -k 'preserves_well_formatted_json_layout or preserves_bullet_segment_breaks or reference_listing_code_block'` 与 `uv run pytest tests/test_pdf_support.py -k 'bare_function_call_inside_code_prefix or splits_leading_code_prefix_from_mixed_body_block or splits_code_like_block_with_inline_trailing_prose or splits_json_code_block_with_inline_trailing_prose or splits_single_line_json_code_block_with_trailing_prose'` 通过）。

## ADR-023：PDF 里的“标签化正文”和“跨行代码续写”必须先于代码块合并恢复，避免第二章路由类样本被误判成代码
- 状态：已验证（真实章节渲染通过）
- 日期：2026-03-21
- 背景：真实 PDF `67283f52...` 的第二章同时暴露了两类边界误判：一是 `At a Glance` 里的 `What:` / `Why:` 标签正文被当成 `key: value` 式单行代码前缀，结构层因此把正文尾部塞进 `refresh_split_render_fragments`；二是 LangChain 跨页代码里的 `RunnableBranch`、被 OCR 折断的注释续行等没有被视为代码连续体，导致整个长代码块在中途被错误截断。更糟的是，这些坏块在进入书籍专用修复前，还会被早期的“相邻代码块合并”提前揉在一起，使导出层更难恢复原结构。
- 候选方案：
  - 继续沿用现有 `single-line code prefix` / `adjacent code merge` 规则，只对真实文档做人工后处理
  - 只在 export 层做 HTML/Markdown 表层替换，不修 parser/export 共用的代码边界识别
  - 在 parser/export 共用 heuristics 里补两类规则：对 `What/Why/Rule of Thumb` 这类标签化正文显式豁免 `structured-data/code` 判定；对 import continuation、短注释续行等补代码连续性识别；同时在 `_render_blocks_for_chapter(...)` 里禁止带 `refresh_split_render_fragments` 的块过早参与相邻代码合并
- 最终决定：采用第三种。`pdf.py` 现在新增了 `labeled prose` 识别与 `code continuation` 识别，`What/Why/...` 不再被当成单行代码前缀，`RunnableBranch` 和短注释续行也能继续留在代码块里。`export.py` 则增加了两层兜底：一是对历史库里已经写坏的 `refresh_split_render_fragments` 做按需恢复，把标签化正文恢复为 paragraph，把伪 prose fragment 恢复回 code；二是在 render block 初建阶段，禁止尚带 `refresh_split_render_fragments` 的 artifact 提前参与相邻 code merge，必须等 book-PDF 修复跑完后再决定是否合并。
- 代价与妥协：代码/正文边界识别进一步依赖启发式规则，`What/Why/Rule of Thumb` 等标签名采用了白名单策略；这比完全通用的语法判定更保守，但能把真实书稿里最常见、最伤交付的误判先压住。
- 推翻条件：如果后续 parser 能稳定输出显式的 `labeled-prose` / `code-continuation` 结构标签，并允许历史 PDF 全量结构刷新，则可以回收这批 export-side 恢复逻辑，把复杂度重新下沉到结构层。
- 影响范围：`src/book_agent/domain/structure/pdf.py`、`src/book_agent/services/export.py`、`tests/test_pdf_support.py`、`tests/test_persistence_and_review.py`、真实文档 `67283f52...` 第二章路由导出质量。
- 探针验证：已通过（`uv run python -m py_compile src/book_agent/domain/structure/pdf.py src/book_agent/services/export.py tests/test_pdf_support.py tests/test_persistence_and_review.py` 通过；真实 chapter render 已确认 LangChain 代码块恢复为单一 code artifact，`At a Glance` 的 `What` / `Why` 恢复为两个独立 paragraph；环境内缺少 `pytest/httpx`，因此这次保留了手工 smoke 与真实章节渲染验证记录）。

## ADR-024：参考文献、列表与跨页代码的导出保真必须基于“逻辑条目”而不是原始换行
- 状态：已验证（真实整书重导出通过）
- 日期：2026-03-21
- 背景：真实 PDF `67283f52...` 的 merged 导出又暴露出 4 个直接影响交付的问题：一是参考文献页里 `1/2/3` 与 URL 被压进同一段，旧规则只按原始换行拆分，遇到 inline 编号和跨行 URL 时完全失效；二是单个 target segment 内的多条 bullet 列表会被一行化，丢掉原始换行；三是 `● Prompt 1: ...` 这类单条列表项因包含冒号和括号，被单行 code heuristic 误升为代码块；四是跨页代码恢复后的 refresh fragment 可能从字符串字面量中段开始，旧逻辑无法把它重新并回代码，也无法在 merge 后把尾部正文再次剥离，导致第 7–9 页 LangChain 示例把说明正文一起吞进代码块，markdown 还会对恢复后的代码做二次 reflow。
- 候选方案：
  - 继续依赖原始换行和单段 target，接受 reference/list/code 在真实 PDF 上偶发串段
  - 仅在最终 HTML 上做表层替换，不修 render block 级结构
  - 在 export 层引入“逻辑条目”规则：reference 先做 inline marker / URL continuation 归并，再按 title+locator 输出；list target 按 source bullet 结构重排；single-line codeish heuristic 对 list item 显式豁免；refresh code fragment 允许从引号续行恢复，并在 markdown 导出时对跨页恢复后的代码块禁用二次 reflow
- 最终决定：采用第三种。reference 现在会先拆 inline 编号、把跨行 URL 拼回完整 locator，再按 `title / url / title / url` 的逻辑块输出；list target 若 source 明显是多条 list item，即使 translation 只回一段，也会重新按 marker 换行；`_looks_like_single_line_codeish_text(...)` 与 code-artifact rejection 对 `● / • / 1.` 这类 list item 不再误判；refresh code fragment 则新增“引号开头字符串续行”识别，并在 code restore 后再次执行 mixed code/prose split。对于带 `cross_page_repaired / export_refresh_split_code_restored` 标记的代码块，markdown 导出直接保留现有换行，避免再次被 reflow 压扁。
- 代价与妥协：export 侧 heuristic 进一步增多，尤其 reference/list/code 三类逻辑开始共享更多结构化补救规则；但这比把真实 PDF 的坏结构直接暴露给最终阅读稿更可控，也能在不做整库结构迁移的前提下快速修复历史产物。
- 推翻条件：如果未来 parser 能稳定产出显式 `reference_entry / locator / list_item / code_fragment` 结构，并完成历史 PDF 全量 refresh，则可回收这层 export-side 逻辑条目修复，改由结构层直接保证。
- 影响范围：`src/book_agent/services/export.py`、`src/book_agent/domain/structure/pdf.py`、`tests/test_persistence_and_review.py`、`tests/test_pdf_support.py`、真实导出目录 `artifacts/exports/67283f52-b775-533f-988b-c7433a22a28f/`
- 探针验证：已通过（`uv run python -m unittest tests.test_persistence_and_review.PersistenceAndReviewTests.test_export_service_preserves_restored_cross_page_code_layout_in_markdown tests.test_persistence_and_review.PersistenceAndReviewTests.test_render_blocks_split_inline_reference_entries_and_wrapped_urls tests.test_persistence_and_review.PersistenceAndReviewTests.test_render_blocks_reflow_collapsed_inline_bullet_list_target tests.test_persistence_and_review.PersistenceAndReviewTests.test_render_blocks_keep_single_bulleted_prompt_item_as_paragraph tests.test_persistence_and_review.PersistenceAndReviewTests.test_render_blocks_restore_quoted_code_refresh_split_and_strip_trailing_prose` 通过；真实 `merged-document.md/html` 已确认参考文献 1/2/3、Prompt 列表换行、单条 `● Prompt 1` 与第 7–9 页 LangChain 代码块恢复正常）。

## ADR-025：翻译系统提示词先新增“高保真但不生硬”的候选 profile，再用真实样本 A/B 决定是否切生产
- 状态：已验证（真实 A/B 已完成）
- 日期：2026-03-21
- 背景：默认 `role-style-v2` 的真实输出更顺，但仍会把本来朴素的技术书比喻润成“服务化”“照料式”中文；旧 `current` profile 又更贴源，却常留下明显的翻译腔。产品卖点要求落在“高保真、不生硬、符合中文技术图书阅读习惯”的中线，而不是在“顺”与“硬”之间二选一。
- 候选方案：
  - 直接回切旧 `current` 高保真 profile
  - 保持 `role-style-v2` 不变，仅靠 review/rerun 兜底
  - 新增一个只改 system prompt 的候选 profile，保留 `role-style-v2` 的上下文消费结构，用真实样本做同模型 A/B
- 最终决定：采用第三种。新增 `role-style-faithful-v4`，system prompt 明确压住 3 类跑偏：解释性增译、语气升级、服务化/营销化润色；同时继续沿用 `role-style-v2` 的 section/intent/literalism user prompt 结构，让实验对照更集中在 system prompt 本身，而不是把整套 prompt contract 一起改掉。
- 代价与妥协：候选 profile 暂不进入默认生产链路，也暂不进入 compact prompt 路径，因此短期不会自动影响全部 packet，且 token 成本会略高于当前默认。
- 推翻条件：若后续更多真实样本表明 `role-style-faithful-v4` 依然频繁产出抽象尾句、出版感不足，或收益不足以支撑更长 prompt，则继续迭代候选 profile，不直接切生产。
- 影响范围：`src/book_agent/workers/translator.py`、`tests/test_translation_worker_abstraction.py`、真实 prompt A/B 验证流程。
- 探针验证：已通过（同一 `deepseek-chat` 样本已完成 `v4/v5/v6` 多轮 A/B：候选 profile 能部分压低“菜品 / 悉心照料 / 贴心服务”类润色，但模型仍会反复回到“长期服务 / 连贯性 / 关怀”这类抽象服务话术，说明仅靠通用 system prompt 已接近收益边界；下一步应继续把约束下沉到 source-aware literalism guardrails / user prompt contract，而不是无限堆 system prompt）。

## ADR-026：当“高保真但不生硬”卡在具体表达层时，应优先下沉 source-aware literalism guardrails，而不是继续堆通用 system prompt
- 状态：已验证（真实 A/B 已完成）
- 日期：2026-03-21
- 背景：`role-style-faithful-v4/v5/v6` 的多轮 system-prompt 实验显示：单纯强化“不要服务化/不要抽象化”并不足以稳定压住模型在特定句型上的习惯性润色。对 `provide consistency and care over time` 这类尾句，模型会反复回到“长期服务 / 连贯性 / 关怀 / 贴心服务”这一类抽象服务话术。
- 候选方案：
  - 继续扩 system prompt，追加更多泛化禁令
  - 把具体坏味道下沉为 source-aware literalism 规则，并在 user prompt contract 中提高其优先级
- 最终决定：采用第二种。新增 `consistency_care_service_literal` 规则，把这类句子直接编译成 packet 级 `literalism_guardrails`；同时在翻译 contract 中加入“若存在 Source-Aware Literalism Guardrails，则其优先于泛化润色”的硬约束，禁止把具体比喻升级成抽象服务或口号式中文。
- 代价与妥协：prompt 规则会更依赖高命中、低误报的窄规则积累；但这比无限膨胀通用 system prompt 更可控，也更容易用 review/rerun 闭环持续修正。
- 推翻条件：若后续真实样本表明同类规则误报高、迁移性差，或者模型对这类 narrow guidance 不再敏感，再重新评估是否需要更大粒度的 material-aware prompt 重构。
- 影响范围：`src/book_agent/services/style_drift.py`、`src/book_agent/services/context_compile.py`、`src/book_agent/workers/translator.py`、`tests/test_translation_worker_abstraction.py`、`tests/test_persistence_and_review.py`、真实 prompt A/B 验证流程。
- 探针验证：已通过（完整 `context_compile -> prompt -> deepseek-chat` 复测中，`role-style-v2` 与 `role-style-faithful-v6` 都开始稳定产出“长期稳定、周到地照应你”这类具体表达，不再回落到“提供连贯性和关怀 / 贴心服务”）。

## ADR-026：代码块导出必须先做“安全修复”，再决定是否原样保留，且 html/md 必须共用同一套 code normalization
- 状态：已验证（真实代码块抽检通过）
- 日期：2026-03-21
- 背景：真实 PDF `67283f52...` 暴露出代码区域保真还存在两类系统性问题：一类是 markdown 因 `cross_page_repaired / export_code_blocks_merged` 标记而直接“保护原样”，把已经损坏的缩进和视觉换行原封不动带进最终产物；另一类是 HTML 侧单独执行的 code reflow 过于激进，会把带撇号的注释、docstring 和后续代码误拼成一行。与此同时，相邻 code block 在跨页合并时还可能保留重叠前缀，导致整段代码重复；而 `name: type = ...`、`"summary": ...` 这类混合结构又会把真实 Python 代码误判成“稳定 structured data”，直接跳过修复。
- 候选方案：
  - 继续维持 markdown/HTML 两套分离逻辑，只在提示词层要求模型“保留代码格式”
  - 让 markdown 继续保留坏代码，单独增强 HTML reflow
  - 把代码块统一收敛到一条 normalization 管线：先判断是否真的需要修，再用保守的行级修复恢复字符串/注释/缩进，并在 code merge 时去掉重叠前缀
- 最终决定：采用第三种。export 现在对 html 和 markdown 共用 `_normalize_code_artifact_text(...)`；只有真正“看起来像坏掉代码”的块才会进入 reflow。reflow 本身也改成更保守：不再因为 `(` / `[` / `{` 或 docstring 里的 `Returns:` 之类误触发大面积拼接；会显式区分普通引号、三引号、词内撇号、纯注释续行和内联注释续行；并在缩进恢复时补“terminal statement 后 dedent”和“closing bracket 回退”规则。相邻 code block 合并则新增前缀/重叠检测，避免跨页重复代码被整段拼接。
- 代价与妥协：export 侧的 code heuristics 会继续变复杂，且某些多行 prompt/list literal 仍可能采用“可复制优先”的近似排版，而不是百分百回到作者原始手工换行；但相比把坏格式直接交给最终阅读稿，这条路线更稳定，也更便于继续在真实样本上增量细化。
- 推翻条件：如果后续结构层能直接产出更强的 code fragment schema（显式 continuation / overlap / docstring / comment metadata）并完成历史 PDF 刷新，就可以把这批 export-side code repair 下沉回 parser，减少导出时启发式复杂度。
- 影响范围：`src/book_agent/services/export.py`、`tests/test_persistence_and_review.py`、真实导出目录 `artifacts/exports/67283f52-b775-533f-988b-c7433a22a28f/`。
- 探针验证：已通过（`uv run pytest tests/test_persistence_and_review.py -k "repairs_broken_cross_page_code_layout_consistently_for_html_and_markdown or reflow_dedents_after_terminal_statement or reflow_keeps_apostrophe_lines_from_collapsing_into_code or reflow_keeps_docstring_section_labels_inside_function_scope or merge_adjacent_code_blocks_dedupes_overlapping_prefix or preserves_restored_cross_page_code_layout_in_markdown or preserves_well_formatted_json_layout"` 与 `uv run pytest tests/test_pdf_support.py -k "bare_function_call_inside_code_prefix or splits_leading_code_prefix_from_mixed_body_block or splits_code_like_block_with_inline_trailing_prose or splits_json_code_block_with_inline_trailing_prose or splits_single_line_json_code_block_with_trailing_prose or recovery_keeps_import_and_wrapped_comment_continuations_inside_code_block or code_continuation_recognizes_opening_quoted_string_after_call_prefix or recovery_does_not_split_labeled_prose_prefix_as_code"` 通过；真实 chapter 抽检已确认第 1/2/3 章跨页代码块在最终 markdown 里恢复了 import 续行、try/except 缩进、docstring 区块和注释续行，且 ADK 重叠代码头已去重）。

## ADR-027：翻译上下文应优先保留“有判别力的记忆”，而不是为所有段落一律注入前文译文
- 状态：已验证（真实 packet 复测完成）
- 日期：2026-03-21
- 背景：真实翻译样本暴露出一个成本/收益失衡点：当前 compiler 虽已大幅裁掉原始 `prev_blocks / next_blocks`，但 `Previous Accepted Translations` 仍会默认带入多个历史片段。对一些自洽长段落，这些历史译文既未明显改善质量，反而持续拉高 prompt token，并把旧术语/旧坏味道带回当前 packet。
- 候选方案：
  - 继续默认注入全部 `Previous Accepted Translations`
  - 一刀切移除所有前文译文
  - 仅在短句、承接句、续接句或 shift 结构里保留最近少量前文译文，其余长段落默认不注入
- 最终决定：采用第三种。context compile 新增了更保守的 `Previous Accepted Translations` 选择逻辑：只有明显依赖前文承接的 packet 才会保留最近 2 条前文译文；对自洽长段落则直接清空这部分上下文。这样既保留了真正需要的局部衔接，也避免为低收益连续性额外消耗 token。
- 代价与妥协：少数长段落若确实存在隐性照应，可能失去一小部分邻接提示；但这类风险远小于“把一整串低相关历史译文反复喂回模型”的成本与污染风险。
- 推翻条件：如果后续真实样本表明某类长段落仍稳定依赖前文译文才能避免指代歧义，应把条件放宽为“检测到明确照应链时保留”，而不是回到默认全量注入。
- 影响范围：`src/book_agent/services/context_compile.py`、`tests/test_translation_worker_abstraction.py`、真实 packet 复测脚本。
- 探针验证：已通过（同一真实 packet `670447b0...` 上，默认链路在改动后 `prev_translated_blocks 4 -> 0`、`token_in 1927 -> 1561`，译文质量未变差，且 prompt 体积明显收缩）。

## ADR-028：对已知“翻译腔术语”应做运行时归一化，避免旧锁定值持续污染后续 packet
- 状态：已验证（compile/review/rerun 全链路定向回归通过）
- 日期：2026-03-21
- 背景：真实 packet 复查表明，`agentic AI` 当前之所以持续被译成 `智能体式AI`，并不只是模型自由发挥，而是 chapter memory / locked terms / rerun override 会把这一旧译法当成“标准答案”反复注入当前 prompt，导致新 packet 即使上下文更干净，也仍会被旧坏术语拖回翻译腔。
- 候选方案：
  - 仅靠新的 system prompt / user prompt 继续压制 `智能体式AI`
  - 直接手工清库，逐条修旧 memory 与 term entry
  - 在运行时增加窄范围术语归一化：对已知坏锁定值进行 compile/review/rerun/lock 全链路纠偏，同时增加 source-aware guardrail
- 最终决定：采用第三种。系统现在会把 `agentic AI -> 智能体式AI` 自动归一化为 `智能体AI`，并在 literalism guardrail 中显式告诉模型“避免 `智能体式AI` 这种生硬术语”。这样即使历史 memory 或旧 issue evidence 仍带着坏值，也不会继续把它当成当前 packet 的 authoritative rendering。
- 代价与妥协：这是一条针对已知坏术语的窄规则，而不是通用术语引擎；它解决的是“旧坏值持续污染”的问题，不意味着所有概念翻译都能靠硬编码纠偏。
- 推翻条件：如果后续术语策略升级为更系统的 domain glossary / title-vs-body register 体系，应把这类窄规则下沉到统一术语层；若用户决定 `agentic AI` 在正文中应统一改为别的标准译法，也只需替换这一归一化规则。
- 影响范围：`src/book_agent/services/term_normalization.py`、`src/book_agent/services/context_compile.py`、`src/book_agent/services/chapter_concept_lock.py`、`src/book_agent/services/review.py`、`src/book_agent/orchestrator/rerun.py`、`src/book_agent/services/style_drift.py`、相关回归测试。
- 探针验证：已通过（定向回归确认：旧 `智能体式AI` concept lock 会被归一化为 `智能体AI`；旧 term conflict evidence 生成的 rerun override 也会自动改正；真实 packet 复测里，当前生产 profile 已从 `智能体式AI` 收敛为 `智能体AI`）。

## ADR-029：在上下文收紧与术语归一化已生效后，将默认翻译 prompt profile 正式切换到 `role-style-faithful-v6`
- 状态：已验证（真实 packet 复测完成）
- 日期：2026-03-22
- 背景：前一轮已证明两个关键事实：一是坏输出的主因不是“额外放了原始前后文”，而是 prompt 风格约束不够窄；二是在上下文收紧与 `agentic AI` 术语归一化之后，默认链路已经更干净，此时继续停留在 `role-style-v2` 会保留较强的“顺但偏抽象”的倾向。我们已经多轮 A/B 验证 `role-style-faithful-v6` 在具体比喻、术语自然度和尾句控制上更接近产品卖点。
- 候选方案：
  - 保持默认 `role-style-v2`，只在人工实验或显式配置时使用 `v6`
  - 直接把默认切到 `role-style-faithful-v6`，并同步所有默认入口
- 最终决定：采用第二种。默认翻译配置现已切到 `role-style-faithful-v6`，并同步更新 `Settings`、`LLMTranslationWorker`、`PacketExperimentOptions` 与 `TranslationChapterSmokeOptions`，避免线上默认链路与实验/烟雾验证仍停留在旧 profile。
- 代价与妥协：`v6` 的 system prompt 更长，单次 prompt token 会略高于 `v2`；但在上下文收紧后，这个额外成本仍处于可接受范围，而且换来的是更稳定的高保真与更少的抽象服务化尾句。
- 推翻条件：如果后续真实整章/整书验证表明 `v6` 在别的材料类型上出现系统性副作用，或者 prompt 成本增幅超过质量收益，再考虑材料分流或回退。
- 影响范围：`src/book_agent/core/config.py`、`src/book_agent/workers/translator.py`、`src/book_agent/services/packet_experiment.py`、`src/book_agent/services/translation_chapter_smoke.py`、`tests/test_translation_worker_abstraction.py`。
- 探针验证：已通过（默认配置下的真实 packet `670447b0...` 复测表明：当前运行 profile 已是 `role-style-faithful-v6`，`Agentic AI` 保持为 `智能体AI`，尾句稳定落在“长期稳定、周到地照应你”，未回退到“连贯性和关怀/贴心服务”式抽象表达）。

## ADR-030：PDF mixed code/prose repair 必须对齐最终 render 路径，而不是仅依赖原始 block 文本
- 状态：已验证（真实整书重导出完成）
- 日期：2026-03-22
- 背景：从第 5 章开始的真实 PDF 样本暴露出一个关键差异：不少跨页代码问题并不是在原始 block 文本里直接表现为“代码 + 正文”，而是在 export/render 阶段经过跨页恢复、refresh split 修复或 code normalization 后，才显式长出 `leading_prose_prefix / trailing_prose_suffix`。原先 `repair_mixed_code_prose_blocks(...)` 只扫描原始 code block，因此会漏掉这类“render 时才显形”的正文泄漏。
- 候选方案：
  - 继续只基于原始 block 文本做 mixed code/prose repair
  - 在 repair 阶段改为直接扫描真实 `render_blocks`，以 `pdf_mixed_code_prose_split` 元数据为准回写目标译文
- 最终决定：采用第二种。`repair_mixed_code_prose_blocks(...)` 现在直接调用 `ExportService._render_blocks_for_chapter(...)` 生成真实 render blocks，并以渲染后出现的 `leading_prose_prefix / trailing_prose_suffix` 段落作为候选，再把目标译文持久化回原始 code block 的 `mixed_code_prose_repair_targets`。这样 repair 逻辑与最终 merged html/md 的呈现路径保持一致，不再漏掉 render-time 才出现的跨页正文泄漏。
- 代价与妥协：repair 服务现在依赖 export repository / render 逻辑，耦合度比“只看原始 block”更高；但换来的是对真实最终产物更可靠的修复覆盖率。
- 推翻条件：如果未来 parser/refresh 层已经能在入库前稳定拆好所有 mixed code/prose，不再需要 render-time 才识别出段落泄漏，则可以把 repair 候选重新下沉回结构层。
- 影响范围：`src/book_agent/services/pdf_prose_artifact_repair.py`、`src/book_agent/services/export.py`、`tests/test_persistence_and_review.py`、真实文档 `67283f52...` 的 merged html/md 导出。
- 探针验证：已通过（真实文档从第 5 章到末章重跑后，Chapter 6 / 7 / 10 / 19 中代码块后的英文正文均已在最终导出物中恢复为中文；render residual 仅剩 Glossary 的 2 个非章节块，不在本轮章节范围内）。

## ADR-031：Multi-Agent 升级继续采用 deterministic control plane，而不是自由协作式 swarm
- 状态：已决定
- 日期：2026-03-22
- 背景：当前仓库已经具备 `bootstrap -> translate -> review -> export` 的可恢复主链路，并且 run-control、issue routing、chapter memory、packet review 都已经有明确定义。Multi-agent 升级的目标是提高结构可解释性和角色边界，而不是引入 agent 对话复杂度。
- 候选方案：
  - 重构成多个自由协作的 LLM agent，由对话自行协调任务
  - 保留 deterministic control plane，把高价值角色显式化为 service boundary
- 最终决定：采用第二种。Phase 1 的 multi-agent 升级只显式化角色和协议，不改成自由协作式 swarm。
- 代价与妥协：短期内“agent 感”更弱，需要继续接受数据库状态和规则系统是主控制面的现实。
- 推翻条件：如果后续某些流程被证明只有通过真正的异步多角色会话才能稳定解决，再单点引入自治 agent，而不是整体重构。
- 影响范围：`document_run_executor.py`、`run_execution.py`、`review.py`、`translation.py`、`docs/multi-agent-final-implementation-plan.md`
- 探针验证：不需要（这是在现有仓库能力和 Phase 1 风险边界下做出的架构风格决策）。

## ADR-032：Structured state 仍是唯一真相，Markdown 只是标准工作视图和交付视图
- 状态：已决定
- 日期：2026-03-22
- 背景：用户明确希望 Markdown 成为更自然的中间结果格式，但当前系统的 sentence alignment、packet provenance、issue routing、rerun 计划、bbox 证据都依赖结构化状态。
- 候选方案：
  - 让 Markdown 取代 JSON/DB，成为唯一中间格式
  - 保留结构化状态为真相，同时把 Markdown 作为统一的人类可读工作视图
- 最终决定：采用第二种。DB/JSON/ORM 状态继续做 control plane 真相；Markdown 用于翻译、阅读、导出和人工检查。
- 代价与妥协：系统会同时维护两类视图，需要持续保证 structured state 和 Markdown render 的一致性。
- 推翻条件：只有当后续证明 alignment、issue、bbox 等控制信息也能稳定嵌入统一文本 IR，且不损失审计能力时，才重新评估。
- 影响范围：`services/export.py`、`services/layout_validate.py`、`domain/models/*`、`docs/multi-agent-final-implementation-plan.md`
- 探针验证：不需要（这是数据真相层的原则性决策）。

## ADR-033：Phase 1 的调度粒度固定为“章间并行、章内串行”
- 状态：已决定
- 日期：2026-03-22
- 背景：用户已确认本次升级首先要解决术语一致性、chapter memory 连续性和 rerun 可控性，而不是把单书吞吐推到极致。当前 chapter memory 设计也天然要求 packet acceptance 具备顺序性。
- 候选方案：
  - 全书 packet 全量并行，靠后处理解决一致性
  - 章节内也并行，但在 memory 层做复杂冲突合并
  - 章间并行、章内串行
- 最终决定：采用第三种。不同 chapter lane 可以并发推进，但同一章任意时刻只允许一个 active translate packet。
- 代价与妥协：单章长章节的吞吐上限会更低，但 memory continuity、issue attribution 和 rerun reproducibility 更稳定。
- 推翻条件：如果后续章节内串行成为明显瓶颈，且 chapter memory 冲突可以被更低成本地控制，再考虑放宽到“局部并行 + 顺序提交”。
- 影响范围：`document_run_executor.py`、`run_execution.py`、`state_machine.py`、`tests/test_run_execution.py`、后续 `tests/test_chapter_lane_serialization.py`
- 探针验证：不需要（这是执行顺序与一致性优先级的权衡决策）。

## ADR-034：Memory 先实现为显式 service facade，worker 只读快照并提出建议
- 状态：已验证（读侧接线完成）
- 日期：2026-03-22
- 背景：此前 `TranslationService` 直接拼接 chapter memory + context compile，worker 实际消费到的是“编译后的结果”，但这条路径没有独立 service 边界，也没有显式表明 memory version 是运行时契约的一部分。
- 候选方案：
  - 继续让 translation service 直接访问 chapter memory repository 和 compiler
  - 先提取 `MemoryService`，负责读快照、装配 compiled context，并把版本元数据显式传入 worker
- 最终决定：采用第二种，并在当前轮次引入 `MemoryService + CompiledTranslationContext`。worker 现在显式接收 `context_compile_version` 和 `memory_version_used`。
- 代价与妥协：当前轮次只完成了读侧 service 化，写侧的“review approval 后再 commit chapter memory”仍待后续回合收口。
- 推翻条件：如果后续证明 `MemoryService` 只是一层薄包装且没有降低复杂度，可将其再次折回，但前提是显式 compiled context 契约保留。
- 影响范围：`src/book_agent/services/memory_service.py`、`src/book_agent/services/context_compile.py`、`src/book_agent/services/translation.py`、`src/book_agent/workers/contracts.py`、`tests/test_memory_service.py`
- 探针验证：已通过（定向回归已证明 compiled context 会显式进入 worker，且 `TranslationRun.model_config_json` 会记录 `context_compile_version` 与 `memory_version_used`）。

## ADR-035：Phase 1 Multi-Agent 范围锁定为 EPUB / PDF_TEXT / PDF_MIXED，交付 merged markdown 与 bilingual HTML
- 状态：已决定
- 日期：2026-03-22
- 背景：在多种升级方向中，最容易拖慢进度的是同时把 scanned PDF、图内文字回写、rebuild EPUB/PDF 一起纳入首版。用户已经明确要求先确保交付节奏和方向稳定。
- 候选方案：
  - 首版同时支持 EPUB / text PDF / mixed PDF / scanned PDF，并直接交付 rebuilt EPUB/PDF
  - 首版只锁 EPUB / text PDF / mixed PDF，输出 merged markdown 与 bilingual HTML，把其余能力延后
- 最终决定：采用第二种。Phase 1 的 multi-agent 升级只承诺 `EPUB + PDF_TEXT + PDF_MIXED` 和 `MERGED_MARKDOWN + BILINGUAL_HTML`。
- 代价与妥协：用户短期内看不到 rebuilt EPUB/PDF 和 scanned PDF 入口，但整个升级可以在更稳的范围内闭环。
- 推翻条件：如果后续 regression corpus 证明 scanned PDF 或 rebuilt EPUB/PDF 已经有足够稳定的结构恢复和 export 路径，再单独开启 Phase 2。
- 影响范围：`docs/multi-agent-final-implementation-plan.md`、`PROGRESS.md`、后续 Phase 1 验收口径
- 探针验证：不需要（这是 Phase 1 范围和交付物边界的用户确认决策）。

## ADR-036：EPUB 图片导出在章节 XHTML 不可 XML 解析时回退到 HTML figure 索引
- 状态：已决定
- 日期：2026-03-22
- 背景：真实 EPUB 文档 `cf32d839...` 的章节 XHTML 含非 XML 友好的字符，`ElementTree` 在 `_parse_xml_document(...)` 处抛 `ParseError`，导致 `_index_epub_figure_archive_paths(...)` 返回空索引，merged HTML 虽保留图注但无法导出真实图片文件。
- 候选方案：
  - 要求重新 bootstrap / refresh EPUB，把图片单独入库为 `DocumentImage`
  - 保持现有导出路径不变，并在导出时为章节 XHTML 增加容错 HTML figure 索引 fallback
- 最终决定：采用第二种。导出层在 XML 解析失败时，回退到 `HTMLParser` 级别扫描 `<figure>/<img>/<figcaption>`，继续按图注签名恢复 EPUB 内部图片路径。
- 代价与妥协：manifest 里的 `pdf_image_summary` 仍是基于 `DocumentImage` 的存量视角，不会因为这条导出时 fallback 自动变成“图片入库统计”；但用户可见的 merged HTML 图片恢复优先级更高。
- 推翻条件：如果后续 EPUB refresh 链路稳定落库 `DocumentImage`，且导出能统一只依赖持久化图片记录，可再收缩这条 fallback。
- 影响范围：`src/book_agent/services/export.py`、`tests/test_persistence_and_review.py`、真实导出目录 `artifacts/exports/cf32d839...`
- 探针验证：已通过（新增 malformed XHTML EPUB 资产恢复回归，且真实文档重导后恢复 44 个图片文件与 44 个 `<img>` 标签）。

## ADR-037：Final export 必须通过 deterministic layout validation，且结构失败要升级为显式 `REPARSE_CHAPTER`
- 状态：已验证（Phase 1 收口完成）
- 日期：2026-03-22
- 背景：在 Multi-Agent Phase 1 的后段，review gate 已经能阻止语义和对齐问题，但 heading 跳级、缺失图片资产、孤儿脚注、不可渲染表格这类结构问题仍可能在 export 时才暴露。如果这些问题只是 warning，最终交付物就会在“控制面看起来通过了”的情况下静默损坏。
- 候选方案：
  - 保持现状，只让 review gate 决定是否允许 export
  - 在 export 时输出 best-effort warning，但仍继续生成成品
  - 为 export 增加 deterministic layout gate，并把结构失败映射为阻断 issue + `REPARSE_CHAPTER`
- 最终决定：采用第三种。`layout_validate.py` 负责对 heading / figure / footnote / table 做 deterministic preflight 校验；`export.py` 在 gate 失败时会落库 `LAYOUT_VALIDATION_FAILURE`，并生成显式 `REPARSE_CHAPTER` followup action。这样 export 不再只是“最后一步渲染”，而是 Phase 1 的最终结构验收口。
- 代价与妥协：首版 layout gate 是窄而硬的规则集，只覆盖能稳定判定的结构损坏，不试图做智能修复，也不覆盖 publication-grade 排版美化；这意味着它更像“交付保险丝”，不是“自动排版代理”。
- 推翻条件：如果未来 parse/layout recovery 已能在更早阶段稳定消化这些结构问题，export gate 可以缩小规则面，但不应退回“静默容忍结构损坏”的模式。
- 影响范围：`src/book_agent/services/layout_validate.py`、`src/book_agent/services/export.py`、`tests/test_layout_validate.py`、`tests/test_export_layout_gate.py`、`docs/multi-agent-final-implementation-plan.md`
- 探针验证：已通过（layout gate focused 回归通过；Phase 1 收口回归也已通过：`ApiWorkflowTests.test_translate_full_run_executes_review_and_exports_in_background`、`PdfBootstrapPipelineTests.test_bootstrap_pipeline_supports_low_risk_text_pdf`、`BasicPdfOutlineRecoveryTests.test_parse_service_routes_pdf_mixed_documents_to_ocr_parser`、`PdfDocumentImagePersistenceTests.test_export_merges_linked_pdf_image_caption_into_single_render_block`）。

## ADR-038：Phase 2 先做 `PDF_SCAN` 生产化，而不是先开 rebuilt export 或 reviewer 自主重写
- 状态：已决定
- 日期：2026-03-22
- 背景：Phase 1 已经在 `EPUB / PDF_TEXT / PDF_MIXED` 上收敛出稳定的 deterministic control plane。后续 deferred 项目里，`PDF_SCAN` 是最大的格式覆盖缺口；而 rebuilt EPUB/PDF、reviewer 本地重写、分布式 agent 则都会同时引入更高的结构、产品和运维复杂度。
- 候选方案：
  - 先做 rebuilt EPUB/PDF，让交付形态更“出版级”
  - 先做 reviewer local rewrite / stylistic reviewer，继续提升中文自然度
  - 先做 `PDF_SCAN` productionization，把扫描件纳入已验证的 control plane
- 最终决定：采用第三种。Phase 2 的唯一主目标是把 `PDF_SCAN` 稳定接入现有 `bootstrap -> translate -> review -> export` 主链路，交付物仍然维持 `MERGED_MARKDOWN + BILINGUAL_HTML`。
- 代价与妥协：短期内不会得到 rebuilt EPUB/PDF，也不会优先得到更“聪明”的 reviewer；但这换来的是一个更清晰的阶段边界，以及对真实格式覆盖率最直接的提升。
- 推翻条件：如果 scanned PDF 的 OCR/runtime 成本被证明远高于预期，或当前样本显示 rebuilt export 才是更紧急的客户瓶颈，再重新评估 Phase 2 优先级。
- 影响范围：`PROGRESS.md`、`docs/phase-2-pdf-scan-plan.md`、后续 OCR/bootstrap/review/export 工作流
- 探针验证：不需要（这是 Phase 2 目标排序决策，当前基于 Phase 1 已完成边界和现有 deferred 列表做出的执行选择）。

## ADR-039：`PDF_SCAN` 的高风险 review gate 保持默认阻断，但允许“单页高置信且锚点完整”的极窄 advisory 例外
- 状态：已验证（Phase 2 收口完成）
- 日期：2026-03-22
- 背景：进入 `MDU-13.1.2` 后，最小 scanned workflow 已经证明 `PDF_SCAN` 可以完成 bootstrap、translate 和 review-package export，但 final `bilingual_html` export 仍被 review 阶段一条 blanket `MISORDERING` 阻断。问题不在 export，而在 review policy 对 `layout_risk=high` 的 scanned 章节默认一律升级为 blocking issue，导致“结构锚点已经足够完整的最小 scanned 样本”也无法走通共享终态。
- 候选方案：
  - 保持现状，让所有 `PDF_SCAN` high-risk 章节继续一律 blocking，接受最小样本也只能停在 review
  - 对所有 `PDF_SCAN` high-risk 章节整体放宽为 advisory
  - 保持默认阻断，只为“单页、章节级高置信、局部高风险原因单一、captioned artifact 锚点完整”的 scanned 章节开放极窄 advisory 例外
- 最终决定：采用第三种。review gate 仍默认把 `PDF_SCAN` 的高风险结构风险视为阻断；只有当章节满足以下全部条件时，`MISORDERING` 才降为低优先 advisory：
  - 章节在本地 evidence 视角下只有单页
  - 章节级 `parse_confidence >= 0.82`
  - 局部高风险原因仅为 `ocr_scanned_page`
  - 页面中不存在 `header / footer / footnote` 角色
  - 至少一个 captioned artifact 同时具备 caption link 和 recovered group-context anchors
- 代价与妥协：这条例外刻意保持很窄，因此不会显著提升广义 scanned PDF 的通过率；但它足以让“结构已被局部证明稳定”的最小样本进入终态导出，用于完成 Phase 2 的真实主链路验收。
- 推翻条件：如果后续更大 scanned corpus 证明这条例外过窄或过宽，就应基于新的 regression corpus 重新定义 scanned review policy，而不是继续累加临时豁口。
- 影响范围：`src/book_agent/services/review.py`、`tests/test_pdf_support.py`、`PROGRESS.md`、`docs/phase-2-pdf-scan-plan.md`
- 探针验证：已通过（定向回归已证明最小 scanned fixture 能走通 `translate -> review -> bilingual_html export`，同时 `review_package` 共享 chapter-bundle 路径和 final export 的 fail-closed layout gate 仍保持成立）。

## ADR-040：Phase 3 改用 `parallel-autopilot` 开启三 lane 候选执行基线，而不是串行只选一个新目标
- 状态：已决定
- 日期：2026-03-22
- 背景：Phase 2 已在 `PDF_SCAN` 上完成最小端到端闭环，当前 roadmap 达到 `59/59` 并暂时清零。下一阶段同时存在三个真实且互相关联的 deferred 方向：rebuild EPUB/PDF、`PDF_SCAN` 扩到更大 corpus、reviewer/stylistic intelligence。若继续沿旧 autopilot 只串行挑一个目标，会丢失跨目标的依赖视野；若直接盲目三向并行，又会把 export contract、review contract 与共享治理状态同时撕开。
- 候选方案：
  - 延续旧 autopilot 的串行策略，只挑一个目标进入 Phase 3
  - 三个目标直接并行落地，不先建立 lane/state/control-plane 基线
  - 不修改 `auto-pilot.md` 本体，改用新的 `parallel-autopilot` skill，以“串行控制面 + 三条并行候选 lane”的方式重开 roadmap
- 最终决定：采用第三种。Phase 3 的控制面保持串行，先完成 requirement lock、ADR freeze、work-graph generation、lane partitioning 和状态工件初始化，再把三个目标组织为以下 lane：
  - `lane-delivery-upgrade`
  - `lane-pdf-scan-scale`
  - `lane-review-naturalness`
  当前 MVP 仍不承诺真并发代码写入；并行只体现在 lane-aware 调度、依赖表达、阻塞传播和后续可升级性。默认首个 claim 顺序为 `lane-pdf-scan-scale -> lane-delivery-upgrade -> lane-review-naturalness`，这只是 kickoff 顺序，不是长期业务优先级。
- 代价与妥协：roadmap 会从“全部完成”重新打开，整体完成度回落；同时新增 `WORK_GRAPH.json`、`LANE_STATE.json`、`RUN_CONTEXT.md` 作为结构化状态真相，需要维护 Markdown 镜像与 JSON 真相的一致性。
- 推翻条件：如果后续事实证明三条 lane 的写集和契约严重重叠，导致 lane-aware 模型只制造伪并行而没有调度价值，则应回退到单目标串行推进，而不是继续维持形式上的三 lane。
- 影响范围：`PROGRESS.md`、`WORK_GRAPH.json`、`LANE_STATE.json`、`RUN_CONTEXT.md`、`docs/phase-3-parallel-autopilot-plan.md`、后续 Phase 3 执行顺序
- 探针验证：不需要（这是下一阶段的控制面与治理面决策；当前依据是 Phase 2 已完成、parallel-autopilot skill 的 MVP 边界，以及三个 deferred 目标的共享依赖分析）。

## ADR-041：`parallel-autopilot` 必须从真实运行中的 bug 与痛点持续反哺 skill 本体
- 状态：已决定
- 日期：2026-03-22
- 背景：Phase 3 已切换到新的 `parallel-autopilot` skill。仅靠一次性设计规格无法覆盖真实运行中的摩擦点；如果每次遇到 bug、协议缺口、lane 歧义、状态表达不足或重复的人肉胶水步骤，都只做本次局部 workaround，而不回写 skill，本质上会让 autopilot 在每轮运行中重复踩同一类坑。
- 候选方案：
  - 把 skill 视为静态安装包，执行时只修当前项目问题
  - 只有出现“明确 bug”时才更新 skill，普通不顺手和操作摩擦不纳入 evolution
  - 将“真实运行中的 bug 与痛点”都纳入 bug-driven evolution，要求每次使用都在必要时补 skill 本体
- 最终决定：采用第三种。`parallel-autopilot` 后续必须持续演化：每当真实使用中暴露出可复用的缺口，就要同时产出当前 run 修复、gap 分类以及对 skill 本体的可复用补丁。这里的触发源不仅包括实现 bug，也包括 intake protocol 不足、lane policy 歧义、state artifact 不足、merge/rollback 摩擦和 operator ergonomics 缺口。
- 代价与妥协：skill 文档、reference protocol 和模板会更频繁更新，需要同步维护安装型 skill 与发布版 skill-package 文档；但这换来的是后续自动驾驶越来越顺手，而不是越来越依赖操作者记忆。
- 推翻条件：如果未来 skill 已迁移到更正式的版本化发布管线，且 skill 演化不再应直接跟随单 repo 执行周期更新，再重新评估是否把 evolution 改为批次式版本发布。
- 影响范围：`.agents/skills/parallel-autopilot/`、`docs/parallel-autopilot-skill-package.md`、后续所有 Phase 3+ 的自动驾驶执行习惯
- 探针验证：不需要（这是技能治理原则；本次已通过直接补丁把该原则写回安装型 skill、reference protocol 与发布版文档）。

## ADR-042：Phase 3 的 `lane-pdf-scan-scale` 以四层 corpus lineage 和代际兼容 telemetry 基线作为起点
- 状态：已决定
- 日期：2026-03-22
- 背景：Phase 3 已将 larger-corpus `PDF_SCAN` 设为一条独立 lane，但现有证据并不来自单一 run generation。历史全书样本 `v11 / v12` 提供 bootstrap 与 retry lineage，`v21 / v22 / v23` 提供 slice repair 与 merge lineage，`v28` 提供 readable rescue 终态。如果继续把这些样本混成一个“扫描书大样本”来谈，后续实现会把 telemetry 缺口、schema 缺口和 failure taxonomy 混在一起，导致 `MDU-16.1.2` 无法聚焦。
- 候选方案：
  - 只保留 `v11 / v12` 两个全书样本，其他 slice / rescue lineage 视为旁路历史
  - 只保留最新的 readable rescue 样本，假设它已经代表 larger-corpus 完整终态
  - 将 larger-corpus scanned baseline 分成四层：full-book bootstrap stress、retry/resume lineage、slice repair/merge lineage、readable rescue end-state，并显式接受 legacy report / DB schema drift
- 最终决定：采用第三种。`lane-pdf-scan-scale` 从 `MDU-16.1.1` 开始，锁定以下基线：
  - Tier A：`v11-full-run-chunked-pagecount`
  - Tier B：`v12-retry-after-balance`
  - Tier C：`v21-slice-1..4`、`v22-merged-slices`、`v23-final-prose-repair`
  - Tier D：`v28-readable-rescue-titlefix`
  同时锁定新的 telemetry baseline：新的 larger-corpus run 必须在 archived `report.json` 中稳定携带 `stage / ocr_status / ocr_progress / db_counts / work_item_status_counts / translation_packet_status_counts / error.* / retry-resume lineage`；而 `v11 / v12` 这类旧样本明确标记为 `legacy report generation`，只能作为 stress/recovery 参考，不能伪装成已经满足当前 telemetry 协议。
- 代价与妥协：Phase 3 的 scanned lane 现在必须承认“不同代际工件负责不同生命周期证据”，不会再假装单一 artifact 家族能同时回答 bootstrap、retry、repair 和 readability 所有问题；这会让治理文档更复杂，但能避免后续实现再次误判 failure taxonomy。
- 推翻条件：如果后续重新跑出一条满足当前 telemetry baseline、同时覆盖 full-book bootstrap 到 final readability 的新一代 scanned corpus，可以再收敛这条四层样本组，减少对 legacy lineage 的依赖。
- 影响范围：`docs/phase-3-pdf-scan-scale-plan.md`、`WORK_GRAPH.json`、`LANE_STATE.json`、`RUN_CONTEXT.md`、`.agents/skills/parallel-autopilot/references/state-artifacts.md`
- 探针验证：已通过（当前已审计 `v11 / v12 / v21 / v23 / v28` 目录与报告/数据库证据，确认 full-book、retry、slice repair 和 readable rescue 分属不同 artifact generation；并确认 sampled DB lineage 仍存在历史 schema 缺口，如缺失 document title columns）。

## ADR-043：Larger-corpus `PDF_SCAN` 运行时必须显式声明 telemetry generation、compatibility 和 failure taxonomy
- 状态：已决定
- 日期：2026-03-22
- 背景：`MDU-16.1.1` 锁定了四层 scanned corpus lineage 后，一个新的执行痛点暴露得很清楚：Phase 2 前后的报告代际并不一致。`v11 / v12` 这类全书样本没有 `stage / db_counts / ocr_status / ocr_progress`，但仍是 larger-corpus baseline 的关键证据；与此同时，provider exhaustion、bootstrap OCR failure 和 repair timeout 也需要不同的恢复动作。如果继续把这些差异藏在 operator 心智里，`MDU-16.1.2` 之后的 larger-corpus 验收仍会反复误判“这是 OCR 问题还是 provider 问题、这是 legacy report 还是 current report”。
- 候选方案：
  - 保持现状，只在文档里说明 report 代际和失败类型差异
  - 只在 `run_real_book_live.py` 写侧补字段，不管 watcher 和历史报告读取
  - 引入共享 reporting helper，在 `run_real_book_live.py` 和 `watch_real_book_live.py` 同时显式声明 telemetry generation / compatibility，并统一输出 failure taxonomy 与 recovery action
- 最终决定：采用第三种。自 `MDU-16.1.2` 起：
  - 当前代运行报告显式声明 `telemetry_generation=current-runtime-report-v2`
  - watcher 对无该字段的历史报告显式标记为 `legacy-report-generation`
  - larger-corpus scanned 运行时统一输出：
    - `telemetry_compatibility`
    - `failure_taxonomy`
    - `recommended_recovery_action`
  - 当前最少明确的失败分桶为：
    - `provider_exhaustion -> top_up_provider_balance_and_resume`
    - `ocr_failure -> fix_ocr_runtime_and_rerun_bootstrap`
    - `repair_timeout -> retry_repair_slice_or_reduce_repair_batch`
- 代价与妥协：report schema 现在会显式扩展到 compatibility/taxonomy 层，live monitor 也不再是纯被动镜像；这增加了一点 reporting 代码，但换来的是 larger-corpus scanned 的恢复动作不再依赖人工猜测。
- 推翻条件：如果后续 larger-corpus Phase 3 acceptance 证明 failure taxonomy 需要更多 family，或 reporting contract 迁移到正式 API schema，再重新评估是否将这些字段下沉到更稳定的 shared schema layer。
- 影响范围：`scripts/real_book_live_reporting_common.py`、`scripts/run_real_book_live.py`、`scripts/watch_real_book_live.py`、`tests/test_real_book_live_reporting.py`、`docs/phase-3-pdf-scan-scale-plan.md`
- 探针验证：已通过（focused regressions 已证明当前 report generation 会携带 `telemetry_generation`；bootstrap OCR failure 和 provider exhaustion 会被正确分桶；legacy report 会被 watcher 标记为 `legacy-report-generation`）。

## ADR-044：Larger-corpus `PDF_SCAN` 的 lane closure 必须以 artifact-driven acceptance threshold 收口
- 状态：已决定
- 日期：2026-03-22
- 背景：`MDU-16.1.1 / MDU-16.1.2` 已锁定 four-tier corpus lineage 与 telemetry / failure taxonomy，但 `lane-pdf-scan-scale` 仍停留在 narrative acceptance：我们知道应该验证 larger-corpus 的稳定性、恢复能力和 readable rescue，却没有把这些目标编译成可重复执行的 pass/fail 机制。这直接暴露了一个 Phase 3 运行痛点：lane closure 很容易只完成“文档解释”，而没有沉淀成可复用的 acceptance artifact。
- 候选方案：
  - 继续用治理文档记录 acceptance，默认由 operator 人工比对 locked corpus
  - 只补一条 ad hoc 测试，验证某个 repair 样本，不覆盖完整 lineage
  - 将 four-tier scanned lineage 编译成 artifact-driven acceptance helper，并冻结首版 threshold，后续 larger-corpus 回归统一复用
- 最终决定：采用第三种。自 `MDU-16.1.3` 起，`lane-pdf-scan-scale` 的 closure 标准固定为一组可执行阈值，并由 `scripts/pdf_scan_corpus_acceptance.py` 和 `tests/test_pdf_scan_corpus_acceptance.py` 承载。首版 acceptance 至少要求：
  - full-book bootstrap floor：`page_count >= 400`、`chapters >= 90`、`translation_packets >= 2000`、`translation_runs >= 2000`
  - legacy full-book failure 仍被正确分类为 `provider_exhaustion`
  - retry/resume lineage 显式保留 `resume_from_status=failed` 与 `retry_from_status=failed`
  - representative slice repair 满足 `candidate_total >= 100`、`candidate_selected >= 30`、`repaired/selected >= 0.90`、`total_cost_usd <= 0.05`，且失败全部落在 `repair_timeout`
  - final repair 满足 `repaired/selected == 1.0`、`failed_candidates == 0`、`total_cost_usd <= 0.01`
  - readable rescue 的 markdown/html 及其 manifests 都存在
  - `v11 / v21-slice-2 / v23 / v28` 的 `97 chapters / 2170 packets / 2171 runs` 结构计数保持稳定
- 代价与妥协：这些 threshold 仍然是基于当前 locked corpus 的 first accepted baseline，不是对所有 scanned books 的永久 universal contract；但它们已经足够把 larger-corpus acceptance 从 narrative 计划推进到可重复执行的证据。
- 推翻条件：如果后续出现新的 larger-corpus scanned lineage，证明这些阈值明显过宽、过窄，或遗漏了关键 failure family / readability 信号，应提升 `scan-corpus-acceptance` contract version，而不是让 operator 再次回到纯文档比对。
- 影响范围：`scripts/pdf_scan_corpus_acceptance.py`、`tests/test_pdf_scan_corpus_acceptance.py`、`docs/phase-3-pdf-scan-scale-plan.md`、`WORK_GRAPH.json`、`LANE_STATE.json`、`PROGRESS.md`、`.agents/skills/parallel-autopilot/references/runtime-protocol.md`
- 探针验证：已通过（focused acceptance regression 已在 locked corpus 上验证 full-book floor、retry/resume lineage、slice repair success ratio、final repair closure、readable rescue export existence 与 lineage structure stability；helper 输出与当前 artifact 事实一致）。

## ADR-045：Lane A 的 rebuilt EPUB/PDF 必须采用 additive document-level contract，而不是重写现有 merged delivery contract
- 状态：已决定
- 日期：2026-03-22
- 背景：切到 `lane-delivery-upgrade` 后，最大的执行风险不是“做不出 rebuilt export”，而是过早把现有导出语义搅乱。当前系统已经稳定交付 `review_package / bilingual_html / merged_html / merged_markdown`，并且这些出口已被 workflow、API 下载与 fail-closed gate 依赖。如果 rebuilt EPUB/PDF 没有先锁定 additive contract、source guard 和 manifest schema，`MDU-15.1.2` 很容易一边实现一边重定义边界。
- 候选方案：
  - 直接在现有 merged export 上叠实现细节，边做边决定 rebuilt contract
  - 将 rebuilt EPUB/PDF 视为现有 merged delivery 的替代物，允许改写当前 API / workflow 语义
  - 先锁 lane-scoped contract：rebuilt artifact 作为 additive document-level export，现有 merged/bilingual/review contract 不变；然后再进入最小实现
- 最终决定：采用第三种。自 `MDU-15.1.1` 起：
  - rebuilt 输出被锁定为新的 **document-level additive export**
  - 允许新增的 export type 是 `rebuilt_epub` 与 `rebuilt_pdf`
  - `rebuilt_epub` 仅对 `source_type=epub` 开放
  - `rebuilt_pdf` 首版允许统一使用 `merged_html` 作为渲染基底，但明确不承诺原始 PDF page-faithful rebuild
  - rebuilt export 必须继续 obey fail-closed gate，不能绕过 `merged_html / merged_markdown` 的稳定性要求
  - 两类 rebuilt export 都必须生成 sidecar manifest，并显式记录 `derived_from_exports`、`renderer_kind`、`expected_limitations`
- 代价与妥协：这意味着 Lane A 的首版 rebuilt PDF 质量目标是“可读、章节顺序正确、结构不坏”，而不是出版级复刻；rebuilt EPUB 也只承诺最小 spine/nav/asset rebuild，而不是全量恢复原始 EPUB 的复杂 CSS/脚本行为。
- 推翻条件：如果后续真实交付要求证明 rebuilt artifact 必须成为主交付面，或者必须达到 page-faithful / publication-grade 级别，则应升级 contract version 并重新定义 Lane A，而不是在当前 MVP 合约上继续堆临时例外。
- 影响范围：`docs/phase-3-delivery-upgrade-plan.md`、`WORK_GRAPH.json`、`LANE_STATE.json`、`RUN_CONTEXT.md`、`PROGRESS.md`、`.agents/skills/parallel-autopilot/references/state-artifacts.md`
- 探针验证：不需要（这是 lane-A 的 contract lock 决策；当前已通过代码阅读确认现有 export/workflow/API 全部依赖 document-level merged delivery 语义，因此必须先锁 additive contract 再做实现）。

## ADR-046：Lane A 首版 rebuilt delivery 必须从稳定 merged artifacts 派生，并同时保留一条旧交付面的守护回归
- 状态：已决定
- 日期：2026-03-22
- 背景：进入 `MDU-15.1.2` 后，真正的工程问题不是“如何凭空再造一个排版系统”，而是“如何在不破坏现有导出主链的情况下新增 rebuilt delivery”。如果 rebuilt EPUB/PDF 直接从 source parse / chapter export 分叉实现，最容易绕过当前 merged gate、复制一套独立渲染逻辑，并在实现完成后才发现把旧交付契约撞坏了。与此同时，本轮真实回归也暴露出另一类痛点：当新增能力与旧交付面相邻时，保守回归常会先撞上过期断言，而不是新 breakage。
- 候选方案：
  - 直接从 source bundle 各自重建 EPUB/PDF，不显式依赖 `merged_html / merged_markdown`
  - 将 rebuilt 输出建立在现有 merged artifacts 之上，并把 preserved-contract regression 当作同一 MDU 的必做项
  - 等未来有 publication-grade renderer 再做 rebuilt delivery，当前先不交付最小实现
- 最终决定：采用第二种。自 `MDU-15.1.2` 起：
  - `rebuilt_epub` 与 `rebuilt_pdf` 都是 **从当前稳定 merged artifacts 派生的 additive document-level export**
  - `rebuilt_epub` 采用最小 EPUB spine rebuild，并复用当前 document bundle 的 visible chapters / translated render blocks / exported assets
  - `rebuilt_pdf` 采用 `merged_html` 作为首版渲染 substrate，renderer 不可用时必须 fail-closed
  - rebuilt sidecar manifest 必须显式记录：
    - `contract_version`
    - `renderer_kind`
    - `derived_from_exports`
    - `derived_export_artifacts`
    - `expected_limitations`
  - 任何实现 rebuilt delivery 的 MDU，都必须至少复跑一条相邻旧交付面的 preserved-contract regression；如果失败根因是陈旧断言而不是当前 breakage，应在同一 MDU 内归一化测试基线
- 代价与妥协：首版 rebuilt delivery 明确偏“可交付最小产物”，不追求 publication-grade fidelity；同时 lane A 的实现 round 需要额外承担一小段旧回归基线清理工作，换来的是不把陈旧断言误当成新回归。
- 推翻条件：若后续决定让 rebuilt artifact 成为新的主交付真相源，或引入独立的出版级排版/渲染栈，则需要升级 `delivery-artifact-contract` 与 `export-manifest` contract version，而不是继续沿当前派生式 MVP 叠补丁。
- 影响范围：`src/book_agent/services/export.py`、`src/book_agent/services/workflows.py`、`src/book_agent/schemas/workflow.py`、`src/book_agent/app/api/routes/documents.py`、`tests/test_persistence_and_review.py`、`tests/test_api_workflow.py`、`.agents/skills/parallel-autopilot/references/runtime-protocol.md`
- 探针验证：已通过（`rebuilt_epub` workflow/API focused regressions 与 `rebuilt_pdf` workflow regression 已通过；同时一条 `merged_html` 与两条 `merged_markdown` preserved-contract regressions 已复跑并完成陈旧断言归一化）。

## ADR-047：带 source guard / fail-closed 边界的 lane closure 必须显式包含负路径证据
- 状态：已决定
- 日期：2026-03-22
- 背景：`MDU-15.1.2` 完成后，lane A 的 happy path 已经成立，但这还不足以证明交付契约真正稳定。rebuild lane 的锁定边界里还明确写了两类容易被忽略的条件：`rebuilt_epub` 只允许 EPUB 源、`rebuilt_pdf` 在 renderer 不可用时必须 fail-closed。如果 lane closure 只跑正路径回归，很容易把“边界条件没被验证”误当成“契约已经稳定”。这也是本轮并行 autopilot 使用中暴露出来的一个真实 skill 缺口。
- 候选方案：
  - 继续把 lane closure 理解为“happy path 能跑通即可”
  - 在具体 lane 文档里临时提醒需要补负路径，但不升级 skill protocol
  - 将“带 source guard / fail-closed 边界的 lane 必须显式补至少一条负路径 regression”升级为并行 autopilot 的 runtime rule
- 最终决定：采用第三种。自 `MDU-15.1.3` 起：
  - 任何 lane 如果 contract 中出现 source guard、dependency unavailable、renderer unavailable、unsupported mode 或其他 fail-closed boundary
  - 则 lane closure 除了正路径 evidence 和 preserved-contract regression 外，还必须至少补一条负路径 regression
  - 若本轮负路径 regression 暴露的是“旧测试没有覆盖边界”，应在同一 MDU 内补齐，而不是留给下一阶段
- 代价与妥协：lane closure 的 test 面会略微增大，但能显著减少“功能看似完成、边界其实没被证明”的假收口。
- 推翻条件：如果后续 skill 进入更正式的自动 lane planner / verifier，并能从 contract metadata 自动合成负路径验证，再重新评估是否还需要在 runtime protocol 中用人工规则显式要求。
- 影响范围：`.agents/skills/parallel-autopilot/references/runtime-protocol.md`、`docs/phase-3-delivery-upgrade-plan.md`、`tests/test_persistence_and_review.py`、`tests/test_api_workflow.py`、`PROGRESS.md`
- 探针验证：已通过（lane A 现已具备 `rebuilt_epub` source guard regression、`rebuilt_pdf` renderer unavailable workflow/API regressions，以及 positive-path 和 preserved-contract regressions；因此可视为 wave-1 accepted lane）。

## ADR-048：质量 / 智能类 lane 在实现 scaffold 前必须先冻结 benchmark、允许动作与保护契约
- 状态：已决定
- 日期：2026-03-22
- 背景：切到 `lane-review-naturalness` 后，一个新的执行痛点变得很明显：与 delivery / scan 这类 lane 不同，质量 / 智能类 lane 很容易在“提升自然度”这种高层目标下滑成自由发挥。如果没有先锁 benchmark families、允许 intervention 和保护契约，`MDU-17.1.2` 很容易从 packet-level source-aware guidance 漂移成 reviewer 自主重写、文风审美竞争，或者把纯 naturalness 问题悄悄升级为 blocking gate。
- 候选方案：
  - 直接进入 stylistic scaffold 实现，边做边讨论 naturalness 的定义
  - 只在 Phase 3 总计划中保留一句“不要变成自由重写”，不额外写 lane-scoped contract
  - 先为 `lane-review-naturalness` 冻结独立 contract：锁 benchmark、允许动作、非目标、blocking 边界，再进入实现
- 最终决定：采用第三种。自 `MDU-17.1.1` 起：
  - `review-style-contract` 升级为 v1，并由 `docs/phase-3-review-naturalness-plan.md` 承载 lane-scoped 真相
  - `naturalness-gate` 升级为 v1，并冻结以下边界：
    - 纯 `STYLE_DRIFT` 维持 packet-level non-blocking advisory
    - 默认 action 仍为 `RERUN_PACKET`
    - auto-followup 中 `TERM_CONFLICT` / `UNLOCKED_KEY_CONCEPT` 继续优先于 style drift
  - 首版 frozen benchmark families 固定为：
    - `LITERALISM_XHTML`
    - `KNOWLEDGE_TIMELINE_LITERALISM_XHTML`
    - `DURABLE_SUBSTRATE_LITERALISM_XHTML`
    - `RESPONSIBILITY_LITERALISM_XHTML`
    - `CONSISTENCY_CARE_LITERALISM_XHTML`
    - `MIXED_AUTO_FOLLOWUP_XHTML`
  - 首版允许的自动 intervention 仅限：
    - source-aware `STYLE_DRIFT` detection
    - packet-level advisory issue
    - packet-scoped rerun guidance aggregation
    - targeted rerun / followup execution
    - rerun 后重新 review 验证
  - 明确不允许：
    - reviewer 默认自由重写
    - chapter/document-level stylistic rewrite
    - 纯 naturalness 默认 blocking
    - 无 source-aware evidence 的大范围风格化润色
- 代价与妥协：Lane C 首版会显得更保守，无法一步跳到“整章更像中文作者写的”；但这样能避免将自然度优化误做成无边界的 rewrite lane，并让后续 focused regressions 有稳定 benchmark。
- 推翻条件：如果后续 `MDU-17.1.2 / MDU-17.1.3` 的证据表明，packet-scoped source-aware scaffold 无法带来可验证收益，且必须引入受控 reviewer patch path，届时应显式升级 `review-style-contract` / `naturalness-gate` version，而不是在当前 contract 上静默扩权。
- 影响范围：`docs/phase-3-review-naturalness-plan.md`、`docs/phase-3-parallel-autopilot-plan.md`、`WORK_GRAPH.json`、`LANE_STATE.json`、`RUN_CONTEXT.md`、`PROGRESS.md`、`.agents/skills/parallel-autopilot/references/lane-policy.md`
- 探针验证：已通过（现有 focused regressions 已证明 `STYLE_DRIFT` 是 non-blocking packet advisory、默认 action 为 `RERUN_PACKET`、style rerun 会聚合 `preferred_hint + prompt_guidance`，且 mixed packet 中 term/concept followup 仍优先于 style drift）。

## ADR-049：Lane C 首版 stylistic scaffold 必须先做 additive observability 与 packet guidance，而不是 schema 迁移或 reviewer 默认重写
- 状态：已决定
- 日期：2026-03-22
- 背景：`MDU-17.1.1` 完成后，lane C 已经锁定了 benchmark、允许动作和 non-goals。进入 `MDU-17.1.2` 时，真正的工程取舍变成：第一步 scaffold 应该落在哪。继续停留在 issue 散点会让 `MDU-17.1.3` 缺少 chapter-level 可观察面；但如果一开始就做持久化 schema 扩展或 reviewer 自主重写，又会把本 lane 从 source-aware packet guidance 拉偏到更高风险的支线。
- 候选方案：
  - 直接给 reviewer 增加默认自由重写能力，让自然度提升立刻体现在 target 覆写上
  - 为 naturalness 新增持久化 summary schema，再决定是否暴露给 workflow / API
  - 先做 additive 的 chapter-level `naturalness_summary` 和更具体的 packet rerun hints，不改变 blocking 语义，也不引入新持久化 schema
- 最终决定：采用第三种。自 `MDU-17.1.2` 起：
  - review summary 新增 additive `naturalness_summary`
  - 首版暴露字段固定为：
    - `advisory_only`
    - `style_drift_issue_count`
    - `affected_packet_count`
    - `dominant_style_rules`
    - `preferred_hints`
  - workflow / API review response 同步暴露该 summary
  - `STYLE_DRIFT` rerun hints 除 `preferred_hint + prompt_guidance` 外，还显式包含命中的生硬译法片段，帮助 targeted rerun 知道“当前哪里发硬”
  - 上述改动均为 additive scaffold：
    - 不改变 `STYLE_DRIFT` 的 non-blocking advisory 语义
    - 不改变 `TERM_CONFLICT` / `UNLOCKED_KEY_CONCEPT` 的优先级
    - 不引入新的持久化 schema
    - 不把 reviewer 变成默认自由重写代理
- 代价与妥协：首版 scaffold 不会立刻让 document summary、历史列表或持久化质量总览拥有 naturalness 统计；但这换来了一个更稳的增量路径，可以先在 review/workflow/API 层证明价值，再决定是否值得下沉到存储层。
- 推翻条件：如果后续 `MDU-17.1.3` 的 focused regressions 证明 additive summary 无法支撑 operator 判断，或必须跨会话稳定保留 naturalness 统计，才应考虑显式升级持久化 schema；如果需要 reviewer patch path，也必须另开 contract 升级，而不是在当前 scaffold 上静默扩权。
- 影响范围：`src/book_agent/services/review.py`、`src/book_agent/orchestrator/rerun.py`、`src/book_agent/services/workflows.py`、`src/book_agent/schemas/workflow.py`、`src/book_agent/app/api/routes/documents.py`、`tests/test_rule_engine.py`、`tests/test_persistence_and_review.py`、`tests/test_api_workflow.py`、`docs/phase-3-review-naturalness-plan.md`、`PROGRESS.md`、`.agents/skills/parallel-autopilot/references/lane-policy.md`
- 探针验证：已通过（focused regressions 已证明 review summary 会产出 `naturalness_summary`、API review response 会返回该字段、`STYLE_DRIFT` rerun hints 会携带命中的生硬译法片段，且原有 non-blocking / priority contract 未被破坏）。

## ADR-050：质量类 lane 收口时必须将 frozen benchmarks 汇总成 dedicated acceptance artifact
- 状态：已决定
- 日期：2026-03-22
- 背景：`MDU-17.1.2` 之后，lane C 虽然已经有了 benchmark、summary 和 rerun guidance，但真正的收口痛点依然存在：证明 naturalness lane 成立的断言仍然散落在多个旧测试里。这样做虽然能回归单点行为，却不利于后续 wave integration、phase checkpoint 和 skill 复用，因为操作者无法一眼看出“哪一组测试才是 lane C 的正式 acceptance artifact”。
- 候选方案：
  - 继续依赖分散在 `tests/test_persistence_and_review.py`、`tests/test_rule_engine.py`、`tests/test_api_workflow.py` 中的既有 focused asserts
  - 为 lane C 新增一份 dedicated acceptance regression，把 frozen benchmarks、guided followup 收敛和 mixed priority 作为明确的收口证据
  - 为 lane C 再引入单独脚本式 acceptance helper，即便当前需求只需要测试层证据
- 最终决定：采用第二种。自 `MDU-17.1.3` 起：
  - `lane-review-naturalness` 的正式 acceptance artifact 固定为 `tests/test_review_naturalness_acceptance.py`
  - 首版 dedicated acceptance 必须显式覆盖：
    - frozen literalism benchmark families 会稳定产出 `naturalness_summary`
    - guided style followup 能在 locked contract 内收敛 literalism benchmark
    - mixed benchmark 继续保持 `TERM_CONFLICT` 优先于 `STYLE_DRIFT`
  - 旧 focused regressions 继续保留，但它们现在是 supporting evidence，不再是 lane closure 的唯一载体
- 代价与妥协：新增了一份看似“重复”的 acceptance test 文件；但这换来了 lane-C 收口证据的集中化，后续 integration gate 和 checkpoint 不必再在多个旧文件里手工拼装证据。
- 推翻条件：如果后续 Phase 4/其他项目证明质量类 lane 更适合脚本式 acceptance helper 或统一评测框架，再评估是否将 dedicated acceptance regression 提升为更通用的 helper；在那之前，不再接受把 lane closure 仅仅留在分散 old tests 里的做法。
- 影响范围：`tests/test_review_naturalness_acceptance.py`、`docs/phase-3-review-naturalness-plan.md`、`docs/phase-3-parallel-autopilot-plan.md`、`PROGRESS.md`、`WORK_GRAPH.json`、`LANE_STATE.json`、`RUN_CONTEXT.md`、`.agents/skills/parallel-autopilot/references/lane-policy.md`
- 探针验证：已通过（dedicated acceptance regression 与既有 scaffold / API / rerun focused regressions 合跑通过，确认 literalism benchmarks、guided followup 收敛与 mixed priority 三类证据已集中化且未破坏既有 contract）。

## ADR-051：Phase integration gate 必须将 lane-local acceptance 聚合成显式矩阵，并补至少一条 cross-lane coherence regression
- 状态：已决定
- 日期：2026-03-22
- 背景：Wave 1 三条 lane 都已 individually accepted，但进入 `MDU-18.1.1` 时暴露出一个新的串行控制面痛点：lane closure 证据虽然都存在，却分散在 lane 文档、helper 和 focused regressions 里。若 integration gate 只写“lane A/B/C 都完成了”，后续 resume、checkpoint 或 skill 复用都需要操作者重新手工回忆每条 lane 的 acceptance 入口，也无法证明这些 lane 在共享主链路上真的可以共存。
- 候选方案：
  - 继续依赖 lane 文档叙述，在 `PROGRESS.md` 中写一句“wave-1 all lanes accepted”
  - 为 phase integration gate 新增一份显式 acceptance matrix，只聚合 lane-local canonical evidence
  - 在第二种基础上，再补至少一条 cross-lane coherence regression，证明 integration gate 不只是“各自单独通过”
- 最终决定：采用第三种。自 `MDU-18.1.1` 起：
  - phase integration gate 必须产出一份显式 acceptance matrix
  - matrix 至少记录：
    - 每条 lane 的 canonical acceptance artifact 或 canonical test IDs
    - 当前 gate 依赖的 contract tags
    - lane doc / lane state / work graph 的一致性检查
  - integration gate 还必须补至少一条 cross-lane coherence regression
  - 当前 Phase 3 的首版 cross-lane coherence regression 固定为：
    - `STYLE_DRIFT` 继续保持 non-blocking advisory 时，不会阻断 `merged_markdown / rebuilt_epub / rebuilt_pdf` 的 document-level 导出
- 代价与妥协：治理面新增了一份 dedicated integration helper/test，看起来比只改 `PROGRESS.md` 更重；但它换来了可恢复、可复跑、可审计的 phase gate 证据，也避免把 lane 间兼容性继续留在操作者脑内。
- 推翻条件：若后续 parallel-autopilot 演化出统一的 phase verifier 框架，可以把当前的 repo-local helper/test 升级成更通用的验证器；在那之前，不再接受“只有 lane-level proof、没有 explicit phase integration matrix”的 checkpoint 收口方式。
- 影响范围：`scripts/phase3_integration_gate.py`、`tests/test_phase3_integration_gate.py`、`docs/phase-3-parallel-autopilot-plan.md`、`PROGRESS.md`、`DECISIONS.md`、`WORK_GRAPH.json`、`RUN_CONTEXT.md`、`.agents/skills/parallel-autopilot/references/runtime-protocol.md`
- 探针验证：已通过（`tests.test_phase3_integration_gate` 已证明 lane-local acceptance matrix、contract coverage 与 cross-lane coherence regression 均成立；并与 `tests.test_pdf_scan_corpus_acceptance`、`tests.test_review_naturalness_acceptance` 合跑通过）。

## ADR-052：Phase checkpoint 关闭后，状态工件必须显式切到“等待下一阶段目标锁定”
- 状态：已决定
- 日期：2026-03-22
- 背景：`MDU-18.1.1` 完成后，虽然 integration gate 已有充分证据，但治理面仍残留一个 operator ergonomics / state model gap：如果只把 `mdu-18.1.2` 标成 done，而不显式处理 `current wave / current MDU / next action`，resume 时很容易把上一阶段最后一个 wave 误读成仍然活跃。并行 autopilot 在 phase 收口后的真实需求不是“默认继续 claim 下一个不存在的 MDU”，而是明确进入“等待新目标锁定”的静止态。
- 候选方案：
  - 只更新 `WORK_GRAPH.json` 节点状态，把“Phase 3 已完成”留给操作者自己推断
  - 在 `PROGRESS.md` 里人工写一句“等待下一阶段目标锁定”，但不升级 skill 协议
  - 将 phase completion handoff 升级为 state-artifact protocol：关闭 checkpoint 时必须显式清理 stale current wave/current MDU，并将 next action 指向新的 requirement lock
- 最终决定：采用第三种。自 `MDU-18.1.2` 起：
  - 若当前 phase 已完成且尚未锁定下一阶段目标：
    - `PROGRESS.md` 必须显式显示“等待下一阶段目标锁定”
    - `RUN_CONTEXT.md` 必须将下一默认动作改为新的 control-plane 入口，而不是上一个已完成的 MDU
    - `LANE_STATE.json.current_wave_id` 允许为 `null`
    - 不允许保留 stale active lane / current MDU
  - 这条规则属于 state-artifact protocol，不再作为 operator 自觉行为
- 代价与妥协：phase closure 的治理更新会略多一步，但能显著降低 resume 时的歧义和“上一阶段已结束却看起来还在跑”的假活跃状态。
- 推翻条件：如果未来引入统一的 run-state schema，并由单一状态机自动生成 operator mirror，可再评估是否保留当前文档级 handoff 规则；在那之前，不再接受 phase 关闭后仍把旧 wave / old MDU 挂在当前态里。
- 影响范围：`PROGRESS.md`、`WORK_GRAPH.json`、`LANE_STATE.json`、`RUN_CONTEXT.md`、`docs/phase-3-parallel-autopilot-plan.md`、`.agents/skills/parallel-autopilot/references/state-artifacts.md`
- 探针验证：已通过（`MDU-18.1.2` 完成后，Phase 3 已显式进入“等待下一阶段目标锁定”，不再残留 stale current wave/current MDU）。

## ADR-053：Phase 4 默认锁定为高风险 PDF 页的双层 OCR / Layout Assist hardening
- 状态：已决定
- 日期：2026-03-23
- 背景：Phase 3 已完成，rebuild delivery、larger-corpus `PDF_SCAN` 与 reviewer naturalness 三条 lane 都已 individually accepted，并通过了 phase integration gate。继续在已关闭 lane 上做小修小补的边际收益已明显下降；当前更高杠杆的问题重新回到上游结构恢复，尤其是高风险 PDF 页和局部区域的 OCR / layout assist 边界。
- 候选方案：
  - 继续在 Phase 3 已关闭的三条 lane 上追加局部优化
  - 直接把整页 VLM 解析拉成新的默认主链路
  - 将下一阶段默认锁定为高风险 PDF 页的 OCR / layout assist hardening，并坚持“规则/几何优先，AI assist 仅用于高风险页/区域兜底”
- 最终决定：采用第三种。自 Phase 4 起：
  - 默认主目标切换为高风险 PDF 页的双层 OCR / layout assist hardening
  - 控制面继续保持串行
  - 新能力必须回流到现有 structured pipeline，而不是旁路生成新的平行主链路
  - 在 requirement lock 与 baseline contract 完成前，不直接 claim OCR 实现型 lane
- 代价与妥协：本轮只重开控制面并锁定默认方向，不立刻产出 OCR 实现收益；同时明确拒绝“整页 VLM 默认化”“publication-grade 表格/公式语义重建”“图内文本重绘”等高成本扩张目标。
- 推翻条件：如果后续 `MDU-19.1.1 / MDU-19.1.2` 证明当前仓库的高风险问题并不集中在 OCR / layout assist，或者规则/几何优先的 fail-closed 路线无法带来合理收益，再重新评估下一阶段主目标。
- 影响范围：`docs/phase-4-ocr-layout-assist-plan.md`、`PROGRESS.md`、`DECISIONS.md`、`WORK_GRAPH.json`、`LANE_STATE.json`、`RUN_CONTEXT.md`、`.agents/skills/parallel-autopilot/references/state-artifacts.md`
- 探针验证：已通过（Phase 4 kickoff 基线已落盘，当前运行态已从“等待下一阶段目标锁定”切到“`MDU-19.1.1` 待执行”的显式控制面状态）。

## ADR-054：Phase 4 采用风险分桶的双层 OCR / Layout Assist，并默认 fail-closed
- 状态：已决定
- 日期：2026-03-23
- 背景：`MDU-19.1.1` 完成后，Phase 4 的方向与范围已经明确，但仍缺一层足够硬的执行合同：什么风险允许进入 assist、什么风险只能保持 advisory、什么风险必须 blocking/fail-closed。如果不在实现前冻结这层合同，后续 OCR / layout assist 很容易从“高风险结构恢复”滑成“看到风险就让模型重做”，破坏当前 deterministic pipeline 的可解释性与可恢复性。
- 候选方案：
  - 保持 heuristic-only，不引入 assist 路由
  - 直接把整页或整章高风险内容默认交给 assist 重建
  - 采用风险分桶的双层 OCR / layout assist：规则/几何优先，仅对 Bucket B/C 允许窄 assist，对 Bucket D 保持 blocking / fail-closed
- 最终决定：采用第三种。Phase 4 baseline 现已冻结为：
  - `Bucket A = pass_through_low_risk`
  - `Bucket B = localized_medium_risk`
  - `Bucket C = single_page_scanned_anchorable_high_risk`
  - `Bucket D = blocking_high_risk_or_low_confidence`
  - assist 默认只允许在 Bucket B/C 中以 page-scoped 或 region-scoped 方式出现
  - assist 不可用、超时、低置信或缺 provenance 时，必须 fail-closed 回退到当前 parser 结果，并保留显式风险证据
- 代价与妥协：这会显著限制首版 assist 的覆盖面，特别是高风险多页或低置信 chapter 仍然不会自动放行；但这换来了更稳的控制面，不会为了局部恢复收益而污染低风险稳定路径。
- 推翻条件：若后续真实实现和 focused regression 证明当前 4 桶划分无法覆盖主流高风险场景，或者 Bucket B/C 的窄 assist 价值不足，再评估是否扩桶、并桶或升级 routing；在那之前，不接受“看起来像高风险就默认整页 assist”的扩张。
- 影响范围：`docs/phase-4-ocr-layout-assist-plan.md`、`PROGRESS.md`、`DECISIONS.md`、`WORK_GRAPH.json`、`RUN_CONTEXT.md`、`.agents/skills/parallel-autopilot/references/runtime-protocol.md`
- 探针验证：已通过（Phase 4 文档已冻结 risk buckets、routing semantics 与 fail-closed contract，当前控制面已具备进入 `MDU-19.1.3` 的前置条件）。

## ADR-055：Kickoff-only phase 收口后，应显式切到同目标下的实现规划，而不是回退成“等待下个目标”
- 状态：已决定
- 日期：2026-03-23
- 背景：`MDU-19.1.3` 收口时暴露出一个新的控制面细节：Phase 4 只是 baseline / kickoff phase，不是整个高风险 OCR / layout assist 目标的终点。如果按旧的“phase complete -> waiting for next goal lock”模板处理，会错误暗示当前目标已结束；而真实状态是“当前目标仍成立，只是下一步应进入实现阶段规划”。
- 候选方案：
  - 保持通用 idle 文案，统一写成“等待下一阶段目标锁定”
  - 在 kickoff-only phase 关闭后，显式切到“同目标下的下一步 planning action”
- 最终决定：采用第二种。以后遇到 baseline / kickoff-only phase 收口时：
  - operator mirror 必须指向同一目标下的下一步 planning action
  - 不应把状态误写成“等待新的目标”
  - `current_wave_id` 可以继续为 `null`，直到真正的实现 wave 被创建
- 代价与妥协：状态表达会比“已完成/未完成”多一层语义，但这能减少 resume 时的误判，也更符合 lane-aware control plane 的真实工作方式。
- 推翻条件：如果未来引入统一的 phase-kind schema，并能自动从 phase type 推断 handoff 语义，再评估是否保留当前文字级规则；在那之前，不再接受 kickoff-only phase 收口后退回泛化 idle 态。
- 影响范围：`PROGRESS.md`、`RUN_CONTEXT.md`、`docs/phase-4-ocr-layout-assist-plan.md`、`.agents/skills/parallel-autopilot/references/state-artifacts.md`
- 探针验证：已通过（Phase 4 kickoff 收口后，当前状态已明确切到“等待高风险 OCR / layout assist 的实现阶段规划”，而不是模糊地等待新目标）。

## ADR-056：高风险 OCR / Layout Assist 的首轮实现规划应先采用 single-lane-first，而不是强拆伪并行 lane
- 状态：已决定
- 日期：2026-03-23
- 背景：Phase 4 kickoff baseline 已冻结了输入范围、风险分桶和 fail-closed contract，但进入实现规划时暴露出一个新的并行 autopilot 痛点：如果为了维持“lane-aware”外观而过早把 OCR/layout assist 拆成多个 lane，会把同一条尚未稳定的契约链拆散到 `bootstrap / assist routing / provenance normalization / downstream re-entry` 多个共享写集上，结果不是并行提速，而是更早暴露 write-set 冲突、contract 漂移和 acceptance 失真。
- 候选方案：
  - 立即拆出多个 lane，例如 `scan lane`、`review lane`、`export lane`
  - 在 implementation planning 阶段明确采用 single-lane-first，先冻结首轮 execution contract，再决定是否值得拆 lane
- 最终决定：采用第二种。自 `MDU-20.1.1` 起：
  - Phase 4 的首轮实现规划先不强拆多个 lane
  - 推荐执行形态是 `Wave 0 = implementation planning`、`Wave 1 = lane-ocr-layout-assist-core`、`Wave 2 = focused acceptance / integration checkpoint`
  - 只有在 `lane-ocr-layout-assist-core` 的 write set、contract tags 与 acceptance gate 冻结后，才允许继续评估后续 lane 拆分
- 代价与妥协：这会让“并行 autopilot”在当前目标上表现得更克制，短期内仍是单 lane 优先；但它避免了伪并行带来的频繁重排，也更符合当前 OCR/layout assist 的共享契约现实。
- 推翻条件：如果后续 `MDU-20.1.2` 证明 execution contract 已经稳定，且可以把写集干净切开，再重新评估是否将后续实现拆成多 lane；在那之前，不再接受为了保持并行外观而强造 lane。
- 影响范围：`docs/phase-4-ocr-layout-assist-implementation-plan.md`、`PROGRESS.md`、`RUN_CONTEXT.md`、`WORK_GRAPH.json`、`.agents/skills/parallel-autopilot/references/lane-policy.md`
- 探针验证：已通过（当前控制面已显式进入 Phase 4 implementation planning，且下一默认动作是冻结 `lane-ocr-layout-assist-core` 的 contract，而不是直接打开多个实现 lane）。

## ADR-057：`lane-ocr-layout-assist-core` 首轮只拥有上游恢复写集，下游 review/export 先保持 consumer 身份
- 状态：已决定
- 日期：2026-03-23
- 背景：`MDU-20.1.2` 的关键问题不是“OCR assist 要不要做”，而是“第一条真实实现 lane 到底拥有哪里”。如果把 `review.py`、`export.py`、`layout_validate.py` 也一起纳入首轮 lane ownership，看起来像是更完整，但实际上会把上游结构恢复 contract 与下游 gate contract 混成一个共享写集，使首轮 focused evidence 失去可解释边界。
- 候选方案：
  - 将首轮 lane 扩成端到端 ownership，同时改上游恢复和下游 gate
  - 将首轮 lane 收缩为上游 OCR / structure recovery ownership，下游 review/export 先只消费信号
- 最终决定：采用第二种。自 `MDU-20.1.2` 起：
  - `lane-ocr-layout-assist-core` 的 primary write set 冻结为：
    - `services/bootstrap`
    - `domain/structure/pdf`
    - `domain/structure/ocr`
    - `services/pdf_structure_refresh`
    - focused regressions / lane-scoped docs
  - 下游 `review.py`、`export.py`、`layout_validate.py` 当前保持 consumer 身份，不作为首轮 lane ownership
  - 首轮 contract tag 明确拆成：
    - `ocr-layout-assist-execution`
    - `ocr-layout-assist-acceptance`
  - acceptance gate 冻结为四类 focused evidence：
    - `bucket-bc-routing`
    - `fail-closed-fallback`
    - `structured-reentry`
    - `low-risk-preservation`
- 代价与妥协：这会让首轮 lane 看起来更窄，短期内不会顺手优化下游 review/export 体验；但它换来了更清晰的 ownership boundary，也避免用“放宽下游 gate”掩盖上游恢复 contract 的问题。
- 推翻条件：如果后续真实实现证明仅靠上游恢复写集无法把 assist 结果稳定回流到主链路，再重新评估是否开启新的下游 contract lane；在那之前，不再接受“上游恢复 + 下游 gate 一起改”作为首轮默认切法。
- 影响范围：`docs/phase-4-ocr-layout-assist-core-plan.md`、`docs/phase-4-ocr-layout-assist-implementation-plan.md`、`PROGRESS.md`、`RUN_CONTEXT.md`、`WORK_GRAPH.json`、`.agents/skills/parallel-autopilot/references/lane-policy.md`
- 探针验证：已通过（lane-scoped contract 已独立落盘，当前控制面下一默认动作已切到 `MDU-20.1.3`，且尚未错误打开 live lane）。

## ADR-058：implementation planning 收口后，必须显式打开 ready-to-claim lane，而不是只留下 frozen contract
- 状态：已决定
- 日期：2026-03-23
- 背景：`MDU-20.1.2` 完成后，contract 已冻结，但控制面仍存在一个新的手感问题：如果只把 planning doc 写完整，却不显式打开下一条 ready lane，resume 时仍会卡在“知道该做什么，但没有明确 claim 入口”的灰区。对 lane-aware autopilot 来说，planning 的终点不应只是 frozen contract，而应是一个可被 claim 的真实执行入口。
- 候选方案：
  - planning phase 完成后，只在文档里写“下一步进入实现”
  - planning phase 完成后，显式创建 `wave / lane / current_mdu` 的 ready-to-claim 状态
- 最终决定：采用第二种。自 `MDU-20.1.3` 起：
  - Phase 4 打开 `wave-1-phase-4-core`
  - `LANE_STATE.json` 显式创建 `lane-ocr-layout-assist-core`
  - lane 状态为 `ready`
  - `current_mdu` 指向 `MDU-21.1.1`
  - `RUN_CONTEXT.md` 与 `PROGRESS.md` 不再停留在 planning，而是直接指向首个真实实现入口
- 代价与妥协：状态面会比“planning complete”多一层 live-ready 语义，但它换来了更清晰的 resume 入口，也避免把下一轮 claim 继续留在 operator 脑内。
- 推翻条件：如果未来引入统一 scheduler，可以自动从 frozen contract 生成 ready lane，再评估是否还需要文档级 handoff；在那之前，不再接受 planning 收口后没有 ready-to-claim lane 的半完成状态。
- 影响范围：`docs/phase-4-ocr-layout-assist-core-execution-plan.md`、`PROGRESS.md`、`RUN_CONTEXT.md`、`WORK_GRAPH.json`、`LANE_STATE.json`、`.agents/skills/parallel-autopilot/references/state-artifacts.md`
- 探针验证：已通过（当前状态已切到 `wave-1-phase-4-core / lane-ocr-layout-assist-core / MDU-21.1.1`，而不是继续停留在 planning idle 态）。

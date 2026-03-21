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

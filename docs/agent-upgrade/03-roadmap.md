# 03 · 路线图：从流水线到 agent（分阶段、可验收）

> 每阶段独立可交付，行为变化受 golden 快照与新增 eval 门控。阶段编号 H0–H4；与 `docs/refactor/PLAN.md` 的 P0–P4 衔接（P4 剩余项并入 H0/H3）。所有「修复」引用 `docs/production/gaps-and-risks.md` 的 R 编号。

## 已确认的决策（2026-09-17）

| 议题 | 决策 | 影响的阶段 |
|---|---|---|
| 句子退役与稳定锚点 | 采用**解析版本分叉**而非原地迁移：每次重解析产生新的 parse revision 与新句子集，旧集只读；译文按句子文本指纹自动搬运，搬不过去的重译；issue / 审批 / 审计通过跨版本映射表关联。锚点 = (页, 归一化 bbox, 文本指纹)。H1 设计评审定稿，**H2 与 issue 版本化一起实施**；在此之前 Structure Agent 以顾问模式（只提 issue，不改结构）运行 | H1 / H2 / H3 |
| Terminology 阶段 | `translate_full` 默认走**抽样前置 + 逐章增量修正**（标题、首段、索引/术语页分层抽样）；`thorough` 选项做全书抽取；targeted / review / export run 不跑。锁定策略自动化：人名、缩写、高频无歧义术语自动 LOCKED，其余 PREFERRED，人工只审例外 | H1 |
| 仓库公开 | 当前远端为私有仓库，已推送备份；**H0 结束、公开前**在全新克隆上运行 `scripts/scrub_history.sh` 并强推所有分支 | H0 |

## H0 · 地基（约 2–3 周）：让每次模型调用可信、可计费、可恢复

目标：不改产品行为，把 harness kernel 的地基打好。

- [x] **统一 LLM client**（`workers/providers`，2026-09-17 落地：httpx 连接池、抖动退避、Retry-After、每调用截止时间、缓存 token 修正；`workers/llm_calls.py` 让术语抽取 / 术语调查 / 概念解析 / provider 测试都产生带 `call_kind` 的 `llm.call.*` 事件）：httpx + 连接池 + 整体截止时间 + 指数退避（jitter、上限、遵守 Retry-After）；Responses/Chat 两种模式；修缓存 token 字段（R12）；每次调用必发 `llm.call.started/completed/failed`，含 `call_kind`（translate / concept / glossary / survey / review / repair / qa）、`run_id`、`agent_id`、`cost_usd`。概念解析、术语抽取、一致性 pass 全部改走它。
- [x] **预算与成本单一口径**（2026-09-17）：`usage_summary`、预算护栏、`GET /runs/{id}/cost` 都从 `llm.call.completed` 事件聚合（`RunControlRepository.usage_from_events`）；工作线程用 `core/run_context.py` 绑定 run，review 期重译与概念解析的花费随之归入 run；翻译工作项完成时立即检查预算；物化视图与 `refresh_cost_rollup()` 由迁移 0034 删除（R15）。
- [x] **运行时正确性**（2026-09-17）：R3 暂停类失败把 work item 留在 RETRYABLE_FAILED 且不消耗重试次数，resume 清零 `consecutive_failures`；R4 supervisor 每 tick 回收非活跃 run 的过期租约，`retry_run` 在仍有在途 work item 时拒绝；R5 review/export 线程在途时 translate 阶段不播种（跟进重译仍在 review 线程内同步执行，改为独立 work item 留到 H2 的 Repair Agent）；R10 `pause_accounting` 记录暂停时长，墙钟扣除、无进展窗口从 resume 起算；R11 连接池超时归为可重试；R13 `translate_packet_scope` 让投影、终态、对账、快照都按 targeted run 的包范围判定。
- [x] **部署与密钥**（2026-09-17）：compose 以 `BOOK_AGENT_APP_SCOPE=prod` 运行、挂载整个 `/app/artifacts`、显式传入 `BOOK_AGENT_SECRET_KEY`（缺失则拒绝启动）与 `BOOK_AGENT_TRANSLATION_OPENAI_API_KEY`（Settings 接受这个命名空间变量，仍忽略裸 `OPENAI_API_KEY`）；prod scope 拒绝 echo backend 与 echo 凭据激活（409），不再自动生成密钥；dev 默认仍为 echo（测试与 smoke 依赖），启动时保持原行为。顺带修复 R14 的 SQLite 部分唯一索引。
- [x] **工程护栏**（2026-09-17）：`.env.example` 入库；dev 工具改为 `[dependency-groups]`（`uv sync` 不再卸掉 pytest/ruff）；`ruff check src tests` 清零（tests/scripts 对 E402 豁免）；`.github/workflows/ci.yml`：lint、PostgreSQL service 上迁移到 head、golden 4 件、`scripts/run_tests_per_file.sh` 逐文件跑全套、前端 tsc/vitest/build；`tests/conftest.py` 提供 SQLite/临时目录/echo 夹具供新测试使用（旧 unittest 模块未迁）；parse-IR 输出走 `Settings.parse_ir_root`、测试指到进程临时目录，OCR 的 uv 缓存改到用户缓存目录（R21）。未做：`ruff format`（226 文件待重排，另行一次性提交）。
- [ ] **公开前历史清洗**：H0 收尾时在全新克隆上运行 `scripts/scrub_history.sh`，核对后强推所有分支，再把仓库设为公开。
- [x] **Prompt 缓存前缀**（2026-09-17）：`TranslationPromptRequest.messages` 按「静态 system（人设 + Core Translation Contract + 风格/记忆规则 + profile 附加段）→ 章级 system（Dynamic Packet Guidance）→ packet user」发送；chat 模式把 JSON schema 契约放进静态 system 消息，user 消息只带 `packet_id must equal`；新增 `translation_openai_structured_output_mode=json_schema` 走约束解码。守护线 guardrail 优先句移入 guardrail 段。prompt golden 因此更新一次（内容不变、位置变化）。书级 BOOK.md 段留到 H1。

验收：全部 golden 通过；新增 `test_llm_call_accounting`（任意路径的调用都有事件与成本）；kill -9 恢复测试；PG 并发测试进 CI。

**H0 收口记录（2026-09-17）**：提交 `d88fdfa` → `fee6e47` 共 8 个。逐文件全套 112 个测试文件通过（本机 `scripts/run_tests_per_file.sh`），前端 tsc / vitest 通过，PostgreSQL 16 上迁移到 `20260917_0034` 并回退验证。覆盖验收项：golden 4 套通过；`test_llm_call_accounting` 与 `test_run_usage_accounting`；PG 并发/漂移/事件/SSE 测试进 CI（`BOOK_AGENT_RUN_PG_TESTS=1`）。未单独补的：模拟 kill -9 的端到端恢复测试（现有 `test_executor_reclaims_expired_leases_before_stage_progression` 与 `test_runtime_recovery` 覆盖租约过期回收与暂停恢复，进程级重启场景留到 H1 的 agent turn 恢复测试一起做）；`ruff format` 全库重排；公开前历史清洗待操作者决定时机。

## H1 · Harness kernel + 第一个 agent（约 4 周）

目标：AgentTurn 成为 work item；ToolRegistry、权限、审批、trace 落地；Terminology Agent 上线并可度量。

- [x] 表：`agent_turns`、`agent_items`、`approvals`、`decisions`（迁移 0035，PG 升降级已验证）；`WorkItemStage.AGENT`；执行器 `_process_agent_stage` / `_execute_agent_work_item`（复用租约/心跳/预算；turn 是持久状态，work item 只是执行尝试；等待审批时阶段保持 running，审批后播种新的尝试）。
- [x] **设计评审**：解析版本分叉方案（新 parse revision + 新句子集 + 指纹搬运 + 跨版本映射表）与稳定锚点格式定稿，附 dry-run 迁移脚本；不在 H1 实施。 → 见 `04-parse-revision-forking.md`（dry-run 脚本随 H2 迁移一起交付）。
- [x] `harness/` 包（2026-09-17）：`kernel/turn.py`（从账本重建消息、无 session 采样、只追加 item、compaction、预算暂停、崩溃后续跑）、`tools/registry.py`（pydantic 输入 → provider schema、权限等级）、`tools/permissions.py`（read / write_reversible 直接执行，write_irreversible 需审批，AutoApproveRule 自动批准并记 AUTO_APPROVED）、`tools/hooks.py`（每 turn 调用上限、审计）、`kernel/trace.py`（agent.turn/tool/approval 事件）。未做：pre_persist hook（翻译期术语拦截仍在原 hook）、OTel span。
- [x] 工具 v1（`harness/tools/book_tools.py`）：`search_book`、`read_block`、`read_chapter_outline`、`get_glossary`、`propose_term`（PREFERRED）、`lock_term`（LOCKED，需审批；人名/机构/缩写/书名/地名与出现 ≥10 句的术语自动批准）、`record_decision`。审批不是工具而是权限层产生的对象。
- [x] **Terminology Agent**（`harness/agents/terminology.py`）：`translate_full` 计划以 `terminology` 为第一阶段（`run_request.terminology ∈ {sampled(默认), thorough, skip}`），translate 阶段门禁等待它；sampled 模式取标题、每章前两段与每第 8 个散文块（≤40k 字符），`GlossaryExtractionService.extract_from_texts` 在样本上抽取候选、全书计数，再由 agent turn 用工具核实并写入。Echo worker 配 `EchoAgentModel` 让离线流水线直接通过。未做：逐章增量补充。
- [x] BOOK.md v1（`harness/context/book_md.py`）：由 `decisions` + 术语表渲染；决策部分以独立 system 消息进入翻译 prompt（`TranslationTask.book_guidance`，无决策时不出现，golden 不变）。未做：替代 `tech-column-meta-v1` 人设、profile → skill。
- [x] 前端：审批收件箱（approvals）、代理回合、BOOK.md 查看（`/approvals` 页）。后端已提供 `GET /documents/{id}/approvals|decisions|book-guide|agent-turns` 与 `POST /approvals/{id}/approve|reject`（决定后唤醒执行器）。

验收：术语一致率 eval（用 RSI 测试书与一本 EPUB）首轮 ≥ 95%；每个 turn 的 trace 可在 UI 回放；预算超限时 turn 被暂停且可 resume。

## H2 · 传感器与自纠正循环（约 4–6 周）

目标：审校从「规则产 issue」升级为「规则 + 模型双通道传感器」，修复从「规则选动作」升级为「Repair Agent 在候选内决策并可定点编辑」。

- [x] `OutputValidator` 升级为拒绝式 guardrail（覆盖不全 / 空译 / 原文回显 / 长度比异常 → 同 turn 内让模型修正）。实现与计划的差异：修复预算 `BOOK_AGENT_TRANSLATION_MAX_OUTPUT_REPAIRS`（默认 1）用尽后不落 FAILED，而是照旧带 `error_code` 持久化，让审校把它变成 OMISSION/ALIGNMENT issue 交给修复循环；每次拒绝一条 `translation.output.rejected` 事件，重试用量计入同一 translation run。
- [x] `review_issues` 版本化（修 R8：重开不重置、人工状态优先）与 issue API（列表/详情/triage/wontfix/resolve/reopen）。见 `production/gaps-and-risks.md` F 节。`IssueType` 枚举已定义（含 Reviewer Agent 用的 MISTRANSLATION_SEMANTIC/LOGIC/REFERENCE，规则引擎已有路由），列保持 TEXT；规则检测器改用枚举、`evidence` 的 pydantic schema 随 Reviewer Agent 一起落地（该 agent 是第一个需要结构化 evidence 的生产者）。
- [x] **解析版本分叉落地**（句子级）：`services/parse_revision_fork.py` + 迁移 0037（退役句子、`sentence_lineage`）；重解析产生新句子集，旧集退役，same 句子搬运译文，packet 原地重建，退役句子上的非人工 issue 解决；REPARSE 动作与 PDF/EPUB 结构刷新都走该路径。块级/章级分叉与 dry-run 脚本留 H3，详见 `04-parse-revision-forking.md` §9。
- [x] **Reviewer Agent**（`Detector.MODEL`，设计见 `05-reviewer-and-repair-agents.md`）：`translate_full` 新增 `model_review` 阶段（translate 之后、review 之前；run 请求 `model_review=sampled|full|skip`，默认 sampled，每章 3 个最可疑 packet）。工具 `next_review_batch / report_issue / finish_packet` + H1 只读工具；发现写入同一 issue 账本并复用规则引擎的类型与动作；同句同族规则 issue 去重；高置信度严重问题才阻断；重译自动关闭模型 issue，复现两次升级为阻断；模型 evidence 变成重译提示。与计划的差异：文档级单 turn（队列工具推进），不是按章 turn；`edit_segment` 留给 Repair Agent。
- [x] **Repair Agent**（设计见 `05-reviewer-and-repair-agents.md`）：`translate_full` 可选阶段 `repair`（run 请求 `repair_agent=on`，默认关闭），位于 review 之后。规则修复循环（硬上限、人工保留阈值）照旧先跑；剩下的阻断 issue 不再让 review 失败，而是交给修复代理。工具：`list_blocking_issues / get_issue`（读）、`execute_action`（执行已计划动作并重跑，单 issue 至多 2 次）、`edit_segment`（`services/segment_edit.py`：新 attempt、旧译文 SUPERSEDED、审计行与 `translation.segment.edited` 事件，护栏为 protect 块、锁定术语、长度比）、`mark_wontfix`（需人工审批，决定人记为批准者）。阶段成功条件：turn 成功且无活动阻断 issue，否则 run 按原语义失败。与计划的差异：替代的是「剩余阻断的处理」，review 内的规则跟进循环保留；默认关闭，待真实 provider 评估后再决定默认值。
- [x] 术语 hook 统一（翻译期与审校期同一匹配器），pre-persist 拦截。`domain/terminology/enforcement.py` 是唯一判定：review 的 TERM_CONFLICT、翻译输出 guardrail（`output_locked_term_violation`，同 turn 修复）、`glossary.violation` 事件都用它；锁定术语集合 = 文档级 + 本章级 LOCKED（`GlossaryService.locked_terms_for_chapter`，随 packet 在 prepare 阶段加载）。旧的计数式 `detect_violations` 删除。review 的「修饰语变体」跳过规则仍只在审校期生效。
- [x] 前端：issue 工作台（`/issues`：筛选、源/译对照、evidence、历史、triage/wontfix/resolve/reopen、「执行并复核」一键动作）、SSE 接入（`lib/runEvents.ts`：活动 run 订阅 `/runs/{id}/stream`，事件去抖后刷新相关查询，流在线时轮询从 2.5s 放宽到 15s；SQLite 部署返回 501 时自动回退轮询；前端事件清单由 `tests/test_frontend_event_kinds.py` 与后端 `EVENT_KINDS` 保持一致）。

验收：审校 eval 集（人工标注 200 句）上模型审校的精确率/召回率；自纠正后阻断 issue 数下降；无循环失控（审计可证）。

**H2 收口记录（2026-09-17）**：提交 `ba8634e` → `1581275`。逐文件全套 124 个测试文件通过（负载 40–65 时 `test_api_workflow` 的 20–30 秒等待偶发超时，单独重跑均通过）；PostgreSQL 16 上迁移到 `20260917_0037` 并逐个回退验证，PG 漂移/并发/工作流/事件/SSE 测试 31 个通过；前端 tsc / vitest 通过。未完成的验收项：审校 eval（人工标注 200 句上模型审校的精确率/召回率）与「自纠正后阻断 issue 数下降」需要真实 provider 运行，产生费用，待用户同意后执行；Repair Agent 默认关闭直到该评估完成。

## H3 · 结构与交付 agent（约 6 周）

- [x] **Structure Agent**：只对 `parse_confidence` 低 / `layout_risk` 高 / sanity 失败的页触发；工具 `render_page_image`（多模态读页）、`split_block`、`merge_blocks`、`relabel_block`、`link_caption`；每个结构改动经 H2 的解析版本分叉产生新 revision。H2 之前可先以顾问模式上线（只 `open_issue`）。
  - 进度（2026-09-17）：顾问模式已实现（`harness/agents/structure.py`）。`translate_full` 可选阶段 `structure_review`（run 请求 `structure_review=sampled|full`，默认 skip，因为需要能读图的模型），排在术语阶段之前；只看 `pdf_page_evidence` 中高风险或版面可疑的页（sampled 最多 20 页）。工具：`next_suspect_page`（页证据 + 块类型/角色/文本/框）、`render_page_image`（PyMuPDF 渲染页面，图片随工具结果存入账本并以图片消息发给模型，chat 与 responses 两种接口都支持）、`report_structure_problem`（非阻断 `STRUCTURE_SUGGESTION` issue，detector=model）、`finish_page`。未做：`split_block / merge_blocks / relabel_block / link_caption` 写工具——依赖块级解析分叉。
  - 写工具（2026-09-18，迁移 0042）：`services/structure_edits.py` 提供 `relabel_block`、`merge_blocks`、`link_caption`。编辑原地改活动块后对涉及的块做解析版本分叉（合并时跨块对齐句子，整句搬进前一块的译文照常搬运；被保护的块不再搬运译文），再重建 packet；每次编辑写入追加式 `structure_edits`（含编辑前块指纹）。结构刷新（PDF/EPUB 与 REPARSE 动作）后、分叉前回放：块回到编辑时的样子就重新应用（分叉无变化、译文不丢），块变了就记为 stale 不再重试。Structure Agent 在 run 请求 `structure_edits=on` 时获得这三个工具，属不可逆档，每次调用等人审批后才执行；人也可以直接调 `POST /v1/documents/{id}/structure-edits`（列表 `GET` 同路径）。`split_block` 未做：结构刷新按序号匹配块，插入新块会让后续序号整体错位，需要先把刷新改为按稳定锚点匹配。
- [x] 把 22 个 recovery pass 中书籍/出版社特定的部分改为 skills，可按书启用：`domain/structure/recovery_skills.py` 注册 manning-listings、academic-sections、text-only-figures、contextual-image-legends 四个技能（默认全开，与原行为一致），指南在 `skills/structure/<name>/SKILL.md`；`PUT /v1/documents/{id}/recovery-skills` 按书关闭，`POST /v1/documents/{id}/structure-refresh` 以新配置重解析并分叉。未拆出的：`recover_embedded_page_headings` 内部的词形标题猜测（与其它分支交织在一个 pass 里），`_lock_listing_scope` 的词表仍在代码中。
- [x] **Export QA Agent**：读渲染后的 HTML/PDF 截图与 manifest，按验收清单（图覆盖率、未译比例、标题层级、空块）产出报告与 issue；替代 `verify_chapter.py` 的 R1–R8 并删除脚本链路（P4 剩余项）。
  - 进度（2026-09-17）：确定性部分已完成。`export/qa.py` 承载 R1–R8（`scripts/verify_chapter.py` 改为同参数、同报告格式的薄 CLI，交付脚本链路照常可用）并新增标题层级、空块、未译比例检查；`services/export_qa.py` 在每次 run 驱动的 HTML 导出后审计，写 `*.qa.json` 报告，失败项记为非阻断 `EXPORT_QA_FAILURE` issue（通过后自动解决，人工决定优先），结果进入 stage payload。未做：读截图的模型部分（需要多模态工具与 Playwright 截图）、删除脚本链路。
  - 进度（2026-09-17，续）：模型部分已实现（`harness/agents/export_review.py`）：可选阶段 `export_review`（run 请求 `export_review=sampled|full`，默认 skip，需读图模型与 Playwright），在两个 HTML 导出之后；逐个导出产物读规则 QA 报告与文本摘录、截取渲染截图（缺 Playwright 时工具返回明确错误），把缺图、表格错乱、漏译、乱码、版面重叠、标题层级问题记为非阻断 `EXPORT_QA_REVIEW` issue。脚本链路保留（`verify_chapter.py` 已是包内检查的薄 CLI）。
- [x] 导出版本化与原子写（R16、R17）；表格按单元格翻译路径（B-20）。
  - 进度（2026-09-17）：R16、R17 已修（原子写、按产物加锁、`export_versions` 历史与 blob 引用、门禁单次执行、审校包不再落阻断 issue、`ExportUnavailableError` 与门禁错误分离、布局 issue 不再引用合成块 id）。B-20 表格按单元格翻译已做：表格块仍是受保护制品，单元格中的文字（`domain/structure/table_cells.py` 判定：含单词、非数字/单位/缩写/代码/URL）按去重后的单元格切成可译句子，表格进入翻译 packet，导出时把译文替换回表格网格（HTML、Markdown、EPUB 各渲染路径）。已有文档需重解析（结构刷新或解析版本分叉）后才会得到单元格句子。
- [x] 前端生产托管：多阶段 Dockerfile（node 构建 `frontend/dist` → python 镜像，非 root 用户 uid 10001）；`BOOK_AGENT_FRONTEND_DIST_DIR` 配置时 API 进程托管 SPA（`app/ui/spa.py`：`/assets` 长缓存、深链接回退 `index.html`、`/runtime-config.js` 运行时注入 API 前缀）；未配置时保留原状态页。验证：`tests/test_frontend_hosting.py`、本地 `vite build`；**镜像构建未验证**（2026-09-17 本机拉取基础镜像时网络中断，Docker 守护进程随之退出）。

验收：56 个 golden PDF 上结构 eval 不退化；新书（非训练集）上 Structure Agent 介入后误判率低于纯启发式；交付验收报告随导出产出。

**H3 收口记录（2026-09-17）**：提交 `36a1d25` → 本记录。逐文件全套 131 个测试文件通过；PostgreSQL 16 上迁移到 `20260917_0038` 并逐个回退验证、漂移检查通过；前端 tsc / vitest 通过；本地 `vite build` 通过。未完成或未验证：Docker 镜像构建（本机拉取基础镜像失败）；Structure Agent 的结构写工具（依赖块级解析分叉）；验收项「56 个 golden PDF 结构 eval 不退化」由现有 PDF golden 测试覆盖，但「新书上 Structure Agent 介入后误判率低于纯启发式」与导出审读的质量评估需要真实读图模型运行，产生费用，待用户同意。

## H4 · 平台化（持续）

- [x] MCP server：暴露 ToolRegistry 的只读与可逆工具，供 Claude Code / Codex 驱动运维与调参；Agent Client Protocol 视需要。
  - 实现（2026-09-17）：`book-agent mcp`（`src/book_agent/mcp/server.py`，无新依赖的 stdio JSON-RPC 实现），13 个工具，见 `06-mcp-server.md`。不暴露不可逆与花钱的操作；直连数据库、不经 API 鉴权，只适合运维机器。Agent Client Protocol 未做。
- [x] 鉴权与多租户（R1）：API key / OIDC、org 表、按 org 的预算与凭据。
  - 进度（2026-09-17）：API key 与 org 已完成（迁移 0039：`orgs`、`api_keys`（只存 sha256）、`documents.org_id`，同一文件可在不同 org 各导入一次）。`BOOK_AGENT_AUTH_MODE=api_key` 时除 `/health`、`/meta` 外所有 API 需 key（`Authorization: Bearer` 或 `X-API-Key`，事件流另收 `access_token` 查询参数）；角色 viewer/editor/admin；路径中的 document/run/issue/approval/action/export 与请求体中的 document 都校验 org，跨 org 一律 404；prod scope 拒绝关闭鉴权。`POST /documents/bootstrap` 只读上传目录与 `BOOK_AGENT_BOOTSTRAP_SOURCE_ROOTS`；provider `base_url` 解析到私网/回环/链路本地地址时拒绝（可用 `BOOK_AGENT_PROVIDER_ALLOW_PRIVATE_HOSTS` 放开）。`/v1/api-keys` 管理接口与 ``book-agent create-api-key``；前端侧栏可填 key。未做：OIDC、按 org 的 provider 凭据与预算（凭据仍是实例级、仅 admin 可管理）、`last_used_at` 只在写请求时落库。
  - OIDC（2026-09-18）：设置 `BOOK_AGENT_OIDC_ISSUER` 与 `BOOK_AGENT_OIDC_AUDIENCE` 后，`Authorization: Bearer <JWT>` 与 API key 并存（`services/oidc.py`）。经发现文档取 JWKS（缓存 10 分钟；未知 kid 触发重新加载，但最多 30 秒一次，防止伪造 kid 放大请求）；只接受 RS/ES/PS 签名算法，校验 iss、aud、exp、sub（30 秒时钟偏差）；org 声明必须指向已存在的组织（不自动创建），角色声明取 viewer/editor/admin（多个时取最高，缺省时用 `BOOK_AGENT_OIDC_DEFAULT_ROLE`）。API 不做登录跳转，由网关或客户端取 token。
  - 按 org 的凭据与预算（2026-09-18，迁移 0041）：`provider_credentials.org_id` 为空表示共享凭据；每个作用域（共享、各 org）至多一个激活凭据（两个部分唯一索引）。默认 org 的 admin 管共享凭据，其他 org 的 admin 只能看到和管理本 org 的凭据，访问别的作用域一律 404；`GET /providers/active` 返回本 org 实际使用的凭据（本 org 激活的，否则共享的，后者不给 key 预览，`shared` 字段标明）。worker 缓存按作用域分开；执行器从工作线程绑定的 run 查出文档所属 org 再取 worker，API 同步操作按调用者 org 取。`orgs.monthly_budget_usd`：本月（UTC）该 org 各 run 的 `llm.call.completed` 花费达到上限后，启动/恢复/重试 run 返回 409，执行器在下一次 tick 以 `budget.org_monthly_exhausted` 暂停运行中的 run（每进程每 org 最多 10 秒查一次）。只有默认 org 的 admin 能改预算（`PUT /v1/orgs/{org_id}/budget` 或 `book-agent set-org-budget`），成员可读 `GET /v1/orgs/current/budget`。未计入：不属于任何 run 的模型调用（同步 API 操作、provider 连通性测试）。前端尚未展示预算。
- [x] Evals harness：`evals/` 目录 + CLI，每次发布跑翻译/审校/结构/导出四类 eval，结果连同 harness 配置写入 manifest。
  - 实现（2026-09-18）：`book-agent eval [--suite ...] [--output DIR]`（`src/book_agent/evals/`，数据集与阈值见 `evals/README.md`）。四个 suite：terminology（两章测试书 + 5 个锁定术语，锁定术语一致率 ≥ 0.95、覆盖率 = 1.0）、review（逐对标注的原文/译文，Reviewer Agent 全量审校，精确率 ≥ 0.8、召回率 ≥ 0.7，另报类型准确率）、structure（56 个 PDF golden 的块类型一致率 ≥ 0.99，仅仓库检出时运行）、export（测试书合并 HTML 的导出 QA 检查通过率 ≥ 0.9，不经导出闸门）。每次运行用报告目录下的独立 SQLite 库，`report.json` + `summary.md` 带 git commit、backend、模型、prompt 版本与 profile；任一阈值不过退出码为 1；某个 suite 抛异常记为失败而不中断报告。echo 后端下 terminology 与 review 必然不过（echo 不翻译、echo agent 不报问题），用于证明流水线可跑。未做：真实 provider 的基线运行（产生费用，待用户同意）；审校集目前 12 对种子数据，离 200 句人工标注还差。
- [x] 多实例：SKIP LOCKED、leader 或按 run 分片、凭据 revision 落库、迁移 advisory lock。
  - 实现（2026-09-18）：按 run 分片而不是 leader。迁移 0040 给 `document_runs` 加 `executor_owner`、`executor_lease_expires_at`：supervisor 每次轮询对可运行的 run 做条件 UPDATE（无主、自己或已过期才能拿到），只为拿到的 run 起 run loop；run loop 每 tick 续约，续约失败即退出；`stop()` 释放本实例的 run，其他实例立即可接管。租约默认 30 秒（`BOOK_AGENT_RUN_OWNERSHIP_TTL_SECONDS`）。实例 id 为 `host:pid:随机`，也写进 work item 租约的 `worker_instance_id`。`BOOK_AGENT_RUN_EXECUTOR_ENABLED=false` 的副本只提供 API。work item 候选查询加 `FOR UPDATE SKIP LOCKED`，领取仍以 CAS 为准。`provider_credentials.config_revision` 在修改或激活活动凭据时递增，`TranslationWorkerProvider` 以（进程内计数、活动凭据 id、config_revision）为缓存键，库内键最多每 2 秒读一次，所以别的进程改了 provider 最迟 2 秒生效。alembic 在 PostgreSQL 上于迁移事务内取 `pg_advisory_xact_lock`，本机 PG 16 上两个并发 `alembic upgrade head` 只有一个执行升级、另一个等待后无事可做。未做：`wake()` 跨实例广播（其他实例靠轮询）；多进程下的端到端压力测试。
- [x] 观测：OTel 导出、Prometheus 指标（tick 时延、租约过期、池占用、每 agent 成本）。
  - 进度（2026-09-17）：Prometheus 指标已完成，无新依赖（`infra/metrics.py` + `app/metrics_route.py`，`GET /metrics`）：HTTP 请求数与时延（按路由模板）、run loop tick 时延、work item 结束结果（按阶段）、回收的过期租约、模型调用次数/token/费用/时延（按 call_kind，agent 调用各有自己的 call_kind）；抓取时读取连接池占用、各状态 run 数、未结束 work item 数。访问：`BOOK_AGENT_METRICS_TOKEN` 作为 bearer；未设时开启鉴权则要 admin key。每个进程一份计数，按进程抓取。OTel trace 导出（2026-09-18）：可选依赖 `book-agent[otel]`（Docker 镜像已包含），`BOOK_AGENT_OTEL_TRACES_ENABLED=true` 开启，OTLP/HTTP 导出器读标准 `OTEL_EXPORTER_OTLP_*` 变量。span：HTTP 请求（沿用调用方的 W3C `traceparent`，按路由模板命名）、run loop tick、work item（run、scope、attempt、结果）、provider 请求（模型、主机、重试次数、token）、agent 步骤与工具调用（turn、agent 类型、权限、成败）。关闭时所有埋点是空操作。未做：work item span 与所属 tick span 的关联（工作线程另起根 span，靠 `book_agent.run_id` 属性关联）；数据库查询 span。

## 依赖与并行关系

```
H0 ──▶ H1 ──▶ H2 ──▶ H3 ──▶ H4
 │             │
 └─ production/gaps E.1–E.3 与 H0 并行
               └─ 前端 SSE/issue 工作台可与 H2 并行
```

解析版本分叉在 H1 设计评审、H2 实施，是 H3 Structure Agent 自动应用改动的前提。

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
- [ ] **解析版本分叉落地**：`document_parse_revisions` 成为一等版本；重解析产生新句子集，旧集 `retired`；句级指纹搬运译文；`revision_links` 映射 issue/审批/审计；`pdf/epub_structure_refresh` 改走该路径（替代「只标 stale 不重建」）。
- [ ] **Reviewer/Editor Agent**（`Detector.MODEL`，设计见 `05-reviewer-and-repair-agents.md`）：按章抽样或全量（可配置）；输入源句 + 译文 + BOOK.md；输出结构化 issue（含置信度、建议改写）；与规则 issue 合并去重。
- [ ] **Repair Agent**：输入 issue 束 + 规则引擎给出的候选动作；工具 `retranslate_packet`（新 attempt）、`edit_segment`（新 attempt，最小改动）、`lock_term`（审批）、`mark_wontfix`（审批或阈值）；替换现有三个循环中的「自动跟进」决策，保留硬上限与人工保留阈值。
- [ ] 术语 hook 统一（翻译期与审校期同一匹配器），pre-persist 拦截。
- [ ] 前端：issue 工作台（evidence、源/译对照、一键动作）、SSE 接入替代轮询（R20 一部分）。

验收：审校 eval 集（人工标注 200 句）上模型审校的精确率/召回率；自纠正后阻断 issue 数下降；无循环失控（审计可证）。

## H3 · 结构与交付 agent（约 6 周）

- [ ] **Structure Agent**：只对 `parse_confidence` 低 / `layout_risk` 高 / sanity 失败的页触发；工具 `render_page_image`（多模态读页）、`split_block`、`merge_blocks`、`relabel_block`、`link_caption`；每个结构改动经 H2 的解析版本分叉产生新 revision。H2 之前可先以顾问模式上线（只 `open_issue`）。
- [ ] 把 22 个 recovery pass 中书籍/出版社特定的部分（Listing 规则、词形标题猜测等）改为 skills，可按书启用。
- [ ] **Export QA Agent**：读渲染后的 HTML/PDF 截图与 manifest，按验收清单（图覆盖率、未译比例、标题层级、空块）产出报告与 issue；替代 `verify_chapter.py` 的 R1–R8 并删除脚本链路（P4 剩余项）。
- [ ] 导出版本化与原子写（R16、R17）；表格按单元格翻译路径（B-20）。
- [ ] 前端生产托管（多阶段 Dockerfile、StaticFiles 或反向代理、运行时配置注入）。

验收：56 个 golden PDF 上结构 eval 不退化；新书（非训练集）上 Structure Agent 介入后误判率低于纯启发式；交付验收报告随导出产出。

## H4 · 平台化（持续）

- [ ] MCP server：暴露 ToolRegistry 的只读与可逆工具，供 Claude Code / Codex 驱动运维与调参；Agent Client Protocol 视需要。
- [ ] 鉴权与多租户（R1）：API key / OIDC、org 表、按 org 的预算与凭据。
- [ ] Evals harness：`evals/` 目录 + CLI，每次发布跑翻译/审校/结构/导出四类 eval，结果连同 harness 配置写入 manifest。
- [ ] 多实例：SKIP LOCKED、leader 或按 run 分片、凭据 revision 落库、迁移 advisory lock。
- [ ] 观测：OTel 导出、Prometheus 指标（tick 时延、租约过期、池占用、每 agent 成本）。

## 依赖与并行关系

```
H0 ──▶ H1 ──▶ H2 ──▶ H3 ──▶ H4
 │             │
 └─ production/gaps E.1–E.3 与 H0 并行
               └─ 前端 SSE/issue 工作台可与 H2 并行
```

解析版本分叉在 H1 设计评审、H2 实施，是 H3 Structure Agent 自动应用改动的前提。

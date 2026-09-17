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
- [ ] **部署与密钥**：R2（compose 挂整个 artifacts；`BOOK_AGENT_SECRET_KEY` 必填、启动校验）、R6（容器读 key 的路径；默认 backend 不再是 echo；生产 scope 拒绝 Echo 凭据激活）。
- [ ] **工程护栏**：R9（`.env.example` 入库；`[dependency-groups] dev`；CI：ruff + 前端 tsc/vitest + golden 4 件 + 单测分片；PG 测试用 service 容器）；conftest 统一 SQLite/临时目录/echo 夹具；测试产物不再写仓库（R21）。
- [ ] **公开前历史清洗**：H0 收尾时在全新克隆上运行 `scripts/scrub_history.sh`，核对后强推所有分支，再把仓库设为公开。
- [ ] **Prompt 缓存前缀**：把已有的 `system_prompt_static/dynamic` 真正按「静态契约 → 书级段 → 章级段 → packet 段」发送；schema 不再塞 user prompt（Responses 模式用 `json_schema`，Chat 模式用 tool-call）。golden 更新一次并记录原因。

验收：全部 golden 通过；新增 `test_llm_call_accounting`（任意路径的调用都有事件与成本）；kill -9 恢复测试；PG 并发测试进 CI。

## H1 · Harness kernel + 第一个 agent（约 4 周）

目标：AgentTurn 成为 work item；ToolRegistry、权限、审批、trace 落地；Terminology Agent 上线并可度量。

- [ ] 表：`agent_turns`、`agent_items`、`approvals`、`decisions`（见 02 §4）；`WorkItemStage.AGENT`；执行器新增 `_execute_agent_work_item`（复用租约/心跳/预算）。
- [ ] **设计评审**：解析版本分叉方案（新 parse revision + 新句子集 + 指纹搬运 + 跨版本映射表）与稳定锚点格式定稿，附 dry-run 迁移脚本；不在 H1 实施。
- [ ] `harness/` 包：`Turn`（无状态请求、只追加 item、compaction）、`ToolRegistry`（pydantic 输入输出、权限等级、幂等键）、`PermissionPolicy`、`Hooks`（pre_tool / post_tool / pre_persist）、`Trace`（OTel span ↔ events）。
- [ ] 工具 v1（只读 + 可逆写）：`search_book`、`read_block`、`read_chapter_outline`、`get_glossary`、`propose_term`、`record_decision`、`request_approval`。
- [ ] **Terminology Agent**：`run_plan` 新增 `terminology` stage，仅 `translate_full` 默认启用（`run_request.terminology ∈ {sampled(默认), thorough, skip}`）；sampled 模式对标题、首段、索引/术语页分层抽样调用 `GlossaryExtractionService`，之后每章翻译前做增量补充；输出 BOOK.md 术语段与 `term_entries`；锁定按自动策略（人名/缩写/高频无歧义 → LOCKED，其余 PREFERRED），例外走审批。
- [ ] BOOK.md v1：术语表 + 体裁/语域 + 保留策略；进入翻译 prompt 的书级段（替代硬编码 `tech-column-meta-v1` 人设，profile 改为 skill 选择）。
- [ ] 前端：审批收件箱（approvals）、BOOK.md 查看。

验收：术语一致率 eval（用 RSI 测试书与一本 EPUB）首轮 ≥ 95%；每个 turn 的 trace 可在 UI 回放；预算超限时 turn 被暂停且可 resume。

## H2 · 传感器与自纠正循环（约 4–6 周）

目标：审校从「规则产 issue」升级为「规则 + 模型双通道传感器」，修复从「规则选动作」升级为「Repair Agent 在候选内决策并可定点编辑」。

- [ ] `OutputValidator` 升级为拒绝式 guardrail（覆盖不全 / 空译 / 原文回显 / 长度比异常 → 同 turn 内让模型修正，超预算才落 FAILED）。
- [ ] `review_issues` 引入 `issue_type` 枚举、`evidence` schema、版本行（修 R8：重开不重置、人工状态优先）；issue API（列表/详情/triage/wontfix/resolve）。
- [ ] **解析版本分叉落地**：`document_parse_revisions` 成为一等版本；重解析产生新句子集，旧集 `retired`；句级指纹搬运译文；`revision_links` 映射 issue/审批/审计；`pdf/epub_structure_refresh` 改走该路径（替代「只标 stale 不重建」）。
- [ ] **Reviewer/Editor Agent**（`Detector.MODEL`）：按章抽样或全量（可配置）；输入源句 + 译文 + BOOK.md；输出结构化 issue（含置信度、建议改写）；与规则 issue 合并去重。
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

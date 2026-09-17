# 02 · 目标架构：book-agent 的 harness 设计

> 本篇给出可实施的设计：新增哪些表与包、每个 harness 原语如何映射到现有代码、各 agent 的职责/工具/预算/审批边界。写法上尽量引用现有模块，让改动是「增量」而不是「重写」。原语命名沿用业界（turn / item / tool / hook / skill / subagent / approval / sensor），便于对照 `01-harness-landscape.md`。

## 1. 分层

```
harness/                      新包（与 app/ services/ 平级）
  kernel/   turn.py item.py budget.py compaction.py trace.py
  tools/    registry.py permissions.py schemas.py hooks.py
  context/  assembler.py book_md.py skills.py
  agents/   base.py terminology.py translator.py reviewer.py repair.py structure.py export_qa.py
  sensors/  computational.py (适配现有 review/gate/validator) inferential.py (模型评审、回译)
  approvals/ service.py policies.py
  mcp/      server.py (H4)
```

依赖方向：`harness.agents → harness.kernel/tools/context/sensors → services/* & infra/*`。`app/runtime/document_run_executor.py` 只新增一种 work item 的执行分支；`orchestrator/run_plan.py` 新增阶段。现有 `services/translation.py` 的 prepare/persist 变成 Translator agent 的两个工具实现。

## 2. Harness kernel

### 2.1 AgentTurn（持久化的 agent 循环）

- 一个 turn = 一个 work item（`stage=AGENT`, `scope_type ∈ {document, chapter, packet, issue}`），带租约、心跳、重试、预算——直接复用 02 篇描述的机制。
- turn 内循环：`assemble_context → sample → (tool_calls → execute → append items)* → final`。每一步的 item 写入 `agent_items`（append-only），模型请求**无状态**：每次把 turn 的 item 列表按缓存友好顺序重建（对应 Codex 的做法），不依赖 provider 的会话 id。
- **只追加，不修改**：配置变化（预算调整、权限收紧）以 `developer` item 追加。
- **压缩**：item 总 token 超阈值时生成 `compaction` item（模型生成的摘要 + 关键决策引用），后续请求用摘要替换早期 item；原 item 仍在库中供回放。
- 崩溃恢复：租约过期 → 新 worker 读取 `agent_items` 续跑（工具调用有幂等键，见 §3），无需从头开始。
- 表：

| 表 | 关键列 |
|---|---|
| `agent_turns` | id, run_id, work_item_id, agent_kind, scope_type, scope_id, status(running/succeeded/failed/paused/awaiting_approval), model, harness_version, skills_json, budget_json, usage_json, started_at, finished_at |
| `agent_items` | id BIGSERIAL, turn_id, ordinal, kind(system/developer/user/assistant/tool_call/tool_result/compaction/approval_request/approval_result), content_json, token_count, created_at；UNIQUE(turn_id, ordinal) |
| `approvals` | id, turn_id, tool_call_item_id, kind, payload_json, status(pending/approved/rejected/auto_approved/expired), decided_by, decided_at, policy_id |
| `decisions` | id, document_id, scope, key, value_json, rationale, decided_by(agent/human), turn_id, created_at（append-only；BOOK.md 的物化来源） |

### 2.2 预算

- 每 turn：`max_tokens_in/out`、`max_cost_usd`、`max_tool_calls`、`max_wall_clock`；每 run 的 `run_budgets` 是上限总和；超限 → turn `paused` 并写 `approval_request(kind=budget_extension)`。
- 所有模型调用经统一 client（H0），事件 `llm.call.*` 带 `turn_id/agent_kind/call_kind`，`usage_summary` 由事件派生，修掉 04 §10 里的四套计费。

### 2.3 Trace

- 每个 turn 一个 OTel trace，每个 item / 工具调用一个 span；`events` 表新增 `agent.turn.started/finished`、`agent.tool.called/returned`、`agent.approval.requested/decided`（把 27 种事件目录里的 `agent.*` 真正用起来）。SSE 直接推送这些事件，UI 可回放 turn。

## 3. 工具层

### 3.1 ToolRegistry

- 每个工具 = pydantic 输入/输出 + 权限等级 + 幂等键函数 + 实现（调用现有 service）。模型看到的是 JSON schema（tool-call 模式），不再把 schema 塞进 user prompt。
- 权限等级：
  - **R（只读）**：`search_book(query, scope)`, `read_block(block_id)`, `read_sentences(block_id)`, `read_chapter_outline(chapter_id)`, `read_page_image(page)`（多模态）, `get_glossary(document_id)`, `get_book_md()`, `get_issues(filter)`, `get_translation(sentence_ids)`, `render_preview(chapter_id, export_type)`.
  - **W-rev（可逆写，产生新版本）**：`propose_term`, `retranslate_packet(packet_id, hints)`（新 attempt）, `edit_segment(segment_id, new_text, reason)`（新 attempt + supersede）, `open_issue`, `resolve_issue(issue_id, note)`, `record_decision`, `split_block/merge_blocks/relabel_block`（新 parse revision，见 §6）.
  - **W-irr（不可逆或高影响，需审批对象）**：`lock_term`, `mark_wontfix`, `reparse_chapter`, `publish_export`, `extend_budget`.
- 幂等：工具调用 item 带 `idempotency_key = stable_id(turn_id, ordinal)`；实现层用它做 upsert 或跳过（复用现有 `stable_id` 习惯）。
- 每个工具有 `max_calls_per_turn` 与 `cost_hint`，纳入预算。

### 3.2 Hooks（确定性护栏，代码而非 prompt）

| hook | 时机 | 现有实现来源 | 行为 |
|---|---|---|---|
| `glossary_enforcement` | pre_persist（翻译 / 编辑） | `services/glossary_enforcement.py` + `domain/terminology/matching.py`（统一为一份） | LOCKED 术语缺失 → 拒绝并把违规作为 tool_result 反馈给模型，同 turn 内改；超次数才落 issue |
| `output_coverage` | pre_persist | `translation/output_validation.py` | 覆盖不全 / 空译 / 原文回显 / 长度比 → 同上 |
| `protect_policy` | pre_tool(edit_segment / retranslate) | `domain/block_rules.py`（接上 `docir_translatability`，修 R7） | 拒绝改动 protect 块 |
| `budget_guard` | pre_tool / post_llm | `RunExecutionService.enforce_budget_guardrails` | 超限暂停 turn |
| `approval_gate` | pre_tool(W-irr) | 新 | 生成 `approvals` 行并把 turn 置 `awaiting_approval`；策略可自动批准（如人名/缩写术语） |
| `audit` | post_tool | `audit_events` | 每次工具调用一条审计 |

### 3.3 Skills（领域专长目录，替代硬编码启发式）

```
skills/
  translation/tech-book-zh/SKILL.md        # 语域、句式、术语策略、示例（替代 tech-column-meta-v1 与 heuristics pack）
  translation/trading-book-zh/SKILL.md
  structure/manning-listing/SKILL.md       # Listing N.M 规则（替代 _lock_listing_scope）
  structure/academic-paper/SKILL.md
  review/naturalness-zh/SKILL.md           # 直译味规则（替代 style_drift_rules）
  export/qa-checklist/SKILL.md
```

- `document.metadata_json.skills`（H1 起）指定启用的 skill 集；Context Assembler 把 SKILL.md 放进书级缓存前缀；skill 内的确定性检查（正则表）由 sensors 加载。
- 现有 `translation/heuristics/tech-book-default.json` 与 13 个 prompt profile 迁移为 skills，golden 用「skill 集固定」的方式保持。

## 4. 上下文层

### 4.1 Context Assembler（缓存友好前缀）

顺序固定、只追加：

1. **静态契约**（harness 版本级）：输出协议、工具使用规则、安全规则。
2. **BOOK.md**（书级，翻译前由 Terminology Agent + 人生成，之后只追加决策）：书名/作者/体裁、语域、术语表（LOCKED/PREFERRED）、保留策略（代码/表格/引用）、已决议歧义、风格样例。
3. **skills**（书级选定）。
4. **章级静态段**：章节简介、章内已锁概念、章节记忆压缩摘要。
5. **动态段**：packet / issue / 页图像等当前任务输入。

翻译 prompt 的 5 段落协议不变（golden 保护），只是 1–3 段提前到缓存前缀。

### 4.2 记忆

- **书级**：`decisions` 表 → BOOK.md；`term_entries` 是术语真相；`style_delta` 快照类型终于有写入方（Reviewer 汇总）。
- **章级**：现有 `ChapterMemory`，但 proposal 改为「翻译 turn 结束即提交，审校发现问题再 supersede」（解决首轮记忆为空），冲突检测统一为版本 CAS。
- **turn 级**：`agent_items` + compaction。

## 5. 各 agent 的职责边界

| agent | 触发（run_plan 阶段） | 输入 | 工具（等级） | 传感器 | 预算与审批 |
|---|---|---|---|---|---|
| **Terminology** | `terminology` 必需阶段，translate 之前 | 全书抽样、`GlossaryExtractionService` 候选、现有术语表 | search_book(R)、propose_term(W-rev)、lock_term(W-irr)、record_decision | 变体匹配一致性统计 | 每本书一个 turn；LOCKED 走审批（策略自动批人名/缩写/高频） |
| **Translator** | `translate`（现有） | packet + 编译上下文 | 无自由工具；输出经 hooks | glossary、coverage | 现有三段事务，仅 client/prompt 前缀变化 |
| **Reviewer/Editor** | `review` | 章译文 + BOOK.md | read_*(R)、open_issue(W-rev)、edit_segment(W-rev，仅小改) | 规则 issue 作为输入去重；回译抽样 | 按章 turn；抽样比例可配；`Detector.MODEL` + confidence |
| **Repair** | review 后 / gate 失败后 | issue 束 + 规则引擎候选动作 | retranslate_packet、edit_segment、resolve_issue、lock_term(W-irr)、mark_wontfix(W-irr)、reparse_chapter(W-irr) | 修复后再跑规则传感器 | 替代三个自动循环的「选动作」；硬上限与人工保留阈值保留 |
| **Structure** | bootstrap 后、仅低置信页/章 | 页图像 + 当前 block 序列 + 解析证据 | read_page_image(R)、split/merge/relabel/link_caption(W-rev)、reparse_chapter(W-irr) | 布局校验、golden 对照 | 每页一个 turn，限次；不可逆改动走审批 |
| **Export QA** | 每次导出后 | 渲染产物截图、manifest | render_preview(R)、open_issue(W-rev)、publish_export(W-irr) | 图覆盖率/未译比例/标题层级/空块 | 验收报告进 manifest；不通过则导出保持 draft |

编排仍是现有执行器：agent 阶段是 run_plan 的阶段，阶段门（`StageGateKeeper`）决定顺序；handoff 就是「上游阶段 SUCCEEDED」。

## 6. 数据模型的必要变更（与 07 篇对照）

1. **版本化而非覆盖**：`review_issues`、`issue_actions`、`exports` 引入 `version` 与 `superseded_by`，`created_at` 不再重置；人工状态（WONTFIX/RESOLVED by human）优先于自动重开。
2. **句子退役模型**：`sentences.status` 增加 `retired` + `replaced_by`；packet/segment/edge 引用旧句子时保留历史；Structure Agent 的结构改动产生新 `document_parse_revisions` 行并迁移可对齐的译文。稳定锚点 = (页, 归一化 bbox, 文本指纹) 而非全书计数。
3. **JSON 契约**：`source_span_json`、`evidence_json`、`status_detail_json`、`packet_json` 各有 pydantic schema，仓储读写校验；`status_detail_json` 拆出 `usage`（从事件派生）与 `run_request`（独立列）。
4. **新表**：`agent_turns`、`agent_items`、`approvals`、`decisions`（§2.1）；`skills` 可先用文件系统。
5. **索引与 FK**：07 §9.3 清单；`events.run_id` 改 UUID + FK。

## 7. 人在环

- **审批收件箱**：`approvals` 表驱动；UI 列出待审批（术语锁定、重解析、WONTFIX、预算扩展、发布），批准/拒绝写事件并唤醒 turn。
- **issue 工作台**：issue 列表/详情（evidence、源/译对照、来源 detector、置信度）、triage、指派（复用 worklist）、一键交给 Repair Agent。
- **BOOK.md 编辑**：人可追加决策（append-only），agent 下次 turn 自动读到。
- **审批策略**：按 org/document 配置，例如「人名/缩写自动锁定」「WONTFIX 需要人」「预算扩展 ≤ 20% 自动」。

## 8. 传感器与评估

- **computational sensors**（保留并升级）：16 项审校规则、导出 gate、layout validator、`OutputValidator`、术语匹配、`term-consistency` 统计——统一接口 `Sensor.run(scope) -> list[Finding]`，可按 skill 开关。
- **inferential sensors**：模型评审（Reviewer）、回译一致性抽样、导出截图审阅（Export QA）。
- **evals**（`evals/`）：
  - 翻译：RSI 书与 EPUB 夹具的人工标注集 + LLM-as-judge，指标 = 术语一致率、覆盖率、自然度评分。
  - 审校：200 句标注集的精确率/召回率。
  - 结构：56 个 golden PDF 的 block 分类准确率 + 新书盲测。
  - 端到端：一本书的成本、时长、阻断次数、验收报告。
  - 每次 run 的 manifest 记录 harness 版本、skills、模型、预算——评测结果与 harness 配置一起披露。

## 9. 与 Codex/Claude Code 机制的逐项对照

| 机制 | Codex / Claude Code | book-agent 实现 |
|---|---|---|
| Agent loop | turn/item，无状态请求，只追加 | `agent_turns/agent_items`，work item 承载 |
| Prompt cache | system→tools→instructions→input，AGENTS.md 常驻 | Context Assembler 五段前缀，BOOK.md 常驻 |
| Compaction | `/responses/compact` | compaction item（模型摘要，provider 无关） |
| Sandbox / approval | 命令沙箱 + 审批策略 | 封闭 ToolRegistry + 权限等级 + `approvals` |
| Hooks | 生命周期 shell hook | pre_tool/post_tool/pre_persist Python hook |
| Skills | SKILL.md 目录 | `skills/` 目录，按书选用 |
| Subagents | 独立上下文的 Task | 每种 agent 一个 work item 类型，独立预算 |
| Handoffs / guardrails / tracing（Agents SDK） | 一等概念 | 阶段门 / hooks+sensors / OTel+events |
| MCP | 工具协议 | H4：把 ToolRegistry 暴露为 MCP server |
| 「组件会随模型进步而消失」 | 设计原则 | recovery pass、render 修补作为 skills/插件，可按模型能力下线 |

## 10. 风险与对策

- **成本上升**：agent 引入更多调用。对策：Reviewer/Structure/Export QA 都是抽样与条件触发；每 turn 预算；成本进 manifest。
- **不可复现**：模型决策非确定。对策：所有决策落 `decisions`/`agent_items`，可回放；temperature/seed 入库；评测披露配置。
- **golden 大面积变化**：prompt 前缀重排、profile 迁 skill。对策：H0 内一次性有意更新并记录；之后 skill 集固定。
- **数据迁移风险**：句子退役与锚点方案。对策：H1 期间做设计评审与 dry-run 迁移脚本；H3 才启用。
- **两套编排**：新 harness 与旧执行器并存。对策：明确 agent turn 只是 work item 的一种，不引入第二个调度器。

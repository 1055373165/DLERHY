# 05 · 审校、问题与修复动作

> 来源：2026-09-16 对分支 `refactor/p0-stabilize`（HEAD `2176d51`）的逐文件源码阅读。行号对应该基线；标注「待确认」的条目未经运行验证。总览与跨子系统结论见 [`00-overview.md`](00-overview.md)。

# book-agent 审校 / 问题 / 修复动作 / 工作流门面与应用层 —— 源码认知笔记

> 阅读基线：分支 `refactor/p0-stabilize`，HEAD `2176d51`。所有行号均对应该基线。
> 所有陈述均来自源码；标注「待确认」的地方是我未能用源码或运行完全证实的推断。
> 我另外用 `.venv/bin/python` 跑了一个探针脚本（`scratchpad/merge_probe.py`）验证 SQLAlchemy `merge` 在 issue 重复检出时的行为，结果见 §3.7。

---

## 0. 阅读清单与整体地图

| 层 | 文件 | 行数 | 角色 |
|---|---|---|---|
| 服务 | `src/book_agent/services/review.py` | 1943 | `ReviewService`：章节级规则审校，产出 issue / action / rerun plan / summary |
| 服务 | `src/book_agent/services/rerun.py` | 156 | `RerunService`：按 `RerunPlan` 执行 realign / reparse / rebuild+重译，然后再审一遍 |
| 服务 | `src/book_agent/services/actions.py` | 161 | `IssueActionExecutor`：把 action 落成 invalidation + audit（不跑 LLM） |
| 服务 | `src/book_agent/services/rebuild.py` | 557 | `TargetedRebuildService`：重建 brief / termbase / packet |
| 服务 | `src/book_agent/services/realign.py` | 267 | `RealignService`：从 run 输出重建对齐边 |
| 服务 | `src/book_agent/services/style_drift.py` | 20 | 只是把启发式包里的 style drift 规则转成 prompt guardrail 行 |
| 服务 | `src/book_agent/services/glossary_enforcement.py` | 157 | 纯函数术语违规检测；**不被 review 调用**，只在翻译服务里发事件 |
| 服务 | `src/book_agent/services/workflows.py` | 441 | `DocumentWorkflowService` 门面 |
| 编排 | `src/book_agent/orchestrator/rule_engine.py` | 130 | issue → action 映射与作用域 |
| 编排 | `src/book_agent/orchestrator/rerun.py` | 140 | `RerunPlan`、concept override / style hint 投影 |
| 应用 | `src/book_agent/application/review_repair.py` | 813 | 文档级审校、自动跟进、阻塞修复循环 |
| 应用 | `src/book_agent/application/issue_actions.py` | 104 | action 执行 + 「人工保留」判定 |
| 应用 | `src/book_agent/application/issue_queries.py` | 359 | issue 聚合查询 |
| 应用 | `src/book_agent/application/worklist.py` | 387 | 章节工作单与分配 |
| 应用 | `src/book_agent/application/analytics.py` | 1077 | 纯聚合函数（热度、队列、SLA、用量） |
| 应用 | `src/book_agent/application/memory_proposals.py` | 364 | 记忆提案审批 |
| 应用 | `src/book_agent/application/document_queries.py` | 566 | 文档摘要、历史、导出看板 |
| 应用 | `src/book_agent/application/export_use_case.py` | 357 | 导出 + gate 自动跟进 |
| 应用 | `src/book_agent/application/read_models.py` | 768 | 结果 dataclass |
| 领域 | `src/book_agent/domain/models/review.py` | 165 | `ReviewIssue` / `ChapterQualitySummary` / `IssueAction` / `Export` ORM |
| 领域 | `src/book_agent/domain/enums.py` | 376 | 枚举 |
| 基础设施 | `src/book_agent/infra/repositories/review.py` | 205 | `ChapterReviewBundle` 加载与持久化 |
| 基础设施 | `src/book_agent/infra/repositories/ops.py` | 174 | action / issue / packet 失效相关查询 |
| API | `src/book_agent/app/api/routes/actions.py` | 57 | `POST /actions/{id}/execute` |
| API | `src/book_agent/app/api/routes/documents.py` | 588 | summary / worklist / proposals / review 入队 |
| Schema | `src/book_agent/schemas/workflow.py` | 716 | Pydantic 响应模型 |
| 运行时 | `src/book_agent/app/runtime/document_run_executor.py` (686-760, 825-852, 1663-1673) | — | review / export 阶段如何调用门面 |
| 测试 | `tests/test_persistence_and_review.py`(11686)、`test_review_naturalness_acceptance.py`、`test_review_skip_visibility.py`、`test_rule_engine.py`、`test_workflow_golden.py` + `workflow_golden_scenario.py` + `tests/golden/workflow_scenario.json`、`test_run_lineage.py`、`test_export_gate_evaluation.py` | — | 见 §12 |

### 0.1 一句话数据流

```
翻译完成(packet TRANSLATED)
  └─ ReviewService.review_chapter(chapter_id)                      services/review.py:130
       ├─ ReviewRepository.load_chapter_bundle                       infra/repositories/review.py:34
       ├─ _build_review_artifacts → 15 类规则检查 → issues           services/review.py:258
       │     └─ 每个 issue: build_issue_action (rule_engine) + build_rerun_plan (orchestrator/rerun)
       ├─ resolve_missing_issues（上轮 OPEN/TRIAGED 但本轮未检出 → RESOLVED）  infra/repositories/review.py:178
       ├─ 章节状态: blocking>0 → REVIEW_REQUIRED（并拒绝相关 packet 的记忆提案）
       │            否则 → QA_CHECKED（并提交各 packet 最新成功 run 的记忆提案）
       └─ save_review_artifacts（merge issues / actions / chapter / quality summary）
自动/人工触发 action
  └─ IssueActionWorkflow.execute_action(action_id, run_followup)    application/issue_actions.py:37
       ├─ IssueActionExecutor.execute → 失效 packet/segment/edge/sentence + audit    services/actions.py:41
       └─ (run_followup) RerunService.execute(plan) → realign | reparse | rebuild+重译 → review_chapter  services/rerun.py:50
三个自动循环（均有界）：review 自动跟进 / 文档阻塞修复 / 导出 gate 跟进   application/review_repair.py, export_use_case.py
```

---

## 1. Review 流程（`services/review.py`）

### 1.1 入口 `review_chapter` 的步骤与副作用（review.py:130-156）

| 步骤 | 行 | 说明 |
|---|---|---|
| 1 | 131 | `load_chapter_bundle`：一次性拉章节下全部 Block(ACTIVE)、Sentence（**无排序**）、Packet、Run、TargetSegment、AlignmentEdge、TermEntry（GLOBAL 或该章 CHAPTER 作用域、ACTIVE）、chapter_brief / chapter_translation_memory 活动快照、existing_issues |
| 2 | 132 | `_build_review_artifacts` 生成 issues / actions / rerun_plans / summary |
| 3 | 133-137 | `resolve_missing_issues`：章节内所有 `OPEN`/`TRIAGED` 且 id 不在本轮集合的 issue → `RESOLVED`，`resolution_note="Resolved by latest QA pass."`。**注意这会把非 review 产生的 issue（导出 gate 的 `ALIGNMENT_FAILURE`/`LAYOUT_VALIDATION_FAILURE`）一并置为 RESOLVED**，而 review 本身并不做布局校验（见 §14 问题 6） |
| 4 | 138-140 | STRUCTURE 层最高 severity 若高于 `chapter.risk_level` 则抬高；**只抬不降** |
| 5 | 141-146 | `blocking_issue_count>0` → `ChapterStatus.REVIEW_REQUIRED` 且 `_reject_review_blocked_memory`；否则 `QA_CHECKED` 且 `_commit_review_approved_memory` |
| 6 | 147-148 | `resolved_issue_ids` 填充；`upsert_chapter_quality_summary` |
| 7 | 149-155 | `save_review_artifacts` 全部 `session.merge` + `flush`；**不 commit**，事务边界在调用方（API 的 `get_db_session`、run executor 的 `session_scope`、CLI 的 `session_scope`） |

**记忆副作用**（review.py:158-196）：
- 提交：对每个 packet 取 `RunStatus.SUCCEEDED` 且 attempt 最大的 run（162-166，`>=` 取后者），交给 `MemoryService.commit_review_approved_chapter_memory`（memory_service.py:220-255）逐提案合并成新快照并标记 COMMITTED。
- 拒绝：blocking issue 的 `packet_id` + evidence `packet_ids_seen` 中已知的 packet → `reject_pending_packet_proposals`（memory_service.py:105-122）。
- 也就是说 **review 结果直接决定章节记忆是否落库**，这是 review 与 memory 子系统最强的耦合点。

### 1.2 输入：审哪个翻译版本

- 对齐/文本检查使用的「活动译文」= 章节所有 `TargetSegment` 中 `final_status != SUPERSEDED` 的（review.py:1542-1546），对齐边只保留指向活动 segment 的（1548-1551）。一句话的「aligned text」= 其所有活动对齐 segment 的 `text_zh` 以空格拼接（706-716）。
- 「最新 run」= 每 packet `attempt` 最大的 run，**不看 `RunStatus`**（1553-1557）；而记忆提交只看 SUCCEEDED（162）。若最新 attempt 是 FAILED run（P3.4 后 worker 失败会写 FAILED run），`latest_segments_by_packet` 对该 packet 为空 → DUPLICATION / ALIGNMENT_FAILURE 检测对该 packet 静默跳过。待确认这种状态组合在实际流程中是否可达（`review_document` 会先跳过含非 TRANSLATED packet 的章节，review_repair.py:105-128）。
- 句子过滤：只审 `translatable` 且 `sentence_status != BLOCKED` 的句子（274-276）。
- 术语：`LockLevel.LOCKED` + `status==active` 的 TermEntry（350-352）。**PREFERRED/SUGGESTED 不参与 TERM_CONFLICT**。

### 1.3 检查项清单

所有检查项均为 `Detector.RULE`、`confidence=1.0`（review.py:1936-1937）。**Review 本身不调用任何 LLM**；唯一的模型调用在自动跟进阶段的概念解析器（§5.5）。

| # | issue_type | root_cause_layer | severity | blocking | 定位（sentence/packet） | 触发规则 | unique_key / id 构成 | 代码 |
|---|---|---|---|---|---|---|---|---|
| 1 | `ALIGNMENT_FAILURE` | ALIGNMENT | HIGH | 是 | 代表句 + packet | packet 最新 run 的 `output_json` 提供 `alignment_suggestions` 或 `source_sentence_ids`（可恢复元数据，1827-1874），且 (a) 有当前句无活动对齐边且该句在可恢复集合内，或 (b) 最新 segment 无活动边但元数据里有映射（orphan）。evidence 含 `requires_packet_rerun`：orphan segment 无源句映射且不是「短尾标签」（≤24 字符且以冒号/分号结尾，或 ≤12 字符无句末标点）时为 True（1876-1904） | `packet.id` | 1598-1686 |
| 2 | `OMISSION` | ALIGNMENT | HIGH | 是 | 句 + packet | 可翻译句无活动对齐边，且未被 #1 处理、未被「PDF 碎片抑制」吞掉（`pdf://` 来源、正文匹配 `^[a-z][a-z0-9_-]{1,24}[.?!:;]$`、是块首句、块文本以其开头且 `parse_confidence<=0.8`，198-213） | 无（id 含 sentence_id） | 278-297 |
| 3 | `LOW_CONFIDENCE` | TRANSLATION | MEDIUM | 否 | 句 + packet | `sentence_status == REVIEW_REQUIRED`（由翻译 worker 的 `low_confidence_flags` 写入，translation.py:619-624） | 无 | 298-315 |
| 4 | `FORMAT_POLLUTION` | TRANSLATION | MEDIUM | 否 | 句 + packet | 非 code/table 块；译文含 ``` 或 `<!DOCTYPE`/`<!--`，或译文中出现源文没有的 `<tag>` 形 token（718-730, 1817-1818） | 无 | 322-339 |
| 5 | `CONTEXT_FAILURE`(packet) | PACKET | HIGH | 是 | packet 首句 + packet | `packet_json.open_questions` 非空；例外：只有 `missing_chapter_title` 且章节是「纯图片无标题章」（844-852, 1730-1745） | `packet.id` | 809-842 |
| 6 | `CONTEXT_FAILURE`(chapter) | MEMORY | HIGH | 是 | 章首句，packet=None | `chapter_brief.content_json.open_questions` 非空；例外同上，另加「无可译句且无 packet」 | `chapter-brief:{version}` | 1688-1728 |
| 7 | `UNLOCKED_KEY_CONCEPT` | MEMORY | MEDIUM | 否 | sentence=None；packet 仅当 `packet_ids_seen` 恰 1 个 | 章节翻译记忆快照 `active_concepts` 中：无 `canonical_zh`、源词未被 LOCKED 术语覆盖、`mention_count`（回退 `times_seen`）≥2 | `source_term.casefold()` | 486-548 |
| 8 | `STALE_CHAPTER_BRIEF` | MEMORY | LOW | 否 | 首个可译句，packet=None | brief 有 summary、记忆快照 `recent_accepted_translations`≥3 条、存在 `times_seen`≥2 且无 canonical、未锁定、且 summary 文本中不含该词的概念 | 缺失概念排序拼接 | 605-683 |
| 9 | `STYLE_DRIFT` | PACKET | MEDIUM | 否 | 句 + packet | 对每条启发式包规则：源文匹配 `source_pattern` 且译文匹配 `target_pattern`。规则来自 `heuristics_pack_for_document(document)`（按 `document.metadata_json.translation_heuristics_pack` 选包，默认 `tech-book-default`，16 条）。evidence 含 `style_rule`、`preferred_hint`、`prompt_guidance`、`matched_target_excerpt` | `{sentence_id}:{pattern_id}` | 550-603 |
| 10 | `DUPLICATION` | PACKET | MEDIUM | 否 | 前一 segment 的首个源句 + packet | packet 最新 run 相邻两个活动 segment 归一化后相同（去空白与标点，≥6 字符）、对齐到不同源句集合、且源句签名不同；每 packet 最多 1 条（`break`） | `packet.id` | 1747-1796 |
| 11 | `TERM_CONFLICT` | MEMORY | HIGH | 是 | 句 + packet（`_find_packet_for_sentence` 线性扫描） | LOCKED 术语的源词在句中（`SourceTermIndex` 整词/最长匹配），译文不含期望译法及 `target_variants_json` 任一（`target_has_rendering`）。跳过条件（739-807）：源词旁有结构性修饰 token（含 `.-_+/` 或首字母大写且非停用词）且（章节标题是 reference/glossary/index 类，或译文含期望译法的变体：末尾 CJK 子串、去掉「大小超微强弱多单」前缀） | `expected_target_term.casefold()`（同期望译法的多源词合并成一条） | 350-404 |
| 12 | `MISORDERING` | STRUCTURE | 策略决定（HIGH/CRITICAL/MEDIUM/LOW） | 策略决定 | 首个可译句，packet=None | 有 `pdf_block_role` 块、章节 `pdf_layout_risk` ∈ {medium,high} 且策略 `emit_issue`。策略见 §1.4 | `layout-risk:{risk}` | 914-949 |
| 13 | `STRUCTURE_POLLUTION` | STRUCTURE | HIGH | 是 | 同上 | header/footer 角色块下存在可译句 | `header-footer-leak` | 951-981 |
| 14 | `FOOTNOTE_RECOVERY_REQUIRED` | STRUCTURE | MEDIUM | 否 | 同上 | footnote 块 `footnote_anchor_matched is False` | `footnote-orphaning` | 983-1012 |
| 15 | `ARTIFACT_GROUP_RECOVERY_REQUIRED` | STRUCTURE | LOW | 否 | 同上 | 仅 `recovery_lane=="academic_paper"`、有本地 high 布局风险页、且有带 caption 的 image/table/equation 块在这些页上没有 group context | `artifact-group-recovery` | 1014-1071, 1427-1463 |
| 16 | `IMAGE_CAPTION_RECOVERY_REQUIRED` | STRUCTURE | MEDIUM(学术)/LOW | 否 | 同上 | 有无 caption 链接的 image/figure 块；策略：学术 lane 一律 MEDIUM 发出；否则有部分链接或章节有 caption 块才发（LOW）；否则不发 | `image-caption-recovery` | 1073-1127, 1367-1425 |

补充：导出 gate 另产生两类 issue，不在 review 里，但共享同一表与同一 action 映射：
- `ALIGNMENT_FAILURE` / `RootCauseLayer.EXPORT`（`export/alignment.py:190-296`，id 由 `packet.id + "export"` 构成，`suggested_action=REALIGN_ONLY`）。
- `LAYOUT_VALIDATION_FAILURE` / `STRUCTURE`（`services/export.py:970-1027`，blocking，`suggested_action=REPARSE_CHAPTER`）。

### 1.4 PDF 布局风险策略 `_pdf_layout_review_policy`（review.py:1131-1241）

| 条件（按顺序） | severity | blocking | emit | reason |
|---|---|---|---|---|
| 默认 | medium→HIGH，high→CRITICAL | 是 | 是 | `default_blocking_layout_risk` |
| risk=high 且 PDF_SCAN、`parse_confidence>=0.82`、本章只有 1 页证据且唯一可疑页、原因 ⊆ {ocr_scanned_page}、无 header/footer/footnote 块、所有 image/table/equation 都有 caption 且有 group context（1243-1304） | LOW | 否 | 是 | `pdf_scan_single_page_locally_anchored_advisory` |
| risk≠medium | 默认 | | | |
| medium 且本地无可疑页也无风险页 | LOW | 否 | **否** | `no_local_layout_signals` |
| `outlined_book` lane 且 `parse_confidence>=0.82`、无 high 页、恰 1 个可疑页、≥4 页证据、原因 ⊆ {multi_column}、≥3 页有 heading/caption/cross_page_repaired（1306-1365） | LOW | 否 | 是 | `outlined_book_single_multi_column_advisory` |
| 非 academic_paper lane | 默认 | | | |
| academic 且 `parse_confidence<0.8` 或缺失 | 默认 | | | `academic_paper_medium_confidence_too_low` |
| academic 且可疑页「结构锚定」（≥3 页恢复标题、可疑页角色只有 caption/heading，1488-1539） | LOW | 否 | 否 | `academic_paper_medium_structurally_anchored` |
| academic 且有 high 页无可疑页 | LOW | 否 | 是 | `academic_paper_local_page_layout_advisory` |
| academic 且章节元数据可疑页 >2 | MEDIUM | 否 | 是 | `academic_paper_medium_wide_layout_advisory` |
| academic 其余 | MEDIUM | 否 | 是 | `academic_paper_medium_layout_advisory` |

这一整块（~450 行）是针对具体 PDF 语料反复打补丁形成的分支树，硬编码阈值 0.8 / 0.82 / 3 页 / 4 页 / 2 页均无配置入口。

### 1.5 `review_issues` 输出字段（domain/models/review.py:22-62）

| 字段 | 来源 | 备注 |
|---|---|---|
| `id` | `stable_id("review-issue", document_id, chapter_id, sentence_id or "no-sentence", issue_type[, unique_key])`（review.py:1922-1926） | 确定性 id，是幂等/去重的基础 |
| `document_id` / `chapter_id` / `sentence_id` / `packet_id` | 检查项 | `block_id` **始终为 None**：`_make_issue` 有 `block_id` 形参但无调用方传入（1919） |
| `issue_type` | 字符串，**无枚举** | 类型集合分散在 review.py / export.py / rule_engine.py 字符串字面量中 |
| `root_cause_layer` / `severity` / `blocking` | 检查项 | |
| `detector` | 固定 `RULE` | `MODEL`/`HUMAN` 枚举值全库未写入 |
| `confidence` | 固定 1.0 | `_dedupe_issues` 取 max，实际无意义 |
| `evidence_json` | 检查项自由结构 | 无 schema；下游（rule_engine、rerun、review_repair、analytics）用字符串 key 读取：`requires_packet_rerun`、`packet_ids_seen`、`source_term`、`expected_target_term`、`preferred_hint`、`style_rule`、`prompt_guidance`、`matched_target_excerpt`、`missing_concepts` |
| `status` | 新建固定 `OPEN` | |
| `suggested_action` | review 不写；只有导出 gate 写 | 与 `issue_actions` 表重复语义 |
| `resolution_note` | `resolve_missing_issues` / `mark_issue_triaged` 写 | 见 §1.7 的残留问题 |
| `created_at` / `updated_at` | `_make_issue` 显式设为 `now` | 见 §1.7 |

### 1.6 去重与合并（review.py:406, 440-484）

- `_dedupe_issues` 按 `issue.id` 合并：severity 取高、blocking 取或、confidence 取大、`updated_at` 取晚、evidence 用 `_merge_issue_evidence` 合并（已有非空 key 不覆盖；`source_terms` 求并集并排序；TERM_CONFLICT 额外把 `source_term` 规范为排序首项）。
- 因为 id 里含 `sentence_id`，实际会碰撞的只有：同一句上多条 LOCKED 术语拥有相同 `expected_target_term`（TERM_CONFLICT unique_key 取期望译法，433-438；测试 `test_review_collapses_term_conflict_variants_with_shared_expected_target`）。
- `ReviewRepository._merge_collection`（review.py:195-205）再按 id 去一次重，然后 `session.merge`。

### 1.7 跨 run 的 issue 生命周期

状态机（`IssueStatus`：OPEN / TRIAGED / RESOLVED / WONTFIX）实际使用情况：

| 转换 | 触发点 | 代码 |
|---|---|---|
| (新) → OPEN | review 检出 / 导出 gate 检出 | review.py:1939, export.py:1023, export/alignment.py:291 |
| OPEN/TRIAGED → RESOLVED | 下一轮 review 未检出（同章全部类型） | infra/repositories/review.py:178-193 |
| OPEN/TRIAGED → RESOLVED | 导出 gate 同类 issue 未再检出（只处理 export ALIGNMENT_FAILURE 与 LAYOUT_VALIDATION_FAILURE） | export.py:939-946 |
| OPEN → TRIAGED | action 执行时 `mark_issue_triaged`（note 固定 "Action executed; awaiting rerun validation."） | services/actions.py:46, ops.py:83-86 |
| → WONTFIX | **无任何写入路径**；只有读侧统计（issue_queries.py:53, analytics.py:1008） | — |
| RESOLVED → OPEN（重开） | 再次检出时 `session.merge` 同 id 的新对象 | review.py:1925-1942 + review repo:125 |

**探针结论（`.venv/bin/python scratchpad/merge_probe.py`）**：对一个已 RESOLVED、`resolution_note="Resolved by latest QA pass."`、`created_at=t0` 的 issue，merge 一个同 id、`status=OPEN`、`created_at=t1` 的新 `ReviewIssue` 后：
- `status` → open（重开成功）；
- `created_at` → **t1（被重置）**；
- `resolution_note` → **仍是 "Resolved by latest QA pass."（残留）**；
- 同 id 的 `IssueAction`（原 COMPLETED）→ **planned（被重置）**。

含义：
1. 一个持续存在的 issue，每跑一次 review，`created_at` 都会被刷成本轮时间 → worklist 的 `oldest_active_issue_at` / `age_hours` / SLA（issue_queries.py:270-298，analytics.py:644-669）**永远从最近一次 review 起算**，长期未修的 issue 不会「breached」；issue 活动时间线（analytics.py:972-1044）的 created 桶也随之漂移。
2. 重开的 issue 带着过期的 `resolution_note`，且 TRIAGED 状态被 OPEN 覆盖。
3. **`issue_actions.status` 不是持久状态**：每轮 review（review repo:128）和每次导出 gate 同步（export.py:952-955）都把 action 重新 merge 为 PLANNED。「执行过」这一事实只存在于 `audit_events`（`_failed_execution_count`，issue_actions.py:72-104）和调用内的 `attempted_action_ids` 集合里。这也是 `list_planned_issue_actions`（export repo:282-290）总能再次找到已执行 action 的原因。

没有「stale」状态；没有 issue 级别的 version / 出现次数计数；没有人工关闭途径。

### 1.8 章节质量摘要（review.py:415-424, 685-704；domain/models/review.py:65-88）

| 字段 | 算法 |
|---|---|
| `coverage_ok` | 无 `OMISSION` |
| `alignment_ok` | 无 `root_cause_layer==ALIGNMENT` 的 issue（含 OMISSION、ALIGNMENT_FAILURE；**不含**导出层 ALIGNMENT_FAILURE） |
| `term_ok` | 无 `TERM_CONFLICT` |
| `format_ok` | 无 `FORMAT_POLLUTION` |
| `blocking_issue_count` / `low_confidence_count` / `format_pollution_count` | 计数 |
| `issue_count` / `action_count` | 本轮数量（1 issue = 1 action） |
| `resolved_issue_count` | 本轮被 `resolve_missing_issues` 关闭的数量（不是累计） |
| `naturalness_summary` | 仅在内存 `ReviewArtifacts.summary` 里（225-256：STYLE_DRIFT 计数、影响 packet 数、top-3 规则与 hint、`advisory_only`）；**未持久化**，`ChapterQualitySummary` 表无对应列 |

持久化用 `chapter_id` 唯一约束做 upsert（review repo:140-176），每章只有最近一轮，无历史。

---

## 2. 规则引擎（`orchestrator/rule_engine.py`、`orchestrator/rerun.py`）

### 2.1 issue → action 完整映射（`resolve_action`，rule_engine.py:24-67；按代码顺序短路）

| 优先级 | 条件 | ActionType | 当前检测器能否触发 |
|---|---|---|---|
| 1 | `root_cause_layer == PARSE` | `REPARSE_DOCUMENT` | 否（无检测器产 PARSE 层） |
| 2 | `root_cause_layer == STRUCTURE` | `REPARSE_CHAPTER` | 是：MISORDERING、STRUCTURE_POLLUTION、FOOTNOTE_*、ARTIFACT_GROUP_*、IMAGE_CAPTION_*、LAYOUT_VALIDATION_FAILURE |
| 3 | `root_cause_layer == SEGMENT` | `RESEGMENT_CHAPTER` | 否 |
| 4 | `STYLE_DRIFT` & MEMORY | `REBUILD_CHAPTER_BRIEF` | 否（review 只产 PACKET 层 STYLE_DRIFT） |
| 5 | `STYLE_DRIFT` 其他 | `RERUN_PACKET` | 是 |
| 6 | `CONTEXT_FAILURE` & MEMORY | `REBUILD_CHAPTER_BRIEF` | 是 |
| 7 | `CONTEXT_FAILURE` & PACKET | `REBUILD_PACKET_THEN_RERUN` | 是 |
| 8 | `CONTEXT_FAILURE` 其他 | `REBUILD_CHAPTER_BRIEF` | 否 |
| 9 | `TERM_CONFLICT` & `involves_locked_term` | `UPDATE_TERMBASE_THEN_RERUN_TARGETED` | 是（`involves_locked_term` 恒等于 `issue_type=="TERM_CONFLICT"`，rule_engine.py:85） |
| 10 | `UNLOCKED_KEY_CONCEPT` | `UPDATE_TERMBASE_THEN_RERUN_TARGETED` | 是 |
| 11 | `STALE_CHAPTER_BRIEF` | `REBUILD_CHAPTER_BRIEF` | 是 |
| 12 | `ENTITY_CONFLICT` | `UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED` | 否（无检测器） |
| 13 | `DUPLICATION` & PACKET | `REBUILD_PACKET_THEN_RERUN` | 是 |
| 14 | `DUPLICATION` 其他 | `REEXPORT_ONLY` | 否 |
| 15 | `LOW_CONFIDENCE` / `FORMAT_POLLUTION` | `RERUN_PACKET` | 是 |
| 16 | `MISTRANSLATION_REFERENCE` | `REBUILD_PACKET_THEN_RERUN` | 否 |
| 17 | `ALIGNMENT_FAILURE` & `requires_packet_rerun` | `RERUN_PACKET` | 是 |
| 18 | `ALIGNMENT_FAILURE` & `translation_content_ok` | `REALIGN_ONLY` | 是（`translation_content_ok` 恒等于 `issue_type!="OMISSION"`，rule_engine.py:86，对 ALIGNMENT_FAILURE 恒为 True） |
| 19 | `ALIGNMENT_FAILURE` 其他 | `RERUN_PACKET` | 否（不可达） |
| 20 | `EXPORT_FAILURE` | `REEXPORT_ONLY` | 否 |
| 21 | `OMISSION` / `MISTRANSLATION_SEMANTIC` / `MISTRANSLATION_LOGIC` | `RERUN_PACKET` | OMISSION 是；另两个否 |
| 22 | 兜底 | `EDIT_TARGET_ONLY` | 否（所有现有类型都被前面命中） |

结论：README 说 12 个动作（`ActionType` 枚举确有 12 个，enums.py:213-225）。`resolve_action` 能产出 11 个（`MANUAL_FINALIZE` 从不产出）；**当前检测器实际可达 6 个**：REPARSE_CHAPTER、REBUILD_CHAPTER_BRIEF、REBUILD_PACKET_THEN_RERUN、UPDATE_TERMBASE_THEN_RERUN_TARGETED、RERUN_PACKET、REALIGN_ONLY。`IssueRoutingContext` 的 `involves_locked_term`、`translation_content_ok` 两个字段在 `routing_context_for_issue` 里是由 issue_type 派生的常量（rule_engine.py:81-88），属于「假的灵活性」。

### 2.2 作用域（`scope_for_action`，rule_engine.py:70-109）

| ActionType | scope | 备注 |
|---|---|---|
| RERUN_PACKET / REBUILD_PACKET_THEN_RERUN / REALIGN_ONLY | PACKET（若 issue 有 packet_id），否则落到 SENTENCE | |
| UPDATE_TERMBASE_THEN_RERUN_TARGETED | TERM_CONFLICT 有 packet_id → PACKET；UNLOCKED_KEY_CONCEPT 且 `packet_ids_seen` 恰 1 → PACKET；否则 CHAPTER | |
| RESEGMENT_CHAPTER / REPARSE_CHAPTER / UPDATE_ENTITY_REGISTRY_* / REBUILD_CHAPTER_BRIEF / REEXPORT_ONLY | CHAPTER | `REEXPORT_ONLY` 明确归为章节作用域 |
| REPARSE_DOCUMENT | DOCUMENT | |
| EDIT_TARGET_ONLY / MANUAL_FINALIZE | SENTENCE | |

`IssueAction.id = stable_id("issue-action", issue.id, action_type)`（112-116）；`created_at/updated_at` 复制 issue 的（128-129）。

### 2.3 「两套规则取并集」的含义

来自提交 `2e9a370 refactor(actions): one issue -> action mapping for review and export`（PLAN.md P3.2 第 105 行）。此前 review 与导出 gate 各自维护一份 routing context + scope 规则并已漂移：导出侧忽略了 locked-term 与 `requires_packet_rerun` 信号以及 TERM_CONFLICT 的 packet 作用域；review 侧把 REEXPORT_ONLY 作用域给了 sentence 且 `reason_json` 缺 `root_cause_layer`。「并集」= 把两边各自多出来的规则都保留进 `rule_engine.build_issue_action`，并统一 REEXPORT_ONLY 为 CHAPTER 作用域。测试 `tests/test_rule_engine.py:250-296` 固定了这些行为。

### 2.4 `RerunPlan` 投影（orchestrator/rerun.py）

- `build_rerun_plan`（109-140）：默认 `scope_ids=[action.scope_id]`；两个特殊投影：UNLOCKED_KEY_CONCEPT + UPDATE_TERMBASE 和 STALE_CHAPTER_BRIEF + REBUILD_CHAPTER_BRIEF 且 evidence 有 `packet_ids_seen` → 改为 PACKET 作用域、`scope_ids=packet_ids_seen`（即 action 表里写的是 CHAPTER，实际执行按 packet）。
- `concept_overrides_for_issue`（21-40）：仅 TERM_CONFLICT，从 `expected_target_term`/`preferred_target_term`/`preferred_hint` 取 canonical，经 `normalize_term_rendering`，生成 `status="locked"` 的 `ConceptCandidate`（测试显示 "智能体式AI" 被规范为 "智能体AI"，test_rule_engine.py:154）。
- `style_hints_for_issue`（43-67）：仅 STYLE_DRIFT，最多 3 条英文提示句。
- 这些投影在 `RerunService.execute` 里会与同 packet 其他未解决 issue 的投影**合并**（rerun.py:57-72）。

---

## 3. 修复执行

### 3.1 第一段：`IssueActionExecutor.execute`（services/actions.py:41-92）

| 步骤 | 行 | 行为 |
|---|---|---|
| 取 action/issue | 42-43 | 找不到抛 `ValueError`（API 转 404） |
| **无前置检查** | — | 不检查 `action.status`（COMPLETED 可重复执行）、不检查 issue 是否已 RESOLVED |
| 标记 | 45-46 | action RUNNING；issue TRIAGED |
| 按 plan 分支 | 52-86 | REALIGN_ONLY+PACKET：只写 audit `packet.marked_for_realign`；REPARSE_CHAPTER/DOCUMENT：拒绝相关 packet 的记忆提案 + audit；其他 PACKET 作用域：拒绝提案 + **失效 packet**（packet INVALIDATED、run/segment/edge 记 invalidation、segment SUPERSEDED、sentence PENDING，103-133）；CHAPTER 作用域：失效全章 packet，chapter→PACKET_BUILT；SENTENCE 作用域：只写 audit `sentence.marked_for_manual_review` |
| 完成 | 88-91 | action COMPLETED；`save_invalidations` + flush |

注意：失效是**破坏性**的（活动译文全部 SUPERSEDED、句子回 PENDING），而 `run_followup=False` 时不会重译，章节会停在「有 packet 被失效但无人重译」的状态；`translate_document` 的同步路径只跑 `PacketStatus.BUILT` 的 packet（workflows.py:248），INVALIDATED 的 packet 不会被再翻，除非 RerunService 的 `mark_packet_ready_for_rerun` 把它拉回 BUILT（ops.py:88-107）。

### 3.2 第二段：`RerunService.execute`（services/rerun.py:50-146）

| action_type | 执行 | 代码 |
|---|---|---|
| REALIGN_ONLY | `RealignService.execute(packet_ids)`：取 packet attempt 最大 run 的活动 segment，用 `output_json` 的 alignment_suggestions / source_sentence_ids 重建边，`replace_alignment_edges` 删旧建新（realign.py:40-94） | 85-87 |
| REPARSE_CHAPTER / REPARSE_DOCUMENT | `PdfStructureRefreshService.refresh_document`；未配置服务抛 ValueError；**非 PDF 文档抛 `ValueError("PDF structure refresh only supports PDF documents.")`**（pdf_structure_refresh.py:100-101）；之后 `session.expire_all()`；不重译 | 88-100 |
| 其余（RERUN_PACKET、REBUILD_*、UPDATE_TERMBASE_*、UPDATE_ENTITY_*、EDIT_TARGET_ONLY、REEXPORT_ONLY、MANUAL_FINALIZE…） | `TargetedRebuildService.apply`（只对 4 种 rebuild 类型生效，否则返回 None，rebuild.py:69-76）→ 对每个 packet：拒绝提案、`mark_packet_ready_for_rerun`（BUILT + chapter PACKET_BUILT + 事件）、`execute_packet(compile_options=concept_overrides, rerun_hints=style_hints, auto_commit_memory=False)` | 101-123 |
| 收尾 | 若 issue 有 chapter 且作用域 ∈ {PACKET, CHAPTER, DOCUMENT} → `review_chapter`；`issue_resolved = 刷新后 status==RESOLVED`（issue 不存在则视为 True，133-137） | 125-137 |

`TargetedRebuildService.apply`（rebuild.py:69-211）：需要 brief / termbase / entity 三个快照都存在，否则 `ValueError("Rebuild prerequisites are missing…")`；REBUILD_CHAPTER_BRIEF 重建 brief；UPDATE_TERMBASE 刷新 termbase 快照（`_refresh_termbase_snapshot`）；UPDATE_ENTITY_REGISTRY **只是返回当前快照**（271-273，注释「P0 still lacks a first-class entity registry editor」）；然后重建目标 packet 并把 chapter 置 PACKET_BUILT。

### 3.3 自动执行 vs 人工确认的边界

三个自动循环，全部由 `IssueActionWorkflow` 承接，全部使用同一个「人工保留」判定：

| 循环 | 入口 | 候选范围 | 上限 | 停止原因 | 审计 |
|---|---|---|---|---|---|
| review 自动跟进 | `ReviewRepairService._apply_review_auto_followups`（review_repair.py:415-510），`review_document(auto_execute_packet_followups=True)` | 仅 `STYLE_DRIFT` / `TERM_CONFLICT` / `UNLOCKED_KEY_CONCEPT`，且投影为 PACKET 作用域；blocking 的只允许带 `expected_target_term` 的 TERM_CONFLICT（614-619）；UNLOCKED 需 `packet_ids_seen` ≤3（621-628）；STALE_CHAPTER_BRIEF 只作为 UNLOCKED 自动锁定失败后的同 packet 集回退（519-522, 661-687） | `max_auto_followup_attempts`（executor 默认 2，可由 run budget 覆盖，document_run_executor.py:1663-1667）；**跨章节共享**同一计数 | 候选耗尽 / `manual_hold_required` / 达上限 | `review.auto_followup.executed` / `.stopped`（chapter 对象） |
| 文档阻塞修复 | `repair_document_blockers_until_exportable`（174-283） | 所有 `blocking` 且 OPEN/TRIAGED 的 issue 的 PLANNED action；排序：DOCUMENT>CHAPTER>PACKET>SENTENCE 作用域、action 类型优先级表（322-336）、issue 类型优先级（338-348）、章序；同一轮内 document 独占、chapter 独占、packet 不重叠（366-404） | `max_rounds`（executor 默认 10 与 followup 上限取大，1669-1673）、每轮 ≤64 个 action | `no_new_actions` / `manual_hold_required` / `max_rounds_reached` | `document.blocker_repair.executed` / `.stopped`（document 对象） |
| 导出 gate 跟进 | `DocumentExportUseCase._pass_export_gate`（export_use_case.py:152-263） | `ExportGateError.followup_actions`（该章 OPEN blocking issue 的 PLANNED action） | `max_auto_followup_attempts`（API 默认 3） | `no_followup_actions` / `no_new_actions` / `manual_hold_required` / `max_attempts_reached` | `export.auto_followup.executed` / `.stopped`（chapter 对象） |

**人工保留判定**（issue_actions.py:47-104）：在 `audit_events` 里数该 issue（document 或 chapter 对象）下 `payload.action_id == 该 action` 且 `issue_resolved is False` 的执行事件，≥2（`AUTO_FOLLOWUP_REPEAT_FAILURE_LIMIT`）即拒绝自动执行。三类执行事件共享计数（17-21）。

**「no black-box self-correction」验证**：
- 动作类型是有限枚举，映射是纯函数（rule_engine），每次执行都写 `issue_actions`、`artifact_invalidations`、`audit_events`，执行结果（`issue_resolved`、rerun packet/run id）写入 audit payload；导出 gate 失败时把 followup 遥测放进 `ExportGateError.to_http_detail()`（export.py:122-144）。这一点成立。
- **不会无限循环**：三个循环各有硬上限 + 调用内 `attempted_action_ids` + 跨调用的审计失败计数。但要注意：(a) `issue_resolved is None`（`followup_executed=False`，例如 SENTENCE 作用域）**永远不计入失败**，只靠 `attempted_action_ids` 和 action 重置为 PLANNED（§1.7）的组合，跨 run 会被再次尝试；(b) 人工保留只对「自动」路径生效，`POST /actions/{id}/execute` 与 CLI `execute-action` 没有任何门槛，可无限次重复失效同一 packet。
- 「自我修正」的模型侧输入只有 `rerun_hints`（style hints）和 `concept_overrides`（锁定概念）两种，全部可在 audit / rerun_plan 中追溯。

### 3.4 失败处理

| 场景 | 行为 |
|---|---|
| `RerunService` 遇到 REPARSE_* 且文档非 PDF | `ValueError` 上抛。导出 gate 循环捕获并 `continue`（export_use_case.py:232-235，**且不写任何 audit / execution 记录**，attempted_action_ids 已加入，所以本次调用不再尝试）；review 自动跟进和阻塞修复**不捕获** → 整个 review 阶段失败（run executor 会把 work item 标失败） |
| 重译 worker 抛异常 | `execute_packet` 记 FAILED run 后 `raise`（translation.py:300-304）→ 同上，阻塞修复循环整体中断，之前已执行的 action 及失效已 flush 但事务是否提交取决于调用方（run executor 在异常时 `session_scope` 会回滚——待确认 `session_scope` 的异常语义） |
| rebuild 前置快照缺失 | `ValueError`，同上 |
| action/issue 不存在 | `ValueError` → API 404 |

没有 action 级别的 `FAILED`/`CANCELLED` 状态写入（`ActionStatus.FAILED`/`CANCELLED` 全库无写入点）；执行中途异常时 action 停在 RUNNING（services/actions.py:45 已设 RUNNING，但异常发生在 flush 前，是否持久化取决于事务回滚）。

---

## 4. 工作单（worklist）与章节分配

### 4.1 数据模型

- `ChapterWorklistAssignment`（domain/models/ops.py:77-98）：`document_id`、`chapter_id`（**unique，一章最多一个 owner**）、`owner_name`、`assigned_by`、`note`、`assigned_at`。owner 是自由字符串，无用户模型。
- 历史来自 `audit_events`：`chapter.worklist.assignment.set` / `.cleared`（worklist.py:243-258, 282-297），`actor_type=HUMAN`，`actor_id=assigned_by/cleared_by`（未经认证，API 请求体自报）。

### 4.2 队列（`get_document_chapter_worklist`，worklist.py:59-139）

1. `load_document_bundle` **只为校验文档存在**（72）——这会加载全书 block/sentence/packet，是纯粹的性能浪费。
2. `issue_queries.chapter_breakdown` → `analytics.issue_chapter_heatmap` → `issue_chapter_queue`（结合活动时间线、oldest_active_issue、assignment、memory proposal 计数）。
3. 过滤（queue_priority / sla_status / owner_ready / needs_immediate_attention / assigned / assigned_owner_name）与分页**全部在内存**（88-104）。
4. 汇总计数、owner 工作量、highlights。

`IssueChapterQueueEntry` 关键派生字段（analytics.py:608-790）：

| 字段 | 算法 |
|---|---|
| 入队条件 | `open>0 or triaged>0 or active_blocking>0` |
| `heat_score` | `open*3 + triaged*2 + active_blocking*4`（568-572）；`heat_level`: 0 none / ≤3 low / ≤6 medium / ≤11 high / else critical |
| `queue_priority` | active_blocking>0 → immediate；dominant 是 `IMAGE_CAPTION_RECOVERY_REQUIRED` → high；`heat>=6 or open>=3` → high；否则 medium |
| `queue_driver` | active_blocking / pdf_image_caption_gap / open_pressure / triaged_backlog |
| `sla_target_hours` | immediate 4h / high 24h / medium 72h（硬编码） |
| `age_hours` | `now - min(created_at of OPEN/TRIAGED issues)`（issue_queries.py:286-293），受 §1.7 的 created_at 重置影响 |
| `age_bucket` | fresh (<50% SLA) / aging / overdue；`sla_status`: on_track / due_soon (≥75%) / breached |
| `owner_ready` | dominant issue type 与 layer 都非空（几乎恒真） |
| `regression_hint` / `flapping_hint` | 最新日桶 `net_issue_delta>0` → regressing；近 3 桶有正有负 → flapping |
| 排序 | active_blocking desc, heat desc, open desc, triaged desc, issue_count desc, ordinal |

### 4.3 详情（`get_document_chapter_worklist_detail`，worklist.py:141-210）

再次加载全书 bundle；返回 issue family breakdown、queue entry、持久化 quality summary、最近 10 条 issue（按 updated_at）、最近 10 条 action、最近 20 条分配历史、memory proposal surface、合并时间线（analytics.py:70-120，取 20 条）。**没有 issue 的 evidence / 句子文本 / 译文**，人工审校无法据此定位问题。

---

## 5. 分析（analytics）

### 5.1 issue 指标（issue_queries.py + analytics.py）

| 读模型 | 来源 | 粒度 |
|---|---|---|
| `issue_hotspots` | SQL group by (issue_type, root_cause_layer)（issue_queries.py:40-98） | 文档 |
| `issue_chapter_pressure` | SQL join chapter group by chapter（100-157；inner join，无 issue 的章不出现） | 章 |
| `issue_chapter_breakdown` | SQL group by (chapter, issue_type, layer)，含 `active_blocking`（165-248） | 章 × 类型 |
| `issue_chapter_heatmap` / `queue` | §4.2 | 章 |
| `issue_activity_timeline` / `activity_breakdown` | 内存：按 `created_at.date()` 计 created，`RESOLVED` 按 `updated_at.date()` 计 resolved，`WONTFIX` 同理；`estimated_open_issue_count` 是累计 net delta 的 max(…,0)（analytics.py:972-1044）。**日期用 `.date()`，未显式转 UTC**（PLAN.md:137 提到修过时区抖动，代码里我没看到 tz 归一，待确认） | 日 |

### 5.2 每章 quality summary
即 §1.8 的持久化记录，通过 `analytics.stored_quality_summary` 转成 `StoredChapterQualitySummary`（1047-1063）；`DocumentSummary.chapters[].quality_summary` 与 worklist detail 共用。

### 5.3 成本 / 用量时间线（analytics.py:189-340）

- 输入 `translation_runs`（`export_repository.list_document_translation_runs`）。
- `translation_usage_summary_from_runs`：run 数、succeeded 数、`token_in/out` 求和、`cost_usd` 求和（round 6）、latency 求和/均值、最近 run 时间。
- `breakdown`：按 `(model_name, model_config_json.worker, model_config_json.provider)` 分组。
- `timeline`：按 `created_at.date()` 日桶，倒序。
- `highlights`：cost / avg latency / run_count 三个 max。
- 导出记录快照：`Export.input_version_bundle_json` 里存了导出时刻的 usage summary/breakdown/timeline/highlights（`*_from_json` 系列反序列化，342-442）。
- **不包含**概念自动锁定（LLM 调用）的 token/cost：`ConceptAutoLockRecord` 有 `token_in/out/cost_usd`（chapter_concept_autolock.py:380-383），但 review_repair 只看 `locked_records` 是否命中（review_repair.py:659），用量丢弃。

---

## 6. 记忆提案审批流（`application/memory_proposals.py`）

| 操作 | 流程 | 约束 |
|---|---|---|
| 列表 | `MemoryService.list_chapter_proposals` + 最近决定审计映射（47-69） | 可按 status 过滤 |
| 批准 | `MemoryService.approve_proposal`（memory_service.py:137-162）：已 COMMITTED 直接返回快照（幂等）；非 PROPOSED 抛错；否则 `commit_approved_packet_memory`：退休同 packet 其他 pending 提案、**乐观并发**检查 `base_snapshot_version == latest.version` 否则抛 "Chapter memory drifted"、`supersede_and_create_next`、标记 COMMITTED；然后写审计 `chapter.memory_proposal.approved`（257-286） | `actor_name` 有值 → `ActorType.HUMAN`，否则 SYSTEM/`memory-proposal-api` |
| 拒绝 | `reject_proposal`：COMMITTED 不可拒；写审计 `.rejected` | |
| 自动 | review 通过时 `commit_review_approved_chapter_memory` 批量提交（**不做版本漂移检查**，逐个 merge 到当前快照，memory_service.py:220-255）；review 阻塞 / action 执行 / rerun 时 `reject_pending_packet_proposals` | 与人工审批并存，没有「已由 review 自动提交」的审计事件 |
| 汇总 | `proposal_surface`（章）与 `proposal_queue_map`（文档，SQL 聚合）供 worklist 使用 | |

API：`GET/POST /documents/{id}/chapters/{cid}/memory-proposals[/{pid}/approve|reject]`（documents.py:425-500），ValueError 按消息文本映射 404/409（87-92）。

---

## 7. `DocumentWorkflowService` 门面（services/workflows.py）

构造器（78-159）为一个 session 装配 4 个 repository、7 个 service、6 个 application 对象。公共方法及委托：

| 方法 | 委托 | 备注 |
|---|---|---|
| `bootstrap_document` (161) | `BootstrapOrchestrator` + `bootstrap_repository.save` + `documents.get_document_summary` | 仍在门面 |
| `bootstrap_epub` (166) | `bootstrap_document` | **纯别名 shim**，可删（需查调用方） |
| `refresh_pdf_structure` (169) / `refresh_epub_structure` (177) | 对应 refresh service | 薄委托 |
| `get_document_summary` (185) | `documents` | 薄委托 |
| `delete_document` (188) | 自身：检查 RUNNING/DRAINING 后 `session.delete` | 仍在门面 |
| `list_document_history` (211) | `documents` | 薄委托（7 个参数透传） |
| `translate_document` (232) | 自身：同步遍历 BUILT packet 调 `translation_service.execute_packet` | 仍在门面；`recorded_memory_proposal_count` 恒等于翻译 packet 数（253），并非真实提案数 |
| `review_document` (271) | `review_repair` | 薄委托 |
| `list_chapter_memory_proposals` / `approve_…` / `reject_…` (284-325) | `memory_proposals` | 薄委托 |
| `repair_document_blockers_until_exportable` (327) | `review_repair` | 薄委托 |
| `export_document` (340) | `exports` | 薄委托 |
| `execute_action` (355) | `issue_actions` | 薄委托 |
| `get_document_export_dashboard` / `get_document_export_detail` (358-376) | `documents` | 薄委托 |
| `get_document_chapter_worklist` / `_detail` / `assign_…` / `clear_…` (378-440) | `worklist` | 薄委托 |

可删的 shim 判断：除 `bootstrap_document`、`delete_document`、`translate_document` 有自身逻辑外，其余 17 个方法都是一行透传；调用方（routes/documents.py、routes/actions.py、cli.py、document_run_executor.py、大量测试）都通过门面调用，所以删除 shim 需要改这些调用点或让门面直接暴露 `service.documents` 等属性（已经是公共属性）。`bootstrap_epub` 是最明显的死别名。

---

## 8. `schemas/workflow.py` 与 `application/read_models.py` 的重复程度

两者是**一比一镜像**：每个 read model dataclass 都有同名 `*Response` Pydantic 模型，字段名与类型逐一相同，路由用 `model_validate(..., from_attributes=True)` 转换，golden 测试 `test_read_models_validate_against_api_response_models` 保证可转换。

| read_models.py | schemas/workflow.py | 差异 |
|---|---|---|
| `StoredChapterQualitySummary` (31) | `StoredChapterQualitySummaryResponse` (12) | 无 |
| `NaturalnessSummarySnapshot` (45) | `NaturalnessSummaryResponse` (25) | 无 |
| `ChapterSummary` / `DocumentSummary` (13/54) | (33/50) | schema 给了默认值 |
| `DocumentHistoryEntry` / `Page` (81/106) | (76/100) | 无 |
| `DocumentTranslationResult` (116) | `TranslateDocumentResponse` (117) | 无 |
| `ChapterMemoryProposal*` (127-176) | (127-177) | schema 用 `Literal` 收窄 decision/status |
| `ChapterReviewResult` / `DocumentReviewResult` (180/214) | `ChapterReviewResultResponse` / `ReviewDocumentResponse` (180/196) | **`ReviewDocumentResponse` 缺 `skipped_chapters`、`total_chapter_count`、`auto_followup_*` 字段**；不过 `/documents/{id}/review` 现在返回 `DocumentRunSummaryResponse`（入队 run），该 schema 已无路由使用（待确认前端） |
| `ChapterReviewSkip`、`DocumentBlockerRepairResult/Execution`、`ReviewAutoFollowupExecution`、`ActionWorkflowResult` | **无对应 schema** | 只经 run executor payload（dict）或 CLI `asdict` 输出 |
| `ChapterExportResult` / `DocumentExportResult` / `ExportAutoFollowupExecution` (278-299, 740) | (217-249) | 无 |
| `TranslationUsage*` (320-363) | (267-307) | 无 |
| `IssueHotspotEntry` … `IssueActivityHighlights` (367-507) | (310-415, 580-604) | 无 |
| `ExportIssueStatusSummary` / `ExportVersionEvidenceSummary` / `ExportRecordSummary` / `DocumentExportDashboard` / `ExportDetail` (511-575, 717) | (607-690) | 无 |
| `DocumentChapterWorklist` / `ChapterWorklistAssignmentSummary` / `ChapterOwnerWorkloadSummary` / `ChapterWorklistIssue` / `Action` / `AssignmentHistoryEntry` / `TimelineEntry` / `DocumentChapterWorklistDetail` (579-713) | (426-577) | schema 多了 `ChapterWorklistAssignmentRequest` / `ClearRequest` / `ClearResponse` |
| — | `ExecuteActionResponse` / `RebuiltSnapshotEvidenceResponse` (693-716) | 由路由手工从 `ActionWorkflowResult` 拼装（actions.py:28-57） |
| — | `BootstrapDocumentRequest`、`TranslateDocumentRequest`、`ExportDocumentRequest`、`DocumentHistoryBackfillResponse` | 请求体 |

约 40 个类型、~600 行完全重复；差异只在默认值和 `Literal`。PLAN.md:141 提到前端类型已由 OpenAPI 生成，所以 schema 层是对外契约、read_models 是内部结构，但维护成本翻倍。

---

## 9. 入口点

| 入口 | 行为 |
|---|---|
| `POST /documents/{id}/review`（documents.py:548-564） | 只入队 `REVIEW_FULL` run（`review_repairs_blockers=False`，run_plan.py:66），executor 调 `review_document(document_id)`（无自动跟进，document_run_executor.py:703-716） |
| `TRANSLATE_FULL` run 的 review 阶段（executor 717-760） | `review_document(auto_execute_packet_followups=True, max=budget or 2)` → `repair_document_blockers_until_exportable(max_rounds=max(10, followup))` → 若修过再 `review_document(False)`；payload 含 skipped_chapters 与 remaining_blocking_issue_count |
| `EXPORT_FULL` run（825-852） | `export_document(auto_execute_followup_on_gate=plan.auto_followup_on_export_gate)`；`ExportGateError` 时先 commit（保留 issue/audit）再抛 |
| `POST /actions/{id}/execute?run_followup=`（actions.py:11-57） | 同步执行，返回 `ExecuteActionResponse`；无鉴权、无状态前置检查 |
| CLI `review` / `execute-action` / `export --auto-followup-on-gate`（cli.py:128-215） | 同步 |
| `GET /documents/{id}`、`/exports`（dashboard）、`/chapters/worklist`、`/chapters/{cid}/worklist`、`PUT …/worklist/assignment`、`POST …/assignment/clear`、memory-proposals 三个 | 读模型 / 分配 / 审批 |
| **没有的端点** | 列 issue（按章/类型/状态/分页）、看单个 issue 的 evidence、改 issue 状态（triage/wontfix/resolve）、列 action、取消 action、人工新建 issue |

---

## 10. 配置项与常量

| 项 | 位置 | 值 / 来源 |
|---|---|---|
| 自动跟进上限 | `DocumentRunExecutor(default_max_auto_followup_attempts=2)`，run budget `max_auto_followup_attempts` 可覆盖（executor 1663-1667；`RunBudget` ops.py:261） | 2 |
| 阻塞修复轮数 | `default_max_blocker_repair_rounds=10`，与上限取大（1669-1673） | 10 |
| 每轮 action 数 | `repair_document_blockers_until_exportable(max_actions_per_round=64)` | 64，无外部入口 |
| 导出 gate 跟进上限 | `ExportDocumentRequest.max_auto_followup_attempts` 默认 3（schemas 214） | 3 |
| 人工保留阈值 | `AUTO_FOLLOWUP_REPEAT_FAILURE_LIMIT = 2`（issue_actions.py:16） | 常量 |
| UNLOCKED / STALE 自动跟进 packet 上限 | `MAX_SAFE_*_PACKET_FOLLOWUP = 3`（review_repair.py:49-50） | 常量 |
| style drift 规则包 | `document.metadata_json.translation_heuristics_pack`，默认 `tech-book-default`（heuristics/__init__.py:20, 95-98） | 文档级 |
| 概念解析 LLM | `build_default_concept_resolver(translation_worker=...)`：用翻译 worker 的 `OpenAICompatibleTranslationClient`，否则 settings 的 `translation_backend/openai_api_key`，否则纯启发式（chapter_concept_autolock.py:324-366） | 无独立开关 |
| SLA 小时、heat 权重、severity/blocking 阈值、PDF 策略阈值 | analytics.py:644-649, 568-572；review.py 各处 | 全部硬编码 |
| **关闭某个检查项** | 不存在 | — |

---

## 11. 耦合点与依赖方向

- `services/review.py` 直接 import `orchestrator.rule_engine` / `orchestrator.rerun`（services → orchestrator），同时 `orchestrator/rerun.py` import `services.term_normalization`（orchestrator → services）：**循环依赖方向**。
- `application/analytics.py:42` import `services.review.NaturalnessSummary`（应用层依赖服务层内部 dataclass，只为做一次字段拷贝）。
- `services/rerun.py` 依赖 `ReviewService`、`TranslationService`、`TargetedRebuildService`、`RealignService`、`PdfStructureRefreshService`——修复执行是一个「胖服务」。
- `ReviewService` 构造时若未传 `memory_service` 会自建 `MemoryService(ChapterTranslationMemoryRepository(repository.session), ChapterContextCompiler())`（review.py:123-128）；`DocumentWorkflowService` 里 `ReviewService(self.review_repository)` **没有传** `self.memory_service`（workflows.py:97），于是 review 用自己的 MemoryService 实例（同 session，功能等价但两份对象）。
- `review_repair.py:37-40, 651-654` 直接构造 `ChapterConceptAutoLockService` 与解析器，绕过门面装配。
- `ops.py:95-96` 在方法体内延迟 import `event_kinds` / `emit_event`（回避循环 import 的迹象）。
- `evidence_json` 字符串 key 契约横跨 review / rule_engine / rerun / review_repair / analytics / export 六个模块。

---

## 12. 测试覆盖

| 测试文件 | 覆盖 | 未覆盖 |
|---|---|---|
| `test_rule_engine.py`（300 行） | `resolve_action` 12 个分支、`build_rerun_plan` 三种投影、`build_issue_action` 作用域与 id 稳定性 | 未断言「当前检测器不可达分支」；未测 `scope_for_action` 的 SENTENCE 回退 |
| `test_persistence_and_review.py`（11686 行，157 个用例，其中 review/action 相关 ≈50 个：8917-11680） | TERM_CONFLICT 及其各种跳过启发式（9364-9575）、UNLOCKED_KEY_CONCEPT（8941-9088）、STYLE_DRIFT 六个基准家族（9089-9310）、STALE_CHAPTER_BRIEF（9834-9921）、概念自动锁定与解析器优先级（9922-10187）、PDF 碎片省略抑制、纯图章跳过、layout advisory（9578-9774）、CONTEXT_FAILURE、DUPLICATION、ALIGNMENT_FAILURE/orphan/realign（11448-11680）、自动跟进候选重算、优先级、三个循环的人工保留（10371-10807）、导出 gate 跟进 | `resolve_missing_issues` 跨轮重开语义（created_at 重置）、action 状态被重置、`execute_action` 重复执行、REPARSE 在 EPUB 上的异常传播、`IMAGE_CAPTION`/`FOOTNOTE`/`ARTIFACT_GROUP` 检测（grep 未见专门用例，待确认在 test_pdf_support 中）、FORMAT_POLLUTION、LOW_CONFIDENCE、review 记忆提交/拒绝副作用 |
| `test_review_naturalness_acceptance.py` | 5 个 literalism 基准 + 引导重译清零 + TERM 优先于 STYLE | — |
| `test_review_skip_visibility.py` | 未翻译完的章被显式标记 skipped | — |
| `test_workflow_golden.py` + `workflow_golden_scenario.py` + `tests/golden/workflow_scenario.json` | 全链路：bootstrap → 翻译 → 提案批准/拒绝 → review → review package → 删对齐边触发导出 gate（`ALIGNMENT_FAILURE`/EXPORT）→ worklist/assignment → `execute_action(REALIGN_ONLY, run_followup=True)` → 双语/合并导出 → 全部读模型快照。快照证实：action 执行后 `review_artifacts.resolved_issue_ids=[<id20>]`，dashboard 的 hotspot/timeline/heatmap 结构，`issue_chapter_queue=[]`（issue 已解决） | 快照把 `age/latency` 置 `<volatile>`，日期 `<date>`，所以 SLA 与时间线的时序语义没有真正被 golden 锁定 |
| `test_export_gate_evaluation.py` | `evaluate_chapter_gate` 只读、`sync_gate_issues` 写 issue+action、`_raise_for_gate` | — |
| `test_run_lineage.py` | 与本子系统无关（run retry 链） | — |

---

## 13. 缺失的生产要素

1. **审校可配置性**：没有按文档/项目关闭或降级某检查项的开关；severity/blocking 硬编码；PDF 策略阈值硬编码；style drift 规则只能整包切换。
2. **审校成本控制**：review 本身零 LLM 成本，但自动跟进会触发 (a) 概念解析 LLM 调用（每个 UNLOCKED 概念一次，用量丢弃）、(b) packet 重译（真实成本）。除 `max_auto_followup_attempts` 外无 token/费用预算；`RunBudget` 只被读了 `max_auto_followup_attempts`。
3. **人工审校 UI 契约**：无 issue 列表/详情/分页/过滤端点；worklist detail 只有 10 条 issue 摘要，无 evidence、无源文/译文；无 triage/wontfix/手动 resolve；无人工新建 issue（`Detector.HUMAN` 未用）；无 action 取消；owner 是自由文本无鉴权。
4. **审计**：有 `audit_events`（action 执行、自动跟进、分配、提案决定），但 (a) review 自动提交记忆无事件；(b) 导出 gate 跳过的不适用 action 无事件；(c) `POST /actions/{id}/execute` 的 actor 固定为 `issue-action-executor`/SYSTEM，无法区分人工触发；(d) issue 的 RESOLVED 转换没有审计，只有 `resolution_note` 文本。
5. **幂等/并发**：`review_chapter` 无锁；两个进程同时 review 同一章会互相 merge 覆盖；`execute_action` 可重复执行破坏性失效。
6. **分页**：worklist 内存分页（`limit<=200`），issue 聚合每次全表扫 `review_issues`（activity_timeline / breakdown 把文档下全部 issue 拉进内存，issue_queries.py:159-163, 314-319）；索引只有 chapter_id / status / issue_type（alembic 0001:338-340），`document_id` 上**无索引**。
7. **历史**：`chapter_quality_summaries` 只有最新一轮；issue 无版本；无法回答「第 N 轮 review 发现了什么」。

---

## 14. 发现的 bug / 不一致 / 风险 / 技术债（带文件:行号）

### 14.1 正确性 / 语义 bug

1. （**H2 已修复**，§14.1-1…5 见 `production/gaps-and-risks.md` F 节）**issue 重开时 `created_at` 被重置、`resolution_note` 残留、TRIAGED 被覆盖**——`services/review.py:1925-1942` 每轮用 `now` 构造 issue，`infra/repositories/review.py:125` 直接 `merge`。已用探针验证。后果：SLA/age 永不累积、时间线失真、重开 issue 带「已解决」备注。
2. **`issue_actions.status` 每轮 review / 每次导出 gate 被重置为 PLANNED**——`orchestrator/rule_engine.py:121` + `infra/repositories/review.py:128` + `services/export.py:952-955`。已验证。`ActionStatus` 表面上有 5 个状态，实际 COMPLETED 只在两次 review 之间可见；`ChapterWorklistAction.status` 展示给用户的值不可信。
3. **`IssueActionExecutor.execute` 无任何前置状态检查**——`services/actions.py:41-46`：COMPLETED/RUNNING 的 action、RESOLVED 的 issue 都能再次执行，且 PACKET/CHAPTER 作用域会再次 SUPERSEDE 全部活动译文（103-133）。公开端点 `routes/actions.py:11-25` 直接暴露。
4. **导出 gate 的 `has_open_blocking_issues` 只看 `OPEN`**（`infra/repositories/export.py:263-280`），而阻塞修复循环把 TRIAGED 也视为活跃（`application/review_repair.py:285-296`）。一个 blocking issue 被 `execute_action(run_followup=False)` 置为 TRIAGED 后，若章节状态恰好仍是 QA_CHECKED/APPROVED（例如 SENTENCE 作用域 action 不改章节状态），`_raise_for_gate` 的最后一道检查（export.py:745）会放行。当前 review 产生的 blocking issue 都会把章节置 REVIEW_REQUIRED，所以实际触发需要状态被别处改回，风险为「防线不一致」，待构造复现。
5. **`resolve_missing_issues` 一刀切解决同章所有 OPEN/TRIAGED issue**（`infra/repositories/review.py:178-193`），包括导出层 `ALIGNMENT_FAILURE`、`LAYOUT_VALIDATION_FAILURE`。review 不做布局校验，却会把布局 issue 标为 "Resolved by latest QA pass."；下次导出会再次创建（同 id → 又是重开 + created_at 重置）。
6. **REPARSE_* action 在 EPUB 文档上必然抛 `ValueError`**——`services/rerun.py:88-98` → `services/pdf_structure_refresh.py:100-101`。导出 gate 循环吞掉（`application/export_use_case.py:232-235`，无审计），但 review 自动跟进与阻塞修复不吞（review_repair.py:238, 473）；而 STRUCTURE 层 issue（如导出 `LAYOUT_VALIDATION_FAILURE`）在 EPUB 上是可以产生的（export.py:684 只对 PDF 跑 layout 校验——所以 EPUB 上实际只有 review 的 PDF 检查项才会给 STRUCTURE 层，而它们需要 `pdf_block_role`；因此 EPUB 触发概率低，但阻塞修复优先级表把 REPARSE 排第一（review_repair.py:324-325），一旦出现会让整个 review 阶段失败）。
7. **`_build_alignment_state` 取最新 attempt 不看 RunStatus**（`services/review.py:1553-1557`），而记忆提交只取 SUCCEEDED（162）。最新 run 为 FAILED 时 DUPLICATION/ALIGNMENT_FAILURE 对该 packet 静默失效；待确认可达性（见 §1.2）。
8. **`UNLOCKED_KEY_CONCEPT` 与 `STALE_CHAPTER_BRIEF` 的计数阈值不一致**：前者 `mention_count`（回退 `times_seen`）≥2（review.py:517-519），后者 `times_seen`≥2（644）。同一概念可能一边报一边不报。
9. **`translate_document.recorded_memory_proposal_count` 恒等于翻译 packet 数**（`services/workflows.py:253`），并非实际记录的提案数。
10. **`RerunService.execute` 把「issue 不存在」当作已解决**（`services/rerun.py:133-137`）。
11. **概念自动锁定的 LLM 用量被丢弃**：`application/review_repair.py:647-659` 只用 `locked_records` 判真假，`ConceptAutoLockRecord.token_in/out/cost_usd`（chapter_concept_autolock.py:380-383）未进任何 usage 统计。
12. `sentence.source_span_json.get(...)` 无 None 保护（`services/review.py:336, 721`），与 201 行的 `(… or {})` 风格不一致；若列可空会 AttributeError（待确认 Sentence 模型是否 `nullable=False`）。
13. `analytics.build_issue_activity_timeline` 与 `translation_usage_timeline_from_runs` 用 `created_at.date()` 分桶（analytics.py:271, 979），未显式 `astimezone(UTC)`；SQLite 回读的 naive datetime 与 Postgres 的 aware datetime 会给出不同日期（PLAN.md:137 说已修「按 UTC 日分桶」，但代码里未见转换，待确认）。

### 14.2 死代码 / 无用抽象

14. `services/review.py:263` `handled_missing_alignment_ids: set[str] = set()` 立即被 264 行覆盖。
15. `ChapterReviewBundle.existing_issues`（`infra/repositories/review.py:27, 99-101`）被加载但 review.py 从未使用（每次多一次全章 issue 查询）。
16. `_make_issue(block_id=...)` 无调用方传入（review.py:1919）；`review_issues.block_id` 恒 None。
17. `IssueRoutingContext.involves_locked_term` / `translation_content_ok`（rule_engine.py:19-20, 85-86）是 issue_type 的函数，分支 19（rule_engine.py:62）不可达。
18. `resolve_action` 中 `ENTITY_CONFLICT`、`MISTRANSLATION_*`、`EXPORT_FAILURE`、PARSE/SEGMENT 层、`EDIT_TARGET_ONLY` 兜底（rule_engine.py:25, 29, 47, 55, 63, 65, 67）无任何生产者；`ActionType.MANUAL_FINALIZE`、`ActionStatus.FAILED/CANCELLED`、`IssueStatus.WONTFIX`、`Detector.MODEL/HUMAN` 全库无写入。
19. `TargetedRebuildService._refresh_entity_snapshot` 是空操作（rebuild.py:271-273），`UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED` 实际等价于「重建 packet + 重译」。
20. `services/style_drift.py` 只是 `DEFAULT_HEURISTICS` 的转发（20 行），且只用默认包，不随文档的 heuristics pack 变化（与 review.py:561 按文档选包不一致）。
21. `services/glossary_enforcement.py` 与 review 无关：只在 `services/translation.py:499-523` 发 `GLOSSARY_VIOLATION` 事件，不产生 issue；README 层面的「术语一致性审校」实际由 review 的 TERM_CONFLICT 承担，两套逻辑（`_count_occurrences` vs `SourceTermIndex/target_has_rendering`）匹配语义不同。（**H2 已修复**：统一到 `domain/terminology/enforcement.py`，见 `agent-upgrade/03-roadmap.md` H2。）
22. `DocumentWorkflowService.bootstrap_epub`（workflows.py:166）纯别名。
23. `schemas/workflow.py:196 ReviewDocumentResponse` 已无路由使用且字段落后于 `DocumentReviewResult`（待确认前端）。
24. `analytics.issue_chapter_queue` 用 lambda-IIFE 构造 entry（analytics.py:705-790），可读性差且 `chapter_activity.get(entry.chapter_id, [None])[0]` 重复四次。

### 14.3 性能

25. `_find_packet_for_sentence`（review.py:732-737）对每个 TERM_CONFLICT 线性扫描全部 packet 的 `packet_json`，而 `sentence_to_packet` 映射已在 261 行算好却未传入。
26. `worklist.get_document_chapter_worklist`（worklist.py:72）与 `clear_document_chapter_worklist_owner`（269）加载全书 bundle 只为校验存在性；`get_document_chapter_worklist_detail`（146）同样全量加载只用 chapter 与 packet 列表。
27. `IssueActionWorkflow._failed_execution_count`（issue_actions.py:91-104）对每个候选 action 各查一次全部相关 audit（无 action_id 过滤，payload 在 Python 侧过滤）；`audit_events` 只有 `(object_type, object_id)` 索引。
28. `issue_queries.activity_timeline / chapter_activity_map / activity_breakdown` 三个方法各自把文档所有 issue 拉进内存（159-163, 254-259, 315-317），dashboard 一次请求触发三次。
29. `review_issues` 无 `document_id` 索引，而 hotspots/pressure/blocker 循环都按 `document_id` 过滤（alembic 0001:338-340）。
30. `ReviewRepository.load_chapter_bundle` 的 Sentence 查询无 `order_by`（review repo:42-44），`representative_sentence_id = bundle.sentences[0]`（review.py:659-662, 872-875, 1700）依赖数据库返回顺序，issue id 可能因此在不同 DB 后端上漂移（待确认 Sentence 表是否有稳定物理序）。

### 14.4 设计债

31. `issue_type` 无枚举，evidence 无 schema（§1.5）。
32. `RerunPlan` 与 `IssueAction` 的作用域可能不一致（§2.4 的两个投影），`issue_actions.scope_type/scope_id` 展示给用户的是 CHAPTER，实际执行是 PACKET。
33. 每章 review 的事务边界由调用方决定，`review_document` 在一个事务里跑全部章节 + 自动跟进 + 重译（LLM 调用期间持有 DB 事务；`execute_packet` 文档字符串本身也警告 run executor 不应这样做，translation.py:290-292）。
34. 章节 `risk_level` 只升不降（review.py:138-140）。
35. `naturalness_summary` 不持久化（§1.8），worklist/summary 看不到 style drift 状态。
36. `ReviewService` 在门面里未复用 `self.memory_service`（workflows.py:97 vs 96）。
37. `application/analytics.py:42` 依赖 `services.review` 内部类型；`orchestrator/rerun.py:7` 依赖 `services.term_normalization`（层次倒置）。
38. `_pdf_structure_issues` 及其策略函数（review.py:854-1539，~690 行）是围绕具体语料的分支树，缺少表驱动或策略对象，任何新 lane 都要再加分支。

---

## 附：`ReviewArtifacts` 与各循环返回的读模型

- `ReviewArtifacts(issues, actions, rerun_plans, summary, resolved_issue_ids)`（review.py:104-110）——`rerun_plans` 只在内存中，未持久化；`RerunService` 与 `IssueActionExecutor` 都重新 `build_rerun_plan`。
- `DocumentReviewResult`（read_models.py:214-233）含 `skipped_chapters`（reason `translate_incomplete|translate_failed`）与自动跟进遥测。
- `DocumentBlockerRepairResult`（250-260）含 `stop_reason`。
- `ActionWorkflowResult`（766-768）= `ActionExecutionArtifacts` + `RerunExecutionArtifacts`。

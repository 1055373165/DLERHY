# 05 · 设计：Reviewer Agent 与 Repair Agent（H2）

> 状态：H2 实施稿（2026-09-17）。Reviewer Agent 已实现（`harness/agents/reviewer.py`，测试 `tests/test_reviewer_agent.py`）；Repair Agent 未实现。前置已完成：输出 guardrail（同 turn 修复）、issue 账本（`review_issue_events`、人工决定优先、action 不再每轮重置）、issue API。本篇固定两个 agent 与现有规则审校、修复循环的接口，避免「模型 issue」与「规则 issue」各走一套。

## 1. 原则

1. **一个账本**：模型发现的问题与规则发现的问题都是 `review_issues` 行，都经 `ReviewRepository.sync_issues` 写入；区别只在 `detector=model`、`confidence` 与 id 里的 `model` 后缀。
2. **复用路由**：模型 issue 使用规则引擎已有的类型与动作，不新增动作种类：

| 模型判断 | issue_type | root_cause_layer | 路由（`rule_engine.resolve_action`） | 重译时的提示来源 |
|---|---|---|---|---|
| 意思译错 | `MISTRANSLATION_SEMANTIC` | translation | RERUN_PACKET | evidence.explanation / suggested_target_text |
| 逻辑、指代关系错 | `MISTRANSLATION_LOGIC` | translation | RERUN_PACKET | 同上 |
| 指代对象依赖上下文、上下文不足 | `MISTRANSLATION_REFERENCE` | packet | REBUILD_PACKET_THEN_RERUN | 同上 |
| 部分内容漏译 | `OMISSION` | translation | RERUN_PACKET | evidence.explanation |
| 直译腔、不自然 | `STYLE_DRIFT` | translation | RERUN_PACKET | evidence.preferred_hint / prompt_guidance（已有） |
| 术语与 BOOK.md 不符 | `TERM_CONFLICT` | memory | UPDATE_TERMBASE_THEN_RERUN_TARGETED | evidence.source_term / expected_target_term（已有） |

3. **所有权**：规则审校只自动解决自己能重新判断的 issue；模型 issue 由「重新翻译」或「下一轮模型审校」关闭（见 §4）；导出层 issue 由导出 gate 关闭。
4. **成本受控**：默认抽样；每 turn 预算；模型 issue 默认不阻断，只有高置信度的严重问题阻断。

## 2. Reviewer Agent

- **阶段**：`model_review`，位于 `translate` 与 `review` 之间（`translate_full` 计划内）。模式 `sampled`（默认）/ `full` / `skip`，由 run 请求的 `model_review` 字段决定。
- **turn 形态**：一个文档级 turn。与目标架构里「按章 turn」的差异：现有执行器一个 agent 阶段一个 turn；文档级 turn 用队列工具逐章推进，上下文由内核压缩控制。按章并行留给 H3。
- **输入**：BOOK.md（系统消息）、规则 issue 摘要（避免重复报告）、审校队列。
- **抽样**：`sampled` 每章取不超过 N 个 packet（默认 3，优先：有 LOW_CONFIDENCE 标记、有 guardrail 修复记录、长度比偏离中位数最多的），`full` 取全部。
- **工具**：

| 工具 | 权限 | 作用 |
|---|---|---|
| `next_review_batch` | R | 返回下一个待审 packet 的源句/译文对（带句子别名），队列空时返回 done |
| `search_book`、`read_block`、`get_glossary` | R | 复用 H1 |
| `report_issue` | W-rev | 写一条模型 issue；同句已有同族规则 issue 时返回「已被规则覆盖」而不写入 |
| `mark_batch_clean` | W-rev | 记录该 packet 已审无问题（用于统计与下一轮跳过） |

- **阻断规则**：`severity ∈ {high, critical}` 且 `confidence ≥ 0.85` 才 `blocking=true`；其余为建议性 issue，出现在 issue 工作台，不阻断导出。

## 3. Repair Agent

- **取代**：review 阶段里的「自动跟进」与「阻断修复循环」中选择动作的那一步。循环本身（轮数上限、人工保留阈值、失败计数）保留在代码里，是硬护栏。
- **输入**：一章的活动 issue 束 + 规则引擎给出的候选动作（`build_issue_action` 的结果）。
- **工具**：`execute_action(action_id)`（W-rev，调用现有 `IssueActionWorkflow`，带 run_followup）、`edit_segment(sentence_id, new_text, reason)`（W-rev，新 attempt + supersede，只允许小改，改动字数上限）、`lock_term`（W-irr，审批）、`mark_wontfix(issue_id, note)`（W-irr，审批；阈值内的建议性模型 issue 自动批准）。
- **不变量**：Repair Agent 不能执行 `ActionNotExecutable` 的动作（账本已保证）；每个 issue 的执行次数上限沿用 `max_auto_followup_attempts`。

## 4. 模型 issue 的关闭

- packet 被重新翻译并持久化后（`persist_packet_result`），该 packet 句子上的**非人工决定的**模型 issue 进入 `resolved`，note 为「译文已更新（attempt N），待下一轮模型审校复核」，事件由账本记录。
- 下一轮 `model_review` 若再次报告同一问题，账本按「系统解决后复现」处理：`reopened`、`reopen_count+1`，并重新计划动作。`reopen_count ≥ 2` 的模型 issue 升级为阻断并要求人工（避免循环失控）。

## 5. 验收

- 单测：抽样选择、`report_issue` 去重、阻断规则、重译关闭模型 issue、复现升级。
- 集成：脚本化模型在 EPUB 样书上跑 `translate_full`，模型 issue 触发重译并在下一轮关闭；审计可证无循环。
- 评估（需真实 provider，花费需用户同意）：200 句人工标注集上的精确率/召回率。

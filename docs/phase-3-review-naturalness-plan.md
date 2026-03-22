---
description: Lane-C execution baseline for Phase 3 reviewer naturalness and stylistic intelligence under parallel-autopilot.
status: lane-c-mdu-17.1.3-complete
lane: lane-review-naturalness
last_updated: 2026-03-22
---

# Phase 3 Lane C：Reviewer / Stylistic Intelligence 基线

## 1. Objective

本 lane 的目标不是把 reviewer 变成默认自由重写代理，而是在保持 source-faithful 的前提下，提升中文自然度、压低翻译腔和概念化空转表达。

`MDU-17.1.1` 的交付目标是三件事：

1. 锁定 reviewer naturalness contract
2. 锁定 naturalness gate 的评估边界与非目标
3. 锁定 `MDU-17.1.2` 可以做什么、不能做什么

## 2. Current Baseline

当前仓库已经具备一条明确但偏窄的 stylistic review 路径：

- `STYLE_DRIFT` 由 [style_drift.py](/Users/smy/project/book-agent/src/book_agent/services/style_drift.py) 中的 source-aware rule 集检测
- 这类 issue 当前是：
  - packet-rooted
  - non-blocking advisory
  - 默认路由到 `RERUN_PACKET`
- 当 packet 上同时存在更高优先级的 `TERM_CONFLICT` / `UNLOCKED_KEY_CONCEPT` 时，workflow auto-followup 先处理这些问题，再处理 `STYLE_DRIFT`
- `STYLE_DRIFT` 的 rerun 不是自由改写，而是将 `preferred_hint + prompt_guidance` 聚合成更窄的 style hints，再交给 targeted rerun

这意味着 Lane C 的首版出发点已经明确：

- 不是从零设计风格审查
- 而是在现有 source-aware literalism / style-drift 基线上，做更清晰的 contract lock 与后续 scaffold 扩展

## 3. Locked Scope

`MDU-17.1.2` 允许进入的实现范围只包括以下方向：

- 扩展 source-aware 的 stylistic detection / evidence 组织
- 改善 naturalness 相关的 rerun guidance、packet-level followup scaffold 或 review summary
- 增加更稳定的自然度评估信号，但必须保持 packet-scoped、可解释、可回放
- 在不改变主链路 blocking 语义的前提下，提高自然度问题被发现、排序和纠偏的质量

## 4. Locked Non-Goals

以下内容明确不属于 Lane C MVP：

- 将 reviewer 切换为默认自由重写代理
- 引入 document-level 全章润色器或章节级自由改写
- 让纯 naturalness 问题默认变成 blocking issue
- 用审美偏好替代 source-faithful 判断
- 为了“更像中文”而允许脱离原文语义重写
- 做整书 voice harmonization、出版级文学润色或 marketing polish
- 在没有明确 source-aware evidence 的情况下发起大范围 stylistic rerun

## 5. Naturalness Gate Boundary

首版 `naturalness-gate` 只处理以下类型的问题：

- 明显字面直译导致的技术中文生硬
- 已知概念名未采用更自然、已验证的技术表达
- 英文骨架被逐词搬进中文，造成定义句或解释句发硬
- 抽象服务化、抒情化、书面腔表达覆盖了原文里更具体、更像技术书的说法

首版 `naturalness-gate` 明确不处理：

- 文学风格优劣
- 整章语气统一
- 作者人格化 voice 还原
- 没有明确 source anchor 的“总体读感不好”
- 需要整段重写才能判断好坏的宽泛审美问题

## 6. Locked Benchmark Set

Lane C 首版必须围绕当前仓库里已经有 focused regression 的样本组，而不是临时挑新句子。冻结的 benchmark families 为：

- `LITERALISM_XHTML`
  - 验证 `context engineering`、`weight of evidence`、`contextually accurate outputs`、命名引导句等典型直译腔
- `KNOWLEDGE_TIMELINE_LITERALISM_XHTML`
  - 验证时间线说明句不要压缩成“获知时间”式骨架
- `DURABLE_SUBSTRATE_LITERALISM_XHTML`
  - 验证抽象技术名词链不要硬压成“持久基底”
- `RESPONSIBILITY_LITERALISM_XHTML`
  - 验证风险/责任强调句不要滑向偏抒情书面腔
- `CONSISTENCY_CARE_LITERALISM_XHTML`
  - 验证具体人物照应关系不要被抽象成“服务/关怀/连贯性”
- `MIXED_AUTO_FOLLOWUP_XHTML`
  - 验证 style drift 在 mixed packet 中继续服从 term / concept 优先级，不抢占主链路

这些样本的用途不是“代表所有中文自然度问题”，而是作为 Lane C 的首版稳定 benchmark，避免后续实现滑向开放式美学判断。

## 7. Locked Intervention Model

首版允许的自动 intervention 固定为：

1. source-aware `STYLE_DRIFT` detection
2. packet-level advisory issue
3. packet-scoped rerun guidance aggregation
4. targeted rerun / followup execution
5. rerun 后重新 review 以验证问题是否收敛

首版不允许的自动 intervention：

- reviewer 自己直接重写整段并覆写 target
- chapter-level stylistic rewrite
- document-level second-pass polish
- 在无明确 source evidence 时只凭“更顺口”直接改译文

## 8. Acceptance Signals

`review-style-contract` v1 与 `naturalness-gate` v1 的成功标准冻结为：

- 纯 `STYLE_DRIFT` 仍然是 non-blocking packet advisory
- `STYLE_DRIFT` 的默认 action 仍然是 `RERUN_PACKET`
- mixed packet 的 auto-followup 仍然优先处理 `TERM_CONFLICT` / `UNLOCKED_KEY_CONCEPT`
- rerun guidance 必须能聚合 `preferred_hint` 与 `prompt_guidance`
- 后续 scaffold 只能在这些前提上增强 naturalness intelligence，而不能悄悄改成自由润色

## 9. What MDU-17.1.2 Is Allowed To Build

下一步 `MDU-17.1.2` 的实现焦点应明确收敛为：

1. 引入更清晰的 stylistic intelligence scaffold
2. 让 reviewer / workflow 对自然度问题有更稳定的解释与引导
3. 保持 packet-scoped、source-aware、non-blocking 默认语义

下一步不该做：

- reviewer 自主重写主路径
- 章节级或文档级风格统一器
- 无 benchmark 的“到处加规则”

## 10. Acceptance For MDU-17.1.1

本 MDU 完成的判断标准：

- lane C 有独立 contract doc，不再只散落在 Phase 3 总计划里
- benchmark families 已锁定
- 允许动作、非目标和 gate 边界已锁定
- 下一步可以直接进入 `MDU-17.1.2`，而不需要重新讨论“naturalness 到底想做什么”

## 11. Execution Consequence

`MDU-17.1.1` 已完成。

下一步 `MDU-17.1.2` 的默认焦点应是：

1. 在现有 `STYLE_DRIFT -> RERUN_PACKET` 主路径上补 stylistic intelligence scaffold
2. 保持 source-aware、packet-scoped、non-blocking 默认语义
3. 让后续 `MDU-17.1.3` 能围绕锁定的 benchmark families 跑 focused regressions

## 12. MDU-17.1.1 Completion Snapshot

当前 lane C 已冻结的 contract 事实：

- `STYLE_DRIFT` 仍是 packet-level advisory，不会直接阻断 chapter/export
- `TERM_CONFLICT` / `UNLOCKED_KEY_CONCEPT` 仍然优先于 style drift auto-followup
- 自然度改进默认通过 targeted rerun guidance 完成，而不是 reviewer 直接重写
- Lane C 的 benchmark、non-goals 与 allowed interventions 已写成独立文档

因此，lane C 现在可以从“方向定义不清”进入“实现最小 scaffold”的阶段，而不会在下一轮滑成自由风格化改写。

## 13. MDU-17.1.2 Completion Snapshot

`MDU-17.1.2` 已完成，当前 lane C 的最小 scaffold 已经落地为：

- review summary 现在会生成 chapter-level `naturalness_summary`
- `naturalness_summary` 当前显式暴露：
  - `advisory_only`
  - `style_drift_issue_count`
  - `affected_packet_count`
  - `dominant_style_rules`
  - `preferred_hints`
- 这层 summary 目前是 additive observability，不改变现有持久化 schema，也不改变 blocking 语义
- workflow / API review response 已同步暴露该 summary，供后续 focused regression 和 operator 判断使用
- `STYLE_DRIFT` 的 rerun hints 现在除了 `preferred_hint + prompt_guidance` 之外，还会带上命中的生硬译法片段，帮助 targeted rerun 更直接地知道“哪里发硬”

## 14. Execution Consequence After MDU-17.1.2

下一步 `MDU-17.1.3` 的默认焦点应明确收敛为：

1. 围绕锁定的 benchmark families 运行 naturalness-focused regressions
2. 证明新的 `naturalness_summary` 和更具体的 rerun hints 真正带来可验证收益
3. 在治理层收口 lane C 证据，而不是继续无边界扩规则

## 15. MDU-17.1.3 Completion Snapshot

`MDU-17.1.3` 已完成，lane C 的收口证据现在不再散落在旧测试里，而是被汇总成 dedicated acceptance artifact：

- 新增 [test_review_naturalness_acceptance.py](/Users/smy/project/book-agent/tests/test_review_naturalness_acceptance.py)
- acceptance 现在显式覆盖三类证明：
  - 锁定的 literalism benchmark families 会稳定产出 chapter-level `naturalness_summary`
  - guided style followup 能在锁定 contract 内收敛 literalism benchmark，而不会把 reviewer 变成自由重写代理
  - mixed benchmark 继续保持 `TERM_CONFLICT` 优先于 `STYLE_DRIFT` 的 auto-followup 语义
- 这份 acceptance artifact 与上一轮的 scaffold regressions 已合跑通过，因此 lane C 的证据面不再只是 narrative contract 或零散 focused asserts

## 16. Execution Consequence After MDU-17.1.3

`lane-review-naturalness` 已 accepted for wave-1。

下一步默认不再继续扩 lane C 规则，而是进入 `MDU-18.1.1`：

1. 执行三条 lane 的 integration gate
2. 清理 stale contract / blocker
3. 固化当前 merge 结论与 Phase 3 checkpoint 准备状态

---
description: Phase 3 baseline for continuing Book Agent under the new parallel-autopilot skill with a serial control plane and three lane-aware execution candidates.
status: phase-3-complete
mode: resume
last_updated: 2026-03-22
---

# Phase 3 并行 Autopilot 执行基线

## 1. Locked Requirement

在不修改 [auto-pilot.md](/Users/smy/project/book-agent/auto-pilot.md) 当前工作流本体的前提下，使用新的 [parallel-autopilot skill](/Users/smy/project/book-agent/.agents/skills/parallel-autopilot/SKILL.md) 继续接管 Book Agent 的下一阶段研发，并将以下三个目标作为同一阶段下的三条并行候选 lane：

1. rebuilt EPUB/PDF 交付形态升级
2. `PDF_SCAN` 扩到更大 corpus 的性能与稳定性
3. reviewer / stylistic intelligence 提升中文自然度

## 2. Non-Goals

- 不把这三个目标直接改造成“真并行写代码”
- 不修改现有 [auto-pilot.md](/Users/smy/project/book-agent/auto-pilot.md) 的命令式协议与历史语义
- 不在本阶段默认引入自动 branch/merge/cherry-pick/rebase 编排
- 不让 reviewer 默认进入自主重写主路径
- 不把 rebuilt EPUB/PDF 一步推到 publication-grade DTP 级排版

## 3. Operating Mode

- 模式：`resume`
- 控制面：串行
- 执行面：lane-aware，并仅在写集、契约和测试基座互斥时视为并行候选
- 当前 MVP 边界：只做“并行感知调度 + lane 状态治理 + 串行集成收敛”，不承诺真并发代码写入

## 4. Serial Control Plane Decisions

以下步骤保持串行：

1. requirement lock
2. ADR freeze
3. work-graph generation
4. lane partitioning
5. contract upgrades
6. merge gate
7. phase checkpoint

本轮已经完成的串行控制面工作：

- 锁定下一阶段为 `Phase 3`
- 将三个目标收敛为三条 lane，而不是三条互不相干的 backlog
- 确认 structured state 为真相源：`WORK_GRAPH.json`、`LANE_STATE.json`
- 确认 `PROGRESS.md`、`DECISIONS.md`、`RUN_CONTEXT.md` 为镜像与操作面

## 5. Wave Plan

### Wave 0：Control Plane Bootstrap

状态：已完成

内容：

- 锁定 requirement 与非目标
- 冻结 Phase 3 的治理决策
- 创建 work graph / lane state / run context
- 将 roadmap 从 `59/59` 重开为新的 lane-aware 阶段

### Wave 1：Three Lane Candidates

状态：已完成，`lane-delivery-upgrade`、`lane-pdf-scan-scale`、`lane-review-naturalness` 均已形成 accepted evidence

lane 列表：

- `lane-delivery-upgrade`
- `lane-pdf-scan-scale`
- `lane-review-naturalness`

### Wave 2：Integration Checkpoint

状态：已完成

## 6. Lane Definitions

| Lane | Objective | Primary Write Set | Contract Tags | Current Status |
|---|---|---|---|---|
| `lane-delivery-upgrade` | 将当前 `MERGED_MARKDOWN + BILINGUAL_HTML` 交付面扩展到 rebuilt EPUB/PDF 的最小可交付路径 | `services/export`, `services/layout_validate`, `app/api`, export tests/docs | `delivery-artifact-contract`, `export-manifest` | `done` |
| `lane-pdf-scan-scale` | 将 `PDF_SCAN` 从最小可行样本扩展到更大 corpus 的稳定性、性能和恢复能力 | `services/bootstrap`, OCR/runtime/reporting scripts, scan regressions/docs | `pdf-scan-runtime`, `scan-corpus-acceptance` | `done` |
| `lane-review-naturalness` | 在不默认启用 reviewer 自主重写的前提下，提高中文自然度与 stylistic review intelligence | `services/review`, `services/workflows`, translator prompt/rule tests/docs | `review-style-contract`, `naturalness-gate` | `done` |

## 7. Parallel-Candidate Rules

允许并行候选的前提：

- lane 不共享文件写集
- lane 不共享模块 ownership
- lane 不升级同一公共接口契约
- lane 不改写同一测试基座并互相失效

必须串行的情况：

- rebuilt export contract 升级
- review gate contract 升级
- 任何状态协议变更
- phase checkpoint 与 rollback 决策

## 8. Recommended Claim Order

虽然 Wave 1 的三条 lane 都被视为并行候选，但在当前 MVP 下，默认仍由单控制器串行 claim。推荐顺序：

1. `lane-pdf-scan-scale`
2. `lane-delivery-upgrade`
3. `lane-review-naturalness`

原因：

- `lane-pdf-scan-scale` 与刚完成的 Phase 2 语料、运行时证据连续性最强
- 该 lane 对共享交付契约的扰动小于 rebuilt export lane
- reviewer naturalness lane 对中文质量价值高，但更依赖已有 export / scan 主链路稳定后再扩回归样本

这不是业务优先级的永久排序，只是 Phase 3 kickoff 的默认 claim 顺序。

## 9. Acceptance Targets

### `lane-delivery-upgrade`

- 至少形成一条 rebuilt EPUB/PDF 的最小导出链路
- 不破坏现有 `MERGED_MARKDOWN + BILINGUAL_HTML` 契约
- export gate 继续 fail-closed

### `lane-pdf-scan-scale`

- 扩展到更大 scanned corpus 的代表性样本
- 提供稳定的运行时 telemetry、失败分类与恢复路径
- 不让 larger-corpus 执行重新引入 hidden state
- 将 acceptance target 编译成可执行阈值与 focused regression，而不是只停留在 narrative plan

### `lane-review-naturalness`

- 建立新的中文自然度评估/纠偏基线
- 不把 reviewer 默认切成自由重写代理
- 真实样本上能证明比当前 deterministic review 更高的中文自然度收益

### Phase 3 Checkpoint

- 三条 lane 至少各自形成一条可验证证据链
- 无未广播的共享契约变更
- `PROGRESS.md`、`DECISIONS.md`、`WORK_GRAPH.json`、`LANE_STATE.json`、`RUN_CONTEXT.md` 一致

## 10. State Artifacts

- [PROGRESS.md](/Users/smy/project/book-agent/PROGRESS.md)：人类可读总览
- [DECISIONS.md](/Users/smy/project/book-agent/DECISIONS.md)：ADR 决策镜像
- [WORK_GRAPH.json](/Users/smy/project/book-agent/WORK_GRAPH.json)：调度真相
- [LANE_STATE.json](/Users/smy/project/book-agent/LANE_STATE.json)：lane 生命周期真相
- [RUN_CONTEXT.md](/Users/smy/project/book-agent/RUN_CONTEXT.md)：当前操作面摘要

## 11. Immediate Next Action

`MDU-18.1.2` 已完成。Phase 3 当前已正式收口，串行控制面已确认：

- `lane-delivery-upgrade` 的 additive rebuilt delivery 不会被 `lane-review-naturalness` 的 non-blocking `STYLE_DRIFT` advisory 破坏
- `lane-pdf-scan-scale` 的 larger-corpus acceptance 继续保持绿灯
- 三条 lane 的 contract tag、acceptance artifact 与治理状态在 `WORK_GRAPH.json` / `LANE_STATE.json` / 计划文档之间一致

下一默认动作不再是继续 claim Phase 3 内部 MDU，而是：

- 等待下一阶段目标锁定
- 在新的 requirement lock 之后重开 work graph / wave / lane 规划

## 12. MDU-18.1.1 Completion Snapshot

本轮新增的 integration gate 工件：

- [scripts/phase3_integration_gate.py](/Users/smy/project/book-agent/scripts/phase3_integration_gate.py)
- [tests/test_phase3_integration_gate.py](/Users/smy/project/book-agent/tests/test_phase3_integration_gate.py)

本轮锁定的 integration 事实：

- wave-1 三条 lane 都已有 canonical acceptance entry：
  - lane A：rebuilt delivery focused regressions + preserved-contract regressions + negative-path regressions
  - lane B：larger-corpus acceptance helper + dedicated acceptance tests
  - lane C：dedicated naturalness acceptance artifact
- integration gate 不再只依赖 lane 文档叙述，而是显式记录 acceptance matrix
- integration gate 至少包含一条 cross-lane regression：
  - `STYLE_DRIFT` 维持 non-blocking advisory 时，不会阻断 `merged_markdown / rebuilt_epub / rebuilt_pdf` 的 document-level 导出
- 当前未发现 stale contract / blocker：
  - `delivery-artifact-contract`
  - `export-manifest`
  - `pdf-scan-runtime`
  - `scan-corpus-acceptance`
  - `review-style-contract`
  - `naturalness-gate`

执行结论：

- `MDU-18.1.1` 已完成
- Phase 3 已进入最终 checkpoint 收口步骤
- 下一默认动作是 `MDU-18.1.2`

## 13. MDU-18.1.2 Completion Snapshot

`MDU-18.1.2` 已完成，Phase 3 的 checkpoint 已正式关闭。

本轮收口内容：

- `phase-3-parallel-autopilot-plan.md` 前台状态从 `wave-2 integration gate complete` 收束为 `phase-3-complete`
- `PROGRESS.md`、`DECISIONS.md`、`WORK_GRAPH.json`、`LANE_STATE.json`、`RUN_CONTEXT.md` 已全部对齐到“Phase 3 已结束、等待下一阶段目标锁定”
- `WORK_GRAPH.json` 中：
  - `mdu-18.1.2 = done`
  - `wave-2 = done`
  - `phase-3-checkpoint` contract version 升为 `2`
- `LANE_STATE.json` 已显式去除 active wave，避免 resume 时误判仍有进行中的 lane

Phase 3 最终结论：

- lane A、lane B、lane C 都已各自形成 executable acceptance evidence
- wave-2 integration gate 已证明 lane 间契约可共存
- 当前没有遗留的 stale contract / blocker 需要在 Phase 3 内继续处理

后续重入条件：

- 只有在新的目标被锁定后，才应重开下一阶段
- 不允许在当前 Phase 3 已关闭的状态上继续悄悄 claim 新 MDU

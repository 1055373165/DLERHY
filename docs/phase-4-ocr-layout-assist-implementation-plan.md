---
description: Phase 4 implementation-planning baseline for high-risk PDF OCR and layout assist hardening under parallel-autopilot.
status: phase-4-implementation-planning-complete
mode: resume
last_updated: 2026-03-23
---

# Phase 4 高风险 PDF OCR / Layout Assist 实现阶段规划

## 1. Locked Continuation

Phase 4 kickoff baseline 已完成；当前不再回答“要不要做高风险 OCR / layout assist”，而是回答更窄的问题：

- 这项能力的首轮实现应该如何拆分
- 哪些工作必须先保持串行
- 哪些 lane 候选现在还不能安全放行
- 进入真实实现前，最小 execution shape 和 acceptance gate 应该长什么样

## 2. Implementation Goal

本阶段的目标不是直接写完 OCR / layout assist，而是把后续实现收敛成一个不会失控的执行形态：

1. 明确首轮实现是单 lane 还是多 lane
2. 明确首轮实现的 primary write set、contract tags 和保护边界
3. 明确第一波实现完成前必须产出的 focused regression / acceptance evidence

## 3. Non-Goals

- 不在本阶段直接实现新的 OCR / layout assist 主路径
- 不为了“并行 autopilot”而强行拆出伪并行 lane
- 不在 contract 仍不稳定时同时改 `bootstrap / parse / review / export gate` 的多个并发分支
- 不把高风险 assist 扩张成默认整页 VLM 重建

## 4. Operating Mode

- 模式：`resume`
- 控制面：串行
- 当前形态：implementation planning
- 当前结论：planning 已完成，真实实现入口已切到 `lane-ocr-layout-assist-core`

## 5. Why Parallel Lanes Are Not Safe Yet

当前目标虽然属于 lane-aware workflow，但首轮实现还不适合直接拆成多 lane，原因是：

- `risk classification -> assist eligibility -> provenance normalization -> bootstrap/review/export re-entry` 仍是一条强耦合链
- 共享写集会同时落到 `services/bootstrap`、结构恢复路径、下游 gate 与 focused regressions
- `ocr-layout-assist` contract 目前只冻结了 baseline 语义，还没有冻结 execution-time artifact contract
- 若此时硬拆 lane，只会得到频繁的 write-set 冲突和 contract 漂移

因此，当前推荐不是“多 lane 并行实现”，而是：

- 先做一个单 lane 的核心实现规划
- 等 execution contract 稳定后，再评估是否拆出后续 lane

## 6. Recommended Execution Shape

当前推荐执行形态：

- `Wave 0`：implementation planning
- `Wave 1`：单 lane `lane-ocr-layout-assist-core`
- `Wave 2`：focused acceptance / integration checkpoint

`lane-ocr-layout-assist-core` 预期负责：

- 将 risk bucket 路由接到真实执行路径
- 定义 assist invocation 的 page/region provenance contract
- 保证 assist 结果可重新进入 block / chapter / packet / export 主链路
- 建立 fail-closed focused regressions

当前明确不拆出的 lane：

- 独立 `review lane`
- 独立 `export lane`
- 独立 `scan-only lane`

原因：这些切法都会共享未冻结的结构契约。

## 7. Task 20.1 Scope

任务 `20.1` 只负责 implementation planning，不负责实现本身。

### `MDU-20.1.1`

- 锁定 implementation phase 的 execution shape、首轮非目标和“不强拆 lane”的结论

### `MDU-20.1.2`

- 冻结第一波实现的 lane boundary、write set、contract tags、acceptance gate

### `MDU-20.1.3`

- 同步 `PROGRESS.md` / `DECISIONS.md` / `WORK_GRAPH.json` / `LANE_STATE.json` / `RUN_CONTEXT.md`
- 将当前控制面 handoff 到第一条真实实现入口

## 8. Planned Lane Contract

首轮 implementation lane 暂定为：

| Lane | Objective | Primary Write Set | Contract Tags | Status |
|---|---|---|---|---|
| `lane-ocr-layout-assist-core` | 将高风险 OCR/layout assist 从 baseline 语义推进到可执行主链路，但保持 fail-closed 和 provenance-first | `services/bootstrap`, `domain/structure/pdf`, `domain/structure/ocr`, `services/pdf_structure_refresh`, focused regressions/docs | `ocr-layout-assist-execution`, `ocr-layout-assist-acceptance` | `contract-frozen / not-yet-opened` |

lane-scoped contract doc：

- [phase-4-ocr-layout-assist-core-plan.md](/Users/smy/project/book-agent/docs/phase-4-ocr-layout-assist-core-plan.md)

## 9. Acceptance Shape

在进入真实实现前，当前 planning phase 先冻结 acceptance 形态：

- 至少一条 focused regression 证明 Bucket B/C 的 assist 路由只会在允许范围内触发
- 至少一条 focused regression 证明 assist 失败时会 fail-closed，并保留显式风险证据
- 至少一条 focused regression 证明 assist 结果能回流到现有 structured pipeline，而不是生成旁路状态
- 至少一条 focused regression 证明低风险页不会被 assist 静默改写

## 10. State Artifacts

- [PROGRESS.md](/Users/smy/project/book-agent/PROGRESS.md)：当前阶段与 planning 位置
- [DECISIONS.md](/Users/smy/project/book-agent/DECISIONS.md)：执行形态决策
- [WORK_GRAPH.json](/Users/smy/project/book-agent/WORK_GRAPH.json)：Phase 20 节点与依赖真相
- [LANE_STATE.json](/Users/smy/project/book-agent/LANE_STATE.json)：当前仍无 live lane
- [RUN_CONTEXT.md](/Users/smy/project/book-agent/RUN_CONTEXT.md)：操作面入口

## 11. Immediate Next Action

`MDU-20.1.3` 完成后，下一步不是继续停留在 planning，而是：

- 直接 claim `lane-ocr-layout-assist-core / MDU-21.1.1`
- 在真实上游恢复路径中接入 Bucket B/C assist routing 与 provenance scaffold
- 保持下游 review/export/layout gate 继续作为 consumer，不在首轮实现里一并改写

## 12. MDU-20.1.1 Completion Snapshot

这一轮 implementation planning 明确了四件事：

- Phase 4 的下一个合理阶段是“实现规划”，不是重新等待新目标
- 当前 goal 虽然 lane-aware，但首轮实现不应强拆成多 lane
- 首轮实现的推荐形态是 `Wave 1 -> lane-ocr-layout-assist-core`
- 当前最关键的下一步是冻结 execution contract，而不是立刻进入实现细节

执行结论：

- `MDU-20.1.1` 已完成
- `MDU-20.1.2` 应作为下一默认动作
- 当前仍未创建真实实现 lane

## 13. MDU-20.1.2 Completion Snapshot

这一轮 implementation planning 已进一步冻结：

- `lane-ocr-layout-assist-core` 的 lane-scoped contract 已独立成文，不再只挂在总计划里
- 首轮 implementation lane 的 owned write set 明确收缩在上游 OCR / structure recovery
- `review / export / layout_validate` 当前被明确定义为 out-of-lane consumer，而不是顺手跟着改
- acceptance gate 已冻结成 4 类 focused evidence：`bucket-bc-routing`、`fail-closed-fallback`、`structured-reentry`、`low-risk-preservation`

执行结论：

- `MDU-20.1.2` 已完成
- `MDU-20.1.3` 应作为下一默认动作
- 当前仍未打开真实实现 wave

## 14. MDU-20.1.3 Completion Snapshot

这一轮 implementation planning 已正式完成治理 handoff：

- 当前控制面不再停留在“planning 进行中”，而是明确打开了第一条真实实现入口
- `Wave 1` 已收敛为单 lane：`lane-ocr-layout-assist-core`
- `LANE_STATE.json` 已显式记录 ready-to-claim lane，而不是继续保持空白 live state
- 下一默认动作已切到 `MDU-21.1.1`

handoff 文档：

- [phase-4-ocr-layout-assist-core-execution-plan.md](/Users/smy/project/book-agent/docs/phase-4-ocr-layout-assist-core-execution-plan.md)

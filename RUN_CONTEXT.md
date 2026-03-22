# 运行上下文

- 当前模式：`resume`
- 当前阶段：Phase 4 Lane Core - OCR / Layout Assist 实现
- 当前 wave：`wave-1-phase-4-core`
- 当前 lane：`lane-ocr-layout-assist-core (ready-to-claim)`
- 活跃 lanes：`lane-ocr-layout-assist-core (ready-to-claim)`
- 阻塞 lanes：无
- 当前集成 gate：`phase-4 core implementation wave open / first claim pending`
- 下一默认动作：执行 `MDU-21.1.1`，在上游恢复路径中接入 Bucket B / Bucket C assist routing 与 provenance scaffold

## 当前目标

在不修改 [auto-pilot.md](/Users/smy/project/book-agent/auto-pilot.md) 本体的前提下，继续使用 [parallel-autopilot skill](/Users/smy/project/book-agent/.agents/skills/parallel-autopilot/SKILL.md) 自动驾驶，并将下一阶段默认锁定为高风险 PDF 页的双层 OCR / layout assist hardening。

## 当前约束

- 控制面仍串行
- 只做按需并行，不做盲目全并行
- merge / rollback / checkpoint 仍串行
- `WORK_GRAPH.json` 与 `LANE_STATE.json` 是调度真相
- 当前只允许打开 `lane-ocr-layout-assist-core` 一条实现 lane

## 当前判断

- Phase 3 已正式关闭；当前 live-ready wave/lane 已切到 Phase 4 core implementation
- rebuilt delivery、larger-corpus `PDF_SCAN` 与 reviewer naturalness 三条 lane 已各自 accepted，并通过了 phase integration gate
- 下一阶段最有杠杆的位置回到上游高风险 PDF 结构恢复，而不是继续在已关闭 lane 上做局部打磨
- Phase 4 的默认方向已锁到“规则/几何优先，AI assist 只用于高风险页/区域兜底”
- `MDU-19.1.1` 已完成，输入范围、保护契约、required outputs 与 ready-for-next-MDU gate 已冻结
- `MDU-19.1.2` 已完成，风险分桶、routing semantics 与 fail-closed contract 已冻结
- `MDU-20.1.1` 已完成，首轮实现阶段已明确采用 single-lane-first 的 planning 结论
- `MDU-20.1.2` 已完成，`lane-ocr-layout-assist-core` 的 write set、execution contract 与 acceptance gate 已冻结
- `MDU-20.1.3` 已完成，当前控制面已正式 handoff 到 `lane-ocr-layout-assist-core / MDU-21.1.1`
- 当前不应重新回到 planning，而应直接从首个真实实现入口开始执行

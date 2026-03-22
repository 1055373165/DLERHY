---
description: Phase 4 baseline for dual-layer OCR and layout assist hardening on high-risk PDF pages under parallel-autopilot.
status: phase-4-kickoff-complete
mode: resume
last_updated: 2026-03-23
---

# Phase 4 高风险 PDF OCR / Layout Assist 基线

## 1. Locked Direction

Phase 3 已完成并正式收口后，下一阶段的默认主目标锁定为：

- 面向高风险 PDF 页与局部区域，补强双层 OCR / layout assist
- 维持当前 deterministic control plane，不重开 Phase 3 已收口的三条 lane
- 让新增的 OCR / layout assist 结果继续回流到现有 structured pipeline，而不是旁路生成一条平行主链路

## 2. Default Objective

本阶段优先解决的不是“把所有 PDF 都丢给 VLM 重做一遍”，而是：

1. 识别哪些页、哪些区域属于结构恢复高风险
2. 冻结规则/几何优先、AI assist 兜底的双层策略
3. 为后续实现锁定 fail-closed contract、risk buckets 和恢复边界

## 3. Locked Requirement

`MDU-19.1.1` 锁定后的 requirement 不是“提升 PDF 质量”这种泛目标，而是以下更窄的可执行问题：

1. 仅针对当前 parser / OCR 已经显式暴露为中高风险的 PDF 页或局部区域
2. 仅提升结构恢复质量，不直接扩张到翻译策略、review 自主重写或 rebuilt 交付 fidelity
3. 任何新增 assist 都必须回流到现有 block / chapter / packet / export 主链路

换句话说，Phase 4 要做的是：

- 让高风险结构恢复从“单层 OCR / layout heuristic”升级到“规则/几何优先 + 高风险 assist 兜底”
- 但不允许把当前 deterministic pipeline 旁路成一个新的黑盒文档重建系统

## 4. In-Scope Inputs

本阶段默认纳入的输入范围：

- `PDF_SCAN`
- `PDF_MIXED`
- `PDF_TEXT` 中被现有 profile 判成 `page_layout_risk=high` 的页

本阶段默认不纳入：

- `EPUB`
- `PDF_TEXT + layout_risk=low` 的常规页
- 仅为了更好看而做的 merged/export 视觉重排

## 5. Required Outputs

Phase 4 后续实现必须产出的不是自由文本，而是可以重新进入当前控制面的结构化结果：

- 页级或区域级 risk classification
- assist 是否触发、为什么触发、触发在哪个 region 的 provenance
- 恢复后的 block / anchor / bbox / protected-artifact 结构结果
- fail-closed 时的明确原因与回退路径

明确不接受的输出形态：

- 只有一份模型生成 Markdown，但没有结构 provenance
- 只有“看起来更对”的人工判断，却无法重新进入现有 bootstrap / review / export 链路
- 需要 operator 从截图或临时日志手工推断 assist 到底做了什么

## 6. Protected Contracts

在进入实现型 MDU 前，本阶段先冻结这些保护契约：

- `parse_confidence / pdf_layout_risk / page_layout_risk` 继续是主链路显式信号，而不是被 assist 静默覆盖
- assist 只允许增强或纠正高风险页/区域，不允许重写低风险页的既有稳定结果
- assist 不得绕过 `review -> export gate -> fail-closed` 这些既有控制点
- 没有可用 assist、assist 失败、assist 置信度不足时，系统必须回退到当前主路径，而不是半成功半失败地写脏状态
- image / caption / code / table / equation 这类 protected artifact 不得因为 assist 引入新的“不可解释的结构漂移”

## 7. Baseline Model

Phase 4 冻结的 baseline 不是“上一个 OCR 模块 + 一个更强模型”的线性升级，而是双层恢复模型：

1. 第一层：沿用当前 profiler / parser / 几何 / 结构恢复主路径
2. 第二层：仅当显式风险信号触发时，才允许进入 page-scoped 或 region-scoped assist

这意味着：

- assist 不是默认主链路
- assist 的单位不是整本文档，也不是默认整页，而是高风险页或局部区域
- assist 产物必须重新落回当前 block / anchor / bbox / protected-artifact 结构，不接受只给 operator 一段“看起来更合理”的黑盒解释

## 8. Risk Buckets

本阶段冻结 4 个风险桶，后续实现与验收都必须沿这套桶路由：

### Bucket A：`pass_through_low_risk`

触发条件：

- `pdf_kind=text_pdf`
- `layout_risk=low`
- `ocr_required=false`

路由语义：

- 不触发 assist
- 继续走当前稳定 parser / bootstrap / review / export 主路径

### Bucket B：`localized_medium_risk`

触发条件：

- chapter 或 page 为 `layout_risk=medium`
- 存在局部 `suspicious_pages` 或 `page_layout_risk_pages`
- `parse_confidence >= 0.8`

路由语义：

- 允许 page-scoped 或 region-scoped assist 候选
- 默认仍保持 deterministic 主路径；优先 advisory / targeted repair，不默认升级成 blocking rewrite lane
- 典型场景：局部双栏错序、标题断裂、caption 邻接不稳、局部表格/公式/代码混排

### Bucket C：`single_page_scanned_anchorable_high_risk`

触发条件：

- `source_type=PDF_SCAN`
- 仅单页高风险
- `parse_confidence >= 0.82`
- `page_layout_reasons` 只包含 `ocr_scanned_page`
- 该页 artifact 已具备 caption link 或 group-context anchor 等局部锚点

路由语义：

- 允许更窄的 scanned-page assist
- 即使 chapter 级 risk 为 `high`，只要局部锚点完整，仍可保持 advisory / narrow-repair 语义
- 不允许因此放大成“整章都交给 assist 重建”

### Bucket D：`blocking_high_risk_or_low_confidence`

触发条件：

- `layout_risk=high` 且不满足 Bucket C
- 或 `parse_confidence < 0.8`
- 或高风险页超过单页局部范围
- 或 `page_layout_reasons` 超出 `ocr_scanned_page` 的窄集合

路由语义：

- 维持 blocking / fail-closed
- 不静默接受 assist 结果
- 优先进入结构问题、reparse、repair 或后续更强恢复 lane，而不是在当前主链路里偷偷吞下不稳定结果

## 9. Fail-Closed Contract

后续实现必须遵守以下 fail-closed 规则：

- assist 只有在 Bucket B/C 触发时才允许执行
- assist 不可用、超时、低置信或输出缺 provenance 时，必须回退到当前 parser 结果
- 回退后必须保留显式风险证据，而不是把失败当作“没有问题”
- assist 不得静默覆盖 `parse_confidence / pdf_layout_risk / page_layout_risk / page_layout_reasons`
- assist 不得对低风险页写入新 block 顺序、bbox 或 protected-artifact 结构
- 如果 assist 结果无法重入现有 bootstrap / review / export 主链路，则视为失败，而不是“先存起来以后再说”

## 10. Non-Goals

- 不默认启用整页 VLM 解析
- 不承诺 publication-grade 的表格/公式语义重建
- 不在本阶段处理图内英文重绘或图片内文本翻译
- 不重开 rebuilt EPUB/PDF、larger-corpus `PDF_SCAN`、review naturalness 这三条已完成 lane
- 不把 OCR / layout assist 直接升级为新的自由式 agent 主链路

## 11. Operating Mode

- 模式：`resume`
- 控制面：串行
- 当前状态：Phase 4 kickoff baseline 已完成，后续实现型 lane / wave 规划尚未开始
- 当前工作形态：当前文档负责锁定 baseline，不直接进入实现；后续若继续推进，应先新开实现阶段计划

## 12. Immediate Task

当前 task `任务 19.1` 已完成。

当前默认下一步不再是继续 claim 本阶段 MDU，而是：

- 锁定高风险 OCR / layout assist 的实现阶段计划
- 再决定是否拆出 lane / wave

当前 handoff 文档：

- [phase-4-ocr-layout-assist-implementation-plan.md](/Users/smy/project/book-agent/docs/phase-4-ocr-layout-assist-implementation-plan.md)

## 13. Planned MDUs

- `MDU-19.1.1`：锁定 Phase 4 requirement、默认目标与 non-goals
- `MDU-19.1.2`：冻结高风险 OCR / layout assist baseline、risk buckets 与 fail-closed contract
- `MDU-19.1.3`：同步 `PROGRESS.md`、`DECISIONS.md`、`WORK_GRAPH.json`、`LANE_STATE.json`、`RUN_CONTEXT.md`，完成 Phase 4 kickoff 基线

## 14. Ready-For-Implementation-Planning Gate

进入下一阶段实现规划前，当前 baseline freeze 必须已经回答清楚：

- 哪些 PDF 输入真的在 Phase 4 范围内
- 哪些信号属于主链路显式真相，不允许被 assist 静默覆盖
- 每个风险桶的触发条件和默认路由是什么
- assist 的成功输出必须长什么样，失败时又如何回退
- 哪些高成本扩张目标明确不做

当前 gate 结论：已满足，可以进入后续实现阶段规划。

## 15. Success Boundary For Kickoff

本轮 kickoff 完成的标准不是代码实现，而是：

- 默认目标从“等待下一阶段目标锁定”切换为“Phase 4 高风险 PDF OCR / layout assist”
- 工作图中明确出现 Phase 4 的控制面节点与依赖
- 运行态不再保留 Phase 3 的 stale lane claim 顺序
- 下一默认动作清晰指向“实现阶段规划”，而不是继续 claim 本阶段 MDU

## 16. MDU-19.1.1 / MDU-19.1.2 / MDU-19.1.3 Completion Snapshot

这一轮 baseline freeze 明确了四件此前还不够硬的事：

- Phase 4 的输入范围只覆盖高风险 PDF 页/区域，不覆盖 EPUB 和低风险 text PDF 常规页
- assist 的输出必须是可回流的结构化结果与 provenance，不接受“只有模型看起来更懂了”的黑盒输出
- 当前主链路的显式信号仍是 `parse_confidence / pdf_layout_risk / page_layout_risk / page_layout_reasons`
- 高风险恢复已经冻结为 4 个风险桶，并明确了 advisory / narrow-assist / blocking / fail-closed 的路由语义

执行结论：

- `MDU-19.1.1` 已完成
- `MDU-19.1.2` 已完成
- `MDU-19.1.3` 已完成
- Phase 4 kickoff baseline 已正式收口
- 当前默认下一步切到“高风险 OCR / layout assist 的实现阶段规划”

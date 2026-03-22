---
description: Lane-scoped execution contract for the first high-risk OCR/layout assist implementation wave under Phase 4.
status: lane-core-contract-frozen
lane: lane-ocr-layout-assist-core
last_updated: 2026-03-23
---

# Phase 4 Lane Core：高风险 OCR / Layout Assist 首轮实现契约

## 1. Objective

本 lane 的目标不是“提升 OCR 效果”这种泛表述，而是把 Phase 4 baseline 中已经冻结的风险分桶与 fail-closed 语义，接到真实执行路径里。

`MDU-20.1.2` 锁定的交付目标是三件事：

1. 冻结 `lane-ocr-layout-assist-core` 的 ownership boundary
2. 冻结首轮 execution contract 与 acceptance gate
3. 冻结哪些下游模块当前只允许消费信号，不允许被本 lane 一并改写

## 2. Locked Scope

首轮实现只允许进入以下范围：

- 将 Bucket B / Bucket C 的 assist eligibility 接入真实 page-scoped / region-scoped 执行路径
- 将 assist provenance 正常回流到现有 block / anchor / bbox / protected-artifact 结构结果
- 在 assist 不可用、低置信、超时或缺 provenance 时，显式回退到当前 parser 主路径
- 为上述行为补 focused regression 与 acceptance 证据

## 3. Locked Non-Goals

以下内容明确不属于首轮 lane：

- 重写 `review.py` 的高风险 gate 语义
- 重写 `export.py` / `layout_validate.py` 的下游 gate 契约
- 引入整页默认 VLM 解析
- 为 EPUB 或低风险 text PDF 添加 assist
- 图内文本翻译、publication-grade 表格/公式重建、视觉级排版修复
- 新开独立 `review lane`、`export lane` 或 `scan-only lane`

## 4. Ownership Boundary

### In-Lane Primary Write Set

首轮实现允许拥有的 primary write set 冻结为：

- [bootstrap.py](/Users/smy/project/book-agent/src/book_agent/services/bootstrap.py)
- [ocr.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/ocr.py)
- [pdf.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py)
- [models.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/models.py)
- [pdf_structure_refresh.py](/Users/smy/project/book-agent/src/book_agent/services/pdf_structure_refresh.py)
- `tests/pdf-layout-assist-focused`
- [phase-4-ocr-layout-assist-core-plan.md](/Users/smy/project/book-agent/docs/phase-4-ocr-layout-assist-core-plan.md)

### Explicit Out-Of-Lane Consumers

以下模块当前只允许消费已有 contract，不属于本 lane 的首轮 ownership：

- [review.py](/Users/smy/project/book-agent/src/book_agent/services/review.py)
- [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
- [layout_validate.py](/Users/smy/project/book-agent/src/book_agent/services/layout_validate.py)
- workflow / API / document delivery surfaces

结论：

- 本 lane 可以改变上游恢复结果如何生成
- 本 lane 不能顺手改变下游 gate 的判断标准来“配合新结果过关”

## 5. Execution Contract

首轮 execution contract 冻结为：

### `ocr-layout-assist-execution`

- assist 仅允许在 Bucket B / Bucket C 中触发
- assist 单位仅允许是 page-scoped 或 region-scoped
- assist 输出必须带 provenance，至少能解释：
  - 为什么触发
  - 触发在哪个 page / region
  - 回流后改动了哪些结构结果
- assist 结果必须重新映射回当前 block / anchor / bbox / protected-artifact 结构
- 低风险页不得因 assist 被静默改写

### `ocr-layout-assist-acceptance`

首轮 acceptance gate 冻结为 4 类 focused evidence：

1. `bucket-bc-routing`
   - Bucket B/C 只在允许范围内触发 assist
2. `fail-closed-fallback`
   - assist 失败或不满足 contract 时，系统回退到当前 parser 结果，并保留风险证据
3. `structured-reentry`
   - assist 结果能重新进入当前 structured pipeline，而不是形成旁路状态
4. `low-risk-preservation`
   - 低风险页不会因为引入 assist 路径而被静默改写

## 6. What This Lane Must Not Hide

首轮实现不允许把这些问题藏起来：

- assist 实际触发过，但没有明确 provenance
- assist 失败了，但 operator 看不到失败原因与回退动作
- assist 改坏了结构后依靠下游 gate 放宽来“算成功”
- 上游恢复 contract 变化了，但 focused regression 没覆盖低风险 preservation

## 7. Immediate Execution Consequence

`MDU-20.1.2` 完成后，下一步 `MDU-20.1.3` 的焦点应明确收敛为：

1. 将当前治理状态 handoff 到首个真实实现入口
2. 明确第一条实现型 claim 不是泛化“做 OCR 优化”，而是执行 `lane-ocr-layout-assist-core`
3. 保持 `LANE_STATE.json` 只有在真实 wave 打开时才进入 live lane 状态

## 8. MDU-20.1.2 Completion Snapshot

这一轮 lane contract freeze 明确了四件此前还不够硬的事：

- 首轮实现只拥有上游 OCR / structure recovery 写集，不顺手改 review/export gate
- 首轮 contract 被拆成 `ocr-layout-assist-execution` 与 `ocr-layout-assist-acceptance`
- acceptance 不再是“看起来结构更好了”，而是 4 类 focused evidence
- 当前并行 autopilot 的合理形态仍然是 single-lane-first，而不是制造伪并行 lane

执行结论：

- `lane-ocr-layout-assist-core` contract 已冻结
- `MDU-20.1.2` 已完成
- 下一默认动作切到 `MDU-20.1.3`

---
description: First executable lane baseline for high-risk OCR/layout assist under Phase 4.
status: lane-core-mdu-21.1.1-ready
lane: lane-ocr-layout-assist-core
last_updated: 2026-03-23
---

# Phase 4 Lane Core：高风险 OCR / Layout Assist 首轮实现入口

## 1. Objective

本 lane 的首轮实现目标不是“整体提升 PDF 质量”，而是把已经冻结的 `ocr-layout-assist-execution` / `ocr-layout-assist-acceptance` 契约落成第一波可执行改动。

当前入口聚焦三件事：

1. 让 Bucket B / Bucket C 的 assist eligibility 进入真实执行路径
2. 让 assist 结果带着 provenance 回流到现有 structured pipeline
3. 在失败时显式 fail-closed，并用 focused regression 证明低风险页未被污染

## 2. Entry Preconditions

进入本 lane 前，以下条件已满足：

- [phase-4-ocr-layout-assist-plan.md](/Users/smy/project/book-agent/docs/phase-4-ocr-layout-assist-plan.md) 已冻结 risk buckets 与 fail-closed baseline
- [phase-4-ocr-layout-assist-implementation-plan.md](/Users/smy/project/book-agent/docs/phase-4-ocr-layout-assist-implementation-plan.md) 已冻结 single-lane-first 的 execution shape
- [phase-4-ocr-layout-assist-core-plan.md](/Users/smy/project/book-agent/docs/phase-4-ocr-layout-assist-core-plan.md) 已冻结 lane ownership boundary、write set 与 acceptance gate

## 3. Execution Boundary

当前 lane 的 owned write set 继续冻结为：

- [bootstrap.py](/Users/smy/project/book-agent/src/book_agent/services/bootstrap.py)
- [ocr.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/ocr.py)
- [pdf.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py)
- [models.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/models.py)
- [pdf_structure_refresh.py](/Users/smy/project/book-agent/src/book_agent/services/pdf_structure_refresh.py)
- focused regressions / acceptance helpers / lane docs

当前 out-of-lane consumer 保持不变：

- [review.py](/Users/smy/project/book-agent/src/book_agent/services/review.py)
- [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
- [layout_validate.py](/Users/smy/project/book-agent/src/book_agent/services/layout_validate.py)
- workflow / API / delivery surfaces

## 4. Task 21.1 Scope

### `MDU-21.1.1`

- 将 Bucket B / Bucket C 的 assist routing 与 provenance scaffold 接入真实执行路径

### `MDU-21.1.2`

- 补 fail-closed fallback、structured re-entry 与 low-risk preservation focused regressions

### `MDU-21.1.3`

- 运行 lane-core acceptance artifact，并同步治理证据

## 5. Acceptance Shape

本 lane 完成前必须形成 4 类 focused evidence：

- `bucket-bc-routing`
- `fail-closed-fallback`
- `structured-reentry`
- `low-risk-preservation`

这些 acceptance family 是当前 lane 的出站 gate，不允许被 narrative “看起来更好了”替代。

## 6. Immediate Next Action

当前默认 claim：

- `lane-ocr-layout-assist-core`
- `MDU-21.1.1`

下一轮进入真实实现时，不再回到 planning 讨论，而是直接在上游恢复路径中接入 Bucket B/C assist routing 与 provenance scaffold。

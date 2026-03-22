---
description: Lane-A execution baseline for Phase 3 rebuilt EPUB/PDF delivery under parallel-autopilot.
status: lane-a-mdu-15.1.3-complete
lane: lane-delivery-upgrade
last_updated: 2026-03-22
---

# Phase 3 Lane A：Rebuilt EPUB/PDF 交付升级基线

## 1. Objective

本 lane 的目标不是替换现有 `MERGED_MARKDOWN + BILINGUAL_HTML` 主交付面，而是在不破坏当前导出契约的前提下，增加一条最小可交付的 rebuilt EPUB/PDF 路径。

`MDU-15.1.1` 的交付目标是三件事：

1. 锁定 rebuilt EPUB/PDF 的 artifact contract
2. 锁定 rebuilt export manifest 的最小字段
3. 锁定本 lane 的非目标与 fail-closed 边界

## 2. Locked Scope

### Additive, Not Replacement

当前已存在且必须继续保持稳定的交付面：

- `review_package`
- `bilingual_html`
- `merged_html`
- `merged_markdown`

Lane A 不允许重写这些出口的语义，也不允许让 rebuilt 输出倒逼它们退化。

### New Export Contract

`MDU-15.1.2` 起允许新增两个 document-level export type：

- `rebuilt_epub`
- `rebuilt_pdf`

锁定规则：

- 这两个都是 **document-level export**，不是 chapter-level export
- rebuilt 导出必须复用当前已批准的 document bundle / merged export substrate
- rebuilt 导出不能绕过现有 export gate
- rebuilt 导出是 additive delivery artifact，不是新的 source of truth

## 3. Source Guards

### `rebuilt_epub`

- 仅对 `source_type=epub` 开放
- 必须建立在当前 EPUB spine / nav / asset 可回放的前提上
- 若缺失必要源资产或重建条件不足，必须 fail-closed，不得静默降级为“伪 EPUB”

### `rebuilt_pdf`

- 允许对 `epub / pdf_text / pdf_mixed / pdf_scan` 统一开放 document-level contract
- 但首版 rebuilt PDF 明确允许以 `merged_html` 为渲染基底
- 不要求首版复刻原始 PDF 的页坐标、图文回流和版心几何
- 若 PDF renderer 不可用，必须显式报 unsupported / unavailable，而不是输出伪成功文件

## 4. Locked Non-Goals

以下内容明确不属于 Lane A MVP：

- publication-grade DTP 级排版复刻
- 原始 PDF page-coordinate faithful rebuild
- 图内英文回写成中文
- 重新 OCR、重新 parse 或重新 segmentation
- chapter-level rebuilt EPUB/PDF
- 用 rebuilt 输出替换当前 `merged_html` / `merged_markdown`
- 修改 `review_package` / `bilingual_html` 的契约语义

## 5. Rebuild Strategy Decision

### `rebuilt_epub`

推荐实现路线：

- 以当前 document bundle 中的可见章节顺序为正文真相
- 以已批准 target text 构造 translated XHTML/spine
- 尽量复用原 EPUB 的 nav、metadata、图片资源与可安全保留的静态资产
- 不尝试在 MVP 中恢复复杂交互脚本、原始 CSS 全保真和 MathML 完全复刻

### `rebuilt_pdf`

推荐实现路线：

- 以 `merged_html` 为统一渲染基底
- 由单一 HTML-to-PDF renderer 负责生成 document-level PDF
- 首版质量目标是“可读、章节顺序正确、标题/图片/列表/代码块不坏”
- 明确不承诺“与原 PDF 外观高度一致”

### Why This Route

选择这条路线，而不是“从 source PDF/EPUB 各自重建独立排版引擎”，原因是：

- 当前仓库已经有稳定的 merged render substrate
- merged export 已具备 layout gate 与 issue routing
- rebuilt delivery 的首要目标是可交付，不是重新发明排版系统
- 这条路线对共享导出契约和 review/export 主链路扰动最小

## 6. Manifest Contract

`MDU-15.1.2` 起，两个 rebuilt export 都必须生成 sidecar manifest。

### Common Required Fields

- `document_id`
- `title`
- `title_src`
- `title_tgt`
- `author`
- `source_type`
- `export_type`
- `output_path`
- `contract_version`
- `renderer_kind`
- `derived_from_exports`
- `chapter_count`
- `issue_status_summary`
- `translation_usage_summary`
- `render_summary`
- `expected_limitations`

### `derived_from_exports`

必须显式记录 rebuilt 输出依赖了哪些上游 artifact。首版冻结为：

- `merged_html`
- `merged_markdown`

如果某个 rebuilt artifact 只依赖其中之一，也必须显式列出来，而不是默认靠操作者推断。

### `renderer_kind`

首版约定值：

- `rebuilt_epub`: `epub_spine_rebuilder`
- `rebuilt_pdf`: `html_print_renderer`

### `expected_limitations`

必须显式写出当前输出的已知边界，例如：

- `not_page_faithful_to_source_pdf`
- `assets_reused_from_source_when_available`
- `no_in_image_text_rewrite`
- `single_document_level_output_only`

## 7. File Naming Contract

为避免与当前 merged 输出冲突，文件命名固定为：

- `rebuilt-document.epub`
- `rebuilt-document.epub.manifest.json`
- `rebuilt-document.pdf`
- `rebuilt-document.pdf.manifest.json`

别名文件名可以在实现阶段按中文标题生成，但 canonical path 先按以上固定名锁定。

## 8. Gate Rules

rebuild export 必须继续遵守 fail-closed：

- 上游 merged export gate 不通过，禁止 rebuilt export 偷跑
- rebuilt export 自身若缺资产、缺 renderer、缺源结构，必须返回显式错误
- 不允许用 rebuild 成功来掩盖当前 `merged_html` / `merged_markdown` 的坏状态

首版 gate 依赖固定为：

1. document 已具备稳定的 `merged_html`
2. document 已具备稳定的 `merged_markdown`
3. 现有 layout validation 未放松

## 9. API / Workflow Boundary

`MDU-15.1.2` 的实现允许扩展：

- `ExportType`
- document export/download API
- export list/history record
- workflow / executor 的 document-level export stage

但不允许：

- 改写当前 `translate_full -> bilingual_html -> merged_html` 的主阶段顺序
- 让 rebuilt export 成为 Phase 1/2 的必经出口
- 将 rebuilt export 混入 chapter-level work item 语义

## 10. Acceptance For MDU-15.1.1

本 MDU 完成的判断标准：

- lane-A 有独立 contract doc，而不是只散落在 Phase 3 总计划里
- rebuilt export 的最小 artifact contract 已锁定
- manifest 最小字段已锁定
- non-goals 和 fail-closed 边界已锁定
- 下一步可以直接进入 `MDU-15.1.2` 做实现，而不需要重新讨论 contract

## 11. Execution Consequence

`MDU-15.1.1` 已完成。

下一步 `MDU-15.1.2` 的焦点应明确收敛为：

1. 在不破坏现有 `merged_html / merged_markdown / bilingual_html` 的前提下，引入 `rebuilt_epub / rebuilt_pdf` 的最小实现
2. 为 rebuilt artifact 写入首版 sidecar manifest
3. 将 rebuilt export 接入 document-level export/download 路径，并保持 gate fail-closed

## 12. MDU-15.1.2 Completion Snapshot

`MDU-15.1.2` 已完成，当前 lane A 的最小实现边界已经落地为：

- 新增 document-level export type：`rebuilt_epub`、`rebuilt_pdf`
- `rebuilt_epub`：
  - 仅对 `source_type=epub` 开放
  - 以当前 visible chapters + translated render blocks 构造最小 EPUB spine
  - canonical 输出：
    - `rebuilt-document.epub`
    - `rebuilt-document.epub.manifest.json`
- `rebuilt_pdf`：
  - 以 `merged_html` 为首版渲染 substrate
  - 通过 `html_print_renderer` 生成 document-level PDF
  - canonical 输出：
    - `rebuilt-document.pdf`
    - `rebuilt-document.pdf.manifest.json`
- rebuilt manifest 已显式包含：
  - `contract_version`
  - `renderer_kind`
  - `derived_from_exports`
  - `derived_export_artifacts`
  - `expected_limitations`
- document export workflow / API / download 路径已经识别新的 export type
- 现有 `merged_html / merged_markdown` 路径未被替换，且已用 preserved-contract regression 复验

本轮 focused evidence：

- workflow regressions：
  - `test_workflow_exports_rebuilt_epub_with_manifest_and_assets`
  - `test_workflow_exports_rebuilt_pdf_from_merged_html_substrate`
- API regression：
  - `test_rebuilt_epub_export_produces_document_level_epub_artifact`
- preserved-contract regressions：
  - `test_workflow_exports_merged_markdown_with_assets`
  - `test_workflow_exports_merged_markdown_from_legacy_db_without_document_images_table`
  - `test_merged_html_export_renders_structured_artifacts_with_special_modes`

当前仍明确保留的边界：

- `rebuilt_pdf` 仍不承诺 page-faithful rebuild
- PDF renderer unavailable 时必须显式 fail-closed
- `translate_full` executor 仍不把 rebuilt delivery 作为默认阶段出口

下一步 `MDU-15.1.3` 应只做：

1. 扩一轮 rebuilt delivery focused regression 证据
2. 同步 lane A / phase 3 治理状态
3. 明确 lane A 是否可以进入 wave-2 integration gate，而不是继续扩实现范围

## 13. MDU-15.1.3 Completion Snapshot

`MDU-15.1.3` 已完成，lane A 当前已经具备进入 wave-2 integration gate 前的最小证据闭环。

新增并确认的 focused evidence：

- positive-path regressions
  - `test_workflow_exports_rebuilt_epub_with_manifest_and_assets`
  - `test_workflow_exports_rebuilt_pdf_from_merged_html_substrate`
  - `test_rebuilt_epub_export_produces_document_level_epub_artifact`
  - `test_rebuilt_pdf_export_downloads_document_level_pdf_when_renderer_is_available`
- negative-path regressions
  - `test_export_service_rebuilt_epub_rejects_non_epub_source_document`
  - `test_workflow_rebuilt_pdf_fails_closed_when_renderer_unavailable`
  - `test_rebuilt_pdf_export_fails_closed_when_renderer_is_unavailable`
- preserved-contract regressions
  - `test_workflow_exports_merged_markdown_with_assets`
  - `test_workflow_exports_merged_markdown_from_legacy_db_without_document_images_table`
  - `test_merged_html_export_renders_structured_artifacts_with_special_modes`

本轮额外固化的事实：

- rebuilt delivery 不只是“能导出”，还已经覆盖：
  - canonical file naming
  - document-level download path
  - fail-closed renderer behavior
  - source guard rejection
- `rebuilt_pdf` 的用户可见下载契约已经被回归证明为 document-level `.pdf` artifact，而不是 zip 或 chapter bundle
- Lane A 这轮没有继续扩 executor 默认阶段，也没有把 rebuilt artifact 混入 Phase 1/2 主交付语义

lane A 当前结论：

- `lane-delivery-upgrade` 已完成 Phase 3 的本 lane MVP
- 当前 lane 已可视为 `accepted for wave-1`
- 下一默认 claim 应切换到 `lane-review-naturalness / MDU-17.1.1`

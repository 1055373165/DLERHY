# 06 · 导出

> 来源：2026-09-16 对分支 `refactor/p0-stabilize`（HEAD `2176d51`）的逐文件源码阅读。行号对应该基线；标注「待确认」的条目未经运行验证。总览与跨子系统结论见 [`00-overview.md`](00-overview.md)。

# book-agent 导出（export）子系统源码阅读笔记

> 阅读基线：分支 `refactor/p0-stabilize`，HEAD `2176d51`（2026-09-16）。所有路径相对 `/Users/smy/project/book-agent`。
> 标注「待确认」的地方是源码中没有直接证据、需要运行或查其它模块才能定论的推断。

## 0. 阅读范围与总览

| 层 | 文件 | 行数 | 职责 |
|---|---|---|---|
| 服务 | `src/book_agent/services/export.py` | 3196 | `ExportService`：门禁、render blocks 组装、七种导出类型的写文件/记录、资产（图片）落盘、`DocumentRenderer` 表 |
| 纯函数包 | `src/book_agent/export/alignment.py` | 306 | 译文选择（target map / sentence→target）、错位证据、`ALIGNMENT_FAILURE` issue 生成 |
| | `src/book_agent/export/render_repair.py` | 1834 | render block 后处理修补：代码/散文拆分、refresh-split 还原、列表/参考文献排版、相邻块合并、render_mode / artifact_kind 判定 |
| | `src/book_agent/export/code_text.py` | 1002 | 代码 vs 散文启发式、代码重排（reflow）、参考文献行拆分 |
| | `src/book_agent/export/markup.py` | 971 | render block → HTML / Markdown / 重建 EPUB XHTML；表格、公式、列表、图片引用 |
| | `src/book_agent/export/titles.py` | 120 | 章节标题解析、前言/附录识别 |
| | `src/book_agent/export/epub_assets.py` | 129 | 从源 EPUB 归档定位图片（含旧数据按图注回溯） |
| | `src/book_agent/export/pdf_crop.py` | 640 | PDF 图片裁剪几何、渲染倍率、内嵌原图探测、`DocumentImage` 物化计划 |
| | `src/book_agent/export/evidence.py` | 511 | manifest / review package 的证据 payload、用量统计 |
| | `src/book_agent/export/common.py` | 408 | 正则、常量、小工具 |
| | `src/book_agent/export/models.py` | 83 | `MergedRenderBlock` 等数据类 |
| | `src/book_agent/export/stylesheets.py` + `templates/*.css` | 16 + 122 | 内嵌 CSS |
| 用例 | `src/book_agent/application/export_use_case.py` | 357 | `DocumentExportUseCase`：用例层门禁 + 自动跟进、调用服务、CAS stamp |
| API | `src/book_agent/app/api/export_downloads.py` | 602 | 下载端点实现：路径解析（CAS / 自愈）、zip 打包、文件名 |
| | `src/book_agent/app/api/routes/documents.py` | 588 | `POST /export`（入队 run）、`GET .../exports/download`、`GET .../exports`、`GET .../exports/{id}` |
| 门禁依赖 | `src/book_agent/services/layout_validate.py` | 272 | 布局校验（标题/图/脚注/表格） |
| | `src/book_agent/services/rebuild.py`、`realign.py` | 557 / 267 | 门禁失败后 follow-up 动作的执行体（重建 packet / 重建对齐边） |
| 数据 | `src/book_agent/infra/repositories/export.py` | 293 | `ChapterExportBundle` / `DocumentExportBundle` 装载、exports 表查询 |
| | `src/book_agent/domain/models/review.py:120-164`、`document.py:216-237` | | `Export`、`DocumentImage` ORM |
| | `alembic/versions/20260422_0028_*`、`20260914_0032_*` | | 完整性列 + 路径 CHECK；删除未实现类型 |
| 存储 | `src/book_agent/infra/storage/blobs.py` | 182 | sha256 stamp + CAS 硬链接 |
| 运行时 | `src/book_agent/app/runtime/document_run_executor.py:601-680, 814-885, 1040-1100` | | export 阶段工作项、门禁失败处理 |
| | `src/book_agent/orchestrator/run_plan.py` | 110 | `EXPORT_FULL` run 的阶段计划 |

### 0.1 调用链一图

```
POST /v1/documents/{id}/export  (routes/documents.py:568-588)
  └─ _enqueue_document_run → RunControlService.create_run(EXPORT_FULL, run_request={export_type, auto_execute_followup_on_gate, max_auto_followup_attempts}) → 202
       └─ DocumentRunExecutor._process_export_stage (executor:601-670)  # 每种 export_type 是 run 的一个 stage（run_plan.py:67-76）
            └─ 工作线程 _execute_export_work_item (executor:814-885)
                 └─ DocumentWorkflowService.export_document (workflows.py:340-355)
                      └─ DocumentExportUseCase.export_document (export_use_case.py:68-83)
                           ├─ _pass_export_gate: 对每章 ExportService.assert_chapter_exportable（用例层门禁 + 可选自动跟进）
                           ├─ 整书类型 → ExportService.export_document_<type>(document_id)   # _DOCUMENT_LEVEL_EXPORTERS (export_use_case.py:36-42)
                           │    └─ _export_document(document_id, DocumentRenderer)   # export.py:232-264，再次 _enforce_gate 每章
                           ├─ 章节类型（bilingual_html / review_package）→ 逐章 ExportService.export_chapter   # export.py:396-406，再次 _enforce_gate
                           └─ _stamp_export_blobs: sha256 + 硬链接到 <artifact_root>/blobs   # export_use_case.py:85-98
GET /v1/documents/{id}/exports/download?export_type=…  → export_downloads.document_export_response（只读已有导出；404 时前端入队）
GET /v1/documents/{id}/chapters/{cid}/exports/download → export_downloads.chapter_export_response
CLI `export` 子命令 (cli.py:134) 与测试同步适配器 tests/document_actions.py:64-91 走同一 workflow.export_document。
```

同步调用方（CLI、测试适配器）在 `ExportGateError` 时**提交**事务以保留门禁写下的 issue（`tests/document_actions.py:88-91`；执行器 `document_run_executor.py:846-851` 同样先 `session.commit()` 再抛）。

---

## 1. 七种 ExportType 总表（问题 1）

`ExportType` 定义于 `src/book_agent/domain/enums.py:252-259`；DB CHECK 与之同步（迁移 `20260914_0032`，删除了 `bilingual_markdown` / `zh_pdf` / `jsonl`）。API 请求体 `ExportDocumentRequest.export_type` 是 `Literal[...]` 白名单（`src/book_agent/schemas/workflow.py:203-213`）。

| ExportType | 粒度 | 入口 | 渲染器 | 输出文件（相对 `<export_root>/<document_id>/`） | manifest | 资产处理 | CSS | 外部依赖 | 边界 / 前置 | 状态副作用 |
|---|---|---|---|---|---|---|---|---|---|---|
| `bilingual_html` | 章 | `export_chapter` → `_export_files`（export.py:421-433） | `_build_bilingual_html`（2350-2400）+ `markup.render_block_html` | `bilingual-<chapter_id>.html` | `bilingual-<chapter_id>.manifest.json`（`_build_bilingual_manifest` 829-878） | `_export_epub_assets_for_chapter_bundle`（2653-2672）：EPUB 源从归档拷到 `assets/<flat>`；PDF 源裁剪到 `assets/pdf-images/<block>.<ext>`；已持久化 `DocumentImage` 复制到 `assets/document-images/` | `bilingual_chapter.css` + `usage_summary.css` | KaTeX CDN（jsdelivr，2380/2393）；PDF 裁图需 `fitz` | 章状态 ∈ {qa_checked, approved, exported}（`_FINAL_EXPORT_CHAPTER_STATUSES` :154） | 章 → `EXPORTED`；文档 → 全部章 EXPORTED 则 `EXPORTED` 否则 `PARTIALLY_EXPORTED`（754-771） |
| `review_package` | 章 | 同上（415-420） | `_build_review_package`（785-827） | `review-package-<chapter_id>.json` | 无（manifest_path=None） | 无 | 无 | 无 | 章状态 ∈ {translated, qa_checked, review_required, approved, exported}（:147-153） | 无（但门禁会写 `ALIGNMENT_FAILURE` issue，见 §4.4） |
| `merged_html` | 整书 | `export_document_merged_html` → `_export_document` | `_render_merged("_build_merged_document_html")`（3065-3072；1315-1358） | `merged-document.html` + 人类可读别名 `《<译名或原名>》-中文阅读稿.html`（`_write_document_export_alias` 333-349） | `merged-document.manifest.json`（`_build_merged_document_manifest` 1510-1565） | `_export_epub_assets_for_document_bundle`（2402-2429），同上但整书 | `merged_document.css` + usage | KaTeX CDN | 每章都要过门禁 | 所有章 → EXPORTED、文档 → EXPORTED（773-783）；另外派生 `document.title_tgt`（`_sync_document_title_tgt` 266-289，仅 EPUB 源） |
| `merged_markdown` | 整书 | 同上 | `_build_merged_document_markdown`（1360-1392）+ `markup.render_block_markdown` | `merged-document.md` + 别名 `《…》-中文阅读稿-Markdown.md` | `merged-document.markdown.manifest.json` | 同 merged_html | 无 | 无 | 同上 | 同 merged_html |
| `rebuilt_epub` | 整书 | `export_document_rebuilt_epub` | `_render_rebuilt_epub` → `_write_rebuilt_epub`（2431-2527）+ `markup.build_rebuilt_epub_chapter_xhtml` | `rebuilt-document.epub` | `rebuilt-document.epub.manifest.json`（`_build_rebuilt_document_manifest` 1567-1608） | 先跑整书资产导出；把 `<output_dir>/assets/**` 全部打进 `OEBPS/assets/`（2478-2486, 2522-2523） | `rebuilt_epub.css` 写入 `OEBPS/styles/book.css` | 无 | **仅 EPUB 源**（`epub_source_only_error` 3168）；`uses_upstream_exports=True` → 先确保 merged_html 与 merged_markdown 存在（否则触发导出，358-388） | **无**（`apply_status_updates=None`），但被牵连的上游 merged 导出会改状态 |
| `zh_epub` | 整书 | `export_document_zh_epub` | `_render_zh_epub` → `_write_source_preserving_epub`（2529-2567） | `zh-document.epub` | `zh-document.epub.manifest.json` | 不导出资产：直接逐条复制源 EPUB 归档所有条目（2560-2567），只改 `.xhtml/.html/.htm` | 无（保留源 CSS） | 需源 EPUB 文件仍在 `document.source_path`（2534-2536） | **仅 EPUB 源**（3185）；`syncs_document_title=False` | 所有章 → EXPORTED、文档 → EXPORTED（2621-2629） |
| `rebuilt_pdf` | 整书 | `export_document_rebuilt_pdf` | `_render_rebuilt_pdf` → `_render_rebuilt_pdf_from_html`（2631-2651） | `rebuilt-document.pdf` | `rebuilt-document.pdf.manifest.json` | 复用 merged_html 及其 `assets/`（file:// 相对路径） | 走 merged_html 的 CSS（`@media print` 规则 merged_document.css:56） | **Playwright chromium**，`page.pdf(format="A4", print_background=True)`；KaTeX 走 CDN | 不限源类型；`uses_upstream_exports=True`（同样确保 merged_html + merged_markdown） | 无 |

要点：
- 「译文选择规则」（所有类型共用）：`alignment.build_target_map` 只排除 `final_status == SUPERSEDED` 的 `TargetSegment`（alignment.py:35-40）；`sentence_target_map` 按 alignment edge 收集候选，多个候选时 `preferred_target_ids_for_sentence` 用 `translation_run_rank_map` 排序 `(run.updated_at or created_at, packet_type 优先级 translate<review<retranslate, attempt)`，**只保留最高秩 run 的 segment**（alignment.py:63-119）。即「最新 run 赢」，且 review/retranslate 包在同一时间戳下优先。`DRAFT`/`REVIEW_REQUIRED`/`FINALIZED` 都会被渲染——`TargetSegmentStatus` 不参与选择。
- 输入表：`ExportRepository.load_chapter_bundle`（repositories/export.py:158-247）装载 `Chapter, Document, Block(ACTIVE), DocumentImage(按 block_id), Sentence, TranslationPacket, TranslationRun, TargetSegment, AlignmentEdge, ReviewIssue, BookProfile(最新版本), ChapterQualitySummary, MemorySnapshot(ACTIVE: 本章 brief + 全局 termbase/entity), AuditEvent(按 chapter/packet/snapshot id)`。`load_document_bundle` = 逐章调用（249-261）。
- 每种类型的「输入版本包」写进 `exports.input_version_bundle_json`（`_record` 436-477 / `_record_document_export` 479-520），字段见 §5.2。

---

## 2. 数据流与事务边界

### 2.1 用例层（`application/export_use_case.py`）
- `export_document`（68-83）= `_write_document_export` + `_stamp_export_blobs`。
- `_pass_export_gate`（152-263）：循环 `bootstrap_repository.load_document_bundle` → 对每章 `export_service.assert_chapter_exportable(chapter_id, export_type)`（会**写** issue/action，见 §4）。若 `auto_execute_followup_on_gate`：取 `exc.followup_actions` 中未尝试的，经 `issue_actions.split_actions_by_manual_hold` 过滤，按预算 `max_auto_followup_attempts` 执行 `issue_actions.execute_action(action_id, run_followup=…)`，每次记 `export.auto_followup.executed` 审计（286-322）；停止原因 `no_followup_actions | no_new_actions | manual_hold_required | max_attempts_reached` 记 `export.auto_followup.stopped`（324-357）并包装进新的 `ExportGateError`（265-284）。
- `_write_document_export`（100-150）：整书类型按 `_DOCUMENT_LEVEL_EXPORTERS`（36-42）反射调用服务方法；章节类型逐章 `export_chapter`。**注意**：使用 `bootstrap_repository.load_document_bundle` 的章序，而服务内部再次用 `ExportRepository.load_document_bundle` 装载。
- `_stamp_export_blobs`（85-98）：flush 后取该 document + export_type 的**所有** SUCCEEDED 记录做 `stamp_export_records`（每次导出都对同类型所有章节文件重新 sha256 + 硬链接）。

### 2.2 服务层事务
- `ExportService` 从不 `commit`；只 `merge`/`flush`（export.py:259, 405, 950, 955）。提交由外层 `session_scope`（执行器 / CLI / 测试适配器）完成。
- 文件写在 DB 提交之前，且非原子（`Path.write_text` / 直接在目标路径写 zip：413-433, 2506-2523, 2560）。崩溃窗口：文件已在磁盘但行未提交，或 zip 写到一半。
- `_export_document` 顺序：门禁 → （可选）上游导出（会**递归**进入 `_export_document` 并 `save_export`）→ 标题同步 → 写文件 → 写 manifest → `save_export`（merge） → 状态更新 → flush。若 `renderer.render` 抛异常（如 Playwright 缺失，2635/2649），上游 merged 导出已 merge 到 session，随外层事务决定去留：执行器在 `ExportGateError` 时 `commit()`（executor:846-851），因此 rebuilt_pdf 失败也会**留下** merged_html/markdown 记录与文件。
- `Export.id = stable_id("export", document_id, [chapter_id], export_type)`（446, 490）→ 每 (文档, 章, 类型) **只有一行**，`save_export` 是 `session.merge`（repositories/export.py:292-293），`created_at` 每次重置为 now（475, 518）。没有导出历史/版本，只有「最后一次」。

### 2.3 运行时（执行器）
- `run_plan.plan_for_run`（run_plan.py:67-76）：`EXPORT_FULL` 的 stages 只有一个 = `export_type.value`；`auto_followup_on_export_gate` 默认 **False**（API 请求默认），`TRANSLATE_FULL` 的 `FULL_PIPELINE_STAGES = (translate, review, bilingual_html, merged_html)`（run_plan.py:20）且 `auto_followup_on_export_gate=True`。
- `_process_export_stage`（executor:601-670）：每个 export stage 播种一个 `WorkItem(scope_type=EXPORT, scope_id=stable_id("document-run-export", run_id, type))`，领取后在独立线程执行 `_execute_export_work_item`。
- `_execute_export_work_item`（814-885）：`ExportGateError` → 先 `assert_lease_held` + `commit`（保留门禁 issue）再抛；失败分类进 `_handle_work_item_failure`（1040-1100）：`error_detail["export_gate"] = exc.to_http_detail()` 写进工作项与 pipeline stage 缓存（`status_detail_json`），run 最终按 `classify_failure` 决定 retryable / paused / failed（`ExportGateError` 是 `ValueError` 子类，分类结果待确认——`workers/failures.classify_failure` 未在本次范围内）。
- 门禁失败对 run 的影响：工作项 `TERMINAL_FAILED` 或 `RETRYABLE_FAILED`（1058-1064），`_process_export_stage` 看到 TERMINAL_FAILED 返回 False（636-637）→ 该 stage 失败；`TRANSLATE_FULL` 中 export 是必需阶段（PLAN P2 决策），run 进 FAILED。

---

## 3. 渲染管线（问题 2）

### 3.1 三层模型
1. **DB 层**：`Block`（`block_type`, `source_span_json` = 解析期元数据 + 修复期 `repair_*` 键）、`Sentence`（`translatable`, `sentence_status`）、`TargetSegment`（`text_zh`）、`AlignmentEdge`。
2. **render block 层**：`MergedRenderBlock`（export/models.py:60-74）= 一个块的最终呈现单元：`block_type`（可被 `repair_block_type` 覆盖）、`render_mode`、`artifact_kind`、`source_text`、`target_text`、`source_metadata`、句子/segment id、`is_expected_source_only`、`notice`。由 `ExportService._build_render_blocks_for_chapter`（export.py:1768-1985）组装，并经 `_within_render_model_scope` 装饰器按 bundle 对象缓存（177-195, 1758-1766）。
3. **标记层**：`markup.render_block_html / render_block_markdown / render_block_rebuilt_epub_xhtml` 把 render block 序列化；整书层由 `_visible_merged_chapters` 决定章序/分组/去重。

### 3.2 `_build_render_blocks_for_chapter` 逐步（export.py:1768-1985）
1. `alignment.build_target_map` / `sentence_target_map`（译文选择）；`resolve_artifact_group_context_ids`（`domain/structure/artifact_grouping.py:161`，把图/表附近的上下文块归组）。
2. 句子按 `(block_id, ordinal_in_block)` 排序分组。
3. 逐块：
   - 跳过：`repair_hidden_from_export`（1787）、`pdf_block_role ∈ {header, footer, toc_entry}`（1789）、带 `caption_for_block_id` 的 CAPTION（1791，图注并入图块）、以及此前被 `skipped_block_ids` 收进的块（linked caption、artifact group 上下文、`repair_skip_block_ids`）。
   - `effective_block_type = repair_block_type or block.block_type`（render_repair.py:1599-1606）。
   - `render_mode_for_block`（render_repair.py:1638-1681）判定顺序：可翻译的「散文型工件」→ `zh_primary_with_optional_source`；有 `image_src` → `image_anchor_with_translated_caption`；CAPTION 且形如 "Figure/Fig./Image/Diagram/Chart" → 同上；形如 URL/路径/常量的参考字面量 → `reference_preserve_with_translated_label`；code-like（`is_code_like_block` 1564-1596：CODE 类型，或 PARAGRAPH/TABLE 带 `pdf_block_role=code_like` 或 `looks_like_code_artifact_text`）→ `source_artifact_full_width`；CODE → 同；TABLE → `translated_wrapper_with_preserved_artifact`；`protected_policy=protect` → `source_artifact_full_width`；`mixed` 或任一句不可译/PROTECTED → `zh_primary_with_inline_protected_spans`；否则 `zh_primary_with_optional_source`。
   - 图/表与 linked caption 合并（1806-1899）：IMAGE/FIGURE + linked CAPTION → `image_anchor_with_translated_caption`，源文本改为图注，target 取图注句 + 组上下文句；TABLE/EQUATION + CAPTION → `translated_wrapper_with_preserved_artifact`，把图注文本放进 `linked_caption_text`。
   - `normalize_pdf_body_render_texts`（render_repair.py:58-136，仅 PDF 且 `zh_primary_with_optional_source`）：标题按句子重新拼接（去软换行）；段落/引用按 target 分组恢复段落分隔（`should_restore_pdf_paragraph_breaks` 1797-1807），写 recovery flag `export_pdf_paragraph_breaks_restored` / `export_pdf_source_soft_wrap_normalized`。
   - `markup.join_block_target_text`（markup.py:328-348）：优先结构化拼接（列表换行 / 参考文献条目分段），否则散文型块内联拼接（`inline_join_target_text`，ASCII 相邻加空格），否则 `\n`。
   - `repair_target_text` 覆盖 target（1920-1922）；`artifact_kind_for_block`（render_repair.py:1684-1731）：image/figure/equation/code/table/reference/protected_artifact/None。
   - 构造 `MergedRenderBlock`，`is_expected_source_only` = render_mode ∈ 四种保留模式（1944-1949），`notice = markup.source_only_notice`（中文提示语，markup.py:304-325）。
   - 就地合并：相邻标题碎片（`should_merge_adjacent_heading_render_blocks` render_repair.py:971-1024，条件：同页、body 页族、阅读序相邻、续行特征、bbox 距离 ≤36pt/横向 ≤48pt）；散文后接被判为 code/table-like 但已有译文的续行块（1331-1392）；相邻 code 块（1157-1170，页码差 ≤1）且都不带 refresh-split 片段。
4. 收尾：学术论文 lane → `normalize_academic_paper_render_blocks`（第 1 章拆 Abstract，render_repair.py:139-148, 851-937）；书籍 PDF → `_normalize_book_pdf_render_blocks`（export.py:1996-2038）；EPUB 直接返回（**EPUB 不经过任何 repair 后处理**）。

### 3.3 `render_repair`「后处理修补」清单
按调用顺序（书籍 PDF 路径 `_repair_book_pdf_render_blocks` export.py:2040-2083 + `_normalize_book_pdf_render_blocks`）：

| # | 函数（render_repair.py 行） | 触发条件 | 效果 / recovery flag |
|---|---|---|---|
| R1 | `should_drop_book_heading_label`（577-588） | HEADING 文本形如 "Chapter N"（`_PURE_CHAPTER_LABEL_PATTERN`）且等于章标题或不是首块 | 整块丢弃 |
| R2 | `should_clear_suspicious_short_source_target_text`（730-733）→ `drop_render_block_target_text` | PARAGRAPH/QUOTE，源是 3–10 词短句而译文 ≥ max(80, 4×源长) | 清空译文，flag `export_book_short_source_target_cleared` |
| R3 | `should_promote_book_block_to_code`（686-701）→ `replace_render_block_as_code` | HEADING/PARAGRAPH/TABLE，非 references 页族，`looks_like_code_artifact_text` 或单行 codeish | 改为 CODE/`source_artifact_full_width`，flag `export_book_code_promoted`，notice「代码保持原样」 |
| R4 | `should_demote_book_code_block_to_paragraph`（704-717）→ `replace_render_block_as_paragraph` | code 工件但文本像参考文献 / 短散文 / 散文 | 改为 PARAGRAPH，flag `export_book_code_demoted`；references 页族用 `join_reference_target_segments` 恢复条目换行（export.py:2059-2069）；可能顺带丢译文（`should_drop_demoted_book_code_target_text` 720-727） |
| R5 | `should_demote_book_heading_with_prose_target`（778-791） | HEADING 的译文像散文（`looks_like_prose_title_text` 1769-1794：≥2 句末标点、或长且多子句） | 降为段落，flag `export_book_heading_target_demoted` |
| R6 | `should_demote_book_heading_to_paragraph`（739-775） | HEADING 源文本：references 页族非允许标题；"Chapter N x…" 小写尾；小写开头且 ≥4 词；≥10 词含动词；≥14 词；≥8 词且句末标点。带字体依据 flag（`_FONT_EMPHASIS_HEADING_FLAGS` 736）豁免 | 降为段落，flag `export_book_heading_demoted` |
| R7 | `repair_collapsed_list_target_text`（591-624） | PARAGRAPH/QUOTE，源文本 ≥2 个列表行，译文行数不足 | 按 `split_inline_list_target_lines`（672-683）拆译文并按源缩进层级重排（`apply_list_line_layouts`），flag `export_book_list_target_layout_restored` |
| R8 | `split_mixed_book_code_prose_render_block`（151-285） | code 工件内首/尾夹有散文行组 | 拆成 `::leading-prose` / code / `::trailing-prose` 三块；散文块译文来自本块译文或持久化的 `mixed_code_prose_repair_targets`（按 `source_signature` 匹配）；flag `export_mixed_code_prose_split` |
| R9 | refresh-split 还原（export.py:2092-2238 + render_repair.py:288-574） | 块 `source_metadata.refresh_split_render_fragments`（由 `services/pdf_structure_refresh.py:321-336` 在结构刷新时保留） | 代码块后接的片段：标签式散文还原为段落（`restore_labeled_prose_refresh_split`）；代码前缀拆分（`split_leading_code_prefix_from_refresh_fragment`）；代码续行并回（`restore_code_refresh_split`）；否则每个片段生成 `::refresh-split::<i>` 子块，散文片段译文可由父块译文的 CJK 部分推断（`infer_refresh_split_fragment_target_text` 550-574，仅父块首行是 shell 命令时）；flags `export_refresh_split_*` |
| R10 | `should_bridge_code_blocks_across_inline_artifact`（1173-1232）→ `merge_code_blocks_across_inline_artifact` | code – 无图注小图 – code 三连，几何上位于代码列内 | 合并两段代码并**隐藏中间图**（`suppressed_artifact_block_ids`），flag `export_inline_image_between_code_suppressed` |
| R11 | `should_merge_adjacent_code_blocks` → `merge_adjacent_code_render_blocks`（1073-1155） | 相邻 code 工件 | 拼接，去重跨页重复行（前缀 ≥4 行或尾/首重叠 ≥3 行），flags `export_code_blocks_merged` / `export_code_overlap_deduped` |
| R12 | `should_merge_adjacent_book_paragraph_fragments`（1262-1329） | 相邻 PARAGRAPH，同页族 body/frontmatter，页/阅读序相邻，后块小写开头，或任一块是被降级标题且前块无句末标点 | 合并，flag `export_book_paragraph_fragments_merged` |
| R13 | `normalize_reference_listing_render_blocks`（1395-1555） | 存在 `pdf_page_family=references` 块，且在允许的参考文献标题之后 | 把连续参考条目块按编号/URL 重新切成 `::reference-entry::` / `::reference-locator::` 段落，译文按编号队列配对，URL 行译文=原文，flag `export_reference_listing_normalized` |
| R14 | `split_academic_paper_frontmatter_block`（851-908，仅学术论文第 1 章） | 段落含 "Abstract" | 拆 作者块 / "摘要" 标题 / 摘要正文 |
| R15 | `normalize_pdf_body_render_texts`（58-136，组装期） | 见 §3.2 | 段落分隔恢复、软换行归一 |
| R16 | 标题合并（971-1055）、散文续行合并（1331-1392） | 组装期 | 见 §3.2 |

**修补层的边界**：全部只作用于 render block（内存），不回写 DB；所有判定基于文本正则/统计启发式（`code_text.py`），没有字体/位置信息（除 bbox 距离与 `_FONT_EMPHASIS_HEADING_FLAGS`）。`services/pdf_prose_artifact_repair.py` 是**会写 DB** 的兄弟模块：它复用 `ExportService` 的私有方法（`pdf_prose_artifact_repair.py:94, 324, 472, 682, 703, 712, 806`）并把结果写成 `repair_source_text / repair_target_text / repair_block_type / repair_skip_block_ids / mixed_code_prose_repair_targets`（251-261, 408, 581），导出时由 R8/`effective_export_block_type`/`is_translatable_prose_artifact_block`（1609-1635，`repair_target_text` 存在即视为可翻译散文）消费。

### 3.4 `code_text.py` 要点
- `looks_like_code_artifact_text`（341-404）：≥2 行；排除参考文献、术语表定义、包裹散文、学术前言/散文；按行打分（shell 命令 3、结构化数据 2、关键字 3、`#`/`@` 2、赋值 2、`print(`/`->`/`ChatPromptTemplate`/`Runnable`/`llm` 1、括号+运算符 1），≥2 注释行 + ≥2 强线索直接判代码；≥2 散文句且无强线索判非代码；`score ≥ 4 且 strong ≥ 1`。**带有具体框架名（LangChain/ADK）的硬编码 token**（common.py:372, code_text.py:235, 378）。
- `looks_like_prose_artifact_text`（94-132）：停用词密度 + 句末标点 + 列表行。
- `reflow_code_artifact_text`（468-503）：对被 PDF 拍平的代码重新缩进（Python 冒号块 / C 家族大括号 / Ruby do-end 等，`opens_python_block` 802-815, `is_top_level_code_reset` 772-799），先 `expand_inline_call_argument_lines` 拆关键字参数、`coalesce_wrapped_code_lines` 合并被折行的语句（引号状态机 `code_string_state` 638-681）。触发：`should_reflow_code_artifact_text`（292-314）—— 无缩进、非稳定结构化布局、且存在 ≥110 字符行或可合并行或冒号块后无缩进。
- `should_preserve_code_artifact_layout`（82-91）：`cross_page_repaired` / `export_refresh_split_code_restored` flag 或合并后 ≥8 行时不重排。
- `split_reference_listing_lines`（938-970）：在编号 "N." / URL / doi / arxiv 前断行，再合并 URL 续行与标题续行。

### 3.5 `markup.py` 渲染规则
HTML（`render_block_html` 456-604）：

| render_mode / 类型 | 输出 |
|---|---|
| `source_artifact_full_width` + image/figure 且有 asset | `<section class='block artifact <type>'>` + note + `<figure><img src=assets/… alt=image_alt><figcaption>源文本</figcaption></figure>`；占位文本 `""/[Image]/[Figure]`（`ARTIFACT_PLACEHOLDER_TEXTS` :34）不作图注 |
| … + equation | `render_equation_html_metadata_aware`（669-684）：`equation_render_mode=latex` 且有 `equation_latex` → `.math-block.katex-display > .katex-source`；否则 `render_math_html`（748-765）按 LaTeX 标记猜测，否则 `<pre>` |
| … + code | `<pre><code>` + `format_preformatted_text`（624-629）= `normalize_code_artifact_text`（含 reflow）+ escape |
| … 其它 | `<div class='artifact-body'>源文本</div>` |
| `translated_wrapper_with_preserved_artifact` | `<div class='zh'>译文</div>` + note + 表格（`render_table_html_metadata_aware` 687-703：优先 `table_markdown` 元数据 → `markdown_table_to_html`；否则 `render_structured_table_html` 632-666，从 `|`/制表/双空格切列，数值列右对齐，`data-source-text` 属性带原文）或 `<div class='artifact-body'>` |
| `image_anchor_with_translated_caption` | 有 asset → `<figure><img>`；无 asset → 显示 `Source:`/`Alt:` 元数据行；随后 `<div class='zh'>译注</div>` + note + `.artifact-source-caption` 原图注 |
| `reference_preserve_with_translated_label` | 译文（若与原文不同）+ note + 原文 |
| TABLE（非保留模式） | `render_structured_table_html(target or source)` |
| 其它（heading/paragraph/list_item/quote/caption/footnote） | `<section class='block <type>'><div class='zh'>译文或原文</div><details><summary>Source</summary>…</details></section>`；`include_source_toggle=False` 时（merged）不出 details；LIST_ITEM 首字符是圆点/短横 → 去掉并加 `bulleted` 类（591-598，CSS 用 `::before` 画悬挂圆点） |

`format_inline_text`（607-621）：按行 escape，`<tag>` 样式文本包成 `<code class='inline-token'>`，前导空格转 `&nbsp;`，行间 `<br/>`。**没有** Markdown/强调/链接解析。

Markdown（`render_block_markdown` 39-163）：图 → `![alt](path)` + `*源图注*`；equation → `$$…$$` 或 ```` ``` ````；code → fenced（语言猜测 `markdown_language_for_artifact` 290-301：json/python/tex/text）；table → `table_markdown` 或 `markdown_table_from_source_text`；HEADING → `### `（**固定三级**，>150 字符当正文）；LIST_ITEM → `- `（已有 `-`/`1.` 前缀则不加）；CAPTION → `*…*`；QUOTE → `> `；多行列表 → `markdown_list_text`（185-202）按层级 3 空格缩进。**merged markdown 不输出原文**（160-163 注释说明）。脚注（FOOTNOTE）在三种渲染里都只是普通段落/`<section class='block footnote'>`，无脚注编号/回链处理。

重建 EPUB XHTML（`render_block_rebuilt_epub_xhtml` 827-908）：与 HTML 类似但用 `<h2>/<p>/<blockquote>/<p class='caption'>`，非保留块附 `<div class='source-note'>Source: …</div>`（**重建 EPUB 每段都带英文原文**，与 merged 的「中文-only」定位不一致）；LIST_ITEM 渲染为 `<p>`（无 `<ul>`）。

### 3.6 整书章节可见性与分组（`_visible_merged_chapters` export.py:1654-1756）
- 每章：render blocks + `titles.resolved_chapter_title_text`（titles.py:41-80：首个有内容块若是 HEADING 且译文不像散文则用其译文，否则 `chapter.title_tgt or title_src`，PDF 再做前言词本地化 `_FRONTMATTER_TITLE_TRANSLATIONS`）。
- 跳过：无标题且无块；`should_skip_merged_frontmatter_chapter`（titles.py:26-38：前言类标题且无可译句且全是图锚点）；EPUB 同 href 重复（1672）；标题+源文本签名完全重复（1681）。
- EPUB / 学术论文：按序编号。书籍 PDF：按 "Chapter N"/"Appendix"/前言标题分组，非起始章并入前一组（1701-1746）——目的是把 PDF 切碎的小节合回真实章。
- `_merged_body_blocks`（1426-1448）：丢掉与章标题同文的首个 HEADING、丢掉纯语言标签段落（"python"、"bash" 等 `_CODE_LANGUAGE_LABELS` 1450-1463）。

### 3.7 输出结构（来自 golden 快照 `tests/golden/exports/*.json`）
双语章节 HTML（epub 夹具 `bilingual-<id2>.html`）：
```
<html><head><meta charset><meta viewport><title>ZH::Chapter One</title><style>{bilingual_chapter.css}{usage_summary.css}</style><link katex.css></head>
<body><main class='page'>
  <header class='hero'><div class='hero-kicker'>Chapter Export</div><h1>译标题</h1><div class='source-title'>原标题</div></header>
  <section class='usage-summary'>翻译统计：翻译调用/Token/费用/延迟/最近一次调用</section>
  <section class='chapter'>
    <section class='block artifact image-anchor figure'><div class='artifact-body'><figure><img src='assets/agent-loop.png'…></figure></div><div class='zh'>ZH::Figure 1.1 …</div><div class='artifact-note'>图片锚点保留</div><div class='artifact-source-caption'>Figure 1.1 …</div></section>
    <section class='block artifact table'><div class='artifact-note'>保留原始结构…</div><div class='artifact-body artifact-table-body'><div class='artifact-table-shell' data-source-text='…'><table class='artifact-table'>…</table></div></div></section>
    <section class='block artifact code'><div class='artifact-note'>公式保持原样</div><div class='math-block equation-text'><pre>x=1</pre></div></section>
    <section class='block artifact reference paragraph'><div class='zh'>ZH::https://…</div><div class='artifact-note'>参考标识保留</div><div class='artifact-body'>https://…</div></section>
  </section></main><script katex.js></script><script>katex.render…</script></body></html>
```
（该夹具的正文段落全被识别为工件，所以看不到普通段落；普通段落形态为 `<section class='block paragraph'><div class='zh'>译</div><details><summary>Source</summary><div class='source'>原</div></details></section>`，见 markup.py:599-604。）

merged Markdown（pdf 夹具 `merged-document.md`）：
```
# golden-export

> **翻译统计**  
> - 翻译调用: 6 次（成功 6）  
> - … 

## Chapter 1

ZH::The introductory paragraph …

![Figure 1.1: Data flow …](assets/pdf-images/<id>.png)

ZH::Figure 1.1: …
ZH::The discussion continues …      ← 图注译文与后文因 artifact group 上下文被并进同一块

## ZH::Chapter 3. Worked Example

ZH::The following Python snippet … ZH::Read it line by line …

```python
def running_sum(values):
   total = 0
   …
```
```
epub 夹具的 merged markdown 出现两次 `## ZH::Chapter One`（第二章 XHTML 的正文标题是 "Chapter One"，`resolved_chapter_title_text` 用正文首标题覆盖了 nav 标题 "Chapter Two"）。

重建 EPUB：`mimetype`(stored) / `META-INF/container.xml` / `OEBPS/content.opf`（dc:language 固定 `zh-CN`，`dc:identifier`=document_id，manifest 含 nav/css/每章/所有 assets 文件） / `OEBPS/nav.xhtml` / `OEBPS/styles/book.css` / `OEBPS/text/chapter-001.xhtml` … / `OEBPS/assets/**`。chapter-001.xhtml 里 `<h1>ZH::Chapter One</h1><p class='chapter-meta'>Source title: Chapter One</p><h2>ZH::Chapter One</h2>…`——标题重复（见 §10 B-07）。

manifest（bilingual）：`chapter_id, chapter_title, export_type, html_path, quality_summary, pdf_page_evidence, pdf_image_evidence, pdf_preserve_evidence, version_evidence{document, chapter, book_profile, active_snapshots, packet_context_versions}, recent_repair_events, export_auto_followup_evidence, export_time_misalignment_evidence, row_summary, issue_summary, render_summary`。
manifest（merged/rebuilt）：`document_id, title, title_src, title_tgt, author, export_type, output_path, chapter_count, pdf_image_summary, translation_usage_*, issue_status_summary, render_summary, chapters[{chapter_id, ordinal, source_ordinal, title_src, status, block_count, sentence_count, render_block_count, quality_summary}], html_path|markdown_path|epub_path|pdf_path`；rebuilt 再加 `source_type, contract_version, renderer_kind, derived_from_exports, derived_export_artifacts, expected_limitations`。


---

## 4. 门禁（问题 3）

### 4.1 三段式（export.py:662-752）
`_enforce_gate = evaluate_chapter_gate（只读）→ sync_gate_issues（写）→ _raise_for_gate（抛）`。

`evaluate_chapter_gate`（667-686）：
1. 状态门：`review_package` 允许 {translated, qa_checked, review_required, approved, exported}；其它类型只允许 {qa_checked, approved, exported}（147-154）。不满足 → `status_blocked=True`，不再算别的。
2. 对齐门：`alignment.build_export_alignment_issues`（alignment.py:190-297）——先算 `ExportMisalignmentEvidence`（137-187）：
   - `missing_target_sentence_ids`：可译且非 BLOCKED 的句子没有被选中的译文；
   - `sentence_ids_with_only_inactive_targets`：其中有 edge 但 edge 全指向 SUPERSEDED segment 的；
   - `orphan_target_segment_ids`：活跃 segment 中属于「被选中 run」却没被任何句子渲染的（**只在首选 run 内找孤儿**，测试 `test_export_misalignment_ignores_orphan_targets_from_non_preferred_translation_runs`）；
   - `inactive_target_segment_ids_with_edges`：edge 指向的 SUPERSEDED segment（仅证据，不阻断）。
   - 按 packet 分桶，任一 packet 有前三类之一 → 生成 `ReviewIssue(issue_type="ALIGNMENT_FAILURE", root_cause_layer=EXPORT, severity=HIGH, blocking=True, suggested_action=REALIGN_ONLY, id=stable_id(…, packet.id, "ALIGNMENT_FAILURE", "export"))`。
   - `review_package` 或有对齐问题 → 到此为止（layout 不算）。
3. 布局门：仅 `_is_pdf_document`（common.py:167-172）时 `_plan_export_layout_issues`（912-929）→ `_build_export_layout_issue`（970-1027）用 `LayoutValidationService.validate_chapter(bundle, render_blocks)`，有问题则合成**一条**章级 `ReviewIssue(issue_type="LAYOUT_VALIDATION_FAILURE", root_cause_layer=STRUCTURE, severity=max, blocking=True, suggested_action=REPARSE_CHAPTER, evidence_json.reason="export_layout_validation", layout_issues=[…])`。

`sync_gate_issues`（688-705）→ `_apply_export_issue_plan`（931-961）：把上一轮同类 issue（`existing`，按 issue_type + root_cause_layer [+ reason] 过滤，901-929）中这次没再出现的、状态 OPEN/TRIAGED 的置为 RESOLVED 并写 `resolution_note`；新 issue `merge`；对每个新 issue `rule_engine.build_issue_action`（orchestrator/rule_engine.py:112）生成 PLANNED `IssueAction`（ALIGNMENT_FAILURE→`REALIGN_ONLY`（packet 作用域）或 `RERUN_PACKET`；STRUCTURE→`REPARSE_CHAPTER`（chapter 作用域））；同步更新 `bundle.review_issues`。issue id 稳定 → 重复导出不会堆积重复行，但会**反复把 RESOLVED 的旧 issue 重新 merge 成 OPEN**（同 id 新对象 status=OPEN）。

`_raise_for_gate`（707-752）：
- review_package：只在 status_blocked 时抛。
- 其它：status_blocked → 抛（附当前 OPEN blocking issue 的 PLANNED action 作为 followup，643-660）；alignment/layout 本轮有 issue → 抛（followup = 刚生成的 action）；最后 `repository.has_open_blocking_issues(chapter_id)`（任何来源的 OPEN 且 blocking issue，含 review 阶段的）→ 抛。

### 4.2 `layout_validate.py` 检查什么
`LayoutValidationService.validate_chapter`（54-76）对 render blocks 逐块：
- `HEADING_EMPTY`（HIGH）：标题规范化后为空；`HEADING_LEVEL_SKIP`（HIGH）：`heading_level/source_heading_level/epub_heading_level/pdf_heading_level` 或 `tag=hN` 推出的级别比前一标题跳 >1 级（78-120）。
- `FIGURE_ASSET_MISSING`（HIGH）：`artifact_kind ∈ {image, figure}` 且既无 `image_src` 又无合法 `source_bbox_json.regions[0]{page_number,bbox}`（122-140, 236-258）。
- `FOOTNOTE_EMPTY`（HIGH）/`FOOTNOTE_ANCHOR_ORPHANED`（MEDIUM，`footnote_anchor_matched is False`）（142-174）。
- `TABLE_EMPTY`（HIGH）/`TABLE_STRUCTURE_UNRENDERABLE`（HIGH）：表格源文本不含 `<table>` 且 `markup.parse_structured_table_rows` 解析失败（176-214, 260-269）——与渲染器共用同一解析器，保证「门禁拒绝的 = 渲染不出来的」。
- 所有 issue `blocking=True`（默认）。它只在 PDF 文档、且对齐干净时运行（export.py:684-685）。注意：`_validate_figure` 不看 `image_path`（解析期物化文件）——`_has_image_asset` 只认 `image_src`（layout_validate.py:236-238）；PDF 图块通常靠 bbox 通过。

### 4.3 门禁失败对 run / 数据的影响
- 写入：ALIGNMENT_FAILURE / LAYOUT_VALIDATION_FAILURE issue + PLANNED action 进 DB（调用方 commit）。执行器把 `exc.to_http_detail()`（export.py:122-145）写进工作项失败详情与 stage 缓存（executor:1056-1057, 1092）。
- 自动跟进（用例层）：执行 `IssueActionWorkflow.execute_action` → `RerunService`：`REALIGN_ONLY` 走 `RealignService.execute`（realign.py:40-94：取 packet **最新 attempt** 的 run，按 `output_json.target_segments[].temp_id` 与现存 segment 顺序对应重建 `AlignmentEdge`，`replace_alignment_edges`，短尾标签段落挂到最后一句 210-257）；`REBUILD_PACKET_THEN_RERUN` / `REBUILD_CHAPTER_BRIEF` / `UPDATE_*` 走 `TargetedRebuildService.apply`（rebuild.py:69-211：重建 packet 与 snapshot，章状态回到 `PACKET_BUILT`，然后 rerun 翻译）；`REPARSE_CHAPTER` 走 `PdfStructureRefreshService`（对 EPUB 抛 ValueError 被用例层 `continue` 跳过，export_use_case.py:232-235）。
- `TRANSLATE_FULL` 在 review 阶段还会先跑 `repair_document_blockers_until_exportable`（executor:722；application/review_repair.py:174+），这是「导出前置修复」，它按 OPEN blocking issue 循环执行 action，最多 `max_rounds`，与导出门禁的 auto-followup 是**两套相似的循环**。

### 4.4 「用例层 gate 与服务内 gate 各跑一遍」
- 用例层 `_pass_export_gate`：`assert_chapter_exportable(chapter_id, export_type)`（export.py:391-394）= 装 bundle + `_enforce_gate`（写 issue）。
- 服务层 `_export_document`（236-237）对每章再 `_enforce_gate`；`export_chapter`（398）也再 `_enforce_gate`。
- 结果：每章 bundle 装载 2 次、对齐证据计算 2 次、PDF 的 render blocks 与 layout 校验 2 次（两次调用各自是独立的 `_within_render_model_scope`，缓存不跨调用）、issue plan 写 2 次（第二次通常是 no-op merge）。`review_package` 类型也会在两层各做一次 alignment sync，即**导出审校包本身会创建 blocking 的 ALIGNMENT_FAILURE issue**，进而阻断之后的最终导出——这是「审校包暴露问题」的隐式设计，但没有文档说明。
- 另一个重复：`_pass_export_gate` 用 `BootstrapRepository.load_document_bundle`（只为拿章列表），服务再用 `ExportRepository.load_document_bundle`。

---

## 5. 文件布局、完整性与下载（问题 4）

### 5.1 目录布局
- 配置：`Settings.export_root = Path("artifacts/exports")`（core/config.py:63），`upload_root = artifacts/uploads`。API 用 `request.app.state.export_root`（routes/documents.py:54-56），`_artifact_roots = (export_root, export_root.parent)`（59-64）。
- 写入根：`ExportService.output_root`；每文档目录 `<export_root>/<document_id>/`：
  - `bilingual-<chapter_id>.html` / `.manifest.json`、`review-package-<chapter_id>.json`
  - `merged-document.html|.md`、`merged-document.manifest.json`、`merged-document.markdown.manifest.json`、别名 `《标题》-中文阅读稿[-Markdown].html|.md`（旧别名按 glob 删除，341-348）
  - `rebuilt-document.epub` / `.epub.manifest.json`、`zh-document.epub` / `.epub.manifest.json`、`rebuilt-document.pdf` / `.pdf.manifest.json`
  - `assets/<flat-name>`（EPUB 归档图片，扁平化命名 `_allocate_flat_asset_filename` 2719-2737，重名加 sha1 前 8 位）、`assets/pdf-images/<block_id>.<ext>`、`assets/document-images/<block_id>.<ext>`
- 物化图片（跨导出共享）：`<export_root>/../document-images/<document_id>/<block_id>.<ext>`（`_materialized_document_image_path` 3035-3043），即 `artifacts/document-images/…`；`DocumentImage.storage_path` 从 bootstrap 的相对占位 `document-images/<doc>/<block>.<ext>`（services/bootstrap.py:595，`storage_status=logical_only`）在首次导出时改成绝对路径并写 `materialized_via / materialized_version=3 / materialized_render_scale / original_asset_availability`（pdf_crop.py:605-632）。
- CAS：`<export_root>/../blobs/<aa>/<bb>/<sha256>`（blobs.py:57-59, 146-149）。

### 5.2 exports 行与完整性
- ORM `Export`（review.py:120-164）：`id, document_id, export_type, input_version_bundle_json, file_path, status, content_sha256, byte_count, last_verified_at, stale_reason` + created/updated。CHECK：`file_path NOT LIKE '/var/folders/%' | '/private/var/folders/%' | '/tmp/%' | '/private/tmp/%'`（132-138；迁移 0028:98-105），阻止 tempdir 路径入库（M0 事故）。`unrecoverable://<basename>` 哨兵由 0028 回填写入。0032 把 CHECK 收敛为 7 种类型并在有旧类型行时拒绝升级。
- stamp：`stamp_and_materialize`（blobs.py:96-143）hash → 硬链接（跨设备回退拷贝）→ ORM UPDATE 三列；best-effort，不抛。调用点只有用例层 `_stamp_export_blobs`。**服务层直接调用（如 `pdf_prose_artifact_repair` 或 `_ensure_upstream_document_export` 递归产生的 merged 导出）不会被 stamp**——rebuilt_epub/pdf 触发的上游 merged 记录 `content_sha256` 为空，直到下一次同类型导出或 `scripts/backfill_export_paths.py`。
- 验证/回收：`scripts/verify_exports.py`（TTL 内跳过，drift → `stale_reason=content_drift`，缺失 → `missing_at_verify`）、`scripts/materialize_blob_store.py`（一次性回填 CAS）、`scripts/reap_blob_store.py`（删除无引用且 >60 分钟的 blob）。都是手动运维脚本，没有调度。
- `input_version_bundle_json`（436-520）记录：`chapter_id, sentence_count, target_segment_count, issue_count, document_parser_version, document_segmentation_version, book_profile_version, chapter_summary_version, active_snapshot_versions, translation_usage_*, issue_status_summary, sidecar_manifest_path, export_auto_followup_summary, export_time_misalignment_counts`（章）/ `chapter_count, merged_render_summary`（整书）。它是「导出时输入版本」的唯一快照，但没有 translation_run id 列表或 segment 哈希，无法判断「现有导出是否已过期」——下载端只看 `status=SUCCEEDED` 的最新行。

### 5.3 旧导出替换策略
- 同一 (文档, 章, 类型) 只有一行（stable id + merge），文件同名覆盖（`write_text` / `ZipFile(mode="w")`）。旧 blob 变孤儿等 reaper。
- 资产：`_copy_asset_if_changed`（86-95）按内容比较覆盖；EPUB 归档资产只在目标不存在时写（2833）；`_purge_legacy_nested_asset_dirs`（2741-2746）在每次 EPUB 源导出时 `rmtree` `assets/` 下除 `document-images` 之外的子目录（`ignore_errors=True`）。
- 无清理：文档删除（`ON DELETE CASCADE` 删 exports 行）不会删磁盘目录；没有按数量/时间的保留策略。

### 5.4 下载端点契约（`export_downloads.py`）
- `GET /v1/documents/{id}/exports/download?export_type=X`（routes:396-408 → `document_export_response` 419-529）：
  - `bilingual_html` → `_bilingual_document_response`（532-602）：每章最新 SUCCEEDED 记录，按 `chapters.ordinal` 排序，zip 进 `《书名》-中英文对照/第N章-章名-双语章节包.html` + 整个 `assets/` 目录（`_export_sidecar_paths` 232-238 取 `<dir>/assets/**` 全部文件）。
  - 其它类型：取最新记录；`assert_record_serviceable`（93-110）→ 410 Gone（`stale_reason` 或哨兵）；`resolve_artifact_path`（185-229）：优先 CAS blob（有 sha 且存在）→ 记录路径 → `merged-document*.html` glob 回退 → `<root>/[exports/]<document_id>/<basename>` 自愈 → 404。单文件直接 `FileResponse`，有 sidecar 则 zip（`build_export_archive` 248-280，临时文件 + `BackgroundTask(cleanup_path)`）。文件名 `《书名》-<label><ext>`，label 表 452-459 **缺 `zh_epub`**（会得到 `《书名》-zh_epub.epub`）。`content_disposition`（167-174）同时给 ASCII 回退名与 UTF-8 `filename*`。
  - 没有记录 → **404**（444-449）。前端契约：`frontend/src/lib/api.ts:766-797` `downloadDocumentExport` 在 404 时 `POST /documents/{id}/export`，轮询 `getRun` 直到终态（默认 2s 间隔、10 分钟超时），成功再下载；前端 `DocumentDownloadType` 只有 `merged_html | bilingual_html | merged_markdown | review_package`（api.ts:740），**没有 zh_epub / rebuilt_epub / rebuilt_pdf 的下载入口**。
- `GET /v1/documents/{id}/chapters/{cid}/exports/download`（routes:379-393 → `chapter_export_response` 334-416）：按 `input_version_bundle_json.chapter_id` 找记录；同样 410/CAS/自愈；有 sidecar 时 zip。前端 `downloadChapterExport` 固定 `bilingual_html`（api.ts:799-808）。
- `GET /v1/documents/{id}/exports`（dashboard，application/document_queries.py:250-333）与 `GET …/exports/{export_id}`（335+）：只读 exports 行与 `input_version_bundle_json`。文档摘要里的 `merged_export_ready` / `bilingual_export_ready` 只看是否存在 SUCCEEDED 记录（document_queries.py:466-483），不看是否过期。

---

## 6. `evidence.py` 与 `pdf_crop.py`（问题 5）

### 6.1 evidence：证据是什么、给谁
全部是 manifest / review package 的 JSON payload（不影响渲染）：
- `quality_summary_payload`（35-49）：`ChapterQualitySummary` 行（review 阶段产物）。
- `pdf_page_evidence_payload`（52-93）：`document.metadata_json.pdf_page_evidence`（解析期页族/布局风险/脚注迁移）按章页范围过滤。
- `pdf_image_evidence_payload`（96-143）：每张 `DocumentImage` 的存储状态、bbox、图注链接。
- `pdf_page_preserve_policy`（146-163）+ `ExportService._pdf_preserve_evidence_payload`（export.py:1032-1150）：按页统计「原样保留」的块（`is_expected_source_only`、`source_artifact_full_width`），给每页一个 `preserve_policy ∈ {source_only, mixed_source_only, preserved_artifacts, filtered_noise_only, source_only_expected, family_only, none}`。`_pdf_page_debug_evidence_payload`（1152-1313，仅 review package）把「可疑页」的每个块（角色、可译性、render_mode、摘录、recovery_flags、bbox）列出——这是给人工审校/调参看的调试数据。
- `version_evidence_payload`（166-210）：document/chapter/book_profile/active snapshots/packet 上下文版本。
- `recent_repair_events_payload` / `export_auto_followup_*`（213-279）：从 bundle 的 audit events 里取 `snapshot.rebuilt / packet.rebuilt / packet.realigned` 与 `export.auto_followup.*` 前 20 条。
- `translation_usage_*_from_runs`（365-511）：run 数、token、`cost_usd` 合计、延迟、按 (model, worker, provider) 分组、按天时间线、top 条目。既进 manifest，也被 `ExportService._build_usage_summary_html/markdown` 用来在**读者交付物里**渲染「翻译统计」段（export.py:575-641），其中费用按内置价格表估算（522-565）。
- 消费者：review package JSON（给审校工具/人）；manifest（给 dashboard `analytics.export_record_summary`、`get_document_export_detail` 读 `translation_usage_*`）；前端交付页。没有代码在导出**前**读这些证据做决策。

### 6.2 pdf_crop：裁剪几何与物化
- `pdf_asset_crop_spec`（30-50）：从 `source_bbox_json.regions[0]` 取页码 + bbox；CAPTION 且 `image_anchor_with_translated_caption` → `caption_anchor`（图在图注上方需推断），否则 `direct`。
- `caption_anchored_pdf_crop_bbox`（53-87）：默认框 = 图注上方 180–420pt、左右各扩 72pt（119-137）；用 `page.get_text("blocks")`（140-161）在图注上方 120pt 内找无文字的大块（`looks_like_page_image_block` 324-327：type==1 或空文本且 ≥96×96）按 (gap, overlap, center distance, area) 选最佳（164-208）；否则用文本块把上边界下压（211-234）。
- `layout_guided_pdf_crop_bbox`（90-116）：seed bbox 与页面图块合并（237-289），再用顶部/底部文本块修剪（292-321）。
- `probe_pdf_original_asset`（471-554）：遍历 `page.get_images(full=True)` 的 rect，与裁剪框重叠 ≥35% 的最大者 `extract_image` 直接输出原始字节（保真、无重采样）；碎片化 (`fragmented_embedded_images`)、矢量页 (`vector_only_page_artifact`) 等 availability 写进 `DocumentImage.metadata_json.original_asset_availability`。
- `save_pdf_asset` / `save_pdf_crop`（400-468）：无原图则 `get_pixmap(matrix=scale, clip=rect)`，倍率 = clamp(max(4.0, 1800/长边, 期望像素/pt), ≤8.0)（383-397，常量 common.py:26-28）→ 4–8× 渲染，PNG。
- `document_image_needs_refresh`（366-380）：`materialized_via` 不在期望集合、`storage_status != materialized`、或 `materialized_version < 3` → 重裁。
- 结果通过 `DocumentImageMaterialization` 回传，服务在 `_export_pdf_assets` 末尾 `apply_document_image_materializations`（export.py:2425, 2668）写回 ORM。

---

## 7. 脚本导出链路 vs 服务导出（问题 6）

> 脚本侧细节由并行阅读汇总（`scripts/export_chapter_zh_html.py` 等 12 个脚本，行号为当前工作树）。结论先行：**12 个脚本没有一个 import `src/book_agent/export/*` 或 `services/export.py`**（唯一例外 `export_chapter_md_official.py:76-86` 经 `DocumentWorkflowService` 调服务）；`export_chapter_bilingual.py:51`、`export_chapter_md.py:42` 以 `import export_chapter_zh_html as zhmod` 复用脚本内部函数。链路编排 `scripts/qa_run_chapter.sh` 只调 zh_html + verify_chapter。

### 7.1 译文选择规则差异
| 实现 | 规则 | 位置 |
|---|---|---|
| 服务 | 每句所有非 SUPERSEDED edge 候选 → 取「最高秩 run」（updated_at/created_at、packet 类型 translate<review<retranslate、attempt）的 segment；segment 状态不参与 | `export/alignment.py:43-119` |
| `export_chapter_zh_html.py`（及 bilingual/md 脚本） | 每句 `TargetSegment JOIN AlignmentEdge WHERE sentence_id=? AND final_status != SUPERSEDED ORDER BY TargetSegment.ordinal LIMIT 1`——「首条 edge」，不看 run/时间 | `:1013-1021` |
| `render_bilingual_subset.py` | packet 最新 `status='succeeded'` 的 run（`ORDER BY attempt DESC`），按 `ordinal_in_block` 与 target `ordinal` **位置对应**，不用 alignment_edges | `:447-462, 526-537, 711-719` |
差异后果：同一句被重译（retranslate/review 包）后，脚本会继续渲染旧译文（旧 segment 只要没被标 SUPERSEDED），服务渲染新译文；`render_bilingual_subset` 则在 1:n / n:1 对齐时错位。PLAN P4 明确要「有意统一」。

### 7.2 启发式对照表
| 主题 | 脚本（zh_html 行号） | 服务对应 | 状态 |
|---|---|---|---|
| 图 bbox：正文串入裁剪 `_trim_top_body_text_leak` `:141-216` | 用 `get_text("blocks")` 剪掉图上方宽文本块 | `pdf_crop.trim_caption_crop_bbox_with_text_blocks` / `trim_direct_crop_bbox_with_text_blocks`（211-234, 292-321）用同源信息修剪上下边 | 已并入（形式不同） |
| 内嵌图 bbox 替换 `_resolve_embedded_image_bbox` `:219-253` | 与 parser bbox 交集 ≥50% 的 raster rect | `probe_pdf_original_asset`（≥35% 重叠取原图字节）+ `best_seed_aligned_image_bbox` | 已并入且更强（直接输出原图） |
| 矢量图按 drawing 扩展 `_expand_figure_bbox_to_drawings` `:256-416`；内容盒吸附 `:614-620, 675-700`；面积上限 50% `:500-504`；PNG>200KiB 转 JPEG `:585-596`；DPI 自适应 180–360 `:100-113` | | 服务无 drawing 级扩展（只看 text blocks 与 image blocks）、无面积上限、只出 PNG 或原图格式、倍率 4–8×/1800px | **未并入**：drawing 扩展、面积上限、JPEG 压缩 |
| 图内文本抑制（几何包含 `:703-756`，`sandwich_ordinals` `:2679-2692`，渲染期 `:3516-3523`）、标签片段 `:3561-3565` | | `resolve_artifact_group_context_ids`（artifact_grouping.py:161）把图周边上下文块并入图块（不是抑制而是合并成图注译文）；R10 只在代码之间抑制小图 | 部分：思路不同，脚本是丢弃，服务是并入图注 |
| caption 链接：parser link 校验 `:2489-2501`，回退配对（同页、±10 ordinal、最近）`:2514-2551`，`_ensure_figure_prefix` 补「图N.M」`:1527-1542`，figcaption 优先级 `:3875-3929` | | 解析期 `linked_caption_block_id`/`caption_for_block_id`；导出期 1806-1899 合并；无回退配对、无图号前缀补全；渲染同时显示译注 + 原图注 | **未并入**：回退配对、图号补全 |
| sidebar/误判为图 `:2561-2615, 3131-3350`；sidebar/cover 缓存 `:3595-3674` | | 无；解析期 `callout_kind`（render_bilingual_subset 用到 `metadata_json.callout_kind`）待确认是否被产品解析器写出 | 未并入（且缓存是人工翻译，不可迁移） |
| 多面板簇 `:2694-2862` | | 解析期 figure cluster（PLAN 2026-09-15 修复条目「图聚类…」） | 解析期已有等价物 |
| 标题：一律 `<h2>` `:1099`；`_looks_like_heading_source` `:1815-1836`（编号/降级词/列表项/句尾）；标题碎片合并 `:2866-2969`；NOTE/TIP callout 粘接 `:781-794, 3753-3760`；节号回退借译 `:2452-2476`；页眉伪影提升为标题 `:2880-2905`；章标题去重 `:3468-3503`；Listing 旁注标题丢弃 `:3566-3579`；后记裁剪 `:3454-3487` | | R1/R5/R6 降级规则、标题碎片合并（render_repair 971-1055）、`_merged_body_blocks` 章标题去重、`pdf_page_family=backmatter` 不可译 + notice；层级：HTML 统一 `.heading`、MD 固定 `###`、EPUB `<h2>` | 部分：callout 粘接、节号借译、页眉提升、Listing 旁注未并入；**服务与脚本都没有真正的标题层级** |
| 页眉/页码/伪影 `_is_page_artifact` `:1421-1441`、`_looks_like_page_header` `:1341-1344` | | 解析期 `pdf_block_role ∈ {header, footer, toc_entry}` 跳过（export.py:1789）；无导出期正则兜底 | 依赖解析期 |
| 脚注一律丢弃 `:3680-3682` | | 渲染为普通块；layout gate 检查 FOOTNOTE_EMPTY/ANCHOR_ORPHANED | 产品行为不同（保留） |
| stub 译文过滤 `_is_stub_translation` `:1444-1454, 1488-1491`（"这是当前段落中唯一的句子"）；`render_bilingual_subset.strip_llm_meta_commentary` `:128-141` | | 无；只有 R2（短源长译清空） | **未并入** |
| 排版码清除 `_strip_typeset_codes` `:1501-1518` | | 无 | 未并入（书籍特定） |
| 段落续接合并 `:2971-3068`（小写接小写，可跨 ≤2 页眉块）；单块多段拆分 `:857-972`（行宽/缩进，用 fitz） | | R12（小写开头续接）、R15 段落分隔恢复（按 target 分组，不用 fitz） | 大体并入 |
| bullet 拆分 `Term—Definition` `:1220-1268`；ordered list 分组 `:3071-3129` | | R7 仅重排源已有列表标记的译文；`markup.markdown_list_text` / `.bulleted` 悬挂标记；解析期列表识别（PLAN 2026-09-15） | 部分：`Term—Definition` 拆分、相邻编号段落成 `<ol>` 未并入 |
| 代码 `_looks_like_real_code` `:1757-1784`；Listing 分组 `:3352-3448` | | `code_text.looks_like_code_artifact_text` + reflow + R3/R4/R8/R11（远比脚本细） | 服务更强；Listing 标题（figure listing）未并入 |
| References/Index 重排 `:3987-4008` | | R13 参考文献；index 无 | 部分 |
| 双语 fold `<details class='source-fold'>` `:2302-2333` | | `<details><summary>Source</summary>` 每块 | 已有 |
| 单文件 data URI 图 `:1299-1310` | | 外部 `assets/` 文件 + zip 下载 | 设计不同 |
| QA 报告 `*.qa_report.json`（repair_stats/image_skip_reasons/untranslated）`:4134-4151`；`verify_chapter.py` R1–R8 | | manifest `render_summary/row_summary/issue_summary`；layout gate | 服务缺「图覆盖率 / 未译比例」这类交付级校验 |

### 7.3 脚本链路独有的产物形态
- `build_full_book_bilingual.py` / `build_llm_book_deliverable.py` / `package_deliverables.py`：从 `.test-tmp/chN-export/*` 拼整书 HTML/MD，`deliverable/{md-zh,md-bilingual,html-zh,html-bilingual}/book.{html,md}+assets/`，书级目录 + 章锚点 + 返回目录链接；无 manifest/zip/校验。服务的 merged_html **没有 TOC 侧栏**（`markup.build_merged_toc` 952-968 无人调用，CSS `.sidebar/.toc-*` 也悬空，merged_document.css:6-13）。
- `verify_exports.py` / `heal_exports_by_basename.py` / `backfill_export_paths.py` 是 exports 表运维，与 `blobs.py`、`export_downloads.py` 的自愈逻辑重叠（同样的 `<root>/[exports/]<doc>/<basename>` 探测）。

---

## 8. 性能热点（问题 7）

1. **重复装载与重复门禁**（§4.4）：整书导出 = 用例层每章 `load_chapter_bundle`（≥13 条 SQL/章，含无上限的 AuditEvent 查询 repositories/export.py:226-231）+ 服务层再来一遍；PDF 文档再加两次 render blocks + layout。rebuilt_epub/pdf 再多一次 `load_document_bundle`（export.py:240），若上游 merged 缺失还会递归各做一次完整导出。
2. **render blocks 缓存只在单次调用内有效**（`_within_render_model_scope`）：bilingual 逐章导出时每章都是独立调用；`_visible_merged_chapters` 在 manifest、render、`_record_document_export`、`_write_rebuilt_epub` 中被调用多次，但因缓存命中只是重复做分组/去重（O(章数)）。
3. **启发式本身**：每块多次正则扫描（`looks_like_code_artifact_text` 等在 `render_mode_for_block`、`artifact_kind_for_block`、`is_code_like_block`、R3/R4/R6 中被反复调用，每次都重新 split/regex）。3k 块的书粗估几十万次正则匹配——待实测。
4. **图片**：PDF 每张图 `fitz.open` 一次文档（每章导出各开一次）、`page.get_text("blocks")`、`get_images` + `get_image_rects`、4–8× `get_pixmap`（一张 A4 图 8× ≈ 4700×6700 px，PNG 数十 MB 内存）；`document_image_needs_refresh` 使版本升级时整书重裁。`_export_epub_assets_for_document_bundle` 先把 `assets/document-images` 全量拷一遍再逐块处理。
5. **CAS stamp**：每次导出对该类型**所有**记录重新 sha256（大 PDF/EPUB 每次全量哈希）。
6. **Playwright**：每次 rebuilt_pdf 启动一个 chromium（2639-2647），无复用；`wait_until="load"` 等 CDN KaTeX；无超时参数。
7. **下载**：zip 在请求线程内同步打包整个 `assets/`（含重复的 `document-images` 与 `pdf-images` 副本），每次请求重新打包到临时文件。
8. **`_bilingual_document_response`**：每章都 `_export_sidecar_paths` 遍历整个 assets 目录（O(章×文件)）。

---

## 9. 缺失的生产要素（问题 8）

| 要素 | 现状 | 依据 |
|---|---|---|
| 导出产物版本化 | 无。stable id + merge → 每 (文档,章,类型) 一行；文件同名覆盖；`created_at` 被重置；manifest 无输入指纹 | export.py:446-477, 490-518; repositories/export.py:292 |
| 过期检测 | 无「译文变了但导出没变」的判断；`merged_export_ready` 只看行存在；rebuilt_pdf 复用旧 merged_html 文件不校验新鲜度 | document_queries.py:466-483; export.py:358-382 |
| 清理/保留 | 无 TTL、无删除文档时删目录；孤儿 blob 靠手动 reaper；`assets/` 只增不减（除 EPUB 源的 rmtree） | scripts/reap_blob_store.py; export.py:2741-2746 |
| 存储抽象 | 全部 `pathlib` 本地盘；下载 `FileResponse`；CAS 依赖硬链接；`document.source_path` 必须是本地文件（zh_epub / PDF 裁图） | blobs.py:83-93; export.py:2534, 2891 |
| 并发互斥 | 无锁。两个 EXPORT_FULL run 同文档同类型并行 → 同名文件互相覆盖、同 id 行 merge 冲突、`assets/` rmtree 竞争；执行器按 run 播种工作项，不同 run 无互斥 | executor:601-670 |
| 原子性 / 部分失败恢复 | 文件先写后提交，无临时文件+rename；zip 直接写目标；失败无回滚清理；rebuilt_pdf 失败留下上游导出 | export.py:413-433, 2506, 2560, 2631-2651 |
| 可观测性 | 无导出耗时/大小指标；异常只在 blobs.py 有日志；Playwright 失败信息被包成 ExportGateError（语义混淆） | export.py:2649-2651 |
| 配置项 | 只有 `export_root`；CDN 地址、A4、渲染倍率、价格表、CSS 全硬编码 | config.py:63; common.py:26-28; export.py:522-536 |
| 离线可用 | HTML/PDF 依赖 jsdelivr KaTeX；无字体内嵌；`rebuilt_pdf` 在无网环境公式不渲染 | export.py:1339, 2380 |
| 表格翻译 | `protected_policy_for_block` 把 TABLE/CODE/FIGURE/EQUATION/IMAGE 一律 `PROTECT`（domain/block_rules.py:29-40），句子 `translatable=False`；导出只能 `translated_wrapper_with_preserved_artifact` 显示原文表格（除非有 linked caption）。文字型对比表在中文版仍是英文（PLAN.md:137） | block_rules.py; render_repair.py:1670-1673 |
| 脚注/交叉引用 | 无脚注编号、无回链、无 `#chapter-` 之外的内部锚点 | markup.py |
| 标题层级 | HTML 全部 `.heading`（无 h2/h3）、MD 固定 `###`、EPUB `<h2>`；解析期 `heading_level` 元数据只被 layout gate 读取 | markup.py:137-142, 900-901; layout_validate.py:220-234 |
| 多语言 | `dc:language` 固定 `zh-CN`、提示语中文硬编码（`source_only_notice`、`_document_export_label`、下载文件名标签） | export.py:2466-2467; markup.py:304-325; common.py:141-152 |
| 交付级验收 | 无图覆盖率、未译比例、空块比例等阈值检查（脚本 `verify_chapter.py` 有 R1–R8） | — |


---

## 10. 发现的 bug / 不一致 / 风险（问题 9）

严重度：**B** = 明确缺陷；**R** = 设计风险/技术债；**I** = 不一致。

| # | 级 | 描述 | 位置 |
|---|---|---|---|
| B-01 | B | 布局门 issue 的 `block_id` 直接取 render block id；拆分/重排产生的合成 id（`<uuid>::leading-prose`、`::refresh-split::N`、`::reference-entry::N`、`::abstract-body`）不是 UUID 也不是 blocks 表主键，而 `ReviewIssue.block_id` 是 `Uuid` + FK `blocks.id` → flush 时报错（Postgres 类型/外键；SQLite 行为待确认） | `services/export.py:988, 950`；`render_repair.py:245, 1520, 2214（export.py）`；`domain/models/review.py:31` |
| B-02 | B | 重建 EPUB 每章 `<h1>标题</h1>` 后紧跟正文首个 HEADING 块 `<h2>同一标题</h2>`（不像 merged 走 `_merged_body_blocks` 去重），golden 快照可见 | `export/markup.py:911-938`；对照 `services/export.py:1426-1448` |
| B-03 | B | `ExportGateError` 被用于非门禁失败：Playwright 缺失/渲染失败、上游类型不支持、无可见章、源 EPUB 缺失。调用方把它当门禁（409、`export_gate` 详情、审计 stop）。运行时分类为何种失败待确认（`classify_failure`） | `services/export.py:235, 382, 2439, 2536, 2635, 2649`；`tests/document_actions.py:88-91`；`document_run_executor.py:1056-1057` |
| B-04 | B | `_ensure_upstream_document_export` 递归生成的 merged_html / merged_markdown 记录不会经过 `_stamp_export_blobs`（只 stamp 请求的类型），`content_sha256` 为空，下载走非 CAS 路径、verify 脚本跳过 | `services/export.py:358-388`；`application/export_use_case.py:85-98` |
| B-05 | B | 下载文件名 label 表缺 `zh_epub`（得到 `《书名》-zh_epub.epub`）；前端 `DocumentDownloadType` 也没有 zh_epub / rebuilt_epub / rebuilt_pdf，三种类型无 UI 下载入口 | `app/api/export_downloads.py:452-460`；`frontend/src/lib/api.ts:740` |
| B-06 | B | 审校包导出会同步 alignment issue：`evaluate_chapter_gate` 对 review_package 也生成 `ALIGNMENT_FAILURE(blocking=True)`，`sync_gate_issues` 落库，随后所有最终导出被 `has_open_blocking_issues` 拦截；用例层 + 服务层各同步一次 | `services/export.py:667-686, 688-705, 745-752` |
| B-07 | B | `_apply_export_issue_plan` 用 `session.merge` 写状态 OPEN 的新对象，同 id 的既有 issue 若已被人工置为 `WONTFIX`/`RESOLVED` 会被翻回 OPEN（只要异常仍存在），人工决定不被尊重 | `services/export.py:948-950` |
| B-08 | B | `_purge_legacy_nested_asset_dirs` 在每次 EPUB 源导出时 `rmtree` `assets/` 下除 `document-images` 外所有子目录（`ignore_errors=True`），并发导出/下载时删到正在读的文件 | `services/export.py:2739-2746, 2805` |
| B-09 | B | 布局门 `_validate_figure` 只认 `image_src` 或 bbox；解析期已物化的 PDF 图（`image_path`）若无 bbox 会被判 `FIGURE_ASSET_MISSING`，而导出快路径明明能用 `image_path`（2905-2915） | `services/layout_validate.py:129, 236-238`；`services/export.py:2905-2915` |
| B-10 | B | 重建 EPUB 渲染忽略 `table_markdown` / `equation_latex` 元数据（HTML/MD 用 `*_metadata_aware`），公式一律 `<pre><code>` | `export/markup.py:850-851, 856-858, 866-872` vs `669-703, 779-813` |
| B-11 | B | `render_math_markdown` 的 LaTeX 标记集合（9 个）远少于 `render_math_html`（30+），同一公式 HTML 走 KaTeX、MD 走代码块 | `export/markup.py:754 vs 773` |
| B-12 | I | `Export.file_path` 形式取决于调用方：执行器传 `Path(export_root).resolve()`（绝对），API/CLI/测试传 `settings.export_root` 原样（默认相对 `artifacts/exports`）；相对路径在 `stamp_export_records`、`resolve_artifact_path` 里都按 CWD 解析 | `app/main.py:95`；`document_run_executor.py:106`；`routes/documents.py:80-84`；`blobs.py:168-173`；`export_downloads.py:210` |
| B-13 | R | 无并发互斥：同文档同类型两个 run/请求并行 → 同名文件互相覆盖、同 id 行 merge 冲突（后提交者赢或 IntegrityError）、rmtree 竞争 | `services/export.py:232-264, 396-406`；`document_run_executor.py:601-670` |
| B-14 | R | 非原子写：先写文件后提交；zip 直接写目标路径；失败不清理。rebuilt_pdf 失败仍留下上游 merged 导出（执行器在 `ExportGateError` 时 commit） | `services/export.py:246-258, 2506-2523, 2560`；`document_run_executor.py:846-851` |
| B-15 | R | 无导出版本/历史：stable id + merge，`created_at` 重置；dashboard 的 `export_count` 实为 (章,类型) 组合数；无法回滚或比较两次导出 | `services/export.py:446, 475, 490, 518`；`repositories/export.py:292` |
| B-16 | R | 陈旧导出不可检测：`merged_export_ready`、下载、rebuilt_pdf 复用 merged_html 均只看行/文件存在；`input_version_bundle_json` 不含 run/segment 指纹 | `application/document_queries.py:466-483`；`services/export.py:358-382` |
| B-17 | R | CAS 优先读：stamp 失败（hash IOError）时旧 sha 仍在，`resolve_artifact_path` 优先返回旧 blob → 下载到上一版内容 | `infra/storage/blobs.py:115-122`；`export_downloads.py:206-209` |
| B-18 | R | 读者交付物内嵌「翻译统计」（调用次数、token、估算费用、延迟），费用按硬编码公开价目表估算（含 `claude-opus-4` 等），与 `Settings.translation_*_cost_per_1m_tokens` / run 上的 `cost_usd` 三处定价 | `services/export.py:522-641`；`core/config.py:73-75` |
| B-19 | R | KaTeX 走 jsdelivr CDN；rebuilt_pdf 在离线/受限网络下公式不渲染且 `wait_until="load"` 可能长时间等待（无超时） | `services/export.py:1339, 1351, 2380, 2393, 2642` |
| B-20 | R | 表格/代码/公式/图整块 `PROTECT`（句子 `translatable=False`）→ 文字型表格永远不翻译；导出无按单元格翻译路径 | `domain/block_rules.py:29-40`；`render_repair.py:1670-1673` |
| B-21 | R | 译文选择按**句**独立取最高秩 run；块内不同句可能来自不同 run，n:1 / 1:n 对齐跨 run 时会重复或漏文本（`orphan` 只在首选 run 内检测，跨 run 的重叠不报） | `export/alignment.py:43-60, 86-119, 163-178` |
| B-22 | R | 门禁重复执行：用例层与服务层各装载 bundle、各算对齐/布局、各写 issue 一次；PDF 的 render blocks 每章至少构建两次 | `application/export_use_case.py:189-193`；`services/export.py:236-237, 398` |
| B-23 | R | `_apply_document_export_status_updates` 把**所有**章（含被 `_visible_merged_chapters` 跳过/合并的）标 EXPORTED；rebuilt_epub/rebuilt_pdf 本身不改状态但它们触发的上游 merged 会改 → 状态语义与「哪个文件真正交付了」脱节 | `services/export.py:773-783, 3120-3196` |
| B-24 | I | 三种渲染器对同一块的处理不一致：merged HTML/MD 不带原文，重建 EPUB 每段带 `Source:` 原文（且列表项渲染成 `<p>`）；MD 标题固定 `###`，HTML 无标题层级，EPUB `<h2>` | `export/markup.py:137-142, 895-908` |
| B-25 | I | `_is_code_language_label_block` 会丢掉内容恰为 `c`、`r`、`go`、`md`、`ts` 等的短段落/引用/图注 | `services/export.py:1450-1476` |
| B-26 | I | `markdown_language_for_artifact` 以 `"from "`/`"import "` 子串判 python，散文型代码块易误标 | `export/markup.py:290-301` |
| B-27 | I | zh_epub：只 patch 有 `anchor`（元素 id）的块；nav.xhtml、`dc:title`、`dc:language` 不翻译；inline 子元素（`<em>`/`<code>`）文本被清空，带 href/id 的子元素保留文本但被挪到译文末尾；`ExportGateError` 提示需要源文件 | `services/export.py:2540-2551, 2595-2619`；`tests/test_source_preserving_epub_export.py:278-279` |
| B-28 | I | PDF 图资产重复：`assets/document-images/<block>.png` 与 `assets/pdf-images/<block>.png` 同内容各一份，HTML 只引用后者，zip/重建 EPUB 都打包两份（golden pdf 快照 14033 字节 ×2） | `services/export.py:2674-2701, 2905-2915`；`export_downloads.py:232-238` |
| B-29 | I | 重建 EPUB 把 `<dir>/assets/**` 全部打进包（含其它导出/章节的资产与重复副本），manifest 也全列 | `services/export.py:2478-2486, 2522-2523` |
| B-30 | I | EPUB 归档资产只在目标不存在时写（不比较内容），且扁平命名冲突检测只在单次调用内；EPUB 结构刷新后同名不同路径的图会拿到旧文件 | `services/export.py:2719-2737, 2833` |
| B-31 | I | `resolved_chapter_title_text` 优先取正文首个 HEADING 的译文覆盖 nav/chapter 标题 → 章内首标题与目录标题不同（或与上一章相同）时整书出现重复/错误章名（golden epub：两章都叫 `ZH::Chapter One`） | `export/titles.py:41-80`；`tests/golden/exports/epub.json` |
| B-32 | I | `pdf_page_debug_evidence` 只进 review package，`pdf_preserve_evidence` 进 bilingual manifest；merged 系列 manifest 没有任何页级证据 | `services/export.py:785-827, 829-878, 1510-1565` |
| B-33 | I | `epub_assets.index_epub_figure_archive_paths` 用 `raw if "raw" in locals() else None` 传递异常前的局部变量（hack） | `export/epub_assets.py:66-72` |
| B-34 | I | `_MODEL_COST_TABLE_PER_MILLION`、KaTeX 版本、A4、渲染倍率 4–8×/1800px、通知文案、下载 label 全硬编码，无 Settings | `services/export.py:522-536, 2643`；`export/common.py:24-28, 141-152` |
| B-35 | I | `common._looks_like_metadata_filename` 与 `domain/document_titles.py:113`、`domain/structure/epub.py:222` 三份实现，export 那份无人调用 | `export/common.py:132-138` |
| B-36 | R | 执行器 `_execute_export_work_item` 在 `plan is None` 时 `next_stage` 硬编码 `"merged_html"`/`"completed"`（旧全流水线假设） | `document_run_executor.py:826-830` |
| B-37 | R | `ExportRepository.load_chapter_bundle` 的 `AuditEvent` 查询无 `object_type` 过滤、无 limit（按 object_id 撞 id 的其它对象也会被载入；长期运行审计表膨胀后每章导出加载整段历史） | `repositories/export.py:226-231`；`evidence.py:228-231, 276-279` 只取前 20 |
| B-38 | R | `code_text` / `common` 内含框架特定 token（`ChatPromptTemplate`, `Runnable`, `LlmAgent`, `session.state[`，`llm`）作为代码线索——启发式对特定书籍过拟合 | `export/common.py:372`；`export/code_text.py:235, 378` |
| B-39 | R | `_bilingual_document_response` / 章下载用 `input_version_bundle_json.chapter_id` 做 JSON 字段过滤（无索引，全量加载该类型所有记录再筛） | `export_downloads.py:344-360, 539-548` |
| B-40 | I | `render_repair.infer_refresh_split_fragment_target_text` 从父块译文里按「第一个 CJK 字符」截取作为片段译文——仅对 shell 命令父块，脆弱 | `export/render_repair.py:550-574` |

已知 hack / TODO / 死代码汇总：
- 无显式 `TODO/FIXME` 注释（grep 未见）；PLAN.md P3.2 明确「用例层 gate 与服务内 gate 仍各跑一遍」为未做项。
- 委托壳（仅测试或 `pdf_prose_artifact_repair` 使用，PLAN 说保留）：`services/export.py:880-898, 963-968, 1029, 1478-1508, 1610, 1987-1994, 2085-2090, 2240-2348, 3018-3033`。其中 `_pdf_image_summary_payload`(1610)、`_markdown_details_source`(1501)、`_split_table_candidate_line`(1504)、`_is_table_separator_row`(1507)、`_looks_like_stable_structured_code_layout`(2307)、`_render_*_metadata_aware`(2310-2340) 只被 `tests/test_export_modality_rendering.py` / `test_persistence_and_review.py` 调用。
- 无人调用：`markup.build_merged_toc`(952-968)、`markup.markdown_details_source`(178)、`code_text.should_preserve_markdown_code_artifact_layout`(75)、`pdf_crop.extract_pdf_original_image`(557)、`common._looks_like_metadata_filename`(132)、`merged_document.css` 的 `.sidebar/.toc-*`(6-13)。
- `MergedRenderBlock.title` 只在 HEADING 时填章标题（export.py:1934），导出层无任何读取者（grep `block.title` 无命中）——死字段。

---

## 11. 配置项、耦合点、测试覆盖

### 11.1 配置项（全部）
| 项 | 位置 | 说明 |
|---|---|---|
| `Settings.export_root`（默认 `artifacts/exports`） | `core/config.py:63` | 唯一导出配置；artifact root = 其父目录（blobs、document-images） |
| `app.state.export_root` | `app/main.py:95` | API/执行器读取 |
| `ExportDocumentRequest.auto_execute_followup_on_gate=False`, `max_auto_followup_attempts` | `schemas/workflow.py:203-214` | run_request 透传 |
| `RunPlan.auto_followup_on_export_gate`（TRANSLATE_FULL 默认 True） | `orchestrator/run_plan.py:33, 73` | |
| `_max_auto_followup_attempts(session, run_id)` | `document_run_executor.py:838` | 无 plan 值时从 run 设置读（未细看） |
| 常量：`_DOCUMENT_IMAGE_MATERIALIZATION_VERSION=3`、渲染倍率 4/8/1800 | `export/common.py:25-28` | 改版本号会触发整书重裁 |
| 价格表 | `services/export.py:522-536` | |
| KaTeX 0.16.9 CDN、A4 | `services/export.py:1339, 2643` | |
| CSS | `export/templates/*.css` | `stylesheets.load` 逐行拼接（无分隔符），CSS 内不可有跨行规则依赖换行 |

### 11.2 耦合点
- `services/export.py` → `domain/structure/epub.py` 私有函数 `_parse_xml_document`、`_join_path`、`_local_name`、`_element_class_tokens`、`_figure_caption_text`、`_figure_like_container`、`_first_descendant`（export.py:40-42；epub_assets.py:11-19；common.py:15-18）；→ `ingestion/pdf/classify` 与 `ingestion/text` 的私有启发式（common.py:19-22；code_text.py:36-51；render_repair.py:42-55）：导出层依赖解析层内部符号。
- `services/pdf_prose_artifact_repair.py` 反向依赖 `ExportService` 私有方法（94, 324, 472, 682, 703, 712, 806）与 `export.common._normalize_signature_text`，并写 `repair_*` 元数据供导出消费——导出与修复互相约定 `source_span_json` 键名，无 schema。
- `services/layout_validate.py:8` 依赖 `export.markup.parse_structured_table_rows`（门禁与渲染共用解析，是有意的）。
- `application/document_queries.py` 与 `export/evidence.py` 各有一套 `translation_usage_*`（analytics 版 vs evidence 版）——待确认是否同源。
- `services/review.py` 有自己的 `_packet_current_sentence_ids`（与 alignment.py:300 重复）。
- `orchestrator/rule_engine.build_issue_action` 决定门禁 followup 类型；`RerunService`/`RealignService`/`TargetedRebuildService`/`PdfStructureRefreshService` 是 followup 执行体。
- 前端契约：404 → POST export → 轮询 run（api.ts:766-797）；`DocumentRunSummary.status/stop_reason`。

### 11.3 测试覆盖
| 测试 | 覆盖 |
|---|---|
| `tests/test_export_golden.py` + `export_golden_scenario.py` | EPUB 夹具 6 种、PDF 夹具 4 种导出的逐文件快照（门禁被 patch 掉：`_enforce_gate` return None，scenario:92）；EPUB/zip 逐条目；不含 rebuilt_pdf |
| `tests/test_export_gate_evaluation.py` | evaluate（只读、不写 session）/ sync（写 issue+action）/ `_raise_for_gate` |
| `tests/test_export_layout_gate.py` | `FIGURE_ASSET_MISSING` → LAYOUT issue + REPARSE_CHAPTER action；修复后 RESOLVED |
| `tests/test_export_figure_placeholders.py` | `[Figure]/[Image]` 不作图注（三种渲染器） |
| `tests/test_export_list_items.py` | 列表项圆点悬挂 |
| `tests/test_export_modality_rendering.py` | `equation_latex` / `table_markdown` 元数据渲染（通过服务委托壳） |
| `tests/test_source_preserving_epub_export.py` | zh_epub 打补丁保留结构（1 个用例） |
| `tests/test_render_bilingual_subset.py` | **脚本** `render_bilingual_subset.py` 的 F2–F11 纯函数（不测服务） |
| `tests/test_api_workflow.py` | 同步适配器路径：409 门禁、bilingual/merged/review 导出、多章 zip 下载（1796-1840）、merged_html 派生 `title_tgt` 与人类文件名（1881-1920）、空前言章（1922+）、后台 run 后下载（1040-1098） |
| `tests/test_persistence_and_review.py` | 67 个含 export/render 字样的用例：错位证据、代码重排/合并、refresh-split、标题降级/合并、参考文献、列表、学术论文、EPUB 资产回溯、rebuilt epub/pdf（含 Playwright 缺失时 fail-closed 2816）、auto-followup（10301, 10674） |
未覆盖：并发、部分失败恢复、CAS stamp 失败路径、下载 410/自愈分支（`test_export_*` 里未见；可能在其它测试文件，待确认）、zh_epub 的 nav/OPF、rebuilt_epub 标题重复（golden 固化了 B-02）、PDF 裁图几何（`pdf_crop` 无直接单测，待确认）。

---

## 附录 A：`ExportService` 逐方法清单（`services/export.py`）

| 行 | 方法 | 类别 | 备注 |
|---|---|---|---|
| 86 | `_copy_asset_if_changed`（模块函数） | 资产 | 内容比较后覆盖 |
| 98-145 | `ExportGateError` | 异常 | `to_http_detail` |
| 147-154 | 状态集合 | 门禁 | |
| 158-174 | `ExportIssuePlan`, `ChapterGateEvaluation` | 门禁 | |
| 177-195 | `_within_render_model_scope` | 缓存 | |
| 199-207 | `__init__` | | `output_root` 默认相对路径 |
| 210-229 | `export_review_package/bilingual_html/document_merged_html/…` | 入口 | |
| 232-264 | `_export_document` | 整书主流程 | |
| 266-331 | `_sync_document_title_tgt`, `_derive_document_title_tgt` | 标题 | 仅 EPUB，从前两章 HEADING 译文推导书名译名并写 `document.metadata_json.document_title` |
| 333-349 | `_write_document_export_alias` | 文件 | 人类可读别名 |
| 351-388 | `_manifest_path_from_export_record`, `_ensure_upstream_document_export`, `_ensure_rebuilt_upstream_exports` | 上游 | |
| 391-434 | `assert_chapter_exportable`, `export_chapter`, `_export_files` | 章级 | |
| 436-520 | `_record`, `_record_document_export` | 记录 | |
| 522-641 | 价格表、`_estimate_run_cost_usd`, `_sum_run_cost_usd`, `_format_usage_summary_lines`, `_build_usage_summary_markdown/html` | 用量 | |
| 643-660 | `_open_blocking_followup_actions` | 门禁 | |
| 662-752 | `_enforce_gate`, `evaluate_chapter_gate`, `sync_gate_issues`, `_raise_for_gate` | 门禁 | |
| 754-783 | `_apply_status_updates`, `_apply_document_export_status_updates` | 状态 | |
| 785-878 | `_build_review_package`, `_build_bilingual_manifest` | 证据 | |
| 880-898 | `_build_export_misalignment_evidence`, `_sync_export_alignment_issues`, `_sync_export_layout_issues` | 委托壳 | 仅测试 |
| 901-961 | `_plan_export_alignment_issues`, `_plan_export_layout_issues`, `_apply_export_issue_plan` | 门禁 | |
| 963-1027 | `_build_export_alignment_issues`（壳）, `_build_export_layout_issue` | 门禁 | |
| 1029 | `_packet_current_sentence_ids` | 壳 | |
| 1032-1313 | `_pdf_preserve_evidence_payload`, `_pdf_page_debug_evidence_payload` | 证据 | |
| 1315-1424 | `_build_merged_document_html/markdown`, `_render_chapter_for_merged_markdown` | 渲染 | |
| 1426-1476 | `_merged_body_blocks`, `_CODE_LANGUAGE_LABELS`, `_is_code_language_label_block` | 渲染 | |
| 1478-1508 | `_render_block_markdown`, `_normalize_markdown_code_artifact_text`, `_markdown_details_source`, `_split_table_candidate_line`, `_is_table_separator_row` | 壳 | |
| 1510-1608 | `_build_merged_document_manifest`, `_build_rebuilt_document_manifest` | manifest | |
| 1610 | `_pdf_image_summary_payload` | 壳 | 无人调用 |
| 1613-1652 | `_render_chapter_for_merged_html` | 渲染 | |
| 1654-1756 | `_visible_merged_chapters` | 章可见性/分组 | |
| 1758-1985 | `_render_blocks_for_chapter`（缓存）, `_build_render_blocks_for_chapter` | 组装 | 核心 |
| 1987-1994 | `_extract_main_chapter_number`, `_looks_like_prose_artifact_text`, `_looks_like_prose_continuation_artifact_text` | 壳 | |
| 1996-2083 | `_normalize_book_pdf_render_blocks`, `_repair_book_pdf_render_blocks` | 修补编排 | |
| 2085-2238 | `_extract_refresh_split_fragment_prose_text`（壳）, `_restore_bad_refresh_split_render_block`, `_append_refreshed_split_render_fragments` | refresh-split | 这段仍有状态逻辑留在服务里 |
| 2240-2348 | 约 20 个委托壳 | 壳 | |
| 2350-2400 | `_build_bilingual_html` | 渲染 | |
| 2402-2429 | `_export_epub_assets_for_document_bundle` | 资产 | |
| 2431-2527 | `_write_rebuilt_epub` | EPUB | 手写 OPF/nav |
| 2529-2629 | `_write_source_preserving_epub`, `_patch_source_preserving_epub_xhtml`, `_patch_epub_element_text`, `_clear_epub_*`, `_apply_source_preserving_epub_status_updates` | zh_epub | |
| 2631-2651 | `_render_rebuilt_pdf_from_html` | PDF | Playwright |
| 2653-2701 | `_export_epub_assets_for_chapter_bundle`, `_export_persisted_document_image_assets` | 资产 | |
| 2703-2746 | 文件名安全化、`_allocate_flat_asset_filename`, `_purge_legacy_nested_asset_dirs` | 资产 | |
| 2748-3016 | `_export_epub_assets`, `_export_epub_archive_assets`, `_export_pdf_assets` | 资产 | PDF 裁图主循环在 2876-3016 |
| 3018-3043 | 裁剪壳、`_materialized_document_image_path` | 资产 | |
| 3046-3196 | `DocumentRenderer` 与 `_DOCUMENT_RENDERERS` 表 | 渲染器注册 | 5 种整书类型 |

## 附录 B：`source_span_json` 中导出层读取的键（隐式 schema）
解析期：`pdf_block_role`(header/footer/toc_entry/body/code_like/table_like), `pdf_page_family`(body/frontmatter/appendix/references/index/backmatter/toc), `source_page_start/end`, `source_bbox_json.regions[{page_number,bbox}]`, `reading_order_index`, `anchor`, `source_path`, `href`, `tag`(math/svg/hN), `image_src`, `image_path`, `image_alt`, `image_width_px/height_px`, `linked_caption_block_id`, `linked_caption_text`, `caption_for_block_id`, `table_markdown`, `equation_render_mode`, `equation_latex`, `heading_level`/`source_heading_level`/`epub_heading_level`/`pdf_heading_level`, `footnote_anchor_matched`, `footnote_anchor_label`, `recovery_flags[]`, `translatable`, `nontranslatable_reason`。
修复期（`pdf_prose_artifact_repair` / `pdf_structure_refresh`）：`repair_hidden_from_export`, `repair_source_text`, `repair_target_text`, `repair_block_type`, `repair_skip_block_ids`, `mixed_code_prose_repair_targets[{split_kind, source_signature, target_text, target_segment_ids}]`, `refresh_split_render_fragments[{block_type, source_text, target_text, repair_target_text, repair_source_signature, source_metadata}]`。
导出期只在内存里追加：`recovery_flags` 的 `export_*` 标记、`artifact_group_context_block_ids`、`linked_caption_text/page`、`suppressed_artifact_block_ids`、`pdf_mixed_code_prose_split`。

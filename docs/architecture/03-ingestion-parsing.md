# 03 · 摄取、解析与分段

> 来源：2026-09-16 对分支 `refactor/p0-stabilize`（HEAD `2176d51`）的逐文件源码阅读。行号对应该基线；标注「待确认」的条目未经运行验证。总览与跨子系统结论见 [`00-overview.md`](00-overview.md)。

# book-agent 文档摄取 / 解析 / 分段 / bootstrap 子系统 —— 源码阅读笔记

> 基线：分支 `refactor/p0-stabilize`，HEAD `2176d51`。所有行号均取自该基线。
> 阅读方式：逐文件全文阅读（`pdf.py` 4509 行、`classify.py` 2312 行逐函数过）；测试部分由子代理汇总（见 §13）。
> 标注「待确认」的地方是我从源码推断但未跑代码验证的结论。

---

## 0. 模块地图与分层

```
app/api/routes/documents.py::bootstrap_document / bootstrap_uploaded_document   (HTTP 入口, 同步)
   └─ services/workflows.py::DocumentWorkflowService.bootstrap_document        (:161)
        ├─ orchestrator/bootstrap.py::BootstrapOrchestrator.bootstrap_document   (纯委托, 18 行)
        │    └─ services/bootstrap.py::BootstrapPipeline.run                     (:797)
        │         ├─ IngestService.ingest        → Document + JobRun(INGEST)
        │         ├─ ParseService.parse          → Chapter/Block/DocumentImage + ParseRevision(+sidecar)
        │         │     ├─ domain/structure/epub.py::EPUBParser
        │         │     ├─ domain/structure/pdf.py::PDFParser → ingestion/pdf/{extract,classify,chapters}
        │         │     ├─ ingestion/pdf/ocr.py::OcrPdfParser (PDF_SCAN / PDF_MIXED)
        │         │     ├─ services/modality_pipeline.py (env 开关, 默认关)
        │         │     └─ services/parse_ir.py::ParseIrService (canonical IR sidecar)
        │         ├─ SegmentationService.segment → Sentence
        │         └─ domain/context/builders (BookProfile / ChapterBrief / Packet, 不在本笔记范围)
        └─ infra/repositories/bootstrap.py::BootstrapRepository.save (session.merge 全量 upsert)
```

依赖方向（P3.3 拆分后）：`ingestion/text` → `ingestion/pdf/models` → `ingestion/pdf/classify` → `ingestion/pdf/extract` → `ingestion/pdf/chapters` → `domain/structure/pdf`。`domain/structure/pdf.py` 仍然 import 了 `ingestion.*` 里几十个下划线私有函数（`pdf.py:33-138`），domain 反向依赖 ingestion，分层只是物理拆文件，不是真正的依赖倒置。

---

## 1. 端到端数据流：上传文件 → `sentences` 表

### 1.1 入口

| 入口 | 位置 | 说明 |
|---|---|---|
| `POST /documents/bootstrap` | `app/api/routes/documents.py:109` | 传本地路径；同步执行整条链路后 `session.commit()` |
| `POST /documents/bootstrap-upload` | `documents.py:131` | 文件写到 `upload_root/<uuid4>/<safe_name>`，再走同一条链路；失败清理文件 |
| `tools/pdf_smoke.py::build_pdf_smoke_report` | `pdf_smoke.py:249` | 不落库，先 `PdfFileProfiler.profile` 再 `BootstrapOrchestrator().bootstrap_document`，可选再 `PDFParser().parse` —— 同一 PDF 抽取 3 次 |

### 1.2 IngestService（`services/bootstrap.py:199-245`）

- 输入：文件路径。`sha256(path.read_bytes())`（整文件读入内存，`:205`）作 `file_fingerprint`。
- `_detect_source_type`：按后缀。`.epub` → `SourceType.EPUB`；`.pdf` → 调 `PdfFileProfiler.profile(path)`（**完整 PyMuPDF 抽取一次**，含图片物化到临时目录，见 §9）→ `pdf_kind` 映射 `text_pdf→PDF_TEXT`、`mixed_pdf→PDF_MIXED`、其他→`PDF_SCAN`。其它后缀 `ValueError`。
- 输出：`Document(id=stable_id("document", fingerprint), status=INGESTED, metadata_json={"file_name", "pdf_profile"})` + `JobRun(INGEST, SUCCEEDED)`。
- 注意：Document id 只由内容哈希决定 → 同一文件二次上传得到同一 `document.id`，`merge` 后 `source_path` 指向最新上传目录，旧目录成为孤儿。

### 1.3 ParseService（`services/bootstrap.py:248-675`）

1. 按 `source_type` 选解析器（`:267-279`）：EPUB→`EPUBParser.parse`；PDF_TEXT→`PDFParser.parse(file, profile=dict)`（**第二次完整抽取**）；PDF_MIXED / PDF_SCAN→`OcrPdfParser.parse`（整本 Surya OCR，文本层完全丢弃）。
2. `resolve_document_titles`（`domain/document_titles.py:208`）决定 `title/title_src/title_tgt`；`document.src_lang = parsed.language`（PDF 固定 `"en"`，`pdf.py:291`）。
3. `_apply_modality_pipeline`（`:466-516`）：仅当 env 开关打开时跑 §6 的 pipeline，**异常静默吞掉**（`:488-489`，无日志）。
4. `ParseIrService.build`（`services/parse_ir.py:94`）：生成 canonical IR 节点/关系/projection hints，写 sidecar `artifacts/parse-ir/<document_id>/v<parser_version>/canonical-ir.json`（相对 CWD，`:91,:364`），返回**被 annotate 过的** `ParsedDocument`（每个 block/chapter metadata 加了 `parse_revision_id / canonical_node_id / canonical_parent_node_id / canonical_root_node_id`）。
5. 逐章 `_build_chapter` / 逐块 `_build_block` / `_materialize_pdf_block_relations` / `_build_document_image`。
6. `JobRun(PARSE, SUCCEEDED)`；失败路径不产生 JobRun（`:355-364` 仅在成功后构造）。

`_build_block`（`:518-571`）字段映射：

| Block 列 | 来源 |
|---|---|
| `id` | `stable_id("block", document.id, chapter.id, parsed_block.ordinal, source_path, anchor or "no-anchor")` |
| `ordinal` | `parsed_block.ordinal` |
| `block_type` | `BlockType(parsed_block.block_type)` |
| `parse_revision_id` / `canonical_node_id` | 从 metadata 取（ParseIrService 注入） |
| `source_text` | `parsed_block.text` 去 NUL |
| `normalized_text` | 空白折叠 |
| `source_anchor` | `f"{source_path}#{anchor}"` |
| `source_span_json` | `{"source_path","anchor", **metadata, "docir_translatability","docir_provenance","docir_confidence_breakdown","docir_style_hints"}` 递归去 NUL |
| `parse_confidence` | 默认 1.0 |
| `protected_policy` | `protected_policy_for_block(block_type, parsed_block.metadata)`（**只看 metadata 的 `translatable` 键 / role / page family / 块类型，不看 `ParsedBlock.translatability`**） |

`_build_document_image`（`:573-628`）：仅 IMAGE/FIGURE 且 metadata 有 int `source_page_start`（即 PDF）时建 `DocumentImage`，`storage_path` 若无则 `document-images/<doc>/<block>.<ext>`，`metadata_json.storage_status="logical_only"`（真正的字节由 export 阶段裁剪/物化）。

`_materialize_pdf_block_relations`（`:630-674`）：把 `linked_caption_source_anchor / caption_for_source_anchor / artifact_group_context_source_anchors / artifact_group_source_anchor` 通过 `source_anchor → block.id` 映射成 `*_block_id` 写回 `source_span_json`。同章内才能解析。

### 1.4 SegmentationService（`services/bootstrap.py:677-773`）

- 对每章按 `ordinal` 排序的 Block，`segmenter.segment_text(block.normalized_text or block.source_text)`；若类型 ∈ {HEADING, CODE, FOOTNOTE, CAPTION, FIGURE, EQUATION, IMAGE} 则整块一句（**先分了再丢弃**，`:714-719`，且 CODE 用的是空白折叠后的 `normalized_text`）。
- `translatability_for_block(block_type, source_span_json)` → `(translatable, reason, status)`；EPUB 纯图片块（`image_src` 且不可译）不产句子（`:727-728`）。
- Sentence id = `stable_id("sentence", document.id, block.id, segmentation_version, ordinal)`；`canonical_node_id = stable_id("canonical-sentence", block_canonical_node_id or block.id, ordinal)`。
- `source_span_json = {block_id, block_type, ordinal_in_block, parse_revision_id, canonical_node_id}`；`upstream_confidence = block.parse_confidence`；`sentence_status` = PENDING / PROTECTED / BLOCKED。

### 1.5 BootstrapPipeline.run（`:797-867`）与持久化

顺序：ingest → parse → segment → BookProfileBuilder → ChapterBriefBuilder → ChapterTranslationMemoryBuilder → ContextPacketBuilder → 章节 `PACKET_BUILT`、文档 `ACTIVE`。所有对象在内存里构造完毕后由 `BootstrapRepository.save`（`infra/repositories/bootstrap.py:51-85`）逐个 `session.merge` + `flush`：document → parse_revision → artifact → chapters → blocks → sentences → book_profile → memory_snapshots → packets(+PACKET_BUILT 事件) → packet maps → job_runs → document_images。

- 全部是 upsert，**从不删除**旧行。重新 bootstrap 同一文件（同 id）时若解析结果的 block id 变了（见 §2.4），旧 block / sentence 仍以 `ACTIVE` 留在库里（待确认：是否有前置校验阻止重复 bootstrap；`file_fingerprint` unique 只保证 document 行唯一）。
- 单事务、单进程、同步，`session.commit()` 在路由层。

### 1.6 落库表清单

| 表 | 模型 | 关键约束 |
|---|---|---|
| `documents` | `domain/models/document.py:30` | `file_fingerprint` unique；`parser_version`/`segmentation_version` 默认 1，**代码里没有任何写入者会递增它们**（grep 结果只有读取和克隆） |
| `chapters` | `:55` | `(document_id, ordinal)` unique；`anchor_start/anchor_end` |
| `blocks` | `:80` | `(chapter_id, ordinal)` unique；`parse_revision_id` FK SET NULL；`source_span_json`(JSON) 是事实上的 IR 载体 |
| `sentences` | `:115` | `(block_id, ordinal_in_block)` unique；`translatable`、`nontranslatable_reason`、`sentence_status`、`active_version` |
| `document_images` | `:216` | `block_id` FK SET NULL；`storage_path`、`bbox_json` |
| `document_parse_revisions` | `domain/models/parse_revision.py:12` | `(document_id, version)` unique；`canonical_ir_path/checksum`、`projection_hints_json`（**整份 hints 冗余存 JSON 列**） |
| `document_parse_revision_artifacts` | `:41` | `(revision_id, artifact_type)` unique；类型固定 `"canonical_ir_sidecar"` |
| `job_runs` / `memory_snapshots` / `book_profiles` / `translation_packets` / `packet_sentence_maps` | 同事务写入，不属本笔记 |

### 1.7 Blob / 文件存储

- `infra/storage/blobs.py` **不是摄取侧的存储**：它是 export 产物的内容寻址（CAS）层——`blob_target = <blob_root>/<aa>/<bb>/<sha256>`（`:57-59`），`stamp_and_materialize` 对 `exports` 行打 `content_sha256/byte_count/last_verified_at`，硬链接失败时退化为 copy，全程 best-effort 不抛异常（`:96-143`）。`blob_root = export_root 的父目录/blobs`（`:146-149`）。
- 摄取侧没有内容寻址：上传文件放 `upload_root/<uuid4>/<filename>`；PDF 内嵌图片 PNG 物化到 `tempfile.mkdtemp(prefix="book-agent-pdf-images-")`（`ingestion/pdf/extract.py:270`，**从不清理**，且绝对路径被写进 `source_span_json.image_path`，`pdf.py:657`）；canonical IR sidecar 写在相对路径 `artifacts/parse-ir`。三处路径策略互不一致，且都不在 `Settings` 里（`upload_root/export_root` 在，其余不在）。

---

## 2. IR 结构

### 2.1 `ParsedDocument / ParsedChapter / ParsedBlock`（`domain/structure/models.py`）

```
ParsedDocument(title, author, language, chapters: list[ParsedChapter], metadata: dict)
ParsedChapter(chapter_id, href, title, blocks, metadata)
ParsedBlock(block_type: str, text, source_path, ordinal, anchor: str|None,
            metadata: dict, parse_confidence: float|None,
            translatability: str = "translate_all",      # TRANSLATE_ALL|PROSE_ONLY|NONE  (:9-15)
            provenance: str = "text_layer",              # text_layer|ocr|vlm|hybrid       (:19-26)
            confidence_breakdown: dict, style_hints: dict)
```

全部 `frozen+slots`，值类型用 str 而非 Enum 是为了 JSON 往返（`:5-8`）。`style_hints` 在整个代码库里**没有任何写入者**（M1.1 计划字段，未落地）。`TRANSLATE_PROSE_ONLY` 无人产出。

`derive_translatability(block_type, metadata)`（`:44-87`）顺序：metadata `translatable is False` → NONE；块类型 ∈ {code, table, figure, equation, image} → NONE（即使 `translatable=True`）；无 `translatable` 键时 role ∈ {header, footer, toc_entry} 或 family=="backmatter" → NONE；否则 ALL。与 `domain/block_rules.py:12-51` 的 DB 侧判定是**手工镜像**（两份代码，注释承认不能合并）。

### 2.2 `BlockType`（`domain/enums.py:38`）

`heading, paragraph, quote, footnote, caption, code, table, list_item, figure, equation, image`。PDF 内部 role→type 映射（`pdf.py:1219-1236`）：`heading→HEADING, footnote→FOOTNOTE, caption→CAPTION, equation→EQUATION, code_like→CODE, table_like→TABLE, list_item→LIST_ITEM, image→IMAGE, figure→FIGURE(仅聚类产出)`，其余（`body, header, footer, toc_entry`）→ PARAGRAPH。**header/footer/toc_entry 都以 PARAGRAPH 类型入库**，只靠 `source_span_json.pdf_block_role` 区分。QUOTE 只有 EPUB 产出。

### 2.3 保护 / 可翻译性标记（三套并存）

| 层 | 字段 | 写入者 | 读取者 |
|---|---|---|---|
| DocIR | `ParsedBlock.translatability` | `chapters.py:139`（derive）、EPUB parser、modality pipeline（references/equation/table/image 强制 NONE） | `image_modality.enhance_document_image_modality` 计数；`bootstrap._build_block` 写成 `docir_translatability` |
| metadata 键 | `metadata["translatable"]`, `nontranslatable_reason` | `chapters.py:104-114`（role/backmatter）、`pdf.py:2051-2052,2078-2079,2118-2119`（listing 注释/代码）、EPUB 图片/figure | `block_rules._forced_translatability`（DB 侧权威） |
| DB | `Block.protected_policy`（TRANSLATE/PROTECT/MIXED）、`Sentence.translatable/nontranslatable_reason/sentence_status` | `bootstrap._build_block`、`_build_sentences`、refresh | 翻译 packet 构建（`block_is_context_translatable`）、export（`render_repair.py:1675-1679`、`alignment.py:150`） |

**关键事实**：`source_span_json.docir_translatability` 写入后**零读取者**（全库 grep 仅 `bootstrap.py:537`）。因此 modality pipeline 给 References 段落打的 `TRANSLATE_NONE` 到不了 `protected_policy` 与 `Sentence.translatable`（References 页家族 `"references"` 不在 `_NONTRANSLATABLE_PDF_PAGE_FAMILIES={"backmatter"}` 里），参考文献仍会被当散文翻译。表格/公式/图片因块类型本身受保护不受影响。`ProtectedPolicy.MIXED` 没有任何产出者（只在 export 读）。

### 2.4 anchor / block id 方案（PLAN「锚点方案单独立项」的现状）

**PDF**（`pdf.py:_recover_blocks:553-682`）：
- `reading_order_index` 是**全文档级**计数器，每个原始文本块或图片块 +1（`:572`）。
- 文本块 `anchor = f"p{page}-b{reading_order_index}"`（`:636`），图片 `p{page}-img{idx}`（`:678`），聚类图 `p{page}-fig{primary_idx}`（`:3637`），纯文字图 `p{page}-tfig{idx}`（`:3931`）。
- 后续 pass 派生锚点靠后缀：`-s{n}`（嵌入标题/摘要/学术节切分 `:3087,3107,3125,3145,3287,3308,3379`）、`-title`（`:2808`）、`-trailing-prose`（`:2352`）、`-code`/`-body`（`:2099,2110`）、`-bodysplit`（`:2017`）。合并时保留前块锚点（`:1515,1648`）。
- `source_path = f"pdf://page/{page}"`；章节 `href = f"pdf://page/{start_page}"`、`chapter_id = f"pdf-chapter-{n:03d}"`（`chapters.py:93-94`）。

**为什么脆弱**：
1. 锚点编号取决于整本书前面所有页的原始块数量。前面任何一页多识别/少识别一个 vector drawing 或被 `find_tables` 合并了几个块，后面所有页的锚点整体位移；`pdf_structure_refresh` 以 `source_anchor` 匹配旧块（`pdf_structure_refresh.py:111-127,200-203`），位移后全部 `skipped`（且不创建新块，见 §8）。
2. `Block.id` 混入 `parsed_block.ordinal`（`bootstrap.py:544-551`），`Chapter.id` 混入 `ordinal + href`（`:386`）；章节切分点或前序块数变化 → 之后所有 id 变化 → 句子 id（含 block.id）全变 → packet / 译文 / 对齐边全部失联。
3. 同一块可能有多次派生（`X-s2-s1`）；`_split_mixed_code_prose_block` 在 `split_mixed_code_prose` 与 `split_mixed_code_prose_again` 两个 pass 里跑两次，代码块保留原锚、散文块得 `-trailing-prose`；理论上可产生重复 `-trailing-prose` 锚点（待确认，需同一块两次被切）。`existing_block_by_source_anchor` 字典遇重复锚点后者覆盖前者。
4. 锚点不含任何几何/文本指纹，无法在重解析后做模糊重定位。
5. 章节 `anchor_start/anchor_end = href#first_block_anchor`（`bootstrap.py:390-391`）——href 是章首页，block 锚点可能来自后面几页，语义上是混合值。

**EPUB**（`epub.py:_extract_blocks:880-938`）：`anchor = element.id` **或从祖先继承**（`:886`），因此同一 `<section id=...>` 下所有 `<p>` 共用同一锚点；`source_anchor` 不唯一，唯一性完全靠 ordinal。figure 派生 `<base>-figure` / `<base>-caption`（`:960-961`，base 可为 `figure-{ordinal}`）。EPUB refresh 用 `(chapter.ordinal, href)` + `block.ordinal` 匹配（`epub_structure_refresh.py:90-140`）。

### 2.5 PDF block metadata 键清单（`source_span_json` 中可见）

来自 `chapters.py:96-115`：`source_page_start, source_page_end, source_bbox_json={"regions":[{page_number,bbox}]}, reading_order_index, pdf_block_role, recovery_flags, translatable, nontranslatable_reason`；
来自 `pdf.py:_metadata_for_block:1238-1290`：`pdf_page_family, pdf_page_family_source, pdf_page_family_heading, pdf_page_content_family, pdf_page_backmatter_cue(_source), footnote_segment_count/roles, detected_by, heading_level, toc_title, toc_page_number, toc_heading`；
来自 `_recover_blocks:590-611`：`pdf_all_lines_bold, pdf_font_names, pdf_leading_emphasis_text, has_monospace_font, raw_text`；图片：`image_type, image_ext, image_width_px, image_height_px, image_path, image_xref`；
pass 产出：`footnote_anchor_label/matched/block_anchor/page/reading_order_index, footnote_relocation_modes, pdf_heading_recovery_source, pdf_academic_heading(_kind), pdf_academic_section_level, pdf_prose_artifact_repaired_from_role, pdf_late_artifact_promotion, pdf_mixed_code_prose_split, pdf_split_source_anchor_base, pdf_listing_annotation_suppressed, pdf_contextual_image_legend, figure_cluster{...}, figure_anchor, linked_caption_text/source_anchor/page, caption_for_source_anchor/page/role, image_alt, artifact_group_context_source_anchors, artifact_group_source_anchor/role, toc_page_number_printed/resolved/offset, toc_page_resolution_source`；
ParseIr 注入：`parse_revision_id, canonical_node_id, canonical_parent_node_id, canonical_root_node_id`；bootstrap 注入：`source_path, anchor, docir_*, linked_caption_block_id, caption_for_block_id, artifact_group_context_block_ids, artifact_group_block_id`；refresh 注入：`refresh_sentences_stale, refresh_invalidated_at/reason, refresh_split_render_fragments`；修复注入：`repair_*`。

没有任何 schema / 文档约束这些键；导出层按字符串键消费。

### 2.6 Canonical IR（`domain/structure/canonical_ir.py`, `services/parse_ir.py`）

`CanonicalDocumentIR(schema_version=1, document_id, revision_id, root_node_id, source_type, nodes, relations, projection_hints, page_plans, metadata)`。节点类型只有 `document / page_extraction_plan / chapter / block`（没有 sentence 节点；sentence 的 `canonical_node_id` 是 bootstrap 侧派生、IR 文件里不存在）。关系只有 `contains`。`page_plans` 从 `pdf_page_evidence` 推出 `intent ∈ {native_text, ocr_overlay, hybrid_merge}`（`parse_ir.py:56-64`）——**纯遥测，没有任何执行者**（设计文档 M2.2 的 `extraction_router.py` 在当前树里不存在）。

`revision_id = stable_id("parse-revision", document.id, parser_version, 1)`（`:96`）；`parser_version` 永远是 1 → 每次重解析（含 refresh）都覆盖同一个 sidecar 文件；refresh 丢弃新的 `parse_revision` 行（`pdf_structure_refresh.py:107` 后未使用 `parse_artifacts.parse_revision`）→ DB 里的 `canonical_ir_checksum` 与磁盘文件立即不一致。`ParseRevisionStatus.SUPERSEDED/INVALIDATED` 无写入者。

---

## 3. PDF 解析管线

### 3.1 Profiler（`ingestion/pdf/extract.py::PdfFileProfiler:1294-1444`）

先完整抽取，再从 `PdfExtraction` 算：

| 输出 | 规则 |
|---|---|
| `pdf_kind` | 页面文本 ≥32 字符算"有字"；`text_ratio<0.2` 或无字→`scanned_pdf`；`<0.9`→`mixed_pdf`（`ocr_required=True`）；否则 `text_pdf`（`:1336-1344`） |
| `suspicious_page_numbers` | 每页 `_page_has_multi_column_signature` 或 `_page_has_column_fragment_signature` |
| `academic_paper_candidate` | text_pdf 且 ≤24 页 且尾部参考文献页≥1 且（pymupdf: 多栏页≥2 或有 outline 或单栏学术首页信号）且（前两页有居中标题信号或有 metadata title）（`:1364-1384`） |
| `outlined_localized_multi_column_book` | ≥80 页、有 outline、可疑页 2..max(12, min(40, 15%)) 且占比≤12%、无 fragment 页 |
| `layout_risk` | scanned→high；mixed→high/medium；academic/outlined/basic_fragment→medium；可疑页≥2→**high**；1 页→medium；否则 low |
| `recovery_lane` | `academic_paper` / `outlined_book` / None |

问题：图多字少的书（图页 >10%）被判 `mixed_pdf` → 整本走 Surya OCR、文本层丢弃（见 §7）。普通书只要两页被判多栏就是 `layout_risk=high` → 每章 `risk_level=CRITICAL`（`bootstrap.py:188-189`）。

### 3.2 抽取层 `PyMuPDFTextExtractor.extract`（`extract.py:262-455`）

逐页 `page.get_text("dict", sort=False)`：
- type 1 块 → `PdfImageBlock`，并 `_materialize_embedded_image`（`:640-715`）：`page.get_images(full=True)` × `get_image_rects` 找中心最近的 xref，`fitz.Pixmap` 存 PNG（CMYK 转 RGB），按 xref 去重。
- type 0 块：逐 line 逐 span 拼接，`_normalize_text` **折叠行内多空格**（`:358`）；记录 `line_styles=(主字号, 整行粗体)`、`line_bboxes`、`font_names`、单字体 mono 占比；`_join_detached_enumerator_lines`（`:160-198`，独占一列的 "1." 与下一行合并）；mono span ≥70% 且 ≥2 行时用 `page.get_text("text", clip=bbox)` 取保留缩进的 `raw_text`（`:374-387`）。
- 页级后处理顺序：`_split_vector_bullet_blocks`（矢量圆点 → 在行首加 "• " 并按行切块，`:96-153`）→ `_split_enumerated_line_blocks`（每行都是连续编号 → 逐行切块，`:209-240`）→ `_merge_ruled_table_blocks`（仅当 `_has_ruling_grid` ≥3 横 ≥2 竖线才调 `page.find_tables()`，把表内块替换成 `"| a | b |"` 行的单块，`ruled_table=True`，`:497-554`）→ `_extract_vector_drawing_blocks`（`get_drawings` 矩形过滤+聚类+外扩，`image_type="vector_drawing"`，`:457-495`）→ 文本层 sanity（§7）。
- outline：`document.get_toc()`，`page_number=max(1, page)`（`:729`）—— 未解析目标（-1）被强制成第 1 页。
- 元数据：`document.metadata` 非空项 + `pdf_extractor="pymupdf"`。

`BasicPdfTextExtractor`（`:735-1266`）：正则解析 PDF 对象流（Flate 解压，`BT..ET` 段，Tj/TJ），估算 bbox；仅当 PyMuPDF 抽取抛 `RuntimeError` 时启用（`DefaultPdfTextExtractor:1281-1291`）。**PyMuPDF 1.27 的 `FileDataError` 继承自 `RuntimeError`（已验证）**，所以损坏/加密 PDF 会静默退到 Basic 抽取器产出垃圾结构，而不是报错。macOS 下还会通过 ctypes 调 CoreGraphics 数页数（`:804-843`）。

### 3.3 恢复层 `PdfStructureRecoveryService.recover`（`pdf.py:205-294`）

```
ordered_pages → RecoveryContext(page_contexts, page_layout_assessments)
→ _recover_blocks(pages, repeated_edge_text, ...)      # 逐页排序、分类、同页/跨页 body 合并、脚注续接
→ for pass in _BLOCK_RECOVERY_PASSES: blocks = pass.run(...)   # 22 个 pass，见 3.5
→ pdf_chapters.build_chapters(...)                      # 3.6
→ _repair_academic_first_page_abstract_continuations    # 学术 lane 首页摘要搬回标题页
→ _apply_ocr_reextraction (可选适配器)                    # §7
→ 文档标题推断 + pdf_page_evidence 遥测
```

#### 3.3.1 页级上下文 `_page_context`（`:1034-1136`）与 `_page_contexts`（`:929-994`）

每页产出 `_PageRecoveryContext(is_toc_page, page_family ∈ {body,toc,frontmatter,appendix,references,index,backmatter}, family_source, family_heading, content_family, backmatter_cue, has_strong_heading, toc_entries_by_text)`。

| 规则 | 条件 | 位置 |
|---|---|---|
| 强标题页 | 非底部、≤120 字符、匹配 `^(chapter|part|appendix)` 或字号 ≥ 页中位 ×1.25 | `:1055-1058` |
| family by heading | 首个 ≤120 字符且字号 ≥ 中位 ×1.15 的块，`_page_family_for_heading`：appendix 前缀 / references 标题集 / index 标题集 / 前 6 页的 frontmatter 标题集 | `:1064-1069`, `classify.py:708-720` |
| inline family | 无 heading 时用首个正文 `_inline_page_family_heading` | `:1075-1090` |
| TOC 页 | (有 "contents" 标题且有条目) 或 条目≥3 或 dense-toc 行≥3 或 … | `:1091-1097` |
| references 内容族 | `_looks_like_reference_entry` ≥2，或 ≥1 且引用标记≥2 且年份≥2 | `:1098-1106` |
| index 内容族 | index-like ≥3 且占比 ≥60% | `:1107-1108` |
| appendix intro | 首段含 "this appendix" 且能推出标题 | `:1112-1117` |
| 跨页传播 | 后向扫描"后面是否还有强标题"；`references/index` 内容族无强标题时升级为 family；`index/appendix` 之后剩余 ≤8/≤6 页且有 backmatter cue → backmatter；backmatter/appendix 向后延续 | `:940-993` |

`_split_toc_entry`（`classify.py:426`）正则要求 `\.{2,}` 点线或 `\s{2,}`；但抽取层已折叠空白，**无点线的目录无法被识别为条目**（`_dense_toc_line_count` 的 `\S\s{2,}\S` 同理失效）。

#### 3.3.2 页眉页脚 `_find_repeated_edge_text`（`:541-551`）

块 bbox 完全落在页高前 12%（top）/后 12%（bottom）区（`_page_zone:1138`），签名 = 小写、数字→`#`、去标点；跨页出现 ≥2 次 → header/footer。纯页码（`^(page\s+)?(\d+|[ivxlcdm]+)$`）在底部直接 footer。局限：只看 12% 固定带；两行页眉超出带即漏；只出现一次的章名页眉→body；「Page 3 of 12」不算页码。

#### 3.3.3 阅读顺序与多栏（`_ordered_page_blocks:714`, `_column_major_blocks:729-803`）

默认 (y,x) 排序；`_page_has_multi_column_signature`（`classify.py:2086-2116`：≥4 个 ≥40 字符且宽 ≤70% 的块，其中宽 ≤58% 的块左列 x0≤22%、右列 x0≥45% 各 ≥2，且右列最小 y < 左列最大 y）触发列优先重排：左列 x0≤30%、右列 x0≥45%；宽块位于列顶上方→组 0、列底下方→组 3，夹在中间的宽块直接放弃重排（返回 None，静默退回 y 序）。**三栏**：中栏块 x0 在 30%~45% 之间既不归左也不归右，排序时落到组 0/3 → 顺序错乱（golden 只断言签名触发，不断言顺序）。学术 lane 图片全部排在文本之后（`:691-695`）。

#### 3.3.4 角色分类 `_classify_role`（`:1147-1217`）——顺序即优先级

| # | 结果 | 条件 | 备注 |
|---|---|---|---|
| 1 | table_like | `raw_block.ruled_table` | find_tables 产物 |
| 2 | header | top 区且签名重复 | |
| 3 | footer | bottom 区且（页码 或 签名重复） | |
| 4 | footnote | top 区、字号 < 中位×0.95、`^(\d+|[*†‡])([.)]|\s)` | 顶部脚注（续接） |
| 5 | toc_entry | TOC 页且文本是 "contents" 标题 | |
| 6 | toc_entry | TOC 页且文本命中条目表 | |
| 7 | footnote | bottom 区、小字号、脚注前缀 | "2019 was…" 也会命中 |
| 8 | caption | `_looks_like_caption_text`：`^(figure|fig\.?|image|diagram|chart)\s*<idx>` / `^table\s+<idx>` / `^eq…` | 在 outline 之前 |
| 9 | heading | 归一化文本 ∈ 本页 outline 标题 | |
| 10 | heading | `^(chapter|part|appendix)\b`（IGNORECASE）且 ≤150 字符 | "Part of the reason…" 段落会命中 |
| 11 | heading | `font_size_max ≥ 中位×1.25` 且 ≤120 字符 且 `_looks_like_visual_heading` 且非底部 | 英文大小写启发 |
| 12 | table_like | `_looks_like_table(line_count, line_texts)` 或 `_looks_like_numeric_table_fragment` | 空白折叠后 `\S\s{2,}\S` 分隔符永远不匹配，只剩 `|` 与数字密度 |
| 13 | equation | `_looks_like_equation`：1–3 行、含运算符、≥3 数学标点、≤18 token、居中/内缩 | |
| 14 | list_item | TOC 页 dense-toc 块 | |
| 15 | list_item | `_looks_like_list_item`：`_LIST_BULLET_PATTERN`（IGNORECASE：`[ivxlcdm]+[.)]\s+` 会把 "Civil. …"、"Did. …" 当列表项） | ≤8 行 |
| 16 | code_like | `_looks_like_code(text, line_count)`（§3.9） | |
| 17 | code_like | 全 mono 字体 ≥2 行且无正文字体 | 字体名正则 |
| 18 | body | 兜底 | |

之后 `_recover_blocks` 还会：全行粗体且紧接粗体标题下一行 → heading（`:583-589`）；`metadata_for_block`（`:1238`）算 `heading_level`（`_book_heading_level`：chapter/part 前缀→1，编号深度→max(2,深度)，appendix 子节→2+深度）；`_parse_confidence_for_role`（`:1311`）= 按 layout_risk 取 0.96/0.82/0.65 的基线 ± 小修正——**"置信度"与识别证据无关，只是文档级风险的函数**。

#### 3.3.5 同页 / 跨页正文合并 `_should_merge`（`:1321-1361`）

前块 role ∈ {body, list_item} 且当前 body；同页：垂直间距 ≤ max(1.8×字号, 18pt) 且前块以连字符结尾或不以句末标点结尾 → 合并（list_item 也会吞掉后面的段落）；跨页（相邻页）：前块底 ≥ 72% 页高、当前顶 ≤ 28%，`-`+小写 或 小写开头 → 合并。合并（`_merge_blocks:1467`）：去连字符、`cross_page_repaired` flag、**metadata 后者覆盖前者**（`:1490`），confidence 取 min。这里被 `_should_keep_inline_book_heading_separate`（字号更大的短行不并）与 `_looks_like_contextual_image_legend_block`（图旁说明不并）拦截。

`_merge_footnote_continuations`（`:2623-2674`，在 `_recover_blocks` 末尾）：脚注后紧跟的 body/footnote，标记一致、字号不大于 ×1.15、间距/跨页位置合规、小写或标点开头 → 合并；识别 `same_page_body_segment` / `cross_page_body_segment` 搬迁模式并计 `footnote_segment_count`。

### 3.4 `_BLOCK_RECOVERY_PASSES`（`pdf.py:4479-4509`）逐项

| # | name | 方法 | 作用 | 备注 |
|---|---|---|---|---|
| 1 | link_footnotes | `_link_footnotes:3491` (in place) | 提取脚注标记，向前在同页/前一页 body/heading/caption 尾 160 字符找 `[n]`/`(n)`/词尾 n 锚点；写 `footnote_anchor_*`，找不到打 `footnote_orphaned` | |
| 2 | promote_inline_book_headings | `_promote_inline_book_heading_blocks:2914` | 短 title-case 段落（≤10 词、`_looks_like_visual_heading`）且字号比下一段大 ≥8%/0.6pt、或位于页顶 ≤140pt、或 TOC 标题 ≤220pt → heading | 纯字号/位置启发 |
| 3 | promote_contextual_image_legends | `_promote_contextual_image_legend_blocks:2986` | "This/These/The … <数字>" 3–9 词、居中窄块、贴着图片 → caption(`pdf_contextual_image_legend`) | |
| 4 | recover_embedded_page_headings | `_recover_embedded_page_heading_blocks:2694` | (a) 有字体粗细信息时把全粗体编号列表项 / 短粗体行升为 heading；(b) 每块 `_split_embedded_page_heading_segments`（`:3009`）：首页摘要切分、`References` 前缀、首页论文标题、学术内联标题（仅学术 lane）、粗体前缀 `styled_heading_and_remainder`、编号书标题、全大写小节、无字体信息时的词形猜测 | 产出 `-s1/-s2` 锚点，级别 1/2/4 |
| 5 | recover_document_title_headings | `_recover_document_title_heading_blocks:2751` | 首页 body 与 `extraction.title` 重叠 → 替换成标题 heading（`-title`） | |
| 6 | recover_academic_sections | `_recover_academic_section_blocks:2676` | 学术 lane：段内反复找 `N[.N] Title` 内联标题切段（`-s{n}`），重编 reading_order_index | |
| 7 | populate_missing_heading_levels | `_populate_missing_heading_levels:2818` | 补/修 heading_level：非首页非章前缀的 level1 降 2；Introduction/Overview/Conclusion 页首升 1；单词 plain heading 紧随 level2 → 3 | |
| 8 | merge_heading_continuations | `_merge_adjacent_heading_continuations:1518` | 相邻 heading 合并：粗体同字号紧贴（`_is_styled_heading_line_continuation:4445`）或续行片段（小写/介词开头、字号比 0.8–1.25、同页间距/48pt 左对齐，跨页 72%/28%） | 谓词内**修改 flags**（`:1562,1619`） |
| 9 | repair_prose_artifact_continuations | `_repair_prose_artifact_continuations:1651` | body 未以句末标点结尾 + 紧随的 code/table 块看起来像散文续写（停用词密度）→ 降回 body 并合并 | |
| 10 | merge_same_anchor_code | `_merge_same_anchor_code_continuations:1678` | 同锚点相邻 code 块合并 | 何时会出现同锚点相邻 code？只有前面 pass 切分后（待确认） |
| 11 | merge_cross_page_code | `_merge_cross_page_code_continuations:1694` | 相邻页 code 块：前底 ≥68%、后顶 ≤34%、横向重叠或左边距差 ≤96pt；续行/未闭合括号引号/shell 续行/兜底"都像代码且有缩进" | 跳过 header/footer/页码 |
| 12 | merge_cross_page_prose | `_merge_cross_page_prose_continuations:2131` | 相邻 1–2 页 body：前块末字符是逗号/连字符/字母，后块小写开头；跳过 header/footer/footnote/页码/`_is_book_running_header_text` | 不检查 bbox 位置 |
| 13 | split_mixed_code_prose | `_split_mixed_code_prose_blocks:2266` | 块前缀若干行像代码 + 后缀像散文 → 拆 code + paragraph(`-trailing-prose`, `pdf_split_source_anchor_base`) | |
| 14 | promote_late_code_bodies | `_promote_late_code_like_bodies:2356` | body 整体像代码、或像 docstring/注释且 ±3 块内有代码 → CODE | |
| 15 | split_mixed_code_prose_again | 同 13 | 对 14 的产物再切 | |
| 16 | promote_late_table_bodies | `_promote_late_table_like_bodies:2418` | body 像表且同页 ±3 块内有 Table 图注或 table 块 → TABLE | |
| 17 | merge_table_fragments | `_merge_adjacent_table_fragments:2471` | 同页相邻 (索引差 ≤2、间距 ≤54pt、水平重叠) 或跨页（70%/30%、列数差 ≤2）table 合并，行用 `\n` 连接 | 谓词内修改 flags（`:2573`） |
| 18 | lock_listing_scope | `_lock_listing_scope:1822` | **Manning 专属**：`^Listing \d+.\d+` 起 16 块内，把代码尾注释拆回、注释段标 `pdf_listing_annotation_suppressed + translatable=False`、关键字起始段落改 code | 硬编码出版社约定与英文动词表 |
| 19 | figure_clustering | `_apply_figure_clustering:3531` → `figure_clustering.cluster_figure_regions` | 同页 image/vector 锚合并（间距 ≤24pt、中间无整句），吸收中心落在锚内的 ≤200 字符短文本为内联标签，选最近的 caption 链接；生成 FIGURE 块（union bbox），去掉被吸收块 | 单锚无标签时跳过（保持 IMAGE） |
| 20 | recover_text_only_figures | `_recover_text_only_figures:3696` | 未链接的 "Figure N.M" 图注：先与同页上方未链接图配对；否则把图注上方 ≥3（无图页 ≥2）个短块（≤240 字符、宽 ≤380pt、纵向跨度 ≤240/480pt、与上方长段落间距 ≥6pt）合成 `text_only_figure` FIGURE | 会吞掉"软"标题 |
| 21 | link_artifact_captions | `_link_artifact_captions:4014` (in place) | image/table/equation/figure 与 caption 配对：同页下方 [-48(-12),120]pt、上方 [-12,80]pt、次页顶部 ≤220pt；写双向 metadata | O(制品数 × 块数) |
| 22 | link_artifact_group_contexts | `_link_artifact_group_contexts:4059` (in place) | 图/表/公式 + 图注簇下方 ≤96pt、阅读序差 ≤4 的一段"说明性散文"（`artifact_grouping.looks_like_artifact_group_context_text`）标为 group context | 学术 lane 更宽松 |

pass 间不共享缓存；`_looks_like_code`、`_expanded_code_candidate_lines` 等对同一块反复计算。

### 3.5 章节构建 `ingestion/pdf/chapters.py`

`chapter_start_candidates`（`:282-320`）候选来源与优先级：
1. `section_family_candidates`（有 family 的 heading）+ `section_family_page_candidates`（family 变化页、appendix 子节）；
2. 学术 lane 加 `find_academic_heading_candidates`（level-1 节标题）；
3. 有 outline → `top_level_outline_entries`（取 `min(level)` 那一层；若 ≥2 条像主章节则过滤掉非章节/非特殊标题；`outlined_book` lane 再过滤）→ 与 1 合并后**直接返回**；
4. 否则 TOC 页条目 `find_toc_candidates`（`:727`，只接受 `^(chapter|part|appendix)` 标题；页码用标题匹配 / 目录偏移 / 页脚偏移解析）；
5. 否则章节导语页（"this chapter covers"）+ `chapter|part|appendix` 开头的 heading。

`build_chapters`（`:51-279`）单遍扫描：块页号到达下一个候选页 → flush；候选来自 outline/toc/academic 且本页已有重叠标题时延后到那个 heading；第一个候选前的块若像前置页（`looks_like_frontmatter_chunk`：≥2 个 preface/copyright 等信号）→ "Front Matter" 章，否则**直接丢弃**（`:216-219`，无任何日志/遥测）。空章（只有 header/footer/toc）丢弃。标题：候选标题 > 本页 heading > `fallback_chapter_title`。`outlined_book` lane 再 `merge_outlined_book_auxiliary_chapters`（按主章节号序列折叠辅助章）。无章节时兜底一章 "Document"。

局限：outline 层级取最小层（Part 为顶层的书只按 Part 切）；outline 页号 -1 被抽取层改成 1；候选页无 heading 时章节标题来自 outline 文本，与页面文本无校验；每章 `ParsedBlock.ordinal` 从 1 重编。

### 3.6 `_repair_academic_first_page_abstract_continuations`（`:407-477`）

学术 lane、首页既是标题页又是正文首章时，把第一章里位于 Introduction 标题之上/小写开头的段落搬回标题页章，重编 ordinal。

### 3.7 文档标题（`:255-265`）

学术：`extraction.title > 首页 heading 推断 > 首章标题 > 文件名`；书：`extraction.title > 推断`，若像 auxiliary（Preface/Chapter…）则改用清洗后的文件名（`document_titles.cleaned_filename_book_title`：去 `_`、去 z-library 等站点噪声括号、泛用词 stem 返回 None）。

### 3.8 `pdf_page_evidence`（`:805-927`）

每页：原始块数、恢复块数、family 及来源、layout signals/risk、`extraction_intent`（`classify._page_extraction_plan`）、role 计数、flags、toc 条目、脚注计数。存进 `document.metadata_json.pdf_page_evidence`（**整本书每页一条 dict 存在 documents 表 JSON 列**）并生成 canonical IR page_plans。

### 3.9 `ingestion/text.py` 行级启发式（被 PDF 各层反复调用）

| 函数 | 规则要点 | 已知误判 |
|---|---|---|
| `_normalize_text:257` | 去软连字符/零宽字符，折叠空白 | 不做 NFKC，`ﬁ ﬂ` 连字原样保留（全库无 unicodedata） |
| `_normalize_multiline_text:262` | 行尾 `-`/`‐` 与下一行字母数字直接拼接 | "well-\nknown" 正确，"pre-\nexisting" 变 "preexisting"（无词典） |
| `_looks_like_code:452` | 行计数：import/控制语句正则、`{ } => :: ->`、`=`、`;`、shell 命令、装饰器、注释/docstring、`_looks_like_embedded_code_line`；≥2 整句且无代码信号→否；组合阈值多条 | `_CODE_CONTROL_LINE_PATTERN` 带 **IGNORECASE**（`:119`）：英文 "If you …:"、"Return …"、"Pass …"、"For … in …:"、"Try:"、"Else:" 都算关键字行；`=` 出现在散文即算赋值行 |
| `_looks_like_embedded_code_line:686` | 单行像代码（key: value、含 `{`、`=` 且不以句末标点结尾、`foo.bar(`…） | "Note: …" 靠 `_looks_like_labeled_prose_line` 豁免（标签表英文） |
| `_looks_like_list_item:412` | `_LIST_BULLET_PATTERN`（IGNORECASE） | 见 3.3.4 #15 |
| `_looks_like_table:887` | 单行→`_looks_like_flattened_table_text`（≥6 数字 + 英文表头词）；多行→分隔符行≥2 / 数字行 / 密集数字 | 分隔符 `\S\s{2,}\S` 在 PyMuPDF 路径永不匹配 |
| `_looks_like_equation:1012` | 见 3.3.4 #13 | |
| `_looks_like_caption_text:1098` | Figure/Table/Eq + 编号 + (标点 或 大写词) | 中文/其它语言无 |
| `_looks_like_sentence_prose_line:787` | ≥6 词、停用词占比 ≥1/6、句末标点或大写开头 | 英文停用词表 |
| `_dense_toc_line_count:424` | 行尾页码 + 点线≥3 或双空格 | 双空格失效 |

### 3.10 `classify.py` 函数清单（2312 行，全部模块级函数，全英文启发式）

| 行 | 函数 | 职责 |
|---|---|---|
| 308 | `_normalize_outline_title` | 去页码后 casefold |
| 313 | `_normalize_outline_heading_text` | 清 PDF 噪声 + 标题碎片修复 |
| 333 | `_looks_like_book_primary_outline_title` | chapter/appendix 前缀或裸数字章号 |
| 345 | `_should_keep_book_top_level_outline_title` | 主章节或特殊标题（preface/index/…） |
| 355 | `_outline_title_label` | "Introduction: …" 取冒号前 |
| 360 | `_extract_book_main_chapter_number` | "Chapter N" / "N Title"（要求 Title 大写开头） |
| 392-414 | `_looks_like_outlined_book_{frontmatter,appendix,glossary}_title`, `_should_start_outlined_book_top_level_chapter` | outlined_book lane 过滤 |
| 417 | `_parse_page_number` | 数字/罗马 |
| 426 | `_split_toc_entry` | 标题 + 页码 |
| 434 | `_strip_leading_page_label` | 去行首页码 |
| 438 | `_collapse_spaced_title_artifacts` | "A ttention" → "Attention"（保留 "A deep"） |
| 459 | `_normalize_intro_title_artifacts` | 八进制转义、单字母序列、碎片 token 合并（多轮 while） |
| 514 | `_normalize_pdf_signal_text` | 去控制字符 |
| 518 | `_normalize_paper_title_candidate` | 标题碎片再合并 |
| 550 | `_looks_like_paper_title` | 5–24 词、≤1 数字、大写词占多数 |
| 568 | `_looks_like_visual_heading` | ≤160 字符、≤4 行、无 `=∼≤…`、title-case 占比 |
| 611 | `_looks_like_author_affiliation_start` | 姓名 + 数字上标 + 机构词 |
| 628 | `_infer_first_page_paper_title_and_remainder` | 在 4..40 token 处找作者边界 |
| 660 | `_leading_reference_heading_and_remainder` | "R e f e r e n c e s"/"References and Notes" 前缀 |
| 694 | `_section_family_display_title` | family → 显示名 |
| 704 | `_looks_like_toc_heading` | contents / table of contents |
| 708 | `_page_family_for_heading` | 见 3.3.1 |
| 723 | `_inline_page_family_heading` | 正文首块内联的 family 标题 |
| 752 | `_looks_like_titleish_backmatter_lead` | ≤8 词 title-case |
| 767 | `_detect_backmatter_cue` | 标题集 或 ISBN/价格/URL/出版社 ≥2 信号 |
| 797 | `_looks_like_frontmatter_signal` | preface/… 前缀 |
| 805 | `_looks_like_title_page_metadata_signal` | copyright/leanpub/© |
| 821 | `_looks_like_reference_entry` | 年份/引用标记/DOI/URL 组合 |
| 852 | `_looks_like_index_entry` | 尾部页码列表 + ≤8 词 |
| 868 | `_looks_like_academic_heading_token` | 大写开头/全大写 token |
| 888 | `_leading_academic_standalone_heading` | abstract/introduction/… 独立标题 + 散文余部 |
| 905 | `_embedded_academic_abstract_segments` | "Abstract" 嵌在段内 |
| 937 | `_consume_academic_heading_title` | 逐 token 吃标题（连接词、body starter 停止） |
| 1024 | `_looks_like_academic_body_continuation` | 小写词 ≥2 |
| 1060 | `_extend_broken_academic_heading_fragment` | 修 "Ar-\nchitecture" 类断词 |
| 1126 | `_extend_academic_heading_trailing_token` | 补尾名词（attention/training…） |
| 1163 | `_move_colon_heading_tail_into_body` | 标题末 "X:" 移回正文 |
| 1183 | `_clean_academic_heading_candidate` | 组合上面三者，≤120 字符 |
| 1209 | `_next_academic_inline_heading` | ≥60 字符块内找 `N[.N] Title` |
| 1266 | `_page_has_centered_title_signal` | 前 4 块、顶 22%、居中、窄 |
| 1287 | `_page_has_title_overlap_signal` | 与 metadata title 重叠 |
| 1305 | `_early_page_has_title_signal` | 前 2 页 |
| 1319 | `_page_has_reference_signature` | 引用标题或条目 |
| 1334 | `_trailing_reference_page_count` | 尾部连续引用页数 |
| 1345-1359 | `_looks_like_chapter_intro_cue`, `_contains_chapter_intro_cue`, `_contains_appendix_intro_cue` | "this chapter covers" / "this appendix" |
| 1362-1468 | `_has_appendix_title_lead`, `_infer_appendix_intro_title`, `_infer_appendix_subheading_title`, `_infer_appendix_nested_subheading_title`, `_appendix_section_subheading_candidate` | 附录标题推断（≤8 词、break words） |
| 1517 | `_trim_intro_title_tail` | 章节导语页标题截断（break words、单大写字母、第 5 词后大写） |
| 1549 | `_infer_intro_page_title` | 导语页前几块拼标题 |
| 1585-1605 | `_title_variants`, `_titles_overlap` | 去 chapter 前缀/编号后集合相交 |
| 1608 | `_extract_footnote_marker` | 脚注标记 |
| 1616 | `_body_contains_footnote_anchor` | 尾 160 字符找标记 |
| 1634 | `_header_footer_signature` | 页眉签名 |
| 1641 | `_is_page_number_text` | 页码 |
| 1660 | `_is_book_running_header_text` | "22\nCHAPTER 2\n…" / "2.2 Title 23" |
| 1669 | `_looks_like_dense_toc_block` | ≥2 行、点线≥10 且页码 token≥3 |
| 1681 | `_looks_like_heading_continuation_fragment` | ≤80 字符、介词/小写开头 |
| 1693 | `_looks_like_prose_continuation_fragment` | ≥48 字符、≥10 token、停用词密度、续写词开头 |
| 1718 | `_looks_like_book_prose_fragment` | ≥24 字符、停用词 ≥ max(3, n/4) |
| 1735 | `_looks_like_inline_book_heading_text` | 2–10 词 visual heading |
| 1751 | `_looks_like_contextual_image_legend_text` | this/these/the 开头 + 数字 |
| 1769 | `_book_heading_level` | 级别推断 |
| 1784 | `_looks_like_book_prose_lead` | 停用词开头 |
| 1802 | `_is_plausible_book_heading_candidate` | 不以 `,;:` 结尾 |
| 1811 | `_remainder_starts_fresh_sentence` | 大写字母开头 |
| 1835 | `_leading_numbered_book_heading_and_remainder` | 快路径按换行 `N\nTitle\nBody`；慢路径 3..12 token 边界扫描（一堆 title-case/缩写/停用词守卫） |
| 1955 | `_leading_all_caps_book_heading_and_remainder` | 全大写前缀 ≥4 词，级别 4 |
| 2017 | `_leading_plain_book_heading_and_remainder` | ≥60 字符、1..6 token 边界，`_BODY_CUT_AT_VERB_LEAD` 守卫（仅无字体信息时用） |
| 2086 | `_page_has_multi_column_signature` | 见 3.3.3 |
| 2119 | `_page_has_column_fragment_signature` | ≥600 字符、span≥700、半页宽的块 ≥2 |
| 2137-2197 | `_page_has_abstract_signal`, `_page_first_numbered_section_heading_top`, `_page_has_asymmetric_academic_first_page_signal`, `_page_has_single_column_academic_first_page_signal` | 学术首页布局 |
| 2200 | `_page_extraction_plan` | intent: scanned→ocr_overlay；mixed→hybrid_merge；学术多栏/碎片→hybrid_merge；多栏/碎片→hybrid_merge；否则 native_text |
| 2240 | `_assess_page_layout` | risk: scanned/首页不对称→high；有 reason→medium；否则 low |
| 2272 | `leading_emphasis_line_count` | 前缀粗体/大字号行数（末行必须非粗体） |
| 2299 | `styled_heading_and_remainder` | 按粗体前缀切标题（≤16 词、非图注/代码） |

### 3.11 图片相关：`figure_clustering.py` / `artifact_grouping.py`

- `cluster_figure_regions`（`figure_clustering.py:499-713`）：按页分区 → `_merge_close_anchors`（面积 ≥1800pt²、间距 ≤24pt、中间无 ≥8 词整句作分隔）→ 每簇 label search zone = bbox 外扩 30pt → 文本块：中心在别的锚内则跳过；`_classify_text_for_anchor`：图注模式优先 → 真节标题（`N.M …`/Chapter）保护 → >200 字符拒 → 整句拒 → **字号比例计算后 `pass`（`:452-457`，`min_label_size_ratio` 配置无效）** → 要求中心在锚 bbox 内 → inline_label；prose density（锚 bbox 内 >200 字符非图注块 >1 个）阻断吸收；图注取离图边最近、最短者。配置从 `Settings.figure_cluster_*` 读（8 项）。
- `artifact_grouping.py`：`looks_like_artifact_group_context_text`（48–900 字符、非图注/标题/代码、按角色的英文提示词或学术 lane 停用词密度）；`resolve_artifact_group_context_ids`（导出侧对持久化 Block 的同逻辑重算，`:161-209`）——**恢复层与导出层各有一份几乎相同的 group-context 推断**（`pdf.py:4201-4273` vs `artifact_grouping.py:298-384`）。

### 3.12 已知误判 / 脆弱点汇总（PDF）

见 §12 编号条目 P-1 … P-40。

---

## 4. EPUB 解析（`domain/structure/epub.py`）

### 4.1 流程 `EPUBParser.parse`（`:640-658`）

`container.xml` → OPF → manifest（href 相对 OPF 目录 normpath）→ nav map（**仅 EPUB3 `properties="nav"`**，取 `epub:type=toc` 的 `<a>`；EPUB2 NCX 完全忽略，`:731-764`）→ metadata（`dc:title` 含 `title-type` refine 的 main/subtitle，`dc:creator` 只取第一个，`dc:language`）→ spine：跳过 `linear="no"`（**脚注/尾注文件常是 non-linear，会整体丢失**，`:776`）、非 xhtml/html 媒体类型、重复 href → 每个 spine 项一个 `ParsedChapter(chapter_id=f"chapter-{n}", href, title, blocks, metadata={"source_path": href})` → `_normalize_spine_chapters`：丢空章、丢 TOC-like 章（标题 contents/… 或 href 含 toc/contents 且只含 heading/paragraph/list_item 且 ≥2 块）、丢 titlepage-like 章（href 含 titlepage/cover/half-title 且内容只是书名或 ≤4 词）。

章节标题：nav 标题（非页码、非 "cover/contents/…" 泛名）> 第一个 heading。

### 4.2 XML 解析容错 `_parse_xml_document`（`:331-397`）

五级：stdlib → 替换 HTML 命名实体 → lxml `recover=True`（结构错误占比 >50% 时放弃）→ 正则清理非法字符/裸 `&` → 组合。全部失败则抛 `ParseError`，`_parse_chapter` 捕获后用 `_FallbackHTMLBlockExtractor`（`HTMLParser` 子类，`:416-627`）。

### 4.3 HTML → block 映射（ET 路径 `_extract_blocks:880-938`，递归 `visit`）

| HTML | block_type | 文本 |
|---|---|---|
| `h1..h6` | heading（`heading_level` 从标签数字，后经 `_normalize_chapter_heading_levels:130` 归一：首标题 =1，其余相对） | rich text |
| `p` | paragraph | `_extract_rich_text`：`code`→`` `x` ``、`b/strong`→`**x**`、`i/em`→`*x*`（markdown 标记直接进 source_text；`has_inline_formatting`, `inline_format_counts`） |
| `blockquote` | quote | rich text（内部 `<p>` 不再拆） |
| `pre` | code | `itertext` 原样，只去首尾换行 |
| `li` | list_item | rich text；**无 ordered/unordered、无嵌套层级、无序号信息** |
| `figcaption` | caption | |
| `table` | table | 只取 `tr` 下直接 `th/td`，单元格 `itertext` 空白折叠，空单元格**被删除**（列错位），行以 `" | "` 连接；rowspan/colspan 丢失 |
| `math`, `svg` | **code** | itertext（MathML 语义丢失，当代码保护） |
| `figure` / `div.figure-container|image-container|imageblock|mediaobject`（含 img） | figure(`[Image]`/alt, translatable=False, `epub_figure_artifact`) + caption（`figcaption` 或首个 h5/h6/p/span/div 文本） | 双向 `linked_caption_source_anchor` / `caption_for_source_anchor` |
| `aside[epub:type~=footnote]` 或任何 `epub:type` 含 footnote 的块 | footnote | |
| 独立 `img`（不在任何块容器内） | paragraph "[Image]"/alt，`image_src/image_path/image_alt`, translatable=False, `image_caption_generated` | |
| `div/section/article/header/dl/dt/dd/…` | 不产块，递归子元素 | **直接放在 `div`/`section`/`dt`/`dd` 里的文本节点被丢弃** |
| `p` 内含文字 + `img` | paragraph（文字） | **图片丢失**（只有空 `p` 才回落到 img 分支） |

Fallback 路径差异：未知标签（如 `<custom>`）的开闭标签**以字面 `<tag>` 文本拼进 block**（`:441-445, :531-534`）；`img` 在活动块内会写入 `image_src`（`:447-459`）；heading_level 同样来自标签。

### 4.4 为 `zh_epub` 回填保留了什么

每块：`source_path`(spine href)、`anchor`(元素或祖先 id)、`metadata.tag`、`heading_level`、图片三元组、`caption_for_source_anchor`/`linked_caption_source_anchor`、内联格式计数。**没有**：元素在文档中的路径/序号、class/attrs、原始 HTML 片段、列表类型、表格结构、被跳过的 non-linear 文件。导出重建时只能重新解析源 EPUB 并按 `(href, ordinal)` 对齐（待确认 export 侧实现）。`ParsedDocument.metadata` 只有 `title/subtitle/document_title_src/author/language`。

### 4.5 EPUB 的 translatability

`derive_translatability` 直接用在每个块（`:902`）；figure/图片块 metadata 显式 `translatable=False` → DB 侧 BLOCKED；`pre/math/svg/table` 靠块类型 PROTECTED。语言来自 `dc:language`（可能是 `en-US`），直接写入 `document.src_lang`。

---

## 5. 句子切分（`domain/segmentation/sentences.py`，103 行）

`EnglishSentenceSegmenter.segment_text`（`:64-86`）步骤：
1. 行首 `^\s*\d{1,3}\.(?=\s+\S)` 的编号点先换成哨兵（保护 "1. Tops and Bottoms"）；空白折叠。
2. 固定缩写表（`:8-31`，Mr./Dr./e.g./i.e./Fig./Eq./U.S./a.m. 等 22 个）做**纯子串替换**（大小写敏感、无词边界）。
3. `_NUMBER_LABEL_ABBREVIATION`：`Rs|Re|Nos|pp|Vol|Ch|Sec|approx|Ref` 后跟数字才保护。
4. `_NAME_INITIAL`：单个大写字母 + `.`，后面接另一个首字母或 "Two Word" 名字；`_FIRST_NAME_INITIAL`："Walter J. Baeyens"。
5. 小数点 `(\d)\.(\d)`。
6. `re.split(r'(?<=[.!?])\s+(?=(?:"|\'|“|‘|\()?[A-Z0-9])')` —— 必须以 `.!?` 结尾且下一句以大写/数字开头。
7. 还原哨兵；`a.m./p.m.` 后再切一次。

`segment_block`（`:92-100`）对 {heading, code, footnote, caption} 不切 —— 但 bootstrap 用的是自己在 `SegmentationService._build_sentences` 里的另一份类型集合（多了 FIGURE/EQUATION/IMAGE），`segment_block/segment_chapter` 在生产链路里无人调用（只在 `pdf_prose_artifact_repair.py:821` 用 `segment_text`）。

已知边界：
- 只支持英文：CJK 句号 `。！？` 不切（中文源 = 整段一句）；`src_lang` 无论是什么都用同一个分段器。
- 引号在句号之后（`he said.” The`）：lookbehind 只认 `.!?`，**不切**；`.)` 同理。
- 下一句以小写品牌词开头（"iPhone…"、"eBay…"）不切；以 `¿¡«` 等符号开头不切。
- 省略号 "..." 后接大写会切；"etc." 在句末时永远不切（被保护）。
- 缩写替换无词边界："Co." 会命中 "Mexico. Then"（`Mexico.` 含 "co."? 大小写敏感，"co." 小写不在表里；但 "St." 命中 "1st. Then" 不会因为大小写；"No." 会命中 "…said No. Then" 阻止切分）。
- 编号哨兵仅 1–3 位；"2020. The" 行首不受影响（4 位）。
- 表格/列表块也走句子切分：`| a | b |` 行被当一句或按内部句号切。

---

## 6. Modality pipeline（`services/modality_pipeline.py` 及四个 extractor）

启用：`ParseService(modality_options=...)` 显式 > env（`bootstrap.py:100-127`，`os.getenv` 直读，**绕过 `Settings`**）：`BOOK_AGENT_PDF_MODALITY_REFERENCES / EQUATIONS / TABLES / IMAGES`，`BOOK_AGENT_PDF_TATR_TABLE_RECOVERY`（仅 TABLES 开时有效）。默认全关 = 零开销。**EPUB 文档也会跑**（无 source_type 判断）。顺序固定 references → equations → tables(+TATR) → images，每步产生新 `ParsedDocument`（4 次全量复制）。

| 模态 | 实现 | 可选依赖 / 启用 | 降级 |
|---|---|---|---|
| References（`references_extractor.py`） | 章标题或块文本 ∈ {references, bibliography, works cited, 参考文献…} 进入段；直到 heading ∈ {index, appendix, glossary, …} 退出；段内所有块 `translatability=NONE` 并 `parse_reference_entry`（年份/DOI/arXiv/URL/作者/标题/期刊正则） | 无依赖 | 结果**只写 DocIR 字段，不进 metadata**，DB 侧不生效（§2.3）；"Appendix A" 不等于 "appendix" → 之后的附录整段被锁 |
| Equations（`equation_extractor.py`） | `EquationLatexAdapter`（默认 NoOp）；`equation_render_mode ∈ {latex, image_anchor, verbatim_text}`；总是 NONE | 无 ML 实现（pix2tex/texify 未接） | 永远 verbatim_text（PDF 公式块无 image_path） |
| Tables（`table_extractor.py`） | `looks_like_table`：≥3 行且 ≥60% 行含 `" {2,}\S"` → 列 gutter 检测 → markdown；写 `table_markdown/table_confidence/table_column_count`；总是 NONE | 无依赖 | PyMuPDF 文本已折叠空白 → gutter 永不出现 → 启发式实际不产出（待确认）；ruled 表的 `\| a \| b \|` 单空格同样不匹配 |
| TATR（`tatr_extractor.py`） | `TatrTableExtractor`：`importlib.find_spec` 探 torch/transformers/PIL；加载 `microsoft/table-transformer-detection` + `-structure-recognition`（首次联网下载 ~220MB）；PyMuPDF 200dpi 渲染 → 检测 0.7 → 每表裁剪 → 结构 0.6 → 行×列交集 → `map_cell_text`（与页文本块重叠 ≥0.4）→ markdown；每文档 ≤50 表（计数**跨调用累积**，`:230`） | torch+transformers+PIL | 缺依赖/异常 → `[]` + metrics；`_apply_tatr_post_pass` 只对无 `table_markdown` 的块跑；`page_dims_by_page` 声明后**从未赋值**（`modality_pipeline.py:250,304`）→ 永远走 `_guess_page_dims` |
| Images（`image_modality.py`） | image/figure → NONE，`image_alt` 升为 `image_canonical_alt` 与文本；caption 强制回 ALL | 无 | 只改 DocIR 字段 |

遥测：`document.metadata_json.modality_pipeline` 计数（`bootstrap.py:491-515`）。异常整体吞掉（`:488`）。

---

## 7. Text-layer sanity 与 OCR

### 7.1 Sanity gate（`domain/structure/text_layer_sanity.py`）

在抽取层对**每页所有文本块拼接**后调用（`extract.py:428-434`）。非空白字符 <80 → `ok=True, reason="insufficient_text"`。指标：字符熵（跳过空白）、PUA 比例（`U+E000–F8FF, F0000–FFFFD, 100000–10FFFD`）、常用英文词命中率（仅上报）。判定：`pua_ratio > 0.02` → `pua_high`；熵 `<2.80` → `entropy_low`；`>5.80` → `entropy_high`。结果放 `PdfPage.text_layer_sanity`。

传导（`chapters.py:116-130`）：页 `ok is False` → 该页所有 block `provenance="ocr"`、`confidence_breakdown={"sanity_ok": False, "sanity_reason"}`；`ok True` → `sanity_ok: True`。没有"置信度"数值，只有布尔。`PdfExtraction.sanity_failed_pages()` 无调用者。

非英文风险：CJK/混排页的字符熵远高于英文（几百个不同汉字）→ 大概率 `entropy_high`（待确认数值）；`dict_hit_rate` 英文词表。目前 `ParsedDocument.language` 固定 "en"，无语言检测。

### 7.2 OCR 重抽取（`pdf.py:296-393`，`ingestion/pdf/surya_reextraction.py`）

触发：`Settings.pdf_sanity_ocr_reextraction=True`（`build_default_recovery_service:4316`，OCR parser 路径强制关闭）。时机：**章节构建之后、所有合并/分类完成之后**。对每个 `sanity_ok is False` 的 block 取 `source_bbox_json.regions[0]`（多页合并块只取首区）→ `SuryaOcrReextractionAdapter.reextract_blocks`：失败页 >20 → 返回 `{}`（**静默放弃**）；PyMuPDF `insert_pdf` 建子集 PDF → `OcrPdfTextExtractor.extract`（Surya 子进程）→ 归一化 bbox 重叠 ≥0.25 匹配；**无匹配时返回整页 OCR 文本**（`:317-318`）→ 同页多个块可能各自被替换成整页文本（N 倍重复）。替换后只改 `text/provenance/confidence_breakdown`，**块类型、角色、合并结果不重算**（垃圾文本上做出的 code/table 判定被保留）。

### 7.3 整本 OCR 路径（`ingestion/pdf/ocr.py`）

`PDF_SCAN` / `PDF_MIXED` → `OcrPdfParser.parse`：`UvSuryaOcrRunner` 用 `uv run --python 3.13 --with surya-ocr==0.17.1 --with transformers==4.56.1 … surya_ocr <pdf> --output_dir` 起子进程（`:113-144`），要求 PATH 有 `uv`、能联网装包/下模型；心跳写 `ocr_status_path` JSON；`ocr_max_runtime_seconds` 默认 None = 无限；`>32` 页按 `ocr_chunk_page_count` 分块、**每块重新 `uv run`（重新加载模型）**。结果 `results.json` → 行（去 `<b>/<i>` 标签）→ `_group_lines_into_blocks`（行距 >1.15×中位行高 或 缩进+前句终止 → 分块；居中大行/编号行 → 独立标题块）→ `PdfTextBlock`，`font_size_* = 行高像素`，页 `width/height = image_bbox 像素`。随后走**同一套**恢复启发式：所有以 pt 计的绝对阈值（18/48/54/96/120/220pt…）在像素坐标下失真；`extraction.title/author/outline` 全空；混合 PDF 的可用文本层被整体丢弃，`hybrid_merge` intent 从未实现。

---

## 8. 结构 refresh

### 8.1 PDF（`services/pdf_structure_refresh.py`）

`refresh_document(document_id, chapter_ids)`：加载 bundle → 克隆 Document → **完整重新 parse**（含 profiler 已存 profile、modality、ParseIr sidecar 覆盖写）→
- 章节按 `(ordinal, href)` 匹配：更新 `title_src/anchor_*/risk_level/metadata_json`；不匹配 → skipped（不新建）。
- 块按 `source_anchor` 匹配：覆盖 `block_type/source_text/normalized_text/protected_policy/parse_confidence/status=ACTIVE/source_span_json`（保留旧 `repair_*` 键；`*_block_id` 关系重新映射到**旧** block id；`pdf_mixed_code_prose_split` 派生块以 `refresh_split_render_fragments` 挂在基块上，并保留旧译文）；若旧句子拼接文本 ≠ 新 `source_text` → `refresh_sentences_stale=True` 并列入 `stale_sentence_block_ids`。**新出现的块不创建**；旧块只有在"是表且新解析同页有 table/caption"时才 INVALIDATED（`_should_invalidate_unmatched_block:522`），其它未匹配块保持 ACTIVE。
- DocumentImage upsert（沿用旧 `storage_path`）。
- document 的 title/author/metadata_json 用新值覆盖并写 `pdf_structure_refresh` 摘要；AuditEvent。

`stale_sentence_block_ids` 的限制（PLAN P3.3 未完成项）：句子表**从不重建**，因为 Sentence 被 packet_sentence_maps / target_segments / alignment_edges 引用且没有退役状态；于是 refresh 后 Block 文本与 Sentence 文本不一致，翻译/导出仍基于旧句子，只能靠调用方看到标记后触发 packet 重建与重译（目前没有这个自动化）。此外：refresh 不更新 `parse_revision` 行、不递增 `parser_version`，但会覆盖 sidecar 文件；`figure_cluster`/anchor 变化导致的锚点位移会让整章 skipped。

### 8.2 EPUB（`services/epub_structure_refresh.py`）

按 `(ordinal, href)` 匹配章，按 `block.ordinal` 匹配块：三态 更新 / 新建（直接 merge 新 Block 对象，其 `chapter_id` 指向重解析产生的**新 chapter id**——与旧 chapter id 相同仅当 `stable_id("chapter", doc, ordinal, href)` 未变）/ INVALIDATED。同样不动句子，且**没有** stale 检测（比 PDF 更弱）。

---

## 9. 性能特征

- **重复抽取**：一次 bootstrap 对 PDF 做 2 次完整 PyMuPDF 抽取（profile + parse），`pdf_smoke` 3 次；refresh 再 1 次。每次 `PyMuPDFTextExtractor()` 实例都 `mkdtemp` 并把所有内嵌图片保存成 PNG（大图/扫描页 → 巨大 PNG），临时目录永不删除。
- **单线程、同步、无超时**：HTTP 请求线程内跑完解析 + OCR 子进程 + 分段 + packet 构建（`documents.py:109-127`）。没有页数/文件大小上限，没有取消。
- **内存**：`path.read_bytes()` 全文件；`PdfExtraction`（全部页/行/字体名）+ `recovered_blocks`（22 个 pass 各自新建 list，`replace()` 拷贝 dataclass）+ `ParsedDocument`（modality 4 次复制）+ ORM 对象 + sidecar `json.dumps` 的全文 IR 字符串 + `document.metadata_json.pdf_page_evidence`（每页 dict）。文本至少 4–5 份常驻。
- **热点**：
  - `page.get_drawings()` 每页必调（矢量图/图表页极慢，无上限）；`find_tables` 仅在有格线时调。
  - `_materialize_embedded_image`：每个图片块遍历 `page.get_images()` × `get_image_rects` → O(图片²)/页。
  - `_link_artifact_captions`：每个制品遍历全部块 → O(A·N)；`_recover_text_only_figures`：每个孤儿图注对同页块两次扫描；`_artifact_group_context_target` 从制品向后线性扫。
  - `_looks_like_code` / `_expanded_code_candidate_lines` 在分类、`_should_repair…`、`_split_mixed…`（×2）、`_promote_late…`、跨页 code 合并里对同一块重复调用，每次逐行跑 ~15 个正则；`_normalize_intro_title_artifacts`/`_normalize_paper_title_candidate` 是 `while previous != normalized` 的多轮循环。
  - `_page_contexts` 每页对所有块跑引用/索引/TOC 正则。
- **缓存**：仅 `materialized_xrefs`（同一实例内）。跨请求无缓存；重新 bootstrap 同一文件从头算。
- **可并行性**：页级抽取、页级上下文、sanity 都是逐页独立，可并行；但恢复 pass 依赖全局 `reading_order_index` 与跨页合并，需按页分片再合并。目前没有任何并行。
- OCR：子进程每 32 页重启一次模型；`uv run` 首次要解析/下载依赖。
- 实测数据：无（仓库里没有 benchmark；记忆里 `test_pdf_support.py` 全套 27 分钟是测试成本，不是单文档）。

---

## 10. 可配置项与硬编码常量

### 10.1 `Settings`（`core/config.py`，前缀 `BOOK_AGENT_`）

`upload_root`, `export_root`, `figure_cluster_enabled`, `figure_cluster_max_anchor_gap_pt=24`, `figure_cluster_label_search_pad_pt=30`, `figure_cluster_max_label_chars=200`, `figure_cluster_min_label_size_ratio=0.85`（无效）, `figure_cluster_max_prose_neighbors_in_zone=1`, `figure_cluster_inline_absorb_requires_inside_anchor=True`, `figure_cluster_min_anchor_area_pt2=1800`, `pdf_sanity_ocr_reextraction=False`, `ocr_status_path`, `ocr_heartbeat_seconds=5`, `ocr_max_runtime_seconds=None`, `ocr_chunk_page_count=32`。

### 10.2 绕过 Settings 的 env / 构造参数

`BOOK_AGENT_PDF_MODALITY_*`、`BOOK_AGENT_PDF_TATR_TABLE_RECOVERY`（`bootstrap.py:62-66`）；`ParseIrService(output_root="artifacts/parse-ir")`；`PDFParser(image_output_dir=None→mkdtemp)`；`SuryaOcrReextractionAdapter(max_failed_pages_per_doc=20, bbox_overlap_threshold=0.25)`；`TatrTableExtractor(max_tables_per_doc=50, dpi=200, cell_overlap_threshold=0.4)`；`UvSuryaOcrRunner(surya_package="surya-ocr==0.17.1", transformers_package="transformers==4.56.1", runtime_python→3.13)`。

### 10.3 硬编码阈值（节选，均无配置）

| 类别 | 常量 | 位置 |
|---|---|---|
| 页区 | top ≤12% / bottom ≥88% | `pdf.py:1141-1144` |
| 字号 | 标题 ≥1.25×中位、family 标题 ≥1.15×、脚注 <0.95×、粗体续行 ±5% | `pdf.py:1056,1064,1166,1183`, `:4452` |
| 多栏 | 宽 ≤0.58/0.62/0.70、左 ≤0.22/0.30、右 ≥0.45、高 ≥0.24、≥4/≥6 块、≥40/≥24 字符 | `classify.py:136,2090-2110`, `pdf.py:737-758` |
| 跨页合并 | prose/heading/footnote 72%/28%；code 68%/34%；table 70%/30%；artifact 70%/32% | `pdf.py:1353-1355,1613-1615,1767-1769,2541-2543,2617-2619` |
| 间距 pt | 同页合并 max(1.8×字号,18)；表 54；code 左差 96；caption 下 [-48,120] 上 [-12,80] 次页 220；group ctx 96；text-only figure 240/480/380/6 | 各函数 |
| 长度 | heading ≤150/120/160/96/80 字符；listing 作用域 16 块/250 字符/160 字符；label ≤200/240 | |
| sanity | 80 字符、熵 2.80–5.80、PUA 0.02、dict 0.12 | `text_layer_sanity.py:53-66` |
| profiler | 32 字符/页、text_ratio 0.2/0.9、学术 ≤24 页、outlined ≥80 页、12%/15%/40 | `extract.py:1318-1402` |
| 抽取 | mono 占比 0.7、格线 ≥3 横 ≥2 竖、bullet 1.5–7pt/36pt 间距、vector 面积 ≥1800/0.25%、簇 ≥12000/1.8% | `extract.py:48-77,374,565-610` |
| 分段 | 22 个缩写、编号 1–3 位 | `sentences.py:8-45` |
| 词表/正则 | 章节标题词、frontmatter/backmatter/index/references 标题集、停用词、学术标题名词、出版社名、代码关键字（多语言）、shell 命令、字体名（mono/正文）、Manning "Listing" | `classify.py:34-305`, `text.py:9-254,366-409`, `pdf.py:141-187` |

---

## 11. 缺失的生产要素

1. **大文件 / 超时 / 内存**：无页数上限、无文件大小上限、无解析超时、无取消；解析在 HTTP 线程内同步执行；OCR 子进程无默认超时；临时 PNG 目录泄漏；文本多份常驻。
2. **失败可观测性**：`pdf.py / extract.py / chapters.py / bootstrap.py` 一行日志都没有；modality 异常静默；章节构建前的"未分配块"直接丢弃无记录；Surya cost guard 触发静默返回空；JobRun 只在成功时写；`pdf_page_evidence` 是唯一的诊断面但塞在 `documents.metadata_json`。没有指标（块数/角色分布/pass 改动计数/耗时）。
3. **解析版本化与再解析**：`parser_version/segmentation_version` 永远 1；`DocumentParseRevision` 永远 v1 且 sidecar 原地覆盖；块 / 章 / 句 id 依赖 ordinal 与全局锚点，任何启发式改动都改变 id；重解析没有"新版本并存 + 迁移译文"的模型；refresh 不重建句子；旧行不删。启发式改动的影响面只能靠 56 个 golden 快照观察。
4. **非英文源**：分段器、sanity 词表、几乎所有标题/散文/代码判定都是英文规则；PDF 语言硬编码 "en"；无语言检测；CJK 页可能触发 sanity 失败→OCR。
5. **扫描件 / 混合件**：整本 Surya 子进程（网络、`uv`、固定版本）；混合 PDF 丢弃文本层；OCR 结果坐标单位与 pt 阈值不匹配；无 OCR 置信度传导到 `parse_confidence`（`_OcrLine.confidence` 读了但没用）。
6. **幂等与去重**：同内容文件重复上传 → 同 document id 覆盖；无内容寻址的上传存储；没有"同文件已存在"的返回语义（待确认路由层）。
7. **契约**：`source_span_json` 上百个自由键无 schema；三套可译性标记；导出层与恢复层各自实现一份 group-context/caption 规则。

---

## 12. 发现的 bug / 脆弱启发式 / 不一致（带位置）

### 12.1 数据流与持久化

| # | 问题 | 位置 |
|---|---|---|
| D-1 | `docir_translatability/provenance/confidence_breakdown/style_hints` 写入 `source_span_json` 后全库无读取者；modality pipeline 的 References 保护到不了 `protected_policy`/`Sentence.translatable`（"references" family 不在非译集合） | `services/bootstrap.py:532-541`, `domain/block_rules.py:8-9,12-25`, `services/references_extractor.py:247` |
| D-2 | `_apply_modality_pipeline` 吞掉所有异常且无日志 | `services/bootstrap.py:488-489` |
| D-3 | 同一 PDF 每次 bootstrap 抽取两次（profile + parse）；每个 `PyMuPDFTextExtractor` 实例 `mkdtemp` 物化全部图片且不清理；临时绝对路径写入 `source_span_json.image_path` 被 export 快路径读取 | `bootstrap.py:239,271`, `extract.py:263-270,707-712`, `pdf.py:657`, `services/export.py:2902-2908` |
| D-4 | `parser_version` 无递增者；revision id 固定 v1；refresh 覆盖 sidecar 但不写新 revision 行 → `canonical_ir_checksum` 与磁盘不一致 | `services/parse_ir.py:95-97,307-319`, `pdf_structure_refresh.py:107` |
| D-5 | `BootstrapRepository.save` 全量 merge 不删旧行；id 含 ordinal → 重解析后旧 block/sentence 残留 ACTIVE（待确认是否允许重复 bootstrap） | `infra/repositories/bootstrap.py:51-89`, `bootstrap.py:544-551,386` |
| D-6 | `_strip_nul_bytes` 只用于 block；document title/author/metadata_json、chapter title 未去 NUL | `bootstrap.py:297-312,389` |
| D-7 | 章节 `anchor_start/anchor_end` = 章首页 href + 任意页 block 锚点 | `bootstrap.py:390-391` |
| D-8 | `_chapter_metadata` 用文档级 `layout_risk` 给每章打 HIGH/CRITICAL | `bootstrap.py:183-196,432` |
| D-9 | `SegmentationService` 对所有块先切句再对受保护类型丢弃；CODE 句子文本为空白折叠版 | `bootstrap.py:714-719` |
| D-10 | 分段类型集合在 domain（`segment_block`）与 services 两处不一致；domain 版无人使用 | `sentences.py:93`, `bootstrap.py:715-718` |
| D-11 | modality env flags 用 `os.getenv` 绕过 `Settings`；`ParseIrService` 输出目录相对 CWD | `bootstrap.py:95-127`, `parse_ir.py:91` |
| D-12 | `EPUB` 文档也会跑 PDF 语义的 modality pipeline（无 source_type 守卫） | `bootstrap.py:320,466-483` |
| D-13 | `ParseProjectionHint` 全量冗余存到 `projection_hints_json` 列（与 sidecar 重复） | `parse_ir.py:333` |
| D-14 | `pdf_smoke` 作为工具会在 CWD 下写 `artifacts/parse-ir` 副作用 | `tools/pdf_smoke.py:263` |

### 12.2 PDF 抽取 / 分类 / 恢复

| # | 问题 | 位置 |
|---|---|---|
| P-1 | 全局 `reading_order_index` 锚点：前页块数变化 → 后续所有锚点位移；refresh 以锚点匹配 | `pdf.py:572,636,678`, `pdf_structure_refresh.py:111-127` |
| P-2 | `FileDataError ⊂ RuntimeError` → 损坏/加密 PDF 静默退到 `BasicPdfTextExtractor` 产出垃圾结构 | `extract.py:1281-1291` |
| P-3 | outline 页号 `max(1, page)`：未解析目标(-1)变第 1 页，成为伪章节起点 | `extract.py:729` |
| P-4 | `top_level_outline_entries` 取 `min(level)`：Part 顶层的书只按 Part 切章 | `chapters.py:700-724` |
| P-5 | 第一个章节候选之前的块（非前置页样式）被无声丢弃 | `chapters.py:203-219` |
| P-6 | 图页占比 >10% 的 text PDF 被判 `mixed_pdf` → 整本 Surya OCR、文本层丢弃 | `extract.py:1336-1344`, `bootstrap.py:272-274` |
| P-7 | 可疑页 ≥2 即 `layout_risk=high` | `extract.py:1415-1416` |
| P-8 | 行内空白折叠后 `\S\s{2,}\S`（表分隔）、`\s{2,}` TOC 条目、`" {2,}\S"` gutter 全部失效 | `extract.py:358`, `text.py:144,441`, `classify.py:36`, `table_extractor.py:93,136` |
| P-9 | `_CODE_CONTROL_LINE_PATTERN` IGNORECASE：英文 "If …:"/"Return …"/"For … in …:" 计为代码关键字行 | `text.py:52-120` |
| P-10 | `_LIST_BULLET_PATTERN` IGNORECASE 的罗马数字分支：以 c/d/i/l/m/v/x 组成的单词（"Civil.", "Did.", "Mild.") + 空格 → 列表项 | `text.py:400-409` |
| P-11 | `_HEADING_PATTERN` IGNORECASE：≤150 字符且以 "Part of…"/"Chapter 3 shows…" 开头的段落→heading | `pdf.py:1180`, `text.py:10` |
| P-12 | 三栏页列重排：中栏块无组归属，排序落到 0/3 组 | `pdf.py:776-803` |
| P-13 | 谓词函数带副作用（在 `_should_*` 里改 `flags`） | `pdf.py:1562,1619,2573` |
| P-14 | `_merge_blocks` metadata 后者覆盖：跨页合并后块的 `pdf_page_family/heading_level/footnote_*` 取自后块 | `pdf.py:1490` |
| P-15 | `_should_merge` 允许 list_item 吞掉紧随的 body 段 | `pdf.py:1327-1329` |
| P-16 | 脚注只在顶/底 12% 区且小字号才识别；"2019 was…" 类数字开头段落在底部会被判脚注 | `pdf.py:1166-1175`, `classify.py:34` |
| P-17 | `_lock_listing_scope` 硬编码 Manning "Listing N.M" 与英文动词/名词表 | `pdf.py:141-187,1822-2129` |
| P-18 | `_recover_text_only_figures` 吸收"软"标题与短段落成 FIGURE（非确定性来源标题保护表硬编码） | `pdf.py:3846-3858` |
| P-19 | `parse_confidence` 只是 layout_risk 的函数，与识别证据无关 | `pdf.py:1311-1319` |
| P-20 | `figure_clustering` 字号比例判定计算后 `pass`；`min_label_size_ratio` 配置无效 | `figure_clustering.py:452-457` |
| P-21 | `_column_major_blocks` 失败静默退回 y 序，无标记 | `pdf.py:794,727` |
| P-22 | 页眉页脚仅靠 12% 带 + 重复签名；单次出现/两行页眉/"Page x of y" 漏判 | `pdf.py:541-551,1162-1165` |
| P-23 | 连字 `ﬁ ﬂ` 无归一化 | 全库无 `unicodedata` |
| P-24 | `_should_merge_cross_page_prose_continuation` 不看 bbox（只看文本首尾）；页脚注/图后的段落也可能被跨页粘合 | `pdf.py:2196-2245` |
| P-25 | `_split_mixed_code_prose_block` 在两个 pass 里跑，锚点后缀可能重复（待确认） | `pdf.py:2352,4499,4501` |
| P-26 | 恢复层与 `artifact_grouping.py` 各一份 group-context 规则、`figure_clustering.CAPTION_LEAD_PATTERN` 与 `text._FIGURE_CAPTION_PATTERN` 与 `artifact_grouping._CAPTION_LEAD_PATTERN` 三份图注正则（PLAN 承认规则不同） | `pdf.py:4201-4273` vs `artifact_grouping.py:298-384`; `figure_clustering.py:119`, `text.py:12`, `artifact_grouping.py:13` |
| P-27 | `_page_context` 把索引/含尾页码的表页误判 TOC（≥3 条目）→ 整页 toc_entry 不可译 | `pdf.py:1091-1097` |
| P-28 | `PdfFileProfile.from_dict` 缺键默认 `text_pdf` | `models.py:79` |
| P-29 | `_materialize_embedded_image` 无图时 `best_xref=None` 后取第一张；同 xref 多处使用共享一份 PNG（logo 合理，重复图不合理） | `extract.py:695-700` |
| P-30 | `get_drawings()` 每页必调无上限 | `extract.py:408-411` |

### 12.3 OCR / sanity

| # | 问题 | 位置 |
|---|---|---|
| O-1 | Surya 重抽取 bbox 无匹配时整页文本替换单个块 → 同页多块各得整页文本 | `surya_reextraction.py:317-318` |
| O-2 | 重抽取只取多页块的首区；替换后不重分类 | `pdf.py:324-345,249-253` |
| O-3 | cost guard >20 页静默返回 `{}`，无日志/事件 | `surya_reextraction.py:122-131` |
| O-4 | OCR 路径页尺寸/字号为像素，pt 阈值失真；`_OcrLine.confidence` 未使用 | `ocr.py:391-392,520,616` |
| O-5 | 分块 OCR 每块重启 `uv run`；`_page_payloads` 页数不符时编号静默错位 | `ocr.py:459-481,388` |
| O-6 | 混合 PDF 文本层丢弃；`hybrid_merge`/page_plans 无执行者；设计文档的 `extraction_router.py` 不存在 | `bootstrap.py:272-274`, `parse_ir.py:56-64` |
| O-7 | sanity 熵阈值英文调优；CJK 页疑似 `entropy_high`（待确认） | `text_layer_sanity.py:63-64` |
| O-8 | `uv`/网络/固定版本依赖；`ocr_max_runtime_seconds` 默认无限 | `ocr.py:86-90,113-121,206` |

### 12.4 EPUB

| # | 问题 | 位置 |
|---|---|---|
| E-1 | `linear="no"` spine 项（脚注/尾注文件）整体跳过 | `epub.py:776` |
| E-2 | 仅 EPUB3 nav；NCX 忽略 | `epub.py:736-740` |
| E-3 | `div/section/dt/dd` 直接文本丢失；`<p>文字<img></p>` 图片丢失 | `epub.py:890-934` |
| E-4 | `math/svg` 映射为 code | `epub.py:33-34` |
| E-5 | 表格空单元格删除导致列错位；无 rowspan/colspan | `epub.py:1057-1071` |
| E-6 | 列表无类型/层级/序号 | `epub.py:30` |
| E-7 | Fallback 解析把未知标签字面 `<tag>` 写进正文 | `epub.py:441-445,531-534` |
| E-8 | 锚点从祖先继承，不唯一 | `epub.py:886` |
| E-9 | 只取第一个 `dc:creator` | `epub.py:716` |
| E-10 | 内联 markdown 标记（`**`、`` ` ``、`*`）直接进 source_text，无转义（原文含 `*` 会污染计数与译文） | `epub.py:180-219,1073-1084` |

### 12.5 分段

| # | 问题 | 位置 |
|---|---|---|
| S-1 | 仅英文；CJK 不切 | `sentences.py:77` |
| S-2 | `.”`/`.)` 后不切 | `sentences.py:77` |
| S-3 | 缩写纯子串替换无词边界 | `sentences.py:70-71` |
| S-4 | 下一句小写/符号开头不切 | `sentences.py:77` |

### 12.6 死代码 / 未接线 / TODO

- 无 `TODO/FIXME` 标记；设计意图散落在 docstring 与 `tasks/pdf-pipeline-v2.md`。
- 死代码：`PdfStructureRecoveryService._build_chapters`（`pdf.py:4290`）与 `_looks_like_frontmatter_chunk`（`:4310`）仅测试引用；`PdfExtraction.sanity_failed_pages`（`models.py:159`）无调用；`NoOpOcrReextractionAdapter` 仅测试；`iter_reference_entries`、`iter_replaced_block_indices`、`_infer_appendix_nested_subheading_title` 仅测试；`ProtectedPolicy.MIXED`、`TRANSLATE_PROSE_ONLY`、`PROVENANCE_VLM/HYBRID`、`ParsedBlock.style_hints`、`ParseRevisionStatus.SUPERSEDED/INVALIDATED` 无产出者；`modality_pipeline.page_dims_by_page` 从未赋值；`_PageAssistPlan`（`models.py:19`）无使用；`BasicPdfTextExtractor` 的 CoreGraphics 页数计数只在 macOS 下退化路径使用。
- 设计文档声称已完成但当前树不存在：`domain/structure/extraction_router.py`（M2.2）、`tests/test_surya_reextraction_adapter.py`（M2.3b，待确认）。

---

## 13. 测试覆盖

（由子代理逐文件阅读汇总，我核对了用例数与夹具数：`test_pdf_support.py` 191 个 `test_`、41 个 `_write_*_pdf` writer；`tests/golden/pdf_structure` 56 个 JSON。`tests/test_surya_reextraction_adapter.py` 存在；`tests/test_extraction_router.py` 与源码 `extraction_router.py` 均不存在。）

### 13.1 总览

| 测试文件 | 用例数 | 夹具方式 |
|---|---|---|
| `test_pdf_support.py` | 191 | 手写 PDF 1.4 字节流（自建 xref，`PAGE 595×842`）+ 合成 `PdfPage/PdfTextBlock/_RecoveredBlock` + 内存 SQLite + FastAPI TestClient；OCR runner 全 mock |
| `test_pdf_structure_golden.py` + `pdf_structure_scenario.py` | 1（56 个 subTest） | 反射收集 41 个 writer + 15 个 `golden_pdfs.fixtures.make_*`；用默认 `PDFParser` 解析后整档 JSON 字符串比对；解析异常本身也被快照；`BOOK_AGENT_UPDATE_GOLDEN=1` 重生成 |
| `test_golden_pdf_regression.py` | 19 | PyMuPDF 真实生成的 15 个 PDF |
| `test_bootstrap_pipeline.py` | 5 | 手写 EPUB zip，全链路（packet 数/句数上限） |
| `test_epub_parser.py` | 13 | 手写 EPUB zip |
| `test_sentence_segmenter.py` | 5 | 纯字符串 |
| `test_pdf_column_reorder.py` 2 / `test_pdf_font_emphasis.py` 7 / `test_pdf_image_and_heading_regressions.py` 4 / `test_pdf_outline_chapter_detection.py` 17 / `test_pdf_parse_ir_planning.py` 1 / `test_pdf_ruled_tables.py` 2 / `test_pdf_sanity_propagation.py` 2 / `test_pdf_smoke_tools.py` 5 / `test_pdf_vector_bullets.py` 3 / `test_pdf_bootstrap_adapter_wiring.py` 9 | — | 合成 page 或 `fitz` 真实 PDF；wiring 类用 env patch |
| `test_figure_clustering.py` 24 / `test_text_layer_sanity.py` 7 / `test_ocr_reextraction_wiring.py` 5 / `test_ocr_runtime.py` 8 | — | stub block / 纯文本 / fake adapter / patch subprocess |
| `test_table_extractor.py` 15 / `test_tatr_extractor.py` 16 / `test_equation_extractor.py` 11 / `test_references_extractor.py` 14 / `test_modality_pipeline.py` 10 / `test_image_modality.py` 11 / `test_parse_service_modality_wiring.py` 12 | — | 纯函数 + Fake adapter（`FakeTatr` 直接设 `_models_loaded=True`） |
| `test_parse_ir.py` 4 / `test_docir_persistence.py` 4 / `test_layout_validate.py` 6 / `test_pdf_structure_refresh_stale_sentences.py` 2 / `test_translatability_guard.py` 18 / `test_text_code_heuristics.py` 7 / `test_run_pdf_chapter_smoke.py` 5 | — | fake parser + SQLite / 纯函数 |

无任何 `skip/xfail/slow/importorskip`；`pymupdf` 是硬依赖。**环境耦合**：`test_tatr_extractor.py::test_no_deps_returns_empty_and_marks_metric` 依赖环境里**没有** torch，装上即失败。

### 13.2 各启发式的测试期望（关键断言摘录）

| 启发式 | 测试（`test_pdf_support.py` 除非另注） | 期望 |
|---|---|---|
| 标题续行合并 | `:1963`, `:1977` | 合并为 1 个 heading，flag `multiline_heading_merged`（`cross_page_heading_merged` **0 覆盖**） |
| code/prose 切分与合并 | `:2016-2850`（约 25 个） | `from langchain_openai…` 前缀成 CODE；`pip install …` 精确匹配；`Note:` 标签散文不拆；跨页 code 合并 flag `cross_page_code_continuation_merged`；同锚合并后散文锚点 `p101-b21-trailing-prose`；docstring/注释邻接代码 → `late_code_like_promoted` |
| 学术内联标题 | `:2886-2942`, `BasicPdfOutlineRecoveryTests:4427-4848` | `"3.2.1 Scaled Dot-Pr"` 修复为 `"3.2.1 Scaled Dot-Product Attention"`，flag `academic_section_heading_recovered`；`"3.2 Attention"` |
| 书籍嵌入标题 / 级别 | `:4262-4304,:4391-4594,:4863` | 通用标题 `heading_level==2`；非首页 title-ish 降 2；单词 plain heading 紧随节标题 → 3；`E.1 …` → 3 |
| 字体粗细标题 | `test_pdf_font_emphasis.py` | `leading_emphasis_line_count` 只认"粗体前缀+末行普通"；`"Why Momentum Matters"` 成 heading（`embedded_book_styled_heading_recovered`），`"As John"` 不得成 heading；跨块粗体章标题合并为 level 1；粗体编号行成 heading 而矢量圆点行成 LIST_ITEM |
| 多栏 | `test_pdf_column_reorder.py`, `:2981`, golden two/three column | 左栏全部先于右栏；单栏不变；三栏只断言签名触发 |
| profiler | `:2968-3263`, `PdfProfilerTests:6328-6768` | 学术 `medium`+lane；碎片页 `high`；scanned；outlined 多栏书降 medium（`multi_column_page_count==6`）；文件名回填 `title_src` |
| TOC / outline 章节 | `:3552`, `:3720`, `:4973`, `test_pdf_outline_chapter_detection.py`(17) | TOC 页文本不进正文；打印页码 + 偏移解析；outline 页号 `[2,3]`；"Chapter N"/"N Title" 章号提取、Introduction 带副标题保留 |
| 脚注 | `:3571-3657` | `footnote_anchor_matched=True`、`footnote_anchor_label="1"`；跨页续接 `source_page_end=2`；多段 `footnote_segment_count=2`, roles `["footnote","body"]` |
| page family / 特殊章 | `:3681-4185` | `pdf_page_family ∈ {appendix, references, index}` 传播；backmatter cue `("Upcoming Titles","heading_title")` / `("…","marketing_signals")`；孤立 index 页不起特殊章 |
| intro page 标题清洗 | `:3901-3979`, `:4205-4240` | 去转义/spaced-word/句子重启噪声；无字体信号靠导语页切章 |
| 表格 | `:1618-1731`, `:4888`, `test_pdf_ruled_tables.py` | 折行数字表合并成 1 个 table 并链接 caption；ruled 表精确 `"\| Timeframe \| RSI level \| Trend \|…"`，跨页合并，空单元格保留 |
| 列表 | `test_pdf_vector_bullets.py` | 矢量圆点 → `"• "` 前缀 LIST_ITEM；连续编号行逐行切；独立数字列合并 |
| 图片 / 图注 / 分组 | `:5014-5987`, `:5092`, `test_figure_clustering.py`(24), `test_pdf_image_and_heading_regressions.py` | `linked_caption_text`、`caption_for_source_anchor` 双向、flag `caption_linked` / `artifact_group_context_linked`；表注不链到图；同页两图各自 xref；聚类：24pt 合并、中间整句阻断、密度守卫、`reject_outside_anchor`、`figure_cluster.anchor_block_anchors==["p1-img1","p1-img2","p1-img3"]` |
| 公式 | `:4866`, `:5344`, `:5845` | 居中 `p(y\|x) = softmax(…)` → equation 并链接 `Equation 1.` 图注 |
| sanity | `test_text_layer_sanity.py`, `test_pdf_sanity_propagation.py`, golden | PUA→`pua_high`；重复文本→`entropy_low`；引用页不误判；失败页块 `provenance=ocr` + `sanity_ok=False` |
| OCR | `:6064-6247`, `test_ocr_runtime.py`, `test_ocr_reextraction_wiring.py`, `test_surya_reextraction_adapter.py` | 命令 `["uv","run","--python","3.13",…,"surya-ocr==0.17.1","transformers==4.56.1"]`；65 页分块 `["0-31","32-63","64-64"]`；超时 terminate；scan/mixed 路由到 OCR parser；OCR lane 强制无 re-extraction adapter；fake adapter 重写后 `reextracted_via="ocr_adapter"` |
| modality | `test_modality_pipeline.py`, `test_*_extractor.py`, `test_image_modality.py`, `test_parse_service_modality_wiring.py` | 顺序/开关/遥测；TATR 不覆盖启发式 markdown；explicit > env；References 段 heading 也 NONE，Appendix 终止 |
| DocIR 持久化 | `test_docir_persistence.py`, `test_translatability_guard.py` | `docir_*` 键落 `source_span_json`；`translatable=True` 不能覆盖 code/equation/table |
| Parse IR | `test_parse_ir.py`, `test_pdf_parse_ir_planning.py` | IR 在 modality 之后构建；page_plan intent `native_text/hybrid_merge`（用 fake parser 输出，未验证真实 `_assess_page_layout`） |
| refresh | `:7675-8138`, `test_pdf_structure_refresh_stale_sentences.py` | 原地更新 caption 链接；split 尾随散文挂 fragment；code→paragraph 更新；文本变化标 `refresh_sentences_stale`，仅空白变化不标 |
| EPUB | `test_epub_parser.py` | 类型序列、实体、malformed fallback 保留 `<think>` 字面量、figure/caption/table/math 映射、heading level 归一 `[1,2,3]`、non-linear/重复 spine 跳过、titlepage/TOC 章丢弃、subtitle 元数据、内联格式 markdown |
| 分段 | `test_sentence_segmenter.py` | `Dr./3.14/5 p.m.`；`Rs. 20` 不断而句末 `Rs.` 断；人名首字母；行首编号；heading 单句 |
| 导出侧对解析产物的消费 | `PdfDocumentImagePersistenceTests:8651-10601`, `PdfApiWorkflowTests`, `PdfReviewTests` | `render_mode` 组合、layout gate fail-closed、图注/上下文合并渲染、evidence 暴露、medium risk → structure issue 等 |

### 13.3 覆盖缺口（源码有、测试无）

- `classify.py` 87 个函数中约 65 个无任何直接引用（仅被 golden 快照间接覆盖）：包括 `_header_footer_signature / _is_book_running_header_text / _is_page_number_text`（页眉页脚零正面断言，golden 仅 `count<=1`）、`_assess_page_layout / _page_extraction_plan`、`_split_toc_entry`、`_extract_footnote_marker / _body_contains_footnote_anchor`、全部 paper-title / intro-title 归一化函数、`_looks_like_index_entry` 等。
- `pdf.py` 约 72 个方法无直接引用。**完全无覆盖（.py 与 golden 均无对应 flag）**：`_lock_listing_scope` 子系统（7 个 flag）、`_recover_text_only_figures`（`text_only_figure_synthesized/orphan_figure_synthesized`）、`_promote_contextual_image_legend_blocks`（`contextual_image_legend_promoted`）、`_promote_inline_book_heading_blocks`（`inline_book_heading_promoted`）、`cross_page_heading_merged`、`cross_page_table_fragments_merged`、`same_anchor_code_continuation_merged`、`embedded_book_heading_recovered / embedded_book_subheading_recovered`、`RecoveryContext.font_emphasis_available`。
- **跨页正文合并**（`_merge_cross_page_prose_continuations` 链）没有单元测试，flag 只在 2 个 golden JSON 出现。
- `_classify_role / _block_type_for_role / _page_zone / _parse_confidence_for_role` 零直接单测。
- 只有 `default_book` 与 `academic_paper` lane 被测；`outlined_book` 仅 1 个 profiler + 1 个折叠测试。
- Golden 是整档字符串比对：回归时产生数千行 diff，无法指出是哪条启发式变了；新增 writer 会先因文件名集合断言失败。
- 三个"软契约"：`GoldenCodeBlockTests`/`GoldenEquationBlockTests` 只要求"若分类为 code/equation 则 NONE"，允许漏判；`GoldenTwoColumnPaperTests` 只断言 `_ordered_page_blocks` 层；`GoldenNumberedSectionTests` 放弃断言 heading 识别。
- 无性能/内存/大文档测试；无非英文源测试；无损坏 PDF（`FileDataError` 退化）测试；无重复 bootstrap 幂等测试；无 refresh 锚点位移场景测试。

---

## 14. 结论性摘要（供优化立项参考）

1. **可译性协议断裂**：DocIR `translatability` 在 DB 侧无读取者，modality pipeline（尤其 References）不生效；应把 `docir_translatability` 接入 `block_rules` 或把 modality 结果写成 metadata `translatable` 键。
2. **锚点/ID 方案不可演进**：全局 reading-order 锚点 + ordinal 参与 id → 任何启发式改动都会让 refresh 失配、旧行残留；需要基于（页, 几何, 文本指纹）的稳定锚点与真正的 parse revision 版本化（含句子退役模型）。
3. **抽取层缺陷**：行内空白折叠让多条表格/TOC 规则失效；`FileDataError` 静默退化；outline -1 页号；图页多的书被整本 OCR；双重抽取与临时目录泄漏。
4. **启发式的系统性误报源**：IGNORECASE 的代码/列表/标题正则、固定 12% 页区、pt 绝对阈值在 OCR 像素坐标下失真、Manning 专属 listing 规则、英文停用词/标题词表。
5. **生产要素缺失**：无日志/指标、无超时/大小限制、同步阻塞请求、OCR 依赖网络与 `uv`、sidecar/临时文件路径不受配置管理、非英文源完全未处理。

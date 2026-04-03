# 高保真文档翻译增量改造方案

Last Updated: 2026-04-03

## 1. 目的

这份文档不是重新发明 book-agent，而是基于当前代码库已经具备的能力，给出一份面向“英文 PDF/EPUB -> 高保真中文版本”的增量改造方案。

当前系统已经具备完整的翻译生产线雏形：

- 上游解析与恢复：
  - [src/book_agent/domain/structure/pdf.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/structure/pdf.py)
  - [src/book_agent/domain/structure/epub.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/structure/epub.py)
  - [src/book_agent/domain/structure/ocr.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/structure/ocr.py)
- bootstrap / 分段 / packet：
  - [src/book_agent/services/bootstrap.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/bootstrap.py)
  - [src/book_agent/domain/context/builders.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/context/builders.py)
- 翻译 / 上下文 / memory：
  - [src/book_agent/services/translation.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/translation.py)
  - [src/book_agent/services/context_compile.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/context_compile.py)
  - [src/book_agent/services/memory_service.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/memory_service.py)
- review / rerun / export gate：
  - [src/book_agent/services/review.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/review.py)
  - [src/book_agent/services/layout_validate.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/layout_validate.py)
  - [src/book_agent/services/export.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/export.py)

真正的问题不是“系统不存在”，而是：

1. 当前持久化 IR 更偏翻译执行视角，缺少面向高保真导出的深层文档结构真相。
2. EPUB 解析已具备结构意识，但导出仍偏“重建阅读稿”，还不是“保留原结构并安全回写译文”。
3. PDF 已有 profiler / recovery lane / OCR / refresh / gate，但 exporter 仍以 merged HTML 为基底，更适合中文阅读稿，而不是高还原正式版。

## 2. 核心判断

### 2.1 应保留的东西

以下能力已经是项目优势，不应该推倒重来：

- `Document / Chapter / Block / Sentence / TranslationPacket / TranslationRun / TargetSegment / AlignmentEdge` 这一整套持久化与追踪链
- `review issue -> action -> rerun -> export gate` 的 fail-closed 控制面
- `chapter brief / termbase / entity registry / chapter memory` 的上下文编排机制
- PDF 的 `pdf_kind / layout_risk / recovery_lane` 思路
- OCR 与 structure refresh 的“可重跑、可修复、可证据化”路线

### 2.2 必须新增的东西

如果目标是“中文读者获得接近英文原版的体验”，下一阶段必须补两个能力层：

1. Canonical Document IR
2. Source-preserving Export

没有第一层，exporter 拿不到足够真相。
没有第二层，parser 再强也只能导出“还不错的阅读稿”。

### 2.3 不该一开始做的东西

以下方向首期不建议追求：

- 对所有 PDF 做页面级 1:1 中文覆盖式复刻
- 图像内文字全面重绘
- 把当前 DB 一次性改成高度范式化的 page/line/span 全量表
- 用单一大模型替代 parser 中已经有效的几何/规则链

这些事情不是永远不做，而是过早追求会拖垮主线。

## 3. 目标架构增量

### 3.1 现状

当前主链路大致是：

`source file -> ParsedDocument -> Chapter/Block -> Sentence -> Packet -> TranslationRun -> Review -> Export`

问题在于 `ParsedBlock` 仍然过粗：

- [src/book_agent/domain/structure/models.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/structure/models.py)

它对翻译执行足够，但对高保真导出不够。尤其缺少：

- line/span 级信息
- 更稳定的 source node identity
- 结构关系图
- CSS/DOM 对应关系
- 页内 zone / column / reading-order graph

### 3.2 目标

建议把架构改为双层 IR：

1. Canonical Document IR
2. Translation Execution IR

其中：

- Canonical Document IR 负责保存文档结构真相
- Translation Execution IR 继续服务现有 `block/sentence/packet/review/rerun/export gate`

关系应该是：

`source -> Canonical IR -> projection -> Block/Sentence/Packet -> translation/review -> source-preserving export`

## 4. Canonical IR 设计

### 4.1 设计原则

Canonical IR 不应首期就完全表化。更现实的路线是两步：

1. 先落成 revision + sidecar artifact
2. 再把高频查询对象结构化进数据库

这样可以避免第一阶段把大量精力花在 schema 爆炸上。

### 4.2 首期建议的数据边界

建议新增一个 parse revision 侧车层，至少表达这些对象：

- document metadata
- package/spine metadata for EPUB
- page for PDF
- region/zone
- block
- line
- span or text run
- asset
- relation

建议新增文件：

- `src/book_agent/domain/structure/canonical_ir.py`
- `src/book_agent/services/parse_ir.py`
- `src/book_agent/infra/repositories/parse_ir.py`

### 4.3 首期数据库建议

建议先新增最小表：

- `document_parse_revisions`
  - `id`
  - `document_id`
  - `source_fingerprint`
  - `parser_family`
  - `parser_version`
  - `ir_schema_version`
  - `status`
  - `artifact_path`
  - `metadata_json`
- `document_parse_revision_artifacts`
  - `id`
  - `revision_id`
  - `artifact_kind`
  - `storage_path`
  - `metadata_json`

其中 `artifact_path` 指向 JSON/JSONL sidecar，内容保存完整 Canonical IR。

不建议首期就新增十几张 `page/line/span` 表。查询热点明确后，再决定是否把以下对象正规化：

- page
- asset
- relation
- export source map

### 4.4 Canonical IR 中必须保留的字段

每个 node 至少要有：

- `node_id`
- `node_type`
- `parent_id`
- `ordinal`
- `source_kind`
- `source_locator`
- `text`
- `normalized_text`
- `role`
- `translatable`
- `confidence`
- `style_ref`
- `bbox_json`
- `metadata_json`

relation 至少要有：

- `relation_type`
- `src_node_id`
- `dst_node_id`
- `confidence`
- `metadata_json`

relation type 首期建议覆盖：

- `reading_order_next`
- `caption_for`
- `artifact_context_for`
- `footnote_ref`
- `footnote_body`
- `toc_target`
- `cross_page_continuation`
- `dom_target`

### 4.5 与当前模型的关系

现有 `Block` / `Sentence` 不应改成 Canonical IR 的直接替代品，而应变成“由 Canonical IR 投影出来的执行视图”。

建议在 [src/book_agent/services/bootstrap.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/bootstrap.py) 中新增一个阶段：

`parse source -> build canonical ir -> project execution artifacts`

然后当前 `Block.source_span_json` 继续保留，但增加：

- `parse_revision_id`
- `canonical_node_id`
- `projection_version`

`Sentence.source_span_json` 也增加：

- `parse_revision_id`
- `canonical_node_id`
- `projection_rule`

## 5. PDF 路线改造

### 5.1 保留的逻辑

以下逻辑应保留并继续演化：

- `PdfFileProfiler`
- `layout_risk`
- `recovery_lane`
- academic paper lane
- caption link / artifact context
- code/table special handling
- refresh / repair / review gate

### 5.2 必须补的能力

当前最大缺口不是“没有 OCR”，而是 OCR 与 native text 仍以文档级分流为主。

建议新增页级和区域级 plan：

- `native_text`
- `ocr_overlay`
- `hybrid_merge`
- `manual_risk_hold`

建议新增文件：

- `src/book_agent/domain/structure/pdf_page_plan.py`
- `src/book_agent/services/pdf_page_planner.py`

planner 输出到 Canonical IR metadata：

- `page_extraction_mode`
- `zone_extraction_mode`
- `zone_column_id`
- `reading_order_group`
- `risk_reasons`

### 5.3 PDF parser 的真正升级点

应把当前“排序后的 block 恢复”升级为：

1. page zoning
2. role classification
3. reading-order graph construction
4. cross-page linking
5. artifact and note linking
6. execution projection

也就是说，几何排序不再直接等价于阅读顺序，而要成为 graph 的输入证据。

### 5.4 OCR 策略

首期不建议把 `OcrPdfParser` 改成“整本 OCR 替代器”。

更合理的方向是：

- native text 是主真源
- OCR 只覆盖无字区、可疑区、低置信区
- merge 时保留 provenance，避免 OCR 噪声覆盖可信 native text

### 5.5 PDF exporter 定位

建议明确产品定义：

- 默认交付：`reading edition pdf`
- 诊断交付：`bilingual / review package / merged html`
- 高还原复刻：后续能力，不作为当前默认目标

对应到代码：

- 继续保留 [src/book_agent/services/export.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/export.py) 的 `merged_html -> pdf` 路线作为阅读稿主链
- 但把 `REBUILT_PDF` 的语义改得更明确：它不是 source-faithful facsimile，而是 `zh_reading_pdf`

## 6. EPUB 路线改造

### 6.1 当前问题

当前 EPUB 解析已经有结构意识，但 rebuilt EPUB 仍然是“根据渲染块重建一本新书”。

这条路线适合 fallback，不适合高保真主链。

### 6.2 新主路线

EPUB 应新增 source-preserving patch exporter。

建议新增文件：

- `src/book_agent/services/epub_patch_export.py`
- `src/book_agent/domain/structure/epub_dom_locator.py`

其核心不是重新生成章节，而是：

1. 解包原 EPUB
2. 遍历 spine
3. 定位可翻译 text node
4. 以稳定 locator 回写译文
5. 保留原始 id / href / note / nav / css / resources
6. 补最小中文样式覆盖
7. 重打包并做校验

### 6.3 EPUB Canonical locator

EPUB parser 在 Canonical IR 中必须保留：

- package path
- spine item id
- DOM path or XPath-like locator
- text-node ordinal
- inline style / class tokens
- anchor id
- href relations

这会成为高保真回写的真锚点。

### 6.4 EPUB exporter 的阶段划分

首期：

- patch text nodes
- 保持原 TOC / footnote / anchors
- 小范围中文排版 CSS

次期：

- heading/title 级细粒度排版优化
- poetry / list / code / callout 精细保持
- fixed-layout EPUB 定向策略

不建议首期做：

- 大规模 DOM 重排
- 强制改写原 CSS 体系
- 对所有文本节点做激进合并或拆分

## 7. 翻译执行层增量

### 7.1 现状

当前 packet builder 已经比较成熟，但构建边界仍然主要从 `Block` 和句数阈值出发：

- [src/book_agent/domain/context/builders.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/context/builders.py)

### 7.2 建议

保留现有 packet / review / rerun 体系，但让 packet 的 source 不再直接依赖粗粒度 block，而是依赖 Canonical IR 派生出的 `TranslationUnit`。

建议新增投影对象：

- `TranslationUnit`
  - `unit_id`
  - `canonical_node_ids`
  - `unit_type`
  - `display_text`
  - `alignment_policy`
  - `translation_policy`
  - `context_scope`

不同 unit type 的策略不同：

- body paragraph: 意译优先，信息锁定
- heading: 更重中文标题可读性
- caption: 兼顾描述与索引号稳定
- footnote: 更保守
- reference/index: 保护优先
- equation/table context: 高约束

### 7.3 多阶段翻译

当前主链路可在不推翻的前提下补成四段：

1. draft translation
2. alignment and completeness check
3. term/style consistency pass
4. risk-based second pass

高风险单元进入第二遍的触发条件建议包括：

- OCR low confidence
- first mention of locked term
- equation/table/caption adjacency
- long paragraph compression risk
- detected style drift

## 8. Export 层增量

### 8.1 EPUB

新增 export type 更合理：

- `PATCHED_EPUB`

现有：

- `REBUILT_EPUB`

保留为 fallback 或 source structure 已丢失时的兜底交付。

### 8.2 PDF

建议重新命名或在文档中明确：

- `REBUILT_PDF` 实际语义是“中文阅读版 PDF”

如果后续真要做 source-faithful PDF，可新增独立 export type，而不是继续让当前 `REBUILT_PDF` 语义漂移。

### 8.3 Export source map

无论 EPUB 还是 PDF，最终都建议产出 export source map sidecar，至少记录：

- target block / node id
- canonical source node ids
- translation unit ids
- exported locator
- asset dependencies

这会直接提升 debug、人工修订和增量重导能力。

## 9. Review / Gate 增量

现有 review 和 export gate 是强项，下一阶段不要削弱，只要前移。

建议新增三类 gate：

1. parse projection gate
2. source-preserving export gate
3. document-family acceptance gate

### 9.1 parse projection gate

在 `Canonical IR -> Block/Sentence` 之后增加检查：

- reading order continuity
- footnote pairing completeness
- caption link completeness
- DOM locator coverage for EPUB
- translation unit projection completeness

### 9.2 export gate

在当前 layout validate 之外增加：

- EPUB anchor/link integrity
- EPUB manifest/spine/nav consistency
- PDF asset completeness
- export source map completeness

## 10. 分阶段执行

### V1：补 Canonical IR 和 EPUB 主链

目标：

- 不推翻现有生产线
- 建立 parser 真源层
- 把 EPUB 做成真正高保真主链

范围：

- `document_parse_revisions` 最小表
- Canonical IR sidecar artifact
- execution projection
- patched EPUB exporter
- projection gate

不做：

- 全量正规化 page/line/span 表
- source-faithful PDF facsimile

验收：

- EPUB 导出默认走 patch 路线
- TOC / footnote / internal href 不损坏
- Block/Sentence/Packet 主链保持兼容

### V2：补 PDF 页级 hybrid extraction 和 TranslationUnit

目标：

- 让 mixed/scanned/high-risk PDF 的错误不再整本扩散
- 让 translator 消费更正确的 unit

范围：

- page/zone planner
- OCR overlay merge
- reading-order graph metadata
- TranslationUnit projection
- parser-level acceptance corpus

不做：

- 页面级中文覆盖式复刻

验收：

- 高风险 PDF 的 fail-closed 更稳定
- mixed PDF 误入整本 OCR 的比例显著下降
- review issue 更集中在真实风险点

### V3：补 source-faithful export 和深层结构化查询

目标：

- 在少量文档家族上做更高还原交付
- 补足调试与可观测性

范围：

- 部分 Canonical IR 正规化
- export source map 查询层
- facsimile-style PDF 小范围试点

不做：

- 在所有 PDF 上承诺 1:1 复刻

## 11. 推荐的第一批改动文件

如果按“最小但关键”的原则推进，建议第一批就只动这些区域：

- 新增：
  - `src/book_agent/domain/structure/canonical_ir.py`
  - `src/book_agent/services/parse_ir.py`
  - `src/book_agent/infra/repositories/parse_ir.py`
  - `src/book_agent/services/epub_patch_export.py`
- 扩展：
  - [src/book_agent/services/bootstrap.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/bootstrap.py)
  - [src/book_agent/domain/structure/epub.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/structure/epub.py)
  - [src/book_agent/services/export.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/export.py)
  - [src/book_agent/domain/models/document.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/models/document.py)
  - `alembic/versions/*`

## 12. 最后的取舍

### 如果资源只够做一件大事

优先做：

`Canonical IR + patched EPUB exporter`

原因：

- 它最能提升高保真上限
- 复用现有 translator/review 主线最多
- 风险比 PDF facsimile 小很多
- 一旦打通，后续 PDF 也有了共同真源层

### 当前最容易被低估的模块

不是 translator，而是 exporter。

更准确地说，是“exporter 所依赖的 source truth”。

如果 source truth 不够深，exporter 永远只能做“尽量看起来合理”，做不到“尽量忠于原作结构和阅读体验”。

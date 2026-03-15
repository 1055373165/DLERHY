# PDF Support Implementation Plan

Last Updated: 2026-03-15

## 1. Current Stage Assessment

### Current project phase

项目当前严格来说仍处于 **P0 late-stage / P0 hardening complete**，而不是已经进入 P1 实现。

更准确地说：

- **P0 的 EPUB-only 主链路已经打通并经过真实书验证**
- **P0 的 run control、QA、rerun、export、operator console 都已经具备生产雏形**
- **P1（PDF support）还没有真正开始实现**

因此当前最合理的阶段判断是：

> **项目已完成 EPUB 路线的 P0 目标，具备进入 P1 文本型 PDF 支持阶段的工程基础。**

### Why this matters

这意味着：

- 我们 **不需要重做翻译核心链路**
- 也 **不应该把 PDF 当成“重新做一遍系统”**
- P1 的重点应该是：
  - 补足 PDF 上游解析与结构恢复
  - 把 PDF 中的不确定性显式传播进既有 QA / rerun / export 体系
  - 控制 PDF 错误不要污染下游翻译与交付链

## 2. What We Already Have That PDF Can Reuse

以下能力可以直接复用，不应因引入 PDF 而推倒重来：

- document / chapter / block / sentence / packet 主数据模型
- stable sentence ID、alignment graph、provenance
- termbase / entity registry / chapter brief / packet builder
- translation worker abstraction / provider adapter
- QA / issue / action / rerun / invalidation 体系
- export gate / review package / bilingual / merged HTML
- run control plane
- operator console / worklist / assignment / owner workload

换句话说：

> PDF 支持不是翻译系统重构，而是 **ingest + parse + structure recovery + PDF-specific QA** 的增量扩展。

## 3. What Is Missing For PDF Today

当前代码库尚缺少以下 PDF 专属能力：

- PDF 文件接入与格式分流
- 文本型 PDF 与扫描型 PDF 的自动判型
- PDF 几何信息驱动的 block recovery
- 阅读顺序恢复
- 页眉页脚检测与剔除
- 跨页断句与断词修复
- 脚注锚点恢复与归位
- 目录 / bookmark / 章节恢复
- PDF-specific QA
- PDF-specific export/source provenance

从风险角度看，**这些缺失都在上游**。  
这也是为什么 P1 的验收重点必须从“译得像不像”转到“结构恢复错没错”。

## 4. Recommended Rollout Strategy

不建议直接上“全 PDF 支持”。  
建议拆成三个工程台阶：

### P1-A: Text PDF, simple layout first

范围：

- 文本型 PDF
- 单栏或近似单栏
- 有可提取文本层
- 简单脚注 / 简单目录 / 简单 heading

目标：

- 让 PDF 能进入现有翻译主链路
- 保证结构恢复和 coverage ledger 不失真
- 不把复杂版式误装成“已支持”

### P1-B: Text PDF, robust recovery

范围：

- 页眉页脚更复杂
- 目录页、附录、索引
- 跨页段落 / 脚注 / 引文
- 更复杂的 paragraph reconstruction

目标：

- 提升真实出版 PDF 的可用率
- 降低 header/footer / footnote / TOC 噪声污染

### P2: Scanned PDF and complex layout

范围：

- 扫描型 PDF
- OCR
- 双栏
- 复杂图表与混排
- 低质量复印 / 倾斜 / 噪声页

目标：

- 覆盖真实出版场景
- 但成本、延迟、人工审校比例显著增加

## 5. Guiding Principles For PDF

### Principle 1

PDF 输入必须先分流，再进入统一中间表示。

### Principle 2

不确定性必须显式传播，而不是让模型“猜”。

### Principle 3

复杂版式优先 fail-safe，不要假装支持。

### Principle 4

P1 先解决“阅读顺序和结构对不对”，再谈“译文像不像”。

### Principle 5

PDF-specific QA 必须在翻译前后都介入，而不是只做最终 review。

## 6. Target Architecture Delta For PDF

建议在现有架构上新增或扩展这些能力层：

### 6.1 File Intake Extension

新增：

- `source_type=pdf`
- 文件指纹与页数探测
- 文本层存在性探测
- `pdf_profile`
  - `text_pdf`
  - `scanned_pdf`
  - `mixed_pdf`
  - `complex_layout_suspected`

输出：

- `document`
- `pdf_file_profile`

### 6.2 PDF Parse Layer

P1 推荐主路径：

- **Primary extractor:** `PyMuPDF` / `fitz`
  - 原因：同时给文本、块、span、bbox、page geometry、outline/bookmarks
- Optional fallback:
  - `pdfminer.six` 只作为对照或 fallback，不作为默认主链

P1 暂不引入 OCR 主路径。

输出：

- `page objects`
- `text spans`
- `block candidates`
- `outline candidates`
- `page-level geometry`

### 6.3 Structure Recovery Layer

新增 PDF-specific recovery：

- reading order resolver
- header/footer detector
- paragraph merger
- dehyphenation repair
- cross-page continuation repair
- footnote detector / relocater
- TOC / bookmark reconciler
- protected block classifier

输出仍应统一到既有 IR：

- `chapters`
- `blocks`
- `sentences`

但每个对象要额外带 PDF provenance：

- `page_number`
- `bbox`
- `reading_order_index`
- `parse_confidence`
- `recovery_flags`

## 7. Detailed P1 Implementation Plan

## 7.1 Workstream A: PDF Intake And Classification

### Goal

在 ingest 阶段把 PDF 分成“能直接走文本恢复”和“必须 later OCR / 人工介入”两类。

### Deliverables

- `PdfFileProfiler`
- `source_type=pdf`
- `pdf_profile_json`

### Required checks

- 页数
- 是否存在文本层
- 页面文本密度
- 每页平均 span 数
- 版式复杂度初判
- bookmark/outline 可用性

### Output example

```json
{
  "pdf_kind": "text_pdf",
  "page_count": 312,
  "has_extractable_text": true,
  "outline_present": true,
  "layout_risk": "low",
  "ocr_required": false
}
```

### Acceptance

- 能把明显扫描 PDF 与文本型 PDF 区分开
- 对复杂版式给出 `layout_risk`，而不是直接放行

## 7.2 Workstream B: Text PDF Extraction

### Goal

从文本型 PDF 中提取 geometry-aware text blocks，为后续恢复提供基础。

### P1 scope

- page
- block
- line
- span
- bbox
- font size / style hints

### Recommended object additions

对 `blocks` / `sentences` 增加：

- `source_page_start`
- `source_page_end`
- `source_bbox_json`
- `reading_order_index`
- `parse_confidence`
- `pdf_block_role`

其中 `pdf_block_role` 至少支持：

- `body`
- `heading`
- `header`
- `footer`
- `footnote`
- `caption`
- `table_like`
- `code_like`
- `equation_like`
- `toc_entry`

### Acceptance

- 每个 block 都能回指页号和 bbox
- extraction 本身不直接切 sentence
- geometry 信息完整落库

## 7.3 Workstream C: Reading Order Recovery

### Goal

恢复“人类阅读顺序”，避免 PDF 文本抽取顺序污染翻译。

### P1 scope

只做：

- 单栏 / 近单栏
- 段落纵向顺序稳定
- 页内 heading/body/footnote 的基本排序

### P1 explicitly out of scope

- 双栏 robust support
- 复杂 sidebar / marginal notes
- 图文交错复杂排版

### Strategy

结合这些信号排序：

- page number
- y coordinate
- x coordinate
- font size / style
- line overlap / block overlap
- page region buckets

同时做 suspicious detection：

- 同页存在明显双栏分布
- 左右块交替穿插
- block 宽度和 x cluster 异常

### If suspicious

不要继续假装成功：

- 打 `STRUCTURE_SUSPECT`
- 降低 `parse_confidence`
- P1 可选择：
  - 阻止继续自动翻译
  - 或进入高风险人工确认

### Acceptance

- 简单文本 PDF 的 block 顺序与人类阅读顺序基本一致
- 对明显双栏 PDF 不误判为低风险

## 7.4 Workstream D: Header/Footer Stripping

### Goal

防止页码、页眉、章名 running header 污染正文和 coverage。

### Strategy

跨页重复检测：

- 同位置重复文本
- 高频页码模式
- 顶部/底部固定区域
- 低内容熵字符串

### Output

- block role 标记为 `header/footer`
- `translatable=false`
- 但仍保留 provenance，不能直接消失

### Acceptance

- coverage ledger 不应把页码页眉算成漏译
- 导出不应混入 running header

## 7.5 Workstream E: Paragraph Reconstruction

### Goal

把 PDF 中被行级切碎的正文恢复成合理 paragraph block。

### Strategy

按以下信号合并：

- vertical gap
- indentation
- font continuity
- punctuation continuation
- lowercase / hyphen continuation
- page-break continuation

不要在这些地方盲合并：

- heading 后
- caption 后
- footnote 区
- code / table / equation block

### Acceptance

- paragraph block 基本对应原书自然段
- 不把 code/table/footnote 合进正文

## 7.6 Workstream F: Cross-Page Repair

### Goal

修复页尾截断导致的段落和句子失真。

### Must handle

- 连字符断词
- 跨页句子续写
- 跨页段落续接

### Rules

- `hyphen + lowercase continuation` 优先修复
- 页末无终止标点 + 下一页起始为 continuation 的正文行，可合并
- 但 code/table/footnote 不走同一规则

### Acceptance

- sentence ledger 不因跨页断裂产生大量伪句子
- coverage 对齐不被 page breaks 扰乱

## 7.7 Workstream G: Footnote Recovery

### Goal

把脚注从“混进正文的噪声”恢复成有归属的独立 block。

### P1 scope

- 常见数字脚注
- 页脚脚注
- 简单 anchor

### Strategy

- 检测页脚区域
- 检测小字号 / 分隔线 / 脚注编号模式
- 把脚注内容标为 `footnote`
- 如果能匹配到正文 anchor，则记录 linkage

### P1 compromise

匹配不稳时：

- 脚注至少独立保存
- 不强行并入正文

### Acceptance

- 脚注不污染正文 paragraph
- 能导出成独立 footnote artifact

## 7.8 Workstream H: TOC / Bookmark / Chapter Recovery

### Goal

恢复章节树，而不是把整本 PDF 当成长文平铺。

### Signals

- PDF bookmarks / outline
- TOC page text
- heading typography
- chapter numbering pattern

### Priority

1. bookmarks / outline
2. heading typography
3. TOC text heuristic

### Acceptance

- 简单技术书 PDF 能恢复基本 chapter tree
- chapter 重跑仍然成立

## 7.9 Workstream I: PDF-Specific Sentence Ledger

### Goal

在 PDF 中仍保持稳定句级覆盖账本。

### Requirements

- sentence ID 继续稳定生成
- 但 source provenance 要带 page + bbox + block role
- `translatable=false` 的 header/footer/部分 artifact 不计入漏译

### Additional safeguards

- `sentence_source_confidence`
- `sentence_parse_flags`
- `cross_page_repaired`

### Acceptance

- coverage 可证明
- PDF 上游噪声不会把 coverage 指标搞假

## 7.10 Workstream J: PDF-Specific QA

必须新增这些 QA：

### Reading Order Suspicion

触发：

- x-cluster 异常
- 同页 block 顺序跳变

动作：

- `STRUCTURE_SUSPECT`
- chapter / page 风险升级

### Header/Footer Leakage

触发：

- 重复页眉页脚出现在 translatable block

动作：

- `FORMAT_POLLUTION` 或 `STRUCTURE_SUSPECT`

### Cross-Page Fragmentation

触发：

- 伪句子异常增多
- 页末页首疑似断裂

动作：

- `SEGMENTATION_RISK`

### Footnote Orphaning

触发：

- 脚注块存在，但未归位且疑似污染正文

动作：

- `FOOTNOTE_RECOVERY_REQUIRED`

### Multi-column Suspicion

触发：

- block x clustering 强双峰

动作：

- P1 默认高风险标记
- 不自动宣称低风险通过

## 8. Data Model Changes Recommended For PDF

建议新增字段，但不要重构主模型：

### On `blocks`

- `source_page_start`
- `source_page_end`
- `source_bbox_json`
- `pdf_block_role`
- `parse_confidence`
- `recovery_flags_json`

### On `sentences`

- `source_page_start`
- `source_page_end`
- `source_bbox_json`
- `sentence_source_confidence`
- `sentence_parse_flags_json`

### New artifacts (optional but recommended)

- `pdf_pages`
- `pdf_page_blocks`
- `pdf_outline_entries`

这些对象是为了：

- debug recovery
- page-level rerun / inspection
- page-level QA evidence

## 9. Workflow Changes

现有流程：

`ingest -> parse -> segment -> profile -> brief -> packet -> translate -> review -> export`

对 PDF 建议变成：

`ingest -> pdf_profile -> extract_pages -> recover_structure -> normalize_blocks -> segment -> profile -> brief -> packet -> translate -> review -> export`

其中新增中间状态：

- `PDF_PROFILED`
- `PDF_EXTRACTED`
- `PDF_STRUCTURE_RECOVERED`

## 10. Operator Console / API Changes Needed

P1 不需要先做复杂 UI，但至少要支持：

- document summary 显示 `pdf_kind / layout_risk`
- chapter/worklist 能看到 `structure_suspect`
- review package 中能看到 page-level evidence

建议新增 API 字段：

- `document.pdf_profile`
- `chapter.parse_confidence`
- `chapter.structure_risk`

## 11. Acceptance Criteria

## P1-A Acceptance

- 文本型单栏 PDF 可以 bootstrap 成 document/chapter/block/sentence
- chapter tree 基本可恢复
- header/footer 大部分可剔除
- coverage ledger 可信
- chapter 可独立 rerun
- review package 可指出结构恢复风险

## P1-B Acceptance

- 页脚脚注能独立保存，尽量归位
- 跨页 paragraph / sentence 修复稳定
- TOC / bookmarks / heading 组合能恢复大多数章节
- 真实技术书 PDF 可以进入全文翻译与导出，不因明显解析问题静默放行

## P1 Fail Conditions

以下情况若无法识别并阻止，视为 P1 失败：

- 双栏错序却仍被当成低风险正文
- 大量页眉页脚进入翻译主链路
- 脚注大面积混入正文
- 覆盖率因为 PDF 噪声而失真

## 12. Recommended Implementation Order

这是最建议的工程顺序：

1. `PdfFileProfiler`
2. text PDF extractor with geometry
3. block role classification
4. reading order resolver for simple layout
5. header/footer stripping
6. paragraph reconstruction
7. cross-page repair
8. sentence ledger + provenance
9. PDF-specific QA
10. chapter recovery / TOC / outline
11. review/export integration
12. real-book PDF smoke

## 13. Suggested First Implementation Milestone

如果新线程要开始干活，我建议第一里程碑只做：

### Milestone 1

**Text PDF, single-column, low-risk only**

交付：

- 能 ingest PDF
- 能区分 text/scanned
- 能抽 geometry blocks
- 能做简单 reading order
- 能剔 header/footer
- 能进入现有 translate/review/export 主链路
- 对复杂 layout 明确拒绝或高风险打标

这版先不要做：

- OCR
- 双栏 robust support
- 复杂表格
- 复杂脚注
- 图像 OCR

## 14. Practical Advice For The Next Thread

下一线程不应该从“翻译”开始，而应该从这三个对象开始建：

- `PdfFileProfiler`
- `PdfTextExtractor`
- `PdfStructureRecoveryService`

只要这三个对象先站住，后面的 packet / translation / QA 都能复用现有体系。

## 15. One-Sentence Summary

> 当前项目已经完成 EPUB 路线的 P0，可进入 P1；PDF 支持应被实现为 **上游解析与结构恢复扩展**，而不是翻译系统重写，第一阶段应聚焦 **文本型、单栏、低风险 PDF**，并把所有不确定性显式传播进既有 QA / rerun / export 控制面。

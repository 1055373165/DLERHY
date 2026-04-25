# PDF Translation Pipeline v2 — M1 实施计划

> 依据:`specs/pdf-v2` 六阶段规格文档 · 北极星:**"不知道比错更重要"**
> M1 范围:DocIR 契约化 + 核心失败模式堵死 · 预计 12-18 人天 · 单人 2-3 周

## 里程碑映射

- **M1**:契约与核心失败模式(本文件)
- **M2**:置信度路由 + VLM 兜底 + 文档级术语
- **M3**:模态专家(Table / Equation / Paper subtype)
- **M4**:扫描件硬化 + 长文档 ToC

---

## M1 任务清单(按依赖拓扑)

### M1.1 · DocIR 字段扩展 (Task #29)

- [ ] `ParsedBlock` 加 4 个可选字段:
  - `translatability: str` — 默认 `"translate_all"`
  - `provenance: str` — 默认 `"text_layer"`
  - `confidence_breakdown: dict` — 默认 `{}`(含 `layout_conf` / `text_conf` / `class_conf` / `sanity_ok` 子指标)
  - `style_hints: dict` — 默认 `{}`(`font_family` / `is_mono` / `font_size_rank` / `is_italic` / `is_bold`)
- [ ] 定义 `Translatability` 与 `Provenance` 常量(不用 Enum 以保 frozen dataclass 兼容)
- [ ] 现有 EPUB/PDF parser 默认值覆盖,零代码变更前提下跑通

**验收**:`pytest tests/` 全绿;现有 EPUB 端到端跑通。

---

### M1.2 · Text-Layer Sanity Gate (Task #30)

- [ ] 新模块 `src/book_agent/domain/structure/text_layer_sanity.py`:
  - `SanityReport` dataclass(`ok`, `metrics`, `reason`)
  - `assess(page: fitz.Page) -> SanityReport`
  - 三指标:unicode 字符熵、PUA 比例、词典命中率
- [ ] `PyMuPDFTextExtractor` 每页抽取前先 assess,若 `ok=False` 则页级元数据标 `text_layer_sanity_failed: true`,不信任文本层
- [ ] 注入坏字体金标(构造 PDF:把 "hello" 经 PUA 编码后 embed),验证 gate 100% 触发

**验收**:gate precision ≥0.85 / recall ≥0.90(在小规模构造金标上)。

---

### M1.3 · 多栏阅读序真正重排 (Task #31) ✅

**实际收敛**:发现 `_academic_column_major_blocks` 已实现完整的列重排逻辑,只是被 `profile.recovery_lane == "academic_paper"` 锁在学术路径。本任务把它解放到所有 lane:

- [x] 重命名 `_academic_column_major_blocks` → `_column_major_blocks`,移除 lane 限制
- [x] `_ordered_page_blocks` 改为仅依赖 `_page_has_multi_column_signature` 判断(grouping 失败时保守回退到 top-down,逻辑不变)
- [x] `tests/test_pdf_column_reorder.py`:构造双栏 page + 单栏 page 两个对照测试,证明 book lane 的多栏页面现在按 LEFT→RIGHT 顺序排列

**验收**:非学术 lane 双栏页面重排正确(测试通过);单栏页面行为不变(回归零);基线零新增失败。

---

### M1.4 · 可译性协议执行 (Task #32)

- [ ] PDF parser 在分类时赋值 `translatability`:
  - `CODE` / `EQUATION` / `REFERENCE_ITEM` / URL-like block → `translate_none`
  - 其他 → `translate_all`
- [ ] EPUB parser 同样处理 `<pre>` / `<code>` / `cite` 等
- [ ] 翻译服务入口读 `block.translatability`:
  - `translate_none` → 跳过 LLM,`target = source`,标 provenance
  - `translate_prose_only` → 提取 prose 片段翻译,保留非 prose 原样
- [ ] 翻译后 schema-level 校验:`translate_none` 的 target 含 CJK 即拒绝

**验收**:code/math/references 零误译(M1.5 测试覆盖)。

---

### M1.5 · 不可译泄漏保护 + 测试骨架 (Task #33)

- [ ] 新 `tests/test_translatability_guard.py`:
  - 构造 code/math/reference/URL 块,跑完整翻译流
  - 断言:`target_text` 与 `source_text` 逐字符一致
  - 断言:无 CJK 字符
- [ ] CI 集成:该测试失败即 block merge

**验收**:测试通过;任意后续改动引入 leak 即 red。

---

## 依赖拓扑

```
M1.1 (ParsedBlock 扩字段)
  ├─► M1.2 (sanity gate 把结果写回字段)
  ├─► M1.4 (translatability 协议)
  │      └─► M1.5 (leak guard 测试)
  └─► M1.3 (多栏重排,独立,可并行)
```

M1.1 是全局前提;M1.3 可并行;M1.4 → M1.5 串行。

---

## 执行原则

1. 每个任务单独 PR(或同一 PR 分 commit),便于独立 rollback
2. 任务开始前先读相关现有模块,了解已有契约
3. 每个任务完成后立刻跑全量 `pytest`,任何回归即停
4. 金标回归集初始化(3-5 份 PDF)作为并行旁路任务,不阻塞 M1 主线

---

## 回归集(M1 结束时初始化)

位置:`tests/golden_pdfs/`

- `academic_2col.pdf` — 2 栏论文(多栏重排测试)
- `corrupted_font.pdf` — 构造的 PUA 编码乱码(sanity gate)
- `code_block_book.pdf` — 含 Python/JS 代码块(translatability)
- `math_paper.pdf` — 含公式(equation translatability)
- `clean_book.pdf` — 干净对照组

---

## 非目标(M1 不做)

- VLM 调用 / 路由(M2)
- 表格结构恢复(M3)
- LaTeX 公式还原(M3)
- ToC / cross-ref(M4)
- 扫描件加固(M4)
- 文档级术语表(M2)

---

## M2 已完成子项

### M2.1 · Page sanity → ParsedBlock.provenance 传导 (Task #34) ✅

- [x] `_build_chapters` 接受可选 `pages` 参数,构建 `sanity_by_page` 查找表
- [x] `_build_parsed_block` 闭包根据 `block.page_start` 查 sanity,失败时 `provenance=PROVENANCE_OCR`,成功时保持默认 `PROVENANCE_TEXT_LAYER`
- [x] `confidence_breakdown` 记录 `sanity_ok` + `sanity_reason` 供 router 诊断
- [x] `tests/test_pdf_sanity_propagation.py` 两测试覆盖:失败页 → OCR provenance、缺省页 → 不回归

**价值**:M1.2 的 sanity 判断现在真的能被下游消费。M2.2 router 可以读 `ParsedBlock.provenance` 决定是否送 OCR/VLM 重抽。

---

### M2.2 · Block-level Extraction Router (Task #35) ✅

- [x] 新模块 `domain/structure/extraction_router.py`:`RouterDecision` 枚举 {KEEP, ESCALATE_OCR, ESCALATE_VLM, SKIP, NOT_APPLICABLE} + `RouterContext` 策略参数 + 纯函数 `route()` + `summarize()` 做遥测聚合
- [x] 决策表(按优先级):translate_none→NOT_APPLICABLE → figure/image→SKIP → vlm provenance→KEEP → sanity_fail→ESCALATE_OCR/VLM → ocr provenance 且 sanity ok→KEEP → 默认→KEEP
- [x] `tests/test_extraction_router.py` 13 个测试覆盖所有决策分支 + summarize 聚合

**价值**:M2.1 埋的 `provenance` + `confidence_breakdown` 现在有消费方。Router 是无副作用的,可以在未接 OCR/VLM 实际调用前先被下游调用记录决策,形成 shadow deployment。

---

### M1 收尾 · 金标回归骨架 ✅

- [x] `tests/golden_pdfs/` 目录 + `__init__.py`(说明为何用程序化生成而非二进制提交)
- [x] `tests/golden_pdfs/fixtures.py`:5 个生成器 — `make_clean_book` / `make_two_column_paper` / `make_code_block_book` / `make_reference_list` + `corrupted_text_sample()`(文本 fixture 避免构造破坏字体 PDF 的复杂度)
- [x] `tests/test_golden_pdf_regression.py` 5 个 harness 测试类,每类对应一个规格 §3.1 失败模式
- [x] 顺手修 `derive_translatability` 一致性 bug:之前 `translatable=True` 会错误地让 code/equation 绕过块类型保护,现已对齐 `block_rules.translatability_for_block` 的优先级语义

**价值**:所有后续 PDF 改动在 CI 上都有 5 份可独立生成的 PDF 做结构化断言。每个 fixture 对应一个具体失败模式,回退即触发 red。

---

### M2.3a · OCR 重抽取适配器接口 + 注入(Task #36)✅

- [x] `domain/structure/ocr_reextraction.py`:`OcrReextractionRequest` dataclass(anchor/page/bbox/current_text/failure_reason)+ `OcrReextractionAdapter` Protocol + `NoOpOcrReextractionAdapter` 默认实现
- [x] `PdfStructureRecoveryService.__init__` 新增 `ocr_reextraction_adapter` kwarg(None=no-op)
- [x] `recover()` 末尾调 `_apply_ocr_reextraction`:扫描所有 block 的 `confidence_breakdown.sanity_ok is False`,构造 requests,调 adapter,用 `dataclasses.replace` 写回新 text + `provenance=OCR` + `sanity_ok=True` + `reextracted_via=ocr_adapter` 审计痕
- [x] `tests/test_ocr_reextraction_wiring.py` 5 个测试:默认路径 / NoOp / Fake 全量替换 / Fake 部分替换 / sanity OK 的页面不送 adapter

**价值**:现在 **把 Surya OCR runner 包成一个 OcrReextractionAdapter 实现,就能让 sanity 失败的页面真正走 OCR**。所有上游协议、下游回写逻辑都已就位,只差 M2.3b(Surya adapter 实现)。

---

### M2.3b · Surya-backed OCR 适配器(Task #37)✅

- [x] `domain/structure/surya_reextraction.py`:`SuryaOcrReextractionAdapter` 包装已有 `OcrPdfTextExtractor`
- [x] 子集 PDF 构建(PyMuPDF `doc.insert_pdf(from_page, to_page)`)让 Surya 只 OCR 失败页,成本与失败页数成正比而非文档长度
- [x] bbox 相对坐标匹配([0,1] 归一化 + 面积交叠比阈值 0.25)避开 DPI 问题;无匹配时回落到全页文本
- [x] 成本护栏 `max_failed_pages_per_doc=20`(超限直接返回 `{}` 不触发 Surya)
- [x] 容错:Surya 失败 / subset 失败 / page dims 失败 → 全部降级返 `{}` 并把原因写进 `SuryaAdapterMetrics.error`
- [x] `last_metrics` 暴露遥测:requests_seen / pages_requested / pages_ocrd / replacements_returned / cost_guard_tripped / error
- [x] `tests/test_surya_reextraction_adapter.py` 11 测试:bbox helper(normalize/overlap 5 种)+ adapter 主路径(empty/cost_guard/surya_failure/happy/no_overlap_fallback/nonexistent_page)

**价值**:构造 `PdfStructureRecoveryService(ocr_reextraction_adapter=SuryaOcrReextractionAdapter())` 即可让 sanity-fail 页面真 OCR。 端到端 user-visible 改动的最后一公里已铺通。剩下的是在 bootstrap 层把 adapter 注入到 recovery service,以及在遥测/成本控制层加运营护栏。

---

### M2.3 闭环 · Bootstrap 接入(Task #38)✅

- [x] `domain/structure/pdf.py` 新增 `build_default_recovery_service()` factory + `_sanity_ocr_reextraction_enabled()` env 检查
- [x] Env flag `BOOK_AGENT_PDF_SANITY_OCR_REEXTRACTION` 接受 `1/true/yes/on` 作为真值,默认关闭 → 行为完全向后兼容
- [x] `PDFParser.__init__` 默认从 factory 取 recovery service,显式传入的 `recovery_service=` 仍优先(explicit > env)
- [x] `OcrPdfParser` 不接入 adapter(扫描路径已全 OCR,再跑一次 reextraction 没意义)
- [x] `tests/test_pdf_bootstrap_adapter_wiring.py` 8 测试:feature flag 真/假值识别 + factory 两分支 + PDFParser 三种配置组合(默认+off、默认+on、显式 recovery_service 优先)

**价值**:**M2 北极星现已可 opt-in 生效**。运维团队设置 `BOOK_AGENT_PDF_SANITY_OCR_REEXTRACTION=1` 后,所有 sanity 失败的页面都会走真 Surya OCR 得到修复后的文本;不设则行为与 M1 完全一致。逐步灰度、观察遥测、最终转默认,按运营节奏推进。

---

### M2.8 · DocIR 字段 → Block.source_span_json 持久化(Task #39)✅

- [x] `services/bootstrap.py::ParseService._build_block` 把 DocIR 四字段(translatability/provenance/confidence_breakdown/style_hints)写入 `source_span_json` 的 `docir_*` 键
- [x] 无 schema 变更,复用既有 JSON 列
- [x] `tests/test_docir_persistence.py` 4 测试:clean paragraph / code block / sanity-failed block / 既有 metadata 保留

**价值**:DocIR 协议**真正落到数据库**,下游服务(block_rules、worker、export)读 `docir_*` 键即可消费。为 M2.7 提供了稳定的读路径。

---

### M2.5 · Pass A 术语挖掘(Task #40)✅

- [x] `services/terminology_miner.py`:纯函数 `mine_terms(ParsedDocument, top_k, min_frequency, max_ngram) -> list[TermCandidate]`
- [x] 1/2/3-gram 候选 + 停用词首尾过滤 + 专有名词与缩略语加权 + 定义句式(`X is defined as` / `(ABBR)`)加权
- [x] `TermCandidate` 携带 provenance(首次出现 chapter/block/ordinal)便于审阅定位
- [x] `tests/test_terminology_miner.py` 8 测试:bigram 召回 / 停用词剔除 / 代码块不泄漏 / 缩略语加权 / 定义句式低频穿透 / 专名优先 / top_k / provenance

**价值**:文档级 salient term 候选可挖出,作为 M2.6 上游输入。

---

### M2.6 · Glossary 锁服务(Task #41)✅

- [x] `services/glossary_service.py`:发现已有 `TermEntry` 表 + `LockLevel{SUGGESTED, PREFERRED, LOCKED}` + `TermStatus{ACTIVE, SUPERSEDED, REJECTED}` 基础设施,**零 schema 变更**,直接包装
- [x] API:`upsert_candidates` / `lock_term` / `unlock_term` / `get_locked_terms(document_id)` / `list_document_entries`
- [x] 版本化 supersede 机制与既有 chapter-concept-lock 统一;session.flush 保证同一事务内多次操作可见
- [x] `tests/test_glossary_service.py` 11 测试:upsert 幂等 / 已锁定跳过 / 锁定升级 SUGGESTED / idempotent 同值 / 改 target 时 supersede / 空值拒绝 / unlock 保留 target 降级 / 未知 term no-op / get_locked 过滤 SUPERSEDED

**价值**:文档级术语锁生命周期完整,**M2.7 可读 `{source: target}` 做约束。**

---

### M2.7 · 术语违规后校验(Task #42)✅

- [x] `services/glossary_enforcement.py`:纯函数 `detect_violations(source, target, locked_glossary) -> list[GlossaryViolation]`
- [x] 词边界匹配(`(?<![A-Za-z0-9])...(?![A-Za-z0-9])`)防 "Agent" 命中 "agentic" 的误报
- [x] CJK target 跳边界、走子串 count
- [x] Severity 区分:hard(完全缺失)vs partial(出现次数 < 源中次数)
- [x] 配套 `detect_non_translatable_leaks(blocks)`:检查 translate_none block 的 target 是否混入 CJK(spec §5.1 KPI 3 硬指标)
- [x] `tests/test_glossary_enforcement.py` 16 测试:空输入 / 源术语不存在跳过 / 完全遵守 / hard/partial 违规 / 词边界 / case-insensitive 源匹配 / 多术语独立 / 空条目过滤 / CJK 检测 3 种 / leak detector 3 种

**价值**:译文质量的第二道防线,**独立于 worker** 可运行,适合作为 review 阶段 gate 或事件发射触发器。未来任何 worker 替换都不破坏这层。

---

### M2.7 worker 接入 · post-validation 集成(Task #43)✅

- [x] `services/translation.py` 加 `_emit_glossary_violations` 助手:execute_packet 调完 worker 后,从 `bundle.context_packet.document_id` 取 GlossaryService.get_locked_terms,聚合 packet 内 source/target 文本调 detect_violations,每条违规发 `GLOSSARY_VIOLATION` 事件
- [x] `try/except` 包裹:任何 post-validation 异常都不中断翻译主流程("report, don't crash")
- [x] payload 携带 source_term / expected_target / severity_hint / source_match_count / target_match_count / document_id / translation_run_id
- [x] `tests/test_translation_glossary_postvalidation.py` 5 测试:无锁定术语零事件 / 译文遵守零事件 / 违规发事件含完整 payload / 跨文档隔离 / 多术语独立判定

**当前接入**:**Post-validation 即时上线** —— GlossaryService 的锁定术语会在每次 packet 翻译完后自动校验,违规事件入 `events` 表,可通过现有 SSE / 审阅 UI / 遥测消费。

**未做**:**Prompt 注入(M2.7b)** —— 在 LLM 翻译之前把 `locked_glossary` 写入 system prompt 作为权威映射。这需要改 14 个 prompt profile + ContextPacket schema + 多个 worker,影响面大,留作下一独立增量。当前 post-validation 等价于"事后纠错信号";prompt 注入则是"事前预防"。两者互补。

---

### M2.7b · Prompt 注入 — 译前防错(Task #44)✅

- [x] 发现现有 `ContextPacket.relevant_terms: list[RelevantTerm]` 字段已被 `workers/translator._sorted_term_lines` 渲染为 `- {source} => {target} ({lock_level})` 写入 system prompt。**零 schema / prompt profile 变更**
- [x] `TranslationService._inject_locked_glossary`:在 `execute_packet` 调 worker 前,从 `GlossaryService.list_document_entries` 取所有 ACTIVE 条目(SUGGESTED + PREFERRED + LOCKED),转 `RelevantTerm`,按 source 大小写不敏感 dedup 后合并到 `compiled_context_packet.relevant_terms`
- [x] **冲突时既有项胜出**:context compiler 已经为某 term 选了 chapter-scope target,document-scope 不覆盖(章节决策更靠近上下文)
- [x] **空 target 的 SUGGESTED 不下发**(`upsert_candidates` 产生的占位行不污染 prompt)
- [x] try/except 包裹:注入失败不阻塞翻译
- [x] `tests/test_translation_glossary_injection.py` 6 测试:空 glossary 不变 / locked 合并 / 既有项胜 / 空 target 跳过 / prompt 字符串实际包含术语 / locked 排在 suggested 前

### M2.9 · 金标回归集 5 → 15(Task #45)✅

新增 10 个 PDF fixture,每个对应一个 M1+M2 已有的失败模式或保护契约:

| Fixture | 测试断言 |
|---------|---------|
| `make_three_column_newsletter` | `_page_has_multi_column_signature` 在 3 栏页面也触发 |
| `make_figure_with_caption` | "Figure 1.1" 字符串在 recovery 后存活,figure-caption 配对契约 |
| `make_equation_block_book` | 若 block_type 被分类为 equation,**必须** translatability=translate_none(无泄漏) |
| `make_inline_url_paragraph` | URL/DOI 字面字符串在抽取后逐字保留 |
| `make_acronym_definition_paper` | terminology miner 通过 `Foo Bar (FB)` 模式捕获被定义的术语 + definition_boost 标记触发 |
| `make_repeated_term_doc` | terminology miner 纯频率路径召回 `attention mechanism` |
| `make_mixed_clean_and_corrupted` | 干净页通过 sanity gate(per-page 独立判断契约) |
| `make_cross_page_paragraph` | 跨页段落两半在 recovery 后均保留(无内容丢失) |
| `make_low_density_figure_page` | 仅图无字的页面**不**误触 sanity gate(false-positive 守护) |
| `make_recurring_header_footer_book` | 反复出现的 running head 不会作为 translate_all body 大量泄漏 |
| `make_numbered_section_paper` | 学术编号节标题页,每节正文均无丢失 |

加 `GoldenCoverageMatrixTests` 元测试:fixture maker 数 ≥ 15,任何回退即报警。

**到 20 的诚实 gap(5 个)**:扫描书 / 表结构恢复 / 公式 LaTeX 还原 / paper-subtype 分类器 / 多语种混排 — 这五个 fixture 即使建立,断言也只能写"内容存在",**真正的结构断言需要 M3/M4 实现的能力**。把它们提前建会变成形式主义而非保护。M3.x 各项落地后立即对应增加 fixture。

---

**价值**:**M2.7 完整双闭环上线**:
- **译前**(M2.7b prompt 注入):锁定术语作为权威映射进入 LLM system prompt,LLM 应主动遵守
- **译后**(M2.7 post-validation):违规时发 `GLOSSARY_VIOLATION` 事件,review/遥测可消费

两层互补:prompt 注入是预防(降低违规率),post-validation 是兜底(违规仍会被发现)。开 `BOOK_AGENT_PDF_SANITY_OCR_REEXTRACTION=1` + 在 review UI 用 `GlossaryService.lock_term` 锁定关键术语,M2 北极星(术语一致性 + sanity OCR 修复)即在生产生效。

---

## M3 模态专家(Tasks #46-#49)✅

| 模态 | 模块 | 测试数 | 实现层次 |
|------|------|--------|----------|
| References | `services/references_extractor.py` | 14 | 完整启发式(无外部依赖) |
| Tables | `services/table_extractor.py` | 15 | 启发式 TSR + Adapter Protocol |
| Equations | `services/equation_extractor.py` | 11 | Adapter Protocol + verbatim/image-anchor 兜底 |
| Images | `services/image_modality.py` | 11 | 契约执行器 |

### M3.1 References

- 检测多语种(英文 References / Bibliography / Works Cited / 参考文献 / 引用)
- 解析 APA/IEEE 风格条目 → `ReferenceEntry`(authors / year / title / venue / DOI / arXiv / urls / raw)
- `protect_references_section`:整个 section 强制 `translatability=translate_none`(含 heading 本身),终止于 Index/Appendix 等终结性 heading
- 防止"作者名/会议名被翻译"这一 spec §3.1 失败模式 3 的硬性失误

### M3.2 Tables

- 启发式 TSR:基于行内空白栅格一致性识别列分隔,产出 markdown 表
- 置信度计算与最低阈值 0.6,低置信不输出表结构(避免半成品)
- `enhance_block_for_table`:无论是否成功提结构,**block_type=table 一律 translatability=translate_none**(半翻译表比原文还差)
- `TableExtractorAdapter` Protocol 留口给将来的 TATR/docling 集成

### M3.3 Equations

- `EquationLatexAdapter` Protocol(默认 NoOp 不识别)
- `enhance_block_for_equation`:三态 render mode
  - `latex` — adapter 成功还原
  - `image_anchor` — 失败但有图像资源
  - `verbatim_text` — 失败且无图像(monospace 显示原文)
- 任何情况下 `translatability=translate_none`,异常抛出仍然安全降级到 verbatim
- spec §3.1 失败模式 3 中"公式当散文译"被严格阻断

### M3.4 Images / Figures

- `enhance_block_for_image`:image/figure block 强制 translate_none + 把 `image_alt` 升格为 canonical 显示文本(空文本时填占位)
- `enhance_caption_block`:caption 强制保持 translate_all(防止上游误判把 caption 锁掉,导致中文产物里没有图注)
- `enhance_document_image_modality`:文档级 idempotent pass + 遥测摘要(images_protected / captions_re_enabled / alt_text_filled)

### 共享设计决策

1. **Adapter Protocol 模式**(M3.2/M3.3 沿用 M2.3a/M2.3b):每个模态都给真正需要外部权重的实现留接口,默认走启发式 / NoOp,生产风险为零
2. **失败时永远向"安全 + 不可译"降级**:启发式无法恢复结构 → 至少不让模型乱翻 → translate_none 是最终防线
3. **纯函数 + 文档级编排器**:每个 modality module 暴露 block-level 函数 + (可选) doc-level pass,方便组合或者 ParseService 末尾按需启用
4. **零 schema 改动**:全部信息在 `block.metadata` 里(`equation_latex` / `equation_render_mode` / `table_markdown` / `table_confidence` / `image_canonical_alt`),export 层按这些键渲染即可

### M3 仍待完成(诚实记录)

- **未集成实际 ML 实现**:TATR(表)、pix2tex/texify(公式)、GROBID 完整版(参考文献)。Adapter Protocol 已就位,落地时只换实现不动调用方
- **未接入 ParseService 末尾**:四个模态的 doc-level pass 还没 wire 到 `ParseService.parse` 的尾部。引入需要决定执行顺序(建议:references → equations → tables → images)和遥测埋点
- **未补金标 fixture**:M2.9 留的 5 个 gap 中的 references/tables/equations/images 现在有能力支持更强断言,可以补到金标里
- **export 层未读新键**:`equation_render_mode` / `table_markdown` 等 metadata 键还没被 merged/bilingual 渲染消费,目前是"模态识别就绪,渲染待接通"

---

## TATR 集成(M3.2 升级路径)

### TATR-a · Adapter shell + protocol(Task #50)✅

- [x] 新协议 `PageImageTableExtractor` —— 接受 page+bbox+pre-extracted text blocks,返回多张 `TableStructure`(与 M3.2 契合)
- [x] `services/tatr_extractor.py`:
  - `TatrTableExtractor` 类,`_run_tatr_inference` 钩子留给 TATR-b 真模型
  - `_ensure_models_loaded` 用 `importlib.util.find_spec` 探 torch/transformers/PIL,**缺依赖即优雅返空**(deps_missing 写进 metrics)
  - 成本护栏 `max_tables_per_doc=50`,**累计跨调用**生效
  - 异常 → 返空 + `error` 写 metrics
  - `_cells_to_grid` / `tatr_table_to_markdown` / `map_cell_text`(bbox-overlap 阈值 0.4)三个纯函数辅助
- [x] `NoOpPageImageTableExtractor` 默认实现
- [x] `tests/test_tatr_extractor.py` 16 测试:bbox overlap helper 3 + map_cell_text 4 + markdown 渲染 2 + NoOp 1 + Fake 注入 6(deps-missing / 主路径 / 成本护栏 / 跨调用累积 / 全空丢弃 / 异常)

**价值**:`torch + transformers + PIL` 还没装,但**协议、bbox 映射、成本护栏、降级路径**全部可被测试和审计。TATR-b 落地时只需要:
1. 在 pyproject.toml 加 `torch`, `transformers`, `pillow` 可选依赖
2. 实现 `_ensure_models_loaded` 的真模型加载(microsoft/table-transformer-detection + structure-recognition)
3. 实现 `_run_tatr_inference`:页面光栅化(PyMuPDF `page.get_pixmap(dpi=200)`)→ Image → detection → 每个 table crop → structure recognition → 转 PDF 坐标返回
4. 已有的 16 个测试 + 现有的 16 个 fake-backend 测试都不需要改

### TATR-b · 真模型落地(待做)

需要的运行时:
- `torch>=2.0`(~800 MB Linux x86_64,~300 MB Apple Silicon)
- `transformers>=4.30`(~50 MB)
- `pillow`(已有大概率)
- `microsoft/table-transformer-detection` 模型权重(~110 MB)
- `microsoft/table-transformer-structure-recognition` 模型权重(~110 MB)

实现要点:
- 模型懒加载,首次调用才下载/加载
- 页面 DPI 200 渲染足够大多数表;低分辨率用 300
- detection 模型置信度阈值默认 0.7,可调
- structure recognition 输出归一化坐标,需乘 image_dim 再除 dpi/72 反算 PDF 点空间
- GPU 自动检测;CPU fallback 慢但可用

### TATR-c · 接入 ParseService(待做)

env flag `BOOK_AGENT_PDF_TATR_TABLE_RECOVERY=1`,默认关闭。开启后在 `ParseService.parse` 末尾对每个 `block_type=table` 的 chapter 调 `TatrTableExtractor.extract`,将返回的 `TableStructure.markdown` 写入 `block.metadata["table_markdown"]`,与 M3.2 heuristic 同 key 同消费方式。

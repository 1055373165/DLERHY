# PDF Status

Last Updated: 2026-03-15

## Purpose

这份文档是 PDF 支持路线的项目驾驶舱状态页。

使用规则：

- 每次 PDF 解析、QA、API contract 或验收边界发生变化时更新
- 任务拆解放到 `docs/pdf_backlog.md`
- 架构取舍和冻结决定放到 `docs/pdf_decisions.md`

## Current Stage

- 内部阶段：`P1-A complete, P1-B partial`
- 对外 contract：仍是 `p1_text_pdf_bootstrap`
- 当前放行范围：`pdf_text`、低风险和一部分 medium-risk 文本 PDF；短篇 academic paper 现在可进入 `academic_paper` medium-risk lane，复杂版式高风险仍默认拒绝，扫描 / OCR 仍未支持

一句话判断：

> 文本型 PDF 已能进入既有主链路，并把结构风险显式传播到 review / rerun；但还没有到“广泛真实出版 PDF 默认放行”的阶段。

## Completed

- 已完成 `PdfFileProfiler`，支持 `text_pdf / mixed_pdf / scanned_pdf / layout_risk`
- 已完成 PDF intake，`source_type` 可分流到 `pdf_text / pdf_scan`
- 已完成文本型 PDF 解析接入，主链路支持 EPUB + PDF 共用 IR
- 已完成 geometry-aware block recovery，保留 page / bbox / reading order / parse confidence
- 已完成简单阅读顺序恢复
- 已完成页眉页脚识别与剔除，且保留 provenance
- 已完成段落合并、跨页续接、连字符断词修复
- 已完成章节级 `parse_confidence / pdf_layout_risk / structure_flags / risk_level`
- 已完成 PDF 结构风险 review：`MISORDERING`、`STRUCTURE_POLLUTION`
- 已完成 TOC / bookmark / heading 组合的章节恢复
- 已完成 TOC page-number reconciliation 第一版，可处理 printed page number 与 PDF page index 的常见 offset
- 已完成 Basic extractor 的 outline/bookmark 读取，避免只依赖 PyMuPDF
- 已完成 `toc_entry` 非翻译化，避免目录噪声进入 packet / export
- 已完成简单 footnote recovery：独立保存、same-page anchor linkage、orphan 标记
- 已完成 footnote recovery v2：支持页末脚注跨页续写、页顶脚注起始、贴词数字锚点和上一页 anchor 匹配
- 已完成页面家族分类第一版：`toc / frontmatter / appendix / references / index`
- 已完成复杂页面家族分类第二版：支持 headingless `references / index` 启始页，以及 `appendix` 连续页继承
- 已完成 chapter boundary hardening 第一版：frontmatter、appendix、references、index 不再默认混入正文 chapter
- 已完成 chapter metadata 中的 `pdf_section_family / pdf_page_family_counts`
- 已完成 TOC metadata 调试字段：`toc_page_number_printed / resolved / offset / resolution_source`
- 已完成 footnote 调试字段增强：`footnote_anchor_page` 现在回指 anchor 所在页，continuation 会打 `footnote_continuation_repaired`
- 已完成 page-level evidence artifacts 第一版：`pdf_page_evidence.pdf_pages / pdf_outline_entries`
- 已完成 review package / bilingual manifest 接入 chapter-sliced `pdf_page_evidence`
- 已增加 `scripts/run_pdf_smoke.py`，可对本机 PDF 产出 profile / bootstrap / parse smoke 报告
- 已增加 `src/book_agent/tools/pdf_smoke.py` 与 `scripts/run_pdf_smoke_corpus.py`，支持 manifest-driven corpus smoke 与 expectation check
- 已增强 `scripts/run_pdf_candidate_scan.py`，支持目录剪枝、候选评分、manifest-ready 推荐输出
- 已完成 short academic paper intake 第一刀：`NIPS-2017-attention-is-all-you-need-Paper.pdf` 现在会被判为 `layout_risk=medium`、`recovery_lane=academic_paper`，并可成功 bootstrap 到 `body + references`
- 已完成真实样本 family heuristic hardening：同一样本 forced-parse 现只保留正文 + 真 `References`，不再误切假 `Index/References`
- 已固化首个本机 smoke corpus manifest：`artifacts/pdf-smoke/corpus.local.json`
- 已把本机 smoke corpus 扩到 2 个真实样本：1 个高风险文本论文 + 1 个扫描型中文书
- 已把本机 smoke corpus 扩到 3 个真实样本，新增 1 个低风险 text PDF 放行样本：`AI Agents in Action`
- 已把本机 smoke corpus 扩到 5 个真实样本，新增第二本低风险长书样本：`LLMs in Production`
- 已修正 fail-safe 风险语义：`ocr_required=true` 的文档现在不会再出现 `layout_risk=low`
- 已修正 headingless special-section 假阳性边界：孤立 `index/references` 误报页不再吞掉后续正文 chapter
- 已完成 long-book chapter recovery 第一轮 hardening：在 fallback extractor 无可靠字号时，新增 `chapter intro page` 信号，能从 `This chapter covers` / intro-page 文本恢复真实章节
- 已完成 frontmatter preamble hardening：真实长书在首个 intro chapter 之前的 `contents/preface/about...` 现在会收敛为 `Front Matter`，不再伪装成 `Chapter 1`
- 已完成 isolated `content_signature=index` cleanup：孤立 index-like 页面现在默认只保留 `content_family` 证据，不再直接提升为 `page_family`
- 已完成 extractor provenance 暴露：`pdf_profile / pdf_page_evidence / smoke parse_summary` 现在都会记录 `extractor_kind`
- 已完成 basic-extractor fragment-only 风险语义第一版：长文本 PDF 若仅出现 `column_fragment` 信号，且无 `multi_column` 证据，会从 `high` 降为 `medium`
- 已完成 medium-risk section recovery 第一刀：`references -> appendix intro -> continuation` 现在可以在 headingless/embedded appendix 页面上切出独立 appendix chapter
- 已完成 inline special-section heading hardening：`INDEX 312 ...` / `Appendix A ...` 这类被合并进正文块的页也能恢复成真实 `page_family`
- 已完成 tail-body-after-special hardening：真实长书在 `appendix / index` 之后回到无 heading 的尾页时，会切出独立 `Back Matter`，避免继续吞进 special section
- 已完成 multi-appendix split 第一刀：同一 `appendix` family 内出现新的 inline appendix heading 时，会切出新的 appendix chapter
- 已完成 `Back Matter` formal family 第一刀：真实长书里的 `index -> tail body` 现在会正式提升成 `page_family=backmatter` 和 `pdf_section_family=backmatter`
- 已完成 `Back Matter policy v2`：`backmatter` 默认 `translatable=false`，并按 source-only/export-preserve 策略处理
- 已完成 `bilingual_html` block render contract 升级：不再绕过 block-level render mode，`source-only` / protected artifact / reference-preserve 与 merged/review 出口保持一致
- 已完成 `pdf_preserve_evidence`：review package / bilingual manifest 现在会显式导出 special-section page contract、render mode、notice 和 source-only 证据
- 已完成 `pdf_page_debug_evidence`：review package 现在会导出 chapter-sliced page/block debug 证据，直接标出 special-section / preserve-contract / layout-suspect 页及其 block render mode
- 已完成 medium-risk appendix finer-grained recovery 第一刀：`Building AI Coding Agents...` 现在已从单一 `Appendix` 提升到 `Appendix A Complete Tool Catalog + Appendix K Full System Prompt Templates`
- 已完成 appendix-intro false-positive guard：长正文中仅因晚出现的 `later in this appendix` 一类 cue，不再误生成新的 appendix 子章节标题
- 已完成 special-section subheading hardening 第一刀：appendix continuation 页上的顶层子标题（如 `K.3` / `K.4`）现在可切出新的 appendix 子章节，而 `K.2.5` 这类嵌套小节仍保持保守忽略
- 已完成 nested appendix subheading evidence 第一刀：`K.2.5` 一类嵌套小节现在会进入 `pdf_page_evidence / pdf_page_debug_evidence`，但不会直接切进 chapter tree
- 已完成 continuation-page density guard：top-level appendix subheading 只有在 continuation 页具有足够 substantive blocks 时才允许切章，避免 `AI Agents in Action` 一类真实样本被 `A.2 / B.4 / B.6` 过切
- 已完成 backmatter cue hardening 第一刀：`appendix -> tail body` 现在只有在尾页出现明确 `Upcoming Titles / About the Author / marketing-signals` 一类 cue 时，才会正式升级成 `page_family=backmatter`
- 已完成 `backmatter_cue` evidence contract：`pdf_page_evidence / pdf_page_debug_evidence / pdf_preserve_evidence` 现在都会显式暴露 `backmatter_cue / backmatter_cue_source`
- 已完成 footnote relocater v3 第一刀：同页页底和跨页续页页顶的小字号 markerless 段落，现在可稳定并回前一个 footnote block
- 已完成 footnote relocation evidence：`footnote_segment_count / footnote_segment_roles / footnote_relocation_modes` 已进入 block metadata，page evidence 也会显式暴露 `relocated_footnote_count / max_footnote_segment_count`
- 已完成 long-book chapter-intro title cleanup 第一刀：`LLMs in Production` 中最明显的 PDF escape / spaced-word / sentence-tail 噪声已从 chapter titles 中压下去
- 已完成 footnote 结构 review：`FOOTNOTE_RECOVERY_REQUIRED`
- 已完成 API / summary / frontend 对 `pdf_profile` 和章节结构风险的暴露
- 已补齐 PDF 回归测试，覆盖 low-risk bootstrap、high-risk reject、medium-risk review、TOC 切章、bookmark 切章、footnote linkage/orphan、cross-page footnote continuation、next-page footnote start、page family 切章、outline + appendix 边界

## Current Limits

- 仍然只适合单栏或近单栏文本 PDF
- medium-risk PDF 虽然可 bootstrap，但 review 仍会打结构风险并建议 `REPARSE_CHAPTER`
- TOC reconciliation 目前仍是启发式的，依赖 title match / footer label，复杂 page-label 体系还不稳
- 页面家族分类现在已支持一部分 content-signature 推断，但复杂 references/index 版式仍不够稳
- footnote v2 仍然偏保守：复杂多段脚注、跨多页脚注、非数字复杂符号锚点还不稳
- footnote relocater v3 当前只覆盖“页底脚注区 + 续页页顶脚注区”的 markerless body 段落；交错双脚注、页中插入式注释和更复杂符号体系仍未覆盖
- document metadata / API 里的 page-level evidence 仍只保留轻量 page summary；更细的 block-level debug 现在只在 review package 中提供，不回写主 metadata
- nested appendix subheading 当前已进入 page/review evidence，但还只是观测信号，不是公共 section-tree contract
- `NIPS-2017-attention-is-all-you-need-Paper.pdf` 已接入 medium-risk academic-paper lane，但当前仍主要恢复到 `body + references`；section-level heading tree 和更稳的双栏阅读顺序仍需继续 harden
- 当前真实样本已消掉首批 `content_signature` 假阳性，但 academic-paper lane 仍需要第二篇真实英文论文确认不是只对单篇样本收敛
- 当前 smoke corpus 已有 5 个真实样本，已覆盖 low-risk 长书放行、medium-risk 放行、high-risk 文本拒绝、OCR 拒绝四条路径
- `AI Agents in Action` 现在已从“单一 Chapter 1”提升到 `Front Matter + 11 个真实章节 + Appendix A + Appendix B + Index + Back Matter`
- `AI Agents in Action` 中 page `123` 现已保持 `body`，page `333-339` 已恢复成真实 `Index`，page `340-346` 已正式标成 `Back Matter`
- `Back Matter` 目前只在已验证的 `index -> tail body` 路径上升级成正式 family + source-only；更广的 backmatter cue 还未推广
- `appendix -> backmatter` 当前只覆盖显式 heading-title 或强 marketing-signals 的尾页；普通 appendix 续页不会仅因靠近文末而自动升级
- `pdf_preserve_evidence` 当前已能让 operator 直接看到 special-section 的 preserve contract，但还没细到 block 原文级 forensics
- `Building AI Coding Agents for the Terminal...` 现在会被判为 `layout_risk=medium` 并成功 bootstrap，且已从 `Chapter 1 + References + Appendix` 进一步提升到 `Chapter 1 + References + Appendix A + Appendix K + Appendix K.3 + Appendix K.4`
- `LLMs in Production` 现在会被判为 `layout_risk=low` 并成功 bootstrap，结构已恢复到 `Front Matter + 12 body chapters + Appendix A/B/C + Index + Back Matter`
- `LLMs in Production` 的 title cleanup 已明显改善，但仍残留 `Adeep / Dataislikegarbage / canyougo` 这类更深层的 extractor 断词噪声
- 常用目录候选扫描已正式工具化；截至当前，本机可直接进入 pass-path 的真实长书仍只有 `AI Agents in Action` 和 `LLMs in Production`
- 扫描 PDF、OCR、academic paper 的 robust 双栏阅读顺序、复杂图表/公式保护仍未开始

## Open Blockers

- appendix / index / references 的复杂页面仍缺更强的模式识别，当前仍以保守 heuristics 为主
- footnote relocater 已有 v3 第一刀，但复杂交错脚注、多锚点同页混排和非数字符号体系仍缺更强归位
- 当前对外 contract 仍停留在 `p1_text_pdf_bootstrap`，尚未升级为更明确的 robust recovery 阶段
- `academic_paper` lane 现已可 bootstrap，但标题断词、section heading recovery 和双栏真实阅读顺序仍只到第一刀
- `AI Agents in Action` 的 `Appendix A / Appendix B` 已能拆开，但 appendix 内更细 section/title recovery 仍未完成
- `Back Matter` 现在已覆盖 `index -> tail body` 与“带显式 cue 的 `appendix -> tail body`”两条路径，但尚未推广到更弱、更模糊的尾部资料页信号
- medium-risk 真实样本已能切出首批 appendix 子章节与 continuation-page 顶层 subheading，但更细的 appendix 内 section tree 仍未完成，当前仍不覆盖 `K.2.5` 这类嵌套小节
- 顶层 appendix continuation split 现在依赖较 dense 的 continuation 页；短步骤页或 block 很少的 appendix 小节仍会保守留在原 appendix 章节中
- 目前长书 text-PDF 的主结构已在 2 个真实低风险样本上稳定，但仍需要第三本或更异质的真实长书，确认 chapter recovery / tail-section policy 不是双样本过拟合

## Next

1. 决定 nested appendix evidence 何时升级成正式 section tree，而不是一直停留在观测态
2. 继续 harden chapter-intro title cleanup，专门处理 `Adeep / Dataislikegarbage / canyougo` 这类更深层断词噪声
3. 评估 `Back Matter` 是否要接受更弱的 cue，还是继续停留在 explicit-cue policy
4. 继续 harden footnote relocater，专门处理交错脚注与更复杂符号体系
5. 若新增本地样本，再继续扩真实长书 smoke，确认当前 contract 不是双样本过拟合

## Verification Baseline

- `uv run pytest tests/test_pdf_support.py -q`
- `uv run pytest tests/test_pdf_support.py tests/test_pdf_smoke_tools.py -q`
- `uv run pytest tests/test_api_workflow.py tests/test_bootstrap_pipeline.py tests/test_cli.py tests/test_frontend_entry.py tests/test_epub_parser.py -q`
- `uv run ruff check src/book_agent/domain/structure/pdf.py src/book_agent/domain/block_rules.py src/book_agent/services/review.py src/book_agent/services/export.py tests/test_pdf_support.py`

## Source Pointers

- 详细实施计划：`docs/pdf-support-implementation-plan.md`
- 当前代码主入口：`src/book_agent/domain/structure/pdf.py`
- 结构风险接入：`src/book_agent/services/bootstrap.py`、`src/book_agent/services/review.py`
- 回归入口：`tests/test_pdf_support.py`
- 真实样本 smoke 报告：`artifacts/pdf-smoke/attention-is-all-you-need.json`
- smoke corpus manifest：`artifacts/pdf-smoke/corpus.local.json`
- smoke corpus summary：`artifacts/pdf-smoke/corpus-local.summary.json`
- 第二个真实样本报告：`artifacts/pdf-smoke/corpus-local/jim-simons-book-scan.json`
- 候选扫描报告：`artifacts/pdf-smoke/candidate-scan.local.json`
- 真实放行样本报告：`artifacts/pdf-smoke/ai-agents-in-action.json`

# TODOS

Last Updated: 2026-03-18

## Purpose

这份文档是 **PDF 改造主线的接力开发手册**。

目标不是记录所有历史，而是保证：

- 重新打开一个新会话时，不需要重新考古就能继续推进
- 明确知道当前做到哪里、哪些已冻结、哪些还没做
- 每完成一刀后，记得同步更新对应驾驶舱文档，避免状态丢失

## Read First

如果是新会话接手，先读这 4 份：

1. [TODOS.md](/Users/smy/project/book-agent/TODOS.md)
2. [pdf_status.md](/Users/smy/project/book-agent/docs/pdf_status.md)
3. [pdf_backlog.md](/Users/smy/project/book-agent/docs/pdf_backlog.md)
4. [pdf_decisions.md](/Users/smy/project/book-agent/docs/pdf_decisions.md)

如果当前目标不是 PDF parser，而是“英译中质量重构 / packet memory / rerun policy”，先补读：

5. [translation-quality-refactor-cockpit.md](/Users/smy/project/book-agent/docs/translation-quality-refactor-cockpit.md)

## One-Screen Summary

- 内部阶段：`P1-A complete, P1-B partial`
- 对外 contract：仍是 `p1_text_pdf_bootstrap`
- 当前主结论：
  - 低风险文本 PDF 已能进入主链路
  - 一部分 `medium-risk` 文本 PDF 已能 bootstrap / review / export
  - 短篇 academic paper 已可通过 `academic_paper` lane 正式导出
  - 单栏 research paper 已能恢复“真实标题 + References”
  - 扫描 / OCR 仍未支持
  - 复杂双栏 / figure/equation-heavy 页面仍未达到 robust support

一句话判断：

> PDF 主线已经从“只能 smoke”推进到“真实样本可运行、可验证、可迭代”，但距离“广泛真实出版 PDF 默认放行”还有一段距离。

## Hard Constraints

继续开发时默认遵守这些边界：

- 不为 PDF 单独重造第二套翻译系统，继续复用统一 IR 与主链路
- 高风险复杂版式继续 fail-safe，不假装支持
- medium-risk 可以进入，但必须把结构风险显式传播到 review / rerun
- 先修结构恢复，再谈质量锦上添花
- 优先做 packet / chapter / smoke corpus 级验证；不要轻易上整本 rerun
- 每做完一轮 PDF slice，都要同步：
  - [pdf_status.md](/Users/smy/project/book-agent/docs/pdf_status.md)
  - [pdf_backlog.md](/Users/smy/project/book-agent/docs/pdf_backlog.md)
  - [pdf_decisions.md](/Users/smy/project/book-agent/docs/pdf_decisions.md)
  - [TODOS.md](/Users/smy/project/book-agent/TODOS.md)

## What Is Already Working

### Intake And Parse

- `PdfFileProfiler` 已支持：
  - `text_pdf / mixed_pdf / scanned_pdf`
  - `layout_risk`
  - `extractor_kind`
- 文本型 PDF 已接进统一 IR：
  - `document -> chapter -> block -> sentence -> packet -> review -> export`
- 已有：
  - geometry-aware 提取
  - reading order recovery
  - header/footer 剔除
  - paragraph merge
  - cross-page repair
  - hyphen fix

### Structure Recovery

- 章节恢复已支持：
  - outline / bookmark
  - TOC heuristic
  - heading heuristic
  - chapter-intro cue
- 页面家族已支持：
  - `toc`
  - `frontmatter`
  - `appendix`
  - `references`
  - `index`
  - `backmatter`
- 长书恢复已验证：
  - `Front Matter + body chapters + appendix/index/back matter`
- appendix split 已支持：
  - `Appendix A / B`
  - 顶层 continuation subheading
- nested appendix subheading 当前只进 evidence，不进正式 section tree

### Risk / Review / Export

- 结构风险已接入 review：
  - `MISORDERING`
  - `STRUCTURE_POLLUTION`
  - `FOOTNOTE_RECOVERY_REQUIRED`
- `backmatter` 已正式进入 source-only / preserve contract
- review package / manifest 已导出：
  - `pdf_page_evidence`
  - `pdf_page_debug_evidence`
  - `pdf_preserve_evidence`
- `bilingual_html` 已使用 block-level render contract，不再绕开 preserve policy

### Academic Paper Lane

- `Attention Is All You Need` 已从“高风险拒绝”推进到：
  - `layout_risk=medium`
  - `recovery_lane=academic_paper`
  - 可正式导出 merged HTML
- heading recovery 已能恢复：
  - `Abstract`
  - `1 Introduction`
  - `3.1 Encoder and Decoder Stacks`
  - `3.4 Embeddings and Softmax`
  - `6 Results`
  - `7 Conclusion`
- 第二篇英文论文 `Forming Effective Human-AI Teams...` 已验证：
  - single-column 低风险路径
  - 真实标题恢复
  - `References` 恢复
  - live run 成功导出 merged HTML

## Verified Real Samples

当前本机 smoke corpus 是 `6/6 passed`，见 [corpus-local.summary.json](/Users/smy/project/book-agent/artifacts/pdf-smoke/corpus-local.summary.json)。

已固化样本：

1. `ai-agents-in-action`
   - 报告：[ai-agents-in-action.json](/Users/smy/project/book-agent/artifacts/pdf-smoke/corpus-local/ai-agents-in-action.json)
   - 价值：低风险长书主样本，覆盖 frontmatter / appendix / index / backmatter

2. `attention-is-all-you-need`
   - 报告：[attention-is-all-you-need.json](/Users/smy/project/book-agent/artifacts/pdf-smoke/corpus-local/attention-is-all-you-need.json)
   - 价值：medium-risk academic paper 主样本

3. `jim-simons-book-scan`
   - 报告：[jim-simons-book-scan.json](/Users/smy/project/book-agent/artifacts/pdf-smoke/corpus-local/jim-simons-book-scan.json)
   - 价值：OCR/scanned fail-safe 样本

4. `building-ai-coding-agents`
   - 报告：[building-ai-coding-agents.json](/Users/smy/project/book-agent/artifacts/pdf-smoke/corpus-local/building-ai-coding-agents.json)
   - 价值：medium-risk appendix 恢复样本

5. `llms-in-production`
   - 报告：[llms-in-production.json](/Users/smy/project/book-agent/artifacts/pdf-smoke/corpus-local/llms-in-production.json)
   - 价值：第二本低风险长书样本，验证 chapter tree 不是单样本过拟合

6. `forming-effective-human-ai-teams`
   - 报告：[forming-effective-human-ai-teams.json](/Users/smy/project/book-agent/artifacts/pdf-smoke/corpus-local/forming-effective-human-ai-teams.json)
   - 价值：single-column research paper 样本

## Verified Live Exports

最重要的真实 PDF live 产物：

- `Attention Is All You Need v2`
  - 报告：[report.json](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-attention-paper-v2/report.json)
  - merged HTML：[merged-document.html](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-attention-paper-v2/exports/b111498d-25cd-5ad7-8141-5cfbf0065481/merged-document.html)
  - manifest：[merged-document.manifest.json](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-attention-paper-v2/exports/b111498d-25cd-5ad7-8141-5cfbf0065481/merged-document.manifest.json)

- `Forming Effective Human-AI Teams v3`
  - 报告：[report.json](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v3/report.json)
  - merged HTML：[merged-document.html](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v3/exports/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.html)
  - manifest：[merged-document.manifest.json](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v3/exports/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.manifest.json)

## Current Known Limits

这些不是 bug 列表，而是当前仍明确存在的能力边界：

- 仍不支持扫描/OCR 主路径
- medium-risk PDF 虽可放行，但只是“可进链路”，不是“默认稳定交付”
- academic paper 的双栏 reading order 仍未 robust
- table / figure / equation-heavy 页面仍需更强 around-order hardening
- appendix 内更细 section tree 仍未正式进入公共 contract
- `LLMs in Production` 仍残留更深层 extractor 断词噪声：
  - `Adeep`
  - `Dataislikegarbage`
  - `canyougo`
- `backmatter` 当前仍偏 explicit-cue policy

## Immediate Next Work

如果开新会话、需要直接继续开发，默认按这个顺序推进：

1. 第三篇真实英文论文或更异质 paper 扩样
   - 目的：确认 current paper-title / references / academic lane 不是双样本过拟合
   - 验证：先进 smoke corpus，再决定要不要 live run

2. `nested appendix section-tree upgrade`
   - 当前：`K.2.5` 只进 evidence，不进 section tree
   - 目标：决定何时正式升级成 chapter/subchapter contract
   - 风险：过切 appendix tree，误伤真实长书

3. `chapter-intro title cleanup v2`
   - 目标：压 `Adeep / Dataislikegarbage / canyougo`
   - 优先样本：`LLMs in Production`

4. `backmatter cue hardening v2`
   - 目标：决定是否接受更弱 cue，还是继续停留 explicit-cue policy
   - 风险：误把 appendix 续页或正文尾页升成 `backmatter`

5. `medium-risk PDF finer-grained policy`
   - 目标：从“可进但高风险”升级到更可操作的放行策略
   - 方向：明确哪些 medium-risk 可以走 formal export、哪些必须先 reparse/review

## If Starting A New Session

建议按这个顺序恢复上下文：

1. 读 [TODOS.md](/Users/smy/project/book-agent/TODOS.md)
2. 读 [pdf_status.md](/Users/smy/project/book-agent/docs/pdf_status.md)
3. 看 [corpus-local.summary.json](/Users/smy/project/book-agent/artifacts/pdf-smoke/corpus-local.summary.json)
4. 看本轮目标对应的真实样本报告
5. 打开主代码入口：
   - [pdf.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py)
   - [review.py](/Users/smy/project/book-agent/src/book_agent/services/review.py)
   - [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
6. 先做小回归，不要直接上 live run

## Main Source Files

高频入口：

- parser 主体：
  - [pdf.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py)
- bootstrap 分流：
  - [bootstrap.py](/Users/smy/project/book-agent/src/book_agent/services/bootstrap.py)
- review：
  - [review.py](/Users/smy/project/book-agent/src/book_agent/services/review.py)
- export：
  - [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
- smoke 工具：
  - [pdf_smoke.py](/Users/smy/project/book-agent/src/book_agent/tools/pdf_smoke.py)
  - [run_pdf_smoke.py](/Users/smy/project/book-agent/scripts/run_pdf_smoke.py)
  - [run_pdf_smoke_corpus.py](/Users/smy/project/book-agent/scripts/run_pdf_smoke_corpus.py)
- 候选扫描：
  - [run_pdf_candidate_scan.py](/Users/smy/project/book-agent/scripts/run_pdf_candidate_scan.py)
- 主要回归：
  - [test_pdf_support.py](/Users/smy/project/book-agent/tests/test_pdf_support.py)
  - [test_pdf_smoke_tools.py](/Users/smy/project/book-agent/tests/test_pdf_smoke_tools.py)

## Safe Verification Loop

默认验证顺序：

1. 先跑定向单测  
   `uv run pytest tests/test_pdf_support.py -q`

2. 如果改了 smoke 工具，再跑  
   `uv run pytest tests/test_pdf_smoke_tools.py -q`

3. 如果改到 API / export / review，再补  
   `uv run pytest tests/test_api_workflow.py tests/test_bootstrap_pipeline.py tests/test_cli.py tests/test_frontend_entry.py tests/test_epub_parser.py -q`

4. 如果改 parser 主体，再跑 smoke corpus  
   `uv run python scripts/run_pdf_smoke_corpus.py --manifest-path artifacts/pdf-smoke/corpus.local.json --output-dir artifacts/pdf-smoke/corpus-local --report-path artifacts/pdf-smoke/corpus-local.summary.json`

## What Not To Forget After Each Slice

每做完一轮 PDF 改造，至少同步 4 件事：

1. 更新 [pdf_status.md](/Users/smy/project/book-agent/docs/pdf_status.md)
2. 更新 [pdf_backlog.md](/Users/smy/project/book-agent/docs/pdf_backlog.md)
3. 更新 [pdf_decisions.md](/Users/smy/project/book-agent/docs/pdf_decisions.md)（如果有新的取舍冻结）
4. 更新 [TODOS.md](/Users/smy/project/book-agent/TODOS.md)

## Resume Hint

如果新会话里不知道“从哪一刀开始”：

- 想继续扩样：从“第三篇真实英文论文扩样”开始
- 想继续长书结构：从 `nested appendix section-tree upgrade` 开始
- 想继续文本质量：从 `chapter-intro title cleanup v2` 开始
- 想继续边界策略：从 `backmatter cue hardening v2` 开始

默认推荐第一刀：

> 先扩第三篇真实英文论文或更异质 paper 样本，再决定 academic-paper 路线下一步是继续 reading-order hardening，还是先转去 appendix/tree 这条长书路线。

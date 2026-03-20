# TODOS

Last Updated: 2026-03-20

## Purpose

这份文档是 **PDF 改造主线的接力开发手册**。

目标不是记录所有历史，而是保证：

- 重新打开一个新会话时，不需要重新考古就能继续推进
- 明确知道当前做到哪里、哪些已冻结、哪些还没做
- 每完成一刀后，记得同步更新对应驾驶舱文档，避免状态丢失

## Read First

如果是新会话接手，先读这 4 份：

1. [TODOS.md](/Users/smy/projects/mygithub/DLEHY/TODOS.md)
2. [pdf_status.md](/Users/smy/projects/mygithub/DLEHY/docs/pdf_status.md)
3. [pdf_backlog.md](/Users/smy/projects/mygithub/DLEHY/docs/pdf_backlog.md)
4. [pdf_decisions.md](/Users/smy/projects/mygithub/DLEHY/docs/pdf_decisions.md)

如果当前目标不是 PDF parser，而是“英译中质量重构 / packet memory / rerun policy”，先补读：

5. [translation-quality-refactor-cockpit.md](/Users/smy/projects/mygithub/DLEHY/docs/translation-quality-refactor-cockpit.md)

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

## Current Handoff Snapshot

这部分是给下一个程序员看的“接力起点”。如果你只读一段，优先读这里。

- 刚完成的最新一刀：
  - 针对真实英文书分页区域，修复了 4 类结构错误：`inline heading` 丢失、`contextual image legend` 被并进正文、跨页 bullet 被误切且前半段被误判成 code、页内图片排序错误
- 本轮主要修改入口：
  - `/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/structure/pdf.py`
  - `/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/export.py`
  - `/Users/smy/projects/mygithub/DLEHY/tests/test_pdf_support.py`
- 本轮 parser/export 关键变化：
  - 页内 block 恢复不再默认“先全部文本、后全部图片”，而是对书籍场景启用 `text/image` 交错排序
  - 新增 inline book heading promotion，`Tokenization: Breaking Text into Pieces` 这类标题会被提升成真实 `heading`
  - 新增 contextual image legend promotion，`This sentence contains 27 tokens` 这类短图例会独立保留并作为 caption 候选
  - 扩展 image-caption target 匹配，允许 contextual legend 绑定图片
  - export 侧新增 bullet paragraph guard，避免 `• GPT (Byte Pair Encoding): ...` 被误提升成 code artifact

### Latest Real-Book Validation

- 最新真实验收样本：
  - 源文件：`/Users/smy/Downloads/Patterns of Application Development Using AI (Obie Fernandez) (z-library.sk, 1lib.sk, z-lib.sk).pdf`
  - 验收目标：首个正文主章节 `Introduction`
- 最新 rerun 产物：
  - 数据库：`/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/patterns-ai-ch1-rerun-v3/run.sqlite`
  - 章节 HTML：`/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/patterns-ai-ch1-rerun-v3/exports/0498168f-2c5a-53c1-8af9-cb5a5735f7af/bilingual-82e5eeb7-5932-5e05-ac0c-bc33246c1297.html`
  - review package：`/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/patterns-ai-ch1-rerun-v3/exports/0498168f-2c5a-53c1-8af9-cb5a5735f7af/review-package-82e5eeb7-5932-5e05-ac0c-bc33246c1297.json`
  - manifest：`/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/patterns-ai-ch1-rerun-v3/exports/0498168f-2c5a-53c1-8af9-cb5a5735f7af/bilingual-82e5eeb7-5932-5e05-ac0c-bc33246c1297.manifest.json`
  - run report：`/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/patterns-ai-ch1-rerun-v3/parallel-report.json`
- 当前结果：
  - `135 / 135` packets 已翻译完成
  - `blocking_issue_count = 0`
  - 仅剩 `1` 个非阻塞 review issue：`IMAGE_CAPTION_RECOVERY_REQUIRED`
- 已确认收敛的具体问题：
  - `Tokenization: Breaking Text into Pieces` 已被正确恢复为标题
  - `This sentence contains 27 tokens` 已作为图片锚点说明保留，不再混进正文
  - `• GPT (Byte Pair Encoding)...` 已跨页合并成完整 paragraph，不再被识别成代码块
- 当前最明显的剩余缺口：
  - 这一章仍有 `7` 张图片未稳定绑上 caption
  - review package 已给出未绑 caption 的页号：`22, 27, 31, 32, 47, 48, 49`
  - 这说明 contextual image legend / figure caption recovery 还只是第一刀，不应误判为“图片 caption 已普遍解决”

## Hard Constraints

继续开发时默认遵守这些边界：

- 不为 PDF 单独重造第二套翻译系统，继续复用统一 IR 与主链路
- 高风险复杂版式继续 fail-safe，不假装支持
- medium-risk 可以进入，但必须把结构风险显式传播到 review / rerun
- 先修结构恢复，再谈质量锦上添花
- 优先做 packet / chapter / smoke corpus 级验证；不要轻易上整本 rerun
- 每做完一轮 PDF slice，都要同步：
  - [pdf_status.md](/Users/smy/projects/mygithub/DLEHY/docs/pdf_status.md)
  - [pdf_backlog.md](/Users/smy/projects/mygithub/DLEHY/docs/pdf_backlog.md)
  - [pdf_decisions.md](/Users/smy/projects/mygithub/DLEHY/docs/pdf_decisions.md)
  - [TODOS.md](/Users/smy/projects/mygithub/DLEHY/TODOS.md)

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
- `image_anchor` 导出已修正为“图片在上、caption 在下”，不再把图注排到图片上方
- PDF 图片导出现在会刷新旧持久化 crop，并按更高目标像素尺寸重渲染，避免 merged HTML 继续复用历史低清图片
- recoverable table 已支持语义化 HTML `<table>` 导出，而不是一律降级成 monospace 文本块
- code continuity 已补第一刀：紧邻代码的 comment-only body 会提升为 code，`code -> inline image -> code` 的误切在 export 侧会桥接收敛

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

当前本机 smoke corpus 是 `6/6 passed`，见 [corpus-local.summary.json](/Users/smy/projects/mygithub/DLEHY/artifacts/pdf-smoke/corpus-local.summary.json)。

已固化样本：

1. `ai-agents-in-action`
   - 报告：[ai-agents-in-action.json](/Users/smy/projects/mygithub/DLEHY/artifacts/pdf-smoke/corpus-local/ai-agents-in-action.json)
   - 价值：低风险长书主样本，覆盖 frontmatter / appendix / index / backmatter

2. `attention-is-all-you-need`
   - 报告：[attention-is-all-you-need.json](/Users/smy/projects/mygithub/DLEHY/artifacts/pdf-smoke/corpus-local/attention-is-all-you-need.json)
   - 价值：medium-risk academic paper 主样本

3. `jim-simons-book-scan`
   - 报告：[jim-simons-book-scan.json](/Users/smy/projects/mygithub/DLEHY/artifacts/pdf-smoke/corpus-local/jim-simons-book-scan.json)
   - 价值：OCR/scanned fail-safe 样本

4. `building-ai-coding-agents`
   - 报告：[building-ai-coding-agents.json](/Users/smy/projects/mygithub/DLEHY/artifacts/pdf-smoke/corpus-local/building-ai-coding-agents.json)
   - 价值：medium-risk appendix 恢复样本

5. `llms-in-production`
   - 报告：[llms-in-production.json](/Users/smy/projects/mygithub/DLEHY/artifacts/pdf-smoke/corpus-local/llms-in-production.json)
   - 价值：第二本低风险长书样本，验证 chapter tree 不是单样本过拟合

6. `forming-effective-human-ai-teams`
   - 报告：[forming-effective-human-ai-teams.json](/Users/smy/projects/mygithub/DLEHY/artifacts/pdf-smoke/corpus-local/forming-effective-human-ai-teams.json)
   - 价值：single-column research paper 样本

## Verified Live Exports

最重要的真实 PDF live 产物：

- `Attention Is All You Need v2`
  - 报告：[report.json](/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/deepseek-attention-paper-v2/report.json)
  - merged HTML：[merged-document.html](/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/deepseek-attention-paper-v2/exports/b111498d-25cd-5ad7-8141-5cfbf0065481/merged-document.html)
  - manifest：[merged-document.manifest.json](/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/deepseek-attention-paper-v2/exports/b111498d-25cd-5ad7-8141-5cfbf0065481/merged-document.manifest.json)

- `Forming Effective Human-AI Teams v3`
  - 报告：[report.json](/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/deepseek-forming-teams-paper-v3/report.json)
  - merged HTML：[merged-document.html](/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/deepseek-forming-teams-paper-v3/exports/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.html)
  - manifest：[merged-document.manifest.json](/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/deepseek-forming-teams-paper-v3/exports/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.manifest.json)

- `Patterns of Application Development Using AI - Introduction rerun v3`
  - run report：`/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/patterns-ai-ch1-rerun-v3/parallel-report.json`
  - chapter HTML：`/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/patterns-ai-ch1-rerun-v3/exports/0498168f-2c5a-53c1-8af9-cb5a5735f7af/bilingual-82e5eeb7-5932-5e05-ac0c-bc33246c1297.html`
  - review package：`/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/patterns-ai-ch1-rerun-v3/exports/0498168f-2c5a-53c1-8af9-cb5a5735f7af/review-package-82e5eeb7-5932-5e05-ac0c-bc33246c1297.json`
  - manifest：`/Users/smy/projects/mygithub/DLEHY/artifacts/real-book-live/patterns-ai-ch1-rerun-v3/exports/0498168f-2c5a-53c1-8af9-cb5a5735f7af/bilingual-82e5eeb7-5932-5e05-ac0c-bc33246c1297.manifest.json`
  - 价值：这是当前最贴近用户真实投诉的 live 验收样本，优先级高于新增样本扩容

## Current Known Limits

这些不是 bug 列表，而是当前仍明确存在的能力边界：

- 仍不支持扫描/OCR 主路径
- medium-risk PDF 虽可放行，但只是“可进链路”，不是“默认稳定交付”
- academic paper 的双栏 reading order 仍未 robust
- table / figure / equation-heavy 页面仍需更强 around-order hardening
- 语义化 table export 当前只覆盖规则较稳定的可恢复表格；复杂跨页/合并单元格表格仍以 preserve/fallback 为主
- code continuity repair 当前只处理“夹在两段代码之间的无 caption 可疑图片”这一类误插入，不等于已完全解决复杂版式下的 code/figure 边界
- appendix 内更细 section tree 仍未正式进入公共 contract
- `LLMs in Production` 仍残留更深层 extractor 断词噪声：
  - `Adeep`
  - `Dataislikegarbage`
  - `canyougo`
- `backmatter` 当前仍偏 explicit-cue policy

## Immediate Next Work

如果开新会话、需要直接继续开发，默认按这个顺序推进：

1. 先审 `Patterns of Application Development Using AI` 第一章最新 HTML
   - 目标：把用户刚指出的真实问题区域作为主验收基准，而不是回到抽象 backlog
   - 输入：
     - `.../patterns-ai-ch1-rerun-v3/.../bilingual-82e5eeb7-5932-5e05-ac0c-bc33246c1297.html`
     - `.../patterns-ai-ch1-rerun-v3/.../review-package-82e5eeb7-5932-5e05-ac0c-bc33246c1297.json`
   - 输出：整理本章剩余 defect 列表，优先收集 image caption miss、图文顺序错位、table/code residual bug

2. 继续做 image-caption / contextual legend recovery v2
   - 目标：把这章里剩余 `7` 张未绑 caption 的图片再压下去
   - 固定验收页：`22, 27, 31, 32, 47, 48, 49`
   - 原则：宁可 preserve/fallback，也不要把正文错绑成 caption

3. 如果第一章进一步收敛，再做同一本书的 chapter-2 或多章节扩展
   - 目标：确认这轮修复不是只对 `Tokenization` 那一段生效
   - 顺序：先 chapter-level rerun，再考虑 document-level merged export

4. 当前这本真实书样本稳定后，再回到 broader roadmap
   - 第三篇真实英文论文或更异质 paper 扩样
   - `nested appendix section-tree upgrade`
   - `chapter-intro title cleanup v2`
   - `backmatter cue hardening v2`
   - `medium-risk PDF finer-grained policy`

## If Starting A New Session

建议按这个顺序恢复上下文：

1. 读 [TODOS.md](/Users/smy/projects/mygithub/DLEHY/TODOS.md)
2. 读 [pdf_status.md](/Users/smy/projects/mygithub/DLEHY/docs/pdf_status.md)
3. 看 [corpus-local.summary.json](/Users/smy/projects/mygithub/DLEHY/artifacts/pdf-smoke/corpus-local.summary.json)
4. 看本轮目标对应的真实样本报告
5. 打开主代码入口：
   - [pdf.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/structure/pdf.py)
   - [review.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/review.py)
   - [export.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/export.py)
6. 先做小回归，不要直接上 live run

## Main Source Files

高频入口：

- parser 主体：
  - [pdf.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/structure/pdf.py)
- bootstrap 分流：
  - [bootstrap.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/bootstrap.py)
- review：
  - [review.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/review.py)
- export：
  - [export.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/export.py)
- smoke 工具：
  - [pdf_smoke.py](/Users/smy/projects/mygithub/DLEHY/src/book_agent/tools/pdf_smoke.py)
  - [run_pdf_smoke.py](/Users/smy/projects/mygithub/DLEHY/scripts/run_pdf_smoke.py)
  - [run_pdf_smoke_corpus.py](/Users/smy/projects/mygithub/DLEHY/scripts/run_pdf_smoke_corpus.py)
- 候选扫描：
  - [run_pdf_candidate_scan.py](/Users/smy/projects/mygithub/DLEHY/scripts/run_pdf_candidate_scan.py)
- 主要回归：
  - [test_pdf_support.py](/Users/smy/projects/mygithub/DLEHY/tests/test_pdf_support.py)
  - [test_pdf_smoke_tools.py](/Users/smy/projects/mygithub/DLEHY/tests/test_pdf_smoke_tools.py)

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

1. 更新 [pdf_status.md](/Users/smy/projects/mygithub/DLEHY/docs/pdf_status.md)
2. 更新 [pdf_backlog.md](/Users/smy/projects/mygithub/DLEHY/docs/pdf_backlog.md)
3. 更新 [pdf_decisions.md](/Users/smy/projects/mygithub/DLEHY/docs/pdf_decisions.md)（如果有新的取舍冻结）
4. 更新 [TODOS.md](/Users/smy/projects/mygithub/DLEHY/TODOS.md)

## Resume Hint

如果新会话里不知道“从哪一刀开始”：

- 想继续当前真实书验收：从 `Patterns... Introduction rerun v3` 的 HTML + review package 开始
- 想继续扩样：从“第三篇真实英文论文扩样”开始
- 想继续长书结构：从 `nested appendix section-tree upgrade` 开始
- 想继续文本质量：从 `chapter-intro title cleanup v2` 开始
- 想继续边界策略：从 `backmatter cue hardening v2` 开始

默认推荐第一刀：

> 先打开 `Patterns of Application Development Using AI` 第一章最新 HTML 和 review package，把剩余的 image-caption miss 与图文顺序残差压下去；只有这一章稳定后，再继续扩新样本或回到 appendix/tree 路线。

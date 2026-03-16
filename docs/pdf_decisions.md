# PDF Decisions

Last Updated: 2026-03-16

## Purpose

记录 PDF 路线已经冻结或暂时接受的架构决策，避免每一轮重新争论同一件事。

全项目通用决策仍放在 `docs/decisions.md`；这里只记 PDF 特有取舍。

---

## PDF-D-001 PDF 是增量扩展，不是系统重写

Status: Accepted

Decision:

- PDF 只扩展 ingest / parse / structure recovery / PDF-specific QA
- 下游继续复用既有 `document -> chapter -> block -> sentence -> packet -> review -> export` 主链路

Why:

- PDF 的主要难点在上游结构恢复，而不是翻译链路本身
- 复用既有 stable sentence ID、alignment、QA、rerun 才能保持可证明性

Consequences:

- 不为 PDF 单独造第二套翻译系统
- PDF 的每次推进都应优先落到统一 IR

---

## PDF-D-002 先分流，再恢复

Status: Accepted

Decision:

- intake 阶段必须先判定 `text_pdf / mixed_pdf / scanned_pdf`
- 同时给出 `layout_risk`

Why:

- 不先分流，复杂版式和 OCR 问题会直接污染整个主链路

Consequences:

- 复杂版式优先 fail-safe
- P1 不承诺扫描 PDF

---

## PDF-D-003 高风险复杂版式不假装支持

Status: Accepted

Decision:

- `layout_risk=high` 的 PDF 不进入自动翻译主链路
- medium-risk 可以进入，但必须在 review 中显式打结构风险

Why:

- 双栏错序、复杂 sidebar、糟糕阅读顺序是上游致命错误
- 这类错误一旦放进下游，coverage 和质量指标都会失真

Consequences:

- 当前 contract 是保守的
- review / rerun 必须把结构问题路由到 `REPARSE_CHAPTER`

---

## PDF-D-004 Primary Extractor 用 PyMuPDF，但必须有仓库内 fallback

Status: Accepted

Decision:

- 优先用 `PyMuPDF`
- 同时保留仓库内 `BasicPdfTextExtractor` 作为 fallback

Why:

- `PyMuPDF` 同时提供文本、bbox、page geometry 和 outline/bookmarks
- 仓库内 fallback 可以保证基础测试和最小能力不被额外 wheel 阻塞

Consequences:

- parser 设计要适配 extractor abstraction
- bookmark / TOC recovery 不能完全写死在某一个外部库上

---

## PDF-D-005 不确定性必须显式传播

Status: Accepted

Decision:

- PDF 结构恢复产生的风险必须落到 metadata、summary、review 和 rerun

Why:

- PDF 不是“译得不自然”，而是“结构可能先错了”
- 如果风险不显式传播，运营面会误以为主链路健康

Consequences:

- 需要 `parse_confidence / pdf_layout_risk / structure_flags / suspicious_page_numbers`
- 需要结构 issue：`MISORDERING`、`STRUCTURE_POLLUTION`、`FOOTNOTE_RECOVERY_REQUIRED`

---

## PDF-D-006 header/footer/toc_entry 要保留 provenance，但不进入翻译

Status: Accepted

Decision:

- `header`、`footer`、`toc_entry` 不直接删除
- 它们保留在 provenance 中，但 `translatable=false`

Why:

- 直接删除会损失 debug 和 coverage 证据
- 但把它们送进 packet / export 会显著污染正文链路

Consequences:

- block rules、context builder、rebuild、export 都要统一过滤这类 role
- review 仍然可以检测这类污染是否泄漏进正文链路

---

## PDF-D-007 chapter recovery 优先级是 outline -> TOC -> heading

Status: Accepted

Decision:

- chapter start 的信号优先级固定为：
  1. outline / bookmark
  2. TOC text heuristic
  3. page heading heuristic

Why:

- outline/bookmark 通常是最强结构信号
- 仅靠 heading typography 容易把 TOC、frontmatter 或 section heading 误切成 chapter

Consequences:

- outline 只使用 top-level entry 切 chapter
- TOC 页不能被当成正文 chapter

---

## PDF-D-008 footnote 先独立保存，再谈归位

Status: Accepted

Decision:

- P1 对脚注的最低要求是：不污染正文、能独立保存
- linkage 匹配不稳时，宁可标 orphan，也不强行并入正文

Why:

- 脚注归位错误比“暂时未归位”更危险
- 当前阶段的目标是结构稳定，不是复杂版面的完美还原

Consequences:

- footnote 会保留 `footnote_anchor_matched`、anchor label 和 orphan flag
- orphan footnote 会进入结构 review，而不是静默通过

---

## PDF-D-009 public contract 要落后于内部能力

Status: Accepted

Decision:

- 只有在真实 text-PDF smoke 和回归都稳定后，才升级对外 PDF 支持宣称

Why:

- 内部 parser 可以先迭代到 P1-B partial，但对外能力边界必须保守

Consequences:

- 当前仍保留 `p1_text_pdf_bootstrap`
- 文档状态页要明确区分“内部阶段”和“对外 contract”

---

## PDF-D-010 special sections 先建模为 page family，再决定更细策略

Status: Accepted

Decision:

- `frontmatter / appendix / references / index / toc` 先作为 page family 信号进入 metadata 和 chapter boundary
- 第一阶段先解决“不要混进正文 chapter”，再决定是否需要更细的导出/送审策略

Why:

- 这类页面最容易污染章节树和 review 口径
- 但它们是否应翻译、如何导出，往往要等更真实的样本再决定

Consequences:

- block / chapter metadata 需要保留 `pdf_page_family / pdf_section_family`
- 当前策略是“先分离边界、保留 provenance、延后更细运营策略”

---

## PDF-D-011 TOC 页码先用 title match，其次 offset reconciliation

Status: Accepted

Decision:

- TOC page-number 解析优先使用 `TOC title -> actual heading` 匹配
- 匹配不足时，再使用页脚 printed page label 推断全局 offset

Why:

- 真正可靠的是结构标题，不是孤立页码数字
- 页脚 label 适合作为 fallback，但不能单独主导所有 TOC 恢复

Consequences:

- TOC metadata 需要保留 `printed / resolved / offset / resolution_source`
- 复杂 page-label 体系仍要留在后续 hardening，而不是在第一轮里过度承诺

---

## PDF-D-012 footnote 先保守连通，再做更强 relocater

Status: Accepted

Decision:

- footnote v2 先解决三件事：跨页续写、上一页 anchor 匹配、贴词数字锚点
- 对复杂多段 / 多页脚注，先保守保留 evidence 和 orphan/risk，而不是强行“智能归位”

Why:

- 这类错误一旦误并入正文，伤害比暂时保守大得多
- 当前阶段更需要降低 orphan 假阳性和跨页断裂，而不是追求脚注完美重建

Consequences:

- parser 会保留 `footnote_continuation_repaired` 等恢复标记
- 更强的 footnote relocater 留到后续单独 hardening，而不是塞进这一轮启发式里

---

## PDF-D-018 真实样本发现要先工具化，再谈自动扩 corpus

Status: Accepted

Decision:

- 在追加真实 PDF smoke 样本前，先提供目录剪枝 + 候选评分 + manifest-ready 推荐输出
- 自动驾驶默认复用候选扫描工具，而不是每轮人工翻本机目录

Why:

- 当前最大的验证缺口是“缺少真实低风险 text PDF 放行样本”，不是单个 parser 启发式
- 如果发现流程仍依赖人工翻目录，自动推进会反复丢状态，也无法稳定复跑

Consequences:

- `run_pdf_candidate_scan.py` 现在属于 PDF 驾驶舱入口之一
- 候选评分只服务于 smoke 选样，不直接参与 parser 风险判定

---

## PDF-D-028 Back Matter 先走 source-only，而不是混入普通双语导出

Status: Accepted

Decision:

- 仅在已验证的 `index -> tail body` 路径上，把 `backmatter` 升级为正式 `page_family / section_family`
- `backmatter` 默认 `translatable=false`
- `review package / bilingual manifest / bilingual_html / merged_html` 必须共用 block-level render contract，不能再让某个导出路径绕过 `source-only`

Why:

- `Back Matter` 的典型内容是 marketing、书单、版权尾页或导读补充，继续走普通双语表格会制造“看起来可翻译”的假象
- 如果 parser 已经把它识别成结构化 `backmatter`，但 export 还按 sentence-table 渲染，运营面看到的 contract 会分叉
- 当前真实样本只验证了 `index -> tail body`，先收敛这条路径，比一开始把所有尾页都强推成 `backmatter` 更稳

Consequences:

- `backmatter` 现在默认保留原文，不进入 packet 翻译
- `bilingual_html` 已从旧的 sentence-table 路径升级为 block-level render path，和 merged/review 出口共享 preserve policy
- `review package` 和 `bilingual manifest` 现在会显式导出 `pdf_preserve_evidence`，让 operator 不必再从 HTML 反推 preserve contract
- 这条策略暂不自动推广到 `appendix -> tail body`，要等更多真实样本再决定
- 即使扫描结果为空，项目也能明确知道阻塞点是“缺样本”，而不是“解析器未知”

---

## PDF-D-029 medium-risk appendix 先恢复 page-start intro 子章节，再谈完整 section tree

Status: Accepted

Decision:

- 对已经进入 `appendix` family 的 medium-risk 文档，允许把 `appendix_intro` 页中的 lettered title 恢复成新的 appendix subchapter
- 当前只接受两类信号：页首已有 appendix label，或块尾明确出现 `this appendix`，并且可以抽出稳定标题
- 仅凭长正文末尾的 `later in this appendix` 一类晚出现 cue，不再允许兜底生成标题；远距离 cue 必须伴随显式 appendix title lead 或可提取的尾部 title match
- 同一 appendix family 内，`appendix_intro` 和 `inline_heading` 一样都可以触发新的 `section_family_subheading`

Why:

- 真实样本 `Building AI Coding Agents...` 已经证明 medium-risk appendix 不是“完全没结构”，而是 page-start 的轻量 section title 被吞成普通正文
- 如果继续只保留单一 `Appendix`，review、rerun 和导出都会停留在过粗粒度，运营上看不到真实边界
- 但 appendix 页里还存在大量块内合并和多小节混排，当前不适合直接承诺完整 section tree

Consequences:

- 当前 smoke contract 对 `Building AI Coding Agents...` 再提升一层：从 `Chapter 1 + References + Appendix` 升级成 `Chapter 1 + References + Appendix A Complete Tool Catalog + Appendix K Full System Prompt Templates`
- 这条策略的前提是假设真正的 appendix subchapter 往往出现在页首，并伴随 appendix-intro 叙述；对页内中段的小节标题仍保持保守
- 适用边界当前只覆盖 `appendix_intro / inline_heading` 型子章节，不覆盖单页内多个 appendix section 的精细切分
- 真实长书里的普通教程正文经常会在段尾提到 `later in this appendix`；这类 cue 现在只保留 family 参考意义，不再直接参与标题生成，避免把正文句子截成假 appendix 章节

---

## PDF-D-030 block-level PDF debug evidence 只放 review/export artifact，不回写主 metadata

Status: Accepted

Decision:

- `pdf_page_debug_evidence` 作为 export-time artifact 进入 `review package`
- 它允许 chapter-sliced 地暴露 page/block 级 debug 证据，包括 `page_family`、preserve policy、render mode、source excerpt 和 block-level provenance
- 不把这类 block-level debug dump 回写到 `document.metadata` 或常规 API summary

Why:

- operator 需要的是“为什么这一页被 source-only / preserve / special-section 处理”，而不是再从 HTML 或散落 metadata 里反推
- 但如果把 block-level debug 直接塞进主 metadata，会让 document contract 迅速膨胀，并把 review/debug 专用细节泄漏到普通 API 消费面

Consequences:

- 当前 `pdf_page_evidence` 仍保持轻量、适合 summary / API / smoke；更细的 debug 信息只在 review/export artifact 中出现
- 这条策略的前提是假设当前最需要 block 级 forensic evidence 的消费者是 operator/reviewer，而不是主链路上的所有调用方
- 适用边界是 chapter-sliced review/export artifact；如果后续需要更通用的 block-level PDF debugging API，再单独设计公共 contract，而不是直接复用 review payload

---

## PDF-D-031 appendix continuation 先只切顶层 subheading，不直接展开 nested section tree

Status: Accepted

Decision:

- 对 `page_family=appendix` 且 `family_source=continuation` 的页面，允许从前几个 substantive block 中恢复顶层 appendix subheading
- 顶层 continuation-page subheading 只有在页面具有足够 substantive blocks 时才允许切章，避免把短步骤页或稀疏 appendix 小节过早拆成独立 chapter
- 当前只接受 `K.3` / `A.2` 这一层级的标签；`K.2.5` 这类嵌套 label 暂时不进入 chapter/section tree
- 这条规则只在 continuation 页生效，不会和 `appendix_intro / inline_heading` 同页抢 chapter start

Why:

- 真实样本 `Building AI Coding Agents...` 已经证明 appendix 不止有 intro-page 标题，后续 continuation 页也会以 `K.3`、`K.4` 这类顶层 section 开新块
- 但如果直接把所有 `K.2.5`、`A.3.1` 之类嵌套小节都拉进 chapter tree，过切和回归风险会快速上升

Consequences:

- 当前 smoke contract 对 `Building AI Coding Agents...` 再提升一层：从 `4` 章升级为 `6` 章，新增 `Appendix K.3 Thinking Mode Templates` 和 `Appendix K.4 Specialized Standalone Templates`
- 这条策略的前提是假设真正值得独立 rerun/review 的 appendix section，往往以顶层 continuation-page subheading 开场，而不是所有嵌套小节
- `AI Agents in Action` 已证明 `A.2 / B.4 / B.6` 这类 block 很少的 appendix 步骤页不应被同样切开，因此当前规则显式依赖 continuation-page density guard
- 适用边界当前只覆盖 top-level appendix subheading；nested section tree 仍留给后续专门决策

---

## PDF-D-032 nested appendix subheading 先进入 evidence，不直接升级成 section tree

Status: Accepted

Decision:

- `K.2.5`、`A.3.1` 一类 nested appendix subheading 先只作为 evidence 进入 `pdf_page_evidence` 和 `pdf_page_debug_evidence`
- 当前不让这些 nested label 直接触发新的 chapter / section tree 节点

Why:

- 真实样本已经证明 nested appendix 小节是稳定存在的结构信号，但它们是否值得独立 rerun / export / review，还没有足够多的真实书支撑
- 直接切树的风险比“先可观测再决策”更高，因为 nested 小节数量多、层级深，最容易把 chapter tree 过切

Consequences:

- operator 现在可以在 review package 里直接看到 nested appendix candidate 页，不需要从原文里手动找 `K.2.5`
- 这条策略的前提是假设当前最重要的是让 nested 结构可观测、可回归，而不是立即把它升级成公共 contract
- 后续若要升级成正式 section tree，需要再补真实样本和 rerun/export 语义，不直接沿用当前 evidence contract

---

## PDF-D-033 appendix 之后的 Back Matter 先采用 explicit-cue policy，不按“靠近文末”一刀切推广

Status: Accepted

Decision:

- `appendix -> tail body` 只有在尾页带明确 `backmatter_cue` 时，才允许升级成正式 `page_family=backmatter`
- 当前接受的 cue 分两类：显式 heading-title（如 `Upcoming Titles` / `About the Author`）和强 marketing-signals（如 `ISBN + pages/price + publisher/web`）
- `backmatter_cue / backmatter_cue_source` 直接进入 `pdf_page_evidence / pdf_page_debug_evidence / pdf_preserve_evidence`

Why:

- 真实回归已经证明，`appendix -> tail body` 如果只靠“页数接近文末”来推断，很容易把合法的附录续页误伤成 source-only 的 `Back Matter`
- 同时运营面又确实需要识别书尾的 marketing / publisher / back-cover 资料页，否则这些页会继续混在 appendix 里进入翻译链路

Consequences:

- 这条策略的前提是假设真正值得 source-only 的尾部资料页，通常会留下明确的标题或营销出版信号，而不是完全无 cue 地贴在附录后面
- 适用边界当前只覆盖 explicit-cue policy；普通 appendix continuation 即使已经接近文末，也不会仅凭位置自动升级成 `backmatter`
- 维护代价是 cue contract 稍微变复杂，但换来的是更可审校的 page-level evidence，而不是黑箱 heuristics
- 后续如果要接受更弱的 cue，必须继续用真实长书 smoke 验证，不能直接沿用现在的 preserve policy

---

## PDF-D-034 footnote relocater v3 先只吃“脚注区里的 markerless 段落”，不碰更复杂交错脚注

Status: Accepted

Decision:

- 当一个 footnote block 后面紧跟着小字号 markerless body 段落，且它仍位于脚注区时，允许把它并回前一个 footnote
- 当前脚注区只接受两种位置：同页页底脚注区，以及跨页续写后的次页页顶脚注区
- relocation 结果直接写进 block/page evidence：`footnote_segment_count / footnote_segment_roles / footnote_relocation_modes / relocated_footnote_count`

Why:

- footnote v2 已经能处理“续写句子”，但对第二段/第三段这种以大写句子开头、上一段又以句号结束的脚注仍然过于保守
- 如果继续只靠 orphan/anchor linkage，operator 看不到“脚注其实已经被拆散但 parser 本可以归位”的问题，也无法稳定回归

Consequences:

- 这条策略的前提是假设真正的脚注续段，大多仍会停留在页底脚注区或续页页顶脚注区，并保持与 footnote 相近的小字号
- 适用边界当前不覆盖交错双脚注、多锚点同页混排、页中插入式注释和非数字复杂符号体系
- 维护代价是 footnote metadata/evidence contract 又多了一层，但换来了更直接的 debug/export 可观测性
- 后续如果要做 v4，必须继续用真实 PDF 校验多锚点混排，而不能仅靠当前 synthetic fixture 外推

---

## PDF-D-035 第二本真实长书样本先冻结“稳定尾部结构”，不把 noisy body titles 过早写死

Status: Accepted

Decision:

- 第二本低风险长书样本 `LLMs in Production` 进入 smoke corpus 时，先冻结 `layout_risk / chapter_count / appendix-index-backmatter tail structure / key page families`
- 当前不把前半段 body chapter 的完整标题全文写成强预期，因为 `basic` extractor 仍会留下 spaced-word / 断词噪声

Why:

- 这本书已经足够证明当前主结构恢复不再只对 `AI Agents in Action` 一书成立，尤其是 `Front Matter + Appendix A/B/C + Index + Back Matter` 这条尾部结构
- 但如果现在把 noisy body titles 全写死，后续只要我们改进 title cleanup，就会被 corpus 误报成回归，反而拖慢迭代

Consequences:

- 这条策略的前提是假设当前更值得冻结的是结构 contract，而不是 extractor 仍未完全清理的标题表面文本
- 适用边界当前只覆盖第二本真实长书样本；如果未来引入 typography 更稳定的 extractor，可以把 body chapter titles 逐步升级成更强预期
- 验证方式是 `corpus.local.json` 新增 `llms-in-production` case，并要求 `5/5 passed`

---

## PDF-D-036 chapter-intro title cleanup 先收最明显的 escape/断词/句子尾噪声，不直接做通用正文清洗

Status: Accepted

Decision:

- title cleanup 当前只作用在 `chapter_intro` fallback title 推断，不扩散到通用正文 block 文本
- 第一刀先处理三类最明显噪声：PDF octal escape、单字母序列断词、intro page 上的句子重启尾巴
- 对 `LLMs in Production` 只把已经明显稳定的 body title 写回 corpus expectation，不把仍有深层断词的标题强行冻结

Why:

- 第二本真实长书已经证明，结构恢复本身基本成立，但 noisy chapter titles 会直接拉低“可交付”观感，也会影响后续 review/operator 判断
- 如果把清洗直接推广到通用正文，风险会远高于收益，因为正文里正常分词和 OCR 噪声的边界更难守

Consequences:

- 这条策略的前提是假设当前最值得优化的是 chapter title surface quality，而不是整篇正文的断词修复
- 适用边界当前只覆盖 `chapter_intro` fallback title；outline / TOC / explicit heading 路径暂时不走这套清洗
- 验证方式是 synthetic unit tests 加 `LLMs in Production` 真实 smoke，当前至少要求 `Words awakening...` 和 `Prompt engineering...` 进入 corpus expectation

---

## PDF-D-019 真实放行样本先锁定 intake contract，不提前冻结章节召回缺口

Status: Accepted

Decision:

- 真实低风险 text-PDF 样本进入 smoke corpus 时，先锁定 `pdf_kind / layout_risk / bootstrap.status`
- 在 long-book chapter recovery 还未稳定前，不把当前的单章召回结果写成 corpus 强预期

Why:

- 当前阶段最先要确认的是“真实低风险文本 PDF 能否稳定放行且不污染 special sections”
- 如果过早把已知偏弱的章节召回写死到 smoke expectation，后续改进会被错误地视为回归

Consequences:

- 真实 pass-path 样本可以先服务于 intake / bootstrap contract
- chapter recovery 质量要通过单独 hardening 和更细粒度断言推进，而不是把临时现状冻结进 smoke 基线

---

## PDF-D-020 fallback extractor 上的长书召回要依赖文本章首信号，而不是字号

Status: Accepted

Decision:

- 当 extractor 无法稳定提供字号层次时，chapter recovery 允许使用 `chapter intro page` 文本信号
- 当前具体信号是：页首标题片段加 `This chapter covers`，以及首个 intro chapter 之前的 frontmatter preamble 收敛

Why:

- `AI Agents in Action` 证明了 fallback extractor 上的字号几乎不可用，但章首页仍有稳定文案模式
- 如果继续只信字号/outline/TOC，真实长书会长期退化成单章，验证价值很有限

Consequences:

- chapter recovery 的召回现在一部分来自文本模式，而不是版式模式
- 这条规则必须持续用真实长书 smoke 校正，避免正文里的弱 `in this chapter` 说明误触发新章节

---

## PDF-D-021 首个 intro chapter 之前的 preamble 在有证据时收敛为 Front Matter

Status: Accepted

Decision:

- 当文档在首个 `chapter_intro` 之前累积出明确 frontmatter 证据时，这段 preamble 不再伪装成 `Chapter 1`
- 当前 frontmatter 证据以 `contents / preface / about ...` 这类文本信号为主

Why:

- 真实长书里，title page / contents / preface 常常没有稳定字号层次，但它们也不应该污染正文章树
- 如果继续把这段内容落成默认 `Chapter 1`，运营面会误判章节覆盖率和结构质量

Consequences:

- frontmatter 现在可以在没有可靠 heading typography 的情况下被单独建模
- 这条规则仍然是保守启发式，只有在 frontmatter 证据足够明显时才会触发

---

## PDF-D-022 isolated `content_signature=index` 默认保留证据，不直接提升 page family

Status: Accepted

Decision:

- `content_signature=index` 不再默认直接提升为 `page_family=index`
- 只有在“邻页也支持 index”或“已接近文末且后面没有新 heading”时，才把它提升成真正的 index page
- 即使不提升，`pdf_page_content_family=index` 仍然保留，供 smoke / evidence / review 调试使用

Why:

- 真实长书里会出现孤立的 index-like 噪声页，例如工具参数列表、书单广告、价格与 ISBN 信息
- 如果这些页直接进入 `page_family=index`，它们会沿着 block metadata 污染 chapter `structure_flags`，让有效章节被错误打成结构风险

Consequences:

- 主链路现在更偏向 fail-safe：孤立噪声页默认按 `body` 处理，避免误打 review / rerun
- 这条规则的前提是假设真正的 back-of-book index 通常有连续页支持，或至少出现在文末
- 适用边界仍是 P1 文本 PDF；非常短、单页、非标准索引如果既不在文末也没有邻页支持，可能会被保守地留在 `body`
- 验证方式是合成 fixture + 真实样本双重回归，当前以 `AI Agents in Action` 的 page `123 / 342` 为 smoke 锚点

---

## PDF-D-023 basic extractor 的 fragment-only 长文本 PDF 先降到 medium，而不是直接 high reject

Status: Accepted

Decision:

- 当 `pdf_extractor=basic` 且文档仅出现 `column_fragment` 信号、没有 `multi_column` 证据时，长文本 PDF 不再直接打成 `layout_risk=high`
- 当前收敛条件是：`text_pdf`、`page_count >= 40`、`fragment_page_count >= 5` 且 fragment 页占比不超过 40%
- 同时把 `extractor_kind / multi_column_page_count / fragment_page_count` 写进 `pdf_profile`，并把 page-level `layout_signals` 透到 `pdf_page_evidence`

Why:

- 当前运行环境主要落在 `BasicPdfTextExtractor`，它的 bbox 是基于 content stream 的简化估算，几何置信度明显低于 PyMuPDF
- 真实样本 `Building AI Coding Agents for the Terminal...` 证明了“basic extractor + scattered column_fragment” 可能仍能 bootstrap，只是应走 medium-risk 可审查路径，而不是被 intake 直接拒绝

Consequences:

- 这条规则的前提是假设真正需要强拒绝的复杂版式，通常会出现更直接的 `multi_column` 证据，或者在短篇文档上暴露得更明显
- 适用边界目前覆盖两类 `basic` extractor 文档：长文本 fragment-only PDF，以及带明显 title-page + trailing references 的 short academic paper
- 维护代价是 profiler contract 变复杂了一点，但换来了更清晰的 extractor-aware 风险语义
- 验证方式是 synthetic profiler tests 加真实样本 smoke：`Building AI Coding Agents...` 现在应为 `medium` 且 bootstrap 成功，`Attention Is All You Need` 现在应进入 `academic_paper` medium-risk lane

---

## PDF-D-028 short academic paper 先走 `academic_paper` medium-risk lane，而不是继续 hard reject

Status: Accepted

Decision:

- 对 `basic` extractor 的短篇英文论文，若同时满足 `fragment-only suspicious pages + title-page signal + trailing references`，先打 `recovery_lane=academic_paper`
- 该 lane 当前仍保留 `layout_risk=medium`，不是降成低风险；bootstrap 允许进入主链路，但 review 继续显式报告结构风险
- 第一刀只承诺 `body + references` 级别的可译结构，不承诺完整的双栏 section tree

Why:

- `Attention Is All You Need` 已经证明：真实论文并不是“完全不可译”，问题在于 intake 把它和更糟的复杂版式一起直接拒绝了
- 当前 forced-parse 已经能恢复标题、正文块和 references；与其继续 hard reject，不如先把论文放进受控的 medium-risk lane，再迭代 section recovery
- 这种做法比直接把所有短篇 `high-risk` PDF 一起放开更稳，因为 profiler 仍要求明确的论文信号组合

Consequences:

- 前提是假设短篇 academic paper 常见结构是 `title page + fragment-only body pages + trailing references`
- 适用边界目前只覆盖 `basic` extractor、`page_count <= 24`、无 OCR、且没有显式 `multi_column` 证据的 text PDF
- 失效边界是：没有 references、标题页不明显、或 extractor 形态与当前样本差异很大的论文，仍可能留在 `high/reject`
- 验证方式是 synthetic bootstrap tests 加真实样本 smoke：`NIPS-2017-attention-is-all-you-need-Paper.pdf` 现在必须 `bootstrap.status=succeeded`、`profile.recovery_lane=academic_paper`、`chapter_count=2`

---

## PDF-D-029 `academic_paper + medium-risk` 先允许 advisory formal export，而不是永久卡在 review gate

Status: Accepted

Decision:

- `ReviewService` 不再因为“存在任意 issue”就把章节打成 `review_required`，而是只看 `blocking_issue_count`
- 对 `academic_paper + layout_risk=medium`，当 `parse_confidence >= 0.8` 且 `suspicious_page_numbers <= 2` 时，`MISORDERING` 从 blocking 降为 advisory
- 这类章节仍保留 open issue 和 `REPARSE_CHAPTER` action，但允许进入正式 `bilingual_html / merged_html` 导出

Why:

- 真实样本 `Attention Is All You Need` 已证明论文当前可以完整翻译并保持 coverage / alignment 正常，真正挡住正式导出的是“medium-risk 一律 blocking”的 review policy，而不是实际翻译失败
- 如果继续把所有 `academic_paper` medium-risk 文档永久卡在 review gate，产品层就会停留在“能翻但不能交付”的半状态
- 现有质量模型已经区分了 `blocking_issue_count` 与普通 issue，总状态机和导出 gate 应该利用这个差异，而不是把 advisory issue 也等价成阻断

Consequences:

- 前提是假设 short academic paper 当前最现实的交付目标是“带结构风险提示的正式导出”，而不是一步到位的完美 section tree
- 适用边界当前只覆盖 `recovery_lane=academic_paper` 且 `layout_risk=medium` 的文档；普通 medium-risk 文本 PDF 仍继续 blocking
- 维护代价是 review policy 从单一规则变成 lane-aware policy，但换来了更符合产品目标的 formal export 语义
- 仍然存在失效边界：双栏阅读顺序和 section heading tree 尚未真正解决，所以 open advisory issue 仍必须保留，不能误报成“完全通过”
- 验证方式是 synthetic review-policy tests 加真实样本 rerun：`Attention Is All You Need` 现在必须 `29/29` packet 成功、保留 2 个 non-blocking `MISORDERING` issue，并正式导出 `merged-document.html`

---

## PDF-D-031 academic paper 先做 inline section heading recovery，再继续压双栏结构风险

Status: Accepted

Decision:

- 在 `academic_paper` lane 中，对正文 paragraph block 追加 inline heading split
- 当前第一刀覆盖两类信号：
  - 起始的 standalone heading，如 `Abstract ...`
  - 句内边界后的 numbered heading，如 `1 Introduction ... 2 Model Architecture ...`
- split 后直接落成现有 `heading` block，带 `academic_section_heading_recovered` flag 和 `pdf_academic_section_level`

Why:

- 真实样本 `Attention Is All You Need` 在 formal export policy 放行后，最大缺口已经不是“能不能导出”，而是正文结构几乎全压成大段 paragraph，operator 很难判断 section tree 是否还在
- 如果 section heading 仍停留在正文字符串里，后续不论是 review policy、reading-order hardening，还是 merged HTML 可读性，都缺少结构锚点
- 与其继续先调 gate，不如先把 parser 里最有辨识度的论文 section signal 变成正式 IR

Consequences:

- 前提是假设 short academic paper 常见的 section heading 仍会在 text stream 中留下 `Abstract` 或 `1 / 1.1 / 3.2.2` 一类 heading cue，即使 extractor 把它们和正文合并了
- 适用边界当前只覆盖 `academic_paper` lane，不会影响普通 text PDF 或 long-book chapter recovery
- 第一刀仍有明确失效边界：heading cleanup 还不够稳，真实样本里仍可能出现 `3 Model Ar`、`Embeddings and Softmax Similarly` 这类残缺 heading；这一步先解决“有无结构锚点”，不是“一次性清洗到完美标题”
- 验证方式是 synthetic inline-section fixture 加真实样本 bootstrap：synthetic paper 必须恢复 `Abstract / 1 Introduction / 2 Model Architecture / 3 Training / 4 Results`，真实 `Attention Is All You Need` 当前应恢复出多个 heading block，而不再只有 chapter title

---

## PDF-D-032 academic paper heading cleanup v2 先在 parser 侧消掉最明显的残缺/误切 heading

Status: Accepted

Decision:

- 在 inline heading split 之后，对 `academic_paper` heading 追加 cleanup：
  - 允许 `Ar + chitectur + e` 一类碎词标题在 heading/body 边界上继续补全
  - 将 `Encoder:` 这类被错误吞进 section heading 的 trailing colon cue 回退到正文
  - 将 `Similarly` 这类明显属于正文句首的词从 heading 尾部剥离
  - 对 standalone heading 使用更严格的 prose-lead 检测，避免表格前的 `Model` 被误切成 heading
- 标题碎词归一化改成收敛式多轮 merge，但显式排除 `we / that / up` 等功能词，避免把正常短词粘连成 `weexplore`

Why:

- section recovery v1 解决了“有没有结构锚点”，但真实样本 `Attention Is All You Need` 立刻暴露出新的可读性问题：`3 Model Ar`、`3.4 Embeddings and Softmax Similarly`、表格前的 `Model` 让最终 HTML 虽然有 heading，却还不够可交付
- 这些问题本质上是 parser cleanup，不应继续靠 review/advisory 策略兜底；如果不先在 parser 侧压掉，后续 reading-order hardening 会一直混着标题噪声一起调
- 与其直接做更激进的 section tree，不如先把最明显的 broken heading 收敛成稳定 contract

Consequences:

- 前提是假设当前 academic-paper 真实失败模式主要集中在少量可枚举的 heading/body 边界噪声，而不是 section cue 完全缺失
- 适用边界当前仍只覆盖 `academic_paper` lane；普通 text-PDF 的 chapter-intro/title cleanup 不直接复用这套更激进的 split cleanup
- 维护代价是标题归一化规则更复杂，必须持续防回归正常 title cleanup；因此这次额外锁定了 intro-title 回归，确保 `Why large` / `Setting up` / `Now we` 不会被误拼
- 验证方式是 synthetic noisy-inline-section fixture + 全量 `tests/test_pdf_support.py` + 真实论文 rerun：`Attention Is All You Need` 的最终 HTML 中必须出现 `3 Model Architecture`、`3.1 Encoder and Decoder Stacks`、`3.4 Embeddings and Softmax`、`6 Results`、`7 Conclusion`

---

## PDF-D-033 academic paper 的 `MISORDERING` 先改成章节本地证据驱动，再继续做更深的双栏顺序恢复

Status: Accepted

Decision:

- `ReviewService` 对 `layout_risk=medium` 的 `MISORDERING` 不再只看文档级 profile，而是先切章到 chapter-local `pdf_page_evidence`
- 如果某个章节本地没有 `layout_suspect` 页，则不再继承文档级 `MISORDERING`
- 对 `academic_paper`，当本地可疑页都已被 recovered section headings 或 caption-only 页面锚定，且章节内已有足够 heading anchors 时，也不再继续生成 advisory `MISORDERING`

Why:

- `Attention Is All You Need` v2 已证明 parser 侧的 heading cleanup 和 section recovery 足以让结构更清晰，但 review 仍然沿用文档级 `layout_risk=medium` 粗暴下发 issue，导致 `References` 这种本地完全无可疑页的章节也被误报
- 如果不先把 review 收成章节本地证据驱动，就算 parser 已经把真实问题压下去，operator 侧仍会持续看到噪声 issue，难以判断哪些章节真的需要介入
- 这一步比直接宣称“双栏阅读顺序已解决”更诚实：先用本地证据消噪，再继续做真正的正文顺序恢复

Consequences:

- 前提是假设 `pdf_page_evidence.layout_suspect / recovery_flags / role_counts` 已足够表达“这一章剩余的顺序风险还有没有落在 operator 需要介入的范围内”
- 适用边界当前主要覆盖 `layout_risk=medium`，尤其是 `academic_paper` lane；高风险复杂版式和扫描/OCR 仍保持更保守的阻断策略
- 失效边界也明确：这不是完整的双栏 reading-order solver，如果出现新的论文样本在本地 page evidence 上仍无法给出足够锚点，`MISORDERING` 仍会保留
- 验证方式是 review 单测 + 真实论文 re-review：`Attention Is All You Need` 的正文与 references 章节在 v2 re-review 后应降到 `0` open issues，同时保持 `merged_export_ready=true`

---

## PDF-D-024 medium-risk 文档中的 headingless appendix 先恢复 section family，再谈 appendix 内细分

Status: Accepted

Decision:

- 对 `body` block 中嵌着 `this appendix ...` 的页，允许直接提升为 `page_family=appendix`
- 一旦 appendix 起点建立，后续无强 heading 的页面继续沿用现有 `appendix` continuation
- 对同页的普通 heading candidate，special-section candidate 优先，避免同一页被重复切章

Why:

- 真实样本 `Building AI Coding Agents...` 在 references 之后进入 appendix，但首个 appendix 页没有独立 heading block，只有嵌在正文里的 appendix 说明句
- 如果不先把 section family 拉出来，后面所有 appendix 页都会被错误留在 references chapter 里，review 和导出都看不见真实结构

Consequences:

- 这条规则当前优先解决“references 之后切出 appendix”这个更上游的问题，不追求 appendix 内 K/F/G 等细分 section 一步到位
- 前提是假设 `this appendix` 在页首 240 字符内出现时，通常是在声明章节/附录身份，而不是正文随口提及
- 适用边界仍是 text PDF；如果正文在页首频繁引用“this appendix”，理论上可能误触发 appendix，但当前真实样本价值高于这个较低概率风险
- 验证方式是合成 fixture + 真实样本 smoke：`Building AI Coding Agents...` 现在应从 `Chapter 1 + References` 提升为 `Chapter 1 + References + Appendix`

---

## PDF-D-025 long-book 的 special-section 先切干净，再细分 appendix/backmatter

Status: Accepted

Decision:

- 对页首被 extractor 合并成单一正文块的 `INDEX ...` / `Appendix A ...`，允许通过 inline heading 恢复 `page_family`
- 当文档在尾部从 `appendix / index` 回到无 heading 的普通页时，先切出独立 `Back Matter` 章节，避免继续吞进 special section
- `Back Matter` 当前只作为 chapter boundary title，不额外升级成正式 page family

Why:

- 真实样本 `AI Agents in Action` 暴露出两个连续问题：appendix/index 的 heading 会被 basic extractor 合并进正文块；即使页 family 修好了，尾部 marketing/back-cover 仍会继续被最后一个 special section 吞掉
- 如果不先把这两层边界切干净，后续再做 `Appendix A / B` 细分或 backmatter family policy，都建立在错误 chapter tree 之上

Consequences:

- 当前 smoke contract 对 `AI Agents in Action` 从 `13 章` 升级到 `15 章`：`Front Matter + 11 body + Appendix + Index + Back Matter`
- 前提是假设书尾从 `appendix / index` 回到无 heading 的普通页时，更可能是 promotional/back-cover material，而不是重新回到正文主线
- 适用边界当前只覆盖文档尾部的 `appendix/index -> body` 回切；references 之后的短尾页仍保持更保守策略，避免误切 `Back Matter`
- 验证方式是合成 fixture + 真实样本双回归：inline `Index` 页必须恢复为 `page_family=index`，而 `AI Agents in Action` 的 page `340 / 342` 必须回落到 `body`

---

## PDF-D-026 同一 appendix family 内的 inline heading 要允许切出多附录

Status: Accepted

Decision:

- 当 `page_family=appendix` 且来源是 `inline_heading` 时，如果当前页标题与上一个 inline appendix 标题不重叠，就把它视为新的 appendix chapter start
- 这条规则当前只作用于 `appendix`，不泛化到 `references / index / frontmatter`

Why:

- 真实样本 `AI Agents in Action` 已经证明 appendix 边界能切出来，但 `Appendix A` 和 `Appendix B` 仍会因为 page family 相同而被合并成单一 appendix chapter
- 如果不在 chapter-start 层显式支持“同 family 内的新 appendix heading”，后续 appendix QA、导出和 rerun 都只能以过粗粒度运行

Consequences:

- 当前 smoke contract 对 `AI Agents in Action` 再升级一层：从 `15 章` 变成 `16 章`，明确拆出 `Appendix A Accessing OpenAI` 和 `Appendix B Python development`
- 前提是假设 `inline_heading` 型 appendix 标题在同一文档中具有稳定命名，并且不同附录标题之间不会高度重叠
- 适用边界当前只覆盖 `appendix + inline_heading`；显式 heading 的多附录已经由既有 `section_family` candidate 覆盖，其他 special family 仍维持更保守策略
- 验证方式是合成 fixture + 真实样本双回归：合成 `Appendix A/B` 必须切成两章，`AI Agents in Action` 必须在 page `327` 开出 `Appendix B`

---

## PDF-D-027 `Back Matter` 先在 `index -> tail body` 路径上升级成正式 family

Status: Accepted

Decision:

- `Back Matter` 先升级成正式 `page_family=backmatter` 和 `pdf_section_family=backmatter`
- 当前 promotion 只作用于真实已验证的 `index -> tail body` 路径，不直接推广到 `appendix -> tail body`
- 暂时不改变 translatability/export policy，只把结构事实接进 parser / smoke / summary contract

Why:

- 前一轮已经证明 `AI Agents in Action` 的尾部不该继续算在 `Index` 里，但当时仍只是 generic body chapter，review 和运营层看不到它是独立尾页家族
- 直接把所有 `appendix -> tail body` 也提升成 `backmatter` 风险过高，合成回归已经证明这会误伤“附录后的正常正文或附录续页”

Consequences:

- 当前 smoke contract 对 `AI Agents in Action` 再提升一层：page `340-346` 现在必须是 `page_family=backmatter`，最后一章 `pdf_section_family=backmatter`
- 前提是假设书尾在 `index` 之后重新回到普通 body 页时，更可能是 marketing / back-cover / catalog material，而不是正文主线
- 适用边界当前只覆盖 `index -> tail body`；是否扩到 `appendix -> tail body` 需要额外 cue，不能靠位置一刀切
- 验证方式是 synthetic backmatter fixture + 真实样本 smoke + corpus：backmatter chapter/page family 都要稳定可见，且 appendix-only fixture 不能被误切成 `Back Matter`

---

## PDF-D-013 页面家族先用 heading，其次用 content signature 和 continuation

Status: Accepted

Decision:

- `references / index` 的 headingless 页面允许使用 content signature 推断
- `appendix` 的无标题续页优先通过连续页继承，而不是要求每页重复 heading

Why:

- 真实出版 PDF 经常在 references/index 页省略重复标题
- appendix 页也常常只有第一页有 heading，后续是纯正文或表格说明

Consequences:

- page family metadata 需要保留 `family_source` 之类的推断来源
- 这套 heuristics 要继续在真实样本上收敛，不能过早宣传成“已稳”

---

## PDF-D-014 页面证据先保留 summary，不直接存 block 原文

Status: Accepted

Decision:

- 第一版 page-level evidence 只保留轻量 `pdf_page_evidence`
- 只记录 `pdf_pages` 和 `pdf_outline_entries` 这类结构事实，不把每个 block 的原文直接塞进 document metadata

Why:

- operator 需要的是“哪一页、什么家族、什么角色、出现了哪些恢复标记”，而不是在 metadata 里复制整份 PDF 文本
- block 原文已经在既有 block provenance 里存在，再重复存一份会放大存储和接口负担

Consequences:

- 当前 evidence 适合 summary、API、review routing 和 smoke 调试
- 如果后续 review package 需要更细定位，再单独增加 `pdf_page_blocks` 或 export-time evidence，而不是污染主 metadata contract

---

## PDF-D-015 chapter 级导出只携带切片后的 page evidence

Status: Accepted

Decision:

- review package 和 bilingual manifest 只携带当前 chapter 对应的 `pdf_page_evidence`
- 切片规则先基于 chapter 的连续页范围 `source_page_start / source_page_end`

Why:

- 审校包最常见的消费方式是按 chapter 处理，整本页面证据重复塞进每个 chapter artifact 会增加噪声和体积
- 当前 P1 chapter boundary 已经稳定保留页范围，因此先复用现有 metadata 比重新维护第二套 page mapping 更简单

Consequences:

- 当前实现默认假设 chapter 页范围连续
- 如果后续真实 PDF 出现大量非连续 chapter 页集合，再把 chapter evidence 从 range 升级成 page-set，而不是现在提前复杂化

---

## PDF-D-016 真实样本 smoke 要显式声明预期，而不是只保存报告

Status: Accepted

Decision:

- 真实 PDF smoke 不只生成报告，还要给每个样本写 expectation rules
- expectation 先覆盖 `profile / bootstrap / parse_summary` 的关键 contract

Why:

- 只有报告没有预期时，下一轮很容易退化成“人工目测 diff”，无法形成稳定回归门槛
- 当前阶段最重要的是确认边界是否正确，比如“应该拒绝的论文是否真的被拒绝”

Consequences:

- `run_pdf_smoke.py` 需要支持 expectation check
- `run_pdf_smoke_corpus.py` 需要能批量执行并返回 pass/fail summary
- smoke corpus 可以先很小，但每个样本都要有明确 contract

---

## PDF-D-017 `ocr_required=true` 必须直接视为高风险

Status: Accepted

Decision:

- 只要 profiler 判定文档 `ocr_required=true`，`layout_risk` 不允许落到 `low`
- `scanned_pdf` 直接记为 `high`，`mixed_pdf` 至少记为 `medium`

Why:

- 对当前 P1 来说，OCR-required 本质上就是“不支持的上游输入”，继续给低风险会误导 operator 和后续策略判断
- 风险语义首先服务于 fail-safe 分流，而不是只描述版式几何复杂度

Consequences:

- 真实扫描件 smoke 现在可以直接校验“unsupported and high-risk”这条 contract
- 后续如果 P2 引入 OCR 主路径，再重新拆分 `ocr_required` 和 `layout_risk` 的语义边界

---

## PDF-D-018 single-column research paper 先在 parser 恢复层拆标题和 References，不单独新建 lane

Status: Accepted

Decision:

- 对 low-risk、single-column、basic-extractor 的 research paper，不新增新的 `recovery_lane`
- 先在 parser 恢复层增量补两类 embedded heading：
  - 首页大块中的 document title
  - 末页合并块中的 broken `References` heading

Why:

- 新样本 `Forming Effective Human-AI Teams...` 暴露的问题不是布局风险，而是 basic extractor 把“标题 + 作者 + 摘要”和“References + 引文条目”压进了单个大块
- 这类问题更像 block recovery / heading recovery 缺口，而不是 intake 分流错误；如果为它单独造 lane，会把本来可复用的低风险文本 PDF 逻辑人为分叉

Consequences:

- `Refer ences ...` 一类 broken heading 现在由 parser 恢复层直接拆成真实 `heading + body`，并把 page 7 提升成 `page_family=references`
- 第一章 title 可直接继承从首页大块恢复出的真实论文标题，不再退化成 `Chapter 1`
- 前提是假设文档仍是单栏/低风险文本 PDF；适用边界是“作者/机构/摘要被合并进首页大块”的 single-column paper/report，不覆盖双栏 academic lane
- 验证方式是 synthetic regression + 真实样本 smoke：`Forming Effective Human-AI Teams...` 必须稳定落成“真实标题 + References”，且 `Attention Is All You Need` 的 medium-risk academic lane 不回退

---

## PDF-D-037 first-page title-like heading 先做轻量清洗，并优先与 document title 对齐

Status: Accepted

Decision:

- 当第一页 `body` 页面上出现 title-like `heading` block 时，允许在 block recovery 阶段做轻量标题清洗
- 当前清洗只做两件事：
  - 用 paper-title 归一化规则修复 `Y ou / Lear ning` 这类 broken words
  - 如果首章标题与 document title 重叠，则首章标题优先对齐到 document title

Why:

- `Attention Is All You Need` 已经证明，parser 主结构可以恢复，但第一页 heading 仍可能保留 `Attention Is All Y ou Need` 这类肉眼可见的标题噪声，直接影响最终 merged HTML 的可读性
- 这类问题发生在结构恢复之后，最适合在 title-like heading 的小范围内修，而不是把更激进的正文清洗扩散到所有 heading/body block

Consequences:

- 前提是假设第一页 title-like heading 和 document title 是最稳定的标题信号之一；适用边界当前只覆盖 `page_number == 1`、`page_family=body` 的 heading block
- 不会把这套清洗推广到普通章节 heading、appendix subheading 或正文 block，避免误拼正常短词
- 验证方式是 synthetic regression + 真实样本 parse/smoke：`Attention Is All Y ou Need` 现在必须收敛成 `Attention is All you Need` 章节标题，并在 heading block 中保留 `Attention Is All You Need`

---

## PDF-D-038 short academic paper 的 positioned parsing 只在短篇复杂定位页启用，并配 academic column-major ordering

Status: Accepted

Decision:

- `basic` extractor 新增 positioned text splitting，但只在 `page_count <= 24` 且单个 `BT ... ET` 段同时满足“左右列起点都明显存在 + 多行定位 + 足够多 text draw ops”时启用
- positioned mode 只改变短篇 academic paper 的 extractor 粒度；长书和普通 fallback 页面继续保留旧的单块提取 contract
- 在 recovery 阶段，`academic_paper` 且页面已命中清晰 multi-column signature 时，阅读顺序从默认 `y,x` 排序切到有限的 column-major 排序
- 当 fallback extractor 让首页 title bbox 漂移时，title-page signal 与标题恢复允许扫描整页 block，并用 document-title overlap 把真实标题补回主链路

Why:

- `Attention Is All You Need` 暴露的核心问题已经不只是 review policy，而是 `basic` extractor 把整页 `BT ... ET` 压成单块，导致双栏页没有可用的列级结构信号
- 直接把 positioned parsing 全局打开会立刻破坏长书主链路；真实回归已经证明，长书和 medium-risk 长文 PDF 仍需要维持旧的 fallback geometry contract
- 所以更稳的做法是：先把新能力收在“短篇 academic paper + 明显复杂定位段”这条边界里，再配一层 column-major ordering，专门提升英文论文

Consequences:

- 前提是假设当前最需要增强的是短篇英文论文，而不是全面重写所有 basic extractor 的几何恢复；适用边界是 `page_count <= 24`、text PDF、并且页面上确实存在左右列定位信号
- 这条策略故意不把 positioned parsing 扩散到长书，否则会把 `AI Agents in Action / LLMs in Production` 这类已稳定样本重新打成高风险
- 失效边界也明确：table / figure / equation-heavy 双栏页仍可能留下 `Scaled Dot-Pr` 一类 heading 残差，后续需要第三刀继续收敛
- 验证方式是 synthetic dual-column fixture + 全量 `tests/test_pdf_support.py` + 真实样本 smoke/corpus：
  - synthetic positioned academic paper 必须稳定 `medium + academic_paper`，并按左列优先恢复正文顺序
  - `Attention Is All You Need` 必须保持 `layout_risk=medium`、`recovery_lane=academic_paper`，且首章标题不再退化成 `Chapter 1`
  - `AI Agents in Action / LLMs in Production / Building AI Coding Agents / Forming Effective Human-AI Teams` 的原有主链路 contract 不得回退

---

## PDF-D-039 citation-heavy paper 与 oversized body block 先在 packet 层拆，不把稳定性问题推给 provider

Status: Accepted

Decision:

- 对 single-column research paper / report，一旦单个 translatable block 的句子数明显过大，就先在 packet builder 层做保守拆包
- 当前策略固定为：
  - `references / bibliography / works cited` 一类章节：按更小 packet 切分
  - 其他超大正文 block：只在句子数超过更高阈值时保守拆包
- 这条策略不改变 parser IR，也不改变 chapter/block/sentence 结构，只改变 translation packet 粒度

Why:

- `Forming Effective Human-AI Teams...` 的真实 live run 已经证明，single-column paper 即使结构恢复正确，也可能因为一个 references 大包或超大正文包把 provider 推到最不稳定的区域，最后以 “structured JSON output payload missing” 结束
- 这类问题更像 packet sizing 失衡，而不是 parser 错了；继续把问题留给 provider retry 或 review gate，只会让一键流程在最后一包失败

Consequences:

- 前提是假设当前最稳的优化点是“减小单包复杂度”，而不是进一步放宽 provider schema 或弱化 review/export contract
- 适用边界是 citation-heavy references chapter 与明显 oversized 的正文 block；普通正文与已有稳定长书路径不应因为这条策略被大量切碎
- 代价是 packet 数会增加，live run 的调度粒度更细；但这比在最后一包 terminal fail 更可控
- 验证方式是：
  - bootstrap 回归：references 大块与超大正文 block 都必须按阈值拆成多个 packet
  - 真实 live run：`Forming Effective Human-AI Teams...` 必须从 `v1/v2` 的 terminal-fail 收敛到 `v3` 的 `19/19` packet succeeded，并正式导出 `merged-document.html`

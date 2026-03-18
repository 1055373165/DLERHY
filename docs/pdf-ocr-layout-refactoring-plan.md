# PDF OCR & Layout Refactoring Plan

Last Updated: 2026-03-18

## Purpose

这份文档整合以下三类上下文，作为 book-agent 面向英文 PDF 书籍、英文论文翻译的下一阶段升级计划：

- `~/.claude/plans/iterative-dreaming-volcano.md` 中的 OCR 与 Layout 六阶段改造设想
- 仓库内既有 PDF 计划、状态与决策文档
- 当前代码实现里已经完成的 Phase 1 基础设施与仍然存在的真实边界

它的目标不是重复 `docs/pdf-support-implementation-plan.md` 的 P1 文本 PDF 计划，而是在 P1 基础已落地后，把 OCR、图像/公式/版式恢复这条后续路线重写成一份和当前代码一致的执行稿。

## Consolidated Context

### External plan summary

`~/.claude/plans/iterative-dreaming-volcano.md` 提出的核心判断是成立的：

- 现有 PDF 管线只真正支持 text PDF
- 图片/图表在提取阶段就会丢失
- 双栏和复杂排版目前只是“检测到风险”，不是“真正恢复了阅读顺序”
- 学术论文里 figure / table / equation-heavy 页面仍是主缺口
- 正确做法应当是先扩展统一 IR，再把 OCR、layout analysis、protected artifact export 接进既有主链路

### Internal docs summary

仓库内相关文档给出了更精确的当前边界：

- `docs/pdf-support-implementation-plan.md`
  - 已完成的主线是 `P1-A/P1-B` 文本 PDF 结构恢复
  - OCR 与 scanned PDF 仍明确属于后续阶段
- `docs/pdf_status.md`
  - 当前对外 contract 仍是 `p1_text_pdf_bootstrap`
  - 低风险文本 PDF 与一部分 medium-risk academic paper 已能进入正式翻译/导出
  - 扫描 PDF、OCR、复杂图表/公式保护仍未开始
- `docs/pdf_decisions.md`
  - 已冻结“先分流、再恢复、fail-safe、风险显式传播”的原则
  - `ocr_required=true` 当前仍表示 unsupported upstream input
- `docs/pdf_backlog.md`
  - OCR、扫描主路径、双栏 robust support 仍在 later 阶段

### Current code baseline

与本次升级计划直接相关的基础设施已经落地：

- `src/book_agent/domain/enums.py`
  - `SourceType.PDF_MIXED`
  - `BlockType.FIGURE / EQUATION / IMAGE`
- `src/book_agent/domain/block_rules.py`
  - `FIGURE / EQUATION / IMAGE` 已按 protected artifact 处理
- `src/book_agent/services/bootstrap.py`
  - ingest 已能把 `mixed_pdf` 分流到 `pdf_mixed`
  - parse 已给 `pdf_scan` 留出 OCR 错误提示路径
  - segmentation 已支持新 block type 不再按普通段落切句
- `src/book_agent/domain/models/document.py`
  - `DocumentImage` 数据模型已存在
- `alembic/versions/20260317_0006_document_images_and_new_enums.py`
  - 枚举扩展和 `document_images` 表 migration 已存在

这意味着原计划里的 Phase 1 已基本完成，但仅完成了“schema 与主链路可接受新类型”的基础层，并未形成端到端 OCR/image/layout 能力。

## Current Gaps That Still Matter

### Gap 1: PDF image blocks are still dropped at extraction time

`PyMuPDFTextExtractor` 当前仍然只保留 `type == 0` 的文本块，非文本 block 会被直接跳过。  
这也是为什么虽然 `FIGURE / IMAGE` 枚举和 `DocumentImage` 已存在，当前 text PDF 里的图片仍不会进入主链路。

### Gap 2: `pdf_mixed` is routed, but not truly supported

当前 ingest 已能把 mixed PDF 单独标成 `pdf_mixed`，但 parse 仍然复用现有 `PDFParser` 文本提取路径。  
这意味着：

- mixed PDF 中有文本层的页面仍可能被处理
- 纯扫描页不会真正 OCR
- 文档被“允许进入系统”和“真正支持 mixed OCR”仍不是一回事

### Gap 3: `DocumentImage` only exists at schema level

目前 `DocumentImage` 还没有 producer / repository / export consumer：

- parser 不写入 `document_images`
- review/export 不读取 `document_images`
- 生命周期、存储路径、清理策略都还没定义

所以后续设计里不能把它当成“已经可用的图像资产系统”，而应当把它当成一个已就位但未接线的数据表。

### Gap 4: export only knows how to copy EPUB assets

当前 export 里的图片复制逻辑只对 EPUB `image_path` 生效。  
PDF 若开始产出 figure/image asset，必须补一条新的资产落盘与引用链路，否则 FIGURE block 即使被恢复出来，也只能以 metadata 占位形式导出。

### Gap 5: OCR validation corpus is not yet aligned to the target use case

当前真实 smoke corpus 的 scanned 路径更多是在验证“unsupported and high-risk”这条 fail-safe 契约。  
对于“英文书籍、英文论文翻译”这个目标，下一阶段必须新增英文扫描书、英文扫描论文、英文 mixed PDF 的真实 corpus，否则 OCR 方案会缺少正确的验收基线。

## Refactoring Goals

升级路线继续沿用既有 PDF 决策，不另造第二套翻译系统：

1. OCR / layout / figure recovery 只扩展 ingest、parse、review、export 的上游能力。
2. 统一落到现有 `document -> chapter -> block -> sentence -> packet -> review -> export` IR。
3. 继续 fail-safe；不因为接入 OCR 就把高风险结构误报成“已支持”。
4. 对英文 technical book 与 academic paper 分别建立验收样本，而不是只靠 synthetic fixture。
5. 先解决“结构是否可靠”，再追求“图像/公式是否漂亮渲染”。

## Phase Plan

### Phase 1: Foundation

Status: Done

已完成事项：

- [x] `BlockType` 扩展：`figure / equation / image`
- [x] `SourceType` 扩展：`pdf_mixed`
- [x] block rules / segmentation 对新 block type 兼容
- [x] bootstrap 路由为 mixed / scan 预留入口
- [x] `DocumentImage` 模型与 migration
- [x] Phase 1 回归测试未破坏现有 text PDF 主链路

当前结论：

- 这一步的设计是合理的，且与现有 P1 文本 PDF 主链路兼容
- 但它只是“铺路”，还不是 OCR / layout 能力本身

### Phase 2: Layout Analysis For Text PDF

Goal:

- 先增强 text PDF，解决双栏英文论文和图文混排英文书最明显的结构问题
- 不把 Phase 2 和 OCR 绑定死；text PDF 的 layout hardening 应可独立交付

Recommended deliverables:

- 新增 `LayoutAnalyzer` protocol，与具体引擎解耦
- 新增 `LayoutRegion / PageLayout` 结构，表达 `text/title/figure/table/equation/caption/header/footer/list`
- 新增 `LayoutEnhancedRecoveryService`
  - 输入：现有 `PdfExtraction` + layout regions
  - 输出：仍是 `ParsedDocument`
- 双栏阅读顺序增强
  - 优先在 `academic_paper` lane 落地
  - 先覆盖 figure / equation 周边最容易错序的页面
- figure region 二次提取
  - 从原 PDF 页面裁剪图片
  - 生成 `ParsedBlock(block_type=FIGURE or IMAGE)`
  - 为后续 `DocumentImage` 持久化准备 metadata

Design constraints:

- 不应直接“用 layout 输出完全替代现有文本恢复”，否则会把已经稳定的 P1 逻辑一并推翻
- 应采用“layout as evidence override”的模式：优先保留现有 recovery，再让 layout 只覆盖 heuristics 最薄弱的地方
- figure/caption 必须定义关联关系，避免 caption 被保留、figure 被重复生成，或反过来 figure 被抽出后正文仍残留重复文本

Acceptance:

- 真实英文双栏论文的阅读顺序明显优于当前 academic paper hardening
- text PDF 中的典型插图不再完全丢失
- 未安装 layout 引擎时，现有 text PDF 路径仍可回退运行

### Phase 3: OCR For Scanned And Mixed PDF

Goal:

- 让 scanned PDF 与 mixed PDF 进入真正可执行的解析主路径
- 让 OCR 成为“支持的输入模式”，而不再只是 fail-safe reject

Recommended deliverables:

- 新增 `OcrPdfTextExtractor`
  - 文本页继续用 PyMuPDF
  - 无文本页或文本极稀疏页走 OCR
- 新增 `OcrPdfParser`
  - OCR 文本恢复
  - layout analysis
  - 结构恢复
  - confidence 传播
- 在 `ParseService` 中把 `PDF_SCAN / PDF_MIXED` 接到 OCR 主路径
- 为 page/block 增加 OCR 来源字段
  - `page_modality`: `text`, `ocr`, `mixed`
  - `ocr_engine`
  - `ocr_confidence`

Required design adjustment:

当前 `ocr_required` 的语义是“P1 unsupported input”。  
当 OCR 真正接入主路径后，需要拆成两个概念：

- `needs_ocr`: 输入形态
- `support_status` 或 `parse_mode`: 当前是否已有可执行 OCR 主路径

否则：

- `pdf_status.md` 里的风险/支持边界会变得含混
- review/operator 无法区分“因为复杂而高风险”和“因为系统未支持而阻断”

Acceptance:

- 一份真实英文扫描论文可以从 ingest 走到 parse 并产出可 review 的章节
- 一份真实 mixed PDF 可以同时处理文本页和扫描页
- OCR 置信度能传播到 block / sentence / chapter 层

### Phase 4: Protected Artifacts, Equation, Table, Export Assets

Goal:

- 让 figure / equation / table 不再只是“识别了但保不住”，而是能稳定进入导出与 review

Recommended deliverables:

- 定义 parser 与 `DocumentImage` 的接线方式
  - 谁负责写入
  - 何时写入
  - 失败时是否允许缺省运行
- equation 处理
  - 第一阶段先允许 equation 以 protected artifact 原样保留
  - LaTeX 化作为增量能力，不强绑在 OCR 首发版本中
- table 处理
  - 文本 PDF 与扫描 PDF 分开设计
  - 避免把“table OCR”与“普通 OCR”一起塞进第一版
- export 资产链路
  - PDF figure/image 的落盘目录
  - manifest 引用方式
  - merged HTML / bilingual HTML 如何引用本地导出资产

Recommended delivery order inside this phase:

1. figure/image 资产落盘与导出
2. protected artifact review evidence
3. equation 原样保护
4. table 结构增强
5. optional LaTeX

Acceptance:

- figure block 可以在 merged/bilingual HTML 中看到真实图片，而不是只有 metadata
- equation/table 至少能安全保留，不污染正文翻译

### Phase 5: QA, Observability, English Corpus Validation

Goal:

- 把 OCR/layout 的不确定性纳入现有 QA / rerun / export gate
- 建立真正针对英文书籍、英文论文的验收样本

Required deliverables:

- OCR/layout-specific review issues
  - low OCR confidence
  - ambiguous column order
  - figure/caption mismatch
  - equation/table recovery failed
- 文档级摘要
  - OCR 页数
  - OCR 平均置信度
  - 低置信度页列表
  - figure/equation/table 识别统计
- 英文 corpus
  - 低风险英文技术书 text PDF
  - medium-risk 英文 academic paper
  - 英文 scanned book
  - 英文 scanned paper
  - 英文 mixed PDF

Acceptance:

- 不再只验证“scanned 会不会被拒绝”，而是验证“英文 scanned/mixed 会不会被正确恢复”
- review package 能给 operator 明确指出 OCR/layout 风险页

## Recommended Next Implementation Order

建议按下面顺序推进，而不是把 six-phase 计划一次性并行展开：

1. 先做 text PDF 的 layout enhancement，不先碰 OCR 主路径
2. 同步补 `DocumentImage` 的最小接线方案与 PDF export asset strategy
3. 再做 `OcrPdfTextExtractor + OcrPdfParser`
4. 之后才上 equation/table 深化
5. 最后补 OCR 质量摘要、自动 issue、operator 驾驶舱指标

原因：

- 当前最稳定的主链路仍是 text PDF
- academic paper 的最大真实缺口是 reading order 和 figure/equation 周边页面，而不是“完全没有 OCR”
- 若先做 OCR 而不补 export asset / review evidence，系统会出现“解析到了，但无法交付或无法审”的半成品状态

## Design Assessment

### Overall assessment

总体判断：方案方向是合理的，但原始六阶段计划需要按当前代码现实做三处收敛，才能从“好想法”变成“可实现方案”。

合理之处：

- 先做 schema / enum / route foundation，再接 OCR 与 layout，是正确分层
- 坚持统一 IR，不另做 PDF 专用翻译链，是正确架构方向
- 先 text PDF layout，后 scanned/mixed OCR，符合当前代码成熟度
- 把 figure / equation / image 当 protected artifact，而不是急于直接翻译，也是合理策略

### Adjustments required before implementation

#### 1. `pdf_mixed` 目前只是 routing，不是 capability

文档必须明确：

- 当前 `pdf_mixed` 是“可识别、可标注”
- 不是“已支持 mixed PDF OCR”

否则 operator 和后续开发都会误读当前完成度。

#### 2. `DocumentImage` 不能被当成已经完工的资产层

它现在只有 schema，没有写入、查询、导出闭环。  
后续计划必须补：

- repository
- parser 写入时机
- 导出读取逻辑
- 清理/重跑策略

#### 3. OCR 接入前必须先拆开风险语义和支持语义

现在：

- `ocr_required=true` 接近“系统不支持”

未来：

- `needs_ocr=true` 只是输入事实

如果不改，P2 以后 `layout_risk`、review issue、operator summary 会混在一起，难以判断真实问题。

#### 4. 英文 OCR 验收样本必须尽快建立

本项目目标是英文书籍和英文论文翻译。  
如果没有英文 scanned/mixed corpus，OCR 方案再完整，也缺少真正能决定是否可上线的验收依据。

## Decision Summary

这份计划的建议结论是：

- 保留原 `iterative-dreaming-volcano` 的总体方向
- 认可已完成的 Phase 1 基础设施
- 下一步优先做 Phase 2 的 text PDF layout enhancement
- 在 Phase 3 启动前先补 `DocumentImage` 接线、PDF export asset strategy、OCR 语义拆分
- 把英文 scanned/mixed corpus 建成正式验收基线，而不是继续只用“unsupported OCR”样本做 smoke

如果按这个顺序推进，`docs/pdf-ocr-layout-refactoring-plan.md` 对当前代码来说是合理且可执行的；如果直接把 OCR、layout、equation、table、export 一次性打包推进，实施风险会明显偏高。

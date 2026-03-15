# PDF Backlog

Last Updated: 2026-03-15

## Purpose

这份文档只追踪 PDF 路线任务，不混入 EPUB 主线或通用平台事项。

优先级规则：

1. 先保证结构恢复正确，再扩覆盖面
2. 先做会影响整条链路稳定性的上游事项，再做质量锦上添花
3. 无法稳定自动恢复时，优先显式打风险和阻断，而不是静默放行

## PDF P0

目标：把“低风险文本 PDF 可进入主链路”这件事做稳，并让状态可观测、可复跑、可审校。

### Must Ship

- [x] PDF intake / source_type 分流
- [x] `pdf_profile` 与 `layout_risk` 判型
- [x] 文本型 PDF geometry-aware 提取
- [x] 简单阅读顺序恢复
- [x] 页眉页脚剔除
- [x] 段落合并与跨页断词修复
- [x] PDF provenance 落到 block / sentence 主链路
- [x] chapter 级 `parse_confidence / risk_level / structure_flags`
- [x] review 接入结构风险 issue 和 `REPARSE_CHAPTER`
- [x] CLI / API / summary / frontend 暴露 PDF 基本状态
- [x] high-risk layout 拒绝放行
- [x] 章节独立 rerun 仍然成立

### Hardening Before Declaring Stable

- [x] 建立 manifest-driven smoke 基础设施，可挂接真实 text-PDF corpus
- [x] 扩充真实 text-PDF smoke corpus，而不只依赖合成 PDF fixture
- [x] 增加一个真实低风险 text PDF 样本，补齐“可放行路径”验证
- [x] 候选扫描工具化：自动遍历常用目录、排序候选、导出 manifest-ready 建议
- [ ] 跑一轮真实技术书 text-PDF smoke，确认 coverage / rerun / export 无静默异常
- [ ] harden long-book chapter recovery：在更多真实长书上复现 `Front Matter + chapter tree`，而不是只对单一本书成立
- [x] second long-book smoke：新增 `LLMs in Production`，把低风险长书 pass-path 从 1 本扩到 2 本
- [x] chapter-intro title cleanup v1：压掉 `LLMs in Production` 中最明显的 PDF escape / spaced-word / sentence-tail 噪声
- [x] 清理 valid chapter 内的 isolated `content_signature` 假阳性，避免误打 `page_family_index/references`
- [x] 为 `basic` extractor 暴露 provenance，并把 fragment-only 长文本 PDF 从 `high` 收敛到 `medium`
- [x] short academic paper intake 第一刀：把 `Attention Is All You Need` 一类短篇英文论文从 `high/reject` 收敛到 `academic_paper` medium-risk lane
- [x] medium-risk 样本的 `references -> appendix` 边界恢复
- [x] long-book 样本的 `appendix -> index -> back matter` 边界恢复
- [x] long-book 样本的 `Appendix A / Appendix B` 多附录切分
- [x] `Back Matter` formal family：真实长书中的 `index -> tail body` 正式提升为 `page_family / section_family`
- [x] `Back Matter policy v2`：`backmatter` 默认 source-only，并在 export 中走 block-level preserve contract
- [ ] 决定何时把 public contract 从 `p1_text_pdf_bootstrap` 升级为更准确的 recovery 阶段
- [ ] 为 PDF failure modes 建立更直接的 operator checklist

## PDF P1

目标：从“能处理简单文本 PDF”推进到“多数真实技术书 PDF 可稳定恢复结构”。

### Now

- [x] 页面家族分类：`toc / frontmatter / appendix / index / references`
- [x] TOC page-number reconciliation：处理 printed page number 和 PDF page index offset
- [x] chapter boundary hardening：避免 appendix / index / toc 混入正文章节树
- [x] footnote recovery v2：跨页脚注、复杂 anchor、降低 orphan 误报
- [x] review evidence 增强：把 PDF 结构问题给出更可操作的 page/block 证据
- [x] 复杂页面家族分类：无明确 heading 的 references / index / appendix
- [x] page-level evidence artifacts v1：`pdf_page_evidence.pdf_pages / pdf_outline_entries`
- [x] footnote relocater v3：复杂多段 / 多页脚注的更稳归位（第一刀：页底/续页页顶 markerless 段落归位）

### Next

- [x] page-level debug artifacts v2：补 review-export page/block 定位信息
- [x] review package 增加 page-level PDF evidence
- [x] content-signature family heuristics hardening：消除首个真实样本中的 `references / index` 假阳性
- [x] inline special-section heading hardening：恢复 `INDEX ...` / `Appendix A ...` 合并块
- [x] tail-body-after-special boundary：避免 appendix / index 吞掉尾部 marketing/backmatter
- [x] multi-appendix split：把 `Appendix A / B / ...` 从单一 appendix chapter 中拆开
- [x] medium-risk appendix 内 finer-grained section/title recovery（第一刀：intro-page appendix subchapters）
- [x] special-section subheading policy hardening（第一刀：appendix continuation 页上的顶层子标题）
- [x] nested appendix subheading policy（第一刀：先进入 page/review evidence，不直接切 section tree）
- [x] review/export evidence 增强：让 `backmatter / appendix / index` 的 preserve policy 更易审校
- [x] backmatter cue hardening：`appendix -> tail body` 在 explicit cue 下可升级为 `backmatter`
- [ ] nested appendix section-tree upgrade：决定 `K.2.5` 这类嵌套小节何时从 evidence 升级为正式 section tree
- [ ] chapter-intro title cleanup v2：继续清理 `Adeep / Dataislikegarbage / canyougo` 这类更深层断词噪声
- [ ] 更复杂的 paragraph reconstruction：quote / list / inset text 边界更稳
- [ ] academic paper section recovery v1：让 short paper 不止恢复 `body + references`，而是补出更稳的 section heading tree
- [ ] medium-risk PDF 的更细粒度放行策略，而不是简单“可进但高风险”
- [ ] frontmatter / appendix / index 的独立导出或独立 chapter policy
- [ ] backmatter cue hardening v2：决定是否接受更弱的 marketing/back-cover cue，还是继续停留在 explicit-cue policy
- [ ] footnote relocater v4：交错双脚注 / 多锚点同页 / 非数字符号体系

### Later

- [ ] OCR 置信度传播
- [ ] 扫描型 PDF 主路径
- [ ] 双栏 robust support
- [ ] 图表 / 公式 / 代码的更强保护
- [ ] 高风险页自动送审

## Suggested Order

1. academic paper section recovery v1
2. nested appendix section-tree upgrade
3. chapter-intro title cleanup v2
4. backmatter cue hardening v2
5. footnote relocater v4

## Current Working Set

如果新一轮要继续推进，默认先做这 3 个：

1. academic paper section recovery v1
2. nested appendix section-tree upgrade
3. chapter-intro title cleanup v2

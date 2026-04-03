# Mainline Progress

Last Updated: 2026-04-03 12:24 +0800
Status: translate-agent-readiness-mainline
Rule: 先用 benchmark 证明可放量，再决定是否扩大整本运行范围。

## 1. 当前主线定义

当前真正的主线不是继续推进 runtime self-heal，也不是继续做独立的 Forge governance hardening。

当前主线是：

1. 让 translate agent 在 `PDF 书籍 / EPUB 书籍 / PDF 论文` 上具备可证明的高保真翻译 readiness。
2. 用 benchmark 而不是主观感觉来决定“是否可以开始整本运行”。
3. 在整本运行阶段默认使用 `slice-first`，而不是直接 blind full-document rollout。

一句话概括：

> 当前主线 = `translate-agent 高保真翻译 readiness` + `benchmark-backed whole-document go/no-go`

## 2. 已完成的主线能力

### 2.1 Benchmark / certification 基础设施

以下能力已经进入可复用状态：

- benchmark manifest
- gold labels
- execution summary
- scorecard
- lane verdict generation
- readiness certification report

### 2.2 EPUB / PDF parser hardening

当前主线已经补齐了这些关键 parser/export 能力：

- EPUB heading level 恢复
- EPUB figure/caption linkage
- EPUB protected artifact 识别
- PDF 黏连标题拆分
- academic first-page abstract/frontmatter 拆分
- appendix heading level 恢复
- figure / table / equation / caption linkage
- 高 artifact 密度论文的受保护 artifact 处理
- PDF 原图优先提取 + 高分辨率 fallback 的正式主链路接入
- PDF asset provenance 现在会把“向量页/不可抽原图页”与“真正的原图 parity 缺口”区分开
- fragmented composite PDF figure 现在也会被视作 noncanonical original opportunity，而不是被误算成 parity miss

### 2.3 当前认证结果

当前 benchmark 结论已经是：

- `L1` `EPUB-reflowable-tech-book` -> `go`
- `L2` `PDF-text-tech-book` -> `go`
- `L3` `PDF-text-academic-paper` -> `go`
- `L6` `High-artifact-density-paper` -> `go`
- `overall` -> `go`

对应权威产物：

- `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-execution-summary-current.json`
- `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-scorecard-current.json`
- `/Users/smy/project/book-agent/artifacts/review/translate-agent-lane-verdicts-current.json`
- `/Users/smy/project/book-agent/artifacts/review/translate-agent-readiness-certification-current.md`

## 3. 当前进度判断

当前 translate-agent 主线已经完成“放量前 readiness 认证”这一阶段。

这意味着：

- 当前代码已经不再停留在“局部样例翻得不错”
- 当前已经有可追溯的 benchmark 证据，支持受控整本运行
- 当前最关键的问题已从“能不能识别 / 能不能高保真”转成“如何安全放量、如何继续扩大认证边界”

## 4. 下一阶段主线 Todo

### P0

- 以 `slice-first` 模式开始受控整本书 / 整篇论文运行
- 扩展 benchmark corpus，把 readiness claim 从当前 9 样本推进到更多文档家族

### P1

- 如果未来文档暴露出真实的 extractable-original miss，再重新打开 asset parity hardening
- 补 mixed / scanned / unusual-layout 样本，明确哪些 lane 应该继续 `go`，哪些应该只给降级支持
- 把整本运行后的抽样复核结果继续并入 readiness truth

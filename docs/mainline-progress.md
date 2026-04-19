# Mainline Progress

Last Updated: 2026-04-03 21:10 +0800
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

- 把 expansion wave1 的 measured `go` 正式并入 active readiness truth
- 先标注并探测第一个 `L4` OCR-heavy 样本
- 在 `L4` 与额外 EPUB family probe 之间选择下一条最低成本的 expansion slice
- 仅当后续 spot-check 暴露具体 lane 问题时，再对已认证 lane 做 deeper resume

### P1

- 如果未来文档暴露出真实的 extractable-original miss，再重新打开 asset parity hardening
- 补 mixed / scanned / unusual-layout 样本，明确哪些 lane 应该继续 `go`，哪些应该只给降级支持
- 把整本运行后的抽样复核结果继续并入 readiness truth

## 5. 最新 pilot 进展

- 生成了当前 pilot pack：
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-pack-current.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-pack-current.md`
- 已完成四条 certified lane 的首轮 pilot：
  - `L1`: `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-epub-hands-on-llm-001/report.json`
    - `run_id = a7a94e52-fdac-4678-a4bb-ba16ab17583f`
    - `translated_packet_count = 15`
  - `L2`: `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-pdf-agentic-design-001/report.json`
    - `run_id = 57715183-7bf7-4249-81e2-1bc12411b147`
    - `translated_packet_count = 10`
  - `L3`: `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-pdf-attention-paper-001/report.json`
    - `run_id = 4660a21a-e925-419d-99df-8ab03e53e4eb`
    - `translated_packet_count = 8`
  - `L6`: `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-pdf-epiplexity-001/report.json`
    - `run_id = bc66c33f-4a61-4d13-bbe5-08023c54c5a9`
    - `translated_packet_count = 6`
- 上述四条 pilot 都是 `run.status = paused` 且 `stop_reason = pilot.slice_target_reached`
- 已生成 pilot summary：
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-summary-current.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-summary-current.md`
- 当前默认下一刀已经切换为 `benchmark corpus expansion`
- 已生成 corpus expansion draft：
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-corpus-expansion-draft.yaml`
- 首批 expansion pair 已经 annotated 并完成 parser/export probe：
  - `/Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-building-ai-agents-001.json`
  - `/Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-173140-001.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-expansion-wave1-execution-summary.json`
- 当前 wave1 结论已经变成 `overall go`，所以主线已经从“先修 measured expansion blockers”切回“继续扩 corpus / 探测新 lane”
- 已修复 slice-first 上限在并发下的 overshoot 缺口，并用严格上限 smoke 证明新逻辑生效：
  - `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-epub-hands-on-llm-001-strict-cap-smoke/report.json`
  - `max_completed_packets = 2`
  - final translated packet count = `2`

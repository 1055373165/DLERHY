# Mainline Progress

Last Updated: 2026-04-03 12:05:01 +0800
Status: high-fidelity-document-translation-foundation
Rule: 先写清楚 parser truth，再谈高保真 exporter；每个 verified batch 后都做 continuation scan。

## 1. 当前主线定义

当前 run 是 Forge v2 的 `change_request` 接管。

旧主线 `runtime-self-heal-mainline` 已经完成并保留为 verified baseline。
新的 active mainline 是：

`high-fidelity document translation foundation`

当前不再把“更多 runtime self-heal hardening”当作第一优先级，而是转向：

1. Canonical Document IR
2. parse revision persistence
3. execution projection provenance
4. 后续 source-preserving EPUB export / risk-aware PDF evolution 的共同真源

## 2. 当前批次

- latest passing feature id: `F006`
- current_step: `batch-66_in_progress`
- active_batch: `batch-66`
- authoritative_batch_contract: `.forge/batches/batch-66.md`
- expected_report_path: `.forge/reports/batch-66-report.md`

## 3. 已验证的 batch-64

batch-64 已经完成并验证：

- `.forge` 主线与 handoff truth 已改写到高保真文档翻译底座
- parse revision ORM / sidecar persistence 已落地
- canonical IR 类型和 parse IR service 已落地
- parse IR repository 已落地
- block/sentence provenance 已补 parse revision / canonical node identity
- targeted unittest 与 py_compile 已通过

## 4. 已验证的 batch-65

batch-65 已经完成并验证：

- 引入 `zh_epub` source-preserving export path
- 基于原始 EPUB archive / XHTML patch 译文
- 保留 nav、anchors、ids、内部链接与 archive 结构
- 做 targeted export tests 与 baseline 校验并通过

## 5. 当前 batch-66 范围

batch-66 是 continuation scan 后的最近 slice：

- 持久化 PDF page 或 zone extraction intent
- 把 extraction-mode risk reasons 写入 canonical truth
- 做 targeted PDF parse-IR tests 与 baseline 校验

## 6. 已知约束

- 单 checkout，单 live worktree
- 不回退用户现有改动
- 第一阶段走 sidecar-first，不立即做 page/line/span 全量表化
- EPUB 长期主路径是 source-preserving patch export
- PDF 近期真实承诺是中文阅读版，不是假装 1:1 facsimile

## 7. 当前 baseline

- `bash .forge/init.sh`
- smoke warning hygiene validated
- governance contract validated

## 8. Forge v2 执行契约

- 继续使用 `mainline_required / mainline_adjacent / out_of_band` 处理 branch work
- 继续执行 continuation scan
- 继续要求 stop-legality 从 file truth 审计

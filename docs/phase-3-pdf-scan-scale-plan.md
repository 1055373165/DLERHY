---
description: Lane-B execution baseline for Phase 3 larger-corpus PDF_SCAN stability, telemetry, and recovery under parallel-autopilot.
status: lane-b-mdu-16.1.3-complete
lane: lane-pdf-scan-scale
last_updated: 2026-03-22
---

# Phase 3 Lane B：Larger-Corpus `PDF_SCAN` 执行基线

## 1. Objective

本 lane 的目标不是继续证明 `PDF_SCAN` “能不能跑”，而是把它从 Phase 2 的最小可行 acceptance 扩展到更大 corpus 的稳定性、性能和恢复能力。

`MDU-16.1.1` 的交付目标是三件事：

1. 锁定代表性 larger-corpus 样本组
2. 锁定新的 telemetry baseline
3. 锁定当前必须显式承认的恢复边界

## 2. Locked Representative Corpus

### Tier A：Full-Book Bootstrap Stress Lineage

主样本：

- `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount`

锁定理由：

- 它是 scanned 全书 bootstrap 压力样本
- 报告中确认 `source_type=pdf_scan`
- 报告中确认 `page_count=458`
- 数据库体量已进入 larger-corpus 量级：`1 document / 97 chapters / 2170 translation_packets / 2171 translation_runs`
- 该 lineage 暴露的是 provider exhaustion，而不是 OCR 缺失

本样本代表：

- 大规模 scanned bootstrap 压力
- 长时运行成本
- OCR 与下游翻译失败的边界分离

### Tier B：Retry / Resume Lineage

主样本：

- `artifacts/real-book-live/deepseek-agentic-design-book-v12-retry-after-balance`

锁定理由：

- 它直接复用 `v11` 的数据库 lineage
- 报告显式携带 `resume_from_status=failed` 和 `retry_from_status=failed`
- 证明 larger-corpus scanned run 不是“一次性跑挂就失真”，而是需要被当作真实 resume/retry 对象治理

本样本代表：

- retry / resume 恢复语义
- 失败 lineage 的延续性
- larger-corpus 场景下的 run recovery 边界

### Tier C：Slice Repair / Merge Lineage

主样本组：

- `artifacts/real-book-live/deepseek-agentic-design-book-v21-slice-1`
- `artifacts/real-book-live/deepseek-agentic-design-book-v21-slice-2`
- `artifacts/real-book-live/deepseek-agentic-design-book-v21-slice-3`
- `artifacts/real-book-live/deepseek-agentic-design-book-v21-slice-4`
- `artifacts/real-book-live/deepseek-agentic-design-book-v22-merged-slices`
- `artifacts/real-book-live/deepseek-agentic-design-book-v23-final-prose-repair`

锁定理由：

- 这条 lineage 代表“不是一次全书从头跑到尾”，而是分 slice 修复、合并、再做末端 repair 的 larger-corpus 现实路径
- `v21-slice-2` 已提供可量化 repair 证据：
  - `candidate_total=145`
  - `candidate_selected=36`
  - `repaired_chain_count=33`
  - `failed_candidates=3`
  - 失败类型均为 `The read operation timed out`
- `v23-final-prose-repair` 进一步证明 merge 后仍需要小规模 final repair：
  - `candidate_total=6`
  - `repaired_chain_count=6`
  - `failed_candidates=0`

本样本组代表：

- larger-corpus repair throughput
- slice-based 恢复策略
- merge 后 residual issue 的闭环难度

### Tier D：Readable Rescue End-State

主样本：

- `artifacts/real-book-live/deepseek-agentic-design-book-v28-readable-rescue-titlefix`

锁定理由：

- 它代表 scanned larger-corpus 不是只追求“run 结束”，而是需要看到可读性交付终态
- 当前目录保留的是最终 DB 与交付报告，而不是 bootstrap 原始报告
- 这意味着 larger-corpus lane 必须接受“不同 lineage 负责不同生命周期证据”的现实

本样本代表：

- downstream readable rescue
- final artifact readability
- title/readability rescue 的后期治理路径

## 3. Telemetry Baseline

从 `MDU-16.1.1` 开始，larger-corpus `PDF_SCAN` 的新 run 至少应在 archived `report.json` 中稳定保留以下字段：

- `started_at`
- `finished_at`
- `duration_seconds`
- `stage`
- `ocr_status`
- `ocr_progress`
- `database_path`
- `db_counts`
- `work_item_status_counts`
- `translation_packet_status_counts`
- `error.stage`
- `error.class`
- `error.message`
- `resume_from_run_id`
- `resume_from_status`
- `retry_from_run_id`
- `retry_from_status`

### Telemetry Compatibility Rule

历史样本允许缺失这些字段，但必须被显式标记为 `legacy-report-generation`，不能假设它们已经满足 Phase 3 baseline。

对当前锁定样本的判断：

- `v11 / v12`：属于 legacy report generation
  - 仍保留 `started_at / finished_at / duration_seconds`
  - 但缺失 `stage / db_counts / ocr_status / ocr_progress / work_item_status_counts / translation_packet_status_counts`
- `v21 / v23 / v28`：属于 downstream repair/export evidence generation
  - 更适合承载 repair throughput 和 export readability 证据
  - 不能替代 bootstrap / OCR telemetry

## 4. Locked Recovery Boundaries

`MDU-16.1.1` 明确锁定以下恢复边界，后续实现不得假装这些问题不存在：

### Boundary A：Legacy Report Schema Drift

- 历史 full-book scanned 样本不一定带有当前 telemetry 字段
- larger-corpus lane 需要兼容旧报告代际，而不是只支持最新 `report.json`

### Boundary B：Legacy DB Schema Drift

- 当前 sampled DB lineage 仍没有 `documents.title_src / title_tgt` 列
- 文档级标题不能假设可直接从 DB 列稳定读取

### Boundary C：Failure Taxonomy Must Stay Split

- `v11` 暴露的是 provider balance / downstream runtime failure
- 它不能被错误归因为 OCR/bootstrap 失败
- `v21-slice-2` 的 `The read operation timed out` 属于 repair/read path failure，也不能混进 OCR failure bucket

### Boundary D：One Artifact Family Does Not Carry All Signals

- bootstrap telemetry、retry lineage、slice repair、final readability 分布在不同目录代际
- Phase 3 larger-corpus baseline 必须接受“多类工件共同构成真实证据面”

## 5. Execution Consequence

`MDU-16.1.1` 已完成。

下一步 `MDU-16.1.2` 的焦点应明确收敛为：

1. 将 legacy report generation 与 current telemetry baseline 的兼容策略落实到 scanned runtime/reporting 路径
2. 收紧 larger-corpus retry / resume / failure routing
3. 明确 provider exhaustion、OCR failure、repair timeout 三类失败的分桶与恢复动作

## 6. MDU-16.1.2 Completion Snapshot

Implemented in this round:

- added shared reporting helpers in `scripts/real_book_live_reporting_common.py`
  - current report generation now explicitly declares `current-runtime-report-v2`
  - legacy reports are classified as `legacy-report-generation`
  - report compatibility is now derived structurally instead of being guessed from operator memory
- hardened `scripts/run_real_book_live.py`
  - every archived runtime report now carries:
    - `telemetry_generation`
    - `telemetry_compatibility`
    - `failure_taxonomy`
    - `recommended_recovery_action`
  - provider exhaustion is explicitly bucketed to `provider_exhaustion -> top_up_provider_balance_and_resume`
  - bootstrap OCR failures are explicitly bucketed to `ocr_failure -> fix_ocr_runtime_and_rerun_bootstrap`
  - generic timeout failures are distinguished from `repair_timeout`
- hardened `scripts/watch_real_book_live.py`
  - live monitor now surfaces legacy-vs-current telemetry generation
  - live monitor now shows failure taxonomy and recovery action even for older archived reports
- added focused reporting regressions in `tests/test_real_book_live_reporting.py`
  - current report generation stays Phase-3 compatible
  - bootstrap OCR failure is bucketed correctly
  - provider exhaustion is bucketed correctly
  - legacy report files are recognized as `legacy-report-generation` by the watcher

What this changes operationally:

- larger-corpus scanned runs now expose a stable compatibility layer instead of silently mixing old and new report generations
- retry/resume no longer depends on manually reading raw errors to decide whether the next action is top-up, OCR fix, or repair retry
- legacy scanned artifacts remain usable, but they are explicitly marked as partial telemetry rather than being mistaken for current-generation evidence

Historical execution consequence at the close of `MDU-16.1.2`:

- `MDU-16.1.2` is complete
- next focus is `MDU-16.1.3`: run larger-corpus stability/performance acceptance against the locked corpus tiers and record the first real acceptance thresholds

## 7. MDU-16.1.3 Acceptance Thresholds

`MDU-16.1.3` 将 `lane-pdf-scan-scale` 的 acceptance 从叙述性目标收敛成可执行阈值。首版阈值固定如下：

### Full-Book Bootstrap Floor

- scanned full-book `page_count >= 400`
- `chapter_count >= 90`
- `translation_packet_count >= 2000`
- `translation_run_count >= 2000`
- legacy full-book bootstrap failure 必须被明确分类为 `provider_exhaustion`

### Retry / Resume Lineage

- retry lineage 必须显式保留 `resume_from_status=failed`
- retry lineage 必须显式保留 `retry_from_status=failed`
- legacy retry report 必须被识别为 `legacy-report-generation`

### Slice Repair Throughput

- representative repair batch `candidate_total >= 100`
- selected repair batch `candidate_selected >= 30`
- `repaired_chain_count / candidate_selected >= 0.90`
- `total_cost_usd <= 0.05`
- failed candidates 若存在，必须全部落在 `repair_timeout`
- representative slice repair 的可接受上限是 `failed_candidates <= 3`

### Final Repair Closure

- final repair `candidate_selected >= 1`
- `repaired_chain_count / candidate_selected == 1.0`
- `failed_candidates == 0`
- `total_cost_usd <= 0.01`

### Readable Rescue End-State

- final readable rescue 必须显式存在：
  - `merged_markdown`
  - `merged_markdown_manifest`
  - `merged_html`
  - `merged_html_manifest`

### Lineage Structure Stability

- `v11 / v21-slice-2 / v23 / v28` 的结构计数必须保持稳定
- 当前冻结值为：
  - `chapters = 97`
  - `translation_packets = 2170`
  - `translation_runs = 2171`

## 8. MDU-16.1.3 Completion Snapshot

Implemented in this round:

- added artifact-driven acceptance helper in `scripts/pdf_scan_corpus_acceptance.py`
  - the helper now converts the locked four-tier scanned lineage into executable checks
  - acceptance is no longer carried only in operator narrative or plan prose
- added focused regression in `tests/test_pdf_scan_corpus_acceptance.py`
  - locked larger-corpus thresholds are now verified against the real artifact lineage
  - the test freezes the first phase-3 baseline for page / chapter / packet / run counts and repair success ratios
- acceptance now explicitly validates:
  - legacy bootstrap failure remains classified as `provider_exhaustion`
  - retry/resume lineage still exposes `failed -> resume/retry`
  - slice repair throughput clears the first cost/success thresholds
  - final repair closes residual failures
  - readable rescue exports exist
  - structural counts remain stable across the main lineage checkpoints

First accepted threshold snapshot:

- Tier A full-book bootstrap:
  - `page_count = 458`
  - `chapter_count = 97`
  - `packet_count = 2170`
  - `run_count = 2171`
- Tier C slice repair:
  - `candidate_total = 145`
  - `candidate_selected = 36`
  - `repaired_chain_count = 33`
  - `success_ratio = 0.9167`
  - `failed_candidates = 3`
  - failure family frozen as `repair_timeout`
- Tier C final repair:
  - `candidate_selected = 6`
  - `repaired_chain_count = 6`
  - `success_ratio = 1.0`
  - `failed_candidates = 0`
- Tier D readable rescue:
  - merged markdown/html plus both manifests are present

Operational consequence:

- `lane-pdf-scan-scale` is now closed for Wave 1
- future scanned larger-corpus regressions can reuse the same acceptance helper instead of reopening the threshold discussion from scratch
- the next serial-controller claim should move to `lane-delivery-upgrade / MDU-15.1.1`

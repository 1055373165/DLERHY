# 测试基线

## 当前基线（P1 完成后，2026-09-14）

逐文件独立进程运行（`ls tests/test_*.py | xargs -P 6 -n 1 …`，见 [PLAN.md](PLAN.md) 的工作原则）：

**959 passed · 0 failed · 4 xfailed · 30 skipped**（P0 结束时 994 passed；P1.2 随实验/benchmark 模块删除了 35 个测试）

前端：`cd frontend && npx vitest run` → 4 files / 4 tests passed。

任何新的失败都是回归；xfail 意外通过（XPASS）也需要处理：修复已落地时移除标记。

### 跳过（需显式开启）

`BOOK_AGENT_RUN_PG_TESTS=1` 且 `BOOK_AGENT_DATABASE_URL` 指向 PostgreSQL 时运行：

- `tests/test_postgres_workflow_integration.py`（22）
- `tests/test_events_bus.py`（5）
- `tests/test_run_stream_sse.py`（2，另需 events 表）
- `tests/test_postgres_schema_drift.py`（1，在临时库上迁移并比对 ORM）

### 预期失败（已知缺陷，附原因）

| 测试 | 原因 | 后续 |
|---|---|---|
| `test_api_workflow.py::test_export_download_bundles_multi_chapter_exports_as_zip` | 双语整书下载被映射到纯中文的 merged 版本 | P4 双语整书下载 |
| `test_persistence_and_review.py::test_workflow_review_auto_executes_packet_scoped_stale_brief_followups_when_concept_autolock_fails` | 源文本不变时重建章节摘要无法消除 STALE_CHAPTER_BRIEF；过去仅因句子乱序偶然通过 | P3.4 记忆 / 摘要策略 |
| `test_pdf_support.py::test_bootstrap_pipeline_recovers_chapters_when_intro_cue_is_embedded_in_body_block` | PyMuPDF 提取下漏识别第 3 页引言提示（Basic 提取器下通过） | P3.3 |
| `test_pdf_support.py::test_bootstrap_pipeline_labels_frontmatter_before_first_intro_chapter` | 跨页正文合并（cd3092e）抹掉前言页族；章节标题吸入正文（fda01fd 起） | P3.3 |

## P0 期间的变化摘要

起点（`main@2890024`）：1043 passed / 44 failed / 5 errors，2 个文件无法收集，`test_api_workflow` 段错误。

- **删除**：自愈层相关 34 个测试文件；依赖从未入库数据的测试（benchmark 执行、PDF 扫描语料验收、phase3 gate 两例、Postgres 版最小流水线 smoke）。
- **修复的产品缺陷**（均有回归测试）：后台 run 使用 Echo worker；预算护栏未生效；执行器瞬时 DB 错误导致 run 失败；已翻译文档的 run 永久挂起；run 在审校/导出前被判成功；GET 写入被回滚；后台导出缺 CAS 印章（且 SQLite 上 UUID 匹配失败）；SSE 断连不释放连接；概念解析器 provider 错误中断审校；句子按随机 UUID 排序；代码/正文拆分片段类型错误；参考文献条目换行丢失；PDF 学术标题截断、单词前言标题、目录页成章、论文标题回退为文件名。
- **测试卫生**：测试强制 echo 后端（不再读取 `.env` 发起真实计费调用）；每个测试进程使用独立临时目录并在退出时清理；`test_api_workflow` 不再用 StaticPool 共享 sqlite 连接。

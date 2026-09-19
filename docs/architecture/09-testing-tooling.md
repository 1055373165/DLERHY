# 09 · 测试体系、脚本与工程工具链

> 来源：2026-09-16 对分支 `refactor/p0-stabilize`（HEAD `2176d51`）的逐文件源码阅读。行号对应该基线；标注「待确认」的条目未经运行验证。总览与跨子系统结论见 [`00-overview.md`](00-overview.md)。

# book-agent 测试体系 / 脚本目录 / 工程工具链 / 仓库卫生 —— 现状笔记

- 仓库：`/Users/smy/project/book-agent`，分支 `refactor/p0-stabilize`（基于 `main@2890024`，领先 104 个提交，未推送）。
- 读取日期：2026-09-16。所有命令在本机实际执行；标注「待确认」的地方表示我没有直接证据。
- 术语：`ROOT` = 仓库根目录；`.venv` = `ROOT/.venv`（uv 管理，Python 3.13.5）。

---

## 0. 十个最重要的发现（摘要）

| # | 发现 | 证据路径 |
|---|---|---|
| 1 | **`uv sync`（dev.sh / service.sh 都会执行）会把 pytest / ruff 从 `.venv` 卸掉**：dev 依赖放在 `[project.optional-dependencies].dev` 而不是 `[dependency-groups]`，`uv sync` 默认不装 extras。读取时 `.venv/bin/pytest`、`.venv/bin/ruff` 均不存在（`.venv/bin` mtime 与 `artifacts/frontend.log` 同为 09-16 00:08，即上一次 `service.sh` 启动）。我用 `uv sync --extra dev` 恢复后才能跑 ruff / collect。 | `pyproject.toml:26-31`、`dev.sh:186`、`service.sh:142` |
| 2 | **`.env.example` 从未被 git 追踪**（被 `.gitignore:5` 的 `.env.*` 规则忽略，`git log -- .env.example` 为空），而 README Quick Start 第一步就是 `cp .env.example .env`。新克隆无法按文档启动。 | `.gitignore:5`、`README.md:38,52` |
| 3 | **测试会把产物写进仓库的 `artifacts/parse-ir/`**：`ParseIrService()` 默认 `output_root="artifacts/parse-ir"`（相对 CWD），`services/bootstrap.py:262` 用默认构造；`test_api_workflow` / `test_persistence_and_review` / `test_pdf_support` 均不覆盖该路径。目前 8 362 个 UUID 目录、88 MB；我跑 golden 测试后目录 mtime 立刻更新。 | `src/book_agent/services/parse_ir.py:91`、`src/book_agent/services/bootstrap.py:262` |
| 4 | **没有 CI、没有 pre-commit、没有 mypy、没有覆盖率**。ruff 现状：`src`+`tests` 121 个错误（74 E402 / 43 F401 / 3 E741 / 1 F841），`scripts` 53 个；`ruff format --check` 226/283 文件需要重排。README 却要求 "Run `uv run pytest` and `uv run ruff check` before pushing"。 | `pyproject.toml:39-41`、无 `.github/` |
| 5 | **`.test-tmp/` 3.7 GB 中 3.4 GB 是 `book-agent-uv-cache`**：OCR 子进程 `uv run --with surya-ocr==0.17.1 --with transformers…` 把 `UV_CACHE_DIR` 指到 `tempfile.gettempdir()/book-agent-uv-cache`，而 `tests/__init__.py` 把 tempdir 重定向到 `.test-tmp/`。另有 14 729 个空的 `book-agent-pdf-images-*` 目录（P0.5 之前 `mkdtemp` 泄漏，09-14 16:52 后不再新增）。 | `src/book_agent/ingestion/pdf/ocr.py:148-152`、`ingestion/pdf/extract.py:270` |
| 6 | **Dockerfile 单阶段、root 运行、`pip install -e .`、不用 `uv.lock`、不含前端**；`.dockerignore` 排除了 `tests` 与 `uv.lock`，因此 `scripts/run_postgres_integration.sh`（在容器里 `python -m unittest tests.…`）不可能工作。镜像本地未构建（`docker images book-agent:local` 为空），体积待确认。 | `Dockerfile`、`.dockerignore`、`scripts/run_postgres_integration.sh` |
| 7 | **`uv.lock` 与 `pyproject.toml` 一致**（`uv lock --check` 通过；两者最后一次改动同在 `c7d78a6`，2026-04-26，5 月 8 日只是文件 mtime）。但 `requires-python >=3.12`、Dockerfile 用 3.12-slim、本地 `.venv` 是 3.13.5、OCR 子进程又强制 `--python 3.13`：三个解释器版本没有统一。 | `pyproject.toml:10`、`Dockerfile:1`、`.venv/pyvenv.cfg`、`ocr.py:117-119` |
| 8 | **测试基础设施靠 `tests/__init__.py` 的 import 副作用**（sys.path、echo 后端、固定 Fernet key、TMPDIR 重定向），没有 conftest；103/107 个测试文件是 `unittest.TestCase`，48 个文件各自手写 `Base.metadata.create_all`，44 个文件共 151 处 `TemporaryDirectory`，8 个文件仍在用 `StaticPool`（P0.2 记录的段错误根因）。 | `tests/__init__.py`、见 §2 |
| 9 | **36 个产品模块没有任何测试引用**（含 `application/` 全部 7 个用例模块 3 664 行、`ingestion/pdf/chapters.py` 887 行、`domain/context/builders.py` 880 行、6 个 API 路由文件）；它们只被 golden / API 测试间接覆盖，没有覆盖率工具能证明这一点。 | 见 §3 |
| 10 | **`scripts/` 39 个文件里至少 20 个只服务于已经消失的 llm-book 交付链路**（硬编码 `chapters_config.json` 里的 llm-book UUID、读 `.test-tmp/chN-export/`、写 `.test-tmp/_*_cache.json`）；`chapters_config.json` 还引用不存在的 `scripts/regenerate_chapters_config.py`，`export_chapter_md_official.py` 文档引用不存在的 `.sh`。 | 见 §4 |

---

## 1. 测试矩阵

### 1.1 总览

| 指标 | 值 |
|---|---|
| `tests/` 下 `.py` 文件 | 121（107 个 `test_*.py` + 5 个场景/夹具模块 + `__init__.py` + `golden_pdfs/{__init__,fixtures}.py`）。任务描述里的「116」与实际略有出入。 |
| `pytest --collect-only` | **1 100 tests collected in 15.2 s**（`grep -c "def test_"` 之和也是 1 100，无参数化） |
| 基线（`docs/refactor/baseline-tests.md`，P1 后） | 959 passed / 0 failed / 4 xfailed / 30 skipped；之后又加了约 100 个用例（术语、PDF 修复等），基线文档未更新 |
| 现存 `expectedFailure` | 3 个（基线文档写 4 个；`test_api_workflow` 的 zip 下载那条已随 P4 修复被移除，文档未同步） |
| 现存 skip（需显式开启） | 31 个：`test_postgres_workflow_integration`(22) + `test_events_bus`(5) + `test_run_stream_sse`(2) + `test_postgres_schema_drift`(1) + `test_postgres_run_concurrency`(1，基线文档漏列) |
| 前端 | `npx vitest run` → 5 files / **6 tests passed**（基线文档写 4/4）；`npx tsc --noEmit` 通过 |
| 测试框架风格 | 103 个文件 `unittest.TestCase`；仅 2 个文件用 `@pytest.fixture`（`test_events_bus`、`test_run_stream_sse`）；1 个文件纯 pytest 函数（`test_render_bilingual_subset`）；`pytest-asyncio` 在 dev 依赖里但 **0 个** async 测试 |

### 1.2 逐文件矩阵

类型说明：单元 = 无 DB；SQLite = `Base.metadata.create_all` 建内存/文件 SQLite；API = `TestClient` / `SyncDocumentActionClient`；golden = 快照比对；PG = 需要 PostgreSQL；脚本 = 测试 `scripts/` 下代码；子进程 = `subprocess.run`。速度为实测（负载 ~12–16 时）或按体量估计（标「估」）。

| 文件 | 主要覆盖模块（`book_agent.` 前缀省略） | 用例 | 类型 | 速度 | 特殊要求 / 备注 |
|---|---|---|---|---|---|
| test_api_deps.py | app.api.deps, app.main | 3 | 单元 | 快 | |
| test_api_workflow.py | app.main, app.runtime.document_run_executor, services.workflows/export/run_execution, workers.* | 54 | API + SQLite（文件） | **慢**（执行器线程等待，8 处 sleep/wait；负载高时 20–30 s 超时） | 3 738 行；被 6 个其它测试模块 import 当夹具库（EPUB 常量、`_content_opf_with_chapters` 等）；曾段错误（StaticPool），P0.2 改文件 SQLite 后消失 |
| test_app_runtime.py | app.main, app.api.export_downloads, core.config | 6 | API | 快 | |
| test_append_only_audits.py | infra.repositories.ops | 2 | SQLite | 快 | |
| test_bootstrap_pipeline.py | orchestrator.bootstrap | 5 | 单元 | 快 | 自带 sys.path hack（E402） |
| test_chapter_memory_model.py | translation.chapter_memory | 4 | 单元 | 快 | |
| test_cli.py | cli | 1 | SQLite | 3 s（实测） | |
| test_concept_resolver_fallback.py | services.chapter_concept_autolock | 1 | 单元 | 快 | |
| test_docir_persistence.py | services.bootstrap, domain.structure.models | 4 | SQLite（StaticPool） | 快 | 5 个未用 import |
| test_document_delete.py | app.main（DELETE 路由） | 5 | API + SQLite（StaticPool） | 快 | |
| test_document_run_endpoints.py | app.main, orchestrator.pipeline_stage_cache | 4 | API + SQLite | 中（1 处 sleep） | |
| test_document_runs_active_unique.py | domain.models.ops（唯一索引） | 5 | SQLite | 快 | |
| test_enum_check_constraints.py | domain.models（CHECK） | 3 | SQLite | 快 | |
| test_enum_persistence.py | domain.enums/models | 5 | SQLite | 快 | |
| test_epub_parser.py | domain.structure.epub | 13 | 单元 | 快 | |
| test_equation_extractor.py | services.equation_extractor | 11 | 单元 | 快 | |
| test_events_bus.py | infra.repositories.events, domain.event_kinds | 5 | **PG** | — | pytest fixture；跳过条件是 `BOOK_AGENT_DATABASE_URL` 不是 PG（**不看** `RUN_PG_TESTS`）→ 若开发者 env 指向 PG 会意外执行 |
| test_executor_lease_loss.py | app.runtime.document_run_executor, services.run_control | 1 | SQLite | 快 | |
| test_executor_run_loop.py | document_run_executor | 6 | SQLite | 快 | `export_root="artifacts/exports"` 字面量（第 36 行） |
| test_executor_translation_transactions.py | document_run_executor, services.workflows | 4 | SQLite | 快 | |
| test_executor_worker_resolution.py | app.main（worker 解析） | 3 | API + SQLite（StaticPool） | 快 | |
| test_export_figure_placeholders.py | export.markup | 2 | 单元 | 快 | |
| test_export_gate_evaluation.py | services.export（gate） | 2 | SQLite | 快 | |
| **test_export_golden.py** | services.export 全部导出类型 | 3 | **golden** + SQLite（文件） | 8 s（实测，3 passed） | 快照 `tests/golden/exports/{epub,pdf}.json`；`BOOK_AGENT_UPDATE_GOLDEN=1` 重生成；rebuilt PDF（Playwright）不在快照内 |
| test_export_layout_gate.py | services.export, infra.repositories.export | 2 | SQLite | 快 | 8 处 E402 |
| test_export_list_items.py | export.markup | 2 | 单元 | 快 | |
| test_export_modality_rendering.py | services.export | 14 | 单元 | 快 | |
| test_failure_classification.py | workers.failures | 3 | 单元 | 快 | |
| test_figure_clustering.py | domain.structure.figure_clustering/pdf, ingestion.pdf.models | 24 | 单元（PyMuPDF 合成 PDF） | 中 | |
| test_frontend_api_types.py | scripts/generate_frontend_api_types.py → `frontend/src/lib/api-types.gen.ts` | 1 | 脚本 + 子进程 | 3 s（实测） | 保证前端类型与 OpenAPI 同步 |
| test_frontend_entry.py | app.main（`/` 入口页） | 2 | API | 快 | |
| test_glossary_enforcement.py | services.glossary_enforcement | 16 | 单元 | 快 | |
| test_glossary_extraction.py | services.glossary_extraction/glossary_service | 7 | SQLite | 快 | |
| test_glossary_service.py | services.glossary_service/terminology_miner | 11 | SQLite（StaticPool） | 快 | |
| test_golden_pdf_regression.py | ingestion.pdf.classify/extract, text_layer_sanity, terminology_miner | 19 | 单元（程序化 PDF） | 中 | 用 `tests/golden_pdfs/fixtures.py` 的 15 个生成器；**不是**快照测试，名字有误导 |
| test_image_modality.py | services.image_modality | 11 | 单元 | 快 | |
| test_layout_validate.py | services.layout_validate | 6 | 单元 | 快 | |
| test_memory_service.py | services.memory_service/context_compile/realign/rebuild/rerun/review/translation… | 13 | SQLite | 中（991 行） | **25 处 E402**（文件最多） |
| test_minimal_pipeline_smoke_script.py | scripts/run_minimal_pipeline_smoke.py | 2 | 脚本 + 子进程 + SQLite | 中 | |
| test_modality_pipeline.py | services.modality_pipeline/tatr_extractor | 10 | 单元 | 快 | |
| test_no_progress_budget.py | services.run_execution | 4 | SQLite | 快 | |
| test_ocr_reextraction_wiring.py | ingestion.pdf.ocr_reextraction | 5 | 单元 | 快 | 5 个未用 import |
| test_ocr_runtime.py | ingestion.pdf.ocr | 8 | 单元（mock subprocess） | 快 | |
| test_parse_ir.py | services.parse_ir, infra.repositories.parse_ir | 4 | SQLite | 快 | 唯一显式传 `output_root` 的测试 |
| test_parse_service_modality_wiring.py | services.bootstrap/modality_pipeline | 12 | 单元 | 快 | |
| test_pdf_bootstrap_adapter_wiring.py | domain.structure.pdf, ocr_reextraction | 9 | 单元 | 快 | |
| test_pdf_column_reorder.py | domain.structure.pdf | 2 | 单元 | 快 | |
| test_pdf_font_emphasis.py | domain.structure.pdf, ingestion.pdf.classify | 7 | 单元 | 快 | |
| test_pdf_image_and_heading_regressions.py | export.render_repair, services.export | 4 | 单元 | 快 | |
| test_pdf_outline_chapter_detection.py | ingestion.pdf.classify | 17 | 单元 | 快 | |
| test_pdf_parse_ir_planning.py | services.parse_ir/bootstrap | 1 | SQLite | 快 | |
| test_pdf_ruled_tables.py | domain.structure.pdf, export.markup | 2 | 单元 | 快 | |
| test_pdf_sanity_propagation.py | domain.structure.pdf | 2 | 单元 | 快 | |
| test_pdf_smoke_tools.py | tools.pdf_smoke | 5 | 单元 | 快 | |
| **test_pdf_structure_golden.py** | domain.structure.pdf（`PDFParser.parse`） | 1（56 subTest） | **golden** | 7 s（实测） | 56 个快照 `tests/golden/pdf_structure/*.json`：41 个来自 `test_pdf_support._write_*_pdf`，15 个来自 `golden_pdfs.fixtures.make_*`；文件集合不一致会直接失败 |
| test_pdf_structure_refresh_stale_sentences.py | services.pdf_structure_refresh | 2 | SQLite | 快 | |
| **test_pdf_support.py** | 全链路：ingestion.pdf.*, domain.structure.pdf, services.bootstrap/export/review/translation…, app.main | **191** | 单元 + SQLite（StaticPool）+ API | **极慢**（正常 ~1.5 min，负载下 ~27 min） | 10 710 行、6 个 TestCase、41 个 PDF 写入函数（被 golden 复用）；**2 个 expectedFailure**（4222、4239 行，P3.3 遗留） |
| test_pdf_vector_bullets.py | domain.structure.pdf | 3 | 单元 | 快 | |
| **test_persistence_and_review.py** | services.* 几乎全部、infra.repositories.*、application.read_models | **137** | SQLite | **慢**（估；11 686 行、20 个 TestCase） | **1 个 expectedFailure**（11331 行）；被 `test_phase3_integration_gate`、`test_pdf_support` import |
| test_phase3_integration_gate.py | services.export/workflows（导出 gate 集成） | 1 | SQLite | 中 | 名字沿用已删除的 `scripts/phase3_integration_gate.py` 时代，但**不 import 该脚本** |
| test_pipeline_stage_finalize.py | document_run_executor | 4 | SQLite | 快 | `mkdtemp(prefix="finalize-stage-")` |
| test_postgres_run_concurrency.py | services.run_execution/run_control（12 并发） | 1 | **PG** | — | `skipUnless(RUN_PG_TESTS=="1")` |
| test_postgres_schema_drift.py | alembic vs `Base.metadata` | 1 | **PG** | — | 在临时库上 `alembic upgrade head` 并比对表/列/可空/索引/CHECK/JSONB |
| test_postgres_workflow_integration.py | services.workflows/export/run_execution（PG 语义） | 22 | **PG** | — | 1 884 行；`setUpClass` 建 session；14 处 E402 |
| test_providers_api.py | app.main（providers 路由）, services.provider_credentials | 2 | API + SQLite | 快 | |
| test_real_book_live_reporting.py | scripts/run_real_book_live.py, watch_real_book_live.py, real_book_live_reporting_common.py | 7 | 脚本 | 快 | 用 stdlib `sqlite3` 写报告 |
| test_reconcile_terminal_state.py | services.run_execution | 10 | SQLite | 快 | |
| test_reconciler.py | orchestrator.reconciler | 4 | SQLite | 快 | |
| test_references_extractor.py | services.references_extractor | 14 | 单元 | 快 | |
| test_render_bilingual_subset.py | scripts/render_bilingual_subset.py | **37** | 脚本（纯函数） | 快 | 唯一的 pytest 函数式文件；`sys.path.insert(scripts)` |
| test_retry_run_cold_start.py | services.run_control | 4 | SQLite | 快 | |
| test_review_naturalness_acceptance.py | services.review/translation | 3 | SQLite | 中 | |
| test_review_skip_visibility.py | application.read_models, services.workflows | 3 | SQLite | 快 | `mkdtemp(prefix="review-skip-")` |
| test_rule_engine.py | orchestrator.rule_engine/rerun | 18 | 单元 | 快 | |
| test_run_control_api.py | app.main（runs 路由） | 4 | API + SQLite（StaticPool） | 快 | 7 处 E402 |
| test_run_control_models.py | domain.models.ops | 2 | SQLite | 快 | |
| test_run_execution.py | services.run_execution, document_run_executor | 11 | SQLite | 中（700 行） | |
| test_run_lineage.py | services.run_control | 5 | SQLite（内存） | 2 s（实测） | |
| test_run_outcome_classifier.py | orchestrator.run_plan/stage_status | 11 | 单元 | 快 | |
| test_run_pdf_chapter_smoke.py | scripts/run_pdf_chapter_smoke.py | 5 | 脚本 + SQLite | 快 | |
| test_run_stream_sse.py | app.api.router（SSE）, infra.repositories.events | 2 | **PG** + API | — | 跳过条件同 `test_events_bus`（看 DSN，不看 `RUN_PG_TESTS`） |
| test_sentence_segmenter.py | domain.segmentation.sentences | 5 | 单元 | 快 | |
| test_service_script.py | `service.sh`（假 uv、假 uvicorn） | 2 | 子进程 bash | 6 s（实测，含 sleep 3） | |
| test_source_preserving_epub_export.py | services.workflows（EPUB 导出） | 1 | SQLite | 中 | |
| test_stage_gate_phase0.py | document_run_executor（阶段门） | 6 | SQLite | 快 | `mkdtemp(prefix="stage-gate-phase0-")` |
| test_stage_gatekeeper.py | orchestrator.stage_gate/stage_status | 7 | SQLite | 快 | |
| test_stage_status_calculator.py | orchestrator.stage_status | 9 | SQLite | 快 | |
| test_surya_reextraction_adapter.py | ingestion.pdf.surya_reextraction（mock subprocess） | 11 | 单元 | 快 | |
| test_table_extractor.py | services.table_extractor | 15 | 单元 | 快 | |
| test_tatr_extractor.py | services.tatr_extractor（不加载 torch） | 16 | 单元 | 快 | |
| test_term_consistency.py | services.term_consistency/glossary_service | 4 | SQLite | 快 | 最近 5 个提交都在改它 |
| test_term_matching.py | domain.terminology.matching | 6 | 单元 | 快 | |
| test_terminology_miner.py | services.terminology_miner | 8 | 单元 | 快 | |
| test_text_code_heuristics.py | export.code_text, ingestion.text | 7 | 单元 | 快 | |
| test_text_layer_sanity.py | domain.structure.text_layer_sanity | 7 | 单元 | 快 | |
| test_translatability_guard.py | domain.structure.models | 18 | 单元 | 快 | |
| test_translate_frontier_plan.py | orchestrator.frontier_plan | 4 | 单元 | 快 | |
| test_translation_glossary_injection.py | services.context_compile/memory_service | 6 | SQLite（StaticPool） | 快 | |
| test_translation_glossary_postvalidation.py | services.translation（后置 hook） | 5 | SQLite（StaticPool） | 快 | |
| test_translation_heuristics_pack.py | translation.heuristics, services.style_drift/term_normalization | 4 | 单元 | 快 | |
| test_translation_output_validation.py | translation.output_validation | 3 | 单元 | 快 | |
| **test_translation_prompt_golden.py** | workers.translator（prompt 构造）, translation.prompt_profiles | 3 | **golden** + SQLite | 5 s（实测） | 快照 `tests/golden/translation_prompts/{epub,pdf}.json`（合计 2 MB，仓库最大的两个被追踪文件）；13 profile × 2 layout |
| test_translation_worker_abstraction.py | workers.translator/factory/providers.openai_compatible, services.translation | 56 | SQLite | 中（3 009 行） | |
| test_work_item_cas.py | infra.repositories.run_control（CAS） | 5 | SQLite | 快 | |
| test_work_items_active_scope_unique.py | domain.models.ops | 3 | SQLite | 快 | |
| test_worker_factory.py | workers.factory, services.secrets | 5 | 单元 | 快 | |
| **test_workflow_golden.py** | services.workflows + application.* 读模型 + schemas.workflow | 2 | **golden** + SQLite | 4 s（实测） | 快照 `tests/golden/workflow_scenario.json`；同时校验读模型能 `model_validate(from_attributes=True)` 进 API 响应模型 |

### 1.3 夹具与场景模块（非测试文件）

| 文件 | 行数 | 作用 | 被谁用 |
|---|---|---|---|
| `tests/__init__.py` | 40 | 全局副作用：`sys.path.insert(src)`；强制 `BOOK_AGENT_TRANSLATION_BACKEND=echo`、`_MODEL=echo-worker`；固定 `BOOK_AGENT_SECRET_KEY`；把 `TMPDIR` / `tempfile.tempdir` 重定向到 `.test-tmp/pytest-<pid>-*`，`atexit` 删除 | 所有测试（靠 `tests` 是包这一事实） |
| `tests/document_actions.py` | 181 | `SyncDocumentActionClient(TestClient)`：拦截 `POST /v1/documents/{id}/translate|review|export`，进程内同步调用 `DocumentWorkflowService` 并返回旧的同步响应形状（P2.6 之后真实端点返回 202 入队） | `test_api_workflow`、`test_pdf_support` 等 API 测试 |
| `tests/export_golden_scenario.py` | 137 | EPUB + PDF 夹具 → bootstrap → echo 翻译 → review → 渲染 6/4 种导出 → 读回并归一化（UUID/sha256/时间戳/临时路径/日期），zip 逐条展开 | `test_export_golden` |
| `tests/pdf_structure_scenario.py` | 79 | 反射收集 `test_pdf_support._write_*_pdf(path)` 与 `golden_pdfs.make_*()`，跑 `PDFParser` 并把 `ParsedDocument` dataclass 归一化 | `test_pdf_structure_golden` |
| `tests/translation_prompt_scenario.py` | 151 | `RecordingPromptWorker` 包住 echo worker，记录每个 `TranslationTask` 的上下文包与 13×2 个 prompt；长字符串 intern 成 `<text:n>` | `test_translation_prompt_golden` |
| `tests/workflow_golden_scenario.py` | 209 | 确定性工作流：bootstrap→翻译→记忆提案决策→review→被阻断导出→issue action→worklist 指派→成功导出，捕获所有读模型；按 UTC 日分桶避免时刻抖动 | `test_workflow_golden` |
| `tests/golden_pdfs/fixtures.py` | — | 15 个纯函数 `make_*() -> bytes` 程序化生成 PDF（不入库二进制） | `test_golden_pdf_regression`、`pdf_structure_scenario`、`export_golden_scenario`、`test_minimal_pipeline_smoke_script` |
| `tests/golden/` | 3.1 MB / 61 个 json | 快照本体：`exports/`(2)、`pdf_structure/`(56)、`translation_prompts/`(2)、`workflow_scenario.json` | 4 个 golden 测试 |

**跨测试模块耦合**：`test_api_workflow` 被 6 个模块 import（EPUB 常量与 OPF 生成函数），`test_persistence_and_review` 被 2 个、`test_pdf_support` 被 2 个 import。import 这些万行文件只为拿几个常量，是 collect 耗时 15 s 的原因之一，也让「删除/拆分大文件」牵一发动全身。

---

## 2. 测试基础设施：现状与痛点

### 2.1 现状

| 议题 | 现状 |
|---|---|
| 建库 | 没有共享夹具。48 个文件各自 `build_engine("sqlite+pysqlite:///:memory:")`（26 个）或文件 SQLite（25 个）+ `Base.metadata.create_all`。`build_engine` 本身有 AppScope 守卫（`smoke`/`e2e` 拒绝非 sqlite；`dev`/`prod` 由 `create_app` 要求 PG），测试靠注入 `app.state.session_factory` 绕过 |
| 临时目录 | `tests/__init__.py` 每进程一个 `.test-tmp/pytest-<pid>-*`，`atexit` 清理；原因是 `exports` 表的 CHECK 约束拒绝 `/tmp/*`、`/var/folders/*` 前缀（`domain/models/review.py:120`）。被 kill 的进程不会清理（现存 4 个残留，09-14/09-15） |
| echo 后端 | `tests/__init__.py` 用 `os.environ` 覆盖（进程 env 优先于 dotenv），因此**任何**测试都不会读 `.env` 的真实 provider；需要 provider 的测试显式构造 `Settings`/客户端 |
| Postgres | `BOOK_AGENT_RUN_PG_TESTS=1` + `BOOK_AGENT_DATABASE_URL` 指向 PG。**不一致**：`test_events_bus`、`test_run_stream_sse` 只看 DSN 是否 PG，不看开关；开发者 `.env` 指向 PG 时会直接跑到开发库上（读 `.env` 的是 `Settings`，`tests/__init__.py` 没覆盖 `DATABASE_URL`） |
| 段错误 / 逐文件 | 根因（P0.2 已修）：`test_api_workflow` 用 `StaticPool` 让多个执行器线程共享同一个 sqlite3 连接。修复后整套仍按逐文件跑（`ls tests/test_*.py \| xargs -P 6 -n 1 sh run_one.sh`，见 memory 与 `baseline-tests.md`），原因：(a) 历史习惯；(b) 8 个文件仍用 `StaticPool`（`test_pdf_support`、`test_document_delete`、`test_run_control_api`、`test_executor_worker_resolution`、`test_docir_persistence`、`test_glossary_service`、`test_translation_glossary_injection`、`test_translation_glossary_postvalidation`），其中 `test_pdf_support` 与 `test_executor_worker_resolution` 会起执行器线程；(c) `pytest-xdist` 未安装，逐文件是唯一的并行方式 |
| 总耗时 | 未有完整计时记录。已知：`test_pdf_support` 单文件 ~1.5 min（负载下 27 min）；golden 4 文件合计 ~24 s；小文件 2–3 s（进程启动 + import 大文件占大头）。按 107 个进程、6 并行、负载正常粗估 **10–20 min**（待确认） |
| golden 更新 | 4 个 golden 测试统一约定：`BOOK_AGENT_UPDATE_GOLDEN=1` 时先写快照再比对（因此更新模式永远 pass）。归一化各自实现一份（UUID/sha/时间戳正则在 4 个 scenario 文件里重复 4 次） |
| 覆盖率 | 无 `pytest-cov`、无 `.coveragerc`；`.gitignore` 里有 `htmlcov/`、`.coverage`，说明曾手动跑过 |
| pytest 配置 | `pyproject.toml` 只有 `testpaths = ["tests"]`；memory 里的 `-p no:cacheprovider -o addopts=""` 是习惯而非必要（没有 addopts）。无 `pythonpath`，依赖 `_editable_impl_book_agent.pth` + `tests/__init__.py` 双重保险 |
| 前端 | vitest（jsdom）5 文件 6 用例；无 eslint / prettier；`tsc` 在 `npm run build` 里 |

### 2.2 重复夹具代码的量

| 模式 | 文件数 | 出现次数 |
|---|---|---|
| `Base.metadata.create_all` | 48 | 51 |
| `tempfile.TemporaryDirectory` | 44 | 151 |
| `tempfile.mkdtemp` | 6 | 7 |
| `def setUp` | 49 | 54 |
| `DocumentWorkflowService(` 手工构造 | 13 | 73 |
| bootstrap 入口（`bootstrap_document` / `BootstrapService` / `ParseService`） | 12 | 119 |
| `EchoTranslationWorker` 手工构造 | 6 | 15 |
| `StaticPool` | 8 | 15 |
| `TestClient(` | 7 | 8 |
| 自带 `sys.path.insert(SRC)` + `os.environ.setdefault(echo)`（E402 来源） | ~12 | 74 处 E402 |
| UUID/时间戳归一化正则 | 4 个 scenario | 4 份 |

结论：一份 `conftest.py`（`sqlite_engine`、`session_factory`、`app`、`client`、`echo_worker`、`tmp_root`、`golden(name)` 夹具 + 统一 `normalize`）可以替换约 50 个 `setUp`、150 处临时目录样板、74 处 E402 和 4 份归一化代码。PLAN P0.5 已记为未完成项。

### 2.3 痛点清单

1. `uv sync` 卸掉 dev 工具（§0-1）：每次 `./dev.sh` 后 `pytest` 消失，README 的 `uv run pytest` 会报 `No module named pytest`（我实测如此）。
2. 测试污染仓库工作区：`artifacts/parse-ir/`（§0-3）、`.test-tmp/book-agent-uv-cache`（§0-5，只在 OCR 相关测试真的启动 `uv run` 时产生，`test_ocr_runtime` 用 mock，但 `.test-tmp` 里 62 个 `tmp*.surya.std{out,err}.log` 说明真实调用发生过）。
3. `test_events_bus` / `test_run_stream_sse` 的开关语义与其它 PG 测试不同（§2.1）。
4. 大文件 import 耦合：删除 `test_api_workflow` 里任何一个常量都会破坏 6 个模块。
5. 三个 `expectedFailure` 是产品缺陷占位（P3.3 / P3.4），不是测试问题；XPASS 不会报错（unittest 语义下 unexpected success 会算失败，实际行为待确认）。
6. `test_golden_pdf_regression.py` 名字暗示快照，实际是断言型单测；真正的 PDF 快照在 `test_pdf_structure_golden.py`。
7. `tests/test_executor_run_loop.py:36` 把 `export_root="artifacts/exports"` 写死为项目相对路径。
8. `test_pdf_support.py:6115` 硬编码 `/tmp/book-agent-ocr-smoke`（与 CHECK 约束禁止 `/tmp` 的策略相悖，只是没写库所以没炸）。
9. 没有慢测标记（`@pytest.mark.slow`）、没有 `-m` 分层，无法只跑「秒级单测」。
10. 前端测试仅 6 个，`WorkspaceContext.tsx`（618 行）、`api.ts`（808 行）几乎无覆盖。

---

## 3. 覆盖缺口

### 3.1 没有任何测试引用的产品模块（36 个，共 ≈ 8 900 行）

判定方法：对 `src/book_agent` 下每个非 `__init__` 模块，在 `tests/**/*.py` 中搜索其点分路径或 `from <parent> import <leaf>`；未命中即列出。**间接覆盖**（例如通过 `DocumentWorkflowService` 门面、golden 快照、TestClient 路由）无法用此法证明，需要覆盖率工具。

| 模块 | 行数 | 可能的间接覆盖 | 风险 |
|---|---|---|---|
| `application/analytics.py` | 1 077 | `test_workflow_golden` 读模型快照 | 高：纯函数却无直接测试 |
| `application/document_queries.py` | 566 | golden / API | 中 |
| `application/export_use_case.py` | 357 | `test_api_workflow`、`test_export_*` 经 workflows 门面 | 中 |
| `application/issue_actions.py` | 104 | golden 场景「issue action」 | 中 |
| `application/issue_queries.py` | 359 | golden | 中 |
| `application/memory_proposals.py` | 364 | golden「提案批准/拒绝」 | 中 |
| `application/worklist.py` | 387 | golden「worklist」 | 中 |
| `app/api/routes/documents.py` | 588 | `test_api_workflow` 走 HTTP | 中：路由本体无直接引用 |
| `app/api/routes/runs.py` | 314 | `test_run_control_api` 走 HTTP | 中 |
| `app/api/routes/run_stream.py` | 212 | `test_run_stream_sse`（PG，默认 skip） | **高：默认从不执行** |
| `app/api/routes/run_cost.py` | 144 | 无（依赖 `cost_rollup_*` 物化视图，PG-only） | **高：无任何测试** |
| `app/api/routes/actions.py` | 57 | 无 | 高 |
| `app/api/routes/health.py` | 28 | `test_app_runtime` 可能 | 低 |
| `app/ui/page.py`、`app/ui/router.py` | 192 | `test_frontend_entry` | 低 |
| `domain/context/builders.py` | 880 | `test_translation_prompt_golden`（上下文包快照）、`test_memory_service` 经 `context_compile` | 中：核心上下文构建无单测 |
| `ingestion/pdf/chapters.py` | 887 | `test_pdf_structure_golden`（56 快照）、`test_pdf_support` | 中：P3.3 刚拆出，仅快照护栏 |
| `domain/structure/artifact_grouping.py` | 386 | 经 `pdf.py` | 中 |
| `domain/structure/canonical_ir.py` | 74 | `test_docir_persistence`？（未直接引用） | 中 |
| `domain/structure/geometry.py` | 29 | 经 figure_clustering | 低 |
| `domain/block_rules.py` | 56 | 无 | 中 |
| `export/alignment.py` | 306 | `test_export_golden` | 中 |
| `export/common.py` | 408 | `test_text_code_heuristics`（经 code_text）| 中 |
| `export/epub_assets.py` | 129 | golden（EPUB 条目） | 低 |
| `export/evidence.py` | 511 | golden（review package） | 中 |
| `export/stylesheets.py`、`export/titles.py` | 136 | golden | 低 |
| `infra/storage/blobs.py` | 182 | `test_api_workflow`（下载）| 中：`stamp_export_records` 无直接测试 |
| `services/epub_structure_refresh.py` | 225 | 无（PDF refresh 有测试，EPUB 没有） | **高** |
| `orchestrator/state_machine.py` | 51 | 无（P1.2 已删无调用方的转移表，剩余是否有调用方待确认） | 中 |
| `core/logging.py` | 9 | — | 低 |
| `schemas/{common,document,health,provider,run_control}.py` | 227 | 经 API 序列化 | 低 |

### 3.2 其它缺口

| 缺口 | 说明 |
|---|---|
| 覆盖率工具 | 无。建议 `pytest-cov` + `[tool.coverage.run] source=["book_agent"]`，先量化再谈阈值 |
| PG-only 行为 | `LISTEN/NOTIFY`、`cost_rollup` 物化视图、JSONB CHECK、行锁并发只在 `RUN_PG_TESTS=1` 下有 31 个用例，本地默认与（不存在的）CI 都不跑 |
| Alembic 迁移 | 只有 `test_postgres_schema_drift`（PG）比对 head；无 downgrade 测试、无「从 0001 逐步升级」测试 |
| CLI | `test_cli.py` 只有 1 个用例；CLI 有 11 个子命令（bootstrap/summary/translate/review/export/refresh-pdf-structure/glossary-extract/glossary-lock/term-consistency/execute-action…） |
| 真实 provider 路径 | `workers/providers/openai_compatible.py`（811 行）仅在 `test_translation_worker_abstraction` 用 mock transport；无 contract test / 录制回放 |
| 可选依赖分支 | Playwright（rebuilt PDF）、TATR（torch/transformers）、Surya OCR 三条分支都只测「未安装时的降级」，没有任何真实执行测试 |
| 前端 | 无 e2e；`api.ts` 19 个手写类型与生成类型未对齐（PLAN P4 未完成项） |
| 负载/性能 | 无 |

---

## 4. `scripts/` 分类表（39 个被追踪文件）

依赖说明：`.test-tmp` = 读写 `ROOT/.test-tmp/`（其中 `chN-export/`、`_chapter_cover_cache.json`、`_sidebar_callout_cache.json` 是 llm-book 时代的手工数据，仓库不追踪）；`llm-book` = 依赖 `chapters_config.json` 里的 llm-book document/chapter UUID 或 `artifacts/uploads/.../llm-book.pdf`（已不在磁盘）。所有脚本的 `book_agent` import 我用 AST 逐个验证过，**全部仍可解析**（无 import 断裂）。

| 脚本 | 行数 | 用途 | 依赖输入 | 测试引用 | 与产品重复 | 建议 | 理由 |
|---|---|---|---|---|---|---|---|
| `export_chapter_zh_html.py` | 4 195 | llm-book 章节中文/双语 HTML+MD 导出，含大量修复启发式 | env 变量传 UUID；`.test-tmp/_*_cache.json`；`ORDINAL_LO/HI` | 否 | **是**：与 `services/export` + `export/*` 平行实现渲染 | **删除（P4 门槛后）** | PLAN P4 决策：移植启发式后删；阻塞于源 PDF 丢失。用 RSI 书验证时 `repair_stats` 全 0，说明启发式只针对 llm-book 版式 |
| `export_chapter_bilingual.py` | 620 | 章节双语块对 HTML（review 通道） | DB + UUID | 否 | 是（`BILINGUAL_HTML` 导出已在产品） | 删除 | 同上 |
| `export_chapter_md.py` | 325 | 中文为主 + 折叠英文的 Markdown | DB + UUID | 否 | 是（`MERGED_MARKDOWN`） | 删除 | |
| `export_chapter_md_official.py` | 103 | 调官方 `ExportService` 导出章节 | `chapters_config.json`、`.test-tmp` | 否 | 是（就是 CLI `export` 的子集） | 删除 | 文档引用不存在的 `export_chapter_md_official.sh`；CLI 已有 `export` |
| `build_full_book_bilingual.py` | 184 | 拼接 9 章 bilingual.html | `.test-tmp/chN-export`、llm-book | 否 | 部分（P4 已把双语整书下载改为打包各章） | 删除 | 输入已不存在 |
| `build_review_ch1_ch2.py` | 140 | 拼接 ch1+ch2 | `.test-tmp` | 否 | — | 删除 | 一次性 |
| `build_llm_book_deliverable.py` | 219 | 打包 `LLM-Book/` 交付目录 | `.test-tmp`（9 处）、`artifacts/uploads` | 否 | — | 删除 | 一次性交付；输出目录已从 git 移除 |
| `package_deliverables.py` | 387 | 打包 `deliverable/` 四个 bundle | `.test-tmp`、`chapters_config.json`、`deliverable/` | 否 | — | 删除 | 同上 |
| `verify_chapter.py` | 360 | 章节 HTML 审计 R1–R8 | 脚本 HTML 结构（`h2`/`p`/`div.pair`） | 否 | 部分（`services/layout_validate.py` 做产品侧 gate） | 删除；R3/R4（图注与正文重复）若产品缺失可移植到 `layout_validate` | PLAN 记录它对产品 HTML「检查为空」，不能当 oracle |
| `qa_run_chapter.sh` | 149 | export→verify→merge 闭环 | `chapters_config.json`、`.venv/bin/python` | 否 | — | 删除 | 只驱动上面两个脚本 |
| `chapters_config.json` | — | llm-book 9 章 UUID / 序号范围 / 输出目录 | — | 否 | — | 删除 | 引用不存在的 `regenerate_chapters_config.py`；UUID 对应的 DB 已不存在 |
| `translate_chapter_covers.py` | 244 | 逐条翻译 "This chapter covers" 列表 | 9 个硬编码 UUID、`.test-tmp` 缓存 | 否 | 是（应属解析器修复：P4 已修列表拆分） | 删除 | 一次性补丁 |
| `translate_sidebar_callouts.py` | 205 | 翻译侧栏 callout | 硬编码 UUID、`.test-tmp` 缓存 | 否 | 是（同上） | 删除 | |
| `backfill_callout_ocr.py` | 279 | 对 callout PNG 做 OCR + 翻译回填 | 直连 postgres（无 book_agent import）、硬编码 UUID | 否 | 是 | 删除 | |
| `render_bilingual_subset.py` | 1 172 | 部分翻译切片的双语 Markdown 渲染（绕过导出 gate） | 硬编码 UUID（4 处）、postgres | **是**：`test_render_bilingual_subset.py` 37 用例 | **是**（第三套 Markdown 渲染） | 删除，连同 37 个测试；F1–F6 防御若产品缺失则移植 | 存在理由（章节检测失败导致 gate 不可达）已被产品修复路线取代 |
| `ingest_pdf_only.py` | 103 | 只解析不翻译 | postgres URL、上传 PDF | 否 | **是**：CLI `bootstrap` 子命令 | 删除 | 文档自述 "thin wrapper" |
| `lock_chapter_concept.py` | 81 | 锁定章节概念 | DB | 否 | 部分：CLI 有 `glossary-lock`，章节概念锁走 `services/chapter_concept_lock` | 迁入 CLI 子命令后删除 | |
| `repair_pdf_prose_artifacts.py` | 114 | 修复被判为 artifact 的散文续行 | `--input-db`（SQLite） | 否 | 服务 `services/pdf_prose_artifact_repair.py` 有测试 | 迁入 CLI（或删除，视产品是否还需要离线修复） | 只接受 SQLite 输入，与 PG-only 冲突 |
| `backfill_chapter_memory.py` | 62 | 回填章节记忆 | DB | 否 | 服务 `services/chapter_memory_backfill.py`（P1.2 明确保留） | **迁入 CLI**（`book-agent memory backfill`） | 运维需要 |
| `backfill_export_paths.py` | 224 | M1.2 一次性回填 sha256/byte_count/last_verified_at | DB | 否 | 否 | 删除（已执行过的一次性迁移；若要保留应转成 alembic data migration） | |
| `heal_exports_by_basename.py` | 157 | 按 basename 修复 `file_path` 不可达的导出行 | DB + 磁盘 | 否 | 对应的运行时 self-heal（`_resolve_artifact_path` 读时自愈）已在 P2.6 删除 | 删除 | 设计前提不存在了 |
| `materialize_blob_store.py` | 230 | 把导出物化到 CAS `artifacts/blobs/<aa>/<bb>/<sha>` | DB + 磁盘 | 否 | 产品 `infra/storage/blobs.py` 仍被 `export_use_case`、`export_downloads` 使用 | **保留为运维**（或迁 CLI `exports materialize`） | blob store 是否仍是产品承诺待确认 |
| `reap_blob_store.py` | 251 | 回收孤儿 blob | 同上 | 否 | 同上 | 保留为运维 / 迁 CLI | |
| `verify_exports.py` | 265 | 重新哈希导出并刷新完整性戳 | 同上 | 否 | 同上 | 保留为运维 / 迁 CLI | |
| `migrate_sqlite_to_pg.py` | 137 | SQLite → PG 全量迁移 | SQLite 文件 | 否 | — | 删除 | 应用 P2.7 起 PG-only，不再有 SQLite 生产数据 |
| `smoke_provider_service.py` | 62 | provider 凭据服务 smoke | DB、`.env`（会自动生成 SECRET_KEY 写入 `.env`） | 否 | `test_providers_api`、`test_worker_factory` | 删除或迁 CLI `providers test` | 副作用（写 `.env`）不适合脚本 |
| `generate_frontend_api_types.py` | 91 | OpenAPI → `api-types.gen.ts` | 无 | **是**：`test_frontend_api_types.py` | 否 | **保留**（开发工具；可移到 `tools/` 或 `make gen-types`） | |
| `run_minimal_pipeline_smoke.py` | 220 | EPUB/PDF 最小端到端 smoke（SQLite，`smoke` scope） | 无 | **是**：`test_minimal_pipeline_smoke_script.py` | 否 | 保留（迁入 `book_agent/tools/` 并给 CLI 子命令） | 唯一的自包含 e2e |
| `run_pdf_smoke.py` | 44 | `tools.pdf_smoke` 薄包装 | PDF 路径 | 否（`tools.pdf_smoke` 有测试） | 否 | 三个合并为一个 CLI 子命令 `pdf-smoke` | |
| `run_pdf_smoke_corpus.py` | 30 | 同上（manifest 驱动） | manifest | 否 | 否 | 同上 | |
| `run_pdf_candidate_scan.py` | 40 | 同上（扫描候选） | 目录 | 否 | 否 | 同上 | |
| `run_pdf_chapter_smoke.py` | 657 | 真实 PDF 首章翻译 smoke | `--database-url` 等 | **是**：`test_run_pdf_chapter_smoke.py` 5 用例（3 个纯函数） | 部分（与执行器/CLI translate 重叠） | 待确认；倾向删除，纯函数若有价值移入 `orchestrator` | autopilot 时代产物 |
| `run_real_book_live.py` | 1 227 | 真实书籍 live 翻译 + 报告 | `--database-url`（内部有 8 处 sqlite 逻辑） | **是**：`test_real_book_live_reporting.py` 7 用例 | 是（与 run 控制面 / SSE 重叠） | 待确认；倾向删除（连同 `watch_*`、`*_common.py` 与 7 个测试） | P1.2 说「运维脚本暂不搬目录」，但它是 SQLite 时代的 |
| `watch_real_book_live.py` | 487 | 监控上面的报告 | 报告 json | 同上 | 是 | 同上 | |
| `real_book_live_reporting_common.py` | 132 | 两者共享的遥测字段 | — | 同上 | — | 同上 | |
| `run_real_chapter_followup_smoke.py` | 269 | 章节 review 自动跟进 smoke | DB + UUID | 否 | 是 | 删除 | |
| `pdf_scan_corpus_acceptance.py` | 314 | 扫描语料验收（15 处 sqlite） | 未入库语料 | 否（其测试 P0 已删） | — | 删除 | 无 `__main__`，无入口 |
| `phase3_integration_gate.py` | 183 | lane contract tag 校验 | — | 否（`test_phase3_integration_gate.py` 不 import 它） | — | 删除 | 无 `__main__`；lane 概念已随自愈层删除 |
| `run_postgres_integration.sh` | 9 | 在 compose `app` 容器里跑 PG 集成测试 | Docker 镜像 | 否 | — | 修复或删除 | 镜像 `.dockerignore` 排除 `tests/`，且用 `unittest` 而非 pytest，**当前不可能工作** |

汇总：**删除 25**（含 P4 门槛后删的 5 个交付脚本 + `render_bilingual_subset` 及其 37 测试）、**迁入产品/CLI 6**（`backfill_chapter_memory`、`lock_chapter_concept`、`repair_pdf_prose_artifacts`、`run_minimal_pipeline_smoke`、`run_pdf_smoke*`×3 合一）、**保留运维 4**（`materialize/reap/verify` blob 三件套、`generate_frontend_api_types`）、**待确认 4**（`run_pdf_chapter_smoke`、`run_real_book_live` 三件套）。

---

## 5. 工程工具链现状与缺口

### 5.1 Python 依赖与解释器

| 项 | 现状 | 问题 |
|---|---|---|
| `pyproject.toml` | hatchling；11 个运行依赖；`dev` extra = httpx/pytest/pytest-asyncio/ruff；`[project.scripts] book-agent = book_agent.cli:main`；`[tool.ruff] line-length=100, target py312`（无 `select`，即默认 E4/E7/E9/F）；`[tool.pytest.ini_options] testpaths` | dev 依赖应改为 `[dependency-groups] dev`（uv 原生，`uv sync` 默认安装）；`pytest-asyncio` 无用；缺 `pytest-cov`、`pytest-xdist`、`mypy`/`pyright` |
| `uv.lock` | `version=1, revision=3`；`uv lock --check` 通过；与 pyproject 同在 `c7d78a6`（2026-04-26）最后改动 | 一致。`.dockerignore` 排除了它 → 镜像不按锁安装 |
| 解释器 | pyproject `>=3.12`；`.venv` = Homebrew 3.13.5（`uv 0.8.15`）；Dockerfile `python:3.12-slim`；`ocr.py` 子进程强制 `--python 3.13` | 三处不一致；OCR 在 3.12 镜像里会再下载 3.13 |
| 未声明的可选依赖 | `lxml`（`domain/structure/epub.py:351` 内联 import）、`playwright`（rebuilt PDF）、`torch`/`transformers`/`PIL`（TATR）、`surya-ocr==0.17.1`（子进程 `uv run --with`） | 没有 `[project.optional-dependencies] ocr / pdf-render / tatr` 分组；`lxml` 是否在 `.venv` 里待确认（site-packages 列表中没有 → EPUB 的 lxml 分支从未在本地执行过） |
| 版本号 | `pyproject` 0.1.0、`Settings.app_version` 0.1.0、`frontend/package.json` 0.1.0；git tag 有 `v0.0.1 … v1.2.0`（10 个） | 三处硬编码且与 tag 脱节；无 CHANGELOG |

### 5.2 Lint / Format / Type

| 工具 | 配置 | 当前结果 |
|---|---|---|
| ruff check | 默认规则集 | `src`: 9 错（5 F401 / 2 E402 / 1 E741 / 1 F841）；`tests`: 112 错（72 E402 / 38 F401 / 2 E741）；`scripts`: 53 错 |
| ruff format | 无配置 | `--check src tests`：**226 个文件需重排**，57 个已符合 → 从未跑过 format |
| mypy / pyright | 无 | 代码里有 `# type: ignore[...]` 注释但没有检查器 |
| pre-commit | 无 `.pre-commit-config.yaml` | |
| 前端 lint | 无 eslint / prettier | 仅 `tsc`（通过） |
| E402 大户 | `tests/test_memory_service.py`(25)、`test_postgres_workflow_integration.py`(14)、`test_export_layout_gate.py`(8)、`test_run_control_api.py`(7)、`src/book_agent/domain/structure/epub.py`(2) | 全是 `sys.path` / `os.environ.setdefault` 先于 import 的模式，conftest 后可全部消除 |

### 5.3 CI / 发布

| 项 | 现状 |
|---|---|
| CI | **无**（无 `.github/`、`.gitlab-ci.yml`、`tox.ini`、`Makefile`） |
| 安全扫描 | 无（无 `pip-audit`、`bandit`、dependabot） |
| 发布流程 | 无。tag 存在但没有构建/发布产物；镜像 tag 固定 `book-agent:local` |
| 提交规范 | `main` 上历史提交是自动生成模板（"style: update component styling and layout - 新增16个函数/变量 … across 26 files"）；`refactor/*` 分支改为手写意图（memory 记录用户批准） |
| 分支状态 | 本地 `main@2890024`（2026-05-25）**领先** `origin/main@09922cc`（2026-05-08）28 个提交，从未推送；`refactor/p0-stabilize` 领先本地 main 104 个提交，按约定不推送。远端 `github.com/1055373165/DLEHY.git`（README 写的是 `DLERHY`，拼写不一致） |

### 5.4 容器与部署

| 文件 | 内容 | 问题 |
|---|---|---|
| `Dockerfile` (17 行) | `python:3.12-slim`；`COPY pyproject.toml README.md alembic.ini src alembic`；`pip install -e .`；`mkdir artifacts/exports`；`uvicorn … --port 8000` | 单阶段；**root**；editable 安装（镜像内 `pip install -e` 无意义且拉全套构建链）；不用 `uv`/锁文件（每次构建解析最新版本）；无 `HEALTHCHECK`；无前端（前端只有 Vite dev server，**没有生产静态托管方案**：`app/ui/page.py` 明确说 workspace 是独立 React 前端）；无 OCR/Playwright 系统依赖；体积待确认（未构建） |
| `compose.yaml` | `postgres:16-alpine`（55432→5432，healthcheck）、`migrate`（一次性 `alembic upgrade head`）、`app`（58000→8000，`env_file: .env`，卷 `app_exports`） | `env_file: .env` 硬性要求文件存在（而 `.env.example` 不在仓库）；`uploads` 不在卷里（`BOOK_AGENT_UPLOAD_ROOT` 默认 `artifacts/uploads`，容器重启即丢）；app 无 healthcheck；DB 密码明文 `postgres/postgres`；无前端服务 |
| `.dockerignore` | 排除 `.git .venv __pycache__ artifacts tests docs uv.lock` 等 | 排除 `uv.lock` 与部署可重复性矛盾；未排除 `frontend/node_modules`（137 MB，虽然 Dockerfile 没 COPY 它，但 build context 仍会上传） |
| `dev.sh` (8.7 KB) | 清理旧实例 → `uv sync --quiet` → `docker compose up -d postgres` → alembic → uvicorn `--reload`(8999) → Vite(4173) | `uv sync` 卸 dev 工具；`source .env` 会把 `.env` 里所有键导出到子进程（含 `OPENAI_API_KEY`），随后又覆盖 `DATABASE_URL` |
| `service.sh` (13.6 KB) | start/stop/restart/status/toggle；PID 文件 `.server.pid`/`.frontend.pid`；日志到 `artifacts/{server,frontend}.log`；总是 PG + 迁移 | 同样 `uv sync`；有 `test_service_script.py` 覆盖（用假 uv） |
| `alembic.ini` | `sqlalchemy.url = …localhost:5433/book_agent` | 端口 5433 与 compose 的 55432、`.env.example` 的 55432 都不同；实际被 `env.py` 用 `get_settings().database_url` 覆盖，因此只是误导 |
| `alembic/` | 33 个版本（0001–0033），线性 | 无 downgrade 测试；`env.py` `from book_agent.domain.models import *` |

### 5.5 环境变量

`.env`（工作区存在，被忽略，**未追踪**）键名：`BOOK_AGENT_DATABASE_URL`, `BOOK_AGENT_CORS_ALLOW_ORIGINS`, `BOOK_AGENT_TRANSLATION_BACKEND`, `BOOK_AGENT_TRANSLATION_MODEL`, `BOOK_AGENT_TRANSLATION_TIMEOUT_SECONDS`, `BOOK_AGENT_TRANSLATION_OPENAI_STREAMING`, `BOOK_AGENT_TRANSLATION_MAX_RETRIES`, `BOOK_AGENT_TRANSLATION_RETRY_BACKOFF_SECONDS`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `BOOK_AGENT_SECRET_KEY`。

`.env.example`（工作区存在，**未追踪**）键名：`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `BOOK_AGENT_TRANSLATION_BACKEND`, `BOOK_AGENT_TRANSLATION_MODEL`, `BOOK_AGENT_DATABASE_URL`, `BOOK_AGENT_TRANSLATION_TIMEOUT_SECONDS`, `BOOK_AGENT_TRANSLATION_MAX_RETRIES`, `BOOK_AGENT_TRANSLATION_RETRY_BACKOFF_SECONDS`, `BOOK_AGENT_CORS_ALLOW_ORIGINS`。

差异：`.env` 多 `BOOK_AGENT_TRANSLATION_OPENAI_STREAMING` 与 `BOOK_AGENT_SECRET_KEY`（后者由 `services/secrets.py:43-54` 在缺失时**自动生成并写回 `.env`**——应用运行时修改配置文件，容器内会写进只读/临时层）。`Settings` 共 30+ 个字段（含 `figure_cluster_*`、`ocr_*`、`translation_openai_request_overrides` 等），`.env.example` 只覆盖 9 个，且注释说 dev.sh 用 55432（正确）。`frontend/.env.example`（已追踪）：`VITE_API_BASE_URL`, `VITE_API_PROXY_TARGET`；`frontend/.env`（未追踪）只有注释。

---

## 6. 仓库卫生

### 6.1 git 追踪面

- `git status` 干净；`git ls-files` **498** 个文件：tests 176、src 173、scripts 39、frontend 38、alembic 37、`.agents` 16、interview 4、docs 3、tasks 1、其它 11。
- 无二进制被追踪（`png/pdf/epub/zip/db` 均无）；最大追踪文件：`tests/golden/translation_prompts/pdf.json`(1.0 MB)、`epub.json`(984 KB)、`test_persistence_and_review.py`(500 KB)、`test_pdf_support.py`(488 KB)、`domain/structure/pdf.py`(204 KB)、`uv.lock`(188 KB)、`scripts/export_chapter_zh_html.py`(188 KB)。
- 仍被追踪但不该在产品仓库里的：

| 路径 | 内容 | 建议 |
|---|---|---|
| `.agents/skills/elegant-design/`（16 个 md，112 KB）+ `.claude/skills/elegant-design`（symlink → 前者） | AI 编码助手的 UI 设计 skill | 移出仓库或放 `.gitignore`；与产品无关 |
| `interview/`（4 个 md，48 KB） | 面试准备材料（项目陈述、架构讲解） | 移出仓库 |
| `tasks/pdf-pipeline-v2.md`（29 KB） | 05-08 的 M1 实施计划，引用不存在的 `specs/pdf-v2` | 归档到 `docs/archive/` 或删除 |
| `docs/README.md` | 与根 README 内容重叠的第二份项目介绍 | 合并 |
| `frontend/package-lock.json` (132 KB) | 正常 | 保留 |

### 6.2 工作区未追踪 / 忽略目录

| 路径 | 大小 | 是什么 | 追踪 | 建议 |
|---|---|---|---|---|
| `.test-tmp/` | **3.7 GB**，14 989 个条目 | `book-agent-uv-cache/` 3.4 GB（OCR 子进程 uv 缓存）；`rsi/` 149 MB（用户提供的 RSI 测试书：`rsi-book.pdf`、5 份 16 MB SQLite 快照、glossary CSV、images）；`backups/book_agent-before-rsi-import.dump` 3.9 MB；`blobs/` 256 KB；14 729 个空 `book-agent-pdf-images-*`、45 个 `finalize-stage-*`、69 个 `stage-gate-phase0-*`、9 个 `review-skip-*`、4 个 `book-agent-test-epub-*`、62 个 `tmp*.surya.*.log`；llm-book 时代的 `ch1..ch9-export/`、`*-translate.log`、`llm-book-full-bilingual.{html,md}`、`_chapter_cover_cache.json`、`_sidebar_callout_cache.json`、`translation-logs/`、`torchinductor_smy/` | 否（忽略） | 把 `UV_CACHE_DIR` 指到 `~/.cache/uv`（或让 OCR 复用宿主 uv 缓存）；删除 14 700 个空目录；`rsi/` 与 `backups/` 迁到 `.local-fixtures/` 之类明确命名的目录；llm-book 残留在 P4 脚本删除后一并清除 |
| `artifacts/` | 99 MB | `parse-ir/` 88 MB / 8 362 个目录（**测试污染**，§0-3）；`exports/`、`uploads/`、`pdf-images/` 为 RSI 书的开发数据；`runtime-bundles/` 39 个目录（148 KB，自愈层遗物，代码已无引用）；`server.log`、`frontend.log` | 否（忽略；P1.3 已从 git 删除 202 个文件） | 让 `ParseIrService` 走 `Settings`（如 `parse_ir_root`）并在测试里指到 tmp；删 `runtime-bundles/` |
| `deliverable/` | 63 MB | llm-book 四种交付 bundle + 2 个 zip | 否 | 移出仓库目录（版权风险 + 与代码无关） |
| `LLM-Book/`、`LLM-Book-review-ch1-ch2/` | 9.2 MB + 1.1 MB | 整书双语 md + assets | 否 | 同上 |
| `books/build-an-ai-agent.epub` | 7.2 MB | 测试用原版书 | 否 | 同上（版权） |
| `.scratch/` | 44 KB | 05-08 的分块翻译脚本与进度 json | 否 | 删除 |
| `.venv/` | 144 MB | uv venv（3.13.5） | 否 | 正常 |
| `frontend/node_modules/` | 137 MB | | 否 | 正常 |
| `frontend/dist/` | 364 KB（04-22 构建） | 过期构建产物 | 否（`dist/` 忽略） | 删除；确定生产托管方案 |
| `frontend/artifacts/runtime-bundles/` | 0 B | 自愈层遗物 | 否 | 删除 |
| `frontend/.omc/` | — | 某工具状态 | 否（忽略） | — |
| `.env` | 634 B | 含真实密钥（`OPENAI_API_KEY`、`BOOK_AGENT_SECRET_KEY`） | 否（忽略） | 正常，但注意 `dev.sh` `source .env` 与 `compose env_file` 都会把它整份注入 |
| `.env.example` | 834 B | 模板 | **否（被 `.env.*` 规则误忽略）** | 修 `.gitignore`（`!.env.example`）并提交 |
| `book-agent.db` | 0 B | 空文件（05-08） | 否 | 删除 |
| `.pytest_cache/`、`.ruff_cache/` | 160 KB / 200 KB | 缓存；`.pytest_cache` 里 `lastfailed` 记录 1 082 个 nodeid（旧） | 否 | 正常 |
| `.claude/settings.local.json`、`.claude/scheduled_tasks.lock` | 8 KB | 个人助手配置（含 Playwright 截图权限等） | 否（P1.3 加入忽略） | 正常 |
| `.DS_Store` ×5（根、src、src/book_agent、alembic、frontend） | — | macOS 元数据 | 否 | 正常（已忽略） |

### 6.3 最近 30 个提交的模式

`git log --stat -30`：全部是 09-14 ～ 09-15 的 refactor 分支提交，手写 conventional 前缀（`fix(pdf)`、`feat(terminology)`、`test(export)`、`docs(refactor)`），每个提交 1–6 个文件、几乎都「产品文件 + 对应测试文件」成对出现；只有 `3dc15d7` 顺带更新了 golden 快照。`main` 上的历史则是大批量自动模板提交（一次 119 个文件）。

---

## 7. 问题清单（含路径）

按严重度排序，S = 阻断/欺骗性，A = 必须在生产化前修，B = 应修，C = 清理。

| 级别 | 问题 | 路径 |
|---|---|---|
| S | `.env.example` 未追踪，README 首步 `cp .env.example .env` 在新克隆上失败；`compose.yaml` `env_file: .env` 硬性依赖它 | `.gitignore:5`、`README.md:38,52`、`compose.yaml:24,36` |
| S | `uv sync`（dev.sh/service.sh）卸掉 pytest/ruff；README 的 `uv run pytest` / `uv run ruff check` 在此之后失败 | `pyproject.toml:26-31`、`dev.sh:186`、`service.sh:142`、`README.md:250-253` |
| S | 无 CI；ruff check 121 错、ruff format 226 文件未格式化；README 声称提交前必须 lint | `pyproject.toml:35-41`、`README.md:332` |
| A | 测试把产物写进 `artifacts/parse-ir/`（8 362 目录，88 MB） | `src/book_agent/services/parse_ir.py:91`、`src/book_agent/services/bootstrap.py:262`；`tests/test_api_workflow.py`、`tests/test_persistence_and_review.py`、`tests/test_pdf_support.py` 未覆盖 `output_root` |
| A | OCR 子进程 uv 缓存落在 tempdir → 测试环境下变成 `.test-tmp/book-agent-uv-cache` 3.4 GB | `src/book_agent/ingestion/pdf/ocr.py:148-152`、`tests/__init__.py:34-38` |
| A | Dockerfile：单阶段、root、`pip install -e .`、无锁文件、无 HEALTHCHECK、无前端、无可选依赖系统库；`.dockerignore` 排除 `uv.lock`/`tests` | `Dockerfile`、`.dockerignore` |
| A | `scripts/run_postgres_integration.sh` 在镜像里跑 `unittest tests.…`，但镜像没有 `tests/` | `scripts/run_postgres_integration.sh:8-9`、`.dockerignore:13` |
| A | `compose.yaml` 未把 `artifacts/uploads` 做成卷；上传原文件随容器消失，而 `exports`/`parse-ir` 记录仍引用它 | `compose.yaml:39-41`、`src/book_agent/core/config.py:63-64` |
| A | 应用运行时自动生成 `BOOK_AGENT_SECRET_KEY` 并写回仓库 `.env` | `src/book_agent/services/secrets.py:34-89` |
| A | PG-only 行为（SSE、cost_rollup、并发锁、schema drift）默认从不测试；两个 PG 测试的跳过条件不看 `RUN_PG_TESTS`，可能打到开发库 | `tests/test_events_bus.py:30-41`、`tests/test_run_stream_sse.py:33-44` |
| A | 36 个模块无直接测试，含 `application/*` 7 个用例模块、`routes/run_cost.py`、`routes/run_stream.py`、`services/epub_structure_refresh.py`；无覆盖率度量 | 见 §3.1 |
| A | 可选依赖（lxml / playwright / torch / transformers / PIL / surya）未在 `pyproject` 声明分组；`lxml` 未安装在 `.venv` | `src/book_agent/domain/structure/epub.py:351`、`services/export.py:2633`、`services/tatr_extractor.py:319-375`、`ingestion/pdf/ocr.py:88-135` |
| A | 解释器版本三处不一致（>=3.12 / 3.12-slim / 3.13 venv / OCR 强制 3.13） | `pyproject.toml:10`、`Dockerfile:1`、`.venv/pyvenv.cfg`、`ocr.py:117-119` |
| B | 没有 conftest；48 处 create_all、151 处 TemporaryDirectory、74 处 E402、4 份归一化正则重复 | `tests/*.py`（§2.2） |
| B | 8 个测试文件仍用 `StaticPool`（段错误根因模式），其中 2 个起执行器线程 | `tests/test_pdf_support.py`、`tests/test_executor_worker_resolution.py` 等 |
| B | 6 个测试模块 import `test_api_workflow`（3 738 行）只为拿常量；collect 需 15 s | `tests/export_golden_scenario.py:22`、`tests/workflow_golden_scenario.py:23`、`tests/translation_prompt_scenario.py:17-18` 等 |
| B | 3 个 `expectedFailure` 代表产品缺陷（PDF 引言线索、前言页族、STALE_CHAPTER_BRIEF），基线文档仍写 4 个且漏列 `test_postgres_run_concurrency` | `tests/test_pdf_support.py:4222,4239`、`tests/test_persistence_and_review.py:11331`、`docs/refactor/baseline-tests.md` |
| B | `alembic.ini` 端口 5433 与 compose/`.env.example` 的 55432 不一致（被 env.py 覆盖，仅误导） | `alembic.ini:4`、`compose.yaml:10`、`.env.example:12` |
| B | 版本号硬编码三处（0.1.0）且与 git tag（v1.2.0）脱节；无 CHANGELOG | `pyproject.toml:7`、`src/book_agent/core/config.py:54`、`frontend/package.json:4` |
| B | 前端无生产托管方案（后端只提供 `app/ui/page.py` 占位页；`frontend/dist` 是 04-22 的过期构建） | `src/book_agent/app/ui/page.py:17,147`、`frontend/dist/` |
| B | README 远端仓库名 `DLERHY` 与实际 `DLEHY` 不一致；本地 `main` 领先 `origin/main` 28 个提交未推送 | `README.md:38,50`、`git remote -v` |
| B | `pytest-asyncio` 声明但零使用；`pytest-xdist`、`pytest-cov` 缺失 | `pyproject.toml:26-31` |
| B | `dev.sh` 用 `set -a; source .env` 把全部密钥导出到所有子进程（含前端 npm） | `dev.sh:159-164` |
| B | `chapters_config.json` 引用不存在的 `scripts/regenerate_chapters_config.py`；`export_chapter_md_official.py` 引用不存在的 `.sh` | `scripts/chapters_config.json:2`、`scripts/export_chapter_md_official.py:8-9` |
| B | `scripts/phase3_integration_gate.py`、`pdf_scan_corpus_acceptance.py` 无入口、无引用 | `scripts/` |
| B | `scripts/repair_pdf_prose_artifacts.py` 只接受 SQLite `--input-db`，与 PG-only 冲突 | `scripts/repair_pdf_prose_artifacts.py:18` |
| C | `.test-tmp` 内 14 729 个空 `book-agent-pdf-images-*`（09-14 之前泄漏）、69+45+9 个旧 mkdtemp 目录、62 个 surya 日志 | `.test-tmp/` |
| C | `artifacts/runtime-bundles/`（39 目录）、`frontend/artifacts/runtime-bundles/` 是已删自愈层遗物 | `artifacts/runtime-bundles/`、`frontend/artifacts/` |
| C | `.agents/skills/elegant-design`、`.claude/skills/elegant-design`（symlink）、`interview/`、`tasks/pdf-pipeline-v2.md`、`docs/README.md` 被追踪但与产品无关/过期 | 见 §6.1 |
| C | `deliverable/`、`LLM-Book*/`、`books/`、`.scratch/`、`book-agent.db`（0 B）仍在工作区 | `ROOT/` |
| C | `tests/test_golden_pdf_regression.py` 命名误导（不是快照）；`tests/test_phase3_integration_gate.py` 沿用已废弃脚本名 | `tests/` |
| C | `tests/test_executor_run_loop.py:36` 与 `tests/test_pdf_support.py:6115` 硬编码项目相对/`/tmp` 路径 | 同左 |
| C | `.pytest_cache/v/cache/lastfailed` 记录着旧的失败（`test_persistence_and_review…legacy_db_without_document_images_table` 等），与当前基线无关 | `.pytest_cache/` |
| C | `src/book_agent/services/tatr_extractor.py` 2 个未用 import、`domain/structure/epub.py` 2 处 E402、1 处 F841 —— `src` 侧 ruff 9 错可一次 `--fix` | `ruff check src` |

---

## 8. 附：本次实际执行过的命令与结果（便于复现）

| 命令 | 结果 |
|---|---|
| `git ls-files \| wc -l` | 498 |
| `uv lock --check` | Resolved 45 packages（一致） |
| `uv sync --extra dev --quiet` | 恢复 `.venv/bin/{pytest,ruff}`（**改变了 `.venv`**，未动仓库文件） |
| `.venv/bin/ruff check src tests` | 121 errors（43 可自动修） |
| `.venv/bin/ruff check scripts` | 53 errors |
| `.venv/bin/ruff format --check src tests` | 226 would reformat / 57 formatted |
| `.venv/bin/python -m pytest --collect-only -q -p no:cacheprovider -o addopts=""` | 1100 tests collected in 15.21s |
| 逐文件计时（负载 12–16） | export_golden 8 s / workflow_golden 4 s / translation_prompt_golden 5 s / pdf_structure_golden 7 s / run_lineage 2 s / frontend_api_types 3 s / service_script 6 s / cli 3 s，全部 passed |
| `cd frontend && npx vitest run` | 5 files / 6 tests passed |
| `cd frontend && npx tsc --noEmit` | exit 0 |
| AST 校验 36 个脚本的 `book_agent` import | 全部可解析 |
| `docker images book-agent:local` | 无（镜像未构建，体积待确认） |

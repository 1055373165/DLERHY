# 01 · 应用层与 API 层

> 来源：2026-09-16 对分支 `refactor/p0-stabilize`（HEAD `2176d51`）的逐文件源码阅读。行号对应该基线；标注「待确认」的条目未经运行验证。总览与跨子系统结论见 [`00-overview.md`](00-overview.md)。

# book-agent 应用层 / API 层源码认知笔记

> 阅读对象：`src/book_agent/app/**`、`schemas/**`、`core/**`、`cli.py`、`services/{provider_credentials,secrets,actions}.py`、`infra/db/**`、相关测试与部署脚本。
> 方法：逐文件完整阅读；对 `app/runtime/document_run_executor.py`（1800 行，属编排层）只读了与应用层耦合的生命周期部分（L60–330、L1788–1800）。
> 所有 `文件:行号` 均指当前工作树（分支 `refactor/p0-stabilize`，HEAD `2176d51`）。标注「待确认」的地方是我未能从源码直接证实的推断。
> 路由清单已通过实际构造 `create_app()` 并遍历 `app.routes` 核对（见 §4）。

---

## 0. 一页摘要

- FastAPI 单进程应用；`create_app()` 在模块导入时执行（`app/main.py:139`），构造期就会读取 Settings 并做 scope 校验。
- **无鉴权、无限流、无请求 ID、无结构化日志、无上传大小限制、无请求超时**；CORS 仅在配置了 origin 时启用。
- 数据访问：每请求一个 SQLAlchemy Session（`deps.py:22-26`），GET 回滚、非 GET 提交；多个 GET 路由自行 `commit()`（有副作用的读）。
- 后台执行器 `DocumentRunExecutor` 与 API 同进程，用 daemon 线程池跑 run；lifespan 退出时若工作线程未退出则**不 dispose engine**（`main.py:62-74`）。
- SSE 每个订阅者独占一条 psycopg 连接（来自同一个连接池，`pool_size=10, max_overflow=20`），并在线程池里 1s 阻塞轮询。
- Provider API key 用 Fernet 存 DB；密钥来自 `BOOK_AGENT_SECRET_KEY`，缺失时**自动生成并写回项目 `.env`**（容器里会丢）。
- CLI 与 API 共享 `DocumentWorkflowService`，但 CLI 的 translate/review/export 走**同步旧路径**，绕过 run 控制面（无 DocumentRun/lease/budget/events）。
- 最重要的具体缺陷见 §17（含 Swagger 链接指向不存在的 `/v1/docs`、Docker 部署下 `.env` 的 `OPENAI_API_KEY` 永远读不到、`ValueError→404` 的过度映射等）。

---

## 1. 应用组装：`app/main.py`

### 1.1 `create_app()` 执行顺序（`main.py:50-136`）

| 步骤 | 位置 | 说明 |
|---|---|---|
| 1 | `main.py:51` | `settings = get_settings()`（`lru_cache(maxsize=1)`，进程级单例） |
| 2 | `main.py:52` | `validate_app_scope(settings)`：DEV/PROD 必须 `postgresql*`，SMOKE/E2E 必须 `sqlite*`，否则抛 `AppScopeViolation`（`config.py:170-188`）。**在导入期抛出**，uvicorn 启动即失败 |
| 3 | `main.py:53` | `configure_logging(settings.log_level)` → `logging.basicConfig` |
| 4 | `main.py:76-81` | `FastAPI(title, version, description, lifespan)`。**未设置** `docs_url/openapi_url/root_path` → 文档在 `/docs`、`/openapi.json`、`/redoc`（已实测），不在 `/v1/` 下 |
| 5 | `main.py:82-90` | 仅当 `settings.cors_allow_origins` 非空时加 `CORSMiddleware`：`allow_credentials=True`、`allow_methods=["*"]`、`allow_headers=["*"]`、`expose_headers=["Content-Disposition"]` |
| 6 | `main.py:91-97` | 初始化 `app.state`（见 1.3） |
| 7 | `main.py:99-110` | 构造 `TranslationWorkerProvider(settings, session_factory=_session_factory)`；`_session_factory` 是惰性闭包，首次调用会触发 `_ensure_database_state` |
| 8 | `main.py:112-116` | `app.state.resolve_translation_worker`：优先 `app.state.translation_worker` 覆盖（测试/嵌入用），否则 `translation_worker_provider.get()` |
| 9 | `main.py:118-132` | 注册两个异常处理器（见 §14） |
| 10 | `main.py:134-135` | `include_router(ui_router)`（`/`），`include_router(api_router, prefix=settings.api_prefix)` |
| 11 | `main.py:139` | 模块级 `app = create_app()` |

### 1.2 lifespan（`main.py:55-74`）

启动：
1. `_ensure_database_state(app, settings)`（`main.py:40-47`）：若 `app.state.session_factory` 已存在（测试注入的情况）直接返回；否则 `build_engine(database_url)` → 设置 `app.state.engine / database_dialect_name / session_factory`。
2. `ensure_document_run_executor(app)`（`document_run_executor.py:69-85`）：若 `app.state.document_run_executor` 已存在直接返回；否则再次调用 `ensure_database_state`，构造 `DocumentRunExecutor(session_factory, export_root, translation_worker, translation_worker_resolver)` 并 `start()`。

停止（`finally` 块）：
1. `executor.stop()`（`document_run_executor.py:166-203`）：置 stop/wake 事件 → join supervisor ≤5s → join 各 run 线程 ≤5s → join 各 work 线程 ≤30s（`work_timeout_seconds`）→ 返回是否全部退出；随后**清空线程注册表**（即便线程仍活着）。
2. `app.state.document_run_executor = None`。
3. 若 `executor_stopped` 为真且 engine 存在 → `engine.dispose()`；否则打 warning「Leaving the database engine open」并保留 engine（`main.py:67-74`）。设计意图（注释 L72-73 与提交 `1bc3d3f`）：work 线程可能卡在不可中断的 LLM 调用中，结束后仍需用 engine 写回结果；dispose 会让它们拿到失效连接。

注意点：
- 启动阶段没有对数据库做连通性验证；`build_engine` 不连库（`pool_pre_ping` 只在取连接时生效）。DB 不可达时进程照常起来，首个请求得到 503（`test_app_runtime.py:121-154` 覆盖）。
- 停止阶段最坏阻塞 5 + 5·N_run + 30·N_work 秒（串行 join），远超 uvicorn 默认 graceful 期与 `service.sh` 的 5 秒 kill 窗口（`service.sh:240-247`）。
- 没有停止 `TranslationWorkerProvider`（它无资源需要释放）；没有关闭 SSE 监听连接（它们随请求取消而 `invalidate`）。

### 1.3 `app.state` 全表

| 键 | 初值（`main.py`） | 后续写入者 | 读取者 |
|---|---|---|---|
| `engine` | `None` (L91) | `_ensure_database_state` L45；lifespan L70 置 None | lifespan |
| `database_dialect_name` | `"postgresql"` (L92) | `_ensure_database_state` L46 | 无其他读取者（死状态；`run_stream/run_cost` 直接看 `engine.dialect.name`） |
| `session_factory` | `None` (L93) | `_ensure_database_state` L47；`deps.get_session_factory` L18（兜底 `build_session_factory()`）；测试直接赋值 | `deps.py`、`ensure_document_run_executor`、`_session_factory` 闭包 |
| `ensure_database_state` | lambda (L94) | — | `deps.py:12-14`、`document_run_executor.py:73-75` |
| `export_root` | `str(settings.export_root)` (L95) | 测试覆盖 | `documents.py:55,82`、`actions.py:21`、executor L79 |
| `upload_root` | `str(settings.upload_root)` (L96) | 测试覆盖 | `documents.py:50` |
| `document_run_executor` | `None` (L97) | `ensure_document_run_executor` L84；lifespan L66 | `runs.py:27`、`documents.py:524` |
| `translation_worker` | `None` (L106) | 测试注入（如 `test_api_workflow.py:1120`） | `_resolve_translation_worker` L113、executor L80 |
| `translation_worker_provider` | `TranslationWorkerProvider` (L107) | — | `_resolve_translation_worker`、`providers.py:169` (`invalidate()`) |
| `resolve_translation_worker` | 闭包 (L116) | — | `documents.py:83`、`actions.py:22`、executor L76、`tests/document_actions.py:73` |

### 1.4 执行器与 engine 的生命周期关系

- 执行器持有的是 `session_factory`（绑定到同一个 engine），所有工作都在 `session_scope(self.session_factory)` 中开短事务（executor L148、L312、L328 等）。
- 执行器线程全部 `daemon=True`（L162、L283、L306、L908），进程退出时不会等待它们；只有 lifespan 的 `stop()` 提供有界等待。
- `ensure_document_run_executor` 在 **API 请求路径**里也会被调用（`runs.py:27`、`documents.py:524`），所以即使 lifespan 没跑（如 `TestClient` 不用 `with`），第一次 POST 也会拉起执行器。反过来 lifespan 停止后若再有请求进来，会重新拉起一个执行器（L66 置 None → L69-84 重建）。
- `wake(run_id)`（L205-213）只 set 事件并顺手清理已死线程；supervisor 每 `poll_interval_seconds=1.0` 也会自行扫描 RUNNING/DRAINING 的 run（L311-324），因此 wake 是优化不是必要条件。

---

## 2. 依赖与会话：`app/api/deps.py`、`infra/db/session.py`

### 2.1 `get_session_factory(request)`（`deps.py:9-19`）
1. 取 `app.state.session_factory`；
2. 为空则调用 `app.state.ensure_database_state()`；
3. 仍为空则 `build_session_factory()`（用 Settings 默认 URL 新建 engine）并写回 `app.state`。第 3 步是给「非 `create_app()` 构造的 app」（如 `test_run_stream_sse.py:48-50` 的裸 `FastAPI()`）兜底，但它会绕过 `app.state.engine` 的登记，lifespan 无法 dispose 这个 engine。

### 2.2 `get_db_session(request)`（`deps.py:22-26`）
- `commit_on_exit = method not in {GET, HEAD, OPTIONS}`。
- 走 `session_scope`（`session.py:40-53`）：正常退出 commit 或 rollback；异常 rollback 并重抛；finally close。
- **HTTPException 也是 Exception** → 路由里抛 4xx 时整个事务回滚。这是隐式约定：例如 `providers.test_provider` 若 `record_test_outcome` 后再抛异常，测试结果不会落库。
- 提交发生在依赖 teardown 中。当前 `.venv` 安装的是 FastAPI 0.135.1 / Starlette 0.52.1；yield 依赖的 teardown 相对响应发送的时序在 0.106 与之后的版本间有过调整（待确认 0.135.1 的确切行为），因此 `documents.py:122-124 / 146-148` 的显式 `session.commit()` 注释（「commit before returning so a follow-up read ... resolves immediately」）在现版本上多半是冗余保险，不是错误。

### 2.3 哪些路由自己 commit（副作用性读 / 提前提交）

| 路由 | 位置 | 原因 |
|---|---|---|
| `POST /documents/bootstrap` | `documents.py:124` | 让后续 GET 立刻可见 |
| `POST /documents/bootstrap-upload` | `documents.py:148` | 同上 |
| `POST /documents/{id}/translate|review|export` | `documents.py:523` | **必须**：执行器在其他线程/会话读取 run，唤醒前须落库 |
| `GET /providers` | `providers.py:50` | GET 默认回滚，但首次访问会从 `.env` 播种凭据行，须持久化 |
| `GET /providers/active` | `providers.py:161` | 同上 |

依赖 `SessionLocal`（即 `session_factory`）而非 `get_db_session` 的路由：
- `GET /health`（`health.py:13-15`）：直接 `session_factory()` 执行 `SELECT 1`。
- `GET /runs/{id}/stream`（`run_stream.py:81-82`）：`session_factory.kw["bind"]` 取 engine，用 `engine.raw_connection()` 与 `engine.connect()`。
- `GET /runs/{id}/cost`（`run_cost.py:33-34`）：同上，`engine.begin()`。
- `TranslationWorkerProvider.get()`（`workers/factory.py:172-175`）：自己开 session 并 commit（可能播种凭据）。

### 2.4 engine 配置（`session.py:11-28`）
- `pool_pre_ping=True`；非 sqlite 时 `pool_size=10, max_overflow=20`（上限 30 连接/进程）。
- 未设置 `pool_timeout`（默认 30s 阻塞等待）、`pool_recycle`、`connect_args`（无 `statement_timeout`、`application_name`、`connect_timeout`）。
- `build_engine` 内部再次调用 `get_settings()` 做 SMOKE/E2E 的 sqlite 强制（L18-22），但**不**强制 DEV/PROD 使用 PG（那只在 `validate_app_scope` 里）→ CLI 和测试可以在 DEV scope 下用 sqlite（`test_cli.py:77-86`）。
- sessionmaker：`autoflush=False, autocommit=False, expire_on_commit=False`（L31-37）。`autoflush=False` 意味着服务层必须显式 `flush()` 才能让同事务内的查询看到新行——代码里大量 `session.flush()` 就是这个原因。

### 2.5 `infra/db/base.py`
- `JsonDocument = JSON().with_variant(JSONB(), "postgresql")`（L15）。
- `enum_value_type`：非原生枚举、存 value、`validate_strings=True`（L18-26）；`install_enum_check_constraints` 给每个枚举列追加 CHECK，让 `create_all`（sqlite 测试）与迁移（PG）语义一致（L51-68）。
- 主键 `Uuid(as_uuid=False)` 存字符串 uuid4（L29-30）；时间戳 `server_default=func.now()`，`updated_at` 带 `onupdate`（L33-42）。

---

## 3. 路由挂载：`app/api/router.py`

```
api_router
├─ health.router            tags=[system]     (/health, /meta)
├─ documents.router  /documents
├─ actions.router    /actions
├─ runs.router       /runs
├─ run_stream.router /runs
├─ run_cost.router   /runs
└─ providers.router  /providers
```
`main.py:135` 以 `settings.api_prefix`（默认 `/v1`）挂载；`ui_router` 挂根路径且 `include_in_schema=False`（`ui/router.py:10`）。

---

## 4. REST 端点清单

鉴权：**所有端点均无鉴权**（无 `Depends(security)`、无中间件、无 API key）。前端 `frontend/src/lib/api.ts` 也不发送任何 `Authorization` 头（grep 无结果）。

图例：写库=是否在请求事务中产生写；入队=是否创建/唤醒 run。

### 4.1 系统

| 方法 | 路径 | 请求 | 响应 | 状态码 | 写库 | 位置 | 备注 |
|---|---|---|---|---|---|---|---|
| GET | `/` | — | HTML | 200 | 否 | `ui/router.py:13-22` | 静态入口页，链接指向 `{api_prefix}/docs`（**404，见 §17-1**） |
| GET | `/v1/health` | — | `HealthResponse{status}` | 200 / 503 | 否 | `health.py:11-16` | `SELECT 1`；DB 挂 → `OperationalError` → 503 |
| GET | `/v1/meta` | — | `MetaResponse{app_name,version,environment,api_prefix,docs_path}` | 200 | 否 | `health.py:19-28` | `docs_path` 是服务器绝对路径（信息泄露，低） |
| GET | `/docs`, `/redoc`, `/openapi.json` | — | — | 200 | 否 | FastAPI 默认 | 无鉴权公开 |

### 4.2 documents（`routes/documents.py`）

| 方法 | 路径 | 请求 | 响应 | 状态码 | 写库 | 入队 | 位置 |
|---|---|---|---|---|---|---|---|
| GET | `/v1/documents/contract` | — | `DocumentContractResponse` | 200 | 否 | 否 | L95-105（硬编码常量） |
| POST | `/v1/documents/bootstrap` | `BootstrapDocumentRequest{source_path}` | `DocumentSummaryResponse` | 201 / 404(文件不存在) / 400(ValueError) | 是 | 否 | L108-127；**接受任意服务器路径**（见 §17-6） |
| POST | `/v1/documents/bootstrap-upload` | multipart `source_file` | `DocumentSummaryResponse` | 201 / 400 | 是（+写磁盘 `upload_root/<uuid>/<name>`） | 否 | L130-157；仅校验后缀 `.epub/.pdf`；无大小限制 |
| GET | `/v1/documents/history` | query: `limit(1..200)=20, offset, query(1..200), source_type, status, latest_run_status, merged_export_ready` | `DocumentHistoryPageResponse` | 200 | 否 | 否 | L160-181 |
| POST | `/v1/documents/history/backfill` | — | `DocumentHistoryBackfillResponse{imported_document_count:0}` | 200 | 否 | 否 | L184-191；**死端点**（SQLite 回填已移除） |
| GET | `/v1/documents/{document_id}` | — | `DocumentSummaryResponse` | 200 / 404 | 否 | 否 | L194-204 |
| DELETE | `/v1/documents/{document_id}` | — | 空 | 204 / 404 / 409(`DocumentBusyError`) | 是（FK CASCADE 硬删） | 否 | L207-219；不删除磁盘上的上传/导出文件 |
| GET | `/v1/documents/{document_id}/exports` | query: `export_type, status(alias), limit(1..200)=50, offset` | `DocumentExportDashboardResponse` | 200 / 404 | 否 | 否 | L222-242 |
| GET | `/v1/documents/{document_id}/chapters` | — | **裸 `list[dict]`，无 response_model** | 200（未知文档也 200 空列表） | 否 | 否 | L245-271；直接 ORM 查询、函数内 import，未走服务层 |
| GET | `/v1/documents/{document_id}/chapters/worklist` | query: `queue_priority, sla_status, owner_ready, needs_immediate_attention, assigned, assigned_owner_name, limit, offset` | `DocumentChapterWorklistResponse` | 200 / 404 | 否 | 否 | L274-302 |
| GET | `/v1/documents/{document_id}/chapters/{chapter_id}/worklist` | — | `DocumentChapterWorklistDetailResponse` | 200 / 404 | 否 | 否 | L305-322 |
| PUT | `.../chapters/{chapter_id}/worklist/assignment` | `ChapterWorklistAssignmentRequest{owner_name,assigned_by,note?}` | `ChapterWorklistAssignmentResponse` | 200 / 404 | 是 | 否 | L325-346 |
| POST | `.../worklist/assignment/clear` | `ChapterWorklistAssignmentClearRequest{cleared_by,note?}` | `ChapterWorklistAssignmentClearResponse` | 200 / 404 | 是 | 否 | L349-376 |
| GET | `.../chapters/{chapter_id}/exports/download` | query `export_type=bilingual_html` | `FileResponse`（单文件或 zip） | 200 / 404 / 410 | 否 | 否 | L379-393 → `export_downloads.chapter_export_response` |
| GET | `/v1/documents/{document_id}/exports/download` | query `export_type`（必填） | `FileResponse` | 200 / 404 / 410 | 否 | 否 | L396-408 → `document_export_response` |
| GET | `/v1/documents/{document_id}/exports/{export_id}` | — | `ExportDetailResponse` | 200 / 404 | 否 | 否 | L411-422 |
| GET | `.../chapters/{chapter_id}/memory-proposals` | query `status(alias)` | `ChapterMemoryProposalListResponse` | 200 / 404 / 409 | 否 | 否 | L425-450；错误码由消息文本猜（L87-92） |
| POST | `.../memory-proposals/{proposal_id}/approve` | `ChapterMemoryProposalDecisionRequest?` | `ChapterMemoryProposalDecisionResponse` | 200 / 404 / 409 | 是 | 否 | L453-475 |
| POST | `.../memory-proposals/{proposal_id}/reject` | 同上 | 同上 | 200 / 404 / 409 | 是 | 否 | L478-500 |
| POST | `/v1/documents/{document_id}/translate` | `TranslateDocumentRequest{packet_ids[]}` | `DocumentRunSummaryResponse` | 202 / 404 | 是 | **是**（`translate_targeted`，create+resume+commit+wake） | L528-545 |
| POST | `/v1/documents/{document_id}/review` | — | 同上 | 202 / 404 | 是 | 是（`review_full`） | L548-564 |
| POST | `/v1/documents/{document_id}/export` | `ExportDocumentRequest{export_type, auto_execute_followup_on_gate=false, max_auto_followup_attempts≥1=3}` | 同上 | 202 / 404 | 是 | 是（`export_full`） | L567-588 |

`_enqueue_document_run`（L503-525）：`RunControlService.create_run(requested_by="api.documents", status_detail_json={"source":..., "run_request": {...}})` → `resume_run(actor_id="api.documents", note="enqueued")`（QUEUED→RUNNING，`run_control.py:366-383`）→ `session.commit()` → `ensure_document_run_executor(app).wake(run_id)`。

### 4.3 actions（`routes/actions.py`）

| 方法 | 路径 | 请求 | 响应 | 状态码 | 写库 | 位置 |
|---|---|---|---|---|---|---|
| POST | `/v1/actions/{action_id}/execute?run_followup=false` | — | `ExecuteActionResponse` | 200 / 404(ValueError) / 502/503(Provider 错误) | 是 | L11-57 |

`run_followup=true` 时**在请求线程内同步执行重译**（`DocumentWorkflowService.execute_action`），会调用 LLM——无超时保护，请求可能持续数分钟。`ExecuteActionResponse.status` 恒为 `"completed"`（L30）。

### 4.4 runs（`routes/runs.py`、`run_stream.py`、`run_cost.py`）

| 方法 | 路径 | 请求 | 响应 | 状态码 | 写库 | 入队 | 位置 |
|---|---|---|---|---|---|---|---|
| POST | `/v1/runs` | `CreateDocumentRunRequest{document_id, run_type, requested_by, backend?, model_name?, priority≥0=100, resume_from_run_id?, status_detail_json{}, budget?}` | `DocumentRunSummaryResponse` | 201 / 404 | 是 | wake（但状态为 `queued`，执行器只调度 RUNNING/DRAINING → 须再 `/resume`） | L113-139 |
| GET | `/v1/runs/{run_id}` | — | `DocumentRunSummaryResponse` | 200 / 404 | 否 | 若 running/draining 则 wake（L30-32、L152） | L142-153 |
| GET | `/v1/runs/{run_id}/lineage` | — | `RunLineageResponse` | 200 / 404 | 否 | 否 | L181-190 |
| GET | `/v1/runs/{run_id}/events` | `limit(1..200)=50, offset` | `RunAuditEventPageResponse` | 200 / 404 | 否 | 否 | L193-204 |
| POST | `/v1/runs/{run_id}/pause` | `RunControlRequest{actor_id, note?, detail_json{}}` | summary | 200 / 404 / 409(`RunControlTransitionError`) | 是 | wake | L207-226 |
| POST | `/v1/runs/{run_id}/resume` | 同上 | summary | 200/404/409 | 是 | wake | L229-248 |
| POST | `/v1/runs/{run_id}/retry` | 同上 | summary（新 run） | 200/404/409 | 是 | wake | L251-270 |
| POST | `/v1/runs/{run_id}/drain` | 同上 | summary | 200/404/409 | 是 | wake | L273-292 |
| POST | `/v1/runs/{run_id}/cancel` | 同上 | summary | 200/404/409 | 是 | wake | L295-314 |
| GET | `/v1/runs/{run_id}/stream` | query `last_event_id≥0`, `kinds`(逗号分隔)；头 `Last-Event-ID` | `text/event-stream` | 200 / 501(非 PG) | 否 | 否 | `run_stream.py:68-158` |
| GET | `/v1/runs/{run_id}/cost?refresh=true` | — | **裸 dict**（无 response_model） | 200 / 501(非 PG) | 是（`refresh_cost_rollup()` 刷新物化视图） | 否 | `run_cost.py:27-107` |

注意：`RunControlTransitionError` 继承 `ValueError`（`run_control.py:29`），路由里先 except 子类再 except `ValueError`，顺序正确。

#### SSE 细节（`run_stream.py`）
- 常量：心跳 20s、NOTIFY 轮询 1s、回填上限 500（L33-35）。
- 游标：`max(header Last-Event-ID, query last_event_id)`（L89-94）。
- 生成器（L99-148）：`run_in_threadpool(_open_listener)` 取 `engine.raw_connection()`，`set_autocommit(True)` + `LISTEN events_channel`（L193-202）→ 回填 `SELECT ... WHERE run_id=:run_id AND id>:after_id ... LIMIT 500`（L161-190）→ `: ready` → 循环：`driver_conn.notifies(timeout=1.0)`（psycopg3 API，L205-212）收集 id → 再 `_fetch_events(after_id=cursor, up_to_id=max(new_ids))`（**注意 `_fetch_events` 每次都 `engine.connect()` 拿第二条连接**，L189）。
- 关闭：`finally` 里同步 `raw.invalidate()`（L140-148），理由是 `close()` 会把带 LISTEN 状态的连接放回池；`invalidate` 直接销毁。测试 `test_run_stream_sse.py:128-160` 验证 `pg_stat_activity` 中监听数回落。
- 并发假设：每订阅者 = 1 条池连接 + 每秒 1 次线程池阻塞调用；订阅数上限受 `pool_size+max_overflow=30` 与 anyio 线程池（默认 40）约束。**超过约 30 个并发 SSE 客户端会耗尽连接池，导致普通请求在 `pool_timeout`（30s）后失败。**
- 不校验 run 是否存在；不做 `kinds` 白名单校验；`org_id` 查出但不输出（L182 vs L44-55）。
- 每个 NOTIFY 批次的 fetch 也受 `LIMIT 500` 约束（L129）。游标只按**已输出行**推进（L134 `cursor = max(cursor, row.id)`），下一轮 `after_id=cursor`（L127），因此突发 >500 事件**不会丢**，但超出的行要等到下一个 NOTIFY 到来才会补发；若之后再无新事件（例如 run 已终态），剩余行会一直挂起。回填阶段同理（L104-114）：`Last-Event-ID` 之后若有 >500 条历史事件，客户端只收到前 500 条且没有「还有更多」的提示。

#### 成本端点细节（`run_cost.py`）
- 每次 GET 默认 `SELECT refresh_cost_rollup()`（L41-43），迁移 `20260412_0021` 中该函数做 `REFRESH MATERIALIZED VIEW CONCURRENTLY`。**无鉴权 + 每次刷新物化视图 = 容易被打成 DoS**，且并发刷新在 PG 上会串行等待。
- 未知 run 不返回 404，返回全零（L80-88）。
- `run_budgets.run_id::text` 强转（L74）：说明 `run_budgets.run_id` 是 UUID 列而 API 用字符串——与 `events.run_id TEXT` 不一致的历史痕迹。

### 4.5 providers（`routes/providers.py`）

| 方法 | 路径 | 请求 | 响应 | 状态码 | 写库 | 位置 |
|---|---|---|---|---|---|---|
| GET | `/v1/providers` | — | `list[ProviderCredentialRead]` | 200 | **是**（首次播种）+ 显式 commit | L42-52 |
| POST | `/v1/providers` | `ProviderCredentialCreate` | `ProviderCredentialRead` | 201 | 是 | L55-77 |
| PATCH | `/v1/providers/{credential_id}` | `ProviderCredentialUpdate`（`api_key=""` 清空，`None` 不动） | `ProviderCredentialRead` | 200 / 404 | 是 | L80-106 |
| DELETE | `/v1/providers/{credential_id}` | — | 空 | 204 / 404 / 409(删除活动项) | 是 | L109-119 |
| POST | `/v1/providers/{credential_id}/activate` | — | `ProviderCredentialRead` | 200 / 404 | 是 | L122-133 |
| POST | `/v1/providers/{credential_id}/test` | — | `ProviderTestResult{status,message,elapsed_ms,sample_output}` | 200 / 404 | 是（记录测试结果） | L136-152；同步外呼 LLM，超时 `min(timeout,60)`s |
| GET | `/v1/providers/active` | — | `ProviderCredentialRead \| null` | 200 | 是（首次播种）+ commit | L155-164 |

`ProviderCredentialRead.api_key_preview`（`providers.py:33` → `provider_credentials.py:184-192`）每次列表都要解密全部密钥；密钥轮换后 `decrypt_secret` 抛 `SecretKeyError`（`secrets.py:123-127`）→ **GET /providers 直接 500**。

路由顺序：`GET /active` 定义在 `PATCH/DELETE /{credential_id}` 之后（L155），因为没有 `GET /{credential_id}`，不冲突（已实测路由表）。

---

## 5. 导出下载：`app/api/export_downloads.py`

设计意图（模块 docstring L1-7）：导出记录存的是写入时的路径，字节可能已迁到 CAS blob 树、旧文件名或规范布局 `<root>/[exports/]<document_id>/`；解析只返回位于「允许根」内的路径。

- 允许根 = `(export_root.resolve(), export_root.parent.resolve())`（`documents.py:59-64`）。取 parent 是为了兼容 `artifacts/` 下的 blobs 与历史布局；副作用是 `artifacts/uploads` 也在允许根内。
- `resolve_artifact_path`（L185-229）顺序：`unrecoverable://` 哨兵 → 410；有 sha 则查 `blobs/aa/bb/sha`（L113-126）；原路径与 `merged-document*.html` 兜底（L48-59）；按 `(document_id, basename)` 自愈（L72-90）；否则 404（两种文案）。
- `assert_record_serviceable`（L93-110）：`stale_reason` 或哨兵前缀 → 410。
- 打包：`build_export_archive`（L248-280）在**系统临时目录**同步生成 zip，`FileResponse(background=BackgroundTask(cleanup_path, archive_path))` 发送后删除（L415、L528、L601）。`cleanup_path`（L37-45）删文件后尝试 `rmdir` 父目录——对 `/tmp` 会失败并被吞掉；对上传目录 `upload_root/<uuid>/` 则正好清掉空目录。
- `content_disposition`（L167-174）：RFC 6266 双形式（ASCII 回退 + `filename*=UTF-8''`），解决 Safari 丢后缀问题。
- `chapter_export_response`（L334-416）：先 `load_chapter_bundle`（未捕获 `ValueError` → **500**，见 §17-9），再筛 `SUCCEEDED` 且 `input_version_bundle_json.chapter_id == chapter_id` 的最新记录。
- `document_export_response`（L419-529）：`bilingual_html` 特殊处理为「每章最新成功导出打包」（L532-602）；其他类型取最新一条 + `assets/` 侧车。对所有匹配记录都做 `resolve_artifact_path`（L465-475）——只用了第一条，其余解析纯属浪费且任何一条旧记录缺文件会导致整个下载 404。
- `.md`/`.html` 才收集侧车（L232-238）；侧车用 `rglob("*")` 无大小/数量限制。

---

## 6. Schema 层：`schemas/*.py`

- `common.BaseSchema`：`extra="forbid"`, `populate_by_name=True`（`common.py:4-5`）。**所有请求体拒绝未知字段**（422）。
- `document.DocumentContractResponse`：三字段常量。
- `health.HealthResponse/MetaResponse`。
- `provider.py`：**不继承 `BaseSchema`**，直接 `BaseModel`（L10、L27、L60）→ 允许多余字段；`ProviderCredentialRead.model_config = from_attributes`。字段约束：`max_output_tokens 1..131072`、`timeout_seconds 1..3600`、`max_retries 0..10`、`retry_backoff_seconds 0..60`、`api_key ≤500`。`base_url` 只限长度不校验 URL 格式。
- `run_control.py`：`RunBudgetRequest` 各字段 `ge` 约束；`CreateDocumentRunRequest.run_type: DocumentRunType`（枚举含 `bootstrap`、`repair_targeted`，但执行器 `EXECUTABLE_RUN_TYPES` 是否包含它们 → 待确认；若不包含，创建后永远不会被调度）。响应侧所有时间戳是 `str`（服务层已 isoformat）。
- `workflow.py`（716 行）：读模型的响应形状，大量 `Field(default_factory=list)`；`ExportDocumentRequest.export_type` 是 `Literal[7 值]`，与 `ExportType` 枚举（`enums.py:236-243`）当前一致，但**双份维护**。`TranslateDocumentResponse / ReviewDocumentResponse / ExportDocumentResponse` 仍存在但 API 已不返回它们（改为 202 + run summary）——只被 `tests/document_actions.py` 使用，属**准死代码**。
- 前端类型通过 `scripts/generate_frontend_api_types.py` 从 `create_app().openapi()` 生成，`tests/test_frontend_api_types.py` 做漂移检查；无 response_model 的两个端点（`/chapters`、`/cost`）在 TS 侧只能是 `unknown`。

---

## 7. 配置：`core/config.py`

### 7.1 加载机制
- `env_prefix="BOOK_AGENT_"`，`env_file=ROOT_DIR/.env`（`ROOT_DIR = config.py 向上 3 级 = 仓库根`，L10；在 Docker 镜像内为 `/app/`），`extra="ignore"`，`populate_by_name=True`（L118-124）。
- 来源顺序（L126-140）：init kwargs → **`_SanitizedEnvSettingsSource(env)`** → dotenv → secrets dir。
- `_SanitizedEnvSettingsSource`（L40-49）从**进程环境变量**里剔除 `translation_openai_api_key / BOOK_AGENT_TRANSLATION_OPENAI_API_KEY / OPENAI_API_KEY`。意图（由 `tests/test_translation_worker_abstraction.py:2889-2912` 证实）：只信任项目 `.env` 文件里的 key，忽略 shell 环境。**后果见 §17-2。**
- `get_settings()` 为 `lru_cache(1)`（L161-163）；测试通过 `cache_clear()` 重置。

### 7.2 字段全表

| 字段 | 默认 | 环境变量 | 消费者 | 备注 |
|---|---|---|---|---|
| `app_name` | `"book-agent"` | `BOOK_AGENT_APP_NAME` | `main.py:77`、`health.py:23`、`ui/router.py:19` | |
| `app_version` | `"0.1.0"` | `BOOK_AGENT_APP_VERSION` | 同上 | 与 `pyproject.toml:7` 重复维护 |
| `environment` | `"development"` | `BOOK_AGENT_ENVIRONMENT` | 仅 `health.py:25` 展示 | 自由字符串，与 `app_scope` 语义重叠但互不约束 |
| `app_scope` | `DEV` | `BOOK_AGENT_APP_SCOPE` | `validate_app_scope`、`session.py:18` | 枚举 prod/dev/smoke/e2e |
| `api_prefix` | `"/v1"` | `BOOK_AGENT_API_PREFIX` | `main.py:135`、`health.py:26`、`ui/router.py:20` | |
| `log_level` | `"INFO"` | `BOOK_AGENT_LOG_LEVEL` | `main.py:53` | |
| `database_url` | `postgresql+psycopg://postgres:postgres@localhost:55432/book_agent` | `BOOK_AGENT_DATABASE_URL` | `main.py:44`、`session.py:13`、`cli.py:108`、`alembic/env.py:19` | 默认含明文凭据 |
| `docs_dir` | `ROOT_DIR/docs` | `BOOK_AGENT_DOCS_DIR` | 仅 `health.py:27` | 无其他用途 |
| `export_root` | `artifacts/exports`（相对 CWD） | `BOOK_AGENT_EXPORT_ROOT` | `main.py:95`、`documents.py:55`、`cli.py:109`、executor | 相对路径依赖启动 CWD |
| `upload_root` | `artifacts/uploads` | `BOOK_AGENT_UPLOAD_ROOT` | `main.py:96`、`documents.py:50` | 同上 |
| `cors_allow_origins` | `[]` | `BOOK_AGENT_CORS_ALLOW_ORIGINS`（逗号或 JSON 数组，L142-158） | `main.py:82` | |
| `translation_backend` | `"echo"` | `BOOK_AGENT_TRANSLATION_BACKEND` | `factory.py:70`、`provider_credentials.py:333` | |
| `translation_model` | `"echo-worker"` | `BOOK_AGENT_TRANSLATION_MODEL` | `factory.py`、播种 | |
| `translation_prompt_version` | `"p0.echo.v1"` | … | `factory.py:74,88` | 仅 settings 构建的 worker 用；凭据构建的 worker 用常量 `CREDENTIAL_*_PROMPT_VERSION`（`factory.py:16-17`） |
| `translation_prompt_profile` | `"tech-column-meta-v1"` | … | `factory.py:53-56` | 凭据与 settings 两条路径都用 |
| `translation_timeout_seconds` | 60 | … | `factory.py:89`、播种 | `.env.example` 设 120 |
| `translation_max_retries` | 1 | … | 同上 | |
| `translation_retry_backoff_seconds` | 1.5 | … | 同上 | |
| `translation_max_output_tokens` | 8192 | … | 同上 | |
| `translation_input_cache_hit_cost_per_1m_tokens` | None | … | `factory.py:43` | 计费 |
| `translation_input_cost_per_1m_tokens` | None | … | `factory.py:44`、`chapter_concept_autolock.py` | |
| `translation_output_cost_per_1m_tokens` | None | … | `factory.py:45` | |
| `translation_openai_api_key` | None | `BOOK_AGENT_TRANSLATION_OPENAI_API_KEY` / `OPENAI_API_KEY`（**仅 .env 文件**） | `factory.py:77-85`、播种 L350-357 | |
| `translation_openai_base_url` | `https://api.openai.com/v1/responses` | `BOOK_AGENT_TRANSLATION_OPENAI_BASE_URL` / `OPENAI_BASE_URL` | `factory.py:86`、播种 | 播种 echo 凭据时也写入此 URL（L340） |
| `translation_openai_streaming` | False | … | `factory.py:93`、播种 | |
| `translation_openai_request_overrides` | `{}` | `BOOK_AGENT_TRANSLATION_OPENAI_REQUEST_OVERRIDES`（JSON） | `factory.py:47,62` | 全局，凭据不能单独覆盖 |
| `figure_cluster_*`（8 个） | 见 L102-109 | … | `domain/structure/figure_clustering.py:716-750`（`getattr`） | PDF 结构分析 |
| `pdf_sanity_ocr_reextraction` | False | … | `domain/structure/pdf.py` | |
| `ocr_status_path` | None | … | `ingestion/pdf/ocr.py` | |
| `ocr_heartbeat_seconds` | 5.0 | … | 同上 | |
| `ocr_max_runtime_seconds` | None | … | 同上 | |
| `ocr_chunk_page_count` | 32 | … | 同上 | |

**不在 Settings 里但被读取的环境变量**：`BOOK_AGENT_SECRET_KEY`（`secrets.py:24`，直接读 `os.environ` 与 `.env`）；启动脚本用的 `BOOK_AGENT_HOST/PORT/FRONTEND_*`（`dev.sh:21-24`、`service.sh:33-40`）。`.env.example` 未列出 `BOOK_AGENT_SECRET_KEY`、`BOOK_AGENT_APP_SCOPE`、`BOOK_AGENT_UPLOAD_ROOT`。

### 7.3 scope 校验的差异
- `validate_app_scope`（`config.py:170-188`，仅 `create_app` 调用）：SMOKE/E2E 拒绝非 sqlite；DEV/PROD 拒绝非 postgresql。
- `build_engine`（`session.py:18-22`）：只重复 SMOKE/E2E 的 sqlite 强制。→ CLI、alembic、脚本在 DEV scope 下可用 sqlite（`test_cli.py`）。
- `test_app_runtime.py:108-119` 覆盖两种违规。

---

## 8. 日志与 ID

- `core/logging.py:4-9`：`logging.basicConfig(level, format="%(asctime)s %(levelname)s %(name)s %(message)s")`。无 JSON、无请求 ID、无 run_id 上下文；uvicorn 访问日志走 uvicorn 自己的 logger 配置。若 root logger 已被别处配置，`basicConfig` 静默无效。
- `core/ids.py:6-8`：`stable_id(*parts) = uuid5(NAMESPACE_URL, "::".join(parts))`，用于幂等派生 ID（`services/actions.py:143`）。

---

## 9. CLI：`cli.py`

| 子命令 | 参数 | 调用 | 与 API 的关系 |
|---|---|---|---|
| `bootstrap` | `--source-path` | `service.bootstrap_document` | = `POST /documents/bootstrap` |
| `summary` | `--document-id` | `get_document_summary` | = `GET /documents/{id}` |
| `translate` | `--document-id --packet-id*` | `service.translate_document`（**同步**） | API 走 run 队列；CLI 绕过控制面 |
| `review` | `--document-id` | `service.review_document`（同步） | 同上 |
| `export` | `--document-id --export-type --auto-followup-on-gate --max-auto-followup-attempts` | `service.export_document`（同步） | 同上 |
| `refresh-pdf-structure` | `--document-id --chapter-id*` | `service.refresh_pdf_structure` | 无 API 对应 |
| `glossary-extract` | `--document-id --output --max-chunk-chars` | `GlossaryExtractionService` | 无 API 对应；需要 LLM client |
| `glossary-lock` | `--document-id --input` | `GlossaryService.lock_term` | 无 API 对应 |
| `term-consistency` | `--document-id --report --dry-run` | `TermConsistencyService` | 无 API 对应 |
| `execute-action` | `--action-id --run-followup` | `service.execute_action` | = `POST /actions/{id}/execute` |

共享点：`DocumentWorkflowService` + `resolve_translation_worker(session, settings)`（`cli.py:112-117`，与 API 的 `TranslationWorkerProvider` 同一解析函数，`factory.py:136-147`）。

差异/问题：
- 全局参数 `--database-url`、`--export-root`（L42-43）。`build_session_factory(database_url=...)` 绕过 DEV/PROD 的 PG 强制。
- **整个命令在一个 `session_scope` 事务里**（L111）：`translate` 一本书 = 一个长事务；中途异常全部回滚（对 translate 而言等于丢掉已完成的 packet）。
- CLI 产生的翻译/导出**不产生 `DocumentRun`/events**，`/documents/history` 的 `latest_run_*` 不会反映 CLI 操作。
- `parser.error` 在 `with` 块内触发 `SystemExit`（L157、L185）—— `session_scope` 只捕获 `Exception`，`SystemExit` 走 `finally: close()`，未 commit 也未显式 rollback（close 会丢弃事务，结果正确但路径隐晦）。
- L266-267 不可达（`required=True` 子解析器）。
- 无 provider 管理、run 控制、迁移等运维子命令；`pyproject.toml:33-34` 注册 `book-agent = book_agent.cli:main`。
- 测试 `test_cli.py` 仅 1 个 happy path（sqlite + echo）。

---

## 10. Provider 凭据与密钥

### 10.1 存储模型（`domain/models/provider_credential.py`）
- 表 `provider_credentials`：`api_key_ciphertext LargeBinary`（L42），`is_active` 部分唯一索引 `postgresql_where=is_active`（L25-32，仅 PG 生效；SQLite 靠服务层事务保证）。`retry_backoff_seconds_x10` 整型存一位小数（L48-50）。

### 10.2 服务层（`services/provider_credentials.py`）
- CRUD：`create_credential` L65-98（`api_key` 空则 `ciphertext=None`；`activate=True` 时同事务内 `_activate_internal`）；`update_credential` L101-142（`api_key=""` 清空）；`delete_credential` L145-150（活动项拒绝，`ValueError` → 409）；`activate_credential` L153-168（先 UPDATE 其他行为 False 再置 True，`synchronize_session=False`）。
- 缓存失效：模块级全局 `_revision` 计数器（L302-311），`_bump_revision()` 在 flush 后、commit 前调用（L140、L168）。`TranslationWorkerProvider.get()`（`factory.py:165-178`）按 revision 缓存 worker。**进程内有效**；多 worker/多副本部署下其他进程不会失效（它们要等自己的进程重启）。
- 播种：`resolve_active_credential`（L314-324）→ 无活动项且**表为空**（L331）时按 `translation_backend` 从 Settings 播种（echo 或 openai_compatible+有 key）。两个并发首次请求可能同时播种 → PG 部分唯一索引冲突 → 其中一个 500（无重试）。
- 连接测试 `test_credential_connection`（L195-291）：echo 直接 OK；否则用 `OpenAICompatibleTranslationClient(max_retries=0, timeout=min(t,60), max_output_tokens=64)` 发一个结构化请求；`RuntimeError`（含 `ProviderResponseFormatError`）视为「已连通但解析警告」→ `OK`（L264-274）。
- `api_key_preview`（L184-192）：解密后取前 3 后 4。

### 10.3 密钥管理（`services/secrets.py`）
- Fernet 对称加密；密钥 = `BOOK_AGENT_SECRET_KEY`（urlsafe-b64 32 字节）。
- `_ensure_secret_key`（L67-93）：`os.environ` → 直接解析项目 `.env`（L57-64，绕过 pydantic）→ **自动生成并写回 `.env`**（L77-87），失败仅 warning。
- `.env` 路径 = `secrets.py` 向上 3 级（L33-34）：源码树时是仓库根；Docker 镜像里是 `/app/.env`（**镜像不含 .env**，写入容器可写层，重启即丢）。
- `_write_secret_to_dotenv`（L43-54）整文件重写，保留其他行，规范化尾部换行。
- `get_fernet` 进程级单例（L96-107）；`reset_fernet_cache_for_tests`（L130-134）。
- `decrypt_secret` 失败抛 `SecretKeyError(RuntimeError)`（L117-127）——**没有任何 API 层处理器**。

安全风险汇总：
1. 密钥自动生成 + 写入工作目录 `.env`：在容器/只读文件系统/多进程场景下要么丢失、要么各进程生成不同密钥互相覆盖（`secrets.py:77-79`）。多 uvicorn worker 同时首次启动 = 竞争写 `.env`。
2. 密钥与密文位于同一主机（`.env` 与 DB 备份一起泄露即明文）；没有 KMS/轮换机制，轮换后只能靠用户重输。
3. `.env` 文件权限依赖 umask，未 `chmod 600`。
4. API 全部无鉴权 → 任何能访问端口的人都能 `POST /providers` 写入自己的 key、`POST /providers/{id}/test` 借用服务器出口测试任意 `base_url`（**SSRF**：`base_url` 任意，L213-222）、以及借活动凭据执行翻译消费额度。
5. `test_provider` 的错误信息会把上游响应体前 200 字符回显（L244），可能泄露上游错误细节。

---

## 11. `services/actions.py`（IssueActionExecutor）

- 纯服务对象，依赖 `OpsRepository` 与 `ChapterTranslationMemoryRepository`（L37-39）。
- `execute(action_id)`（L41-92）：取 action/issue → `status=RUNNING` → `mark_issue_triaged` → `build_rerun_plan` → 按 `(action_type, scope_type)` 分支：只审计 / 撤回待定记忆提案 / 失效 packet 束（packet→INVALIDATED，target segment→SUPERSEDED，sentence→PENDING，L107-133）/ 章节回退到 `PACKET_BUILT`（L82）→ `status=COMPLETED` → `save_invalidations`（`ops.py:118-128` 用 `merge`，配合 `stable_id` 幂等）→ `flush`。
- **不检查 action 当前状态**：已 COMPLETED 的 action 可被重复执行（API 无幂等保护）。RUNNING→COMPLETED 在同一事务内完成，外部永远看不到 RUNNING。
- `SENTENCE` scope 只写审计不做任何失效（L84-86）——"manual review" 语义靠审计事件承载。
- 时间戳统一用一次 `_utcnow()`（L44）。

---

## 12. 部署与启动

### 12.1 `Dockerfile`
- `python:3.12-slim`，以 **root** 运行，无 `HEALTHCHECK`，`pip install -e .`（可编辑安装打进镜像），`PYTHONPATH=/app/src`。
- 只 COPY `pyproject.toml README.md alembic.ini src alembic`；**不含 `.env`、`frontend`**。
- `CMD uvicorn book_agent.app.main:app --host 0.0.0.0 --port 8000`：单 worker、无 `--proxy-headers`、无 `--timeout-graceful-shutdown`、无 `--limit-concurrency`。
- `.dockerignore` 存在（119 字节，内容待确认）。

### 12.2 `compose.yaml`
- `postgres:16-alpine`，密码硬编码 `postgres/postgres`，暴露 55432。
- `migrate` 一次性服务 `alembic upgrade head`（`depends_on: postgres healthy`）；`app` `depends_on: migrate completed_successfully`。
- 两者 `env_file: .env`（**以环境变量方式注入**，不挂载文件）+ 覆盖 `BOOK_AGENT_DATABASE_URL`、`BOOK_AGENT_EXPORT_ROOT=/app/artifacts/exports`；`app_exports` 卷只挂 exports，**uploads 不持久化**（`/app/artifacts/uploads` 在容器层）。
- app 无 healthcheck、无资源限制、无 `restart` 之外的自愈。

### 12.3 `dev.sh`
- 清理同端口旧实例（按命令行匹配，`dev.sh:68-114`），`set -a; source .env`（把 `.env` 灌入 shell env，L172-178），强制 `BOOK_AGENT_DATABASE_URL` 指向 compose PG（L219），`alembic upgrade head`，`uvicorn --reload --reload-dir src`，可选 Vite。
- 提示「API 文档 → /v1/docs」（L133、L229）——错误路径。

### 12.4 `service.sh`
- pid 文件 `.server.pid/.frontend.pid`，日志追加到 `artifacts/server.log`（无轮转）。
- 启动前跑 alembic；`nohup uvicorn ... $RELOAD`（可在「服务」模式下开 `--reload`）。
- 停止：`kill` 后最多等 5s 再 `kill -9`（L238-247）——比 lifespan 的执行器 join 预算短，会硬杀正在 LLM 调用的 work 线程（lease 之后靠过期回收）。
- 状态输出 `docs -> /docs`（L397、L431），与 dev.sh 不一致但**这个是对的**。

### 12.5 alembic
- `alembic.ini:4` `sqlalchemy.url` 指向 `localhost:5433`（陈旧值），被 `env.py:18-19` 用 `get_settings().database_url` 覆盖；`env.py` 用 `NullPool`。
- `env.py:14` `from book_agent.domain.models import *` 载入全部模型以供 autogenerate。

### 12.6 `.env.example`
- 默认 `BOOK_AGENT_TRANSLATION_BACKEND=openai_compatible` + NVIDIA endpoint；没有 `BOOK_AGENT_SECRET_KEY`。

---

## 13. 数据流小结

```
浏览器/前端 ──HTTP──▶ FastAPI 路由 ──Depends(get_db_session)──▶ Session(事务)
      │                    │                                       │
      │                    ├─ DocumentWorkflowService(session, export_root, worker) ── 读模型/写模型
      │                    ├─ RunControlService(RunControlRepository(session)) ── DocumentRun/RunAuditEvent/RunBudget
      │                    └─ session.commit() → executor.wake(run_id)
      │                                                   │
      │                          DocumentRunExecutor (daemon threads, 同进程)
      │                            supervisor → run 线程 → work 线程 (LLM 调用) ── 各自 session_scope 短事务
      │                                                   │
      │                           emit_event → INSERT events → trigger pg_notify('events_channel', id)
      │                                                   │
      └──SSE GET /runs/{id}/stream ◀── raw psycopg conn LISTEN + 每秒 notifies() + SELECT 回填
```

事务边界要点：
- 请求事务 = 一个 HTTP 请求（GET 回滚）；执行器事务 = 每个 tick / 每个 work item 的若干短事务；LLM 调用**不在事务内**（提交 `3e66700`）。
- 请求线程与执行器线程共用连接池；SSE 订阅者长期占用连接。

---

## 14. 错误处理与响应格式

| 场景 | 处理 | 位置 | 响应体 |
|---|---|---|---|
| `OperationalError`（任何路由） | 503 | `main.py:118-125` | `{"detail": "Database unavailable. Ensure PostgreSQL is running and BOOK_AGENT_DATABASE_URL is configured correctly."}`（`_database_error_detail` 的 `exc` 参数未使用，L23） |
| `ProviderTransportError` | 503（网络错误 / HTTP 408,409,429,5xx）或 502 | `main.py:30-37,127-132` | `{"detail": str(exc)}` |
| 服务层 `ValueError` | 各路由手工映射为 404（documents/runs/actions）或 400（bootstrap）或 409/404（memory-proposals 靠文本匹配 `documents.py:87-92`） | 各路由 | `{"detail": msg}` |
| `RunControlTransitionError` | 409 | `runs.py` | 同上 |
| `DocumentBusyError` | 409 | `documents.py:217-218` | 同上 |
| `LookupError` | 404 | `providers.py` | 同上 |
| Pydantic 校验 | FastAPI 默认 422 | — | `{"detail": [...]}` |
| 其他异常（`SecretKeyError`、`AppScopeViolation`、`ExportGateError` 若逃逸、`KeyError` 等） | **无处理器** → Starlette 默认 500，`text/plain "Internal Server Error"`，非 JSON | — | 与其他错误格式不一致 |

统一性评估：
- 已处理错误统一为 `{"detail": string}`，未处理错误是纯文本 500，**不统一**。
- 没有错误码/类型字段，前端只能靠状态码 + 文案。
- `ValueError → 404` 是粗粒度映射：服务层任何参数校验类 `ValueError`（如 `RunControlService.create_run` 的 "resume_from_run_id must belong to the same document"，`run_control.py:162`）也会变成 404。
- 两个端点返回裸 dict/list（`run_cost.py:32`、`documents.py:250`）。

---

## 15. 缺失的生产要素（逐项现状）

| 要素 | 现状 | 证据 |
|---|---|---|
| 鉴权/授权 | 无。所有 API、Swagger、SSE、成本、凭据管理均匿名可达 | 全部路由无 `Depends`；`main.py` 无中间件 |
| 多租户 | `events.org_id` 固定 `'default'`（`events.py:36`），其他表无租户列 | |
| 限流 | 无 | |
| 请求 ID / 关联 ID | 无中间件；`events.correlation_id` 存在但 API 层不生成/透传 | `run_stream.py:53` |
| 结构化日志 | `basicConfig` 文本格式；无请求日志（依赖 uvicorn access log） | `logging.py` |
| 指标/追踪 | 无 Prometheus/OTel | |
| 健康检查深度 | `/health` 只做 `SELECT 1`；不检查执行器线程是否存活、连接池饱和、磁盘（export/upload root）、迁移版本、活动 provider | `health.py:11-16` |
| 就绪/存活分离 | 无 | |
| CORS | 可配置；`allow_credentials=True` + 显式 origin 列表（正确组合）；未配置则不加中间件 | `main.py:82-90` |
| 上传大小限制 | 无（`UploadFile` 直接 `copyfileobj` 到磁盘）；无 MIME 校验、无病毒扫描；上传文件永久保留 | `documents.py:136-144` |
| 请求超时 | 无；`execute_action?run_followup=true` 与 `providers/{id}/test` 在请求内同步调 LLM | `actions.py:19-23`、`providers.py:145` |
| 优雅关闭 | lifespan 有界 join（最长 40s+）；`service.sh` 5s 后 SIGKILL；uvicorn 无 `--timeout-graceful-shutdown` | `main.py:62-74`、`service.sh:240-247` |
| 连接池治理 | 固定 10+20；SSE 每客户端占一条；无 `pool_timeout/recycle` | `session.py:23-26` |
| 幂等性 | 无 Idempotency-Key；重复 POST translate/export 会创建多个 run；`execute_action` 可重复执行 | |
| 输入安全 | `bootstrap.source_path` 接受任意服务器路径；`providers.base_url` 任意（SSRF） | `documents.py:114-121`、`provider_credentials.py:213-222` |
| 密钥管理 | 见 §10.3 | |
| 配置校验 | 无对 `export_root/upload_root` 可写性的启动校验；相对路径依赖 CWD | |
| 迁移守卫 | 启动不检查 alembic head 是否已应用（compose 通过 `migrate` 服务保证；`service.sh/dev.sh` 手动跑） | |
| 多进程/多副本 | 执行器与 revision 缓存都是进程内；跑 N 个 worker 会有 N 个执行器抢 run（靠 lease 互斥，待确认充分性）且 provider 切换不跨进程 | `document_run_executor.py`、`provider_credentials.py:302` |
| 审计 | run 有 `RunAuditEvent`；provider 变更、文档删除**无审计** | |
| API 版本策略 | 只有前缀 `/v1`；无弃用机制 | |
| 文档一致性 | Swagger 实际在 `/docs`，页面/README/dev.sh 指向 `/v1/docs` | §17-1 |

---

## 16. 测试覆盖（应用/API 层）

| 文件 | 规模 | 覆盖 | 未覆盖/备注 |
|---|---|---|---|
| `tests/test_api_workflow.py` | 3738 行，≈60 用例 | 几乎所有 documents/actions 路由、worklist、memory-proposals、exports dashboard/detail/download、history 过滤、`POST /runs` + resume 全流程、retry lineage | **translate/review/export 三个 202 端点被 `SyncDocumentActionClient` 拦截为同步调用**（`tests/document_actions.py:47-57`），因此这些用例不测真实入队路径；sqlite + 真实执行器线程 |
| `tests/test_document_run_endpoints.py` | 4 用例 | 真实 `POST /translate|review|export` 202 → 执行器 → 终态 → 下载；未知文档 404 | 依赖线程 + sqlite，`_wait_for_terminal` 60s 轮询 |
| `tests/test_run_control_api.py` | 4 用例 | create/pause/resume/drain/cancel/events/summary 聚合；GET summary 唤醒执行器 | 用 `_NoopDocumentRunExecutor`；未测 `retry` 端点（在 workflow 测试里测）、未测 budget 校验 422 |
| `tests/test_providers_api.py` | 2 用例 | GET 列表/active 的首次播种持久化且不重复 bump revision | **POST/PATCH/DELETE/activate/test 端点零覆盖**；`SecretKeyError` 路径零覆盖 |
| `tests/test_run_stream_sse.py` | 2 用例（需活 PG，否则 skip） | 回填 + 实时、断连后监听连接释放 | `kinds` 过滤、`Last-Event-ID` 头、501 分支未测 |
| `tests/test_app_runtime.py` | 6 用例 | scope 违规、DB 不可达 503（上传与 health）、artifact 路径回退、zip 命名 | lifespan 停止分支（线程未退出不 dispose）未测 |
| `tests/test_api_deps.py` | 3 用例 | GET 回滚/POST 提交、503 文案 | `get_session_factory` 兜底分支未测 |
| `tests/test_cli.py` | 1 用例 | bootstrap→translate→review→export happy path（sqlite） | 其余 6 个子命令未测 |
| `tests/test_frontend_api_types.py` | 1 用例 | OpenAPI → TS 漂移 | |
| 其他相关 | `test_document_delete.py`（DELETE）、`test_run_lineage.py`（lineage）、`test_pdf_support.py`（contract） | | `/v1/meta`、`/v1/runs/{id}/cost`、`history/backfill`、`/{id}/chapters`、CORS、上传后缀拒绝路径：**无测试** |
| `tests/__init__.py:27` | 固定 `BOOK_AGENT_SECRET_KEY` | 避免测试写 `.env` | |

契约要点（从断言看）：
- 202 端点返回的 run summary 里 `status=="running"`、`status_detail_json.run_request` 回显请求（`test_document_run_endpoints.py:103-105`）。
- `POST /runs` 返回 `status=="queued"`、`events.event_count==1`（`test_run_control_api.py:109-111`）。
- 非法转换 409，`detail` 含 "cannot transition"（L177-178）。
- 事件列表按时间倒序（`event_types[0]=="run.cancelled"`，L148-149）。
- SSE 帧：`id:`/`event:`/`data:` 三行，`: ready` 哨兵（`test_run_stream_sse.py:98-107`）。

---

## 17. 发现的问题 / 风险 / 技术债（按严重度）

### 高

1. **Swagger/OpenAPI 链接指向不存在的路径。** `FastAPI()` 未设置 `docs_url/openapi_url`（`main.py:76-81`），实际路径为 `/docs`、`/openapi.json`（已实测）。但入口页 `ui/page.py:7-8` 生成 `{api_prefix}/docs`，前端 `frontend/src/lib/api.ts:410-411`、`README.md:180`、`docs/README.md`、`dev.sh:133,229` 都写 `/v1/docs` → 404。`service.sh:397,431` 用 `/docs` 是对的。修法二选一：`FastAPI(docs_url=f"{prefix}/docs", openapi_url=f"{prefix}/openapi.json")` 或改链接。

2. **Docker/compose 部署下 `.env` 中的 `OPENAI_API_KEY` 永远读不到。** `_SanitizedEnvSettingsSource`（`config.py:40-49`）按设计忽略进程环境中的 key，只信 `.env` 文件；而 `Dockerfile` 不 COPY `.env`，`compose.yaml:25,39` 的 `env_file` 是环境注入。结果：容器内 `translation_openai_api_key=None` → `_bootstrap_from_settings` 不播种（`provider_credentials.py:350`）→ `build_translation_worker` 抛 `ValueError`（`factory.py:77-82`）→ 见第 3 条。用户必须通过 UI 手工创建 provider 才能用，`docs/README.md:81` 「only OPENAI_API_KEY is required」在容器部署下不成立。

3. **worker 解析失败被误报为 404/400。** 所有 documents 路由都在 `try: _workflow_service(request, session)... except ValueError → 404` 里构造服务（如 `documents.py:200-203`、`233-241`、`289-301`），而 `_workflow_service` 内部调用 `resolve_translation_worker()`（L83）可能抛 `ValueError("Missing OpenAI-compatible provider credentials...")` 或 `SecretKeyError`。前者变成 `GET /documents/{id}` 返回 **404 + 凭据错误文案**，`bootstrap` 返回 400；后者 500。建议把 worker 解析移出 `try`，或为只读路由使用不需要 worker 的服务构造。

4. **无鉴权 + `POST /providers/{id}/test` / `POST /providers` 任意 `base_url` = SSRF 与额度盗用。** `provider_credentials.py:213-239` 使用用户提供的 URL 从服务器发起请求；`provider.py:14` 仅限长度。

5. **Fernet 密钥自动生成并写入 `.env`（`secrets.py:77-93`）。** 容器重启即丢（镜像无 `.env`，写入可写层）；多进程首次启动竞争写入；丢失后所有已存 key 变为不可解密，`GET /providers` 因 `api_key_preview` 解密而整体 500（`providers.py:33`、`provider_credentials.py:187`、`secrets.py:123-127`）。`.env.example` 未列该变量。

6. **`POST /documents/bootstrap` 接受任意服务器路径**（`documents.py:114-121`），配合无鉴权可让服务解析服务器上任意 `.epub/.pdf`（甚至不限后缀——该端点没有走 `_safe_upload_filename`）。至少应限制在 `upload_root` 内或删除该端点（前端用的是 `bootstrap-upload`）。

7. **SSE 订阅者占用池连接**（`run_stream.py:194` + `session.py:25-26`）：≥30 个并发流即耗尽连接池，普通请求在 `pool_timeout` 后报错；且 `_fetch_events` 每次再借一条（L189）。生产应使用独立的 LISTEN 连接（单条共享 + 内存扇出）或独立 NullPool engine。

8. **`GET /runs/{id}/cost` 每次刷新物化视图**（`run_cost.py:41-43`）且无鉴权 → 廉价 DoS；`REFRESH ... CONCURRENTLY` 互斥排队。应改为后台定时刷新或加节流。

### 中

9. `chapter_export_response` 里 `export_repository.load_chapter_bundle(chapter_id)`（`export_downloads.py:343`）不在 `try` 内，未知 chapter 抛 `ValueError` → 500 而非 404。

10. `document_export_response` 对**所有**成功记录做路径解析（`export_downloads.py:465-475`）但只用第一条：任一历史记录文件缺失即整个下载 404（除非被标 stale）。应只解析最新一条。

11. `GET /documents/{id}/chapters`（`documents.py:245-271`）：无 `response_model`、函数内 import、直接 ORM、未知文档返回 200 空列表、`status` 用 `hasattr(...,'value')` 兜底——是临时补丁进入主干，前端 `api.ts:536` 已依赖。

12. `POST /documents/history/backfill`（`documents.py:184-191`）为死端点，永远返回 0；应删除并同步前端类型。

13. `ValueError → 404` 的泛化映射（`runs.py:136-137`、`documents.py` 多处）把服务层校验错误也当成「不存在」；`_proposal_http_exception`（`documents.py:87-92`）靠错误文案里的 "not found"/"does not belong" 决定 404/409，脆弱。

14. 有副作用的 GET：`GET /providers`、`GET /providers/active` 会写库并 commit（`providers.py:49-50,160-161`）；`GET /runs/{id}` 会唤醒执行器（`runs.py:152`）。播种在并发首访下可能撞唯一索引（PG）返回 500。

15. `execute_action?run_followup=true`（`actions.py:11-25`）与 `providers/{id}/test` 在请求线程内同步调用 LLM，无总超时；uvicorn 默认无请求超时 → 长挂连接。同时 `execute_action` 无幂等/状态检查（`services/actions.py:41-48`），重复 POST 会重复失效。

16. 上传：无大小上限、无 MIME 检查、上传文件永久保留且 DELETE 文档不清理（`documents.py:130-157`、`207-219`）；`compose.yaml` 未持久化 uploads 卷。

17. lifespan 停止预算（≤5s + 5s×run + 30s×work，`document_run_executor.py:166-203`）与 `service.sh:240-247` 的 5s SIGKILL、Dockerfile 无 `--timeout-graceful-shutdown` 不匹配；且 `stop()` 在线程仍存活时清空注册表（L199-202），随后若 `ensure_document_run_executor` 再被请求触发会**再起一个执行器**（L69-84），与遗留线程并存。

18. 多进程部署不安全：`_revision`（`provider_credentials.py:302`）与 `TranslationWorkerProvider` 缓存是进程内的；执行器每进程一个。当前 Dockerfile 单 worker 规避了，但没有任何守卫阻止 `--workers N`。

19. `app.state.database_dialect_name`（`main.py:92`、L46）无人读取；`run_stream/run_cost` 通过 `session_factory.kw["bind"]` 取 engine（`run_stream.py:82`、`run_cost.py:34`）——依赖 sessionmaker 私有结构。

20. `_database_error_detail(exc=...)` 参数未用（`main.py:23-27`），所有 `OperationalError`（包括查询期的锁超时、语句错误）都被说成「数据库不可用」。

21. 相对路径默认 `artifacts/exports`、`artifacts/uploads`（`config.py:63-64`）依赖启动 CWD；`documents.py:51,56` 才 `resolve()`。执行器在构造时 `resolve()`（`document_run_executor.py:107`），路由每次 resolve——若进程中途 `chdir` 会不一致（低概率）。

22. `alembic.ini:4` 端口 5433 与其他地方的 55432 不一致（被 env.py 覆盖，但误导）。`Settings.app_version` 与 `pyproject.version` 双份维护。

23. `schemas/provider.py` 不继承 `BaseSchema`（`extra=forbid`），与其他 schema 行为不一致：多余字段静默忽略。

24. `ExportDocumentRequest.export_type` 的 `Literal` 与 `ExportType` 枚举双份维护（`workflow.py:203-212` vs `enums.py:236-243`）；`TranslateDocumentResponse/ReviewDocumentResponse/ExportDocumentResponse`（`workflow.py:117-124,196-200,238-249`）已无 API 使用者，仅测试 helper 引用。

25. `CreateDocumentRunRequest.run_type` 允许 `bootstrap`/`repair_targeted`（`enums.py:280-286`），但 `EXECUTABLE_RUN_TYPES` 只含 `translate_full/translate_targeted/review_full/export_full`（`run_plan.py:79-86`，已核实）。`POST /runs` 以这两种类型创建的 run 返回 201 后**永远不会被 supervisor 调度**（`document_run_executor.py:311-324` 只扫描可执行类型），`resume` 也能把它置为 running 而无人处理。应在 schema 或 `create_run` 层拒绝。

26. CLI 的 translate/review/export 走同步路径、单事务、不产生 run/events（`cli.py:111-142`），与 API 语义分叉；`--database-url` 可绕过 PG 强制。

### 低

27. `GET /meta.docs_path` 暴露服务器绝对路径（`health.py:27`）。
28. `run_stream.py` 不校验 `run_id` 存在、`kinds` 不做白名单；`org_id` 查而不用。
29. `cleanup_path`（`export_downloads.py:37-45`）对系统临时目录尝试 `rmdir` 父目录，靠 `OSError` 吞掉。
30. `documents.py:13` 一行导入 9 个枚举、`documents.py:14` 从 `routes/runs.py` 导入私有 `_to_run_summary_response`（跨路由模块耦合）。
31. `main.py:14-15` 两行分别从同一模块导入。
32. `cli.py:266-267` 不可达代码。
33. 播种 echo 凭据时 `base_url` 写成 OpenAI URL（`provider_credentials.py:340`）。
34. `logging.basicConfig` 在 root 已配置时无效；无请求 ID。
35. `provider_credentials.py:293-295` 三个空行等格式残留。

---

## 18. 术语/约定速查

- **run 控制面**：`DocumentRun`（状态机 queued/running/paused/draining/succeeded[_with_warnings]/failed/cancelled）+ `WorkItem` + `WorkerLease` + `RunAuditEvent`（分页审计）+ `events`（事件总线，SSE 源）。
- **`RUN_REQUEST_KEY = "run_request"`**（`run_plan.py:18`）：文档级 202 端点把请求参数塞进 `status_detail_json["run_request"]`，执行器据此生成 `RunPlan`。
- **AppScope**：`prod/dev` 要 PG；`smoke/e2e` 要 sqlite（防止临时目录路径污染共享库，见 `config.py:13-32` 注释）。
- **artifact roots**：`(export_root, export_root.parent)`；CAS blob 在 `<parent>/blobs/aa/bb/<sha>`。
- **revision 缓存**：进程内整数，activate/update 活动凭据时 +1，`TranslationWorkerProvider` 据此重建 worker。

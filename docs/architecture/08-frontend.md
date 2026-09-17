# 08 · 前端

> 来源：2026-09-16 对分支 `refactor/p0-stabilize`（HEAD `2176d51`）的逐文件源码阅读。行号对应该基线；标注「待确认」的条目未经运行验证。总览与跨子系统结论见 [`00-overview.md`](00-overview.md)。

# book-agent 前端源码阅读笔记（`frontend/`）

阅读日期：2026-09-16，分支 `refactor/p0-stabilize`（HEAD `2176d51`）。所有行号以当前工作树为准。
验证过的事实：`npx tsc --noEmit` 通过（exit 0）；`npx vitest run` 5 个文件 / 6 个用例全部通过；`npx vite build` 成功（`dist/assets/index-*.js` 328 KB / gzip 101 KB，`index-*.css` 36 KB / gzip 7 KB）。
标注「待确认」的地方是我从源码无法完全确定的推断。

---

## 0. 总览

| 项 | 值 | 出处 |
|---|---|---|
| 技术栈 | React 19.2.4、TypeScript 5.9.3、Vite 7.3.1、@tanstack/react-query 5.95.2、react-router-dom 7.13.2、vitest 2.1.9、jsdom 26 | `frontend/package.json`、`npm ls` |
| 源码规模 | `src/**` 共 8110 行（含测试 1238 行、CSS 2101 行、生成类型 857 行） | `wc -l` |
| 入口 | `index.html` → `src/main.tsx` → `App` | `frontend/index.html:19`、`src/main.tsx:9-15` |
| 路由 | `BrowserRouter` + 4 条路由，全部包在 `AppLayout` 里 | `src/app/App.tsx:16-21` |
| 全局状态 | 单一 `WorkspaceProvider`（618 行）+ React Query 单例 `queryClient` | `src/app/WorkspaceContext.tsx`、`src/app/queryClient.ts` |
| 后端契约 | `src/lib/api.ts`（手写 fetch 封装 + 手写类型）+ `src/lib/api-types.gen.ts`（OpenAPI 生成） | 见 §3 |
| 样式 | `tokens.css`（CSS 变量）+ `global.css`（reset/工具类）+ 每页一个 CSS Module | 见 §7 |
| 测试 | vitest + RTL，5 个测试文件，`fetch` 全局 stub | 见 §8 |
| 生产部署 | **没有**：Dockerfile 不含 Node/前端，后端不 serve `dist/`，`service.sh` 用 `vite dev` 起前端 | 见 §9 |
| Lint / CI | 无 ESLint、无 Prettier、无 `.github/workflows` | `ls frontend`、`ls .github` |

页面级依赖：所有页面只依赖 `useWorkspace()` 与 `lib/workflow.ts` 的纯函数，没有任何第三方 UI 库、表单库、日期库、图标库（图标全是内联 SVG，`src/components/AppLayout.tsx:10-38`）。

---

## 1. 路由与页面清单、用户流程、端点与状态码

### 1.1 路由表

| 路径 | 组件 | 文件 | 行数 | 说明 |
|---|---|---|---|---|
| `/` | `WorkspacePage` | `src/features/workspace/WorkspacePage.tsx` | 481 | 上传 / bootstrap，章节复核工作台（proposal / issue / action / timeline） |
| `/runs` | `RunsPage` | `src/features/runs/RunsPage.tsx` | 232 | 运行总览：阶段列表、进度条、主操作按钮、焦点章节、最近事件 |
| `/deliverables` | `DeliverablesPage` | `src/features/deliverables/DeliverablesPage.tsx` | 165 | 三种交付资产下载、交付阻塞原因、导出记录 |
| `/library` | `LibraryPage` | `src/features/library/LibraryPage.tsx` | 435 | 书库：搜索/过滤/分页、打开、下载菜单、删除确认框 |
| 其他 | 无 | `src/app/App.tsx:16-21` | — | **没有 404 / catch-all 路由**，未知路径只渲染空 `Outlet` |

URL 不携带任何业务标识：当前文档 id 存在 `localStorage["book-agent.current-document-id"]`（`WorkspaceContext.tsx:115,124-142`），章节选择只在内存。因此没有深链接、多标签页会互相覆盖选择。

### 1.2 布局壳 `AppLayout`（`src/components/AppLayout.tsx`）

- 左侧固定 48px 折叠侧栏，hover 展开到 200px（`AppLayout.module.css:10-29`），≤860px 变为抽屉 + 遮罩 + 汉堡按钮（`AppLayout.tsx:62-67,119-126`；CSS `291-346`）。
- 侧栏底部：`API` / `OpenAPI` 外链（`AppLayout.tsx:97-102`，指向 `SERVICE_LINKS`，见 §11 #1）与健康状态点（`AppLayout.tsx:104-112`，来自 `["health"]` 查询，30 s 轮询）。
- 顶栏显示当前页名 + 当前书名（`AppLayout.tsx:127-135`）。

### 1.3 端到端用户流程（上传 → bootstrap → translate → review → export → download）

| 步骤 | 触发位置 | 调用链 | 端点（均在 `VITE_API_BASE_URL`，默认 `/v1`） | 后端成功码 | 后端错误码（来源） |
|---|---|---|---|---|---|
| 上传 + bootstrap | `WorkspacePage.tsx:68-77,171-178`（`accept=".pdf,.epub"`） | `uploadFile` → `uploadMutation` → `uploadDocument` | `POST /documents/bootstrap-upload`（multipart `source_file`） | 201 `DocumentSummaryResponse` | 400 解析失败 `ValueError`（`routes/documents.py:152-153`）；422 缺文件；503 DB 不可用（`main.py:118-125`） |
| 成功后 | `WorkspaceContext.tsx:390-394` | `setQueryData(["document", id])` + `selectDocument` + 全量失效 | — | — | — |
| 启动整书转换 | `WorkspacePage.tsx:235-243`、`RunsPage.tsx:115-121` | `runPrimaryAction` → `getPrimaryRunAction` 决定 mode（§4） | create: `POST /runs`（`run_type:"translate_full"`, `requested_by:"react-ui"`）→ 再 `POST /runs/{id}/resume` | 201 / 200 | 404 文档不存在；409 `RunControlTransitionError`（`routes/runs.py:144-147`） |
| 继续 / 重试 | 同上 | `resumeRun` / `retryRun`（`api.ts:679-705`） | `POST /runs/{id}/resume`、`POST /runs/{id}/retry` | 200 | 409 非法转移（resume 只允许 queued/paused，`run_control.py:377`；retry 只允许 failed/cancelled/paused 或「stale failed-stage」，`run_control.py:394-402`） |
| 进度观察 | `WorkspaceContext.tsx:168-197` | 4 个查询 2.5 s 轮询 | `GET /documents/{id}`、`GET /runs/{id}`、`GET /runs/{id}/events?limit=8&offset=0`、`GET /documents/{id}/exports?limit=5&offset=0` | 200 | 404；注意 `GET /runs/{id}` 有副作用：running/draining 时唤醒执行器（`routes/runs.py:30-32,152`） |
| review（人工复核） | `WorkspacePage.tsx:88-113,362-444` | `approveMemoryProposal` / `rejectMemoryProposal` / `executeChapterAction` | `POST /documents/{d}/chapters/{c}/memory-proposals/{p}/approve|reject`；`POST /actions/{id}/execute?run_followup=true` | 200 | 404 / 409（`_proposal_http_exception`，`routes/documents.py:87-92`）；action 404 |
| 章节队列 | `WorkspaceContext.tsx:213-241`、`WorkspacePage.tsx:59-63` | worklist / worklist detail / chapters | `GET /documents/{id}/chapters/worklist?limit=50&offset=0[&queue_priority&assigned&assigned_owner_name]`、`GET /documents/{id}/chapters/{c}/worklist`、`GET /documents/{id}/chapters` | 200 | worklist 404；`/chapters` 对未知文档返回 `[]` 而非 404（`routes/documents.py:245-271`） |
| export（按需） | `api.ts:765-796` | `downloadDocumentExport`：先 GET 下载，404 则 `POST /documents/{id}/export` 入队，轮询 `GET /runs/{id}` 到终态再 GET 下载 | `POST /documents/{id}/export {export_type}` | 202 `DocumentRunSummaryResponse`（`routes/documents.py:567-585`） | 404 |
| download（整书） | `DeliverablesPage.tsx:28-35`、`LibraryPage.tsx:105-115` | `downloadAsset` / `downloadDocumentExport` | `GET /documents/{id}/exports/download?export_type=merged_html|bilingual_html|merged_markdown|review_package` | 200 文件或 zip，`Content-Disposition` 双形式（`export_downloads.py:167-174`） | 404 无成功导出（`export_downloads.py:444-449,552-556`）；410 记录已作废（`export_downloads.py:93-110`） |
| download（单章） | `WorkspacePage.tsx:115-122,347-352`、`RunsPage.tsx:57-64,191-197` | `downloadChapterAsset` → `downloadChapterExport` | `GET /documents/{id}/chapters/{c}/exports/download?export_type=bilingual_html` | 200 | 404 / 410 |
| 书库 | `LibraryPage.tsx:78-90` | `listDocumentHistory` | `GET /documents/history?limit&offset[&query&status&latest_run_status&merged_export_ready]` | 200 | 422（enum 不合法；`query` 有 `min_length=1`，前端只在非空时传） |
| 删除 | `LibraryPage.tsx:117-130` | `deleteDocument` | `DELETE /documents/{id}` | 204 | 404；409 `DocumentBusyError`（`routes/documents.py:207-219`） |
| 健康 | `WorkspaceContext.tsx:156-160` | `getHealth` | `GET /health` | 200 `{status:"ok"}`（会执行 `SELECT 1`） | 503 |

未被前端使用的后端端点（供优化时参考）：`GET /meta`、`GET /documents/contract`、`POST /documents/bootstrap`（路径式）、`POST /documents/history/backfill`、`GET /documents/{id}/exports/{export_id}`、`GET .../memory-proposals`（列表）、`PUT/POST .../worklist/assignment[/clear]`（api.ts 有封装但 UI 没有入口）、`POST /documents/{id}/translate|review`、`POST /runs/{id}/pause|drain|cancel`、`GET /runs/{id}/lineage`、`GET /runs/{id}/cost`、**`GET /runs/{id}/stream`（SSE）**、`/providers/*`（LLM 凭据管理，完全没有 UI）。

### 1.4 各页面细节

**WorkspacePage（`/`）**
- 无文档：大号上传区（`:141-183`），选择文件后按 `Bootstrap`；有反馈条 `feedback`（`:179-181`）。
- 有文档：紧凑上传条（`:186-215`）、文档头（标题、状态徽章、「回到首页上传新书」、5 个统计、主操作按钮 `:218-246`）、工作台两栏（`:249-467`）。
- 左栏「Chapters」：两个 `<select>` 过滤（优先级、是否分配）+ Reset（`:258-284`）；列表优先渲染 worklist 队列条目（`:288-307`），队列为空时回退渲染 `GET /chapters` 的全章节（`:308-322`），都为空显示「No chapters found.」（`:323-325`）。
- 右栏：章节头（标题、`Download bilingual`、3 个统计 `:342-359`）、MEMORY PROPOSALS（Approve/Reject `:362-392`）、RECENT ISSUES（`:395-419`）、PENDING ACTIONS（Execute `:422-444`）、TIMELINE（前 20 条 `:447-463`）。
- 本页自带一个 React Query 查询 `["document-chapters", docId]`（`:59-63`），是唯一不在 Context 里的查询。

**RunsPage（`/runs`）**
- 仅当 `currentRun && currentDocument` 才显示总览（`:74`）；否则「No active run」空态（`:163-170`）。因此**文档已解析但尚未创建 run 时，本页没有「开始整书转换」按钮**，只能回 `/` 点。
- 总览：5 个统计、4px 进度条（`:103-111`）、主操作 + `Refresh`（`:114-125`）、5 步 pipeline 列表（`:132-160`，`PIPELINE_STEPS`）。
- 下方两栏：FOCUS CHAPTERS（`getFocusChapters` 取 open issue 最多的前 6 章，`workflow.ts:437-447`）、RECENT EVENTS（最多 8 条，来自 `getRunEvents` 的 `limit=8`）。

**DeliverablesPage（`/deliverables`）**
- 三个资产行（`DELIVERY_ASSETS`：merged_html / bilingual_html / review_package，`workflow.ts:58-77`），按钮由 `downloadReady` 决定可用（`:55-61`）。
- GATE 区：`deliverableBlockerReason` 文案 + 前 3 个 issue hotspot（`:109-137`）。
- LOG 区：导出记录（最多 5 条，因 `getDocumentExports` 固定 `limit=5`，`api.ts:509-513`）。

**LibraryPage（`/library`）**
- 搜索（`useDeferredValue`）、3 个下拉过滤、Reset；分页（页码窗口 `buildPageWindow`、跳页表单、每页 12/24/50/100）。
- 每行：Open（`selectDocument` + `navigate("/")`，`:99-102`）、Download 下拉菜单（仅 `merged_export_ready` 时可点，`:247-269`，菜单含 merged_html / merged_markdown / bilingual_html）、Delete（自定义确认弹窗 `:398-432`）。
- 删除成功只失效 `["document-history"]`（`:124`），不处理「删除的是当前选中的文档」。

---

## 2. `WorkspaceContext.tsx` 的职责与拆分理由

### 2.1 它持有什么（`src/app/WorkspaceContext.tsx`）

| 类别 | 内容 | 行号 |
|---|---|---|
| 本地 UI 状态 | `selectedDocumentId`（初始从 localStorage 读）、`selectedReviewChapterId`、`chapterWorklistFilters{queuePriority, assignment, assignedOwnerName}` | 146-154 |
| 查询（7 个） | `["health"]` 30 s；`["document-history","bootstrap"]` staleTime 30 s；`["document", id]`；`["run", runId]`；`["run-events", runId]`；`["document-exports", id]`；`["chapter-worklist", id, 3 个 filter]`；`["chapter-worklist-detail", id, chapterId]` | 156-241 |
| 副作用 | 写 localStorage；启动时若无选中文档则自动选中历史里第一个「活跃 run」的文档；章节自动选择/纠正；`Document not found` 时清空选中 | 243-315 |
| 变更（5 个 mutation） | upload、runAction（create/resume/retry/recover 的分支逻辑都在 `mutationFn` 里）、reviewDecision、assignment、actionExecution | 388-532 |
| 直接函数 | `downloadAsset`、`downloadChapterAsset`（不是 mutation，无 pending 状态） | 534-548 |
| 导出的 value | 45 个字段的大对象，每次渲染新建 | 550-607 |

### 2.2 为什么要拆（源码层面的证据）

1. **单一 Provider 承载全部数据 + 全部写操作 + UI 选择状态**，四个页面共用；`value` 对象没有 `useMemo`，所有回调都是普通函数（`:317-386,534-548`），每次 Provider 渲染都会生成新引用 → 运行期间（6 个查询各 2.5 s 一次）所有 `useWorkspace()` 消费者每 2.5 s 至少重渲染 6 次。
2. **大量导出字段无消费者**（grep 各页面）：`assignChapterOwner`、`clearChapterAssignment`、`setChapterAssignedOwnerFilter`、`assignmentPending`、`currentDocumentLoading`、`currentDocumentError`、`currentRunLoading`、`chapterWorklistError`、`currentChapterReviewError`、`serviceLinks`、`bootstrapHistory`、`selectedDocumentId` —— 12 个字段只在 Context 内定义，页面从不读取。其中三个 `*Error` 说明**错误信息计算了却没有展示**。
3. **业务分支逻辑混在 mutation 中**（`:397-451` 的 create→resume 两步、`recover` 模式的「先刷新再决定 resume/retry」），与 `lib/workflow.ts` 的 `getPrimaryRunAction` 形成两层决策。
4. **隐式耦合的自动行为**：`:248-267` 启动时自动打开一个正在运行的文档（用户没有选择就被带进工作台）；`:269-306` 每次 worklist 变化都可能强制改变用户选中的章节（过滤器把当前章节筛掉时跳到第一项）。这些应属于页面级或路由级状态。
5. **PLAN 的记录**：`docs/refactor/PLAN.md:142`「`WorkspaceContext` 拆分未做」。

合理拆分方向（仅建议）：`useDocumentQueries(documentId)`、`useRunQueries(runId)`、`useChapterWorklist(documentId, filters)`、`useRunActions()`、`useDownloads()` 各自成 hook；选择状态上提到 URL（`/documents/:id/chapters/:chapterId`）。

---

## 3. `lib/api.ts` 与 `api-types.gen.ts` 的关系

### 3.1 生成脚本工作方式（`scripts/generate_frontend_api_types.py`）

- 直接 `create_app().openapi()`（`:68-72`），只遍历 `components.schemas`（`:54`），按名字排序，每个 schema 输出 `interface`（有 `properties`）或 `type` 别名（枚举等）。
- 类型映射 `_ts_type`（`:21-49`）：`$ref`→名字、`anyOf`→联合、`enum`/`const`→字面量、数组、`additionalProperties`→`Record`，其余 `unknown`。
- **可选性规则**：不在 `required` 里的字段一律加 `?`（`:58-62`）。Pydantic 有默认值的字段（如 `chapters: list = []`）在 OpenAPI 里不 `required`，于是生成为可选，但后端总会序列化它们。这就是「生成类型比实际响应弱」的根源，也是 19 个手写类型没能替换的直接原因。
- `--check` 模式做**逐字节比较**（`:80-84`）；`tests/test_frontend_api_types.py` 用子进程跑 `--check`。任何 FastAPI/Pydantic 升级导致 schema 文本差异（描述、顺序、`title`）都会让该测试失败 —— 但脚本只输出类型不含描述，风险主要是 `anyOf` 顺序和新增字段。
- **只覆盖 schema，不覆盖 path/operation**：端点路径、query 参数、HTTP 方法、状态码全部在 `api.ts` 手写；`GET /documents/{id}/chapters` 没有 `response_model`（`routes/documents.py:245`），所以 `ChapterListItem` 只能手写（`api.ts:524-530`）。
- 生成文件头注释后多一个空行（`api-types.gen.ts:2-3`），无害。

### 3.2 `api.ts` 中类型分类

**16 个别名（已指向生成类型）**：`DocumentStatus`(:3)、`HealthResponse`(:17)、`RunEventSummary`(:127)、`TranslationUsageSummary`(:175)、`IssueHotspotEntry`(:177)、`IssueChapterHighlights`(:188)、`ExportRecordSummary`(:190)、`ChapterMemoryProposalDecisionAudit`(:210)、`ChapterMemoryProposal`(:212)、`ChapterWorklistTimelineEntry`(:224)、`ChapterWorklistAssignment`(:324)、`ChapterWorklistAssignmentClearResponse`(:337)、`ChapterWorklistIssue`(:339)、`ChapterWorklistAction`(:341)、`ChapterWorklistAssignmentHistoryEntry`(:362)、`ChapterMemoryProposalDecisionResponse`(:390)。与 PLAN.md:141「16 个」一致。

**手写但有生成对应物、且存在差异的（PLAN 说的 19 个）**：

| 手写类型（api.ts 行） | 生成对应 | 差异 |
|---|---|---|
| `RunStatus` :5-15 | `DocumentRunStatus` :326 | 手写含 `pending`/`partial`（后端不存在）且以 `\| string` 兜底；**缺 `succeeded_with_warnings`** |
| `ChapterSummary` :19-28 | `ChapterSummaryResponse` :89-104 | 手写把 `open_issue_count`/`bilingual_export_ready` 设为必填；生成为可选；生成多出 `risk_level`、`quality_summary` 等 |
| `DocumentSummary` :30-52 | `DocumentSummaryResponse` :354-378 | `status: DocumentStatus`（窄）vs `string`；`chapters` 必填 vs 可选；缺 `pdf_*` 字段 |
| `DocumentHistoryEntry` :54-76 | `DocumentHistoryEntryResponse` :293-315 | 同上（status 更窄、`merged_export_ready` 等必填） |
| `DocumentHistoryPage` :78-85 | `DocumentHistoryPageResponse` :317-324 | `entries`/`has_more`/`offset` 必填 vs 可选 |
| `RunWorkItemSummary` :115-119 | `RunWorkItemSummaryResponse` :783-787 | `status_counts`/`stage_counts` 必填 vs 可选 |
| `RunLeaseSummary` :121-125 | `RunLeaseSummaryResponse` :755-759 | 同上 |
| `DocumentRunSummary` :129-152 | `DocumentRunSummaryResponse` :328-348 | `status_detail_json` 手写成带 `pipeline`/`control_counters` 的结构（后端是 `Record<string, unknown>`）；缺 `budget` |
| `RunAuditEvent` :154-163 | `RunAuditEventResponse` :707-716 | `payload_json` 必填 vs 可选 |
| `RunAuditEventPage` :165-173 | `RunAuditEventPageResponse` :697-705 | `entries` 必填 vs 可选 |
| `DocumentExportDashboard` :192-208 | `DocumentExportDashboardResponse` :258-287 | 手写只保留 UI 用到的子集；生成多 10+ 字段 |
| `ChapterMemoryProposalSurface` :214-222 | `ChapterMemoryProposalSurfaceResponse` :63-71 | 必填 vs 可选 |
| `ChapterMemoryProposalQueueSummary` :226-232 | `ChapterMemoryProposalQueueSummaryResponse` :43-49 | 同上 |
| `IssueChapterQueueEntry` :234-272 | `IssueChapterQueueEntryResponse` :570-608 | `is_assigned` 必填 vs 可选 |
| `ChapterOwnerWorkload` :274-288 | `ChapterOwnerWorkloadResponse` :73-87 | 计数字段必填 vs 可选 |
| `DocumentChapterWorklist` :290-313 | `DocumentChapterWorklistResponse` :227-250 | `entries` 等必填 vs 可选 |
| `ChapterWorklistAssignmentRequest` :326-330 | 同名 :142-146 | `note?: string` vs `string \| null` |
| `ChapterWorklistAssignmentClearRequest` :332-335 | 同名 :119-122 | 同上 |
| `ExecuteActionResponse` :343-360 | 同名 :380-398 | 数组字段必填 vs 可选；缺 `rebuilt_snapshots` |
| `DocumentChapterWorklistDetail` :364-383 | `DocumentChapterWorklistDetailResponse` :204-225 | 列表必填 vs 可选；缺 `quality_summary`、`issue_family_breakdown` |
| `ChapterMemoryProposalDecisionPayload` :385-388 | `ChapterMemoryProposalDecisionRequest` :21-24 | `string` vs `string \| null` |
| `RunControlPayload` :401-405 | `RunControlRequest` :744-748 | 同上 |

（上表 22 行，其中 `RunStatus`、两个 Request payload 是否计入 PLAN 的「19」无法确定 —— 待确认；本质差异只有两类：**状态字面量更窄**与**可选性不同**，与 PLAN.md:142 描述一致。）

**纯前端类型、无后端对应物**：`RunStageDetail`/`RunPipelineDetail`/`RunControlCounters`（:87-113，是 `status_detail_json` 的约定结构，后端在 `document_run_executor.py:455-476,1714-1715` 与 `run_execution.py:745-748` 写入，无 schema）、`IssueChapterHighlightEntry`（:179-186，**无任何使用**）、`DocumentChapterWorklistFilters`(:315)、`HistoryFilters`(:392)、`ChapterListItem`(:524)、`DocumentDownloadType`(:740)。

**api.ts 中未被任何页面/Context 引用的导出**（grep 结果）：类型 `DocumentHistoryPage`、`RunPipelineDetail`、`RunControlCounters`、`RunWorkItemSummary`、`RunLeaseSummary`、`RunEventSummary`、`RunAuditEventPage`、`TranslationUsageSummary`、`IssueHotspotEntry`、`IssueChapterHighlightEntry`、`IssueChapterHighlights`、`ExportRecordSummary`、`ChapterMemoryProposalDecisionAudit`、`ChapterMemoryProposal`、`ChapterMemoryProposalSurface`、`ChapterMemoryProposalQueueSummary`、`IssueChapterQueueEntry`、`ChapterOwnerWorkload`、`ChapterWorklistIssue`、`ChapterWorklistAction`、`ChapterWorklistAssignmentHistoryEntry`、`HistoryFilters`、`RunControlPayload`、`ChapterListItem`、`DocumentDownloadType`（多数作为内部嵌套类型间接使用，直接引用为 0）。

### 3.3 fetch 封装（`api.ts:407-462`）

- `API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "/v1").replace(/\/$/, "")`（:407）：构建期注入；若环境变量设为空字符串 `??` 不会回退。
- `requestJson`（:448-454）：无超时、无 `AbortSignal`、无 `credentials`、无自定义 header（无鉴权），错误统一转 `Error(detail|message|"Request failed: N")`（:425-446）。**HTTP 状态码在抛出后丢失**，上层只能靠字符串匹配（如 `WorkspaceContext.tsx:310` 匹配 `"Document not found"`）。
- `deleteDocument` 单独处理 204（:500-507）。
- `saveBinaryResponse`（:723-738）：整个响应先 `blob()` 进内存再用 `<a download>` 触发保存；文件名解析 `filename*=UTF-8''` 优先（:707-721）。
- `downloadDocumentExport`（:765-796）：404 → `POST /export` → `waitForRunToFinish` 每 2 s 轮询、最长 10 min（:744-759）；终态集合 `TERMINAL_RUN_STATUSES` 含 `paused`（:742）。

---

## 4. `lib/workflow.ts`：业务逻辑是否与后端重复

`workflow.ts`（603 行）是纯函数集合，无副作用；但它承担了大量**状态推导**，其中确有与后端重复/漂移的部分：

| 函数（行） | 做什么 | 与后端关系 |
|---|---|---|
| `isRunActive`(:97-99) | `queued/running/draining/paused` 视为活跃 → 驱动所有轮询 | 后端 `DocumentRunStatus`（`domain/enums.py:289-301`）；前端把 `paused` 当活跃继续轮询（合理但成本高） |
| `stageStatus`(:191-213) | 从 `run.status_detail_json.pipeline.stages[key].status` 推每个阶段状态；bootstrap 阶段由 `document.status==="failed"` 推 | 依赖后端**无 schema 的 JSON 约定**（`document_run_executor.py:1699-1715`）；`run.status==="succeeded"` 时把所有阶段标成 succeeded（:209-211）—— **不含 `succeeded_with_warnings`** |
| `failedPipelineStage`(:215-226) | 找第一个 failed/cancelled 阶段 | 后端有 `classify_run_outcome`（enums 注释 :298），前端另算一遍 |
| `failedStageRetryEligible`(:228-244) + `STALE_FAILED_STAGE_RETRY_MS = 3 min`(:95) | 若 run 非终态但最近心跳/更新超过 3 分钟，允许重试 | **直接复制后端规则**：`run_control.py:398 _is_stale_failed_stage_run`。两边阈值各自维护，会漂移 |
| `getPrimaryRunAction`(:352-394) | 决定 disabled/create/resume/retry/recover 及按钮文案 | 复制了后端状态机的允许转移（resume 只在 queued/paused，retry 在 failed/cancelled/paused）；后端在 409 时才告诉你不允许。`succeeded_with_warnings` 落到 `create` 分支且文案是「开始整书转换」而不是「重新运行」(:391) |
| `translateProgress`(:246-272) | 用 `control_counters.completed_work_item_count / total_packet_count` | `completed_work_item_count` 在后端按 run 内所有 SUCCEEDED work item 计数（`run_control.py:263-269`），是否只含 translate 阶段 —— 待确认；前端用 `Math.min(1, …)` 钳制说明作者也不确定 |
| `documentBadge`(:313-350) 与 `historyBadge`(:449-480) | 同一套「阶段 × 状态 → 中文标签」 | 两个函数 70% 相同，只是入参形状不同（`DocumentSummary+run` vs `DocumentHistoryEntry`） |
| `nextMilestoneText`/`assetAvailabilityText`/`deliverableBlockerReason`(:274-311,532-589) | 文案化的「下一步」和「为什么不能下载」 | 属于产品解释层，后端没有等价物；但 `blockingIssueCount` 只加总 `issue_hotspots`（:430-435），而 dashboard 只取了 `limit=5` 条记录 |
| `downloadReady`(:515-530) | 三种资产是否可下 | `review_package` 依赖 `exportsDashboard.records`（仅 5 条）→ 记录多时会误判为不可下 |
| `STATUS_LABELS`(:79-93) | 状态中文 | 含 `pending`/`partial`/`retryable_failed`（后端无此状态，死键）；缺 `succeeded_with_warnings`、`ingested`、`parsed` |
| `shorten`(:124-131)、`currentPipelineDetail`(:176-181)、`nextMilestoneText`(:274) | — | **无调用方**（grep），死代码 |

结论：前端把「run 状态机 + 阶段推导 + 重试资格」在客户端重算了一遍；后端并没有提供「下一可用动作」的接口，所以这层重复是被迫的。生产化时应由后端返回 `allowed_actions` / `stage_states`，前端只做渲染。

---

## 5. 数据获取策略

### 5.1 React Query 配置

- `queryClient.ts:3-10`：`retry: 1`、`refetchOnWindowFocus: false`，其余默认（`staleTime: 0`，`gcTime` 5 min）。无全局 `onError`、无 `QueryCache` 订阅、无 devtools。
- 单例在模块顶层创建并被测试 `setup.ts:24-28` 直接 `clear()`。

### 5.2 Query key 设计与轮询

| key | enabled | 轮询 | 备注 |
|---|---|---|---|
| `["health"]` | 总是 | 30 s 固定 | `WorkspaceContext.tsx:156-160` |
| `["document-history","bootstrap"]` | 总是 | 无，staleTime 30 s | 只为启动自动恢复用（:162-166） |
| `["document-history","library", q, status, runStatus, mergedReady, pageSize, offset]` | 页面挂载 | 无；`placeholderData: prev` | `LibraryPage.tsx:78-90`，与上面共享根 key `"document-history"` 便于一起失效 |
| `["document", id]` | 有 id | **2.5 s**，条件 `isRunActive(data.latest_run_status)`；`retry:false` | :168-175 |
| `["run", runId]` | 有 runId | 2.5 s，条件用**文档**的 `latest_run_status` | :178-183 |
| `["run-events", runId]` | 有 runId | 2.5 s，条件用 run 状态 | :185-190 |
| `["document-exports", id]` | 有 id | 2.5 s，条件 run 或文档状态 | :192-197 |
| `["chapter-worklist", id, pri, assign, owner]` | 有 id | 2.5 s，同上；`retry:false` | :213-229 |
| `["chapter-worklist-detail", id, chapterId]` | 有 id+chapter | 2.5 s，同上；`retry:false` | :231-241 |
| `["document-chapters", id]` | 有 doc | 无 | `WorkspacePage.tsx:59-63` |

活跃 run 期间每个客户端每 2.5 s 发 **6 个请求**（其中 worklist 是后端较重的队列计算），README 也如此宣称（`README.md:202`）。后端已实现 SSE `GET /v1/runs/{id}/stream`（`routes/run_stream.py`，基于 Postgres LISTEN/NOTIFY，含 `Last-Event-ID` 回填），**前端完全没有使用**。

### 5.3 缓存失效与乐观更新

- `invalidateWorkspaceQueries`（:360-375）按根 key 谓词失效 7 类查询；upload、runAction、actionExecution 成功后调用；reviewDecision 与 assignment 只失效 3 个精确 key（:470-480,508-518）。`["document-chapters"]` 不在失效列表里。
- `refreshCurrentDocument`（:377-386）= 全量失效 + 强制 refetch 文档。
- **没有任何乐观更新**；唯一的 `setQueryData` 是上传成功后写入文档（:391）。
- 删除文档后仅失效 `["document-history"]`（`LibraryPage.tsx:124`），`["document", id]` 缓存及 `selectedDocumentId` 不清理。

---

## 6. 错误处理与用户反馈

- **模式统一但简陋**：每页一个 `feedback: {tone, text} | null` 本地 state（`WorkspacePage.tsx:20,51`、`RunsPage.tsx:25,38`、`DeliverablesPage.tsx:20,24`、`LibraryPage.tsx:19,48`），`try/catch` 后 `setFeedback`，渲染成一条带左边线的 `<div className={s.feedback} data-tone>`。无自动消失、无关闭按钮、无 `role="status"`/`aria-live`、跨页面不共享、切页即丢失。
- **查询错误基本不展示**：`currentDocumentError`、`chapterWorklistError`、`currentChapterReviewError` 在 Context 中算好（:559-581）但没有页面读取。worklist 失败 → `isLoading` 变 false → 列表为空 → 显示「No chapters found.」（`WorkspacePage.tsx:323-325`），把错误伪装成空态。文档查询失败 → `currentDocument` 为 null → 工作台退回上传界面，没有任何解释（唯一处理是 `Document not found` 字符串匹配清空选择，:308-315）。
- **重试**：仅 React Query 默认 `retry: 1`；文档/worklist/detail 显式 `retry: false`。无手动「重试」按钮（RunsPage 的 `Refresh` 是全量刷新）。
- **加载态**：`Loading...` 文本（`WorkspacePage.tsx:287,336`、`LibraryPage.tsx:221`）；按钮 pending 时文案变 `...`/`Uploading...`/`Executing...`（`WorkspacePage.tsx:176,208,241`、`RunsPage.tsx:120`）。无骨架屏；下载/导出等待期间（最长 10 分钟）按钮**不禁用**（`DeliverablesPage.tsx:55-61` 只看 `downloadReady`；`LibraryPage.tsx:244-268` 无 pending）。
- **空态**：每页有插画空态（`RunsPage.tsx:163-170`、`DeliverablesPage.tsx:70-80`、`LibraryPage.tsx:373-394`）；Library 区分「有过滤无结果」与「无文档」。
- **错误边界**：**没有** `ErrorBoundary`（`main.tsx`、`App.tsx` 均无），渲染异常 = 白屏。
- **后端语义 → 前端语义丢失**：409（run 转移冲突 / 文档忙 / proposal 冲突）、410（导出作废）、503（DB 不可用）都只剩 `detail` 字符串。

---

## 7. 样式系统与可访问性

### 7.1 结构

- `src/styles/tokens.css`（101 行）：`:root` 定义灰阶、4 个信号色、语义表面/文字/边框、间距 `--sp-1..12`、圆角、字体族（Inter / JetBrains Mono / Noto Sans SC）、字号 `--text-xs(11px)..3xl`、布局常量（侧栏 48/200px、顶栏 40px、内容 1200px）。`color-scheme: light`（:2）—— **没有深色模式**，也没有 `prefers-color-scheme` 分支（全局 grep 无 `dark`/`prefers-color`）。
- `src/styles/global.css`（214 行）：box-sizing reset、基础排版、`button/input` 继承字体、`:focus-visible` 2px 蓝色外框（:80-88）、自定义滚动条、共享 `.btn/.btn-primary/.btn-sm/.btn-approve/.btn-reject`（:116-176）、`.label`、两个 keyframes、`prefers-reduced-motion`（:206-214）。
- 每个页面/组件一个 CSS Module（`*.module.css`，合计 7 个、~2100 行、~270 个类）。所有响应式断点统一为 `@media (max-width: 860px)`（`AppLayout:291`、`WorkspacePage:618`、`LibraryPage:463`、`RunsPage:314`、`DeliverablesPage:194`）。
- 字体从 Google Fonts 加载（`index.html:7-12`）—— 内网/离线部署会退回系统字体并多一次外部请求。

### 7.2 具体可访问性/样式问题

1. 侧栏文字标签仅在 `:hover` 时 `opacity:1`（`AppLayout.module.css:126-136,138-149,159-169,207-216`）：键盘 Tab 到 NavLink 时看不到标签，仅靠 `title` 属性（`AppLayout.tsx:86`）。
2. `.content { width: 70%; max-width: 1100px }`（`AppLayout.module.css:273-280`），移动端媒体查询没有覆盖 `width` → 手机上内容只占 70% 视口且无左右 16px 留白。
3. 大量 9-10px 字号（`global.css:157`、`WorkspacePage.module.css:349,377`、`LibraryPage.module.css:157` 等）与 `opacity:.35` 的禁用态（`global.css:77,140`），对比度偏低。
4. 弹窗 `role="dialog" aria-modal` 放在**遮罩层**上（`LibraryPage.tsx:398-405`），无焦点陷阱、无 Escape、无焦点归还。
5. 下载下拉菜单（`LibraryPage.tsx:247-268`）无 `aria-haspopup/aria-expanded`、无键盘导航、只在 `mousedown` 外点关闭（:66-75）。
6. 反馈条无 `aria-live`（各页面 `feedback` 渲染处）。
7. 章节队列用 `<button data-active>` 表示选中（`WorkspacePage.tsx:289-306`），无 `aria-pressed/aria-current`；worklist 过滤 `<select>` 无 `aria-label`（:259-277）。
8. 文件输入视觉隐藏（`WorkspacePage.module.css:58-64`），包在 `<label>` 里可访问；但没有拖放。
9. 做得好的地方：全局 `:focus-visible`、`prefers-reduced-motion`、Library 分页按钮有 `aria-label`/`aria-current`（:295-341）、搜索清除按钮有 `aria-label`（:178）。
10. `Surface` 组件声明了 `description` prop 却不渲染（`Surface.tsx:8,13`）。

---

## 8. 测试

### 8.1 现状（`npx vitest run`：5 文件 / 6 用例通过，6.9 s）

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `src/app/App.test.tsx` | 1 | 侧栏渲染 + 点击「书库」切换 |
| `src/features/library/LibraryPage.test.tsx` | 1 | 历史条目 Open → 工作台显示书名 |
| `src/features/deliverables/DeliverablesPage.test.tsx` | 1 | `localStorage` 预置文档 id → 两个下载按钮可用 |
| `src/features/workspace/WorkspacePage.test.tsx` | 1 | 「回到首页上传新书」清空选择与 localStorage |
| `src/lib/api.test.ts` | 2 | `downloadDocumentExport` 的 404→入队→轮询→下载；失败 run 抛错 |

- **Mock 方式**：每个测试文件自建 `installFetchMock()`，用 `vi.stubGlobal("fetch", vi.fn(...))` 按 URL 子串/`pathname` 分派，返回 `new Response(JSON)`；未匹配即 `throw`（`App.test.tsx:8-36` 等）。没有 MSW，没有共享 fixture 工厂。
- `src/test/setup.ts`：polyfill `URL.createObjectURL/revokeObjectURL`、`scrollIntoView`（后者当前无调用方）；`afterEach` 清 `queryClient`、`localStorage`、`vi.restoreAllMocks()`（注意 `restoreAllMocks` 不会撤销 `stubGlobal`，只有 `api.test.ts:10-13` 自己调用了 `unstubAllGlobals`）。
- `vitest.config.ts`：`jsdom`、`globals: true`；`tsconfig.json:19` 把 `vitest/globals` 类型也带进了应用编译。
- `api.test.ts` 运行时 jsdom 打印 `Not implemented: navigation` 噪音（`anchor.click()` 触发），不影响结果。

### 8.2 被删测试的遗留夹具（PLAN.md:75 提到「遗留夹具在 P4 前端整理时清理」）

- `WorkspacePage.test.tsx` 851 行中约 770 行是 `installFetchMock` 夹具（:45-817），模拟了三章的 proposal/assignment/action/timeline 全状态机与 8 个写端点（approve/reject/assignment PUT/clear/execute），**当前唯一一个测试完全不触碰这些**。
- 死代码：`waitFor`、`within` 导入未用（:1）、`STORAGE_KEY_WORKBENCH_MODE` 未用（:9）—— `tsc --noUnusedLocals` 会报出（tsconfig 没开）。
- 夹具返回 `action.status:"completed"`（:711,729）与后端一致，但生产代码判断的是 `"executed"`（见 §11 #4）。

### 8.3 缺口

- `workflow.ts`（逻辑最密集的 603 行）**零单元测试**。
- 没有错误路径测试（4xx/5xx、网络失败）、没有轮询/失效行为测试、没有 a11y 断言、没有覆盖率配置、没有 E2E（Playwright/Cypress 都不在依赖中）。
- 没有 `lint`/`typecheck` npm script；`build` 脚本靠 `tsc && vite build` 兜底类型检查。

---

## 9. 构建与部署

### 9.1 本地/开发

- `frontend/vite.config.ts`：dev server `0.0.0.0:4173`（对局域网暴露），`/v1` 代理到 `VITE_API_PROXY_TARGET`（默认 `http://127.0.0.1:8999`）。无 `base`、无 `build.*` 配置、无手动分块。
- `frontend/.env.example`：`VITE_API_BASE_URL=/v1`、`VITE_API_PROXY_TARGET=http://127.0.0.1:8999`；`frontend/.env` 未跟踪（根 `.gitignore:5-6`），本机版本只有注释。
- `dev.sh:239-251`：`npm install`（若无 node_modules）后 `npm run dev -- --host 0.0.0.0 --port 4173`。
- `service.sh:43-45,327-360`：所谓「service」模式**同样是 `npm run dev`**（`FRONTEND_CMD="npm run dev -- --host 0.0.0.0 --port ${FRONTEND_PORT}"`），用 nohup + pid 文件守护；没有 build/preview/静态服务分支。

### 9.2 生产

- `Dockerfile`：`python:3.12-slim`，只 `COPY pyproject.toml README.md alembic.ini src alembic`（:10-12），**没有 Node 阶段、没有 COPY frontend、没有 dist**；`EXPOSE 8000` 仅后端。
- `compose.yaml`：`postgres`、`migrate`、`app`（`58000:8000`），**没有前端服务、没有反向代理**。
- 后端 `main.py` **没有 `StaticFiles` 挂载**（全仓库 grep 无 `StaticFiles`/nginx/caddy），`ui/router.py:13-22` 的 `/` 只返回一个静态「service entry」HTML（`ui/page.py`），页面文案明说「workspace is expected to run as a standalone React/Vite frontend」。`docs/README.md:278` 把 `ui/` 标注为「Frontend serving」已过时。
- `npm run build` 产物 `frontend/dist/`（`.gitignore` 忽略）**没有任何消费者**。`dist/index.html` 用绝对路径 `/assets/...`，若将来挂在子路径需配 `base`。
- 环境变量：`VITE_API_BASE_URL` 在构建期内联到 bundle（`api.ts:407`），**没有运行时注入机制**（如 `window.__CONFIG__` 或 `/config.json`）；每个环境要单独 build。
- CORS：`config.py:65` `cors_allow_origins` 默认空 → 跨域部署必须设置 `BOOK_AGENT_CORS_ALLOW_ORIGINS`（`.env.example:20` 给的是 dev 端口）；`main.py:82-90` `allow_credentials=True` + `expose_headers=["Content-Disposition"]`（下载文件名依赖此项）。
- 性能预算：无；单 bundle 328 KB（React 19 + Router 7 + Query 5 全部打进一个 chunk），没有代码分割、没有分析工具。

结论：**当前仓库没有可交付的前端生产路径**；生产化至少需要：多阶段 Dockerfile（Node build → 拷贝 `dist`）+（后端 `StaticFiles` 挂载并处理 SPA fallback，或独立 nginx/caddy 服务）+ 运行时配置注入 + CORS/base path 决策。

---

## 10. 缺失的生产要素清单

| 要素 | 现状 | 证据 |
|---|---|---|
| 鉴权 / 身份 | 无登录、无 token、无 header；操作者身份硬编码 `"react-ui"`（`api.ts:669,673`、`WorkspaceContext.tsx:408-442`）、`"reviewer-ui"`（`WorkspacePage.tsx:89`）；后端也无鉴权依赖（`api/deps.py` grep 无 auth） | — |
| 国际化 | 无 i18n 库；文案中英混杂（`WorkspacePage.tsx:154-155` 「载入书稿」+「Upload a PDF or EPUB…」；按钮 `Bootstrap`/`Approve` 与 「回到首页上传新书」并存）；`Intl` 硬编码 `zh-CN`（`workflow.ts:105,113`）；`formatDate` 不含年份（:113-118） | — |
| 大列表虚拟化 | 无；worklist 固定 `limit=50` 无分页（`api.ts:545`），章节回退列表全量渲染（`WorkspacePage.tsx:308-322`），timeline 截前 20（:451） | — |
| 上传进度 / 拖放 / 大小限制 | 无进度（`fetch` FormData）；无拖放；前后端都没有大小限制（后端 grep 无 max upload） | `api.ts:468-475` |
| 下载进度 / 流式 | 整个文件先进内存 blob（`api.ts:724`）；导出等待最长 10 min 无进度、不可取消（:744-759） | — |
| 错误边界 / 全局错误 | 无 `ErrorBoundary`；无全局 query error 处理；无 toast 系统 | `main.tsx`、`App.tsx`、`queryClient.ts` |
| 日志 / 监控 / 追踪 | 无 Sentry/OTel、无 request-id header、无 `console` 以外的埋点 | `api.ts:448-454` |
| 性能预算 / 分包 / 分析 | 无 | `vite.config.ts` |
| E2E / 视觉回归 / a11y 测试 | 无 | `package.json` |
| Lint / 格式化 / CI | 无 ESLint、Prettier、`.github/workflows` | `ls` |
| 深链接 / 路由状态 | 文档与章节选择不在 URL；无 404 路由 | `App.tsx`、`WorkspaceContext.tsx:115` |
| 实时推送 | 后端有 SSE，前端用 6 路 2.5 s 轮询 | `run_stream.py`、`WorkspaceContext.tsx:168-241` |
| 深色模式 / 主题 | 无 | `tokens.css:2` |
| 离线字体 | 依赖 Google Fonts | `index.html:7-12` |
| Provider（LLM 凭据）管理 UI | 后端 `/providers/*` 有完整 CRUD/test/activate，前端无页面 | `routes/providers.py` |
| 运行控制 | 无 pause/cancel/drain 按钮（后端有） | `routes/runs.py:207-317` |
| 引擎版本约束 | `package.json` 无 `engines`；vitest 2.1.9 与 vite 7 搭配（vitest 2.x 官方 peer 是 vite 5/6 —— 待确认，`npm ls` 未报错） | — |

---

## 11. 发现的 bug / 不一致 / 风险（带文件:行号）

按严重程度粗略排序。

1. **Swagger/OpenAPI 链接错误**（确定）：`api.ts:409-412` `SERVICE_LINKS.docs = "/v1/docs"`、`openapi = "/v1/openapi.json"`，但 FastAPI 未设置 `docs_url`（`main.py:76-81`），实际路径是 `/docs`、`/openapi.json`（运行 `create_app()` 验证：`docs_url /docs openapi_url /openapi.json`）。侧栏两个外链（`AppLayout.tsx:97-102`）在 dev 下经 Vite 代理到后端得到 404。同样的错误也在后端首页 `ui/page.py:7-8`、`dev.sh:133,229`，且被 `tests/test_frontend_entry.py:40-41` 断言固化；`service.sh:397,431` 用的是正确的 `/docs`。
2. **`succeeded_with_warnings` 未被前端认识**：后端有该终态（`domain/enums.py:300`，生成类型 `api-types.gen.ts:326` 也有），但 `workflow.ts:79-93` 无标签、`:209`/`:260-263` 的「成功 ⇒ 全部阶段成功 / 进度 100%」不触发、`:391` 按钮文案错成「开始整书转换」、`documentBadge` 落到 `statusLabel(document.status)`。`api.ts:5-15` 的 `RunStatus` 也缺它（但因 `| string` 兜底不报错）。
3. **`review_package` 可下载判断依赖只取 5 条的 dashboard**：`api.ts:511` 固定 `limit=5`，`workflow.ts:529` 在 `records` 里找 `review_package && succeeded`；当最近 5 条导出都是其他类型时按钮被禁用，尽管后端 `GET .../exports/download?export_type=review_package` 能成功。`blockingIssueCount`（:430-435）同样只看这份被截断的 dashboard 的 `issue_hotspots`（hotspots 本身是否受 limit 影响 —— 待确认）。
4. **Action 执行结果判定与后端不一致**：`WorkspacePage.tsx:105` `ok = result.status === "executed" || result.issue_resolved`，后端固定返回 `status="completed"`（`routes/actions.py:30`）；于是 `issue_resolved` 为 `null/false`（未跟进或未解决）时反馈条显示为**错误**色，即使动作已成功执行。
5. **删除当前文档不清理工作台**：`LibraryPage.tsx:117-130` 删除后只失效 `["document-history"]`；若被删的是 `selectedDocumentId`，`["document", id]` 缓存仍在、localStorage 仍在，回到 `/` 继续显示已删文档；只有下一次该查询真的重新请求并返回 `Document not found` 时（`WorkspaceContext.tsx:308-315`，非活跃 run 不轮询）才会清空。
6. **导出等待期间无禁用/取消**：`downloadDocumentExport` 可能阻塞 10 分钟（`api.ts:768`），期间 `LibraryPage.tsx:244-268` 与 `DeliverablesPage.tsx:55-61` 的按钮可重复点击 → 重复 `POST /export` 入队多个 export run。Library 下拉菜单里的 `merged_markdown` 在只有 `merged_html` 时必然走这条路径（`LibraryPage.tsx:22-24`）。
7. **`TERMINAL_RUN_STATUSES` 把 `paused` 当终态**（`api.ts:742`）：导出 run 若被暂停，`downloadDocumentExport` 立即抛「导出未完成（paused）」，而 `isRunActive`（`workflow.ts:98`）却把 `paused` 当活跃 —— 两处口径矛盾。
8. **Library 下载入口与可用性不一致**：`LibraryPage.tsx:250,253` 仅 `merged_export_ready` 才能打开菜单，但菜单里的 `bilingual_html` 依赖的是章节导出（`workflow.ts:527` 用 `chapter_bilingual_export_count`），存在「双语可下但菜单打不开」和「菜单能开但 markdown 不存在需现导」两种错位。
9. **前端复制后端「stale failed-stage 3 分钟」规则**：`workflow.ts:95,228-244` vs `run_control.py:398`（`_is_stale_failed_stage_run`）。两边阈值独立维护；前端判错时用户点「重试」得到 409。
10. **create→resume 两步不原子**：`WorkspaceContext.tsx:405-411` 先 `POST /runs`（后端 `_wake_executor`，`routes/runs.py:39`）再 `POST /runs/{id}/resume`。执行器不主动接管 `QUEUED` run（`document_run_executor.py` grep 无 QUEUED），因此目前不会竞争；但若后端将来自动启动 queued run，resume 会 409。建议后端提供「create-and-start」或前端容忍 409。
11. **过滤器变化会劫持用户选中的章节**：`WorkspaceContext.tsx:269-282` 当 worklist 非空且当前章节不在其中，强制跳到 `queueEntries[0]`。用户从「全章节回退列表」选了无 issue 的章节后，一旦过滤条件产生非空队列，选择被覆盖。
12. **`selectDocument` 不重置章节选择**（`WorkspaceContext.tsx:317-321`）：从 Library 打开另一本书时，`["chapter-worklist-detail", newDoc, oldChapterId]` 会先发一次注定 404 的请求（`retry:false`，错误不展示），直到自动选择效果修正。
13. **错误被伪装成空态**：`WorkspacePage.tsx:323-325` 在 worklist 请求失败时显示「No chapters found.」；`chapterWorklistError`/`currentChapterReviewError`/`currentDocumentError` 计算后从未渲染（`WorkspaceContext.tsx:559-581`）。
14. **RunsPage 缺少启动入口**：`RunsPage.tsx:74` 条件 `currentRun && currentDocument`；文档已解析但无 run 时整个操作区不渲染，「开始整书转换」只在 `WorkspacePage.tsx:235-243`。
15. **进度语义待确认**：`workflow.ts:257` 用 `control_counters.completed_work_item_count`（后端按 run 内所有 SUCCEEDED work item 计数，`run_control.py:263-269`）除以 translate 的 `total_packet_count`；若 review/export 阶段也产生 work item，比例会超 1（前端用 `Math.min` 钳到 1，`:270`）。
16. **HTTP 状态码在错误链中丢失**（`api.ts:439-446`）：上层只能靠 `message.includes("Document not found")`（`WorkspaceContext.tsx:310`）之类的字符串匹配，后端改文案即失效；409/410/503 无法区分处理。
17. **`GET /chapters` 对未知文档返回 `[]`**（`routes/documents.py:245-271`，无 404、无 `response_model`）：前端回退列表会把「文档不存在」显示成「No chapters found.」；该端点也因此无生成类型（`api.ts:524-530` 手写）。
18. **Provider 无 `useMemo`/`useCallback`**（`WorkspaceContext.tsx:550-607`）：`value` 每次渲染新建 → 活跃 run 时全部消费者每 2.5 s 重渲染 ≥6 次；`startTransition` 包裹普通 setState（:263,279,286,303,311,318-358）没有实际收益。
19. **移动端内容宽度 70%**：`AppLayout.module.css:273-280` 的 `width: 70%` 在 `@media (max-width: 860px)` 中未覆盖（:291-346 只处理侧栏/顶栏），手机上内容被压到 70% 视口且无水平留白。
20. **可访问性问题汇总**：侧栏标签仅 hover 可见（`AppLayout.module.css:126-136`）；弹窗 `role="dialog"` 在遮罩层上且无焦点管理（`LibraryPage.tsx:398-405`）；下拉菜单无 ARIA/键盘（:247-268）；反馈条无 `aria-live`；9-10px 字号与 `opacity:.35` 禁用态（`global.css:77,140,157`）。
21. **死代码 / 未用导出**：`workflow.ts` 的 `shorten`(:124)、`currentPipelineDetail`(:176，仅内部)、`nextMilestoneText`(:274，仅被 `deliverableBlockerReason` 内部调用)；`api.ts` 的 `IssueChapterHighlightEntry`(:179-186) 零引用；`STATUS_LABELS` 的 `pending/partial/retryable_failed`(:84-87)；Context 的 12 个无消费者字段（§2.2）；`WorkspacePage.tsx:6` 未用的 `Surface` 导入与 `:26` 未用的 `currentExports`；`Surface.tsx:8` 未渲染的 `description`；`setup.ts:22` 的 `scrollIntoView` polyfill 无调用方；测试夹具遗留（§8.2）。`tsconfig.json` 未开 `noUnusedLocals/noUnusedParameters`，所以这些不会被 `tsc` 拦住。
22. **两套徽章逻辑重复**：`workflow.ts:313-350` `documentBadge` 与 `:449-480` `historyBadge` 几乎相同。
23. **`Vite dev` 作为服务进程**：`service.sh:44` 用 `npm run dev` 长驻并 `--host 0.0.0.0`，等于把开发服务器（含 HMR websocket、源码映射）暴露给网络；`vite.config.ts:9` 也默认 `0.0.0.0`。
24. **API base 空字符串陷阱**：`api.ts:407` 用 `??`，`VITE_API_BASE_URL=""` 时不会回退到 `/v1`，所有请求打到根路径。
25. **`GET /runs/{id}` 有副作用**（`routes/runs.py:30-32,152`）：前端 2.5 s 轮询顺带唤醒执行器；若改用 SSE 或降低轮询频率，需确认执行器不依赖这个唤醒（待确认）。
26. **文档过时**：`docs/README.md:278` 「`ui/` — Frontend serving」；`README.md:202` 宣称轮询是设计选择，但同一后端已实现 SSE。
27. **测试夹具与生产代码口径不一致**：`WorkspacePage.test.tsx:711,729` 返回 `status:"completed"`，生产判断 `"executed"`（见 #4），说明该分支从未被测试覆盖。
28. **Context 自动打开活跃文档**（`WorkspaceContext.tsx:248-267`）：首次进入且 localStorage 为空时，只要历史里有活跃 run 就自动选中它 —— 多用户/多书场景下会「打开别人的书」，且与 URL 无关无法回退。
29. **`tsconfig.json:19` 将 `vitest/globals` 纳入应用类型**，生产代码可以无导入地引用 `vi/describe` 而不报错。
30. **依赖版本搭配待确认**：`vitest@2.1.9` 与 `vite@7.3.1`（vitest 2.x 的 peer range 为 vite 5/6；本机 `npm ls` 无报错，可能是 `package-lock` 锁定下的宽松解析）。

---

## 12. 与 PLAN.md 的对应关系

- `docs/refactor/PLAN.md:72`（P1.3）：`frontend/.omc/` 已 `git rm --cached` 并加入 `.gitignore`（根 `.gitignore:37`）；本机磁盘上 `frontend/.omc/` 仍存在（未跟踪，可删）。
- `PLAN.md:75`：删除了 `WorkspacePage.test.tsx` 中 15 个测试，剩 1 个；「遗留夹具在 P4 前端整理时清理」—— 夹具仍在（§8.2）。当时记录「vitest 4/4」，现在因 `api.test.ts` 新增变为 5 文件 / 6 用例。
- `PLAN.md:140`（P4）：「前端下载在 404 时先入队导出 run、等待完成再下载」= `api.ts:765-796`。
- `PLAN.md:141`：类型生成脚本 + 16 个别名 = §3.2。
- `PLAN.md:142`：「其余 19 个手写类型…尚未改为生成类型；`WorkspaceContext` 拆分未做」= §3.2 表、§2.2。

---

## 13. 文件索引（便于后续引用）

| 文件 | 行数 | 角色 |
|---|---|---|
| `frontend/package.json` | 32 | 依赖与 4 个 script（dev/build/preview/test） |
| `frontend/vite.config.ts` | 17 | dev server + `/v1` 代理 |
| `frontend/vitest.config.ts` | 11 | jsdom + globals + setup |
| `frontend/tsconfig.json` | 22 | strict，Bundler 解析，无 unused 检查 |
| `frontend/index.html` | 21 | Google Fonts、`#root` |
| `frontend/src/main.tsx` | 15 | StrictMode + BrowserRouter |
| `frontend/src/app/App.tsx` | 27 | QueryClientProvider + WorkspaceProvider + Routes |
| `frontend/src/app/queryClient.ts` | 10 | 单例 |
| `frontend/src/app/WorkspaceContext.tsx` | 618 | 全局状态/查询/变更 |
| `frontend/src/components/AppLayout.tsx` (+ .module.css 346) | 144 | 壳 |
| `frontend/src/components/StatusBadge.tsx` (+ 28) | 12 | 点 + 文本徽章 |
| `frontend/src/components/Surface.tsx` (+ 40) | 24 | 分区容器 |
| `frontend/src/features/workspace/WorkspacePage.tsx` (+ 653) | 481 | 工作台 |
| `frontend/src/features/runs/RunsPage.tsx` (+ 328) | 232 | 运行 |
| `frontend/src/features/deliverables/DeliverablesPage.tsx` (+ 204) | 165 | 交付 |
| `frontend/src/features/library/LibraryPage.tsx` (+ 496) | 435 | 书库 |
| `frontend/src/lib/api.ts` | 808 | fetch 封装 + 手写类型 |
| `frontend/src/lib/api-types.gen.ts` | 857 | OpenAPI 生成（勿手改） |
| `frontend/src/lib/workflow.ts` | 603 | 纯函数：状态推导、文案 |
| `frontend/src/styles/tokens.css` / `global.css` | 101 / 214 | 设计令牌 / 基础样式 |
| `frontend/src/test/setup.ts` | 29 | 测试环境 |
| `scripts/generate_frontend_api_types.py` | 91 | 类型生成 |
| `tests/test_frontend_api_types.py` / `tests/test_frontend_entry.py` | 23 / 63 | 生成类型同步检查 / 后端首页与 CORS |
| `src/book_agent/app/ui/{router,page}.py` | 22 / 170 | 后端「service entry」静态页 |
| `src/book_agent/app/main.py` | 139 | CORS、异常处理，无静态文件挂载 |
| `Dockerfile` / `compose.yaml` / `service.sh` / `dev.sh` | 21 / 53 / 468 / 261 | 部署与启动（前端仅 dev 模式） |

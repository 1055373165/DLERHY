# 08 · 前端

> 更新于 2026-09-20，分支 `feat/ui-overhaul`（UI/UX 升级 M0–M2 之后）。设计依据见 [`docs/specs/ui-ux-overhaul/SPEC.md`](../specs/ui-ux-overhaul/SPEC.md)。
> 旧版（2026-09-16 按 `refactor/p0-stabilize` 写的逐文件阅读笔记，含 7 个一级页面、`WorkspaceContext`、前端状态推导）在 git 历史里：`git show 1ad7e03:docs/architecture/08-frontend.md`。

## 0. 总览

| 项 | 值 |
|---|---|
| 技术栈 | React 19、TypeScript、Vite 7、@tanstack/react-query 5、react-router-dom 7、vitest + Testing Library；无 UI 组件库，CSS Modules + `styles/tokens.css` |
| 读者 | 想快速读到英文内容中文版的人（SPEC §0 用户裁决）；审校类功能在书页的高级标签里 |
| 状态来源 | **后端**。书的处境（可读、翻译进度、后台导出、需要你处理的事、一句话状态、唯一主操作）来自 `GET /v1/documents/{id}/overview`；跨书的待处理和刚结束的翻译来自 `GET /v1/inbox`。前端不推导阶段、徽章或下一步（SPEC 决策 3、禁止事项 1） |
| 全局状态 | 没有全局 Provider。只有 React Query 单例 `app/queryClient.ts`；每个页面、标签自己取数 |
| 规模 | `src/` 约 7.2k 行（含测试、不含 `api-types.gen.ts`） |

## 1. 路由（`src/app/App.tsx`）

| 路径 | 组件 | 说明 |
|---|---|---|
| `/` | `features/home/HomePage` | 粘贴链接 / 上传；导入前检查服务商（`GET /providers/active`）；最近的书，每张卡片显示该书的一句话状态（`GET /overviews?ids=`） |
| `/books/:documentId` | `features/book/BookPage` | 书页：状态条（headline、主操作、进度、自动开始说明、「完成时通知我」）、「需要你处理」卡片（就地批准 / 继续）、开始面板（按费用上限）、标签栏；index 重定向到 `read`（可读）或 `progress` |
| `…/progress` | `book/ProgressTab` | 步骤（后端给的标签和状态）+ 最近动态（`/runs/{id}/events?milestones=true`，`lib/workflow.describeRunEvent` 转成句子） |
| `…/read` | `library/ReaderPage` | 阅读：中文网页 / 中文版 / 中英对照 / Markdown / PDF（PDF 只在 `GET /meta` 的 `capabilities.pdf_renderer` 为真时出现） |
| `…/terms` | `book/TermsTab` | 术语表（谁锁定的：快速阅读模式 / 规则 / 人；修改、解锁走 `PUT /documents/{id}/terms`）+ BOOK.md |
| `…/issues` | `issues/IssuesPage` | 问题清单与详情（高级） |
| `…/log` | `book/LogTab` | 代理回合与全部运行事件（给技术用户，高级） |
| `…/chapters` | `workspace/WorkspacePage` | 逐章审校台（高级）；数据由 `workspace/useChapterReview` 自己取 |
| `/inbox` | `features/inbox/InboxPage` | 跨书「需要你处理」，每项一个动作 |
| `/library` | `library/LibraryPage` | 全部书：搜索、筛选、分页、下载、删除 |
| `/settings` | `features/settings/SettingsPage` | 阅读偏好（自动开始上限、快速阅读模式、浏览器通知、API key）+ `providers/ProvidersPage` |
| `/runs`、`/deliverables`、`/issues` | 重定向 | 到最近打开那本书的 `progress` / `read` / `issues`（`lib/currentBook`）；没有则回 `/`；全部 `replace`，返回键不来回跳 |
| `/approvals`、`/providers`、`/library/:id/read`、`*` | 重定向 | 分别到 `/inbox`、`/settings`、`/books/:id/read`、`/` |

后端在 `BOOK_AGENT_FRONTEND_DIST_DIR` 配置时托管构建产物并做深链接回退（`app/ui/spa.py`），所以以上路径刷新都能打开。

## 2. 外壳（`components/AppLayout.tsx`）

- 导航三项：首页、需要你处理（带数量角标，折叠侧栏也可见）、设置。
- 每 30 秒轮询 `GET /inbox?since=<页面打开时间>`：数量用于角标、标签页标题「(n) 等你处理」和顶栏提示；`announce()`（`lib/notifications.ts`）据此发浏览器通知。
- 健康状态点（`GET /health`，30 秒）。

## 3. 数据与 API（`src/lib/`）

| 文件 | 内容 |
|---|---|
| `api.ts` | 手写 fetch 封装和类型；`requestJson`、API key 头、导出下载（缺格式时按需发起导出；书忙时收到 409 就等待重试，不报错） |
| `api-types.gen.ts` | 由 `scripts/generate_frontend_api_types.py` 从 OpenAPI 生成，勿手改 |
| `overview.ts` | `BookOverview`、`Inbox`、`getOverview`、`getOverviews`、`getInbox` 的类型与调用（overview/inbox 路由返回字典，类型在这里手写） |
| `autoStart.ts` | 导入后能否自动开始：预估最多扣费 ≤ 上限才开始；没有价格从不自动开始（SPEC 决策 6） |
| `notifications.ts` | 通知：`pickNotifications`（可读了 / 失败 / 需要你处理，按事件去重）、`announce`、`enableNotifications`（只在点击时请求权限） |
| `preferences.ts` | 按浏览器保存的偏好：快速阅读模式（默认开）、自动开始上限（默认 $1） |
| `currentBook.ts` | 最近打开的书，只供旧地址重定向使用 |
| `runEvents.ts` | 运行事件 SSE（Postgres 部署）：事件到达后去抖失效相关查询（含 `overview`、`inbox`、`terms`）；SQLite 返回 501 时退回轮询 |
| `workflow.ts` | 只剩格式化与文案：`formatDate`、`statusLabel`、`preferredTitle`、`describeRunEvent` 等；**不含状态推导** |

轮询节奏：书页的 overview 在翻译或导出进行中每 3 秒一次，其余时间不轮询（SSE 在线时由事件触发刷新）；首页卡片在有书进行中时 10 秒一次；收件箱 30 秒一次；逐章审校台的查询只在该标签打开时存在。

## 4. 用户能做的写操作

| 操作 | 位置 | 接口 |
|---|---|---|
| 导入网页 / 文件 | 首页 | `POST /documents/bootstrap-url`、`POST /documents/bootstrap-upload` |
| 开始 / 自动开始 | 书页 | `POST /runs`（`run_request.fast_reading`、`auto_started`）→ `POST /runs/{id}/resume` |
| 停止 / 继续 / 重试 | 书页、收件箱 | `POST /runs/{id}/pause|resume|retry` |
| 批准 / 拒绝 | 书页、收件箱 | `POST /approvals/{id}/approve|reject` |
| 改术语 / 解锁 | 术语与决策 | `PUT /documents/{id}/terms` |
| 问题分诊、结构编辑、记忆提案、执行修复 | 高级标签 | `/issues/*`、`/documents/{id}/structure-edits`、`/documents/{id}/chapters/*`、`/actions/{id}/execute` |
| 服务商增改、激活、测试 | 设置 | `/providers/*`（激活或更新后，后端自动恢复因 `provider.not_configured` 暂停的运行） |

## 5. 通知

- 触发：翻译完成且可读（点击打开阅读）、翻译失败（带原因）、需要你处理（审批、需要人处理的暂停）。每个事件只通知一次（`sessionStorage` 记住已发的键）。
- 权限：只在用户点击「完成时通知我」（书页进行中）、「有事等我处理时通知我」（收件箱）、「开启通知」（设置）时请求；从不在打开页面时弹窗。未开启时事件照样记为已见，不补发。
- 局限：依赖页面开着（任一标签页）；浏览器关闭后收不到（SPEC §12）。

## 6. 测试

- 单元 / 组件：`npx vitest run`（17 个文件）、`npx tsc --noEmit`。`src/test/setup.ts` 在每个测试后先卸载页面、取消进行中的查询再清缓存，避免上一条测试的响应落进下一条；`src/test/fixtures.ts` 提供 `makeOverview`。
- 中文文案：`src/test/chineseCopy.test.tsx` 扫描首页、书库、书页进度、收件箱的可见文本和 `aria-label` / `title` / `placeholder`，白名单外出现英文即失败。
- 主流程（真实浏览器）：`uv run python frontend/e2e/core_path.py [--priced] [--max-clicks N] [--max-leaves 0]`。它把前端构建到 `.test-tmp/` 下的临时目录，由一个临时 SQLite + echo 模型的 API 托管，抓取 `e2e/fixtures/article.html`，统计点击次数和离开书页的次数，并用页面内的假 `Notification` 断言「可以阅读了」通知已发出。不碰 8999/4173 上的服务，不产生费用。2026-09-20 实测：需确认 3 次点击、自动开始 2 次点击，均 0 次离开书页。

## 7. 构建与部署

- 开发：`vite` 在 4173，`/v1` 代理到 `VITE_API_PROXY_TARGET`（默认 8999）；`./book-agent start` 同时起前后端。
- 生产：多阶段 Dockerfile 构建 `frontend/dist`，API 进程在配置 `BOOK_AGENT_FRONTEND_DIST_DIR` 时托管（`/assets` 长缓存、深链接回退、`/runtime-config.js` 注入 API 前缀）。

## 8. 已知缺口

- 视觉系统（字号梯度、暗色模式、骨架屏、无障碍审计）是 SPEC 的 M3，尚未做。
- 书页标签在窄屏横向滚动，其余页面只保证 860px 以下可用。
- overview / inbox 路由没有 OpenAPI 响应模型，前端类型在 `lib/overview.ts` 手写，改后端字段时要同步。

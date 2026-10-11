# book-agent 交接文档

> 写于 2026-10-03，对应 `main` @ 8d8d93a（本文件提交前）。接手方：Codex。目的：接手后续的功能维护、架构升级与重构。
> 本文是**入口**：先读本文，再按需深入 §13 列出的专题文档。专题文档 `docs/architecture/00–09` 写于 2026-09-17～20，**此后的大量变化只记录在本文与 git 历史中**（见 §9.4）。

---

## 1. 产品是什么

把英文书（PDF / EPUB）和英文网页翻译成高保真中文版的产品。目标用户：**想快速读完英文内容中文版的普通读者**（不是译者、不是审校）。核心卖点：版式保真（图、表、代码、列表、侧栏、提示框）、术语一致、读起来像中文作者写的。

已由用户（产品负责人）确认、不要推翻的决策：

| 主题 | 决策 |
|---|---|
| 收费模式 | **预付额度**：充值，按实际 token 花费扣减，余额不足暂停。支付服务商**未选定**，不要自行选型。 |
| 试译 | 每本书先**免费试译前两章**（不是第一章，第一章常是导论），走完整流程（含质检与自动修复），把最终效果给用户看，用户自愿决定是否付费翻全书。「质量优先，不满意不要钱」。试译成本由我们承担，不扣用户额度（`billing_category='pilot'`）。 |
| 质量闭环 | 质检 agent 分层查问题 → 修复 agent 判断「这本书特有 / 系统性」→ 特有走本书专属路径，系统性给绕行 + 缺陷单走开发流程 → 直到无严重问题才放行。**运行中绝不改代码**（曾经的运行时自修复层已删除，原因是不可验证、不可回滚）。 |
| 持续进化 | 每本书的问题、分类、修复路径、效果要记录，让后来的书受益（经验账本 Q6，**未完成**，见 §9.2）。 |
| 译文纯净 | 译文里只有原文的译文，不出现「图片锚点保留」之类的程序提示。 |
| 提示框 | 书中的「图标 + 提示词」框：自然语言提示词**只保留纯中文**，不保留英文原文；代码结构的提示框按代码处理不翻译。 |
| 界面 | 以书为中心的信息架构；侧边栏为 首页 / **书库** / 需要你处理 / 设置。**书库 Tab 不要删**（曾被误删，用户明确要求保留）。 |
| 快速阅读模式 | 每次运行可开，术语锁定自动批准，UI 默认开。 |
| 自动开始 | 导入后若预估最高花费 ≤ 浏览器内上限（默认 $1，0 = 总是询问）则自动开始；没有单价时绝不自动开始。 |
| git 历史 | **完整保留**，不做 history scrub / filter-repo。 |
| 付费测试 | 允许用真实模型做**有界**的付费测试（一本书或几章，报告花费）；大规模或开放式付费评测先问用户。 |

## 2. 快速上手

### 2.1 环境与服务

- Python 3.13 + `uv`；前端 React + TypeScript + Vite（`frontend/`，npm）。
- 数据库：**仅 PostgreSQL**（dev/prod scope 拒绝 SQLite；SQLite 只用于单元测试）。本地用 Docker 容器 `book-agent-postgres`，端口 **55432**，库名 `book_agent`。
- 一键脚本 `./book-agent`（可 `./book-agent install` 链接到 PATH）：

```bash
./book-agent start [--reload]   # 起 PostgreSQL + 后端(8999) + 前端(4173)，启动时自动 alembic upgrade head
./book-agent stop [--keep-db]
./book-agent restart
./book-agent status
./book-agent logs [backend|frontend]   # 日志在 artifacts/server.log、artifacts/frontend.log
./book-agent <其它>              # 透传给 Python CLI（src/book_agent/cli.py）
```

- 访问：前端 http://localhost:4173 ，API http://localhost:8999 （文档 `/docs`）。
- **重启后端前先确认没有进行中的运行**：`select count(*) from document_runs where status in ('queued','running')`。
- Docker Desktop 可能没启动（机器重启后），先 `open -a Docker`，再 `docker start book-agent-postgres`。
- 不要随手 `docker build`：这台机器上曾导致 Docker daemon 崩溃。

### 2.2 配置

- `.env`（不入库，模板 `.env.example`）。所有设置在 `src/book_agent/core/config.py`，环境变量前缀 `BOOK_AGENT_`。
- `BOOK_AGENT_SECRET_KEY`：Fernet 密钥，加密数据库里存的服务商 API key。**丢失则已存的 key 无法解密。**
- 模型服务商**以数据库为准**（表 `provider_credentials`，界面「设置 → 服务商」，API `/v1/providers`）；`.env` 只是首次启动时的种子。当前启用：DeepSeek，`deepseek-v4-flash`，base_url `https://api.deepseek.com`，`request_overrides={"thinking":{"type":"disabled"}}`（**必须关思考模式**，否则输出额度被思考耗尽）。
- 单价（每百万 token，美元）：输入 0.15、缓存命中 0.003、输出 0.60；`peak_price_multiplier=2`，`off_peak_window_utc="16:30-00:30"`（北京时间 00:30–08:30 为优惠时段）。每次调用按完成时刻计价（`workers/providers/pricing.py`）。优惠时段时间窗是按 DeepSeek 以往公告设的，用户给的价目表没写时段，若有变化只改数据库。
- 其它常用开关：`translation_polish_enabled`（润色二道，默认开）、`translation_prompt_profile`（默认 `tech-column-meta-v1`）、`run_executor_enabled`、`auth_mode`（本地 disabled）、`billing_price_multiplier`。

### 2.3 第一次跑通

前端首页上传 PDF / 粘贴网页链接 → 书页「免费试译前两章」→ 进度页看各阶段 → 「阅读试译」→「翻译全书」。无成本的端到端检查：`uv run python frontend/e2e/core_path.py`（echo 模型，测点击路径与通知）。

## 3. 代码地图

```
src/book_agent/
├── app/            FastAPI 组装、路由(app/api/routes/*)、后台执行器 runtime
├── application/    读模型与用例：book_overview.py（书页唯一状态源）、export_use_case、issue_*、worklist
├── cli.py          CLI：bootstrap/translate/review/export/eval/… 以及 reparse-untranslated、heal-alignment、reaudit-pilot-exports
├── core/           config、logging、ids、run_context
├── domain/         ORM 模型(domain/models)、枚举、结构(domain/structure: pdf.py 恢复管线、parse_health、callouts)、术语(domain/terminology)、质量规则(domain/quality: pilot_qa.py、pilot_repair.py、zh_text_fixes.py)
├── export/         渲染：markup.py（块→HTML）、render_repair.py、titles.py、reader_epub、templates/*.css、pdf_crop
├── harness/        agent 内核：kernel(turn/budget/model/trace)、tools(registry/permissions/hooks/book_tools)、approvals、agents(terminology/reviewer/repair/structure/export_review/pilot_repair)
├── infra/          DB 会话、repositories
├── ingestion/      pdf/（extract、classify、chapters、figure_extent、hyphenation、identifiers、ocr…）、web.py（网页）、text.py
├── mcp/            MCP server（docs/agent-upgrade/06-mcp-server.md）
├── orchestrator/   stage_status（PIPELINE_STAGES）、门禁、规则引擎
├── schemas/        Pydantic API schema
├── services/       业务服务（见 §4）
├── translation/    contracts、prompt_profiles、polish.py（润色二道）
└── workers/        LLM 客户端（providers/openai_compatible.py、pricing.py）、translator、factory、llm_calls（每次调用记事件与成本）
frontend/src/features/  home、library、book（书页各 Tab、PilotFlow、PilotQualityPanel、ParseHealthPanel）、inbox、issues、settings、providers、workspace（逐章审校）
alembic/versions/   55 个迁移，最新 20260930_0055_book_quality_scope
skills/structure/   PDF 恢复技能（manning-listings 等）
tests/              181 个测试文件（unittest 风格，pytest 运行）
tests/corpus/       真实书样本库清单（PDF 不入库，见 §7.3）
```

## 4. 端到端流程

```
导入(bootstrap) → 解析(PDF/EPUB/web) → G1 解析体检(parse_health, 免费)
   → 试译 run(translate_full, scope=pilot, 前两章)：
        terminology → translate(+polish) → model_review → review → [repair] → bilingual_html → merged_html → [export_review]
        → pilot_qa(分层质检) → pilot_repair(修复循环 + 重新导出 + 终检)
   → 用户看试译 → 「翻译全书」(translate_full 全书，复用试译的包)：
        [自动重解析未翻译章节] → … 同上 … → book_qa → book_repair
```

- 运行的阶段列表：`orchestrator/stage_status.py::PIPELINE_STAGES`。执行器为 PostgreSQL 账本 + 后台线程，按章节并行（每章一个包在途，最多 8 章并行）。
- 待审批与「需要你处理」：`approvals` 表；前端 `/inbox` 每 30 s 轮询驱动浏览器通知。
- 书页所有状态只来自 `GET /v1/documents/{id}/overview`（`application/book_overview.py`）。**前端不要自己推导阶段状态**（这是一次大重构的成果）。

### 4.1 关键服务（`src/book_agent/services/`）

| 文件 | 职责 |
|---|---|
| bootstrap.py | 导入；文档 id 由文件内容 sha256 派生（同一 PDF 不能重复导入，须先删）；记录 `parser_fingerprint` |
| parse_health.py | G1 解析体检（10+ 项检查），表 `parse_health_reports`，`GET/POST /documents/{id}/parse-health` |
| translation.py | 包翻译、输出校验与同轮修复、润色二道（`_polish`，失败或校验不过则保留初稿，`output_json["polish"]` 存审计与初稿） |
| pilot.py | 试译：选前两章（跳过前言/Part 页）、`POST /documents/{id}/pilot`、`whole_book_request`（全书复用试译包） |
| pilot_quality.py / book_quality.py | 分层质检服务（scope=pilot / book），报告表 `pilot_quality_reports`（带 scope） |
| pilot_repair.py / pilot_repair_tools.py | 修复循环与工具箱（§5） |
| chapter_reparse.py | 用当前解析器重解析**未翻译**章节（parse revision fork，可撤销）；全书运行开始前若 parser 指纹变化会自动执行 |
| structure_edits.py | 结构修改（改类型、合并/拆分、改 metadata、merge_callout…），记录可重放 |
| segment_edit.py | 确定性改写译文（rewrite_packet、fill_sentences 翻译记忆） |
| parse_revision_fork.py | 解析版本分叉：结构变化时只让受影响的包重译 |
| review.py | 规则审校（含锁定术语冲突、版式风险策略 `_pdf_layout_review_policy`） |
| export.py / export_qa.py | 导出与确定性导出 QA（试译只审试译章节） |
| cost_estimate.py | 费用估算（`GET /documents/{id}/cost-estimate`） |
| prepaid_credit.py / usage_statement.py / org_budget.py | 预付额度、账单、组织预算 |
| provider_credentials.py | 服务商凭据、单价、峰谷价 |

## 5. 试译质量关卡（本项目最近两周的重心）

设计文档：`docs/agent-upgrade/08-pilot-quality-gate.md`（G1–G5、Q1–Q6）。实现状态：

| 阶段 | 状态 |
|---|---|
| Q1 解析体检 | 完成 |
| Q2 真实书样本库 | 完成（`tests/corpus/manifest.json`、`tests/test_parse_health_corpus.py`） |
| Q3 试译运行 | 完成（免费、复用、付费翻全书入口） |
| Q4 分层质检 L1–L7 | 完成，已扩展到全书（book_qa） |
| Q5 修复 agent | 完成，已扩展到全书（book_repair） |
| Q6 经验账本 | **未做**：分类器的 `LessonLedger` 协议目前是 `NoLedger` |

### 5.1 分层质检（`domain/quality/pilot_qa.py`，纯函数）

- 层：L1 结构、L2 图表、L3 表格、L4 代码与格式、L5 术语、L6 忠实度、L7 排版与纯净。严重度：blocking / major / minor。
- 每条 finding：`check_id`、layer、severity、location（chapter/packet/block/sentence/page）、evidence、`target`、`repair` 提示（如 `structure_edit.relabel_as_heading`）、稳定 `key`。`CHECKS` 里有全部检查（60+ 项）。
- 旧审校系统里试译章节的阻断级 review issue 会被并入质检（`check_review_blockers`），**质检是「有没有严重问题」的唯一判据**。
- 可插拔：视觉/模型检查可作为 `extra_checks` 接入（目前未接视觉）。

### 5.2 修复循环（`services/pilot_repair.py`）

分类（§7.1 规则 1–4；规则 1–2 需要账本）→ 选首个未试路径 → 在 DB savepoint 里执行、只重译受影响包 → 整体重跑质检 → **目标问题消失且没有 blocking/major 变多才保留**，否则回滚试下一条。边界：3 轮、预算 = 2×max(实际花费, 两章预估)（全书：书预估的一定比例，上限 15%）、时间与次数上限。翻译自身输出导致的回退会先重试一次。修不掉的升级为 `approvals`（kind `pilot_question`，一个问题类一个中文是/否问题；**目前回答是/否不触发任何动作**）。系统性问题建 `pilot_defect_tickets`（按问题类+文档+原因去重），管理端 `GET/PATCH /pilot-defects`。每次尝试记录在 `pilot_repair_attempts`（Q6 的数据来源）。

工具箱（`REPAIR_PATHS`）：resplit_chapters、structure_edit.*（relabel / split_caption / split_run_in_heading / hide_running_header / hide_duplicated_label / hide_figure_residue / relabel_as_heading / relabel_table_as_code / merge_icon_callout / nest_list_items / relabel_as_sidebar / join_continuation / link_listing_caption …）、figure_region_override、glossary.*、retranslate_packets、segment_edit.*、review_recheck.rerun_detectors，以及一个只在规则无法定位时才用的结构修复 agent（`harness/agents/pilot_repair.py`）。

API：`/documents/{id}/pilot-quality`、`/pilot-repair`、`/book-quality`、`/book-repair`（GET 最新，POST 重跑，`budget_usd` 可选）。

## 6. 其它子系统要点

- **PDF 解析**（最复杂、最常出问题）：PyMuPDF 抽取 → `domain/structure/pdf.py` 二十多个恢复 pass。书签优先的章节切分、图片区域（`figure_extent.py`、`figure_clustering`）、断行连字符/标识符按**本书自身证据**恢复、Manning 风格标题/侧栏/代码清单/提示框（`domain/structure/callouts.py`）、列表层级、跨页/跨图段落合并。每本新 PDF 都可能暴露新问题——这正是质量关卡存在的原因。
- **EPUB**：结构较稳；`services/epub_structure_refresh.py`。
- **网页**：`ingestion/web.py`，Playwright 渲染，`web_html` 阶段输出保留原布局的中文页。
- **翻译**：包（packet）为单位；prompt profile 在 `translation/prompt_profiles.py`（`CHINESE_EXPRESSION_RULES` 吸收了 humanizer-zh 的理念，MIT）；润色二道 `translation/polish.py` 经 48 段盲评验证后上线（偏好 20:10，忠实度持平），禁止的改写写在其 system prompt 里。
- **术语**：Terminology Agent（试译与全书第一阶段，抽样）；锁定术语匹配 `domain/terminology/matching.py` / `enforcement.py`；不把代码标识符、通用动词当术语。
- **导出**：bilingual_html（逐章中英）、merged_html（中文阅读版，`artifacts/exports/<doc>/merged-document.html`）、EPUB、Markdown。读者版不带任何程序注记（`include_notice=False`）。标题有层级样式；侧栏为 `<aside class='book-sidebar'>`（注意 `.sidebar` 已被阅读版导航占用）。
- **发布到博客**（`services/blog_publish.py`、`export/blog_metadata.py`、`app/api/routes/blog.py`）：书库下载菜单「发布到博客 · 中文版 / 中英对照版」→ `POST /v1/documents/{id}/blog-publish`。用 `.env` 里的 `BOOK_AGENT_BLOG_API_URL / _WEB_URL / _EMAIL / _PASSWORD` 登录 `~/project/blog`，逐张上传 Markdown 引用的图片并替换链接，再以**草稿**新建文章；标题、摘要、SEO 按规则生成（不调模型）。书的封面（`services/book_cover.py`：PDF 第一页 / EPUB 声明的封面图）上传到博客封面库的「图书封面」分类并设为文章封面。同一版本再发布只更新原文章的正文（先 GET 文章，正文没变就什么都不发；标题、SEO、发布状态和在博客上换过的封面都不动；文章没有封面时才补上）；文章在博客上被删了就新建。文章 ID、已上传图片和封面的哈希记在 `documents.metadata_json.blog_publish`，发布中途失败时已上传的也记下，重试不重传。博客端代码未改动，只用它现有的登录、`/api/upload/image`、`/api/cover/upload`、`/api/articles` 接口。
- **前端**：书页 Tab：阅读 / 进度 / 问题 / 术语与决策 / 运行记录 / 逐章审校（高级）。试译卡片合并为一个摘要（结论、各层 chip、自动修复了什么、还要你看什么）。
- **计费**：每次 LLM 调用写 `llm.call.completed` 事件含 `cost_usd`；试译归我们，全书扣预付额度。

## 7. 测试与验证

### 7.1 运行测试

```bash
scripts/run_tests_per_file.sh                          # 全量：每个文件一个进程（全量单进程曾段错误）
uv run --extra pdf python -m pytest -q -p no:warnings tests/test_xxx.py   # 单文件
BOOK_AGENT_RUN_PG_TESTS=1 uv run --extra pdf python -m pytest -q tests/test_postgres_schema_drift.py
cd frontend && npx tsc --noEmit -p . && npx vitest run
uv run ruff check src/ tests/
```

- **务必带 `--extra pdf`**，否则 `uv run` 重新同步环境会卸掉 playwright。
- macOS 没有 `timeout` 命令。机器负载高时 `test_api_workflow` 的执行器测试会超时，单独重跑再判断。
- **SQLite 会掩盖 PostgreSQL 才有的问题**（枚举 CHECK 约束、坏 UUID 后事务中止）。新增枚举值、改 agent 工具、加迁移时必须跑 PG 测试（`docs/architecture/09-testing-tooling.md` 有不碰开发库的 docker 配方）。
- A/B 对比用 `git worktree add` 到 `/tmp` **以外**的目录（`/tmp` 下会触发导出临时目录的 CHECK 约束）。

### 7.2 金样（golden）

`test_export_golden`、`test_golden_pdf_regression`、`test_pdf_structure_golden`、`test_workflow_golden`、`test_translation_prompt_golden`。只有**有意**改变输出时才用 `BOOK_AGENT_UPDATE_GOLDEN=1` 重新生成，并在提交说明里解释每处变化。

### 7.3 真实书样本库

- PDF 在用户外置硬盘 `/Volumes/touch`（**常常没挂载**；没挂载时语料测试会 skip，重解析会干净地拒绝）：
  - RAG：`/Volumes/touch/zlibrary/new20260325/RAG/A Simple Guide to Retrieval Augmented Generation (Abhinav Kimothi) (z-library.sk, 1lib.sk, z-lib.sk).pdf`
  - Agent：`/Volumes/touch/Build an AI Agent Agents that reason, plan, and act autonomously (From Scratch) (Jungjun Hur, Younghee Song) (z-library.sk, 1lib.sk, z-lib.sk).pdf`
- `BOOK_AGENT_RUN_CORPUS_TESTS=1 uv run --extra pdf python -m pytest -q tests/test_parse_health_corpus.py`。**任何解析代码改动都必须跑**，并跑 `test_pdf_support`、`test_pdf_book_structure`、`test_pdf_figure_regions`、`test_figure_clustering`、`test_icon_callouts`、`test_pdf_reading_structure` 和金样。
- **PDF/EPUB 有版权，永远不要提交进仓库。**

### 7.4 「通过了」不等于「好了」

质检全绿时，阅读版仍然可能有读者一眼就看出的问题（曾出现：全部小节标题被排成正文、图里的文字漏成乱码表格）。交付或宣布完成前：用 Playwright 打开 `merged-document.html`，在多个位置截图**亲眼看**，并统计 `<section class='block TYPE'>`（像标题的段落、短代码块、稀疏表格、坏图）。每发现一类肉眼问题，就同时加一条质检规则，不只修一次。

## 8. 工作约定

- 在分支上开发（不要直接提交 main），每个独立完整的步骤一个提交，提交说明写真实意图。
- `git add` 显式路径，**不要 `git add -A` / `git add .`**（工作区常有无关的 `artifacts/` 变动）。**不要用 `git stash`**（曾有改动卡在 stash 里），A/B 用 worktree。
- 远程 `origin` = https://github.com/1055373165/DLERHY.git（私有）。**main 领先 origin 215+ 个提交、尚未推送**；推送前先问用户。
- 用户可见文案用中文；代码、标识符、提交说明用英文。
- 运行中不改代码；系统性缺陷走「分支修复 + 样本库回归 + 用户批准合并」。
- 开发库（`book_agent`）里是用户自己的书。做实验先复制：`docker exec book-agent-postgres psql -U postgres -c "CREATE DATABASE book_agent_x TEMPLATE book_agent"`，用完删掉。删除用户的文档、数据库前要征得同意。2026-09-25 的整库备份在 `artifacts/backups/book_agent-before-reimport-20260925.dump`。

## 9. 当前状态与未完成工作

### 9.1 开发库里的书

| 文档 | id | 状态 |
|---|---|---|
| A Simple Guide to Retrieval Augmented Generation（检索增强生成简明指南） | a5373e9a-3552-5935-ae13-8f589e5097a1 | 前两章试译完成，质检通过（剩 1 项：第 2 章「小结」标题；修复已合并，**需在开发库重跑** `POST /v1/documents/a5373e9a-…/pilot-repair` 才生效） |
| Build an AI Agent (From Scratch)（从零开始构建 AI 智能体） | ae18bd2e-2caa-59f8-a21e-776f34a4e7b0 | **全书已翻译**（2026-10-08，run dd157986），全书质检 + 修复完成，剩 4 个阻断：3 个「阅读顺序」标记（p.143 代码旁注、p.292 图文并排、索引多栏；人工核对过前两处无误，已作为问题交给用户）、表 5.2（p.150）没能重建。原 PDF 现在在 `~/Downloads/ai 书籍/`（`documents.source_path` 已改） |
| 其它（RSI 书、Distributed systems EPUB、若干 Anthropic 网页） | — | 已完成，可读 |

### 9.2 待办（按优先级）

1. **Agent 书收尾**（全书已于 2026-10-08 翻完）：表 5.2 的行重建；`pilot_question` 的回答要能触发动作（目前三个「阅读顺序」问题回答了也没有效果）；`L1.paragraph_split_midsentence` 的 `join_continuation` 不能把列表项接到段落上（2 处）；p.324 页眉隐藏失败。跨页的代码清单会显示成两个框，可考虑合并。
2. **已知解析缺陷**（都在质检里能看到）：
   - RAG p198 图中标注被读成表格，挡住了段落合并。
   - Agent p70 代码被读成公式，其「代码清单」标题挂不上。
   - 页眉仍被读成正文：RAG p17、Agent p16 和 p272。
   - RAG p55 侧栏里的代码（`%pip install`）粘在正文上。
   - 约 9 处 `x_ y` 标识符断开，因为书里没有证据，没有合并。
   - 矢量绘制的提示框图标（O'Reilly 风格）识别不到。
3. **Q6 经验账本**：按出版社/生产工具查高发问题；同类「特有问题」在 ≥2 本书出现自动升级为系统性候选；跟踪进化指标（试译一次通过率、每本书阻断数、修复轮数）。数据源是 `pilot_repair_attempts` 和 `pilot_defect_tickets`。
4. **升级问题的回答要能触发动作**：`pilot_question` 的 是/否 目前只记录。
5. **`parser_fingerprint` 太敏感**：解析器源码任何改动都会让未翻译章节在下次全书运行前重解析；未变化的章节会被跳过，但会增加运行前的耗时。
6. **生产化缺口**（`docs/production/gaps-and-risks.md`、`paid-readiness.md`）：支付服务商未选；Docker 镜像没有 Chromium（生产环境的网页翻译与 PDF 截图受影响）；多实例、可观测性。
7. **文档过期**：`docs/architecture/00–09` 停在 2026-09-20 前，需按 §5–6 的现状更新（尤其 03 解析、05 审校修复、06 导出、07 数据模型、08 前端）。
8. 小项：中英文混排空格还有少量不一致；单独预览某几章时会跳过术语阶段；`active_target_texts` 已修复，但旧数据要用 `heal-alignment` 清理。

### 9.3 全书翻译的操作步骤（以 Agent 书为例，已执行过）

2026-10-08 实际执行时遇到并已修复的问题（都在 main 上，供排查参考）：DeepSeek 连接池卡死导致整轮失败（现在会换连接池、并改为暂停而非失败）；规则审校把多栏版式当阻断让全书运行失败（现在交给 book_qa）；Agent 回合因连接重置被判阶段失败（现在等重试）；`heal-alignment` 把同一长块拆出的窗口包当重复作废，导致 32 句漏译（已修，数据已恢复）；全书质检的四类误报；表格行合并、编号列表双标记、章标题格式、提示框换行、英文残留。

```bash
# 0. 挂载 /Volumes/touch（重解析需要原 PDF）；确认没有进行中的运行；./book-agent restart（会迁移到 0055）
# 1. 用当前解析器刷新未翻译的第 3–10 章（先演练）
uv run --extra pdf python -m book_agent.cli reparse-untranslated --document-id ae18bd2e-2caa-59f8-a21e-776f34a4e7b0 --dry-run
uv run --extra pdf python -m book_agent.cli reparse-untranslated --document-id ae18bd2e-2caa-59f8-a21e-776f34a4e7b0
#    （不做这一步，全书运行也会自动执行；结果记在 run 的 status_detail_json.chapter_reparse）
# 2. 合并第 2 章提示框遗留的重复包
uv run --extra pdf python -m book_agent.cli heal-alignment --document-id ae18bd2e-2caa-59f8-a21e-776f34a4e7b0
# 3. 开始全书（与 UI「翻译全书」按钮等价）
curl -X POST localhost:8999/v1/runs -H 'Content-Type: application/json' \
  -d '{"document_id":"ae18bd2e-2caa-59f8-a21e-776f34a4e7b0","run_type":"translate_full","requested_by":"smy","status_detail_json":{"run_request":{"fast_reading":"on"}}}'
curl -X POST localhost:8999/v1/runs/<run_id>/resume -H 'Content-Type: application/json' -d '{"actor_id":"smy"}'
# 4. 跟进：GET /v1/runs/<run_id> 或书页「进度」；最后自动 book_qa → book_repair
curl localhost:8999/v1/documents/ae18bd2e-2caa-59f8-a21e-776f34a4e7b0/book-quality
curl localhost:8999/v1/documents/ae18bd2e-2caa-59f8-a21e-776f34a4e7b0/book-repair
# 5. 按 §7.4 亲自检查 artifacts/exports/ae18bd2e-…/merged-document.html
```

刚刷新完时，book_qa 会把前言、目录、索引报为 `L1.export_chapter_missing`，因为那时全书导出还不存在；全书导出完成后这些应该消失，要复查。

### 9.4 文档未覆盖的近期变化（2026-09-20 之后，按时间）

网页翻译 → 润色二道 → PDF 解析两轮修复（章节、项目符号、代码注释、连字符、前言、图片裁剪）→ Q1 体检 → Q2 样本库 → Q3 免费试译 → 峰谷价 → Q4 分层质检 → Q5 修复循环（对齐修复、试译导出 QA 只审试译章节、阻断级 review 并入质检）→ 标题层级、图中文字残留 → 提示框（callout）→ 全书质检/修复、未翻译章节重解析、列表/侧栏/跨页段落/代码清单标题/标识符修复。每一步都有对应的合并提交（`git log --merges --since=2026-09-20`），提交说明写了原因和验证方式。

## 10. 架构升级与重构建议（给接手方）

1. **先补文档，再动大手术**：先读本文和 `08-pilot-quality-gate.md`，再按现状更新 `docs/architecture`，避免照着过期的文档重构。
2. **`domain/structure/pdf.py` 与 `ingestion/pdf/*` 是最大的技术债**：恢复 pass 多，并且彼此有顺序依赖。建议：
   - 把 pass 显式注册成有输入/输出契约的管线，每个 pass 一个模块、带各自的 fixture；
   - 书籍或出版社特有的规则下沉到 `skills/structure/*`；
   - 每次改动用样本库加金样守住。
3. **质检规则是修复的唯一契约**：新增修复路径时，先写会失败的质检规则，再写工具；工具的 `target` / `evidence` 字段名与规则共享常量。
4. **Q6 经验账本**：实现 `LessonLedger`（目前用的是 `NoLedger`），接入分类规则 1–2，并在导入阶段做「按出版社预警」。
5. **视觉质检**：L2/L3 目前只有规则。可以接入 Export QA agent 的截图能力，作为 `extra_checks`。
6. **`services/` 偏胖**：`export.py`、`review.py`、`pilot_repair_tools.py` 都超过千行。按职责拆分，与 `application/` 的读模型边界保持一致。
7. **全量测试耗时、对负载敏感**：考虑给 PG 测试做 fixture 化、并行化，并在 CI 里加上 PG 与语料（可选）两条流水线。

## 11. 常见坑

- 合并后不重启后端，跑的还是旧代码（出现过章节标题缺失）。
- `uv run` 不带 `--extra pdf` 会卸掉 playwright，网页翻译和截图随之失效。
- 外置盘没挂载：重解析和语料测试会失败或 skip；要先确认 `/Volumes/touch` 存在。
- 同一 PDF 不能重复导入：文档 id 由文件内容派生，要先删旧文档，而删除要征得用户同意。
- 思考模式没关：输出额度被思考耗尽，运行会暂停。
- `.sidebar` 这个 CSS 类已被阅读版导航占用，不要复用。
- 前端不要自己推导运行或阶段状态，一律读 overview API。
- 曾用 Claude Code 子代理并行开发，`.claude/worktrees/` 下留有旧的 worktree 和分支，都已合并进 main，可以清理（`git worktree list`）。

## 12. 关键表（新增部分）

`parse_health_reports`、`document_runs.billing_category`、`pilot_quality_reports(scope)`、`pilot_repairs(scope)`、`pilot_repair_attempts`、`pilot_defect_tickets`、`structure_edits`（含 `merge_callout`）、`provider_credentials`（含 `peak_price_multiplier`、`off_peak_window_utc`）、`approvals`（含 `pilot_question`）、`export_versions`、`document_parse_revisions`。完整的表说明见 `docs/architecture/07-data-model.md`（截至 0040 左右），之后的变化见各迁移文件头部的说明。

## 13. 文档索引

| 文档 | 用途 |
|---|---|
| `README.md` | 安装、API、CLI 概览 |
| `docs/README.md` | 设计文档索引 |
| `docs/agent-upgrade/08-pilot-quality-gate.md` | 试译关卡设计（当前主线） |
| `docs/agent-upgrade/00–07` | agent 化愿景、目标架构、H0–H4 路线、解析分叉、审校修复 agent、MCP、付费评测 |
| `docs/architecture/00–09` | 各子系统源码级说明（**截至 2026-09-20**） |
| `docs/production/*` | 风险登记簿、付费就绪清单 |
| `docs/specs/ui-ux-overhaul/SPEC.md` | 界面重构规格与证据 |
| `docs/refactor/PLAN.md` | 早期 P0–P4 重构记录 |
| `docs/user-guide.md` | 面向用户的操作说明 |

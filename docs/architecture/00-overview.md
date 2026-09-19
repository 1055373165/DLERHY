# 00 · 系统总览：book-agent 是什么、怎么工作、哪里脆弱

> 基线：分支 `refactor/p0-stabilize`，HEAD `2176d51`（2026-09-16）。本篇是 9 份子系统笔记（01–09）的综合，只保留跨子系统的结论；细节、行号与完整问题清单在各分篇。

## 1. 一句话定位

book-agent 是一个 **以 PostgreSQL 为唯一真相源的长文档翻译流水线**：EPUB / PDF 进，中文阅读稿（HTML / Markdown / EPUB / PDF）与双语审校包出。LLM 只在两个位置被调用——翻译一个 packet、按 JSON schema 生成一个结构化对象——其余一切（解析、分段、分包、调度、审校、修复、导出、门禁）都是确定性代码。

它 **不是**当前意义上的 AI agent：没有工具调用循环，没有模型自主规划，没有模型选择动作。名字里的 agent 是产品愿景，不是架构事实（详见 [agent-upgrade/00-vision.md](../agent-upgrade/00-vision.md)）。

## 2. 端到端数据流

```
上传 (.epub/.pdf)
  └─ Ingest      sha256 → documents（id = 内容哈希）
  └─ Parse       EPUBParser | PDFParser(extract→classify→22 个 recovery pass→chapters) | OcrPdfParser
                 → ParsedDocument（chapter/block IR，metadata 自由键）→ canonical IR sidecar
  └─ Segment     EnglishSentenceSegmenter → sentences（仅英文规则）
  └─ Packetize   ContextPacketBuilder：≤3 段落/≤900 字符/≤6 句合并为 packet；prev/next 各 ≤2 块
                 + BookProfile / ChapterBrief / ChapterTranslationMemory(seed) 快照
  └─ Run         POST /runs 或 /documents/{id}/translate|review|export → document_runs
       └─ DocumentRunExecutor（同进程 daemon 线程：supervisor → run loop → work 线程 + heartbeat）
            ├─ translate  每章一个活动 packet（frontier），≤8 并行，租约 120s
            │      prepare(事务) → LLM(无连接) → persist(事务, assert_lease_held) → complete(事务)
            ├─ review     16 项规则检查 → review_issues / issue_actions（规则引擎映射）
            │      translate_full 还会跑「自动跟进 + 阻断修复」两个有界循环（会重译）
            └─ export     每种 ExportType 一个阶段；门禁 = 章状态 + 对齐 + PDF 布局校验
                          render blocks 组装 → 16 类后处理修补 → HTML/MD/EPUB/PDF 写盘 + CAS stamp
  └─ Download   GET …/exports/download 只读已有导出；404 时前端入队 export run 再轮询
```

四个横向层：**Client**（React SPA，6 路 2.5s 轮询）→ **API / 控制面**（FastAPI，无鉴权）→ **执行器与服务**（同进程线程）→ **状态**（PostgreSQL 32 张表 + 本地文件 artifacts/）。外部依赖只有 OpenAI 兼容的 LLM 端点（可选：Playwright、Surya OCR、TATR）。

## 3. 模块地图（`src/book_agent`，约 6 万行）

| 包 | 职责 | 规模 / 备注 | 分篇 |
|---|---|---|---|
| `app/main.py`, `app/api/**`, `schemas/**`, `core/config.py`, `cli.py` | 组装、路由、配置、CLI | 路由约 30 个；`Settings` 30+ 字段 | 01 |
| `app/runtime/document_run_executor.py`, `services/run_execution.py`, `services/run_control.py`, `orchestrator/**` | run 状态机、工作项、租约、阶段推导、门禁、预算 | 执行器 1800 行，所有参数硬编码 | 02 |
| `ingestion/**`, `domain/structure/**`, `domain/segmentation`, `services/bootstrap.py`, `services/*_extractor.py`, `services/*_structure_refresh.py` | 文件 → IR → 句子 | `pdf.py` 4.5k + `classify.py` 2.3k，纯英文启发式 | 03 |
| `domain/context/builders.py`, `services/context_compile.py`, `services/translation.py`, `translation/**`, `workers/**`, `services/memory_service.py`, `services/glossary_*`, `services/term_*` | packet、上下文、prompt、provider、记忆、术语 | provider 用 urllib；13 个 prompt profile | 04 |
| `services/review.py`, `application/**`, `orchestrator/rule_engine.py`, `services/rerun.py`, `services/actions.py`, `services/workflows.py` | 审校、issue→action、修复循环、读模型 | review 零 LLM；`application/` 无直接测试 | 05 |
| `services/export.py`, `export/**`, `application/export_use_case.py`, `app/api/export_downloads.py`, `services/layout_validate.py` | 7 种导出、门禁、资产、下载 | `export.py` 3.2k + `render_repair.py` 1.8k | 06 |
| `domain/models/**`, `infra/**`, `alembic/**` | ORM、仓储、迁移、blob | 32 表、34 迁移、无 relationship() | 07 |
| `frontend/src/**` | 4 页 SPA | 8k 行，无生产托管路径 | 08 |
| `tests/**`, `scripts/**` | 1100 用例、39 脚本 | 无 conftest、无 CI | 09 |

## 4. 关键机制与不变量（值得保留的设计资产）

1. **状态即账本**：`translation_packets.status` + `work_items.status` 是阶段状态的唯一真相；`document_runs.status_detail_json` 只是投影缓存，读 API 时被派生值覆盖。进程可随时被杀，靠租约过期回收。
2. **CAS 与行锁**：work item 的每次状态迁移都是 `WHERE status IN (...)` 的 CAS UPDATE；改 run 行先 `get_run_for_update`；worker 在提交译文的事务末尾 `assert_lease_held`，租约丢失则整包回滚。
3. **确定性 ID**：`stable_id(uuid5)` 贯穿 document/chapter/block/sentence/packet/run/segment/issue/action/export，重放即 upsert（代价是 exports/issues 没有历史，见 §6）。
4. **三段事务翻译**：LLM 调用期间不持有数据库连接（有测试断言 checked-out == 0）。
5. **失败分类**：`classify_failure` 按异常类型给 RETRY / PAUSE / FAIL；402/401/403 暂停整个 run。
6. **规则引擎**：issue 类型 → 12 个 `ActionType` 的纯函数映射；三个自动修复循环都有硬上限与「人工保留」阈值，确无黑盒自修正。
7. **Golden 护栏**：workflow / export / PDF 结构（56 个夹具）/ prompt（13 profile × 2 布局）四套字节级快照，是重构的安全网。
8. **上下文编译**：packet prompt = 契约 + 章节简介 + 术语 + 章节概念记忆 + 前文译文 + 前后源文 + 句子账本（S1..Sn 别名），输出 JSON 含逐句对齐。

## 5. 当前能力边界（README 承诺 vs 源码事实）

| 承诺 | 事实 |
|---|---|
| 「one required env var」 | Docker 下 `.env` 的 `OPENAI_API_KEY` 永远读不到（Settings 故意忽略进程环境）；须在 UI 手工建 provider |
| 「暂停后从断点继续」 | 402/401/403 触发的暂停把 work item 写成 TERMINAL_FAILED，resume 后 run 直接 FAILED；只能 retry 冷启动 |
| 「术语一致」 | bootstrap 术语表为空；首轮翻译章节记忆为空；8 章并行无跨章同步；一致性只能事后靠 `term-consistency` CLI（PREFERRED，不阻断） |
| 「12 个修复动作」 | 当前检测器实际可达 6 个；`MANUAL_FINALIZE`、`WONTFIX`、`Detector.MODEL/HUMAN` 无写入方 |
| 「SSE 实时」 | 后端有 SSE，前端未用；27 种事件只发 8 种，run 生命周期不进事件总线 |
| 「成本仪表盘」 | 只统计成功的翻译调用；概念解析 / 术语抽取 / review 期重译不计费、不受预算约束；物化视图首刷疑似报错 |
| 「7 种导出」 | zh_epub / rebuilt_epub / rebuilt_pdf 无前端下载入口；rebuilt_pdf 依赖 CDN KaTeX 与 Playwright |
| 「扫描件 OCR」 | 整本走 `uv run surya` 子进程；图页 >10% 的普通书被判 mixed 并丢弃文本层 |
| 「运行在笔记本」 | 成立；但多实例部署无 leader、无 SKIP LOCKED、凭据缓存进程内 |

## 6. 跨子系统的结构性问题（决定优化优先级）

这些问题不属于单一模块，是设计层面的债：

1. **ID / 锚点不可演进**（03, 07）：PDF 锚点是全书级 reading-order 计数，block/chapter id 混入 ordinal；任何启发式改动都让重解析后 refresh 失配、旧行残留、句子从不重建。这是「解析质量迭代」与「数据稳定」之间的根本冲突，也是 PLAN 里单独立项的原因。
2. **JSON 列无契约**（07）：`source_span_json` 上百个自由键横跨解析/修复/导出；`evidence_json` 字符串键横跨 6 个模块；`status_detail_json` 是读改写热点。没有 schema，只能靠 golden 察觉变化。
3. **确定性 ID + merge = 没有历史**（05, 06, 07）：issue 重开会重置 `created_at`、残留 `resolution_note`、把 action 状态刷回 PLANNED，把人工 WONTFIX 翻回 OPEN；exports 每 (文档,章,类型) 只有一行。SLA、时间线、版本回滚都失真。
4. **LLM 调用不统一**（02, 04, 05）：翻译走三段事务并计费；概念解析、术语抽取、一致性 pass、review 期跟进重译各有自己的 client、事务与（缺失的）计费。预算护栏和成本视图因此失明。
5. **同一规则多份实现**（03, 04, 05, 06）：可译性判定三套；术语匹配翻译期与审校期两套；图注正则三份；group-context 推断解析层与导出层各一份；前端复制了后端状态机与「3 分钟 stale」规则。
6. **书籍特定启发式硬编码**（03, 04, 06）：默认 prompt profile 是「AI/LLM 技术专栏」人设；heuristics pack 16 条规则全部针对一本 agentic-AI 书；导出代码识别含 LangChain 特定 token；解析含 Manning「Listing N.M」专属规则；分段/sanity/标题判定全部英文词表。换一本书就是换一套误判。
7. **并发与生命周期缺口**（02）：非活跃 run 的租约永不回收；review 线程内重译与 translate 前沿并发写同一 packet；暂停时长计入预算；连接池耗尽被判终止失败；SSE 每客户端占一条池连接。
8. **持久化不完整**（01, 07）：compose 只挂 exports 卷；Fernet 密钥自动生成写进容器层；删文档不删文件；无备份。
9. **同步阻塞的请求路径**（01, 03）：bootstrap（含 OCR 子进程）、`execute_action?run_followup=true`、provider test 都在 HTTP 线程内跑 LLM/解析，无超时。
10. **工程护栏缺失**（09）：无 CI、无 conftest、ruff 121 错、`uv sync` 会卸掉 pytest、`.env.example` 未追踪、Dockerfile 单阶段 root、前端无生产托管。

每一项的具体位置、复现路径与修复方向见对应分篇的「问题清单」；合并后的优先级表见 [production/gaps-and-risks.md](../production/gaps-and-risks.md)。

## 7. 阅读顺序建议

- 想改运行时/并发：02 → 07 §4 → 04 §5。
- 想改翻译质量：04 → 05 → agent-upgrade/02。
- 想改解析：03（先看 §2.4 锚点与 §12 问题表）。
- 想上线：01 §15 → 07 §8/§10 → 09 §5 → production/gaps-and-risks.md。

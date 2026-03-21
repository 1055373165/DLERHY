# TODOS

Last Updated: 2026-03-21 16:10 CST

## Purpose

这份文档是当前 `book-agent` 项目的接力开发快照。

目标只有两个：

- 让新接手的程序员 5 分钟内知道项目做到哪里了
- 让下一刀开发可以直接开始，而不是重新考古

## Read First

**架构重构主线（当前活跃方向）：**

1. `docs/multi-agent-final-implementation-plan.md` — **Phase 1 锁定计划（必读）**，7 个 workstream、编码顺序、验收标准
2. `docs/multi-agent-translation-product-review.md` — 产品评审，确定"selective expansion"方向
3. `docs/multi-agent-architecture-design.md` — 技术深度补充（OCR 方案对比、Memory 数据结构、成本模型、Markdown 中间格式 schema）
4. `auto-pilot.md` — 开发框架协议

**项目基础（仍然有效）：**

5. `PROGRESS.md` — Web 产品化 slice 已 100% 完成
6. `DECISIONS.md` — 已有 14 条 ADR
7. `README.md` — 项目总览
8. `docs/translation-agent-system-design.md` — 系统设计
9. `docs/orchestrator-state-machine.md` — 状态机设计

如果要继续看实现，按 workstream 顺序入手：

- WS2 起点：`src/book_agent/services/memory_service.py`（当前只有 64 行骨架）
- WS2 上游：`src/book_agent/services/context_compile.py`、`src/book_agent/infra/repositories/chapter_memory.py`
- WS3 起点：`src/book_agent/workers/contracts.py`、`src/book_agent/services/translation.py`
- WS4 起点：`src/book_agent/app/runtime/document_run_executor.py`、`src/book_agent/services/run_execution.py`
- WS5 起点：`src/book_agent/services/review.py`、`src/book_agent/orchestrator/rule_engine.py`

## One-Screen Summary

```
                        ┌──────────────────────────┐
                        │  Web 产品化 slice: 100%   │  ← 上一轮完成
                        │  Dedication: exported      │
                        │  Agentic AI: translating   │
                        └────────────┬─────────────┘
                                     │
                        ┌────────────▼─────────────┐
                        │  Multi-Agent 架构重构      │  ← 当前活跃方向
                        │  设计阶段: 100% 完成       │
                        │  编码阶段: 0% 未开始       │
                        │  下一步: WS2 + WS3 编码    │
                        └──────────────────────────┘
```

- Web 产品化 slice 已全部完成（33/33 MDU）。上传、整书翻译、review/repair loop、导出全链路已通。
- 首页刚完成第二轮整页重写：主流程已改为全宽工作台结构，书库历史不再挤在右侧窄栏里。
- Multi-Agent 架构重构已完成**全部设计工作**：产品评审 → 实施计划锁定 → 技术深度补充文档。
- **编码尚未开始。** 下一步是按锁定计划的 WS2 + WS3 开始实现。

一句话判断：

> 架构设计文档已齐备并互相一致，Phase 1 范围已冻结，编码顺序已锁定。接手后直接进入 WS2 编码，不需要再做任何设计讨论。

## Recent Addendum: UI Rewrite Snapshot

这轮新增了一次**彻底的首页工作台重写**，不是旧布局改皮肤。

### 新首页现在是什么

- 入口文件仍是 `src/book_agent/app/ui/page.py`
- 但整页 HTML / CSS / JS 已重写为新的“整书译制工作台”结构
- 信息架构变成：
  - 新建书籍任务
  - 当前书籍
  - 运行总览
  - 交付资产
  - 复核与阻塞
  - 章节注意区
  - 最近运行记录
  - 书库历史（底部全宽）

### 第二轮收敛现在又做了什么

- 核心流程区全部改为 full-width，不再和历史书库抢宽度
- 书库历史从右侧窄栏挪到底部整段展示，长标题、长路径和筛选器不再截断
- 视觉系统从偏暖、偏柔光的奶油背景收敛到更冷静的 slate / blue-gray 工作台配色
- 主字体改为现代无衬线，弱化过强的“编辑部样张感”，提升专业工具感
- 历史卡片的源路径被拆到独立 `history-path` 容器，不再把卡片正文撑爆

### 这次重写顺手修掉了什么

- 刷新页面后丢当前 document 上下文：
  - 前端现在会把 `document_id` 存到 `localStorage`
  - 刷新后优先恢复上次查看的书
  - 没有缓存时，会自动打开当前仍在处理中的历史书籍

### 接手时要知道的约束

- 这仍然是 FastAPI 直出页面，不是独立前端工程
- 所以如果继续做 UI，优先在 `page.py` 内维护信息架构和样式 token，而不是马上拆出前端构建链
- 如果后面要上多页路由、复杂组件复用、浏览器级状态管理，再考虑拆前端工程

## Architecture Refactor State

### 已完成的设计工件

| 文档 | 状态 | 核心内容 |
|------|------|---------|
| `docs/multi-agent-translation-product-review.md` | locked | 产品方向："selective expansion"，不做自由协作 agent swarm |
| `docs/multi-agent-final-implementation-plan.md` | locked-mvp-scope | Phase 1 范围冻结，7 workstreams，编码顺序，验收标准 |
| `docs/multi-agent-architecture-design.md` | reference-supplement | 10 节技术深度：OCR 方案对比、Markdown schema、Memory 数据结构、成本/延迟模型 |

### Phase 1 锁定范围

- 输入格式：`EPUB`、`PDF_TEXT`、`PDF_MIXED`（`PDF_SCAN` 推迟）
- 输出格式：`MERGED_MARKDOWN`、`BILINGUAL_HTML`（rebuilt EPUB/PDF 推迟）
- Reviewer 不做自由重写（只做 deterministic hard-gate QA）
- 控制面保持确定性（不引入 agent-to-agent 对话循环）
- 服务角色在进程内做 boundary，不拆分进程

### 7 个 Workstream 及当前状态

| WS | 名称 | 状态 | 依赖 | 主要文件 | 完成标志 |
|----|------|------|------|---------|---------|
| WS1 | Structure provenance hardening | 未开始 | 无 | `domain/structure/pdf.py`, `services/bootstrap.py` | 下游可读 `layout_risk`, `bbox`, `linked_caption` |
| **WS2** | **Memory service extraction** | **未开始 ← 从这里开始** | WS1 不阻塞 | `services/memory_service.py`, `services/context_compile.py` | 翻译不再从多处 ad-hoc 读 chapter memory |
| **WS3** | **Compiled translation context** | **未开始** | WS2 | `workers/contracts.py`, `services/translation.py` | 每次翻译记录 compiled context version + memory version |
| WS4 | Chapter-lane packet control | 未开始 | WS3 | `app/runtime/document_run_executor.py`, `services/run_execution.py` | 同一章节不出现竞争 packet |
| WS5 | Deterministic review gate | 未开始 | WS3 | `services/review.py`, `orchestrator/rule_engine.py` | rerun 决策显式且可重复 |
| WS6 | Layout validation gate | 未开始 | WS1, WS5 | `services/layout_validate.py`(新), `services/export.py` | export 可在结构损坏时快速失败 |
| WS7 | Regression harness | 与 WS1-6 并行 | 各 WS | `tests/*` | 每个 WS 至少一个聚焦回归 + 一个集成证明 |

**编码顺序硬约束（见锁定计划 §16）：**

```
WS2 → WS3 → WS4 → WS5 → WS6
         WS7 并行跟进
         WS1 可随时穿插
```

### Pre-Code Readiness Checklist

以下各项均已在设计阶段确认为 true：

- [x] Phase 1 只覆盖 `EPUB` + `PDF_TEXT` + `PDF_MIXED`
- [x] Phase 1 只产出 `MERGED_MARKDOWN` + `BILINGUAL_HTML`
- [x] Reviewer 不做自由重写
- [x] `ContextPacket` vs `CompiledTranslationContext` 边界已明确
- [x] Memory 写入权限已明确：worker propose → review approve → memory commit
- [x] 章节 lane 规则已明确：每章一个活跃 packet
- [x] Layout validation 确认阻塞 export
- [x] 优先用 JSON payload 扩展（`packet_json`, `model_config_json`, `source_span_json`, `chapter.metadata_json`）
- [x] 不新增 table 除非 join/uniqueness/audit 要求
- [x] Packet sub-state 先用 JSON，稳定后再升级为 enum
- [ ] 回归语料选定（需要 1 个 EPUB + 1 个 text PDF + 1 个 mixed PDF 样本）

**唯一未完成的 checklist 项是选定回归语料。** 接手后第一步建议先从现有测试 fixtures 或真实样本中选定三个。

## Next Step: How to Start WS2

### WS2 目标

把 `memory_service.py`（当前 64 行骨架）升级为 Memory Service 的显式读/提议/提交门面。

### WS2 要做的事

1. **`load_compiled_context(packet_id)`** — 冻结 memory snapshot，返回 compiled context + `memory_snapshot_version`
2. **`load_latest_chapter_memory(document_id, chapter_id)`** — 读取最新章节记忆
3. **`record_translation_proposals(packet_id, proposals)`** — 记录 worker 的术语/概念候选（只提议不锁定）
4. **`commit_approved_packet_memory(packet_id, translation_run_id)`** — review 通过后提交章节记忆

### WS2 主要改动文件

- 扩展 `src/book_agent/services/memory_service.py`
- 调整 `src/book_agent/services/context_compile.py`（把分散的 memory 读取收敛到 memory service）
- 调整 `src/book_agent/infra/repositories/chapter_memory.py`
- 新增 `tests/test_memory_service.py`

### WS2 不做的事

- 不新建术语表系统（继续用现有 `TermEntry`）
- 不新建 table（用 `MemorySnapshot` payload 扩展）
- 不改翻译 prompt（那是 WS3 的事）

### WS2 验收标准

- 翻译请求始终携带显式 `memory_version`
- rerun 可重现翻译时使用的 memory snapshot
- `test_memory_service.py` 覆盖 load/propose/commit 路径

## Current Live Snapshot

### Book A: `Dedication`

- document id: `67283f52-b775-533f-988b-c7433a22a28f`
- 文档状态：`exported`
- open blocking issue：`0`
- 章节状态：`92 exported`
- 历史污染修复状态：
  - 旧 document title 已纠偏，不再显示 `Dedication`
  - 历史 mixed code/prose block 已通过 `refresh + fragment repair` 修干净
  - merged export 标题已稳定为真实书名
- 已产物：
  - 整书阅读包：`artifacts/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.html`
  - 双语章节导出目录：`artifacts/exports/67283f52-b775-533f-988b-c7433a22a28f/`

### Book B: `Agentic AI`

- document id: `cf32d839-ac79-5503-bcde-ba5ea12e92e6`
- 当前活跃 run：`229a6dc6-5072-4ea3-ada7-3cc0db0f8113`
- run 状态：`running`
- 当前阶段：`translate`
- 截至本次快照：`701 succeeded / 189 pending / 1 running`
- 当前有 `1` 个 active lease
- 这本书之前是为了给 `Dedication` 让路被手动暂停的；本轮已经重新拉起

## Key Design Decisions for the Refactor

接手后不需要重新做这些决策：

1. **不做 agent swarm** — 保持确定性控制面，服务角色在进程内做 boundary（见 product review §1）
2. **Markdown 是工作视图，DB 是真相源** — Markdown 不替代 sentence alignment / issue routing / packet provenance（见 implementation plan §2.3）
3. **Translation packet 是语义块，不是 RAG chunk** — 按 heading/paragraph/table/code boundary 切，不按固定字符窗切（见 implementation plan §2.4）
4. **Worker 只 propose，Reviewer approve，Memory commit** — 术语锁定权在 reviewer 和人工（见 architecture design §4.3）
5. **Path A（Orchestrator 重调度）为默认修复路径** — Reviewer 不直接重写翻译（见 architecture design §6.3）
6. **实时同步 + review 后锁定** — 不需要翻译前统一术语 pass（见 architecture design §8.4）

## Important Operational Constraints

### SQLite 仍然是单机保守模式

- 本地默认数据库还是 `artifacts/book-agent.db`
- SQLite 可以支持当前开发和单书长跑
- 但不要同时放多本大书并行跑
- 如果需要恢复运行，优先保证同一时刻只有一条重型 run 在持续写库

### 当前推荐的运行方式

- 如果只是查看页面：
  - `uv run uvicorn book_agent.app.main:app --host 127.0.0.1 --port 8000`
- 如果只是继续后台整书翻译：
  - 启一个本地 executor daemon 即可，不一定要开 Web

### 当前 UI 还剩下的最值钱工作

- 做一次稳定的浏览器级 UI QA：
  - 这轮代码和测试都通过了，但本地 `browse` 工具当时因为端口分配失败，没有留下完整视觉验收证据
- 继续把“导出阻塞解释”做得更细：
  - 现在已经能展示 blocker/hotspot/最重章节
  - 但还没把 chapter queue / owner-ready / SLA 这些 review worklist 信息产品化进首页

### 当前已拉起的后台翻译

- `Agentic AI` 已由之前的会话拉起本地 executor daemon
- 如果你接手时发现它不再推进，先不要同时再拉第二个 daemon
- 先检查当前是否还有旧的 `uvicorn` / daemon 在跑，避免双 executor 抢 SQLite 锁

## Command Cheat Sheet

### 1. 查看某本书最新 run 状态

```bash
uv run python - <<'PY'
from sqlalchemy import select
from book_agent.core.config import get_settings
from book_agent.infra.db.session import build_session_factory, session_scope
from book_agent.domain.models.ops import DocumentRun, WorkItem

settings = get_settings()
session_factory = build_session_factory(database_url=settings.database_url)
document_id = "cf32d839-ac79-5503-bcde-ba5ea12e92e6"
with session_scope(session_factory) as session:
    run = session.scalars(
        select(DocumentRun).where(DocumentRun.document_id == document_id).order_by(DocumentRun.created_at.desc())
    ).first()
    items = session.scalars(select(WorkItem).where(WorkItem.run_id == run.id)).all()
    counts = {}
    for item in items:
        key = getattr(item.status, "value", item.status)
        counts[key] = counts.get(key, 0) + 1
    print(run.id, getattr(run.status, "value", run.status), counts)
PY
```

### 2. 恢复 `Agentic AI` 的当前 paused / queued run

```bash
uv run python - <<'PY'
from book_agent.core.config import get_settings
from book_agent.infra.db.session import build_session_factory, session_scope
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_control import RunControlService

settings = get_settings()
session_factory = build_session_factory(database_url=settings.database_url)
run_id = "229a6dc6-5072-4ea3-ada7-3cc0db0f8113"
with session_scope(session_factory) as session:
    summary = RunControlService(RunControlRepository(session)).resume_run(
        run_id,
        actor_id="ops",
        note="resume Agentic AI",
        detail_json={"source": "handoff"},
    )
    print(summary.run_id, summary.status)
PY
```

### 3. 启本地 executor daemon

```bash
uv run python - <<'PY'
import time
from book_agent.core.config import get_settings
from book_agent.infra.db.session import build_session_factory, session_scope
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_control import RunControlService, RunControlTransitionError
from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.workers.factory import build_translation_worker

run_id = "229a6dc6-5072-4ea3-ada7-3cc0db0f8113"
settings = get_settings()
session_factory = build_session_factory(database_url=settings.database_url)

with session_scope(session_factory) as session:
    control = RunControlService(RunControlRepository(session))
    try:
        control.resume_run(run_id, actor_id="ops", note="resume from daemon", detail_json={"source": "handoff"})
    except RunControlTransitionError:
        pass

executor = DocumentRunExecutor(
    session_factory=session_factory,
    export_root=settings.export_root,
    translation_worker=build_translation_worker(settings),
)
executor.start()
executor.wake(run_id)
print("executor started", run_id, flush=True)

try:
    while True:
        time.sleep(60)
except KeyboardInterrupt:
    executor.stop()
PY
```

### 4. 启 Web 页面

```bash
uv run uvicorn book_agent.app.main:app --host 127.0.0.1 --port 8000
```

页面地址：

- `http://127.0.0.1:8000/`

### 5. 跑全量测试

```bash
uv run --extra dev python -m pytest tests/ -q
```

## Anti-Drift Rules

在 Phase 1 编码期间，主动拒绝以下诱惑（来自锁定计划 §19）：

- "顺便支持一下 scanned PDF"
- "顺便加个更智能的 reviewer 自动重写"
- "顺便把 JSON payload 换成新 table"
- "顺便加个向量库做 memory retrieval"
- "顺便把 service 拆成独立进程"

如果一个改动不能减少控制流、memory provenance 或 export gating 的歧义，它大概率不属于 Phase 1。

## Known Gaps

- 回归语料尚未选定（需要 1 EPUB + 1 text PDF + 1 mixed PDF 样本）
- 全量测试没有在最近一轮全部复跑
- 这轮只完成了代码级与入口/API 级验证，仍缺一次稳定的浏览器级截图验收
- 后台 executor 还是进程内模型，离真正的 durable worker 还有距离
- 目前更适合单用户 / 单机使用，不适合多人同时提交多本大书长跑

## Bottom Line

- `Dedication`：已完成，可直接交付
- `Agentic AI`：已恢复，继续翻译中
- Multi-Agent 重构：**设计阶段 100% 完成，编码阶段 0% 未开始**
- 下一位程序员的第一个任务：**选定回归语料 → 开始 WS2（memory_service.py 升级）**
- 不需要再做任何架构讨论，所有决策已在三份设计文档中锁定

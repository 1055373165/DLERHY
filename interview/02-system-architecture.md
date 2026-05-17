# 系统架构图

> 反映实际代码的架构（基于 `src/book_agent/` 实际类与方法）。

## 一、ASCII 架构图

```
                ┌─────────────────────────────────────────────────────────────┐
                │                          USER                                │
                │            Upload PDF/EPUB  ────────► Download bilingual MD/HTML
                └────────────────┬────────────────────────────▲────────────────┘
                                 │                            │
                                 ▼                            │
        ┌────────────────────────────────────────────────────────────────────┐
        │                       INGEST LAYER (Bootstrap)                      │
        │ ┌────────────┐ ┌────────────┐ ┌─────────────────────────────────┐ │
        │ │ IngestSvc  │ │ ParseSvc   │ │     PdfStructureRecoveryService  │ │
        │ │ (sha256    │─►│ PDFParser  │─►│  • _split_embedded_page_heading │ │
        │ │  finger-   │ │ EPUBParser │ │  • _merge_cross_page_prose_*    │ │
        │ │  print)    │ │            │ │  • _apply_figure_clustering      │ │
        │ └────────────┘ └────────────┘ │  • _recover_text_only_figures    │ │
        │                                │  • _classify_role (heading/body) │ │
        │                                │  • 30+ recovery passes (idempotent│ │
        │                                └─────────────────────────────────┘ │
        │ Output IR:  Document → Chapter → Block → Sentence  (5-level)        │
        └────────────────────────────────────────┬───────────────────────────┘
                                                 │ persist
                                                 ▼
        ┌────────────────────────────────────────────────────────────────────┐
        │                  POSTGRES  (single source of truth)                 │
        │ documents | chapters | blocks | sentences | translation_packets    │
        │ target_segments | memory_snapshots | book_profiles                 │
        │ translation_runs | events | chapter_memory_proposals               │
        └────────────────────────────────────────┬───────────────────────────┘
                                                 │
            ┌────────────────────────────────────┼────────────────────────────┐
            │                                    │                            │
            ▼                                    ▼                            ▼
  ┌───────────────────┐         ┌──────────────────────────┐    ┌──────────────────┐
  │  MEMORY LAYER     │         │   CONTEXT PACKET BUILDER  │    │  ORCHESTRATOR     │
  │ ┌───────────────┐ │         │                           │    │ (reconciliation   │
  │ │BookProfile v1 │ │         │  for each translatable    │    │  loop, K8s-style) │
  │ │  style+policy │ │◄───────┤  block:                   │    │                   │
  │ └───────────────┘ │         │   • current = block(s)    │    │  desired_state    │
  │ ┌───────────────┐ │         │   • prev_blocks = last 2  │    │   = packets.BUILT │
  │ │Termbase   v1+ │ │  inject │   • next_blocks = next 2  │    │  actual_state     │
  │ │ (GLOBAL)      │─┼────────►│   • relevant_terms        │    │   = poll workers  │
  │ │ source→target │ │         │     (regex match on text) │    │                   │
  │ │ lock_level    │ │         │   • relevant_entities     │    │  diff → action    │
  │ └───────────────┘ │         │   • chapter_brief summary │    │                   │
  │ ┌───────────────┐ │         │   • heading_path          │    │  dispatch ▼       │
  │ │Entity Registry│ │         │   • style_constraints     │    │           │       │
  │ │ (GLOBAL)      │─┼────────►│   • budget_hint(tok)      │    │           │       │
  │ └───────────────┘ │         │                           │    │           │       │
  │ ┌───────────────┐ │         │  → TranslationPacket      │    │           │       │
  │ │ChapterBrief   │ │         │     (stable_id, hash of   │    │           │       │
  │ │ (per chapter) │─┼────────►│      chapter+block+memvers│    │           │       │
  │ └───────────────┘ │         └──────────────┬────────────┘    │           │       │
  │ ┌───────────────┐ │                        │                  │           │       │
  │ │ChapterMemory  │ │                        │                  │           │       │
  │ │ recent_accepted_translations            │                  │           │       │
  │ │ (per chapter) │ │                        │                  │           │       │
  │ └───────────────┘ │                        │                  └──────────┼───────┘
  └───────────────────┘                        │                              │
                                               ▼                              ▼
        ┌────────────────────────────────────────────────────────────────────────┐
        │                    TRANSLATION EXECUTION (worker pool)                  │
        │ ┌──────────────────────────────────────────────────────────────────┐  │
        │ │           TranslationService.execute_packet(packet_id)            │  │
        │ │  ┌────────────────────────────────────────────────────────────┐  │  │
        │ │  │ 1. load_packet_bundle (DB)                                  │  │  │
        │ │  │ 2. memory_service.load_compiled_context                     │  │  │
        │ │  │    → merge chapter_memory_snapshot at runtime               │  │  │
        │ │  │ 3. _inject_locked_glossary  (defense in depth)              │  │  │
        │ │  │ 4. worker.translate(ContextPacket)                          │  │  │
        │ │  │    └─► LLMTranslationWorker → DeepSeek API                  │  │  │
        │ │  │ 5. _coerce_worker_result (JSON-schema validation)           │  │  │
        │ │  │ 6. glossary_enforcement.check  (post-validate)              │  │  │
        │ │  │ 7. persist TargetSegment (per sentence) + Alignment edges    │  │  │
        │ │  │ 8. emit ChapterMemoryProposal (new terms detected)          │  │  │
        │ │  └────────────────────────────────────────────────────────────┘  │  │
        │ └──────────────────────────────────────────────────────────────────┘  │
        │                                                                          │
        │   Worker N   Worker N+1   Worker N+2   ...   (ThreadPool, 4–8 conc.)   │
        └──────────────────────────────────────────────┬─────────────────────────┘
                                                       │
                              outcome ─────────────────┤
                                                       │
              ┌────────────────────────────────────────┴──────────────────────────┐
              │                ERROR CLASSIFIER  /  HEALER                         │
              │                                                                    │
              │   exc/result.code ──►  case:                                       │
              │     TransientNetwork  → exp-backoff + re-enqueue                   │
              │     RateLimit         → cooldown window, re-enqueue                │
              │     SchemaViolation   → strip + repair + retry (≤ MAX_RETRY)       │
              │     QualityViolation  → repair-prompt + resubmit                   │
              │     GlossaryViolation → inject hard glossary + resubmit            │
              │     BudgetExhausted   → circuit-break, pause orchestrator          │
              │     ProviderDown      → circuit-break, pause orchestrator          │
              │     ToolError/Bug     → DEAD_LETTER → escalate to UI               │
              └────────────────────────────────────────┬──────────────────────────┘
                                                       │ success ⇣
                                                       ▼
        ┌────────────────────────────────────────────────────────────────────────┐
        │                      EXPORT PIPELINE  (read-only)                       │
        │  export_chapter_zh_html.py   →  per-chapter zh-only HTML + MD           │
        │                              →  per-chapter bilingual HTML + MD         │
        │  build_full_book_bilingual.py →  concatenate 9 chapters → one HTML      │
        │  verify_chapter.py           →  structural R1–R8 checks                  │
        └────────────────────────────────────────────────────────────────────────┘

   ── observability ─────────────────────────────────────────────────────────────
   events table  ─ LLM_CALL_STARTED / LLM_CALL_FAILED / PACKET_COMPLETED / …
   translation_runs ─ run snapshot, KPIs (cost, latency, retry count)
```

---

## 二、D2 语法代码（可直接渲染）

> 用 `d2` CLI 渲染：`d2 architecture.d2 architecture.svg`

```d2
direction: down

user: User {shape: person}

ingest: "Ingest Layer (Bootstrap)" {
  ingest_svc: "IngestService\nsha256 fingerprint"
  parse_svc: "ParseService\n• PDFParser\n• EPUBParser"
  recovery: "PdfStructureRecoveryService\n30+ recovery passes" {
    p1: "_split_embedded_page_heading_segments"
    p2: "_merge_cross_page_prose_continuations"
    p3: "_apply_figure_clustering"
    p4: "_recover_text_only_figures"
    p5: "_classify_role"
  }
  ingest_svc -> parse_svc -> recovery
}

db: "PostgreSQL (single source of truth)" {
  shape: cylinder
  tables: |md
  - documents
  - chapters
  - blocks (Document → Chapter → Block → Sentence IR)
  - sentences
  - translation_packets
  - target_segments
  - memory_snapshots
  - book_profiles
  - chapter_memory_proposals
  - translation_runs
  - events
  |
}

memory: "Memory Layer" {
  book_profile: "BookProfile v1\nstyle_policy_json\nspecial_content_policy"
  termbase: "Termbase (GLOBAL)\nMemorySnapshot\nscope=GLOBAL\ntype=TERMBASE"
  entities: "Entity Registry (GLOBAL)\nMemorySnapshot\nscope=GLOBAL\ntype=ENTITY_REGISTRY"
  brief: "ChapterBrief (per chapter)\nMemorySnapshot\nscope=CHAPTER\ntype=CHAPTER_BRIEF"
  chap_mem: "ChapterTranslationMemory (per chapter)\nrecent_accepted_translations\nlast_packet_id"
}

builder: "ContextPacketBuilder.build_packet" {
  shape: rectangle
  body: |md
  for each translatable block:
    current_blocks = [block]
    prev_blocks    = blocks[-2:]
    next_blocks    = blocks[+1:+3]
    relevant_terms     = match(termbase,    context_text)
    relevant_entities  = match(entities,    context_text)
    chapter_brief      = brief.summary
    heading_path       = brief.heading_path
    style_constraints  = book_profile.style_policy_json
    budget_hint        = {6000 in / 2500 out}
    packet_id = stable_id(chapter+block+mem_versions)
  |
}

orchestrator: "Orchestrator (K8s-style reconciliation)" {
  desired: "desired = packets where status=BUILT"
  actual: "actual = poll worker status"
  diff: "diff → action"
  desired -> diff
  actual  -> diff
}

execution: "TranslationService.execute_packet" {
  s1: "1. load_packet_bundle"
  s2: "2. memory_service.load_compiled_context\n   (merge chapter_memory_snapshot)"
  s3: "3. _inject_locked_glossary\n   (defense in depth)"
  s4: "4. worker.translate(ContextPacket)\n   → DeepSeek API"
  s5: "5. JSON schema validation"
  s6: "6. glossary_enforcement post-validate"
  s7: "7. persist TargetSegment + AlignmentEdge"
  s8: "8. emit ChapterMemoryProposal\n   (auto-discovered terms)"
  s1 -> s2 -> s3 -> s4 -> s5 -> s6 -> s7 -> s8
}

healer: "Error Classifier / Healer" {
  shape: rectangle
  policies: |md
  TransientNetwork  → exp-backoff + re-enqueue
  RateLimit         → cooldown, re-enqueue
  SchemaViolation   → repair + retry (≤ MAX_RETRY)
  QualityViolation  → repair-prompt + resubmit
  GlossaryViolation → inject hard glossary + resubmit
  BudgetExhausted   → circuit-break, pause
  ProviderDown      → circuit-break, pause
  ToolError/Bug     → DEAD_LETTER → UI escalate
  |
}

export: "Export Pipeline" {
  zh_html: "export_chapter_zh_html.py\n(zh-only + bilingual)"
  full_book: "build_full_book_bilingual.py\n(concat 9 chapters)"
  verify: "verify_chapter.py\n(R1–R8 structural checks)"
  zh_html -> full_book
  zh_html -> verify
}

user -> ingest: "upload"
ingest -> db: "persist IR"
db -> memory: "BookProfile/Brief/Termbase\nbuilt on bootstrap"
memory -> builder: "inject snapshots"
db -> builder: "load Block/Sentence"
builder -> db: "save TranslationPacket"
db -> orchestrator
orchestrator -> execution: "dispatch packet"
execution -> healer: "on failure"
healer -> orchestrator: "re-enqueue / escalate"
execution -> db: "persist target_segments"
db -> export
export -> user: "deliverable"
```

---

## 三、关键设计决策（面试要点）

| 决策 | 选择 | Trade-off |
|---|---|---|
| **IR 层级** | 5 层：Document → Chapter → Block → Sentence + Packet | 灵活但 migration 成本高 |
| **Packet 粒度** | 1-3 个相邻 block，~500 token | 失败爆炸半径小 + 上下文够用；代价是跨 packet 一致性需 memory 注入 |
| **解析与下游解耦** | 共享 `ParsedDocument` IR；PDF/EPUB 仅 parser 不同 | 增加抽象层；换格式零下游成本 |
| **状态存储** | 单一 PostgreSQL 真相源 | 无分布式状态；扩展上限是单库 |
| **并发模型** | ThreadPool per chapter (4–8 worker) | 简单可观察；CPU 不是瓶颈，IO-bound |
| **重试策略** | 按错误类型分层（不是统一 backoff） | 复杂；但避免在 BudgetExhausted 上做无意义重试 |
| **记忆增长** | ChapterMemoryProposal review 队列 | 增加 review 成本；保证术语锁定的可信度 |
| **stable_id** | 把 memory 版本嵌入 packet ID | 内存升级自动失效旧 packet；可重放性强 |

---

## 四、可观察性 (Observability)

- **events 表**：每个 LLM 调用 / packet 转换都有事件。可以拉时间线还原任意 run。
- **translation_runs 表**：run 级 KPI（cost、latency P50/P95、retry count、failure rate）。
- **chapter_memory_proposals**：术语提案的审计 trail（谁、何时、为何引入 X→Y）。
- **JSON 报告**：`*-bilingual.qa_combined.json` 包含每章 R1–R8 结构检查 + repair_stats。

---

## 五、扩展路径（招聘官常问的 "where would you take this next?"）

1. **Multi-tenant**：用户级配额隔离 + 按 tenant 分库 / 按 tenant queue
2. **结构化错误码**：worker 返回 `error_code` 而非 string match
3. **Termbase 冷启动自动化**：NER + LLM 抽取 + 人工审核 loop
4. **Multi-provider failover**：DeepSeek/GPT/Claude 之间根据 cost、latency、quality 动态路由
5. **Incremental 重译**：只重译 termbase 升级后受影响的 packet（已有 stable_id 机制支持）

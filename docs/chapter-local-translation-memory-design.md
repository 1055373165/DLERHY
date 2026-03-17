# Chapter-Local Translation Memory Design

Last Updated: 2026-03-17
Status: draft-for-implementation

相关驾驶舱文档：

- [translation-quality-refactor-cockpit.md](/Users/smy/project/book-agent/docs/translation-quality-refactor-cockpit.md)
- [translation-consistency-and-cache-plan.md](/Users/smy/project/book-agent/docs/translation-consistency-and-cache-plan.md)

## 1. Why This Design Exists

当前系统虽然已经不是“每句单独调用一次模型”，但真实 packet 证据已经证明：

- packet 内句序会错乱
- prompt 仍然以 `Current Sentences` 为主，而不是以有序段落为主
- chapter brief 到章节后半段会失效
- `relevant_terms / relevant_entities` 往往为空
- `prev_translated_blocks` 虽然存在，但没有被提升为稳定的章节记忆机制

结果是：

- 每个请求在运行时是独立的
- 请求之间缺少强约束的篇章连续性
- 模型更像“带一点上下文的句级翻译器”，而不是“沿章节持续推进的段落级译者”

这份设计文档的目标是把“跨 packet 的上下文连续性”从隐式行为，升级成显式、可持久化、可重跑的章节级翻译记忆。

## 2. Goals

- 保持请求独立，避免整章绑死在一个黑箱长对话 session 上
- 让 packet 2 真正继承 packet 1 的已接受译文、概念决策和论证状态
- 让 packet 3 能感知 packet 1 和 packet 2 已建立的上下文
- 保持句级 alignment、局部 rerun、审计和 export 的现有能力
- 尽量减少额外 token 成本，优先使用外部记忆而不是长对话历史

## 3. Non-Goals

- 第一版不追求完整 discourse parser
- 第一版不引入新的 LLM 调用来专门做记忆抽取
- 第一版不解决所有风格问题
- 第一版不做跨文档共享概念库

## 4. Core Principle

主原则只有一句话：

> 请求独立，但章节记忆连续。

这意味着：

- **不依赖 LLM 的隐藏会话状态**
- **依赖系统自己的外部显式记忆**

这样做的原因是：

- 可重跑
- 可追踪
- 可局部修复
- 可比较不同 memory 版本的效果

## 5. Proposed Runtime Model

### 5.1 Existing raw packet remains unchanged

当前 bootstrap 产物 [ContextPacket](/Users/smy/project/book-agent/src/book_agent/workers/contracts.py) 仍保留，作为“原始 packet 上下文”。

它继续承担：

- 当前块
- 前后块
- term / entity snapshot
- chapter brief

但它不再直接等价于“最终 prompt 输入”。

### 5.2 Add a runtime-only compiled context

新增运行时对象：

`CompiledTranslationContext`

建议结构：

```json
{
  "packet_id": "ba917844-c3b9-5689-91ad-f984703dea71",
  "chapter_id": "d1ff075e-e6cf-52ee-9eb7-789d2b6a4a9c",
  "ordered_current_paragraph_text": "For this to be possible, memory is the key....",
  "ordered_current_sentences": [
    {"sentence_id": "s1", "ordinal_in_block": 1, "source_text": "..."}
  ],
  "section_path": ["Toward Memory as Infrastructure"],
  "section_brief": "本节在解释 memory 为什么是 agentic AI 的基础设施前提。",
  "discourse_bridge": {
    "previous_paragraph_role": "类比",
    "current_paragraph_role": "技术定义",
    "relation_to_previous": "从类比转入概念解释",
    "active_referents": ["memory", "agentic AI", "distributed SQL"]
  },
  "active_concepts": [
    {"source_term": "agentic AI", "canonical_zh": "智能体式AI", "status": "locked"}
  ],
  "recent_accepted_translations": [
    {"packet_id": "670447b0-...", "source_excerpt": "...", "target_excerpt": "..."}
  ],
  "local_source_neighbors": {
    "prev_blocks": [...],
    "next_blocks": [...]
  },
  "style_policy": {
    "voice": "technical-explainer",
    "sentence_preference": "paragraph_led_natural_cn",
    "preserve_structure": true
  },
  "sentence_ledger": [
    {"alias": "S1", "sentence_id": "uuid-1", "source_text": "..."}
  ]
}
```

这个对象的定位是：

- `ContextPacket` 是 bootstrap 的静态结构工件
- `CompiledTranslationContext` 是每次请求前，由“原始 packet + 章节记忆”编译出的动态上下文工件

## 6. Persistence Strategy

### 6.1 Reuse `MemorySnapshot` first

第一版不新建 memory 表，直接复用 [MemorySnapshot](/Users/smy/project/book-agent/src/book_agent/domain/models/document.py)。

需要扩展：

- [SnapshotType](/Users/smy/project/book-agent/src/book_agent/domain/enums.py) 新增  
  `CHAPTER_TRANSLATION_MEMORY = "chapter_translation_memory"`

使用方式：

- `scope_type = CHAPTER`
- `scope_id = chapter_id`
- `snapshot_type = CHAPTER_TRANSLATION_MEMORY`
- `version = 1, 2, 3...`

### 6.2 Memory payload schema

`MemorySnapshot.content_json` 建议使用如下 schema：

```json
{
  "schema_version": 1,
  "chapter_id": "uuid",
  "last_packet_id": "uuid",
  "last_packet_ordinal": 17,
  "section_path": ["Toward Memory as Infrastructure"],
  "section_brief": "本节在解释 memory 为什么是 agentic AI 的基础设施前提。",
  "discourse_bridge": {
    "previous_paragraph_role": "类比",
    "current_paragraph_role": "定义展开",
    "relation_to_previous": "从 metaphor 转入 technical explanation",
    "active_referents": ["memory", "agentic AI", "distributed SQL"]
  },
  "active_concepts": [
    {
      "source_term": "context engineering",
      "canonical_zh": "上下文工程",
      "status": "candidate",
      "confidence": 0.82,
      "first_seen_packet_id": "2e26e803-...",
      "evidence_sentence_ids": ["..."]
    }
  ],
  "recent_accepted_translations": [
    {
      "packet_id": "670447b0-...",
      "block_id": "a8684576-...",
      "source_excerpt": "If generative AI chat is a recipe book...",
      "target_excerpt": "如果说生成式AI聊天是食谱书，那么智能体式AI就是私人厨师。"
    }
  ],
  "style_observations": {
    "voice": "technical-explainer",
    "forbidden_patterns": ["情境工程", "证据的分量表明"]
  },
  "open_decisions": [
    {
      "type": "concept_translation",
      "source_term": "context engineering",
      "candidates": ["上下文工程", "语境工程"],
      "status": "needs_confirmation"
    }
  ]
}
```

### 6.3 Reproducibility metadata

为了保证 packet rerun 可复现，第一版不必立即加新列，可以先把下面信息写入 [TranslationRun.model_config_json](/Users/smy/project/book-agent/src/book_agent/domain/models/translation.py)：

```json
{
  "context_compile_version": "v1",
  "chapter_memory_snapshot_version_used": 4
}
```

这样可以先避免数据库迁移；等方案站稳后，再决定是否提升为显式列。

## 7. Data Flow

### 7.1 Bootstrap stage

在 [builders.py](/Users/smy/project/book-agent/src/book_agent/domain/context/builders.py) 现有流程后追加：

1. `BookProfileBuilder.build(...)`
2. `ChapterBriefBuilder.build_many(...)`
3. `ContextPacketBuilder.build_many(...)`
4. `ChapterTranslationMemoryBootstrapper.build_many(...)`

`ChapterTranslationMemoryBootstrapper` 的职责：

- 为每个 chapter 创建 `version=1` 的空 memory
- 初始化：
  - `section_brief`
  - `active_concepts = []`
  - `recent_accepted_translations = []`
  - `style_observations` 的默认值

### 7.2 Translation stage

当前链路大致是：

- [translation.py](/Users/smy/project/book-agent/src/book_agent/infra/repositories/translation.py) `load_packet_bundle()`
- [services/translation.py](/Users/smy/project/book-agent/src/book_agent/services/translation.py) `execute_packet()`
- [translator.py](/Users/smy/project/book-agent/src/book_agent/workers/translator.py) `build_translation_prompt_request()`

建议改成：

1. `TranslationRepository.load_packet_bundle(packet_id)`
2. `ChapterMemoryRepository.load_latest(chapter_id)`
3. `ContextCompiler.compile(bundle, chapter_memory)`
4. `build_translation_prompt_request(compiled_context, ...)`
5. `worker.translate(...)`
6. 保存 translation artifacts
7. `ChapterMemoryUpdater.write_next_snapshot(...)`

### 7.3 Export / review stage

review 和 export 暂不直接消费整份 memory，但应能看到：

- 本 packet 用的是哪版 `chapter_memory`
- 当前 packet 是否新引入了 `candidate concept`
- 当前 packet 是否触发了 `open_decisions`

这会为后续 `UNLOCKED_KEY_CONCEPT`、`STYLE_DRIFT` 等 issue 打基础。

## 8. Prompt Design

### 8.1 New prompt shape

第二版 prompt 不应再以 `Current Sentences` 为主体，而应以“当前有序段落 + 句级账本”分层输入。

建议顺序：

1. System policy
2. Output contract
3. Style policy
4. Section path + section brief
5. Discourse continuity notes
6. Active concepts
7. Recent accepted translations
8. Previous/upcoming source neighbors
9. Current paragraph
10. Sentence ledger for alignment

### 8.2 Example user prompt skeleton

```text
Core Translation Contract:
- Produce natural Chinese at paragraph level.
- Preserve all propositions.
- Maintain complete sentence-level alignment coverage.

Section Context:
- Section Path: Toward Memory as Infrastructure
- Section Brief: ...

Discourse Continuity:
- Previous paragraph role: analogy
- Current paragraph role: technical explanation
- Relation to previous: shift from metaphor to definition

Active Concepts:
- agentic AI => 智能体式AI (locked)
- context engineering => 上下文工程 (candidate)

Recent Accepted Translations:
- If generative AI chat is a recipe book... => 如果说生成式AI聊天是食谱书...

Current Paragraph:
For this to be possible, memory is the key. Without it, ...

Sentence Ledger:
1. [S1] For this to be possible, memory is the key.
2. [S2] Without it, a chef will forget...
...
```

### 8.3 Expected output

建议把输出扩成：

```json
{
  "packet_id": "uuid",
  "paragraph_text_zh": "为了实现这一点，记忆是关键。没有记忆，厨师会忘记你的过敏史……",
  "target_segments": [...],
  "alignment_suggestions": [...],
  "low_confidence_flags": [...],
  "notes": [...]
}
```

其中：

- `paragraph_text_zh` 是段落级自然中文成稿
- `target_segments` 继续服务句级对齐和 rerun

## 9. Memory Writeback

### 9.1 Phase 1 writeback should be deterministic

第一版不额外调用 LLM 来提炼 memory，先做规则型写回，避免增加 token 成本。

建议写回来源：

- 当前 packet 的 `ordered_current_paragraph_text`
- 已保存的 `target_segments`
- alignment 结果
- 当前 packet 的 `relevant_terms`
- 当前 packet 的 heading / section path

### 9.2 What to write back

至少写回 4 类信息：

- `recent_accepted_translations`
- `discourse_bridge`
- `active_concepts` 的新增候选
- `style_observations` 的禁止项或推荐项

### 9.3 Initial deterministic rules

第一版概念候选可以用保守规则：

- Title Case 多词短语
- 引号中的术语
- 带缩写括号的术语
- 章节内重复出现的多词短语
- 已进入 `relevant_terms` 但未 locked 的概念

如果规则命中但没有 canonical 中文，就进入 `candidate`，不立刻自动锁定。

## 10. File-Level Implementation Plan

### 10.1 New modules

建议新增：

- `src/book_agent/domain/context/chapter_memory.py`
- `src/book_agent/infra/repositories/chapter_memory.py`
- `src/book_agent/services/context_compile.py`

### 10.2 Existing files to modify

- [enums.py](/Users/smy/project/book-agent/src/book_agent/domain/enums.py)
- [document.py](/Users/smy/project/book-agent/src/book_agent/domain/models/document.py)
- [builders.py](/Users/smy/project/book-agent/src/book_agent/domain/context/builders.py)
- [translation.py](/Users/smy/project/book-agent/src/book_agent/infra/repositories/translation.py)
- [services/translation.py](/Users/smy/project/book-agent/src/book_agent/services/translation.py)
- [translator.py](/Users/smy/project/book-agent/src/book_agent/workers/translator.py)
- [review.py](/Users/smy/project/book-agent/src/book_agent/services/review.py)

### 10.3 Minimal code change order

建议按这个顺序落：

1. 修 packet 句序硬伤
2. 新增 `SnapshotType.CHAPTER_TRANSLATION_MEMORY`
3. 加 `ChapterMemoryRepository`
4. 加 `ContextCompiler`
5. 改 prompt 为 paragraph-led
6. 改 translation save 流程，写回 memory snapshot
7. 补 review issue 类型

## 11. Rollout Plan

### Slice 1

- 修复 packet 句序
- 固定 3 个真实 packet A/B
- 不引入 chapter memory

### Slice 2

- 引入空白 `chapter translation memory`
- prompt 消费 `section_brief + recent_accepted_translations`
- 仍不写回 concepts

### Slice 3

- 写回 `recent_accepted_translations + discourse_bridge`
- 加 `chapter_memory_snapshot_version_used`

### Slice 4

- 引入 `active_concepts` 候选发现
- review 增加 `UNLOCKED_KEY_CONCEPT`

### Slice 5

- 根据真实书籍效果，再决定是否扩大到：
  - `style_observations`
  - `open_decisions`
  - section-aware rerun

## 12. Validation Plan

每个 slice 至少做以下 3 类验证中的 2 类：

- 单元测试
- 真实 packet A/B
- 真实章节小范围 rerun

强制保留的回归样本：

- [670447b0-0bca-5df9-aea8-4f30cbcd8b1f.json](/Users/smy/project/book-agent/artifacts/analysis/packet-debug/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/670447b0-0bca-5df9-aea8-4f30cbcd8b1f.json)
- [ba917844-c3b9-5689-91ad-f984703dea71.json](/Users/smy/project/book-agent/artifacts/analysis/packet-debug/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.json)
- [2e26e803-5d84-52ae-9b01-c5b8e07a7d93.json](/Users/smy/project/book-agent/artifacts/analysis/packet-debug/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.json)

关键验收指标：

- 句序恢复正确
- `context engineering` 不再自由漂移
- 段内中文更像整段表达，不再逐句发硬
- packet rerun 可复现
- token 成本增长可控

## 13. Open Questions

- `chapter_memory_snapshot_version_used` 是先放 `model_config_json`，还是直接升成显式列？
- `active_concepts` 的候选发现是否需要第二阶段引入轻量 LLM 提炼？
- 对 PDF 论文是否共用这套 chapter memory，还是先只服务 EPUB/书籍正文？
- `paragraph_text_zh` 是否进入持久化主链，还是先只作为导出和 QA 参考字段？

## 14. Recommended Next Step

最合理的下一步不是直接把整套 memory 都写出来，而是：

1. 先修 packet 句序
2. 以这 3 个真实 packet 为样本
3. 把 prompt 改成 paragraph-led
4. 再接入最小的 `recent_accepted_translations + section_brief` 版 chapter memory

这样可以把实现风险压到最低，同时最快看到真实译文改善。

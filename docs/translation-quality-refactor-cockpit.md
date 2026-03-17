# Translation Quality Refactor Cockpit

Last Updated: 2026-03-17
Status: active
Scope: `Agentic AI Data Architectures How Distributed SQL Unifies Enterprise Scale and AI-Native Application Design`

这份文档是“英译中质量重构”的单一驾驶舱。后续只要沿这条线开发，就必须先看这份文档，并在每次完成一个功能切片后同步更新：

- `Last Updated`
- `Status`
- `Progress Tracker`
- `Evidence / Validation`
- `Open Risks / Next Slice`

目标不是只记录想法，而是把**真实 packet 证据、已确认根因、改造优先级、完成进度**固化下来，防止重复分析、遗漏和 token 浪费。

实施设计配套文档：

- [chapter-local-translation-memory-design.md](/Users/smy/project/book-agent/docs/chapter-local-translation-memory-design.md)

## 1. Current Evidence Base

本轮分析完全基于本地现有产物完成，没有重新翻整本书：

- 导出目录：[exports](/Users/smy/project/book-agent/artifacts/exports/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4)
- 资源目录：[assets](/Users/smy/project/book-agent/artifacts/exports/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/assets)
- 合并版导出：[merged-document.html](/Users/smy/project/book-agent/artifacts/exports/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/merged-document.html)
- 实际数据库：[book-agent.db](/Users/smy/project/book-agent/artifacts/book-agent.db)
- packet 调试样本目录：[packet-debug](/Users/smy/project/book-agent/artifacts/analysis/packet-debug/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4)
- 调试样本导出脚本：[export_packet_debug.py](/Users/smy/project/book-agent/scripts/export_packet_debug.py)

本次抽检章节：

- 章节：[Toward Memory as Infrastructure](/Users/smy/project/book-agent/artifacts/exports/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/bilingual-d1ff075e-e6cf-52ee-9eb7-789d2b6a4a9c.html)
- chapter_id: `d1ff075e-e6cf-52ee-9eb7-789d2b6a4a9c`

## 2. Representative Real Packets

本轮用于定位根因的 3 个真实 packet：

| Case | block_id | packet_id | Core symptom |
|---|---|---|---|
| recipe metaphor | `a8684576-c5a5-5bbc-b205-83ba3ce08a10` | `670447b0-0bca-5df9-aea8-4f30cbcd8b1f` | 句序被打乱，结论句先于铺垫句进入 prompt |
| memory paragraph | `56e3320e-6d55-530f-9a28-481ed29524bd` | `ba917844-c3b9-5689-91ad-f984703dea71` | 12 句长段被乱序后逐句翻译，最终中文发硬 |
| context engineering | `83a33338-aeee-58ff-b1f1-af15c043a4ff` | `2e26e803-5d84-52ae-9b01-c5b8e07a7d93` | 关键概念未锁定，chapter brief 过期，术语漂移为“情境工程” |

## 3. Confirmed Root Causes

### 3.1 Sentence order corruption inside packet loading

这是目前最硬的根因证据。

- packet 在构建时本意上是“段落外壳 + 句级对齐”
- 但真实运行时，prompt 消费的是 `Current Sentences`
- 这些句子在读取时没有稳定顺序，导致模型看到的是乱序句子集合

相关代码：

- [translation.py](/Users/smy/project/book-agent/src/book_agent/infra/repositories/translation.py)
- [translation.py](/Users/smy/project/book-agent/src/book_agent/domain/models/translation.py)

当前判断：

- 当前首要问题是 packet 读取链路没有按源顺序严格 `ORDER BY`
- `PacketSentenceMap` 仍缺少显式 `ordinal`，但第一刀已先通过 `Block.ordinal + Sentence.ordinal_in_block` 恢复稳定句序

影响：

- 模型不是在翻“一个有论证顺序的段落”
- 而是在翻“一组被打散的句子”

### 3.2 Prompt ignores ordered block text and overweights sentence ledger

`ContextPacket.current_blocks[0].text` 本身是有序段落正文，但当前 prompt 主要喂给模型的是句子列表，而不是段落正文。

相关代码：

- [contracts.py](/Users/smy/project/book-agent/src/book_agent/workers/contracts.py)
- [translator.py](/Users/smy/project/book-agent/src/book_agent/workers/translator.py)
- [openai_compatible.py](/Users/smy/project/book-agent/src/book_agent/workers/providers/openai_compatible.py)

影响：

- 设计上是 packet 翻译
- 行为上更像“多句一起发，但逐句翻译再拼接”

### 3.3 Chapter brief is weak and stale

当前 chapter brief 只是本章前 3 个可翻译句子的轻量拼接摘要。

相关代码：

- [builders.py](/Users/smy/project/book-agent/src/book_agent/domain/context/builders.py)

影响：

- 对章节后半段几乎失效
- `context engineering` 所在 packet 仍拿到“recipe book / personal chef”式的早期摘要

### 3.4 Term and entity memory are nearly empty

当前全书 profile 的 `termbase_snapshot` 和 `entity_snapshot` 初始近乎为空，而且匹配策略偏弱。

相关代码：

- [builders.py](/Users/smy/project/book-agent/src/book_agent/domain/context/builders.py)

影响：

- `context engineering` 这种高价值概念不会被系统识别成待治理对象
- 模型会临场自由翻译成“情境工程”

### 3.5 Previous accepted translations can reinforce weak terminology

当前 packet 会带 `prev_translated_blocks`，这是有价值的；但如果前文已经出现了不理想译法，它也会反向污染后文。

相关代码：

- [translation.py](/Users/smy/project/book-agent/src/book_agent/infra/repositories/translation.py)

影响：

- 坏译法会形成局部自强化
- packet 越往后，修正成本越高

### 3.6 QA is too narrow for concept translation quality

当前 review 更擅长抓：

- locked term miss
- coverage / structure / risk issues

但还不擅长抓：

- 未锁定关键概念
- 行业常用译法缺失
- literalism / style drift

相关代码：

- [review.py](/Users/smy/project/book-agent/src/book_agent/services/review.py)

## 4. What Is Not The Primary Root Cause

以下问题会影响观感，但不是本轮确认的首要根因：

- HTML 段内换行渲染  
  这条已经修过，不是“情境工程”或“逐句硬译”的主因。
- 前端样式或字号  
  这只影响可读性，不解释概念译错和论证发硬。
- 单纯模型能力不足  
  当前更像是“系统输入与约束方式压制了模型的中文表达能力”。

## 5. Real Packet Weakness Snapshot

### 5.1 Packet fields that are genuinely helping

- `current_blocks`
- `prev_blocks`
- `next_blocks`
- `prev_translated_blocks`

这些字段说明系统并不是完全没有上下文。

### 5.2 Packet fields that are currently too weak

- `chapter_brief`
- `relevant_terms`
- `relevant_entities`
- `style_constraints`
- `heading_path`
- `open_questions`

当前这些字段要么内容过薄，要么对当前段落不相关，要么根本没有命中。

## 6. Refactor Direction

### Phase 0: Lock the evidence and stop repeating analysis

目标：

- 固化真实 packet 调试样本
- 后续任何翻译质量讨论都先看样本，不重新整书翻译

建议固定的调试样本目录：

- `artifacts/analysis/packet-debug/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/`

当前已固化样本：

- [670447b0-0bca-5df9-aea8-4f30cbcd8b1f.json](/Users/smy/project/book-agent/artifacts/analysis/packet-debug/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/670447b0-0bca-5df9-aea8-4f30cbcd8b1f.json)
- [ba917844-c3b9-5689-91ad-f984703dea71.json](/Users/smy/project/book-agent/artifacts/analysis/packet-debug/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.json)
- [2e26e803-5d84-52ae-9b01-c5b8e07a7d93.json](/Users/smy/project/book-agent/artifacts/analysis/packet-debug/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.json)

### Phase 1: Fix sentence ordering as a hard bug

目标：

- 为 packet 句映射增加显式顺序
- 读取时稳定恢复原段落句序

验收：

- 上述 3 个真实 packet 的 `Current Sentences` 顺序与源段落一致

### Phase 2: Move from sentence-led prompt to paragraph-led prompt

目标：

- prompt 主体改为有序段落正文
- 句级 ledger 退居 sidecar，对齐仍保留

建议输出结构：

- `paragraph_text_zh`
- `target_segments`
- `alignment_suggestions`

验收：

- 同一 packet 在不丢 alignment 的前提下，中文自然度明显提升

### Phase 3: Introduce concept registry

目标：

- 对高价值概念做候选发现、锁定、回写和 rerun

第一批优先对象：

- `context engineering`
- `agentic AI`
- `memory substrate`

验收：

- 首次概念译法可锁定
- 后续命中一致

### Phase 4: Upgrade QA from term miss to concept-quality review

新增建议 issue：

- `UNLOCKED_KEY_CONCEPT`
- `LITERALISM`
- `STYLE_DRIFT`
- `STALE_CHAPTER_BRIEF`

验收：

- “情境工程”“证据的分量表明”这类问题能在 review 中主动暴露

## 7. Progress Tracker

### Done

- [x] 从 live DB 重建真实 packet，而不是只看静态代码
- [x] 锁定 3 个代表性真实 packet
- [x] 确认 sentence-order corruption 是当前首要硬伤
- [x] 确认 prompt 仍然是 sentence-led，而不是 paragraph-led
- [x] 确认 chapter brief / term memory / QA 都偏弱
- [x] 将 3 个真实 packet 固化到 `artifacts/analysis/packet-debug/...`
- [x] 提供可重复执行的调试样本导出脚本 `scripts/export_packet_debug.py`
- [x] 首刀句序修复：packet 读取链路已按 `Block.ordinal + Sentence.ordinal_in_block` 恢复稳定顺序
- [x] 3 个真实 packet 调试样本已复导，`matches_block_order = true`
- [x] 第二刀 prompt 重排：已切到 `Current Paragraph + Sentence Ledger` 结构
- [x] 3 个真实 packet 调试样本已复导，`prompt_request.user_prompt` 已同步为 paragraph-led 版本
- [x] 最小 `chapter-local memory` 已接入：bootstrap 初始化 + translation 读写 + prompt 复用
- [x] 单章回归已证明第 4 个 packet 能复用第 1 个非相邻 packet 的已接受译文

### Planned

- [ ] 引入 concept registry v1
- [ ] 扩展 review 规则以抓概念和直译腔问题
- [ ] 做真实 packet A/B 回归，而不是整书盲重翻

## 8. Validation Protocol

后续每完成一刀，必须至少补下面 4 类验证中的 2 类：

- 单元/仓储测试：句序、packet 读取、prompt 构造
- 真实 packet A/B：只对固定 packet 做翻译对比
- 真实章节小范围 rerun：只重跑受影响 packet 或 chapter
- 导出核验：确认 merged/chapter HTML 里实际中文表现改善

禁止每次都直接整书重跑再人工看结果。优先：

1. 固定 packet 调试样本
2. 小范围 rerun
3. 必要时再做整章或整书复核

## 9. Update Discipline

以后沿这条线开发时，每次都按下面规则更新本文档：

1. 改完代码后，先更新 `Last Updated`
2. 把本轮完成项从 `Planned` 或 `In Progress` 移到 `Done`
3. 在文档末尾新增一条 `Progress Log`
4. 记录：
   - 本轮改了什么
   - 验证了什么
   - 还有什么残余问题
5. 如果结论被推翻，直接改“Confirmed Root Causes”，不要把旧判断留在主干里继续误导

## 10. Progress Log

### 2026-03-17

- 完成真实 packet 级分析，确认当前翻译质量问题的首要根因不是“模型不会翻”，而是 packet 句序损坏、prompt 句级主导、chapter brief 过弱、concept memory 缺位和 QA 过窄。
- 已固定 3 个代表性 packet：`670447b0-0bca-5df9-aea8-4f30cbcd8b1f`、`ba917844-c3b9-5689-91ad-f984703dea71`、`2e26e803-5d84-52ae-9b01-c5b8e07a7d93`。
- 后续开发默认以本文档为驾驶舱，不再重复从零分析。
- 已将 3 个真实 packet 导出到 `artifacts/analysis/packet-debug/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/`，JSON 内包含 `context_packet`、`prompt_request`、`current_blocks_ordered`、`current_sentences_loaded`、`sentence_order_diagnostics`、`persisted_target_segments` 和 `persisted_alignment_edges`。
- 已新增可复用导出脚本 [export_packet_debug.py](/Users/smy/project/book-agent/scripts/export_packet_debug.py)。后续如果要扩样本，只需要提供 `document_id + packet_id`，不需要重翻整本书。
- 已新增接近实施粒度的设计稿 [chapter-local-translation-memory-design.md](/Users/smy/project/book-agent/docs/chapter-local-translation-memory-design.md)，覆盖数据结构、调用链、prompt 形状、写回策略和 rollout 顺序。
- 已完成首刀句序修复，当前实现不引入 schema 迁移，直接在 [translation.py](/Users/smy/project/book-agent/src/book_agent/infra/repositories/translation.py) 读取 packet 时按 `Block.ordinal + Sentence.ordinal_in_block` 排序，优先兼容现有 live DB。
- 已补回归 [test_translation_worker_abstraction.py](/Users/smy/project/book-agent/tests/test_translation_worker_abstraction.py)，锁住 `load_packet_bundle()` 和 prompt 中 `Current Sentences` 的顺序恢复。
- 已重新导出 3 个真实 packet 调试样本，`sentence_order_diagnostics.matches_block_order` 已全部从 `False` 变为 `True`。
- 已完成第二刀 prompt 重排，当前 [translator.py](/Users/smy/project/book-agent/src/book_agent/workers/translator.py) 会先给模型 `Current Paragraph`，再给 `Sentence Ledger` 做对齐保底；不再让句子列表独占主输入。
- 已补 prompt contract 回归，确保新 prompt 至少包含 `Section Context`、`Current Paragraph`、`Sentence Ledger` 三个关键 section。
- 已实现最小 `chapter-local memory` 链路：
  - bootstrap 生成 `CHAPTER_TRANSLATION_MEMORY` 快照
  - translation 前加载并编译章节记忆
  - translation 后写回 `recent_accepted_translations`
  - 当前 prompt 的 `Previous Accepted Translations` 不再只依赖局部 `prev_translated_blocks`
- 当前最小版本故意收窄，只做 `chapter_brief + recent_accepted_translations`，暂不引入 concept registry，避免过早扩大改动面。
- 本轮验证严格限制在单章/单测级，没有发起整本书 rerun。

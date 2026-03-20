# Translation Quality Refactor Cockpit

Last Updated: 2026-03-19
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
- [multi-agent-translation-product-review.md](/Users/smy/project/book-agent/docs/multi-agent-translation-product-review.md)

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
- [x] `concept registry v1` 最小闭环已接入：候选概念写回章节记忆并进入后续 prompt
- [x] review 已能主动报 `UNLOCKED_KEY_CONCEPT`，不再只靠人工肉眼发现
- [x] review 已接入首版 `STYLE_DRIFT` literalism 检测，能主动报“情境工程 / 证据的分量表明”这类高信号坏味道
- [x] review 已接入首版 `STALE_CHAPTER_BRIEF` advisory，能主动报章节早期摘要未覆盖后半章核心概念
- [x] 真实 packet A/B 工位已工具化：固定 packet 现可离线导出多套 context/prompt 变体对照
- [x] 单 packet 实验脚本已接入：默认 dry-run，显式 `--execute` 才会调用模型
- [x] 单 packet diff 工位已接入：两份实验 JSON 现可直接生成结构化 diff 工件
- [x] 章节记忆回填器已接入：可基于现有成功 packet 零 token 回填 `chapter_translation_memory`
- [x] 真实章节扫描工位已接入：可按 `memory_gain + concept_gain` 为单 packet execute 挑候选
- [x] 已锁定当前真实章节的首个 execute 候选 packet：`ba917844-c3b9-5689-91ad-f984703dea71`
- [x] 已完成首个真实 single-packet execute 对照：`ba917844-c3b9-5689-91ad-f984703dea71`
- [x] `chapter concept memory` 首轮降噪规则已落地
- [x] `chapter memory backfill` 已支持 `reset_existing`，可对老文档重建干净记忆
- [x] 实验工件已补来源指纹：`generated_at / database_url / chapter_memory_snapshot_id`
- [x] 已确认“脚本工件 vs 进程内服务”不一致不是主链路读旧数据，而是陈旧实验工件未刷新；当前脚本与进程内结果已对齐
- [x] 已完成首个“干净 concept memory” single-packet execute，对象仍为 `ba917844-c3b9-5689-91ad-f984703dea71`
- [x] 已补零 token 的章节概念锁定能力：可将 `source_term -> canonical_zh` 直接写入 `chapter_translation_memory`
- [x] 已补单 packet 的临时 concept override 能力：可不写 live DB，直接做术语裁决实验
- [x] 已完成 `context engineering -> 上下文工程` 的首个真实 single-packet execute 验证
- [x] 已完成 `agentic AI` 三候选首轮真实对照：`智能体AI / 智能体式AI / 代理式AI`
- [x] 已在定义性更强的 `ba917844-c3b9-5689-91ad-f984703dea71` packet 上完成 `agentic AI` 三候选真实对照
- [x] 已完成 `agentic AI -> 智能体式AI` 的零 token 锁定写回，并在后续 packet 上完成真实连锁验证
- [x] 已将“锁定章节概念”桥接到主链路 `Locked and Relevant Terms`：锁定后的概念不再只停留在 `Chapter Concept Memory`
- [x] `ChapterConceptLockService` 已同步 upsert 章节级 `TermEntry`，锁定结果开始具备主链路术语实体
- [x] 已完成零 token 验证：post-lock packet 的 `compiled_relevant_term_count` 从 `0 -> 1`，prompt 中已出现 `Locked and Relevant Terms: Agentic AI => 智能体式AI`
- [x] review 已尊重已锁定章节术语：同一概念若已有锁定 `TermEntry`，不再重复报 `UNLOCKED_KEY_CONCEPT`
- [x] review 已能用章节级锁定术语反查旧译文：若历史译文仍使用过时术语，会自动报 `TERM_CONFLICT`
- [x] 已完成 `context engineering -> 上下文工程` 的 live 锁定写回与单 packet 真正验证
- [x] `STYLE_DRIFT` 第二阶段已接入：开始覆盖真实 packet 中暴露出的回退变体 `证据权重表明 / 上下文更准确的输出`
- [x] 已完成 A/B/C 提示词矩阵实验（3 个固定 packet × 3 组 prompt profile）
- [x] 已将 `role-style-v2` 提升为实验工位默认提示词基线；`run_packet_experiment.py` 与 `PacketExperimentOptions` 默认值已切换，相关回归已更新
- [x] `STYLE_DRIFT` 第二阶段已继续扩面：开始覆盖 `语境工程 / 证据权重显示 / 更具上下文准确性的输出结果` 等更接近真实回退的变体，并在 evidence 中记录 `matched_target_excerpt`
- [x] 已接入“定向 rerun 提示闭环”：`RerunPlan` 现可携带 `concept_overrides + style_hints`，packet rerun 时会注入 prompt，而不再是盲 rerun
- [x] 已完成首个真实 `STYLE_DRIFT` hint-rerun 验证：`2e26e803-5d84-52ae-9b01-c5b8e07a7d93` 在注入 rerun hints 后，`证据权重表明 / 上下文更准确的输出` 两个命中已同时降到 0
- [x] live rerun 已支持同 packet sibling issue 聚合：单次 `STYLE_DRIFT` action 触发 rerun 时，会自动合并同 packet 其它未决 `STYLE_DRIFT/TERM_CONFLICT` 的 hints/overrides
- [x] 已完成首条 live rerun 端到端回归：`IssueActionExecutor -> RerunService -> ReviewService` 可在单次 packet rerun 后同时清掉同 packet 上的两个 `STYLE_DRIFT` 命中
- [x] workflow 级 review 自动纠偏已从 `STYLE_DRIFT` 扩展到安全的 packet 级 `TERM_CONFLICT`：仅限非阻断、已锁定术语、packet scope 的 `UPDATE_TERMBASE_THEN_RERUN_TARGETED`
- [x] 已补 workflow 级单章回归：review 阶段现在可在单次 auto-followup 中自动清掉同 packet 上的 `TERM_CONFLICT`，无需先手工执行 action
- [x] workflow 级 auto-followup 候选已新增优先级：预算有限时，先处理 blocking 的 packet `TERM_CONFLICT`，再处理 advisory `STYLE_DRIFT`
- [x] 已补混合样本回归：同章同时存在 `TERM_CONFLICT + STYLE_DRIFT` 且预算仅 `1` 时，workflow 会先执行 `TERM_CONFLICT` 的 packet rerun
- [x] `chapter concept memory` 的 `times_seen` 语义已修正为“唯一 packet 数”，不再因同一 packet 重跑而虚高
- [x] `UNLOCKED_KEY_CONCEPT` issue evidence 已开始显式携带 `packet_ids_seen`，后续做真正的 targeted rerun 时不再只有 `last_seen_packet_id`
- [x] `ChapterConceptAutoLockService` 已接入“启发式优先、模型兜底”的 resolver 组合：章节里已有稳定中文共识时，可零 token 自动锁词
- [x] `STALE_CHAPTER_BRIEF` issue evidence 已显式携带 `packet_ids_seen`，不再只是 chapter-scope advisory
- [x] `REBUILD_CHAPTER_BRIEF + STALE_CHAPTER_BRIEF` 已可投影成 packet-scope rerun plan：先零 token 重建 brief，再只重跑受影响 packet
- [x] workflow auto-followup 已接入 `STALE_CHAPTER_BRIEF` 的安全路径，但当前只作为 `UNLOCKED_KEY_CONCEPT` 自动锁词失败时的 packet-level fallback
- [x] `STALE_CHAPTER_BRIEF` 已从通用 auto-followup 候选池中收回；当前只在 `UNLOCKED_KEY_CONCEPT` 自动锁词失败且 packet 集合一致时，才作为 workflow fallback 触发
- [x] 已给正式 `role-style-v2` prompt 接入高置信 `Paragraph Intent Signal`：当前只把 `definition / evidence` 这类高精度意图显式注入 prompt，类比段默认不再放大
- [x] 已给 `role-style-v2` 接入 source-aware literalism guardrails（可开关）：当源文命中 `context engineering / weight of evidence / contextually accurate outputs` 这类高风险 literalism 锚点时，会把更自然的中文改写方向显式编译进 prompt
- [x] 已将 `STYLE_DRIFT` 检测规则与 source-aware literalism guardrails 收拢成共享定义：后续扩规则时，首轮 prompt 与 review 检测不再各自漂移

### Planned

- [ ] 继续扩展 review 规则，把 `STYLE_DRIFT` 从当前词组级模式推进到更泛化的直译腔检测
- [ ] 基于这次 `denoised execute` 结果，决定是否继续收紧 `concept registry` 候选策略，尤其是 `智能体AI` 这类仍需人工裁决的术语
- [ ] 基于 `context engineering` 实验结果，决定是否把“临时 override -> 锁定写回”升成正式术语裁决流程
- [ ] 决定是否把 `agentic AI -> 智能体式AI` 正式提升为当前章节默认裁决，并作为后续 packet 的首选基线
- [ ] 再决定下一刀是继续强化 concept policy，还是扩大 `STYLE_DRIFT` 规则面

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
- 新增回归证明：在一个 4 段章节里，执行前 3 段后，第 4 段的 prompt 已能看到第 1 段的已接受译文，这说明章节记忆已跨越“仅前 2 段邻接上下文”的旧边界。
- 已接入 `concept registry v1` 的最小版本：
  - translation 后按保守规则抽取技术概念候选
  - 候选概念写入 `CHAPTER_TRANSLATION_MEMORY.active_concepts`
  - context compile 会把 `active_concepts` 注入 `ContextPacket.chapter_concepts`
  - prompt 新增 `Chapter Concept Memory`
- 已接入 `UNLOCKED_KEY_CONCEPT` review 规则：当候选概念在章节内反复出现但尚未锁定中文译法时，系统会生成非阻断 advisory，并建议 `UPDATE_TERMBASE_THEN_RERUN_TARGETED`。
- 本轮验证仍然只做单章/单测级，没有触发整本书重跑；测试输出里有 SQLite `ResourceWarning`，但不影响当前功能正确性，这条后续再单独清理。
- 已接入首版 `STYLE_DRIFT` literalism 规则，当前故意只覆盖高信号坏味道：
  - `context engineering -> 情境工程`
  - `weight of evidence -> 证据的分量表明`
- 当前 `STYLE_DRIFT` 一律按非阻断 advisory 处理，并走 `RERUN_PACKET`，避免在规则仍然偏窄时误伤导出主链路。
- 已补单章级回归：使用固定坏味道译文样本，验证 review 会生成 `STYLE_DRIFT`，且 action 为 `RERUN_PACKET`。
- 已接入首版 `STALE_CHAPTER_BRIEF` 规则：当章节早期摘要未覆盖后半章反复出现的核心概念时，review 会生成非阻断 advisory，并建议 `REBUILD_CHAPTER_BRIEF`。
- 已补单章级回归：使用“前 3 段讲 recipe/chef，后 2 段反复讲 context engineering”的固定样本，验证 review 会生成 `STALE_CHAPTER_BRIEF`，且 action 为 `REBUILD_CHAPTER_BRIEF`。
- 已将 `export_packet_debug.py` 升级成 A/B 导出工位。当前每个固定 packet JSON 除默认 `prompt_request` 外，还会额外输出 `prompt_variants`：
  - `paragraph_led_current`
  - `paragraph_led_no_memory`
  - `paragraph_led_no_concepts`
  - `sentence_led_current`
- context compile 已支持显式开关：
  - `include_memory_blocks`
  - `include_chapter_concepts`
  - `prefer_memory_chapter_brief`
- prompt builder 已支持 `paragraph-led / sentence-led` 两种布局，单测已锁定两种布局的关键 section 顺序。
- 已重新导出 3 个固定真实 packet，后续分析 prompt/context 改造收益时，优先比较这些离线 A/B 变体，不需要整书重翻。
- 已新增单 packet 实验服务 [packet_experiment.py](/Users/smy/project/book-agent/src/book_agent/services/packet_experiment.py) 和脚本 [run_packet_experiment.py](/Users/smy/project/book-agent/scripts/run_packet_experiment.py)。
- 当前设计边界：
  - 默认只导出编译后的 `context_packet + prompt_request`，不调用模型
  - 只有显式传 `--execute` 才会对单个 packet 调用当前配置的 translation worker
  - 实验结果不写回主翻译持久化链路，不会污染 live DB
- 已补单测锁住两条关键 contract：
  - dry-run 模式不会生成 `worker_output`
  - execute 模式只对单个 packet 调用 worker，并返回该 packet 的输出工件
- 本轮验证仍然只做 packet/单章级测试；合并单测时仍有 SQLite `ResourceWarning`，但不影响当前功能正确性。
- 已新增 diff 服务 [packet_experiment_diff.py](/Users/smy/project/book-agent/src/book_agent/services/packet_experiment_diff.py) 和脚本 [compare_packet_experiments.py](/Users/smy/project/book-agent/scripts/compare_packet_experiments.py)。
- 当前 diff 工件会同时输出：
  - `summary`
  - `context_delta`
  - `prompt_delta.user_prompt_unified_diff`
  - `output_delta`
- 已落第一份真实 diff：
  - [2e26e803-5d84-52ae-9b01-c5b8e07a7d93.paragraph_led_current.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.paragraph_led_current.json)
  - [2e26e803-5d84-52ae-9b01-c5b8e07a7d93.sentence_led_no_memory.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.sentence_led_no_memory.json)
  - [2e26e803-5d84-52ae-9b01-c5b8e07a7d93.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.diff.json)
- 这份真实 diff 的当前观察：
  - `prompt_layout_changed = true`
  - `user_prompt_changed = true`
  - 但 `previous_translation_count_changed = false`、`chapter_concept_count_changed = false`
  - 说明对这个 packet 而言，当前 memory 开关还没有贡献额外上下文，主要变化仍来自 prompt 结构
- 因此下一默认 slice 不应直接浪费 token 做这个 packet 的真实 rerun，而应先挑一个“章节记忆确实参与”的 packet 再进入 execute 对照。
- 已新增章节记忆回填服务 [chapter_memory_backfill.py](/Users/smy/project/book-agent/src/book_agent/services/chapter_memory_backfill.py) 和脚本 [backfill_chapter_memory.py](/Users/smy/project/book-agent/scripts/backfill_chapter_memory.py)。
- 这条能力专门用于“老文档没有 `chapter_translation_memory` 快照，但已经有成功翻译产物”的场景：直接基于现有 packet / translation run / target segments 回放章节记忆，不重新请求模型。
- 已补单章级回归：先翻 4 个 packet，再删除 `CHAPTER_TRANSLATION_MEMORY`，随后执行 backfill，确认能零 token 重建最新章节记忆。
- 针对当前真实章节 `d1ff075e-e6cf-52ee-9eb7-789d2b6a4a9c`，已完成一次真实回填：
  - [chapter-backfill.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/chapter-backfill.json)
  - `translated_packet_count = 49`
  - `replayed_packet_count = 49`
  - `latest_snapshot_version = 50`
  - `latest_concept_count = 12`
- 已新增章节扫描服务 [packet_experiment_scan.py](/Users/smy/project/book-agent/src/book_agent/services/packet_experiment_scan.py) 和脚本 [scan_packet_experiments.py](/Users/smy/project/book-agent/scripts/scan_packet_experiments.py)。
- 扫描工位当前按以下信号为 packet 排序：
  - `memory_gain`
  - `concept_gain`
  - `brief_from_memory`
  - `current_sentence_count`
- 当前真实章节扫描结果已落盘：
  - [chapter-scan.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/chapter-scan.json)
- 已锁定当前最值得做首次真实 execute 的 packet：
  - `packet_id = ba917844-c3b9-5689-91ad-f984703dea71`
  - `memory_gain = 4`
  - `concept_gain = 12`
  - `memory_signal_score = 652`
- 已为该候选 packet 补齐零 token A/B 工件：
  - [ba917844-c3b9-5689-91ad-f984703dea71.paragraph_led_current.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.paragraph_led_current.json)
  - [ba917844-c3b9-5689-91ad-f984703dea71.sentence_led_no_memory.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.sentence_led_no_memory.json)
  - [ba917844-c3b9-5689-91ad-f984703dea71.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.diff.json)
- 这份 top-candidate diff 的当前结论：
  - `previous_translation_count_changed = true`，从 `2 -> 6`
  - `chapter_concept_count_changed = true`，从 `0 -> 12`
  - `chapter_brief_changed = false`
  - 说明当前最有价值的提升来源已经不是 chapter brief，而是 `recent_accepted_translations + chapter_concepts`
- 已完成该 packet 的首次真实 execute 对照：
  - [baseline execute](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.sentence_led_no_memory.execute.json)
  - [candidate execute](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.paragraph_led_current.execute.json)
  - [execute diff](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.execute.diff.json)
- 这次真实 execute 的直接观察：
  - baseline：`token_in=2278`，`token_out=1306`，`cost_usd=0.00118636`
  - candidate：`token_in=2782`，`token_out=1331`，`cost_usd=0.00127347`
  - 增量成本很小，但 candidate 明显改善了多处表达：
    - `AI代理` -> `AI智能体`
    - `并非可选之举` -> `不是可选项`
    - `忽略已有的食材` -> `忽略手头已有的食材`
    - `这正是分布式SQL变得至关重要的地方` -> `这就是分布式SQL变得至关重要的地方`
- 当前真实 execute 的结论：
  - 段落级 prompt + chapter memory 的组合已经在真实模型输出上产生了可见提升
  - 当前最强收益来自 `recent_accepted_translations + chapter_concepts`
  - `chapter_brief` 仍未成为关键增益点
  - 新暴露出的主要问题是 `chapter concept memory` 候选质量太噪，例如会混入 `agent might`、`Aakash Gupta Context`、`accomplish these responses agentic` 这类明显不该进入概念记忆的条目
- 因此下一默认 slice 不再是扩大 rerun 范围，而是先做 `concept registry` 候选降噪，再继续单 packet execute 验证。
- 另外，本轮还修平了分析脚本的源码加载与路径稳定性：
  - `export_packet_debug.py`
  - `run_packet_experiment.py`
  - `compare_packet_experiments.py`
  - `scan_packet_experiments.py`
  - `backfill_chapter_memory.py`
- 现在这些脚本都会显式从工作区 `src/` 加载代码，并在入口解析绝对路径，避免“脚本产物落后于当前工作区实现”的假象。
- 已收紧 `chapter concept memory` 的候选提取规则：
  - 只保留更像术语的短语
  - 排除含明显 stopword 的短语
  - 排除以人名/引用片段开头的伪概念
  - 保留 `Agentic AI / generative AI / context engineering / distributed SQL / adaptive agent` 这类高价值概念
- 对应单元回归已补：
  - 保留：`agentic ai`、`distributed sql`、`context engineering`、`generative ai`、`adaptive agent`
  - 排除：`agent might`、`Aakash Gupta Context Engineering`、`accomplish these responses agentic`
- 已扩展 `chapter_memory_backfill.py` 支持 `reset_existing`，用于 supersede 老的 noisy chapter memory，并从现有成功 packet 零 token 重建一份干净版本。
- 对应单章回归已补：即使当前 active snapshot 被手工污染，`reset_existing=True` 也能重建出保留核心概念、排除噪声概念的新章节记忆。
- 进程内直接验证结果已经显示降噪生效：
  - active snapshot 已更新到 `version=100`
  - 同一 packet 的 `chapter_concepts` 已收敛成 `Agentic AI / context engineering / distributed SQL / language model / adaptive agent ...`
  - 噪声项如 `Aakash Gupta Context Engineering` 不再出现在进程内实验结果中
- 当前残余问题：
  - `run_packet_experiment.py` 生成的 reset 工件仍旧显示旧的 `chapter_memory_snapshot_version=50` 和旧 concept list
  - 但进程内 `PacketExperimentService.run()` 已能正确读到 `version=100` 与干净概念集合
  - 说明“脚本工件落盘”和“进程内服务结果”之间仍有一条待补平的不一致链路
  - 这不会影响主链路翻译，但会影响后续分析工位的可靠性，因此下一刀先修这里，再继续做 execute 对照

### 2026-03-18

- 已确认昨天看到的 `chapter_memory_snapshot_version=50` 并不是主链路还在读旧 snapshot，而是旧实验 JSON 没有刷新。现在同一 packet 的脚本 dry-run 和进程内 `PacketExperimentService.run()` 已都稳定读到：
  - `chapter_memory_snapshot_version = 100`
  - `chapter_memory_concept_count = 12`
  - 干净的 `chapter_concepts` 集合
- 为了防止后续再被陈旧工件误导，[packet_experiment.py](/Users/smy/project/book-agent/src/book_agent/services/packet_experiment.py) 已新增：
  - `generated_at`
  - `database_url`
  - `chapter_memory_snapshot_id`
- 已对同一真实 packet `ba917844-c3b9-5689-91ad-f984703dea71` 执行一次新的单 packet execute，产物在：
  - [denoised execute](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.paragraph_led_current.denoised.execute.json)
  - [denoised diff](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.paragraph_led_current.denoised.diff.json)
- 这次 execute 仍然只作用于单 packet，没有触发整章/整书 rerun。实际消耗：
  - `token_in = 2765`
  - `token_out = 1306`
  - `cost_usd = 0.00124208`
- 与旧的 paragraph-led candidate 相比，当前结论是：
  - 降噪后的 concept memory 没有破坏 paragraph-led 的中文自然度提升
  - 多处表达继续保持或略有改善，例如：
    - `忽略手头已有的食材而浪费原料` -> `忽视手头已有的食材而造成浪费`
    - `正是记忆使得...成为可能` -> `正是记忆实现了...`
- 已把 `STALE_CHAPTER_BRIEF` 从纯人工 advisory 推进成可安全自动执行的 packet fallback：
  - review 现在会在 issue evidence 中写入 `packet_ids_seen`
  - rerun plan 对 `REBUILD_CHAPTER_BRIEF + STALE_CHAPTER_BRIEF` 会自动收窄到 packet scope
  - workflow auto-followup 会优先尝试 `UNLOCKED_KEY_CONCEPT` 的零 token 自动锁词；如果自动锁词失败，才会退到同 packet 集合上的 `STALE_CHAPTER_BRIEF` follow-up
- 这条策略已经由单章回归锁住：
  - 当自动锁词成功时，`UNLOCKED_KEY_CONCEPT` 本身就会清掉 stale brief，不需要额外 rerun
  - 当自动锁词失败时，workflow 会安全地执行 `STALE_CHAPTER_BRIEF` 的 packet rerun，只重跑受影响 packet，不扩大到整章
- 当前结论已经收敛：
  - `STALE_CHAPTER_BRIEF` 值得纳入 workflow 级安全 follow-up
  - 但它不应成为第一优先级，也不应独立扩大为 chapter rerun 默认策略
  - 它最合适的定位是“自动锁词失败时的零 token brief rebuild + packet rerun fallback”
- 本轮又进一步收紧了策略边界：
  - `STALE_CHAPTER_BRIEF` 不再出现在通用 auto-followup candidate pool 里
  - 当自动锁词成功时，workflow 只执行 `UNLOCKED_KEY_CONCEPT` 的 packet rerun，不会再额外跑 stale brief
  - 当自动锁词失败时，workflow 才会退到同 packet 集合上的 stale brief fallback
  - 这两条分支现在都已经有单章回归锁住
- 已给正式 `role-style-v2` prompt 补上轻量 `Paragraph Intent Signal`，但这轮先只做了零 token 验证，没有发起新的真实 execute：
  - [context_compile.py](/Users/smy/project/book-agent/src/book_agent/services/context_compile.py) 现在会基于当前 paragraph 文本和 chapter brief 做轻量意图判断，当前支持：
    - `definition`
    - `analogy`
    - `transition`
    - `evidence`
    - `summary`
    - `exposition`
  - [translator.py](/Users/smy/project/book-agent/src/book_agent/workers/translator.py) 的 `role-style-v2 / role-style-memory-v2 / role-style-brief-v3` prompt 现在都会显示 `Paragraph Intent Signal:`
  - 对应单测已经锁住：
    - `Context compiler` 能把定义段推成 `paragraph_intent = definition`
    - `role-style-v2` prompt 会显式出现 `Intent: definition`
  - 真实 packet 的零 token dry-run 也已经确认：
    - [ba917844-c3b9-5689-91ad-f984703dea71.role-style-v2.intent.dryrun.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.role-style-v2.intent.dryrun.json)
    - 该 packet 的 `style_constraints` 已包含：
      - `paragraph_intent = definition`
      - `paragraph_intent_hint = Treat this as concept-definition prose...`
  - 当前判断：
    - 这是一个低风险、可直接增强生产默认 prompt 的信号层
    - 下一步最值得做的是只对固定 packet 做一次小范围真实 execute，对比它是否比当前 `role-style-v2` 进一步改善定义段中文自然度
    - `第二章` -> `第2章`
  - 但核心术语仍残留一个更值得处理的问题：`agentic AI` 当前在这个 packet 中稳定成了 `智能体AI`，说明下一刀更应该进入“术语裁决/概念锁定”，而不是继续盲目扩大概念候选提取规则。
- 已新增零 token 的章节概念锁定能力：
  - 服务：[chapter_concept_lock.py](/Users/smy/project/book-agent/src/book_agent/services/chapter_concept_lock.py)
  - 脚本：[lock_chapter_concept.py](/Users/smy/project/book-agent/scripts/lock_chapter_concept.py)
- 这条能力允许我们在不重翻整章的前提下，直接把 `source_term -> canonical_zh` 写入 `chapter_translation_memory`，然后只对单 packet 做真实 rerun，验证术语裁决对译文的影响。
- 已补单元回归，确认：
  - 锁定后会 supersede 当前章节记忆快照
  - 后续 packet prompt 会显式携带 `context engineering => 上下文工程 (locked ...)`
- 本轮没有对 live 书的真实章节记忆写入任何主观术语裁决，只把“锁定能力”补成了可用工位；后续若要给 `context engineering / agentic AI` 下注，仍然会先做单 packet 验证，再决定是否进入主链路。
- 已新增“单 packet 临时 concept override”能力：
  - 不写 live DB
  - 只对实验请求临时注入 `source_term -> canonical_zh`
  - 适合先验证术语裁决，再决定是否写回章节记忆
- 对真实 packet `2e26e803-5d84-52ae-9b01-c5b8e07a7d93` 已完成首次 override execute：
  - 产物：[context-engineering execute](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.paragraph_led_context_engineering_locked.execute.json)
  - override：`context engineering = 上下文工程`
  - 实际消耗：`token_in = 2570`，`token_out = 437`，`cost_usd = 0.00083863`
- 这次真实结果已经证明：
  - 核心术语 `context engineering` 可以通过临时 override 稳定拉到“上下文工程”
  - 译文中原本的“情境工程”已经被消掉
- 已在 live chapter memory 中将 `agentic AI -> 智能体式AI` 做零 token 锁定写回：
  - 工件：[agentic-ai-lock.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/agentic-ai-lock.json)
  - `snapshot_version = 101`
  - 这一步不是只改实验 prompt，而是把裁决真正写入了当前章节的活动记忆
- 已对后续真实 packet `50e768de-0f32-5421-9a77-8b1e0182f624` 做单 packet execute 验证：
  - 工件：[post-lock execute](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/50e768de-0f32-5421-9a77-8b1e0182f624.post_lock.execute.json)
  - `token_in = 1843`
  - `token_out = 181`
  - `cost_usd = 0.00052755`
  - prompt 已显式带上 `Agentic AI => 智能体式AI (locked, seen=18)`
  - 输出已从“智能体AI通过吸收反馈持续改进...”切到“智能体式AI通过吸收反馈持续改进...”
- 已把“锁定章节概念”进一步桥接到主链路术语层：
  - [context_compile.py](/Users/smy/project/book-agent/src/book_agent/services/context_compile.py) 现会把带 `canonical_zh` 的章节概念合并进 `relevant_terms`
  - [chapter_concept_lock.py](/Users/smy/project/book-agent/src/book_agent/services/chapter_concept_lock.py) 现会同步 upsert 章节级 `TermEntry`
  - [packet_experiment.py](/Users/smy/project/book-agent/src/book_agent/services/packet_experiment.py) 已补 `raw_relevant_term_count / compiled_relevant_term_count`
- 已完成零 token post-lock dry-run 验证：
  - 工件：[post-lock dryrun](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/50e768de-0f32-5421-9a77-8b1e0182f624.post_lock.dryrun.json)
  - `raw_relevant_term_count = 0`
  - `compiled_relevant_term_count = 1`
  - prompt 中已出现 `Locked and Relevant Terms:` 和 `Agentic AI => 智能体式AI`
- 已补 review 闭环：若某章节概念已经有锁定的活动 `TermEntry`，review 不再继续报 `UNLOCKED_KEY_CONCEPT`
  - 对应回归在 [test_persistence_and_review.py](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)
  - 这保证“概念候选 -> 锁定 -> 术语实体 -> review 收敛”已经形成最小闭环，不会反复对同一已裁决概念报 advisory
- 已进一步补全“锁定术语反查旧译文”的 review 闭环：
  - 新增单章回归，使用 `Agentic AI` 的旧译文“智能体AI”作为样本
  - 在章节中零 token 锁定 `agentic AI -> 智能体式AI` 后，review 会自动对旧译文报 `TERM_CONFLICT`
  - 同时不会再重复报 `UNLOCKED_KEY_CONCEPT`
  - 这意味着“候选概念 -> 锁定 -> 主链路 prompt -> review 抓存量偏差”已经完成最小闭环
- 已将 `context engineering -> 上下文工程` 真正写入 live chapter memory：
  - 工件：[context-engineering-lock.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/context-engineering-lock.json)
  - `snapshot_version = 102`
  - post-lock dry-run 工件：[2e26...post_lock.dryrun.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.post_lock.dryrun.json)
  - 当前同一 packet 的 `compiled_relevant_term_count = 2`
  - prompt 已明确出现 `Locked and Relevant Terms:` 与 `context engineering => 上下文工程`
- 已完成该定义性 packet 的 live-lock single-packet execute：
  - 工件：[2e26...post_lock.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.post_lock.execute.json)
  - 对照 diff：[2e26...post_lock.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.post_lock.diff.json)
  - 实际消耗：`cost_usd = 0.00084129`
- 这次 live-lock execute 的结论是双面的：
  - 正向：`context engineering` 已经稳定进入“上下文工程”，说明概念锁定链路对核心术语有效
  - 负向：同一 packet 里的 `weight of evidence` 又回退成了“证据权重表明”，说明单概念锁定不足以解决更广义的直译腔问题
  - 因而下一默认 slice 不再是继续做更多单词级锁定，而是优先扩大 `STYLE_DRIFT` 的检测与纠偏能力
- 已落 `STYLE_DRIFT` 第二阶段最小扩展：
  - `weight_of_evidence_literal` 现已覆盖 `证据权重表明` 这类真实回退变体，不再只抓“证据的分量表明”
  - 新增 `contextually_accurate_outputs_literal`，用于抓 `上下文更准确的输出`
- 对应单章回归已更新：
  - [LiteralismWorker](/Users/smy/project/book-agent/tests/test_persistence_and_review.py) 现在使用更贴近真实 packet 的坏味道样本
  - review 会稳定产出至少 3 个 `STYLE_DRIFT` issue
  - `preferred_hint` 现在同时覆盖 `上下文工程` 和 `更符合上下文的输出`
- 这一轮仍然没有发起任何新的 packet execute；只做了单章级 review 回归，确认新规则已经把真实 live-lock packet 暴露出来的两类回退纳入检测面
- 已完成 3 组提示词 A/B/C 矩阵实验，固定 packet 为：
  - `670447b0-0bca-5df9-aea8-4f30cbcd8b1f`
  - `ba917844-c3b9-5689-91ad-f984703dea71`
  - `2e26e803-5d84-52ae-9b01-c5b8e07a7d93`
- 实验工件目录：
  - [prompt-profile-matrix](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix)
  - 汇总：[prompt-profile-matrix.summary.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/prompt-profile-matrix.summary.json)
- 三组 profile 定义：
  - `current`：当前主提示词
  - `role-style-v2`：只升级角色与风格目标
  - `role-style-memory-v2`：升级角色 + 段落优先 + 结构化问题处理 + 强化 concept memory 使用
- 当前矩阵结论：
  - alignment coverage：9/9 全部保持 `1.0`
  - 术语稳定性：`agentic AI / context engineering` 在这 3 个 packet 上都已稳定，不是当前 profile 差异主因
  - `STYLE_DRIFT`：只有 `2e26...` 仍有 2 个命中，三组 profile 都未显著降低，说明单靠提示词升级不足以消掉这类 literalism
  - 中文自然度：`role-style-v2` 整体最佳；`role-style-memory-v2` 在定义段与长段上更容易把段落打碎，收益不如预期
- 费用汇总：
  - `current = 0.00173770 USD`
  - `role-style-v2 = 0.00086504 USD`
  - `role-style-memory-v2 = 0.00111872 USD`
- 当前判断：
  - 下一阶段最值得推进的是把 `role-style-v2` 作为新的实验基线
  - `role-style-memory-v2` 暂不进入主链路，因为它没有额外降低 `STYLE_DRIFT`，却在长段上更容易产生碎句感
  - 同一 packet 里还顺带把 “证据的分量表明” 提升成了 “大量证据表明”
- 当前残余也更清楚了：
  - 这次 override 还没有触及 `agentic AI`
  - “情境更准确的输出” 这种词组仍然残留在段尾，说明下一刀可以有两种方向：
    - 继续做术语裁决实验，先把 `agentic AI` 定住
    - 或扩大 `STYLE_DRIFT`，开始抓 `contextually accurate -> 情境更准确` 这种次一级直译腔
- 已在更短的 recipe metaphor packet `670447b0-0bca-5df9-aea8-4f30cbcd8b1f` 上完成 `agentic AI` 的三候选真实对照：
  - [baseline](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/670447b0-0bca-5df9-aea8-4f30cbcd8b1f.baseline.execute.json)
  - [智能体AI](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/670447b0-0bca-5df9-aea8-4f30cbcd8b1f.agentic_ai.execute.json)
  - [智能体式AI](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/670447b0-0bca-5df9-aea8-4f30cbcd8b1f.agentic_style.execute.json)
  - [代理式AI](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/670447b0-0bca-5df9-aea8-4f30cbcd8b1f.proxy_style.execute.json)
- 这组对照的当前结论不是“已经能拍板”，而是：
  - baseline 在这个 packet 上本来就会自然收敛到“智能体AI”
  - `智能体AI` override 没带来明显额外收益
  - `智能体式AI` 在概念上更明确，但语感仍有轻微术语化
  - `代理式AI` 读起来顺，但更接近当前我们想摆脱的 literal policy
- 因此目前更稳妥的判断是：
  - recipe metaphor packet 不足以最终裁决 `agentic AI`
  - 真正该作为下一轮裁决依据的，仍应是定义性更强的 `ba917844-c3b9-5689-91ad-f984703dea71`
- 已在定义性更强的 `ba917844-c3b9-5689-91ad-f984703dea71` packet 上完成第二轮 `agentic AI` 三候选真实对照：
  - [智能体AI](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.agentic_ai.execute.json)
  - [智能体式AI](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.agentic_style.execute.json)
  - [代理式AI](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.proxy_style.execute.json)
  - 对照基线：[baseline_current](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.paragraph_led_current.denoised.execute.json)
- 当前结论已经明显收敛：
  - `智能体AI`：延续当前默认输出，概念够短，但更像内部缩写，不够出版化
  - `代理式AI`：易懂，但明显更贴 literal policy，和我们当前整体质量方向不完全一致
  - `智能体式AI`：在定义段里概念边界最清楚，而且把整段压成 5 个 target segments 后仍保持 `12/12` 句覆盖、`0` 低置信
- 因此，到目前为止 `agentic AI -> 智能体式AI` 已经是当前最值得推进的首选候选，但仍有一个边界：
  - 这还只是 packet 级实验结论，尚未写回 live 章节记忆，也还没有在后续 packet 上验证连锁效果
- 这条边界本轮已经被打穿：
  - 已通过 [lock_chapter_concept.py](/Users/smy/project/book-agent/scripts/lock_chapter_concept.py) 对 live chapter memory 执行零 token 锁定写回：
    - [agentic-ai-lock.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/agentic-ai-lock.json)
    - `snapshot_version = 101`
    - `source_term = agentic AI`
    - `canonical_zh = 智能体式AI`
- 写回后，已在更下游、更干净的 packet `50e768de-0f32-5421-9a77-8b1e0182f624` 上做了真实 execute 验证：
  - [post_lock execute](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/50e768de-0f32-5421-9a77-8b1e0182f624.post_lock.execute.json)
  - 实际消耗：`token_in = 1843`，`token_out = 181`，`cost_usd = 0.00052755`
- 这次连锁验证已经证明两件事：
  - prompt 确实显式携带了 `Agentic AI => 智能体式AI (locked, seen=18)`
  - 下游输出已从原来的  
    `智能体AI通过吸收反馈持续改进...`  
    收敛成  
    `智能体式AI通过吸收反馈持续改进...`
- 所以到当前为止，`agentic AI -> 智能体式AI` 已经不只是 packet 级候选，而是完成了：
  - 候选比较
  - 零 token 锁定写回
  - 单 packet 下游连锁验证
- 当前残余只剩最后一层决策：要不要把这条裁决正式提升为主链路默认策略，以及是否把类似流程产品化到更多高价值概念。
- 已将 `role-style-v2` 提升为实验工位默认提示词基线：
  - [PacketExperimentOptions](/Users/smy/project/book-agent/src/book_agent/services/packet_experiment.py) 默认 `prompt_profile` 已从 `current` 切到 `role-style-v2`
  - [run_packet_experiment.py](/Users/smy/project/book-agent/scripts/run_packet_experiment.py) 的 `--prompt-profile` 默认值也已同步切换
  - 对应回归已更新，dry-run 产物默认会记录 `prompt_profile = role-style-v2`
- 这一刀仍然只作用于实验工位，不直接改动正式主链路默认提示词；目标是后续所有 packet 级实验先统一建立在当前最优实验基线上，再继续比较 `STYLE_DRIFT` 与术语策略的增益。
- 已继续扩展 `STYLE_DRIFT` 第二阶段规则面：
  - `context engineering` 现会同时抓 `情境工程 / 语境工程`
  - `weight of evidence` 现会同时抓 `证据权重显示 / 证据重量证明` 等更宽的字面直译变体
  - `contextually accurate outputs` 现可抓到 `更具上下文准确性的输出结果` 这类真实坏味道
  - issue evidence 现会写出 `matched_target_excerpt`，后续做 packet rerun 决策时不必再从整段译文里人工定位命中片段
- 已接入最小“定向 rerun 提示闭环”：
  - [RerunPlan](/Users/smy/project/book-agent/src/book_agent/orchestrator/rerun.py) 现在会从 review issue 自动提取两类纠偏信息：
    - `TERM_CONFLICT -> concept_overrides`
    - `STYLE_DRIFT -> style_hints`
  - [RerunService](/Users/smy/project/book-agent/src/book_agent/services/rerun.py) 在 packet rerun 时会把这些信息传给 [TranslationService](/Users/smy/project/book-agent/src/book_agent/services/translation.py)
  - [TranslationService](/Users/smy/project/book-agent/src/book_agent/services/translation.py) 会把 `concept_overrides` 编译进章节上下文，并把 `style_hints` 注入 packet `open_questions`
  - [translator.py](/Users/smy/project/book-agent/src/book_agent/workers/translator.py) 现在会显式渲染 `Open Questions and Rerun Hints:`，所以 rerun 时模型能直接看到“该往哪个术语/表达收敛”
- 这一刀仍然是 packet 级、零 schema 迁移的最小闭环：
  - 没有新增数据库表
  - 没有发起整章/整书 rerun
  - 当前只先把 review -> rerun -> prompt 这条纠偏信息链打通
- 实验工位 [PacketExperimentOptions](/Users/smy/project/book-agent/src/book_agent/services/packet_experiment.py) 与 [run_packet_experiment.py](/Users/smy/project/book-agent/scripts/run_packet_experiment.py) 现已支持 `rerun_hints`，所以可以对真实 packet 做低成本 execute，而不必走整条 live rerun 状态机
- 已在真实 `STYLE_DRIFT` packet `2e26e803-5d84-52ae-9b01-c5b8e07a7d93` 上完成首个 hint-rerun execute：
  - 工件：[rerun_hints.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.rerun_hints.execute.json)
  - diff：[rerun_hints.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.rerun_hints.diff.json)
  - 实际消耗：`cost_usd = 0.00088206`
- 这次真实结果已经证明“定向 rerun 提示”不是空转：
  - baseline 还命中：
    - `weight_of_evidence_literal -> 证据权重表明`
    - `contextually_accurate_outputs_literal -> 上下文更准确的输出`
  - 注入两个 rerun hints 后，candidate 已收敛为：
    - `大量证据表明`
    - `更符合上下文的输出`
  - 用当前 `STYLE_DRIFT` 规则对 candidate 复扫，命中已从 `2 -> 0`
- 当前结论已经比上一轮更强：
  - `STYLE_DRIFT` 不仅能“发现问题”
  - 还已经能通过 packet 级 rerun hints 在真实样本上把问题压下去
  - 下一阶段更值得做的，不再是继续证明这条链可行，而是决定要不要把这条“preferred_hint -> rerun_hints”正式纳入 live rerun 默认策略
- live rerun 默认策略也已补齐关键缺口：
  - [OpsRepository](/Users/smy/project/book-agent/src/book_agent/infra/repositories/ops.py) 现可列出同 packet 未决 issue
  - [RerunService](/Users/smy/project/book-agent/src/book_agent/services/rerun.py) 会在 packet scope rerun 前自动聚合 sibling issue 的 `concept_overrides + style_hints`
  - 因此像 `2e26...` 这种同一 packet 上同时存在 `weight_of_evidence_literal` 和 `contextually_accurate_outputs_literal` 的情况，不再需要连续 rerun 两次
- 已补 live rerun 端到端回归：
  - [test_packet_style_drift_action_rerun_uses_aggregated_hints_and_resolves_packet_issues](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)
  - 流程覆盖：`review -> action execute -> rerun -> re-review`
  - 结果：单次 packet rerun 后，目标 packet 上剩余 `STYLE_DRIFT` issues 已降为 `0`
- `TERM_CONFLICT` 现在也补到了同等级的最小 live 闭环，而且仍然保持最小作用域：
  - [ReviewService](/Users/smy/project/book-agent/src/book_agent/services/review.py) 现在对 `TERM_CONFLICT + packet_id` 会优先生成 `packet scope` 的 `UPDATE_TERMBASE_THEN_RERUN_TARGETED`
  - 这条收窄只作用于已锁定术语冲突，不影响 `UNLOCKED_KEY_CONCEPT` 这类仍应保持 chapter scope 的 termbase 更新动作
- 已新增单 packet 级 `TERM_CONFLICT` 端到端回归：
  - [test_packet_term_conflict_action_rerun_uses_locked_concept_and_resolves_packet_issues](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)
  - 使用单 paragraph、双句同 packet 的 `Agentic AI` 样本，先产出 `智能体AI`，再零 token 锁定 `agentic AI -> 智能体式AI`
  - 流程覆盖：`review -> action execute -> rerun -> re-review`
  - 结果：目标 packet 的 `TERM_CONFLICT` 已能在单次 rerun 后清零，且 `rerun_plan.concept_overrides` 会稳定携带 `agentic AI`
- 到当前为止，两条最关键的 packet 级质量闭环都已站稳：
  - `STYLE_DRIFT -> preferred_hint -> aggregated live rerun -> issue_count drops to 0`
  - `TERM_CONFLICT -> locked concept override -> packet rerun -> issue_count drops to 0`
- 当前残余已进一步收敛：
  - rerun 主链路的 packet 级纠偏能力已经足够强，下一刀更值得做的是决定哪些实验能力该正式提升为生产默认策略
  - 更具体地说，下一阶段不必再继续证明“packet rerun 是否可行”，而应开始收敛：
    - `role-style-v2` 是否进入正式翻译主提示词
    - `locked concept -> relevant_terms` 是否作为生产默认行为保留
    - `STYLE_DRIFT preferred_hint` 是否默认进入 live rerun prompt
- 这条“实验能力 -> 主链路默认”已经开始落地：
  - [Settings](/Users/smy/project/book-agent/src/book_agent/core/config.py) 新增 `translation_prompt_profile`
  - 默认值已设为 `role-style-v2`
  - [LLMTranslationWorker](/Users/smy/project/book-agent/src/book_agent/workers/translator.py) 现已显式接收 `prompt_profile`，默认也是 `role-style-v2`
  - [build_translation_worker](/Users/smy/project/book-agent/src/book_agent/workers/factory.py) 会把 `translation_prompt_profile` 注入正式 worker，且写入 `runtime_config["prompt_profile"]`
- 这意味着：
  - packet experiment 默认基线和正式英译中主链路默认提示词现在已经统一
  - 后续继续做 packet 级 A/B 时，不再会出现“实验环境表现更好，但正式 worker 还在用旧 prompt profile”的偏差
- 当前已验证的最小生产默认行为：
  - 正式 `LLMTranslationWorker` 默认生成的 `system_prompt` 已切到 `role-style-v2` 对应的“senior technical translator and localizer”角色设定
  - `translation_run.model_config_json` 中会显式记录 `prompt_profile = role-style-v2`
  - 因此后续即使只看落库 run 元数据，也能分辨这一轮翻译到底用了哪套提示词基线
- 到当前为止，正式主链路已完成的默认策略升级包括：
  - `role-style-v2` 成为正式英译中 worker 默认 prompt profile
  - packet rerun 默认会聚合同 packet sibling issue 的 `style_hints + concept_overrides`
  - 已锁定章节概念会自动并入 `relevant_terms`
- 当前残余继续收敛为两类更高杠杆问题：
  - 是否把更多实验工位里已证明有效的“概念裁决/风格提示”提升为生产默认 heuristics，而不是仍依赖人工触发
  - workflow 级 auto-followup 现在已覆盖 `STYLE_DRIFT + TERM_CONFLICT` 两条最安全的 packet 路径；下一阶段更适合评估是否要继续扩到其它 packet 级 advisory

### 2026-03-18

- workflow 级 review 自动纠偏已从 `STYLE_DRIFT` 安全扩到 packet 级 `TERM_CONFLICT`：
  - 当前允许自动执行的 blocking issue 只新增这一类，而且仍然要求：
    - `issue_type == TERM_CONFLICT`
    - `scope_type == PACKET`
    - evidence 中存在明确 `expected_target_term`
  - 其它 blocking issue 仍不会被 workflow 自动 follow-up 触发。
- 已补新的单章回归 [test_workflow_review_auto_executes_packet_term_followups](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)：
  - 样本：单 packet、双句 `Agentic AI`
  - 流程：`translate -> lock concept -> review(auto_execute_packet_followups=True) -> re-review`
  - 结果：workflow review 阶段单次 auto-followup 即可将 packet 上的 `TERM_CONFLICT` 清零，章节状态直接回到 `qa_checked`。
- 本轮顺手修复了 [ChapterConceptAutoLockService](/Users/smy/project/book-agent/src/book_agent/services/chapter_concept_autolock.py) 在 `slots=True` 下的 init-only repository/service 字段初始化缺口，避免后续单测因 attribute slot 缺失而误报。
- 本轮验证：
  - `uv run python -m unittest tests.test_persistence_and_review tests.test_translation_worker_abstraction tests.test_rule_engine` -> `62 tests OK`
  - `uv run python -m py_compile src/book_agent/services/workflows.py src/book_agent/services/chapter_concept_autolock.py tests/test_persistence_and_review.py`
- 当前结论进一步收敛：
  - workflow 级 auto-followup 已能默认处理两条最有价值、最安全的 packet 路径：
    - `STYLE_DRIFT -> RERUN_PACKET`
    - `TERM_CONFLICT -> UPDATE_TERMBASE_THEN_RERUN_TARGETED`
  - 下一阶段不需要再证明“workflow 自动纠偏是否可行”，而应开始收敛它的边界：继续停在这两类安全 packet issue，还是谨慎扩到更多 packet 级 advisory。

### 2026-03-18

- workflow 级 auto-followup 的候选排序已从“按 packet 命中数”升级成“先看 blocking / issue type，再看 packet 命中数”：
  - 当前排序语义：
    - blocking `TERM_CONFLICT`
    - 其它 `TERM_CONFLICT`
    - `STYLE_DRIFT`
  - 仍然保持 packet 去重，所以单次 auto-followup 预算只会消耗在一个 packet 上。
- 已新增混合样本 [test_workflow_review_auto_followups_prioritize_packet_term_conflicts_before_style_drift](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)：
  - 同一章同时包含 `Agentic AI` 术语冲突和 literalism 风格漂移
  - 在 `max_auto_followup_attempts = 1` 下，workflow 会优先救 `TERM_CONFLICT`
  - 验证口径不是“所有 term issue 一次清零”，而是：
    - 首次执行的 auto-followup 必定是 `TERM_CONFLICT`
    - rerun 后 `TERM_CONFLICT` 数量下降
    - `STYLE_DRIFT` 仍然保留，证明预算优先级确实生效
- 本轮验证：
  - `uv run python -m unittest tests.test_persistence_and_review tests.test_translation_worker_abstraction tests.test_rule_engine` -> `63 tests OK`
  - `uv run python -m py_compile src/book_agent/services/workflows.py tests/test_persistence_and_review.py`

### 2026-03-18

- `chapter concept memory` 的统计语义已经纠偏：
  - [TranslationService](/Users/smy/project/book-agent/src/book_agent/services/translation.py) 现在会维护显式的 `packet_ids_seen`
  - `times_seen` 不再按“这个概念被写回了多少次”累计，而是按“出现过多少个唯一 packet”累计
  - 这避免了同一 packet 多次 rerun 把 `UNLOCKED_KEY_CONCEPT` 人工抬高成假阳性
- [ReviewService](/Users/smy/project/book-agent/src/book_agent/services/review.py) 现在会把 `packet_ids_seen` 一起写进 `UNLOCKED_KEY_CONCEPT` evidence；后续如果要把这类问题收窄成真正的 targeted rerun，已经有干净的影响范围底座。
- 对应回归：
  - [test_merge_active_concepts_counts_unique_packets_only](/Users/smy/project/book-agent/tests/test_translation_worker_abstraction.py)
  - [test_review_reports_unlocked_key_concept_from_chapter_memory](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)
  - 同时这条语义修正也让 `test_workflow_review_auto_executes_packet_style_followups` 的正确终态从“残留一个伪 `UNLOCKED_KEY_CONCEPT`”收敛成了真正的 `0 issue`
- 本轮验证：
  - `uv run python -m unittest tests.test_persistence_and_review tests.test_translation_worker_abstraction tests.test_rule_engine` -> `64 tests OK`
  - `uv run python -m py_compile src/book_agent/services/translation.py src/book_agent/services/review.py tests/test_translation_worker_abstraction.py tests/test_persistence_and_review.py`

### 2026-03-18

- [ChapterConceptAutoLockService](/Users/smy/project/book-agent/src/book_agent/services/chapter_concept_autolock.py) 现在会优先尝试零 token 的 [HeuristicConceptResolver](/Users/smy/project/book-agent/src/book_agent/services/chapter_concept_autolock.py)：
  - 先从章节已接受译文的对齐示例里抽“稳定共识术语”
  - 命中明确锚点时直接锁词，不消耗模型 token
  - 只有启发式拿不准时，默认 resolver 才会退回模型兜底
- 当前启发式优先支持的高信号锚点包括：
  - `称为X / 称作X / 叫做X`
  - `X决定 / X通过 / X指的是 / X依赖于 ...`
  - `X::...` 这类调试/示例格式
- 已新增零 token 单章回归：
  - [test_chapter_concept_auto_lock_service_heuristically_locks_consistent_examples_without_token_usage](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)
  - 使用 `Context engineering` 的一致译文样本，验证 auto-lock 可直接锁成 `上下文工程`
  - `token_in = 0`, `token_out = 0`
- 这条能力现在的意义不是立刻扩大 workflow auto-followup 范围，而是给 `UNLOCKED_KEY_CONCEPT` 提供一条“先尝试零 token 术语裁决”的真实落地点，避免一上来就用模型补决策。
- 本轮验证：
  - `uv run python -m unittest tests.test_persistence_and_review tests.test_translation_worker_abstraction tests.test_rule_engine` -> `65 tests OK`
  - `uv run python -m py_compile src/book_agent/services/chapter_concept_autolock.py tests/test_persistence_and_review.py`

### 2026-03-18

- `UNLOCKED_KEY_CONCEPT` 的 targeted rerun 已从“单 packet 特例”推进到“多 packet 受影响集合”：
  - [build_rerun_plan](/Users/smy/project/book-agent/src/book_agent/orchestrator/rerun.py) 现在会优先读取 issue evidence 里的 `packet_ids_seen`
  - 对 `UPDATE_TERMBASE_THEN_RERUN_TARGETED + UNLOCKED_KEY_CONCEPT`，rerun plan 会直接收窄成 `scope_type = PACKET`
  - scope ids 不再退回整章，而是只包含真正命中过该概念的 packet 集合
- [IssueActionExecutor](/Users/smy/project/book-agent/src/book_agent/services/actions.py) 现在按“投影后的 rerun plan”执行 invalidation：
  - 即使 action 在库里仍是 chapter scope，真正失效化和 rerun 的也只会是 `packet_ids_seen`
  - 这让“先 chapter 级锁词，再 targeted rerun”第一次真正落成了低 token 成本的实现，而不是语义上说 targeted、实际仍整章失效化
- [DocumentWorkflowService](/Users/smy/project/book-agent/src/book_agent/services/workflows.py) 也同步切到了 rerun-plan 驱动：
  - auto-followup 候选筛选现在按 projected packet scope 判断，而不是死看 `action.scope_type`
  - 对 `UNLOCKED_KEY_CONCEPT`，当前安全边界是：`packet_ids_seen` 非空且数量不超过 `3`
  - 这意味着小范围、多 packet 的概念问题也可以自动锁词并只 rerun 受影响 packet，而不会误伤整章
- 同时修掉了一个新暴露出来的统计 bug：
  - [TranslationService](/Users/smy/project/book-agent/src/book_agent/services/translation.py) 现在新增 `packet_mention_counts`
  - `mention_count` 不再因为同一个 packet 的 rerun 被重复累加
  - 这修掉了 style rerun 之后凭空长出伪 `UNLOCKED_KEY_CONCEPT` 的问题
- 新增/更新的关键回归：
  - [test_unlocked_key_concept_action_targets_only_packets_seen](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)
  - [test_workflow_review_auto_executes_multi_packet_unlocked_concept_followups_without_chapter_rerun](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)
  - [test_merge_active_concepts_counts_unique_packets_only](/Users/smy/project/book-agent/tests/test_translation_worker_abstraction.py)
  - 同时 [test_workflow_review_auto_executes_packet_style_followups](/Users/smy/project/book-agent/tests/test_persistence_and_review.py) 再次稳定回到 `0 issue`
- 本轮验证：
  - `uv run python -m unittest tests.test_persistence_and_review tests.test_translation_worker_abstraction tests.test_rule_engine` -> `72 tests OK`
  - `uv run python -m py_compile src/book_agent/services/translation.py src/book_agent/orchestrator/rerun.py src/book_agent/services/actions.py src/book_agent/services/workflows.py tests/test_persistence_and_review.py tests/test_translation_worker_abstraction.py`
- 当前结论继续收敛：
  - `TERM_CONFLICT`、`STYLE_DRIFT`、小范围 `UNLOCKED_KEY_CONCEPT` 三条 packet 路径现在都已经具备“锁定/提示 -> targeted rerun -> re-review 收敛”的工作流闭环
  - 下一阶段更值钱的问题不再是“能不能自动纠偏”，而是“哪些 prompt/style/brief 改造值得正式提升为生产默认策略”

### 2026-03-18

- [ChapterBriefBuilder](/Users/smy/project/book-agent/src/book_agent/domain/context/builders.py) 已从“前 3 句摘要”升级成零 token 的代表句摘要器：
  - 仍然保留首句，避免章节开头语境丢失
  - 会优先从后半章挑高信号概念句（如 `context / memory / distributed SQL / agentic`）
  - 再补一个代表句，尽量覆盖章节前后段落，而不是把 brief 锁死在开头
- 这条改造的目标不是直接解决所有 `STALE_CHAPTER_BRIEF`，而是先把“后半章出现的高信号核心概念天然进不去 brief”这个系统性弱点收掉。
- 新增/更新的正反样本：
  - [test_bootstrap_pipeline_chapter_brief_captures_late_high_signal_concept](/Users/smy/project/book-agent/tests/test_bootstrap_pipeline.py)
    - 使用真实风格的 late-concept 章节样本
    - 验证 brief 现在会包含后半章的 `Context engineering determines how context is created.`
  - [test_review_skips_stale_chapter_brief_when_late_high_signal_concept_is_already_in_summary](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)
    - 验证高信号后置概念进入 brief 后，不再误报 `STALE_CHAPTER_BRIEF`
  - [test_review_reports_stale_chapter_brief_when_late_concept_is_missing](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)
    - 保留负样本，但改成更严格的 `adaptive agent` 晚出场场景
    - 证明 stale 检测本身没有被“更聪明的 brief”误伤掉
- 本轮验证：
  - `uv run python -m unittest tests.test_bootstrap_pipeline tests.test_persistence_and_review tests.test_translation_worker_abstraction tests.test_rule_engine` -> `77 tests OK`
  - `uv run python -m py_compile src/book_agent/domain/context/builders.py tests/test_bootstrap_pipeline.py tests/test_persistence_and_review.py`
- 当前结论进一步收敛：
  - `chapter brief` 现在已经从纯开头摘要，提升成了零 token 的“章节代表句”摘要
  - 这条能力对新翻译、packet rerun、chapter memory compile 都是直接收益
  - 下一阶段更值得推进的是：要么把 `STALE_CHAPTER_BRIEF` 也纳入 workflow 级安全 follow-up，要么继续升级 worker prompt，把新的 brief / concept memory 更深地转化成译文风格收益

### 2026-03-18

- `role-style-brief-v3` 的实验型提示词已经接进 [translator.py](/Users/smy/project/book-agent/src/book_agent/workers/translator.py) 和 [run_packet_experiment.py](/Users/smy/project/book-agent/scripts/run_packet_experiment.py)：
  - 新 profile：`role-style-brief-v3`
  - 核心新增：
    - `Memory and Ambiguity Handling` 更明确要求利用 `chapter brief + concept memory + previous accepted translations`
    - 新增 `Paragraph Intent Priorities`
    - system role 升级为 `publication-grade English-to-Chinese translator and localizer`
- 单测已经覆盖并通过：
  - [test_build_translation_prompt_request_supports_prompt_profiles](/Users/smy/project/book-agent/tests/test_translation_worker_abstraction.py)
  - [test_build_translation_prompt_request_includes_open_questions_and_rerun_hints](/Users/smy/project/book-agent/tests/test_translation_worker_abstraction.py)
  - `uv run python -m unittest tests.test_translation_worker_abstraction` -> `30 tests OK`
- 已为 3 个固定 packet 生成零 token dry-run 工件：
  - [670447b0-0bca-5df9-aea8-4f30cbcd8b1f.role-style-v2.dryrun.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/670447b0-0bca-5df9-aea8-4f30cbcd8b1f.role-style-v2.dryrun.json)
  - [670447b0-0bca-5df9-aea8-4f30cbcd8b1f.role-style-brief-v3.dryrun.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/670447b0-0bca-5df9-aea8-4f30cbcd8b1f.role-style-brief-v3.dryrun.json)
  - [ba917844-c3b9-5689-91ad-f984703dea71.role-style-v2.dryrun.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/ba917844-c3b9-5689-91ad-f984703dea71.role-style-v2.dryrun.json)
  - [ba917844-c3b9-5689-91ad-f984703dea71.role-style-brief-v3.dryrun.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/ba917844-c3b9-5689-91ad-f984703dea71.role-style-brief-v3.dryrun.json)
  - [2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.dryrun.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.dryrun.json)
  - [2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-brief-v3.dryrun.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-brief-v3.dryrun.json)
- 对应的结构化 diff 也已落盘：
  - [670447b0-0bca-5df9-aea8-4f30cbcd8b1f.role-style-brief-v3.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/670447b0-0bca-5df9-aea8-4f30cbcd8b1f.role-style-brief-v3.diff.json)
  - [ba917844-c3b9-5689-91ad-f984703dea71.role-style-brief-v3.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/ba917844-c3b9-5689-91ad-f984703dea71.role-style-brief-v3.diff.json)
  - [2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-brief-v3.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-brief-v3.diff.json)
- 当前 dry-run 结论：
  - 上下文编译层没有变化：`chapter_brief / previous_translation_count / chapter_concept_count` 都保持不变
  - 真正变化发生在 prompt 形状：
    - system prompt 更强调 `publication-grade`、`chapter intent`、`concept continuity`
    - user prompt 新增了 `Memory and Ambiguity Handling` 与 `Paragraph Intent Priorities`
  - 这意味着 `role-style-brief-v3` 目前是“更激进的提示词层升级”，不是新的 memory/context 数据路径
- 真实 execute 暂时被外部 provider 阻断：
  - 对 3 个固定 packet 发起 `role-style-brief-v3 --execute` 时，provider 返回 `HTTP 402 / Insufficient Balance`
  - 这不是代码回归，而是额度问题；因此本轮只收口到了单测 + dry-run 工件
- 当前收敛结论：
  - `role-style-brief-v3` 还不能提升到生产默认
  - 但实验工位、packet 基线和 diff 工件已经准备完毕
  - 一旦 provider 额度恢复，下一步只需要继续对这 3 个固定 packet 做 execute 对照，就能判断它是否真的优于当前生产默认 `role-style-v2`

### 2026-03-18

- provider 额度恢复后，已完成 `role-style-brief-v3` 的真实 execute，对象仍然严格限制在 3 个固定 packet：
  - [670447b0-0bca-5df9-aea8-4f30cbcd8b1f.role-style-brief-v3.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/670447b0-0bca-5df9-aea8-4f30cbcd8b1f.role-style-brief-v3.execute.json)
  - [ba917844-c3b9-5689-91ad-f984703dea71.role-style-brief-v3.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/ba917844-c3b9-5689-91ad-f984703dea71.role-style-brief-v3.execute.json)
  - [2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-brief-v3.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-brief-v3.execute.json)
- 对应的 v2-v3 真实 diff 已落盘：
  - [670447b0-0bca-5df9-aea8-4f30cbcd8b1f.role-style-brief-v3.execute.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/670447b0-0bca-5df9-aea8-4f30cbcd8b1f.role-style-brief-v3.execute.diff.json)
  - [ba917844-c3b9-5689-91ad-f984703dea71.role-style-brief-v3.execute.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/ba917844-c3b9-5689-91ad-f984703dea71.role-style-brief-v3.execute.diff.json)
  - [2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-brief-v3.execute.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/prompt-profile-matrix/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-brief-v3.execute.diff.json)
- 当前真实 execute 结论已经足够收敛：
  - `670447...`（recipe metaphor）：
    - `role-style-v2` 与 `role-style-brief-v3` 都保持 `1/1 paragraph` 风格和完整覆盖
    - `v3` 语气更像出版物，但优势不是压倒性的
  - `ba917...`（memory definition paragraph）：
    - `v3` 更强烈地把整段收束成单一连贯段落
    - alignment coverage 仍保持 `12/12`
    - 说明 `chapter brief + paragraph intent` 在“长段连贯感”上确实有正向收益
  - `2e26...`（context engineering definition）：
    - `v3` 仍能稳定维持 `context engineering -> 上下文工程`
    - 但对 `STYLE_DRIFT` 的压制不稳定：`证据权重表明 / 上下文更准确的输出` 仍可能残留
    - 即使补了更明确的 `Literalism Guardrails`，单 packet 复验仍未稳定压掉这两处字面直译
- 因此当前结论不是“v3 失败”，而是：
  - `role-style-brief-v3` 对段落连贯性有正向价值
  - 但仅靠 prompt 升级，不能稳定替代现有 `STYLE_DRIFT -> preferred_hint -> rerun` 闭环
  - 尤其在定义性、论证性段落上，literalism 仍更适合交给 review/rerun 层定向压制
- 已做的 v3 补丁：
  - 新增 `Literalism Guardrails`
  - 明确写入：
    - `大量证据表明 / 现有证据表明`
    - `更符合上下文的输出`
  - 但当前真实样本还不足以证明这条 prompt-only guardrail 足够稳定
- 当前决策收敛为：
  - 继续保持 [role-style-v2](/Users/smy/project/book-agent/src/book_agent/workers/translator.py) 为正式生产默认 prompt profile
  - `role-style-brief-v3` 保留为实验 profile，不提升到生产默认
  - `STYLE_DRIFT` 的主纠偏路径继续放在 `review -> preferred_hint -> packet rerun`
- 本轮验证：
  - `uv run python -m unittest tests.test_translation_worker_abstraction` -> `30 tests OK`
  - 本轮真实 token 消耗仅限固定 packet execute；未发起整章或整书重跑

### 2026-03-18

- 已根据上一轮真实 packet A/B 结论，收窄 `Paragraph Intent Signal` 的生产适用边界：
  - [context_compile.py](/Users/smy/project/book-agent/src/book_agent/services/context_compile.py) 现在只会把高精度意图 `definition / evidence` 注入 `style_constraints`
  - `analogy / transition / summary / exposition` 仍可被内部推断，但默认不再进入 prompt，避免类比段被意图提示放大后出现轻微“顺但有发挥”的副作用
- 单测已补齐：
  - [test_translation_worker_abstraction.py](/Users/smy/project/book-agent/tests/test_translation_worker_abstraction.py) 现在同时锁住两件事：
    - 定义段仍会进入 `Paragraph Intent Signal`
    - 类比段不会再带 `paragraph_intent / paragraph_intent_hint`
- 已完成两个零 token 的真实 packet dry-run 复核：
  - [670447b0-0bca-5df9-aea8-4f30cbcd8b1f.role-style-v2.intent-narrowed.dryrun.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/670447b0-0bca-5df9-aea8-4f30cbcd8b1f.role-style-v2.intent-narrowed.dryrun.json)
    - `Paragraph Intent Signal: - none`
  - [ba917844-c3b9-5689-91ad-f984703dea71.role-style-v2.intent-narrowed.dryrun.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.role-style-v2.intent-narrowed.dryrun.json)
    - `Paragraph Intent Signal: Intent: definition`
- 当前收敛结论：
  - `Paragraph Intent Signal` 作为零 token 辅助层仍值得保留
  - 但它不应对所有意图默认全开；至少当前证据下，`analogy` 不值得继续进入生产 prompt
  - 下一刀如果继续走提示词优化，应优先围绕“定义段/证据段”深挖，而不是重新扩大意图范围
- 本轮验证：
  - `uv run python -m unittest tests.test_translation_worker_abstraction` -> `32 tests OK`
  - `uv run python -m py_compile src/book_agent/services/context_compile.py tests/test_translation_worker_abstraction.py`

### 2026-03-18

- 已对“高精度 intent-only”版本做最小真实 execute 复核，仍然只限 2 个 packet：
  - 定义段：[ba917844-c3b9-5689-91ad-f984703dea71.role-style-v2.no-intent.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.role-style-v2.no-intent.execute.json)
  - 定义段（intent）：[ba917844-c3b9-5689-91ad-f984703dea71.role-style-v2.intent-narrowed.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.role-style-v2.intent-narrowed.execute.json)
  - 证据段：[b4d1a602-acb4-501f-8ab0-68bef11bfa02.role-style-v2.no-intent.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/b4d1a602-acb4-501f-8ab0-68bef11bfa02.role-style-v2.no-intent.execute.json)
  - 证据段（intent）：[b4d1a602-acb4-501f-8ab0-68bef11bfa02.role-style-v2.intent-narrowed.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/b4d1a602-acb4-501f-8ab0-68bef11bfa02.role-style-v2.intent-narrowed.execute.json)
  - 对应 diff：
    - [ba917...intent-narrowed.execute.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.role-style-v2.intent-narrowed.execute.diff.json)
    - [b4d1...intent-narrowed.execute.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/b4d1a602-acb4-501f-8ab0-68bef11bfa02.role-style-v2.intent-narrowed.execute.diff.json)
- 当前 execute 结论：
  - 定义段：`intent-narrowed` 比 `no-intent` 更稳，中文更凝练，概念边界也更清楚；成本几乎不变（`0.00109693 -> 0.00110603 USD`）
  - 证据段：`intent-narrowed` 有轻微正向收益，会把“性能会按照特定的数学模式”收束成更自然的“性能的提升遵循特定的数学模式”；成本几乎不变（`0.00060343 -> 0.00060973 USD`）
  - 因此当前决策从“保守观望”收敛成“继续保留”：`definition / evidence` 型 `Paragraph Intent Signal` 继续保留在生产默认链路中
  - 同时保持上一轮结论不变：`analogy` 仍不值得重新纳入生产 prompt
- 为了让后续实验工件更容易识别这一阶段的语义，[context_compile.py](/Users/smy/project/book-agent/src/book_agent/services/context_compile.py) 的 `compile_version` 已提升为 `v1.chapter-memory.intent-precision`
- 本轮验证：
  - 真实 token 消耗仍只限 2 个 packet 的 4 次 execute，未发起整章或整书重跑

### 2026-03-18

- 已给 [context_compile.py](/Users/smy/project/book-agent/src/book_agent/services/context_compile.py) 新增 `source-aware literalism guardrails`，并保持可开关：
  - `weight of evidence` -> `大量证据表明 / 现有证据表明`
  - `contextually accurate outputs` -> `更符合上下文的输出`
  - `context engineering` -> `上下文工程`
- 新增的 guardrail 只作用于当前 packet 命中的高风险短语，不做泛化重写；同时已通过 [run_packet_experiment.py](/Users/smy/project/book-agent/scripts/run_packet_experiment.py) 暴露 `--disable-literalism-guardrails`，方便后续继续做 packet A/B。
- 对应单测已补在 [test_translation_worker_abstraction.py](/Users/smy/project/book-agent/tests/test_translation_worker_abstraction.py)：
  - prompt 会出现 `Source-Aware Literalism Guardrails:`
  - context compiler 会编译出 guardrails
  - 显式关闭后不会再写入 `style_constraints`
- 已对最关键的真实 `STYLE_DRIFT` packet 做最小真实 A/B：
  - baseline：[2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.no-literalism-guardrails.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.no-literalism-guardrails.execute.json)
  - candidate：[2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.literalism-guardrails.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.literalism-guardrails.execute.json)
  - diff：[2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.literalism-guardrails.execute.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.literalism-guardrails.execute.diff.json)
- 这轮真实结论非常明确：
  - baseline 仍会产出：`证据权重表明`、`上下文更准确的输出`
  - candidate 已直接收敛成：`现有证据表明`、`更符合上下文的输出`
  - 用当前 [review.py](/Users/smy/project/book-agent/src/book_agent/services/review.py) 的 `STYLE_DRIFT` 规则零 token 复扫：
    - baseline 命中 `2`
    - candidate 命中 `0`
  - 说明 `source-aware literalism guardrails` 已经不只是“看起来更顺”，而是能在首轮输出里直接降低 `STYLE_DRIFT`
- 成本变化仍然很小：
  - baseline：`0.00068281 USD`
  - candidate：`0.00088774 USD`
- 当前收敛决策：
  - `source-aware literalism guardrails` 值得保留在生产默认链路中
  - 这条能力不会替代 `review -> preferred_hint -> rerun`，但已经开始把一部分原本依赖 rerun 的 style drift 前移到首轮翻译阶段解决
- 本轮验证：
  - `uv run python -m unittest tests.test_translation_worker_abstraction` -> `34 tests OK`
  - `uv run python -m py_compile src/book_agent/services/context_compile.py src/book_agent/workers/translator.py src/book_agent/services/packet_experiment.py scripts/run_packet_experiment.py tests/test_translation_worker_abstraction.py`

### 2026-03-18

- 已新增共享规则模块 [style_drift.py](/Users/smy/project/book-agent/src/book_agent/services/style_drift.py)，把 `STYLE_DRIFT` 的：
  - `pattern_id`
  - `source_pattern`
  - `target_pattern`
  - `preferred_hint`
  - `message`
  - `prompt_guidance`
  统一收拢到同一份定义里。
- [review.py](/Users/smy/project/book-agent/src/book_agent/services/review.py) 现在直接复用这套共享 `STYLE_DRIFT_RULES`；[context_compile.py](/Users/smy/project/book-agent/src/book_agent/services/context_compile.py) 也通过同一份定义生成 `source-aware literalism guardrails`。这意味着：
  - 以后新增或调整 literalism 规则时，不再需要同时改“review 检测”和“prompt guardrail”两套实现
  - 首轮翻译和 review/rerun 的纠偏方向开始真正同步
- 同时，[context_compile.py](/Users/smy/project/book-agent/src/book_agent/services/context_compile.py) 的 `compile_version` 已提升到 `v1.chapter-memory.intent-precision.style-drift-sync`，方便后续实验工件区分这一阶段的语义。
- 本轮验证：
  - `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_translation_worker_abstraction tests.test_persistence_and_review tests.test_rule_engine` -> `83 tests OK`
  - `uv run python -m py_compile src/book_agent/services/style_drift.py src/book_agent/services/context_compile.py src/book_agent/services/review.py`

### 2026-03-18

- 已继续沿共享 `STYLE_DRIFT` / prompt guardrail 路线补了一条更窄的 literalism 规则，目标是压掉定义句里的：
  - `what was known, when it was known` -> `获知时间`
- 具体改动已落在 [style_drift.py](/Users/smy/project/book-agent/src/book_agent/services/style_drift.py)：
  - 新增 `knowledge_timeline_literal`
  - `preferred_hint` 固定为：`已知内容、知晓这些内容的时间点，以及其对行动的重要性`
  - `prompt_guidance` 也同步升级成显式中文表达，不再只是抽象提醒
- 最小回归已补齐：
  - [test_translation_worker_abstraction.py](/Users/smy/project/book-agent/tests/test_translation_worker_abstraction.py) 现在会锁住 context compiler 能把这条新 guardrail 写进 `literalism_guardrails`
  - [test_persistence_and_review.py](/Users/smy/project/book-agent/tests/test_persistence_and_review.py) 新增了专门的 `KNOWLEDGE_TIMELINE_LITERALISM_XHTML`，确认 review 会把 `获知时间` 打成非阻断 `STYLE_DRIFT`
- 已对固定 packet [2e26e803-5d84-52ae-9b01-c5b8e07a7d93](/Users/smy/project/book-agent/artifacts/analysis/packet-debug/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.json) 做最小真实 A/B：
  - baseline：[no-literalism-guardrails.timeline.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.no-literalism-guardrails.timeline.execute.json)
  - candidate：[literalism-guardrails.timeline.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.literalism-guardrails.timeline.execute.json)
  - diff：[literalism-guardrails.timeline.execute.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.literalism-guardrails.timeline.execute.diff.json)
- 这轮真实结论是明确正向的：
  - baseline 仍会产出：`获知时间`、`证据权重表明`、`上下文更准确的输出`
  - candidate 已收敛成：`知晓这些内容的时间点`、`大量证据表明`、`更符合上下文的输出`
  - 用当前共享 `STYLE_DRIFT_RULES` 零 token 复扫：
    - baseline 命中 `3`
    - candidate 命中 `0`
- 成本仍然很小：
  - baseline：`0.00087937 USD`
  - candidate：`0.00092039 USD`
- 当前收敛决策：
  - `knowledge_timeline_literal` 值得保留在共享规则集中
  - 这类高信号 literalism 继续适合走“共享定义 -> 首轮 prompt guardrail -> review/rerun 同步复用”的闭环，而不是扩成宽泛重写策略
- 本轮验证：
  - `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_translation_worker_abstraction tests.test_persistence_and_review` -> `73 tests OK`
  - `uv run python -m py_compile src/book_agent/services/style_drift.py src/book_agent/services/context_compile.py src/book_agent/services/review.py tests/test_translation_worker_abstraction.py tests/test_persistence_and_review.py`

### 2026-03-18

- 在上轮新增 `knowledge_timeline_literal` 后，又从真实 packet 里暴露出一条紧邻残差：
  - `what some are beginning to call ...` 仍可能落成 `称之为……的领域/内容`
  - 第一版 candidate 还伴随了 `context` 从 `上下文` 回退成 `情境` 的副作用
- 这轮我没有扩大实验面，而是直接把这条回退收进现有共享规则：
  - [style_drift.py](/Users/smy/project/book-agent/src/book_agent/services/style_drift.py) 新增 `emerging_term_scaffolding_literal`
  - 同时收紧 `context_engineering_literal`：
    - 不仅继续抓 `情境工程 / 语境工程`
    - 也会抓同一定义句里的 `情境如何... / 语境如何...`
    - prompt guidance 明确要求在这类定义句里把 `context` 一致保持为 `上下文`
- 对应最小回归已补在：
  - [test_translation_worker_abstraction.py](/Users/smy/project/book-agent/tests/test_translation_worker_abstraction.py)
  - [test_persistence_and_review.py](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)
- 真实验证仍然只用固定 packet [2e26e803-5d84-52ae-9b01-c5b8e07a7d93](/Users/smy/project/book-agent/artifacts/analysis/packet-debug/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.json)：
  - baseline：[no-literalism-guardrails.timeline.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.no-literalism-guardrails.timeline.execute.json)
  - final candidate：[literalism-guardrails.scaffolding-context.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.literalism-guardrails.scaffolding-context.execute.json)
  - diff：[literalism-guardrails.scaffolding-context.execute.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.literalism-guardrails.scaffolding-context.execute.diff.json)
- 最终 candidate 已经同时把 4 个高信号 residual 压掉：
  - `称之为“上下文工程”的领域`
  - `获知时间`
  - `证据权重表明`
  - `上下文更准确的输出`
- 当前 final candidate 的对应表达已经收敛成：
  - `有人开始将其称为上下文工程，即对上下文的创建、维护与应用进行精心设计...`
  - `已知内容、知晓这些内容的时间点...`
  - `大量证据表明`
  - `更符合上下文的输出`
- 用当前共享 `STYLE_DRIFT_RULES` 零 token 复扫：
  - baseline 命中 `4`
  - final candidate 命中 `0`
- 成本仍然很小：
  - baseline：`0.00087937 USD`
  - final candidate：`0.00091700 USD`
- 当前收敛决策：
  - `emerging_term_scaffolding_literal` 值得保留
  - `context_engineering_literal` 需要继续保持“术语本身 + 同句上下文一致性”这两层约束
  - 对这类高信号定义句 literalism，继续优先走“共享规则 -> 首轮 prompt guardrail -> review/rerun 共用”的闭环，而不是扩大到宽泛句法重写
- 本轮验证：
  - `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_translation_worker_abstraction tests.test_persistence_and_review` -> `74 tests OK`
  - `uv run python -m py_compile src/book_agent/services/style_drift.py src/book_agent/services/context_compile.py src/book_agent/services/review.py tests/test_translation_worker_abstraction.py tests/test_persistence_and_review.py`

### 2026-03-18

- 继续沿同一固定定义段 packet 收敛 residual literalism，这轮目标是：
  - `durable substrate` -> `持久基底`
- 具体改动已落在 [style_drift.py](/Users/smy/project/book-agent/src/book_agent/services/style_drift.py)：
  - 新增 `durable_substrate_literal`
  - `preferred_hint` 固定为：`使上下文得以持久存在的基础`
  - `prompt_guidance` 明确要求避免 `持久基底` 这类抽象名词链硬译
- 最小回归已补齐：
  - [test_translation_worker_abstraction.py](/Users/smy/project/book-agent/tests/test_translation_worker_abstraction.py) 现在会锁住 context compiler 能把 `durable substrate` guardrail 编进 prompt
  - [test_persistence_and_review.py](/Users/smy/project/book-agent/tests/test_persistence_and_review.py) 新增了 `DURABLE_SUBSTRATE_LITERALISM_XHTML`，确认 review 会把 `持久基底` 打成非阻断 `STYLE_DRIFT`
- 真实验证仍然只用同一个固定 packet [2e26e803-5d84-52ae-9b01-c5b8e07a7d93](/Users/smy/project/book-agent/artifacts/analysis/packet-debug/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.json)：
  - baseline：[literalism-guardrails.scaffolding-context.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.literalism-guardrails.scaffolding-context.execute.json)
  - candidate：[literalism-guardrails.substrate.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.literalism-guardrails.substrate.execute.json)
  - diff：[literalism-guardrails.substrate.execute.diff.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.literalism-guardrails.substrate.execute.diff.json)
- 这轮真实结论继续是明确正向的：
  - baseline 仍残留：`持久基底`
  - candidate 已收敛成：`使上下文得以持久存在的基础`
  - 候选译文还把三句定义段自然收束成了一个连贯中文段，而不是碎句拼接
  - 用当前共享 `STYLE_DRIFT_RULES` 零 token 复扫：
    - baseline 命中 `1`
    - candidate 命中 `0`
- 结构侧也已核对：
  - candidate 只有 `1` 个 target segment，但仍保留 `3` 条 `alignment_suggestions`
  - `source_sentence_ids` 为 `3/3` 全覆盖
  - `low_confidence_flags = []`
- 成本依旧很小，且这次还有下降：
  - baseline：`0.00091700 USD`
  - candidate：`0.00084706 USD`
- 当前收敛决策：
  - `durable_substrate_literal` 值得保留在共享规则集中
  - 当前这条定义段上的 5 个高信号 literalism（术语脚手架、timeline、weight of evidence、contextually accurate outputs、durable substrate）都已经被压到 `STYLE_DRIFT = 0`
  - 下一步如果继续扩规则，应该优先换到新的 residual 类型，而不是继续在这一句上堆更宽的重写规则
- 本轮验证：
  - `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_translation_worker_abstraction tests.test_persistence_and_review` -> `76 tests OK`
  - `uv run python -m py_compile src/book_agent/services/style_drift.py src/book_agent/services/context_compile.py src/book_agent/services/review.py tests/test_translation_worker_abstraction.py tests/test_persistence_and_review.py`
- 当前收敛结论：
  - `STYLE_DRIFT` 的规则定义已经从“review 专用”升级成“首轮 prompt + review 共用”的共享策略层
  - 下一刀如果继续沿这条线推进，最值得做的不是再复制新规则，而是挑一个新的高价值 literalism 模式，走同样的“共享定义 -> 单 packet A/B -> review 复扫”闭环

- 新增了一刀“脏的 previous translations 清洗”，目标是避免历史坏译法继续污染后续 packet prompt：
  - 在 [context_compile.py](/Users/smy/project/book-agent/src/book_agent/services/context_compile.py) 新增了两类过滤：
    - `STYLE_DRIFT_RULES` 已知命中的旧译法记忆会从 `Previous Accepted Translations` 里剔除
    - 命中已锁定术语但仍使用旧译法的历史块，也会从 `Previous Accepted Translations` 里剔除
  - 同时补了共享规则 `in_context_information_literal`，专门覆盖：
    - `in-context information -> 情境信息`
    - `external context -> 外部情境`
  - 对应回归已补在 [test_translation_worker_abstraction.py](/Users/smy/project/book-agent/tests/test_translation_worker_abstraction.py)：
    - `test_context_compiler_filters_stale_literalism_and_locked_term_conflict_from_previous_translations`
- 零 token dry-run 已确认这条清洗真的生效：
  - 工件：[2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.sanitized-prev-memory.dryrun.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.sanitized-prev-memory.dryrun.json)
  - 观察结果：
    - prompt 中已不再出现 `情境信息 / 外部情境 / 智能体AI`
    - 干净的 `智能体式AI` 仍然保留在 prompt 中
    - 保留下来的 previous translations 也都变成了干净的 bibliography / concept context，而不是带毒记忆
- 单 packet execute 也已完成：
  - 工件：[2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.sanitized-prev-memory.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.sanitized-prev-memory.execute.json)
  - 结果：
    - `STYLE_DRIFT` 继续保持 `0`
    - 成本更低：`0.00059310 USD`
    - 但译文重新分成了 `3` 个 target segments，说明“清洗脏记忆”虽然是正向 hygiene 改进，却没有自动带来更强的段落压缩
- 我又试了一刀更窄的 prompt 文案，要求 cohesive prose 倾向用最少 paragraph segments 输出：
  - 工件：[2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.sanitized-prev-memory.segment-guard.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.sanitized-prev-memory.segment-guard.execute.json)
  - 结果：
    - `STYLE_DRIFT` 仍是 `0`
    - 但 target segments 仍然是 `3`
    - 没有证明出净收益
  - 处理决定：
    - 这条 prompt 试验已回滚，不进入主链路
    - 只保留“previous translations 清洗”这条已经被验证有价值的 hygiene 改造
- 本轮验证：
  - `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_translation_worker_abstraction tests.test_persistence_and_review` -> `77 tests OK`
  - `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_translation_worker_abstraction` -> `38 tests OK`
  - `uv run python -m py_compile src/book_agent/services/style_drift.py src/book_agent/services/context_compile.py src/book_agent/workers/translator.py tests/test_translation_worker_abstraction.py tests/test_persistence_and_review.py`
- 当前收敛结论：
  - `previous translation sanitization` 值得保留，它能零 token 地减少 prompt 被坏译法污染的概率
  - “让模型自动重新压回单段输出”这件事，暂时不值得继续靠 prompt 文案硬推；后续如果再做，应换更高杠杆的结构/导出层方案，而不是继续堆 prompt 字句

- 在保留上述 hygiene 改造后，我又用当前主链路默认配置重新验证了另外两个 anchor packet，确认这波重构已经不再只对 `2e26...` 单点成立：
  - recipe metaphor packet：
    - 旧工件：[670447b0-0bca-5df9-aea8-4f30cbcd8b1f.baseline.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/670447b0-0bca-5df9-aea8-4f30cbcd8b1f.baseline.execute.json)
    - 新工件：[670447b0-0bca-5df9-aea8-4f30cbcd8b1f.current-default.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/670447b0-0bca-5df9-aea8-4f30cbcd8b1f.current-default.execute.json)
    - 结果：
      - `Agentic AI` 已稳定收敛成锁定译法 `智能体式AI`
      - prompt 中已明确带上 `Agentic AI => 智能体式AI`
      - 译文从 `3` 个 target segments 收束成 `1` 个自然段
      - 成本变化很小：`0.00057632 USD -> 0.00059377 USD`
  - memory definition packet：
    - 旧工件：[ba917844-c3b9-5689-91ad-f984703dea71.paragraph_led_current.denoised.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.paragraph_led_current.denoised.execute.json)
    - 新工件：[ba917844-c3b9-5689-91ad-f984703dea71.current-default.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/ba917844-c3b9-5689-91ad-f984703dea71.current-default.execute.json)
    - 结果：
      - `智能体AI` 已收敛成 `智能体式AI`
      - 长定义段从 `12` 个 target segments 收束成 `1` 个自然段
      - 中文表达更接近出版级技术译文，例如：
        - `将记忆构建到AI中不是可选项` -> `为AI构建记忆并非可选项，而是实现持久性、适应性和连续性的基础`
        - `这就是分布式SQL变得至关重要的地方` -> `这正是分布式SQL的关键所在`
      - 成本反而下降：`0.00124208 USD -> 0.00087318 USD`
- 这一轮 anchor 复核后的当前验收判断：
  - 固定 3 个关键 packet 上，当前默认链路已经形成稳定闭环：
    - `670...`：术语锁定生效，中文自然度明显提升
    - `ba917...`：术语锁定 + 长定义段连贯性明显提升
    - `2e26...`：高信号 literalism 已压到 `STYLE_DRIFT = 0`
  - 这意味着本波“packet 级翻译质量重构”已经达到一版可验收状态
  - 后续如果继续推进，应切到新的 residual 类型、或转向更大范围的 chapter-level 受控验证，而不是继续在当前三条 anchor 上反复微调

- 为了把验收从“3 个独立 packet”再往上抬一层，我新增了通用单章 smoke 工位：
  - 服务：[translation_chapter_smoke.py](/Users/smy/project/book-agent/src/book_agent/services/translation_chapter_smoke.py)
  - 脚本：[run_translation_chapter_smoke.py](/Users/smy/project/book-agent/scripts/run_translation_chapter_smoke.py)
  - 它会先用 `PacketExperimentScanService` 选取章节内最值得验证的 packet，再执行受控 packet experiment，并聚合：
    - `style_drift_hits`
    - `target_segment_count`
    - `alignment_suggestion_count`
    - `usage.cost_usd`
  - 最小回归已补在 [test_translation_worker_abstraction.py](/Users/smy/project/book-agent/tests/test_translation_worker_abstraction.py)
- 我先用它对 `d1ff075e-e6cf-52ee-9eb7-789d2b6a4a9c` 这一章的 3 个固定 anchor packet 做了章节级 smoke：
  - 首轮报告：[d1ff075e-e6cf-52ee-9eb7-789d2b6a4a9c.current-default.anchor3.json](/Users/smy/project/book-agent/artifacts/analysis/chapter-smoke/d1ff075e-e6cf-52ee-9eb7-789d2b6a4a9c.current-default.anchor3.json)
  - 聚合结果：
    - `selected_packet_count = 3`
    - `total_style_drift_hits = 1`
    - `zero_style_drift_packet_count = 2`
  - 暴露出的唯一回退是 `2e26...` 再次出现：
    - `有人开始称之为“上下文工程”的领域`
- 我没有扩大实验面，而是只对这条共享规则再加强了一刀：
  - [style_drift.py](/Users/smy/project/book-agent/src/book_agent/services/style_drift.py) 里 `source_aware_literalism_guardrail_lines()` 现在不仅输出 `prompt_guidance`，也会把 `preferred_hint` 显式编进 prompt
  - 这让 prompt 不再只是告诉模型“不要怎么写”，也明确告诉它“优先怎么写”
  - 最小回归也已更新到 [test_translation_worker_abstraction.py](/Users/smy/project/book-agent/tests/test_translation_worker_abstraction.py)
- 这条增强先在固定 packet `2e26...` 上单独验证过：
  - 工件：[2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.preferred-hints.execute.json](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4/2e26e803-5d84-52ae-9b01-c5b8e07a7d93.role-style-v2.preferred-hints.execute.json)
  - 结果：
    - `STYLE_DRIFT = 0`
    - 表达收敛成：
      - `有人开始将其称为上下文工程，它指的是……`
      - 不再回退成 `称之为……的领域`
- 然后我又用同一章、同一组 3 个 anchor packet 复跑了一次章节级 smoke：
  - 报告：[d1ff075e-e6cf-52ee-9eb7-789d2b6a4a9c.current-default.anchor3.preferred-hints.json](/Users/smy/project/book-agent/artifacts/analysis/chapter-smoke/d1ff075e-e6cf-52ee-9eb7-789d2b6a4a9c.current-default.anchor3.preferred-hints.json)
  - 聚合结果：
    - `selected_packet_count = 3`
    - `executed_packet_count = 3`
    - `total_style_drift_hits = 0`
    - `zero_style_drift_packet_count = 3`
    - `total_cost_usd = 0.00090073`
- 当前这一波的最终验收判断已经可以上提到“单章 anchor smoke”层级：
  - `670...`：`智能体式AI` 锁定稳定，中文自然
  - `ba917...`：长定义段连贯性稳定，锁定术语稳定
  - `2e26...`：高信号 literalism 在章节级受控验证里也归零
  - 所以本轮“英译中 packet/chapter-level 重构”已经达到更扎实的验收状态，不再只是 packet 单点成立

- 针对后续 QA/review 代码审查暴露出来的 3 个架构性问题，本轮已经全部收口：
  - `REBUILD_CHAPTER_BRIEF` 不再只是“消掉 review 告警”，新的 brief 会真正写回章节记忆，并在后续 context compile 中优先于旧 memory brief 生效
  - review auto-followup 不再只在开始时计算一次候选动作，而是每轮 rerun 后都会基于新的 `ReviewArtifacts` 重算，避免对白跑/已解决 issue 继续 rerun
  - chapter smoke 的 `STYLE_DRIFT` 统计已和正式 review 统一到同一套“源侧 + 目标侧”规则，不再有验收口径漂移
  - 同时修掉了两条真实链路缺口：
    - merged packet 的 targeted rebuild 现在会按 `block_start_id -> block_end_id` 重建整个 block group，不再在 packet-scope rerun 后丢句并冒出 `OMISSION`
    - `ChapterConceptAutoLockService` 现在按 `mention_count` 对齐 review 的 `UNLOCKED_KEY_CONCEPT` 阈值，不会再漏掉“同 packet 多次提及”的可锁定概念
  - 配套还把 `CONTEXT_ENGINEERING_THREE_PACKET_XHTML` 恢复成了真正的 multi-packet fixture，继续锁住“多 packet 概念 issue 只 targeted rerun 受影响 packets”这条行为
- 本轮验证：
  - `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_translation_worker_abstraction` -> `48 tests OK`
  - `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_persistence_and_review tests.test_rule_engine` -> `54 tests OK`
  - `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_translation_worker_abstraction tests.test_persistence_and_review tests.test_rule_engine` -> `103 tests OK`
- 当前附加验收判断：
  - chapter brief freshness、packet-scope auto-followup recomputation、chapter smoke drift parity 这 3 个 review 发现都已经修复并有回归锁住
  - `STYLE_DRIFT / TERM_CONFLICT / UNLOCKED_KEY_CONCEPT / STALE_CHAPTER_BRIEF` 的 packet/chapter follow-up 逻辑现在重新处于一致、可验证状态

## 7. 2026-03-19 Minimal Context Slice

这一轮没有继续堆 prompt，而是把“连续性”真正下沉成 compile 规则：

- `prev_translated_blocks` 升格为默认连续性信号
- raw `prev_blocks / next_blocks` 改成按需兜底
- `chapter_brief` 改成：
  - 仍保留在 compiled packet / chapter memory
  - 但可以从 prompt 中单独抑制
- 对 `shift from ... to ...` 这种概念换挡句，保留 chapter brief 的窄例外

对应代码：

- [context_compile.py](/Users/smy/project/book-agent/src/book_agent/services/context_compile.py)
- [translator.py](/Users/smy/project/book-agent/src/book_agent/workers/translator.py)
- [packet_experiment.py](/Users/smy/project/book-agent/src/book_agent/services/packet_experiment.py)
- [run_packet_experiment.py](/Users/smy/project/book-agent/scripts/run_packet_experiment.py)

这轮同时补了新的 source-aware 窄规则：

- `goal_reason_how_literal`
  - 目标：约束 `telling a computer what to do / why ... / the how`
  - preferred hint：`告诉它目标与原因，让它自己决定如何实现`

对应代码：

- [style_drift.py](/Users/smy/project/book-agent/src/book_agent/services/style_drift.py)

真实 packet A/B 结论：

1. `42057...`

- 当前策略把 prompt 从 `3271 -> 3058 chars`
- 译文基本不变，成本略降
- 说明“previous accepted translations 主导连续性”已经成立

2. `c120...`

- 当前策略把 prompt 从 `3181 -> 2534 chars`
- 但中文自然度仍未稳定达到目标句式
- 这说明剩余问题已不再是“上下文不够”，而是“packet-level rewrite 仍不够稳”

本轮验证：

- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_translation_worker_abstraction`
  - `54 tests OK`
- 实验工件目录：
  - [packet-experiments](/Users/smy/project/book-agent/artifacts/analysis/packet-experiments/67283f52-b775-533f-988b-c7433a22a28f)

当前收敛判断：

- “更短 prompt + 不明显伤害质量”已经在 `42057...` 这类 packet 上成立
- `c120...` 暴露出的 residual，优先级已经从 context compile 转到更窄的 rewrite / rerun hint
- 所以下一刀不该回退到长 prompt，而应继续做：
  - 更窄的 `goal / reason / how` rewrite
  - `profound sense of responsibility`
  - `the stakes are immeasurably high`
  - `real problem`

## 8. 2026-03-19 Agentic Design v16 Export Repair

本轮针对 `agentic-design-book` 的两个真实导出问题做了收口：

- 历史 DB 里的 multiline heading continuation 现在会在 export 阶段自动合并
  - 典型案例：`A Thought Leader's Perspective: Power` + `and Responsibility`
  - 新导出结果稳定合并为：`思想领袖视角：力量与责任`
- render 层新增了 `prose artifact` 识别
  - 对 `block_type=code/table`、`protected_policy=protect`，但文本本质是叙述性 prose 的块，不再一律走 `代码保持原样`
  - 只要已有 target，或存在 repair metadata，就按正文渲染

为了兼容已经落库的旧 run（`v11` DB），这次还对 `v16` 导出副本加了最小 repair metadata：

- `5cea19dc...`
  - 把被错误切成 `code_like` 的感谢语，与下一页 continuation 合并后导出
- `2624802c...`
  - 把被错误落成 `table_like` 的感谢语恢复为正常正文输出

对应代码：

- [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
- [test_persistence_and_review.py](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)

对应产物：

- [v16 merged markdown](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v16-merged-markdown/67283f52-b775-533f-988b-c7433a22a28f/merged-document.md)
- [v16 merged html](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v16-merged-markdown/67283f52-b775-533f-988b-c7433a22a28f/merged-document.html)

本轮验证：

- `uv run python -m py_compile src/book_agent/services/export.py tests/test_persistence_and_review.py`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_persistence_and_review.PersistenceAndReviewTests.test_render_blocks_merge_multiline_heading_fragments_from_historical_pdf_export tests.test_persistence_and_review.PersistenceAndReviewTests.test_render_blocks_treat_prose_like_code_block_with_targets_as_translated_paragraph tests.test_persistence_and_review.PersistenceAndReviewTests.test_render_blocks_honor_prose_artifact_repair_metadata_and_skip_continuation`
  - `3 tests OK`

当前判断：

- 这次用户点名的两个坏例在 `v16` 里都已经收口
- 但 `pdf-scan technical book` 仍存在更大范围的 `prose mistaken as artifact` 历史包袱，不能误判为“全书 code detection 已彻底完成”

## 9. 2026-03-19 Agentic Design Prose-Artifact Sweep (v23-v26)

这轮不是继续加 prompt，而是把一个长期影响翻译质量的上游结构问题彻底往前推进：

- 历史 DB 里有大量正文段落被误落成 `code_like / table_like + protect`
- 结果就是 merged 中出现：
  - `代码保持原样`
  - 英文原文正文直接落到中文主文流
  - 一段 prose 被错误拆成“前半段中文翻译 + 后半段代码块原样”

根因拆解：

1. inline code 规则过宽

- `class of` 会被误当作代码信号
- 典型坏例：
  - `I admit, I began as a skeptic ... a new class of "reasoning" models`

2. repair scanner 只看 `body family`

- appendix / references 里的正文型坏块完全绕过 repair
- 典型坏例：
  - `Frameworks provide distinct mechanisms ...`
  - `This Python script defines a single function called greet ...`
  - `A2A Client ...`

3. 放开 family 后，还要继续排除真实代码

- OCR 扁平化代码、单行 `def aggregator(...)` 这类真实代码示例，不能被误翻

本轮代码：

- [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
  - 收紧 `_INLINE_CODE_LIKE_PATTERN`
  - 新增 `codeish line` / `strong codeish line` 护栏
- [pdf_prose_artifact_repair.py](/Users/smy/project/book-agent/src/book_agent/services/pdf_prose_artifact_repair.py)
  - repair scanner 不再只限定 `body`
  - appendix / references 的 prose-like artifact 也会进入 repair
- [repair_pdf_prose_artifacts.py](/Users/smy/project/book-agent/scripts/repair_pdf_prose_artifacts.py)
  - 用于历史 DB 分轮 repair + export

真实产物推进：

- `v23`
  - 清掉首批 body-family 残留
- `v24`
  - 扩大到更宽口径扫描，修复 `37` 个候选中的 `36` 个
- `v25`
  - 补掉最后一个 timeout chain
- `v26`
  - appendix / references sweep，`18/18` repaired
  - 最新口径下复核 `remaining_candidates = 0`

最终产物：

- [v26 merged markdown](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v26-prose-artifact-final-plus-appendix/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.md)
- [v26 merged html](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v26-prose-artifact-final-plus-appendix/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.html)

定点结论：

- `I admit, I began as a skeptic ...` 已恢复为中文正文
- `While the chapters are ordered ...` 已恢复为完整中文段落
- `Frameworks provide distinct mechanisms ...`
- `This Python script defines a single function called greet ...`
- `A2A Client / Synchronous Request/Response`
  - 上述 appendix / references 坏例均已恢复成中文正文
- 真实代码示例仍保持代码工件导出，没有被误翻成 prose

这轮收敛说明：

- 影响最终阅读体验的很多“翻译质量问题”，根因其实不是 prompt，而是上游结构识别错误
- 一旦把 prose mistaken as artifact 清掉，merged 质量会明显提升，而且不需要扩大 prompt 成本

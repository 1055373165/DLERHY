# Translation Quality Refactor Cockpit

Last Updated: 2026-03-18
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

### Planned

- [ ] 扩展 review 规则，把 `STYLE_DRIFT` 从高信号白名单扩到更泛化的直译腔检测
- [ ] 基于这次 `denoised execute` 结果，决定是否继续收紧 `concept registry` 候选策略，尤其是 `智能体AI` 这类仍需人工裁决的术语
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
    - `第二章` -> `第2章`
  - 但核心术语仍残留一个更值得处理的问题：`agentic AI` 当前在这个 packet 中稳定成了 `智能体AI`，说明下一刀更应该进入“术语裁决/概念锁定”，而不是继续盲目扩大概念候选提取规则。

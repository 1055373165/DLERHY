# 04 · 翻译核心（packet / 上下文 / prompt / worker / 记忆 / 术语）

> 来源：2026-09-16 对分支 `refactor/p0-stabilize`（HEAD `2176d51`）的逐文件源码阅读。行号对应该基线；标注「待确认」的条目未经运行验证。总览与跨子系统结论见 [`00-overview.md`](00-overview.md)。

# book-agent 翻译核心子系统源码阅读笔记

> 范围：packet 构建、上下文编译、prompt、worker/provider、事务、记忆、术语、风格启发式、成本、生产要素缺口。
> 方法：逐文件通读（见文末「阅读清单」），所有陈述均标注 `文件:行号`（相对 `src/book_agent/`，测试相对仓库根）。不确定处标「待确认」。
> 日期：2026-09-16，分支 `refactor/p0-stabilize`，HEAD `2176d51`。

---

## 0. 总览：一次 packet 翻译的端到端数据流

```
bootstrap (一次性)
  BookProfileBuilder ──► book_profiles + termbase/entity MemorySnapshot(空)     builders.py:128-213
  ChapterBriefBuilder ──► CHAPTER_BRIEF MemorySnapshot(3 句启发式摘要)           builders.py:247-313
  ChapterTranslationMemoryBuilder ──► CHAPTER_TRANSLATION_MEMORY v1 (seed)        builders.py:316-369
  ContextPacketBuilder ──► translation_packets + packet_sentence_map             builders.py:372-486

run executor (每个 packet 三段事务)                                                 document_run_executor.py:971-1020
  T1 prepare_packet   : load_packet_bundle → MemoryService.load_compiled_context
                        (latest chapter memory + document glossary) → ChapterContextCompiler.compile
                        → emit LLM_CALL_STARTED                                    translation.py:311-359
  --  call_worker     : LLMTranslationWorker.translate → build_translation_prompt_request
                        → OpenAICompatibleTranslationClient.generate_translation (urllib, 无 DB)
                        → alias 回映射                                              translator.py:872-928
  T2' record_worker_failure (仅失败) : FAILED TranslationRun + LLM_CALL_FAILED     translation.py:365-404
  T2 persist_packet_result : LLM_CALL_COMPLETED → OutputValidator → TranslationRun(SUCCEEDED)
                        + TargetSegment[] + AlignmentEdge[] + Sentence.status + Packet.status
                        → PACKET_TRANSLATED → hooks(GlossaryViolationHook, ChapterMemoryProposalHook)
                        → assert_lease_held                                        translation.py:406-489
  T3 complete_translate_success : work_item 释放 + run 用量计数器累加            run_execution.py:279-332
```

关键事实先行：
- 执行器路径下 `auto_commit_memory=False`（`document_run_executor.py:1005`），章节记忆只以 **proposal** 落库，直到 review 阶段才 commit（`services/review.py:158-175`）。因此**首轮翻译时章节记忆快照始终是 seed 版本**，「章节概念记忆」「recent_accepted_translations」在首轮 prompt 中实际为空；段落间连续性只来自 `TranslationRepository._build_prev_translated_blocks`（DB 里前文句子的对齐译文，`infra/repositories/translation.py:71-135`）。
- Provider 传输层是 **urllib**（`workers/providers/openai_compatible.py:63-212`），不是 httpx；`httpx` 只在 `pyproject.toml:21,27` 声明，源码中无使用（`grep httpx src/` 只命中一个陈旧 pycache）。
- 默认 prompt profile 是 `tech-column-meta-v1`（`core/config.py:69`、`workers/translator.py:601,854`），其 system prompt 是硬编码的中文「AI/LLM 技术专栏」人设（`translation/prompt_profiles.py:34-40`），对非 AI 领域书籍（如最近测试的 RSI 交易书）领域错配。

---

## 1. Packet：定义、分组规则、ORM、版本与重译、句子对齐落库

### 1.1 Packet 是什么
一个 `TranslationPacket` = 一次 LLM 调用的工作单元：1 个或多个（合并后 ≤3）连续可翻译 block 的「当前段落」+ 前后各最多 2 个 block 的源文上下文 + 该 packet 快照时的术语/实体/章节简介/风格约束，序列化为 `packet_json`（即 `ContextPacket` pydantic 模型，`translation/contracts.py:61-86`）。

### 1.2 分组规则（`domain/context/builders.py`）
- 只对 `block_is_context_translatable(block)` 为真的 block 建 packet（`builders.py:395`）；规则在 `domain/block_rules.py:42-56`：`CODE/TABLE/FIGURE/EQUATION/IMAGE` 以及 `source_span_json` 里 `translatable=False` 或 `pdf_block_role∈{header,footer,toc_entry}`、`pdf_page_family=backmatter` 的 block 一律 **不进入任何 packet**（既不是 current，也不是 prev/next 上下文）。这就是 protect 块的处理：**完全不可见**，译文由导出层原样回填（导出侧不在本文范围）。
- 合并规则 `_block_groups`（`builders.py:488-524`）：只有 `PARAGRAPH` 且句数 1..3 且字符数 1..420 的 block 才是合并候选（`_is_merge_candidate`, 526-536）；连续 ordinal、累计 ≤3 块、≤900 字符、≤6 句时合并为一个 packet（常量 41-48 行）。非候选 block 单独成 packet。
- 单 block 切窗 `_packet_sentence_windows`（837-857）：一般章节 >32 句时按 32 句切片；`pdf_section_family=="references"` 或标题为 references/bibliography/works cited 时 >24 句才切、每片 24 句。切片 packet 的 `current_block_text` 为该窗句子拼接（425-428），packet id 额外掺入首尾句 id 与 chunk_index（413-424）。
- prev/next 上下文：`translatable_blocks[start-2:start]` 与 `[end+1:end+3]`（401-402），即最多前 2 后 2 个 block（编译阶段会再裁到每侧 1 块 / 320 字符，见 §2）。
- 术语/实体匹配（859-880）：对「prev+current+next 全文小写」做 **子串包含**匹配 termbase/entity 快照。注意 termbase 快照 bootstrap 时为空 `{"terms": []}`（`builders.py:155-165`），只有 `services/rebuild.py:241-268` 在 rebuild 时才从 `TermEntry` 刷新；entity registry 永远为空（`rebuild.py:271-273` 明确「reuse latest」）。所以在正常流程里 `packet_json.relevant_terms/relevant_entities` 几乎总是 `[]`（golden 快照 14 条记录全部为 0，见 `tests/golden/translation_prompts/*.json`）。
- `protected_spans=[]` 永远为空（`builders.py:632,755`），`budget_hint={"max_input_tokens":6000,"max_output_tokens":2500}` 写入但**全仓库无人读取**（grep 仅 contracts/builders 命中）。
- `heading_path` 只有章标题一级（`builders.py:305,626`），不含小节标题。
- `packet_ordinal` 为章内顺序号（429/451），写进 `packet_json.packet_ordinal / input_version_bundle / runtime_state`（639-652）。

### 1.3 ORM（`domain/models/translation.py`）
| 表 | 关键字段 | 行 |
|---|---|---|
| `translation_packets` | `chapter_id, block_start_id, block_end_id, packet_type(translate/retranslate/review), book_profile_version, chapter_brief_version, termbase_version, entity_snapshot_version, style_snapshot_version, packet_json, risk_score(恒 0.1), status(built/running/translated/invalidated/failed)` | 33-64 |
| `packet_sentence_map` | `(packet_id, sentence_id) PK, role(current/prev_context/next_context/lookback)` | 67-83；`LOOKBACK` 无人使用 |
| `translation_runs` | `packet_id, model_name, model_config_json, prompt_version, attempt(UNIQUE with packet), status(running/succeeded/failed), output_json, token_in, token_out, cost_usd Numeric(12,6), latency_ms, error_code` | 86-108 |
| `target_segments` | `chapter_id, translation_run_id, ordinal(UNIQUE with run), text_zh, segment_type, confidence, final_status(draft/review_required/finalized/superseded)` | 153-177 |
| `alignment_edges` | `sentence_id, target_segment_id, relation_type(1:1/1:n/n:1/protected), confidence, created_by` | 181-201 |
| `chapter_memory_proposals` | 见 §6 | 111-150 |
| `term_entries` | 见 §7 | 205-239 |

packet id 是 `stable_id("packet", document, chapter, block_start[, block_end], brief_version, termbase_version, entity_version[, chunk])`（`builders.py:600-608, 732-741`）——任一输入版本变化会产生新 id。

### 1.4 版本 / 重译的表示
- **attempt**：同一 packet 的每次 worker 调用产生一个 `TranslationRun`，`attempt = max(attempt)+1`（`infra/repositories/translation.py:137-141`），失败也占用一个 attempt（`translation.py:369-373`，测试 `tests/test_executor_translation_transactions.py:128-140` 断言 `(1,FAILED),(2,SUCCEEDED)`）。
- **运行时状态**：`packet.status` 只在成功时变为 `TRANSLATED`（`translation.py:628`）；失败时 packet 仍为 `BUILT`（测试 126 行），细粒度状态写在 `packet_json.runtime_state.substate`（`document_run_executor.py:1555-1581`）。`PacketStatus.FAILED` 枚举存在但**无人设置**（grep 仅读取）。
- **重译（rerun）**：review 动作把 packet 置 `INVALIDATED`、旧 `TargetSegment.final_status=SUPERSEDED`、句子回 `PENDING`（`services/actions.py:108-130`，唯一的 SUPERSEDED 写入点），随后 rebuild 用 `ContextPacketBuilder.build_packet(packet_type=RETRANSLATE, packet_id=同 id)` 重建（`builders.py:541-576`）。重译时通过 `compile_options.concept_overrides` 和 `rerun_hints`（→ `open_questions`）注入锁定术语与风格提示（`orchestrator/rerun.py:21-67`，`translation.py:283-284`，`memory_service.py:56-61`）。
- **「当前有效译文」选择**：所有读取方（前文上下文、review、glossary、export）都用 `final_status != SUPERSEDED` 过滤（如 `infra/repositories/translation.py:88`、`glossary_extraction.py:304`、`term_consistency.py:305`），**不是**「最新 attempt」。⚠ 若同一 packet 在未 invalidate 的情况下出现两个 SUCCEEDED run（例如 lease 丢失后旧 worker 的 persist 在 `assert_lease_held` 之前已 flush 但事务最终回滚——这是安全的；但 `execute_packet` 同步路径无 lease 保护、可被重复调用），两批 DRAFT segment 会同时被当作有效译文，`_build_prev_translated_blocks` 会把两份译文拼接（`translation.py:93-97`）。PLAN.md P4 也记录了「脚本：首个 edge；服务：最新 run」的选择规则尚未统一。

### 1.5 译文按句落库（sentence ↔ translation 对齐）
`_build_artifacts`（`translation.py:540-635`）：
1. 每个输出 `target_segments[i]` → `TargetSegment(id=stable_id("target-segment", run_id, ordinal))`，`temp_id→id` 映射（577-593）；`segment_type` 经 `_normalize_segment_type` 宽松映射，未知值落 `SENTENCE`（173-188）。
2. 每个 `alignment_suggestions` 做笛卡尔展开 `source_sentence_ids × target_temp_ids` 生成 `AlignmentEdge`，只保留合法句 id 与 temp id（595-617），去重（211-220）。`relation_type` 未知值落 `1:1`（191-208）。
3. 句子状态：在 `low_confidence_flags` 里的句子 → `REVIEW_REQUIRED`，否则 `TRANSLATED`（619-626）。
4. `TargetSegment.source_sentence_ids`（模型也返回）**不入库**，对齐完全依赖 `alignment_suggestions`。
5. worker 侧先把 `S1..Sn` 别名回映射为真实句 id，并**静默丢弃**非法 id（`translator.py:882-928`），因此 `OutputValidator.unknown_source_sentence_ids` 在 LLM worker 路径上永远为空。

---

## 2. 上下文编译（`services/context_compile.py` + `services/memory_service.py`）

### 2.1 输入来源
| 组件 | 来源表/对象 | 位置 |
|---|---|---|
| packet 本体（current/prev/next blocks、packet 级 relevant_terms/entities、chapter_brief 文本、style_constraints、open_questions） | `translation_packets.packet_json` | `infra/repositories/translation.py:60` |
| `prev_translated_blocks` | `packet_sentence_map(role=prev_context)` 的句子 → `alignment_edges` → 非 SUPERSEDED `target_segments`，按 block 聚合 | 同文件 71-135 |
| 章节记忆快照 | `memory_snapshots(scope=CHAPTER, type=CHAPTER_TRANSLATION_MEMORY, ACTIVE, max version)` | `infra/repositories/chapter_memory.py:24-35` |
| 文档术语表 | `term_entries(scope=GLOBAL, ACTIVE, target_term 非空)` → `RelevantTerm(lock_level=entry.lock_level)` | `glossary_service.py:95-105`，经 `memory_service.py:77-84`（异常吞掉返回 `[]`） |
| 重译提示 | `rerun_hints` → 追加到 `open_questions` | `memory_service.py:56-61` |

### 2.2 编译步骤（`ChapterContextCompiler.compile`，866-966，版本串 `"v4.section-brief-discourse-bridge"`）
1. `memory_blocks`：快照 `recent_accepted_translations` 尾部最多 4 条（492-515，常量 26）。
2. `memory_concepts`：快照 `active_concepts` → `ConceptCandidate`，经 `normalize_concept_candidate` 术语渲染覆盖（518-544）。
3. `_merge_concepts(memory, overrides)`：override 覆盖同名，排序（有 canonical 优先、times_seen 降序），截 12（547-566）。
4. `_filter_relevant_concepts`：只保留 source_term 在「current+prev 源文+prev_translated 源文」haystack 中出现（整串或去停用词后各 token 的复数变体都命中）的概念，截 4（188-204，`MAX_RELEVANT_CHAPTER_CONCEPTS=4`）。
5. `_merge_relevant_terms(packet.relevant_terms, concepts, document_terms)`：优先级 **文档术语表 < packet termbase < 章节概念**（569-604）。⚠ 任何带 `canonical_zh` 的章节概念都被写成 `lock_level="locked"`（588-594），与其 `status` 无关。排序 locked→preferred→suggested。
6. `_filter_relevant_terms`：同样的本地相关性过滤（207-222），**无数量上限**。
7. 前文译文：合并 memory_blocks + packet.prev_translated_blocks 去重（434-443）→ 清洗：删掉命中 style-drift 规则（源/译同时匹配）的块、删掉与 locked 术语冲突的块（446-489）→ 选择：`_should_keep_previous_translated_blocks`（319-333）为真取最后 2 条；否则若「记忆已累积 ≥3 条且多于 packet 自带前文」取最后 4 条，否则 0 条（336-350）。
8. 章节简介：`_preferred_chapter_brief` 取记忆里的 brief，除非 packet 的 `chapter_brief_version` 更新（413-431）；`_compress_chapter_brief` 压到 220 字符（353-375）；`_trim_chapter_brief` 决定 prompt 是否显示（378-410），不显示时在 `style_constraints` 打 `suppress_chapter_brief_in_prompt=True`（922-926）。
9. 源文上下文裁剪 `_trim_source_context`（276-316）：段落型 current 且已有前文译文时**丢弃 prev 源文**；否则 current ≤220 字符或以桥接词开头才保留；next 只在 current 以延续标点结尾/非终止标点时保留；每侧最多 1 块 320 字符。
10. `paragraph_intent`：`_infer_paragraph_intent_from_text`（607-642）用**硬编码英文标记词**（"refers to", "recipe book", "personal chef", "chapter 2 explores", "weight of evidence", "context engineering"…）判定 definition/analogy/transition/evidence/summary/exposition；只有 `definition|evidence` 会晋升进 `style_constraints`（`PROMOTED_PARAGRAPH_INTENTS`, 27, 662-670）。
11. `literalism_guardrails`：对 current 源文跑 heuristics pack 的 `style_drift_rules.source_pattern`，命中则把 `preferred_hint`/`prompt_guidance` 以 `" || "` 拼进 `style_constraints`（673-683，`services/style_drift.py:9-20`）。
12. `section_brief`（738-780）与 `discourse_bridge`（822-859）：模板句，基于 intent + `_active_referents`（locked 术语与概念中出现在 current 里的前 3 个，693-711）。
13. 产出 `CompiledTranslationContext`（`contracts.py:89-113`）附 `context_compile_version / memory_version_used / compile_metadata`，后者会写进 `TranslationRun.model_config_json`（`translation.py:555-561`）。

### 2.3 token 预算控制
**没有 token 计数**。全部是字符阈值（26-34 行常量：4 条记忆、每侧 1 块/320 字符、2 条前文译文、brief 220 字符、section brief 160）。`budget_hint` 不被使用。输出侧上限只有 provider 的 `max_output_tokens=8192`（`config.py:73`）。对 32 句的 references 大 packet 没有任何输入侧保护（32 句 × 引文可达数千 token，chat_completions 模式还要加上约 1.5k token 的 schema 文本，见 §4.1）。

### 2.4 `ChapterContextCompileOptions`（99-109）
`include_memory_blocks / include_chapter_concepts / prefer_memory_chapter_brief / prefer_previous_translations_over_source_context / include_paragraph_intent / include_literalism_guardrails / trim_source_context / trim_chapter_brief / concept_overrides`。生产路径全部默认；只有 rerun 传 `concept_overrides`（`translation.py:283`，测试 `tests/test_translation_worker_abstraction.py:2063`）。

---

## 3. Prompt：结构、13 个 profile、结构化输出与校验

### 3.1 构造入口
`build_translation_prompt_request(task, model_name, prompt_version, prompt_layout="paragraph-led", prompt_profile="tech-column-meta-v1", allow_compact_prompt=True)`（`workers/translator.py:595-789`）。`LLMTranslationWorker.translate` 只传 profile（872-878），**`prompt_layout` 始终为默认 `paragraph-led`**，sentence-led 布局仅在测试/golden 中出现。

### 3.2 System prompt 选择优先级（756-778）
`split_system_static_lines`（cn-native-*）> `fixed_system_prompt`（tech-column-meta-v1）> material-aware（按 `style_constraints.translation_material` 选 5 种之一，493-530）> compact（`COMPACT_SYSTEM_PROMPT`）> `profile.system_prompt or DEFAULT_SYSTEM_PROMPT`。
cn-native 系列的 system prompt 被拆成「Static Translation Contract」+「Dynamic Packet Guidance」两段（`TranslationSystemPromptParts.combined_prompt`, 275-298；动态行 559-576 含 material 行、section/chapter brief、paragraph intent）。`TranslationPromptRequest` 同时暴露 `system_prompt_static/dynamic`（245-254）——设计意图是给 provider 侧 prompt cache 用静态前缀，但 `_build_payload` 只发送合并后的 `system_prompt`（`openai_compatible.py:358,371`），**静态/动态拆分未被利用**。

### 3.3 User prompt 结构（顺序固定，710-754；空段落省略）
```
Core Translation Contract:            8 条（material-aware 为 5-7 条；compact 为 5 条）；有 guardrail 时第 3 条插入 guardrail 优先声明
Chinese Style Priorities: | Material-Specific Style Target:
Section-Level Scaffolding:            Section Brief / Previous|Current Paragraph Role / Relation to Previous / Active Referents
Paragraph Intent Signal:              Intent / Hint
Source-Aware Literalism Guardrails:
Memory and Ambiguity Handling:
<profile.extra_sections>              仅 role-style-brief-v3：Paragraph Intent Priorities / Literalism Guardrails
Open Questions and Rerun Hints:
Section Context:                      Heading Path / Chapter Brief / Translation Material
Locked and Relevant Terms:            "- src => tgt (lock_level)"，locked 优先
Relevant Entities:                    "- name [type] => zh|(unset)"
Chapter Concept Memory:               "- term => zh|(translation not locked yet) (status, seen=n)"；minimal 只留前 2 条有译法的
Previous Accepted Translations (same local context):   "- src_excerpt => tgt_excerpt"（minimal 截 140 字符、最多 2 条）
Previous Source Context:              "- B1 [type] text"
Upcoming Source Context:
Current Paragraph:                    "- P1 [paragraph] text"
Sentence Ledger:                      "1. [S1] sentence"；单句时 "- [S1] This is the only sentence in the current paragraph."
```
实际样本（golden `epub.json` 记录 6，`tech-column-meta-v1`）：
```
Core Translation Contract:
- Translate the current paragraph into natural Chinese at paragraph level first, then ensure sentence-level coverage is complete.
- Reuse the canonical Chinese rendering of any locked or previously established concept.
- If a concept already appears in Previous Accepted Translations, keep the same Chinese term unless the current packet explicitly redefines it.
- Preserve meaning, preserve protected spans, and maintain complete alignment coverage.
- Preserve inline formatting markers exactly: keep `backtick code`, **bold**, and *italic* markdown wrappers intact around the same content spans in the translation.
- Use the sentence ledger only for alignment, coverage, and low-confidence flags.
- Use only the sentence aliases shown below (for example: S1, S2) in source_sentence_ids and low_confidence_flags.sentence_id.
- Return JSON that matches the provided response schema.
Section Context:
- Heading Path: Chapter Three
Previous Accepted Translations (same local context):
- Chapter One => ZH::Chapter One
Current Paragraph:
- P1 [paragraph] Use the example carefully.
Sentence Ledger:
- [S1] This is the only sentence in the current paragraph.
```
句子别名：`S{n} → sentence.id`（618-620）；模型只见别名，worker 回映射。

### 3.4 Compact prompt（198-219）
仅 `compact_eligible` 的 profile（`role-style-v2`、`material-aware-minimal-v1`）且：全段落型、≤3 块、≤720 字符、1..4 句、无实体/open_questions、无 intent/guardrail 时启用；用 `COMPACT_SYSTEM_PROMPT` + 5 条契约，去掉风格/记忆段落。

### 3.5 13 个 profile 差异（`translation/prompt_profiles.py:61-196`）
| profile | system prompt 来源 | role_style 段 | 记忆处理段 | 其他 |
|---|---|---|---|---|
| current | 自定义短 system | 否 | 否 | 最原始 |
| role-style-v2 | 自定义 | 是 | 否 | compact 可用 |
| role-style-faithful-v4/v5/v6 | 三版逐步加长的「保真+具体意象」system | 是 | 否 | v5/v6 针对具体意象被抽象化（服务/菜单语言）加约束 |
| role-style-memory-v2 | DEFAULT_SYSTEM_PROMPT | 是 | 4 行 | |
| role-style-brief-v3 | 自定义 | 是 | 6 行 | 额外 2 个段落（Paragraph Intent Priorities、Literalism Guardrails 硬编码 4 条，含"大量证据表明"等中文示例） |
| material-aware-v1 | 按 material 生成 | 用 Material-Specific Style Target 替代 | 按 material 动态 | Section Context 加 `Translation Material:` |
| material-aware-minimal-v1 | material minimal 版 | 同上精简 | 精简 | compact 可用；单句时把 `[S1]` 内联进 Current Paragraph 并省略 Ledger（642-652） |
| cn-native-faithful-v1/v2/v3 | 静态 4-5 行 + 动态包指导 | 否 | 否 | 唯一使用 split system 的系列 |
| tech-column-meta-v1（默认） | 固定中文 system（34-40） | 否 | 否 | user prompt 与 `current` 相同 |

`PromptProfile` Literal（`translator.py:258-272`）与注册表一致性由 `tests/test_translation_prompt_golden.py:59-60` 保证；13 profile × 2 layout × 7 packet × 2 fixture 的 prompt 快照逐字节锁定在 `tests/golden/translation_prompts/{epub,pdf}.json`。

### 3.6 结构化输出
- Schema = `TranslationWorkerOutput.model_json_schema()`（`translator.py:787`；模型定义 `contracts.py:116-146`）：`packet_id, target_segments[{temp_id,text_zh,segment_type,source_sentence_ids,confidence?}], alignment_suggestions[{source_sentence_ids,target_temp_ids,relation_type,confidence?}], low_confidence_flags[{sentence_id,reason}], notes[{type,message}]`，`additionalProperties:false`。
- Responses API 模式：`text.format={type:json_schema,name:translation_worker_output,schema}`（`openai_compatible.py:378-384`）。
- chat/completions 模式：`response_format={type:json_object}` + **把整个 schema JSON 与一段输出契约追加到 user prompt 末尾**（430-442），流式模式下再把 `response_format` 删除（337-348）。
- 解析（444-499, 579-811）：`output_parsed` → output blocks 的 `json`/`text` → 直接 `json.loads` → ```` ```json ```` 围栏 → 平衡大括号扫描；再 `_unwrap_payload_candidate` 从 `translation/output/result/data/response` 包裹键里取（667-679）；`_normalize_translation_payload` 兼容 `type/target_type/source_sentence_id/source_ids/target_id…` 别名（681-765）；最后 `TranslationWorkerOutput.model_validate`，失败抛 `ProviderResponseFormatError`（248-251）。
- ⚠ **输出 `packet_id` 不与请求校验**（`generate_translation`/`persist_packet_result` 均未比较）。
- ⚠ 推理模型的 `reasoning_content` 在流式路径被累积后放进合成响应但无人使用（206-209），其 token 仍计费。

### 3.7 `OutputValidator`（`translation/output_validation.py:42-63`）
只检查**覆盖率**：每个 current 句必须被至少一条 alignment 指向存在的 temp_id；报告 `uncovered_sentence_ids / unknown_source_sentence_ids / unknown_target_temp_ids`。**不拒绝输出**：run 仍 `SUCCEEDED`，只把 `error_code="output_sentence_coverage_incomplete"` 写到 `TranslationRun.error_code`（`translation.py:452`），并把报告放进 `PACKET_TRANSLATED` 事件 payload（466-467）。未覆盖句子的 `sentence_status` 仍被置为 `TRANSLATED`（621-624）——由 review 的 OMISSION/ALIGNMENT 检查兜底（docstring 1-6）。不检查：空 `text_zh`、`text_zh` 是否含英文原文回显、markdown 标记是否保留、`packet_id`、翻译长度比。

---

## 4. Worker / Provider

### 4.1 请求构造（`workers/providers/openai_compatible.py`）
- 端点解析 `_resolve_endpoint`（293-301）：`…/responses` → Responses API；`…/chat/completions` 或 `…/v1` 或其他 → 追加 `/chat/completions`。默认 base_url `https://api.openai.com/v1/responses`（`config.py:84-86`，凭据 `ProviderCredential.base_url`）。
- 请求体（353-385）：`model`、messages/input、结构化输出声明、`max_tokens=max_output_tokens`（仅 chat 模式；Responses 模式**没有** `max_output_tokens` 字段）。**没有 `temperature/top_p/seed`**（grep 全仓库无 temperature）。`request_overrides`（settings `translation_openai_request_overrides`，`config.py:97`，如 `{"thinking":{"type":"disabled"}}`）以 dict 合并到顶层（243, 279）——可覆盖 model/messages 等任何键。
- 头：`Authorization: Bearer <key>` + `extra_headers`（287-291）。无 `User-Agent`、无幂等键、无请求 id。
- Echo/凭据来源见 §4.6。

### 4.2 超时、重试、退避（303-351）
- `timeout_seconds` 传给 `urlopen(timeout=)`——这是 **socket 级超时**（连接/单次读），不是整体截止时间；流式模式每行读都重置。settings 默认 60s，凭据默认 120s（`provider_credential.py:45`）。
- `_request_with_retries`：捕获 `ProviderTransportError`（含 HTTP/网络），HTTP 只对 `{408,409,429}∪5xx` 重试（350-351）；退避 `retry_backoff_seconds * 2**(attempt-1)`，同步 `time.sleep`，**无 jitter、无 Retry-After 解析、无最大退避上限**。settings 默认 `max_retries=1, backoff=1.5`（`config.py:71-72`）；凭据默认 2 次、2.0s。
- `ProviderResponseFormatError` 不在客户端重试（它是 RuntimeError 而非 TransportError，31-32），由工作项级别按 `classify_failure` → RETRY 重试。
- 传输异常映射（63-107）：`HTTPError→ProviderHTTPError(code, body)`、`URLError/OSError/Timeout→ProviderNetworkError`、`IncompleteRead` 有 partial 时**直接用残缺字节继续解析**（86-88，随后 JSON 解析失败抛 TransportError 触发重试）、`RemoteDisconnected→ProviderTransportError`。

### 4.3 失败分类（`workers/failures.py`）与执行器处置
`classify_failure`（40-72）：`ProviderHTTPError` 402 → PAUSE(`provider.insufficient_balance`)、401/403 → PAUSE(`provider.authentication_failed`)、`{408,409,425,429}`∪5xx → RETRY、其他 HTTP → FAIL；`ProviderResponseFormatError` → RETRY(`provider.malformed_response`)；`ProviderTransportError` → RETRY；`sqlalchemy OperationalError` → RETRY；`TimeoutError/ConnectionError` → RETRY；其余 → FAIL(`unclassified`)。
执行器 `_complete_failure`（`document_run_executor.py:1043-1100`）：pause_reason 非空 → `pause_run_system`（整 run 暂停，stop_reason 写入）；retryable → work_item `RETRYABLE_FAILED`（按 max attempts，待确认上限位置）；否则 `FAILED`。⚠ 客户端重试集合 `{408,409,429,5xx}` 与分类器 `{408,409,425,429,5xx}` 不一致（425 只在工作项级重试）。

### 4.4 usage / cost（501-562）
- token：chat 模式读 `prompt_tokens/completion_tokens/total_tokens`，Responses 读 `input_tokens/output_tokens`。
- 缓存 token：`prompt_cache_hit_tokens or cache_creation_input_tokens` 当 **hit**，`prompt_cache_miss_tokens or cache_read_input_tokens` 当 **miss**（513-519）。⚠ Anthropic 风格字段语义**反了**：`cache_read_input_tokens` 是命中、`cache_creation_input_tokens` 是未命中/写入。DeepSeek 字段正确。
- 两者都为 0 时 miss=token_in（521-522），因此 `_estimate_cost_usd` 559-560 的分支永不触发（死代码）。
- 单价来自 **settings**（`translation_input_cache_hit_cost_per_1m_tokens / translation_input_cost_per_1m_tokens / translation_output_cost_per_1m_tokens`，`config.py:74-76`），凭据表**无单价字段**（`provider_credential.py`），所以切换凭据/模型不会切换单价（`factory.py:34-35` 注释明说「prompt profile and token prices always come from settings」）。未配置输出单价时 `cost_usd=None`。
- `provider_request_id = response.id`；`raw_usage` 原样保存在 `TranslationUsage` 但**不入库**（`TranslationRun` 无该列；`LLM_CALL_COMPLETED` payload 也未包含）。

### 4.5 并发与连接
- 执行器：每个 run 一个 run 线程，每个 work_item 一个 daemon 线程（`document_run_executor.py:288-309`）；translate 并发上限 `default_max_parallel_workers=8`，可被 run budget 的 `max_parallel_workers` 覆盖（103, 1124-1131, 1203-1208）；frontier 规则「每章最多一个活动 packet」（1436-1520），所以 8 并发 = 8 个不同章节同时翻。
- 无 httpx client、无连接池：每次 `urlopen` 新建连接；8 线程 = 8 条独立 TCP/TLS 连接，无 keep-alive。
- `OpenAICompatibleTranslationClient` 是无状态 dataclass，被 8 线程共享是安全的（唯一共享对象 `UrllibJSONTransport` 也无状态）。
- `TranslationWorkerProvider.get()`（`factory.py:150-183`）用 `threading.Lock` + 凭据 `_revision` 缓存 worker；`_revision` 是**进程内全局计数器**（`provider_credentials.py:302-311`），多进程/多实例部署下另一进程改凭据不会使本进程缓存失效。
- DB 连接：三段事务保证 LLM 调用期间不持有连接（测试 `test_executor_translation_transactions.py:104-112` 断言 checked-out 为 0）；引擎 `pool_size=10, max_overflow=20`（`infra/db/session.py:24-26`）。

### 4.6 Worker 构造与凭据
- `build_translation_worker(settings)`（`factory.py:68-99`）：`translation_backend=echo|openai_compatible`；后者缺 key 抛 ValueError。
- `build_worker_from_credential(record, settings)`（102-133）：解密 `api_key_ciphertext`（Fernet，`services/secrets.py`），连接参数来自凭据，profile/单价/`request_overrides` 来自 settings；`prompt_version` 固定 `"p0.openai-compatible.v1"`（17, 122）。
- `resolve_translation_worker`（136-147）→ `resolve_active_credential`（`provider_credentials.py:314-324`）：无活动凭据时从 .env 自动建一条并激活（327-366）。
- 执行器通过 `translation_worker_resolver` 每次 `_workflow_service(session)` 时解析（`document_run_executor.py:216-217`）；CLI 用 `resolve_translation_worker`（`cli.py:23,116`）；API 用 `app.state.translation_worker_provider`（`app/main.py:107`）。
- 密钥：`Settings` 源里显式剔除 shell 环境的 `OPENAI_API_KEY`，只从 `.env` 读（`config.py:40-47, 121-134`；测试 `test_settings_read_openai_key_from_dotenv_and_ignore_shell_env`）。Fernet 主密钥 `BOOK_AGENT_SECRET_KEY` 缺失时**自动生成并写回项目 .env**（`secrets.py:67-93`）。`api_key_preview` 解密完整明文再截断（`provider_credentials.py:184-192`）。
- 连接测试 `test_credential_connection`（195-291）：发一次 `generate_structured_object`，解析失败视为 OK（264-274）。

### 4.7 Echo worker（`translator.py:792-837`）
输出 `"ZH::" + 原文`、1:1 对齐、confidence 0.75/0.95，用于流水线验证与 golden。防误用：
- `TranslationService(worker=None)` **默认回落到 Echo**（`translation.py:262`）——任何忘记传 worker 的调用方都会把 `ZH::…` 当译文写库；`DocumentWorkflowService` 默认 `translation_worker=None`（`workflows.py:83`）。API/执行器/CLI 都显式解析了 worker，但 `ChapterMemoryBackfillService` 内部新建的 `TranslationService` 也用 Echo（`chapter_memory_backfill.py:42-46`，它不调用 worker，仅借用 `write_chapter_memory`，无害但耦合）。
- `translation_backend` 默认 `"echo"`（`config.py:66`），首次启动 `_bootstrap_from_settings` 会把 Echo 凭据激活（`provider_credentials.py:334-349`）——生产环境若忘配 .env 会静默用 Echo 翻译整本书。CLI 的 `glossary-extract/term-consistency` 有显式拒绝 Echo（`cli.py:155-157, 183-185`），但 `translate` 命令没有。
- 测试 `test_settings_worker_matches_credential_worker_shape` 等在 `tests/test_worker_factory.py`。

---

## 5. 事务模型

### 5.1 三段（执行器路径 `document_run_executor.py:971-1020`）
| 段 | 事务 | 写入 |
|---|---|---|
| prepare | session_scope #1（提交） | `events(llm.call.started)`；读 packet、sentence_map、sentences、target_segments/alignment_edges（前文）、memory_snapshots、term_entries |
| call_worker | 无 session | 无 |
| record_worker_failure（失败分支） | session_scope #2'（提交） | `translation_runs(FAILED, attempt=n, error_code=分类原因)`、`events(llm.call.failed)`；**不改** packet.status |
| persist | session_scope #2（提交） | `events(llm.call.completed)`、`translation_runs(SUCCEEDED)`、`target_segments`、`alignment_edges`、`sentences.sentence_status/updated_at`、`translation_packets.status=TRANSLATED`、`chapters.status=TRANSLATED`（章内全部 packet 已译时，`infra/repositories/translation.py:163-170`）、`events(packet.translated)`、hook：`events(glossary.violation)*`、`chapter_memory_proposals`（create_or_replace + 退役同 packet 其他 pending）、（auto_commit 时）`memory_snapshots` |
| complete_translate_success | session_scope #3 | `work_items` 释放、`document_runs.status_detail_json.control_counters`（token/cost 累加）、`run_audit_events`、`translation_packets.packet_json.runtime_state` |

租约校验位置：仅在 persist 事务末尾 `assert_lease_held(lease_token)`（1008-1010，`run_execution.py:261-263` → `lock_active_lease` 行锁），租约丢失抛 `LeaseLostError` → 整个 persist 事务回滚、结果丢弃（`_execute_claimed_work_item` 942-950；测试 `tests/test_executor_lease_loss.py:88-131`）。⚠ `record_worker_failure` 事务**无租约校验**：过期 worker 仍会写入 FAILED run（占用 attempt 号）与失败事件。⚠ prepare 事务的 `llm.call.started` 也不校验（影响小）。

### 5.2 同步路径 `execute_packet`（`translation.py:279-309`）
单 session 内 prepare→call→persist（DB 连接横跨 LLM 调用），供 `DocumentWorkflowService.translate_document`（`workflows.py:232-268`）/CLI/rerun 使用；失败时 `record_worker_failure` 后 re-raise，由调用方决定回滚——⚠ 若调用方回滚，FAILED run 与失败事件一起丢失（与执行器路径行为不一致）。

### 5.3 幂等性
- `TranslationRun.id = stable_id(packet, attempt)`、`TargetSegment.id = stable_id(run, ordinal)`、`AlignmentEdge.id = stable_id(sentence, target)`：同一 attempt 重放会因 PK 冲突失败而非重复。
- `next_attempt` 读 `max(attempt)`（非锁定）+ UNIQUE(packet, attempt)：并发写同一 packet 时后者报唯一约束冲突（执行器 frontier 保证不并发；同步路径无保护）。
- 执行器在 prepare 前检查 `packet.status != BUILT` 则跳过（981-990）；但 persist 前**不再检查**（依赖 lease）。

---

## 6. 记忆系统

### 6.1 `ChapterMemory` 模型（`translation/chapter_memory.py:49-60`）
`schema_version=1, chapter_id, chapter_title, heading_path, chapter_brief, chapter_brief_version, active_concepts[dict], recent_accepted_translations[dict], last_packet_id, last_translation_run_id, extras`。
- `active_concepts[i]`：`source_term, canonical_zh, status(candidate|locked|…), confidence, first/last_seen_packet_id, packet_ids_seen, times_seen, mention_count, packet_mention_counts`（`translation.py:725-736`）。
- `recent_accepted_translations[i]`：`packet_id, block_id, source_excerpt, target_excerpt, source_sentence_ids`（679-687），按 packet 去重、尾部保留 4（`chapter_memory.py:19,136-141`）。
- 合并策略：brief 版本 ≥ 现有才采用（113-134）；概念按 source_term 小写 upsert、非空字段覆盖、times_seen 取 max（149-173）。
存储：`memory_snapshots(scope_type=CHAPTER, scope_id=chapter, snapshot_type=CHAPTER_TRANSLATION_MEMORY, version, content_json, status)`，每次变更 supersede 旧行 + 新建 version+1（`infra/repositories/chapter_memory.py:37-69`，id 由 (doc, chapter, version) 稳定生成）。

### 6.2 快照生成 / 更新时机
| 时机 | 调用 | 说明 |
|---|---|---|
| bootstrap | `ChapterTranslationMemoryBuilder.build`（`builders.py:339-369`） | seed v1，只含 brief |
| packet 翻译后 | `ChapterMemoryProposalHook`（`translation.py:863-887`） | 只写 **proposal**；`auto_commit_memory=True` 时立刻 `commit_approved_packet_memory` |
| review 通过 | `ReviewService._commit_review_approved_memory`（`review.py:158-175`） → `commit_review_approved_chapter_memory`（`memory_service.py:220-255`） | 取每个 packet 最新 SUCCEEDED run 的 proposal，按 created_at 顺序逐个 merge 提交 |
| review 有阻断问题 | `_reject_review_blocked_memory`（`review.py:177-196`） | 相关 packet 的 pending proposal → REJECTED |
| 概念锁定 | `ChapterConceptLockService.lock_concept`（`chapter_concept_lock.py:51-140`） | 直接 supersede 出新版本 |
| backfill | `ChapterMemoryBackfillService`（§6.5） | 直接写 |
| 人工审批 | `ChapterMemoryProposalService.approve/reject`（`application/memory_proposals.py:71-132`） | 走 `MemoryService.approve_proposal` + `AuditEvent` |

### 6.3 Proposal 流程
- 谁提：翻译 hook，每个 SUCCEEDED run 一条（`chapter_memory_proposals.translation_run_id` UNIQUE，`models/translation.py:113`），`base_snapshot_id/version` 记录当时快照；同 packet 的旧 pending 自动 REJECTED（`chapter_memory.py:126-129`）。
- 内容：`_build_chapter_memory_content_json`（`translation.py:658-700`）= 当前快照 + brief 更新 + 本 packet 的 recent translation + `_merge_active_concepts` 抽取的概念候选（702-761，最多 12，候选来自 `_extract_concept_candidates` 799-819：2-4 gram、必须含 pack 的 `concept_hint_keywords`、尾词属 `concept_headwords`、前置词属 `concept_modifiers`/缩写/专名，且不含 `STOPWORDS`——⚠ `STOPWORDS` 在 `translation.py:68-131` 硬编码，未进 pack）。
- 谁批：(a) review 自动批（无人工）；(b) API 人工批（`memory_proposals.py`，记 `AuditEvent(object_type="chapter_memory_proposal")`）；(c) `auto_commit_memory=True`（仅 golden/测试/`translation_auto_commit_memory` 参数）。
- 冲突检测：单条批准 `commit_approved_packet_memory` 校验 `latest.version == proposal.base_snapshot_version` 否则 ValueError「drifted」（`memory_service.py:198-206`）；批量 review 提交 `commit_review_approved_chapter_memory` **不校验**，直接 merge（220-255）。⚠ 两条路径语义不一致：review 已批量提交后，用户再从 API 批准一个旧 proposal 必然报 drifted。

### 6.4 概念锁 / 自动锁
- `ChapterConceptLockService.lock_concept`（`chapter_concept_lock.py:51-140`）：写两处——记忆快照 `active_concepts`（status=locked, confidence=1.0，截 12）+ `term_entries(scope=CHAPTER, scope_id=chapter, term_type=CONCEPT, lock_level=LOCKED)` 版本化 upsert（142-199）。`canonical_zh` 先过 `normalize_term_rendering`（术语渲染覆盖，如 `智能体式AI→智能体AI`，`term_normalization.py:16-26`）。
- `ChapterConceptAutoLockService`（`chapter_concept_autolock.py:394-471`）：候选 = 记忆里 `canonical_zh` 为空、`mention_count ≥ min_times_seen(2)`、未被 LOCKED 的概念（473-509）；例句 = 含该词的句子 + 对齐译文，最多 5（511-555）；解析器链 `FallbackConceptResolver(OpenAICompatibleConceptResolver → HeuristicConceptResolver)`（324-366；LLM 解析异常时降级到启发式，`tests/test_concept_resolver_fallback.py`）；单复数兄弟词复用已锁译法（571-605）。启发式解析器（75-208）用锚点正则（`称为/叫做/…::`）、最长公共子串、共识 token 三级推断。调用方：review repair 的 `UNLOCKED_KEY_CONCEPT` 自动修复（`application/review_repair.py:646-660`，`min_times_seen=1`）。
- 概念 → prompt 的路径：记忆 `active_concepts` → `_memory_concepts` → 相关性过滤 → `Chapter Concept Memory:` 段；有 `canonical_zh` 的还进 `Locked and Relevant Terms:`（locked）。

### 6.5 Backfill（`services/chapter_memory_backfill.py`）
用途：不重跑 LLM，用已有 SUCCEEDED run 的非 SUPERSEDED target segments 按 packet 顺序（Block.ordinal）**重放** `write_chapter_memory`，从 `last_packet_id` 检查点之后继续（173-188）；`reset_existing=True` 时先重新 seed。用于记忆损坏/schema 迁移/proposal-first 模式下想直接得到完整快照的场景。⚠ 它绕过 proposal 机制直接 supersede 快照，会使已有 pending proposal 的 `base_snapshot_version` 过期（触发 §6.3 的 drifted）。调用方：仅测试（`tests/test_translation_worker_abstraction.py:2198,2253`）；生产代码无调用（grep 无命中）。

---

## 7. 术语系统

### 7.1 `term_entries` 表（`models/translation.py:205-239`）
`document_id, scope_type(GLOBAL|CHAPTER), scope_id, source_term, target_term, term_type(person/org/place/concept/title/abbr/other), lock_level(suggested/preferred/locked), status(active/superseded/rejected), evidence_sentence_id, target_variants_json[list], version`。无唯一约束；id 由 `(doc, "global"|chapter, source_key, version)` 稳定生成（`glossary_service.py:136-142, 206-213`；`chapter_concept_lock.py:180-187`）。

### 7.2 三条写入通道
1. **启发式挖掘** `terminology_miner.mine_terms`（`services/terminology_miner.py:118-163`，纯规则 1-3gram、频次/专名/缩写/定义模式加权，top 200）→ `GlossaryService.upsert_candidates`（`glossary_service.py:109-160`）写 `SUGGESTED, target_term=""`。（**无生产调用方**：grep 全 `src/` 仅 `terminology_miner.py`/`glossary_service.py` 自身定义，其余只在测试中使用——即 M2.5 挖掘通道是死路径。）
2. **LLM 抽取 + CSV 审核回路**（`services/glossary_extraction.py`，commit `6074d16`）：按章把可翻译散文句拼成 ≤12000 字符块（151-166），每块调 `generate_structured_object`（system prompt 55-71，schema 73-92），提案经 `merge_proposals`（169-228）按 `source_term_key` 归并、用 `SourceTermIndex` 在源文中整词最长匹配计数、0 次出现的丢弃（模型臆造）、<2 次且非人名/机构/书名的丢弃、多数译法为准、已译书报告 `current_mismatches`。`write_glossary_csv`（327-348）输出 `lock,source_term,target_term,term_type,occurrences,current_mismatches,alternative_targets,note`，`recommended_lock`（人名/缩写/模型标 required）预勾选；`read_glossary_csv`（357-368）只读 `lock` 为真值（y/yes/1/true/x/是/锁定）的行。CLI：`glossary-extract --output x.csv` / `glossary-lock --input x.csv`（`cli.py:153-179`）→ `lock_term(..., LOCKED)`。
3. **自动一致性 pass** `TermConsistencyService`（`services/term_consistency.py`，commits `62e4da6`→`2176d51`）：见 §7.6。

### 7.3 变体匹配（`domain/terminology/matching.py`）
- 源侧 `source_term_key`（85-87）：token 化（`&`→and、去所有格、小写），前 n-1 个 token 直接拼接（忽略空格/连字符），最后一个 token 单数化（48-62，含不规则表与 -s 结尾单数词表）。`SourceTermIndex.find`（113-129）：整 token、最长优先、位置独占（"Inverted Head & Shoulders" 不再计入 "Head & Shoulders"；"bull" 不匹配 "bullish"），`MAX_TERM_TOKENS=6`。
- 目标侧 `normalize_target`（142-148）：NFKC、去空白与标点、Latin casefold；`target_has_rendering` 任一 accepted variant 子串命中即视为出现。
- 使用方：review TERM_CONFLICT（`review.py:350-405`）、glossary extraction、term consistency。**不使用**变体匹配的：翻译时 `GlossaryViolationHook` 的 `detect_violations`（§7.5）与 packet 构建期 `_match_terms`（子串）。

### 7.4 翻译时注入
`MemoryService._document_prompt_terms` → `GlossaryService.prompt_terms`（所有 ACTIVE GLOBAL 且 target 非空的条目，含 SUGGESTED/PREFERRED/LOCKED）→ `_merge_relevant_terms`（优先级最低，被 packet termbase 与章节概念覆盖）→ 本地相关性过滤 → prompt `Locked and Relevant Terms:`。`target_variants_json` **不注入 prompt**。测试 `tests/test_translation_glossary_injection.py:109-176`。

### 7.5 译后校验（`GlossaryViolationHook`，`translation.py:849-860, 491-538`）
- 每 packet 查一次 `get_locked_terms`（仅 GLOBAL+LOCKED；`glossary_service.py:63-79`），把 packet 全部源句拼接、全部 target 拼接后调 `detect_violations`（`glossary_enforcement.py:52-92`）：源词计数（Latin 词边界正则、大小写不敏感，136-157）> 目标词计数（**精确子串、不用 normalize_target、不认 variants**）即为违规，`severity_hint=hard|partial`；每条违规 emit `glossary.violation` 事件（**不阻断、不重译、不改句子状态**）。hook 内异常整体吞掉（859-860）。
- ⚠ 与 review 的判定标准不同（review 用 `target_has_rendering` + variants + `_should_skip_locked_term_conflict` 的修饰词豁免，`review.py:739-759`），同一译文可能翻译期报违规、review 期不报，反之亦然。
- ⚠ 事件 `GLOSSARY_INJECTED/RESOLVED/SUPPRESSED` 在 `event_kinds.py:39-42` 注册但全仓库无人 emit（死目录项）。

### 7.6 term_consistency 自动一致性 pass（最新 5 个提交）
流程（`term_consistency.py:240-294`）：
0. 先 `list_document_entries` 触库，schema 问题在任何 provider 调用前失败（242-243，commit `1cce354`）。
1. Extract：复用 `GlossaryExtractionService.extract`（全书再跑一遍抽取，费用与 §7.2 相同）。
2. Survey（320-347）：对每个「术语 × 含该术语的译文片段」（`SegmentUnit` 由 target_segment+对齐源句构成，298-316；`min_segments=2`）以 16 项/批发送，模型只报告 `rendering_zh`（必须是译文中**逐字存在**的连续片段，`exact_span` 158-161）和 `sense=term|other`；非 CJK 且非字母的片段丢弃。
3. Decide（`decide_canonical` 164-184）：`sense=term` 的观察计数；<2 次跳过；>3 种译法视为语境依赖跳过；最高票 ≥70% 且无并列（或并列中含模型提议）才定为 canonical。`renders_canonical`（187-198）：片段包含 canonical 或被 canonical 包含（短形式）都算一致（commit `94441fb`）。
4. Replace（374-437，`replace_rendering` 207-219）：仅替换 `sense=term`、非一致、≥2 字、与 canonical 字符重叠率 >50%（commit `0ee5b51`）、片段在该 segment 中出现次数 == 源文中该术语出现次数（无歧义）的片段；按 canonical 长度降序处理；每个改动的 segment emit `glossary.updated` 事件（before/after/replacements），然后直接改 `TargetSegment.text_zh`（**不产生新 run/attempt，不 supersede，原译文只存在事件 payload 里**）。
5. Record：`lock_term(..., lock_level=PREFERRED)`（commit `2176d51`，270-284）。
6. Report：Markdown（459-498），严格一致率前后对比、未替换原因、跳过原因中文化。
CLI：`term-consistency --document-id --report x.md [--dry-run]`（`cli.py:181-198`）。测试 `tests/test_term_consistency.py`（4 个用例覆盖多数替换、跳过、歧义、修饰词/异概念不替换）。

### 7.7 LOCKED vs PREFERRED vs SUGGESTED 语义（源码事实）
| 层 | LOCKED | PREFERRED | SUGGESTED |
|---|---|---|---|
| prompt 注入（`prompt_terms`） | 注入，排最前 | 注入 | 注入（若 target 非空；miner 产生的空 target 被过滤） |
| 翻译时违规事件 `get_locked_terms` | 检查 | 不检查 | 不检查 |
| review `TERM_CONFLICT`（blocking, severity HIGH） | 检查（含 variants） | 不检查 | 不检查 |
| review `UNLOCKED_KEY_CONCEPT` 豁免 | 豁免 | 不豁免 | 不豁免 |
| context compile `_conflicts_with_locked_terms` 过滤前文译文 | 生效 | 不生效 | 不生效 |
| `upsert_candidates` 覆盖保护 | 保护 | 保护 | 可被再次 upsert（幂等跳过） |
| `unlock_term` | → SUGGESTED（保留 target） | 无操作（只处理 LOCKED，`glossary_service.py:238-241`） | — |
| 章节级概念锁 `TermEntry(scope=CHAPTER)` | 只写 LOCKED | — | — |
结论：PREFERRED = 「只进 prompt、不做任何强制」，这是 `2176d51` 为避免 52 个 TERM_CONFLICT 阻断导出而选的折中。

### 7.8 跨章一致性
- 文档级术语（GLOBAL）对所有章生效；章节级概念锁（CHAPTER）只在本章 prompt 与 review 中生效（`glossary_service.py:63-70` 明确排除；`rebuild.py:_serialize_terms` 也按章过滤）。
- 没有「书级概念记忆」：`active_concepts` 严格按章；同一概念在 8 章并行首译时各自决定译法，只有事后（review autolock 每章各锁一次、或 term-consistency pass）才统一。

---

## 8. 风格漂移与启发式包

### 8.1 包结构（`translation/heuristics/__init__.py`、`tech-book-default.json`）
`HeuristicsPack{name, style_drift_rules[16], term_rendering_overrides[1], concept_hint_keywords[18], concept_headwords[15], concept_modifiers[16]}`；`load_heuristics_pack` 用 `importlib.resources` + `@cache`（87-92）；`heuristics_pack_for_document` 读 `document.metadata_json["translation_heuristics_pack"]`（95-99）；`DEFAULT_HEURISTICS` 模块级加载（102）。

### 8.2 style_drift_rules 内容
16 条，全部针对一本「agentic AI / context engineering」书：`agency_autonomy_collapse`、`agentic_ai_stiff_term`、`context_engineering_literal`、`weight_of_evidence_literal`、`contextually_accurate_outputs_literal`、`knowledge_timeline_literal`、`emerging_term_scaffolding_literal`、`durable_substrate_literal`、`in_context_information_literal`、`shift_from_to_literal`、`vantage_point_literal`、`immeasurably_high_literal`、`fun_anecdote_literal`、`profound_responsibility_literal`、`consistency_care_service_literal`；每条 `source_pattern`（英文正则）+ `target_pattern`（中文正则）+ `preferred_hint` + `message` + `prompt_guidance`。其中 `goal_reason_how_literal` 的 source_pattern 是三段式 `.*` 串联（127 行），实际几乎不可能命中。

### 8.3 用途
1. **翻译前**：`source_aware_literalism_guardrail_lines`（`style_drift.py:9-20`）——只看 `source_pattern`，命中即把 hint/guidance 注入 prompt（`Source-Aware Literalism Guardrails:` 段）。⚠ 始终用 `DEFAULT_HEURISTICS`（`style_drift.py:6`、`term_normalization.py:23`、`translation.py:65-67`），**不按文档选包**（PLAN.md P3.4 最后一条已承认）。
2. **编译时清洗**：`_has_stale_literalism_memory` 丢弃源/译都命中的前文译文块（`context_compile.py:446-454`）。
3. **review**：`_style_drift_issues` 按文档选包（`review.py:561`），产生 `STYLE_DRIFT` issue，rerun 时 `style_hints_for_issue` 把 hint/guidance 变成 `open_questions`（`rerun.py:43-67`）。
4. `term_rendering_overrides`：`normalize_term_rendering` 把「已知坏译法」替换为首选（`term_normalization.py:16-26`），用于概念锁、review 期望值、概念候选归一化。

### 8.4 其他硬编码的「书籍特定」启发式（未进 pack）
- `context_compile.py:617-641` 段落意图标记词（"recipe book", "personal chef", "chef", "pantry", "chapter 2 explores", "context engineering"）。
- `builders.py:49-58` `CHAPTER_BRIEF_PRIORITY_KEYWORDS`（agentic/context/llm/sql…）决定章节简介选句。
- `builders.py:215-222` 书籍类型推断关键词。
- `translation.py:68-131` STOPWORDS。
- `chapter_concept_autolock.py:49-61` 中文停用短语/泛词。
- `prompt_profiles.py:34-40` 默认 profile 的领域人设；`role-style-brief-v3` 的 Literalism Guardrails 段（142-147）。

---

## 9. 质量特征：一致性保证与漏洞

### 9.1 保证
- 章内顺序：frontier「每章一个活动 packet」+ 按 `packet_ordinal` 排序（`document_run_executor.py:1491-1520`）⇒ 同章 packet 严格串行，前一 packet 的译文在下一 packet prepare 时已 commit，可作为 `prev_translated_blocks`。
- 前文译文只取 `prev_context` 句子（前 2 块）——编译后最多 2 条（或记忆累积 ≥3 时 4 条）。
- locked 术语 + 概念锁 + `_conflicts_with_locked_terms` 清洗 + review TERM_CONFLICT 阻断 + rerun `concept_overrides`。
- prompt/上下文 golden 快照保证 profile 修改可审计。

### 9.2 漏洞
1. **首轮翻译记忆为空**（§0）：proposal-first 下章节记忆直到 review 才提交；`Chapter Concept Memory` 与「记忆累积 ≥3 条」分支在首轮从不生效；概念候选也只在 review 后才出现，`UNLOCKED_KEY_CONCEPT` → autolock → 重译是事后修补。
2. **8 章并行无跨章同步**：无书级记忆；文档术语表只有用户/CLI 主动 lock 后才有内容（bootstrap 为空）；同一术语在 8 章可能得到 8 种译法。唯一的事后统一手段是 `term-consistency`（PREFERRED，不阻断）与 review autolock（按章）。
3. **termbase/entity 快照实际无效**（§1.2）。
4. **相关性过滤是词面匹配**：术语/概念只在源文出现时注入；代词指代或同义改写的段落拿不到。
5. **OutputValidator 不拒绝**：覆盖不全、空译文、原文回显都会以 SUCCEEDED 落库，`sentence_status=TRANSLATED`。
6. 大 packet（32 句）+ 单一 system prompt 没有分段输出保护；`max_tokens=8192` 截断时 JSON 不完整 → `ProviderResponseFormatError` → 整包重试（同样的输入很可能同样截断）。
7. 上下文裁剪偏激进：段落型 packet 一旦有前文译文就**丢弃源文 prev 块**，且 chapter brief 会被抑制；对定义/转折以外的长段落只剩标题路径 + 2 条前文译文。
8. 违规检测与 review 判定标准不一致（§7.5）。
9. `Sentence Ledger` 单句时不给原文（"This is the only sentence…"），模型需从 Current Paragraph 推断；对 merged packet（多 block）也只给一份 ledger。

---

## 10. 成本

- **token 计数**：只来自 provider `usage`（无本地 tokenizer 估算）；Echo 全 0。
- **单价**：settings 三个 `*_cost_per_1m_tokens`（`config.py:74-76`），全局而非按模型/凭据；缓存命中价可选。
- **计算**：`_estimate_cost_usd`（`openai_compatible.py:539-562`）= hit×hit_price + miss×miss_price + out×out_price，8 位小数。
- **落库**：`translation_runs.token_in/token_out/cost_usd/latency_ms`（`translation.py:566-569`）；`events(llm.call.completed).payload{token_in,token_out,total_tokens,cost_usd,latency_ms,provider_request_id}`（426-446）；`document_runs.status_detail_json.control_counters`（`run_execution.py:279-332` 累加，供 `enforce_budget_guardrails` 500-560 判断 `max_total_cost_usd/token_in/token_out/no_progress` 并 pause）。
- **Rollup**：PostgreSQL 物化视图 `cost_rollup_by_run / cost_rollup_by_chapter`（`alembic/versions/20260412_0021_cost_rollup_materialized_view.py:22-53`，从 `events` 表 `kind='llm.call.completed'` 的 JSON payload 聚合），API `/runs/{id}/cost` 每次调用 `refresh_cost_rollup()`（`app/api/routes/run_cost.py:41-43`），SQLite 返回 501。`application/analytics.py:196-208` 另有基于 `translation_runs` 的求和（两套口径）。
- **未计费的调用**：概念解析（`chapter_concept_autolock.py` 的 usage 只进 `ConceptAutoLockRecord`）、glossary extraction / term survey（只在 CLI 输出 token 数）、连接测试——这些 `generate_structured_object` 调用**不 emit `llm.call.*` 事件**，不进 rollup，不受 run 预算约束。
- 失败调用（超时/5xx 后已消耗的 token）不计。

---

## 11. 缺失的生产要素（按源码确认「没有」）

| 要素 | 现状 |
|---|---|
| Prompt 版本化 | `prompt_version` 是自由字符串（settings `p0.echo.v1` / 凭据固定 `p0.openai-compatible.v1`），与 profile/模板内容无绑定；实际发送的 system/user prompt **不入库**（`TranslationRun` 只有 `output_json`）；变更只能靠 golden 测试察觉 |
| Provider 可观测性 | 只有 `llm.call.*` 事件 + `latency_ms`；无直方图/错误率/按 provider 维度指标；无结构化日志（`openai_compatible.py` 无 logger）；无 request/response 原文留存；`raw_usage` 不入库 |
| 速率限制/并发控制 | 仅 `max_parallel_workers`（默认 8）；无按 provider 的 RPM/TPM 限流、无全局信号量、无 429 `Retry-After` 遵守、无退避上限/jitter |
| 缓存 | 无请求级缓存（相同 packet 重译=重新付费）；provider 端 prompt cache 未利用静态/动态拆分 |
| 幂等 | 无 provider 幂等键；DB 侧依赖 stable_id + lease |
| 密钥安全 | Fernet 密钥自动生成写入 .env；无轮换流程（`SecretKeyError` 提示重输）；预览需解密全文 |
| 流式输出 | 仅作为「兼容 NIM 的传输 workaround」（累积后合成非流式响应），不向上层/前端流式输出；且流式模式**放弃了 `response_format`** |
| 结构化输出强约束 | chat 模式仅 `json_object` + 提示词 schema；无 `json_schema` strict、无 tool-call 方案 |
| 采样参数 | 无 temperature/seed → 无确定性控制、无法做 A/B 或回归复现 |
| 输入预算 | 无 token 估算、无超长 packet 拆分 |
| 输出质量门 | Validator 不拒绝；无「译文含大量英文/空串」检查；无长度比检查 |
| 多进程 | worker 缓存失效依赖进程内 `_revision`；执行器为线程模型 |
| 健康检查 | 仅手动 `test_credential_connection` |

---

## 12. Bug / 不一致 / 风险清单（带位置）

### 12.1 明确 bug
1. `workers/providers/openai_compatible.py:513-519` —— Anthropic 风格缓存字段 `cache_creation_input_tokens`（写/未命中）被当作 hit，`cache_read_input_tokens`（命中）被当作 miss，价格算反。
2. `workers/providers/openai_compatible.py:559-560` —— 死分支：521-522 已保证 miss=token_in 时 hit=0 且 miss≠0。
3. `services/glossary_enforcement.py:77-80` 与 `services/review.py:350-405` —— 同一 LOCKED 术语两套判定（子串 vs normalize+variants+修饰词豁免）。
4. `services/memory_service.py:198-206` vs `220-255` —— 单条批准校验 base version、批量 review 提交不校验；review 之后 API 批准旧 proposal 必然 ValueError。
5. `services/translation.py:365-404` —— `record_worker_failure` 无租约校验，过期 worker 会写入 FAILED run 占用 attempt。
6. `services/context_compile.py:585-594` —— 章节概念只要有 `canonical_zh` 即以 `locked` 注入 prompt，与概念 `status` 无关。
7. `workers/providers/openai_compatible.py:86-88` —— `IncompleteRead` 有 partial 时用残缺 body 继续，随后 `json.loads` 失败抛 `ProviderTransportError("non-JSON")`，错误信息误导排障。
8. `translation/heuristics/tech-book-default.json:127` —— `goal_reason_how_literal` 的 source_pattern 三段 `.*` 串联几乎不可命中（且 `re.DOTALL` 未开）。

### 12.2 不一致 / 设计债
9. 客户端 HTTP 重试集 `{408,409,429,5xx}`（`openai_compatible.py:351`）vs 分类器 `{408,409,425,429,5xx}`（`failures.py:20`）。
10. 默认 profile `tech-column-meta-v1` 领域硬编码（`prompt_profiles.py:34-40`、`config.py:69`、`translator.py:601,854`）。
11. `prompt_version` 不随 profile 变化（`factory.py:17,122`）；prompt 文本不入库。
12. `budget_hint`（`builders.py:636,759`）、`protected_spans`（632,755）、`PacketSentenceRole.LOOKBACK`、`PacketStatus.FAILED`（无 setter）、`GLOSSARY_INJECTED/RESOLVED/SUPPRESSED`（无 emit）、`TranslationPromptRequest.system_prompt_static/dynamic`（不发送）、`TranslationUsage.raw_usage`（不入库）、`ChapterMemoryBackfillService`（无生产调用）—— 死字段/死代码。
13. `builders.py:859-880` termbase/entity 子串匹配（"agent" 命中 "agentic"）+ 快照永远为空。
14. `translation.py:262` / `workflows.py:83` —— `TranslationService` 默认 Echo；`config.py:66` 默认 backend echo；首启自动激活 Echo 凭据（`provider_credentials.py:334-349`）。
15. `chapter_memory_backfill.py:42-46` —— backfill 内部 new 一个带 Echo 的 `TranslationService` 只为借 `write_chapter_memory`（应抽成独立函数）。
16. `translation.py:68-131` STOPWORDS、`context_compile.py:617-641` 意图标记、`builders.py:49-58` brief 关键词、`builders.py:215-222` 书型关键词 —— 未纳入 heuristics pack；`style_drift.py:6`、`term_normalization.py:23`、`translation.py:65-67` 固定用默认包，与 review 的按文档选包不一致。
17. `openai_compatible.py:430-442` —— chat 模式每次请求把完整 JSON schema（约 1.5k token）塞进 user prompt。
18. `openai_compatible.py:243,279` —— `request_overrides` 以顶层 dict 合并，可覆盖 `model/messages`。
19. `provider_credential.py:45-50` 默认 timeout 120/retries 2/backoff 2.0 vs `config.py:70-72` 60/1/1.5。
20. `provider_credentials.py:302-311` 进程内 `_revision`。
21. `secrets.py:77-93` 库代码写项目 `.env`。
22. `translation.py:763-765` `_count_concept_mentions` 是「含该词的句子数」而非出现次数，字段名 `mention_count` 误导。
23. `infra/repositories/translation.py:88-97` 前文译文未按 run/attempt 去重，多份 DRAFT 会拼接（同步路径重复执行时可复现）。
24. `translation.py:452` —— 覆盖不全的 run 标 `SUCCEEDED` 但带 `error_code`；`RunStatus` 无「partial」语义，下游按 status 过滤会忽略 error_code。
25. `translation.py:499-506` docstring 与实现一致但指出「per-sentence pinpointing later」——违规事件无 sentence_id，review UI 无法定位。
26. `term_consistency.py:436` 直接改 `TargetSegment.text_zh`，无新 run、无 SUPERSEDED、无 `updated_at` 更新；回滚只能靠事件 payload 手工恢复。
27. `glossary_extraction.py:237-281` 与 `term_consistency.py:244` —— term-consistency 每次全书重跑抽取（无缓存/无复用上次 CSV）。
28. `chapter_concept_autolock.py:345-366` —— settings 路径下每次 `build_default_concept_resolver()` 新建 client（无单价缓存问题，但 usage 不计入 run）。
29. `openai_compatible.py:337-348` —— 流式模式丢弃 `response_format`，JSON 合规完全靠提示词。
30. `document_run_executor.py:981-990` 已译 packet 返回 `"already-translated"` 伪 run id 并计 0 成本——`_complete_translate_success` 会把它写入 `work_items.output_artifact_refs_json.translation_run_id`（待确认下游是否解析）。

### 12.3 风险
31. 大 packet（references 24 句/一般 32 句）在 8192 输出上限下易截断，重试同样失败 → 工作项耗尽重试 → run 停滞（`budget.no_progress_exceeded` 才暂停）。
32. 8 线程 × 无限流 × 指数退避无上限：429 风暴时所有线程同步 sleep，无全局协调。
33. `_bootstrap_from_settings` 静默把 .env 里的 key 复制进 DB（`provider_credentials.py:350-365`），之后 .env 改动不再生效，用户易困惑。
34. `execute_packet` 同步路径本身无 lease/状态检查；两个生产调用方各自做了 `status == BUILT` 守卫（`workflows.py:247-249`、`services/rerun.py:109-111`，后者先 `mark_packet_ready_for_rerun` 再判断），所以重复执行只在直接调用 `execute_packet` 或与执行器并发时可能发生（无锁）。

---

## 13. 测试覆盖（本子系统）

| 文件 | 覆盖点 | 缺口 |
|---|---|---|
| `tests/test_translation_prompt_golden.py` + `translation_prompt_scenario.py` + `tests/golden/translation_prompts/*.json` | EPUB(3 章 7 packet) + 学术 PDF(7 packet) 全 profile×layout 的 context packet 与 prompt 逐字节快照（`auto_commit_memory=True`，Echo） | 夹具无术语表、无实体、无概念、无 guardrail 命中（14 条记录 terms/concepts 均 0），prompt 的术语/概念段从未进快照 |
| `tests/test_translation_worker_abstraction.py`（3009 行，55 用例） | prompt 各段、compact、material、split system、context compiler 各裁剪规则、guardrail 规则逐条、概念锁→prompt、backfill、provider payload/解析/重试/别名归一化、factory | 无 temperature；无超长输入；无 429 Retry-After |
| `tests/test_translation_output_validation.py` | 覆盖率三态 | — |
| `tests/test_translation_glossary_injection.py` / `_postvalidation.py` | 术语表注入优先级与过滤；违规事件 | 未测 variants；未测与 review 的一致性 |
| `tests/test_translation_heuristics_pack.py` | 包加载/文档选包/未知包 | — |
| `tests/test_worker_factory.py` | 凭据 worker 与 settings worker 同构、overrides 到达 payload、概念解析器复用 client | — |
| `tests/test_failure_classification.py` | retry/pause/fail 三类 | — |
| `tests/test_executor_translation_transactions.py` | LLM 调用期间 0 连接、失败事件持久化、attempt 递增、覆盖不全 error_code | — |
| `tests/test_executor_lease_loss.py` | 租约丢失丢弃结果 | 未测 `record_worker_failure` 路径的租约 |
| `tests/test_memory_service.py`（991 行，12 用例） | proposal-first、退役、review 提交/拒绝、API 批准/拒绝 | 未测 drifted 不一致 |
| `tests/test_chapter_memory_model.py` | 模型合并策略 | — |
| `tests/test_concept_resolver_fallback.py` | LLM 失败降级 | — |
| `tests/test_glossary_service.py` / `_extraction.py` / `_enforcement.py` / `test_term_matching.py` / `test_term_consistency.py` / `test_terminology_miner.py` | 各自单元 | `lock_term(lock_level=PREFERRED)` 仅在 term_consistency 测试间接覆盖 |
运行须知（memory 文件）：需按文件跑，全量 segfault。

---

## 14. 配置项汇总（翻译核心相关）

| 键（env 前缀 `BOOK_AGENT_`） | 默认 | 位置 | 备注 |
|---|---|---|---|
| `translation_backend` | `echo` | `config.py:66` | |
| `translation_model` | `echo-worker` | 67 | |
| `translation_prompt_version` | `p0.echo.v1` | 68 | settings worker 用；凭据 worker 固定 `p0.openai-compatible.v1` |
| `translation_prompt_profile` | `tech-column-meta-v1` | 69 | 13 选 1；凭据 worker 也从这里取 |
| `translation_timeout_seconds` | 60 | 70 | socket 超时 |
| `translation_max_retries` | 1 | 71 | |
| `translation_retry_backoff_seconds` | 1.5 | 72 | |
| `translation_max_output_tokens` | 8192 | 73 | 仅 chat 模式发送 |
| `translation_input_cache_hit_cost_per_1m_tokens` / `translation_input_cost_per_1m_tokens` / `translation_output_cost_per_1m_tokens` | None | 74-76 | 全局单价 |
| `translation_openai_api_key`（别名 `OPENAI_API_KEY`） | None | 77-83 | 仅从 .env 读 |
| `translation_openai_base_url`（别名 `OPENAI_BASE_URL`） | `https://api.openai.com/v1/responses` | 84-93 | 决定 API 模式 |
| `translation_openai_streaming` | False | 94 | NIM workaround |
| `translation_openai_request_overrides` | `{}` | 97 | JSON，顶层合并 |
| `BOOK_AGENT_SECRET_KEY` | 自动生成 | `secrets.py:24` | Fernet |
| 凭据表字段 | timeout 120 / retries 2 / backoff 2.0 / max_output_tokens 8192 / streaming False | `provider_credential.py:43-50` | |
| 执行器 | `lease_seconds=120, review_lease_seconds=1800, heartbeat=15, default_max_parallel_workers=8` | `document_run_executor.py:97-103` | run budget 可覆盖并行度 |
| 编译常量 | 见 `context_compile.py:26-34, 86-87`；`builders.py:41-48` | | 全部字符/条数阈值 |
| `document.metadata_json.translation_heuristics_pack` | `tech-book-default` | `heuristics/__init__.py:19-20` | 仅 review 生效 |

---

## 阅读清单（完整读）
`services/translation.py`, `services/context_compile.py`, `domain/context/builders.py`, `translation/{contracts,chapter_memory,output_validation,prompt_profiles}.py`, `translation/heuristics/{__init__.py,tech-book-default.json}`, `workers/{translator,factory,failures}.py`, `workers/providers/{__init__,openai_compatible}.py`, `services/{memory_service,chapter_memory_backfill,chapter_concept_lock,chapter_concept_autolock,glossary_service,glossary_extraction,glossary_enforcement,term_consistency,term_normalization,terminology_miner,style_drift,provider_credentials,secrets}.py`, `domain/terminology/matching.py`, `application/memory_proposals.py`, `domain/models/translation.py`, `domain/models/provider_credential.py`, `infra/repositories/{translation,chapter_memory,events}.py`, `orchestrator/frontier_plan.py`, `domain/block_rules.py`, `core/config.py`(1-140)。
节选阅读：`app/runtime/document_run_executor.py`(90-135, 288-326, 380-600, 660-1100, 1436-1581), `services/run_execution.py`(78-145, 240-335, 471-560), `services/review.py`(150-200, 340-420, 486-530, 739-780), `services/actions.py`(80-140), `services/rebuild.py`(240-290), `services/workflows.py`(80-95, 225-272), `orchestrator/rerun.py`, `application/review_repair.py`(600-680), `cli.py`(120-215), `app/api/routes/run_cost.py`, `alembic/.../0021_cost_rollup`, `docs/refactor/PLAN.md` P2.1/P2.2/P2.3/P3.4，git log 最近 25 条。
测试：全部列出文件的用例名 + `test_translation_prompt_golden.py`、`translation_prompt_scenario.py`(1-120)、`test_executor_translation_transactions.py`、`test_executor_lease_loss.py`(60-132)、`test_translation_glossary_injection.py`(100-179)、`test_translation_worker_abstraction.py`(2448-2560)、`test_memory_service.py`(829-860)、golden JSON 结构与 5 个 profile 的实际 prompt 文本。

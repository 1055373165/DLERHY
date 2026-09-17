# 04 · 设计评审：解析版本分叉（parse-revision forking）

> 状态：H1 设计评审稿（2026-09-17），供 H2 实施。**H2 已实施句子级分叉**（见文末「§9 实施记录」）；块级与章级分叉留给 H3 Structure Agent。对应 `03-roadmap.md` 已确认决策第 1 条。目标是让「重解析 / 结构修改」成为可重复、可回滚、不丢译文的操作，同时给 Structure Agent 一个可安全落笔的工作区。

## 1. 现状与约束（来自 03/07 篇）

- `Block.id = stable_id(document, chapter, ordinal, source_path, anchor)`，`Sentence.id = stable_id(document, block, segmentation_version, ordinal)`；PDF 锚点是全书级 `reading_order_index`。任何前序页多识别一个块，其后所有 id 都变。
- `BootstrapRepository.save` 只 merge 不删除；`pdf_structure_refresh` 按锚点匹配旧块，失配即跳过，句子从不重建（`refresh_sentences_stale` 只标记）。
- 句子被 `packet_sentence_map`、`alignment_edges`、`review_issues.sentence_id`、`term_entries.evidence_sentence_id` 引用，没有退役状态。
- `document_parse_revisions` 永远 v1，sidecar 原地覆盖，`parser_version` 从不递增。

## 2. 目标形态

**不原地迁移。每次重解析产生一个新的、只读的解析版本；旧版本保留；译文按句子指纹自动搬运；跨版本的关系用映射表表达。**

```
documents ──1:N── document_parse_revisions (version 1,2,3…; status active|superseded)
                     └─ chapters/blocks/sentences 都带 parse_revision_id（已有列，现在真正区分版本）
sentence_lineage (new): from_sentence_id → to_sentence_id, relation(same|split|merge|moved), similarity
revision_links   (new): object_type, from_object_id, to_object_id  （issue / approval / decision 的跨版本映射）
```

- `documents.active_parse_revision_id`（新列）指向当前活动版本；所有读侧（packet 构建、review、export、tools）只看活动版本的 chapters/blocks/sentences。
- 旧版本的句子不删，`sentences.status` 增加 `retired`（枚举扩展），`retired_by_revision_id`。
- 稳定锚点：`blocks.anchor_key = sha1(page, round(bbox/4pt), normalized_text[:64])`（PDF）/ `sha1(href, element path, normalized_text[:64])`（EPUB）。锚点只用于跨版本对齐与人类可读定位，不再参与 id 生成；id 改为随机 uuid4（新版本起）。

## 3. 重解析流程（`ReparseService.fork(document_id, reason, *, chapter_ids=None)`）

1. 创建 `document_parse_revisions` 新行（version = max+1，status=building），运行解析器得到 `ParsedDocument`。
2. 构建新 chapters/blocks/sentences（uuid4 id，带 `parse_revision_id`、`anchor_key`）。
3. **句子对齐**（纯函数，可单测）：对每个新句子，在旧活动版本同章（或全书）中按 `anchor_key` → 归一化文本相等 → 文本相似度（difflib ratio ≥ 0.92，且长度差 ≤ 15%）依次找匹配；写 `sentence_lineage`。一个新句匹配多个旧句 = `merge`，一个旧句匹配多个新句 = `split`。
4. **译文搬运**：`relation=same` 的句子直接复制活动 `target_segments`/`alignment_edges` 到新句（新 `translation_runs` 行，`attempt=1`，`model_config_json.carried_from_run_id` 记来源，成本 0）。`split/merge` 不搬运，句子回到 PENDING 并进入重译队列；映射保留供 review 参考。
5. **packet 重建**：对新版本运行 `ContextPacketBuilder`；已搬运译文的 packet 直接标 TRANSLATED（所有句子都有译文时），否则 BUILT。
6. **跨版本对象**：`review_issues.sentence_id/block_id` 指向旧对象的 issue 不改写；`revision_links` 记录旧→新句映射，issue 查询按映射投影；无映射的 issue 自动 `resolved(note=source retired)`。
7. 切换：`documents.active_parse_revision_id = new`；旧版本 `superseded`，旧句子 `retired`。整个流程在一个事务内，失败即回滚，新版本行不残留（status=building 的孤儿由启动清理）。
8. 事件：`document.reparsed`（新事件种类），payload 含 same/split/merge/新增/退役计数与搬运比例——这是 Structure Agent 与运维的观测面。

## 4. Structure Agent 的写路径

- 顾问模式（H2 前）：只 `open_issue`。
- 自动模式（H3）：`split_block / merge_blocks / relabel_block / link_caption` 不直接改活动版本，而是把「结构编辑指令」记入 `structure_edits`（append-only，带 turn_id），`ReparseService.fork(..., edits=[...])` 在步骤 2 之后应用这些编辑再对齐句子。因此一次 fork 可以同时消化解析器升级和 agent 的编辑，且可回放。
- 不可逆的整章重解析（`reparse_chapter` 工具）需要审批；顾问模式下的 issue 不需要。

## 5. 数据迁移与兼容

- 迁移 A（schema）：新列/新表/枚举扩展，不改现有数据；现有 chapters/blocks/sentences 的 `parse_revision_id` 已存在（0016），为空者回填到 v1；`documents.active_parse_revision_id` 回填 v1。
- 迁移 B（数据，dry-run 脚本 `scripts/parse_revision_dry_run.py`）：对一本书执行 fork 但不切换，输出对齐统计（same/split/merge 比例），用于评审阈值。目标：RSI 测试书 same ≥ 97%。
- id 策略变化只影响新版本；旧对象继续用 stable_id 生成的 uuid，无需重写。
- golden：`test_pdf_structure_golden` 比的是 `ParsedDocument`，不受影响；`test_workflow_golden` / `test_export_golden` 在活动版本上运行，不受影响。

## 6. 与 exports / issues 版本化的关系

同一原则（追加不覆盖）在 H2 一并落地：`exports` 增加 `version` 与 `superseded_by`，文件按 `<type>-v<n>` 命名；`review_issues` 重开时不重置 `created_at`，人工 `WONTFIX/RESOLVED` 优先于自动重开（登记簿 R8、R17）。

## 7. 评审要点（需要在 H2 开工前定）

| 议题 | 建议 | 备选 |
|---|---|---|
| 相似度阈值 | 0.92 + 长度差 15% | 按语料校准；先跑 dry-run |
| 搬运译文的审校状态 | 保持原 `final_status`，review 标记 `carried_over` | 全部 REVIEW_REQUIRED（更保守，成本高） |
| 旧版本保留期 | 永久（只读，体积可控） | 保留最近 3 个版本 |
| id 策略 | 新版本 uuid4 | 保持 stable_id 但去掉 ordinal（仍脆弱） |
| 多版本 UI | 只展示活动版本 + 「查看变更统计」 | 版本对比视图（H4） |

## 8. 验收

- 单测：句子对齐纯函数（same/split/merge/moved 各一组）；fork 事务失败回滚；搬运译文与 packet 状态。
- 集成：RSI 测试书解析两次（第二次故意改一条启发式）→ fork → same ≥ 97%，导出内容不变。
- 性能：600 页书 fork < 60s（对齐是 O(N) 哈希 + 局部相似度）。

## 9. 实施记录（H2，2026-09-17）

实现的是**句子级分叉**，覆盖了登记簿里的实际缺陷：结构刷新改了块文本却从不重建句子（`refresh_sentences_stale` 只打标记）。

| 设计项 | 实现 | 与设计的差异 |
|---|---|---|
| 新 parse revision | `ParseRevisionForkService.resegment_blocks` 打开新版本，旧 ACTIVE 版本置 SUPERSEDED；元数据记录原因与统计 | 活动版本用 `status=ACTIVE` 表示，没有新增 `documents.active_parse_revision_id` 列 |
| 句子退役 | `sentences.retired_by_revision_id`；唯一约束改为只约束未退役行（部分唯一索引，迁移 0037） | 退役标记是列而不是 `SentenceStatus.RETIRED`，避免与翻译状态混用 |
| 句子对齐 | `domain/structure/sentence_alignment.py` 纯函数：same（归一化相等或相似度 ≥0.92 且长度差 ≤15%）/ split / merge / removed；写 `sentence_lineage` | 与设计一致 |
| 译文搬运 | 受影响 packet 原地重建（`TargetedRebuildService.rebuild_packets`），最新译文复制为新 attempt，same 句子的对齐重映射；全部覆盖则 TRANSLATED，否则 BUILT 待重译 | 仅以 packet 为单位搬运；只作上下文引用该句的相邻 packet 也会被重建并得到一个复制 attempt |
| issue 投影 | 退役句子上的非人工 issue 以「parse revision vN 退役」解决，review 在新句子上重新检测；人工决定的 issue 不动，列入 `human_decided_issue_ids` 待重新确认 | 没有 `revision_links` 表；lineage 足以追溯 |
| 读侧 | 所有按文档/章节/块读取句子的查询都排除退役句子（bootstrap、review、export、术语、BOOK 工具、文档统计） | — |
| 接入 | REPARSE_* 动作在 PDF 刷新后执行分叉并立即重译 BUILT 的 packet；`refresh_pdf_structure` / `refresh_epub_structure` 之后也执行分叉；审计行与 `document.reparsed` 事件 | 刷新服务本身的锚点匹配逻辑未改 |

未做（有意留到 H3）：块级分叉（新增/拆分块获得新 id 与新 packet；目前新增块若不在任何 packet 的块范围内，会列入 `unpacketed_block_ids` 并由 review 报 OMISSION，run 失败而不是静默丢失）、章级分叉、Structure Agent 的结构编辑回放、dry-run 统计脚本。


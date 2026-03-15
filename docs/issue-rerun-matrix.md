# Issue to Rerun Action Matrix

## 1. 目的

本文件把 [Error Taxonomy](/Users/smy/project/book-agent/docs/error-taxonomy.md) 和 [Rerun Policy](/Users/smy/project/book-agent/docs/rerun-policy.md) 进一步落成可执行规则表，用于：

- orchestrator 自动决策
- review issue triage
- 人工审校后的修复指引
- QA 失败后的统一重跑动作

本文件回答两个问题：

1. 某类 issue 出现后，默认应该触发什么修复动作。
2. 修复动作默认影响多大范围，何时允许升级或降级。

## 2. 决策原则

默认按以下顺序决策：

1. 先定 issue 类型。
2. 再判断根因层。
3. 再选最小可验证修复动作。
4. 最后确定影响范围。

如果 issue 的表象和根因不同，始终按根因层决策，而不是按表象决策。

## 3. 动作枚举

- `EDIT_TARGET_ONLY`
- `REALIGN_ONLY`
- `RERUN_PACKET`
- `REBUILD_PACKET_THEN_RERUN`
- `REBUILD_CHAPTER_BRIEF`
- `UPDATE_TERMBASE_THEN_RERUN_TARGETED`
- `UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED`
- `RESEGMENT_CHAPTER`
- `REPARSE_CHAPTER`
- `REPARSE_DOCUMENT`
- `REEXPORT_ONLY`
- `MANUAL_FINALIZE`

## 4. 规则矩阵

| Issue Type | 根因层 | 默认动作 | 默认范围 | Blocking | 说明 |
| --- | --- | --- | --- | --- | --- |
| `OMISSION` | `translation` | `RERUN_PACKET` | `packet` | `true` | 句级覆盖缺失，但切分和结构可信。 |
| `OMISSION` | `alignment` | `REALIGN_ONLY` | `packet` | `true` | 译文已存在，仅缺失对齐关系。 |
| `OMISSION` | `segment` | `RESEGMENT_CHAPTER` | `chapter` | `true` | 句切分错误导致覆盖率失真。 |
| `OMISSION` | `structure` | `REPARSE_CHAPTER` | `chapter` | `true` | block 归类或锚点错误导致漏入。 |
| `MISTRANSLATION_SEMANTIC` | `translation` | `RERUN_PACKET` | `packet` | `true` | 语义错误，局部重译优先。 |
| `MISTRANSLATION_LOGIC` | `translation` | `RERUN_PACKET` | `packet` | `true` | 否定、因果、比较等逻辑错。 |
| `MISTRANSLATION_REFERENCE` | `packet` | `REBUILD_PACKET_THEN_RERUN` | `packet` | `true` | 指代、说话人、缩写依赖上下文。 |
| `MISTRANSLATION_PRAGMATIC` | `translation` | `EDIT_TARGET_ONLY` | `sentence` | `false` | 语气或措辞不理想但信息完整。 |
| `MISORDERING` | `structure` | `REPARSE_CHAPTER` | `chapter` | `true` | 阅读顺序或脚注归位错误。 |
| `MISORDERING` | `export` | `REEXPORT_ONLY` | `chapter` | `true` | 导出装配错序但源对齐正常。 |
| `TERM_CONFLICT` | `memory` | `UPDATE_TERMBASE_THEN_RERUN_TARGETED` | `packet/chapter` | `true` when locked | 锁定术语未命中或多译。 |
| `ENTITY_CONFLICT` | `memory` | `UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED` | `chapter/document subset` | `true` when core entity | 专名冲突优先修 registry。 |
| `STRUCTURE_POLLUTION` | `structure` | `REPARSE_CHAPTER` | `chapter` | `true` | 页眉、脚注、图注、代码块污染正文。 |
| `STRUCTURE_POLLUTION` | `parse` | `REPARSE_DOCUMENT` | `document` | `true` | parser 主策略失效或全局目录锚点错误。 |
| `FORMAT_POLLUTION` | `structure` | `REPARSE_CHAPTER` | `chapter` | `true` | block type 分类错导致公式/代码被翻译。 |
| `FORMAT_POLLUTION` | `packet` | `REBUILD_PACKET_THEN_RERUN` | `packet` | `true` | protected spans 注入缺失。 |
| `STYLE_DRIFT` | `translation` | `RERUN_PACKET` | `packet` | `false` | 局部风格偏移，不影响事实语义。 |
| `STYLE_DRIFT` | `memory` | `REBUILD_CHAPTER_BRIEF` | `chapter` | `false` | 风格 profile 或章节 brief 失稳。 |
| `CONTEXT_FAILURE` | `packet` | `REBUILD_PACKET_THEN_RERUN` | `packet` | `true` | 默认优先修 packet。 |
| `CONTEXT_FAILURE` | `memory` | `REBUILD_CHAPTER_BRIEF` | `chapter` | `true` | 章节摘要或实体状态不足。 |
| `ALIGNMENT_FAILURE` | `alignment` | `REALIGN_ONLY` | `packet/chapter` | `true` | 翻译内容没问题，仅映射缺失或 target segment 失联。 |
| `ALIGNMENT_FAILURE` | `export` | `REALIGN_ONLY` | `packet` | `true` | export-time 发现当前导出对齐异常，但内容仍可通过 realign 修复。 |
| `ALIGNMENT_FAILURE` | `segment` | `RESEGMENT_CHAPTER` | `chapter` | `true` | 对齐失败来自句切分漂移。 |
| `DUPLICATION` | `export` | `REEXPORT_ONLY` | `chapter/document` | `false` | 装配重复。 |
| `DUPLICATION` | `packet` | `REBUILD_PACKET_THEN_RERUN` | `packet` | `false` | 重叠窗口导致重复输出。 |
| `EXPORT_FAILURE` | `export` | `REEXPORT_ONLY` | `chapter/document` | `true` | 不应误触发重译。 |
| `PIPELINE_FAILURE` | `ops` | `MANUAL_FINALIZE` or rerun job | `job/chapter/document` | `depends` | 看是否污染 artifact 或仅作业失败。 |

## 5. 升级规则

以下情况会把默认范围升级一级：

- 同一 issue 在相邻 3 个以上 packet 重复出现。
- issue 命中 locked term 且影响多个章节。
- issue 来自上游版本变化。
- issue 无法在一次 rerun 后关闭。

升级路径：

- `sentence -> packet -> chapter -> document`

示例：

- 一个 packet 的 `TERM_CONFLICT` 不需要整章重跑。
- 如果同一 locked term 在 6 个章节持续冲突，则从 `packet/chapter` 升级到 `document subset`。

## 6. 降级规则

以下情况允许把动作降级到更小范围：

- 人工确认问题仅是措辞，不影响术语和上下文。
- alignment 缺失仅是记录未写入，实际 target segment 完整。
- 某个 issue 只影响一句，且不会破坏后续一致性。

典型可降级为 `EDIT_TARGET_ONLY` 的场景：

- 中文表达略生硬
- 个别标点不符合风格
- 语义完整但用词偏硬

## 7. 自动决策优先级

orchestrator 在 issue triage 时建议按以下优先级选择动作：

1. `REPARSE_DOCUMENT`
2. `REPARSE_CHAPTER`
3. `RESEGMENT_CHAPTER`
4. `REBUILD_CHAPTER_BRIEF`
5. `UPDATE_TERMBASE_THEN_RERUN_TARGETED`
6. `UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED`
7. `REBUILD_PACKET_THEN_RERUN`
8. `RERUN_PACKET`
9. `REALIGN_ONLY`
10. `REEXPORT_ONLY`
11. `EDIT_TARGET_ONLY`
12. `MANUAL_FINALIZE`

原则：

- 越靠上说明根因越上游。
- 若上游动作已触发，下游动作通常不再单独执行，而是随失效传播一起重建。

## 8. 自动 triage 伪规则

```text
if issue.root_cause_layer in [parse] and issue.scope in [chapter, document]:
  action = REPARSE_CHAPTER or REPARSE_DOCUMENT
elif issue.root_cause_layer == structure:
  action = REPARSE_CHAPTER
elif issue.root_cause_layer == segment:
  action = RESEGMENT_CHAPTER
elif issue.type in [TERM_CONFLICT] and issue.involves_locked_term:
  action = UPDATE_TERMBASE_THEN_RERUN_TARGETED
elif issue.type in [ENTITY_CONFLICT]:
  action = UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED
elif issue.type in [CONTEXT_FAILURE] and issue.root_cause_layer == memory:
  action = REBUILD_CHAPTER_BRIEF
elif issue.type in [CONTEXT_FAILURE, MISTRANSLATION_REFERENCE]:
  action = REBUILD_PACKET_THEN_RERUN
elif issue.type in [DUPLICATION] and issue.root_cause_layer == packet:
  action = REBUILD_PACKET_THEN_RERUN
elif issue.type in [ALIGNMENT_FAILURE] and translation_content_ok:
  action = REALIGN_ONLY
elif issue.type in [EXPORT_FAILURE, DUPLICATION] and source_artifacts_ok:
  action = REEXPORT_ONLY
elif issue.type in [MISTRANSLATION_SEMANTIC, OMISSION]:
  action = RERUN_PACKET
else:
  action = EDIT_TARGET_ONLY or MANUAL_FINALIZE
```

## 9. 人工审校后的动作建议

| 人工结论 | 推荐动作 |
| --- | --- |
| “只是措辞不自然” | `EDIT_TARGET_ONLY` |
| “上下文不够，所以翻偏了” | `REBUILD_PACKET_THEN_RERUN` |
| “这个词应该全书统一” | `UPDATE_TERMBASE_THEN_RERUN_TARGETED` |
| “这其实是同一实体” | `UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED` |
| “脚注和正文归错了” | `REPARSE_CHAPTER` |
| “导出顺序错了，但译文没问题” | `REEXPORT_ONLY` |

## 10. 与 orchestrator 的接口建议

建议为 orchestrator 暴露一张静态规则表：

```json
{
  "issue_type": "TERM_CONFLICT",
  "root_cause_layer": "memory",
  "blocking": true,
  "default_action": "UPDATE_TERMBASE_THEN_RERUN_TARGETED",
  "default_scope": "chapter",
  "upgrade_threshold": 3
}
```

orchestrator 的职责是：

- 读取 issue
- 结合 artifact versions 和命中范围
- 决定无效化哪些对象
- 生成 rerun plan

## 11. 不应自动化的情况

以下情况建议只自动生成建议，不自动执行：

- `critical` 的 `MISTRANSLATION`
- 涉及核心人物、书名、机构名的 `ENTITY_CONFLICT`
- 涉及书种风格主策略变化的 `STYLE_DRIFT`
- 需要从章节级升级到文档级的 rerun

## 12. P0 实施建议

P0 先支持以下自动动作就够用：

1. `RERUN_PACKET`
2. `REBUILD_PACKET_THEN_RERUN`
3. `UPDATE_TERMBASE_THEN_RERUN_TARGETED`
4. `RESEGMENT_CHAPTER`
5. `REPARSE_CHAPTER`
6. `REEXPORT_ONLY`

这已经能覆盖 EPUB-only 版本的大部分高频返工路径。

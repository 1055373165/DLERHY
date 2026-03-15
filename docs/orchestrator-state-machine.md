# Orchestrator State Machine

## 1. 目的

本文件定义长文档翻译 Agent 的编排状态机、失效传播规则和重跑执行逻辑。  
目标不是把 orchestrator 做成“智能决策代理”，而是把它做成一个显式、可恢复、可追踪的状态机系统。

orchestrator 负责三件事：

1. 推动 artifact 按既定状态流转。
2. 根据 issue 和版本变化触发失效传播。
3. 生成最小可验证 rerun plan。

## 2. 核心对象

orchestrator 直接关心以下对象：

- `document`
- `chapter`
- `sentence`
- `translation_packet`
- `translation_run`
- `review_issue`
- `issue_action`
- `job_run`
- `artifact_invalidation`

## 3. 编排原则

### 3.1 状态优先于对话

系统不依赖长对话上下文记住“做到哪一步”，而依赖数据库状态和 artifact 版本。

### 3.2 Job 幂等

同一个 `job_type + scope + input_version_bundle` 再次执行，应尽量复用或安全重跑，不产生双写污染。

### 3.3 先失效，再重建

一旦确认上游 artifact 已不可信，先记录 `artifact_invalidation`，再触发下游重跑。

### 3.4 失败隔离

默认以 `chapter` 为主要失败隔离边界，以 `packet` 为局部重试边界。

## 4. 文档级状态机

### 4.1 状态

- `ingested`
- `parsed`
- `active`
- `partially_exported`
- `exported`
- `failed`

### 4.2 转移规则

- `ingested -> parsed`
  - parse 成功，章节和 block 可用。
- `parsed -> active`
  - 至少一个章节已切分，book profile 可用。
- `active -> partially_exported`
  - 至少一个章节通过 QA 并成功导出。
- `partially_exported -> exported`
  - 所有目标章节均导出完成。
- `* -> failed`
  - 致命 pipeline failure，且无法自动恢复。

## 5. 章节级状态机

### 5.1 状态

- `ready`
- `segmented`
- `packet_built`
- `translated`
- `qa_checked`
- `review_required`
- `approved`
- `exported`
- `failed`

### 5.2 转移规则

- `ready -> segmented`
  - sentences 生成完成，句 ID 稳定。
- `segmented -> packet_built`
  - 当前章全部 packet 生成完成。
- `packet_built -> translated`
  - 当前章全部 packet 至少完成一次成功翻译。
- `translated -> qa_checked`
  - QA 完成并已产出 issue 集。
- `qa_checked -> review_required`
  - 存在 blocking 或人工优先审校 issue。
- `qa_checked -> approved`
  - 所有 QA gate 通过。
- `review_required -> packet_built`
  - 有 packet 级或 brief 级 rerun 动作。
- `review_required -> segmented`
  - 有 segment 级 rerun 动作。
- `review_required -> ready`
  - 有 structure/parse 级 rerun 动作。
- `approved -> exported`
  - 导出成功。
- `* -> failed`
  - chapter job 无法恢复。

## 6. 句级状态机

### 6.1 状态

- `pending`
- `protected`
- `translated`
- `review_required`
- `finalized`
- `blocked`

### 6.2 转移规则

- `pending -> protected`
- `pending -> translated`
- `translated -> review_required`
- `translated -> finalized`
- `review_required -> finalized`
- `review_required -> blocked`
- `blocked -> pending`
  - 上游修复后重新进入流程。

## 7. Packet 级状态机

### 7.1 状态

- `built`
- `running`
- `translated`
- `invalidated`
- `failed`

### 7.2 转移规则

- `built -> running`
- `running -> translated`
- `running -> failed`
- `translated -> invalidated`
  - 上游版本变化或 review issue 触发。
- `invalidated -> built`
  - rebuild packet 后重新进入。

## 8. Job 级状态机

### 8.1 状态

- `queued`
- `running`
- `succeeded`
- `failed`
- `cancelled`

### 8.2 重试规则

- `parse`
  - 最多自动重试 1 次，避免重复污染结构。
- `segment`
  - 最多自动重试 1 次。
- `translate`
  - 可自动重试 2 到 3 次，但同一输入版本不能无限重试。
- `qa`
  - 可自动重试 1 次。
- `export`
  - 可自动重试 2 次。

如果失败根因是 deterministic 输入问题，不应继续同参重试，应升级为 issue。

## 9. 事件驱动模型

orchestrator 推荐以事件推动，而不是串行脚本推动。

### 9.1 关键事件

- `document.ingested`
- `document.parsed`
- `chapter.segmented`
- `chapter.packet_built`
- `packet.translated`
- `chapter.qa_completed`
- `issue.created`
- `issue.triaged`
- `artifact.invalidated`
- `chapter.approved`
- `export.completed`

### 9.2 事件到动作

- `document.ingested` -> enqueue parse
- `document.parsed` -> enqueue bootstrap profile + chapter segmentation
- `chapter.segmented` -> enqueue chapter brief + packet build
- `chapter.packet_built` -> enqueue translation jobs
- `packet.translated` -> check chapter completeness, maybe enqueue QA
- `issue.triaged` -> create issue_action + invalidations + rerun jobs
- `chapter.approved` -> enqueue export

## 10. 失效传播规则

### 10.1 上游到下游依赖图

```text
document
  -> chapters
    -> blocks
      -> sentences
        -> translation_packets
          -> translation_runs
            -> target_segments
              -> alignment_edges
                -> review_issues
                  -> exports
```

原则：

- 上游 artifact 变更时，应显式标记下游为 `invalidated`，而不是悄悄覆盖。
- invalidation 必须可审计。

### 10.2 典型失效传播

`REPARSE_CHAPTER`
- invalidate: blocks, sentences, packets, translation_runs, target_segments, alignment_edges, chapter-scoped exports

`RESEGMENT_CHAPTER`
- invalidate: sentences, packets, translation_runs, target_segments, alignment_edges

`REBUILD_CHAPTER_BRIEF`
- invalidate: affected packets, translation_runs, target_segments, alignment_edges

`UPDATE_TERMBASE_THEN_RERUN_TARGETED`
- invalidate: only packets and downstream artifacts that hit changed terms

`REEXPORT_ONLY`
- invalidate: exports only

## 11. Rerun Plan 生成逻辑

orchestrator 处理 `issue` 时，建议生成一个结构化 `rerun_plan`：

```json
{
  "issue_id": "iss_xxx",
  "root_cause_layer": "packet",
  "action_type": "REBUILD_PACKET_THEN_RERUN",
  "scope_type": "packet",
  "scope_ids": ["pkt_101", "pkt_102"],
  "invalidate": [
    {"object_type": "translation_packet", "object_id": "pkt_101"},
    {"object_type": "translation_run", "object_id": "run_201"}
  ],
  "followup_jobs": [
    {"job_type": "packet", "scope_type": "chapter", "scope_id": "ch_01"},
    {"job_type": "translate", "scope_type": "packet", "scope_id": "pkt_101"},
    {"job_type": "qa", "scope_type": "chapter", "scope_id": "ch_01"}
  ]
}
```

## 12. 章节完成判定

一个章节进入 `approved` 前，应满足：

- 所有 `translatable=true` 句子都不处于 `pending`
- 当前章不存在 `blocking=true AND status=open/triaged` 的 issue
- 当前章所有有效 packet 都有成功 translation run
- 当前章已完成一次 QA

## 13. 导出 Gate

document 或 chapter 进入 export 前，orchestrator 必须校验：

- coverage gate 通过
- alignment gate 通过
- term gate 通过
- format gate 通过
- blocking issue gate 通过

如果只导出 review package，可以放宽为：

- 允许存在 `review_required`
- 但必须完整保留 issue 和 provenance

## 14. 并发与锁

### 14.1 推荐并发粒度

- 文档级：低并发
- 章节级：主并发单位
- packet 级：受控并发

### 14.2 锁策略

建议至少加两类逻辑锁：

- `chapter_mutation_lock`
  - 防止同一章节同时重切分和重译。
- `termbase_update_lock`
  - 防止多个 reviewer 同时锁定冲突术语。

## 15. 伪代码

```text
on issue.triaged(issue):
  action = rule_engine.resolve(issue)
  plan = build_rerun_plan(issue, action)
  write_invalidations(plan.invalidate)
  enqueue(plan.followup_jobs)

on packet.translated(packet):
  if chapter_all_packets_translated(packet.chapter_id):
    enqueue(job_type='qa', scope_type='chapter', scope_id=packet.chapter_id)

on chapter.qa_completed(chapter):
  if chapter_has_blocking_issues(chapter.id):
    move_chapter_status(chapter.id, 'review_required')
  else:
    move_chapter_status(chapter.id, 'approved')
    enqueue(job_type='export', scope_type='chapter', scope_id=chapter.id)

on artifact.version_changed(object):
  affected = dependency_graph.invalidate_downstream(object)
  write_invalidations(affected)
  enqueue_followup_jobs(affected)
```

## 16. P0 建议先实现的编排能力

P0 不需要一开始就把 orchestrator 做成复杂工作流引擎。先实现以下能力就足够：

1. 章节级主状态流转
2. packet 级翻译并发
3. issue 到 rerun action 的规则路由
4. 失效传播记录
5. rerun 后自动触发 QA 复检
6. export gate

## 17. 不建议在 P0 做的事

- 不建议做自由决策式 agent orchestrator
- 不建议做多层嵌套子工作流图编辑器
- 不建议让模型自己决定 rerun scope
- 不建议在没有 artifact versioning 的前提下做复杂自动重跑

## 18. 推荐实现顺序

1. chapter/document 状态机
2. job_runs + issue_actions
3. invalidation 写入
4. rerun_plan 生成
5. export gate
6. dashboard 和告警

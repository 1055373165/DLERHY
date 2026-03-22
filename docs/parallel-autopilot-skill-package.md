---
description: Publishable development orchestration skill package that upgrades command-driven autopilot into a natural-language, selectively parallel, stateful software delivery skill with lane scheduling, conflict control, rollback, and governance artifacts.
---

# Parallel Autopilot Skill Package

## 1. Package Summary

`Parallel Autopilot Skill Package` 是一个面向真实软件开发场景的通用开发接管技能包。它接收用户的一句需求、一个重构目标、一个迁移计划，自动完成需求锁定、架构决策、技术探针、任务拆解、依赖图构建、按需并行 lane 调度、集成收敛、审查验收和状态治理。

这个 skill package 的核心定位不是“让更多 agent 一起工作”，也不是“给 autopilot 加一个 parallel 开关”，而是把当前命令式、串行式的 autopilot 工作流，迁移为一个自然语言触发、图驱动、按需并行、可恢复、可治理的开发编排器。

它的目标不是最大化并行度，而是在不牺牲质量、可解释性和恢复能力的前提下，只对真正适合并行的工作做安全并行。

## 2. What This Package Does

本技能包负责：

- 将一句开发需求编译为可执行的全周期开发工作流
- 自动完成需求锁定，而不是要求用户使用命令式入口
- 生成 ADR、任务树、工作图、lane 分配和执行状态
- 识别哪些任务可以并行，哪些必须串行
- 在并行执行中控制共享写集、接口契约、集成顺序和回溯影响面
- 对开发过程中的 bug、回溯、变更请求和失败进行结构化治理
- 在真实运行中发现 skill 自身的缺口、痛点和不顺手之处时，回写 skill 本体而不是只做局部 workaround
- 输出一套可持续恢复的状态工件，而不是只输出一次性的聊天结论

## 3. What This Package Does Not Do

本技能包不直接负责：

- 盲目把所有任务并行化
- 直接修改旧 `autopilot.md` 的工作流本体
- 用 CLI/斜杠命令驱动主入口
- 用“自由 swarm”替代明确的控制面
- 在没有依赖分析、冲突控制和状态治理的前提下执行真并行

也就是说，它负责编排开发，不负责制造伪并行。

## 4. Core Design Position

这个技能包建立在以下核心立场上：

- 串行控制面必须保留
- 并行执行面必须受依赖图和写集约束
- 自然语言入口必须替代命令式入口
- `PROGRESS.md` 和 `DECISIONS.md` 可以保留，但不再足以单独承载并行执行真相
- 真相状态必须结构化，Markdown 只负责可读摘要

## 5. Recommended Trigger Conditions

当用户出现以下意图时，建议触发本技能包：

- 想让系统自动接管一个完整开发任务，而不是只要一份计划
- 想把串行开发流程升级为按需并行的可执行工作流
- 想设计一个新的开发型 skill，而不是只写命令文档
- 想在保留 ADR / PROGRESS / 回溯 / bug-driven evolution 的前提下引入并行执行
- 想让用户只输入一句需求，系统就自动进入开发接管状态

典型触发语包括：

- “把 autopilot 升级成并行开发版 skill”
- “我不想再靠命令行驱动开发工作流”
- “给我一个支持按需并行开发的通用 skill”
- “让系统根据需求自动接管全部开发流程”
- “重构现有 autopilot 的工作流形态，但不要直接修改它”

## 6. Input Contract

本技能包必须接受以下输入形态：

- 一句话开发需求
- 一个重构目标
- 一个迁移计划
- 一个架构升级方向
- 当前 repo 的关键上下文文件
- 现有状态文件，例如 `PROGRESS.md`、`DECISIONS.md`

输入最少时，系统必须自动补足以下高价值控制约束：

- 当前是在新建工作流、续跑、变更请求还是回溯恢复
- 当前需求中哪些部分必须串行冻结
- 哪些工作可能成为并行 lane 候选
- 哪些模块或文件大概率构成共享写集
- 当前状态工件是否足以支持并行治理

## 7. Output Contract

### Default Output

默认输出至少包含：

```text
诊断摘要：
- 当前工作流模式：
- 是否适合并行：
- 推荐并行单位：
- 必须串行的控制面：
- 关键风险：

执行工件：
- DECISIONS.md
- PROGRESS.md
- WORK_GRAPH.json
- LANE_STATE.json
- MERGE_QUEUE.json
- CONTRACT_MAP.json
- RUN_CONTEXT.md

接管后的工作流：
【完整工作流规格】
```

### Skill-Spec Output

如果用户明确要求产出可发布 skill 包，则输出：

```text
skill description:
package summary:
trigger conditions:
input contract:
output contract:
runtime protocol:
parallel execution policy:
state artifacts:
rollback/change-request protocol:
quality requirements:
```

### Resume Output

如果用户输入的是已有状态工件，则输出：

```text
恢复判断：
当前执行位置：
阻塞 lane：
可继续执行的 lane：
需要回滚的节点：
下一步接管动作：
```

### Change Request Output

如果用户输入的是变更请求，则输出：

```text
变更影响面：
失效工作图节点：
保留节点：
需要新增的 lane：
新的执行基线：
```

## 8. Runtime Protocol

### Step 1: Detect Execution Mode

识别当前输入属于哪一类：

- 新任务接管
- 已有项目续跑
- 需求变更
- 回溯恢复
- 并行重规划

### Step 2: Lock Requirement

自动完成需求理解和最小必要追问，生成唯一的 `locked_requirement`。

### Step 3: Freeze Decision Baseline

将关键架构选型、边界、非目标和推翻条件写入 `DECISIONS.md`。

### Step 4: Run Necessary Spikes

只对高风险技术决策做最小探针验证，不允许在全量拆解后才发现核心选型不可行。

### Step 5: Build Work Graph

将任务拆解为：

- Phase
- Task
- MDU
- contract tag
- write set
- dependency edge
- integration gate

并生成结构化工作图。

### Step 6: Partition Execution Lanes

基于工作图把可并行节点划分为 lane / wave。

### Step 7: Execute Lane-Local MDUs

每个 lane 内仍然按 MDU 串行推进，避免 lane 内部失控扩散。

### Step 8: Merge And Integrate

所有 lane 产出必须经过统一的 merge queue 和 integration gate。

### Step 9: Review And Checkpoint

在 lane、wave、phase 三个层级分别执行审查和验收。

### Step 10: Update State Artifacts

把所有决策、完成状态、阻塞原因、恢复点和失败信息落入状态工件。

### Step 11: Evolve The Skill Itself

如果本轮执行暴露出 skill 自身的协议缺口、状态缺口、lane 划分歧义、merge gate 摩擦或重复性人工胶水步骤，必须把这些发现回写到 skill 本体。

最少应更新以下之一：

- 安装型 `SKILL.md`
- reference protocols
- templates
- publishable skill package 文档

### Step 12: Loop Or Close

如果仍有可执行节点，则进入下一 wave；如果所有节点完成，则进入最终收口。

## 10.1 Skill Evolution Protocol

这套 skill 不应被视为静态文档，而应被视为可演化的编排器。

每次真实使用时，只要出现以下任一情况，就必须触发 skill evolution：

- 同类追问反复出现，说明 intake protocol 不足
- lane 划分经常要靠临时人工解释，说明 lane policy 不足
- 状态文件无法表达当前执行事实，说明 state artifact 不足
- merge / checkpoint / rollback 经常临时补规则，说明 protocol 不足
- 操作上频繁需要重复的人肉胶水步骤，说明 ergonomics 不足

每次 evolution 至少输出三件事：

1. 当前 run 的局部修复或 workaround
2. skill-gap 分类
3. 对 skill 本体的可复用补丁

推荐 skill-gap 分类：

- intake gap
- lane policy gap
- state artifact gap
- contract / merge gate gap
- rollback / recovery gap
- operator ergonomics gap

## 9. Parallel Execution Policy

### Parallelism Principle

并行必须是按需并行，不是默认并行。

### Parallel Unit

推荐并行单位不是 Phase，也不是单个 MDU，而是 `lane`。

原因：

- Phase 过粗，不适合调度
- MDU 过细，容易制造伪并行和高频冲突
- lane 可以作为“受控的并行执行容器”

### What Can Run In Parallel

以下工作通常适合并行：

- 互不依赖的技术探针
- 共享接口已冻结后的独立模块实现
- 与核心代码解耦的测试补强
- 文档、观测、告警、只读分析类工作
- 明确 disjoint write set 的 feature slice

### What Must Remain Serial

以下工作天然必须串行：

- 需求锁定
- 关键 ADR 冻结
- 工作图生成
- lane 划分
- 契约升级
- merge gate
- phase checkpoint
- 全局回溯决策
- 需求变更决策

### Conflict Detection Rules

任一满足以下条件时，不允许并行：

- 共享写同一文件
- 共享写同一模块目录
- 共享写同一数据模型
- 共享写同一公共接口
- 共享写同一状态协议
- 一个 lane 的输出是另一个 lane 的直接输入

### Contract Dependency Rules

如果某项工作会引入：

- schema 变更
- API 契约变更
- 公共类型变更
- shared runtime protocol 变更

则该工作必须成为独立的串行前置 lane。

### Recommended Orchestration Topology

推荐采用：

- Orchestrator
- Lane Planner
- Lane Workers
- Integrator
- Reviewer

而不是去中心化自由协作。

## 10. State Artifacts

### Required Artifacts

本技能包推荐维护以下状态工件：

- `DECISIONS.md`
- `PROGRESS.md`
- `WORK_GRAPH.json`
- `LANE_STATE.json`
- `MERGE_QUEUE.json`
- `CONTRACT_MAP.json`
- `RUN_CONTEXT.md`

### Artifact Responsibilities

`DECISIONS.md`

- 架构决策
- 推翻条件
- 探针验证结果

`PROGRESS.md`

- 人类可读进度面
- 阶段/任务/MDU 完成度
- 当前阶段、当前任务、当前 lane 摘要

`WORK_GRAPH.json`

- 所有节点、依赖边、写集、契约标签、状态
- 并行调度真相源

`LANE_STATE.json`

- 当前 active lanes
- lane owner
- lease
- attempt
- stale / blocked / merged 状态

`MERGE_QUEUE.json`

- 待集成项
- 集成顺序
- gate 状态
- 冲突结果

`CONTRACT_MAP.json`

- 接口/模型/协议 owner
- version
- consumer lanes
- breaking-change 广播记录

`RUN_CONTEXT.md`

- 当前接管摘要
- 当前波次
- 当前阻塞
- 下一步动作

### State Consistency Rule

结构化状态为真相，Markdown 为镜像。

## 11. Conflict Control And Integration

### Shared Write-Set Conflict

两个 lane 同时触碰同一共享模块时：

- 不允许继续并行
- 低优先级 lane 转入 `stale_blocked`
- 由 orchestrator 重新排队

### Contract Broadcast

接口契约变更后必须：

- 提升 contract version
- 写入 `CONTRACT_MAP.json`
- 标记所有 consumer lane 为 `needs_rebase`

### Merge Gate

merge gate 必须串行，并检查：

- 写集冲突
- contract version 漂移
- 必跑测试是否通过
- reviewer 是否放行
- 状态文件是否同步更新

### Reviewer Gate

reviewer 不只审代码，还要审：

- 是否越过本 lane 边界
- 是否偷偷扩大写集
- 是否破坏冻结接口
- 是否漏写状态工件

### Failure Propagation

lane 失败不一定阻塞全局。

规则：

- 若失败节点只影响局部子图，则只阻塞依赖它的节点
- 若失败节点拥有 schema / contract / root-state ownership，则触发全局 freeze

### Rollback Impact Handling

回溯后不是整盘重来，而是：

- 计算受影响子图
- 失效相关节点
- 保留未受影响节点
- 从最近稳定快照重排 lane

## 12. Change Request Protocol

当用户在执行中提出变更时，系统必须：

1. 暂停当前 wave
2. 识别变更影响面
3. 判断哪些已完成节点仍然有效
4. 识别失效节点和新增节点
5. 重建工作图和 lane 分配
6. 更新状态工件

禁止把变更“悄悄融入”当前 lane。

## 13. Rollback Protocol

回溯触发条件包括：

- 上游架构判断被证明错误
- 技术探针推翻关键 ADR
- 共享契约在集成时破裂
- checkpoint 发现结构性失配

回溯必须输出：

- 回溯起点
- 失效节点
- 保留节点
- 新的执行基线
- 重新生效的 gate

## 14. Bug-Driven Evolution Protocol

每次发现 bug 时，除了修 bug 本身，还必须：

1. 识别根因层
2. 判断是代码缺陷、协议缺陷、拆解缺陷、依赖缺陷还是验收缺陷
3. 把新防御约束写回 skill 工作流或状态协议
4. 在 `DECISIONS.md` 或对应治理文档中留下记录

也就是说，bug 必须推动框架进化，而不是只推动局部修复。

## 15. Quality Requirements

本技能包必须满足以下质量要求：

- 不制造伪并行
- 不允许隐藏状态漂移
- 不允许没有 gate 的直接合并
- 不允许跳过依赖分析
- 不允许在需求未锁定前开始并行实现
- 不允许在 contract version 漂移时继续旧 lane
- 不允许只更新 Markdown 而不更新结构化状态

## 16. Migration Guidance

从 `autopilot.md` 迁移到本技能包时，推荐遵循：

- 复用旧 autopilot 的需求锁定协议
- 复用旧 autopilot 的 ADR 机制
- 复用旧 autopilot 的 MDU 标准
- 复用旧 autopilot 的阶段验收、显式回溯、需求变更和 bug-driven evolution 协议
- 重写入口、执行状态机、工作图和 lane 调度

旧工作流与新工作流关系：

- 旧 autopilot 是串行开发驾驶员
- 新 skill 是串行控制面 + 并行执行面的开发编排器

## 17. Release Positioning

这个 skill package 适合被描述为：

- 用一句需求自动接管完整开发流程的并行开发技能包
- 支持 lane 调度、状态治理和冲突控制的开发编排器
- 将命令式 autopilot 迁移为自然语言接管式开发 orchestration skill 的通用基座

## 18. Final Principle

这个技能包的本质，不是让更多 agent 一起写代码，而是让开发工作流在保持可解释、可恢复、可治理的前提下，安全地按需并行。

只要它能把一句开发需求稳定转换为一条有状态、有依赖图、有 lane 调度、有合并门禁、有回溯能力的完整开发工作流，这个 skill package 就达到了它的发布标准。

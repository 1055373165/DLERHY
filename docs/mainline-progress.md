# Mainline Progress

Last Updated: 2026-03-29
Status: reviewer-workbench-mainline
Rule: 每轮结束后先校准主线，再决定下一轮；不阻断用户使用的增强项一律下沉到优化清单。

## 1. 当前主线定义

当前真正的主线不是继续扩写 Runtime V2 的抽象设计，而是：

1. 保持 Runtime V2 control plane 已落地能力稳定。
2. 继续把 Memory Governance 做成 reviewer/operator 可直接使用的产品能力。
3. 把 standalone frontend 的 `Chapter Workbench` 收敛成可持续使用的 reviewer/operator 工作台。

一句话概括：

> 当前主线 = `Runtime V2 已落地控制面` + `Memory Governance` + `Chapter Workbench 产品化`

## 2. 已完成主线能力

### 2.1 Runtime V2 round-2

以下能力已经进入“已完成并可继续复用”的状态：

- `ReviewSession` runtime scaffold
- packet/review `lane health`
- `RecoveryMatrix`
- review deadlock incidentization + bounded replay
- `chapter_hold` escalation boundary
- bundle rollback governance + stable revision rebinding
- `REQ-MX-01 ~ REQ-MX-04` acceptance

### 2.2 Memory Governance

以下能力已不再是设计草案，而是主线代码：

- `proposal-first` chapter memory 写入
- review pass commit memory proposals
- stale / blocking / rerun / action invalidation proposal retirement
- explicit `list / approve / reject` workflow
- explicit override HTTP API
- explicit override 的 operator audit / timeline surface

### 2.3 Chapter Workbench

当前前端 reviewer/operator 工作台已经具备：

- chapter queue rail
- owner / assignment 操作
- pending proposal approve / reject
- chapter detail worklist
- unified review / action / memory timeline
- `Latest Change`
- `Recommended Next Step`
- timeline-driven focus
- `Next in Queue`
- `Session Trail / Session Digest`
- `focused / flow` workbench mode
- workbench mode 持久化恢复

## 3. 当前进度判断

当前阶段已经不再缺“更多底层能力”，而是进入：

`Reviewer / Operator 工作台主线收敛阶段`

判断标准：

- 后端 control plane 足够支撑继续推进产品面。
- memory proposal lifecycle 已形成闭环。
- reviewer/operator API 已足够支撑前端持续迭代。
- 当前最影响项目推进的，是让 reviewer/operator 真能顺畅连续工作，而不是继续补抽象层。

## 4. 下一阶段主线 Todo

这些事项继续保留在主线上，因为它们直接影响用户是否能顺畅使用系统。

### P0

- 把 `focused` 模式做成真正的“单章精查”视图
  - 收窄 queue/batch 提示
  - 突出当前章节的 blocker、proposal、action
- 把 `flow` 模式做成真正的“连续处理”视图
  - 强化 next-in-queue 入口
  - 强化连续处理节奏反馈
- 补 reviewer/operator 工作台的端到端 acceptance
  - chapter queue
  - assignment
  - proposal override
  - follow-up action
  - timeline / latest change / next step

### P1

- 把 explicit override / action execution 的 actor、note、result 更完整挂到统一 timeline
- 继续把 reviewer/operator console 收敛成真正的 chapter workbench，而不是功能拼盘
- 决定 standalone frontend 与正式产品入口的关系

## 5. 非主线项处理规则

以下类型默认不进入主线，而进入优化清单：

- 不影响用户完成当前任务的 UI 微调
- 不影响 reviewer/operator 完成闭环的交互动效
- 更细的可视化 polish
- 更强但暂不阻断使用的统计摘要
- 更漂亮但不改变决策质量的布局与文案修饰

执行规则：

1. 每轮结束先判断本轮改动是否直接提升主线闭环能力。
2. 如果只是“更顺滑 / 更好看 / 更丰富”，但不阻断使用，则写入 `docs/optimization-backlog.md`。
3. 只有直接影响项目推进和用户闭环效率的内容，才继续占用主线。

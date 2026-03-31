# Mainline Progress

Last Updated: 2026-03-31
Status: runtime-self-heal-mainline
Rule: 每轮结束后先校准主线，再决定下一轮；不阻断用户使用的增强项一律下沉到优化清单。

## 1. 当前主线定义

当前真正的主线不是继续扩写 reviewer/operator 前端工作台，而是：

1. 保持 Runtime V2 control plane 已落地能力稳定。
2. 把 `incident -> repair plan -> validation -> bundle publish/rollback -> replay` 这条自愈链补成 runtime 自己可驱动的闭环。
3. 把 Memory Governance 与 Chapter Workbench 视为辅助产品面，不再占用当前最核心的主线节奏。

一句话概括：

> 当前主线 = `Runtime V2 已落地控制面` + `自愈 incident/repair 闭环` + `最小必要产品面`

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
- runtime incidents now seed structured `repair plan` plus runtime-owned `repair_dispatch / execution lineage` across review deadlock and export misrouting
- runtime repair dispatch is now bound to a claimable `REPAIR` work-item lane, so self-heal execution is no longer only proposal/incident JSON metadata
- repair execution ownership now sits in the executor-owned `REPAIR` lane: review deadlock and export misrouting are scheduled first, then claimed and finished after commit, and repair work-items only succeed after validate/publish/finalize complete
- `REQ-EX-02` now proves the scheduled repair lane can be claimed, executed, published, and replayed end-to-end through the API surface
- repair execution is now delegated through a dedicated `RuntimeRepairWorker`, and repair work-items carry explicit worker contract metadata (`claim_mode / claim_target / dispatch_lane / worker_hint / worker_contract_version`) instead of relying on executor-only inline assumptions
- repair work-items are now resolved through an explicit `RuntimeRepairWorkerRegistry`, so `worker_hint / worker_contract_version` are part of the real execution contract instead of passive metadata
- unsupported repair worker hints or contract versions now fail deterministically inside the `REPAIR` work-item lifecycle, preserving audit and failure lineage instead of escaping around the lane bookkeeping

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
- queue judgment filter
- operator lens 子队列
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
- focused 模式下的“当前章节优先面 / 当前先处理”
- flow 模式下的“连续处理节奏 / 连续处理接力 / Flow Exit Strategy”
- release-ready lane 的 fallback candidate、gate-count judgment、连续放行判断，以及“放行后看最后观察”的 lane 内切换
- release-ready lane 的连续放行决策卡：明确区分“现在可放行 / 还差最后观察 / 下一步切哪条 lane”
- release-ready lane 的批量放行反馈：明确当前 lane 里还剩多少章可连续放行、多少章要退回最后观察
- release-ready lane 的连续放行结果反馈：在 operator 动作后明确显示这次变化是继续放行、切回最后观察，还是退回继续观察
- release-ready lane 的放行 lane 收口反馈：在 operator 动作后明确显示这条 lane 里还剩多少章可直接放行、以及之后回到多少章最后观察
- release-ready lane 的放行 lane 退出策略：在 operator 动作后明确给出下一步是继续下一条放行候选、切到最后观察 lane、留在观察 lane，还是回到整条队列
- release-ready lane 的放行链完成态：明确区分这条放行链当前是继续推进中、本轮已收口、退回继续观察，还是当前 scope 已整体收口
- release-ready lane 的放行批处理阶段：明确区分当前批处理是在连续放行中、已转入最后观察收尾、退回继续观察修正，还是本轮已整体收口
- release-ready lane 的放行批处理摘要：明确显示这一轮批处理中已完成放行多少章、剩余多少 release-ready 章节、以及还有多少章在最后观察 lane
- release-ready lane 的批处理摘要已上提到 Operator Lens 顶部 lane 摘要，reviewer 切到 release-ready 视角时不用滚到 detail 中段也能先判断这轮还该不该继续推进
- release-ready lane 的批处理摘要已接入 Session Digest，flow 模式下即使还没展开中段 detail，也能先看这一轮放行链是否值得继续
- release-ready lane 的退出策略已接入 Session Digest，reviewer 在 flow 模式顶部就能直接看到“继续推进 / 切回最后观察 / 回队列”的建议和快捷动作
- release-ready lane 的判断已下沉到 queue rail 顶部的“放行链总览”，左侧一眼就能看出这轮该继续冲放行，还是切回最后观察收尾
- release-ready lane 的压力/容量视角已进入 queue rail 顶部，reviewer 在左侧就能判断这轮还有多少放行余量、观察 backlog 还有多大
- release-ready lane 的压力判断已带直接执行入口，reviewer 在 queue rail 顶部就能按当前压力建议切回最后观察 backlog，或继续沿 release-ready lane 推进
- release-ready lane 的去留判断已上提到 Operator Lens 与 Session Digest，reviewer 进入 release-ready 视角后，不必先滚进 detail 区也能知道这条 lane 该继续冲还是先回观察 backlog
- release-ready lane 的放行把握度已进入 queue rail / Operator Lens / Session Digest，reviewer 现在能区分这条 lane 是稳定可冲、临界边缘，还是其实已经退回观察收尾
- release-ready lane 的漂移趋势已进入 queue rail / Operator Lens / Session Digest，reviewer 现在还能看出这条 lane 是在变稳，还是已经开始明显向收尾切换点漂移
- release-ready lane 的 lane health 汇总已进入 queue rail / Operator Lens / Session Digest，reviewer 现在可以直接把一条 lane 判断成“健康可冲 / 临界收尾 / 需要切回观察修正”
- release-ready lane 的路线建议已进入 queue rail / Operator Lens / Session Digest，reviewer 现在可以先读 `Lane Health` 对应的主导动作，再决定是否继续冲放行、切回最后观察，还是回整条队列
- 当 release-ready lane 已处于决定性状态时，supporting signals 会默认收拢，只保留路线建议与按需展开入口，减少 reviewer 在进入 detail 前的扫描成本
- release-ready 的 route-first 判断现在更接近 lane 入口：在决定性状态下，reviewer 进入该视角后先看到路线建议，再决定是否停留在这条 lane，而不是先扫中段 supporting cards
- release-ready 的 route-first 判断现在已经进入 `Operator Lens` 入口层；reviewer 切到 release-ready 子队列后，可以先按入口判断处理，再决定是否继续停留在当前章节 surface
- release-ready 的 route-first 判断现在已经前移到 `子队列入口预判`；reviewer 在真正切入某条 release-ready 子队列前，就能先读 go / no-go 建议，减少误入 lane 的次数
- release-ready 的 route-first 判断现在已经进一步上提到 `Lens 选择预判`；reviewer 在真正点选某条 Operator Lens 子队列前，就能先知道应该优先进入哪条处理链，进一步减少错选 lens 后再回退的成本
- release-ready 的 route-first 判断现在已经进一步进入 `Active Scope / Session Digest` 的高层摘要；reviewer 在滚到 Operator Lens 控制区前，就能先读到更值得进入哪条 lens，再决定是否继续下钻到子队列入口
- release-ready 的 route-first 判断现在已经收成可执行的 `高层路线建议`；reviewer 在 `Active Scope / Session Digest` 这一层就能直接按建议进入合适的 lane，而不用再滚到 Operator Lens 才开始操作
- release-ready 的高层判断现在已经压成更简洁的 `Lane 去留判断`；reviewer 在顶部先读“继续冲 / 切回观察 / 回队列”，再决定是否继续下钻到 lane detail
- `Lane 去留判断` 现在已经进一步收成更短的“状态 + 理由 + 动作”卡；reviewer 在 Active Scope / Session Digest 顶部先读短理由，再决定是否继续下钻到 lane detail
- `Lane 去留判断` 现在已经进一步去掉 summary 卡上的多枚 chips，改成一条更轻的 `摘要 · ...`；reviewer 在 Active Scope 顶部读完状态、理由和摘要后，就能更快做 go / no-go 决策
- 当 `Lane 去留判断` 已经存在时，顶层重复的 `Lens 选择建议 / Session 入口建议` 现在会直接折叠掉；reviewer 在 queue/session 层先读一张主导卡，而不是看多张含义相近的 route cue
- queue rail 上的 `适合放行 / 继续观察` 结果提示，以及最近一次放行链的下一步 lane 反馈

## 3. 当前进度判断

最近几轮主要在推进 `Chapter Workbench`，这对 operator 产品面有价值，但和“runtime 发现故障后能自决策、自修复、继续拉起失败任务”的核心目标相比，已经发生了主线偏移。

现在已经重新校准为：

`Runtime Self-Heal 主线收敛阶段`

判断标准：

- review/export deadlock 与 bundle rollback 的 control-plane 骨架已经在。
- incident 已经能自动生成可执行 repair plan，并把 deterministic handoff 落到 proposal / incident 上。
- runtime 现在已经会把 repair dispatch 显式绑定到 claimable `REPAIR` work item，并通过现有 work-item 生命周期完成领取和成功收口。
- 当前真正缺的是“把这条 repair worker registry 继续接到真正独立的 repair-agent 实现”，让 runtime 不只是按 hint/version 选择 contract，还能把 repair lane 交给不同 repair executors。
- Memory Governance 与 Chapter Workbench 已经够用，后续默认降为辅助线，除非直接阻断自愈主线。

## 4. 下一阶段主线 Todo

这些事项继续保留在主线上，因为它们直接影响用户是否能顺畅使用系统。

### P0

- 把 runtime incident 继续推进成 runtime-owned repair dispatch / execution surface
- 把 repair dispatch 从当前 executor-owned repair lane 继续推进到独立 repair agent / repair lane worker contract
- 把 repair worker registry 继续推进到 genuinely distinct repair-agent implementations / selection
- 把后续 patch validation、bundle publish/rollback、replay scope 继续统一绑定到 repair work-item lineage
- 保持最小 replay scope 和 audit lineage 不被“智能修复”破坏

### P1

- 把 explicit override / action execution 的 actor、note、result 更完整挂到统一 timeline
- 继续维护 Chapter Workbench，但只处理直接阻断 reviewer/operator 使用的缺口
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

# Mainline Progress

Last Updated: 2026-04-02 15:22 +0800
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
- the repair registry now resolves distinct worker implementations for review deadlock and export misrouting, and those workers explicitly reject unsupported incident kinds instead of behaving like one generic worker behind different hints
- the executor-owned repair lane now resolves explicit repair-agent adapters instead of raw workers, so future remote or agent-backed executors can slot in behind the same contract without reopening executor orchestration
- the executor-owned repair lane now also resolves an explicit repair-executor contract, so `execution_mode / executor_hint / executor_contract_version` are first-class runtime routing inputs instead of implicit assumptions
- unknown repair executor hints or versions now fail deterministically inside the repair work-item lifecycle, preserving the same bounded failure/audit behavior already established for worker selection
- export misrouting repair now runs through an agent-backed subprocess executor behind that same executor contract, proving the REPAIR lane can hand a repair dispatch to a genuinely independent execution body without reopening lifecycle semantics
- the repair lane now also resolves an explicit repair-transport contract, so `transport_hint / transport_contract_version` are first-class routing inputs underneath the executor contract instead of hidden subprocess assumptions
- export misrouting repair now runs through a `transport_backed` executor plus a subprocess transport, proving the runtime can separate executor selection from transport selection without reopening REPAIR lifecycle semantics
- review deadlock repair now also runs through that same `transport_backed` executor plus subprocess transport path, so transport-backed self-heal is no longer limited to export misrouting
- transport-backed repair execution now also supports a `configured_command_repair_transport`, and review/export controllers can honor run-level transport override hints without reopening repair lane lifecycle semantics
- transport-backed repair execution now also supports an `http_repair_transport`, and bounded repair lanes can route through a remote HTTP executor endpoint while preserving the same deterministic REPAIR work-item lifecycle and result lineage
- unknown repair transport hints or versions now fail deterministically inside the repair work-item lifecycle, preserving bounded audit and failure lineage at the transport layer too
- run-level repair dispatch preferences now also cover `execution_mode / executor_hint / executor_contract_version`, not just transport
- export misrouting now proves a run-level `agent_backed` subprocess executor override end-to-end
- review deadlock now proves all currently supported bounded-lane routing variants end-to-end: built-in subprocess transport, configured-command transport, HTTP transport, and agent-backed subprocess executor override
- `packet_runtime_defect` is now the third bounded repair lane:
  - packet controller can open bounded packet runtime defect incidents and seed repair dispatch
  - packet replay stays bounded to packet scope
  - controller reconcile now auto-projects packet lane health instead of leaving packet repair as an explicit-call side path
- packet runtime defect now proves direct controller-level routing parity across:
  - in-process
  - agent-backed subprocess executor
  - configured-command transport
  - HTTP transport
- packet runtime defect now also proves the real `ControllerRunner -> REPAIR lane` automatic path across:
  - HTTP transport
  - configured-command transport
  - agent-backed subprocess executor
- `REPAIR` work-items now carry an explicit external-agent request contract, including:
  - request contract version
  - full repair dispatch snapshot
  - full repair plan snapshot
  - owned files
  - validation / bundle / replay sections
- repair execution now emits an explicit result contract across both in-process and runner-backed paths,
  so future remote repair agents can return stable executor/transport/result metadata without relying
  on ad hoc dict shape
- transport-backed and agent-backed executor paths now reject malformed remote result payloads unless
  they satisfy the explicit repair result contract version, so bad remote replies fail
  deterministically at the executor boundary instead of entering incident lineage as fake success
- the runtime now also has a truly independent remote repair-agent contract path:
  - `runtime_repair_contract_runner`
  - contract-backed agent executors
  - contract-backed HTTP transport execution
  - auto-default remote route selection when a global endpoint is configured
- remote repair results now preserve execution provenance:
  - execution id
  - execution status
  - execution start / completion timestamps
  - transport endpoint
- bounded repair payloads now also carry explicit repair decisions:
  - `repair_agent_decision`
  - `repair_agent_decision_reason`
  - runtime currently accepts `publish_bundle_and_replay` deterministically and rejects unsupported decisions
- the `REPAIR` lane now interprets non-default remote decisions deterministically:
  - `manual_escalation_required` becomes an explicit terminal repair outcome
  - `retry_later` becomes an explicit retryable repair outcome
- decision-aware repair dispatch lineage now also records:
  - `next_action`
  - `retryable`
  - `retry_after_seconds`
  - `next_retry_after`
- packet runtime defect now also proves the retry-later path, so all three bounded repair lanes
  share decision-aware repair lineage semantics

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
- 当前真正缺的，已经不再是“更多 bounded repair lanes”，因为 packet runtime defect 已经成为第三条 lane，并且自动路径也证明了 route parity。
- 现在离用户核心目标最近的缺口，已经不再是“理解 decision 是什么”，而是让 scheduler / claim layer 真正 honor 这些 guidance。
- 最近两条已完成主线是：
  - `batch-37`: `remote decision-aware REPAIR lane`
  - `batch-38`: `decision-aware repair dispatch lineage`
- 依照 fork rule，下一条已冻结的主线是：`repair backoff / escalation-aware scheduling`。原因是：
  - 三条 bounded repair lanes 都已在主线上
  - run-level executor / transport routing 已在主线上
  - `REPAIR` lane claim/execute/validate/publish/replay 已在主线上
  - explicit request/result contract 已在主线上
  - truly independent remote contract execution 已在主线上
  - remote execution provenance 与 explicit repair decision 已在主线上
  - `next_action / retryable / retry_after` lineage 已在主线上
  - 剩余最高影响缺口就是让 runtime 在 scheduling / claim 层真正 honor 这些 guidance
- Memory Governance 与 Chapter Workbench 已经够用，后续默认降为辅助线，除非直接阻断自愈主线。

## 4. 下一阶段主线 Todo

这些事项继续保留在主线上，因为它们直接影响用户是否能顺畅使用系统。

### P0

- 把 runtime incident 继续推进成 runtime-owned repair dispatch / execution surface
- 把 decision-aware repair guidance 接到真正的 scheduling / claim layer 上
- 让 runtime 不只记录 repair decision，还要真正 honor：
  - `manual_escalation_required`
  - `retry_later`
  - `publish_bundle_and_replay`
- 对 `retry_later` 引入 bounded backoff-aware claimability，而不是立刻又把 REPAIR work-item claim 回来
- 对 manual escalation 引入 explicit non-claimable / human-resume boundary，而不是继续自动重试
- 把后续 patch validation、bundle publish/rollback、replay scope 继续统一绑定到 repair work-item lineage
- 保持最小 replay scope 和 audit lineage 不被“智能修复”破坏

### P1

- 把 explicit override / action execution 的 actor、note、result 更完整挂到统一 timeline
- 继续维护 Chapter Workbench，但只处理直接阻断 reviewer/operator 使用的缺口
- 决定 standalone frontend 与正式产品入口的关系

## 4.1 最新批次推进

主线在 `batch-38` 之后已经继续闭环到 `batch-62`：

- `batch-39`
  - repair scheduling / claimability 会真正 honor `retry_later`
  - manual-escalation terminal repair item 默认保持 non-claimable
  - terminal manual-escalation item 会阻止 reseed duplication
- `batch-40`
  - `IncidentController.resume_repair_dispatch(...)` 已落地
  - blocked repair dispatch 支持 explicit resume、route override、request-contract refresh
- `batch-41`
  - packet runtime defect direct path 已验证 `manual_escalation -> explicit resume -> override`
  - real `ControllerRunner -> REPAIR lane` automatic packet path 已验证同样 parity
- `batch-42`
  - bounded-lane control-plane surface 已直接暴露 `repair_blockage`
- `batch-43`
  - Forge v2 的 `SPEC.md / FEATURES.json / init.sh` 已从 repo truth 引导落地
- `batch-44`
  - run/document/export detail surface 已暴露 normalized blockage summary
- `batch-45`
  - export dashboard record 也已暴露同样的 blockage summary
- `batch-46`
  - document summary / history latest-run context 不再只偏 export lane
- `batch-47`
  - `.forge/init.sh` 已扩大到 `Ran 41 tests, OK`
  - 默认 resume smoke 现在覆盖 workflow blockage parity
- `batch-48`
  - `snapshot.md / progress.txt / docs/mainline-progress.md` 已与 `.forge` truth 对齐
- `batch-49`
  - packet latest-run workflow parity 已补齐显式 API 回归
- `batch-50`
  - `.forge/init.sh` 已扩大到 `Ran 42 tests, OK`
  - 默认 resume smoke 现在同时覆盖 review / packet 两条 non-export latest-run parity
- `batch-51`
  - Forge v2 已把动态分叉治理、单 ledger 回写、合法停机审计写成正式协议
- `batch-52`
  - 默认 Forge v2 smoke 现在还会自动校验 governance drift
  - 这条 init baseline 不再只验证 runtime 行为，也验证自治协议没有悄悄退化
- `batch-53`
  - fully green inventory 现在只算 checkpoint
  - active takeover 会继续做 post-completion continuation scan，而不是自然停机
- `batch-54`
  - app lifecycle 已切到 lifespan-managed path
  - 默认 smoke 不再出现 FastAPI `on_event` deprecation warning
- `batch-55`
  - sqlite backfill helper 与代表性 smoke tests 已补齐资源释放
  - 默认 smoke 不再出现 `ResourceWarning: unclosed database`
- `batch-56`
  - 默认 `.forge/init.sh` 现在会自己 hard-fail 旧的 warning class 回归
  - warning hygiene 不再只靠 report 里的 side probe 维持
- `batch-57`
  - governance validator 现在会证明 warning-hygiene gate 仍然接在默认 `.forge/init.sh` 上
  - governance validator 现在也会证明 forbidden-warning pattern 没被悄悄删掉
- `batch-58`
  - governance validator 现在会证明最新 verified batch/report artifacts 还在磁盘上
  - governance validator 现在也会证明 `.forge/STATE.md` 仍然维持显式 mainline checkpoint 字段
- `batch-59`
  - governance validator 现在会从 `.forge` truth 动态推断最新 feature / batch / report marker
  - 后续 verified checkpoint 不再需要追着最新 marker id 改 validator
- `batch-60`
  - governance validator 现在验证默认 `bash .forge/init.sh` contract 与后置校验输出，而不是固定 smoke 数量
  - 后续 smoke widening 不再需要为了 `Ran N tests` 再补一次 validator self-patch
- `batch-61`
  - governance validator 现在会从 `.forge/STATE.md` 动态推断当前 checkpoint 形态
  - 后续 active batch 不再需要为了 `mainline_complete / none` 状态假设再补一次 validator self-patch
- `batch-62`
  - governance validator 现在会从 `.forge/STATE.md` 动态推断并校验完整 checkpoint tuple
  - `authoritative_batch_contract / expected_report_path` 不再只是弱存在字段

当前 widened runtime self-heal baseline 仍保持：

- `Ran 95 tests, OK`

当前默认 Forge v2 smoke baseline 已更新为：

- `bash .forge/init.sh`
  - `Ran 42 tests, OK`
  - `smoke warning hygiene validated`
  - `governance contract validated`
  - no FastAPI lifecycle deprecation warning
  - no sqlite unclosed-database resource warning

主线判断：

- 现在最接近用户目标的缺口，不再是新的 transport / UI / operator polish
- 而是保持：
  - runtime self-heal 闭环稳定
  - workflow blockage parity 不回退
  - Forge v2 在遇到 mid-run 分叉时也能自治且不早停

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

## 6. 2026-04-02 15:22 +0800 更新

- `batch-50` 已闭环：
  - `.forge/init.sh` 已扩大为 `Ran 42 tests, OK`
  - 这条 smoke 现在覆盖：
    - controller/runtime self-heal baseline
    - run summary blockage parity
    - export self-heal blockage parity
    - export dashboard blockage parity
    - document summary/history blockage parity
    - packet latest-run workflow parity
- `batch-51` 已闭环：
  - Forge v2 已正式定义：
    - `mainline_required / mainline_adjacent / out_of_band`
    - branch decision 的单 `.forge/` ledger transaction
    - post-verification stop-legality audit
- `batch-52` 已闭环：
  - `.forge/init.sh` 现在还会自动校验 governance drift
  - 默认 takeover baseline 现在同时守住 runtime self-heal 与 Forge v2 autonomy contract
- `batch-53` 已闭环：
  - Forge v2 在 full takeover 下会把 inventory completion 视为 checkpoint
  - 后续必须做 continuation scan，找到下一个 credible `change_request` 或显式证明没有
- `batch-54` 已闭环：
  - 默认 smoke 不再出现 FastAPI `on_event` deprecation warning
  - lifecycle lazy-init regression 已通过 `REQ-EX-02` targeted regression 收口
- `batch-55` 已闭环：
  - 默认 smoke 不再出现 sqlite `ResourceWarning: unclosed database`
- `batch-56` 已闭环：
  - 默认 `.forge/init.sh` 会直接拒绝已知 warning class 回归
  - 这条 warning-free baseline 现在是默认接管协议的一部分，而不是事后补充命令
- `batch-57` 已闭环：
  - warning-hygiene gate 不再只是 runtime 路径里的行为
  - governance drift 校验现在也会证明这条 wiring 和 forbidden-warning pattern 仍然存在
- `batch-58` 已闭环：
  - active latest checkpoint 不再只靠 handoff prose 指向
  - governance drift 校验现在也会证明最新 verified artifacts 和 mainline-complete STATE 字段仍然对齐
- `batch-59` 已闭环：
  - governance validator 不再把最新 checkpoint marker 写死在脚本里
  - 最新 verified checkpoint 现在是从 `.forge` truth 动态发现的
- `batch-60` 已闭环：
  - governance validator 不再把当前 smoke 测试数写死在脚本里
  - 默认 smoke contract 现在按命令与后置校验输出受保护，而不是按固定 `Ran N tests` 标记受保护
- `batch-61` 已闭环：
  - governance validator 不再把当前 checkpoint 状态形态写死在脚本里
  - 默认治理校验现在会按 `.forge/STATE.md` 的 live truth 校验当前 `current_step / active_batch`
- `batch-62` 已闭环：
  - governance validator 现在会按 `.forge/STATE.md` 的 live truth 校验完整 checkpoint tuple
  - `authoritative_batch_contract / expected_report_path` 现在也进入了正式治理约束
- `batch-63` 已闭环：
  - 剩余开发任务现在已经显式委托给 Forge v2 的 `change_request` intake
  - 下一个会话不应把“没有开放 batch”误解成“没有接管人”
- 当前 `.forge` 主线状态：
  - `current_step: mainline_complete`
  - `active_batch: none`
- 当前 feature inventory：
  - `F001` ~ `F029` 全部 passing
- 下一个接手者不应再从旧 frozen batch 继续，而应把任何新工作作为 `change_request` 接到现有 `.forge/` truth 上

## 7. 剩余开发任务委托

- 当前没有待执行的 active batch。
- 剩余开发任务的默认 owner 是 Forge v2。
- 接管方式不是继续旧 batch，而是：
  - 以 `change_request` 模式接到现有 `.forge/` ledger
  - 先做 branch intake 与 stop-legality / dependency scan
  - 只有确认形成真实 slice 后，才冻结新的 batch

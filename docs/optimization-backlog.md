# Optimization Backlog

Last Updated: 2026-03-29
Scope: 不阻断当前用户使用、但值得后续打磨的增强项

## 使用规则

- 这里只放“不做也不阻断主线”的优化。
- 主线优先保证 reviewer/operator 能完成闭环。
- 本文不是总 backlog；它只收纳可延期的体验、可视化和效率增强。

## Chapter Workbench

### Focused / Flow 模式细化

- 让 `focused` 模式进一步弱化 queue rail 和 batch 语义
- 让 `flow` 模式进一步强化连续处理节奏和批处理感
- 为 mode switch 增加更强的 mode-specific 空态 / 提示
- 为 `release-ready` lane 增加更细的视觉层级与批处理 affordance，但不改变当前放行判断逻辑

### Timeline / Session 可视化

- 为 timeline 分组增加更强的视觉层级
- 为 `Latest Change` / `Session Trail` / `Session Digest` 增加更紧凑的信息密度设计
- 为“已影响当前状态”的 timeline event 增加更明显的视觉标记
- 为 session history 增加更长窗口的轻量回看能力
- 为 `Flow Exit Strategy` / `连续处理接力` / `连续放行判断` 增加更强的状态动效与视觉反馈

### Operator 便利性增强

- 为 next-step / next-in-queue 增加更丰富的快捷动作
- 增加更细的 owner workload 提示和轻量 balancing 建议
- 增加非阻断性的 reviewer/operator productivity shortcuts

### 前端 polish

- 动效与状态切换细节优化
- 视觉节奏、边距和组件层级打磨
- 长文本 / 密集信息场景的可读性优化

## Runtime / Governance

- 更完整的 reviewer/operator timeline 过滤与摘要视图
- 更细粒度但非阻断的审计信息展示
- 更丰富但不影响主流程的 dashboard / analytics 视图

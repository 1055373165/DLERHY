# 生产级重构 · P0 实施计划

> 依据：`决策 1 / 3 / 5 / 7` + 三条补强（已采纳）
> 实施原则：每阶段可独立验证、可独立 rollback；阶段之间有显式依赖

---

## 阶段依赖关系

```
P0.0 (派生状态读)  ──┬─► P0.1 (主循环重构)
                     ├─► P0.2 (evidence-driven fail)
                     └─► P0.3 (retry 冷启动)
```
P0.0 是前提；P0.1 / P0.2 / P0.3 内部独立，但都依赖 P0.0 完成。

---

## P0.0 · StageStatusCalculator 成为唯一读源（决策 1）

**目标**：消除 "pipeline JSON 缓存" 与 "业务真相" 的读写双路径。

- [ ] `stage_status.py`：补 `stuck_work_items` 字段（为 P0.2 预埋）
- [ ] `stage_gate.py`：确认已切换到 Calculator（现状已是）
- [ ] `document_run_executor.py`：所有读 `status_detail_json.pipeline.stages.*.status` 的位置改调 `StageStatusCalculator`
  - `_process_translate_stage` / `_process_review_stage` / `_process_bilingual_html_stage` / `_process_merged_html_stage` 入口判断
  - `_finalize_stage_snapshots_on_success` 重写为"只写 payload 元数据，不改 status（status 由 API 层按需派生）"
- [ ] `run_execution.py`：`reconcile_run_terminal_state` 改为 Calculator-based
- [ ] API 读接口（`get_run_summary` 等）：`pipeline.stages.*.status` 改为即时派生，不再信 JSON 缓存
- [ ] 回归测试：现有 translate 流程从头到尾跑通

**验证**：造一个 run，手动在 DB 把 `pipeline.stages.translate.status` 改成 `"failed"`，UI 仍应显示正确的 `running`（证明不再信缓存）。

---

## P0.1 · 主循环重构（决策 5 + 补强 2）

**目标**：DECIDE/EXECUTE 分离 + 主循环作为 frontier advance 单一写者 + CAS 强制。

- [ ] `_run_loop` 重构：单次迭代 = `read_snapshot()` → `plan_actions()` → `execute_writes()`
- [ ] `seed_translate_work_item`：追加 `UNIQUE(run_id, stage, target_id)` 约束（migration）
- [ ] 所有 write path 改 CAS：
  - `UPDATE document_runs SET status='succeeded' WHERE id=? AND status='running'`
  - `UPDATE work_items SET status=? WHERE id=? AND status=<expected>`
  - `UPDATE translation_packets SET status=? WHERE id=? AND status=<expected>`
- [ ] worker `on_success` 回调剥离 frontier seed 责任（如果现存）
- [ ] 读 session 显式 `REPEATABLE READ READ ONLY`
- [ ] 新增 CAS 违规监控指标 `cas_conflict_total`

**验证**：跑并发压测，assert 所有 log 里没有 `duplicate key` 错误 + 所有 run 最终达到预期终态。

---

## P0.2 · Evidence-driven fail + required/optional + stuck + PAUSED（决策 7 + 补强 3）

**目标**：run 失败不再来自计数器，且不会因局部永久失败阻塞整体。

- [ ] `StageStatusCalculator` 增加 `stuck_work_items` 计算（`RETRYABLE_FAILED` + updated_at < now - 300s + attempts >= 3）
- [ ] Stage 分类常量：`REQUIRED_STAGES = {"translate"}`, `OPTIONAL_STAGES = {"review", "bilingual_html", "merged_html"}`
- [ ] 新增 run 终态枚举值：`SUCCEEDED_WITH_WARNINGS`, `PAUSED`
- [ ] `reconcile_run_terminal_state` 重写：
  - required stage FAILED → run FAILED
  - 仅 optional stage FAILED → run SUCCEEDED_WITH_WARNINGS（记录 `warnings.failed_stages`）
  - 所有 stage 都 SUCCEEDED → run SUCCEEDED
  - no-progress 超过 `NO_PROGRESS_SECONDS`（默认 600s） → run PAUSED
- [ ] 配置项 `BOOK_AGENT_NO_PROGRESS_SECONDS=600` 加到 `.env.example`
- [ ] 新增 API：`POST /api/v1/runs/{id}/resume` 从 PAUSED 转回 running
- [ ] UI：PAUSED 状态显示"已暂停等待恢复" + resume 按钮

**验证**：
1. 造一个 translate work_item 永久失败的 fixture → assert run 终态 `FAILED`
2. 造一个 review work_item 永久失败但 translate 完成的 fixture → assert run 终态 `SUCCEEDED_WITH_WARNINGS`，bilingual_html 照常输出
3. 造一个空载 10min 的 run → assert 进入 PAUSED，resume API 可恢复

---

## P0.3 · Retry 冷启动 + 并发 UNIQUE + lineage（决策 3 + 补强 1）

**目标**：retry = 新 run，零字段继承；并发重试被 DB 拦截；历史链可查。

- [ ] Migration：
  - `ALTER TABLE document_runs ADD COLUMN retry_of_run_id UUID REFERENCES document_runs(id)`
  - `CREATE UNIQUE INDEX idx_unique_active_run_per_document ON document_runs(document_id) WHERE status NOT IN ('succeeded', 'succeeded_with_warnings', 'failed', 'cancelled')`
- [ ] `run_control.py` · `_retry_status_detail`：删除字段继承逻辑，pipeline 冷启动
- [ ] `create_retry_run` API：捕获 UNIQUE 冲突 → 返回 409 Conflict 并附带当前 active run id
- [ ] 新增接口 `GET /api/v1/runs/{id}/lineage`：递归返回 `retry_of_run_id` 链（最多 10 层）
- [ ] UI：run 详情页显示 "本次是第 N 次重试 · 查看历史" 链接

**验证**：
1. 已失败 run 点 retry → 新 run pipeline.stages 全为 `not_started`
2. 同一 document 连续两次 retry 请求在 100ms 内 → 第二次返回 409
3. lineage API 在 3 次重试后返回 `[run4, run3, run2, run1]` 顺序正确

---

## 风险与 Rollout 策略

- **不向后兼容的 DB migration**：`UNIQUE INDEX` 若有并发 active run 会失败 → migration 前跑一次性清理脚本
- **UI 合约变更**：`pipeline.stages.*.status` 语义变化 → 前端需同步改读路径；建议 backend 改完发 RC，前端跟进后再 release
- **灰度策略**：feature flag `USE_DERIVED_STAGE_STATUS=true` 控制 P0.0 切换，便于快速回滚

---

## 完成标准（DoD）

- [ ] 原始 bug 复现测试：CH.3 留 344 BUILT packets 场景 → run 正确保持 running 直到所有 packets TRANSLATED
- [ ] 所有 P0.0-P0.3 验证点通过
- [ ] `pytest` 全量 green
- [ ] 手动跑一本真实书翻译全流程 + 一次 retry，无异常日志
- [ ] 日志中 `cas_conflict_total / no_progress_pause_total / succeeded_with_warnings_total` 指标正常上报

---

## 预估工作量

| 阶段 | 人天 | 备注 |
|---|---|---|
| P0.0 | 0.5 | 核心是搬读路径，机械工作 |
| P0.1 | 0.75 | migration + CAS 改造 + 压测 |
| P0.2 | 0.5 | 逻辑清晰，主要是 enum + reconcile 重写 |
| P0.3 | 0.5 | migration + API 新增 |
| 回归 | 0.25 | 手工端到端验证 |
| **合计** | **2.5 人天** | |

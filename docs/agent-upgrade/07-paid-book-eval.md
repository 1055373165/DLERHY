# 07 · 付费整书实跑记录（RSI 测试书，2026-09-18）

用户批准对 PDF 翻译做付费测试后，用 `scripts/run_paid_book_eval.py` 把 70 页的 RSI 测试书整本跑完真实 provider（`.env` 配置的 `deepseek-v4-flash`）。脚本走产品自身的路径：bootstrap → `TRANSLATE_FULL` run（术语 sampled、模型审校 sampled、修复 agent 关闭）→ 执行器（租约、预算、并发 8）→ 规则审校 → 双语与合并 HTML 导出 → 导出 QA，最后把阶段结果、用量、护栏、issue、术语一致率、导出 QA 与样例对照写成报告。

## 1. 三次跑出来的三类缺陷（都已修复）

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | 术语阶段 22 分钟没有任何进展，work item 重试 41 次，期间的花费在账本上完全看不到 | `deepseek-v4-flash` 是思考模型，8192 的输出上限全被 reasoning 用掉，`content` 为空 → 结构化输出解析失败；失败重试没有次数上限也没有退避；失败调用的事件随事务回滚一起消失 | provider 客户端把「答案在输出上限处被截断」单独建模（`ProviderOutputTruncated`，全是 reasoning 时 `reasoning_only`），失败异常带上已计费的 usage；`reasoning_only` 暂停 run 并提示调大上限或关闭思考；work item 默认最多 6 次尝试并指数退避；模型调用事件在事务回滚后补写（`recorded_after_rollback`），run 用量把失败调用的 token 也算进去 |
| 2 | 术语 agent 一步里请求了多个 `lock_term`，第二个等审批；审批后 run 直接失败（provider HTTP 400：assistant 的 tool_calls 没有全部对应的 tool 消息） | 恢复时只执行了等审批的那一个调用，同一步里后面的调用从未开始 | 恢复时按顺序补跑该步剩余调用；顾问型 agent（术语、模型审校、结构、导出审读）最后一次尝试仍失败时把阶段标记为 degraded 并继续翻译，只有暂停类失败才停 run，修复 agent 仍然会让 run 失败 |
| 3 | 每锁一个术语要一次审批、一次恢复、一次 work item，40 分钟只在锁词 | 一步中第一个需要审批的调用就停住 | 一步里所有调用都会启动：不需要审批的直接执行，需要审批的一次性全部提交；最后一个审批决定后 turn 才恢复；tool 消息按模型的调用顺序排列 |

## 2. 第三次（修复后）的结果

| 指标 | 值 |
|---|---|
| run 状态 / 耗时 | succeeded / 1482 秒（约 25 分钟） |
| 模型调用 | 759 次（2 次失败：provider 返回格式错误，重试后成功） |
| token | 输入 3,191,758 / 输出 205,655（未配置价格，故报告无金额） |
| 句子覆盖 | 1429 / 1429 |
| 锁定术语 / 一致率 | 75 / 1.0 |
| 输出护栏 | 1 次锁定术语违规、2 次长度比异常、2 次 provider 格式错误；13 次同轮修复调用 |
| 审校 issue | 模型审校开 45 个（42 个在跟进后解决），规则 LOW_CONFIDENCE 7 个；阻断性未决 0 |
| 导出 | 13 个双语 HTML + 1 个合并 HTML，导出 QA 全部通过；合并文档 11 章、29 张图、约 3.7 万汉字 |

按调用类型：翻译 638 次（输入 1.76M / 输出 175k），审校 agent 98 次（输入 1.12M / 输出 15k），术语 agent 21 次，术语抽取 2 次。审校 agent 的输入 token 占比高是因为每一步都重发整段对话——后续可用压缩或分批审校降低。

## 3. 运行须知

- **思考模型**：`deepseek-v4-flash` 默认会把输出预算用在 reasoning 上。本次用 `BOOK_AGENT_TRANSLATION_OPENAI_REQUEST_OVERRIDES='{"thinking": {"type": "disabled"}}'` 关闭（只在该进程的环境变量里设置，没有改用户的 `.env`）。不关的话第一处结构化输出就会触发 `reasoning_only` 暂停。
- **审批**：术语 agent 会为「不常见的概念术语」申请审批。无人值守跑用 `--approve-pending`，审批人记为 `paid-eval:auto`，每条审批都写进日志。
- **费用**：配置 `BOOK_AGENT_TRANSLATION_INPUT_COST_PER_1M_TOKENS` 等三个价格变量后，事件里才会有 `cost_usd`，报告和 org 预算也才能给出金额。
- **代理**：`httpx[socks]` 已加入依赖，SOCKS 代理下 provider 调用才可用。

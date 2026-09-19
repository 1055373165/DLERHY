# 可付费产品就绪清单

目标：用户上传一本英文书（PDF 或 EPUB），配好模型服务商，就能拿到可以直接阅读、可以付费交付的中文版，过程可控、花费可见、出错可恢复。本清单按「付费用户最先碰到的问题」排序，逐项记录状态与证据。

状态：✅ 已完成并有测试 · 🟡 部分完成 · ⬜ 未做 · 🔒 需用户决定

## 1. 交付物质量

| 项 | 状态 | 证据 |
|---|---|---|
| 整书真实翻译跑通（PDF） | ✅ | `docs/agent-upgrade/07-paid-book-eval.md`：RSI 书 1429/1429 句，术语一致率 1.0，导出 QA 全过 |
| 中文版单文件 HTML（图片内嵌、目录、不含翻译统计） | ✅ | `export/standalone.py`、`export/reader_epub.reader_html`；`tests/test_standalone_html.py` |
| 中英对照整书单文件 HTML | ✅ | `assemble_bilingual_book`；同上 |
| EPUB（任何来源，含 PDF） | ✅ | `export/reader_epub.py`，`package=epub`；XML 校验 + PyMuPDF 打开测试 |
| Markdown 图片在常见阅读器可见 | ✅ | `markdown_image_reference`；`tests/test_markdown_image_references.py` |
| 书名、章节标题为中文 | ✅ | `services/title_translation.py`、`export/titles.localize_chapter_label`；`tests/test_title_translation.py` |
| 章节结构不丢章（数字开头标题、带副标题的前置内容） | ✅ | `extract_main_chapter_number`、`looks_like_frontmatter_title`；QA Q4 `chapter_sections` |
| 渲染器升级后旧导出自动刷新 | ✅ | `EXPORT_RENDERER_VERSION`；下载旧版本返回 404 → 前端自动重导出 |
| 句级对齐粒度（整段译文挂在多个句子上） | 🟡 | 成品不受影响（按块渲染）；句级视图与审校受影响，未修 |

## 2. 可控与可恢复

| 项 | 状态 | 证据 |
|---|---|---|
| 失败重试有上限与退避，不会无限花钱 | ✅ | `DEFAULT_MAX_ATTEMPTS_PER_WORK_ITEM=6`、指数退避；`tests/test_provider_robustness.py` |
| 思考模型把输出预算用尽时暂停并说明原因 | ✅ | `ProviderOutputTruncated.reasoning_only` → `provider.reasoning_exhausted_output` |
| 顾问型 agent 失败不拖垮整本书 | ✅ | 阶段降级（degraded）；`tests/test_agent_stage_execution.py` |
| 审批按步批量提交、恢复一次 | ✅ | `tests/test_harness_kernel.py` |
| 多实例、租约、崩溃恢复 | ✅ | `tests/test_multi_instance.py`、`test_runtime_recovery.py` |
| 新装未配置服务商时应用可用，并提示去哪里配置 | ✅ | `UnconfiguredTranslationWorker`、`ProviderNotConfigured`；启动翻译返回 409 并指向「服务商」页，运行中遇到则暂停（`provider.not_configured`）；`tests/test_provider_not_configured.py` |
| 运行暂停/失败时告诉用户原因和下一步 | ✅ | `frontend/src/lib/runStopReason.ts`：余额不足、key 被拒、思考模型耗尽输出、预算上限、连续失败等，附「服务商」页链接 |
| kill -9 端到端恢复测试 | ✅ | `scripts/crash_recovery_drill.py`：440 包的书在第 222 包时 SIGKILL 服务进程，新进程 5.3 秒后接手，run 成功，2440/2440 句、无一重复翻译，整书 HTML 可下载（`--kills 3` 连杀三次同样通过）；死进程手里的包在 120 秒租约到期后重做 |
| 真实浏览器走通上传 → 预估 → 整书运行 → 三种下载 | ✅ | Playwright 驱动构建后的前端（echo 模型）；修掉了随之发现的英文按钮、折叠侧栏错位、小书预估偏高 |

## 3. 花费可见

| 项 | 状态 | 证据 |
|---|---|---|
| 所有模型调用计入账本（含失败、含回滚事务） | ✅ | `recorded_after_rollback`；`tests/test_provider_robustness.py` |
| 按组织月度预算与限额 | ✅ | `services/org_budget.py`；`tests/test_org_tenancy.py` |
| 服务商单价在界面配置（不改 .env） | ✅ | 「服务商」页；迁移 0044；`tests/test_provider_settings.py`、`ProvidersPage.test.tsx` |
| 思考模式等请求参数在界面配置 | ✅ | 「关闭思考模式」开关 + 高级 JSON；保留字段（model、messages 等）被拒绝 |
| 开始翻译前的费用预估 | ✅ | `services/cost_estimate.py`（按真实整书运行校准，RSI 书估算与实测相差约 5%，给出 ±30% 区间）；工作台开始按钮下显示；`tests/test_cost_estimate.py` |

## 4. 账号与部署

| 项 | 状态 | 证据 |
|---|---|---|
| API key、角色、组织隔离、OIDC | ✅ | `tests/test_api_auth.py`、`test_oidc.py`、`test_org_tenancy.py` |
| 指标与链路追踪 | ✅ | `/metrics`、OpenTelemetry（`tests/test_metrics.py`、`test_tracing.py`） |
| Docker 镜像构建验证 | 🔒 | 本机构建曾与 Docker 守护进程崩溃同时发生，需用户同意后再试 |
| 支付与套餐（收费渠道） | 🔒 | 需用户决定定价与支付服务商 |
| 用户协议、版权声明 | 🔒 | 需用户决定 |

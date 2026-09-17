# 生产化差距与风险登记簿

> 汇总自 `docs/architecture/01–09` 各篇的问题清单（合计 200+ 条），按「上线前必须 / 应修 / 清理」分级并去重。每条给出所在分篇编号，具体 `文件:行号` 在分篇里。日期 2026-09-16。

## A. 分级原则

- **S（阻断）**：会造成数据丢失、静默错误结果、或让文档承诺的核心流程不成立。
- **A（上线前必修）**：正确性或安全缺陷，在真实负载/部署形态下必然触发。
- **B（应修）**：一致性、性能、可维护性债，影响交付质量或迭代速度。
- **C（清理）**：死代码、遗留物、命名与文档。

## B. 风险登记簿（S / A 级）

| # | 级 | 领域 | 问题 | 后果 | 分篇 |
|---|---|---|---|---|---|
| R1 | S | 安全 | 全部 API 无鉴权；`POST /providers` 与 `/providers/{id}/test` 接受任意 `base_url`；`POST /documents/bootstrap` 接受任意服务器路径；`GET /runs/{id}/cost` 每次刷新物化视图 | SSRF、额度盗用、任意文件解析、廉价 DoS | 01 §17 |
| R2 | S | 持久化 | compose 只挂 `artifacts/exports`；uploads / blobs / parse-ir / document-images 与自动生成的 Fernet 密钥都在容器层 | 容器重建 = 原件、图片、IR 丢失；已存 API key 永久不可解密，`GET /providers` 500 | 07 §8, 01 §10 |
| R3 | S | 运行时 | 402/401/403 触发的暂停把 work item 写成 TERMINAL_FAILED；resume 后下一 tick 判 FAILED | 「暂停后继续」不成立；余额不足即整本重跑 | 02 §12-1 |
| R4 | S | 运行时 | 非 RUNNING/DRAINING 的 run 不回收租约；从 PAUSED `retry_run` 的新 run 与旧在途线程并发翻译同一 packet | 重复译文 / 主键冲突 → 终止失败 | 02 §12-3 |
| R5 | S | 运行时 | review 线程内的跟进重译与 run loop 的 translate 前沿并发（包被置回 BUILT 后被两条路径领取） | 双写、成本重复、不可复现 | 02 §12-4 |
| R6 | S | 配置 | Docker 下 `.env` 的 `OPENAI_API_KEY` 被 `_SanitizedEnvSettingsSource` 忽略；`translation_backend` 默认 `echo` 且首启自动激活 Echo 凭据 | 生产环境可能静默用 `ZH::原文` 翻译整本书 | 01 §17-2, 04 §4.7 |
| R7 | S | 数据 | 可译性协议断裂：`docir_translatability` 写入后无读者，References 保护到不了 `Sentence.translatable` | 参考文献被当散文翻译并计费 | 03 §2.3 |
| R8 | S | 数据 | 审校 issue 重开重置 `created_at`、残留 `resolution_note`、action 状态每轮刷回 PLANNED；导出 gate 用 merge 把人工 WONTFIX 翻回 OPEN | SLA/时间线失真；人工决定不被尊重；已执行动作可反复执行（破坏性失效） | 05 §14-1/2/3, 06 B-07 |
| R9 | S | 工程 | `.env.example` 未被 git 追踪；`uv sync`（dev.sh/service.sh）卸掉 pytest/ruff；无 CI | 新克隆无法按 README 启动；质量门形同虚设 | 09 §0 |
| R10 | A | 运行时 | 暂停时长计入 wall-clock / no-progress 预算；`consecutive_failures` resume 不清零 | 长暂停后 resume 立即再暂停或直接 FAILED | 02 §12-2/7 |
| R11 | A | 运行时 | `sqlalchemy.exc.TimeoutError`（连接池耗尽）被分类为不可重试 → run FAILED；SSE 每客户端占 1 池连接（上限 30） | 容量问题变成业务失败 | 02 §12-6, 01 §17-7 |
| R12 | A | 成本 | 概念解析 / 术语抽取 / 一致性 pass / review 期重译不 emit `llm.call.*`、不进 rollup、不受预算；`llm.call.completed` 随 persist 回滚而丢失；Anthropic 风格缓存 token 字段语义反转 | 成本低估，预算护栏失明 | 04 §10/§12-1, 02 §9.4 |
| R13 | A | 数据 | `translate_targeted` 终态后投影/对账不传 `packet_ids`：UI 显示 translate=running，Reconciler 每 30s 记伪漂移 | 状态失真 | 02 §12-5 |
| R14 | A | 数据 | `uq_provider_credentials_one_active` 在 SQLite 退化为全列唯一；`memory_snapshots` 唯一约束对 global 快照（scope_id NULL）无效；`seed_work_items` 去重不过滤终态与索引语义矛盾 | smoke/e2e 与 PG 行为不一致；重复快照随机命中 | 07 §11-1/4/7 |
| R15 | A | 数据 | `refresh_cost_rollup()` 对 `WITH NO DATA` 视图首刷 CONCURRENTLY 抛 55000，函数只捕 0A000 | 新库首次 `GET /cost` 500（待 PG 复现） | 07 §6 |
| R16 | A | 导出 | 门禁在用例层与服务层各跑一遍并各写 issue；`review_package` 导出也落 blocking `ALIGNMENT_FAILURE`；布局 issue 的 `block_id` 可能是合成 id 违反 FK | 审校包阻断终稿；flush 报错 | 06 B-01/06/22 |
| R17 | A | 导出 | 无版本、无原子写、无互斥：同名覆盖、先写文件后提交、`assets/` rmtree、`ExportGateError` 被用于 Playwright/源文件缺失等非门禁失败 | 并发导出互踩；失败留半成品；错误语义混淆 | 06 B-03/08/13/14/15 |
| R18 | A | 解析 | 损坏/加密 PDF（`FileDataError ⊂ RuntimeError`）静默退到正则抽取器；行内空白折叠让表格/TOC/gutter 规则失效；outline 页号 -1 变第 1 页；图页 >10% 判 mixed 整本 OCR | 垃圾结构静默入库；误判整本 | 03 §12 P-2/3/6/8 |
| R19 | A | 解析 | 每次 bootstrap 抽取两次 PDF；图片 mkdtemp 永不清理且绝对路径入库；解析在 HTTP 线程内同步跑无超时无大小上限 | 内存/磁盘泄漏；大书请求超时 | 03 §9/§11 |
| R20 | A | 前端 | 无生产托管（Dockerfile 无前端、后端无 StaticFiles、service.sh 用 `vite dev`）；Swagger 链接指向不存在的 `/v1/docs`；不认识 `succeeded_with_warnings` | 无法交付；文档失真 | 08 §9/§11 |
| R21 | A | 工程 | 测试写产物进仓库 `artifacts/parse-ir/`（8k 目录）；OCR 子进程 uv 缓存落到 `.test-tmp`（3.4GB）；36 个模块无直接测试；PG-only 行为默认从不测 | 环境污染；回归盲区 | 09 §0/§3 |
| R22 | A | 部署 | Dockerfile 单阶段、root、`pip install -e .`、不用锁文件、无 HEALTHCHECK；多实例无 leader、无 SKIP LOCKED、凭据 revision 缓存进程内、迁移无 advisory lock | 不可水平扩展 | 09 §5.4, 02 §11.1 |

## C. B 级（应修）主题清单

| 主题 | 代表问题 | 分篇 |
|---|---|---|
| 读写放大 | `GET /documents/{id}` 拉全部句子 + packet_json + 所有 superseded 快照；bootstrap 逐行 merge（数十万 SQL）；`get_run_summary` 每 tick 全量扫 work_items；worklist 加载全书 bundle 只为校验存在 | 07 §9, 02 §4, 05 §14.3 |
| 缺失索引 | `exports(document_id)`、`packet_sentence_map(sentence_id)`、`review_issues(document_id/packet_id/…)`、`worker_leases(run_id)` 等；ORM 几乎不声明二级索引，漂移测试单向 | 07 §9.3 |
| 术语/记忆 | 首轮翻译记忆为空（proposal-first）；termbase/entity 快照永远空；两套术语判定；单条批准与批量提交的 drift 校验不一致；`term-consistency` 原地改译文不产生新 attempt | 04 §9/§12 |
| Prompt/Provider | urllib 无连接池、socket 级超时、退避无 jitter/上限/Retry-After；chat 模式每次塞 1.5k token schema；无 temperature/seed；prompt 文本不入库；`prompt_version` 不随 profile 变 | 04 §4/§11 |
| 审校 | 无检查项开关；PDF 布局策略 450 行硬编码分支树；无 issue 列表/详情/状态变更端点；`resolve_missing_issues` 一刀切解决导出层 issue；REPARSE 在 EPUB 上抛异常中断整个 review 阶段 | 05 §13/§14 |
| 导出 | 三种渲染器标题层级不一致（HTML 无层级、MD 固定 `###`、EPUB 重复 h1/h2）；表格整块 protect 永不翻译；读者稿内嵌硬编码价目表的「翻译统计」；KaTeX 走 CDN；译文选择三套规则 | 06 §7/§9/§10 |
| 前端 | 状态机在前端复制并漂移；6 路轮询不用 SSE；`WorkspaceContext` 无 memo 且 12 字段无消费者；错误被伪装成空态；action 结果判断 `executed` vs 后端 `completed` | 08 §4/§11 |
| API 一致性 | `ValueError → 404` 泛化映射；`POST /runs` 允许不可执行的 run 类型；重复入队返回 500 而非 409；两个端点裸 dict；`schemas/workflow.py` 与 `read_models.py` 40 个类型一比一重复 | 01 §17, 05 §8 |
| 层次依赖 | `services → orchestrator → services` 循环；`domain/structure/pdf.py` 反向 import `ingestion` 私有函数；导出层依赖解析层私有符号；`application/analytics.py` 依赖 `services.review` 内部类 | 03 §0, 05 §11, 06 §11.2 |

## D. C 级（清理）

- 死枚举与死字段：`DocumentRunType.bootstrap/repair_targeted`、`WorkItemStage.BOOTSTRAP`、`PacketStatus.RUNNING/FAILED`、`ActionStatus.FAILED/CANCELLED`、`IssueStatus.WONTFIX`、`Detector.MODEL/HUMAN`、`ProtectedPolicy.MIXED`、`budget_hint`、`protected_spans`、`system_prompt_static/dynamic`、`raw_usage`、19 种从不发出的事件。
- 死代码：`ChapterMemoryBackfillService` 无生产调用；`markup.build_merged_toc`；`StageGateKeeper.candidate_evidence`；`bootstrap_epub` 别名；前端 `shorten`/`currentPipelineDetail`；约 20 个 `ExportService` 委托壳只被测试调用。
- 脚本：39 个中建议删 25、迁 CLI 6、保留 4、待定 4（09 §4）。
- 仓库：`.agents/skills`、`interview/`、`tasks/pdf-pipeline-v2.md`、`docs/README.md` 与根 README 重叠；工作区 3.7GB `.test-tmp`、`deliverable/`、`LLM-Book*`、`books/` 版权风险目录。
- 文档：README 远端仓库名拼写、`alembic.ini` 端口 5433、`Settings.app_version` 与 pyproject 双份维护、baseline-tests.md 数字过期。

## E. 建议的处理顺序

1. **止血（1–2 周）**：R3/R4/R5/R10/R11（运行时正确性）、R6（Echo 误用与 key 读取）、R2（卷与密钥外置）、R9（`.env.example`、dependency-groups、最小 CI：ruff + golden + 单元）。
2. **统一 LLM 调用面（与 agent 升级 H0 合并）**：单一 client（httpx、连接池、超时、退避、Retry-After）、每次调用一条 `llm.call.*` 事件、统一计费与预算；修 R12。
3. **数据契约**：为 JSON 列定义 pydantic schema；issue/action/export 引入版本行（修 R8、R17）；补索引；漂移测试双向。
4. **鉴权与多租户骨架**（R1）：至少 API key + 单租户 org 表 + 路径/URL 白名单。
5. **解析层止血**（R7、R18、R19）：可译性接线、FileDataError 上抛、空白折叠改为保留、解析移到后台 work item。
6. **前端生产路径**（R20）与 SSE 接入。
7. B 级按子系统随 agent 升级各阶段逐步消化（见 [agent-upgrade/03-roadmap.md](../agent-upgrade/03-roadmap.md)）。

## F. 处理记录

| 日期 | 编号 | 提交 | 说明 |
|---|---|---|---|
| 2026-09-17 | R12 | `d88fdfa`、`0fcca44` | 统一 provider client；所有模型调用都产生带 `call_kind` 的事件；run 花费从事件聚合；缓存 token 字段修正 |
| 2026-09-17 | R15 | `0fcca44` | 物化视图与 `refresh_cost_rollup()` 删除（迁移 0034），成本端点直接聚合事件 |
| 2026-09-17 | R3、R4、R5、R10、R11、R13 | 本次提交 | 见 `docs/agent-upgrade/03-roadmap.md` H0「运行时正确性」条目；R5 只堵住了双写入口，review 期重译改为独立 work item 留到 H2 |
| 2026-09-17 | R2、R6、R14（索引部分） | 本次提交 | compose 挂载整个 artifacts、prod scope 强制密钥与非 echo；命名空间 API key 变量；`uq_provider_credentials_one_active` 在 SQLite 上也是部分索引。R14 其余两项（memory_snapshots 全局唯一、seed 去重语义）未处理 |
| 2026-09-17 | R8；05 §14.1-1…5；06 B-07 | 见 H2 提交 | issue 账本：`review_issues` 增 `version / reopen_count / last_seen_at / decided_by / decided_at` 与 `document_id` 索引，新表 `review_issue_events`（迁移 0036，追加式）。review 与导出 gate 都经 `ReviewRepository.sync_issues`：`created_at` 不再重置；人工 WONTFIX/RESOLVED 不被检测器翻回（只记 `seen_while_closed`）；系统解决后复现 → `reopened`、清备注、重新计划 action；系统 TRIAGED 复现 → 回 OPEN（重跑验证失败），人工 TRIAGED 保持；action 状态不再每轮刷回 PLANNED，COMPLETED/RUNNING action 与已关闭 issue 的 action 拒绝执行（`ActionNotExecutable`）；导出 gate 的阻断判断把 TRIAGED 也算活跃；review 不再解决导出层 issue，action 跟进时改由导出 gate 复核。新增 issue API：`GET /documents/{id}/issues`、`GET /issues/{id}`、`POST /issues/{id}/triage|wontfix|resolve|reopen` |
| 2026-09-17 | R16、R17；06 B-01/B-03/B-06/B-08/B-13/B-14/B-15/B-22 | `36a1d25`、`5b1eb10` | 导出原子写（临时文件 + `os.replace`）与按产物文件锁；`exports.version` + `export_versions`（迁移 0038，保留最近 10 个版本，blob 回收计入引用）与历史接口；门禁每次导出只跑一次；审校包不再持久化阻断 ALIGNMENT_FAILURE；渲染器缺失/源文件缺失等改抛 `ExportUnavailableError`；布局 issue 的 `block_id` 只引用真实块 |
| 2026-09-17 | R1 | 见 H4 提交 | API key 鉴权与角色、org 隔离（documents.org_id，路径与请求体资源校验，跨 org 404）、prod scope 强制鉴权、bootstrap 源路径白名单、provider base_url SSRF 检查。`GET /runs/{id}/cost` 的物化视图问题已在 R15 处理。按 org 的凭据与预算、OIDC 未做 |

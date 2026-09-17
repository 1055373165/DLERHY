# book-agent 文档索引

安装、启动与 API 速览见仓库根目录 [`README.md`](../README.md)。本目录是设计与实现认知文档，全部基于 2026-09-16 对分支 `refactor/p0-stabilize`（HEAD `2176d51`）的逐文件源码阅读，是后续「生产级优化」与「agent 升级」的依据。

## 架构与实现（现状）

| 文档 | 内容 |
|---|---|
| [architecture/00-overview.md](architecture/00-overview.md) | 系统定位、端到端数据流、模块地图、关键机制、README 承诺 vs 事实、跨子系统结构性问题 |
| [architecture/01-app-api.md](architecture/01-app-api.md) | FastAPI 组装、lifespan、会话/事务、全部端点、Settings 全表、CLI、凭据与密钥、部署脚本、错误处理、35 条问题 |
| [architecture/02-runtime-orchestration.md](architecture/02-runtime-orchestration.md) | run 状态机、执行器线程模型、工作项与租约、阶段推导、门禁、事务、预算、失败分类、事件/审计/成本、35 条问题 |
| [architecture/03-ingestion-parsing.md](architecture/03-ingestion-parsing.md) | 上传→句子的数据流、IR 与锚点方案、PDF 解析 22 个 recovery pass、EPUB 解析、分段、modality、OCR、refresh、性能、60+ 条问题 |
| [architecture/04-translation-core.md](architecture/04-translation-core.md) | packet、上下文编译、prompt 与 13 个 profile、provider、三段事务、记忆、术语、启发式包、成本、34 条问题 |
| [architecture/05-review-repair.md](architecture/05-review-repair.md) | 16 项审校检查、规则引擎映射、三个自动修复循环、worklist、analytics、门面与读模型重复、38 条问题 |
| [architecture/06-export.md](architecture/06-export.md) | 7 种导出、渲染管线与 16 类修补、门禁、文件与 CAS、下载契约、脚本链路对照、40 条问题 |
| [architecture/07-data-model.md](architecture/07-data-model.md) | 32 张表逐表定义与 ER 图、JSON 列 payload、ID/时间/删除、并发原语、34 个迁移、物化视图、存储布局、索引缺口、30 条问题 |
| [architecture/08-frontend.md](architecture/08-frontend.md) | 路由与用户流程、WorkspaceContext、手写类型 vs 生成类型、状态推导重复、轮询、错误处理、样式与 a11y、构建与部署、30 条问题 |
| [architecture/09-testing-tooling.md](architecture/09-testing-tooling.md) | 107 个测试文件矩阵、测试基础设施痛点、36 个无测试模块、39 个脚本分类、工具链与仓库卫生 |

## 生产化

| 文档 | 内容 |
|---|---|
| [production/gaps-and-risks.md](production/gaps-and-risks.md) | 合并去重后的风险登记簿（S/A/B/C 分级）与处理顺序 |

## Agent 升级（model + harness）

| 文档 | 内容 |
|---|---|
| [agent-upgrade/00-vision.md](agent-upgrade/00-vision.md) | 为什么现在不是 agent、目标形态、创新落点、不做什么、成功标准 |
| [agent-upgrade/01-harness-landscape.md](agent-upgrade/01-harness-landscape.md) | Codex / Claude Code / Agents SDK / 开源 harness 的设计要点与对 book-agent 的含义（附来源） |
| [agent-upgrade/02-target-architecture.md](agent-upgrade/02-target-architecture.md) | harness kernel、工具与权限、hooks/skills、上下文与记忆、六个 agent 的边界、数据模型变更、人在环、传感器与 evals |
| [agent-upgrade/03-roadmap.md](agent-upgrade/03-roadmap.md) | H0–H4 分阶段路线与验收标准 |

## 重构记录（历史）

| 文档 | 内容 |
|---|---|
| [refactor/PLAN.md](refactor/PLAN.md) | 2026-09-14 起的 P0–P4 重构计划与完成状态 |
| [refactor/baseline-tests.md](refactor/baseline-tests.md) | 测试基线（数字已过期，见 09 篇 §1.1） |
- [agent-upgrade/06-mcp-server.md](agent-upgrade/06-mcp-server.md) — MCP server: connecting Claude Code / Codex, tool list, safety boundary

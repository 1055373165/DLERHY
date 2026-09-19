# 01 · 业界 harness 现状与可借鉴之处（2026-09）

> 目的：用当前开源/公开的 agent harness 设计，校准 book-agent 升级方案里每个组件的取舍。资料来自 2026 年 9 月的公开文章与仓库，见文末来源；凡是与 book-agent 直接相关的判断都在「对 book-agent 的含义」一栏。

## 1. 「Agent = Model + Harness」的共识定义

2026 年业界把 agent 拆成两半：**model** 提供推理，**harness** 是围绕模型的运行时——执行循环、工具调用、上下文投递与压缩、沙箱与权限、审批、会话与记忆、可观测性。一个常被引用的表述是：模型自己能推理，但不能行动、执行或跨轮记忆，这些全由 harness 提供。另一个关键观察：前沿模型能力趋同后，**harness 决定了大部分结果差异**（引用的数据包括仅靠减少工具数把成功率从 80% 提到 100%，以及同一模型换 scaffold 在基准上 42% 到 78% 的波动）。

Martin Fowler 站点的《Harness engineering for coding agent users》给出一个对 book-agent 特别有用的分类：harness 由 **guides（前馈：文档、约定、工具，让模型第一次就做对）** 和 **sensors（反馈：linter、测试、AI 评审，让模型在人介入前自我纠正）** 组成；每种又分 **computational（确定、快、廉价）** 与 **inferential（语义、慢、概率）**。原则是：快检查放在提交前，贵的传感器放在流水线里；先把代码库变得「可 harness」（强类型、清晰边界）；由人根据反复出现的失败模式迭代 harness，而不是追求全自动。

`awesome-harness-engineering` 的分类学把 harness 的设计原语列为：Agent Loop、Planning & Task Decomposition、Context Delivery & Compaction、Tool Design、Skills & MCP、Permissions & Authorization、Memory & State、Task Runners & Orchestration、Verification & CI Integration、Observability & Tracing、Human-in-the-Loop，外加 Security & Sandboxing 与 Evals。一句被反复引用的话：**每个 harness 组件存在都是因为模型自己做不到，好的 harness 在设计时就假定这些组件会随模型进步而变得不必要**。

## 2. 具体 harness 的设计要点

### 2.1 OpenAI Codex（CLI / Cloud / IDE 共用一个核心）

- **循环模型**：一个 turn = 一次用户输入到 agent 回复；turn 内模型与工具反复迭代，直到模型产出 assistant message 而不是 tool call。每条消息是带 `type/role/content` 的 item；tool 结果作为新 item **追加**到 prompt 尾部再次采样。
- **无状态请求**：故意不用 `previous_response_id`，每个请求自带完整历史，以满足零数据保留（ZDR）；代价由 **prompt cache** 抵消：prompt 顺序固定为 system → tools → instructions → input，只追加不修改，保证前缀精确命中，让采样成本从二次方降为线性。配置变更（沙箱、审批模式、工作目录）也通过追加新的 developer/user 消息表达，而不是改早先的消息。
- **上下文压缩**：超过阈值时调用 `/responses/compact`，得到一个不透明的 compaction item 保留模型的潜在理解。
- **沙箱与审批**：只有 Codex 自带工具在沙箱内；MCP 工具须自守规则。沙箱决定命令能触达什么，审批策略决定何时停下来问人。
- **AGENTS.md**：仓库级常驻指令，放在缓存前缀里；会话中改动它会让最大缓存前缀失效。
- 2026 年迁移到 Responses API 后缓存利用率与基准成绩提升。

**对 book-agent 的含义**：翻译 packet 的 prompt 应改为「静态契约 + 书级 BOOK.md + 章节静态段」在前、「packet 动态段」在后，并做真正的 prompt cache（现在 `system_prompt_static/dynamic` 拆分存在但未发送，chat 模式每次还把 1.5k token 的 schema 塞进 user prompt）。agent turn 需要 DB 持久化的 item 流，而不是内存对象。

### 2.2 Claude Code / Claude Agent SDK

- 三层扩展原语，各有正确的层次：**Skills**（按需加载的领域专长目录，`SKILL.md` + 脚本/资产）、**Hooks**（在生命周期固定点运行的确定性 shell 命令，可拦截工具调用、改写 prompt、记事件；因为是代码而不是模型判断，是最可靠的护栏）、**Subagents**（拥有独立上下文窗口的分叉会话，用 Task/Agent 工具启动）。
- 常见错误是「用对了工具放错了层」：该是 hook 的行为约束写进 system prompt，该是 skill 的可复用流程复制进每次对话，该是 subagent 的任务塞进主会话。
- harness 被描述为「操作系统」：策划上下文、设置 prompt 与 hooks 完成启动、通过工具描述与 skills 提供「驱动」。

**对 book-agent 的含义**：现有的 heuristics pack（16 条针对单本书的规则）、prompt profile（13 个）、书籍类型关键词表，都应变成 **skills**（按书/体裁选择的目录包），而不是硬编码；术语强制、成本护栏、协议校验应变成 **hooks**（确定性，pre-persist 拦截），而不是 prompt 里的一句话；结构解析歧义、审校、修复应是 **subagents**（独立上下文），而不是主翻译 prompt 的附加段落。

### 2.3 OpenAI Agents SDK

- **Handoffs**：一个 agent 把所有权移交给另一个 agent（如抽取 → 匹配 → 审批）。
- **Guardrails**：输入/输出校验与安全检查与 agent 执行并行，任一失败即快速失败。
- **Sessions**：持久化的工作上下文层，跨调用自动存取历史。
- **Tracing** 默认开启：记录 LLM 生成、工具调用、handoff、guardrail 与自定义事件。

**对 book-agent 的含义**：翻译 → 审校 → 修复 → 导出 QA 天然是 handoff 链；guardrails 对应现有的 `OutputValidator`（但要改成能拒绝）与术语 hook；tracing 对应 `events` 表，但需要覆盖每次 LLM 调用与每个工具调用（现在 27 种事件只发 8 种）。

### 2.4 开源 harness 生态（OpenCode、DeepSeek Harness、Goose、OpenHands、Pi、deepagents、Pydantic AI Harness、TrueForge、Zed Agent）

- 共同点：都是「完整循环」而非聊天包装——tool loop、会话管理、模型无关、计划/执行两种模式、client-server（`opencode serve` 提供无头 OpenAPI 服务）。
- DeepSeek Harness：**一切皆插件，包括 agent loop 本身**；子代理是一等插件；能把 Claude Code / Codex 作为子进程驱动。
- OpenHands：事件流架构，每会话一个 Docker 沙箱，持久会话存储。
- deepagents（LangGraph）：内置规划工具、虚拟文件系统、子代理委派、持久记忆。
- Pydantic AI Harness：以类型系统做权限与校验，「五十个可组合能力」（文件系统、shell、记忆、规划）。
- 互操作标准正在收敛：MCP（工具）、AGENTS.md（指令）、Agent Client Protocol（Zed、OpenHands、DeepSeek Harness 互相驱动）。
- 选型建议：不要比功能矩阵，看「它假定你已经决定了什么」（模型厂商、部署位置、谁来改 agent 行为——Markdown/CLI 还是类型化代码）；类别仍不稳定，「为未来 18 个月选，不为 5 年选」。

**对 book-agent 的含义**：book-agent 的 harness 应是**自有的、领域专用的**（翻译工作流 + PostgreSQL 账本是核心资产），而不是套一个通用 coding harness；但应把工具面通过 **MCP server** 暴露出去，让 Claude Code / Codex 这类通用 harness 也能驱动它（做批量运维、调参、写 skill）。agent loop 也应做成可替换的插件（便于换模型厂商、换 Responses/Chat API）。

## 3. 从对比中提炼给 book-agent 的设计准则

1. **确定性优先，模型填空**：book-agent 现有的账本、租约、门禁、规则引擎是 computational sensors/guides，是优势；升级不是用模型替代它们，而是让模型在这些护栏内做现在由脆弱启发式做的判断（结构歧义、审校、修复选择、术语决策、导出布局）。
2. **每个模型调用都走同一个 harness kernel**：统一 client、统一 item 持久化、统一计费与预算、统一 trace。现状是四套并行的调用路径。
3. **Prompt 是缓存友好的分层前缀**：静态契约 → 书级 BOOK.md → 章级静态 → packet 动态；只追加。
4. **护栏放对层**：拦截性约束 = hook（代码）；领域专长 = skill（目录）；独立任务 = subagent（独立上下文 + 独立预算）。
5. **人在环的地方要有耐久的审批对象**：审批不是 UI 状态，是事件表里的一行，且 harness 在该点确定性地停下来（对应 Codex 的 approval policy）。
6. **可评估**：每个 agent 有离线 eval 集与判分器；对外报告结果时披露 harness 配置（业界已在呼吁「不披露 harness 就别比较模型」）。
7. **为组件的消失做准备**：把「模型做不到所以 harness 补」的部分（如 22 个 PDF recovery pass 的大部分）设计成可按模型能力逐步下线的插件，而不是永久的核心。

## 来源

- [Unrolling the Codex agent loop（OpenAI）](https://openai.com/index/unrolling-the-codex-agent-loop/)（页面需登录访问，本文引用其 ZenML 转述）
- [Building Production-Ready AI Agents: OpenAI Codex CLI Architecture and Agent Loop Design（ZenML）](https://www.zenml.io/llmops-database/building-production-ready-ai-agents-openai-codex-cli-architecture-and-agent-loop-design)
- [Codex CLI Guide 2026: Setup, Sandbox, AGENTS.md & MCP](https://blakecrosley.com/guides/codex)
- [Everything About Codex (2026)](https://bhavishyapandit9.substack.com/p/everything-about-codex-the-complete)
- [Harness engineering for coding agent users（martinfowler.com）](https://martinfowler.com/articles/harness-engineering.html)
- [awesome-harness-engineering（GitHub）](https://github.com/ai-boost/awesome-harness-engineering)
- [Harness Engineering for AI Agents: 2026 Playbook（Lyzr）](https://www.lyzr.ai/blog/harness-engineering-for-ai-agents/)
- [Agent Harness Engineering — The Rise of the AI Control Plane](https://medium.com/@adnanmasood/agent-harness-engineering-the-rise-of-the-ai-control-plane-938ead884b1d)
- [Claude Code: Skills, Subagents, Hooks, Plugins, and Harnesses](https://boringbot.substack.com/p/claude-code-skills-subagents-hooks)
- [Claude Code Hooks vs Skills vs Subagents（DEV）](https://dev.to/kenimo49/claude-code-hooks-vs-skills-vs-subagents-three-ways-to-extend-the-agent-and-when-each-backfires-1728)
- [Claude Agent SDK: Build Your Own Agent Harness（HatchWorks）](https://hatchworks.com/blog/claude/agent-sdk/)
- [OpenAI Agents SDK 文档](https://openai.github.io/openai-agents-python/)、[Handoffs](https://openai.github.io/openai-agents-python/handoffs/)、[Tracing](https://openai.github.io/openai-agents-python/tracing/)
- [A Comparison of AI Agent Harnesses in 2026（Winder.AI）](https://winder.ai/ai-agent-harness-comparison/)
- [Best Open Source Agent Harness: Top 5 Projects Compared for 2026（TrueFoundry）](https://www.truefoundry.com/blog/best-open-source-agent-harness)
- [Best Open Source CLI Coding Agents in 2026（Pinggy）](https://pinggy.io/blog/best_open_source_cli_coding_agents/)
- [Stop Comparing LLM Agents Without Disclosing the Harness（arXiv）](https://arxiv.org/pdf/2605.23950)
- [Agentic Harness Engineering: Observability-Driven Automatic Evolution of Coding-Agent Harnesses（arXiv）](https://arxiv.org/pdf/2604.25850)

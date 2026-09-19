# 06 · MCP server：让 Claude Code / Codex 直接操作 book-agent

`book-agent mcp` 在 stdio 上提供 MCP 服务（JSON-RPC 2.0，按行分隔；实现 `initialize`、`ping`、`tools/list`、`tools/call`），复用产品内 agent 使用的同一套类型化工具，直接读写本机配置的数据库（`BOOK_AGENT_DATABASE_URL`，或 `--database-url`）。

## 接入

```bash
# Claude Code
claude mcp add book-agent -- uv run --directory /path/to/book-agent book-agent mcp

# 任意 MCP 客户端（stdio）
uv run --directory /path/to/book-agent book-agent --database-url postgresql+psycopg://... mcp
```

## 工具

| 工具 | 类型 | 说明 |
|---|---|---|
| `list_documents` | 只读 | 书库列表，可按标题/作者过滤 |
| `document_summary` | 只读 | 一本书的状态、计数、最近 run |
| `book_guide` | 只读 | BOOK.md（术语表与翻译决策） |
| `search_book` / `read_block` / `read_chapter_outline` / `get_glossary` | 只读 | 与术语代理、审校代理相同的书籍工具 |
| `list_issues` / `get_issue` | 只读 | 问题清单与详情（源/译对照、历史、计划动作） |
| `run_summary` | 只读 | run 状态与阶段进度 |
| `propose_term` | 可逆写 | 记录首选译法（翻译提示会遵循） |
| `record_decision` | 可逆写 | 写入书级决策（后写覆盖先写） |
| `triage_issue` | 可逆写 | 将问题标为已分诊并记录备注 |

**不暴露**：锁定术语、标记不修复、执行修复动作、启动 run 等不可逆或会产生模型费用的操作。它们需要产品内审批或 API 权限。

## 安全边界

MCP 服务直接连数据库，不经过 API 鉴权与 org 隔离，等同于本机管理员权限。只在能访问数据库的运维机器上运行；多租户部署中不要把它暴露给租户。

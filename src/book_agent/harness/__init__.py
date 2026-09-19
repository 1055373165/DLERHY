"""The agent harness: durable turns, typed tools, permissions, hooks, approvals.

See docs/agent-upgrade/02-target-architecture.md. The kernel turns a model
into an agent that acts only through registered tools, under a budget, with
every step written to the ledger so a turn survives crashes and pauses.
"""

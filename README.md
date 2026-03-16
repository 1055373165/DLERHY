# book-agent

Long-document translation agent for translating English books into high-quality Chinese with sentence-level coverage, traceability, packet-based context management, QA, and rerun control.

## Current Scope

- P1-A: EPUB + low-risk text PDF
- Priority genres: nonfiction, technical, business
- Deliverable target: reviewable high-quality Chinese draft

## Source of Truth

- [System Design](/Users/smy/project/book-agent/docs/translation-agent-system-design.md)
- [Error Taxonomy](/Users/smy/project/book-agent/docs/error-taxonomy.md)
- [Rerun Policy](/Users/smy/project/book-agent/docs/rerun-policy.md)
- [Issue to Rerun Matrix](/Users/smy/project/book-agent/docs/issue-rerun-matrix.md)
- [P0 Database DDL](/Users/smy/project/book-agent/docs/p0-database-ddl.sql)
- [Orchestrator State Machine](/Users/smy/project/book-agent/docs/orchestrator-state-machine.md)
- [Merged Export Rendering Policy](/Users/smy/project/book-agent/docs/merged-export-rendering-policy.md)
- [Status](/Users/smy/project/book-agent/docs/status.md)

## Planned Stack

- Python
- FastAPI
- SQLAlchemy
- Alembic
- PostgreSQL

## Current Runtime Notes

- The runtime now exposes a modern operator-facing frontend entry at `/`, so the project no longer ships as API-only.
- The homepage is intentionally served by FastAPI itself rather than a separate SPA, which keeps the product surface tightly aligned with the existing workflow, run-control, review, and export contracts.
- The homepage now also includes a lightweight control workspace with document bootstrap/load actions, a run console, and a chapter worklist board backed by the existing `/v1` APIs.
- The same control workspace now supports chapter drill-down, owner assignment operations, and run-event auto-refresh, so the UI can be used as a real ops console rather than a static overview.
- Default translation backend is `echo`, which preserves the full persistence and QA path without calling an external model.
- The runtime now exposes the same workflow through FastAPI and a local CLI.
- A provider-specific `openai_compatible` translation client is now wired behind the worker factory.
- Provider contract tests use a fake transport, so the adapter path can be validated without external keys or network.
- Docker and Docker Compose are now the recommended local path for PostgreSQL-backed integration.
- The Dockerized PostgreSQL main workflow smoke has been validated end-to-end for `bootstrap -> translate -> review -> export`.
- SQLite development and test runs now enable foreign key enforcement to stay closer to PostgreSQL behavior.
- Follow-up action execution now supports targeted rebuild for packet / termbase refresh before rerun.
- Action follow-up responses now include structured rebuild evidence, including rebuilt snapshot types and current context versions.
- Review package exports now include quality summary, version evidence, and recent repair events so exported artifacts remain self-describing.
- Bilingual HTML exports now emit a sidecar manifest with quality summary, version evidence, repair evidence, and row-level export counts.
- Export artifacts now also emit `export_time_misalignment_evidence`, so post-review alignment corruption can still be surfaced during export.
- Final bilingual export now treats export-time misalignment as a gate failure, while review package export remains available as the diagnostic path.
- Export-time misalignment is now persisted as formal `ReviewIssue` / `IssueAction` records, so blocked final exports can flow back into the existing repair pipeline.
- API-side 409 export failures now return structured follow-up hints, including persisted `issue_ids`, `action_ids`, and executable action scope metadata.
- Final export now also supports an opt-in auto-repair path: if export is blocked by export-origin follow-up actions, callers can ask the workflow to execute those actions with follow-up rerun/review and retry the export in the same request.
- Export auto-repair now exposes attempt telemetry and a hard safety cap, so callers can see how many follow-up actions were executed and why the workflow stopped.
- Export auto-followup telemetry is now also persisted as chapter-level audit events and surfaced in review packages / bilingual manifests via `export_auto_followup_evidence`.
- Export records now persist an `export_auto_followup_summary` inside `input_version_bundle_json`, so future dashboards or admin APIs can read the latest counts without replaying audit events.
- `GET /v1/documents/{document_id}/exports` now supports `export_type`, `status`, `limit`, and `offset`, while keeping document-level dashboard counts stable across filtered views.
- Export records now also persist `translation_usage_summary` and `issue_status_summary`, so export detail reads can stay snapshot-oriented instead of recomputing chapter state on the fly.
- Translation usage is now also exposed as a per-model / per-worker breakdown, so dashboards can distinguish mixed rerun paths instead of only showing document-level totals.
- Export dashboards now also expose a daily `translation_usage_timeline`, so usage can be read as totals, breakdowns, and time buckets from the same contract.
- Export dashboards now also expose `translation_usage_highlights`, giving direct top-cost / top-latency / top-volume entries without requiring callers to post-process the breakdown.
- Export dashboards now also expose `issue_hotspots`, grouped by `issue_type + root_cause_layer`, so ops surfaces can see where problems cluster without replaying all review issues.
- Export dashboards now also expose `issue_chapter_pressure`, so operators can see which chapters currently carry the most open or blocking review pressure.
- Export dashboards now also expose `issue_chapter_highlights`, giving direct top-open / top-blocking / top-resolved chapter cards without requiring callers to post-process chapter pressure.
- Export dashboards now also expose `issue_chapter_breakdown`, drilling chapter pressure down to `chapter + issue_type + root_cause_layer`.
- Export dashboards now also expose `issue_chapter_heatmap`, surfacing chapter heat based on active open/triaged/blocking pressure plus the dominant issue family.
- Export dashboards now also expose `issue_chapter_queue`, turning chapter heat into an actionable queue with rank, priority, queue driver, lightweight regression/flapping hints, and worklist fields such as `oldest_active_issue_at`, `age_hours`, `sla_target_hours`, `sla_status`, and `owner_ready`.
- A dedicated chapter worklist API is now available, exposing the same queue contract through a lighter ops-oriented surface with filtering, pagination, and worklist summary counts.
- The chapter worklist API now also exposes `highlights`, including `top_breached_entry`, `top_due_soon_entry`, `top_oldest_entry`, and `top_immediate_entry`.
- A dedicated chapter worklist detail API is now available at `GET /v1/documents/{document_id}/chapters/{chapter_id}/worklist`, exposing the chapter queue entry, live issue pressure, recent issues/actions, and the persisted chapter quality summary.
- Chapter worklist owner assignment is now persisted per chapter, exposed on queue/detail responses, and manageable through dedicated assign/clear APIs.
- Chapter worklist responses now also expose `owner_workload_summary`, aggregating actionable assigned chapters by owner with queue/SLA pressure.
- Chapter worklist responses now also expose `owner_workload_highlights`, giving direct top-loaded / top-breached / top-blocking / top-immediate owner cards.
- Chapter worklist detail now also exposes `assignment_history`, derived from persisted chapter assignment audit events.
- A dedicated run-control API is now available at `/v1/runs`, exposing durable run creation, budget snapshots, run summaries, ordered run audit events, and validated run-level state transitions.
- Export dashboards now also expose `issue_activity_timeline`, so operators can see whether issue load is accumulating or being resolved over time.
- Export dashboards now also expose `issue_activity_breakdown`, so operators can drill daily issue flow down to `issue_type + root_cause_layer`.
- Export dashboards now also expose `issue_activity_highlights`, giving direct top-regressing / top-resolving / top-blocking issue families.

## Translation Backends

### Echo backend

Default deterministic backend for pipeline validation:

```bash
BOOK_AGENT_TRANSLATION_BACKEND=echo
BOOK_AGENT_TRANSLATION_MODEL=echo-worker
```

### OpenAI-compatible backend

Provider-backed worker with structured JSON transport:

```bash
BOOK_AGENT_TRANSLATION_BACKEND=openai_compatible
BOOK_AGENT_TRANSLATION_MODEL=gpt-5-mini
BOOK_AGENT_TRANSLATION_OPENAI_API_KEY=<your_api_key>
BOOK_AGENT_TRANSLATION_OPENAI_BASE_URL=https://api.openai.com/v1/responses
BOOK_AGENT_TRANSLATION_TIMEOUT_SECONDS=60
BOOK_AGENT_TRANSLATION_MAX_RETRIES=2
BOOK_AGENT_TRANSLATION_RETRY_BACKOFF_SECONDS=2.0
```

Notes:

- Standard aliases `OPENAI_API_KEY` and `OPENAI_BASE_URL` are also accepted, so local shells and existing OpenAI tooling can be reused without renaming variables.
- The provider adapter is wired through the same `LLMTranslationWorker` contract used by the rest of the pipeline.
- When `base_url` points at a provider root such as `https://api.deepseek.com`, the client now automatically uses `chat/completions`; the default OpenAI path still uses `/v1/responses`.
- Default test coverage validates payload construction and structured response parsing without performing a live API call.
- Live provider smoke is intentionally separate from the default local test suite.
- Provider-backed prompts now expose sentence aliases like `S1 / S2` to the model and map them back to real sentence IDs before persistence, reducing the risk of long UUID corruption in live runs.
- Real-book live reruns should use `scripts/run_real_book_live.py`, which now runs through the durable Run Control Plane and writes an incremental run-level JSON progress report.
- The same provider path now also persists real `token_in / token_out / latency_ms` and estimate-ready `cost_usd` on `translation_runs` when pricing config is provided.
- The real-book runner now supports `--parallel-workers` for higher-throughput packet execution while preserving per-packet commit and resume safety.
- The same runner now supports `--run-id` resume, per-work-item lease heartbeat, expiry reclaim, budget guardrails, and graceful `Ctrl-C -> pause`.
- The current DeepSeek baseline on the real EPUB `Build an AI Agent (From Scratch)` has progressed past the initial sample run into a resumed full-book `v3` rerun; see `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-full-run-v3/report.json`.

## Package Layout

```text
src/book_agent/
  app/
  core/
  domain/
  infra/
  orchestrator/
  schemas/
  workers/
```

## Next Milestones

1. Project bootstrap and schema layer
2. EPUB ingest / parse / segmentation
3. Packet builder and memory snapshots
4. Translation worker and alignment
5. QA, rerun, export

## CLI

Examples:

```bash
book-agent --database-url sqlite+pysqlite:///./local.db bootstrap --source-path ./sample.epub
book-agent --database-url sqlite+pysqlite:///./local.db summary --document-id <document_id>
book-agent --database-url sqlite+pysqlite:///./local.db translate --document-id <document_id>
book-agent --database-url sqlite+pysqlite:///./local.db review --document-id <document_id>
book-agent --database-url sqlite+pysqlite:///./local.db export --document-id <document_id> --export-type bilingual_html
book-agent --database-url sqlite+pysqlite:///./local.db export --document-id <document_id> --export-type bilingual_html --auto-followup-on-gate
book-agent --database-url sqlite+pysqlite:///./local.db export --document-id <document_id> --export-type bilingual_html --auto-followup-on-gate --max-auto-followup-attempts 1
book-agent --database-url sqlite+pysqlite:///./local.db execute-action --action-id <action_id> --run-followup
```

API notes:

- `GET /` serves the operator-facing frontend homepage and links directly into the live API surfaces.
- The same homepage now acts as a lightweight operator console for document, run, and chapter queue operations; it is still intentionally thinner than a full review workstation.
- The operator homepage now also exposes owner-aware queue filters plus explicit document/run/worklist live refresh state, so operators can see whether the surface is fresh, stale, or actively syncing without leaving the page.
- The same operator homepage now also exposes owner workload drill-down and lightweight balancing hints, turning the owner lane from a static summary into a directly usable triage surface.
- The owner surface now also emits clickable routing alerts for breached owner load, unassigned immediate queue pressure, and simple rebalance candidates, again without introducing a separate reporting API.
- `POST /v1/documents/{document_id}/export` accepts `{"auto_execute_followup_on_gate": true, "max_auto_followup_attempts": 3}` for opt-in export-time repair.
- Successful auto-repaired exports return `auto_followup_requested`, `auto_followup_attempt_count`, `auto_followup_attempt_limit`, and `auto_followup_executions`.
- When auto-repair stops at the safety cap, export gate failures still return HTTP `409`, but now include `auto_followup_attempt_count`, `auto_followup_attempt_limit`, `auto_followup_stop_reason`, and any already executed auto-followup summaries.
- `GET /v1/documents/{document_id}/exports` returns export history plus a lightweight dashboard summary, including per-type counts and each export record's `export_auto_followup_summary`.
- The same endpoint also supports `export_type`, `status`, `limit`, and `offset`; `export_count` stays document-global, while `filtered_export_count`, `record_count`, and `has_more` describe the current records window.
- `GET /v1/documents/{document_id}/exports` now also returns document-level `translation_usage_summary` and per-record translation usage snapshots.
- The same dashboard now includes `translation_usage_breakdown`, grouped by `model_name + worker_name (+ provider when available)`.
- The same dashboard also includes a daily `translation_usage_timeline` for lightweight trend inspection.
- The same dashboard also includes `translation_usage_highlights` for quick operational reading.
- Per-record export snapshots now also include `translation_usage_timeline` and `translation_usage_highlights`, so export detail can reconstruct the full usage view from the export-time bundle rather than only the current live dashboard.
- The same dashboard also includes `issue_hotspots`, summarizing issue concentration by type and root-cause layer.
- The same dashboard also includes `issue_chapter_pressure`, summarizing issue concentration by chapter.
- The same dashboard also includes `issue_chapter_highlights` for quick chapter-ops reading.
- The same dashboard also includes `issue_chapter_breakdown` for chapter-level issue family drill-down.
- The same dashboard also includes `issue_chapter_heatmap` for current chapter heat and dominant issue family reading.
- The same dashboard also includes `issue_chapter_queue` for actionable chapter triage ordering, including `regression_hint`, `flapping_hint`, `oldest_active_issue_at`, `age_hours`, `age_bucket`, `sla_target_hours`, `sla_status`, `owner_ready`, and `owner_ready_reason`.
- `GET /v1/documents/{document_id}/chapters/worklist` returns the same chapter worklist contract through a dedicated API, with `queue_priority / sla_status / owner_ready / needs_immediate_attention / assigned / assigned_owner_name / limit / offset` filters plus summary counts.
- The same worklist API also returns `owner_workload_summary`, grouped by current owner and derived from the full actionable queue rather than the filtered entries window.
- The same worklist API also returns `owner_workload_highlights`, derived from the full owner workload summary rather than the filtered entries window.
- The same worklist API also returns `highlights`, so ops surfaces can read the most urgent SLA-breached / due-soon / oldest / immediate chapters without rescanning the full queue.
- `GET /v1/documents/{document_id}/chapters/{chapter_id}/worklist` returns a single-chapter worklist detail view, including `queue_entry`, current issue counts, issue family breakdown, recent issues/actions, persisted `quality_summary`, and the current assignment.
- The same chapter worklist detail API also returns `assignment_history`, ordered newest-first from `chapter.worklist.assignment.set/cleared` audit events.
- `PUT /v1/documents/{document_id}/chapters/{chapter_id}/worklist/assignment` persists or updates a chapter owner assignment.
- `POST /v1/documents/{document_id}/chapters/{chapter_id}/worklist/assignment/clear` clears the current chapter owner assignment and returns the cleared assignment id.
- The same dashboard also includes `issue_activity_timeline`, summarizing daily created/resolved issue flow.
- The same dashboard also includes `issue_activity_breakdown`, summarizing daily issue flow per issue family.
- The same dashboard also includes `issue_activity_highlights` for quick issue-ops reading.
- `GET /v1/documents/{document_id}/exports/{export_id}` returns export detail, including persisted translation usage, issue status summary, misalignment counts, and version evidence.
- The same export detail now includes persisted `translation_usage_timeline` and `translation_usage_highlights` from the export-time snapshot.
- `POST /v1/runs` creates a durable run-control record with optional budget limits.
- `GET /v1/runs/{run_id}` returns the current run summary, including budget, work-item counts, worker-lease counts, latest heartbeat, and event totals.
- `GET /v1/runs/{run_id}/events` returns newest-first run audit events with pagination metadata.
- `POST /v1/runs/{run_id}/pause|resume|drain|cancel` applies validated run-level state transitions and appends ordered run audit events with operator metadata.
- Real-book translate runs now execute through the same run-control objects, rather than a standalone batch loop.

## Docker

Start PostgreSQL and the API:

```bash
docker compose up -d postgres
docker compose run --rm app alembic upgrade head
docker compose up -d app
```

The compose app container is pinned to the local PostgreSQL service via
`BOOK_AGENT_DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/book_agent`,
so migrations and API smoke tests run against the same database backend by default.

Check health:

```bash
curl http://localhost:58000/v1/health
```

Host-side PostgreSQL port:

```bash
postgresql+psycopg://postgres:postgres@localhost:55432/book_agent
```

Run CLI commands against containerized PostgreSQL:

```bash
docker compose run --rm --no-deps app book-agent summary --document-id <document_id>
docker compose run --rm --no-deps app book-agent review --document-id <document_id>
```

Run the PostgreSQL integration regression:

```bash
./scripts/run_postgres_integration.sh
```

Run a real-book live rerun with a provider-backed worker:

```bash
.venv/bin/python scripts/run_real_book_live.py \
  --source-path /Users/smy/project/book-agent/books/build-an-ai-agent.epub \
  --database-url sqlite+pysqlite:////Users/smy/project/book-agent/artifacts/real-book-live/deepseek-full-run-v4/full.sqlite \
  --export-root /Users/smy/project/book-agent/artifacts/real-book-live/deepseek-full-run-v4/exports \
  --report-path /Users/smy/project/book-agent/artifacts/real-book-live/deepseek-full-run-v4/report.json \
  --parallel-workers 4 \
  --lease-seconds 180 \
  --heartbeat-interval-seconds 15 \
  --max-wall-clock-seconds 43200 \
  --max-total-cost-usd 5.0 \
  --max-retry-count-per-work-item 3 \
  --max-consecutive-failures 25
```

Resume an existing run:

```bash
.venv/bin/python scripts/run_real_book_live.py \
  --source-path /Users/smy/project/book-agent/books/build-an-ai-agent.epub \
  --database-url sqlite+pysqlite:////Users/smy/project/book-agent/artifacts/real-book-live/deepseek-full-run-v4/full.sqlite \
  --export-root /Users/smy/project/book-agent/artifacts/real-book-live/deepseek-full-run-v4/exports \
  --report-path /Users/smy/project/book-agent/artifacts/real-book-live/deepseek-full-run-v4/report.json \
  --run-id <existing_run_id> \
  --parallel-workers 4
```

Export a single merged reading HTML from an existing real-book database:

```bash
PYTHONPATH=src .venv/bin/python -m book_agent.cli \
  --database-url sqlite+pysqlite:////Users/smy/project/book-agent/artifacts/real-book-live/deepseek-full-run-v4/full.sqlite \
  --export-root /Users/smy/project/book-agent/artifacts/real-book-live/deepseek-full-run-v4/exports \
  export \
  --document-id 003b7864-d84b-50ae-a54c-cc48858ea57e \
  --export-type merged_html
```

Stop services:

```bash
docker compose down
```

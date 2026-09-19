#!/usr/bin/env bash
# Evidence for docs/specs/ui-ux-overhaul/SPEC.md §4. Read-only: queries the dev DB and greps the server log.
# Usage: bash docs/specs/ui-ux-overhaul/experiments.sh   (needs the book-agent-postgres container running)
set -euo pipefail
cd "$(dirname "$0")/../../.."
LOG=artifacts/server.log
q() { docker exec book-agent-postgres psql -U postgres -d book_agent -Atc "$1"; }

echo "== H1: does anyone use the chapter review workbench? (fails if any write > 0)"
echo "memory-proposal decisions via API: $(grep -cE 'POST /v1/documents/[^ ]*/memory-proposals/[^ ]*/(approve|reject)' "$LOG" || true)"
echo "action executions via API:          $(grep -cE 'POST /v1/actions/[^ ]*/execute' "$LOG" || true)"
echo "worklist assignments via API:       $(grep -cE '(PUT|POST) /v1/documents/[^ ]*/worklist/assignment' "$LOG" || true)"
q "select 'memory proposals '||status||': '||count(*) from chapter_memory_proposals group by status"
q "select 'review issues decided by a human: '||count(*) filter (where decided_by like 'human:%')||' of '||count(*) from review_issues"
q "select 'worklist assignments: '||count(*) from chapter_worklist_assignments"

echo "== H2: how much of a run's wall time is spent blocked on a person?"
q "
with r as (select id, created_at, finished_at from document_runs where run_type='translate_full' and status='succeeded'),
paused as (
  select p.run_id, sum(extract(epoch from (coalesce(
    (select min(x.created_at) from run_audit_events x where x.run_id=p.run_id and x.event_type='run.resumed' and x.created_at>p.created_at),
    p.created_at) - p.created_at))) s
  from run_audit_events p where p.event_type='run.paused' group by p.run_id),
appr as (
  select t.run_id, sum(extract(epoch from (a.decided_at - a.created_at))) s
  from approvals a join agent_turns t on t.id=a.turn_id
  where a.decided_by like 'human:%' group by t.run_id)
select left(r.id::text,8)||' wall '||round(extract(epoch from (r.finished_at-r.created_at))/60,1)||' min, paused '||
       round(coalesce(paused.s,0)/60,1)||' min, approval wait (sum over items) '||round(coalesce(appr.s,0)/60,1)||' min'
from r left join paused on paused.run_id=r.id left join appr on appr.run_id=r.id order by r.created_at"
q "select 'human approvals: n='||count(*)||' avg '||round(avg(extract(epoch from (decided_at-created_at))))||'s max '||round(max(extract(epoch from (decided_at-created_at))))||'s' from approvals where decided_by like 'human:%'"

echo "== H3: do people read in the app or download? (fails if downloads > in-app reads)"
echo "in-app reads (package=preview): $(grep -oE 'GET /v1/documents/[^ ]*/exports/download\?[^ ]*package=preview' "$LOG" | wc -l | tr -d ' ')"
echo "downloads (no package=preview): $(grep -oE 'GET /v1/documents/[^ ]*/exports/download\?[^ ]*' "$LOG" | grep -v 'package=preview' | grep -vc 'preview=true' || true)"

echo "== Polling share: requests per endpoint (ids collapsed)"
grep -oE '"(GET|POST|PUT|DELETE) /v1/[^ ?]*' "$LOG" \
  | sed -E 's#[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}#{id}#g' | sort | uniq -c | sort -rn | head -15

echo "== Documents uploaded but never started"
q "select 'never started: '||count(*)||' of '||(select count(*) from documents) from documents d where not exists (select 1 from document_runs r where r.document_id=d.id)"

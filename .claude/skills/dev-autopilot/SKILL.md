---
name: dev-autopilot
description: One-click full-lifecycle development autopilot. Input a requirement, system auto-drives through requirement lock -> architecture -> spike -> task decomposition -> MDU coding -> review -> checkpoint -> progress update. Use when user invokes /dev-autopilot, /dev-autopilot-resume, /dev-autopilot-status, /dev-autopilot-skip, /dev-autopilot-pause, /dev-autopilot-backtrack, /dev-autopilot-change-request.
---

# Dev Autopilot for Claude Code

Full-lifecycle development autopilot adapted for Claude Code's tool chain. User inputs one requirement; system auto-drives the entire development cycle.

## Quick Commands

| Command | Action |
|---------|--------|
| `/dev-autopilot` | Start new project development |
| `/dev-autopilot-resume` | Resume after session interruption |
| `/dev-autopilot-status` | Show progress snapshot |
| `/dev-autopilot-skip` | Skip current MDU (auto-mark downstream blocked) |
| `/dev-autopilot-pause` | Pause execution, save state |
| `/dev-autopilot-backtrack` | Trigger explicit backtrack |
| `/dev-autopilot-change-request` | Submit requirement change |

## State Files

All state persists in project root:
- `PROGRESS.md` — full progress tracking (phases, tasks, MDUs, completion %)
- `DECISIONS.md` — architecture decision records (ADRs)

## Execution Protocol

### Phase 1: Requirement Lock (Steps 1-3)

**Step 1**: Record raw requirement verbatim.

**Step 2**: AI directly expands understanding. Output:
1. Core objective (one sentence)
2. Required capabilities
3. Technical boundaries and constraints
4. Implicit requirements user didn't state
5. Risk points easily overlooked
Mark uncertain items `[TBC]`. Keep under 500 words.

**Step 3**: Interactive requirement lock (USER INTERACTION POINT)
- Restate core requirement in one sentence
- List all `[TBC]` items
- Identify top 3 ambiguities, each with a precise question + 2-3 options
- Max 5 questions total
- After user confirms, output `locked_requirement` — the single source of truth for all subsequent steps

### Phase 2: Architecture & Decision Fixation (Steps 4-5)

**Step 4**: Generate architecture plan using cognitive architecture:
1. Problem decomposition — core technical contradictions, sub-problems, dependencies
2. Adversarial check — mainstream approach, failure conditions, unconventional alternatives
3. Constraint identification — assumptions, hidden assumptions, fallback if assumptions fail
4. Synthesis — layered architecture, module responsibilities, tech choices, interfaces, data models

Use `Agent` subagents for deep codebase exploration if needed. (USER INTERACTION POINT — wait for confirmation)

**Step 5**: Record ADRs to `DECISIONS.md`. Each ADR:
```
## ADR-NNN: [title]
- Status: decided / verified (spike passed) / overturned (reason:)
- Date:
- Background:
- Candidates:
- Decision:
- Trade-offs:
- Overturn conditions:
- Impact scope:
- Spike needed: yes/no
```

Output: `decisions_file` + `spike_candidates`

### Phase 3: Technical Spike (Step 6)

If `spike_candidates` is empty, skip to Phase 4.

For each spike candidate:
1. Define spike goal, pass criteria, fallback plan
2. Design minimal verification (< 100 lines)
3. Execute verification using `Bash` tool
4. If pass: update ADR status to "verified (spike passed)"
5. If fail: (USER INTERACTION POINT) propose alternative, update ADR

### Phase 4: Task Decomposition (Steps 7-9)

**Step 7**: Decompose into structured task list:
- Level 1: Phases (3-6)
- Level 2: Tasks per phase (2-4)
- Level 3: Subtasks

**Step 8**: Recursive refinement to MDU (Minimum Development Unit).

MDU criteria (ALL must be met):
- [ ] Single function — does one thing
- [ ] Completable in one coding session
- [ ] Clear input/output boundary
- [ ] Independently testable
- [ ] Estimated ≤ 200 lines of code change

Circuit breakers:
- Max recursion depth: 4 levels
- MDU total warning: > 60 → pause
- Single task children limit: 8

**Step 9**: Dependency analysis — annotate each MDU with `depends_on` and `blocks`.

(USER INTERACTION POINT — show total phases/tasks/MDUs, critical path, wait for confirmation)

Write complete plan to `PROGRESS.md`.

### Phase 5: Execution Loop

```
for each phase:
  for each task:
    for each mdu:
      1. Inject Scope Lock
      2. MDU_Execute(mdu)
      3. Backtrack check
      4. Progress heartbeat (every 10% completion or phase switch)
    end
  end
  Phase_Checkpoint(phase)
  Progress_Update(phase)
end
```

#### MDU_Execute(mdu)

**Step A — Context Assembly**:
- Read relevant source files with `Read` tool
- Read related ADRs from `DECISIONS.md`
- Read upstream MDU outputs (if any)

**Step B — Implementation**:
- Write code using `Edit`/`Write` tools
- Run tests using `Bash` tool
- Use `Agent` subagents for parallel independent subtasks within the MDU

**Step C — Code Review (max 3 rounds)**:
Review dimensions:
1. Logic correctness (edge cases, null handling, race conditions)
2. Architecture alignment (SRP, minimal interfaces, coupling)
3. Maintainability (readability, implicit assumptions)
4. Performance (redundant computation, memory leaks)
5. Security (input validation, injection, authz)
6. Project convention consistency (ADR alignment, code style)

Each dimension: pass / suggestion / MUST_FIX

If MUST_FIX after 3 rounds → trigger backtrack protocol.

**Step D — Update**: Mark MDU complete in `PROGRESS.md`.

**Step E — Upstream Issue Detection**: If upstream problems found, trigger backtrack — never use workarounds.

#### Scope Lock (auto-injected per MDU)

```
SCOPE LOCK — MDU-{id}: {description}
Phase: {phase} > Task: {task} > Subtask: {subtask}

STRICT CONSTRAINTS:
1. Only complete work within this MDU's scope
2. Extra work needed? Mark as TODO in PROGRESS.md, do NOT expand
3. Do not modify files outside this MDU's scope (except imports/interfaces)
4. Upstream issues? Mark and trigger backtrack, never workaround
5. Complete → report status → next MDU
6. Do not proactively optimize other modules
```

#### Phase_Checkpoint(phase) (USER INTERACTION POINT)

AI-autonomous checks:
1. Deliverable completeness
2. Static code review (cross-module interface consistency)
3. ADR alignment
4. Tech debt inventory
5. Downstream dependency readiness

User-required checks:
6. Run verification (provide specific commands)
7. Functional verification (list test steps)

All checks pass → proceed. Any blocker → generate fix MDUs.

#### Progress_Update(phase)

Update `PROGRESS.md` with:
- Phase completion status
- Current position
- Completion % (completed MDUs / total MDUs)
- Health assessment
- Forgotten items check
- Next action

### Phase 6: Global Wrap-up

1. Final global review: ADR-code alignment, cross-module consistency, code quality uniformity
2. Final `PROGRESS.md` snapshot (100%)
3. Project completion summary: total phases/tasks/MDUs, key decisions, backtrack count, tech debt

## Backtrack Protocol

Triggers: upstream issue in MDU, review deadlock, user `/dev-autopilot-backtrack`, checkpoint failure.

Flow:
1. Classify root cause → requirement / architecture / tech choice / task decomposition / implementation
2. (USER INTERACTION POINT) Show analysis, wait for confirmation
3. Impact analysis: which MDUs need redo, which ADRs need update
4. Execute backtrack to target step
5. Resume from earliest affected MDU
6. Record in `PROGRESS.md` change log

Rule: 2 consecutive backtracks to same step → pause, require user intervention.

## Change Request Protocol

Trigger: `/dev-autopilot-change-request`

1. Pause current MDU
2. Receive change description
3. Impact assessment: affected MDUs, new MDUs, deleted MDUs, ADR changes
4. (USER INTERACTION POINT) User confirms
5. Update `locked_requirement`, `DECISIONS.md`, `PROGRESS.md`
6. Resume from earliest affected MDU

## Bug-Driven Evolution (HARD CONSTRAINT)

Every bug triggers 3-level drill-down:

**Level 1**: Fix the bug (minimal fix within scope lock).

**Level 2**: Root cause analysis — why didn't the framework catch this?
Categories: a) prompt deficiency, b) process gap, c) mechanism blind spot, d) data model deficiency, e) dependency analysis gap, f) acceptance criteria gap

**Level 3**: Framework evolution — write prevention back into:
- This skill file (add defensive constraint to relevant section)
- Execution code (update relevant module)
- Record in `PROGRESS.md` change log as type=framework-evolution

**This is a hard constraint, not a suggestion. All 3 levels mandatory for every bug.**

## Session Recovery (/dev-autopilot-resume)

1. Read `PROGRESS.md` and `DECISIONS.md`
2. Read current code structure with `Glob`/`Read`
3. Locate current MDU position
4. List 3 most uncertain points for user confirmation
5. Resume from interrupted MDU (restart it from scratch — never continue half-written code)

## Progress Heartbeat

Format: `[Heartbeat] MDU {completed}/{total} | {percent}% | Phase: {phase} | Current: {mdu}`

Triggers: every 10% completion or phase switch.

## PROGRESS.md Structure

```markdown
# Project Progress

## Project Info
- Name:
- One-line goal:
- Created:
- Last updated:
- Protocol: v2-cc

## Global Metrics
- Total phases:
- Total MDUs:
- Completed MDUs:
- Completion: XX%
- Max decomposition depth: N

## Phase Overview
| Phase | Status | MDUs | Done | % |
|-------|--------|------|------|---|

## Detailed Task List
### Phase X: [name]
#### Task X.Y: [name]
- Status: not-started / in-progress / done / skipped (reason:)
- MDUs:
  - [x] MDU-X.Y.1: [desc] [depends: none]
  - [ ] MDU-X.Y.2: [desc] [depends: MDU-X.Y.1]

## Current Position
- Phase:
- Task:
- Current MDU:
- Completion: XX%

## Change Log
| Time | Type | Description | Impact |
|------|------|-------------|--------|
```

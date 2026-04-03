# Forge v2 — Requirement Amplification

This protocol converts a vague user request into an executable specification.

Its purpose is not to "tidy up requirements."
Its purpose is to redefine the problem with enough depth that a top-tier practitioner in the
domain would recognize the specification as serious, precise, and implementable.

Every stage feeds the next stage.
Use narrative paragraphs as the default output form.
Use structure only for naturally structured content such as requirements, constraints, comparison
sets, acceptance criteria, and risk registers.

## Stage 1 — 需求透视

Question: the user said something; what do they actually need to become true?

### 1.1 表面需求提取

Go sentence by sentence through the user's request and extract every explicit requirement without
adding interpretation.

### 1.2 底层动机挖掘

For each explicit requirement, ask three layers of "why":

- why do they want this feature or outcome
- what behavior changes if this problem is solved
- what ultimate value do they gain if it is solved well

Then write 2-3 paragraphs describing the user's true desired end state.
This end state becomes the north star for every later design decision.

### 1.3 隐含需求发掘

List the needs the user did not state but that must still be solved.

Use this test:
if every explicit requirement were met but this hidden need were ignored, would the user still be
disappointed or blocked?

Exit gate:
you can now describe the user's real target state clearly enough that naive interpretations look
obviously incomplete.

## Stage 2 — 约束透镜

Question: what realities make some solutions invalid even if they sound attractive?

Identify the constraints that govern the problem:

- technical constraints
- product and business constraints
- compliance or safety constraints
- latency, cost, scale, or operability constraints
- team, workflow, or migration constraints
- legacy compatibility constraints

Separate them into:

- confirmed facts
- reasonable assumptions
- unknowns that may still change the shape of the solution

Do not bury unknowns inside confident prose.
Name them explicitly and decide whether each one is tolerable, inferable, or blocking.

Exit gate:
every hard constraint is labeled as fact or assumption, and every unresolved unknown is either
contained or escalated.

## Stage 3 — 问题重构

Question: is the user asking for a solution, or are they describing symptoms of a deeper problem?

Reframe the request from surface form into problem structure:

- what capability is actually being sought
- what system boundary the problem belongs to
- which actors or stakeholders matter
- what adjacent domains shape the outcome
- where the real failure modes live

Generate 2-3 plausible problem framings.
Then choose the framing that best explains both the explicit request and the hidden needs.

The winning framing should clarify why a simplistic interpretation would underperform.

Exit gate:
you can explain, in one tight paragraph, why the chosen problem statement is better than the
literal wording of the request.

## Stage 4 — 方案拓扑

Question: what families of solution exist, and why is one topology superior here?

Generate 2-4 serious solution directions, not superficial tool swaps.

Compare them on:

- ability to satisfy the north star
- compatibility with hard constraints
- failure containment
- operability and maintainability
- migration and rollout complexity
- cost of future change

Choose a preferred topology and record why it wins.
Also record why the rejected options lose.

This is where "world-class thinking" shows up: not in stack fashion, but in precise tradeoff
judgment.

Exit gate:
the chosen topology has a specific rationale that would survive expert scrutiny.

## Stage 5 — 可执行规格

Question: what exactly must be true in the final system for us to call this done?

Write the specification in a way that can be built and verified:

- target user or operator flows
- interfaces and interaction contracts
- data and state expectations
- non-functional requirements
- edge cases and failure handling
- observability or supportability requirements
- out-of-scope boundaries
- measurable success criteria

Then convert that spec into an exhaustive acceptance inventory in `.forge/spec/FEATURES.json`.

Each feature item should describe:

- a stable id
- category
- priority
- the user-visible or operator-visible capability
- concrete verification steps
- dependencies when relevant
- `"passes": false`

Keep `FEATURES.json` machine-friendly and stable.
During implementation, the mutable field should be the pass status, not the semantics of the test.

Exit gate:
another engineer could start executing without guessing what "good enough" means.

## Stage 6 — 交付编排与验证

Question: how do we land this work safely across many sessions without losing correctness?

Translate the spec into delivery mechanics:

- dependency-closed batches
- file ownership boundaries
- verification commands
- end-to-end proof points
- rollback boundaries
- discovered-branch intake rules
- change-request handling rules
- true blockers versus false blockers

Define the first 1-3 batches explicitly and identify what makes each batch done.
Also define how new information should change the plan:

- when to update the spec
- how to classify newly discovered branch work
- when a discovered branch is required versus only adjacent
- how accepted, deferred, or rejected branches are written back to the ledger
- when to version a batch
- when to pause for a human decision
- when to keep going automatically
- how stop legality is proven from file truth

Exit gate:
the work has a credible first delivery path, a verification strategy, a governed way to absorb
change, and a legal way to stop without losing the mainline.

## Final Output Contract

After all six stages, produce artifacts that execution can trust:

- `.forge/spec/SPEC.md`
- `.forge/spec/FEATURES.json`
- `.forge/DECISIONS.md`
- initial `.forge/STATE.md`
- initial batch contract(s)

If a real blocker remains, make it narrow and explicit.
Do not hide a structural unknown inside a fake requirement lock.

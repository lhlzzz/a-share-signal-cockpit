# Engineering Workflow

Status: ACTIVE
Applies to: Claude / Codex / MiMoCode agents

Every task follows this workflow. No shortcuts. No skipping phases.

---

## Phase 1: INTAKE

Before writing any code:

1. **Karpathy Guidelines** — load skill, internalize principles:
   - Think before coding. State assumptions.
   - Simplicity first. Minimum code that solves the problem.
   - Surgical changes. Touch only what you must.
   - Goal-driven execution. Define success criteria.

2. **Understand the task** — read the request, clarify ambiguity, identify scope.

3. **Create task** — use `task` tool to register the work item:
   ```
   task({ operation: "create", summary: "..." })
   ```

---

## Phase 2: UNDERSTAND CODEBASE

Before modifying any code:

1. **GitNexus context** — `gitnexus_context({ name: "symbolName" })` for 360-degree view of affected symbols.

2. **GitNexus query** — `gitnexus_query({ query: "concept" })` to find execution flows.

3. **CodeGraph context** — `codegraph_codegraph_context({ task: "..." })` for code structure and relationships.

4. **GitNexus impact** — `gitnexus_impact({ target: "symbolName", direction: "upstream" })` BEFORE editing any function/class. Report blast radius.

5. **Understand-Anything** (optional) — for architecture-level questions, use `/understand` skill.

---

## Phase 3: PLAN

For multi-step tasks:

1. **Plan Enforcer** — use `plan-enforcer-discuss` skill to capture intent, then `plan-enforcer-draft` to create implementation plan.

2. **State plan explicitly:**
   ```
   1. [Step] → verify: [check]
   2. [Step] → verify: [check]
   3. [Step] → verify: [check]
   ```

3. **For simple tasks** — skip formal planning, but still state success criteria before starting.

---

## Phase 4: IMPLEMENT

1. **RTK for commands** — use `rtk` prefix for token-efficient command output.

2. **Edit surgically** — match existing code style. Don't refactor unrelated code.

3. **One file at a time** — read before edit. Understand context.

4. **Track progress** — update task status:
   ```
   task({ operation: "start", id: "T1" })
   ```

---

## Phase 5: VALIDATE

After every change:

1. **Run tests** — `PYTHONPATH=... rtk pytest ... -q`

2. **GitNexus detect changes** — `gitnexus_detect_changes({ scope: "all" })` before committing.

3. **Lint/typecheck** — if available, run `npm run lint`, `npm run typecheck`, `ruff`, etc.

4. **Git diff** — `git diff` to review all changes.

---

## Phase 6: COMPLETE

1. **Verify success criteria** — check every item from Phase 3.

2. **GitNexus detect changes** — final check before commit.

3. **Commit** — only when explicitly requested:
   ```
   git add <specific files> && git commit -m "..."
   ```

4. **Update task** — mark done:
   ```
   task({ operation: "done", id: "T1", event_summary: "..." })
   ```

---

## Tool Reference

| Tool | Use For |
|------|---------|
| Karpathy Guidelines | Code quality principles |
| Plan Enforcer | Task planning, tracking, drift prevention |
| CodeGraph | Code structure, definitions, callers, callees |
| GitNexus | Execution flows, impact analysis, change detection |
| RTK | Token-efficient command output |
| Understand-Anything | Architecture analysis, knowledge graph |
| Task | Persistent work items |
| Actor | Subagent spawning for parallel work |

---

## Rules

- NEVER edit a function/class without first running `gitnexus_impact`.
- NEVER commit without running `gitnexus_detect_changes`.
- NEVER skip validation (Phase 5).
- ALWAYS state success criteria before starting work.
- ALWAYS run tests after changes.
- Prefer modifying existing files over creating new ones.
- Prefer simpler solutions over complex ones.

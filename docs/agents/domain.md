# Domain Docs

How the engineering skills should consume this workspace's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the workspace root when it exists
- **`docs/adr/`** for ADRs that touch the area you're about to work in

If any of these files don't exist, proceed silently. Don't flag their absence; don't suggest creating them upfront. The producer skill (`/grill-with-docs`) creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context workspace:

```
/
├── CONTEXT.md
├── docs/adr/
└── src/ or project files
```

## Use the glossary's vocabulary

When your output names a domain concept, use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, either reconsider whether the project uses that language or note the gap for `/grill-with-docs`.

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding.

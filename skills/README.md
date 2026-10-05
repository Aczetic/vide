# Skill documents

Agents load versioned instruction documents from this directory at runtime and
resolve them by name through the registry (`vide.registry`). Each file carries
YAML frontmatter — `name`, `version`, `kind`, `source` — and the registry syncs
them into the database, where exactly one version per name is active.

**The documents themselves are not included in this repository.** They encode
production craft and are maintained privately. The registry, versioning,
per-project pinning and comparison machinery is all here; supply your own
documents to use it.

The format:

```markdown
---
name: breakdown
version: 1
kind: extraction
source: in-house
description: One line describing what this skill does.
---

# Instructions the agent loads into its context.
```

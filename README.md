# vide

**Production infrastructure for AI-generated film and episodic drama.**

A screenplay goes in. What comes out is every character, set and prop the story
needs — specified, generated, reviewed, and waiting for a human to approve —
plus a shot list and a keyframe for every shot.

Not a prompt tool. The hard parts of this work are continuity, scheduling and
review, and that is what this is built around.

---

## Why

Everything before a frame is generated is still done by hand.

Someone reads the script and lists the characters. Someone works out that the
lead wears nine different outfits because the story spans nine days. Someone
notices that a set appears only inside an action line and never in a heading, so
nobody designed it. Someone writes the prompts, generates four options, judges
them, regenerates.

It takes people who know the craft vocabulary, and it does not scale. A handful
of titles in flight is the ceiling — and the ceiling is human hours, not compute.

So the platform is built on one bet:

> **Expertise belongs in the tooling. Judgement stays with people.
> Agents do the generation and the first-pass review.**

A reviewer should never need to know how to phrase a camera instruction. They
should only need to know whether what came back looks right.

---

## The pipeline

```
  script ──▶ structure ──▶ extraction ──▶ entities ──▶ continuity ──▶ design order
                                                                           │
                        ┌──────────────────────┬───────────────────────────┤
                        ▼                      ▼                           ▼
                   characters              locations                     props
                        │                      │                           │
                        └──────────┬───────────┴───────────────────────────┘
                                   ▼
                          agent review ──▶ ◆ human approval
                                                   │
                                                   ▼
                                       shot cards ──▶ keyframes ──▶ ◆
```

`◆` is a gate. Gates are the only thing in the system allowed to block.
Everything else runs concurrently.

---

## What it does

### Reads a script the way a production manager would

Ingests `.docx`, `.pdf`, `.html`, `.xlsx`, `.md` and plain text, then splits it
into episodes and scenes — handling the inconsistent formatting real scripts
arrive with rather than demanding a clean one.

**Structural parsing is deterministic, not generated.** Scene boundaries,
headings, dialogue and delivery cues have exact answers, so they are parsed:
free, instant, reproducible, and incapable of inventing a scene that is not
there. Model judgement is spent only on the layer that actually needs it.

Every extracted item keeps a span back to the line it came from, so a reviewer
can always click through to the original text.

### Works out what has to be built

Per scene, it extracts what a production has to make — who is present including
people named only in action lines, locations the action implies that no heading
names, props, vehicles, on-screen graphics, wardrobe, and the physical states
that later scenes must match.

Then it resolves the mess. A script calls one person four things and one room
three; clustering collapses those into single identities, assigns tiers from
scene and line counts, and builds the location hierarchy.

**What it cannot settle, it asks.** Where nothing in the text determines whether
two names are one thing, that becomes an explicit question for a human rather
than a silent merge. A wrong merge means building one set instead of two, and it
is discovered on screen.

Story-days are computed deterministically — the same script always yields the
same answer. That number multiplies into the costume count, so it is not allowed
to drift between runs.

The output is a **design order**: every character state, every set at every time
of day, every prop, ranked so that whatever is reused most is built first.

### Generates the assets

Character sheets, location plates and prop sheets — several candidates each,
generated in parallel.

Specifications are written once per entity and then pasted **verbatim** into
every prompt that uses them. That repetition is not redundancy; it is what keeps
a character the same person across hundreds of shots.

Prompts are written by agents that load versioned skill documents, then checked
against assertions before anything is sent. A skill instructs, and a model may
ignore an instruction — the assertions are enforced. A prompt that breaks one is
repaired surgically, and rejected only if repair fails, because spending four
generations to rediscover a known failure is waste.

Locations build in tiers: a master establishes the space before any derived
view, so every later view inherits one room instead of inventing its own.

### Checks the work before a person sees it

A vision agent reviews every image against its specification and returns a
verdict, a reason in plain language, and a **failure category**.

The category is the point. "It looks wrong" sends someone guessing. A category
says what to change — patch one prompt section, simplify the shot, or rebuild
the underlying asset.

Rejections are never deleted. They stay visible, a human can override, and those
overrides are the signal used to tune the reviewer.

### Plans the shots

Each scene splits into shots, each with a structured card covering material,
direction, camera and edit. The scene's geography is fixed **once** and inherited
by every shot in it — which is what stops a room rearranging itself between cuts.

Keyframes are composed from *approved* assets, not merely generated ones. A
still built on something nobody signed off has to be rebuilt the moment that
thing changes.

### Puts people only where they are needed

A cross-project review queue shows everything waiting on a decision, so
reviewers clear work rather than hunt for it. One click decides; keyboard
shortcuts move through candidates. Selecting writes a gate decision, which is
what releases the work downstream.

### Measures itself

Every model call is recorded with its prompt and structured sections, the skill
versions that produced it, its references, outputs, cost and latency — enough to
replay any generation exactly.

That log is the measurement: first-pass approval rate, attempts to approval,
cost per approved element, and which failure categories dominate. Because skill
versions are recorded per call, two versions can be compared on the same work
instead of argued about.

Where a number cannot be computed, it is reported as missing rather than as
zero.

---

## Principles

| | |
|---|---|
| **Gates are the only blocking points** | Nothing waits except on a person. |
| **Per-element flow** | Pipelines are graphs of elements, not global phases. One character clearing its gate moves on alone. |
| **Assets before shots** | Nothing downstream is attempted until what it depends on is approved. |
| **Versions are immutable** | A variant never overwrites its parent. Changing something upstream marks dependents stale — flagged for a human, never silently regenerated. |
| **Change one thing at a time** | A regeneration patches the failing section and leaves the rest byte-identical. A rewritten prompt loses whatever already worked. |
| **Model-agnostic core** | Providers and models are configuration. Model-specific knowledge lives in adapters and skill documents, never in the pipeline. |
| **Skills are data** | Agents request a skill by name; the registry returns the active version. Swapping one is configuration. Projects pin versions, which is how comparisons run. |

---

## Architecture

```
Python · FastAPI · SQLAlchemy · PostgreSQL + pgvector · Alembic
Next.js · React · TypeScript · Tailwind
S3-compatible object storage · Docker
```

```
src/vide/
  models/      data model — projects, scripts, entities, assets,
               generations, reviews, gate decisions
  providers/   adapters behind two protocols: fast synchronous text, and slow
               asynchronous media. Tier config maps task to model, so cost
               scales with the project rather than the code
  registry/    versioned skill registry with per-project pinning
  jobs/        Postgres-backed queue — horizontal workers, retry with backoff,
               reclaimable leases, fan-out per element
  pipeline/    ingest, extraction, entity resolution, continuity, design order,
               specs, prompts, generation, review, shot planning, keyframes
  storage/     declared object-storage layout with drift reconciliation
  api/         HTTP API
web/           review interface
```

**The interface** is a dense asset grid with a detail overlay, built for someone
reviewing a few hundred images an hour rather than browsing a gallery. Status
reads as a hairline on the tile instead of a badge — badges cost the density
that makes the view usable at all.

---

## Running it

Requires Docker and Python 3.11+.

```bash
docker compose up -d                       # Postgres + pgvector
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env                       # add provider credentials
.venv/bin/alembic upgrade head
.venv/bin/python -m vide.doctor            # checks every dependency; no side effects
```

```bash
.venv/bin/uvicorn vide.api:app --port 8000
cd web && npm install && npm run dev
```

### Skill documents

Agents load versioned instruction documents from `skills/` at runtime and
resolve them by name through the registry.

**Those documents are not in this repository.** They encode production craft and
are maintained privately. Everything around them — the registry, versioning,
per-project pinning, comparison — is here. Supply your own; see
[`skills/README.md`](skills/README.md) for the format.

---

## Status

Under active development, running against real productions.

Pre-production is built: script through to approved assets, shot cards and
keyframes. Video generation is next, and the architecture already accommodates
it — the data model, the generation log and the skill registry need no redesign
to support it.

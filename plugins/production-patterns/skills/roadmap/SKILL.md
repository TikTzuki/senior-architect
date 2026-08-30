---
name: roadmap
description: Track and work through the Dev Insider backend engineering roadmap — five tiers from Foundations to Technical Leadership. Use to see where you stand, decide what to study next, record a series as learned, or turn something you just learned into a review lesson. Triggers on "what should I learn next", "roadmap progress", "mark X as done", or questions about the Foundations / Distributed / Production Data / System Design / Leadership tiers.
argument-hint: "[status | next | done <series> | promote <series>]"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# Roadmap

Work through the Dev Insider path from developer to senior engineer, and convert what you learn
into review knowledge that outlasts the reading.

Full structure — five tiers, 31 series, with what each tier is for:
[references/dev-insider-roadmap.md](references/dev-insider-roadmap.md). **Read it before
answering anything about tier contents or ordering.**

## The one rule that makes the roadmap work

The roadmap's own thesis is that the failure mode is not missing knowledge but **learning the
right thing at the wrong time** — microservices before transactions, Kubernetes before knowing
how an app is operated.

So: **do not recommend skipping ahead.** If someone with nothing marked in Tier 1 asks about
Event Sourcing or a payment ledger, answer the question, then say plainly which foundation it
rests on and where it sits in the order. The value of a roadmap is entirely in its sequence; a
roadmap you take out of order is just a reading list.

The exception is a live work problem. If they are shipping a payment integration on Monday, help
with the payment integration. Note the foundation gap once, then be useful.

## Progress file

Progress lives in `ROADMAP.md` at the root of the user's working repository — outside the plugin,
which may be installed read-only. Create it from the tier tables in the reference if missing.

Each series carries one of three states:

| State             | Meaning                                                            |
|-------------------|--------------------------------------------------------------------|
| `[ ]` not started | —                                                                  |
| `[~] learned`     | studied it; understands the ideas                                  |
| `[x] captured`    | learned **and** a lesson exists in `production-review/references/` |

`captured` is the state that matters. `learned` decays; a lesson file keeps working, because it
audits every future diff. A roadmap tracked only to `learned` is a reading log.

## Commands

**`status`** — Read `ROADMAP.md`. Report counts per tier, the current tier, and the next
unstarted series in order. Lead with the single most useful sentence: what to study next, and
why it comes next. Do not dump all 31 rows unless asked.

**`next`** — Name the next series in roadmap order, say what it covers and what it unlocks
downstream, and check whether any existing lesson already touches it. Recommend one concrete
starting point, not a survey.

**`done <series>`** — Mark `[~] learned`. Then ask the question that matters: *did anything here
describe a failure mode that working code exhibits?* If yes, offer to promote it. If no, say so
and move on — most leadership and mindset material is genuinely not promotable.

**`promote <series>`** — Hand off to [lesson-add](../lesson-add/SKILL.md) to write the lesson,
then flip the series to `[x] captured` and update the coverage mapping in the roadmap reference.

**`gaps`** — List series where `learned` but not `captured`, highest tier first. This is the
backlog of knowledge that has not yet been made durable.

## Working through a series

The roadmap's series are TechCraft's paid Dev Insider content, which is **not accessible from
here**. Do not fabricate its contents or imply you have read it.

What to do instead, when the user is studying a series:

1. Ask what they read or watched, and what the central claim was.
2. Discuss it properly — push on it, connect it to systems they work on, surface the trade-off
   the material may have skipped.
3. Look for the reviewable residue: a specific way working code gets this wrong.
4. If there is one, promote it. If not, mark `learned` and move on without inventing a lesson.

Step 4 is where the discipline lives. A knowledge base padded with unreviewable "lessons" reviews
nothing, and every added file dilutes the ones that work.

## Honesty about coverage

Five lessons currently touch this roadmap, all partial, all in Tiers 1 and 3. Tiers 2, 4, and 5
have none. When reporting progress, say that plainly rather than letting a coverage table imply
a tier is handled. One lesson is one failure mode — not a series.

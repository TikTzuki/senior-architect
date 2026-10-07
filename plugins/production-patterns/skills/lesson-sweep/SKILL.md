---
name: lesson-sweep
description: Work through the saved TechCraft Dev Insider posts series by series and turn them into new or extended production-patterns lessons, with a coverage ledger and an originality gate. Use when the user says "sweep the next posts", "continue the Dev Insider extraction", "turn series X into lessons", or "what's left to extract".
argument-hint: "[status | next [series] [-n N] | post <post_id>]"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# Lesson Sweep

Repeatable pipeline from saved subscriber posts to published review knowledge. Each run picks up
where the last one stopped, because progress lives in a ledger, not in conversation memory.

```
save (logged in) → index → to-md → coverage next → per post: read → decide → write → gate → wire → verify → mark
```

[lesson-add](../lesson-add/SKILL.md) owns *how a single lesson is written*. This skill owns
*which posts, in what order, and proof that each one was handled*. Read lesson-add before writing.

## Ground rules (why this skill exists)

- **The posts are paid content. The repo and site are public.** Nothing from
  `dev-insider-docs/` is committed, linked, or embedded: no text, no translations, no images
  from `assets/`. That folder is gitignored, and it stays that way.
- **A lesson is not a retelling.** Read the post, close it, then write the failure modes, the
  mechanism, and the checklist in this repository's structure, from engineering knowledge. If
  the result reads like the post in English, it is a translation. Rewrite it.
- **Diagrams are redrawn from the concept**, as ASCII or mermaid, in a shape the lesson needs.
  The post's layout is not the template.
- **A post that adds no failure mode is skipped with a reason.** Skipping is a normal outcome.
  The bar in lesson-add applies unchanged.

## 0. Bring new posts in (only when the user saved more)

The user saves each post while logged in (Chrome: *Save Page As → Webpage, Single File*) into
the matching `dev-insider-docs/<series>/` folder. Then:

```bash
scripts/dev-insider-index.py check <post-url>...   # exit 2 = already saved; don't re-save
scripts/dev-insider-to-md.py                       # .mhtml → .md + assets; verifies ≥98% words
scripts/dev-insider-index.py                       # rebuild INDEX.md / index.json
```

Never fetch Patreon yourself. If a converted post says it is not viewable, the save was made
logged out. Ask the user to save it again.

## 1. Where are we

```bash
scripts/dev-insider-coverage.py status
scripts/dev-insider-coverage.py next [--series <folder>] [-n 3]
```

Default to **3 posts per run**, in roadmap order (see
[roadmap](../roadmap/references/dev-insider-roadmap.md): Tier 1 series before later tiers).
Fewer, better lessons beat a large batch of thin ones.

## 2. Per post

**a. Shortlist overlap.**
```bash
scripts/dev-insider-coverage.py suggest <post_id>
```
This is a title-keyword ranking. Treat it as a starting point and confirm by reading the
candidate lessons' rule lines and failure-mode headings.

**b. Read the post** (`file` from step 1). Note in your own words: the failure modes it names,
the mechanism behind each one, and the numbers it gives. Say how much substance it actually has.
Image-only posts are common. If the substance sits in images you can't see, say so.

**c. Decide**: exactly one of the following.

| Decision | When | Then |
|---|---|---|
| `extend` | An existing lesson covers the topic; the post adds failure modes or checklist items | Edit that lesson. Add to its attribution line |
| `lesson` | A distinct failure mode with no home | New file via lesson-add steps 4–6 |
| `skip`   | Common knowledge, opinion, intro/outro post, or already fully covered | Record the reason |

State the decision and one-line reason to the user before writing.

**d. Write** following lesson-add's structure and its attribution format. In the attribution,
name what the post contributed and what is this repo's.

**e. Gate.** Must pass before wiring:
```bash
scripts/originality-check.py <lesson.md> --source "dev-insider-docs/<series>/<post>.md"
```
A failure lists lesson line numbers. Rewrite those passages from the concept. Don't paraphrase
the flagged phrases. The check is mechanical and cannot see translation. So also re-read the
lesson against the post's section order. If the headings line up one-to-one, restructure it.

**f. Wire and verify**, as in lesson-add steps 5–7: index row in
`production-review/SKILL.md`, entry in `knowledge-map.yaml` (tags must exist in tik_space
`tags.yml`), and the three grep checks. Then update the series' *Lesson coverage* cell in
`roadmap/references/dev-insider-roadmap.md`.

**g. Mark**: only after f passes:
```bash
scripts/dev-insider-coverage.py mark <post_id> --as lesson|extend --target <lesson.md>
scripts/dev-insider-coverage.py mark <post_id> --as skip --reason "<why>"
```

## 3. End of run

Report it as a table: post → decision → lesson file → gate result. Then show
`scripts/dev-insider-coverage.py status` for the series touched and `git status --short`.

**Do not commit or push.** The user reviews the diff first. If they ask to commit, use one
commit per lesson, `feat: add <topic> lesson` / `docs: extend <topic> lesson`, and confirm
`git status` shows nothing under `dev-insider-docs/`.

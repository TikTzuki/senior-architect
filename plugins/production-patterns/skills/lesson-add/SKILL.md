---
name: lesson-add
description: Capture a production engineering lesson from an article, conference talk, post-mortem, social post, or hard-won incident into the production-patterns knowledge base, so future code reviews check for it. Use when the user shares a link or writeup and wants it turned into reusable review knowledge, or says "add this as a lesson" or "remember this pattern for reviews".
argument-hint: "[url-or-topic]"
allowed-tools: Bash, Read, Write, Edit, Grep, WebFetch
---

# Add a Lesson

Turn a source — an article, talk, post-mortem, social post, or something learned the hard way —
into a lesson file that [production-review](../production-review/SKILL.md) checks code against.

## The bar

A lesson earns its place only if it describes a **failure mode that working code exhibits**. It
must be something that passes local testing and review, then breaks under production conditions.

Reject anything that is a style preference, a library recommendation, a restatement of common
knowledge, or a pattern with no concrete failure attached. The value of this knowledge base is
inversely proportional to how much of it is filler.

## Steps

**1. Get the source.** Fetch the URL if given. If retrieval returns only a summary or a teaser —
common for social posts, where the substance sits in images — **say so explicitly and state how
much real content you got.** Never let a thin extract silently become a thick lesson.

Some sources cannot be fetched at all. Patreon, and any site that renders content client-side
behind a login, returns nothing useful to a plain fetch. For those, ask the user to open the post
while logged in and save it (**Save Page As → Web Page, Complete**), then extract:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/lesson-add/scripts/extract-patreon.py saved.html -o post.md
python3 ${CLAUDE_PLUGIN_ROOT}/skills/lesson-add/scripts/extract-patreon.py *.html -d posts/   # batch
```

It reports `not viewable by the saved session` if the save was made logged out, and exits
non-zero — check that rather than assuming an empty result means an empty post. Screenshots also
work for image-heavy posts, since diagrams are frequently where the real content lives.

**2. Decide the content source with the user.** If the source is too thin to build on, ask
whether to write the lesson from established practice or wait for them to paste the material.
Do not quietly fill the gap from your own knowledge and present it as theirs.

**Write lessons in your own structure, not the source's.** A lesson is a review artifact —
failure modes, mechanisms, and a checklist — not a retelling. This matters most for paid or
subscriber material: extract the engineering substance the user is entitled to learn from, and
express it as reviewable knowledge, rather than reproducing the author's prose or walkthrough.

**3. Check for overlap.** Read the index in `../production-review/SKILL.md`. If an existing
lesson covers this, extend that file rather than adding a near-duplicate — two lessons on one
topic means reviews check one and miss the other.

**4. Write the lesson** to `../production-review/references/<topic>.md`, hyphen-cased and named
for the problem, not the solution (`large-file-upload.md`, not `use-presigned-urls.md`) — the
reader is searching by the symptom they have.

Follow the structure of the existing lessons:

- A one-line rule in bold at the top — the whole lesson compressed
- A table of contents if the file runs past ~100 lines
- The anti-pattern, concretely, with a diagram where the shape matters
- Why development hides it
- How it fails in production, as a list of distinct named failure modes
- The correct pattern, walked through step by step
- A review checklist of specific, checkable items
- An attribution line stating what came from the source and what did not

**5. Register it** in the lesson index table in `../production-review/SKILL.md`. A lesson absent
from that table is never loaded and never used — this step is what makes it real.

**6. Add it to `knowledge-map.yaml`** at the repo root. This is what publishes the lesson to
the site — its title, which of the seven sets it belongs to, and its tags. A lesson missing
from the manifest still works in this skill, but it reaches the site untagged and ungrouped,
which is how a taxonomy quietly rots.

```yaml
  <topic>.md:
    title: <the H1, verbatim>
    group: runtime | storage | transactions | data-modeling | api-design | distributed-systems | payments
    tags: [ <the group's topic tag>, tier-N-... ]
    sidebar_position: <next free number within that group>
```

The `group` must match the set named in the lesson's own `Part of the X set:` line — that
line and this field are the same fact written twice, so they must agree. Tags must exist in
`TikTzuki/tik_space/docs/tags.yml` or **the site build fails**.

Do **not** add front matter to the lesson file itself. The sync script injects it into the
published copy; the file here stays plain markdown so relative links between lessons and
`${CLAUDE_PLUGIN_ROOT}` resolution keep working for marketplace installs.

**7. Verify the lesson is wired up.** The failure mode here is a lesson file that exists but is
never loaded, so check every link actually resolves:

```bash
cd ${CLAUDE_PLUGIN_ROOT}/skills/production-review
grep -c "references/<topic>.md" SKILL.md   # must be >= 1: it is in the index
test -f references/<topic>.md && echo "lesson file present"
grep -c "^  <topic>.md:" ../../../../knowledge-map.yaml   # must be 1: it will publish
```

## Writing guidance

Write for an engineer who will read this while reviewing a diff under time pressure.

- Be concrete. "Each in-flight upload pins a worker for the duration of the transfer" beats
  "this does not scale well."
- Name the numbers that matter — default proxy timeouts, practical size thresholds — and mark
  them as typical rather than universal.
- Explain *why* a failure happens. A checklist teaches nothing; the reasoning is what transfers
  to the next unfamiliar situation.
- Keep it under ~200 lines. Past that, split by sub-topic and cross-link.

## Attribution

Always end with what the source actually contributed:

```markdown
*Topic prompted by [source](url). The post states the problem and names X as the answer; the
detail here is written from established practice, not transcribed from it.*
```

Credit accurately in both directions. Do not attribute your own writing to a source that did not
say it, and do not omit a source that supplied the substance.

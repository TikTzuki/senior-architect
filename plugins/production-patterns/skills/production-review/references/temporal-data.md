# Temporal Data

**Rule: a system does not act on objective truth — it acts on what it believed at the time.**
Reconstructing a past decision needs two time axes: when the fact was true, and when you knew it.

Part of the data-modeling set: [retention & history](data-retention-and-history.md) ·
**temporal data** (this file) · [event sourcing & CQRS](event-sourcing-and-cqrs.md) ·
[message delivery](message-delivery-semantics.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Valid time: modelling when a fact applied](#valid-time-modelling-when-a-fact-applied)
- [Why one timeline is not enough](#why-one-timeline-is-not-enough)
- [Bitemporal: two axes](#bitemporal-two-axes)
- [When you actually need it](#when-you-actually-need-it)
- [Implementation notes](#implementation-notes)
- [Review checklist](#review-checklist)

## The anti-pattern

```sql
CREATE TABLE interest_rates (
  product_id  bigint PRIMARY KEY,
  rate        numeric NOT NULL       -- the current rate. Only the current rate.
);

UPDATE interest_rates SET rate = 8.5 WHERE product_id = :id;   -- the old rate is gone
```

Then legal asks: *"Which rate did we apply to this loan when it was approved in March?"* The table
holds one number, and it is today's.

Even a history table only partly rescues this, because it records *when the row changed*, not *when
the rate was in force*. A rate effective from 1 January that was entered into the system on
15 February has two different dates, and a single-timeline schema cannot express both.

## Why development hides it

Tests assert on current values, and the current value is always correct. Nothing locally asks a
question anchored to a past moment.

The failure is also **silent and unrecoverable**: nothing errors, and the information was never
stored, so no amount of later work can produce the answer. Like the retention patterns, this is a
schema decision that cannot be revisited after the fact.

## Valid time: modelling when a fact applied

The first axis is **valid time** — the period during which a fact is true in the real world:

```sql
CREATE TABLE interest_rates (
  product_id   bigint NOT NULL,
  rate         numeric NOT NULL,
  valid_from   date NOT NULL,
  valid_to     date,                     -- NULL = still in force
  PRIMARY KEY (product_id, valid_from)
);
```

Querying "the rate that applied on 15 March":

```sql
SELECT rate FROM interest_rates
WHERE product_id = :id
  AND valid_from <= '2026-03-15'
  AND (valid_to IS NULL OR valid_to > '2026-03-15');
```

This makes time a first-class part of the model rather than metadata about the row. It also lets
you insert a future-dated change — a rate effective next month — which a mutable `rate` column
cannot represent at all.

Two invariants worth enforcing rather than trusting: **no overlapping periods** for the same key,
and **no gaps** where a required fact has no value. In PostgreSQL an `EXCLUDE` constraint with a
range type enforces non-overlap directly; see
[transaction-isolation](transaction-isolation.md) for why an application-level check is not enough.

## Why one timeline is not enough

The scenario that makes this concrete, and it is worth reading carefully:

> An insurer approves a claim in March. In October, an auditor asks: *"Why did you approve such a
> low payout for John Doe in March?"*
>
> The engineer checks and answers: "Because he was born in 1990 — low-risk band, cheaper premium,
> lower benefit."
>
> The auditor replies: "But the system says 1988 today."
>
> Both are correct. His date of birth was always 1988 — that is the fact. But in March the system
> **held** 1990, and it priced the policy on what it held. Someone corrected the record in June.

Now: was the March decision wrong? With one timeline you cannot tell. You know today's truth and
have permanently lost *what the system knew when it decided*.

> A system does not run on objective truth. It runs on what it knew at a specific point in time.

Correcting data is not the same as data changing. A customer moving house is a new fact with a new
valid period. A typo in their birth date being fixed is a **retroactive correction** — the fact was
always the corrected value, and only your knowledge changed. Conflating those two is the bug.

## Bitemporal: two axes

Keep both:

- **Valid time** — when the fact was true in the world
- **Transaction time** (or *decision time*) — when the system recorded it

```sql
CREATE TABLE customer_facts (
  customer_id     bigint NOT NULL,
  date_of_birth   date NOT NULL,
  valid_from      date NOT NULL,          -- real-world validity
  valid_to        date,
  recorded_at     timestamptz NOT NULL,   -- when we learned it
  superseded_at   timestamptz,            -- when this belief was replaced
  PRIMARY KEY (customer_id, valid_from, recorded_at)
);
```

Two distinct questions, both answerable:

```sql
-- What is true now, as best we know?
WHERE valid_to IS NULL AND superseded_at IS NULL

-- What did we believe in March about the state of the world in March?
WHERE valid_from <= '2026-03-15' AND (valid_to IS NULL OR valid_to > '2026-03-15')
  AND recorded_at <= '2026-03-31' AND (superseded_at IS NULL OR superseded_at > '2026-03-31')
```

The second query is the one that defends the March decision. It reconstructs not just the past
state of the world but the past state of your *knowledge* — and that is what an audit actually
examines.

## When you actually need it

Bitemporal modelling is genuinely expensive: more storage, harder queries, and a real conceptual
load on everyone who touches the schema. Do not apply it by default.

**You need it when a past decision must be defensible after the underlying data has been
corrected.** In practice:

- **Insurance** — pricing and claims decided on customer facts that get corrected later
- **Banking and lending** — credit decisions, applied rates, limits under regulatory review
- **Fund management** — NAV recomputed after a corrected trade or price
- **Payroll and tax** — retroactive salary adjustments and backdated tax rules
- **Healthcare** — clinical decisions made on records later amended

**You do not need it for** product catalogues, user preferences, feature flags, or anything where
"it changed and now it's different" is a complete account. For those, valid time alone or a plain
history table is right.

The reviewable question: *if this data were corrected next year, would anyone need to explain a
decision made on the old value?* If yes, one timeline is not enough.

## Implementation notes

- **Never `UPDATE` in place.** A correction inserts a new version and closes the previous belief by
  setting `superseded_at`. Same immutability logic as [payment-ledger](payment-ledger.md).
- **Index for the query you run.** Point-in-time lookups need the key plus both time ranges;
  without that, every historical query degrades into a scan — see
  [indexes-and-query-plans](indexes-and-query-plans.md).
- **Decide on inclusive/exclusive bounds once** and document it. Half-open `[from, to)` avoids the
  off-by-one-day ambiguity that otherwise appears in every report.
- **Use range types and exclusion constraints** where available, rather than trusting application
  code to keep periods disjoint.
- **Keep "now" out of the data.** Never store a computed `is_current` flag that must be maintained;
  derive current from `valid_to IS NULL AND superseded_at IS NULL`.

## Review checklist

- [ ] Facts whose past values matter carry `valid_from` / `valid_to`, not a mutable column
- [ ] Overlapping validity periods are prevented by constraint, not convention
- [ ] Required-fact gaps in the timeline are detected
- [ ] Corrections are distinguished from genuine changes in the model
- [ ] Where past decisions must be defensible, transaction time is stored alongside valid time
- [ ] Point-in-time queries pin both axes, not just valid time
- [ ] Historical rows are never updated in place; corrections supersede
- [ ] Temporal queries are indexed on key plus time range
- [ ] Interval bound convention (half-open) is consistent and documented
- [ ] Current state is derived, not stored as a maintained flag

---

*Synthesized from TechCraft's Data Modeling Patterns series, parts P5–P6
([collection](https://www.patreon.com/techcraft_official)). The insurance date-of-birth scenario and
the "a system runs on what it knew, not on objective truth" framing are TechCraft's; the review
structure and checklist are this repository's.*

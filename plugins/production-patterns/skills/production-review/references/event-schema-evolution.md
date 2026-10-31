# Event Schema Evolution

**Rule: every event version ever written is a format you must read, with its original meaning, for
as long as the store exists.** A change to an event is not a deploy — it is a permanent addition to
the set of shapes your code must understand, and it is tested only when something replays history.

Part of the data-modeling set: [retention & history](data-retention-and-history.md) ·
[temporal data](temporal-data.md) · [event sourcing & CQRS](event-sourcing-and-cqrs.md) ·
[event replay & rebuild](event-replay-and-projection-rebuild.md) ·
**event schema evolution** (this file) · [message delivery](message-delivery-semantics.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Failure modes](#failure-modes)
- [Shape changes and meaning changes](#shape-changes-and-meaning-changes)
- [Upcasters translate; they do not invent](#upcasters-translate-they-do-not-invent)
- [Readers first, writers second](#readers-first-writers-second)
- [Prove old history still reads](#prove-old-history-still-reads)
- [The event store is not a public API](#the-event-store-is-not-a-public-api)
- [Review checklist](#review-checklist)

## The anti-pattern

A tidy-up merged on a Tuesday:

```diff
 class FundPurchased(Event):
-    amount: Decimal
+    investment_amount: Decimal   # clearer name, product asked for it
+    currency: str = "VND"        # new markets coming
```

New events are written with the new shape; the live projector reads them; every test passes, because
every test builds events with today's class. Weeks later someone rebuilds a projection. The first
three years of history have `amount` and no `currency`, so the rebuild either fails on every old
event or — worse, with a lenient deserializer — reads them as zero, in VND.

## Why development hides it

Live code mostly reads events written moments ago by the same version of the code. Test fixtures are
constructed from current classes, so they can only ever be current-shape. Old events are read only
by replays, rebuilds and new consumers starting from the beginning — operations that run rarely, and
usually in an incident. A schema mistake therefore ships silently and is discovered months later,
at the worst time, across every event written before it.

## Failure modes

**1. A rename or removal breaks history.** A field renamed or dropped without a version leaves every
earlier event unreadable to the new code — discovered at the next rebuild, not at deploy.

**2. A default invents facts.** An upcaster fills a missing `currency` with `"VND"` or a missing
`fund_code` with the most common fund. Historical reports now state with confidence something the
system never recorded. If the value cannot be derived from what the old event actually carries, it
is **unknown** and must say so — explicitly, distinct from null.

**3. The same field changes meaning.** `amount` used to be gross and is now net, with the same name
and the same version. Nothing fails to parse; every replay, report and reconciliation over old
events is now wrong by the tax amount. This is the most dangerous change because no check can see
it — the same class of breakage as a unit change in an
[API contract](api-contracts.md#three-breakages-that-pass-every-schema-check).

**4. An enum value is removed.** Code drops a status no longer produced. Events from two years ago
still contain it. The consumer that meets one cannot deserialize it, and its partition stops — for
the few hundred accounts on that partition — while every infrastructure metric stays green. Adding
values is the consumer's problem; removing them is the producer's, forever.

**5. A rollback that cannot roll back.** The new writer has already appended events in V2 when a bug
forces a rollback. The code goes back; the events do not. The previous reader now meets V2 events
and fails. In an event-sourced system, rollback safety is decided before the writer ships.

**6. A non-deterministic upcaster.** One that looks up a reference table or calls a service to fill a
field gives different answers on different replays, which breaks the determinism every rebuild
relies on — see [replay](event-replay-and-projection-rebuild.md#apply-must-be-deterministic).

**7. Facts discarded at write time.** An event that records only what today's feature needs drops
what the system knew at that moment — source, channel, the rule applied. Nothing can recover it
later; history only contains what was written. Record the context the command actually had.

## Shape changes and meaning changes

Classify every change before writing code for it:

| Change | Old events | Handling |
|---|---|---|
| Add an optional field | lack it | upcast to an explicit UNKNOWN, unless derivable from data the old event carries |
| Rename / restructure | old shape | upcaster maps old → new deterministically; never edit stored events |
| Remove a field or enum value | still contain it | keep reading it; stop *writing* it |
| Change what a field means | indistinguishable | **do not reuse the field** — new field or new event type |
| The fact itself changed | describes the old fact | new event type; old events keep their meaning |

Tolerant readers (ignore unknown fields, treat new ones as optional) cover additive changes cheaply
but let meaning drift unnoticed. Copying the whole history into a new store is the heavy option,
justified only by a platform change or a compliance requirement — never to tidy a field name.

## Upcasters translate; they do not invent

An upcaster converts a stored event to the current shape at read time, chained version by version,
before domain code sees it. The stored bytes never change. Its rules:

- **Deterministic and self-contained** — input is the old event only.
- **No fabricated values** — UNKNOWN, not a plausible default.
- **One per version step**, kept for as long as events of that version exist.
- **Tested against real old payloads**, not hand-built ones.

## Readers first, writers second

1. **Inventory every consumer** — projectors, replay jobs, exports, other teams' subscribers. One
   without an owner is a risk you have not sized.
2. **Deploy readers** that understand both versions, while writers still emit the old one.
3. **Replay a mixed-version history** in staging.
4. **Canary the writer** for a small slice of traffic; watch unknown-version counts,
   deserialization errors and projection lag.
5. **Expand** the writer once those stay at zero.
6. **Contract** — remove old-version handling only when no stored event of that version remains
   within retention, which for an event store usually means never.

This is [expand and contract](schema-migrations.md#expand-and-contract), with one difference: a table
migration can rewrite old rows; an event store cannot, so the "contract" step rarely comes.

## Prove old history still reads

Make compatibility a CI gate, not a review comment:

- **Fixtures of every past version**, captured from real payloads, must deserialize and upcast.
- **A golden stream** — a curated history with every version, the awkward cases, corrections and
  missing fields — replayed to known final states.
- **Every important projection rebuilt** from a mixed-version history without errors.
- **A check that missing data comes out as UNKNOWN**, not as a default.

## The event store is not a public API

Domain events exist to rebuild one service's state; their shape follows that service's internals
and must stay free to change. Publishing them directly to a broker turns every internal refactor into
a breaking change for teams you have never met: split a pricing event in two and payment can no
longer find the final amount, analytics double-counts, notifications crash on a missing field, and
a shared consumer's backlog stalls an unrelated team.

Publish **integration events** instead: a separate, deliberately small, versioned contract produced
by a translator from domain events and sent through the [outbox](message-delivery-semantics.md#outbox-do-not-lose-the-event).
Internal IDs, calculation breakdowns and personal data stay inside. Treat its schema with the
discipline of an [API contract](api-contracts.md).

**Publish facts you own, not requests you made.** A service that has only *asked* for stock to be
held must not emit `InventoryReserved` — downstream will tell the customer it is reserved. Emit
`InventoryReservationRequested`; the inventory service emits `InventoryReserved` when it is true.
A contract should also state what it does **not** assert ("order confirmed" does not mean stock is
held), so consumers do not infer it.

## Review checklist

- [ ] Event changes are classified: additive, rename, removal, meaning change, new fact
- [ ] No stored event is ever edited; old versions are translated at read time
- [ ] A changed meaning gets a new field or event type, never the old field
- [ ] Upcasters are deterministic, call nothing, and emit UNKNOWN rather than invented defaults
- [ ] Enum values and fields still present in history are never removed from readers
- [ ] Readers that understand the new version ship before any writer emits it; writers are canaried
- [ ] A rollback plan accounts for new-version events already written
- [ ] CI deserializes real fixtures of every past version and replays a golden stream
- [ ] Unknown-version and deserialization-error counts are monitored per consumer
- [ ] Events record the context the system had at the time, not only what today's feature needs
- [ ] Domain events are not published directly; integration events go through a translator and outbox
- [ ] Published events state facts the producer owns, and say what they do not assert

---

*Prompted by TechCraft's Event Sourcing & CQRS series, parts P9, P14, P23 and P28
([collection](https://www.patreon.com/techcraft_official)). The posts supply the fund-purchase
version history, UNKNOWN over invented defaults, the gross-to-net meaning change, the rename found at
rebuild, the compatibility matrix and CI gate with a golden stream, readers-first rollout with a
canary writer, the rollback that cannot roll back events, the removed enum that stalled a partition,
discarded provenance, and the domain-versus-integration-event incident with intent versus fact. The
structure, the change-classification table, and the review checklist are this repository's.*

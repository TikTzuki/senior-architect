# Event Sourcing, Snapshots & CQRS

**Rule: storing state answers "what is it now"; storing events answers "how did it get there".**
Choosing events is choosing a permanent obligation — snapshots, projections, and versioned schemas —
so choose it for a reason.

Part of the data-modeling set: [retention & history](data-retention-and-history.md) ·
[temporal data](temporal-data.md) · **event sourcing & CQRS** (this file) ·
[event replay & rebuild](event-replay-and-projection-rebuild.md) ·
[event schema evolution](event-schema-evolution.md) ·
[message delivery](message-delivery-semantics.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [State thinking loses the reason](#state-thinking-loses-the-reason)
- [Event sourcing](#event-sourcing)
- [The hidden debt: replay cost](#the-hidden-debt-replay-cost)
- [Snapshots](#snapshots)
- [CQRS](#cqrs)
- [Materialized views are not caches](#materialized-views-are-not-caches)
- [Migrating from CRUD](#migrating-from-crud)
- [When not to do this](#when-not-to-do-this)
- [Review checklist](#review-checklist)

## The anti-pattern

```sql
UPDATE accounts SET balance = 500 WHERE id = :id;
```

The row is correct and the reason is gone. Was that a deposit, a refund, a manual correction by
support, a fee reversal, or a fraud clawback? Was it one movement or four? The number survives; the
story does not.

The opposite anti-pattern, and it is just as common in teams that read one article about event
sourcing:

```python
# Every read rebuilds from the beginning of time.
def get_balance(account_id):
    events = event_store.load_all(account_id)      # 400,000 events after two years
    return reduce(apply_event, events, Account())  # 3 seconds, every request
```

Correct, auditable, and unusable. This is the failure mode that makes teams abandon event sourcing
and conclude it does not work in production.

## Why development hides it

For state thinking, the missing "why" is invisible until someone asks — usually risk, support, or
an auditor months later, and by then the reason was never recorded.

For event sourcing, the replay cost is a function of **event count**, which in development is
dozens. A projection rebuilt from 50 events is instant; the same code over two years of production
history is a timeout. The design looks validated when the only thing tested was a small dataset.

## State thinking loses the reason

A CRUD row is a photograph of the present. Business questions are rarely about the present:

- Why is this balance what it is?
- What sequence of actions produced this state?
- Replay the last month with a corrected fee rule — what should the balance have been?
- The support agent says they never applied that credit. Who did?

An [audit log](data-retention-and-history.md) can answer *who and when*. Only events make state
**derivable** — the state stops being the truth and becomes a computed consequence of the truth.

## Event sourcing

Store the immutable sequence of things that happened; derive state by folding over it.

```
AccountOpened{id, owner}
MoneyDeposited{amount: 300, ref: "txn_1"}
FeeCharged{amount: 5, rule: "monthly_v2"}
MoneyWithdrawn{amount: 100, ref: "txn_2"}
                    ↓ fold
              balance = 195
```

Properties that make an event store trustworthy:

- **Events are facts in the past tense.** `MoneyDeposited`, not `DepositMoney`. A command may be
  rejected; an event already happened.
- **Append-only, never mutated.** Revoke `UPDATE`/`DELETE` at the database level. A mistake is
  corrected by a new compensating event, exactly as in [payment-ledger](payment-ledger.md).
- **Ordered per aggregate**, with a monotonic sequence number. Order across aggregates is usually
  not guaranteed and should not be assumed.
- **Optimistic concurrency on append** — write expecting version N, fail if another writer got
  there. See [locking](optimistic-vs-pessimistic-locking.md).
- **Versioned schemas.** Events live forever, so your five-year-old `V1` event must still be
  readable by today's code. This obligation never goes away and is the most underestimated cost —
  see [event schema evolution](event-schema-evolution.md).

## The hidden debt: replay cost

Event sourcing is frequently sold as free history. The bill is replay.

Rebuild cost grows linearly and without bound with event count. A hot aggregate — a wallet, a
popular product's inventory — accumulates events forever, and read latency grows with its age.
Nothing about this appears until real history exists.

This is not a reason to avoid event sourcing. It is a reason to treat snapshots as **mandatory
infrastructure, not an optimization to add later.**

## Snapshots

Periodically persist the folded state plus the version it reflects:

```
snapshot(account_id, version=1000, state={balance: 195})
+ events 1001..1004
= current state
```

Load the latest snapshot, then apply only events after it.

- **Trigger by event count** (every N events) rather than by time — it bounds worst-case replay.
- **Snapshots are a cache, never the source of truth.** They must be deletable and fully
  rebuildable from events. If a snapshot is the only place a value exists, you no longer have event
  sourcing.
- **Version them with your state schema.** A changed state shape invalidates old snapshots; they
  must be discardable and regenerable, which is safe precisely because events remain authoritative.

The paradox worth naming: you store complete history and then arrange to skip reading most of it.
That is not a contradiction — history is for *correctness and audit*, snapshots are for *latency*.

## CQRS

Reads and writes want opposite things:

|               | Write model                   | Read model                       |
|---------------|-------------------------------|----------------------------------|
| Optimized for | Correctness, invariants       | Query speed                      |
| Shape         | Normalized, aggregate-bounded | Denormalized, query-shaped       |
| Volume        | Lower                         | Often orders of magnitude higher |

One schema serving both is a compromise that degrades as either side grows. CQRS separates them:
commands mutate the write model, and **projections** build read models shaped for specific queries.

Two clarifications that prevent most CQRS mistakes:

- **CQRS is not event sourcing.** They compose well and are independent. You can have CQRS over
  plain CRUD tables, and event sourcing with a single model. Adopting both at once because they
  appeared in the same article is how teams acquire two hard problems instead of one.
- **Read models are eventually consistent.** A user who writes then immediately reads may not see
  their own change — see [consistency-boundaries](consistency-boundaries.md). Design for it: read
  the write model for read-your-writes paths, or make the UI reflect the submitted command
  optimistically. Do not pretend the lag is zero.
- **Commands decide against the write model, never a read model.** A balance check that reads the
  projection approves a second withdrawal while the first is still in flight, because the read
  model has not caught up. Validation that guards money or stock loads the aggregate (or locks the
  row); the projection is for showing, not for deciding.

### One read model per question, and what it costs

Shaping a read model per use case keeps each one simple, and has two costs that only appear at
production cardinality:

- **Update amplification.** One event can touch many read-model rows — a price or NAV change updates
  every holder's portfolio summary. Live lag is fine until that event fires against tens of
  thousands of rows. Measure the fan-out of each event type and the time to converge after the
  widest one, against the view's freshness target.
- **Drift between copies.** Denormalizing a customer's name into three views is deliberate, but each
  copy needs a named owner, the event that updates it, and a periodic check against the source.
  One projector that misses a correction leaves the copies disagreeing indefinitely.

## Materialized views are not caches

The distinction is a **cost shift**, not a speed trick:

|              | Cache                               | Materialized view                           |
|--------------|-------------------------------------|---------------------------------------------|
| Populated    | Lazily, on miss                     | Deliberately, on write or schedule          |
| On absence   | Recompute inline (slow path exists) | The data is simply not there yet            |
| Invalidation | TTL / eviction, best-effort         | Refresh strategy you own                    |
| Authority    | Never authoritative                 | Not authoritative, but the only served copy |

A cache moves cost to the *first* reader after eviction. A materialized view moves it to **write
time or refresh time**, so no reader ever pays it. That matters because read traffic typically grows
far faster than write traffic — precomputing once per write to serve a million reads is the whole
argument.

The trap is treating a materialized view like a cache and shipping without a refresh strategy.
Then it silently serves stale data with no TTL to blame and no miss path to recompute. Every
materialized view needs an explicit answer to: *what refreshes this, how often, and what is the
maximum staleness anyone can observe?* Concurrent refresh (`REFRESH MATERIALIZED VIEW CONCURRENTLY`)
matters too, or the refresh locks out the readers it exists to serve.

## Migrating from CRUD

A CRUD table holds where each entity ended up, not how it got there. A row in `REFUNDED` may have
been one full refund or three partial ones after a failed capture. Converting it into a tidy sequence
of events inferred from the final status **invents history**: the audit trail now shows a clean path
that never happened, and the retries, cancellations and releases a dispute needs are gone.

Start each migrated stream with **one opening event** — `PaymentStateImported{legacy_version, …}` —
that states what the legacy snapshot knew at the cut-over moment. Queries before that point answer
"snapshot only". Then move authority, not rows, in stages:

1. **Baseline the legacy data** — counts, totals per status and currency, known duplicates and
   orphans — so its existing defects are not later blamed on the new path.
2. **Emit facts while legacy stays authoritative**, through an [outbox](message-delivery-semantics.md#outbox-do-not-lose-the-event).
3. **Build shadow projections nobody reads**, and classify every difference as legacy bug, new bug,
   semantic difference or unknown. Unknown goes to zero before going further.
4. **Shadow the commands**: run the new aggregate beside the old logic and compare its decisions —
   with no side effects from the shadow path.
5. **Cut over per entity or tenant** through a control marker that the router reads. After it, the
   legacy table becomes a projection fed from the stream.

Never write both stores in the same request without an atomic boundary: one succeeds, the other
times out, and the next read trusts whichever is stale. Exactly one store is authoritative for each
entity at any moment, and a **writes-to-legacy-after-cutover** metric proves nothing still writes
the old one.

## When not to do this

Each of these patterns adds permanent complexity. Skip them when:

- **The entity has no interesting history.** User preferences, feature flags, product catalogue
  entries. "It changed and now it's different" is a complete account — use CRUD.
- **Nobody will ask "how did this happen?"** If no audit, dispute, or replay requirement exists, an
  [audit log](data-retention-and-history.md) is far cheaper than event sourcing and answers the
  questions you actually get.
- **The team cannot carry the operational load.** Event schema versioning, projection rebuilds,
  snapshot management, and eventual-consistency debugging are ongoing work, not a one-time build.

The honest default: **CRUD plus an audit log** covers most systems. Reach for event sourcing where
state is *derived from money or legally significant movements* — ledgers, wallets, trading positions
— which is exactly where [payment-ledger](payment-ledger.md) applies.

## Review checklist

- [ ] The choice between CRUD, audit log, and event sourcing is justified by a real question
- [ ] Events are named in the past tense and stored append-only, with `UPDATE`/`DELETE` revoked
- [ ] Events are ordered per aggregate with a version, and appends use optimistic concurrency
- [ ] Event schema versioning is planned; old event versions remain readable — see
      [event schema evolution](event-schema-evolution.md)
- [ ] Corrections are compensating events, never edits to stored events
- [ ] Snapshots exist before production history accumulates, triggered by event count
- [ ] Snapshots are rebuildable and never the sole source of a value
- [ ] CQRS read models are recognized as eventually consistent, with read-your-writes handled
- [ ] Commands that guard money or stock validate against the write model, never a projection
- [ ] Each event type's read-model fan-out is known, and convergence after the widest one is tested
- [ ] Denormalized copies have an owner, an updating event and a drift check
- [ ] Migrated streams start with an explicit opening event; no history is inferred from final state
- [ ] Migration has one authoritative store per entity, shadowed commands, and a writes-after-cutover metric
- [ ] Every materialized view has an explicit refresh strategy and a stated maximum staleness
- [ ] Materialized view refresh does not lock out readers
- [ ] Projection rebuild has been exercised, not just assumed to work — see
      [event replay & rebuild](event-replay-and-projection-rebuild.md)

---

*Synthesized from TechCraft's Data Modeling Patterns series parts P7–P10, reconciled against
Distributed Systems part P9
([collection](https://www.patreon.com/techcraft_official)). The event-sourcing "hidden debt" framing,
snapshots-as-mandatory argument, and the materialized-view-as-cost-shift distinction are TechCraft's;
the review structure and checklist are this repository's. The command-versus-projection check,
update amplification and drift, and the CRUD migration section (opening event, shadow and command
shadowing, per-entity authority cut-over, writes-after-cutover metric) come from TechCraft's Event
Sourcing & CQRS parts P4, P11, P27 and P28.*

# Consistency Boundaries

**Rule: decide which invariants must be true *right now*, and let everything else converge.**
Consistency is a budget spent where being wrong is expensive — not a setting turned up everywhere.

Part of the transaction set:
[atomicity & durability](transaction-atomicity-and-durability.md) ·
[isolation](transaction-isolation.md) · [locking](optimistic-vs-pessimistic-locking.md) ·
[distributed transactions](distributed-transactions.md) ·
**consistency boundaries** (this file)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [The C in ACID is not distributed consistency](#the-c-in-acid-is-not-distributed-consistency)
- [Invariants, stated explicitly](#invariants-stated-explicitly)
- [Drawing the boundary](#drawing-the-boundary)
- [Where real systems draw it](#where-real-systems-draw-it)
- [Consistency engineering: four layers](#consistency-engineering-four-layers)
- [Consistency SLIs](#consistency-slis)
- [It is a business decision](#it-is-a-business-decision)
- [Review checklist](#review-checklist)

## The anti-pattern

Two opposite failures, and the second is the one teams congratulate themselves for.

**Everything eventual:**

```python
# One transaction, one service. Balance can go negative and nothing notices.
wallet.balance -= amount
db.commit()
```

Accounts drift negative by millions. Zero database exceptions, zero alerts — the database enforced
every constraint it was given, and it was never given this one.

**Everything strong:**

```python
with distributed_transaction():  # holds locks across five services
    update_balance(...)
    update_ledger(...)
    update_analytics_dashboard(...)  # ← does this need to be atomic with the balance?
    update_recommendation_index(...)
    update_loyalty_points(...)
```

Correct, and unable to scale past a few hundred writes per second. The analytics dashboard being
300 ms stale harms nobody; making it transactional with the balance turns a global system into one
bounded by the slowest participant.

## Why development hides it

One database, one node, no replicas, no partitions — so *everything* is strongly consistent by
accident. There is no observable difference between an invariant you protected deliberately and one
that happened to hold because there was only one writer.

The cost of over-consistency is equally invisible: correctness is preserved, so nothing looks wrong.
The bill arrives as a scaling ceiling much later, when the design is expensive to change.

## The C in ACID is not distributed consistency

The most abused word in the field, meaning two unrelated things:

- **ACID's C** — after a transaction, your *declared constraints* still hold. Nothing more.
- **Distributed consistency (CAP)** — whether replicas agree on a value at a point in time.

A database is an ignorant machine. It does not know a balance must never go negative, that an
order's total must equal the sum of its lines, or that a seat cannot be sold twice. **It enforces
what you declared and nothing you merely intended.**

> A green database is not a living business.

So the review question is never "is the database consistent?" It is *which business rules must be
impossible to violate, and are they actually enforced somewhere the code cannot bypass?*

## Invariants, stated explicitly

An invariant is a statement that must be true at every observable moment:

- `balance >= 0`
- `sum(ledger entries per transaction) = 0`
- `seats sold <= seats available`
- `order.total = sum(order lines)`
- a payment in a terminal state never changes again

For each one, decide **where** it is enforced. In descending order of reliability:

1. **A database constraint** — `CHECK`, `UNIQUE`, `EXCLUDE`, foreign key. Holds against every code
   path, every migration, every manual fix at 2am.
2. **An atomic conditional statement** — `UPDATE ... WHERE balance >= :amount`, with the affected
   row count checked.
3. **A guarded transition under the right isolation level** — see
   [transaction-isolation](transaction-isolation.md).
4. **Application code** — the weakest, because it is bypassed by the next service, script, or
   backfill that writes the same table.

An invariant "enforced" only in application code is a convention, and conventions do not survive
contact with a second writer.

## Drawing the boundary

> A consistency boundary is a safe room. Inside it, everything must be exactly correct. Step
> outside and you are in a public lobby, where information arrives late and you accept convergence
> over time.

Inside the boundary: synchronous, transactional, invariant-enforced, single-writer.
Outside: events, replicas, caches, projections — all allowed to lag.

This maps onto the DDD **aggregate**: the aggregate is the unit within which invariants hold
immediately, and the natural transaction boundary. Cross-aggregate consistency is eventual by
design, not by compromise.

The engineering discipline is keeping the boundary **small**. Every entity pulled inside costs
throughput and availability for every write. Do not spend strong consistency on things that do not
deserve it.

## Where real systems draw it

| Domain         | Inside the boundary (strong)           | Outside (eventual)                                                  |
|----------------|----------------------------------------|---------------------------------------------------------------------|
| **Banking**    | Balance, ledger, settlement            | Marketing campaigns, analytics, segmentation                        |
| **E-commerce** | Inventory, payment status, order state | Recently viewed, search ranking, recommendations, seller dashboards |
| **Trading**    | Holdings, transaction log, settlement  | User profile dashboard, historical charts, campaigns                |

The reasoning is uniform, and it is commercial rather than technical: analytics being 1% wrong is
survivable; a ledger being wrong is a legal and trust event. A customer will not complain that
"recently viewed" lags a second — they will sue if they paid and the system says out of stock.

The banking-app paradox is the clearest illustration: your balance updates instantly while the
spending chart lags. That is not a bug. It is the boundary, drawn on purpose.

## Consistency engineering: four layers

Inconsistency in a distributed system is a property, not a bug — so build the machinery that finds
and repairs it:

**1. Detection** — the most important and most often missing layer. Answers *how would we know the
data is wrong when no infrastructure error occurred?* Monitor **invariants**, not CPU:

```sql
-- Money received but never booked to the ledger. Any row here is an incident.
SELECT count(*)
FROM payments p
         LEFT JOIN ledger l ON l.payment_id = p.id
WHERE p.status = 'SUCCESS'
  AND l.id IS NULL;
```

**2. Reconciliation** — periodically compare independent sources. In finance, a three-way match:
service against service, internal against provider statement, and recorded against actual bank
settlement. Compare line by line, not just aggregates — offsetting errors cancel in a total. See
[payment-reconciliation](payment-reconciliation.md).

**3. Recovery** — repair through tooling, never by hand. Idempotent replay of an event or workflow,
forced state-machine transitions to re-trigger a flow, and prepared compensating scripts. A human
running `UPDATE` in production is not a recovery layer.

**4. Audit** — immutable history of every change, so you can answer *why was the total off by one
cent at 02:00 on the 15th?* Storing the current balance answers nothing; storing the movements that
produced it answers everything. See [payment-ledger](payment-ledger.md).

## Consistency SLIs

Stop saying "fairly consistent" and measure it:

- **MTTD** — mean time to *detect* an inconsistency. If this is 24 hours, you can lose a lot of
  money before reacting. Mature systems target under a minute.
- **MTTR** — once detected, how long until the repair tooling restores correctness.
- **Inconsistency rate** — share of transactions that end up diverged.

These are the numbers that turn consistency from an argument into an engineering target.

## It is a business decision

The same technical failure has different severities, and only the business can say which:

> A network partition drifts 10,000 loyalty points (~$100) and $10,000 of customer money in the
> ledger. Technically identical bugs — inconsistent state after a partial failure. Marketing says
> "reconcile in the morning, send an apology." Finance and Legal join the call immediately, because
> this is audit exposure and banking-relationship risk.

So consistency is not binary and not a database setting. It is a **budget**, allocated where being
wrong is expensive — and the allocation belongs to the business, informed by you. Big tech does not
build systems that never diverge; it decides where divergence is tolerable and invests in detecting
and repairing the rest.

**In review, the question to force into the open:** *what must be true the instant this returns, and
what is allowed to catch up?* A design that has not answered it has usually answered "everything,
immediately" by accident — and will not scale — or "nothing, ever" by accident, and is losing money
silently.

## Review checklist

- [ ] Business invariants are written down explicitly, not implied by code
- [ ] Each invariant is enforced by a constraint or atomic statement, not only application code
- [ ] The consistency boundary is identified: what is synchronous vs what may lag
- [ ] The boundary is as small as it can be; nothing is inside it by default
- [ ] Cross-aggregate/cross-service consistency is eventual by design, with a bounded window
- [ ] Detection queries monitor invariants, not just infrastructure health
- [ ] Reconciliation compares independent sources line by line, not aggregates
- [ ] Repair happens through idempotent tooling, never manual `UPDATE`s
- [ ] An immutable audit trail can reconstruct how a value came to be
- [ ] MTTD, MTTR, and inconsistency rate are measured, with targets
- [ ] Tolerable staleness per data class was agreed with the business, not assumed by engineering

---

*Synthesized from TechCraft's Transaction & Consistency series parts P4 and P18–P20, reconciled
against Distributed Systems part P14
([collection](https://www.patreon.com/collection/2256242)). The safe-room/public-lobby framing, the
three-domain boundary table, the four-layer Consistency Engineering framework, Consistency SLIs, and
the consistency-budget argument are TechCraft's; the review structure and checklist are this
repository's.*

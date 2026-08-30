# Optimistic vs Pessimistic Locking

**Rule: the choice is a bet on how often writes actually collide, and being wrong is expensive in
both directions.** Optimistic under contention becomes a retry storm; pessimistic under load
becomes a queue.

Part of the transaction set:
[atomicity & durability](transaction-atomicity-and-durability.md) ·
[isolation](transaction-isolation.md) · **locking** (this file) ·
[distributed transactions](distributed-transactions.md) ·
[consistency boundaries](consistency-boundaries.md)

For locks that must span processes rather than rows, see
[distributed-locks](distributed-locks.md).

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [The two bets](#the-two-bets)
- [Optimistic locking](#optimistic-locking)
- [The retry storm](#the-retry-storm)
- [Pessimistic locking](#pessimistic-locking)
- [A lock is a debt](#a-lock-is-a-debt)
- [Choosing](#choosing)
- [Review checklist](#review-checklist)

## The anti-pattern

Two failures, opposite in shape.

**Optimistic locking on a hot row:**

```python
for attempt in range(100):                          # unbounded-ish, no backoff
    item = db.query("SELECT stock, version FROM items WHERE id = ?", item_id)
    n = db.execute("UPDATE items SET stock = ?, version = version + 1 "
                   "WHERE id = ? AND version = ?", item.stock - 1, item_id, item.version)
    if n == 1:
        break
```

A flash sale puts 10,000 requests on one row. Almost every attempt loses its version check and
retries immediately, so the database receives a multiple of the real load — all of it doomed. The
correctness is fine; the system is down.

**Pessimistic locking on a hot row:**

```python
with db.transaction():
    item = db.query("SELECT * FROM items WHERE id = ? FOR UPDATE", item_id)
    ...
    payment_gateway.charge(...)      # holding the lock across a network call
```

Now every request queues behind the slowest holder, connections drain waiting, and endpoints that
never touch this row start failing. See [lock-contention](lock-contention.md).

## Why development hides it

Both strategies are correct with one user, and correctness is what tests check. Contention is a
*load* property: at one request per second neither strategy is distinguishable, and at a few
thousand on the same row they fail in completely different ways.

Nothing in a unit test measures conflict rate, which is the single number that decides which
strategy is right.

## The two bets

The distinction is not mechanical, it is a belief about the world:

|                 | Optimistic                                          | Pessimistic                                                   |
|-----------------|-----------------------------------------------------|---------------------------------------------------------------|
| Assumption      | Conflicts are rare                                  | Conflicts are expected                                        |
| Approach        | Trust first, verify at write                        | Trust no one, lock first                                      |
| Cost when wrong | Retry storms, wasted work                           | Queueing, lock contention, deadlocks                          |
| Blocks readers  | No                                                  | Depends on lock mode                                          |
| Best for        | Many rows, low collision (user profiles, documents) | Few hot rows, high collision (flash-sale stock, seat booking) |

## Optimistic locking

Add a `version` column, and make the write conditional on the version you read:

```sql
UPDATE documents SET content = :content, version = version + 1
WHERE id = :id AND version = :expected_version;
```

Zero rows affected means someone else committed first. **Checking the affected row count is the
entire mechanism** — code that ignores it has optimistic locking in name only, and silently loses
updates. See [transaction-isolation](transaction-isolation.md).

What to do on a loss is a product decision, not a technical one:

- **Reload and retry** for a mechanical increment (a counter, a stock decrement)
- **Surface a conflict** when a human's intent is involved — a user editing a stale form should be
  told, not silently overwritten by a retry

Its real advantage: nothing blocks. Readers are never delayed, and a client that dies mid-edit
holds nothing.

## The retry storm

The failure mode that makes optimistic locking dangerous, and the one most implementations miss.

Under contention, losers retry, and their retries collide with each other and with new arrivals.
Load *increases* precisely when the system is already struggling — a positive feedback loop.

Requirements to make retrying safe:

- **Bounded attempts.** Then fail the request. An unbounded loop converts contention into an
  outage.
- **Jittered exponential backoff.** Without jitter, all losers retry in lockstep and collide
  again. Jitter is not a refinement here; it is what breaks the synchronization.
- **Re-run the whole operation**, including the read. Retrying only the write re-applies a decision
  made from a stale snapshot.
- **Watch the conflict rate as a metric.** A rising retry rate is the leading indicator that this
  row has outgrown optimistic locking.

When the conflict rate is high, the answer is usually not "retry better" — it is to stop reading
the value at all and use an atomic conditional update:

```sql
UPDATE items SET stock = stock - 1 WHERE id = :id AND stock > 0;
```

No version, no read, no retry. Most hot-row contention dissolves here.

## Pessimistic locking

Take the lock first and let others wait:

```sql
SELECT * FROM items WHERE id = :id FOR UPDATE;
```

Correct, simple to reason about, and the right call when a collision is likely and its cost is
high — overselling the last unit, double-booking a seat.

The costs are the ones in [lock-contention](lock-contention.md): serialized access to the row,
locks held until commit, queueing that spreads to unrelated endpoints, and deadlocks when two
paths acquire in different orders.

Non-negotiables when using it:

- **Never hold a lock across a network call.** This is the single most common way pessimistic
  locking takes a system down.
- **Set `lock_timeout`.** A bounded failure beats an unbounded stall.
- **Acquire in a deterministic order** (sorted key) to prevent deadlocks.
- **Consider `NOWAIT` or `SKIP LOCKED`.** For worker/queue tables `SKIP LOCKED` is the standard
  answer, so workers take different rows instead of queueing on the same one.

## A lock is a debt

> Locks are borrowed time. You pay them back in latency.

Every lock trades throughput for certainty. That is often the right trade — but it is a trade, and
it should be visible in review. A lock taken "to be safe" without knowing the conflict rate is an
unpriced debt, and it comes due at exactly the traffic level you built the system for.

## Choosing

Reach for **optimistic** when writes to the same row are rare relative to total writes: user
profiles, documents, settings, order records keyed per customer. The common case pays nothing.

Reach for **pessimistic** when the same row is genuinely contended and a lost update is
unacceptable: last-unit inventory, seat allocation, wallet balance under burst.

Reach for **neither** when the operation can be expressed as one atomic conditional statement. That
is faster than both, needs no retry, and is correct at every isolation level — check it first.

## Review checklist

- [ ] The locking strategy matches the expected conflict rate, and that rate is known
- [ ] Single-statement atomic updates were considered before either locking strategy
- [ ] Optimistic writes check the affected row count; a zero count is handled explicitly
- [ ] Retries are bounded, with jittered exponential backoff
- [ ] Retries re-run the whole operation including reads
- [ ] Conflict/retry rate is exposed as a metric
- [ ] Losing a conflict surfaces to the user when human intent is involved
- [ ] No lock is held across a network call
- [ ] `lock_timeout` is set; multi-row locks are acquired in deterministic order
- [ ] Worker/queue tables use `SKIP LOCKED` rather than blocking

---

*Synthesized from TechCraft's Transaction & Consistency series, parts P12–P13
([collection](https://www.patreon.com/collection/2256242)). The trust-vs-paranoia framing, the
retry-storm failure mode, and "a lock is a technical debt" are TechCraft's; the review structure and
checklist are this repository's.*

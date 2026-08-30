# Message Delivery Semantics

**Rule: exactly-once delivery does not exist. You get at-least-once, and you make the *effect*
happen once.** Outbox stops events being lost; Inbox stops duplicates being applied.

Part of the data-modeling set: [retention & history](data-retention-and-history.md) ·
[temporal data](temporal-data.md) · [event sourcing & CQRS](event-sourcing-and-cqrs.md) ·
**message delivery** (this file)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [A queue is for survival, not speed](#a-queue-is-for-survival-not-speed)
- [Why exactly-once is a myth](#why-exactly-once-is-a-myth)
- [Outbox: do not lose the event](#outbox-do-not-lose-the-event)
- [Inbox: do not apply it twice](#inbox-do-not-apply-it-twice)
- [Both, or neither works](#both-or-neither-works)
- [Ordering is the enemy of parallelism](#ordering-is-the-enemy-of-parallelism)
- [Where Inbox still fails](#where-inbox-still-fails)
- [Review checklist](#review-checklist)

## The anti-pattern

**Producer side — publishing outside the transaction:**

```python
with db.transaction():
    payment.status = "succeeded"
    db.save(payment)
queue.publish("payment_succeeded", payment.id)   # ← crash here: event lost forever
```

Or worse, inside it — then a rollback publishes an event for something that never happened.

**Consumer side — trusting delivery to be unique:**

```python
def on_payment_succeeded(event):
    account = db.get(event.account_id)
    account.balance += event.amount      # redelivery credits the money twice
    db.save(account)
```

Both look correct. Both are wrong in ways that only appear under retry.

## Why development hides it

Locally the broker delivers each message once, in order, and nothing crashes between the commit and
the publish. There is no consumer timeout causing redelivery, no rebalance, no at-least-once retry.

The consumer bug is especially well hidden: it is not a crash but a *quiet double-apply*. The
balance is simply wrong, with no error anywhere, and the discrepancy surfaces during reconciliation
weeks later.

## A queue is for survival, not speed

Worth correcting up front, because it changes what you check in review: a message queue does not exist
to make the system faster. It exists so the system **survives** a burst or a downstream outage —
absorbing load the consumer cannot yet handle, and decoupling the producer's availability from the
consumer's.

That reframing has a consequence. If a queue is treated as a speed optimization, nobody monitors depth
or consumer lag, and the queue silently becomes an unbounded buffer of work that will never be done.
Depth and lag are the health metrics; throughput is not.

## Why exactly-once is a myth

The producer sends a message and the broker's acknowledgment is lost in the network. The producer
cannot distinguish "the broker never got it" from "the broker got it and the ack vanished."

- Do not resend → possible **message loss**
- Resend → possible **duplicate**

There is no third option. The same problem repeats between broker and consumer: the consumer
processes a message and dies before acknowledging, so the broker redelivers.

> Exactly-once delivery is largely a myth. Duplicates are not a bug — they are how distributed
> systems actually work.

So every real system chooses **at-least-once delivery** and then makes duplicate *processing*
harmless. What can be achieved is exactly-once **effect**, not exactly-once delivery. Systems
advertising "exactly-once" are doing this internally — deduplication plus atomic offset commits —
not defeating the network.

## Outbox: do not lose the event

State change and event emission must be atomic. Since they cannot span two systems, put both in one
database:

```sql
BEGIN;
  UPDATE payments SET status = 'succeeded' WHERE id = :id AND status = 'pending';
  INSERT INTO outbox (id, event_type, payload, created_at)
       VALUES (:uuid, 'payment_succeeded', :payload, now());
COMMIT;
```

A **relay** then polls undispatched rows and publishes them.

The guarantee: *if the state changed, the event exists.* No window where money moved and nothing was
announced.

What the outbox explicitly does **not** give you: exactly-once publishing. The relay can publish and
die before marking the row dispatched, so it republishes. Deliberately at-least-once — which is why
the consumer side is not optional.

Operational points that get missed: the relay must be **idempotent and single-flighted** (two relays
double-publish — use `FOR UPDATE SKIP LOCKED`, see [lock-contention](lock-contention.md)); the
outbox table needs **pruning** or it grows forever; and **backlog depth must be alerted on**,
because a stalled relay is silent — see [distributed-transactions](distributed-transactions.md).

## Inbox: do not apply it twice

The consumer-side mirror. Record every processed message ID, and — this is the entire point —
record it **in the same transaction as the business effect**:

```sql
BEGIN;
  INSERT INTO inbox (message_id, processed_at) VALUES (:message_id, now());
  -- unique violation here => already processed => roll back and ack
  UPDATE accounts SET balance = balance + :amount WHERE id = :account_id;
COMMIT;
```

The `UNIQUE` constraint on `message_id` is the mechanism. Two concurrent deliveries race, one wins,
the other fails the insert — the same constraint-over-check-then-act logic as
[payment-state-and-idempotency](payment-state-and-idempotency.md).

**Checking a dedupe store outside the transaction defeats it:**

```python
if inbox.contains(msg.id):   # check
    return
process(msg)                 # ← crash here: effect applied, never recorded
inbox.add(msg.id)            # record
```

Crash between the effect and the record, and redelivery applies it again. Check-then-act with a
window, plus a non-atomic write — both failure modes at once. **One transaction, or it does not
work.**

Inbox rows also need pruning, bounded by the broker's maximum redelivery window rather than
arbitrarily.

## Both, or neither works

They solve opposite halves and neither is sufficient alone:

|            | Prevents          | Without it                     |
|------------|-------------------|--------------------------------|
| **Outbox** | Lost events       | State changed, nobody was told |
| **Inbox**  | Duplicate effects | Money credited twice           |

Outbox without Inbox: no events lost, and the at-least-once relay double-applies them.
Inbox without Outbox: no duplicates, and events vanish when the publish fails.

The question to ask in review is not "do we have an outbox?" but **"can an event be lost, and can
an event be applied twice?"** — and they have separate answers.

## Ordering is the enemy of parallelism

Deduplication does not give you order, and order is the guarantee people assume for free.

> Messages are never guaranteed to arrive in the order they were sent.

Why not: multiple partitions, multiple consumers, retries that re-enqueue at the tail, network delay
differences, and redelivery after failure. A `ProfileUpdated{name: "A"}` followed by
`ProfileUpdated{name: "B"}` can land in either order, and last-write-wins on arrival order gives you
"A" — permanently wrong, with no error anywhere.

**The trade nobody states out loud:** strict ordering requires that related messages be processed
**serially**, and serial processing is the opposite of scaling. Total ordering across a topic means
one consumer, one partition — a single point of throughput and a bottleneck you cannot widen.

So the design question is not "how do we guarantee order?" but **"what is the smallest scope that
needs to be ordered?"**

| Scope                                                | Cost                                                                                              |
|------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| Global total order                                   | One partition, one consumer. Does not scale.                                                      |
| **Per-key order** (per account, per order, per user) | Partition by that key — parallel across keys, ordered within one. Almost always the right answer. |
| No order required                                    | Full parallelism                                                                                  |

Per-key partitioning is what Kafka's partition key, SQS FIFO's message group ID, and RabbitMQ's
consistent-hash exchange all exist to provide.

And where you cannot guarantee order, **make handlers order-independent** rather than hoping:

- **Version or sequence numbers** — reject or ignore a message older than the state you hold.
- **Timestamp on the event**, not on arrival — last-write-wins by *event* time, and be aware that
  clock skew across producers makes this approximate.
- **Validate the transition** instead of assigning state — an event that would drive an illegal
  transition is dropped, exactly as in
  [payment-provider-and-webhooks](payment-provider-and-webhooks.md).
- **Model state changes as deltas** where possible (`balance += 100`), since commutative operations
  do not care about order at all.

One more consequence worth checking: **a retried message loses its place.** Even with per-key
ordering, a failure that re-enqueues a message sends it behind messages that were originally after
it. Retry in place (blocking that key) or accept reordering — those are the only two options, and
teams usually assume a third that does not exist.

## Where Inbox still fails

Having both is not a completed proof:

- **Non-transactional side effects.** Sending an email or calling a third-party API is outside your
  database transaction, so the dedupe insert cannot cover it. Crash after sending, before commit,
  and the retry sends again. These need their own idempotency keys at the boundary.
- **Multiple consumers, separate inboxes.** Deduplication is per-consumer. That is correct — each
  consumer must apply the event once — but it means "processed" is not a global fact.
- **Ordering is still not guaranteed.** Deduplication does not order. A stale event can arrive after
  a newer one, so state transitions must still be validated, not assigned — see
  [payment-provider-and-webhooks](payment-provider-and-webhooks.md).
- **Poison messages.** A message that always fails retries forever or lands in a dead-letter queue.
  Both are silent unless dead-letter depth is monitored.
- **Non-idempotent business logic.** `balance += amount` guarded by an inbox is safe; a fulfilment
  workflow that creates a shipment per invocation may not be, if any step escapes the transaction.

## Review checklist

- [ ] Events are emitted via an outbox in the same transaction as the state change
- [ ] Nothing is published inside a transaction that may roll back
- [ ] The relay is single-flighted (`SKIP LOCKED`) and idempotent
- [ ] Outbox backlog depth and relay liveness are alerted on
- [ ] Consumers deduplicate on a message ID with a `UNIQUE` constraint
- [ ] The dedupe insert and the business effect are in **one** transaction
- [ ] No check-then-act dedupe against an external store
- [ ] Non-transactional side effects carry their own idempotency keys
- [ ] Consumers validate state transitions rather than assigning state (ordering is not guaranteed)
- [ ] Dead-letter queue depth is monitored and has an owner
- [ ] Outbox and inbox tables are pruned; inbox retention covers the redelivery window
- [ ] No design assumes exactly-once delivery
- [ ] Required ordering scope is stated; global ordering is not assumed
- [ ] Messages needing order are partitioned by key, not globally serialized
- [ ] Handlers are order-independent where order is not guaranteed (version, event time, or delta)
- [ ] Retry's effect on ordering is understood and accepted, or retried in place
- [ ] Queue depth and consumer lag are monitored as health metrics, not throughput

---

*Synthesized from TechCraft's Data Modeling Patterns parts P11–P12, Backend Internals parts P13–P16,
and Distributed Systems parts P8, P10–P11 ([collection](https://www.patreon.com/techcraft_official)). The
exactly-once-as-myth framing,
the Inbox-insert-in-the-same-transaction mechanism, and "ordering is the enemy of parallelism" are
TechCraft's; the review structure and checklist are this repository's.*

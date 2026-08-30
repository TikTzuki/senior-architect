# Atomicity & Durability

**Rule: `COMMIT SUCCESSFUL` is a promise about your database's local rows, and nothing else.**
It says nothing about the HTTP call you made inside the transaction, and nothing about business
correctness.

Part of the transaction set: **atomicity & durability** (this file) ·
[isolation](transaction-isolation.md) · [locking](optimistic-vs-pessimistic-locking.md) ·
[distributed transactions](distributed-transactions.md) ·
[consistency boundaries](consistency-boundaries.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [What ACID does not promise](#what-acid-does-not-promise)
- [A half transaction is worse than none](#a-half-transaction-is-worse-than-none)
- [Where atomicity actually ends](#where-atomicity-actually-ends)
- [Durability is a promise, not a write](#durability-is-a-promise-not-a-write)
- [Ways durability turns out to be a lie](#ways-durability-turns-out-to-be-a-lie)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
with db.transaction():
    payment = Payment.create(order_id=order.id, amount=order.total, status="succeeded")
    provider.charge(order.total)            # ← external system, outside the transaction's reach
    inventory_service.reserve(order.items)  # ← another one
    order.status = "paid"
```

The dashboard afterwards: CPU at 30%, zero database exceptions, `200 OK` returned, log line reads
`Transaction committed`. And the customer's money is gone with no order to show for it.

Every signal you monitor is green because every signal you monitor is about the database. The
database did exactly what it promised — it committed its own rows atomically. The charge that
happened at the provider is not part of that promise, and neither is the inventory reservation.

If the transaction rolls back after `provider.charge()` returns, the database is pristine and the
customer has still paid.

## Why development hides it

Local stubs never fail, so the rollback path never runs while an external call has already
succeeded. There is no network partition between the charge and the commit, no OOM kill, no
deploy landing mid-transaction.

The deeper reason it survives review: **the code is technically correct.** Nobody reading it can
see the defect, because the defect is in what the transaction's guarantee *excludes* — and that
exclusion is invisible at the call site.

## What ACID does not promise

ACID is an insurance policy on the integrity of data operations. It is not a checklist you satisfy
by using `BEGIN`, and it does not make your logic right.

Specifically, ACID does not protect against:

- **Semantic errors.** Debiting the wrong account, computing the wrong fee, applying a discount
  twice. Each commits perfectly.
- **Anything outside the database.** API calls, queue publishes, file writes, emails. The
  transaction cannot roll them back.
- **Business invariants the database does not know about.** The `C` in ACID means *your declared
  constraints still hold* — not *your business rules are satisfied*. A database has no idea that a
  balance must never go negative unless you tell it. See
  [consistency boundaries](consistency-boundaries.md).

> A green database is not a living business. Database consistency and business consistency are
> different things.

## A half transaction is worse than none

> A failed transaction is usually safe. A half-completed transaction is the real disaster.

A full failure leaves the world as it was, and the customer retries. Nobody is harmed. A partial
success creates an **intermediate state** that no part of the system is designed to interpret:

- Balance decremented, transaction history missing → the money left and cannot be explained
- Payment recorded, order absent → the customer paid for nothing
- Order created, inventory not reserved → you sold something you do not have

These states are worse than an error because they are *silent*. Nothing throws. The system reports
success and the divergence is discovered later by a customer or an accountant.

This is what atomicity buys: not "the operation succeeds" but **"the system is never left
mid-thought."** It is a business-trust requirement expressed as a technical one.

## Where atomicity actually ends

The boundary is the database. Inside it, `WAL` (write-ahead logging) makes atomicity real: changes
are written to a sequential log *before* being applied to data files, so a crash mid-write is
recoverable — on restart, the engine replays committed transactions and discards incomplete ones.

That machinery covers rows in that database. It does not extend one inch past it.

WAL is also why atomicity and durability are the *same* mechanism seen from two sides. The log is
written sequentially — which matters because sequential disk writes are an order of magnitude faster
than random ones, so the log can be flushed synchronously without destroying throughput while data
pages are updated lazily afterwards. Recovery then uses the log twice: **redo** to reapply committed
transactions whose pages had not been written, and **undo** to reverse uncommitted ones. A
**checkpoint** periodically flushes dirty pages and truncates the log, which is what bounds recovery
time — a longer checkpoint interval means faster steady-state writes and a slower restart, and that
trade is your RTO.

So the rule for review: **a transaction may contain database work only.** Move every external
effect outside it:

```python
charge = provider.charge(order.total)      # outside: slow, irreversible, unrollbackable

with db.transaction():                     # inside: local, fast, atomic
    Payment.create(order_id=order.id, charge_id=charge.id, status="succeeded")
    Outbox.insert("payment_succeeded", payload)

# the outbox worker publishes after commit
```

This raises the real question the original code was hiding: what if the charge succeeds and the
commit fails? That is a distributed-transaction problem with distributed-transaction answers —
idempotency keys, the outbox pattern, reconciliation — not something a `BEGIN` can solve. See
[distributed transactions](distributed-transactions.md). The transaction was never making it
atomic; it was only making it slow, and holding locks while it waited. See
[lock-contention](lock-contention.md).

## Durability is a promise, not a write

Durability is the commitment: *I have accepted this change and it will survive, even if this
machine dies in the next instant.*

It is **not** the same as having written data to disk. A `write()` that returns has usually landed
in the OS page cache, not on physical media. Durability requires the write-ahead log to be flushed
and `fsync`'d before the commit is acknowledged — which is why commit latency is dominated by disk
sync, and why `synchronous_commit = off` makes writes fast by making the promise conditional.

In a distributed setting the promise extends further: a commit is durable when enough replicas have
acknowledged it. Acknowledge on the primary alone and a failover loses transactions that were
reported as committed.

## Ways durability turns out to be a lie

- **`fsync` disabled or relaxed.** `synchronous_commit = off` in PostgreSQL, or
  `innodb_flush_log_at_trx_commit` set to 0 or 2 in MySQL, trade a window of committed-but-lost
  transactions for throughput. That is a legitimate choice for some data and a catastrophic one for
  money — the point is that it must be a *decision*, not a default someone copied.
- **Lying hardware.** Consumer SSDs and some virtualized storage acknowledge `fsync` while data
  sits in a volatile cache. A power loss then loses acknowledged commits.
- **Async replication plus failover.** The primary acknowledged, the replica had not received it,
  the primary died. Those transactions are gone, and the application was told they were safe.
- **Backups that were never restored.** A backup you have not restored is not a backup — it is an
  untested assumption. Same for a replica you have never failed over to.

## Review checklist

- [ ] No HTTP call, queue publish, file write, email, or third-party SDK call inside a transaction
- [ ] Transactions contain database work only, and are opened as late as possible
- [ ] External effects are ordered so that a failure cannot leave money moved with no record
- [ ] Cross-system operations use idempotency keys and an outbox, not a wrapping transaction
- [ ] Business invariants are enforced by constraints, not assumed from ACID
- [ ] Partial-failure states are enumerated, and none of them is silent
- [ ] Durability settings (`synchronous_commit`, `innodb_flush_log_at_trx_commit`) are deliberate
- [ ] Replication acknowledgment level matches how much committed data you can afford to lose
- [ ] Failover and backup restore have actually been exercised, not just configured

---

*Synthesized from TechCraft's Transaction & Consistency series, parts P1–P3 and P6
([collection](https://www.patreon.com/collection/2256242)), and reconciled against Database Internals
parts P7 and P10 ([collection](https://www.patreon.com/collection/2115129)). The "transaction
succeeded is a lie" framing, the half-transaction-is-worse argument, the ACID-as-insurance-policy
distinction, and the WAL/redo/undo/checkpoint mechanics are TechCraft's; the review structure and
checklist are this repository's.*

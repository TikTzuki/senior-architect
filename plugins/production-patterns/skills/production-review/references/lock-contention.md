# Locks, Deadlocks & Long Transactions

**Rule: a transaction holds its locks until it commits, so the only reliable way to reduce
contention is to make transactions short.** Never wait on the network inside one.

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [How it fails in production](#how-it-fails-in-production)
- [Deadlock: always the same cause](#deadlock-always-the-same-cause)
- [Keeping transactions short](#keeping-transactions-short)
- [Idle in transaction, and why it is worse than it looks](#idle-in-transaction-and-why-it-is-worse-than-it-looks)
- [Diagnosing](#diagnosing)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
with db.transaction():
    order = db.query("SELECT * FROM orders WHERE id = ? FOR UPDATE", order_id)
    charge = payment_gateway.charge(order.total)  # network call, 200ms - 30s
    db.execute("UPDATE orders SET status='paid', charge_id=? WHERE id=?",
               charge.id, order_id)
    email.send_receipt(order.customer_email)  # another network call
```

The row lock is taken at line 2 and released at commit. Everything between — including two calls
to systems you do not control — is time that lock is held. When the payment gateway is slow, this
transaction holds a database lock for as long as the gateway takes to answer.

The related shape: **any transaction whose duration depends on something other than the database.**
HTTP calls, queue publishes, S3 uploads, `sleep`, waiting on a user, or a loop over a large
collection doing one round trip per item.

## Why development hides it

One transaction at a time means no one is waiting behind your locks, so lock duration is
invisible — it shows up as latency you already attribute to the payment gateway. A locally stubbed
gateway returns in a millisecond, compressing the dangerous window to nothing.

Contention is superlinear in concurrency. At one request per second it is unmeasurable; at a few
hundred it is an outage. There is no gentle warning in between.

## How it fails in production

- **Queueing behind a single slow holder.** Lock waits are FIFO in PostgreSQL. One transaction
  holding a lock for 30 seconds does not slow one other request — it stalls every request that
  wants that row, and they accumulate. The metric that moves is p99 latency on an endpoint that
  looks unrelated.
- **Connection pool exhaustion, one step later.** Each blocked request holds its pooled
  connection while it waits. The pool drains, and requests that never touch the contended row
  start failing to get a connection at all. The reported symptom is "database down"; the cause is
  one slow lock holder.
- **Deadlock under load.** Two orderings that never met in testing meet under concurrency.
- **A blocked query looks like a slow query.** Time in a lock wait is wall-clock time, so it lands
  in your slow-query log looking like a performance problem. Teams add indexes and rewrite SQL for
  days without touching the cause.
- **DDL amplifies it catastrophically.** An `ALTER TABLE` waiting for a lock queues behind the
  current holder — and every subsequent reader queues behind the `ALTER`, because lock requests do
  not overtake each other. One long transaction plus one migration takes down all reads on the
  table.

## Deadlock is not a bug

Worth stating plainly, because teams treat a deadlock as a defect to eliminate: **a deadlock is the
price of concurrency, and the database resolving it is correct behaviour.** The engine detects the
cycle and kills one transaction precisely so the other can proceed — without that, both would wait
forever.

So the goal is not zero deadlocks. It is (a) reduce their frequency by ordering lock acquisition,
and (b) **always retry the victim**, because the database has already rolled it back cleanly. Code
that surfaces `40P01` to the user as an error is treating a survivable event as a failure.

## Deadlock: always the same cause

Two transactions acquire the same locks in opposite orders:

```
T1: lock account A ──▶ wants B
T2: lock account B ──▶ wants A     both wait forever; the database kills one
```

Every deadlock is this, however it is dressed up. The fix is correspondingly simple:

**Acquire locks in a consistent, globally defined order.** For a transfer between accounts, lock
the lower ID first regardless of which is sender:

```python
first, second = sorted([from_id, to_id])
db.execute("SELECT ... FROM accounts WHERE id IN (?,?) ORDER BY id FOR UPDATE", first, second)
```

Sources of accidental inconsistent ordering worth checking in review:

- A multi-row `UPDATE ... WHERE id IN (...)` — row order follows the plan, not your list. Add
  `ORDER BY` to a preceding `SELECT ... FOR UPDATE` to pin it.
- Foreign keys taking locks on the parent row you did not mention.
- A trigger touching a second table you did not know about.
- Two code paths that update the same pair of tables in opposite order.

Deadlocks cannot be eliminated entirely, so **retry on `40P01` regardless** — the database has
already rolled one transaction back, and re-running it usually succeeds.

## Keeping transactions short

The single highest-value rule: **do the slow work outside, the database work inside.**

```python
charge = payment_gateway.charge(order_total)  # outside: slow, no locks held

with db.transaction():  # inside: fast, deterministic
    db.execute("UPDATE orders SET status='paid', charge_id=? WHERE id=? AND status='pending'",
               charge.id, order_id)

queue.publish("send_receipt", order_id)  # outside: after commit
```

This introduces the question the original code was avoiding: what if the charge succeeds and the
update fails? That is a real distributed-systems problem, and the honest answers are an
idempotency key on the charge plus a reconciliation job, or the outbox pattern — not holding a
database lock across the gateway call. The lock never made it atomic; it only made it slow.

Also:

- **Publish to queues after commit, not inside.** A message published inside a transaction that
  later rolls back has been sent for an event that did not happen. Consumers are usually fast
  enough to receive it before the transaction commits, too.
- **Set `lock_timeout` and `statement_timeout`.** A bounded failure beats an unbounded stall.
  `SELECT ... FOR UPDATE NOWAIT` or `SKIP LOCKED` where the workload allows — `SKIP LOCKED` is the
  standard way to build a queue table without workers blocking each other.
- **Batch bulk updates.** One transaction updating a million rows holds a million locks and blocks
  everything. Chunk it, committing between batches.
- **Do not hold a transaction across a user interaction.** Ever.

## Idle in transaction, and why it is worse than it looks

A connection that has begun a transaction and is doing nothing is the worst state in the system.
It holds every lock it has taken *and* pins the MVCC snapshot horizon.

The second part is the one that surprises people. PostgreSQL cannot vacuum row versions still
visible to any open transaction. One connection idle in transaction for hours prevents cleanup of
dead tuples across the **entire database**, not just the tables it touched. Tables bloat, planner
statistics drift, sequential scans get slower, and disk fills — with no lock contention visible
anywhere to explain it.

Common causes: an ORM opening a transaction on first query and holding it for the request; an
exception path that skips rollback; a connection returned to the pool without commit or rollback;
an interactive session someone left open.

Set `idle_in_transaction_session_timeout` and let the database kill these. It is a seatbelt, not a
fix, but it converts a slow database-wide degradation into one loud failure.

## Diagnosing

```sql
-- PostgreSQL: who is blocking whom, right now
SELECT pid,
       state,
       wait_event_type,
       wait_event,
       now() - xact_start AS txn_age,
       left(query, 80)
FROM pg_stat_activity
WHERE state <> 'idle'
ORDER BY xact_age DESC NULLS LAST;

-- The blocking chain
SELECT pid, pg_blocking_pids(pid), left(query, 60)
FROM pg_stat_activity
WHERE cardinality(pg_blocking_pids(pid)) > 0;
```

`pg_locks` joined to `pg_stat_activity` gives the full picture. On MySQL,
`performance_schema.data_lock_waits` and `SHOW ENGINE INNODB STATUS` — the latter prints the last
deadlock with both transactions' lock sets, which is usually enough to spot the ordering.

**Alert on transaction age**, not just query duration. The longest-running transaction is a
leading indicator for both contention and bloat, and it is one cheap query.

## Review checklist

- [ ] No HTTP call, queue publish, file upload, sleep, or user interaction inside a transaction
- [ ] Multi-row locks are acquired in a deterministic order (sorted key, explicit `ORDER BY`)
- [ ] Deadlocks (`40P01`) are retried with the full transaction re-run
- [ ] `lock_timeout` and `statement_timeout` are set; `idle_in_transaction_session_timeout` too
- [ ] Bulk updates are batched, committing between chunks
- [ ] Queue publishes happen after commit, or via an outbox
- [ ] Transactions are opened as late and closed as early as possible
- [ ] Every path — including exceptions — commits or rolls back before returning the connection
- [ ] Worker/queue tables use `FOR UPDATE SKIP LOCKED` rather than blocking
- [ ] Long-running transaction age is monitored and alerted on

---

*Originally written from established practice, then reconciled against TechCraft's Database
Internals series, parts P6, P9 and P17
([collection](https://www.patreon.com/collection/2115129)). The "deadlock is the price of
concurrency, not a bug" framing comes from that series; MVCC bloat and vacuum are expanded in
[storage-engines](storage-engines.md). The diagnostics and checklist are this repository's.*

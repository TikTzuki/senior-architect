# Transaction Isolation

**Rule: a read-modify-write split across two statements is a race condition unless you have
explicitly told the database to prevent it.** Wrapping it in a transaction does not.

Part of the transaction set:
[atomicity & durability](transaction-atomicity-and-durability.md) ·
**isolation** (this file) · [locking](optimistic-vs-pessimistic-locking.md) ·
[distributed transactions](distributed-transactions.md) ·
[consistency boundaries](consistency-boundaries.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Why it survives code review](#why-it-survives-code-review)
- [What each isolation level still permits](#what-each-isolation-level-still-permits)
- [The four anomalies, concretely](#the-four-anomalies-concretely)
- [Fixes, in order of preference](#fixes-in-order-of-preference)
- [Three fixes that do not work](#three-fixes-that-do-not-work)
- [Retry is not optional](#retry-is-not-optional)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
with db.transaction():
    account = db.query("SELECT balance FROM accounts WHERE id = ?", id)  # reads 100
    if account.balance >= amount:  # checks 100 >= 80
        db.execute("UPDATE accounts SET balance = ? WHERE id = ?",
                   account.balance - amount, id)  # writes 20
```

Two concurrent withdrawals of 80 from a balance of 100: both read 100, both pass the check, both
write 20. The account has paid out 160 and shows 20.

The transaction is real and it committed. Transactions give atomicity — all-or-nothing — not
mutual exclusion. Under the default isolation level, nothing here serializes the two callers.

The shape to recognize in review: **read a value, decide in application code, write a value
derived from what was read.** Check-then-insert, check-then-update, increment-a-counter,
reserve-if-available, and allocate-next-number are all this pattern.

## Why development hides it

Requests arrive one at a time. There is no second transaction to interleave with, so every
read-modify-write is trivially serial and every test passes. The bug requires two callers inside
the same window — microseconds wide — which is common at production traffic and essentially
unreachable by hand.

It also survives code review, because the code is correct when read top to bottom. The defect is
in what *another* transaction may do between line 2 and line 4, which is not visible at the site
of the bug.

## Why it survives code review

Worth separating from "development hides it," because this class of bug clears every gate a team
normally trusts:

- **It passes code review.** Read top to bottom, the code is correct. The defect lives in what
  *another* transaction may do between two of its lines — which is not visible at the site of the
  bug.
- **It passes QA and automated tests.** Test suites assert on outcomes of sequential operations.
  Reproducing a lost update needs two callers inside a window microseconds wide, which almost no
  test harness arranges.
- **It produces no error.** No exception, no failed request, no log line. The write that vanished
  simply is not there.

So the only place it can be caught is design review, by recognizing the *shape* — read a value,
decide in application code, write a value derived from what was read.

## What each isolation level still permits

| Level            | Dirty read | Non-repeatable read | Phantom | Lost update | Write skew |
|------------------|------------|---------------------|---------|-------------|------------|
| READ UNCOMMITTED | yes¹       | yes                 | yes     | yes         | yes        |
| READ COMMITTED   | no         | yes                 | yes     | yes         | yes        |
| REPEATABLE READ  | no         | no                  | no²     | no³         | yes        |
| SERIALIZABLE     | no         | no                  | no      | no          | no         |

¹ PostgreSQL has no true READ UNCOMMITTED; it behaves as READ COMMITTED.
² The SQL standard permits phantoms at REPEATABLE READ. PostgreSQL and MySQL InnoDB both prevent
them, by snapshot isolation and next-key locking respectively.
³ Prevented *within* the database — a conflicting write aborts or blocks. An application-level
read-modify-write across two statements can still lose an update at READ COMMITTED.

**The defaults differ between engines, and this is a real portability trap:**

- PostgreSQL, Oracle, SQL Server: **READ COMMITTED**
- MySQL InnoDB: **REPEATABLE READ**

Code that is correct on MySQL can be wrong on PostgreSQL for no reason visible in the code.
Check what your connection actually uses; ORMs and poolers sometimes set it too.

## The four anomalies, concretely

**Lost update.** Two transactions read the same row, both compute a new value from it, both
write. The second overwrites the first, which vanishes with no error. The withdrawal example
above.

**Non-repeatable read.** A transaction reads a row twice and gets different values, because
another transaction committed in between. At READ COMMITTED each *statement* gets a fresh
snapshot, so a long transaction that reads a row, does work, and re-reads it can see it change
underneath.

**Phantom read.** A transaction runs a range query twice and the second run returns rows that
did not exist before. `SELECT COUNT(*) FROM bookings WHERE room = 5 AND day = '...'` returning 0,
then 1, breaks any "insert only if none exist" logic built on it.

The distinction that matters: the other anomalies are about a *row*, phantoms are about a *set*.
Most business invariants are set-level ("no overlapping bookings", "at most N seats sold"), so
locking the rows you read cannot protect them — the offending row does not exist yet. Engines
prevent phantoms differently: PostgreSQL through snapshot isolation (and SSI at SERIALIZABLE),
MySQL InnoDB through **gap locks and next-key locks**, which lock the *range* rather than existing
rows. That is also why InnoDB deadlocks sometimes involve rows a statement never mentioned.

**Write skew.** The subtle one, and the reason SERIALIZABLE exists. Two transactions read an
overlapping set, each checks an invariant that currently holds, each writes to a *different* row.
Neither write conflicts, both commit, and together they break the invariant.

> Two doctors are on call. Each independently requests leave. Each transaction reads
> `COUNT(on_call) = 2`, confirms "at least one will remain," and updates its own row. Both
> commit. Zero doctors are on call.

No row was written twice, so nothing detects a conflict. **REPEATABLE READ does not prevent
this.** Only SERIALIZABLE, or an explicit lock on the rows the decision was based on, does.

## Fixes, in order of preference

**1. Make it one atomic statement.** The best fix removes the window entirely by letting the
database do the read and write together:

```sql
UPDATE accounts
SET balance = balance - :amount
WHERE id = :id
  AND balance >= :amount;
```

Then **check the affected row count** — zero means insufficient funds. This is correct at every
isolation level and needs no locking or retry. Most read-modify-write races collapse into this.

**2. Let a constraint enforce it.** A `UNIQUE` index makes duplicate-insert races impossible; the
loser gets a constraint violation to handle. `CHECK (balance >= 0)` makes an overdraft
unrepresentable. A PostgreSQL `EXCLUDE` constraint with a range type prevents double-booking
across overlapping intervals. A constraint holds regardless of which code path writes, including
the migration script and the manual fix at 2am — this is its real advantage over application
checks.

**3. Lock the rows you read.** When the decision genuinely needs application logic:

```sql
SELECT balance
FROM accounts
WHERE id = :id FOR UPDATE;
```

Other transactions block until you commit. Correct, but serializes access to that row, and holds
the lock for the rest of the transaction — so keep it short. `FOR UPDATE` on a range does **not**
prevent phantoms in the general case; new rows can still appear. For write skew you must lock the
rows the *decision* depends on, which is easy to get wrong.

**4. Optimistic concurrency.** Better under low contention, since nothing blocks. The choice
between this and locking, and the retry-storm failure mode it brings, is covered in
[locking](optimistic-vs-pessimistic-locking.md):

```sql
UPDATE documents
SET content = :content,
    version = version + 1
WHERE id = :id
  AND version = :expected_version;
```

Zero rows affected means someone else won; reload and retry or surface a conflict. This is also
the honest way to handle "user edited a stale form."

**5. SERIALIZABLE.** The only thing that prevents write skew without hand-placed locks.
PostgreSQL's SSI detects conflicts at commit time and aborts one transaction. Correct by
construction — but it *requires* the retry loop below, and it is not free under contention.

## Three fixes that do not work

Each of these is offered regularly and none of them prevents a lost update:

**"Just wrap it in a transaction."** Transactions give atomicity, not mutual exclusion. Both
racing transactions commit successfully — that is precisely the problem.

**"The database handles it."** At the default level it does not. PostgreSQL, Oracle, and SQL
Server default to READ COMMITTED, where an application-level read-modify-write can lose an update
with no error. The database is behaving exactly as specified.

**"I'll use an application-level lock."** A `synchronized` block, a `Lock` object, or a
process-local mutex serializes callers *within one process*. Run two instances behind a load
balancer — which every production deployment does — and the guarantee evaporates, while the code
still looks protected. This one is especially dangerous because it works in staging on a single
instance.

If you need a lock rather than a constraint or an atomic statement, it must be in the database or a
distributed lock service — see [locking](optimistic-vs-pessimistic-locking.md) and
[distributed-locks](distributed-locks.md).

## Retry is not optional

At REPEATABLE READ and SERIALIZABLE the database will abort transactions to preserve correctness.
This is normal operation, not an outage. Code that does not retry converts a correctness
mechanism into user-visible 500s.

Retry on:

- `40001` serialization_failure
- `40P01` deadlock_detected

with a bounded attempt count and jittered backoff. **The entire transaction must re-run**,
including the reads — retrying only the failed statement re-applies a decision made from a
snapshot that has been invalidated, which is precisely the bug the abort prevented.

Retry only these codes. A constraint violation or a syntax error will fail identically forever.

## Review checklist

- [ ] No read-modify-write split across statements without a lock, a version check, or SERIALIZABLE
- [ ] Conditional updates use `WHERE` guards and check the affected row count
- [ ] Invariants that must always hold are enforced by a database constraint, not only in code
- [ ] Uniqueness relies on a `UNIQUE` index, not a preceding `SELECT` to check existence
- [ ] The isolation level is known and asserted, not inherited by accident
- [ ] Serialization failures and deadlocks are retried, with the whole transaction re-run
- [ ] Retry is bounded and jittered; non-retryable errors are not retried
- [ ] Multi-row invariants ("at least one", "no overlap") are held by SERIALIZABLE, an `EXCLUDE`
  constraint, or an explicit lock — not by a `COUNT(*)` check
- [ ] Transactions hold locks for as little time as possible
- [ ] No reliance on process-local locks (`synchronized`, mutex) for cross-request correctness
- [ ] Set-level invariants are protected by range/predicate locking, SERIALIZABLE, or a constraint

---

*Originally written from established practice, then reconciled against TechCraft's Transaction &
Consistency series, parts P5 and P7–P11
([collection](https://www.patreon.com/collection/2256242)). The "survives code review" framing, the
three non-fixes, and the row-versus-set distinction for phantoms come from that series; the
anomaly tables, fix ordering, and checklist are this repository's.*

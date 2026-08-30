# Connection Pools & Latency

**Rule: a database does not scale with connection count, and a 10 ms query does not make a 10 ms
API.** Latency is the whole pipeline, and round trips dominate it.

Part of the storage set: [storage engines](storage-engines.md) ·
[indexes & query plans](indexes-and-query-plans.md) · [replication & sharding](replication-and-sharding.md) ·
[session consistency](session-consistency.md) · **connection pools & latency** (this file)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Connections are not free](#connections-are-not-free)
- [Bigger pools make it worse](#bigger-pools-make-it-worse)
- [Sizing a pool](#sizing-a-pool)
- [Pool exhaustion is a cascade](#pool-exhaustion-is-a-cascade)
- [Where the other 290 ms went](#where-the-other-290-ms-went)
- [Move the computation to the data](#move-the-computation-to-the-data)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
# "Traffic doubled, so double the pool."
POOL_SIZE = 500          # across 20 app instances = 10,000 connections
```

And the diagnostic dead end that accompanies it:

```
Slow query log:  SELECT ... FROM orders WHERE id = ?   →  9.8 ms   ✅
API p99:                                                  310 ms   ❌
```

Every query is fast. The endpoint is slow. Staring at the slow query log will never explain it,
because the time is not in the queries.

## Why development hides it

One user means one connection and no queue. The pool is never saturated, so its size is irrelevant
and any number works.

Latency hides differently: on localhost a round trip is ~0.05 ms, so 60 sequential queries cost
3 ms and look free. The same 60 round trips at 1 ms across a network are 60 ms, and at 5 ms to
another AZ they are 300 ms. **The code did not change — the distance did.**

## Connections are not free

The thread-side view of the same constraint is in
[server-concurrency-model](server-concurrency-model.md): a pooled thread blocked on I/O and a pooled
connection held open are the same resource-exhaustion shape.

A database connection is not a cheap handle. In PostgreSQL it is an OS **process**; in MySQL a
thread. Each carries several megabytes of private memory, and every one participates in internal
bookkeeping — lock tables, snapshot visibility, and scheduling.

> A database does not scale with the number of connections.

Past a modest count, throughput *falls* as connections rise: the same CPUs now context-switch
between far more workers, contend on internal latches, and thrash cache. The database spends its
time coordinating instead of executing.

This is why a **pool** exists — borrow and return a small set of long-lived connections rather than
creating and destroying them. Connection setup (TCP, TLS, auth, session state) costs milliseconds,
which is significant when the query costs one.

## Bigger pools make it worse

The counterintuitive result that matters most in review: **for a CPU-bound database, a smaller pool
is usually faster.**

With 8 cores, 8 concurrent queries use the machine fully. 500 concurrent queries do not run 60×
faster — they run the same total work with 60× the context switching, lock contention and memory
pressure, and every individual query gets slower. Throughput plateaus, then declines, while latency
climbs across the board.

Queueing in the pool is not the enemy. **A short queue in front of a healthy database beats a long
queue inside a sick one**, because a saturated database slows every query, including the ones that
were already running.

## Sizing a pool

Start from the database's capacity, not the app's concurrency:

```
pool_size  ≈  (core_count × 2) + effective_spindle_count
```

For a typical 8-core server on SSD, that lands near 16–20 — far below what most teams configure.
Then remember pools are **per instance**: 20 app instances × 20 connections = 400 connections at the
database, which must fit under `max_connections` with headroom for admin sessions and replication.

- **Use a connection proxy** (PgBouncer, ProxySQL, RDS Proxy) when many instances or serverless
  functions must share a small server-side pool. Transaction-mode pooling multiplexes far more
  clients onto few connections — with the caveat that session state, prepared statements and
  advisory locks behave differently.
- **Separate pools by workload.** Give reports and background jobs their own small pool so a slow
  analytical query cannot starve request traffic — see
  [circuit-breakers-and-bulkheads](circuit-breakers-and-bulkheads.md).
- **Set an acquisition timeout.** Failing fast when the pool is exhausted is better than every
  request hanging.

## Pool exhaustion is a cascade

The failure is never contained to the slow thing:

```
one slow query (or a lock wait, or a transaction holding a connection across an HTTP call)
      ↓ connections held longer than usual
pool exhausted
      ↓ every request waits to acquire, including trivial ones
health check can't get a connection  →  instance marked unhealthy  →  removed
      ↓ its traffic shifts to remaining instances
their pools exhaust  →  full outage from one slow query
```

Two consequences for review. First, **never hold a pooled connection across a network call** — this
is the same rule as [lock-contention](lock-contention.md), and pool exhaustion is how it usually
manifests. Second, **health checks should not depend on the same pool as request traffic**, or a
saturated pool takes down healthy instances.

## Where the other 290 ms went

A request's latency is the sum of every step, and the query is often the smallest:

| Contributor                            | Typical cost                                       |
|----------------------------------------|----------------------------------------------------|
| Query execution                        | the number in your slow log                        |
| Network round trip, app ↔ database     | 0.5–2 ms same AZ; 5–20 ms cross-AZ or cross-region |
| **× number of sequential queries**     | the actual problem                                 |
| Connection acquisition                 | ~0 when warm, unbounded when the pool is exhausted |
| Result serialization / deserialization | grows with row and column count                    |
| ORM hydration into objects             | often exceeds query time on large result sets      |
| TLS, proxy hops, service mesh          | per hop, per call                                  |

> Latency is not query time. It is the sum of every step in the system.

The multiplier is **sequential round trips**. 60 queries at 10 ms each is 600 ms of waiting even
though each query is "fast" — and this is exactly the N+1 shape from
[indexes-and-query-plans](indexes-and-query-plans.md), where each individual query is properly
indexed and the *count* is the defect.

It gets worse across services: an N+1 inside a service call that is itself inside a loop multiplies
round trips by the product, not the sum. Latency is the one cost that compounds with distance, and
distance is a deployment decision made after the code was written.

Two more items that hide in plain sight. **Serialization**: `SELECT *` returning wide rows or large
JSON columns spends real time encoding, transferring and hydrating data the caller discards —
another reason page-width matters, see [storage-engines](storage-engines.md). And **fetching to
count or filter in application code**: pulling 100,000 rows to compute a sum moves the whole dataset
across the wire to do work the database would have done in one pass.

## Move the computation to the data

The general fix is to reduce round trips and let the database do set work:

- **Batch.** `WHERE id = ANY(:ids)` instead of a query per id. One round trip.
- **Aggregate in SQL.** `COUNT`, `SUM`, `GROUP BY` server-side rather than in application code.
- **Use joins or CTEs** to express in one statement what a loop expresses in N.
- **Paginate with keyset**, not large `OFFSET` — deep offsets read and discard everything skipped.
- **Select the columns you need**, so serialization and hydration cost what the data is worth.
- **Insert and update in bulk** rather than row at a time.
- **Assert query counts in tests** for hot endpoints. It is the only reliable way to catch round-trip
  regressions, since none of them look wrong in code review.

## Review checklist

- [ ] Pool size is derived from database cores, not from request concurrency
- [ ] Total connections across all instances fit under `max_connections` with headroom
- [ ] A connection proxy is used where many instances or serverless functions connect
- [ ] Background jobs and reports use a separate pool from request traffic
- [ ] Connection acquisition has a timeout; exhaustion fails fast rather than hanging
- [ ] Health checks do not consume the request pool
- [ ] No pooled connection is held across an HTTP call, queue publish, or sleep
- [ ] Hot endpoints assert query counts in tests
- [ ] Loops do not issue per-item queries; batching or joins are used
- [ ] Aggregation happens in SQL, not by fetching rows into the application
- [ ] Pagination is keyset-based on large tables
- [ ] Wide `SELECT *` on hot paths is justified against serialization and hydration cost
- [ ] Cross-AZ or cross-region round trips are counted, not assumed negligible

---

*Synthesized from TechCraft's Database Internals parts P18–P20
([collection](https://www.patreon.com/collection/2115129)) and Backend Internals part P5. The "a database does not scale
with
connections" framing, the smaller-pool-is-faster result, and "latency is the sum of every step, not
query time" are TechCraft's; the review structure and checklist are this repository's.*

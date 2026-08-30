# Replication & Sharding

**Rule: a replica is a different point in time, and a shard key is a decision you cannot undo.**
Reading from a replica means accepting stale data; choosing a shard key wrong means rewriting the
data layer.

Part of the storage set: [storage engines](storage-engines.md) ·
[indexes & query plans](indexes-and-query-plans.md) · **replication & sharding** (this file) ·
[session consistency](session-consistency.md) · [connection pools & latency](connection-pools-and-latency.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Replication lag is not an error state](#replication-lag-is-not-an-error-state)
- [Read-your-writes](#read-your-writes)
- [More replicas can mean more lag](#more-replicas-can-mean-more-lag)
- [Replication is not a backup](#replication-is-not-a-backup)
- [Synchronous vs asynchronous](#synchronous-vs-asynchronous)
- [Failover loses data, by design](#failover-loses-data-by-design)
- [Sharding: the key is the architecture](#sharding-the-key-is-the-architecture)
- [Hot shards](#hot-shards)
- [What you give up](#what-you-give-up)
- [CAP, concretely](#cap-concretely)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
# "Reads go to replicas, that's the standard scaling pattern."
user_service.create(user)                    # writes to primary
profile = read_replica.get_user(user.id)     # ← may not exist yet
return render(profile)                       # 404 on a user just created
```

And the sharding equivalent, decided in an afternoon:

```python
shard = hash(order.id) % 16      # even distribution, looks ideal
```

Even distribution is not the goal — **query alignment** is. Every "list this customer's orders"
query now hits all 16 shards, because orders for one customer are spread across all of them.

## Why development hides it

There is one database. No replica, so lag is exactly zero and read-after-write always works. No
shards, so every join and transaction is local and free.

Both failures appear only after an infrastructure change that arrives with the reassuring framing of
"just adding replicas" or "just sharding." Neither is just anything: both change the consistency
model the application code was written against.

## Replication lag is not an error state

A replica applies changes from the primary's log after the primary committed them. That interval is
**normal operation**, not a fault. It is typically milliseconds, and it becomes seconds or minutes
during bulk writes, index builds, long transactions on the primary, or network pressure.

> If you read from a replica, you may see old data while the primary already has the new value.

So sending reads to a replica is a decision about which data may be stale, and it belongs in the
[consistency boundary](consistency-boundaries.md) — not a transparent performance win.

**Route by tolerance, not by verb.** "Reads to replicas" is too crude a rule:

| Data                                                                | Route               |
|---------------------------------------------------------------------|---------------------|
| Balance, inventory, payment status, anything about to be decided on | **Primary**         |
| Analytics, reports, dashboards, search, recommendations             | Replica             |
| A user's view of something they just changed                        | Primary (see below) |

**Monitor lag as a first-class signal** and degrade deliberately: if lag exceeds a threshold, route
back to the primary rather than serving data of unknown staleness.

## Read-your-writes

The single most common replica bug, and the most user-visible: a user updates their profile, is
redirected, reads from a replica that has not caught up, and sees the old value. They conclude the
save failed and do it again.

Options, in descending robustness:

1. **Route that user's reads to the primary** for a short window after they write (session
   stickiness). Simple and effective.
2. **Track the write position** (LSN/GTID) and require a replica at least that current, else fall
   back to the primary. Precise, more machinery.
3. **Render optimistically from the submitted value** rather than re-reading. Often the best UX
   answer, and it sidesteps the problem.

Read-your-writes is only the first of three per-client guarantees, and fixing it does not fix
the other two — a user can see their own write and still watch the balance move backwards on
the next refresh, or see a reply before the message it answers. When a read path matters to a
user, work through [session consistency](session-consistency.md).

## More replicas can mean more lag

The reflex when the primary is saturated is to add read replicas. Every replica added is
another destination the primary must ship its change stream to, so past some point the
fan-out is itself the constraint: lag rises on *all* replicas, and the change made to buy
throughput widened the staleness window instead.

Two consequences for a review. A plan that adds replicas should say what it expects to happen
to lag, not only to read capacity. And a consistency guarantee that was holding at three
replicas is not automatically still holding at ten.

## Replication is not a backup

They are different mechanisms answering different questions, and conflating them is how
people discover they have no backups.

Replication copies the current state, including a `DROP TABLE`, a bad migration, or a
corrupting write — it will faithfully reproduce your mistake on every replica within
milliseconds. Backups exist to recover a *previous* state, which is the case replication
cannot serve by design.

If the answer to "what if someone deletes the wrong rows" is "we have replicas", that is a gap.
See [schema migrations](schema-migrations.md) for the migration half of the same risk.

## Synchronous vs asynchronous

- **Asynchronous** — the primary commits and acknowledges immediately; replicas follow. Fast, and a
  primary failure can lose acknowledged transactions.
- **Synchronous** — the primary waits for replica acknowledgment before confirming the commit. No
  loss on failover, and every write now pays replica round-trip latency, and an unhealthy replica
  can stall writes entirely.

The middle ground most production systems want: **quorum** — wait for *some* replicas, not all. In
PostgreSQL `synchronous_standby_names` with `ANY 1 (...)`; in MySQL semi-synchronous replication.

Tie it back to durability: an async setup that reports a commit as durable is making a promise it
cannot keep across a failover. See
[transaction-atomicity-and-durability](transaction-atomicity-and-durability.md).

## Failover loses data, by design

When a primary dies, a replica is promoted — often via an election among candidates. With async
replication, transactions the primary acknowledged but had not shipped are **gone**, and the
application was told they were committed.

That is a deliberate trade, and it must be a known quantity:

- Know your **RPO** (how much data you can lose) and configure replication to match — not the
  reverse.
- **Test failover.** A replica you have never promoted is an assumption, exactly like a backup you
  have never restored.
- Plan for the **split-brain** case: two nodes believing they are primary is worse than downtime,
  and fencing is what prevents it.

## Sharding: the key is the architecture

Replication scales reads and gives you availability. It does not scale **writes** or **data volume**
— every replica holds the whole dataset and applies every write. When one machine can no longer
hold the data or absorb the write rate, you shard.

**Choose the shard key from the access pattern, not from distribution.** The question is not "does
this spread evenly?" but *"what does the dominant query filter on?"*

For an orders table where nearly every query is scoped to a customer, shard on `customer_id`: one
customer's orders live together, and the common query touches one shard. Shard on `order_id` and
every customer query becomes a scatter-gather across all shards — the slowest shard sets your
latency, and you have bought fan-out instead of scale.

**The key is effectively permanent.** Changing it means moving all the data while serving traffic.
Treat the choice with the seriousness of a schema you cannot migrate.

## Hot shards

Even distribution of *keys* does not give even distribution of *load*. One enterprise customer with
a thousand times the volume of everyone else puts a thousand times the traffic on one shard, and
that shard is now your capacity limit while the rest idle.

Mitigations: composite keys that split large tenants across shards, separate dedicated shards for
outsized tenants, or a directory-based mapping so individual keys can be relocated. All of them are
easier to adopt if you assumed a skewed distribution from the start — real-world key distributions
are almost never uniform.

## Consistent hashing

`hash(key) % N` is the trap. Change `N` from 3 to 4 and **nearly every key remaps** — so "just add a
server" triggers a near-total data migration, and if the store is a cache, a near-total miss storm
that hits the database at once (a [cache avalanche](caching.md)).

> Adding a server is usually the start of a new disaster, not the solution to the old one.

Consistent hashing places nodes and keys on a hash ring; a key belongs to the next node clockwise.
Adding a node reassigns only the keys in its arc — roughly `1/N` of them — and removing one moves its
arc to a single neighbour.

Two details matter in practice. **Virtual nodes** are required, not optional: with one point per
physical node the ring is uneven and load is lopsided, so each node is placed at 100–200 points to
smooth distribution. And **consistent hashing bounds movement, not skew** — a hot key still lands on
one node, which is the [hot shard](#hot-shards) problem unchanged.

Where it shows up: cache clusters, sharded stores, and load balancers routing by key for cache
locality (see
[health-checks-and-load-balancing](health-checks-and-load-balancing.md)).

## What you give up

Sharding costs things that were previously free, and the review question is whether the design has
faced them:

- **Cross-shard joins** — no longer a database operation. Either denormalize, or join in the
  application.
- **Cross-shard transactions** — gone. This is now a distributed-transaction problem; see
  [distributed-transactions](distributed-transactions.md).
- **Global uniqueness** — `AUTO_INCREMENT` per shard collides. Use ULID/UUIDv7 or a central ID
  service; note the storage-locality implications in [storage-engines](storage-engines.md).
- **Global ordering and aggregates** — `COUNT(*)`, `ORDER BY`, and pagination across shards require
  scatter-gather and merge.
- **Rebalancing** — adding shards moves data. Consistent hashing or a directory layer makes this
  survivable; `hash % N` makes adding a shard a migration of nearly everything.

## CAP, concretely

Under a **network partition** you choose:

- **CP** — refuse to serve rather than risk wrong data. Correct for balances, inventory, bookings.
- **AP** — keep serving, converge later. Correct for feeds, catalogues, recommendations.

"CA" is not an option in a distributed system, because partitions are not optional. And the choice
is not system-wide: it is made **per data class**, which is the same decision as
[consistency boundaries](consistency-boundaries.md). A system that has not made it explicitly has
made it accidentally, and usually inconsistently.

## Review checklist

- [ ] Does this add replicas? If so, what is the expected effect on replication lag, not just on read capacity?
- [ ] Is replication being relied on for anything a backup should cover? A destructive statement replicates as
  faithfully as a correct one.

- [ ] Replica reads are chosen per data class by staleness tolerance, not by "reads go to replicas"
- [ ] Data about to be decided on (balance, stock, payment status) is read from the primary
- [ ] Read-your-writes is handled for user-visible paths
- [ ] Replication lag is monitored, with deliberate degradation past a threshold
- [ ] Replication mode (async / quorum / sync) matches the stated RPO
- [ ] Durability claims account for what failover loses
- [ ] Failover has been tested by actually promoting a replica
- [ ] Split-brain fencing exists
- [ ] Shard key is derived from the dominant query's filter, not from hash uniformity
- [ ] Skewed key distribution is assumed; hot-shard mitigation exists
- [ ] Cross-shard joins, transactions, uniqueness, and pagination each have an answer
- [ ] Rebalancing strategy avoids `hash % N` requiring a full migration
- [ ] CP-vs-AP is decided per data class and written down
- [ ] Key distribution uses consistent hashing with virtual nodes, not `hash % N`
- [ ] Adding or removing a node moves ~1/N of keys, not nearly all of them

---

*Synthesized from TechCraft's Database Internals parts P11–P12 and P14
([collection](https://www.patreon.com/collection/2115129)), and Distributed Systems parts P15, P17–P18. The
replication-lag framing, the
shard-by-access-pattern rule, the hot-shard trap, and the "CA is theory only" argument are
TechCraft's; the review structure and checklist are this repository's.*

# Replica Placement & Quorums

**Rule: durability is not the number of copies you configured — it is the number of copies that
must acknowledge a write, counted only across failure domains that do not fail together.** A
replication factor of 3 with one acknowledgment, or with all three copies on one rack, is a
replication factor of 1 that costs three times as much.

Part of the storage set: [storage engines](storage-engines.md) ·
[indexes & query plans](indexes-and-query-plans.md) · [replication & sharding](replication-and-sharding.md) ·
**replica placement & quorums** (this file) · [session consistency](session-consistency.md) ·
[connection pools & latency](connection-pools-and-latency.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Three numbers, not one](#three-numbers-not-one)
- [Failure modes](#failure-modes)
- [W + R > N, and what it does not promise](#w--r--n-and-what-it-does-not-promise)
- [Placement: count failure domains, not copies](#placement-count-failure-domains-not-copies)
- [Choosing the numbers](#choosing-the-numbers)
- [Review checklist](#review-checklist)

## The anti-pattern

A configuration that reads as "three copies, safe":

```properties
# Kafka topic
replication.factor=3
min.insync.replicas=1        # broker default
# producer
acks=1                       # "faster"
```

```java
// Cassandra keyspace RF=3, application code
session.execute(debit_stmt.setConsistencyLevel(ONE))   # "lowest latency"
```

```yaml
# Kubernetes StatefulSet, replicas: 3, no affinity rules
replicas: 3
```

Each piece is a default or a documented option. Together they mean: a write is reported as
successful once **one** copy has it, and the three copies may share a node, a rack or a zone.
The system has the cost of RF=3 and the failure tolerance of RF=1.

## Why development hides it

Locally there is one node, or three containers on one laptop, and nothing ever fails
independently. Every write reaches every copy within a millisecond, so `ONE` and `QUORUM`
return identical results and `acks=1` loses nothing. Benchmarks reward the weak setting: fewer
acknowledgments is lower latency and higher throughput, so the configuration that loses data is
the one that wins the load test.

The difference only appears when a copy is **behind or gone** at the moment it matters — a node
that dies between acknowledging and replicating, a zone that fails as a unit, a replica that is
lagging when a read lands on it. None of that happens in a test suite unless someone injects it.

## Three numbers, not one

| Number | Question it answers | Typical names |
|---|---|---|
| **N** — copies | How many copies exist eventually? | `replication.factor`, keyspace RF, replica count |
| **W** — write acknowledgments | How many must hold the write before the client is told "done"? | `acks` + `min.insync.replicas`, write consistency level, `synchronous_standby_names` |
| **R** — read participants | How many are consulted before a read returns? | read consistency level; 1 for any single-replica read |

N is what people configure and talk about. **W decides durability** — an acknowledged write
survives the loss of at most `W − 1` copies. **W and R together decide whether reads are
fresh.** A review that only checks N has checked the number that matters least.

## Failure modes

**1. Acknowledged on one copy.** With `W = 1` the client is told the write succeeded while it
exists on one machine. If that machine dies before replicating, a failover promotes a copy that
never saw it. For a top-up this is the customer whose bank was debited and whose wallet shows
the old balance. Nothing was "corrupted"; the system did exactly what `W = 1` permits.

**2. `acks=all` that means one.** In Kafka, `acks=all` waits for every replica *currently in
sync*. When followers fall behind, the in-sync set shrinks — down to the leader alone — and
`acks=all` is then satisfied by one copy. Only `min.insync.replicas=2` (with RF=3) makes the
broker refuse writes it cannot put on two copies. The broker default is 1, so `acks=all` with
defaults degrades silently to `acks=1` exactly when the cluster is unhealthy.

**3. RF=2 has no margin.** After one loss, every byte exists once until re-replication finishes —
which, for terabytes at a few hundred MB/s, takes hours, during which the survivor carries its own
load plus the rebuild's reads. A second failure in that window is data loss. And with a majority
quorum, RF=2 needs both copies: one failure stops quorum writes entirely. RF=2 buys the cost of
replication without the tolerance; the arithmetic is the same as the
[even-node trap](consensus-and-leader-election.md#quorum).

**4. Correlated copies.** Three replicas scheduled on one Kubernetes node, one rack behind one
top-of-rack switch, or one availability zone. A single failure removes every copy at once. This
is the default outcome without explicit placement rules — the scheduler optimises for resources,
not for spreading your replicas.

**5. Planned failures stack on unplanned ones.** A rolling node upgrade drains one replica while
another is still rebuilding from yesterday's disk failure. Two of three copies are down and
nobody "had an incident". A `PodDisruptionBudget` and a check that replication is healthy before
each drain step are what prevent this.

**6. Copies faithfully replicate corruption.** Replication copies whatever the primary wrote,
including a bad write from a faulty controller or a bug. More copies spread it faster. Page
checksums detect it at the storage layer (PostgreSQL only enables them by default from 18), and
only [backups](replication-and-sharding.md#replication-is-not-a-backup) recover from it.

## W + R > N, and what it does not promise

In leaderless stores (Cassandra, DynamoDB-style, Riak), if the write set and the read set must
overlap — `W + R > N` — every read touches at least one copy that has the latest acknowledged
write, and the newest version wins. With N=3, `QUORUM` writes and `QUORUM` reads (2 + 2 > 3)
overlap; `ONE`/`ONE` (1 + 1 ≤ 3) does not, and a read can return the previous value immediately
after a successful write.

The overlap is weaker than it sounds. It does **not** give you:

- **Ordering of concurrent writes.** "Newest" is usually decided by timestamp — last write wins —
  so clock skew between writers can make the later write lose. Two concurrent debits are not
  serialized by a quorum; that needs a conditional write or a [lock](distributed-locks.md).
- **Rollback of failed writes.** A write that reached one copy but not W is reported as failed and
  is **not** undone. Later reads may return it, or not, depending on which copies they touch.
- **The guarantee under a sloppy quorum.** If the store accepts writes on substitute nodes during
  an outage (hinted handoff), the W nodes may not be among the N a read consults.

So quorum reads and writes make stale reads unlikely; they do not make a leaderless store a
ledger. For "the balance must never be wrong", use a single-leader or consensus-backed store and a
[conditional update](transaction-atomicity-and-durability.md), not a consistency level.

The level is usually per request, which is the useful part: `QUORUM` for the payment path, `ONE`
for view counts, in the same keyspace. In multi-datacenter clusters, `LOCAL_QUORUM` keeps the
latency in-region at the price of cross-region freshness; see [session consistency](session-consistency.md).

## Placement: count failure domains, not copies

Ask, for each copy, "what single event removes this one together with another?" and spread until
the answer is "nothing likely":

```
host         → podAntiAffinity (required, not preferred) on kubernetes.io/hostname
zone         → topologySpreadConstraints on topology.kubernetes.io/zone
rack         → rack-aware placement: Cassandra NetworkTopologyStrategy + snitch, Kafka broker.rack
region       → a different problem: latency and failover, not just placement
```

`preferredDuringScheduling` anti-affinity is a hint the scheduler drops when the cluster is tight —
which is when it matters. On a cluster with fewer nodes than replicas, required anti-affinity
leaves a replica `Pending`, and that is the honest outcome: you do not have three failure domains,
and the configuration should say so rather than pretend.

## Choosing the numbers

| Data | N | W | Notes |
|---|---|---|---|
| Money, orders, anything decided on | 3 (or 5) across zones | majority (2 of 3) | `min.insync.replicas=2`, `acks=all`; reads from the leader or `R` with `W + R > N` |
| Ordinary application data | 3 across zones | majority | the default worth defending |
| Logs, metrics, caches, rebuildable projections | 1–2 | 1 | losing some is acceptable by definition — say so in writing |

The point of the table is that each row is an explicit decision with a stated loss tolerance —
the same per-data-class choice as [CAP](replication-and-sharding.md#cap-concretely). A single
cluster-wide setting is almost always too weak for some data or too expensive for the rest.

## Review checklist

- [ ] For each replicated store, N, W and R are known — not only N
- [ ] Data that is decided on (balances, payment state) is written with a majority W, never `ONE` / `acks=1`
- [ ] Kafka topics carrying business events set `min.insync.replicas` ≥ 2 with RF=3 and producers use `acks=all`
- [ ] No production data store relies on RF=2 for durability
- [ ] Reads that must see the latest write either go to the leader or satisfy `W + R > N`
- [ ] Concurrent updates to the same key are serialized by a conditional write, not by quorum + timestamps
- [ ] Replicas are spread by required anti-affinity / topology spread / rack awareness, verified on the running pods
- [ ] A `PodDisruptionBudget` keeps planned drains from removing more than one replica
- [ ] Re-replication time after a node loss is known, and a second failure in that window is considered
- [ ] Page or block checksums are enabled, and backups exist independently of replication

---

*Prompted by TechCraft's Replication & Scaling P14 (Replication Factor) and P15 (Quorum)
([collection](https://www.patreon.com/collection/2351128)). The posts supply the RF=1/2/3
trade-off, the co-located-rack and replicated-corruption failure stories, the `W + R > N` overlap
argument, and the `W = 1` top-up that vanished on failover. The Kafka `min.insync.replicas`
degradation, the limits of quorum overlap (concurrent writes, unrolled partial writes, sloppy
quorums), the Kubernetes placement rules, the planned-plus-unplanned failure case, and the review
structure are written from established practice, not from the posts.*

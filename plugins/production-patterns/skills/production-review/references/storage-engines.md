# Storage Engines

**Rule: the database does not read rows — it reads fixed-size pages.** Almost every "slow query"
is really "too many pages fetched from disk," and your key choice decides how many.

Part of the storage set: **storage engines** (this file) ·
[indexes & query plans](indexes-and-query-plans.md) · [replication & sharding](replication-and-sharding.md) ·
[session consistency](session-consistency.md) · [connection pools & latency](connection-pools-and-latency.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Pages, not rows](#pages-not-rows)
- [Random UUIDs destroy write locality](#random-uuids-destroy-write-locality)
- [B-Tree vs LSM Tree](#b-tree-vs-lsm-tree)
- [The three amplifications](#the-three-amplifications)
- [Compaction and vacuum](#compaction-and-vacuum)
- [Review checklist](#review-checklist)

## The anti-pattern

```sql
CREATE TABLE events (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),   -- random v4
  payload    jsonb,
  created_at timestamptz DEFAULT now()
);
```

Clean, portable, no sequence contention, and it degrades badly at volume for a reason invisible in
the schema: each insert lands at a random point in the index, so the engine must fetch, modify and
write a different page nearly every time — and split pages constantly.

The matching mistake in diagnosis: latency rises, so the team rewrites the SQL. The query text was
never the problem. The problem is how many pages have to move between disk and memory.

## Why development hides it

A dev table fits entirely in the buffer pool. Every read is a memory hit, so page count is free and
access patterns are irrelevant — random and sequential keys perform identically.

In production the working set exceeds RAM. Now page count *is* the cost, and the schema decision you
made months ago is fixed. Nothing about the query changed; the ratio of memory to data did.

## Pages, not rows

The unit of I/O is a **page** — 8 KB in PostgreSQL, 16 KB in InnoDB by default. Rows live inside
pages, and the engine can only move whole pages.

Reading one row means: check the **buffer pool** for that page; on a miss, read the whole page from
disk; and evict something else to make room.

So the two questions that actually govern performance are:

1. **How few pages can satisfy this query?**
2. **How long do the hot pages stay resident?** (buffer pool hit rate)

This reframes several familiar rules. `SELECT *` is expensive not because of column count but
because wider rows mean fewer rows per page, so more pages per query. A covering index is fast
because it answers from index pages alone. And a sequential scan of a small table beats an index
lookup because random single-page reads cost more than a streamed range — which is why the planner
correctly ignores your index at low row counts, as in
[indexes-and-query-plans](indexes-and-query-plans.md).

**Buffer pool hit rate is the metric to watch.** A falling hit rate means the working set has
outgrown memory, and it explains latency rises that no query change will fix.

## Random UUIDs destroy write locality

A B-Tree keeps entries in sorted order. That interacts with key choice in a way that decides write
throughput:

**Sequential keys** (bigserial, ULID, UUIDv7, Snowflake) always insert at the right edge. One page
stays hot in memory and fills before moving on — few page reads, few splits, dense pages.

**Random keys** (UUIDv4) insert everywhere. Each write targets a different page, so the engine reads
a page it does not have cached, modifies it, splits it when full, and writes it back. At scale the
index no longer fits in memory and this becomes disk-bound.

**Page splits** are the compounding cost: a full page is divided in two, leaving both roughly half
empty. Random inserts split constantly, so the index inflates — often to double the size it needs —
and low page density means the same data now needs twice the pages, which halves your effective
cache.

If you need non-guessable IDs, use a **time-ordered** UUID (v7) or ULID: random enough externally,
monotonic internally. That keeps the property that matters. If you already have random primary keys
at volume, clustering on a sequential column (InnoDB) or periodic `REINDEX` (PostgreSQL) reclaims
some density.

## B-Tree vs LSM Tree

Two answers to the same problem, and the trade is write pattern against read cost.

**B-Tree** — update in place, sorted, balanced.

- Reads are predictable: a handful of page hops to any row
- Range scans and ordered reads are excellent
- Writes require **random I/O**, which is the bottleneck as data grows
- Used by PostgreSQL, InnoDB, SQL Server, Oracle

**LSM Tree** — never update in place; append and merge later.

- Writes go to an in-memory table, then flush to immutable sorted files (**SSTables**)
- All writes become **sequential I/O**, which is dramatically faster
- A read may consult the memtable plus several SSTable levels before knowing the current value
- Used by RocksDB, Cassandra, ScyllaDB, LevelDB, and many time-series stores

The inversion: B-Tree pays at write time to keep reads cheap; LSM pays at read time to make writes
cheap. Neither is better — pick against your workload. Write-heavy ingestion and time-series favour
LSM; transactional workloads with heavy point reads, ordered scans, and updates favour B-Tree.

Most business applications should stay on a B-Tree engine. The reviewable error is not choosing one
over the other but choosing without knowing the workload's read/write ratio.

## The three amplifications

Naming these makes storage trade-offs discussable:

- **Write amplification** — bytes actually written per logical byte. B-Trees rewrite whole pages
  (and WAL, and full-page images); LSMs rewrite data repeatedly during compaction. This is what
  wears SSDs and saturates disk.
- **Read amplification** — pages read per logical read. LSM's characteristic cost: several levels
  consulted per lookup, mitigated by Bloom filters that cheaply exclude files that cannot contain
  the key.
- **Space amplification** — bytes stored per logical byte. Dead tuples awaiting vacuum, half-empty
  pages from splits, superseded SSTable entries awaiting compaction.

You cannot minimize all three. Every storage decision improves one and worsens another.

## Compaction and vacuum

Neither engine deletes in place, so both accumulate garbage and both need a background collector.

**PostgreSQL — dead tuples and `VACUUM`.** MVCC means an `UPDATE` writes a new row version and marks
the old one dead; `DELETE` only marks. `VACUUM` reclaims that space for reuse.

Its critical constraint: **vacuum cannot remove a version still visible to any open transaction.**
One long-running or idle-in-transaction connection blocks cleanup **across the whole database** —
tables bloat, pages thin out, scans slow down, and no lock contention appears to explain it. See
[lock-contention](lock-contention.md). Watch dead-tuple ratio and autovacuum lag; when autovacuum
cannot keep up with write volume, bloat grows monotonically.

**LSM — compaction.** Merging SSTables to discard superseded values and keep read amplification
bounded. It is I/O-intensive and competes with foreground traffic, so latency spikes during
compaction are normal and must be planned for, not debugged as anomalies.

The shared lesson: **space is reclaimed asynchronously, and the collector can fall behind.** A
storage layer without monitoring on its cleanup process degrades silently.

## Review checklist

- [ ] Primary keys are time-ordered (bigserial, UUIDv7, ULID), not random UUIDv4, on large tables
- [ ] Random-key tables at volume have a mitigation (clustering, periodic reindex)
- [ ] Buffer pool hit rate is monitored; a decline is understood as working set vs memory
- [ ] Wide `SELECT *` on hot paths is justified — it costs pages, not just columns
- [ ] Covering indexes are used for the hottest read paths
- [ ] Storage engine choice matches the workload's read/write ratio
- [ ] Dead-tuple ratio and autovacuum lag are monitored (PostgreSQL)
- [ ] No long-running or idle-in-transaction sessions block vacuum
- [ ] `idle_in_transaction_session_timeout` is set
- [ ] For LSM stores, compaction-time latency spikes are expected and capacity-planned
- [ ] Index bloat is measured, not assumed absent

---

*Synthesized from TechCraft's Database Internals series, parts P1, P16 and P17
([collection](https://www.patreon.com/collection/2115129)). The pages-not-rows framing, "UUID as the
destroyer of index pages", and the B-Tree/LSM write-versus-read inversion are TechCraft's; the review
structure and checklist are this repository's.*

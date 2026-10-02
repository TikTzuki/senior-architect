# Data Stores, Caching & Asynchronism

**Rule: reads usually outnumber writes 100:1 or more, so design the read path first. But every
copy you make for reads is something you now have to keep consistent.** Replicas, denormalised
tables and caches are all copies.

## Contents

- [Scaling a relational database](#scaling-a-relational-database)
    - [Replication](#replication) · [Federation](#federation) · [Sharding](#sharding) ·
      [Denormalization](#denormalization) · [SQL tuning](#sql-tuning)
- [NoSQL families](#nosql-families)
- [SQL or NoSQL](#sql-or-nosql)
- [Caching](#caching)
- [Asynchronism](#asynchronism)

## Scaling a relational database

ACID is what you start with: **A**tomic (all or nothing), **C**onsistent (valid state to valid
state), **I**solated (concurrent transactions behave as if run one at a time), **D**urable
(committed means kept). The "serial" isolation is only true at `SERIALIZABLE`; most engines
default lower. See [transaction isolation](../../production-review/references/transaction-isolation.md).

Scaling moves, roughly in the order you reach for them:

| Move                | Splits                | Buys                                                                | Costs                                                |
|---------------------|-----------------------|---------------------------------------------------------------------|------------------------------------------------------|
| **SQL tuning**      | nothing               | Often an order of magnitude, for free                               | Needs benchmarking and profiling                     |
| **Read replicas**   | reads                 | Read throughput, a fail-over target                                 | Lag, promotion logic, more hardware                  |
| **Master-master**   | writes (sort of)      | Writes survive the loss of one master                               | Conflicts; either loose consistency or slower writes |
| **Federation**      | databases by function | Less traffic and lag per DB, parallel writes, better cache locality | Cross-DB joins, routing logic                        |
| **Sharding**        | rows of one table     | Write scale, smaller indexes, partial outages                       | Hot shards, rebalancing, cross-shard queries         |
| **Denormalization** | nothing (adds copies) | Joins avoided on the read path                                      | Duplicated data, slower and riskier writes           |

### Replication

- **Master-slave:** the master takes reads and writes and replicates to slaves, which only
  serve reads (slaves can chain in a tree). If the master dies, you're read-only until a slave
  is promoted.
- **Master-master:** both take writes and coordinate. You need an LB or app logic to choose the
  write target. In practice you get **either loose consistency (ACID broken) or higher write
  latency**, and conflict resolution gets worse with more nodes and more distance.

Costs common to both:

- Writes the master hasn't replicated yet are **lost** when it dies
- Every write is replayed on every replica. Write-heavy load starves replica reads
- More replicas, more lag
- Replicas may apply writes **single-threaded**, while the master writes in parallel, so they
  fall behind under load

Deep dives: [replication & sharding](../../production-review/references/replication-and-sharding.md) ·
[session consistency](../../production-review/references/session-consistency.md).

### Federation

Functional partitioning: separate `users`, `products` and `forums` databases instead of one.
Each gets less traffic and less lag, more of its data fits in memory, and with no single master
serialising writes, they write in parallel.

Doesn't help when one function or table is itself huge (shard that). App logic has to route
each query, and cross-database joins need a server link.

### Sharding

Each database holds a subset of the rows (users A–M on one, N–Z on another). Like federation:
less traffic, less replication, more cache hits, smaller indexes, and one shard going down
leaves the rest serving. Give each shard replicas, or a lost shard means lost data.

- **Shard keys:** last-name initial and geography are the primer's examples. Both skew; a hash of
  a high-cardinality ID spreads load more evenly. That note is this repository's.
- **Hot shards:** power users concentrate load on one shard.
- **Rebalancing** is costly. **Consistent hashing** keeps the data moved on resize to about
  1/N of keys instead of nearly all of them.
- Cross-shard joins and queries are hard. Design access patterns so a request touches one shard.

### Denormalization

Write redundant copies so reads skip expensive joins. That matters most after federation or
sharding, when a join would cross machines or datacenters. Materialised views (PostgreSQL,
Oracle) can maintain the copies for you.

Costs: duplicated data, constraints to keep the copies in sync, and **under heavy write load it
can be slower than the normalised version**. Use it when reads dominate.

### SQL tuning

**Benchmark** (simulate load, e.g. `ab`) and **profile** (slow query log) before changing
anything. The usual wins:

- **Tighten the schema.** `DECIMAL` for money, never float. `NOT NULL` where it applies. Keep
  large blobs in an object store and store their location. `TEXT` for large bodies of text.
- **Index what you filter, join, group and sort on.** Indexes are B-trees: O(log n) lookups
  and in-order scans, at the cost of memory and slower writes. For bulk loads, drop the indexes,
  load, then rebuild.
- **Avoid expensive joins:** denormalise where the numbers demand it.
- **Partition tables:** move hot rows to a separate table so they stay in memory.

Dated in the primer: `CHAR`-over-`VARCHAR` speed and the `VARCHAR(255)` byte-length rule are
storage-engine trivia that rarely matter now. The MySQL query cache was **removed in MySQL 8.0**,
so tune the buffer pool instead. Deep dives:
[indexes & query plans](../../production-review/references/indexes-and-query-plans.md) ·
[storage engines](../../production-review/references/storage-engines.md) ·
[schema migrations](../../production-review/references/schema-migrations.md).

## NoSQL families

Data is denormalised and joins happen in application code. Most NoSQL stores give up full
ACID for **BASE**: Basically Available, Soft state (it can change without input as replicas
converge), Eventually consistent. On the CAP axis that's choosing availability. Several modern
stores (DynamoDB, MongoDB) now offer transactions; that note is this repository's.

| Family          | Abstraction                                               | Strengths                                                                                     | Typical use                                    | Examples                                  |
|-----------------|-----------------------------------------------------------|-----------------------------------------------------------------------------------------------|------------------------------------------------|-------------------------------------------|
| **Key-value**   | Hash table; some keep keys sorted for range scans         | O(1) reads/writes, memory- or SSD-backed, very fast                                           | Caches, sessions, rapidly changing simple data | Redis, Memcached                          |
| **Document**    | Key-value with queryable documents as values              | Flexible schema, queries on document internals                                                | Data whose shape changes now and then          | MongoDB, CouchDB, DynamoDB, Elasticsearch |
| **Wide column** | `ColumnFamily<RowKey, Columns<ColKey, Value, Timestamp>>` | High availability, huge scale, sorted row-key ranges; timestamps for versioning and conflicts | Very large datasets, time-series, write-heavy  | Bigtable, HBase, Cassandra                |
| **Graph**       | Nodes (records) and edges (relationships)                 | Many-to-many and deep relationship queries                                                    | Social graphs, recommendations, fraud rings    | Neo4j, FlockDB                            |

Key-value stores push complexity into the application: anything beyond get/put is your code.
They're also what document stores, and some graph stores, are built on.

## SQL or NoSQL

| Choose **SQL** for                                   | Choose **NoSQL** for                       |
|------------------------------------------------------|--------------------------------------------|
| Structured, relational data with a strict schema     | Semi-structured data with a dynamic schema |
| Complex joins                                        | No joins needed                            |
| Transactions                                         | Many TB/PB, very data-intensive workloads  |
| Well-known scaling patterns; mature tools and people | Very high IOPS                             |
| Fast index lookups                                   |                                            |

Data that suits NoSQL: clickstream and log ingest, leaderboards and scores, temporary data like
shopping carts, hot lookup and metadata tables. **Default to SQL** unless a specific number or
access pattern rules it out. Moving off later is easier than discovering you needed transactions.

## Caching

A cache serves repeat work from memory, and absorbs the skew of popular items that would
otherwise hot-spot a partition.

**Where caches sit**, from client to data: client (browser/OS) → CDN → web server / reverse
proxy (Varnish, NGINX) → application cache (Redis/Memcached) → the database's own cache (tune it
for your workload). Avoid file-based caches on app servers; they get in the way of cloning and
autoscaling.

**What granularity to cache:**

| Level                            | Pros                                                                         | Cons                                                                            |
|----------------------------------|------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| **Query** (hash of SQL → result) | Easy to add                                                                  | Invalidation is hard. One changed cell can live in any number of cached queries |
| **Object** (assembled entity)    | Invalidate when the object changes; workers can build objects asynchronously | You have to design the object model                                             |

Good candidates: user sessions, fully rendered pages, activity streams, user graph data.

**Update strategies:**

| Strategy               | Flow                                                             | Pros                                               | Cons                                                                                                  |
|------------------------|------------------------------------------------------------------|----------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| **Cache-aside** (lazy) | App reads the cache; on a miss, reads the DB and fills the cache | Only requested data is cached; survives cache loss | A miss costs 3 trips; stale until the TTL expires or you invalidate; a new node starts cold           |
| **Write-through**      | App writes to the cache; the cache writes the DB synchronously   | Cache never stale; reads after a write are fast    | Slower writes; a new node stays empty until the data is written; caches data nobody reads (add a TTL) |
| **Write-behind**       | App writes to the cache; it flushes to the DB asynchronously     | Fast writes                                        | **Data loss** if the cache dies before flushing; complex                                              |
| **Refresh-ahead**      | Cache refreshes hot entries before they expire                   | Lower latency, when it predicts well               | Wrong predictions cost more than no cache                                                             |

Common pairing: cache-aside for reads plus write-through for hot entities, with a TTL on
everything. Eviction is usually LRU. Redis adds persistence and data structures (sorted sets,
lists). Leaderboards and timelines are built on those.

Cache invalidation is the hard part, and expiry is when caches fail. Read the
[caching & stampede lesson](../../production-review/references/caching.md) before shipping any of
these strategies.

## Asynchronism

Take slow work off the request path, or do it ahead of time (periodic pre-aggregation).

- **Message queues:** the app publishes a job and tells the user its status; a worker
  processes it and signals completion. The client can fake completion in the meantime: a tweet
  shows on your own timeline instantly and fans out to followers later.
    - Redis: simple broker, **can lose messages**
    - RabbitMQ: AMQP, you run the nodes
    - SQS: hosted, higher latency, **may deliver twice**
    - Kafka: a log rather than a queue; replayable, partition-ordered (this repository's addition)
- **Task queues** (Celery): take a task and its data, run it, return results. They support
  scheduling and compute-heavy background jobs.
- **Back pressure:** an unbounded queue grows past memory, spills to disk and slows everything
  down. **Bound the queue.** When it's full, return `503 Server Busy` and have clients retry with
  exponential backoff. Little's law (items in system = arrival rate × time in system) tells you
  how big the bound should be.
- **Don't** queue cheap calculations or real-time workflows. A queue adds delay and complexity.

"May deliver twice" is the normal case for queues, not an edge case: consumers must be
idempotent. See [message delivery semantics](../../production-review/references/message-delivery-semantics.md)
and [async operations in APIs](../../production-review/references/api-async-operations.md).

*Compressed from [donnemartin/system-design-primer](https://github.com/donnemartin/system-design-primer)
(CC BY 4.0): database, cache and asynchronism sections. The ordering of scaling moves, the
"dated" notes, hash-key sharding, NoSQL transactions, Kafka, Little's-law sizing and the
"default to SQL" stance are this repository's.*

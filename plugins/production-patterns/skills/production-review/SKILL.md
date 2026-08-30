---
name: production-review
description: Review code against known production failure modes — the bugs that only surface under concurrency, retries, timeouts, and real traffic, and that pass review because they work fine in development. Use when reviewing a diff, PR, or design for production readiness, when asked "will this hold up in production", or when the code touches file uploads, object storage, background jobs, or external calls.
allowed-tools: Bash, Read, Grep, Glob
---

# Production Review

Development is a single user on a fast local network with no proxy in front. That environment
cannot apply the pressure that breaks a design, so code reaches review already "working."

This skill reviews for the gap: what holds under concurrency, retries, timeouts, partial
failure, and out-of-order delivery. It is about **design-level failure modes**, not style or
ordinary bugs — use `/code-review` for those.

## Lesson index

Each lesson is a self-contained reference. **Read the lesson file before reviewing code in its
area** — the checklist alone is not enough to review against, and the failure modes are the
part worth knowing.

| Lesson                                                                                        | Read it when the code touches                                                                                                                        |
|-----------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| [large-file-upload.md](references/large-file-upload.md)                                       | File or image uploads, multipart form handling, object storage (S3/GCS/R2/MinIO), presigned URLs, media processing, user-supplied downloads          |
| [server-concurrency-model.md](references/server-concurrency-model.md)                         | Thread pool or worker sizing, event-loop code, async/await on request paths, high connection counts, "slow but low CPU"                              |
| [memory-and-gc.md](references/memory-and-gc.md)                                               | Loading full result sets, unbounded caches or maps, `ThreadLocal` on pooled threads, OOM kills, p99-vs-p50 divergence, container memory limits       |
| [caching.md](references/caching.md)                                                           | Any cache read/write, TTL choice, cache invalidation, hot-key rebuilds, Redis/Memcached usage, stale-data tolerance                                  |
| [distributed-locks.md](references/distributed-locks.md)                                       | `synchronized`/mutex guarding shared resources, Redis or ZooKeeper locks, leader election, "only one worker should do this"                          |
| [health-checks-and-load-balancing.md](references/health-checks-and-load-balancing.md)         | Health/readiness endpoints, probe config, service registration, load balancer rules, session or cache state held in-process                          |
| [deployment-and-config.md](references/deployment-and-config.md)                               | Config files and env vars, timeout/retry/pool values, feature flags, deploy strategy, graceful shutdown, migrations during rollout                   |
| [storage-engines.md](references/storage-engines.md)                                           | Primary key choice on large tables, UUID keys, buffer pool or cache-hit issues, `VACUUM`/bloat, B-Tree vs LSM engine selection                       |
| [replication-and-sharding.md](references/replication-and-sharding.md)                         | Replica reads, read-your-writes, replication lag, failover and RPO claims, shard key choice, cross-shard queries                                     |
| [session-consistency.md](references/session-consistency.md)                                   | Reads routed to replicas, read-your-writes, stale reads a user acts on, cross-device or cross-region reads, comment/reply or notification ordering   |
| [connection-pools-and-latency.md](references/connection-pools-and-latency.md)                 | Pool sizing, `max_connections`, pool exhaustion, fast queries with slow endpoints, round-trip counts, cross-AZ calls                                 |
| [lock-contention.md](references/lock-contention.md)                                           | Transactions containing network calls, `SELECT FOR UPDATE`, bulk updates, queue-table workers, deadlock reports, unexplained p99 latency             |
| [indexes-and-query-plans.md](references/indexes-and-query-plans.md)                           | New queries or `WHERE`/`ORDER BY` clauses, ORM lazy loading and loops that query, new indexes, slow endpoints, reports over large tables             |
| [schema-migrations.md](references/schema-migrations.md)                                       | Any migration file — `ALTER TABLE`, `CREATE INDEX`, adding constraints, column renames or drops, data backfills                                      |
| [transaction-isolation.md](references/transaction-isolation.md)                               | Any read-modify-write, balance or inventory updates, check-then-insert, counters, booking or reservation logic, anything wrapped in a transaction    |
| [transaction-atomicity-and-durability.md](references/transaction-atomicity-and-durability.md) | Any `BEGIN`/`@transactional` block, external calls near a commit, durability or `fsync` settings, replication acknowledgment, backup/failover claims |
| [optimistic-vs-pessimistic-locking.md](references/optimistic-vs-pessimistic-locking.md)       | `version` columns, `SELECT FOR UPDATE`, retry loops around writes, hot-row contention, flash-sale or seat-booking logic                              |
| [distributed-transactions.md](references/distributed-transactions.md)                         | Business operations spanning services, Saga or compensation logic, 2PC/XA, outbox workers, event consumers, cross-service timeouts                   |
| [consistency-boundaries.md](references/consistency-boundaries.md)                             | New service or aggregate boundaries, deciding what must be synchronous, invariant enforcement, reconciliation/detection design, replica reads        |
| [data-retention-and-history.md](references/data-retention-and-history.md)                     | `DELETE` on business data, `is_deleted`/soft-delete flags, audit logging, history tables, retention or GDPR erasure                                  |
| [temporal-data.md](references/temporal-data.md)                                               | Rates, prices, limits or terms that change over time; point-in-time queries; retroactive corrections; regulated decisions defended after the fact    |
| [event-sourcing-and-cqrs.md](references/event-sourcing-and-cqrs.md)                           | Event stores, aggregate replay, snapshots, projections, read models, materialized views and their refresh                                            |
| [message-delivery-semantics.md](references/message-delivery-semantics.md)                     | Outbox or inbox tables, message consumers, dedupe logic, dead-letter handling, any claim of exactly-once                                             |
| [api-contracts.md](references/api-contracts.md)                                               | New or changed public endpoints, field renames, response shape changes, versioning, OpenAPI/proto schemas, deprecation                               |
| [api-resource-modeling.md](references/api-resource-modeling.md)                               | New endpoint naming, verb-shaped endpoints, exposing table columns, client-set status fields, lifecycle transitions                                  |
| [api-list-endpoints.md](references/api-list-endpoints.md)                                     | Any list/search endpoint, pagination, `OFFSET`, sortable or filterable parameters, `total_count`, exports                                            |
| [api-async-operations.md](references/api-async-operations.md)                                 | Endpoints that may exceed a few seconds, report or export generation, job status polling, `202 Accepted`, long workflows                             |
| [webhook-provider-design.md](references/webhook-provider-design.md)                           | Sending webhooks or callbacks to consumers, signing, delivery retries, event replay endpoints                                                        |
| [rate-limiting.md](references/rate-limiting.md)                                               | Public endpoints without limits, `429` handling, quota or tier design, multi-tenant fairness, auth endpoint abuse, load shedding                     |
| [network-and-latency.md](references/network-and-latency.md)                                   | Service-to-service calls, dependency chains, fan-out, p99 latency, cross-AZ or cross-region hops, RPC that looks local                               |
| [timeouts-and-retries.md](references/timeouts-and-retries.md)                                 | Any remote call, timeout values, retry loops, backoff, SDK/mesh retry defaults, deadline propagation                                                 |
| [circuit-breakers-and-bulkheads.md](references/circuit-breakers-and-bulkheads.md)             | Shared thread or connection pools, dependency isolation, graceful degradation, essential-vs-enhancing dependencies                                   |
| [consensus-and-leader-election.md](references/consensus-and-leader-election.md)               | Leader election, "only one instance should" logic, quorum, etcd/ZooKeeper/Consul, split-brain risk, manual promotion                                 |
| [designing-for-failure.md](references/designing-for-failure.md)                               | Adding a dependency, degradation behaviour, blast radius, failover/restore/rollback testing, fault injection                                         |
| [payment-state-and-idempotency.md](references/payment-state-and-idempotency.md)               | Payment or charge records, `paid` booleans, retry-sensitive money operations, idempotency keys, order→payment modelling                              |
| [payment-provider-and-webhooks.md](references/payment-provider-and-webhooks.md)               | Calls to Stripe/VNPay/MoMo/PayPal, timeout handling on money operations, any webhook endpoint                                                        |
| [payment-order-consistency.md](references/payment-order-consistency.md)                       | Order status driven by payment outcome, outbox tables, event consumers, reconciliation jobs                                                          |
| [payment-ledger.md](references/payment-ledger.md)                                             | Balance columns, money movement records, fee splits, multi-party accounting, anything summing transaction amounts                                    |
| [payment-refunds.md](references/payment-refunds.md)                                           | Refund or cancellation flows, partial refunds, reversing a completed payment, revoking delivered goods or access                                     |
| [payment-reconciliation.md](references/payment-reconciliation.md)                             | Reconciliation or settlement jobs, stuck-pending sweeps, any job that repairs state by comparing against a provider                                  |
| [payment-fraud-detection.md](references/payment-fraud-detection.md)                           | Risk or fraud checks, velocity limits, manual review queues, capture-vs-authorize logic                                                              |
| [payment-settlement.md](references/payment-settlement.md)                                     | Merchant balances and payouts, settlement report ingestion, authorization-vs-capture logic, fee accounting, T+n date arithmetic                      |

Load with:

```
${CLAUDE_PLUGIN_ROOT}/skills/production-review/references/<lesson>.md
```

When no lesson covers the area under review, say so and review from general principles rather
than forcing an unrelated checklist onto the code.

## How to review

**1. Establish what changed.** `git diff`, the named PR, or the paths given. Ask if the target
is ambiguous rather than guessing.

**2. Match lessons to the diff.** Use the index above. Prefer the concrete signals — an import
of a storage SDK, a multipart handler, a queue publish — over filenames.

**3. Read the matched lesson in full**, then review the code against it.

**4. Trace the failure, don't pattern-match.** For each candidate finding, state the concrete
path to breakage: the input or interleaving, the state it produces, and the user-visible
consequence. A finding you cannot walk through concretely is a guess — drop it or mark it
clearly as one.

**5. Weigh it against this system.** An internal tool with ten users and a public API have
different thresholds. Check the scale the code actually operates at — existing limits,
timeouts, config, traffic assumptions in nearby code — before calling something a problem. Say
which threshold you assumed.

## Reporting

Order findings by consequence: data loss and corruption first, then availability, then cost,
then maintainability. For each one give:

- **What breaks** — one sentence
- **When** — the specific conditions, e.g. "two uploads of the same file complete concurrently"
- **Fix** — the concrete change, with the lesson section that covers it

Close with what you checked and found clean. A review that lists only problems leaves the reader
unable to tell thoroughness from luck.

State plainly when the diff is fine. Manufacturing findings to look thorough wastes the reader's
attention and trains them to ignore the next review.

## Scope

**In scope:** concurrency and races, retry and idempotency behavior, timeout and resource
exhaustion, partial failure and recovery, unbounded growth (memory, storage, queues), and
trust boundaries around client-supplied input.

**Out of scope:** formatting, naming, test coverage, and ordinary logic bugs. Those belong to
`/code-review`, which is a better tool for them.

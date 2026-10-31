# Dev Insider Roadmap

TechCraft's learning path from *Developer who can code* to *Senior Engineer*. Five tiers,
32 series, in a deliberate order.

## The ordering principle

```
Runtime → Correctness → Distributed → Production → Architecture → Leadership
```

The roadmap's central claim is that **the biggest mistake is not missing knowledge — it is
learning the right thing at the wrong time.** Its examples:

- Microservices before understanding transactions
- Event Sourcing before understanding data modeling
- System Design interviews before understanding how a request traverses a server
- Kubernetes before understanding how an application is operated

The stated consequence: knowledge accumulates while the foundation weakens, which is why effort
stops converting into progress. The order below is therefore not a suggestion — it is the point.

## Where to start

Begin with **AI-Proof Engineer** — not to learn AI, but to understand what is losing value in
the industry and what is gaining it. The roadmap calls this the "eye-opening" part.

Then go to Tier 1 and proceed in order.

## Tier 1 — Foundations

> The tier most developers skip, and the reason many stay mid-level for years.

| # | Series | Lesson coverage here |
|---|---|---|
| 1 | Backend Internals | swept, 8 lessons — [caching](../../production-review/references/caching.md), [connection-pools-and-latency](../../production-review/references/connection-pools-and-latency.md), [deployment-and-config](../../production-review/references/deployment-and-config.md), [distributed-locks](../../production-review/references/distributed-locks.md), [health-checks-and-load-balancing](../../production-review/references/health-checks-and-load-balancing.md), [memory-and-gc](../../production-review/references/memory-and-gc.md), [message-delivery-semantics](../../production-review/references/message-delivery-semantics.md), [server-concurrency-model](../../production-review/references/server-concurrency-model.md) |
| 2 | Database Internals | swept, 7 lessons — [connection-pools-and-latency](../../production-review/references/connection-pools-and-latency.md), [indexes-and-query-plans](../../production-review/references/indexes-and-query-plans.md), [lock-contention](../../production-review/references/lock-contention.md), [replication-and-sharding](../../production-review/references/replication-and-sharding.md), [schema-migrations](../../production-review/references/schema-migrations.md), [storage-engines](../../production-review/references/storage-engines.md), [transaction-atomicity-and-durability](../../production-review/references/transaction-atomicity-and-durability.md) |
| 3 | Data Modeling Patterns | swept, 9 lessons — [data-retention-and-history](../../production-review/references/data-retention-and-history.md), [distributed-transactions](../../production-review/references/distributed-transactions.md), [event-sourcing-and-cqrs](../../production-review/references/event-sourcing-and-cqrs.md), [message-delivery-semantics](../../production-review/references/message-delivery-semantics.md), [payment-ledger](../../production-review/references/payment-ledger.md), [payment-reconciliation](../../production-review/references/payment-reconciliation.md), [payment-settlement](../../production-review/references/payment-settlement.md), [payment-state-and-idempotency](../../production-review/references/payment-state-and-idempotency.md), [temporal-data](../../production-review/references/temporal-data.md) |
| 4 | Transaction & Consistency | swept, 5 lessons — [consistency-boundaries](../../production-review/references/consistency-boundaries.md), [distributed-transactions](../../production-review/references/distributed-transactions.md), [optimistic-vs-pessimistic-locking](../../production-review/references/optimistic-vs-pessimistic-locking.md), [transaction-atomicity-and-durability](../../production-review/references/transaction-atomicity-and-durability.md), [transaction-isolation](../../production-review/references/transaction-isolation.md) |

**What you should end up understanding:** how a request actually traverses the system; how
threads, cache, queues, and connection pools govern performance and stability; how a database
stores and retrieves data beneath familiar SQL; and why data is not only its current schema but
also audit trail, history, timeline, and business truth.

The tier's thesis: **consistency was never purely a technical problem — it is a business one.**

*After this tier:* you stop seeing software as classes and tables, and start seeing the
mechanisms underneath them.

## Tier 2 — Distributed Thinking

> The transition from implementer to someone who understands the system.

| # | Series | Lesson coverage here |
|---|---|---|
| 5 | API Design Patterns | swept, 8 lessons — [api-async-operations](../../production-review/references/api-async-operations.md), [api-contracts](../../production-review/references/api-contracts.md), [api-list-endpoints](../../production-review/references/api-list-endpoints.md), [api-resource-modeling](../../production-review/references/api-resource-modeling.md), [payment-state-and-idempotency](../../production-review/references/payment-state-and-idempotency.md), [rate-limiting](../../production-review/references/rate-limiting.md), [timeouts-and-retries](../../production-review/references/timeouts-and-retries.md), [webhook-provider-design](../../production-review/references/webhook-provider-design.md) |
| 6 | Distributed Systems Explained | swept, 10 lessons — [circuit-breakers-and-bulkheads](../../production-review/references/circuit-breakers-and-bulkheads.md), [consensus-and-leader-election](../../production-review/references/consensus-and-leader-election.md), [consistency-boundaries](../../production-review/references/consistency-boundaries.md), [designing-for-failure](../../production-review/references/designing-for-failure.md), [distributed-transactions](../../production-review/references/distributed-transactions.md), [event-sourcing-and-cqrs](../../production-review/references/event-sourcing-and-cqrs.md), [message-delivery-semantics](../../production-review/references/message-delivery-semantics.md), [network-and-latency](../../production-review/references/network-and-latency.md), [replication-and-sharding](../../production-review/references/replication-and-sharding.md), [timeouts-and-retries](../../production-review/references/timeouts-and-retries.md) |
| 7 | Replication & Scaling | swept, 5 lessons — [consensus-and-leader-election](../../production-review/references/consensus-and-leader-election.md), [multi-region-failover](../../production-review/references/multi-region-failover.md), [replica-placement-and-quorums](../../production-review/references/replica-placement-and-quorums.md), [replication-and-sharding](../../production-review/references/replication-and-sharding.md), [session-consistency](../../production-review/references/session-consistency.md) |
| 8 | Event Sourcing & CQRS | swept, 4 lessons — [data-retention-and-history](../../production-review/references/data-retention-and-history.md), [event-replay-and-projection-rebuild](../../production-review/references/event-replay-and-projection-rebuild.md), [event-schema-evolution](../../production-review/references/event-schema-evolution.md), [event-sourcing-and-cqrs](../../production-review/references/event-sourcing-and-cqrs.md) |

**What you should end up understanding:** an API is a long-term contract, not an endpoint.
Failure is not an exception — it is the default state of a distributed system. Why retry,
timeout, circuit breaker, saga, and idempotency appear in every large system. And that scaling
data is usually far harder than scaling compute.

*After this tier:* you see a service as part of a larger system, and understand that distributed
systems are hard not because of technology, but because you no longer control everything.

## Tier 3 — Production Data Systems

> The tier that creates the widest gap between an ordinary developer and a senior engineer.
> Once a system is large enough, the question stops being whether the code is right and becomes
> whether the data is still correct.

| # | Series | Lesson coverage here |
|---|---|---|
| 9 | Data Architecture | none |
| 10 | Production Data Operation | not swept; topically related — [schema-migrations](../../production-review/references/schema-migrations.md) |
| 11 | Production Database Design | none |
| 12 | Database Optimization | not swept; topically related — [indexes-and-query-plans](../../production-review/references/indexes-and-query-plans.md) |

**What you should end up understanding:** data does not stay inside one service's database — it
flows through analytics, risk, finance, compliance, reporting, and support. Three claims worth
carrying:

- A backup you have never restored is not a backup.
- Replication you have never failed over is not high availability.
- Production database design is not drawing a pretty ERD — it is protecting the business's
  version of the truth.

And optimization is not query tuning; it is identifying the system's real bottleneck.

*After this tier:* you treat data as an asset that must survive production.

## Tier 4 — Production System Design

> Where the pieces connect. Difficulty rises naturally:
> `Money → Business Transactions → Distributed Systems → Internet Scale → Architecture Thinking`

| # | Series | Lesson coverage here |
|---|---|---|
| 13 | Payment System | swept, 7 lessons — [payment-fraud-detection](../../production-review/references/payment-fraud-detection.md), [payment-ledger](../../production-review/references/payment-ledger.md), [payment-order-consistency](../../production-review/references/payment-order-consistency.md), [payment-provider-and-webhooks](../../production-review/references/payment-provider-and-webhooks.md), [payment-reconciliation](../../production-review/references/payment-reconciliation.md), [payment-refunds](../../production-review/references/payment-refunds.md), [payment-state-and-idempotency](../../production-review/references/payment-state-and-idempotency.md) |
| 14 | Wallet System | none |
| 15 | Banking System | none |
| 16 | Order System | none |
| 17 | Inventory System | none |
| 18 | Chat System | none |
| 19 | Notification System | none |
| 20 | News Feed | none |
| 21 | Search Engine | none |
| 22 | Recommendation System | none |
| 23 | Design Mindset | none |

The roadmap singles out **Payment + Banking + Order + Inventory** as the strongest cluster,
because those are the systems most backend developers actually meet at work.

**What you should end up understanding:** why payments need idempotency, ledgers, reconciliation,
webhooks, and auditability; why notifications need queues, retries, and delivery guarantees; why
chat needs realtime transport, message ordering, and fan-out; why search and recommendation need
data architectures entirely unlike transactional systems; and why anything touching money demands
an unusually high correctness bar.

*After this tier:* you understand not just the pattern but **why it exists, when to use it, and
when not to** — with real users, real traffic, real money, and real consequences attached.

## Tier 5 — Technical Leadership

> From building systems to deciding about them.

| #  | Series                      | Lesson coverage here |
|----|-----------------------------|----------------------|
| 24 | Architecture Fundamentals   | none                 |
| 25 | Reliability Engineering     | none                 |
| 26 | Scaling Engineering         | none                 |
| 27 | Engineering Decision Making | none                 |
| 28 | Software Evolution          | none                 |
| 29 | Senior Engineering Mindset  | none                 |
| 30 | Tech Lead Playbook          | none                 |
| 31 | Engineering Leadership      | none                 |

**What you should end up understanding:** when to keep a monolith, when to modularize, when
microservices genuinely pay. That reliability is not a monitoring dashboard and scaling is not
more servers. That a senior engineer is not distinguished by knowing more frameworks, but by
seeing trade-off, risk, cost, and business impact — and that technical leadership is not managing
more people, it is helping the team decide better.

*After this tier:* you can evaluate an architecture, see systemic risk, and anticipate failure
before it happens.

## Coverage summary

| Tier                         | Series | Swept | Topically related only |
|------------------------------|--------|-------|------------------------|
| 1 — Foundations              | 4      | 4     | 0                      |
| 2 — Distributed Thinking     | 4      | 4     | 0                      |
| 3 — Production Data Systems  | 4      | 0     | 2                      |
| 4 — Production System Design | 11     | 1     | 0                      |
| 5 — Technical Leadership     | 8      | 0     | 0                      |
| **Total**                    | **31** | **9** | **2**                  |

"Swept" means every published part of the series was saved, read in full, and decided against the
knowledge base — captured as a lesson, used to extend one, or recorded as already covered. The
lessons listed are where that series' failure modes now live; lessons are shared, so one appears
under several series. A swept series is not memorised knowledge: the lessons hold only what can be
checked in a diff, and much of each series — motivation, judgment, trade-off reasoning — is
deliberately not in them. "Topically related" means a lesson touches the subject, but the series
itself has not been read.

## Not every topic becomes a lesson

The `production-review` knowledge base holds **failure modes catchable in a diff**. Much of this
roadmap is not that, and should not be forced into it:

- **Tier 1–3** are dense with reviewable failure modes. Most lessons will come from here.
- **Tier 4** yields lessons about specific mechanisms — idempotency keys, ledger invariants,
  delivery guarantees, message ordering.
- **Tier 5** is judgment, not checklist. "When to keep a monolith" has no reviewable form.
  Learn it; do not manufacture a lesson file for it.

Mark a topic learned freely. Promote it to a lesson only when you can name a concrete failure
that working code exhibits.

---

*Source:
TechCraft's ["Roadmap Dev Insider"](https://www.patreon.com/techcraft_official/posts/roadmap-ben-dev-161167166).
The tier and series structure above is TechCraft's, transcribed from the post. The lesson-coverage
mapping is this repository's own.*

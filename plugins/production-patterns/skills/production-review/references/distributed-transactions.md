# Distributed Transactions

**Rule: ACID stops at the database boundary, and "localhost is a great lie."** Across services you
choose between blocking everything (2PC) and compensating for what already happened (Saga) — there
is no third option that keeps atomicity for free.

Part of the transaction set:
[atomicity & durability](transaction-atomicity-and-durability.md) ·
[isolation](transaction-isolation.md) · [locking](optimistic-vs-pessimistic-locking.md) ·
**distributed transactions** (this file) ·
[consistency boundaries](consistency-boundaries.md)

Resilience mechanics for the calls between services are in
[timeouts & retries](timeouts-and-retries.md) and
[circuit breakers & bulkheads](circuit-breakers-and-bulkheads.md).

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [The boundary of ACID](#the-boundary-of-acid)
- [UNKNOWN is the real problem](#unknown-is-the-real-problem)
- [Two-phase commit, and why it is rare](#two-phase-commit-and-why-it-is-rare)
- [Saga: compensate instead of block](#saga-compensate-instead-of-block)
- [Eventual consistency and the outbox](#eventual-consistency-and-the-outbox)
- [When "eventually" becomes "never"](#when-eventually-becomes-never)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
@transactional                                  # ← guards this service's rows only
def buy_fund(order):
    Order.create(status="processing")           # local DB
    payment_service.debit(order.user, order.amount)   # remote, commits independently
    portfolio_service.add_units(order.units)          # remote, commits independently
    order.status = "completed"
```

`payment_service.debit()` commits in *its* database the moment it returns. If
`portfolio_service.add_units()` then times out, the local `@transactional` rolls back the order —
and the debit stays. The customer has paid and owns nothing.

The annotation looks like it covers the method. It covers one database.

## Why development hides it

Locally every service is on the same machine, responds in a millisecond, and never partitions. So
the sequence always completes, and the rollback path never runs with a remote commit already
behind it.

> Localhost is a great lie.

It removes exactly the property that makes distributed transactions hard: the possibility that one
participant succeeded and you cannot find out.

## The boundary of ACID

A transaction is *one transaction, one database, one source of truth.* WAL, locks, and rollback all
operate inside that scope.

Split the business operation across services and each service still has perfect local ACID, while
the operation as a whole has none. Nobody holds the global picture, and no participant can roll
back another's commit.

This is not a gap to be patched. It is the defining property of the architecture, and the design
question becomes *how do we stay correct without global atomicity?*

## UNKNOWN is the real problem

The obsession worth having is not "how do I roll back?" but **"what do I do when I don't know?"**

A timeout calling another service means the call may have succeeded. Treating it as failure and
compensating will reverse something that did not happen; treating it as success will confirm
something that never occurred. Both corrupt state.

So UNKNOWN must be a modelled state, resolved by querying the participant or waiting for its
event — never guessed. This is the same discipline as
[payment-provider-and-webhooks](payment-provider-and-webhooks.md), generalized.

## Two-phase commit, and why it is rare

2PC pursues global atomicity with a coordinator:

```
Phase 1 — PREPARE:  coordinator asks every participant "can you commit?"
                    each locks resources and promises it can
Phase 2 — COMMIT:   if all promised, coordinator says commit; otherwise abort
```

It genuinely delivers strong consistency. The costs are why almost nobody uses it for
service-to-service work:

- **Locks are held across the whole protocol**, including network round trips. Throughput collapses
  under load, and this is contention at its worst — see
  [lock-contention](lock-contention.md).
- **Coordinator failure is unbounded.** If the coordinator dies after participants promise but
  before it decides, participants sit locked, unable to commit or abort, waiting for an instruction
  that may never come. The system becomes a zombie: alive, holding locks, making no progress.
- **It requires participants that speak the protocol.** A payment provider or a bank will not enrol
  in your prepare phase. The moment a third party is involved, 2PC is not available at all.
- **In CAP terms it chooses consistency over availability** — a partition stalls the system rather
  than degrading it.

2PC remains reasonable *within* one trust boundary you fully control — say, two databases in one
datacentre with XA. Across service and organizational boundaries, it is the wrong tool.

## Saga: compensate instead of block

A Saga replaces one atomic transaction with a sequence of local transactions, each with a
**compensating action** that semantically undoes it:

```
reserve inventory  →  charge payment  →  create shipment
      ↓ compensate         ↓ compensate        ↓ compensate
release inventory     refund payment      cancel shipment
```

If step three fails, run the compensations for steps two and one in reverse. Nothing was locked
across the sequence, so the system stays available.

The shift in mindset: **stop trying to prevent the failure, and build something that survives and
heals after it.**

Two coordination styles:

- **Orchestration** — a coordinator drives each step. Explicit, debuggable, easy to see the whole
  flow; the coordinator is a dependency and can become a bottleneck.
- **Choreography** — each service reacts to events. Loosely coupled and scalable; the end-to-end
  flow exists nowhere in one place, which makes failures hard to trace.

Prefer orchestration when the flow is business-critical and must be auditable — payments, orders.

**What makes Sagas fail in production:**

- **Compensation is not rollback.** A refund is a new transaction, visible to the customer, and may
  itself fail. Reversing a sent email or a shipped item may be impossible.
- **Compensations must be idempotent and retryable**, because they run exactly when the system is
  already unhealthy.
- **There is no isolation.** Intermediate states are visible: inventory reserved for an order that
  will be cancelled. Other readers can act on states that are about to be undone — the anomaly
  isolation normally hides. See [transaction-isolation](transaction-isolation.md).
- **A compensation that fails leaves a stuck Saga.** These need detection and a manual path, not
  infinite retries.

## Eventual consistency and the outbox

Give up simultaneity and keep convergence: participants disagree briefly and reach the same state
in bounded time.

The mechanism that makes it trustworthy is the **outbox** — the state change and the event are
written in one local transaction, and a worker publishes afterwards:

```sql
BEGIN;
  UPDATE payments SET status = 'succeeded' WHERE id = :id AND status = 'pending';
  INSERT INTO outbox (event_type, payload) VALUES ('payment_succeeded', :payload);
COMMIT;
```

If the state changed, the event exists. Delivery is at-least-once, so **every consumer must be
idempotent.** Full treatment in
[payment-order-consistency](payment-order-consistency.md).

The trade being made explicitly: forcing synchronous consistency across services costs availability
and throughput — threads pile up waiting on remote calls until the pool is exhausted. Eventual
consistency buys back both, at the price of a visible window of staleness that the product must be
designed to tolerate.

## When "eventually" becomes "never"

Eventual consistency is a promise of convergence, and an unmonitored promise is a wish. It silently
becomes permanent inconsistency when:

- A consumer hits a poison message and stops, or dead-letters it where nobody looks
- The outbox worker dies and nothing alerts on unpublished rows
- A compensation fails and the Saga is abandoned mid-sequence
- Replica lag grows without bound during an incident

None of these produce an error in the request path. The fix is not more retries — it is
**detection**: alert on outbox backlog, consumer lag, dead-letter depth, and stuck-Saga age, and
reconcile independently. See [consistency boundaries](consistency-boundaries.md) and
[payment-reconciliation](payment-reconciliation.md).

## Review checklist

- [ ] No `@transactional`-style annotation is assumed to cover remote calls
- [ ] Business operations spanning services use a Saga or an outbox, not a wrapping transaction
- [ ] Timeouts on remote calls are treated as UNKNOWN and resolved by query or event
- [ ] 2PC is used only inside one controlled trust boundary, never across third parties
- [ ] Every Saga step has a defined, idempotent, retryable compensation
- [ ] Compensations that cannot succeed (sent email, shipped goods) are identified in the design
- [ ] Intermediate Saga states that other readers can observe are enumerated and acceptable
- [ ] Stuck Sagas are detected and have a manual resolution path
- [ ] Outbox pattern used for event emission; consumers are idempotent
- [ ] Outbox backlog, consumer lag, dead-letter depth, and stuck-Saga age are alerted on
- [ ] The staleness window of eventual consistency is bounded and acceptable to the product

---

*Synthesized from TechCraft's Transaction & Consistency series parts P14–P17, reconciled against
Data Modeling Patterns parts P11 and P13 and Distributed Systems parts P12–P13
([collection](https://www.patreon.com/collection/2256242)). The "boundary of ACID" and "localhost is
a great lie" framings, the 2PC coordinator-failure zombie scenario, and the
prevent-versus-heal shift are TechCraft's; the review structure and checklist are this repository's.*

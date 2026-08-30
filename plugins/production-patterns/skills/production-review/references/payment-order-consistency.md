# Order & Payment Consistency

**Rule: money moved and the order updated are two facts in two places, and no transaction spans
both.** Make the state change and the event atomic locally, then converge.

Part of the payment set: [state & idempotency](payment-state-and-idempotency.md) ·
[provider & webhooks](payment-provider-and-webhooks.md) ·
**order consistency** (this file) · [ledger](payment-ledger.md) ·
[refunds](payment-refunds.md) · [reconciliation](payment-reconciliation.md) ·
[settlement](payment-settlement.md) · [fraud detection](payment-fraud-detection.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Why not a distributed transaction](#why-not-a-distributed-transaction)
- [Two domains, two state machines](#two-domains-two-state-machines)
- [The outbox pattern](#the-outbox-pattern)
- [Idempotent consumer](#idempotent-consumer)
- [Reconciliation](#reconciliation)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
charge = payment_service.charge(order.total)  # money moves — irreversible
if charge.succeeded:
    order_service.mark_paid(order.id)  # ← anything here loses the link
```

The gap between those two lines is where money goes missing. If `mark_paid` times out, throws,
or the process dies, the customer has paid and the order stays `payment_pending` forever.

The mirror image is worse commercially: the order unlocks and ships, and the payment later fails
at the provider.

Same shape, other direction: publishing `payment_succeeded` to a queue *inside* the payment
transaction. If that transaction rolls back, you have announced an event for something that never
happened.

## Why development hides it

Both services are up, fast, and local. The window between the charge and the order update is
sub-millisecond and nothing ever fails inside it. There is no network partition, no deploy
mid-request, no OOM kill, no queue backlog — so the two writes appear atomic when they are simply
never interrupted.

## Why not a distributed transaction

The instinct is 2PC across both databases. It does not work here, for a reason more fundamental
than complexity: **the money already moved in a system you do not control.** You cannot enrol a
bank or a payment provider in your commit protocol, cannot hold a prepare phase across a webhook
that arrives minutes later, and cannot roll back a completed capture.

So the correct model is not "make both writes atomic." It is **eventual consistency with
guaranteed event delivery, plus reconciliation to catch what still diverges.**

## Two domains, two state machines

Order and payment are related but distinct:

- **Order** — the customer's intent: what they want, how much, where it goes.
- **Payment** — the money: whether funds were actually captured.

Each gets its own state machine (`created, payment_pending, paid, fulfilled, cancelled, refunded`
for orders; see [state & idempotency](payment-state-and-idempotency.md) for payments).

**The order service must never infer payment state.** It reacts to events emitted by the payment
service. An order service that guesses — "the charge call returned, so we're paid" — is the
anti-pattern above wearing a state machine.

## The outbox pattern

The core problem: updating payment state and publishing the event are two operations, and any
split between them loses the event.

Solution — write both **in the same local transaction**:

```
BEGIN
  UPDATE payments SET status = 'succeeded' WHERE id = :id AND status = 'pending';
  INSERT INTO outbox (event_type, payload) VALUES ('payment_succeeded', :payload);
COMMIT
```

Then a separate **outbox worker** polls the table and publishes to the message queue, marking
rows dispatched.

The guarantee: if the payment state changed, the event exists. Both are in one database, so one
commit covers both. There is no window in which money moved and nothing was announced.

Two properties to keep in mind:

- Delivery is **at-least-once**, not exactly-once. The worker can publish and die before marking
  the row, so the event is re-sent. This is why the consumer must be idempotent.
- The publish happens **after** commit, never inside the transaction — see
  [lock-contention](lock-contention.md) for why network calls inside transactions are their own
  failure mode.

## Idempotent consumer

Delivery semantics, the Inbox pattern, and why exactly-once does not exist are covered in
[message-delivery-semantics](message-delivery-semantics.md). The payment-specific points follow.

Since events arrive at least once, the order service must handle duplicates as no-ops:

```sql
UPDATE orders
SET status = 'paid'
WHERE id = :order_id
  AND status = 'payment_pending';
-- zero rows affected: already paid. Not an error — do nothing.
```

Guard on the expected current state and check affected rows. The dangerous version processes
side effects unconditionally: a duplicate `payment_succeeded` that re-triggers fulfilment ships
the goods twice.

**Every side effect the consumer fires — fulfilment, receipt email, loyalty points — needs its
own idempotency guard**, not just the status write. The status update being idempotent while the
email is not still sends two emails.

## Reconciliation

Everything above reduces divergence; none of it eliminates it. Webhooks get lost, workers have
bugs, providers have incidents. Reconciliation is the audit that finds what slipped through, by
comparing three sources:

```
Order state   vs   Payment state   vs   Provider state
```

The one specific to this lesson: **payment `succeeded` while the order is still
`payment_pending`** means the event was lost or the consumer failed, and the fix is to re-drive
the event rather than to update the order row directly.

Full treatment — the four mismatch classes, how to target high-risk records instead of scanning,
and the rule that reconciliation must never patch silently — is in
[reconciliation](payment-reconciliation.md).

## Review checklist

- [ ] No attempt at a distributed transaction spanning payment, order, and provider
- [ ] Order and payment have separate state machines; the order service only reacts to events
- [ ] State change and event insert happen in one local transaction (outbox)
- [ ] Events are published after commit, never inside the transaction
- [ ] The outbox worker is idempotent and marks dispatch separately from publishing
- [ ] Consumers guard on expected current state and check affected rows
- [ ] Every consumer side effect has its own idempotency guard, not just the status write
- [ ] Reconciliation exists and re-drives lost events (see [reconciliation](payment-reconciliation.md))

---

*Synthesized from TechCraft's Payment System series, part P6
([collection](https://www.patreon.com/collection/2186651)). The domain separation, outbox
reasoning, and three-way reconciliation are TechCraft's; the review structure and checklist are
this repository's.*

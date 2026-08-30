# Refunds

**Rule: a refund is a new transaction, not an undo.** It has its own state machine, its own
idempotency key, and its own ledger entries — and it never edits the original payment.

Part of the payment set: [state & idempotency](payment-state-and-idempotency.md) ·
[provider & webhooks](payment-provider-and-webhooks.md) ·
[order consistency](payment-order-consistency.md) · [ledger](payment-ledger.md) ·
**refunds** (this file) · [reconciliation](payment-reconciliation.md) ·
[settlement](payment-settlement.md) · [fraud detection](payment-fraud-detection.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [A refund is a historical fact, not an erasure](#a-refund-is-a-historical-fact-not-an-erasure)
- [Refunds need their own state machine](#refunds-need-their-own-state-machine)
- [Partial refunds](#partial-refunds)
- [Refund is not reverse logistics](#refund-is-not-reverse-logistics)
- [Idempotency, or you refund twice](#idempotency-or-you-refund-twice)
- [UNKNOWN applies here too](#unknown-applies-here-too)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
def refund(payment_id):
    provider.refund(payment_id)
    db.execute("UPDATE payments SET status = 'refunded' WHERE id = ?", payment_id)
```

Three defects in three lines:

- **It edits history.** The original payment record is overwritten. The fact that money was
  collected, and when, is gone.
- **No idempotency.** A user spamming the button, or a worker retrying on timeout, issues several
  refunds for one request. The customer is paid back two or three times.
- **It assumes refunds are instant.** `provider.refund()` returning does not mean the money has
  moved; bank settlement takes seconds to days. Marking `refunded` immediately is a claim you
  cannot support.

The word "undo" is the root error. Nothing in a payment system can be undone — only compensated.

## Why development hides it

The provider stub refunds instantly and always succeeds, so the pending window never exists and
the state machine looks unnecessary. One tester clicking once never produces a duplicate. And
partial refunds — the common case in production — are rarely in the test fixtures at all.

## A refund is a historical fact, not an erasure

A successful payment is a historical fact: money moved, parties recorded it, statements were
issued. You cannot make that untrue.

So a refund is a **new transaction** with its own identity, referencing the original. Never delete
the payment row, never rewrite its amount, never pretend it did not happen. This preserves
traceability and is what makes the audit trail defensible months later during a dispute.

In the ledger this is a reverse movement, not a deletion — see [ledger](payment-ledger.md):

| Account             | Direction | Amount  |
|---------------------|-----------|---------|
| Merchant receivable | debit     | 100,000 |
| User cash           | credit    | 100,000 |

Both the original and the reversal remain, which is why the books can still be explained.

## Refunds need their own state machine

A refund is asynchronous, so it needs states of its own — not a status borrowed from the payment:

`created → pending → succeeded` / `failed`

Validate before initiating. At minimum: the original payment is in a refundable state
(`succeeded`), the requested amount is available to refund, the currency matches, and the payment
is within any provider refund window.

A refund attempt that fails validation must not reach the provider.

## Partial refunds

Customers return one of three items. Partial refunds are normal, and they are where the arithmetic
goes wrong.

Track the **cumulative refunded amount** against the original and enforce:

```
sum(all succeeded refunds for a payment)  <=  original payment amount
```

Enforce it atomically — this is a read-modify-write on a running total, so two concurrent partial
refunds can each pass a naive check and together exceed the original. Use a guarded update or a
constraint; see [transaction-isolation](transaction-isolation.md).

Order state follows: `paid → partially_refunded`, or `→ refunded` once fully returned.

## Refund is not reverse logistics

The subtle one, and the source of real business loss.

**Returning money does not automatically mean withdrawing what was delivered.** Revoking digital
access, cancelling a shipment already in transit, reclaiming loyalty points, and clawing back a
merchant payout are each separate flows with their own rules.

A refund handler that "undoes everything" will revoke access a customer is still entitled to
under a partial refund, or attempt to cancel a delivery that has already completed. Decide each
consequence deliberately rather than defaulting to a blanket reversal.

## Idempotency, or you refund twice

Every refund request carries its own **idempotency key** — distinct from the payment's key,
because it is a distinct operation.

Without it: the user double-clicks, the support agent retries, the worker times out and re-runs,
and money leaves several times. The direct loss is bad; the corrupted ledger is worse, because now
the books disagree with reality and every downstream report is wrong.

Same mechanics as the payment path — unique constraint on the key, stored response replayed
verbatim, request hash to catch a changed amount under the same key. See
[state & idempotency](payment-state-and-idempotency.md).

## UNKNOWN applies here too

A timeout calling the provider's refund API means UNKNOWN, not failed. **Do not retry blindly as
a new request.**

Park the refund in `pending`, then resolve it the same way as a payment:

1. **Query the provider** for the status of that idempotency key.
2. **Wait for the webhook** (`refund_succeeded` / `refund_failed`), remembering that webhooks
   arrive late, duplicated, and out of order — so transitions must be validated, not applied.

See [provider & webhooks](payment-provider-and-webhooks.md). Anything still unresolved is
reconciliation's problem — see [reconciliation](payment-reconciliation.md).

## Review checklist

- [ ] Refunds are new records referencing the original; the payment row is never edited
- [ ] Refunds have their own state machine with an async `pending` state
- [ ] Validation runs before calling the provider: refundable state, available amount, currency
- [ ] Cumulative refunded amount is enforced against the original, atomically
- [ ] Concurrent partial refunds cannot together exceed the original amount
- [ ] Order state reflects `partially_refunded` vs `refunded`
- [ ] Fulfilment reversal (access, shipping, points, payouts) is decided per concern, not blanket
- [ ] Every refund request has its own idempotency key with a unique constraint
- [ ] Refund responses are stored and replayed on retry
- [ ] Timeout is treated as UNKNOWN; resolution is by provider query or webhook, never blind retry
- [ ] Refund movements are written as reversing ledger entries, not as adjustments to the original

---

*Synthesized from TechCraft's Payment System series, part P8
([collection](https://www.patreon.com/collection/2186651)). The refund-as-new-transaction framing,
partial-refund invariant, and the refund-is-not-reverse-logistics distinction are TechCraft's; the
review structure and checklist are this repository's.*

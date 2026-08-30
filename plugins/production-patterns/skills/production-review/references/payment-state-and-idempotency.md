# Payment State & Idempotency

**Rule: a payment is a state machine with an idempotency key, never a boolean.** `paid = true`
cannot represent a transaction whose outcome you do not yet know.

Part of the payment set: **state & idempotency** (this file) ·
[provider & webhooks](payment-provider-and-webhooks.md) ·
[order consistency](payment-order-consistency.md) · [ledger](payment-ledger.md) ·
[refunds](payment-refunds.md) · [reconciliation](payment-reconciliation.md) ·
[settlement](payment-settlement.md) · [fraud detection](payment-fraud-detection.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [The state machine](#the-state-machine)
- [Never copy the provider's states](#never-copy-the-providers-states)
- [One order, many payment attempts](#one-order-many-payment-attempts)
- [Idempotency: the key and the stored response](#idempotency-the-key-and-the-stored-response)
- [Request hash: same key must mean same request](#request-hash-same-key-must-mean-same-request)
- [The concurrency hole](#the-concurrency-hole)
- [Audit trail](#audit-trail)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
# Two separate defects in four lines.
def pay(order_id, amount):
    charge = provider.charge(amount)          # no idempotency key
    if charge.status == "success":
        db.execute("UPDATE orders SET paid = true WHERE id = ?", order_id)
```

**Defect one — the boolean.** `paid` has no value meaning "we sent the charge and do not yet
know the outcome." So the moment the provider is slow, the system must lie: either claim failure
(and the user was charged) or claim success (and they were not).

**Defect two — no idempotency.** The user double-clicks, or the client retries on timeout, and
`provider.charge()` runs twice. Two charges, one order.

Both are invisible in the code. It reads as a correct happy path.

## Why development hides it

A local provider stub answers in a millisecond and always succeeds. There is no timeout to
produce an unknown state, no second click inside the request window, and no retry. Every state
transition observed locally is `created → succeeded`, which is exactly the one path that needs
no design.

The failure requires latency, concurrency, or an unhappy provider — none of which a dev
environment supplies.

## The state machine

Money moves through states, and the intermediate ones are the point:

| State                | Meaning                                           |
|----------------------|---------------------------------------------------|
| `created`            | Record exists, local ID minted, nothing sent yet  |
| `pending`            | Sent to the provider, awaiting outcome            |
| `succeeded`          | Terminal. Money captured.                         |
| `failed`             | Terminal. Declined.                               |
| `cancelled`          | Terminal. Abandoned or expired before processing. |
| `refunded`           | Reversed after success                            |
| `partially_refunded` | Part of the amount returned                       |

`created` matters more than it looks: it is where the local ID and the idempotency key are
minted, **before** any provider call. You cannot make a call idempotent retroactively.

**Enforce legal transitions, and enforce them in the database:**

```sql
UPDATE payments SET status = 'succeeded'
WHERE id = :id AND status = 'pending';    -- then check affected rows
```

- Legal: `created→pending`, `pending→succeeded`, `pending→failed`, `succeeded→refunded`
- **Illegal: `failed→succeeded`, `refunded→succeeded`, `cancelled→succeeded`**

A terminal state is terminal. A late webhook claiming success on a `failed` payment must be
rejected, not applied — see [provider & webhooks](payment-provider-and-webhooks.md). If the user
wants to try again, that is a **new attempt**, not a resurrected one.

**Never unlock the product on `pending`.** Fulfilment requires `succeeded`.

## Never copy the provider's states

Stripe has `authorized`/`captured`, others have `settled`, VNPay and MoMo have their own. Storing
those directly couples your domain to one vendor's vocabulary and breaks when you add a second.

Keep internal states and **map** provider states onto them. Store the raw provider status
alongside, unmapped, for audit — but branch your logic only on the internal state.

## One order, many payment attempts

Model order → payment as **1:N, not 1:1.** A card declines, the user tries a different card and
succeeds. They should not need a new order, and you must retain both attempts.

That history is what lets you answer "what did this customer actually try?" during a dispute, and
it is required input for reconciliation.

## Idempotency: the key and the stored response

Retry is not an edge case — it is the network's normal behaviour. Idempotency is what converts a
retry from a duplicate charge into a safe recovery.

Every payment attempt carries a unique **idempotency key** minted at `created` (e.g.
`checkout_123_attempt_1`, or a UUID). Every retry of that attempt sends the *same* key.

The server side is not just an existence check — you must store and replay the response:

| Column            | Purpose                                    |
|-------------------|--------------------------------------------|
| `idempotency_key` | **UNIQUE.** The identity of the attempt.   |
| `request_hash`    | Fingerprint of the request body            |
| `status`          | in-progress / completed                    |
| `response_body`   | The exact response returned the first time |

- **Key absent:** process, call the provider, store the response, return it.
- **Key present:** do **not** re-run the payment logic. Return the stored `response_body`
  verbatim — same `payment_id`, same timestamps. The caller must not be able to tell a retry from
  the original.

Returning a freshly computed response instead of the stored one is a subtle version of the same
bug: the client sees two different payment IDs for one attempt and cannot tell which is real.

## Request hash: same key must mean same request

What if the same key arrives with the amount changed from 100k to 1,000k — a client bug, or an
attack?

Checking only the key would return the old response and silently accept the mutated request.
So hash the significant fields (amount, currency, order ID) and store it. **Key matches but hash
differs → reject the request outright.** The key is an assertion that this is the same operation;
a different body proves it is not.

## The concurrency hole

`SELECT` then `INSERT` is a race, not a check:

```
Thread A: SELECT key -> absent
Thread B: SELECT key -> absent      (A has not inserted yet)
A and B both call the provider  ->  DOUBLE CHARGE
```

This is the general read-modify-write race — see
[transaction-isolation](transaction-isolation.md) — with money attached.

**The reliable fix is a `UNIQUE` constraint on `idempotency_key`.** Let both threads insert; the
database rejects the second, and the loser reads the winner's row. A constraint holds regardless
of code path, deploy, or manual intervention.

A distributed lock (Redis) can reduce contention as a first gate, but it is not the guarantee —
locks expire, nodes partition. Keep the constraint underneath.

## Provider-level idempotency

Your own idempotency does not cover the hop to the provider. If your call succeeds but the
response times out, you must not issue a fresh charge with the same details.

1. **Pass your idempotency key down to the provider** if they support it. Then your retry is safe
   end to end.
2. **Otherwise, query first.** Call the provider's status API to learn what happened before
   deciding whether to retry. Never re-charge on the assumption of failure.

## Audit trail

Status alone is worthless three months later during a dispute. Persist, immutably, for every
transition:

- `provider_payment_id` — the vendor's reference
- `provider_response` — the full raw JSON, unmodified
- `raw_status` — the provider's own status before mapping
- timestamp, and who or what caused the change

Raw provider responses are not redundant storage; they are the only evidence you hold when
arguing with a provider or an auditor. Store them before you interpret them.

## Review checklist

- [ ] Payment status is a state machine with explicit states, not a boolean
- [ ] The local record and idempotency key are created **before** any provider call
- [ ] State transitions are enforced with a guarded `UPDATE` and an affected-row check
- [ ] Terminal states cannot be left; retries create a new attempt
- [ ] Product/fulfilment is never unlocked on `pending`
- [ ] Internal states are mapped from provider states, not copied
- [ ] Order → payment is 1:N and every attempt is retained
- [ ] `idempotency_key` carries a `UNIQUE` constraint
- [ ] The first response is stored and replayed verbatim on retry
- [ ] `request_hash` is checked; same key with a different body is rejected
- [ ] The idempotency key is passed to the provider, or its status API is queried before retry
- [ ] Raw provider responses and every transition are persisted immutably

---

*Synthesized from TechCraft's Payment System series parts P1–P3
([collection](https://www.patreon.com/collection/2186651)), Data Modeling Patterns final part on the
Idempotency Pattern, and API Design Patterns part P7
([collection](https://www.patreon.com/techcraft_official)). The state model,
idempotency mechanics, and failure scenarios are TechCraft's; the review structure and checklist are
this repository's. Consumer-side deduplication is covered in
[message-delivery-semantics](message-delivery-semantics.md).*

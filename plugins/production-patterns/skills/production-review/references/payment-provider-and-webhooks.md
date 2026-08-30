# Payment Providers & Webhooks

**Rule: a provider response and a webhook are external signals, not truth.** The only source of
truth is your own state machine, and `timeout` means UNKNOWN — never failed.

Part of the payment set: [state & idempotency](payment-state-and-idempotency.md) ·
**provider & webhooks** (this file) ·
[order consistency](payment-order-consistency.md) · [ledger](payment-ledger.md) ·
[refunds](payment-refunds.md) · [reconciliation](payment-reconciliation.md) ·
[settlement](payment-settlement.md) · [fraud detection](payment-fraud-detection.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Timeout means UNKNOWN](#timeout-means-unknown)
- [Hard decline vs temporary failure](#hard-decline-vs-temporary-failure)
- [Resolving UNKNOWN](#resolving-unknown)
- [The webhook pipeline](#the-webhook-pipeline)
- [Webhooks arrive late, twice, and out of order](#webhooks-arrive-late-twice-and-out-of-order)
- [The unknown-transaction webhook](#the-unknown-transaction-webhook)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
try:
    result = provider.charge(amount)
except TimeoutError:
    mark_payment_failed(payment_id)        # ← the expensive assumption
    return {"error": "Payment failed, please try again"}
```

The request timed out at *your* hop. The chain continues past you:

```
Your service  ──▶  Provider  ──▶  Bank / Card network
      ▲
   timeout here says nothing about what happened downstream
```

The bank may have debited the customer. You have now told them it failed and invited them to
retry — and if the retry carries no idempotency key, they pay twice.

The webhook equivalent:

```python
@app.post("/webhook")
def webhook(payload):
    payment = db.get(payload["payment_id"])
    payment.status = payload["status"]      # no signature check, no dedupe,
    db.save(payment)                        # no transition validation
    send_receipt_email(payment)             # slow work inside the request
    return 200
```

Four defects: anyone can POST this, a duplicate delivery double-processes, an out-of-order event
overwrites a newer state, and the slow email makes the provider time out and retry.

## Why development hides it

Stubs return instantly and succeed, so `TimeoutError` never fires and UNKNOWN never exists.
Webhooks in development are usually hand-fired once, in order, from a trusted terminal — which
exercises none of duplicate delivery, reordering, replay, or forgery.

## Timeout means UNKNOWN

> The most dangerous answer in a payment system is not "failed." It is "I don't know."

Make it a real state. `unknown` or `pending_verification` belongs in the state machine, and it
must block every action that assumes an outcome: no fulfilment, no failure message, no retry
without a key.

Treating unknown as failed is what produces the two worst outcomes in payments — a customer
charged for nothing, or a customer charged twice.

## Hard decline vs temporary failure

Collapsing all errors into "failed" throws away the distinction that decides what to do next:

| Class                 | Examples                                        | Action                                   |
|-----------------------|-------------------------------------------------|------------------------------------------|
| **Hard decline**      | Card expired, insufficient funds, fraud block   | Terminal. Mark `failed` with confidence. |
| **Temporary failure** | Network error, provider maintenance, rate limit | **UNKNOWN.** Verify before deciding.     |

A hard decline is information from the bank — the transaction definitively did not happen. A
temporary failure is an absence of information about your own request.

## Resolving UNKNOWN

Three mechanisms, in order of reliability:

1. **Idempotency key sent to the provider.** Makes retrying safe: ten calls, one charge. This is
   the primary defence — see [state & idempotency](payment-state-and-idempotency.md).
2. **Query the provider actively.** `GET /payments/{id}` and ask what happened, rather than
   waiting. This is how UNKNOWN resolves promptly.
3. **Webhook.** Useful, but the least trustworthy: late, duplicated, reordered, and forgeable.

And behind all three, **reconciliation** as the final net — see
[order consistency](payment-order-consistency.md).

## The webhook pipeline

A webhook handler is a security boundary and a queue intake, not a place for business logic.
The order of these five steps is the design:

```
1. Verify signature      reject anything unsigned or stale
2. Store raw event       persist before interpreting
3. Dedupe                on provider_event_id
4. Enqueue job           hand off to a worker
5. Return 200 OK         fast, before any business logic runs
```

**1 — Verify.** The endpoint is public, so a plausible-looking JSON body is not evidence of
anything. Verify the provider signature or shared secret. Also enforce **timestamp tolerance**:
without it, an attacker who captures one valid webhook can replay it indefinitely. Add IP
allowlisting and rate limiting at the gateway where the provider supports it.

**2 — Store raw first.** Persist the unmodified payload immediately, before any parsing or
business logic. This is what lets you replay events after fixing a handler bug without asking the
provider to resend. A `webhook_events` table with the raw body, provider event ID, receipt
timestamp, and processing status.

**3 — Dedupe on `provider_event_id`.** Providers retry. Without dedupe you mark paid twice, email
twice, and — worst — fulfil twice. If the event is already processed, return success so the
provider stops retrying.

**4 and 5 — Acknowledge fast, process async.** Providers have short timeouts and will retry what
looks like a failure, so slow work inside the handler creates a retry storm precisely when you
are already struggling. Enqueue and return 200 immediately. This also separates infrastructure
failure (receiving) from business failure (processing), so each retries independently.

## Webhooks arrive late, twice, and out of order

Never assume delivery order. `refund_succeeded` can arrive before `payment_succeeded`; a stale
event can arrive after a newer state is set.

So a webhook does not *set* state — it **requests a transition**, which is validated:

```
current_state + event_type  ->  is this transition legal?
```

Legal `pending → succeeded`. Illegal `succeeded → pending`, `refunded → succeeded`. An event
that would drive an illegal transition is recorded and dropped, not applied.

Because two webhooks can arrive concurrently, the check-and-update must be atomic — a guarded
`UPDATE ... WHERE status = :expected` with an affected-row check, or row locking. A validation
that reads state then writes it in two statements is the same race as everywhere else.

## The unknown-transaction webhook

You will receive webhooks for payments that are not in your database. **Do not 404 and discard
them.**

The usual cause is a **mapping delay**: the provider processed a redirect payment and fired the
webhook before your own order write landed. The transaction is real and you are about to lose it.

Store the event, retry with exponential backoff, and if it still cannot be matched after several
attempts, push it to a **reconciliation queue** for a human. An unmatched webhook is a signal
about money that exists; discarding it converts a timing artefact into lost revenue.

## Review checklist

- [ ] Timeout is treated as UNKNOWN and modelled as a real state, never as failure
- [ ] Hard declines and temporary failures are classified differently
- [ ] UNKNOWN is resolved by provider status query, not by re-charging
- [ ] Webhook signature is verified, with timestamp tolerance against replay
- [ ] Raw webhook payload is persisted before any interpretation
- [ ] Events are deduped on `provider_event_id`
- [ ] Handler returns 200 immediately; business logic runs in a worker
- [ ] Webhooks request a validated transition; they never assign state directly
- [ ] Illegal transitions from stale or reordered events are rejected and recorded
- [ ] Concurrent webhook processing is atomic (guarded update or row lock)
- [ ] Unmatched webhooks are stored, retried with backoff, then escalated — never dropped
- [ ] Raw provider responses are retained for audit and replay

---

*Synthesized from TechCraft's Payment System series, parts P4–P5
([collection](https://www.patreon.com/collection/2186651)). The UNKNOWN-state framing, failure
classification, and five-step webhook pipeline are TechCraft's; the review structure and
checklist are this repository's.*

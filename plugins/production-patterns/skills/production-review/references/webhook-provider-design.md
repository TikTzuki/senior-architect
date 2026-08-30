# Webhook Provider Design

**Rule: sending a webhook once is not delivering it.** Without signatures, retries, and a way to
replay, you have built a notification your consumers cannot trust or reconcile against.

Part of the API set: [contracts & versioning](api-contracts.md) ·
[resource modeling](api-resource-modeling.md) · [list endpoints](api-list-endpoints.md) ·
[async operations](api-async-operations.md) · **webhook provider** (this file) ·
[rate limiting](rate-limiting.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Sign it, and let them verify](#sign-it-and-let-them-verify)
- [Retry with backoff, and give up deliberately](#retry-with-backoff-and-give-up-deliberately)
- [A slow consumer must not slow you](#a-slow-consumer-must-not-slow-you)
- [Payload design](#payload-design)
- [Ordering and duplicates are the consumer's problem — help them](#ordering-and-duplicates-are-the-consumers-problem--help-them)
- [Give them a way to catch up](#give-them-a-way-to-catch-up)
- [Being a good outbound citizen](#being-a-good-outbound-citizen)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
def on_payment_succeeded(payment):
    requests.post(customer.webhook_url,  # inside the transaction
                  json={"event": "payment.succeeded",
                        "payment_id": payment.id},
                  timeout=30)  # blocking, 30s
```

Everything that can go wrong, does: the consumer's endpoint is down and the event is **gone forever**;
the payload is unsigned so anyone who learns the URL can forge it; a 30-second timeout on a shared
worker means one slow consumer occupies your capacity; and it is inside a transaction, so a rollback
announces something that never happened.

This is [payment-provider-and-webhooks](payment-provider-and-webhooks.md) from the other side. That
lesson tells consumers not to trust webhooks. **This one is about deserving trust.**

## Why development hides it

Your test consumer is a local endpoint that always returns `200` in a millisecond. It never goes
down, never times out, never returns `500`, and never receives an event twice — so a naive
fire-and-forget looks complete and correct.

Real consumers deploy, restart, run out of quota, have bugs, and go offline for hours. Delivery
reliability is a property of *their* infrastructure, which is precisely what you cannot test.

## Sign it, and let them verify

Your webhook endpoint is a public URL. A JSON body that looks right proves nothing, so sign every
request:

```
X-Webhook-Signature: t=1756640400,v1=5257a869e7ecebeda32affa62cdca3fa51cad7e77a0e56ff536d0ce8e108d8bd
```

Sign `timestamp + "." + raw_body` with HMAC-SHA256 and a per-consumer secret. Details that matter:

- **Include the timestamp in the signed payload**, so a captured request cannot be replayed later.
  Tell consumers to reject anything outside a tolerance window (five minutes is typical).
- **Sign the raw bytes**, not a re-serialized object — any difference in key order or whitespace
  breaks verification, and it will break for exactly one consumer whose parser differs.
- **Support two active secrets** so rotation does not require simultaneous deploys on both sides.
- **Document the algorithm precisely**, with a verification snippet. A signature scheme consumers
  cannot implement gets skipped, and then it protects nobody.

## Retry with backoff, and give up deliberately

A single attempt makes delivery contingent on the consumer being up at that instant. Retry on
connection failure, timeout, `5xx`, and `429`:

```
attempt 1   immediately
attempt 2   +10s      exponential, with jitter
attempt 3   +1m
attempt 4   +10m
attempt 5   +1h
attempt 6   +6h
then        dead-letter, and notify the consumer out of band
```

**Jitter is not optional.** A shared outage means thousands of pending deliveries; without jitter
they retry in lockstep and you deliver a synchronized flood the moment the consumer recovers — see
[timeouts-and-retries](timeouts-and-retries.md).

Do **not** retry `4xx` other than `429`: a `400` or `422` means the consumer rejected the payload and
will reject it identically forever.

And retry has an end. Define it, expose the delivery's terminal state, and **tell the consumer they
are failing** — an email or dashboard alert, not silence. A consumer discovering three weeks later
that they stopped receiving events is a support incident you could have prevented.

## A slow consumer must not slow you

Delivery must be **asynchronous and isolated**, because you are calling infrastructure you do not
control:

- **Never send inside a database transaction.** Enqueue via the outbox; the transaction commits, then
  a worker delivers. See [message-delivery-semantics](message-delivery-semantics.md).
- **Never send on the request path.** The API call that triggered the event must not wait for a third
  party.
- **Short timeouts** — 5–10 seconds is plenty. A consumer needing 30 seconds should acknowledge fast
  and process async, which is the advice you give them.
- **Isolate per consumer.** One consumer's failures must not consume the shared delivery pool — a
  bulkhead, per-destination concurrency limits, and ideally a circuit breaker that stops hammering a
  dead endpoint. See [circuit-breakers-and-bulkheads](circuit-breakers-and-bulkheads.md).

## Payload design

- **Include an event id and event type**, and keep both stable. The id is what consumers dedupe on.
- **Include a timestamp of the event**, not of the delivery attempt — a retry an hour later must not
  look like a fresh event.
- **Prefer a reference plus minimal data over a full snapshot.** A large embedded object is stale by
  the time it arrives, and encourages consumers to treat it as authoritative. Sending
  `{payment_id, status}` and letting them fetch the current state avoids that; sending everything
  saves them a call. State which you are doing, because it changes how they must treat the data.
- **Never include secrets or full PII** in a payload delivered to a URL that may be logged by
  proxies.
- **Version the payload**, and treat its shape with the same care as any other contract — a webhook
  schema is an API. See [api-contracts](api-contracts.md).

## Ordering and duplicates are the consumer's problem — help them

You cannot guarantee ordering across retries: an event retried an hour later arrives after events
that happened later. Rather than pretend otherwise:

- **Say plainly that delivery is at-least-once and unordered.** Consumers who know this build
  correctly; consumers who assume otherwise build a bug.
- **Provide a monotonic sequence number** per subscription where you can, so a consumer can detect
  gaps and out-of-order arrival.
- **Keep event ids stable across retries** — the same delivery retried must carry the same id, or
  consumer deduplication cannot work.
- **Make events self-sufficient about state.** Include the resource's state at event time so a
  consumer applying an out-of-order event can recognize it as stale.

## Give them a way to catch up

The part most providers omit, and the one that turns webhooks from a liability into a system:

- **A list endpoint for past events** (`GET /events?since=...`), so a consumer that was down can
  reconcile without contacting support.
- **Manual replay** — let them re-request delivery of a specific event or a range, from a dashboard
  or an endpoint.
- **Delivery history per event** — attempts, response codes, and timings. This is what ends the
  "we never received it" argument in one query instead of a day.

Webhooks are a push optimization over a pull source of truth. If the only path to an event is a push
you may have failed to deliver, your consumers cannot ever be correct.

## Being a good outbound citizen

- **Publish your source IPs** or offer static egress, so consumers can allowlist.
- **Use a stable `User-Agent`** identifying your service and version.
- **Keep the URL configurable per environment**, and verify ownership when it is set — an unverified
  webhook URL is an SSRF vector, and a redirect-following delivery worker can be aimed at internal
  services. Do not follow redirects; resolve and reject private address ranges.
- **Let consumers subscribe to event types** rather than receiving everything.

## Review checklist

- [ ] Every webhook is signed (HMAC over timestamp + raw body) with a per-consumer secret
- [ ] Timestamp is signed and consumers are told to enforce a tolerance window
- [ ] Two secrets can be active simultaneously for rotation
- [ ] Delivery is asynchronous via an outbox — never inside a transaction or on the request path
- [ ] Retries use exponential backoff **with jitter**, and a defined terminal state
- [ ] Non-`429` `4xx` responses are not retried
- [ ] Consumers are notified out of band when deliveries are persistently failing
- [ ] Per-consumer isolation prevents one bad endpoint consuming shared capacity
- [ ] Delivery timeouts are short (5–10s)
- [ ] Payloads carry a stable event id and event timestamp; ids are stable across retries
- [ ] At-least-once and unordered delivery are documented, ideally with a sequence number
- [ ] No secrets or full PII in payloads
- [ ] An event list/replay endpoint exists so consumers can catch up unaided
- [ ] Delivery history (attempts, status codes) is queryable
- [ ] Webhook URLs are verified; redirects are not followed and private ranges are rejected

---

*Synthesized from TechCraft's API Design Patterns series, part P10
([collection](https://www.patreon.com/techcraft_official)). The "a webhook is not trustworthy without
retry and signature" framing and the gap between a demo callback and a production delivery system are
TechCraft's; the signing scheme, retry schedule, catch-up endpoints, and checklist are this
repository's.*

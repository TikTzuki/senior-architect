# Timeouts & Retries

**Rule: retry without idempotency is not error handling — it is a bug.** And a retry that fires
without backoff and jitter is a load multiplier aimed at a system that is already struggling.

Part of the distributed set: [network & latency](network-and-latency.md) ·
**timeouts & retries** (this file) ·
[circuit breakers & bulkheads](circuit-breakers-and-bulkheads.md) ·
[consensus & leader election](consensus-and-leader-election.md) ·
[designing for failure](designing-for-failure.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Timeouts: the number you must choose](#timeouts-the-number-you-must-choose)
- [Timeout budgets](#timeout-budgets)
- [Retry amplification](#retry-amplification)
- [Backoff and jitter](#backoff-and-jitter)
- [What is safe to retry](#what-is-safe-to-retry)
- [Retry needs idempotency, or it is a bug](#retry-needs-idempotency-or-it-is-a-bug)
- [Where to retry](#where-to-retry)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
# "If the bank call fails or times out, try again immediately, up to 3 times."
for attempt in range(3):
    try:
        return bank_api.charge(amount)     # no timeout, no idempotency key
    except (Timeout, ConnectionError):
        continue                            # no backoff, no jitter
raise PaymentFailed()
```

The humane reading is "be resilient." What it does under stress: the bank slows down, every client
times out, every client immediately sends three more requests, and a system already at capacity now
receives **4× the traffic at its weakest moment**.

> No hacker, no DDoS — just "reasonable" retry code.

And because there is no idempotency key, the retries that *do* land charge the customer more than
once.

## Why development hides it

The dependency stub always succeeds, so the retry loop never executes. There is no timeout to tune
because nothing is slow, and no concurrency, so amplification cannot occur.

Retry logic is also the code least likely to be tested: it runs only on failure, and failure is what
the test environment removes. The first real execution of this branch is during an incident.

## Timeouts: the number you must choose

"No timeout" is a choice — the worst one. A call without a timeout inherits whatever the OS eventually
does, which can be minutes, and holds a thread or connection for all of it. See
[network-and-latency](network-and-latency.md) for why slow is worse than dead.

Set them from measurements, not intuition:

- **Connection timeout** short (1–3 s). Establishing a TCP connection either works quickly or the
  host is unreachable.
- **Read/request timeout** derived from the dependency's **p99, plus headroom** — not its average, and
  not a round number someone liked.
- **Total/overall timeout** bounding the whole operation including retries. Without it, three retries
  at a 10-second timeout is a 30-second request nobody intended.

The subtle trap: **a timeout set at p99 retries the slowest 1%** of requests — the ones already
struggling. Setting it too tight converts healthy-but-slow into failure and generates the retries
that cause the outage.

## Timeout budgets

Timeouts must decrease as you go deeper, or inner calls outlive the outer request:

```
client        10s
  gateway      9s
    service A  8s
      service B 4s   ← A can retry B twice and still fit
        database 2s
```

If B's timeout exceeds A's, A gives up while B keeps working — capacity spent on an answer nobody will
read. That is the same waste as a
[cache stampede](caching.md) computing responses for timed-out clients.

**Propagate the deadline**, not the timeout. Pass "you have 3.2 s remaining" (gRPC deadlines,
`Deadline` headers) so a downstream service can decline work it cannot finish rather than starting it.
A service that checks its remaining budget before an expensive call is doing something no static
timeout can.

## Retry amplification

Retries are a load multiplier, and the multiplication happens at the worst possible time:

```
normal:      1,000 rps
degraded:    1,000 rps + 3 retries each = 4,000 rps
             → dependency slows further
             → more timeouts → more retries → collapse
```

This is a positive feedback loop, and it is why retries cause more outages than they prevent. Three
properties keep it bounded:

- **Retry budgets** — cap retries as a *fraction* of total requests (say 10%). When the failure rate
  is high, retries are suppressed system-wide rather than per-call. This is the single most effective
  control, and it is the one most implementations lack.
- **Circuit breakers** — stop calling a dying dependency at all. See
  [circuit-breakers-and-bulkheads](circuit-breakers-and-bulkheads.md).
- **Retry at one layer only** — see [below](#where-to-retry).

## Backoff and jitter

```python
delay = min(base * (2 ** attempt), cap)
delay = random.uniform(0, delay)          # full jitter
```

**Exponential backoff** gives the dependency time to recover instead of hammering it.

**Jitter is not a refinement — it is the point.** Without it, all clients that failed at the same
instant retry at the same instant, so you replace continuous overload with synchronized spikes. Any
shared outage synchronizes every client's clock, which is precisely when lockstep is most likely.

Also cap the delay (a retry an hour later is usually useless), and cap total elapsed time, not just
attempt count.

## What is safe to retry

| Response                         | Retry?                                                               |
|----------------------------------|----------------------------------------------------------------------|
| Connection refused / DNS failure | **Yes** — the request never arrived                                  |
| Timeout                          | **Only if idempotent** — it may have succeeded                       |
| `500`, `502`, `503`, `504`       | Yes, with backoff                                                    |
| `429`                            | Yes, honouring `Retry-After` — see [rate-limiting](rate-limiting.md) |
| `400`, `422` (validation)        | **No** — it will fail identically forever                            |
| `401`, `403`                     | No (except once after refreshing a token)                            |
| `404`                            | No                                                                   |
| `409` (conflict)                 | Only after re-reading state                                          |

The dangerous row is **timeout**, because it is genuinely ambiguous: the request may have completed
and only the response was lost. Retrying a non-idempotent operation after a timeout is how customers
get charged twice — see
[payment-provider-and-webhooks](payment-provider-and-webhooks.md), where the same ambiguity is
modelled as `UNKNOWN`.

## Retry needs idempotency, or it is a bug

> Implementing retry without idempotency is not error handling; it is a serious bug.

Before adding a retry, the operation must be safe to execute twice. Options, in order of preference:

1. **Naturally idempotent** — a `GET`, or a `PUT` of a complete state.
2. **Idempotency key** — the server deduplicates and replays the original response. See
   [payment-state-and-idempotency](payment-state-and-idempotency.md).
3. **Conditional write** — `UPDATE ... WHERE status = 'pending'`, with the affected row count checked.
4. **Query-then-decide** — ask the dependency what happened before acting.

If none applies, **do not retry**. Record the uncertainty, surface it, and reconcile — see
[payment-reconciliation](payment-reconciliation.md). An unretried unknown is recoverable; a duplicate
charge is not.

## Where to retry

Retries compose multiplicatively, and this is the mistake that turns 3 retries into 27:

```
client retries 3 ×  gateway retries 3 ×  service retries 3  =  27 requests
```

Pick **one** layer and disable the others deliberately. Usually the layer closest to the failure has
the best information, but whichever you choose, the others must be explicitly off — and that includes
retries hidden inside SDKs, HTTP clients, and service meshes, which are frequently on by default and
invisible in your code.

Audit those defaults. A library retrying three times underneath your own three-times loop is the most
common source of unexplained amplification.

## Review checklist

- [ ] Every remote call has connection and read timeouts
- [ ] Timeouts derive from measured p99, with an overall bound covering retries
- [ ] Timeout budgets decrease with depth; deadlines are propagated
- [ ] Retries use exponential backoff **with jitter** and a capped delay
- [ ] Total retry attempts and total elapsed time are both bounded
- [ ] A retry budget caps retries as a fraction of traffic
- [ ] Only retryable responses are retried; `4xx` (except `429`) is not
- [ ] `429` honours `Retry-After`
- [ ] Every retried operation is idempotent, by key, conditional write, or nature
- [ ] Timeout is treated as UNKNOWN for non-idempotent operations, not as failure
- [ ] Retries happen at exactly one layer; SDK/mesh/client defaults are audited and disabled
- [ ] Retry rate is a monitored metric, alerted on when it rises

---

*Synthesized from TechCraft's Distributed Systems series, parts P4–P5 and P11
([collection](https://www.patreon.com/techcraft_official)), and API Design Patterns P11. The
"reasonable retry code killed the system" story, uncertainty framing, and "retry without idempotency
is a bug" are TechCraft's; the budget/jitter mechanics, retryable-response table, and checklist are
this repository's.*

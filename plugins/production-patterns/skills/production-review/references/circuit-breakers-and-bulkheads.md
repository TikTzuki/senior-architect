# Circuit Breakers & Bulkheads

**Rule: a dying dependency needs to be isolated, not attacked with more requests.** Breakers stop
you calling what cannot answer; bulkheads stop one failure consuming the resources everything else
needs.

Part of the distributed set: [network & latency](network-and-latency.md) ·
[timeouts & retries](timeouts-and-retries.md) ·
**circuit breakers & bulkheads** (this file) ·
[consensus & leader election](consensus-and-leader-election.md) ·
[designing for failure](designing-for-failure.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Stop calling what is dying](#stop-calling-what-is-dying)
- [The three states](#the-three-states)
- [Tuning it](#tuning-it)
- [What to do when the circuit is open](#what-to-do-when-the-circuit-is-open)
- [Bulkheads](#bulkheads)
- [Where to put the walls](#where-to-put-the-walls)
- [Breakers and bulkheads are not interchangeable](#breakers-and-bulkheads-are-not-interchangeable)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
# One shared pool, no breaker. Every endpoint depends on every dependency's health.
executor = ThreadPoolExecutor(max_workers=100)

def product_page(pid):
    product  = executor.submit(catalog.get, pid).result()
    reviews  = executor.submit(review_service.get, pid).result()   # this one is dying
    recs     = executor.submit(recommender.get, pid).result()      # nice-to-have
    return render(product, reviews, recs)
```

The review service degrades to 8-second responses. Every product page now holds three workers for
eight seconds, the shared pool fills, and **checkout starts failing** — an endpoint that never calls
the review service at all.

Two independent defects: nothing stops calling a dependency that is clearly failing, and nothing
prevents one dependency's slowness consuming capacity the rest of the system needs.

> "The network is a bit slow, the system will recover on its own."
> "If the service is failing, keep trying — maybe next time works."

Both are the reasoning that produces this.

## Why development hides it

Dependencies are stubs that always answer instantly, so a breaker never trips and a shared pool never
saturates. There is no partial failure — a dependency is either wired up or absent, never *sick*.

The failure also requires **concurrency plus a shared resource**, so it cannot appear with one request
at a time. Nothing in a passing test suite distinguishes an isolated design from a coupled one.

## Stop calling what is dying

> A service that is dying usually needs to be isolated, not attacked by thousands of new requests.

Once a dependency is failing, continuing to call it is harmful in both directions:

- **To you** — every call costs a timeout's worth of held resources, for a response you will not get.
- **To it** — a service struggling to recover cannot, while traffic keeps arriving. Retries make this
  worse; see [timeouts-and-retries](timeouts-and-retries.md).

A **circuit breaker** fails fast instead: after enough failures it stops attempting the call and
returns immediately, giving the dependency room to recover and freeing your capacity.

The value is not politeness. **Failing in 1 ms instead of 8 s is what keeps your own pool alive.**

## The three states

```
        failure rate exceeds threshold
CLOSED ─────────────────────────────────▶ OPEN
  ▲                                        │ after cool-down
  │ probe succeeds                         ▼
  └────────────────── HALF-OPEN ◀──────────┘
                          │ probe fails
                          └──────────▶ OPEN
```

- **Closed** — normal. Calls pass; failures are counted.
- **Open** — calls fail immediately without attempting. No resources held.
- **Half-open** — after a cool-down, allow a small number of trial calls. Success closes the circuit;
  failure re-opens it.

Half-open is the part that matters and the part often implemented badly: it must allow **a few**
requests, not all of them. Reopening the floodgates onto a service that has just come back is how you
knock it over again.

## Tuning it

- **Trip on failure *rate*, not absolute count**, over a rolling window with a minimum request volume.
  Without a volume threshold, two failures during a quiet period open the circuit.
- **Count slowness as failure.** A dependency answering at 10× its normal latency is failing, even
  with `200`s. Timeouts are what convert slow into countable — which is why breakers require
  timeouts to work at all.
- **Do not count client errors.** A `400` or `404` is a correct response about a bad request; counting
  them lets one malformed client open the circuit for everyone. Count `5xx`, timeouts, and connection
  errors.
- **Per dependency, and usually per endpoint.** One slow endpoint on a service should not disable
  calls to its healthy ones.
- **Cool-down long enough to matter** (seconds to tens of seconds) — too short and you are effectively
  retrying without backoff.

Set thresholds from the dependency's normal behaviour. A service with a naturally 2% error rate needs
a higher threshold than one that is normally flawless.

## What to do when the circuit is open

Failing fast is only half the design. The other half is **what the user gets**, and it should be
decided per dependency:

| Dependency                       | Open-circuit behaviour                             |
|----------------------------------|----------------------------------------------------|
| Recommendations, "related items" | Omit the section. The page still works.            |
| Reviews                          | Show cached, or hide with a notice                 |
| Inventory display                | Serve last-known with a staleness note             |
| **Payment authorization**        | **Fail the request.** Never fake success on money. |
| Auth                             | Fail closed — never degrade into granting access   |

This is the reviewable question: *is this dependency essential or enhancing?* An enhancing dependency
that can fail the whole request has been mis-specified — and most product pages have several. Serving
a degraded page beats serving an error, except where correctness or security is at stake.

## Bulkheads

Named after ship compartments: a hull breach floods one compartment, not the vessel.

The failure a bulkhead prevents is **resource stealing** — one slow dependency consuming a shared
pool. The fix is to stop sharing:

```python
# Separate, bounded capacity per dependency.
catalog_pool  = ThreadPoolExecutor(max_workers=40)
review_pool   = ThreadPoolExecutor(max_workers=10)   # can starve alone
recommend_pool= ThreadPoolExecutor(max_workers=5)    # least important, least capacity
```

Now the review service can be as slow as it likes; it exhausts 10 workers and stops. Checkout keeps
its own capacity.

> Failure is normal. The question is not "how do we never fail?" but "when one part collapses, how do
> we stop it dragging the whole thing down?"

## Where to put the walls

Bulkheads apply to every shared, exhaustible resource — not just thread pools:

- **Thread/worker pools** per dependency. See
  [server-concurrency-model](server-concurrency-model.md).
- **Connection pools** — a separate pool for background jobs and reports, so an analytical query
  cannot starve request traffic. See
  [connection-pools-and-latency](connection-pools-and-latency.md).
- **Per-tenant capacity** so one customer cannot consume everything — this is where bulkheads and
  [rate limiting](rate-limiting.md) meet.
- **Queues and consumers** — a separate queue per workload, so a backlog of one message type does not
  block others.
- **Instances** — dedicated instances for critical paths (checkout separate from browsing), the
  strongest and most expensive form.
- **Webhook delivery per consumer** — see
  [webhook-provider-design](webhook-provider-design.md).

The cost is real: partitioned capacity means lower utilization, because each compartment must be
sized for its own peak. That is the trade — some efficiency for containment — and it should be made
explicitly rather than discovered during an incident.

## Breakers and bulkheads are not interchangeable

They solve different problems and you generally want both:

|                     | Prevents                                             | Without it                                       |
|---------------------|------------------------------------------------------|--------------------------------------------------|
| **Circuit breaker** | Wasting resources on a dependency that cannot answer | You hold resources through every timeout         |
| **Bulkhead**        | One dependency consuming shared capacity             | One slow dependency degrades unrelated endpoints |

A breaker without a bulkhead still lets pre-trip traffic exhaust a shared pool. A bulkhead without a
breaker keeps burning its compartment's capacity on doomed calls. Together: the compartment is bounded
*and* stops paying for failure.

## Review checklist

- [ ] Every remote dependency has a circuit breaker, or a documented reason not to
- [ ] Breakers trip on failure **rate** over a window, with a minimum request volume
- [ ] Slow responses count as failures (timeouts are set — a breaker needs them)
- [ ] Client errors (`4xx`) do not trip the breaker
- [ ] Breakers are scoped per dependency, and per endpoint where behaviour differs
- [ ] Half-open allows a limited probe, not full traffic
- [ ] Each dependency is classified essential vs enhancing
- [ ] Enhancing dependencies degrade gracefully; the request does not fail
- [ ] Auth and payment fail closed — never a faked success
- [ ] Thread/worker pools are partitioned per dependency, not shared globally
- [ ] Background jobs and reports use separate connection pools from request traffic
- [ ] Queues are separated by workload
- [ ] Breaker state changes are logged and alerted on
- [ ] The utilization cost of partitioning is accepted deliberately

---

*Synthesized from TechCraft's Distributed Systems series, parts P6–P7
([collection](https://www.patreon.com/techcraft_official)). The "a dying service needs isolation, not
more requests" framing, the two misconceptions about self-recovery, and the divide-and-contain framing
of bulkheads are TechCraft's; the state-machine tuning detail, degradation table, and checklist are
this repository's.*

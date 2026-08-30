# The Network & Latency

**Rule: a healthy service plus a healthy service does not equal a healthy system.** The network
between them fails, and a service does not need to die to take you down — it only needs to be slow.

Part of the distributed set: **network & latency** (this file) ·
[timeouts & retries](timeouts-and-retries.md) ·
[circuit breakers & bulkheads](circuit-breakers-and-bulkheads.md) ·
[consensus & leader election](consensus-and-leader-election.md) ·
[designing for failure](designing-for-failure.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [The fallacies you are relying on](#the-fallacies-you-are-relying-on)
- [Slow is worse than dead](#slow-is-worse-than-dead)
- [Latency adds up, then multiplies](#latency-adds-up-then-multiplies)
- [The abstraction cost](#the-abstraction-cost)
- [Tail latency is the number that matters](#tail-latency-is-the-number-that-matters)
- [What to do about it](#what-to-do-about-it)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
def checkout(cart_id):
    cart    = cart_service.get(cart_id)          # each looks like a function call
    price   = pricing_service.quote(cart)        # each is a network round trip
    order   = order_service.create(cart, price)  # each can fail, or hang
    payment = payment_service.charge(order)      # none has a timeout
    return payment
```

Four services, each with 99.9% availability and each "fast." The combined availability of the chain
is 99.6%, and the latency is the *sum* of four round trips plus whatever the slowest one does today.

Nothing here reads as risky. The syntax of a method call hides four opportunities for the network to
behave badly.

## Why development hides it

Everything runs on one machine. Round trips are ~0.05 ms, packets never drop, no connection resets,
no DNS failure, no partition. `localhost` is not a small version of production — it is a **different
system**, one where the network cannot fail.

> Unit tests pass 100%, CI is green, and the business logic is unchanged from the monolith.

That is exactly what makes this class of failure so surprising when a monolith is split: the logic is
identical and the failure surface is entirely new.

## The fallacies you are relying on

Each of these is assumed by code that makes a remote call look like a local one:

1. **The network is reliable** — it is not; packets drop, connections reset, partitions happen.
2. **Latency is zero** — every call has a floor set by physics and hop count.
3. **Bandwidth is infinite** — large payloads are slow and expensive.
4. **The network is secure** — anything unencrypted or unauthenticated between services is exposed.
5. **Topology does not change** — instances move, scale, and get rescheduled constantly.
6. **There is one administrator** — the team owning the service you call has their own deploys and
   incidents.
7. **Transport cost is zero** — serialization, TLS handshakes, and proxy hops all cost real time.
8. **The network is homogeneous** — same-AZ, cross-AZ, and cross-region differ by orders of
   magnitude.

The practical one to check in review is **#5 plus #6**: the service you depend on will be redeployed
during your peak, by people who do not know you are calling it.

## Slow is worse than dead

> The thing that kills your system is usually not a service that is completely down, but one that is
> still alive and responding very slowly.

A dead dependency fails fast: the connection is refused, you get an error, you handle it. A slow
dependency **holds your resources**:

```
dependency latency 50ms → 5s
   ↓
your threads/connections held 100× longer
   ↓
pool exhausted            ← server-concurrency-model, connection-pools-and-latency
   ↓
requests that never touch that dependency start failing
   ↓
health checks can't get a connection → instances evicted → cascade
```

This is the central asymmetry of distributed systems: **degradation propagates further than
failure.** A crash is contained by the crash; slowness spreads through every shared resource.

Which is why "no timeout" is never a safe default, and why the fix is
[timeouts and retries](timeouts-and-retries.md) plus
[circuit breakers and bulkheads](circuit-breakers-and-bulkheads.md).

## Latency adds up, then multiplies

**Sequential calls add.** Four services at 100 ms each is 400 ms, and the client sees the total. The
reflex "it's only 400 ms slower, users won't notice" misses that this is *per request*, and
concurrency is latency × throughput — so 400 ms extra at 1,000 rps means 400 more requests in flight,
each holding resources.

**Nested calls multiply.** If A calls B in a loop of 20, and B calls C, you have made 20 × 2 round
trips for one user action. This is the N+1 shape from
[indexes-and-query-plans](indexes-and-query-plans.md) at service scale, and it is worse because each
hop adds serialization, TLS, and queueing.

**Fan-out is bounded by the slowest.** Calling five services in parallel takes as long as the
slowest, not the average — so your p99 is set by the worst participant's p99, and it gets worse with
each service you add to the fan-out.

## The abstraction cost

An RPC framework, a service mesh, or an ORM-like client makes a remote call look local. That is
valuable and it has a price: **the code no longer shows where the failure boundaries are.**

`pricing_service.quote(cart)` looks like `calculate_price(cart)`. One can hang for 30 seconds,
partially succeed, or return stale data from a retry. Nothing in the syntax distinguishes them.

So the reviewable habit: **treat every `.` that crosses a process boundary as a failure point** and
ask what happens when it is slow, when it fails, and when it succeeds but the response is lost. Each
answer must exist in the code, not in an assumption.

## Tail latency is the number that matters

Averages hide everything. At p50 = 50 ms and p99 = 3 s, one request in a hundred is 60× slower — and
if a page makes ten such calls, roughly one in ten page loads hits at least one slow call.

Two consequences:

- **Fan-out amplifies the tail.** With 10 parallel calls each having a 1% chance of being slow, ~10%
  of requests are slow. The more you decompose, the more the tail dominates.
- **Retries are drawn from the tail.** A timeout set at p99 means you retry the slowest 1% —
  precisely when the dependency is least able to absorb extra load.

Measure and alert on p99 and p99.9, per dependency. An average latency dashboard will look fine
throughout the incident.

## What to do about it

- **Give every remote call a timeout**, derived from a budget. See
  [timeouts and retries](timeouts-and-retries.md).
- **Reduce round trips.** Batch, aggregate server-side, or denormalize the read model rather than
  fanning out per item.
- **Parallelize independent calls** instead of chaining them, so latency is the max rather than the
  sum.
- **Make non-essential calls non-blocking.** A recommendation or an analytics event must never sit on
  the critical path.
- **Cache what tolerates staleness**, and know what does not. See
  [caching](caching.md) and [consistency-boundaries](consistency-boundaries.md).
- **Keep chatty services close** — same AZ, or merged. Cross-region round trips are a design
  decision, not an accident.
- **Propagate deadlines**, so a downstream service knows how much time remains rather than working on
  a request that has already timed out.

## Review checklist

- [ ] Every cross-process call has an explicit timeout
- [ ] Combined availability of dependency chains has been considered, not assumed
- [ ] Sequential calls that could be parallel are parallelized
- [ ] No per-item remote call inside a loop
- [ ] Non-essential dependencies are off the critical path
- [ ] p99/p99.9 latency is measured per dependency, not just averages
- [ ] Fan-out width is bounded and its tail-amplification is understood
- [ ] Deadlines are propagated to downstream calls
- [ ] Cross-AZ and cross-region hops are deliberate
- [ ] Payload sizes are bounded; large responses are paginated or streamed
- [ ] Remote calls are visibly distinguishable from local ones in the code

---

*Synthesized from TechCraft's Distributed Systems series, parts P1–P3
([collection](https://www.patreon.com/techcraft_official)). The "healthy A + healthy B ≠ healthy
system" framing, "a system does not need to die to fail, it only needs to be slow", and the
abstraction-cost argument are TechCraft's; the fallacy enumeration, tail-latency analysis, and
checklist are this repository's.*
